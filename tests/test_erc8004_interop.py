"""ERC-8004 interop: cross-identifiers, and the validator adapter that cannot be built yet.

Reads the source rather than importing app.main — the assertions are about which
identifier appears where, and importing drags in the whole application.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "app" / "main.py").read_text()
ERC = (ROOT / "app" / "erc8004.py").read_text()
DOCS = (ROOT / "docs" / "spec-fakten" / "erc-8004.md").read_text()
MIGRATION = (ROOT / "migrations" / "2026-09-18_erc8004_backfill.sql").read_text()


def _block(src, start, end):
    return src[src.index(start):src.index(end)]


class TestRegistrationFile:
    """A reader arriving from the chain must find the DID, and vice versa."""

    def test_carries_also_known_as(self):
        blk = _block(ERC, "def build_registration_file", "# --- On-Chain Resolver ---")
        assert '"alsoKnownAs": also_known_as' in blk

    def test_always_names_the_did(self):
        blk = _block(ERC, "def build_registration_file", "# --- On-Chain Resolver ---")
        assert "also_known_as = [did]" in blk

    def test_names_the_chain_identity_only_when_known(self):
        """An entry we cannot substantiate is worse than none: a consumer
        cannot tell a missing link from a broken one."""
        blk = _block(ERC, "def build_registration_file", "# --- On-Chain Resolver ---")
        assert "if erc8004_agent_id is not None:" in blk
        assert 'f"{AGENT_REGISTRY_ID}:{erc8004_agent_id}"' in blk

    def test_registry_string_is_not_restated(self):
        """One definition of the CAIP prefix; a copy would drift the day the
        registry moves."""
        assert 'AGENT_REGISTRY_ID = f"eip155:{BASE_CHAIN_ID}:{IDENTITY_REGISTRY}"' in ERC
        assert ERC.count('= f"eip155:') == 1


class TestDidDocument:
    def test_selects_the_column_it_renders(self):
        """The builder reads erc8004_agent_id; the shared SELECT has to fetch
        it or every document silently loses the link."""
        cols = _block(MAIN, "_AGENT_DOC_COLUMNS = (", ")\n\n\ndef _build_did_document")
        assert "erc8004_agent_id" in cols

    def test_emits_also_known_as(self):
        blk = _block(MAIN, "def _build_did_document", "async def _resolve_did_web_external")
        assert 'doc["alsoKnownAs"]' in blk

    def test_stays_silent_without_a_recorded_link(self):
        blk = _block(MAIN, "def _build_did_document", "async def _resolve_did_web_external")
        assert "if erc is not None:" in blk

    def test_tolerates_a_row_without_the_column(self):
        """_build_did_document is shared with /identity/resolve-external, whose
        row shape is not guaranteed to carry it."""
        blk = _block(MAIN, "def _build_did_document", "async def _resolve_did_web_external")
        assert 'in row.keys()' in blk

    def test_imports_the_registry_constant_rather_than_hardcoding_it(self):
        assert "AGENT_REGISTRY_ID as ERC8004_AGENT_REGISTRY_ID" in MAIN
        assert 'eip155:8453:0x8004' not in MAIN


class TestVerifyEndpoint:
    def test_answers_with_both_names(self):
        blk = _block(MAIN, "async def verify_agent", "# Grade → tier mapping")
        assert '"erc8004"' in blk
        assert '"caip"' in blk

    def test_selects_the_id_it_reports(self):
        blk = _block(MAIN, "async def verify_agent", "# Grade → tier mapping")
        assert "erc8004_agent_id FROM agents" in blk

    def test_field_is_present_even_when_unlinked(self):
        """A key that appears only sometimes forces every consumer to guess.
        It is null when there is no link, not absent."""
        blk = _block(MAIN, "async def verify_agent", "# Grade → tier mapping")
        assert '"erc8004": None' in blk


class TestReverseLookup:
    """Somebody holding only an ERC-8004 id must be able to ask what we know."""

    def test_returns_a_moltrust_score(self):
        blk = _block(MAIN, "async def erc8004_resolve", "@app.get(\"/.well-known/agent-registration.json\")")
        assert '"moltrust_trust_score"' in blk

    def test_absent_score_is_not_reported_as_zero(self):
        """Zero is a verdict. Absence is not."""
        blk = _block(MAIN, "async def erc8004_resolve", "@app.get(\"/.well-known/agent-registration.json\")")
        assert 'result["moltrust_trust_score"] = None' in blk

    def test_a_failing_score_does_not_fail_the_lookup(self):
        blk = _block(MAIN, "async def erc8004_resolve", "@app.get(\"/.well-known/agent-registration.json\")")
        assert "except Exception" in blk

    def test_is_not_rate_limited_into_uselessness(self):
        """Free and reachable is the point; 10/minute is the existing limit and
        this test exists so a future tightening is a decision, not a drift."""
        blk = _block(MAIN, '@app.get("/resolve/erc8004/{agent_id}")', "async def erc8004_resolve")
        assert "10/minute" in blk


class TestBackfillMigration:
    def test_records_a_fact_verified_on_chain(self):
        assert "21351" in MIGRATION
        assert "did:moltrust:455d06aa3d9d4fac" in MIGRATION
        assert "ownerOf" in MIGRATION

    def test_is_idempotent_and_does_not_overwrite(self):
        assert "erc8004_agent_id IS NULL" in MIGRATION

    def test_touches_nothing_else(self):
        for verb in ("DROP", "DELETE", "INSERT", "ALTER", "TRUNCATE"):
            assert verb not in MIGRATION.upper()


class TestValidatorPathIsSpecOnly:
    """The ValidationRegistry is deployed nowhere. Nothing may write to it."""

    def test_no_validation_registry_address_in_the_codebase(self):
        for src in (MAIN, ERC):
            assert "validationResponse" not in src
            assert "validationRequest" not in src

    def test_docs_state_the_deployment_status_with_evidence(self):
        assert "ValidationRegistry ist nirgends deployt" in DOCS
        assert "0x8004A818" in DOCS  # the testnet vanity pattern, checked and empty

    def test_docs_give_the_target_signature(self):
        assert "validationResponse(bytes32 requestHash" in DOCS

    def test_docs_name_the_three_preconditions(self):
        assert "ERC8004_VALIDATION_ENABLED" in DOCS
        assert "Gas-Budget" in DOCS
        assert "Signer menschengesteuert" in DOCS

    def test_docs_point_at_the_registry_that_is_deployed(self):
        """ReputationRegistry is live on Base and already written to — the
        shorter route to the same goal."""
        assert "0x8004BAa1" in DOCS
        assert "post_reputation_feedback" in DOCS


class TestDocsRecordWhatWasFound:
    def test_lists_all_four_token_ids(self):
        for tok in ("21023", "21351", "21352", "33553"):
            assert tok in DOCS

    def test_records_the_two_contradictions_as_open(self):
        assert "21023 nennt 33553" in DOCS
        assert "Offen" in DOCS

    def test_explains_why_the_registrations_were_empty(self):
        """It was a data gap, not a code bug — worth stating so the next
        reader does not go looking in the builder."""
        assert "kein Code-Fehler" in DOCS

    def test_warns_about_the_blockscout_index_gap(self):
        assert "lückenhaft" in DOCS
