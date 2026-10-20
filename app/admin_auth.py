"""MolTrust Admin Dashboard — Auth Module"""
import os
import secrets
from datetime import datetime, timezone, timedelta
import bcrypt


def _load_admin_users() -> dict:
    """
    Load admin users from env vars rather than baking bcrypt hashes into
    source. The expected format is a comma-separated triplet list:

        MOLTRUST_ADMIN_USERS="lars:superadmin:$2b$12$...,harald:admin:$2b$12$...,bernd:admin:$2b$12$..."

    Empty / missing env var → no admins registered (login will refuse
    every request, fail-closed). Hashes that don't parse as bcrypt are
    skipped with a startup warning.
    """
    raw = os.environ.get("MOLTRUST_ADMIN_USERS", "").strip()
    if not raw:
        return {}
    users: dict[str, dict] = {}
    for entry in raw.split(","):
        parts = entry.strip().split(":", 2)
        if len(parts) != 3:
            continue
        username, role, hashval = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not username or not role or not hashval.startswith("$2"):
            continue
        users[username] = {"hash": hashval, "role": role}
    return users


ADMIN_USERS: dict[str, dict] = _load_admin_users()

# In-memory sessions (sufficient for 3 users)
SESSIONS: dict[str, dict] = {}


def verify_password(username: str, password: str) -> bool:
    user = ADMIN_USERS.get(username)
    if not user:
        return False
    return bcrypt.checkpw(password.encode(), user["hash"].encode())


def create_session(username: str) -> tuple[str, datetime]:
    # Hex, not token_urlsafe: the response scrubber in app/main.py
    # (SENSITIVE_PATTERNS) redacts any run of exactly 40 base64 characters and
    # "sk-" followed by 20 alphanumerics. About 1.8 % of token_urlsafe(32)
    # values contain one, and the caller then received a token with
    # "[REDACTED]" in the middle that matched no session. Hex cannot match
    # either pattern; the entropy is the same 256 bits.
    token = secrets.token_hex(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    SESSIONS[token] = {
        "username": username,
        "role": ADMIN_USERS[username]["role"],
        "expires": expires,
    }
    return token, expires


def verify_session(token: str) -> dict | None:
    session = SESSIONS.get(token)
    if not session:
        return None
    if datetime.now(timezone.utc) > session["expires"]:
        SESSIONS.pop(token, None)
        return None
    return session


def invalidate_session(token: str):
    SESSIONS.pop(token, None)
