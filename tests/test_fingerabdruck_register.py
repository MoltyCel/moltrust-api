"""Der Fingerabdruck eines Registerbefunds traegt nur den Befund.

Nicht das erwartete Gruen-Datum, nicht den Begruendungstext des Registers,
nicht den Zaehler. Alles drei sagt etwas ueber die Verwaltung des Befunds und
nichts darueber, ob er steht.

Belegt am 09.10.2026 an a-track-record-burst: dieselbe Invariante, derselbe
Wert, zwei Fingerabdruecke. Der zweite entstand um 08:37Z — elf Minuten
nachdem das korrigierte Gruen-Datum ausgerollt war. Die Korrektur des Datums
hat die Meldung also zweimal erneut durchgelassen.

Und die Gegenrichtung, die beim Bauen einen echten Defekt zeigte: zwei
Befunde mit verschiedenen DIDs ergaben denselben Abdruck, weil das Muster
"Git-Hash" den Hex-Teil einer DID frisst. Ein Befund ueber einen anderen
Agenten waere als Wiederholung des vorigen gedrosselt worden.
"""
import datetime as dt

import pytest

from app import notify as n

BEFUND = "[FAIL] a-track-record-burst — 1 (erwartet eq 0)"
UEBERFAELLIG = (BEFUND + " · UEBERFAELLIG: gruen erwartet war "
                "2026-10-09T02:33:55+00:00, der Zustand haelt an — das ist "
                "jetzt ein neuer Befund")
BEKANNT = (BEFUND + " · bekannter Zustand, gruen erwartet "
           "2026-10-10T16:22:20+00:00: Die drei Schleifen-DIDs stehen noch im "
           "Siebentagefenster. Entscheid Lars 3.10.")
MIT_ZAEHLER = BEFUND + "\n\n(24 Laeufe seit 09.10. 03:37Z, gleichlautend)"
MIT_ALTEM_ZAEHLER = BEFUND + "\n\n(3x seit 03:37Z, gleichlautend)"


@pytest.mark.parametrize("fassung,name", [
    (UEBERFAELLIG, "ueberfaellig"),
    (BEKANNT, "bekannter Zustand mit neuem Datum"),
    (MIT_ZAEHLER, "mit Laufzaehler"),
    (MIT_ALTEM_ZAEHLER, "mit altem x-Zaehler"),
])
def test_die_verwaltungsklausel_aendert_den_abdruck_nicht(fassung, name):
    assert n.fingerprint(fassung) == n.fingerprint(BEFUND), (
        f"{name} ergibt einen anderen Fingerabdruck:\n"
        f"  {n._normalise(fassung)!r}\n  {n._normalise(BEFUND)!r}")


def test_altes_und_neues_gruen_datum_sind_ein_abdruck():
    """Die Gegenprobe des Auftrags, wortwoertlich."""
    assert n.fingerprint(UEBERFAELLIG) == n.fingerprint(BEKANNT)


def test_andere_did_ergibt_einen_neuen_abdruck():
    """Die zweite Gegenprobe — und der Defekt, den sie gefunden hat.

    `_VOLATIL` enthaelt ("Git-Hash", r"\\b[0-9a-f]{7,40}\\b"). Das trifft
    `899e04aa60954055` in `did:moltrust:899e04aa60954055` genauso wie einen
    Commit. Ohne Schutz fielen alle DIDs auf dasselbe Zeichen zusammen und ein
    Befund ueber einen anderen Agenten galt als Wiederholung.
    """
    a = BEFUND + ", DID did:moltrust:899e04aa60954055"
    b = BEFUND + ", DID did:moltrust:fe06d95a11cc462b"
    assert n.fingerprint(a) != n.fingerprint(b)
    # Und beide unterscheiden sich vom Befund ohne DID.
    assert n.fingerprint(a) != n.fingerprint(BEFUND)


def test_andere_adresse_ergibt_einen_neuen_abdruck():
    a = "[WARN] wallet-drift — 0x3b076c41d775813b3341b81cedC877e7c7025f45"
    b = "[WARN] wallet-drift — 0x5Fa91DB83039C803Be5951ED8898AB23E5C7F342"
    assert n.fingerprint(a) != n.fingerprint(b)


def test_mehrere_dids_in_einer_meldung_zaehlen_als_menge():
    """Die Reihenfolge darf nicht zaehlen, die Menge schon."""
    a = BEFUND + " did:moltrust:aaaa1111bbbb2222 did:moltrust:cccc3333dddd4444"
    b = BEFUND + " did:moltrust:cccc3333dddd4444 did:moltrust:aaaa1111bbbb2222"
    c = BEFUND + " did:moltrust:aaaa1111bbbb2222"
    assert n.fingerprint(a) == n.fingerprint(b), "Reihenfolge zaehlt mit"
    assert n.fingerprint(a) != n.fingerprint(c), "eine DID weniger faellt nicht auf"


def test_der_befund_selbst_bleibt_unterschieden():
    """Was NICHT herausfallen darf."""
    assert n.fingerprint("[FAIL] inv — 1") != n.fingerprint("[WARN] inv — 1")
    assert n.fingerprint("[FAIL] inv — 1") != n.fingerprint("[FAIL] inv — 2")
    assert n.fingerprint("[FAIL] a — 1") != n.fingerprint("[FAIL] b — 1")


def test_der_git_hash_faellt_weiter_heraus():
    """Das Muster bleibt, es wird nur nicht mehr auf Identitaeten angewandt."""
    a = "[WARN] versioned-artifacts — gleich 9 Artefakte @ 3c9e842"
    b = "[WARN] versioned-artifacts — gleich 9 Artefakte @ 76fc88a"
    assert n.fingerprint(a) == n.fingerprint(b)


def test_identitaeten_werden_benannt():
    wer = n.identitaeten(
        "did:moltrust:899e04aa60954055 und 0x3b076c41d775813b3341b81cedC877e7c7025f45")
    assert len(wer) == 2
    assert any(w.startswith("did:") for w in wer)
    assert any(w.startswith("0x") for w in wer)


def test_jeder_volatil_eintrag_ist_benannt():
    """Als Liste mit Namen, damit jeder Eintrag einzeln begruendet ist."""
    for eintrag in n._VOLATIL:
        assert len(eintrag) == 2, eintrag
        name, muster = eintrag
        assert isinstance(name, str) and name.strip(), eintrag
        assert isinstance(muster, str) and muster.strip(), eintrag
    namen = [k for k, _ in n._VOLATIL]
    assert len(namen) == len(set(namen)), f"doppelte Namen: {namen}"
    # Die drei aus dem Auftrag vom 09.10.
    assert any("Registerklausel" in k for k in namen)
    assert any("Zaehler" in k for k in namen)


# -- das Sendeprotokoll ------------------------------------------------------

def test_ein_gedrosselter_befund_steht_im_sendeprotokoll(tmp_path, monkeypatch):
    """Eine Zeile je Versuch, auch bei Unterdrueckung.

    send_befunde drosselt je Befund und filtert, bevor _deliver_telegram
    laeuft — und dort sass die Protokollzeile. Vom 09.10. 10:37Z an stand
    deshalb nichts mehr ueber a-track-record-burst im Protokoll, obwohl der
    Befund in jedem Lauf vorlag: die Grundgesamtheit sah nach 5 aus und war 13.
    """
    import json

    log = tmp_path / "sent.jsonl"
    monkeypatch.setattr(n, "SENT_LOG", str(log))
    monkeypatch.setattr(n, "FINGERPRINTS", str(tmp_path / "fp.json"))
    monkeypatch.setenv("MOLTRUST_NOTIFY", "on")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:" + "a" * 32)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    for k in n.CHANNELS:
        monkeypatch.delenv(f"TELEGRAM_CHAT_ID_{k.upper()}", raising=False)
    n._ENV_CACHE.clear()

    class _R:
        status_code = 200
        content = b""

        @staticmethod
        def json():
            return {"result": {"message_id": 1}}

    monkeypatch.setattr(n.requests, "post", lambda url, **kw: _R())

    befunde = [("a-track-record-burst", BEFUND)]
    erst = n.send_befunde(befunde, channel=n.ALERTS)
    assert erst["gesendet"] == ["a-track-record-burst"]

    zweit = n.send_befunde(befunde, channel=n.ALERTS)
    assert zweit["gedrosselt"] == ["a-track-record-burst"]

    saetze = [json.loads(z) for z in log.read_text(encoding="utf-8").splitlines()]
    assert len(saetze) == 2, saetze
    assert saetze[0]["erfolg"] is True
    assert saetze[1]["erfolg"] is False
    assert saetze[1]["grund"] == "gedrosselt"
    assert saetze[0]["fingerabdruck"] == saetze[1]["fingerabdruck"]
