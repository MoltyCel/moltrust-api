"""MolTrust Admin Dashboard — Auth Module"""
import secrets
from datetime import datetime, timezone, timedelta
import bcrypt

ADMIN_USERS = {
    "lars": {
        "hash": "$2b$12$rxHaimEF4Ok1bXO4jybvQOx8cSmwhM/JRGWfTtlZ0OvvoFftTg6NC",
        "role": "superadmin",
    },
    "harald": {
        "hash": "$2b$12$c5GzSAMWozukKvNWmIiZ8OnP7I9i/7Ho0kKx5hVNGGUbJzWKXvZgC",
        "role": "admin",
    },
    "bernd": {
        "hash": "$2b$12$l3IuGfAveTEmC06YS7CNb.C3yGU5rkRvRJYnfpD6C4OtyBcqlMQBK",
        "role": "admin",
    },
}

# In-memory sessions (sufficient for 3 users)
SESSIONS: dict[str, dict] = {}


def verify_password(username: str, password: str) -> bool:
    user = ADMIN_USERS.get(username)
    if not user:
        return False
    return bcrypt.checkpw(password.encode(), user["hash"].encode())


def create_session(username: str) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
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
