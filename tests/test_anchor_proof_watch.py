"""The weekly self-check that our published proof actually replays.

An external auditor did this by hand on 2026-09-20 and found three defects. The
check exists so the next one is ours, a week earlier.

It deliberately shares no code with the thing it checks: a verifier built from
the same functions agrees with the implementation by construction and would
have passed while `merkle_proof` was being published as null.
"""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "agents" / "watchdog.py").read_text()

NS: dict = {"hashlib": hashlib, "httpx": None}
exec(SRC[SRC.index("def _replay_merkle"):SRC.index("def check_anchor_proof_replay")], NS)
replay = NS["_replay_merkle"]


def _sha(*parts: bytes) -> bytes:
    return hashlib.sha256(b"".join(parts)).digest()


class TestReplay:
    """Built from a tree made here, so the expected root is independent."""

    A = hashlib.sha256(b"a").digest()
    B = hashlib.sha256(b"b").digest()
    C = hashlib.sha256(b"c").digest()
    D = hashlib.sha256(b"d").digest()

    def test_one_step(self):
        root = _sha(self.A, self.B).hex()
        got = replay(self.A.hex(), [{"hash": self.B.hex(), "position": "right"}])
        assert got == root

    def test_the_side_matters(self):
        """Swapping the side gives a different root — which is the whole reason
        `position` is in the proof."""
        got = replay(self.A.hex(), [{"hash": self.B.hex(), "position": "left"}])
        assert got == _sha(self.B, self.A).hex()
        assert got != _sha(self.A, self.B).hex()

    def test_two_levels(self):
        ab, cd = _sha(self.A, self.B), _sha(self.C, self.D)
        root = _sha(ab, cd).hex()
        got = replay(self.A.hex(), [
            {"hash": self.B.hex(), "position": "right"},
            {"hash": cd.hex(), "position": "right"},
        ])
        assert got == root

    def test_raw_bytes_not_hex_text(self):
        """Hashing the hex string builds a different tree.

        This is the mistake anchoring.html spells out, so the test names it
        rather than leaving it to the reader.
        """
        wrong = hashlib.sha256((self.A.hex() + self.B.hex()).encode()).hexdigest()
        assert replay(self.A.hex(), [{"hash": self.B.hex(), "position": "right"}]) != wrong

    def test_a_tampered_sibling_does_not_reach_the_root(self):
        root = _sha(self.A, self.B).hex()
        tampered = replay(self.A.hex(), [{"hash": self.C.hex(), "position": "right"}])
        assert tampered != root

    def test_an_empty_path_returns_the_leaf(self):
        """A single-leaf tree is its own root; anything else with an empty path
        fails the root comparison in the caller rather than here."""
        assert replay(self.A.hex(), []) == self.A.hex()


class TestCheckShape:
    BLOCK = SRC[SRC.index("def check_anchor_proof_replay"):SRC.index("def run():")]

    def test_it_uses_the_public_api_not_the_database(self):
        """A stranger has no database. Reading one here would check something
        nobody else can see."""
        assert "api.moltrust.ch/identity/verify" in self.BLOCK
        assert "db_pool" not in self.BLOCK
        assert "conn" not in self.BLOCK

    def test_a_missing_proof_is_a_failure_not_a_skip(self):
        """The state this exists to catch: an anchor that looks fine in the
        response and cannot be verified by anyone."""
        assert "has no usable proof" in self.BLOCK

    def test_the_root_is_checked_against_the_chain(self):
        assert "eth_getTransactionByHash" in self.BLOCK
        assert 'calldata.rsplit("/", 1)[-1] != root' in self.BLOCK

    def test_calldata_is_read_as_utf8(self):
        """Decoding it as ABI finds nothing and reads as an empty anchor."""
        assert '.decode("utf-8", "replace")' in self.BLOCK
        assert "ANCHOR_CALLDATA_PREFIX" in self.BLOCK

    def test_an_unreachable_dependency_is_reported_not_swallowed(self):
        """A check that answers ok when it could not look is worse than none."""
        assert self.BLOCK.count('"ok": False') >= 5
        assert '"ok": True' in self.BLOCK

    def test_it_runs_weekly(self):
        assert "PROOF_CHECK_WEEKDAY = 6" in SRC
        assert "now.weekday() == PROOF_CHECK_WEEKDAY" in SRC
