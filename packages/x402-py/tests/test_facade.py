"""The facade must stay a facade.

A re-export that quietly grows its own copy of a function is the failure this
package was chosen to avoid, so the test asserts object identity rather than
equal behaviour: same function, not a function that happens to agree today.

The invitation is the one thing this package adds on its own. It is listed
here by name: anything else appearing in ``__all__`` is a copy that has
started to drift, and the test is meant to catch exactly that.
"""
import moltrust_enforce.gate as gate
import moltrust_x402
from moltrust_x402 import invite as invite_module

#: What this package owns, over and above the re-exported gate.
OWN_NAMES = {
    "CHALLENGE_URL",
    "INVITE_SOURCE",
    "INVITE_TEXT",
    "REGISTER_URL",
    "gate_block",
    "invite_for",
    "parse_operator_did",
}


def test_every_public_name_is_the_same_object():
    for name in gate.__all__:
        assert hasattr(moltrust_x402, name), f"{name} missing from moltrust_x402"
        assert getattr(moltrust_x402, name) is getattr(gate, name), \
            f"{name} is a copy, not a re-export"


def test_exports_nothing_beyond_the_gate_and_the_invite():
    assert set(moltrust_x402.__all__) == set(gate.__all__) | OWN_NAMES


def test_the_gate_core_does_not_carry_the_invite():
    # The verification core has no opinion about who gets credited for a
    # registration; keeping it that way is why the invite lives here.
    for name in OWN_NAMES:
        assert not hasattr(gate, name), f"{name} leaked into moltrust_enforce.gate"


def test_the_own_names_come_from_the_invite_module():
    for name in OWN_NAMES:
        assert getattr(moltrust_x402, name) is getattr(invite_module, name)
