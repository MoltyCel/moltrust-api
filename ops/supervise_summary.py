"""Render a selftest JSON as a GitHub job summary. Formatting only.

Its own file because the alternative is a heredoc inside a YAML block scalar,
and that is how the workflow stopped parsing the first time.
"""
import json
import sys

LAMP = {"green": "✅", "yellow": "⚠️", "red": "❌"}
RANK = {"red": 0, "yellow": 1, "green": 2}


def main() -> int:
    try:
        doc = json.load(open(sys.argv[1]))
    except Exception as e:
        print(f"Antwort nicht lesbar: {type(e).__name__}: {e}")
        print("\nDas ist selbst ein Befund — der Server hat etwas geschickt, "
              "das kein Selbsttest-JSON ist.")
        return 0
    findings = doc.get("findings") or []
    counts = {k: sum(1 for f in findings if f.get("light") == k)
              for k in ("green", "yellow", "red")}
    print(f"Stand {doc.get('at')} · Gesamt **{doc.get('light')}** · "
          f"{counts['green']} grün, {counts['yellow']} gelb, {counts['red']} rot\n")
    if not findings:
        print("Keine Prüfpunkte im Ergebnis — der Lauf hat nichts geprüft, "
              "und das ist nicht dasselbe wie grün.")
        return 0
    print("| | Prüfpunkt | Befund | Korrektur |")
    print("|---|---|---|---|")
    for f in sorted(findings, key=lambda f: (RANK.get(f.get("light"), 9),
                                             f.get("check", ""))):
        fix = f.get("fix") or "—"
        detail = str(f.get("detail", "")).replace("|", "\\|")
        print(f"| {LAMP.get(f.get('light'), '?')} | `{f.get('check')}` | "
              f"{detail} | {fix} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
