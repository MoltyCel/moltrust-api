"""LinkedIn OAuth for @moltrust: authorise, store, renew. No posting.

Every endpoint below was read from the documentation before it was written,
per docs/linkedin-api.md. The URLs, with the sections they came from:

  authorization   https://www.linkedin.com/oauth/v2/authorization
                  learn.microsoft.com/linkedin/shared/authentication/
                  authorization-code-flow#step-2-request-an-authorization-code
  token           POST https://www.linkedin.com/oauth/v2/accessToken
                  …/authorization-code-flow#step-3-exchange-authorization-code-
                  for-an-access-token   (Content-Type x-www-form-urlencoded)
  refresh         same endpoint, grant_type=refresh_token
                  learn.microsoft.com/linkedin/shared/authentication/
                  programmatic-refresh-tokens
  userinfo        https://api.linkedin.com/v2/userinfo
                  learn.microsoft.com/linkedin/consumer/integrations/self-serve/
                  sign-in-with-linkedin-v2#getting-started  (OIDC discovery)

These pages carry no `?view=` parameter — they are not versioned per month, so
there is none to quote. The Marketing API pages are; nothing here touches them.

**The one thing the documentation says that changes the design.** From the
refresh-token page, first sentence:

> LinkedIn supports programmatic refresh tokens for all approved Marketing
> Developer Platform (MDP) partners.

This app carries *Share on LinkedIn* (Default Tier) and *Sign In with LinkedIn
using OpenID Connect* (Standard Tier). Neither is MDP. So a refresh token is
probably not issued to us at all, and the automatic renewal has nothing to
renew. The code therefore does three things rather than one: it asks, it
records what came back, and it says plainly which of the two worlds we are in.
A renewal path built on the assumption and never exercised would look finished
and fail on day sixty.

**The member id.** `w_member_social` needs an author URN, and the id comes from
the `sub` claim of the userinfo response — `subject_types_supported: pairwise`,
so it is specific to this app and not a global profile id. Stored as
`urn:li:person:<sub>`, which is the form the share API expects
(learn.microsoft.com/linkedin/shared/api-guide/concepts/urns).

**Analytics are not here and will not be.** `memberCreatorPostAnalytics` is not
available to this app, so the LinkedIn figures stay a manual series in
`scripts/linkedin_metrics.py`. Nobody should go looking for an endpoint.

**Posting is not implemented.** By instruction: authorise, confirm, report. The
decision about automatic posting comes after this runs.

**Why the store is Postgres and not a file.** The first version wrote
`data/linkedin_token.enc` and the API answered 500: the service runs on a
read-only filesystem by systemd hardening. That property is worth keeping, so
the state moved rather than the hardening. It is also the better store — the
callback writes it and the daily renewal cron reads and writes it, and a file
with two writers is the shape that has already cost us three incidents. The
column is `bytea` and holds Fernet ciphertext: a dump or a replica carries no
clear token, and the key stays in ~/.moltrust_secrets.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import logging
import os
import secrets
import urllib.parse

import httpx

log = logging.getLogger("linkedin_oauth")

AUTHORIZE = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO = "https://api.linkedin.com/v2/userinfo"

# openid and profile come from Sign In with LinkedIn (OIDC); w_member_social
# from Share on LinkedIn. The OIDC discovery document lists only
# openid/profile/email under scopes_supported — w_member_social is granted by
# the other product, and both are on the app, so one authorisation covers them.
SCOPES = ("openid", "profile", "w_member_social")

# Seven days before the access token expires, as instructed. The access token
# lives 5 184 000 seconds = 60 days per the documented sample response.
RENEW_BEFORE = datetime.timedelta(days=7)
STATE_TTL = datetime.timedelta(minutes=15)


def _connect():
    """A short-lived connection. psycopg2 and not the async pool on purpose.

    The same two functions are called from the FastAPI route and from the daily
    cron, and one implementation that works in both is worth more than a saved
    millisecond on an endpoint a human triggers twice per sixty days.
    """
    import psycopg2
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        dbname=os.getenv("DB_NAME", "moltstack"),
        user=os.getenv("DB_USER", "moltstack"))


def _row() -> tuple:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT state, state_at, token_enc FROM linkedin_oauth "
                    "WHERE id = 1")
        return cur.fetchone() or (None, None, None)


def client() -> tuple[str, str]:
    cid = os.getenv("LINKEDIN_CLIENT_ID", "").strip()
    secret = os.getenv("LINKEDIN_CLIENT_SECRET", "").strip()
    return cid, secret


def redirect_uri() -> str:
    return os.getenv("LINKEDIN_REDIRECT_URI",
                     "https://api.moltrust.ch/oauth/linkedin/callback")


# ── the token at rest ──
#
# Fernet with a key derived from the client secret. The ciphertext lives in
# data/, the secret stays in ~/.moltrust_secrets, and the two are only ever
# together in memory — which is the point of the instruction not to put the
# tokens in the secrets file in clear text.
#
# Derived rather than a separate key on purpose: a second secret to rotate is a
# second secret to forget, and anyone who can read the client secret can already
# impersonate the app. This protects the token against someone who reads the
# data directory, which is the realistic case (backups, a stray scp, the
# diagnose path).

def _fernet():
    from cryptography.fernet import Fernet
    _, secret = client()
    if not secret:
        raise RuntimeError("LINKEDIN_CLIENT_SECRET not set — cannot open the store")
    key = base64.urlsafe_b64encode(
        hashlib.sha256(b"moltrust-linkedin-token-v1:" + secret.encode()).digest())
    return Fernet(key)


def save_tokens(payload: dict) -> None:
    blob = _fernet().encrypt(json.dumps(payload, sort_keys=True).encode())
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO linkedin_oauth (id, token_enc, updated_at) "
                    "VALUES (1, %s, now()) ON CONFLICT (id) DO UPDATE "
                    "SET token_enc = EXCLUDED.token_enc, updated_at = now()",
                    (blob,))
    log.info(f"token stored, {len(blob)} bytes of ciphertext")


def load_tokens() -> dict | None:
    _, _, blob = _row()
    if not blob:
        return None
    try:
        return json.loads(_fernet().decrypt(bytes(blob)))
    except Exception as e:
        # A store that will not open is a finding, not an empty store: silently
        # returning None here would send us through a fresh authorisation and
        # leave ciphertext nobody can read sitting in the row.
        raise RuntimeError(f"token store unreadable: {type(e).__name__}") from e


# ── the flow ──

def start() -> dict:
    """The authorisation URL, with a random state recorded for the callback."""
    cid, secret = client()
    if not cid or not secret:
        raise RuntimeError("LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET not set")
    state = secrets.token_urlsafe(24)
    now = datetime.datetime.now(datetime.timezone.utc)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO linkedin_oauth (id, state, state_at, updated_at) "
                    "VALUES (1, %s, %s, now()) ON CONFLICT (id) DO UPDATE "
                    "SET state = EXCLUDED.state, state_at = EXCLUDED.state_at, "
                    "updated_at = now()", (state, now))
    params = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": redirect_uri(),
        "state": state,
        "scope": " ".join(SCOPES),
    }
    return {"url": f"{AUTHORIZE}?{urllib.parse.urlencode(params)}",
            "scopes": list(SCOPES), "state": state,
            "expires_at": (now + STATE_TTL).isoformat()}


def check_state(given: str) -> None:
    """The state is ours and fresh, or the callback is refused.

    LinkedIn documents `state` as optional. It is required here: the parameter
    exists to stop a third party replaying a callback at us, and optional is a
    statement about their API, not about our risk.
    """
    state, at, _ = _row()
    if not state or not at:
        raise PermissionError("no authorisation in progress")
    if not given or not secrets.compare_digest(str(state), given):
        raise PermissionError("state does not match the one we issued")
    if datetime.datetime.now(datetime.timezone.utc) - at > STATE_TTL:
        raise PermissionError(f"state older than {STATE_TTL}")
    # Single use, and cleared in the same statement that checked it — a second
    # callback with the same state finds nothing in progress.
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE linkedin_oauth SET state = NULL, state_at = NULL, "
                    "updated_at = now() WHERE id = 1")


def exchange(code: str) -> dict:
    """Authorization code for tokens, and record what actually came back."""
    cid, secret = client()
    r = httpx.post(TOKEN, data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect_uri(),
        "client_id": cid, "client_secret": secret},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"token exchange HTTP {r.status_code}: {r.text[:200]}")
    return _record(r.json())


def _record(body: dict) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    access_ttl = int(body.get("expires_in") or 0)
    refresh_ttl = body.get("refresh_token_expires_in")
    stored = {
        "access_token": body.get("access_token"),
        "refresh_token": body.get("refresh_token"),
        "scope": body.get("scope"),
        "obtained_at": now.isoformat(),
        "access_expires_at": (now + datetime.timedelta(seconds=access_ttl)
                              ).isoformat() if access_ttl else None,
        "refresh_expires_at": (now + datetime.timedelta(seconds=int(refresh_ttl))
                               ).isoformat() if refresh_ttl else None,
        # Recorded, not assumed. If this is False the seven-day renewal has
        # nothing to renew and re-authorisation is a manual step every 60 days.
        "refreshable": bool(body.get("refresh_token")),
    }
    save_tokens({**(load_tokens_safe() or {}), **stored})
    return stored


def load_tokens_safe() -> dict | None:
    try:
        return load_tokens()
    except RuntimeError:
        return None


def fetch_member(access_token: str) -> dict:
    """The `sub` claim is the member id w_member_social needs as author."""
    r = httpx.get(USERINFO,
                  headers={"Authorization": f"Bearer {access_token}"},
                  timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"userinfo HTTP {r.status_code}: {r.text[:200]}")
    body = r.json()
    sub = body.get("sub")
    if not sub:
        raise RuntimeError("userinfo returned no sub claim")
    out = {"member_sub": sub, "author_urn": f"urn:li:person:{sub}",
           "name": body.get("name"), "locale": body.get("locale")}
    tokens = load_tokens_safe() or {}
    save_tokens({**tokens, **out})
    return out


def refresh() -> dict:
    """Renew the access token. Refuses clearly when there is nothing to renew."""
    tokens = load_tokens()
    if not tokens:
        raise RuntimeError("no token stored — authorise first")
    rt = tokens.get("refresh_token")
    if not rt:
        raise RuntimeError(
            "no refresh token: this app is not an approved Marketing Developer "
            "Platform partner, and LinkedIn issues programmatic refresh tokens "
            "only to those. Re-authorisation is manual every 60 days.")
    cid, secret = client()
    r = httpx.post(TOKEN, data={
        "grant_type": "refresh_token", "refresh_token": rt,
        "client_id": cid, "client_secret": secret},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"refresh HTTP {r.status_code}: {r.text[:200]}")
    return _record(r.json())


def status(now: datetime.datetime | None = None) -> dict:
    """What we hold, and whether it needs doing something about.

    Read-only, and the shape agents/supervision.py reports from — so a token
    that is about to expire reaches the collected report rather than being
    discovered on the day it stops working.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        t = load_tokens()
    except RuntimeError as e:
        return {"state": "unreadable", "detail": str(e)}
    if not t:
        return {"state": "none", "detail": "nicht autorisiert"}
    out = {"state": "ok", "scope": t.get("scope"),
           "member_sub": t.get("member_sub"),
           "author_urn": t.get("author_urn"),
           "refreshable": t.get("refreshable"),
           "access_expires_at": t.get("access_expires_at"),
           "refresh_expires_at": t.get("refresh_expires_at")}
    exp = t.get("access_expires_at")
    if exp:
        left = datetime.datetime.fromisoformat(exp) - now
        out["days_left"] = round(left.total_seconds() / 86400, 1)
        if left <= datetime.timedelta(0):
            out["state"] = "expired"
        elif left <= RENEW_BEFORE:
            out["state"] = "renew_due" if t.get("refreshable") else "reauth_due"
    return out
