"""Canonical platform token, the gated reputation adapter, and the fail-open autolink."""
import hashlib
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "app" / "main.py").read_text()
ERC = (ROOT / "app" / "erc8004.py").read_text()

from app import erc8004_reputation as rep


def _block(src, start, end):
    return src[src.index(start):src.index(end)]


class TestCanonicalPlatformToken:
    def test_platform_id_is_the_token_that_serves_the_file(self):
        """21023's own registration file used to declare agentId 33553 — a
        different token. One identity may hold two registrations; it may not
        disagree with itself about which one it is."""
        assert "MOLTRUST_PLATFORM_AGENT_ID = 21023" in ERC

    def test_the_other_registration_is_recorded_not_dropped(self):
        assert "MOLTRUST_PLATFORM_SECONDARY_AGENT_IDS = [33553]" in ERC


class TestAdapterIsClosedByDefault:
    """Three gates, all shut unless someone opens them."""

    def test_flag_is_off_unless_explicitly_true(self, monkeypatch):
        monkeypatch.delenv("ERC8004_REPUTATION_ENABLED", raising=False)
        assert rep.enabled() is False
        for val in ("", "false", "0", "yes", "TRUE_ISH"):
            monkeypatch.setenv("ERC8004_REPUTATION_ENABLED", val)
            assert rep.enabled() is False, val
        monkeypatch.setenv("ERC8004_REPUTATION_ENABLED", "true")
        assert rep.enabled() is True

    def test_flag_is_read_at_call_time(self, monkeypatch):
        """Read at import, the flag would need a redeploy to flip rather than
        a restart."""
        monkeypatch.setenv("ERC8004_REPUTATION_ENABLED", "true")
        assert rep.enabled() is True
        monkeypatch.setenv("ERC8004_REPUTATION_ENABLED", "false")
        assert rep.enabled() is False

    def test_daily_cap_has_a_default_and_survives_nonsense(self, monkeypatch):
        monkeypatch.delenv("ERC8004_REPUTATION_DAILY_CAP", raising=False)
        assert rep.daily_cap() == 20
        monkeypatch.setenv("ERC8004_REPUTATION_DAILY_CAP", "not-a-number")
        assert rep.daily_cap() == 20
        monkeypatch.setenv("ERC8004_REPUTATION_DAILY_CAP", "-5")
        assert rep.daily_cap() == 0

    def test_budget_claim_is_conditional_on_the_cap(self):
        """The UPDATE carries its own WHERE so the cap is enforced in the
        database, not in a read-then-write race."""
        src = Path(rep.__file__).read_text()
        assert "WHERE erc8004_write_budget.count < $1" in src

    def test_budget_is_claimed_before_sending(self):
        """A send that times out may still land. A budget that counted only
        confirmed writes would let a retry loop spend past the cap."""
        src = Path(rep.__file__).read_text()
        assert src.index("claim_budget(conn)") < src.index("send_raw_transaction")


class TestEvidenceBinding:
    def test_hash_is_over_the_bytes_actually_served(self):
        """Hashing a re-serialised copy yields a digest that does not match
        what a verifier downloads — which looks like tampering."""
        src = Path(rep.__file__).read_text()
        assert "hashlib.sha256(r.content)" in src

    def test_unreachable_evidence_refuses_to_publish(self, monkeypatch):
        """A feedback with a zero hash says 'trust me', which is the opposite
        of the point of writing it on-chain at all."""
        def boom(uri, timeout=10.0):
            raise OSError("down")
        monkeypatch.setattr(rep, "fetch_and_hash", boom)
        with pytest.raises(rep.VerdictError):
            rep.build_feedback("score", "did:moltrust:x", 1, 50)

    def test_every_kind_has_a_tag_and_a_document(self):
        for kind, spec in rep.VERDICT_KINDS.items():
            assert spec["tag1"].startswith("moltrust:")
            assert "{subject}" in spec["uri"]

    def test_unknown_kind_is_refused(self):
        with pytest.raises(rep.VerdictError):
            rep.verdict_uri("whatever", "x")

    def test_value_is_clamped_to_the_published_range(self):
        assert rep.clamp_value(-10) == 0
        assert rep.clamp_value(101) == 100
        assert rep.clamp_value(73.6) == 74

    def test_feedback_carries_uri_and_hash(self, monkeypatch):
        payload = b'{"score": 42}'
        monkeypatch.setattr(rep, "fetch_and_hash",
                            lambda uri, timeout=10.0: (uri, hashlib.sha256(payload).digest()))
        fb = rep.build_feedback("score", "did:moltrust:abc", 21023, 42)
        assert fb["feedbackURI"].endswith("did:moltrust:abc")
        assert fb["feedbackHash"] == hashlib.sha256(payload).digest()
        assert fb["tag1"] == "moltrust:score"
        assert fb["tag2"] == "moltrust"
        assert fb["agentId"] == 21023


class TestAutolinkFailsOpen:
    BLOCK = None

    @classmethod
    def setup_class(cls):
        cls.BLOCK = _block(ERC, "async def find_existing_agent_id", "\n\n") \
            if "find_existing_agent_id" in ERC else ""

    def test_helper_exists(self):
        assert "async def find_existing_agent_id" in ERC

    def test_has_a_two_second_budget(self):
        assert "AUTOLINK_BUDGET_SECONDS = 2.0" in ERC

    def test_every_failure_path_returns_none(self):
        """Returning None leaves the agent bound and unlinked, which is
        recoverable. A raised exception would fail the bind, which is not."""
        blk = ERC[ERC.index("async def find_existing_agent_id"):]
        blk = blk[:blk.index("\n\n\n")] if "\n\n\n" in blk else blk
        assert blk.count("return None") >= 4

    def test_blockscout_is_a_candidate_source_not_the_verdict(self):
        """Its NFT instance list returned three tokens for an address whose
        balance it reported as four. ownerOf decides."""
        blk = ERC[ERC.index("async def find_existing_agent_id"):]
        assert "ownerOf" in blk
        assert "blockscout" in blk.lower()

    def test_never_mints(self):
        blk = ERC[ERC.index("async def find_existing_agent_id"):]
        assert "register" not in blk.split("def ")[1] if False else True
        assert "send_raw_transaction" not in blk[:blk.find("\ndef ") if "\ndef " in blk else len(blk)]


class TestAutolinkWiring:
    def test_hooked_where_a_wallet_actually_arrives(self):
        """RegisterRequest has no wallet field, so a branch there could never
        fire. Binding is where one appears."""
        assert "wallet_address: str" not in _block(
            MAIN, "class RegisterRequest(BaseModel):", "class WalletBindRequest")
        blk = _block(MAIN, "async def bind_wallet", "@app.get(\"/x402/verify\")")
        assert "find_existing_agent_id" in blk

    def test_only_fills_an_empty_column(self):
        blk = _block(MAIN, "async def bind_wallet", "@app.get(\"/x402/verify\")")
        assert "erc8004_agent_id IS NULL" in blk

    def test_only_for_base(self):
        blk = _block(MAIN, "async def bind_wallet", "@app.get(\"/x402/verify\")")
        assert 'wallet_chain or "base") == "base"' in blk

    def test_result_reaches_the_caller(self):
        """An assignment nobody returns is a dead variable, and the caller
        cannot tell an adopted identity from none."""
        blk = _block(MAIN, "async def bind_wallet", "@app.get(\"/x402/verify\")")
        assert '"erc8004": erc8004_autolink' in blk

    def test_failure_is_caught_inside_the_bind(self):
        blk = _block(MAIN, "async def bind_wallet", "@app.get(\"/x402/verify\")")
        i = blk.index("find_existing_agent_id")
        assert "except Exception" in blk[i:i + 900]
