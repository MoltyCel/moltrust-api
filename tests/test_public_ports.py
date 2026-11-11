"""Only listeners reachable from outside count as open ports (ops SEC-1025-SRV)."""
import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location(
    "public_ports",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "public_ports.py")
pp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pp)

# `ss -tlnH` on api.moltrust.ch, 25.10.2026 16:14Z, process column dropped.
LIVE_20261025 = """\
LISTEN 0 511 127.0.0.1:6379 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:8282 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:33087 0.0.0.0:*
LISTEN 0 511 127.0.0.1:3006 0.0.0.0:*
LISTEN 0 511 127.0.0.1:3003 0.0.0.0:*
LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:9050 0.0.0.0:*
LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*
LISTEN 0 511 0.0.0.0:80 0.0.0.0:*
LISTEN 0 511 0.0.0.0:443 0.0.0.0:*
LISTEN 0 200 127.0.0.1:5432 0.0.0.0:*
LISTEN 0 50 127.0.0.1:9740 0.0.0.0:*
LISTEN 0 4096 127.0.0.54:53 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:8168 0.0.0.0:*
LISTEN 0 2048 127.0.0.1:8004 0.0.0.0:*
LISTEN 0 2048 127.0.0.1:8002 0.0.0.0:*
LISTEN 0 2048 127.0.0.1:8001 0.0.0.0:*
LISTEN 0 2048 127.0.0.1:8000 0.0.0.0:*
LISTEN 0 200 [::1]:5432 [::]:*
LISTEN 0 511 [::1]:6379 [::]:*
LISTEN 0 4096 [::]:22 [::]:*
LISTEN 0 511 [::]:80 [::]:*
LISTEN 0 511 [::]:443 [::]:*
""".splitlines()


def test_the_live_host_exposes_only_ssh_and_web():
    # Silence test: normal operation must not report 3006 or any other
    # loopback-only service.
    assert pp.public_ports(LIVE_20261025) == [22, 80, 443]


def test_a_loopback_listener_is_not_open():
    assert pp.public_ports(["LISTEN 0 511 127.0.0.1:3006 0.0.0.0:*"]) == []
    assert pp.public_ports(["LISTEN 0 511 [::1]:3006 [::]:*"]) == []
    assert pp.public_ports(["LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*"]) == []


def test_a_wildcard_or_public_listener_is_open():
    assert pp.public_ports(["LISTEN 0 511 0.0.0.0:3006 0.0.0.0:*"]) == [3006]
    assert pp.public_ports(["LISTEN 0 511 [::]:3006 [::]:*"]) == [3006]
    assert pp.public_ports(["LISTEN 0 511 *:3006 *:*"]) == [3006]
    assert pp.public_ports(["LISTEN 0 511 46.225.175.218:3006 0.0.0.0:*"]) == [3006]


def test_a_port_open_on_any_address_counts_once():
    lines = ["LISTEN 0 511 127.0.0.1:8168 0.0.0.0:*",
             "LISTEN 0 511 0.0.0.0:8168 0.0.0.0:*",
             "LISTEN 0 511 [::]:8168 [::]:*"]
    assert pp.public_ports(lines) == [8168]


def test_header_and_non_listen_lines_are_ignored():
    lines = ["State Recv-Q Send-Q Local Address:Port Peer Address:Port Process",
             "ESTAB 0 0 46.225.175.218:443 1.2.3.4:5555", ""]
    assert pp.public_ports(lines) == []
