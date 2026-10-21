#!/usr/bin/env python3
"""Guard for ops KEY: no write key may reach the MoltGuard build that still signs with cast.

The deployed MoltGuard build (main c955a08 and older) anchors a paid skill
credential by running `cast send --private-key <BASE_WRITE_KEY>`, which puts the
key into the process list for as long as cast runs. The replacement
(moltguard#63, in-process signing) is merged and ships with the deploy
workflow (ops D3).

Until it runs in production this guard turns red as soon as a write key is set
where that build reads it:

  1. the environment of the running MoltGuard process (/proc/<pid>/environ:
     systemd Environment= and anything inherited), and
  2. ~/moltguard/.env, which dotenv loads into process.env at start. It is not
     visible in /proc, and it would arm the path at the next restart.

It only says that a value is there, never the value, not even a prefix or a hash.

The guard retires itself: once the deployed build no longer contains the cast
path, it prints "moltguard-write-key=ok entfaellt ..." and can be removed.

Prints one line per source and a closing line "moltguard-write-key=ok ..." or
"moltguard-write-key=FAIL ...". A source that cannot be read prints
"UNREADABLE ...", which the selftest runner treats as red. Exit 0 ok, 1 red,
2 unreadable.
"""
from __future__ import annotations

import argparse
import os
import sys

NAMES = ("BASE_WRITE_KEY",)
HOME = "/home/moltstack/moltguard"
ENTRY = f"{HOME}/dist/index.js"
DIST_SKILL = f"{HOME}/dist/services/skill.js"
DOTENV = f"{HOME}/.env"
CAST_MARK = b"--private-key"


class Unreadable(Exception):
    pass


def find_pids(proc: str, entry: str) -> list[int]:
    pids = []
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, "cmdline"), "rb") as f:
                argv = f.read().split(b"\0")
        except OSError:
            continue
        if entry.encode() in argv:
            pids.append(int(name))
    return sorted(pids)


def environ_names_set(proc: str, pid: int) -> set[str]:
    """Names from NAMES with a non-empty value in the process environment."""
    try:
        with open(os.path.join(proc, str(pid), "environ"), "rb") as f:
            raw = f.read()
    except OSError as e:
        raise Unreadable(f"/proc/{pid}/environ: {type(e).__name__}") from e
    found = set()
    for item in raw.split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep and k.decode(errors="replace") in NAMES and v.strip():
            found.add(k.decode())
    return found


def dotenv_names_set(path: str) -> set[str]:
    """Names from NAMES with a non-empty value in a dotenv file (dotenv's own
    rules: optional `export`, optional quotes, `#` comments)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return set()
    except OSError as e:
        raise Unreadable(f"{path}: {type(e).__name__}") from e
    found = set()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[len("export "):].lstrip()
        k, sep, v = s.partition("=")
        k, v = k.strip(), v.strip()
        if not sep or k not in NAMES:
            continue
        if v[:1] in ("'", '"') and v[-1:] == v[:1] and len(v) >= 2:
            v = v[1:-1]
        elif "#" in v:
            v = v.split("#", 1)[0]
        if v.strip():
            found.add(k)
    return found


def cast_path_deployed(dist_skill: str) -> bool:
    try:
        with open(dist_skill, "rb") as f:
            return CAST_MARK in f.read()
    except OSError as e:
        raise Unreadable(f"{dist_skill}: {type(e).__name__}") from e


def run(proc: str = "/proc", entry: str = ENTRY, dotenv: str = DOTENV,
        dist_skill: str = DIST_SKILL) -> tuple[int, list[str]]:
    try:
        if not cast_path_deployed(dist_skill):
            return 0, [f"build {dist_skill} ohne cast-Pfad",
                       "moltguard-write-key=ok entfaellt: der neue Stand laeuft, Pruefung kann raus"]
        pids = find_pids(proc, entry)
        if not pids:
            return 2, [f"UNREADABLE kein laufender MoltGuard-Prozess mit {entry}"]
        lines, fails = [f"build {dist_skill} mit cast-Pfad (--private-key)"], []
        for pid in pids:
            names = environ_names_set(proc, pid)
            lines.append(f"prozess {pid} umgebung: " + (", ".join(sorted(names)) + " gesetzt" if names else "kein Schreibschluessel"))
            if names:
                fails.append(f"{','.join(sorted(names))} im laufenden MoltGuard-Prozess {pid} gesetzt")
        names = dotenv_names_set(dotenv)
        lines.append(f"{dotenv}: " + (", ".join(sorted(names)) + " gesetzt" if names else "kein Schreibschluessel"))
        if names:
            fails.append(f"{','.join(sorted(names))} in {dotenv} gesetzt (laedt dotenv beim Start)")
    except Unreadable as e:
        return 2, [f"UNREADABLE {e}"]
    if fails:
        lines.append("moltguard-write-key=FAIL " + "; ".join(fails)
                     + " -- der alte cast-Weg (skill.ts:997, cast send --private-key) ist damit scharf:"
                       " jede bezahlte Skill-Ausstellung legt den Schluessel in die Prozessliste")
        return 1, lines
    lines.append(f"moltguard-write-key=ok kein Schreibschluessel ({len(pids)} Prozess, .env), cast-Pfad ruht")
    return 0, lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proc", default="/proc")
    ap.add_argument("--entry", default=ENTRY, help="Fehlerprobe: anderen Prozess pruefen")
    ap.add_argument("--dotenv", default=DOTENV, help="Fehlerprobe: andere .env pruefen")
    ap.add_argument("--dist-skill", default=DIST_SKILL)
    a = ap.parse_args(argv)
    code, lines = run(a.proc, a.entry, a.dotenv, a.dist_skill)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
