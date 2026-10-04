"""Post one text share to LinkedIn, on a button press and never otherwise.

**The endpoint, and why this one.** `POST https://api.linkedin.com/v2/ugcPosts`,
from the Share on LinkedIn guide:

  learn.microsoft.com/linkedin/consumer/integrations/self-serve/share-on-linkedin
    #creating-a-share-on-linkedin
  "For all shares created on LinkedIn, the request will always be a POST
   request to the User Generated Content (UGC) API."

Not `/rest/posts`. That is the versioned Posts API and lives under
`marketing/community-management/shares/posts-api?view=li-lms-2026-09` — the
Marketing Developer Platform surface, which requires versioned access this app
does not have (the same tier that denies us programmatic refresh tokens). It
also carries a deprecation notice of its own: Marketing 202510 sunsets on
2026-10-15. The consumer UGC API is unversioned and is the documented path for
*Share on LinkedIn*, which is the product on this app.

**Headers.** `X-Restli-Protocol-Version: 2.0.0` and nothing else:

  "All requests require the following header: X-Restli-Protocol-Version: 2.0.0"

**No `LinkedIn-Version` header.** That one belongs to the versioned APIs —
`YYYYMM`, documented under marketing/ and talent/ versioning. Sending it to the
v2 UGC endpoint would be cargo cult: the endpoint is not versioned, so there is
no version to name. Said out loud because it is the obvious thing to add by
reflex, and the rule in docs/linkedin-api.md is that the header comes from the
page, not from habit.

**The post id arrives in a response header.** 201 Created, and the URN is in
`X-RestLi-Id` — not in the body. A reader expecting JSON would find an empty
one and conclude the post failed.

**It is Lars's personal profile and his voice.** There is no schedule here, no
retry that posts, and no path from a cron to this function. The only caller is
the button handler.
"""
from __future__ import annotations

import datetime
import json
import logging
import os

import httpx

from app import linkedin_oauth as oauth
from app import paths

log = logging.getLogger("linkedin_post")

UGC_POSTS = "https://api.linkedin.com/v2/ugcPosts"
LEDGER = "linkedin_posts.jsonl"
PENDING = "linkedin_pending.json"

# Share on LinkedIn documents CONNECTIONS and PUBLIC. PUBLIC is the instruction
# and the only value used; the constant exists so the body reads as the
# documentation does rather than carrying a bare string.
VISIBILITY_PUBLIC = "PUBLIC"


def pending_path() -> str:
    return paths.data(PENDING)


def ledger_path() -> str:
    return paths.data(LEDGER)


def _read(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def load_pending() -> dict:
    return _read(pending_path(), {})


def save_pending(d: dict) -> None:
    path = paths.ensure(pending_path())
    with open(path, "w") as f:
        json.dump(d, f, indent=1, sort_keys=True)
    os.chmod(path, 0o640)


def remember(key: str, text: str, item: dict) -> None:
    """Park a drafted share until somebody presses a button.

    Written by agents/syndicate.py and read by the consumer in
    agents/reply_radar.py. One writer per field: syndicate creates the entry,
    the consumer only ever sets `result` on it.
    """
    d = load_pending()
    d[key] = {"text": text, "title": item.get("title"),
              "source": item.get("link"), "mode": item.get("_mode", "regular"),
              "drafted_at": datetime.datetime.now(
                  datetime.timezone.utc).isoformat()}
    save_pending(d)


def post_share(text: str, author_urn: str | None = None) -> dict:
    """The one write. Returns the post URN and the URL to look at it."""
    tokens = oauth.load_tokens()
    if not tokens:
        raise RuntimeError("nicht autorisiert — /oauth/linkedin/start zuerst")
    token = tokens.get("access_token")
    author = author_urn or tokens.get("author_urn")
    if not token or not author:
        raise RuntimeError("Token oder Autor-URN fehlt im Speicher")
    exp = tokens.get("access_expires_at")
    if exp and datetime.datetime.fromisoformat(exp) <= datetime.datetime.now(
            datetime.timezone.utc):
        # Checked before the call rather than after a 401: the token's own
        # recorded expiry is the cheapest way to say what went wrong, and this
        # app cannot renew it programmatically.
        raise RuntimeError(f"Access-Token abgelaufen ({exp}) — "
                           f"Neuautorisierung nötig, kein Refresh möglich")

    body = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                # NONE: text only. A link in the text stays text — LinkedIn
                # renders it, and ARTICLE would need a media block we do not
                # build here.
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": VISIBILITY_PUBLIC
        },
    }
    r = httpx.post(UGC_POSTS, json=body, timeout=45, headers={
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    })
    if r.status_code not in (200, 201):
        raise RuntimeError(f"ugcPosts HTTP {r.status_code}: {r.text[:300]}")
    # The URN is in the header. The body of a 201 is empty, and reading it
    # would look like a failed post.
    urn = r.headers.get("X-RestLi-Id") or r.headers.get("x-restli-id")
    if not urn:
        raise RuntimeError(f"HTTP {r.status_code} ohne X-RestLi-Id — "
                           f"der Post könnte existieren, die Kennung fehlt")
    return {"urn": urn, "url": f"https://www.linkedin.com/feed/update/{urn}/",
            "author": author, "visibility": VISIBILITY_PUBLIC}


def record(key: str, posted: dict, entry: dict) -> None:
    """The ledger line, and the pending row in the manual series.

    Two writes on purpose. The ledger is what we did; the series row is the
    place the numbers go, created empty so the Sunday prompt has something to
    ask about. Without it a posted share is invisible to the measurement until
    somebody remembers it.
    """
    row = {"kind": "linkedin", "key": key,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "urn": posted["urn"], "url": posted["url"],
            "author": posted["author"], "visibility": posted["visibility"],
            "title": entry.get("title"), "source": entry.get("source"),
            "mode": entry.get("mode"), "chars": len(entry.get("text") or "")}
    path = paths.ensure(ledger_path())
    with open(path, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    os.chmod(path, 0o640)

    # The manual series: a row with the identity filled in and every figure
    # absent. Absent, not zero — an unread analytics panel and a post nobody
    # saw are different facts, and scripts/linkedin_metrics.py keeps them apart.
    try:
        import importlib.util
        import sys as _sys
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "linkedin_metrics", os.path.join(here, "scripts",
                                             "linkedin_metrics.py"))
        lm = importlib.util.module_from_spec(spec)
        _sys.modules["linkedin_metrics"] = lm
        spec.loader.exec_module(lm)
        lm.append({"kind": "linkedin", "posted_at": row["at"],
                   "url": row["url"], "topic": entry.get("title"),
                   "pending": True})
    except Exception as e:
        # A missing series row is worth a warning and not a lost post.
        log.warning(f"Messreihen-Zeile nicht angelegt: {type(e).__name__}: {e}")
