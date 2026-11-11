"""Charter watch: the AUDIT BoF charter, read for one kind of change only.

Watches mirjak/audit-bof-preparation, the repository where the chairs keep the
draft charter: new issues, new PRs and new comments on PRs. It reports only
when a PR or a comment inserts or changes an out-of-scope section in
audit-charter.md (Lars, 23.10.2026). Everything else is counted in the log and
stays out of the report.

The repository has no such section on main today. The out-of-scope statements
sit in running text (lines 12, 36, 37 at 7af346a7), and issue #64 of
23.10.2026 asks whether to add one. Its items touch the delegation and
authorization surface, which is why the change is worth a message and the rest
of the traffic is not.

What counts as an out-of-scope section:
  * a heading (markdown `#` or a line that is bold and nothing else) whose
    text says out of scope, non-goals or not in scope, up to the next heading
    of the same or a higher level;
  * a line in running text that says so and ends with a colon, together with
    the list that follows it.
A negated phrase ("not out of scope") is not a marker.

What counts as inserting or changing one:
  * a PR whose head, against its merge base, has different out-of-scope
    sections in audit-charter.md: one added, one removed, or the text changed;
  * a review comment on audit-charter.md that carries a suggestion, when the
    suggestion holds an out-of-scope section or the commented lines lie inside
    one;
  * a comment on a PR whose fenced or quoted text holds an out-of-scope
    section. Prose that only argues about scope is not proposed text.
A commit straight to main that changes the section is reported as well: it is
the same change without a PR around it.

Every read pages to the end or raises ReadError, and a run that raised reports
the failure instead of a quiet charter.
"""

import base64
import hashlib
import re
from datetime import datetime, timedelta

REPO = "mirjak/audit-bof-preparation"
CHARTER = "audit-charter.md"
MAX_PAGES = 10
# First run reads this far back, so the watch starts on real traffic rather
# than on an empty window.
FIRST_WINDOW = timedelta(days=14)
# Re-read this much before the previous run, so a comment written while the
# last run was reading is not lost between two windows. `seen` drops the repeat.
OVERLAP = timedelta(hours=1)

API = "https://api.github.com"

_OOS = re.compile(r"\b(out[\s-]+of[\s-]+scope|non[\s-]?goals?|not\s+in\s+scope)\b", re.I)
_NEGATION = re.compile(r"\b(not|no|never|isn't|aren't)\s+(?:\w+\s+)?$", re.I)
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BOLD_LINE = re.compile(r"^\s*(?:\d+\.\s*)?\*\*(.+?)\*\*\s*:?\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[*+-]|\d+\.)\s+")
_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.S | re.M)
_SUGGESTION = re.compile(r"^```suggestion[^\n]*\n(.*?)^```", re.S | re.M)


class ReadError(Exception):
    """A read did not see everything it asked for."""


# ─── Sections ─────────────────────────────────────────────────────────────────

def is_marker(text):
    """True when the text names an out-of-scope section and does not negate it."""
    m = _OOS.search(text or "")
    if not m:
        return False
    return not _NEGATION.search(text[:m.start()])


def _heading(line):
    """(level, text) for a heading line, None otherwise. Bold lines rank below #."""
    m = _MD_HEADING.match(line)
    if m:
        return len(m.group(1)), m.group(2)
    m = _BOLD_LINE.match(line)
    if m:
        return 7, m.group(1)
    return None


def oos_spans(text):
    """[(first, last)] 1-based line ranges of every out-of-scope section."""
    lines = (text or "").splitlines()
    spans = []
    i = 0
    while i < len(lines):
        h = _heading(lines[i])
        if h and is_marker(h[1]):
            j = i + 1
            while j < len(lines):
                nh = _heading(lines[j])
                if nh and nh[0] <= h[0]:
                    break
                j += 1
        elif not h and is_marker(lines[i]) and lines[i].rstrip().endswith(":"):
            j = i + 1
            while j < len(lines) and (not lines[j].strip()
                                      or _LIST_ITEM.match(lines[j])
                                      or lines[j].startswith("  ")):
                j += 1
        else:
            i += 1
            continue
        while j - 1 > i and not lines[j - 1].strip():
            j -= 1
        spans.append((i + 1, j))
        i = j
    return spans


def oos_blocks(text):
    """The text of every out-of-scope section, whitespace-normalised per line."""
    lines = (text or "").splitlines()
    return ["\n".join(l.rstrip() for l in lines[a - 1:b]) for a, b in oos_spans(text)]


def _digest(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _proposed_text(body):
    """Text a comment proposes: fenced blocks and quoted lines."""
    body = body or ""
    out = [m.group(1) for m in _FENCE.finditer(body)]
    quoted = [l[1:].lstrip() if l.startswith(">") else None for l in body.splitlines()]
    run = []
    for q in quoted + [None]:
        if q is None:
            if run:
                out.append("\n".join(run))
            run = []
        else:
            run.append(q)
    return out


# ─── Reads ────────────────────────────────────────────────────────────────────

def _next_link(headers):
    for part in (headers.get("Link") or "").split(","):
        if 'rel="next"' in part:
            m = re.search(r"<([^>]+)>", part)
            if m:
                return m.group(1)
    return None


def _get(gh, url, params=None):
    try:
        return gh.get(url, params=params)
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        raise ReadError(f"{url}: {status or type(e).__name__}") from e


def read_all(gh, url, params=None):
    """Every item of a paginated list, or ReadError."""
    params = dict(params or {}, per_page=100)
    items = []
    for _ in range(MAX_PAGES):
        data, headers = _get(gh, url, params)
        if not isinstance(data, list):
            raise ReadError(f"{url}: not a list")
        items.extend(data)
        url, params = _next_link(headers), None
        if not url:
            return items
    raise ReadError(f"{url}: more than {MAX_PAGES} pages")


def read_updated_pulls(gh, since_iso):
    """Every PR updated at or after since_iso, newest first, paging until older."""
    url = f"{API}/repos/{REPO}/pulls"
    params = {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100}
    out = []
    for _ in range(MAX_PAGES):
        data, headers = _get(gh, url, params)
        if not isinstance(data, list):
            raise ReadError(f"{url}: not a list")
        for pr in data:
            if (pr.get("updated_at") or "") < since_iso:
                return out
            out.append(pr)
        url, params = _next_link(headers), None
        if not url:
            return out
    raise ReadError(f"{url}: more than {MAX_PAGES} pages")


def read_charter(gh, ref, repo=REPO):
    """audit-charter.md at ref; "" when the file does not exist there."""
    url = f"{API}/repos/{repo}/contents/{CHARTER}"
    try:
        data, _ = gh.get(url, params={"ref": ref})
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 404:
            return ""
        raise ReadError(f"{url}@{ref}: {status or type(e).__name__}") from e
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        raise ReadError(f"{url}@{ref}: unexpected payload")
    return base64.b64decode(data["content"]).decode("utf-8")


# ─── Checks ───────────────────────────────────────────────────────────────────

def _change(old, new):
    if not old and new:
        return "inserted"
    if old and not new:
        return "removed"
    return "changed"


def check_pr(gh, pr):
    """A hit when the PR changes the out-of-scope sections of the charter."""
    num = pr["number"]
    files = read_all(gh, f"{API}/repos/{REPO}/pulls/{num}/files")
    if not any(f.get("filename") == CHARTER for f in files):
        return None
    head = pr["head"]["sha"]
    cmp_, _ = _get(gh, f"{API}/repos/{REPO}/compare/{pr['base']['sha']}...{head}")
    base = (cmp_.get("merge_base_commit") or {}).get("sha")
    if not base:
        raise ReadError(f"PR #{num}: no merge base")
    head_repo = (pr["head"].get("repo") or {}).get("full_name") or REPO
    old = oos_blocks(read_charter(gh, base))
    new = oos_blocks(read_charter(gh, head, head_repo))
    if old == new:
        return None
    return {
        "key": f"pr#{num}:{_digest(*old, '|', *new)}",
        "kind": "PR",
        "ref": f"#{num}",
        "actor": (pr.get("user") or {}).get("login", ""),
        "title": pr.get("title") or "",
        "change": _change(old, new),
        "text": "\n\n".join(new) or "\n\n".join(old),
        "url": pr.get("html_url", ""),
    }


def check_review_comment(gh, c, file_cache):
    """A hit when a suggestion on the charter writes an out-of-scope section."""
    if c.get("path") != CHARTER:
        return None
    suggestions = [m.group(1) for m in _SUGGESTION.finditer(c.get("body") or "")]
    if not suggestions:
        return None
    hit = any(oos_spans(s) for s in suggestions)
    if not hit:
        ref = c.get("commit_id") or c.get("original_commit_id")
        if ref not in file_cache:
            file_cache[ref] = read_charter(gh, ref)
        last = c.get("line") or c.get("original_line")
        first = c.get("start_line") or c.get("original_start_line") or last
        if last:
            hit = any(a <= last and first <= b for a, b in oos_spans(file_cache[ref]))
    if not hit:
        return None
    return _comment_hit(c, "review comment", "\n\n".join(suggestions))


def check_pr_comment(c):
    """A hit when a PR comment proposes text holding an out-of-scope section."""
    if "/pull/" not in (c.get("html_url") or ""):
        return None
    proposed = [p for p in _proposed_text(c.get("body")) if oos_spans(p)]
    if not proposed:
        return None
    return _comment_hit(c, "PR comment", "\n\n".join(proposed))


def _comment_hit(c, kind, text):
    m = re.search(r"/pull/(\d+)", c.get("html_url") or "")
    return {
        "key": f"comment#{c['id']}",
        "kind": kind,
        "ref": f"#{m.group(1)}" if m else "",
        "actor": (c.get("user") or {}).get("login", ""),
        "title": "",
        "change": "proposed",
        "text": text,
        "url": c.get("html_url", ""),
    }


# ─── Run ──────────────────────────────────────────────────────────────────────

def run(gh, cw_state, now):
    """(hits, next_state, counts). Raises ReadError when a read was incomplete.

    cw_state is never modified. The caller stores next_state once the report
    that carries the hits has gone out, so a failed send reports them again.
    """
    cw_state = cw_state or {}
    seen = set(cw_state.get("seen", []))
    first_run = not cw_state.get("since")
    since = (now - FIRST_WINDOW) if first_run else _parse(cw_state["since"]) - OVERLAP
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    hits = []

    issues = read_all(gh, f"{API}/repos/{REPO}/issues",
                      {"state": "all", "since": since_iso, "sort": "created"})
    new_items = [i for i in issues if (i.get("created_at") or "") >= since_iso]
    counts = {
        "new_issues": sum(1 for i in new_items if "pull_request" not in i),
        "new_prs": sum(1 for i in new_items if "pull_request" in i),
    }

    pulls = read_updated_pulls(gh, since_iso)
    counts["prs_read"] = len(pulls)
    for pr in pulls:
        h = check_pr(gh, pr)
        if h:
            hits.append(h)

    file_cache = {}
    review = read_all(gh, f"{API}/repos/{REPO}/pulls/comments",
                      {"since": since_iso, "sort": "created", "direction": "asc"})
    review = [c for c in review if (c.get("created_at") or "") >= since_iso]
    convo = read_all(gh, f"{API}/repos/{REPO}/issues/comments",
                     {"since": since_iso, "sort": "created", "direction": "asc"})
    convo = [c for c in convo if (c.get("created_at") or "") >= since_iso
             and "/pull/" in (c.get("html_url") or "")]
    counts["new_pr_comments"] = len(review) + len(convo)
    for c in review:
        h = check_review_comment(gh, c, file_cache)
        if h:
            hits.append(h)
    for c in convo:
        h = check_pr_comment(c)
        if h:
            hits.append(h)

    main, _ = _get(gh, f"{API}/repos/{REPO}/commits/main")
    main_sha = main.get("sha")
    if not main_sha:
        raise ReadError("main: no sha")
    main_blocks = oos_blocks(read_charter(gh, main_sha))
    main_fp = _digest(*main_blocks)
    old_fp = cw_state.get("main_fp")
    if old_fp is not None and old_fp != main_fp:
        hits.append({
            "key": f"main:{main_fp}",
            "kind": "commit on main",
            "ref": main_sha[:8],
            "actor": ((main.get("commit") or {}).get("author") or {}).get("name", ""),
            "title": ((main.get("commit") or {}).get("message") or "").split("\n")[0],
            "change": "changed" if main_blocks else "removed",
            "text": "\n\n".join(main_blocks),
            "url": main.get("html_url", ""),
        })

    fresh = [h for h in hits if h["key"] not in seen]
    counts["hits"] = len(fresh)
    next_state = {
        "since": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seen": sorted(seen | {h["key"] for h in fresh}),
        "main_fp": main_fp,
        "main_sha": main_sha,
    }
    return fresh, next_state, counts


def _parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
