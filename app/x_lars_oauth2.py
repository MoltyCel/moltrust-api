"""A read-only OAuth 2.0 user token for @LarsOnMoltrust (ops CM22).

Lars decided on 31.10.2026: the radar may read his account, never write to it.
OAuth 1.0a cannot express that — an access pair carries whatever permission the
app has, and this app is "Read and write" because @MolTrust posts through it.
OAuth 2.0 can: the token carries the scopes it was granted, and a token granted
without `tweet.write` cannot post. So this module asks for exactly

    tweet.read users.read offline.access

and refuses to store a token whose granted scope list holds anything else.

Every endpoint below was read from the X documentation before it was written
(CLAUDE.md, "Fremde APIs: der Endpunkt kommt aus der Doku"):

  authorize  https://x.com/i/oauth2/authorize
             docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code
             (PKCE: code_challenge, code_challenge_method S256; the code is
             valid for 30 seconds, so the exchange runs right in the callback)
  token      POST https://api.x.com/2/oauth2/token, x-www-form-urlencoded,
             Basic authorization for a confidential client (Web App / bot)
             docs.x.com/fundamentals/authentication/oauth-2-0/user-access-token
  refresh    same endpoint, grant_type=refresh_token; a refresh token is issued
             only with offline.access. The access token lives two hours.
             Whether X rotates the refresh token is not documented, so the
             store keeps whichever one the answer carries and the old one
             otherwise.
  scopes     GET /2/tweets, GET /2/users/{id}/tweets, GET /2/users/me each list
             OAuth2UserToken with tweet.read + users.read
             (docs.x.com/x-api/posts/get-posts-by-ids, …/users/get-posts,
             …/users/get-my-user)
  metrics    non_public_metrics / organic_metrics: "OAuth 1.0a or OAuth 2.0
             with user context for posts you own", posts of the last 30 days
             (docs.x.com/x-api/fundamentals/metrics)

**Where the token lives.** In ~/.moltrust_secrets, under names of its own and
apart from the @MolTrust pair (CM22 criterion 3). The file is read here
directly, not through os.environ: cron sources it once at start, and after a
refresh in another process the value in the environment is the spent one. The
refresh rewrites the file under an exclusive lock and through a rename, so a
cron line that sources it mid-refresh reads either the old file or the new one,
never half of each. Nothing in this module prints or logs a token value.

**No fallback.** Without these names there is no Lars token. Nothing here ever
reads X_ACCESS_TOKEN: that pair is @MolTrust, and its reads would return no
profile clicks for Lars's posts — a zero that looks like a result.
"""
from __future__ import annotations

import base64
import contextlib
import datetime
import fcntl
import hashlib
import logging
import os
import re
import secrets
import urllib.parse

log = logging.getLogger("x_lars_oauth2")

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"

SCOPES = ("tweet.read", "users.read", "offline.access")

# The existing app's OAuth 2.0 client (Developer Console, Keys and tokens).
CLIENT_ID_VAR = "X_OAUTH2_CLIENT_ID"
CLIENT_SECRET_VAR = "X_OAUTH2_CLIENT_SECRET"
# Lars's grant. The four names this module writes, and the only ones.
ACCESS_VAR = "X_LARS_OAUTH2_ACCESS_TOKEN"
REFRESH_VAR = "X_LARS_OAUTH2_REFRESH_TOKEN"
EXPIRES_VAR = "X_LARS_OAUTH2_EXPIRES_AT"
SCOPE_VAR = "X_LARS_OAUTH2_SCOPE"
TOKEN_VARS = (ACCESS_VAR, REFRESH_VAR, EXPIRES_VAR, SCOPE_VAR)

# The handle the grant has to come from. A browser signed in as @MolTrust
# would otherwise hand us a second @MolTrust token under Lars's names.
EXPECTED_HANDLE = "LarsOnMoltrust"

# Refresh this long before the two hours are up, so a read never starts on a
# token that dies during it.
REFRESH_MARGIN = datetime.timedelta(minutes=10)

_NAME_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
# What a value may look like when written back unquoted. X tokens are
# URL-safe; anything else is refused rather than quoted into a file bash sources.
_VALUE_RE = re.compile(r"^[A-Za-z0-9._~:+/=,-]*$")


class GrantError(RuntimeError):
    """The grant cannot be used as it stands. The message never holds a token."""


def secrets_path() -> str:
    return os.environ.get("MOLTRUST_SECRETS_FILE",
                          os.path.expanduser("~/.moltrust_secrets"))


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def read_secrets(names: tuple[str, ...]) -> dict[str, str]:
    """The named values from the secrets file; a missing file is an empty dict."""
    out: dict[str, str] = {}
    try:
        with open(secrets_path()) as f:
            for line in f:
                m = _NAME_RE.match(line.strip())
                if m and m.group(1) in names:
                    out[m.group(1)] = _unquote(m.group(2))
    except FileNotFoundError:
        pass
    return out


@contextlib.contextmanager
def _locked():
    """One writer at a time: the grant, the consumer and the daily measurement."""
    path = secrets_path()
    with open(path + ".lock", "a") as lock:
        os.chmod(path + ".lock", 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _write_secrets(updates: dict[str, str]) -> None:
    """Replace or append NAME=value lines; every other line stays as it was.

    Called under _locked(). Written to a sibling file and renamed over the old
    one, with the old file's mode (600 on the server).
    """
    for k, v in updates.items():
        if k not in TOKEN_VARS:
            raise GrantError(f"refusing to write {k}: not one of the grant's names")
        if not _VALUE_RE.match(v or ""):
            raise GrantError(f"refusing to write {k}: unexpected characters")
    path = secrets_path()
    try:
        with open(path) as f:
            lines = f.read().splitlines()
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        lines, mode = [], 0o600
    left = dict(updates)
    out = []
    for line in lines:
        m = _NAME_RE.match(line.strip())
        if m and m.group(1) in left:
            out.append(f"{m.group(1)}={left.pop(m.group(1))}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in left.items())
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode or 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, mode or 0o600)
    os.replace(tmp, path)


def client() -> tuple[str, str]:
    c = read_secrets((CLIENT_ID_VAR, CLIENT_SECRET_VAR))
    return c.get(CLIENT_ID_VAR, ""), c.get(CLIENT_SECRET_VAR, "")


def _basic(cid: str, secret: str) -> dict:
    raw = f"{urllib.parse.quote(cid, safe='')}:{urllib.parse.quote(secret, safe='')}"
    return {"Authorization": "Basic " + base64.b64encode(raw.encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded"}


def scopes_ok(granted: str | list) -> tuple[bool, list[str]]:
    """The granted scopes, and whether they are exactly the read-only set."""
    got = sorted(set(granted.split() if isinstance(granted, str) else granted or []))
    return got == sorted(SCOPES), got


# ── the grant ───────────────────────────────────────────────────────────────

def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge), RFC 7636 §4.1/§4.2."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    params = {"response_type": "code", "client_id": client_id,
              "redirect_uri": redirect_uri, "scope": " ".join(SCOPES),
              "state": state, "code_challenge": challenge,
              "code_challenge_method": "S256"}
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"


def _token_request(form: dict, cid: str, secret: str) -> dict:
    import requests
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if secret:
        headers = _basic(cid, secret)
    else:
        form = {**form, "client_id": cid}
    r = requests.post(TOKEN_URL, data=form, headers=headers, timeout=30)
    if r.status_code != 200:
        # The error body names the error, never a token; it is still cut short.
        try:
            err = (r.json() or {}).get("error") or ""
        except Exception:
            err = ""
        raise GrantError(f"token endpoint answered {r.status_code} {err}".strip())
    return r.json()


def _store(body: dict, now: datetime.datetime, keep_refresh: str = "",
           keep_scope: str = "") -> list[str]:
    """Write a token answer into the secrets file. Called under _locked().

    A refresh answer without a scope keeps the stored one: a refresh cannot
    widen a grant (RFC 6749 §6), and the stored list was checked at the grant.
    """
    ok, granted = scopes_ok(body.get("scope") or keep_scope)
    if not ok:
        raise GrantError("granted scopes are not the read-only set: "
                         + " ".join(granted) + "; nothing stored")
    access = body.get("access_token") or ""
    refresh = body.get("refresh_token") or keep_refresh
    if not access or not refresh:
        raise GrantError("token answer without access or refresh token; nothing stored")
    expires = now + datetime.timedelta(seconds=int(body.get("expires_in") or 7200))
    _write_secrets({ACCESS_VAR: access, REFRESH_VAR: refresh,
                    EXPIRES_VAR: expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    SCOPE_VAR: ",".join(granted)})
    return granted


def exchange_code(code: str, verifier: str, redirect_uri: str,
                  now: datetime.datetime | None = None) -> list[str]:
    """Trade the code for tokens and store them. Returns the granted scopes.

    The tokens are not stored until users/me has answered with Lars's handle,
    so a grant clicked in a browser signed in as another account leaves the
    file as it was.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cid, secret = client()
    if not cid:
        raise GrantError(f"{CLIENT_ID_VAR} is not in the secrets file")
    body = _token_request({"code": code, "grant_type": "authorization_code",
                           "redirect_uri": redirect_uri, "code_verifier": verifier},
                          cid, secret)
    ok, granted = scopes_ok(body.get("scope") or "")
    if not ok:
        raise GrantError("granted scopes are not the read-only set: "
                         + " ".join(granted) + "; nothing stored")
    handle = handle_of(BearerAuth(body.get("access_token") or ""))
    if handle != EXPECTED_HANDLE:
        raise GrantError(f"the grant came from @{handle}, not @{EXPECTED_HANDLE}; "
                         "nothing stored")
    with _locked():
        return _store(body, now)


# ── using it ────────────────────────────────────────────────────────────────

class BearerAuth:
    """requests auth that signs with a user access token. repr never shows it."""

    def __init__(self, token: str):
        self._token = token

    def __call__(self, r):
        r.headers["Authorization"] = f"Bearer {self._token}"
        return r

    def __repr__(self) -> str:
        return "BearerAuth(<lars oauth2>)"


def handle_of(auth, source: str = "lars-oauth2-grant") -> str | None:
    """GET /2/users/me with this auth; the handle, or None."""
    from agents import x_meter
    try:
        r = x_meter.get("https://api.x.com/2/users/me", auth=auth, timeout=30,
                        source=source)
    except Exception as e:
        log.error(f"users/me failed: {type(e).__name__}")
        return None
    if r.status_code != 200:
        log.error(f"users/me answered {r.status_code}")
        return None
    return ((r.json() or {}).get("data") or {}).get("username")


def _parsed(stamp: str) -> datetime.datetime | None:
    try:
        when = datetime.datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def granted() -> bool:
    """Is there a grant at all? Reads names only, sends nothing."""
    s = read_secrets((ACCESS_VAR, REFRESH_VAR))
    return bool(s.get(ACCESS_VAR) and s.get(REFRESH_VAR))


def auth(now: datetime.datetime | None = None) -> BearerAuth | None:
    """A live bearer auth for Lars's account, refreshed when due. None without a grant.

    None is the answer to every failure here — a missing grant, a refused
    refresh, a scope list that is not read-only. The caller treats it as "not
    readable now" and does nothing; there is no second account to try.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    s = read_secrets(TOKEN_VARS)
    if not (s.get(ACCESS_VAR) and s.get(REFRESH_VAR)):
        return None
    if not scopes_ok((s.get(SCOPE_VAR) or "").replace(",", " "))[0]:
        log.error("stored grant is not read-only; not used")
        return None
    expires = _parsed(s.get(EXPIRES_VAR) or "")
    if expires is not None and now < expires - REFRESH_MARGIN:
        return BearerAuth(s[ACCESS_VAR])
    try:
        with _locked():
            # Another process may have refreshed while this one waited.
            s = read_secrets(TOKEN_VARS)
            expires = _parsed(s.get(EXPIRES_VAR) or "")
            if expires is not None and now < expires - REFRESH_MARGIN:
                return BearerAuth(s[ACCESS_VAR])
            cid, secret = client()
            if not cid:
                log.error(f"{CLIENT_ID_VAR} missing; cannot refresh Lars's token")
                return None
            body = _token_request({"grant_type": "refresh_token",
                                   "refresh_token": s[REFRESH_VAR]}, cid, secret)
            _store(body, now, keep_refresh=s[REFRESH_VAR],
                   keep_scope=(s.get(SCOPE_VAR) or "").replace(",", " "))
            return BearerAuth(body["access_token"])
    except GrantError as e:
        log.error(f"refresh of Lars's token failed: {e}")
    except Exception as e:
        log.error(f"refresh of Lars's token failed: {type(e).__name__}")
    return None
