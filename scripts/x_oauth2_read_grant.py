"""Grant the radar read-only access to @LarsOnMoltrust (ops CM22). Run once, by Lars.

    # on the Mac, a tunnel so the browser's redirect reaches the server:
    ssh -L 8765:127.0.0.1:8765 moltstack@api.moltrust.ch
    # in that shell:
    cd ~/moltstack && venv/bin/python scripts/x_oauth2_read_grant.py

It prints one authorize URL. Open it in a browser signed in to X as
@LarsOnMoltrust and press "Authorize app". X sends the browser back to
http://127.0.0.1:8765/callback, the tunnel carries that to this script, and
the script trades the code for tokens on the spot — X keeps a code valid for
30 seconds, which is too short to copy one by hand reliably.

`--paste` is the fallback without a tunnel: the browser then shows an error
page at 127.0.0.1, and its full address is pasted here within those 30 seconds.

What it prints: the granted scope list and the handle GET /2/users/me answers
with. What it never prints: a token, a code, the client secret. The tokens go
into ~/.moltrust_secrets under X_LARS_OAUTH2_* (app/x_lars_oauth2.py), and only
when the scopes are exactly tweet.read users.read offline.access and the
handle is LarsOnMoltrust.
"""
from __future__ import annotations

import http.server
import os
import secrets
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import x_lars_oauth2 as grant  # noqa: E402

PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{PORT}/callback"


def parse_callback(url_or_query: str, state: str) -> str:
    """The code from a callback address, after the state check. Raises GrantError."""
    query = urllib.parse.urlsplit(url_or_query.strip()).query or url_or_query.strip()
    q = urllib.parse.parse_qs(query.lstrip("?"))
    if q.get("error"):
        raise grant.GrantError(f"X answered error={q['error'][0]}")
    if (q.get("state") or [""])[0] != state:
        raise grant.GrantError("state does not match; callback refused")
    code = (q.get("code") or [""])[0]
    if not code:
        raise grant.GrantError("callback carries no code")
    return code


def wait_for_callback(state: str, port: int = PORT) -> str:
    """Serve exactly one request on 127.0.0.1 and return its code."""
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            try:
                got["code"] = parse_callback(self.path, state)
                body, status = "Granted. You can close this tab.", 200
            except grant.GrantError as e:
                got["error"] = e
                body, status = f"Refused: {e}", 400
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):  # the request line carries the code
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    try:
        while not got:
            srv.handle_request()
    finally:
        srv.server_close()
    if "error" in got:
        raise got["error"]
    return got["code"]


def main(argv: list[str]) -> int:
    cid, _ = grant.client()
    if not cid:
        print(f"{grant.CLIENT_ID_VAR} is not in {grant.secrets_path()}. "
              "Copy the OAuth 2.0 Client ID (and Client Secret as "
              f"{grant.CLIENT_SECRET_VAR}) from the X Developer Console first.")
        return 2
    verifier, challenge = grant.pkce_pair()
    state = secrets.token_urlsafe(24)
    print("Open this URL in a browser signed in to X as @"
          f"{grant.EXPECTED_HANDLE}:\n\n"
          f"{grant.authorize_url(cid, REDIRECT_URI, state, challenge)}\n")
    try:
        if "--paste" in argv:
            code = parse_callback(input("Paste the full address the browser "
                                        "landed on: "), state)
        else:
            print(f"Waiting for the redirect on 127.0.0.1:{PORT} …")
            code = wait_for_callback(state)
        scopes = grant.exchange_code(code, verifier, REDIRECT_URI)
    except grant.GrantError as e:
        print(f"Not stored: {e}")
        return 1
    print(f"Stored. Granted scopes: {' '.join(scopes)}")
    print(f"GET /2/users/me: @{grant.handle_of(grant.auth())}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
