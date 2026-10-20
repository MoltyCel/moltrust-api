"""nginx configuration state for the 08:00 report (since 2026-10-10).

A configuration nginx cannot load stays unnoticed while the running master
keeps the old one in memory; it shows on the next restart or reboot. On
2026-10-10 two backups in sites-enabled/ (nginx reads that directory whole)
put limit_req_zone trouvart_api in three times, and nginx -t failed.

`nginx -t` itself needs root: it opens the private keys under
/etc/letsencrypt/live. When `sudo -n /usr/sbin/nginx -t` is allowed, that is
the check. Otherwise the line says so and runs what a non-root user can read:

  - files in sites-enabled/ that look like backups (WORKFLOW 11.9),
  - http-level names defined twice across everything nginx reads whole
    (limit_req_zone/limit_conn_zone zones, upstream, proxy_cache_path keys_zone,
    map targets): each one is an [emerg],
  - include targets that do not exist,
  - files changed after the running master last loaded its configuration.

These read checks cover the failure of 2026-10-10. They are no full nginx -t.
"""
from __future__ import annotations

import datetime as dt
import glob
import os
import re
import subprocess

ETC = "/etc/nginx"
BACKUP_NAME = re.compile(
    r"(\.bak|\.orig|\.old|\.save|\.backup|\.swp|\.tmp|\.dpkg-\w+|\.ucf-\w+|~)"
    r"|bak[-_.]?\d{6,}|\d{8}T\d{6}Z$", re.I)
DEFINES = [
    (re.compile(r"^\s*limit_req_zone\s.*\bzone=([\w-]+)"), "limit_req_zone"),
    (re.compile(r"^\s*limit_conn_zone\s.*\bzone=([\w-]+)"), "limit_conn_zone"),
    (re.compile(r"^\s*proxy_cache_path\s.*\bkeys_zone=([\w-]+)"), "keys_zone"),
    (re.compile(r"^\s*upstream\s+([\w.-]+)\s*\{"), "upstream"),
    (re.compile(r"^\s*map\s+\S+\s+(\$\w+)\s*\{"), "map"),
]
INCLUDE = re.compile(r"^\s*include\s+([^;\s]+)\s*;")


def read_files(etc: str) -> list[str]:
    """What nginx reads whole: nginx.conf, conf.d/*.conf, sites-enabled/*."""
    files = [os.path.join(etc, "nginx.conf")]
    files += sorted(glob.glob(os.path.join(etc, "conf.d", "*.conf")))
    files += sorted(glob.glob(os.path.join(etc, "sites-enabled", "*")))
    return [f for f in files if os.path.isfile(f)]


def _lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [ln.split("#", 1)[0] for ln in fh]
    except OSError:
        return []


def findings(etc: str = ETC, loaded_at: dt.datetime | None = None) -> list[str]:
    out = []
    for f in sorted(glob.glob(os.path.join(etc, "sites-enabled", "*"))):
        if BACKUP_NAME.search(os.path.basename(f)):
            out.append(f"Sicherung in sites-enabled/ (wird mitgelesen): {os.path.basename(f)}")
    seen: dict[tuple[str, str], str] = {}
    newest = []
    for f in read_files(etc):
        newest.append(f)
        for ln in _lines(f):
            for rx, kind in DEFINES:
                m = rx.match(ln)
                if not m:
                    continue
                key = (kind, m.group(1))
                if key in seen:
                    out.append(f"{kind} {m.group(1)} doppelt: {os.path.relpath(seen[key], etc)}"
                               f" und {os.path.relpath(f, etc)}")
                else:
                    seen[key] = f
            m = INCLUDE.match(ln)
            if m:
                target = m.group(1)
                target = target if target.startswith("/") else os.path.join(etc, target)
                hits = glob.glob(target)
                if not hits and not any(c in target for c in "*?["):
                    out.append(f"include fehlt: {m.group(1)} (in {os.path.relpath(f, etc)})")
                newest += [h for h in hits if os.path.isfile(h)]
    if loaded_at:
        for f in sorted(set(newest)):
            try:
                mt = dt.datetime.fromtimestamp(os.stat(f).st_mtime, dt.timezone.utc)
            except OSError:
                continue
            # a file written in the same second as the reload is the reload's input
            if mt > loaded_at + dt.timedelta(seconds=2):
                out.append(f"geaendert nach dem letzten Laden ({loaded_at:%d.%m. %H:%M}Z):"
                           f" {os.path.relpath(f, etc)} {mt:%d.%m. %H:%M}Z")
    return out


def loaded_at() -> dt.datetime | None:
    """When the running master last read its config: last reload, else start."""
    try:
        r = subprocess.run(["systemctl", "show", "nginx", "-p", "ExecReload",
                            "-p", "ExecMainStartTimestampMonotonic", "-p", "ExecMainStartTimestamp"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    times = []
    m = re.search(r"start_time=\[\w{3} (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC\]", r.stdout)
    if m:
        times.append(m.group(1))
    m = re.search(r"^ExecMainStartTimestamp=\w{3} (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC", r.stdout, re.M)
    if m:
        times.append(m.group(1))
    if not times:
        return None
    return max(dt.datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
               for t in times)


def sudo_nginx_t() -> tuple[bool | None, str]:
    """(ok, last line) from sudo -n nginx -t; ok is None if sudo is not allowed."""
    try:
        r = subprocess.run(["sudo", "-n", "/usr/sbin/nginx", "-t"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, type(exc).__name__
    err = (r.stderr or "").strip().splitlines()
    if r.returncode != 0 and any("password is required" in e or "not allowed" in e for e in err):
        return None, "sudo nicht erlaubt"
    emerg = [e for e in err if "[emerg]" in e]
    return r.returncode == 0, (emerg[0] if emerg else (err[-1] if err else ""))[:160]


def lines(etc: str = ETC) -> list[str]:
    ok, msg = sudo_nginx_t()
    f = findings(etc, loaded_at())
    if ok is True:
        head = "nginx -t: ok"
    elif ok is False:
        head = f"nginx -t: FEHLER — {msg}"
    else:
        head = "nginx -t: ohne Root nicht moeglich, lesend geprueft"
    head += f" — {len(f)} Befund(e)" if f else " — keine Befunde"
    return [head] + [f"  {x}" for x in f[:8]]


if __name__ == "__main__":
    print("\n".join(lines()))
