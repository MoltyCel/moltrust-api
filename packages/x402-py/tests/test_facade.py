"""The facade must stay a facade.

A re-export that quietly grows its own copy of a function is the failure this
package was chosen to avoid, so the test asserts object identity rather than
equal behaviour: same function, not a function that happens to agree today.
"""
import moltrust_enforce.gate as gate
import moltrust_x402


def test_every_public_name_is_the_same_object():
    for name in gate.__all__:
        assert hasattr(moltrust_x402, name), f"{name} missing from moltrust_x402"
        assert getattr(moltrust_x402, name) is getattr(gate, name), \
            f"{name} is a copy, not a re-export"


def test_exports_nothing_the_gate_does_not_export():
    assert set(moltrust_x402.__all__) == set(gate.__all__)
