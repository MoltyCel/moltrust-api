"""One-time YouTube sign-in for the MolTrust channel, run by Lars on the Mac (ops UC-VID).

Takes the OAuth client JSON of type "Desktop" downloaded from the Google Cloud
Console, opens the browser for the Google sign-in (sign in as the account that
owns the MolTrust channel, hello@moltrust.ch), and writes three lines to
~/.moltrust_secrets on the server:

  YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN

Nothing secret is printed, passed on a command line, or written on the Mac.
The values travel over ssh on standard input; on the server an existing line
for the same name is replaced, the others are left as they are.

Scopes: youtube.upload (videos.insert) and youtube (playlists.insert,
playlistItems.insert). The consent screen has to be "In production": in
"Testing" a refresh token expires after 7 days.

    python3 scripts/usecase_video/youtube_auth.py ~/Downloads/client_secret_XXXX.json

Standard library only (installed-app flow with a loopback redirect and PKCE,
https://developers.google.com/youtube/v3/guides/auth/installed-apps).
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import pathlib
import secrets
import subprocess  # nosec B404 - ssh to the fixed server only
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"  # nosec B105 - an endpoint, not a secret
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]
SERVER = "moltstack@api.moltrust.ch"
NAMES = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")

# Runs on the server: reads NAME=VALUE lines on stdin, replaces or appends them
# in ~/.moltrust_secrets, keeps the file mode, prints only a count.
REMOTE = r'''
import os, sys, pathlib
p = pathlib.Path.home() / ".moltrust_secrets"
new = dict(l.split("=", 1) for l in sys.stdin.read().splitlines() if "=" in l)
lines = p.read_text().splitlines() if p.exists() else []
out = [l for l in lines if l.split("=", 1)[0] not in new]
out += [f"{k}={v}" for k, v in new.items()]
tmp = p.with_suffix(".tmp")
tmp.write_text("\n".join(out) + "\n")
os.chmod(tmp, 0o600)
os.replace(tmp, p)
print(f"{len(new)} values written")
'''


def client_from(path: pathlib.Path) -> tuple[str, str]:
    data = json.loads(path.read_text())
    inst = data.get("installed")
    if not inst:
        raise SystemExit("this is not a Desktop client JSON (no 'installed' section)")
    return inst["client_id"], inst["client_secret"]


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client_id: str, redirect: str, challenge: str, state: str) -> str:
    return AUTH_URI + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state})


def wait_for_code(state: str):
    """Start a loopback server on a free port for one request. Returns
    (server, thread, result); result gets "redirect" now and "code" or
    "error" once Google redirects back."""
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = q.get("state", [""])[0] == state and "code" in q
            result["code"] = q.get("code", [None])[0] if ok else None
            result["error"] = None if ok else (q.get("error", ["state mismatch"])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Fertig. Dieses Fenster kann geschlossen werden.\n".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    t = threading.Thread(target=srv.handle_request)
    t.start()
    result["redirect"] = redirect
    return srv, t, result


def exchange(client_id: str, client_secret: str, code: str, verifier: str, redirect: str) -> dict:
    body = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "code_verifier": verifier, "grant_type": "authorization_code",
        "redirect_uri": redirect}).encode()
    req = urllib.request.Request(TOKEN_URI, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310 - fixed https endpoint
        return json.load(r)


def remote_command() -> str:
    """The remote shell line. ssh joins its arguments into one string for the
    remote shell, so the script travels base64-encoded: no quotes, no spaces."""
    b64 = base64.b64encode(REMOTE.encode()).decode()
    return f"python3 -c 'import base64;exec(base64.b64decode(\"{b64}\"))'"


def write_remote(values: dict) -> str:
    payload = "".join(f"{k}={v}\n" for k, v in values.items())
    got = subprocess.run(  # nosec B603 B607 - fixed host and program, values on stdin
        ["ssh", SERVER, remote_command()], input=payload,
        capture_output=True, text=True, timeout=60)
    if got.returncode != 0:
        raise SystemExit(f"ssh failed: {got.stderr.strip()[:200]}")
    return got.stdout.strip()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    client_id, client_secret = client_from(pathlib.Path(argv[0]).expanduser())
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    srv, t, result = wait_for_code(state)
    url = auth_url(client_id, result["redirect"], challenge, state)
    print("Browser opens for the Google sign-in. Sign in as the MolTrust channel owner.")
    webbrowser.open(url)
    t.join(timeout=600)
    srv.server_close()
    if not result.get("code"):
        raise SystemExit(f"no authorization code ({result.get('error')})")
    tokens = exchange(client_id, client_secret, result["code"], verifier, result["redirect"])
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise SystemExit("Google returned no refresh token; revoke the app's access and run again")
    print(write_remote(dict(zip(NAMES, (client_id, client_secret, refresh)))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
