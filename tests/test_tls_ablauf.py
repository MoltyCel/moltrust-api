"""TLS days left for moltrust.ch and api.moltrust.ch (ops SSL-1110).

Origin: Lars 10.11.2026, the admin card "SSL Days" showed "?". The old reader
called `_dt.strptime` on the datetime module, raised AttributeError inside a
bare except and returned None. Nothing here touches the network: the fetch is
a stub.
"""
import datetime as dt

from app import tls_ablauf

NOW = dt.datetime(2026, 11, 10, 14, 0, tzinfo=dt.timezone.utc)
JAN14 = dt.datetime(2027, 1, 14, 20, 9, 42, tzinfo=dt.timezone.utc)


def stub(table):
    def abrufen(host):
        v = table[host]
        if isinstance(v, Exception):
            raise v
        return v
    return abrufen


def test_both_hosts_are_read():
    assert tls_ablauf.HOSTS == ("moltrust.ch", "api.moltrust.ch")
    stand = tls_ablauf.lesen(now=NOW, abrufen=stub({"moltrust.ch": JAN14, "api.moltrust.ch": JAN14}))
    assert stand["moltrust.ch"]["tage"] == 65 and stand["api.moltrust.ch"]["tage"] == 65
    assert tls_ablauf.kleinste(stand) == 65


def test_the_card_shows_the_fewer_days():
    soon = NOW + dt.timedelta(days=9, hours=1)
    stand = tls_ablauf.lesen(now=NOW, abrufen=stub({"moltrust.ch": JAN14, "api.moltrust.ch": soon}))
    assert tls_ablauf.kleinste(stand) == 9


def test_an_unreadable_host_is_a_reason_and_no_number():
    stand = tls_ablauf.lesen(now=NOW, abrufen=stub({"moltrust.ch": JAN14,
                                                    "api.moltrust.ch": TimeoutError()}))
    assert stand["api.moltrust.ch"] == {"fehler": "TimeoutError"}
    assert tls_ablauf.kleinste(stand) is None


def test_lines_for_the_shell_trigger_and_quiet():
    soon = NOW + dt.timedelta(days=9, hours=1)
    stand = tls_ablauf.lesen(now=NOW, abrufen=stub({"moltrust.ch": JAN14, "api.moltrust.ch": soon}))
    z = tls_ablauf.zeilen(stand, 14)
    assert z[0].startswith("OK moltrust.ch 65 2027-01-14")
    assert z[1].startswith("KURZ api.moltrust.ch 9 ")
    # Quiet: with a threshold below both, no KURZ line.
    assert all(l.startswith("OK ") for l in tls_ablauf.zeilen(stand, 9))


def test_the_old_reader_is_gone_from_main():
    src = open("app/main.py", encoding="utf-8").read()
    assert "_dt.strptime(exp_str" not in src
    assert '"openssl", "s_client"' not in src
