"""ops MELD-1: ein Bericht ohne Handgriff erreicht Lars nicht.

Vorgabe Lars 27.10.2026, Nachtrag 28.10.2026: Telegram nur noch, wenn Lars
konkret etwas entscheiden oder tun muss. Steht in der ersten Zeile
"Lars muss: nichts", bleibt der Bericht in seiner Detaildatei und in STATUS.md.

Gemessen war die Gegenprobe zweimal rot: in den Naechten des 28./29. und des
29./30.10.2026 stand im Sendeprotokoll je eine Zustellung der Sammelmeldung auf
dem STATS-Kanal, die wortgleich mit "Lars muss: nichts" begann.

Jeder Test traegt seinen Negativfall: eine Schwelle, die nur den Befund prueft,
ist geraten (Regel „Eine Schwelle braucht eine Schweigeprobe").
"""
import datetime as dt

import pytest

from app import notify

T0 = dt.datetime(2026, 10, 30, 20, 0, tzinfo=dt.timezone.utc)


# --- die Entscheidung selbst ------------------------------------------------

def test_ohne_handgriff_keine_zustellung():
    assert notify.braucht_zustellung("Lars muss: nichts\nSelbsttest: 2 Laeufe.") is False


def test_mit_handgriff_zustellung():
    assert notify.braucht_zustellung("Lars muss: Label lars-go setzen\nSelbsttest: 2 "
                                     "Laeufe.") is True


def test_der_satz_weiter_unten_entscheidet_nicht():
    """Nur die erste Zeile traegt den Handgriff.

    Ein Befundtext, der denselben Satz zitiert, darf eine faellige Zustellung
    nicht unterdruecken.
    """
    text = "Lars muss: den Schluessel freigeben\nSteht seit 28.10.: Lars muss: nichts"
    assert notify.braucht_zustellung(text) is True


def test_ein_einzeiler_ohne_zeilenumbruch_wird_gelesen():
    assert notify.braucht_zustellung("Lars muss: nichts") is False
    assert notify.braucht_zustellung("Lars muss: etwas tun") is True


# --- die Sammelmeldung benutzt sie ------------------------------------------

class _Body:
    def read(self):
        return b""


def _digest(monkeypatch, tmp_path, handgriff):
    """Die Sammelmeldung fahren, mit und ohne Handgriff in der ersten Zeile."""
    from scripts import selftest
    sent = []
    monkeypatch.setenv("MOLTRUST_NOTIFY_STATE_DIR", str(tmp_path / "selftest"))
    monkeypatch.setattr(selftest, "OUTDIR", str(tmp_path / "out"))
    monkeypatch.setattr(selftest.throttle, "state", lambda: {"gesendet": {}})
    monkeypatch.setattr(selftest.throttle, "due_digest_slot",
                        lambda now, g: ("2026-10-30T20", T0))
    monkeypatch.setattr(selftest.throttle, "save_state", lambda st: None)
    monkeypatch.setattr(selftest.notify, "stehende_befunde", lambda now: [])
    # Der Text kommt aus kurzfassung; hier zaehlt allein die erste Zeile.
    echt = selftest.kurzfassung

    def kurz(*a, **k):
        text = echt(*a, **k)
        if handgriff:
            rest = text.split("\n", 1)[1] if "\n" in text else ""
            text = "Lars muss: den Entwurf freigeben\n" + rest
        return text

    monkeypatch.setattr(selftest, "kurzfassung", kurz)

    def send(text, *, channel, **k):
        sent.append((text, channel, k))
        return True

    monkeypatch.setattr(selftest.notify, "send_telegram", send)
    monkeypatch.delenv("HEALTHCHECK_SELFTEST_DIGEST_URL", raising=False)
    monkeypatch.setattr(selftest.urllib.request, "urlopen",
                        lambda url, timeout=0: _Body())
    return selftest.send_digest(), sent


def test_die_sammelmeldung_ohne_handgriff_geht_nicht_raus(monkeypatch, tmp_path):
    rc, sent = _digest(monkeypatch, tmp_path, handgriff=False)
    assert rc == 0, "ohne Handgriff ist das kein Fehlschlag"
    assert sent == [], sent
    # Geschrieben wird sie trotzdem, sonst waere sie verloren.
    detail = tmp_path / "out" / "digest-2026-10-30T20.md"
    assert detail.exists() and detail.read_text().startswith("MolTrust — ")


def test_die_sammelmeldung_mit_handgriff_geht_raus(monkeypatch, tmp_path):
    rc, sent = _digest(monkeypatch, tmp_path, handgriff=True)
    assert rc == 0
    assert len(sent) == 1, sent
    text, channel, _k = sent[0]
    assert channel == notify.STATS
    assert text.startswith("Lars muss: den Entwurf freigeben")


def test_kurzfassung_ohne_befund_nennt_die_eine_zeile():
    """Die gebaute Zeile und die geprueft wird, sind dieselbe."""
    from scripts import selftest
    text = selftest.kurzfassung(T0, 2, 0, [], [], [], [], 0, [], "/tmp/d.md",
                                titel={})
    assert text.splitlines()[0] == notify.KEIN_HANDGRIFF
    assert notify.braucht_zustellung(text) is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
