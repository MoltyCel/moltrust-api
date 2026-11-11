"""Der Meldeweg von deploy.sh, Ende zu Ende geprueft.

Am 09.10.2026 ging deploy.sh von curl auf `python -m app.notify`. Die Pruefung
danach war die richtige — die Funktion wirklich aufrufen statt den Quelltext
lesen — und sie hat sofort gefunden, was dabei verloren ging: die alte Fassung
lud `~/.moltrust_secrets` selbst, bevor sie sendete, die neue nicht. notify las
den Token direkt aus os.environ, ohne den Rueckfall auf die Datei, den es fuer
die Chat-ID hat. Ergebnis: Chat-ID gefunden, Token nicht, "token/chat missing",
Meldung verloren. Ein gescheiterter Deploy haette nichts gemeldet.

deploy.sh laeuft als forced command ueber SSH. Dort ist von den Secrets nichts
in der Umgebung, also darf die Sendefunktion sich nicht darauf verlassen, dass
irgendwer sie vorher geladen hat.
"""
import pathlib
import re

import pytest

from app import notify

WURZEL = pathlib.Path(__file__).resolve().parent.parent
DEPLOY_SH = WURZEL / "ops" / "deploy" / "deploy.sh"


def test_token_kommt_auch_aus_der_datei(monkeypatch, tmp_path):
    """Nicht nur aus os.environ. _resolve ist fuer genau diese Aufrufer da."""
    datei = tmp_path / "secrets"
    datei.write_text('TELEGRAM_BOT_TOKEN="aus-der-datei"\n'
                     "TELEGRAM_CHAT_ID=4711\n", encoding="utf-8")
    monkeypatch.setenv("MOLTRUST_SECRETS_FILE", str(datei))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    notify._ENV_CACHE.clear()

    assert notify._resolve("TELEGRAM_BOT_TOKEN") == "aus-der-datei"


def test_senden_gelingt_ohne_token_in_der_umgebung(monkeypatch, tmp_path):
    """Die Gegenprobe zum Befund: Token nur in der Datei, und es geht raus."""
    datei = tmp_path / "secrets"
    datei.write_text('TELEGRAM_BOT_TOKEN="t"\nTELEGRAM_CHAT_ID=4711\n'
                     "MOLTRUST_NOTIFY=on\n", encoding="utf-8")
    monkeypatch.setenv("MOLTRUST_SECRETS_FILE", str(datei))
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "MOLTRUST_NOTIFY"):
        monkeypatch.delenv(name, raising=False)
    for kanal in notify.CHANNELS:
        monkeypatch.delenv(f"TELEGRAM_CHAT_ID_{kanal.upper()}", raising=False)
    notify._ENV_CACHE.clear()

    gerufen = []

    class _R:
        status_code = 200
        content = b""

        @staticmethod
        def json():
            return {"result": {"message_id": 1}}

    def _post(url, **kw):
        gerufen.append((url, kw))
        return _R()

    monkeypatch.setattr(notify.requests, "post", _post)

    assert notify.send_telegram("probe", channel=notify.ALERTS) is True
    assert len(gerufen) == 1
    assert "bott/sendMessage" in gerufen[0][0], gerufen[0][0]


def test_deploy_sh_laedt_die_secrets_selbst():
    """Die Sendefunktion ist fuer sich genommen vollstaendig.

    Als forced command ueber SSH hat sie keine geladene Umgebung. Sich darauf
    zu verlassen, dass der Aufrufer sie mitbringt, ist der Fehler vom
    09.10.2026.
    """
    quelle = DEPLOY_SH.read_text(encoding="utf-8")
    m = re.search(r"^telegram\(\) \{(.*?)^\}", quelle, re.S | re.M)
    assert m, "telegram() nicht gefunden"
    rumpf = m.group(1)
    assert ".moltrust_secrets" in rumpf, \
        "telegram() laedt die Secrets nicht und verliert den Token ueber SSH"
    assert "app.notify" in rumpf, "telegram() geht nicht ueber notify"


def test_deploy_sh_wertet_den_rueckgabewert_aus():
    """Die Exitcodes trennen gesendet, gedrosselt und gescheitert.

    Ohne das steht im Konsolenprotokoll nur, dass gesendet wurde — und eine
    verlorene Meldung sieht aus wie eine zugestellte. Genau so ist der Befund
    vom 09.10. ueberhaupt sichtbar geworden.
    """
    quelle = DEPLOY_SH.read_text(encoding="utf-8")
    m = re.search(r"^telegram\(\) \{(.*?)^\}", quelle, re.S | re.M)
    rumpf = m.group(1)
    import re as _re
    assert "rc=$?" in rumpf
    # Nach der Bedeutung geprueft, nicht nach der Schreibweise: seit dem
    # Abbruch-Haken teilen 5 und 70 einen Zweig, weil der Haken die 5 der CLI
    # mit MUTE_EXIT_CODE ueberschreibt. Ein Test auf das Literal "5)" war
    # genau an dieser Zusammenlegung rot geworden, ohne dass etwas fehlte.
    for code in (3, 4, 5, 70):
        assert _re.search(rf"(^|\||\s){code}(\||\))", rumpf, _re.M), \
            f"Exitcode {code} wird nicht behandelt"


@pytest.mark.parametrize("code,wort", [(3, "gate"), (4, "throttl"),
                                       (5, "report"), (70, "report")])
def test_jeder_grund_steht_im_konsolenprotokoll(code, wort):
    """Ein Exitcode allein sagt nicht, was fehlt.

    Der Zweig darf den Code mit anderen teilen — 5 und 70 bedeuten dasselbe,
    die Meldung ist weg. Gesucht wird der Zweig, der diesen Code faengt, und
    die Protokollzeile darin.
    """
    quelle = DEPLOY_SH.read_text(encoding="utf-8")
    m = re.search(rf"^\s*(?:\d+\|)*{code}(?:\|\d+)*\)\s*log \"([^\"]*)\"",
                  quelle, re.M)
    assert m, f"kein log fuer Exitcode {code}"
    assert wort in m.group(1).lower(), m.group(1)
