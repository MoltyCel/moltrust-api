"""After a deploy: does the running code carry the rule the commit says it does.

On 2026-10-05 the radar logged `prompt v3-2026-10-05` and drafted under the old
rule. `scripts/citation_index.py` was not on main, `citation_block()` returned
"" as designed, and `draft_reply` dropped the new rule — correctly, because a
source rule pointing at an empty index turns every candidate into a SKIP. The
version string was right. The log line was right. Every other signal was green.

**A silent fallback is indistinguishable from health in every signal except
this one.** So this runs after the health probe of every deploy and compares
the *effective artefact* against the committed expectation in
`config/versioned_paths.yaml` — not the version string, which was never wrong.

A mismatch is **red and goes out as a single message on the spot**, not into
the twice-daily report. That is a deliberate exception to the volume rule and
not a fourth entry in `supervision.IMMEDIATE`: this is not a routine check that
fires every hour, it fires once per deploy and only when the deploy did not
take. Twelve hours of drafting under the wrong rule cannot be undone, and the
cost of being wrong here is a message nobody needed.

    python3 scripts/deploy_verify.py            # check and report
    python3 scripts/deploy_verify.py --alert    # and send on mismatch
    python3 scripts/deploy_verify.py --json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "config", "versioned_paths.yaml")
WEB = os.path.expanduser("~/moltrust-web")


def committed(path: str) -> str:
    """The file as the deployed commit has it.

    `git show HEAD:<path>` and not the working file: the question is whether
    what is running matches what was deployed, and reading the same file twice
    answers nothing.
    """
    try:
        return subprocess.run(["git", "-C", ROOT, "show", f"HEAD:{path}"],
                              capture_output=True, text=True,
                              timeout=30).stdout
    except Exception:
        return ""


def load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def finding(name: str, ok: bool, detail: str, **extra) -> dict:
    return {"path": name, "ok": ok, "detail": detail, **extra}


# ── the four paths ──

def check_prompt(spec: dict) -> list[dict]:
    """The version, and the markers that version promises to carry."""
    out = []
    try:
        from agents import reply_radar as rr
    except Exception as e:
        return [finding("prompt", False,
                        f"reply_radar nicht importierbar: {type(e).__name__}: {e}")]
    running = getattr(rr, "PROMPT_VERSION", None)
    src = committed("agents/reply_radar.py")
    want = None
    for line in src.splitlines():
        if line.startswith("PROMPT_VERSION"):
            want = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
    if not want:
        out.append(finding("prompt/version", False,
                           "im Commit steht kein PROMPT_VERSION"))
    elif running != want:
        out.append(finding("prompt/version", False,
                           f"läuft {running!r}, committet ist {want!r} — "
                           f"der Prozess hat alten Code geladen"))
    else:
        out.append(finding("prompt/version", True, f"{running}"))

    # The part the version string cannot tell us.
    markers = {}
    for prefix, need in (spec.get("markers_for_version") or {}).items():
        if str(running or "").startswith(prefix):
            markers = {prefix: need}
            break
    if not markers:
        out.append(finding("prompt/markers", True,
                           f"keine Marker für {running} deklariert"))
        return out
    prefix, need = next(iter(markers.items()))
    try:
        index = rr.citation_block()
        effective = (rr.SYSTEM_PROMPT
                     + (rr.VARIANT3_RULE if index else "") + "\n" + index)
    except Exception as e:
        return out + [finding("prompt/markers", False,
                              f"Prompt nicht baubar: {type(e).__name__}: {e}")]
    missing = [m for m in need if m not in effective]
    if missing:
        out.append(finding(
            "prompt/markers", False,
            f"Version sagt {running}, aber im gebauten Prompt fehlt: "
            f"{', '.join(missing)} — ein Fallback hat gegriffen",
            missing=missing))
    else:
        out.append(finding("prompt/markers", True,
                           f"{len(need)} Marker vorhanden, Prompt "
                           f"{len(effective)} Zeichen"))
    return out


def check_citation_index(spec: dict) -> list[dict]:
    floor = spec.get("floor") or {}
    try:
        mod = load("citation_index_verify", "scripts/citation_index.py")
        idx = mod.build()
    except Exception as e:
        return [finding("citation_index", False,
                        f"baut nicht: {type(e).__name__}: {e}")]
    c = idx["counts"]
    low = [f"{k} {c.get(k)} < {v}" for k, v in floor.items()
           if (c.get(k) or 0) < v]
    if low:
        return [finding("citation_index", False,
                        f"unter dem Boden: {', '.join(low)}", counts=c)]
    return [finding("citation_index", True,
                    f"{c['posts']} Posts, {c['specs']} Spec-Seiten, "
                    f"{c['figures']} Zahlen", counts=c)]


def check_voice_gate(spec: dict) -> list[dict]:
    """Rule count against the floor, and the docs against moltrust-web."""
    out = []
    floor = (spec.get("floor") or {}).get("rules", 0)
    try:
        from agents import voice_gate as vg
        rules, lexicons = vg.parse_spec(vg._read(vg.DOC_SCAN))
    except Exception as e:
        return [finding("voice_gate_rules", False,
                        f"nicht ladbar: {type(e).__name__}: {e}")]
    if len(rules) < floor:
        out.append(finding(
            "voice_gate_rules", False,
            f"{len(rules)} Regeln geparst, Boden {floor} — ein yaml-Block "
            f"ist unlesbar und die Regel wird nicht mehr erzwungen",
            rules=len(rules)))
    else:
        out.append(finding("voice_gate_rules", True,
                           f"{len(rules)} Regeln, {len(lexicons)} Lexika",
                           rules=len(rules)))

    try:
        prints = vg.docs_fingerprint()
    except Exception as e:
        return out + [finding("voice_gate_docs", False,
                              f"Fingerprints nicht lesbar: {type(e).__name__}")]
    missing = [k for k, v in prints.items() if v == "missing"]
    if missing:
        out.append(finding("voice_gate_docs", False,
                           f"fehlende Dokumente: {', '.join(missing)} — "
                           f"der Prompt bekommt '[… missing]' statt der Regeln"))
        return out
    # Against moltrust-web's own copy, which is the source of record for these
    # three. A mirror that stopped refreshing scans against yesterday's rules
    # and says so nowhere.
    drift = []
    # The three paths as voice_gate itself resolves them, mirrored onto the
    # moltrust-web checkout. Read off DOC_SCAN/DOC_ANTI_KI/DOC_MY_VOICE_EN
    # rather than retyped: two of the three sit at the repo root and only
    # pre-send-scan.md is under docs/, which a second list would get wrong.
    for name, doc in (("pre_send_scan", vg.DOC_SCAN),
                      ("anti_ki_sprech", vg.DOC_ANTI_KI),
                      ("my_voice_en", vg.DOC_MY_VOICE_EN)):
        rel = os.path.relpath(str(doc), str(vg.WEB_DOCS))
        p = os.path.join(WEB, rel)
        if not os.path.exists(p):
            continue
        want = hashlib.sha256(
            open(p, encoding="utf-8").read().encode()).hexdigest()[:12]
        if prints.get(name) and prints[name] != want:
            drift.append(f"{name}: Spiegel {prints[name]} ≠ Web {want}")
    out.append(finding("voice_gate_docs", not drift,
                       "; ".join(drift) + " — Spiegel ist veraltet" if drift
                       else f"Fingerprints: "
                            f"{', '.join(f'{k} {v}' for k, v in prints.items())}"))
    return out


def check_expectations(spec: dict) -> list[dict]:
    floor = (spec.get("floor") or {}).get("pipelines", 0)
    try:
        from agents import supervision
        reg = supervision.load_expectations()
    except Exception as e:
        return [finding("expectations", False,
                        f"nicht ladbar: {type(e).__name__}: {e}")]
    n = len(reg.get("pipelines") or [])
    want = committed("config/expectations.yaml")
    try:
        n_committed = len((yaml.safe_load(want) or {}).get("pipelines") or [])
    except Exception:
        n_committed = None
    if n < floor:
        return [finding("expectations", False,
                        f"{n} Pipelines, Boden {floor}")]
    if n_committed is not None and n != n_committed:
        return [finding("expectations", False,
                        f"{n} Pipelines laufen, {n_committed} sind committet — "
                        f"der Prozess liest eine andere Datei")]
    return [finding("expectations", True, f"{n} Pipelines")]


CHECKS = {"prompt": check_prompt, "citation_index": check_citation_index,
          "voice_gate_rules": check_voice_gate,
          "expectations": check_expectations}


def run() -> list[dict]:
    try:
        spec = yaml.safe_load(open(SPEC))
    except Exception as e:
        return [finding("versioned_paths", False,
                        f"{SPEC} nicht lesbar: {type(e).__name__}: {e}")]
    out = []
    for entry in spec.get("paths") or []:
        fn = CHECKS.get(entry["name"])
        if not fn:
            out.append(finding(entry["name"], False,
                               "deklariert, aber kein Prüfer dafür"))
            continue
        try:
            out.extend(fn(entry))
        except Exception as e:
            out.append(finding(entry["name"], False,
                               f"Prüfung selbst gescheitert: "
                               f"{type(e).__name__}: {e}"))
    return out


def report(results: list[dict], sha: str = "") -> str:
    bad = [r for r in results if not r["ok"]]
    head = ("✅ Deploy-Prüfung: alles trägt die committete Regel"
            if not bad else
            f"\U0001f6a8 <b>Deploy-Prüfung rot — {len(bad)} Pfad(e)</b>")
    L = [head + (f"  ({sha[:7]})" if sha else ""), ""]
    for r in results:
        L.append(f"{'✅' if r['ok'] else '❌'} {r['path']}: {r['detail']}")
    if bad:
        L += ["", "Ein stiller Fallback sieht in jedem anderen Signal gesund "
              "aus — deshalb kommt das sofort und nicht im Sammelbericht."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--alert", action="store_true",
                    help="bei Abweichung sofort melden")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sha", default="")
    a = ap.parse_args(argv)
    results = run()
    bad = [r for r in results if not r["ok"]]
    text = report(results, a.sha)
    print(json.dumps(results, indent=1, ensure_ascii=False) if a.json else text)
    if a.alert and bad:
        from app import notify
        notify.send_telegram(text, channel=notify.ALERTS, parse_mode="HTML")
    return 2 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
