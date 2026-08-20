"""Client fuer POST /enforce/check.

Duenn und ausdruecklich: kein Decorator, kein Framework-Hook, keine Mandats-Erzeugung. Zwei
Operationen, und der Betreiber sieht bei beiden, was passiert.

    check(mandate, transaction)            -> das Verdikt des Servers
    verify(response, mandate, transaction) -> dasselbe lokal nachgerechnet

Der zweite Aufruf ist der Punkt der Sache. `check` glaubt dem Server; `verify` rechnet das
Verdikt aus Mandat und Transaktion selbst nach und vergleicht den `core_digest`. Weichen sie
ab, meldet das SDK MISMATCH — dann stimmt etwas mit dem Server nicht, und der Betreiber
erfaehrt es, statt es zu uebernehmen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple, Union

import httpx

from ._core import DENY, PENDING, PERMIT, _ct_eq, core_digest, enforce_check
from ._ratify_core import (
    APPROVED, DISAPPROVED, RATIFIED, REJECTED, RatifyError,
    core_digest as ratification_digest, mandate_authorities, ratify as _ratify_local,
    recompute as _recompute_ratification,
)
from ._core import _TAG_MANDATE, _digest
from .errors import EnforceProtocolError, EnforceTransportError

__all__ = ["EnforceClient", "Verdict", "VerifyResult", "Ratification",
           "PERMIT", "DENY", "PENDING", "APPROVED", "DISAPPROVED", "RATIFIED", "REJECTED"]

DEFAULT_TIMEOUT = 5.0
DEFAULT_PATH = "/enforce/check"
DEFAULT_RATIFY_PATH = "/enforce/ratify"
_VERDICTS = (PERMIT, DENY, PENDING)
_STATUSES = (RATIFIED, REJECTED)


@dataclass(frozen=True)
class Verdict:
    """Das Ergebnis eines Checks.

    `from_server=False` heisst: der Server hat nichts geliefert, das DENY ist lokal entstanden
    (Transportfehler, Fehlerstatus, unlesbare Antwort). Dann sind `core` und `core_digest` None
    und es gibt nichts nachzurechnen.
    """

    verdict: str
    reason: str
    grant_index: Optional[int] = None
    trace: Tuple[dict, ...] = ()
    core: Optional[dict] = None
    core_digest: Optional[str] = None
    from_server: bool = True

    @property
    def permitted(self) -> bool:
        """Nur PERMIT ist eine Erlaubnis. PENDING ist keine, DENY erst recht nicht."""
        return self.verdict == PERMIT

    @property
    def pending(self) -> bool:
        return self.verdict == PENDING

    @property
    def denied(self) -> bool:
        return self.verdict == DENY

    @classmethod
    def local_deny(cls, reason: str) -> "Verdict":
        return cls(verdict=DENY, reason=reason, from_server=False)


@dataclass(frozen=True)
class Ratification:
    """Das Ergebnis einer Ratifikation.

    `status=RATIFIED` heisst: der Vorgaenger gilt ab jetzt als `decision`. `status=REJECTED`
    heisst: die Autoritaet hat nicht getragen, der Vorgaenger behaelt seinen Status.

    `from_server=False` heisst, das REJECTED ist lokal entstanden (Transportfehler,
    Fehlerstatus, unlesbare Antwort) — dann gibt es keinen Record und nichts nachzurechnen.
    """

    status: str
    decision: str
    ratifies: Optional[str] = None
    authority: Optional[str] = None
    reason: str = ""
    trace: Tuple[dict, ...] = ()
    core: Optional[dict] = None
    core_digest: Optional[str] = None
    from_server: bool = True

    @property
    def ratified(self) -> bool:
        """Nur ein RATIFIED aendert den Status des Vorgaengers."""
        return self.status == RATIFIED

    @property
    def approved(self) -> bool:
        """Der Vorgaenger gilt jetzt als freigegeben. Beides muss stimmen."""
        return self.status == RATIFIED and self.decision == APPROVED

    @classmethod
    def local_reject(cls, reason: str, decision: str = "") -> "Ratification":
        return cls(status=REJECTED, decision=decision, reason=reason, from_server=False)


@dataclass(frozen=True)
class VerifyResult:
    """Ergebnis der lokalen Nachrechnung.

    `ok=False` heisst nicht „die Aktion ist verboten", sondern „diese Server-Antwort traegt
    nicht". Was der Betreiber daraus macht, entscheidet er; ihr zu folgen ist keine Option.
    """

    ok: bool
    mismatches: Tuple[str, ...]
    local: Optional[Verdict] = None
    # Nur bei Ratifikations-Pruefungen belegt: False, wenn ohne authority_proof geprueft
    # wurde und die Signatur deshalb nicht nachgerechnet werden konnte.
    full_recompute: bool = True

    def __bool__(self) -> bool:
        return self.ok


def _as_int_or_none(x: Any) -> Optional[int]:
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def _parse(payload: Any) -> Verdict:
    """Server-Antwort in ein Verdikt uebersetzen. Alles Unerwartete ist ein Protokollfehler.

    Insbesondere ein unbekannter `verdict`-String: das SDK deutet nichts, was es nicht kennt.
    """
    if not isinstance(payload, Mapping):
        raise EnforceProtocolError("response body is not a JSON object")
    verdict = payload.get("verdict")
    if verdict not in _VERDICTS:
        raise EnforceProtocolError(f"unknown verdict {verdict!r}")
    record = payload.get("record")
    if not isinstance(record, Mapping):
        record = {}
    core = record.get("core")
    digest = record.get("core_digest")
    trace = payload.get("trace")
    return Verdict(
        verdict=verdict,
        reason=payload.get("reason") if isinstance(payload.get("reason"), str) else "",
        grant_index=_as_int_or_none(payload.get("grant_index")),
        trace=tuple(trace) if isinstance(trace, Sequence) and not isinstance(trace, (str, bytes)) else (),
        core=dict(core) if isinstance(core, Mapping) else None,
        core_digest=digest if isinstance(digest, str) else None,
        from_server=True,
    )


class EnforceClient:
    """Client gegen einen MolTrust-Endpunkt mit `POST /enforce/check`.

    Parameter
    ---------
    base_url
        z.B. ``https://api.moltrust.ch``.
    api_key / did
        Auth wie die API sie erwartet: ``X-API-Key`` oder ``X-MolTrust-DID``. Mindestens eines.
    timeout
        Sekunden, Default 5.
    on_transport_error
        ``"deny"`` (Default) liefert bei Transport- oder Protokollfehlern ein lokales DENY.
        ``"raise"`` wirft stattdessen. Beides ist fail-closed — ein PERMIT entsteht in keinem
        der beiden Faelle. Ein drittes, durchlassendes Verhalten gibt es nicht.
    on_pending
        Optionaler Haken, der bei PENDING aufgerufen wird. Er loest PENDING NICHT auf: der
        Rueckgabewert wird ignoriert, das Verdikt bleibt PENDING. Der Haken ist zum Melden
        gedacht (Freigabe-Queue, Alarm), nicht zum Entscheiden.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        did: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        on_transport_error: str = "deny",
        on_pending: Optional[Callable[[Verdict], Any]] = None,
        path: str = DEFAULT_PATH,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if on_transport_error not in ("deny", "raise"):
            raise ValueError('on_transport_error must be "deny" or "raise"')
        if not api_key and not did:
            raise ValueError("either api_key or did is required (the endpoint authenticates)")
        self.base_url = base_url.rstrip("/")
        self.path = path
        self.timeout = timeout
        self.on_transport_error = on_transport_error
        self.on_pending = on_pending
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        if did:
            headers["X-MolTrust-DID"] = did
        self._client = httpx.Client(base_url=self.base_url, headers=headers,
                                    timeout=timeout, transport=transport)

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EnforceClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --------------------------------------------------------------------- check

    def check(self, mandate: Any, transaction: Any,
              prev_core_digest: Optional[str] = None) -> Verdict:
        """Fragt den Server und gibt dessen Verdikt zurueck.

        Fail-closed: erreicht der Aufruf den Server nicht, kommt ein Fehlerstatus zurueck oder
        ist die Antwort unlesbar, ist das Ergebnis DENY (bzw. eine Exception bei
        ``on_transport_error="raise"``). Ein PERMIT entsteht ausschliesslich aus einer
        gelesenen 200-Antwort, die PERMIT sagt.

        PENDING wird unveraendert durchgereicht. Gibt es einen ``on_pending``-Haken, wird er
        aufgerufen; das Verdikt bleibt trotzdem PENDING, und `permitted` bleibt False. Das SDK
        laesst eine PENDING-Aktion nie durch.
        """
        body: dict = {"mandate": mandate, "transaction": transaction}
        if prev_core_digest is not None:
            body["prev_core_digest"] = prev_core_digest

        try:
            response = self._client.post(self.path, json=body)
        except httpx.HTTPError as exc:
            return self._fail_closed(f"transport failure: {type(exc).__name__}: {exc}")

        if response.status_code != 200:
            return self._fail_closed(
                f"server returned HTTP {response.status_code}: {response.text[:200]}")

        try:
            verdict = _parse(response.json())
        except EnforceProtocolError as exc:
            return self._fail_closed(f"protocol failure: {exc}")
        except ValueError as exc:
            return self._fail_closed(f"protocol failure: response is not JSON: {exc}")

        if verdict.pending and self.on_pending is not None:
            # Rueckgabewert bewusst ignoriert — der Haken meldet, er entscheidet nicht.
            self.on_pending(verdict)
        return verdict

    def _fail_closed(self, reason: str) -> Verdict:
        if self.on_transport_error == "raise":
            raise EnforceTransportError(reason)
        return Verdict.local_deny(reason)

    # -------------------------------------------------------------------- verify

    def verify(self, response: Union[Verdict, Mapping], mandate: Any,
               transaction: Any) -> VerifyResult:
        """Rechnet das Verdikt lokal nach und vergleicht es mit der Server-Antwort.

        Braucht keinen Netzzugriff und keinen Serverzustand: der Kern ist rein, das Verdikt
        haengt allein an Mandat und Transaktion. Geprueft wird dreifach —

        1. traegt sich die Antwort selbst (``core_digest`` passt zum mitgelieferten ``core``),
        2. ergibt die lokale Auswertung denselben ``core_digest``,
        3. sagt der Server dasselbe Verdikt wie die lokale Auswertung.

        Jede Abweichung landet in ``mismatches``.
        """
        mismatches: list = []
        server = response if isinstance(response, Verdict) else _safe_parse(response, mismatches)

        prev = None
        if server is not None and isinstance(server.core, Mapping):
            prev = server.core.get("prev_core_digest")
        local_raw = enforce_check(mandate, transaction,
                                  prev_core_digest=prev if isinstance(prev, str) else None)
        local = Verdict(
            verdict=local_raw["verdict"], reason=local_raw["reason"],
            grant_index=local_raw["grant_index"], trace=tuple(local_raw["trace"]),
            core=local_raw["core"], core_digest=local_raw["core_digest"], from_server=False,
        )

        if server is None:
            return VerifyResult(False, tuple(mismatches), local)

        if not server.from_server:
            mismatches.append("no server record: this verdict was produced locally "
                              "(transport-level DENY), there is nothing to verify")
            return VerifyResult(False, tuple(mismatches), local)

        if server.core is None or server.core_digest is None:
            mismatches.append("response carries no record (core / core_digest missing)")
            return VerifyResult(False, tuple(mismatches), local)

        recomputed = core_digest(server.core)
        if not _ct_eq(recomputed, server.core_digest):
            mismatches.append(
                f"core_digest does not match the core it ships with "
                f"(claimed {server.core_digest}, recomputed {recomputed})")

        if not _ct_eq(local.core_digest, server.core_digest):
            mismatches.append(
                f"local recompute disagrees with the server record "
                f"(local {local.core_digest}, server {server.core_digest})")

        if server.verdict != local.verdict:
            mismatches.append(
                f"verdict mismatch: server says {server.verdict}, local says {local.verdict}")

        stated = server.core.get("verdict")
        if stated != server.verdict:
            mismatches.append(
                f"response verdict {server.verdict} contradicts its own core verdict {stated!r}")

        return VerifyResult(not mismatches, tuple(mismatches), local)

    # -------------------------------------------------------------------- ratify

    def ratify(self, prior_record: Any, decision: str,
               authority_proof: Any, prev_core_digest: Optional[str] = None) -> Ratification:
        """Ratifiziert einen DENY- oder PENDING-Record ueber POST /enforce/ratify.

        Der Vorgaenger wird nicht veraendert; zurueck kommt ein zweiter Record, der ihn
        referenziert.

        Fail-closed: Transportfehler, Fehlerstatus, unlesbare Antwort und unbekannter
        `status`-Wert ergeben ein lokales REJECTED (`from_server=False`) — oder eine
        `EnforceTransportError` bei ``on_transport_error="raise"``. RATIFIED entsteht in
        keinem dieser Faelle. Eine Statusaenderung kann also nie aus einer Stoerung folgen.

        Ein nicht ratifizierbarer Vorgaenger (etwa ein PERMIT) ist ein Aufrufer-Fehler und
        wird vom Server mit 422 quittiert; das landet hier ebenfalls als REJECTED, mit dem
        Servertext im `reason`.
        """
        body: dict = {"prior_record": prior_record, "decision": decision,
                      "authority_proof": authority_proof}
        if prev_core_digest is not None:
            body["prev_core_digest"] = prev_core_digest

        try:
            response = self._client.post(DEFAULT_RATIFY_PATH, json=body)
        except httpx.HTTPError as exc:
            return self._fail_closed_ratify(
                f"transport failure: {type(exc).__name__}: {exc}", decision)

        if response.status_code != 200:
            return self._fail_closed_ratify(
                f"server returned HTTP {response.status_code}: {response.text[:200]}", decision)

        try:
            return _parse_ratification(response.json())
        except EnforceProtocolError as exc:
            return self._fail_closed_ratify(f"protocol failure: {exc}", decision)
        except ValueError as exc:
            return self._fail_closed_ratify(
                f"protocol failure: response is not JSON: {exc}", decision)

    def _fail_closed_ratify(self, reason: str, decision: str) -> Ratification:
        if self.on_transport_error == "raise":
            raise EnforceTransportError(reason)
        return Ratification.local_reject(reason, decision)

    def verify_ratification(self, ratification_record: Union[Ratification, Mapping],
                            prior_record: Any, mandate: Any,
                            authority_proof: Any = None) -> VerifyResult:
        """Prueft eine Ratifikation lokal, ohne dem Server zu glauben.

        Ohne `authority_proof` laeuft die strukturelle Pruefung: traegt sich der Record
        selbst, haengt er am richtigen Vorgaenger, passt das Mandat zu dessen
        `mandate_digest`, und leitet sich die genannte Autoritaet ueberhaupt aus dem Mandat
        ab. Das faengt eine erfundene Autoritaet und eine gebrochene Kette.

        Was es NICHT faengt: einen von RATIFIED erzaehlenden Record, dessen Autoritaet zwar
        im Mandat steht, dessen Signatur aber nie geprueft hat. Dafuer wird der
        `authority_proof` gebraucht — wird er mitgegeben, rechnet diese Methode den Record
        vollstaendig nach. Ob das geschehen ist, steht in `result.full_recompute`; bei False
        ist die Pruefung schwaecher und sagt es.
        """
        mismatches: list = []
        rec = (ratification_record if isinstance(ratification_record, Ratification)
               else _safe_parse_ratification(ratification_record, mismatches))
        if rec is None:
            return VerifyResult(False, tuple(mismatches), None, full_recompute=False)

        if not rec.from_server:
            mismatches.append("no server record: this ratification was produced locally "
                              "(transport-level REJECTED), there is nothing to verify")
            return VerifyResult(False, tuple(mismatches), None, full_recompute=False)
        if rec.core is None or rec.core_digest is None:
            mismatches.append("response carries no record (core / core_digest missing)")
            return VerifyResult(False, tuple(mismatches), None, full_recompute=False)

        # 1 — traegt sich der Record selbst?
        recomputed = ratification_digest(rec.core)
        if not _ct_eq(recomputed, rec.core_digest):
            mismatches.append(
                f"core_digest does not match the core it ships with "
                f"(claimed {rec.core_digest}, recomputed {recomputed})")

        # 2 — Kette: referenziert er wirklich diesen Vorgaenger?
        prior_digest = (prior_record or {}).get("core_digest") if isinstance(prior_record, Mapping) else None
        if not isinstance(prior_digest, str):
            mismatches.append("prior_record carries no core_digest")
        else:
            if not _ct_eq(rec.core.get("ratifies"), prior_digest):
                mismatches.append(
                    f"ratifies {rec.core.get('ratifies')!r} is not the prior record "
                    f"{prior_digest!r}")
            prior_core = prior_record.get("core") if isinstance(prior_record, Mapping) else None
            if isinstance(prior_core, Mapping):
                if not _ct_eq(core_digest(dict(prior_core)), prior_digest):
                    mismatches.append("prior_record.core_digest does not match its own core")
                # 3 — bindet das gelieferte Mandat an den Vorgaenger?
                supplied = _digest(_TAG_MANDATE, mandate)
                if not _ct_eq(supplied, prior_core.get("mandate_digest")):
                    mismatches.append(
                        "supplied mandate does not match the prior record's mandate_digest")

        # 4 — leitet sich die genannte Autoritaet aus dem Mandat ab?
        if rec.status == RATIFIED:
            allowed = [d for d, _k, _r in mandate_authorities(mandate)]
            if not any(_ct_eq(d, rec.authority) for d in allowed):
                mismatches.append(
                    f"ratifying authority {rec.authority!r} does not derive from the mandate "
                    f"(mandate names {allowed})")

        # 5 — vollstaendige Nachrechnung, wenn der Nachweis vorliegt
        full = authority_proof is not None
        if full:
            record = {"core": rec.core, "core_digest": rec.core_digest}
            try:
                ok = _recompute_ratification(prior_record, rec.decision, authority_proof, record)
            except RatifyError as exc:
                ok = False
                mismatches.append(f"recompute refused the inputs: {exc}")
            if not ok:
                mismatches.append(
                    "local recompute disagrees with the server record "
                    "(status, decision, authority or trace does not follow from the inputs)")

        return VerifyResult(not mismatches, tuple(mismatches), None, full_recompute=full)


def _parse_ratification(payload: Any) -> Ratification:
    if not isinstance(payload, Mapping):
        raise EnforceProtocolError("response body is not a JSON object")
    status = payload.get("status")
    if status not in _STATUSES:
        raise EnforceProtocolError(f"unknown ratification status {status!r}")
    decision = payload.get("decision")
    if decision not in (APPROVED, DISAPPROVED):
        raise EnforceProtocolError(f"unknown decision {decision!r}")
    record = payload.get("record")
    if not isinstance(record, Mapping):
        record = {}
    core = record.get("core")
    digest = record.get("core_digest")
    trace = payload.get("trace")
    authority = payload.get("authority")
    return Ratification(
        status=status,
        decision=decision,
        ratifies=payload.get("ratifies") if isinstance(payload.get("ratifies"), str) else None,
        authority=authority if isinstance(authority, str) else None,
        reason=payload.get("reason") if isinstance(payload.get("reason"), str) else "",
        trace=tuple(trace) if isinstance(trace, Sequence) and not isinstance(trace, (str, bytes)) else (),
        core=dict(core) if isinstance(core, Mapping) else None,
        core_digest=digest if isinstance(digest, str) else None,
        from_server=True,
    )


def _safe_parse_ratification(payload: Any, mismatches: list) -> Optional[Ratification]:
    try:
        return _parse_ratification(payload)
    except EnforceProtocolError as exc:
        mismatches.append(f"response is not a well-formed ratification: {exc}")
        return None


def _safe_parse(payload: Any, mismatches: list) -> Optional[Verdict]:
    try:
        return _parse(payload)
    except EnforceProtocolError as exc:
        mismatches.append(f"response is not a well-formed verdict: {exc}")
        return None
