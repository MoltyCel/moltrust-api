"""Content pull for PASS items only.

Discovery -> authenticated GitHub API (never unauth; the 60/h shared limit
throttles). Pull the issue BODY + latest comments, not just the title.
NewsScout -> fetch the article, follow aggregator redirects to the primary
source, return a readable text extract.
"""
import re

import httpx

from . import config

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\n\s*\n\s*\n+")


def _html_to_text(html: str) -> str:
    html = _TAG.sub(" ", html)
    txt = _TAGS.sub(" ", html)
    txt = (txt.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
           .replace("&#39;", "'").replace("&quot;", '"').replace("&nbsp;", " "))
    txt = re.sub(r"[ \t]+", " ", txt)
    return _WS.sub("\n\n", txt).strip()


def parse_issue_url(url: str):
    m = re.search(r"github\.com/([^/]+)/([^/]+)/issues/(\d+)", url)
    return (m.group(1), m.group(2), int(m.group(3))) if m else (None, None, None)


def pull_discovery(url: str, gh_token: str) -> str:
    """Full issue body + up to 5 latest comments via the authenticated API."""
    owner, repo, num = parse_issue_url(url)
    if not owner:
        return ""
    h = {"Authorization": f"token {gh_token}", "Accept": "application/vnd.github+json",
         "User-Agent": config.USER_AGENT}
    base = f"https://api.github.com/repos/{owner}/{repo}/issues/{num}"
    with httpx.Client(timeout=20, headers=h) as c:
        issue = c.get(base).json()
        parts = [f"# {issue.get('title', '')}", "", issue.get("body") or "(no body)"]
        try:
            comments = c.get(base + "/comments", params={"per_page": 5, "sort": "created",
                             "direction": "desc"}).json()
            for cm in (comments or [])[:5]:
                parts.append(f"\n--- comment by @{cm.get('user', {}).get('login', '?')} ---\n"
                             + (cm.get("body") or ""))
        except Exception:
            pass
    return "\n".join(parts)[:12000]


def pull_article(url: str) -> tuple[str, str]:
    """Return (final_url, readable_text). Follows aggregator redirects."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True,
                          headers={"User-Agent": config.USER_AGENT}) as c:
            r = c.get(url)
            final = str(r.url)
            # Google News interstitials embed the primary link; chase it once.
            if "news.google.com" in final:
                m = re.search(r'href="(https?://(?!news\.google)[^"]+)"', r.text)
                if m:
                    r = c.get(m.group(1))
                    final = str(r.url)
            return final, _html_to_text(r.text)[:12000]
    except Exception as e:
        return url, f"[article fetch failed: {e}]"
