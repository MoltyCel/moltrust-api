"""Metadata anchor on self-minted ERC-8004 IDs (BIND-CHECK 3). No chain call.

Red: a missing or a different entry is a deviation. Green: the exact DID bytes
match. Sending refuses without both arming conditions.
"""
import pytest
from eth_utils import keccak

from app import erc8004_anchor as ea

DID = "did:moltrust:0123456789abcdef"


def test_matching_entry_is_a_match():
    assert ea.check_anchor(DID.encode(), DID) == ea.MATCH


@pytest.mark.parametrize("stored", [b"", None])
def test_missing_entry_is_a_deviation(stored):
    assert ea.check_anchor(stored, DID) == ea.MISSING


@pytest.mark.parametrize("stored", [b"did:moltrust:ffffffffffffffff", DID.encode() + b"\x00",
                                    keccak(text=DID), DID.upper().encode()])
def test_different_entry_is_a_deviation(stored):
    assert ea.check_anchor(stored, DID) == ea.MISMATCH


def test_verify_anchor_reads_through_the_given_reader():
    assert ea.verify_anchor(7, DID, read=lambda a: DID.encode()) == ea.MATCH
    assert ea.verify_anchor(7, DID, read=lambda a: b"") == ea.MISSING


def test_value_is_only_a_moltrust_did():
    with pytest.raises(ValueError):
        ea.anchor_value("did:web:example.com")


def test_set_metadata_call_encodes_key_and_value(monkeypatch):
    from web3 import Web3
    monkeypatch.setattr("app.erc8004._get_w3", lambda: Web3())  # encoding only, no provider
    data = ea.build_anchor_call(95131, DID)._encode_transaction_data()
    raw = bytes.fromhex(data[2:])
    assert raw[:4] == keccak(text="setMetadata(uint256,string,bytes)")[:4]
    assert int.from_bytes(raw[4:36], "big") == 95131
    assert ea.METADATA_KEY.encode() in raw and DID.encode() in raw


def test_send_refuses_unless_armed_twice(monkeypatch):
    monkeypatch.delenv("BIND_CHECK_ANCHOR_ARMED", raising=False)
    with pytest.raises(ea.AnchorNotArmed):
        ea.send_anchor(1, DID, "a2a", armed=True)
    monkeypatch.setenv("BIND_CHECK_ANCHOR_ARMED", "1")
    with pytest.raises(ea.AnchorNotArmed):
        ea.send_anchor(1, DID, "a2a")


def test_send_refuses_a_test_agent_even_when_armed(monkeypatch):
    monkeypatch.setenv("BIND_CHECK_ANCHOR_ARMED", "1")
    with pytest.raises(ea.AnchorRefused):
        ea.send_anchor(1, DID, "test", armed=True)


async def test_self_minted_links_are_those_the_signer_owns():
    signer = "0x" + "8e" * 20

    class Conn:
        async def fetch(self, q):
            return [{"agent_id": 1, "did": "did:moltrust:a", "platform": "a2a"},
                    {"agent_id": 2, "did": "did:moltrust:b", "platform": "base"}]

    owners = {1: signer.upper().replace("0X", "0x"), 2: "0x" + "11" * 20}
    got = await ea.self_minted_links(Conn(), owners.__getitem__, signer)
    assert got == [(1, "did:moltrust:a", "a2a")]
