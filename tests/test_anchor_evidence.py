"""External audit 2026-09-20: anchor evidence has to stand on its own.

Four findings were reported. Three were real; one was a correct number read
against the wrong endpoint. The tests below pin the three fixes and the
distinction behind the fourth.
"""
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "app" / "main.py").read_text()
IPR = (ROOT / "app" / "provenance" / "ipr.py").read_text()


def _exec_block(src: str, start: str, end: str, ns: dict) -> dict:
    """Run one block of a module that cannot be imported whole here."""
    exec(src[src.index(start):src.index(end)], ns)
    return ns


class TestMalformedIprId:
    """(a) /vc/ipr/1 answered 500, so a wrong id looked like a broken endpoint.

    The proof endpoint itself was never broken: with a real UUID it replays the
    Merkle proof and returns verified: true. Only the id parse escaped.
    """

    NS = _exec_block(IPR, "class InvalidIprId", "async def get_ipr", {"uuid": uuid})

    def test_a_real_uuid_parses(self):
        parsed = self.NS["parse_ipr_id"]("b9a9ab5f-c7cd-4db5-a488-a54188a7e1e0")
        assert str(parsed) == "b9a9ab5f-c7cd-4db5-a488-a54188a7e1e0"

    def test_anything_else_raises_the_named_error(self):
        for bad in ("1", "", "abc", None, 123, "not-a-uuid"):
            try:
                self.NS["parse_ipr_id"](bad)
            except self.NS["InvalidIprId"]:
                continue
            raise AssertionError(f"{bad!r} should not parse")

    def test_no_raw_uuid_call_is_left_in_the_module(self):
        """One guarded path and two unguarded ones is still a 500."""
        assert "uuid.UUID(ipr_id)" not in IPR

    def test_every_handler_turns_it_into_a_400(self):
        assert MAIN.count("except InvalidIprId as e:") == 3
        assert MAIN.count('raise HTTPException(400, str(e))') >= 3


class TestKeyAnchorSource:
    """(c) /identity/key said anchor_verified false for agents /identity/verify
    was showing an anchor for. Two columns, one fact.

    Measured on 2026-09-20: base_tx_hash populated for 171 of 191 agents,
    key_anchor_tx for 1.
    """

    BLOCK = MAIN[MAIN.index("async def get_agent_public_key"):]
    BLOCK = BLOCK[:BLOCK.index("# --- DID-Wallet Binding Endpoints ---")]

    def test_it_reads_the_same_column_identity_verify_reads(self):
        assert "base_tx_hash" in self.BLOCK

    def test_the_dedicated_column_still_wins_when_set(self):
        """A real key anchor is more specific than the registration anchor."""
        assert 'row["key_anchor_tx"] or row["base_tx_hash"]' in self.BLOCK

    def test_the_answer_says_which_anchor_it_is(self):
        """Otherwise the caller cannot tell a key anchor from a registration
        anchor that covers the key by containing it."""
        assert '"anchor_source"' in self.BLOCK

    def test_verified_follows_the_resolved_anchor(self):
        assert '"anchor_verified": anchor_tx is not None' in self.BLOCK


class TestInlineMerkleProof:
    """(d) A verifier had to call the IPR endpoint for the proof. It cannot
    check us while that endpoint is down, which is when it matters."""

    BLOCK = MAIN[MAIN.index('result["credentials"] = ['):]
    BLOCK = BLOCK[:BLOCK.index("await update_last_seen(did)")]

    def test_the_proof_ships_with_the_credential(self):
        assert '"merkle_proof": _anchor_proof(c["merkle_proof"])' in self.BLOCK

    def test_the_query_actually_selects_it(self):
        assert "a.merkle_root, a.merkle_proof" in MAIN

    def test_the_leaf_rule_is_linked(self):
        assert '"leaf_rule": ANCHOR_LEAF_RULE_URL' in self.BLOCK

    def test_the_calldata_format_is_stated(self):
        """Calldata is a UTF-8 string, not ABI. An auditor decoding it as ABI
        finds nothing and concludes the anchor is empty."""
        assert '"calldata_format": ANCHOR_CALLDATA_FORMAT' in self.BLOCK
        assert 'ANCHOR_CALLDATA_FORMAT = "utf8:MolTrust/VC/v1/<merkle_root>"' in MAIN


class TestAnchorProofShape:
    NS = _exec_block(MAIN, "def _anchor_proof", "ANCHOR_LEAF_RULE_URL",
                     {"json": __import__("json")}) if "def _anchor_proof" in MAIN else {}

    def _f(self):
        ns = {"json": __import__("json")}
        exec(MAIN[MAIN.index("def _anchor_proof"):MAIN.index('@app.post("/credentials/admin/anchor"')], ns)
        return ns["_anchor_proof"]

    def test_a_json_array_becomes_a_list(self):
        assert self._f()('["a","b"]') == ["a", "b"]

    def test_a_list_passes_through(self):
        assert self._f()(["x"]) == ["x"]

    def test_bytes_decode(self):
        assert self._f()(b'["c"]') == ["c"]

    def test_absent_and_unparseable_are_both_none(self):
        """None means no proof. Returning a broken string would make a verifier
        fail on our encoding rather than on the evidence."""
        for v in (None, "", "kaputt", '{"a":1}'):
            assert self._f()(v) is None
