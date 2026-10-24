"""The selftest digest on Telegram, in words (Lars, 2026-10-24).

First line "Lars muss: nichts" or the step with its link; then each finding
by its catalogue title with its owner. Check IDs and fingerprints stand only
in the detail file.
"""
import datetime as dt

from scripts import selftest

SLOT = dt.datetime(2026, 10, 24, 8, 0, tzinfo=dt.timezone.utc)
TITEL = {"c-context-urls-resolve": "Jede JSON-LD-Kontext-URL loest auf",
         "c-watchdog-threshold-fits-cron": "Die Schwelle der Wache passt zum Takt",
         "f-no-stale-escrow": "Kein Escrow bleibt liegen"}


def _text(**kw):
    args = dict(slot_at=SLOT, runs=13, neu=[], bekannt=[], offen=[], stehend=[],
                befunde=[], korrigiert=0, rest=[], detail="/d/digest-2026-10-24T08.md",
                titel=TITEL)
    args.update(kw)
    return selftest.kurzfassung(**args)


def test_a_quiet_digest_says_lars_has_nothing_to_do():
    text = _text()
    assert text.splitlines()[0] == "Lars muss: nichts"
    assert text.splitlines()[1] == "Selbsttest 24.10. 08:00Z: 13 Läufe, 0 neu, 0 stehend, 0 bekannt mit Termin."
    assert text.splitlines()[-1] == "Details: /d/digest-2026-10-24T08.md"


def test_a_finding_waiting_on_lars_puts_the_step_first():
    text = _text(befunde=[{"fp": "6927a30e8b9895df", "erstmals": "2026-10-23T13:37:22+00:00",
                           "anfang": "[FAIL] c-context-urls-resolve — ohne 'gleich '"}])
    first = text.splitlines()[0]
    assert first.startswith("Lars muss: Label lars-go an moltrust-web #308")
    assert "https://github.com/MoltyCel/moltrust-web/pull/308" in first
    assert ("Steht seit 23.10., nicht erfüllt: Jede JSON-LD-Kontext-URL loest auf "
            "(Job E3, wartet auf dich)") in text


def test_no_check_id_and_no_fingerprint_reaches_telegram():
    text = _text(neu=[("c-watchdog-threshold-fits-cron", "FAIL")],
                 stehend=[("f-no-stale-escrow", "FAIL", SLOT)],
                 befunde=[{"fp": "eb359f2f27b6d93a", "erstmals": "2026-10-20T20:55:01+00:00",
                           "anfang": ""},
                          {"fp": "6927a30e8b9895df", "erstmals": "2026-10-23T13:37:22+00:00",
                           "anfang": "[FAIL] c-context-urls-resolve — ohne 'gleich '"}],
                 rest=["Deploys 24 h — 16 ok, letzter d902ac9 07:57Z",
                       "  3x gesendet [alerts] [FAIL] c-context-urls-resolve — ohne",
                       "  1x gesendet [alerts] Fingerabdruck 3355b85a79d99f6d"])
    for k in list(TITEL) + ["eb359f2f", "6927a30e", "3355b85a"]:
        assert k not in text, k
    assert "Neu, nicht erfüllt: Die Schwelle der Wache passt zum Takt (die Console sieht nach)" in text
    assert "Steht noch, nicht erfüllt: Kein Escrow bleibt liegen (die Console sieht nach)" in text
    assert "Steht seit 20.10., eine ältere Meldung ohne gespeicherten Text" in text
    assert "Deploys 24 h — 16 ok, letzter d902ac9 07:57Z" in text
    assert "13 Läufe, 1 neu, 3 stehend" in text


def test_a_finding_named_twice_is_listed_once():
    text = _text(stehend=[("c-context-urls-resolve", "FAIL", SLOT)],
                 befunde=[{"fp": "a", "erstmals": "2026-10-23T13:37:22+00:00",
                           "anfang": "[FAIL] c-context-urls-resolve — ohne 'gleich '"}])
    assert text.count("Jede JSON-LD-Kontext-URL") == 1
    assert "1 stehend" in text


def test_a_finding_outside_the_catalogue_keeps_its_own_words():
    text = _text(befunde=[{"fp": "59b75bb7528fcbd3", "erstmals": "2026-10-24T11:05:01+00:00",
                           "anfang": "Web-Root: 1 Schreibvorgang/-vorgaenge ausserhalb deploy.sh in /var/www/html"}])
    assert "Steht seit 24.10., Web-Root: 1 Schreibvorgang/-vorgaenge ausserhalb deploy.sh " \
           "in /var/www/html (Job WRW-3)" in text
