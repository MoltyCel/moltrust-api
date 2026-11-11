#!/usr/bin/env python3
"""Je Zweck ein Schlüssel, je Schlüssel die Leser, die in der Liste stehen.

Ops-Job S5. Die Liste ist `config/key_purposes.json`; sie nennt für jede
Schlüsselvariable die Dateien, die sie roh lesen dürfen, und für jeden Zweck
die Lesestelle, über die alle anderen gehen.

Geprüft wird dreierlei:

1. **Kein neuer Leser.** Ein Rohzugriff aus einer Datei, die nicht in der Liste
   steht, ist ein Befund. So bleibt die Antwort auf „wer liest diesen
   Schlüssel" eine Aufzählung und keine Suche.
2. **Kein toter Eintrag.** Eine Datei, die in der Liste steht und nicht mehr
   liest, ist ebenfalls ein Befund — sonst wächst die Liste zu einer
   Erlaubnismenge, die niemand mehr prüft, und die nächste Umstellung sieht
   eine Kopplung, die es nicht mehr gibt.
3. **Die Lesestelle existiert.** Wer `app/chain_keys.py:ipr_anchor_signer`
   umbenennt, ohne die Liste nachzuziehen, lässt eine Dokumentation zurück, die
   auf eine Funktion zeigt, die es nicht gibt.

Mit `--live` kommt die Messung gegen die laufende Umgebung dazu: ist die
Variable je Zweck gesetzt, und leitet der Kettenschlüssel die Adresse ab, die
die Liste nennt. Ausgegeben werden nur Namen und Adressen — ein Schlüsselwert
verlässt diese Datei nicht, auch nicht gekürzt.

    python3 scripts/key_purposes_check.py            # nur das Repo
    python3 scripts/key_purposes_check.py --live     # dazu die Umgebung

Exit 0 heißt sauber, 1 heißt Befund, 2 heißt die Prüfung selbst konnte nicht
laufen.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

CODE_SUFFIXES = (".py", ".js", ".ts", ".mjs", ".cjs", ".sh", ".bash")
# Prosa über einen Schlüssel ist kein Zugriff auf ihn. Diese Verzeichnisse
# halten Berichte, Prüfprotokolle und Spezifikationstexte; sie nennen die
# Variablennamen und lesen sie nicht.
SKIP_PREFIXES = ("docs/", "audits/", "reports/")


def tracked_files(root: str) -> list[str]:
    out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True,
                         text=True, check=True).stdout.splitlines()
    return [p for p in out
            if p.endswith(CODE_SUFFIXES) and not p.startswith(SKIP_PREFIXES)]


def read_patterns(var: str) -> re.Pattern:
    """Wie ein Programm eine Umgebungsvariable liest, nicht wie es sie nennt.

    `BASE_WRITE_KEY=` in einer .env-Vorlage ist eine Zuweisung, ein Satz in
    einem Kommentar ist Prosa; beides darf nicht als Leser zählen. Gesucht
    werden deshalb die Formen, mit denen Python, Node und die Shell lesen.
    """
    v = re.escape(var)
    return re.compile(
        r"(?:"
        rf"""os\.getenv\(\s*["']{v}["']"""
        rf"""|os\.environ\.get\(\s*["']{v}["']"""
        rf"""|os\.environ\[\s*["']{v}["']\s*\]"""
        rf"""|environ\.get\(\s*["']{v}["']"""
        rf"""|getenv\(\s*["']{v}["']"""
        rf"""|process\.env\.{v}\b"""
        rf"""|process\.env\[\s*["']{v}["']\s*\]"""
        rf"""|loadSecret\(\s*["']{v}["']"""
        rf"""|\$\{{{v}\}}"""
        rf"""|\${v}\b"""
        r")"
    )


def readers_in_tree(root: str, variables: list[str]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {v: set() for v in variables}
    patterns = {v: read_patterns(v) for v in variables}
    for rel in tracked_files(root):
        try:
            text = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for var, pat in patterns.items():
            if pat.search(text):
                found[var].add(rel)
    return found


def accessor_findings(root: str, config: dict) -> list[str]:
    out = []
    for name, purpose in config["zwecke"].items():
        where = purpose.get("zugang") or ""
        if ":" not in where:
            out.append(f"{name}: Zugang ohne Datei:Funktion ({where!r})")
            continue
        path, func = where.rsplit(":", 1)
        full = os.path.join(root, path)
        if not os.path.exists(full):
            out.append(f"{name}: Zugang {path} gibt es nicht")
            continue
        text = open(full, encoding="utf-8", errors="replace").read()
        if not re.search(rf"^\s*(?:async )?def {re.escape(func)}\s*\(", text, re.M):
            out.append(f"{name}: {path} hat keine Funktion {func}")
    return out


def findings(root: str, config: dict) -> list[str]:
    allowed: dict[str, dict[str, str]] = {v: dict(files) for v, files
                                          in config["leser"].items()}
    for var in config.get("ausgelaufen", {}):
        allowed.setdefault(var, {})
    found = readers_in_tree(root, sorted(allowed))

    out = []
    for var in sorted(allowed):
        new = found[var] - set(allowed[var])
        for path in sorted(new):
            out.append(f"{var}: neuer Leser {path} steht nicht in config/key_purposes.json")
        stale = set(allowed[var]) - found[var]
        for path in sorted(stale):
            out.append(f"{var}: {path} steht in der Liste und liest nicht mehr")
    out.extend(accessor_findings(root, config))
    return out


def _kms_finding(name: str, purpose: dict, addr: str) -> list[str]:
    """A managed signer (`"signierer": "kms"`): GetPublicKey via the accessor."""
    if any(not os.environ.get(v, "").strip() for v in purpose["variablen"]):
        return []  # already reported as unset above
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    path, func = purpose["zugang"].rsplit(":", 1)
    try:
        import importlib
        accessor = getattr(importlib.import_module(path[:-3].replace("/", ".")), func)
        got = accessor().address
    except Exception as exc:
        return [f"{name}: {func} failed ({type(exc).__name__})"]
    if got.lower() != addr.lower():
        return [f"{name}: KMS key derives {got}, the list says {addr}"]
    print(f"ok {name}: kms -> {got}")
    return []


def live_findings(config: dict) -> list[str]:
    """Die Umgebung, in der der Dienst läuft, gegen die Liste. Keine Werte."""
    out = []
    for name, purpose in config["zwecke"].items():
        for var in purpose["variablen"]:
            if not os.environ.get(var, "").strip():
                out.append(f"{name}: {var} ist in dieser Umgebung nicht gesetzt")
    expected = {}
    for name, purpose in config["zwecke"].items():
        addr = purpose.get("adresse") or ""
        if not addr.startswith("0x"):
            continue
        if purpose.get("signierer") == "kms":
            # No key in the environment to derive from: ask the accessor, which
            # derives the address from the KMS public key. Read-only, signs nothing.
            out.extend(_kms_finding(name, purpose, addr))
            continue
        expected[name] = (purpose["variablen"][0], addr)
    if not expected:
        return out
    try:
        from eth_account import Account
    except ImportError:
        out.append("eth_account fehlt, die Adressprobe konnte nicht laufen")
        return out
    for name, (var, addr) in sorted(expected.items()):
        value = os.environ.get(var, "").strip()
        if not value:
            continue
        try:
            got = Account.from_key(value).address
        except Exception:
            out.append(f"{name}: {var} ist kein brauchbarer Schlüssel")
            continue
        if got.lower() != addr.lower():
            out.append(f"{name}: {var} leitet {got} ab, die Liste nennt {addr}")
        else:
            print(f"ok {name}: {var} -> {got}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
    ap.add_argument("--live", action="store_true",
                    help="dazu die Umgebung dieses Prozesses prüfen")
    args = ap.parse_args()

    path = os.path.join(args.root, "config", "key_purposes.json")
    try:
        config = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"config/key_purposes.json: {exc}", file=sys.stderr)
        return 2

    problems = findings(args.root, config)
    if args.live:
        problems += live_findings(config)

    for p in problems:
        print(p)
    zwecke = len(config["zwecke"])
    leser = sum(len(f) for f in config["leser"].values())
    print(f"{zwecke} Zwecke, {leser} erlaubte Lesestellen, {len(problems)} Befunde")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
