import datetime as dt
import os

from scripts import nginx_state as ns

T0 = dt.datetime(2026, 10, 10, 9, 0, tzinfo=dt.timezone.utc)


def _etc(tmp_path, extra=None):
    etc = tmp_path / "nginx"
    (etc / "sites-enabled").mkdir(parents=True)
    (etc / "snippets").mkdir()
    (etc / "snippets" / "deny-backups.conf").write_text("location ~ \\.bak$ { return 404; }\n")
    (etc / "nginx.conf").write_text(
        f"http {{\n  include {etc}/sites-enabled/*;\n}}\n")
    (etc / "sites-enabled" / "default").write_text(
        "limit_req_zone $binary_remote_addr zone=trouvart_api:10m rate=60r/m;\n"
        "server {\n  include snippets/deny-backups.conf;\n}\n")
    for name, body in (extra or {}).items():
        (etc / "sites-enabled" / name).write_text(body)
    old = (T0 - dt.timedelta(hours=1)).timestamp()
    for root, _, files in os.walk(etc):
        for f in files:
            os.utime(os.path.join(root, f), (old, old))
    return str(etc)


def test_clean_config_has_no_findings(tmp_path):
    assert ns.findings(_etc(tmp_path), T0) == []


def test_backup_in_sites_enabled_and_the_duplicate_zone_are_named(tmp_path):
    etc = _etc(tmp_path)
    body = open(os.path.join(etc, "sites-enabled", "default")).read()
    etc = _etc(tmp_path / "b", {"default.bak-contexts-20261010T084124Z": body})
    f = ns.findings(etc, T0)
    assert any("Sicherung in sites-enabled/" in x for x in f)
    assert any("limit_req_zone trouvart_api doppelt" in x for x in f)


def test_missing_include_is_named(tmp_path):
    etc = _etc(tmp_path, {"extra": "server {\n  include snippets/nope.conf;\n}\n"})
    assert any("include fehlt: snippets/nope.conf" in x for x in ns.findings(etc, T0))


def test_file_changed_after_last_load_is_named(tmp_path):
    etc = _etc(tmp_path)
    later = (T0 + dt.timedelta(minutes=5)).timestamp()
    os.utime(os.path.join(etc, "snippets", "deny-backups.conf"), (later, later))
    f = ns.findings(etc, T0)
    assert any("snippets/deny-backups.conf" in x and "geaendert" in x for x in f)


def test_same_second_as_the_reload_is_not_a_finding(tmp_path):
    etc = _etc(tmp_path)
    same = (T0 + dt.timedelta(seconds=1)).timestamp()
    os.utime(os.path.join(etc, "sites-enabled", "default"), (same, same))
    assert ns.findings(etc, T0) == []


def test_without_sudo_the_line_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "sudo_nginx_t", lambda: (None, "sudo nicht erlaubt"))
    monkeypatch.setattr(ns, "loaded_at", lambda: T0)
    out = ns.lines(_etc(tmp_path))
    assert out == ["nginx -t: ohne Root nicht moeglich, lesend geprueft — keine Befunde"]


def test_sudo_failure_is_reported_with_the_emerg_line(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "sudo_nginx_t", lambda: (False, "[emerg] zone trouvart_api is already bound"))
    monkeypatch.setattr(ns, "loaded_at", lambda: T0)
    assert ns.lines(_etc(tmp_path))[0].startswith("nginx -t: FEHLER — [emerg]")
