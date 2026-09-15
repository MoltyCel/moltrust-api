"""Reach filter: GitHub repo size (stars + contributors) for PASS leads.

Numbers come from the authenticated GitHub REST API:
  stars        -> GET /repos/{owner}/{repo}                      .stargazers_count
  contributors -> GET /repos/{owner}/{repo}/contributors?per_page=1&anon=true
                  count = page number of the Link rel="last" URL, else len(body)

Lookups are cached per repo for the lifetime of one ReachCache (one pipeline run).
An API failure (401 / rate limit / network) never discards a lead: the tier becomes
"unknown" and the lead stays pending_review, labelled "reach-unknown".
"""
import logging
import re
from typing import Callable, Optional

import httpx

from . import config

log = logging.getLogger("content_scout.reach")

TIER_HIGH = "high"          # label deferred-high, pending_review
TIER_MID = "mid"            # normal pending_review
TIER_LOW = "low"            # below threshold -> discarded
TIER_UNKNOWN = "unknown"    # API error -> pending_review, label reach-unknown
TIER_NA = "n/a"             # not a GitHub repo target (e.g. newsscout article)

LABELS = {TIER_HIGH: "deferred-high", TIER_MID: "mid", TIER_LOW: "below-reach",
          TIER_UNKNOWN: "reach-unknown", TIER_NA: "n/a"}

_LAST_PAGE = re.compile(r'[?&]page=(\d+)[^>]*>;\s*rel="last"')
_TOO_LARGE = "too large"


def split_target(target: str):
    """'owner/repo#123' -> ('owner', 'repo'); anything else -> (None, None)."""
    m = re.match(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:#\d+)?$", (target or "").strip())
    return (m.group(1), m.group(2)) if m else (None, None)


def contributors_from_response(resp: httpx.Response) -> int:
    m = _LAST_PAGE.search(resp.headers.get("link", ""))
    if m:
        return int(m.group(1))
    body = resp.json()
    return len(body) if isinstance(body, list) else 0


def fetch_repo_reach(owner: str, repo: str, gh_token: str,
                     client: Optional[httpx.Client] = None) -> dict:
    """Return {'stars': int|None, 'contributors': int|None, 'error': str|None}.
    Contributors is None with no error when GitHub declines to count a very large
    repo (403 'too large'); callers treat that as meeting the contributor minimum."""
    out = {"stars": None, "contributors": None, "error": None}
    if not gh_token:
        out["error"] = "no GitHub token"
        return out
    headers = {"Authorization": f"token {gh_token}", "Accept": "application/vnd.github+json",
               "User-Agent": config.USER_AGENT}
    own = client is None
    c = client or httpx.Client(timeout=config.REACH_HTTP_TIMEOUT)
    base = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        r = c.get(base, headers=headers)
        if r.status_code != 200:
            out["error"] = f"repo lookup HTTP {r.status_code}"
            return out
        out["stars"] = int(r.json().get("stargazers_count") or 0)
        r = c.get(base + "/contributors", headers=headers,
                  params={"per_page": 1, "anon": "true"})
        if r.status_code == 204:
            out["contributors"] = 0
        elif r.status_code == 200:
            out["contributors"] = contributors_from_response(r)
        elif r.status_code == 403 and _TOO_LARGE in r.text.lower():
            out["contributors"] = None  # GitHub will not count it: very large repo
        else:
            out["error"] = f"contributors lookup HTTP {r.status_code}"
        return out
    except Exception as e:  # network, JSON, timeout
        out["error"] = f"{type(e).__name__}"
        return out
    finally:
        if own:
            c.close()


def assess(owner: Optional[str], stats: Optional[dict]) -> dict:
    """Pure tiering. owner=None -> n/a. Returns the reach record stored on the row."""
    if owner is None:
        return {"tier": TIER_NA, "label": LABELS[TIER_NA], "stars": None,
                "contributors": None, "bypass": False, "reason": "not a GitHub repo target",
                "error": None}
    stats = stats or {}
    stars, contribs, err = stats.get("stars"), stats.get("contributors"), stats.get("error")
    rec = {"stars": stars, "contributors": contribs, "error": err,
           "bypass": owner.lower() in config.REACH_BYPASS_ORGS}
    if rec["bypass"]:
        rec.update(tier=TIER_HIGH, reason="standards org (reach filter bypass)")
    elif err:
        rec.update(tier=TIER_UNKNOWN, reason=f"reach unknown: {err}")
    else:
        contribs_ok = contribs is None or contribs >= config.REACH_MIN_CONTRIBUTORS
        if stars < config.REACH_MIN_STARS or not contribs_ok:
            rec.update(tier=TIER_LOW, reason=(
                f"below reach threshold: stars {stars} (min {config.REACH_MIN_STARS}), "
                f"contributors {contribs} (min {config.REACH_MIN_CONTRIBUTORS})"))
        elif stars >= config.REACH_HIGH_STARS:
            rec.update(tier=TIER_HIGH, reason=f"high reach: stars {stars} >= {config.REACH_HIGH_STARS}")
        else:
            rec.update(tier=TIER_MID, reason="meets reach threshold")
    rec["label"] = LABELS[rec["tier"]]
    return rec


class ReachCache:
    """Per-run cache: one GitHub lookup per repo, however many leads point at it."""

    def __init__(self, gh_token: str, fetch: Optional[Callable] = None):
        self.gh_token = gh_token
        self._fetch = fetch
        self._stats = {}

    def lookup(self, target: str) -> dict:
        owner, repo = split_target(target)
        if owner is None:
            return assess(None, None)
        key = f"{owner}/{repo}".lower()
        if key not in self._stats:
            self._stats[key] = (self._fetch or fetch_repo_reach)(owner, repo, self.gh_token)
        rec = assess(owner, self._stats[key])
        if rec["tier"] == TIER_UNKNOWN or (rec["bypass"] and rec["error"]):
            log.warning("REACH UNKNOWN for %s: %s — lead kept as pending_review (%s)",
                        key, rec["error"], rec["label"])
        return rec
