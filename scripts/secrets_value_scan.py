#!/usr/bin/env python3
"""Where do secret values turn up outside the places that are meant to hold them.

Ops backlog S7, 2026-10-12. The existing checks look at the secrets file itself:
duplicate keys (c-secret-keys-unique) and plaintext copies of the whole file
(c-secrets-no-plaintext-copy). Neither looks for a single value that leaked into
a log line, the journal, a tracked file of one of our repositories, or a backup
archive. This does.

    python3 scripts/secrets_value_scan.py            # one line per finding place, total last
    python3 scripts/secrets_value_scan.py --since 2d # journal window (default 2d)

What counts as a secret value: every value in the secrets file whose name does not
mark it as public (addresses, chat ids, public and publishable keys), at least 16
characters long; plus the Anthropic key file and the PyPI token in ~/.pypirc.

Output: `<kind>\\t<place>\\t<count>` per place with a finding, then the total on
the last line. Never a value, never a key name, never a hash of a value. A source
that cannot be read prints UNREADABLE; the selftest runner treats that as FAIL.
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

HOME = Path(os.environ.get("SCAN_HOME", str(Path.home())))
SECRETS_FILE = Path(os.environ.get("SECRETS_FILE", str(HOME / ".moltrust_secrets")))

MIN_LEN = 16
MAX_FILE = 20 * 1024 * 1024
# Names whose values are public by construction. URLs are deliberately not here:
# a healthcheck ping URL or a share link carries its token in the path.
# Measured on the first run (12.10.2026): 1236 of 2347 hits were MOLTGUARD_V1_DID
# and 80 SMTP_HOST, both public.
PUBLIC_NAME = re.compile(r"(ADDR|ADDRESS|CHAT_ID|PUBLIC|PUBLISHABLE|_KID$|_NETWORK$|_CHAIN|_REGION$|"
                         r"_PRICE_|_LOOKUP|_AGENT_ID$|_TX_HASH$|_USERNAME$|_USER$|_EMAIL$|_DOMAIN$|"
                         r"_DID$|_HOST$|_PORT$)")

# Places that are meant to hold the values. Everything else is a finding.
ALLOWED = {".moltrust_secrets", ".anthropic_key", ".pypirc"}
ALLOWED_RELATIVE = {"moltguard/.env"}

LOG_DIRS = ("moltstack/logs", "logs", "selftest")
SKIP_DIRS = {"node_modules", "venv", ".venv", "__pycache__", "site-packages", ".git"}


def secret_values() -> list[bytes]:
    values: set[str] = set()
    for line in SECRETS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if PUBLIC_NAME.search(name.strip()) or len(value) < MIN_LEN:
            continue
        values.add(value)
    key_file = HOME / ".anthropic_key"
    if key_file.exists():
        v = key_file.read_text(encoding="utf-8", errors="replace").strip()
        if len(v) >= MIN_LEN:
            values.add(v)
    pypirc = HOME / ".pypirc"
    if pypirc.exists():
        for line in pypirc.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("password"):
                v = line.split("=", 1)[-1].strip()
                if len(v) >= MIN_LEN:
                    values.add(v)
    return sorted(v.encode() for v in values)


def matcher(values: list[bytes]) -> re.Pattern:
    # Longest first, so a value that contains another is counted as itself.
    return re.compile(b"|".join(re.escape(v) for v in sorted(values, key=len, reverse=True)))


def count(pattern: re.Pattern, data: bytes) -> int:
    return sum(1 for _ in pattern.finditer(data))


def read_file(path: Path) -> bytes | None:
    try:
        if path.stat().st_size > MAX_FILE:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if path.suffix == ".gz" and not path.name.endswith((".tar.gz",)):
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw
    return raw


def allowed(path: Path) -> bool:
    if path.name in ALLOWED:
        return True
    try:
        rel = path.relative_to(HOME).as_posix()
    except ValueError:
        return False
    return rel in ALLOWED_RELATIVE


def scan_logs(pattern):
    for d in LOG_DIRS:
        base = HOME / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and not allowed(p):
                data = read_file(p)
                if data is not None:
                    n = count(pattern, data)
                    if n:
                        yield "log", str(p), n


def scan_journal(pattern, since: str):
    cmd = ["journalctl", "--since", f"-{since}" if not since.startswith("-") else since,
           "-o", "cat", "--no-pager"]
    for prefix in ([], ["sudo", "-n"]):
        try:
            p = subprocess.run(prefix + cmd, capture_output=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if p.returncode == 0:
            n = count(pattern, p.stdout)
            if n:
                yield "journal", f"journalctl --since -{since.lstrip('-')}", n
            return
    raise RuntimeError("journal unreadable")


def repos():
    for git_dir in sorted(HOME.glob("*/.git")) + sorted(HOME.glob("*/*/.git")):
        if any(part in SKIP_DIRS for part in git_dir.parent.relative_to(HOME).parts):
            continue
        yield git_dir.parent


def scan_repos(pattern):
    for repo in repos():
        try:
            files = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True,
                                   timeout=120).stdout.split(b"\0")
        except (OSError, subprocess.TimeoutExpired):
            continue
        for rel in files:
            if not rel:
                continue
            p = repo / rel.decode("utf-8", "replace")
            if p.is_file() and not allowed(p):
                data = read_file(p)
                if data is not None:
                    n = count(pattern, data)
                    if n:
                        yield "repo", str(p), n


def archives():
    seen = set()
    for pat in ("*.tar.gz", "*.tgz", "*.tar", "*.zip"):
        for depth in ("", "*/", "*/*/"):
            for p in HOME.glob(depth + pat):
                if p.is_file() and not any(part in SKIP_DIRS for part in p.parts) and p not in seen:
                    seen.add(p)
                    yield p


def scan_archives(pattern):
    for arc in sorted(archives()):
        try:
            if arc.suffix == ".zip":
                with zipfile.ZipFile(arc) as z:
                    for info in z.infolist():
                        if info.is_dir() or info.file_size > MAX_FILE:
                            continue
                        n = count(pattern, z.read(info))
                        if n:
                            yield "archive", f"{arc}!{info.filename}", n
            else:
                with tarfile.open(arc) as t:
                    for m in t:
                        if not m.isfile() or m.size > MAX_FILE:
                            continue
                        f = t.extractfile(m)
                        if f is None:
                            continue
                        n = count(pattern, f.read())
                        if n:
                            yield "archive", f"{arc}!{m.name}", n
        except (OSError, tarfile.TarError, zipfile.BadZipFile, EOFError):
            yield "unreadable-archive", str(arc), 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default="2d", help="journal window, e.g. 2d or 36h")
    ap.add_argument("--skip", action="append", default=[],
                    choices=["logs", "journal", "repos", "archives"])
    args = ap.parse_args(argv)
    try:
        values = secret_values()
    except OSError as exc:
        print(f"UNREADABLE secrets file: {type(exc).__name__}")
        return 0
    if not values:
        print("UNREADABLE secrets file holds no secret value")
        return 0
    pattern = matcher(values)
    total = 0
    scanners = {"logs": lambda: scan_logs(pattern), "journal": lambda: scan_journal(pattern, args.since),
                "repos": lambda: scan_repos(pattern), "archives": lambda: scan_archives(pattern)}
    for name, run in scanners.items():
        if name in args.skip:
            continue
        try:
            for kind, place, n in run():
                print(f"{kind}\t{place}\t{n}")
                total += n
        except RuntimeError as exc:
            print(f"UNREADABLE {name}: {exc}")
            return 0
    print(total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
