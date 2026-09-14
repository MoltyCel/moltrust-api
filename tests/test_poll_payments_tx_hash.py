"""Transaction hashes must be stored in one spelling.

web3 6 returned ``HexBytes.hex()`` with a ``0x`` prefix; web3 7 dropped it. The
poller kept storing whatever ``.hex()`` gave it, so the same on-chain payment
reached payment_events twice — once prefixed from the x402 verifier, once bare
from the poller — and both duplicate checks compared two spellings of the same
transaction. Observed live on 2026-09-14 with tx 0x1a18114d… and 0x7c42d2a5….
"""
import pytest

from hexbytes import HexBytes

from monitor.poll_payments import TRANSFER_TOPIC, _hex0x

CANONICAL_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)
TX = "0x1a18114d120c889f2aa1e934f983b3482a98805ef0169b73c0c9a181063a6efb"


class TestHex0x:
    def test_adds_the_prefix_to_bare_hexbytes(self):
        assert _hex0x(HexBytes(bytes.fromhex(TX[2:]))) == TX

    def test_leaves_an_already_prefixed_string_alone(self):
        assert _hex0x(TX) == TX

    def test_is_idempotent(self):
        assert _hex0x(_hex0x(TX)) == TX

    def test_lowercases(self):
        assert _hex0x(TX.upper().replace("0X", "0x")) == TX

    @pytest.mark.parametrize("value", [HexBytes(b"\x12\x34"), "1234", "0x1234", "0X1234"])
    def test_every_input_spelling_collapses_to_one(self, value):
        assert _hex0x(value) == "0x1234"

    def test_two_spellings_of_one_transaction_compare_equal(self):
        """The property the duplicate checks in record_to_db depend on."""
        from_verifier = TX
        from_poller = TX[2:]
        assert _hex0x(from_verifier) == _hex0x(from_poller)


def test_transfer_topic_is_the_canonical_erc20_signature():
    """Also unprefixed before the fix, and the same constant the x402
    verifier matches on. If these two ever disagree, the poller and the
    verifier are watching different events."""
    assert TRANSFER_TOPIC == CANONICAL_TRANSFER_TOPIC
