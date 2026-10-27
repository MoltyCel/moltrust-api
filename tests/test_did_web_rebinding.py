"""did:web resolution connects to the address it checked (ops EGRESS).

Until 26.10.2026 _resolve_did_web_external checked the host's addresses and
then let httpx resolve the name a second time on connect. A name that answers
public to the first lookup and 127.0.0.1 to the second walked past the check.
This test fails on that code: it sees two lookups and a connection attempt to
the loopback answer.
"""
import json
import socket

import pytest
from fastapi import HTTPException

from app import egress
from app.main import _resolve_did_web_external

PUBLIC = "93.184.215.14"
DOC = {"id": "did:web:rebind.example", "verificationMethod": []}


async def test_did_web_is_fetched_from_the_checked_address(monkeypatch):
    lookups = []

    def dns(host, port, *args, **kwargs):
        lookups.append(host)
        ip = PUBLIC if len(lookups) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    connected = []

    class Conn:
        def __init__(self, host, address, port, *, timeout, context):
            connected.append(address)

        def request(self, method, path, headers):
            self.path = path

        def getresponse(self):
            class R:
                status = 200

                def getheaders(self):
                    return []

                def read(self, n):
                    return json.dumps(DOC).encode()
            return R()

        def close(self):
            pass

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(egress, "_CONNECTION", Conn)

    try:
        doc = await _resolve_did_web_external("did:web:rebind.example")
    except HTTPException as e:
        doc = e
    assert lookups == ["rebind.example"], f"name resolved {len(lookups)} times: {lookups}"
    assert connected == [PUBLIC]
    assert doc == DOC


@pytest.mark.parametrize("did", [
    "did:web:127.0.0.1", "did:web:localhost", "did:web:169.254.169.254",
    "did:web:10.0.0.1%3A8080", "did:web:evil.example%2F..%2Fx", "did:web:a%40b.example",
])
async def test_internal_or_malformed_hosts_are_refused(did, monkeypatch):
    monkeypatch.setattr(egress, "_CONNECTION", lambda *a, **k: pytest.fail("connected"))
    with pytest.raises(HTTPException) as e:
        await _resolve_did_web_external(did)
    assert e.value.status_code == 400
