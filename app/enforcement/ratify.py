"""Ratifikation — nachtraegliche Statusaenderung durch Anhaengen (AAE -02-Kandidat §9).

Ein Verdikt-Record aus `enforce_check` bleibt unveraendert. Wer den Status nachtraeglich
setzen will, haengt einen zweiten, signierten Record an, der den ersten per `core_digest`
referenziert. Historie wird korrigiert, indem angehaengt wird, nie indem editiert wird.

Rein und deterministisch wie `enforce_check`: keine DB, kein Netz, keine Uhr. Der
Ratifikations-Record haengt allein an (prior_record, decision, authority_proof); wer die drei
hat, rechnet den `core_digest` ohne diesen Server nach.

Witness-not-Ruler (der sicherheitskritische Punkt)
--------------------------------------------------
Die ratifizierende Autoritaet wird AUSSCHLIESSLICH aus dem Mandat abgeleitet, das der
Vorgaenger-Record referenziert — der ausstellende Principal oder eine im Mandat benannte Rolle.
Keine MolTrust-Instanz, keine zentrale Aufsichtsrolle, kein Sonderweg. Der oeffentliche
Schluessel, gegen den geprueft wird, stammt IMMER aus dem Mandat, nie aus dem Nachweis selbst —
sonst brauchte ein Angreifer nur seinen eigenen Schluessel mitzuliefern.

Das Mandat kommt im `authority_proof` mit, weil der Vorgaenger-Record nur den
`mandate_digest` traegt und sich aus einem Hash keine Autoritaet ableiten laesst. Gebunden wird
es ueber genau diesen Hash: stimmt `JCS(mandate)`-Digest nicht mit dem des Vorgaengers ueberein,
ist der Nachweis wertlos und die Ratifikation wird abgelehnt.

Zwei Wachen
-----------
- **Guard 1 (Autoritaet).** Nachweis fehlt, Mandat passt nicht zum Vorgaenger, Autoritaet steht
  nicht im Mandat, oder die Signatur prueft nicht: `status = REJECTED`, der Vorgaenger behaelt
  seinen Status. Das ist kein Fehler, sondern ein Ergebnis — mit Spur, warum.
- **Guard 2 (ratifizierbarer Vorgaenger).** Nur DENY und PENDING lassen sich ratifizieren. Ein
  PERMIT zu ratifizieren ergibt keinen Sinn und ist ein Aufrufer-Fehler: `RatifyError`.
- **Guard 3 (Kettenbindung).** Wird `prev_core_digest` mitgegeben, MUSS er auf genau den
  Record zeigen, der ratifiziert wird. Eine Ratifikation ist eine Aussage ueber einen
  bestimmten Vorgaenger; ein Kettenglied, das woandershin zeigt, beschriebe eine andere
  Historie als die, die der Record behauptet. Auch das ist ein Aufrufer-Fehler, kein
  Ergebnis: es gibt nichts zu protokollieren, wenn schon die Frage nicht zusammenpasst —
  `RatifyError`, wie Guard 2.
"""
from __future__ import annotations

from typing import Any, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.enforcement.enforce_check import (
    DENY, FAIL, PASS, PENDING, PERMIT, _TAG_MANDATE, _ct_eq, _digest, _pred,
    _trace_for_core,
    canonicalize, core_digest as _verdict_core_digest,
)

APPROVED = "APPROVED"
DISAPPROVED = "DISAPPROVED"
RATIFIED = "RATIFIED"
REJECTED = "REJECTED"

RATIFY_VERSION = "3.0"

# Eigene Domain-Tags: eine Signatur ueber eine Ratifikation darf nie als Verdikt-Signatur
# durchgehen und umgekehrt.
_TAG_STATEMENT = b"aae:enforce-ratify-statement:v1\x00"
_TAG_CORE = b"aae:enforce-ratify-core:v1\x00"

_DECISIONS = (APPROVED, DISAPPROVED)
# Nur diese Vorgaenger-Verdikte sind ratifizierbar (Guard 2).
_RATIFIABLE = (DENY, PENDING)

MAX_AUTHORITIES = 64


class RatifyError(ValueError):
    """Aufrufer-Fehler: der Vorgaenger ist gar nicht ratifizierbar (Guard 2), oder die
    Entscheidung ist keine. Abzugrenzen von einer abgelehnten Ratifikation, die ein
    regulaeres Ergebnis mit Spur ist."""


# --------------------------------------------------------------------------- Bausteine

def _ed25519_verify(public_key_hex: Any, signature_hex: Any, message: bytes) -> bool:
    """Ed25519-Pruefung, fail-closed. Jede Stoerung (falsches Hex, falsche Laenge,
    ungueltige Signatur) ist ein glattes False, nie eine Exception nach oben."""
    if not isinstance(public_key_hex, str) or not isinstance(signature_hex, str):
        return False
    try:
        raw = bytes.fromhex(public_key_hex)
        sig = bytes.fromhex(signature_hex)
    except ValueError:
        return False
    if len(raw) != 32 or len(sig) != 64:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(sig, message)
        return True
    except Exception:
        return False


def ratification_statement(prior_core_digest: str, decision: str, authority_did: str) -> dict:
    """Was die Autoritaet signiert.

    Bindet Vorgaenger, Entscheidung und Autoritaet aneinander: eine APPROVED-Signatur laesst
    sich weder auf einen anderen Record umhaengen noch nach DISAPPROVED umdeuten noch einer
    anderen Autoritaet zuschreiben.
    """
    return {"ratify_version": RATIFY_VERSION, "ratifies": prior_core_digest,
            "decision": decision, "authority": authority_did}


def statement_bytes(prior_core_digest: str, decision: str, authority_did: str) -> bytes:
    """Die exakten Bytes, ueber die signiert wird — Domain-Tag vor JCS(statement)."""
    return _TAG_STATEMENT + canonicalize(
        ratification_statement(prior_core_digest, decision, authority_did))


def mandate_authorities(mandate: Any) -> list:
    """Wer laut Mandat ratifizieren darf: `(did, public_key_hex, rolle)`.

    Genau zwei Quellen, beide im Mandat: der ausstellende Principal und die dort benannten
    Rollen. Ein Eintrag ohne DID oder ohne Schluessel zaehlt nicht — ohne Schluessel gaebe es
    nichts zu pruefen.
    """
    if not isinstance(mandate, dict):
        return []
    out: list = []
    principal = mandate.get("principal")
    if isinstance(principal, dict):
        did, key = principal.get("did"), principal.get("public_key")
        if isinstance(did, str) and isinstance(key, str):
            out.append((did, key, "principal"))
    named = mandate.get("ratification_authorities")
    if isinstance(named, list):
        for entry in named[:MAX_AUTHORITIES]:
            if not isinstance(entry, dict):
                continue
            did, key = entry.get("did"), entry.get("public_key")
            if isinstance(did, str) and isinstance(key, str):
                role = entry.get("role")
                out.append((did, key, role if isinstance(role, str) else "named_authority"))
    return out


def _prior_problem(prior_record: Any) -> Optional[str]:
    """Traegt der Vorgaenger sich selbst? Ein Record, dessen Digest nicht zu seinem Core
    passt, ist keine Grundlage fuer irgendetwas."""
    if not isinstance(prior_record, dict):
        return "prior_record missing or not an object"
    core = prior_record.get("core")
    claimed = prior_record.get("core_digest")
    if not isinstance(core, dict):
        return "prior_record.core missing or not an object"
    if not isinstance(claimed, str):
        return "prior_record.core_digest missing"
    if not _ct_eq(_verdict_core_digest(core), claimed):
        return "prior_record.core_digest does not match its own core"
    if not isinstance(core.get("mandate_digest"), str):
        return "prior_record.core.mandate_digest missing"
    return None


# ------------------------------------------------------------------------- oeffentlich

def core_digest(core: dict) -> Optional[str]:
    """Digest ueber den Ratifikations-Core. Ein Dritter ruft das mit dem Core aus dem
    Record auf."""
    return _digest(_TAG_CORE, core)


def ratify(prior_record: Any, decision: Any, authority_proof: Any,
           prev_core_digest: Optional[str] = None) -> dict:
    """Ratifiziert einen DENY- oder PENDING-Record. Rein, ohne Seiteneffekt.

    `authority_proof` = ``{"mandate": {...}, "authority": "<did>", "signature": "<hex>"}``.
    Das Mandat muss dasselbe sein, auf das sich der Vorgaenger bezieht — geprueft ueber
    `mandate_digest`, nicht geglaubt.

    `prev_core_digest` ist das Kettenglied. Ohne Angabe wird der `core_digest` des Vorgaengers
    eingesetzt; mit Angabe MUSS er genau dieser Wert sein (Guard 3).

    Rueckgabe: ``{status, decision, ratifies, authority, reason, trace, core, core_digest}``.
    `status` ist RATIFIED oder REJECTED. Nur bei RATIFIED gilt `decision` fuer den Vorgaenger;
    bei REJECTED behaelt er seinen urspruenglichen Status.

    Wirft `RatifyError`, wenn der Vorgaenger gar nicht ratifizierbar ist (Guard 2), das
    Kettenglied woandershin zeigt (Guard 3), oder die Entscheidung keine ist — das sind
    Aufrufer-Fehler, keine Ergebnisse.
    """
    if decision not in _DECISIONS:
        raise RatifyError(f"decision must be one of {_DECISIONS}, got {decision!r}")

    problem = _prior_problem(prior_record)
    if problem is not None:
        raise RatifyError(problem)

    prior_core = prior_record["core"]
    prior_digest = prior_record["core_digest"]
    prior_verdict = prior_core.get("verdict")

    # --- Guard 2: nur DENY/PENDING sind ratifizierbar -------------------------------
    if prior_verdict == PERMIT:
        raise RatifyError("a PERMIT is not ratifiable — there is no status to change")
    if prior_verdict not in _RATIFIABLE:
        raise RatifyError(f"prior verdict {prior_verdict!r} is not ratifiable "
                          f"(expected one of {_RATIFIABLE})")

    # --- Guard 3: das Kettenglied zeigt auf den ratifizierten Record -----------------
    # Ohne Angabe faellt es unten auf `prior_digest` zurueck; mit Angabe wird es geprueft
    # statt geglaubt. Ein wohlgeformter, aber fremder Digest ist sonst unauffaellig — der
    # Record behauptete dann eine Kette, die es nicht gibt.
    if prev_core_digest is not None and not _ct_eq(prev_core_digest, prior_digest):
        raise RatifyError(
            "prev_core_digest must equal the core_digest of the record being ratified "
            f"(ratifies {prior_digest}, got {prev_core_digest!r})")

    # --- Guard 1: Autoritaet aus dem Mandat, Signatur nachrechenbar -----------------
    trace: list = [_pred("prior_ratifiable", None, PASS,
                         f"prior verdict {prior_verdict} may be ratified", prior_verdict, None)]
    authority_did: Optional[str] = None
    status, reason = REJECTED, "authority not established"

    if not isinstance(authority_proof, dict):
        trace.append(_pred("authority_proof", None, FAIL, "authority_proof missing or not an object"))
        reason = "authority_proof missing or not an object"
    else:
        mandate = authority_proof.get("mandate")
        claimed_authority = authority_proof.get("authority")
        signature = authority_proof.get("signature")

        # Derselbe Tag wie im Verdikt-Kern — importiert, nicht nachgebaut, sonst driftet er.
        supplied_digest = _digest(_TAG_MANDATE, mandate) if mandate is not None else None
        bound = _ct_eq(supplied_digest, prior_core["mandate_digest"])
        trace.append(_pred("mandate_binding", "mandate", PASS if bound else FAIL,
                           "supplied mandate is the one the prior record refers to" if bound
                           else "supplied mandate does not match prior_record.mandate_digest",
                           supplied_digest, prior_core["mandate_digest"]))
        if not bound:
            reason = "supplied mandate does not match the prior record"
        else:
            # Der Schluessel kommt aus dem MANDAT, nie aus dem Nachweis.
            allowed = mandate_authorities(mandate)
            match = None
            for did, key_hex, role in allowed:
                if _ct_eq(did, claimed_authority):
                    match = (did, key_hex, role)
                    break
            trace.append(_pred("authority_in_mandate", "authority",
                               PASS if match else FAIL,
                               f"authority derives from mandate as {match[2]}" if match
                               else "authority is not the issuing principal and not a role named "
                                    "in the mandate",
                               claimed_authority, [d for d, _k, _r in allowed]))
            if not match:
                reason = "authority does not derive from the mandate"
            else:
                did, key_hex, role = match
                ok = _ed25519_verify(
                    key_hex, signature, statement_bytes(prior_digest, decision, did))
                trace.append(_pred("authority_signature", "signature", PASS if ok else FAIL,
                                   "signature verifies against the mandate-held key" if ok
                                   else "signature does not verify against the mandate-held key",
                                   None, None))
                if ok:
                    authority_did = did
                    status = RATIFIED
                    reason = (f"ratified by {role} {did}: prior {prior_verdict} -> {decision}")
                else:
                    reason = "authority signature does not verify"

    core = {
        "ratify_version": RATIFY_VERSION,
        "ratifies": prior_digest,
        "prior_verdict": prior_verdict,
        "decision": decision,
        "status": status,
        "authority": authority_did,
        "mandate_digest": prior_core["mandate_digest"],
        "trace": _trace_for_core(trace),
        "prev_core_digest": prev_core_digest if isinstance(prev_core_digest, str) else prior_digest,
    }
    return {"status": status, "decision": decision, "ratifies": prior_digest,
            "authority": authority_did, "reason": reason, "trace": trace,
            "core": core, "core_digest": core_digest(core)}


def recompute(prior_record: Any, decision: Any, authority_proof: Any,
              record: Any) -> bool:
    """Dritt-Nachrechnung: ergeben dieselben Eingaben denselben Ratifikations-Core?

    Vergleicht den Digest, nicht die Objektform — dieselbe Pruefung, die ein externer
    Verifizierer ohne Serverzugriff anstellt. Fail-closed: alles Unerwartete ist False.
    """
    if not isinstance(record, dict):
        return False
    claimed = record.get("core_digest")
    if not isinstance(claimed, str):
        return False
    prev = record.get("core", {}).get("prev_core_digest") if isinstance(record.get("core"), dict) else None
    try:
        fresh = ratify(prior_record, decision, authority_proof,
                       prev_core_digest=prev if isinstance(prev, str) else None)
    except RatifyError:
        return False
    return _ct_eq(fresh["core_digest"], claimed)
