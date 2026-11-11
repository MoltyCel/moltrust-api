"""app/egress.py: the one path for outbound requests to hosts someone else named.

No network: DNS and the connection are replaced, so each test says exactly
which addresses a name resolves to and records where the connection went.
"""
import socket

import pytest

from app import egress

PUBLIC = "93.184.215.14"
PUBLIC_2 = "93.184.215.15"


class FakeDNS:
    """getaddrinfo that answers from a script, one answer per call."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, host, port, *args, **kwargs):
        self.calls.append(host)
        ips = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (ip, port)) for ip in ips]


class FakeServer:
    """Stands in for the pinned connection; maps (host, path) to a response."""

    def __init__(self, routes):
        self.routes = routes
        self.connections = []

    def connection(self, host, address, port, *, timeout, context):
        server = self

        class Conn:
            def request(self, method, path, headers):
                server.connections.append((host, address, port, path, headers["Host"]))
                self._key = (host, path)

            def getresponse(self):
                status, headers, body = server.routes[self._key]

                class R:
                    def __init__(self):
                        self.status = status
                        self._body = body

                    def getheaders(self):
                        return list(headers.items())

                    def read(self, n):
                        return self._body[:n]
                return R()

            def close(self):
                pass
        return Conn()


@pytest.fixture
def net(monkeypatch):
    def install(dns, routes):
        server = FakeServer(routes)
        monkeypatch.setattr(egress.socket, "getaddrinfo", dns)
        monkeypatch.setattr(egress, "_CONNECTION", server.connection)
        return server
    return install


# --- which addresses count as public -----------------------------------------

@pytest.mark.parametrize("ip", [
    "0.0.0.0", "10.1.2.3", "100.64.0.1", "127.0.0.1", "169.254.169.254",
    "172.16.0.1", "192.168.1.1", "198.18.0.1", "224.0.0.1", "255.255.255.255",
    "::", "::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a00:1",
    "2002:a00:1::1", "fd00:ec2::254", "fe80::1", "ff02::1", "2001:0:4136:e378::1",
    "fe80::1%eth0", "not-an-ip",
])
def test_non_public_addresses_are_refused(ip):
    assert egress.address_is_public(ip) is False


@pytest.mark.parametrize("ip", [PUBLIC, "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_pass(ip):
    assert egress.address_is_public(ip) is True


# --- URL rules, before any lookup ----------------------------------------------

@pytest.mark.parametrize("url", [
    "http://example.org/x", "file:///etc/passwd", "gopher://example.org/",
    "ftp://example.org/", "data:text/plain,hi", "//example.org/x", "",
])
def test_only_https_is_allowed(url, net):
    dns = FakeDNS([PUBLIC])
    server = net(dns, {})
    with pytest.raises(egress.EgressRefused):
        egress.fetch(url)
    assert dns.calls == [] and server.connections == []


def test_credentials_in_the_url_are_refused(net):
    dns = FakeDNS([PUBLIC])
    net(dns, {})
    with pytest.raises(egress.EgressRefused, match="credentials"):
        egress.fetch("https://user:pw@example.org/")
    assert dns.calls == []


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/", "https://[::1]/", "https://169.254.169.254/latest/meta-data/",
    "https://localhost/", "https://metadata.google.internal/",
])
def test_literal_and_named_internal_targets_are_refused(url, net):
    server = net(FakeDNS([PUBLIC]), {})
    with pytest.raises(egress.EgressRefused):
        egress.fetch(url)
    assert server.connections == []


# --- resolution: once, every address, connect to what was checked -------------

def test_rebinding_connects_to_the_checked_address(net):
    # First answer public, every later answer loopback. One lookup only, and
    # the connection goes to the address that passed.
    dns = FakeDNS([PUBLIC], ["127.0.0.1"])
    server = net(dns, {("rebind.example", "/doc"): (200, {}, b"{}")})
    resp = egress.fetch("https://rebind.example/doc")
    assert resp.status == 200
    assert dns.calls == ["rebind.example"]
    assert [c[1] for c in server.connections] == [PUBLIC]
    assert server.connections[0][4] == "rebind.example"   # Host header carries the name


def test_a_name_with_one_internal_address_is_refused_whole(net):
    dns = FakeDNS([PUBLIC, "10.0.0.5"])
    server = net(dns, {})
    with pytest.raises(egress.EgressRefused, match="10.0.0.5"):
        egress.fetch("https://mixed.example/")
    assert server.connections == []


def test_pinned_connection_dials_the_address_and_verifies_the_name(monkeypatch):
    dialled, wrapped = [], []

    class Ctx:
        def wrap_socket(self, sock, server_hostname):
            wrapped.append(server_hostname)
            return sock

    monkeypatch.setattr(egress.socket, "create_connection",
                        lambda addr, timeout, *a: dialled.append(addr) or object())
    conn = egress._PinnedHTTPSConnection("did.example", PUBLIC, 443, timeout=1, context=Ctx())
    conn.connect()
    assert dialled == [(PUBLIC, 443)]
    assert wrapped == ["did.example"]


# --- redirects: followed, and every target checked like the first --------------

def test_redirect_to_an_internal_host_is_refused(net):
    dns = FakeDNS([PUBLIC], ["169.254.169.254"])
    server = net(dns, {("a.example", "/"): (302, {"Location": "https://b.example/x"}, b"")})
    with pytest.raises(egress.EgressRefused, match="169.254.169.254"):
        egress.fetch("https://a.example/")
    assert [c[1] for c in server.connections] == [PUBLIC]


@pytest.mark.parametrize("location", [
    "http://a.example/plain", "https://127.0.0.1/", "https://[::1]:8443/", "file:///etc/passwd",
])
def test_redirect_targets_obey_the_url_rules(location, net):
    server = net(FakeDNS([PUBLIC]), {("a.example", "/"): (301, {"Location": location}, b"")})
    with pytest.raises(egress.EgressRefused):
        egress.fetch("https://a.example/")
    assert len(server.connections) == 1


def test_relative_redirect_to_a_public_host_is_followed(net):
    server = net(FakeDNS([PUBLIC]), {
        ("a.example", "/old"): (308, {"Location": "/new"}, b""),
        ("a.example", "/new"): (200, {}, b'{"ok": true}'),
    })
    resp = egress.fetch("https://a.example/old")
    assert resp.status == 200 and resp.url == "https://a.example/new"
    assert [c[3] for c in server.connections] == ["/old", "/new"]


def test_redirect_loop_stops(net):
    net(FakeDNS([PUBLIC]), {("a.example", "/"): (302, {"Location": "/"}, b"")})
    with pytest.raises(egress.EgressRefused, match="redirects"):
        egress.fetch("https://a.example/")


# --- size ------------------------------------------------------------------------

def test_body_over_the_cap_fails(net):
    net(FakeDNS([PUBLIC]), {("a.example", "/"): (200, {}, b"x" * 101)})
    with pytest.raises(egress.EgressFailed, match="exceeds"):
        egress.fetch("https://a.example/", max_bytes=100)


def test_declared_length_over_the_cap_fails(net):
    net(FakeDNS([PUBLIC]), {("a.example", "/"): (200, {"Content-Length": "5000"}, b"")})
    with pytest.raises(egress.EgressFailed, match="declares"):
        egress.fetch("https://a.example/", max_bytes=100)


def test_unresolvable_host_is_a_failure_not_a_refusal(monkeypatch):
    def nx(*a, **k):
        raise socket.gaierror("nx")
    monkeypatch.setattr(egress.socket, "getaddrinfo", nx)
    with pytest.raises(egress.EgressFailed, match="does not resolve"):
        egress.fetch("https://nx.example/")
