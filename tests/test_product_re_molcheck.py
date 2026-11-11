"""Both outbound gates refuse our product names, MolCheck included (ops MC-API).

MoltProof is now MolCheck. A draft naming the product under its new name must
fail the same check the old name fails; ordinary words must not.
"""
import pytest

from agents import comment_gate, reply_radar


@pytest.mark.parametrize("gate", [comment_gate, reply_radar])
@pytest.mark.parametrize("text", ["Try MolCheck for this", "molcheck verdict", "MoltProof does that"])
def test_product_name_is_caught(gate, text):
    assert gate.PRODUCT_RE.search(text)


@pytest.mark.parametrize("gate", [comment_gate, reply_radar])
@pytest.mark.parametrize("text", ["a mol check in chemistry", "check the mandate", "proof of work"])
def test_ordinary_words_pass(gate, text):
    assert not gate.PRODUCT_RE.search(text)
