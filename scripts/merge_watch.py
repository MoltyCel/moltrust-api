#!/usr/bin/env python3
"""Wait for a PR's checks, merge it, and delete its branch — in that order only.

Written after two incidents of the same shape on 2026-10-05.

The first: a branch deletion was chained onto a merge call with `&&`, the merge
failed on conflicts, and the deletion ran anyway. PR #236 lost its head branch and
closed. Nothing here deletes a branch unless the merge response said
`"merged": true`.

The second: PR #618 stayed blocked, and the ad-hoc loop waiting on it had no
ceiling, so it would have waited forever. Worse, the rerun it fired was written as
`gh api ... rerun-failed-jobs && echo ok || gh api ... rerun`, which told the
operator nothing: measured against a run whose only job was `cancelled`,
rerun-failed-jobs answers **201 Created**, and a second call answers **403 "This
workflow is already running"**. Both are meaningful and the chain hid both. This
reads the status code and says which case it is.

Retries are capped because they are not always able to help. `reserved-names-guard`
in this repository triggers on `pull_request` and `push` with no concurrency group,
so one push produces two runs reporting the same check name; when the later one is
the cancelled one, no number of reruns changes what the required-check evaluation
reads. A ceiling plus a clear message beats an unbounded wait.

Exit status: 0 merged, 2 not merged within the budget, 3 the API refused.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"


def call(method: str, path: str, token: str, body: dict | None = None):
    """(status, parsed-body-or-None). Never raises for an HTTP status."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "moltrust-merge-watch/1.0"})
    try:
        # nosec B310 - the scheme is fixed by the API constant above; no caller
        # supplies a URL, only a path appended to https://api.github.com
        with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None
    except Exception as e:  # noqa: BLE001
        print(f"  Netzfehler: {type(e).__name__}: {e}", file=sys.stderr)
        return -1, None


OK = ("success", "neutral", "skipped")


def blocking(repo: str, sha: str, token: str) -> list[str]:
    """Every check run on `sha` that is not success — not one per name.

    The first version of this took the newest run per name, which is not the rule
    GitHub applies. On 2026-10-05 PR #620 carried two runs each for
    `pytest --collect-only` and `pytest (credit middleware)`: one cancelled at
    20:24:51 and one successful at 20:29. Newest-per-name reported all five
    required checks green, and the merge was refused with

        2 of 5 required status checks are cancelled.

    So a cancelled run blocks even with a newer successful run of the same name
    beside it, and a watcher that deduplicates by name can report "nothing
    blocking" while the merge is impossible. Every non-success run is listed, with
    its name repeated when it has more than one, because the repetition is the
    finding.
    """
    st, body = call("GET", f"/repos/{repo}/commits/{sha}/check-runs?per_page=100", token)
    if st != 200 or not body:
        return [f"(check-runs nicht lesbar, HTTP {st})"]
    out = []
    for run in body.get("check_runs", []):
        if run.get("conclusion") in OK:
            continue
        started = (run.get("started_at") or "")[11:19] or "--:--:--"
        out.append(f"{run['status']}/{run.get('conclusion') or '-'} "
                   f"{run['name']} @{started}")
    return sorted(out)


def rerun(repo: str, sha: str, token: str) -> str:
    """Ask for a rerun of every non-success run on `sha`. Returns what happened.

    rerun-failed-jobs answers 201 even for a run whose jobs were cancelled rather
    than failed, and 403 "This workflow is already running" when one is in flight.
    Both are reported by name instead of being swallowed.
    """
    st, body = call("GET", f"/repos/{repo}/commits/{sha}/check-runs?per_page=100", token)
    if st != 200 or not body:
        return f"check-runs nicht lesbar (HTTP {st})"
    # The rerun endpoints take a run id, which the check run carries in its html_url.
    run_ids: set[str] = set()
    for cr in body.get("check_runs", []):
        if cr.get("conclusion") in ("success", "neutral", "skipped", None):
            continue
        url = cr.get("html_url") or ""
        if "/actions/runs/" in url:
            run_ids.add(url.split("/actions/runs/")[1].split("/")[0])
    if not run_ids:
        return "kein wiederholbarer Lauf gefunden"
    out = []
    for rid in sorted(run_ids):
        st1, b1 = call("POST", f"/repos/{repo}/actions/runs/{rid}/rerun-failed-jobs",
                       token, body={})
        if st1 in (201, 202):
            out.append(f"{rid}: rerun-failed-jobs angenommen ({st1})")
            continue
        msg = (b1 or {}).get("message", "")
        if st1 == 403 and "already running" in msg.lower():
            out.append(f"{rid}: laeuft schon ({st1})")
            continue
        # Not a refusal we understand — try the whole run once, and say so.
        st2, b2 = call("POST", f"/repos/{repo}/actions/runs/{rid}/rerun", token, body={})
        out.append(f"{rid}: rerun-failed-jobs {st1} ({msg or '-'}), "
                   f"voller rerun {st2} ({(b2 or {}).get('message', '-')})")
    return "; ".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--branch", help="delete it, but only after a confirmed merge")
    ap.add_argument("--method", default="squash")
    ap.add_argument("--poll-seconds", type=int, default=60)
    ap.add_argument("--max-polls", type=int, default=20)
    ap.add_argument("--max-reruns", type=int, default=2,
                    help="ceiling on rerun rounds; 0 disables reruns entirely")
    args = ap.parse_args()

    # One name only. tests/failure_paths.py::test_only_one_token_name_is_read
    # forbids a fallback: two names mean two places a stale credential can
    # hide, and the error message no longer says which one was wrong.
    token = os.environ.get("MOLTYCEL_GH_TOKEN")
    if not token:
        print("MOLTYCEL_GH_TOKEN nicht gesetzt", file=sys.stderr)
        return 3

    reruns = 0
    sha = None
    for attempt in range(1, args.max_polls + 1):
        st, pr = call("GET", f"/repos/{args.repo}/pulls/{args.pr}", token)
        if st != 200 or not pr:
            print(f"  PR nicht lesbar (HTTP {st})", file=sys.stderr)
            return 3
        sha = pr["head"]["sha"]
        state = pr.get("mergeable_state")

        if state == "clean":
            st, res = call("PUT", f"/repos/{args.repo}/pulls/{args.pr}/merge", token,
                           body={"merge_method": args.method})
            merged = bool((res or {}).get("merged"))
            print(f"  merge: HTTP {st} merged={merged} "
                  f"{(res or {}).get('message', '')} {(res or {}).get('sha', '')[:12]}")
            if not merged:
                print("  Branch bleibt — der Merge ist nicht bestaetigt.")
                return 2
            if args.branch:
                dst, _ = call("DELETE",
                              f"/repos/{args.repo}/git/refs/heads/{args.branch}", token)
                print(f"  Branch {args.branch} gelöscht: HTTP {dst}")
            return 0

        if state in ("dirty", "behind"):
            print(f"  mergeable_state={state} — das löst kein Warten. Abbruch.")
            print(f"  blockierend: {blocking(args.repo, sha, token)}")
            return 2

        if state == "blocked" and reruns < args.max_reruns:
            bad = blocking(args.repo, sha, token)
            if bad:
                reruns += 1
                print(f"  Versuch {attempt}: blocked durch {bad}")
                print(f"  Rerun {reruns}/{args.max_reruns}: "
                      f"{rerun(args.repo, sha, token)}")
        elif state == "blocked" and reruns >= args.max_reruns and attempt == 1:
            # The ceiling is already spent for this invocation. Say so once rather
            # than firing reruns that cannot change what the evaluation reads.
            print(f"  blocked, Rerun-Obergrenze {args.max_reruns} erschoepft — "
                  f"es wird nur noch gewartet")
        time.sleep(args.poll_seconds)

    print(f"  nach {args.max_polls} Durchläufen weiter nicht mergebar.")
    print(f"  Rerun-Runden verbraucht: {reruns}/{args.max_reruns}")
    if sha:
        print(f"  blockierend: {blocking(args.repo, sha, token)}")
    print("  Nichts gemergt, kein Branch gelöscht.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
