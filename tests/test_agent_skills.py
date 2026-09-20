"""The URL here comes from an untrusted field, so every test is about refusal.

An agent supplies `agent_card_url` at registration and this module fetches it
from inside our network. Each case below is a way that has been used to turn
such a fetch into a probe of the host that made it.
"""
import pytest

from app.agent_skills import (
    SkillFetchRefused,
    _address_is_public,
    _clean_skills,
    check_url,
    skills_from_agent_card,
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    MAX_SKILLS,
    MAX_SKILL_NAME,
)


class TestAddressChecks:
    @pytest.mark.parametrize("ip", [
        "127.0.0.1", "127.1.2.3",          # loopback
        "10.0.0.1", "172.16.0.1", "192.168.1.1",  # RFC 1918
        "169.254.169.254",                  # the cloud metadata endpoint
        "0.0.0.0",
        "224.0.0.1",                        # multicast
        "::1", "fe80::1", "fc00::1",
        "::ffff:127.0.0.1",                 # IPv4-mapped loopback
        "2002:7f00:1::",                    # 6to4 wrapping 127.0.0.1
    ])
    def test_non_public_addresses_are_refused(self, ip):
        assert _address_is_public(ip) is False

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
    def test_ordinary_public_addresses_pass(self, ip):
        assert _address_is_public(ip) is True

    def test_garbage_is_not_an_address(self):
        assert _address_is_public("not-an-ip") is False
        assert _address_is_public("") is False


class TestUrlChecks:
    @pytest.mark.parametrize("url", [
        "http://example.com/card.json",
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/x",
        "//example.com/card.json",
    ])
    def test_only_https_is_accepted(self, url):
        with pytest.raises(SkillFetchRefused):
            check_url(url)

    def test_credentials_in_the_url_are_refused(self):
        """user@host is how a permitted-looking host is smuggled past a naive
        prefix check."""
        with pytest.raises(SkillFetchRefused):
            check_url("https://evil.com@127.0.0.1/card.json")

    def test_non_standard_ports_are_refused(self):
        for port in (22, 6379, 8080, 3306):
            with pytest.raises(SkillFetchRefused):
                check_url(f"https://example.com:{port}/card.json")

    def test_literal_private_addresses_are_refused(self):
        for host in ("127.0.0.1", "169.254.169.254", "10.0.0.1", "[::1]"):
            with pytest.raises(SkillFetchRefused):
                check_url(f"https://{host}/card.json")

    def test_missing_or_oversized_url(self):
        with pytest.raises(SkillFetchRefused):
            check_url("")
        with pytest.raises(SkillFetchRefused):
            check_url("https://example.com/" + "a" * 2100)

    def test_allowlist_is_applied_before_dns(self):
        with pytest.raises(SkillFetchRefused) as exc:
            check_url("https://example.com/x", host_allowlist=("api.moltrust.ch",))
        assert "allowlist" in str(exc.value)

    def test_allowlist_comparison_ignores_case_and_trailing_dot(self):
        """`API.MolTrust.CH.` is the same host as `api.moltrust.ch`.

        Asserting it gets refused would pass without any normalisation at all,
        so the assertion is the other way round: with the host on the list, the
        call must get *past* the allowlist check. Whatever happens after that
        is DNS, which this test does not care about.
        """
        try:
            check_url("https://API.MolTrust.CH./x", host_allowlist=("api.moltrust.ch",))
        except SkillFetchRefused as exc:
            assert "allowlist" not in str(exc), f"normalisation failed: {exc}"

    def test_the_defaults_are_the_narrow_ones(self):
        assert ALLOWED_SCHEMES == ("https",)
        assert ALLOWED_PORTS == (443,)


class TestSkillParsing:
    def test_reads_a_plain_list(self):
        assert _clean_skills(["verify", "audit"]) == ["verify", "audit"]

    def test_reads_objects_by_name_id_or_skill(self):
        assert _clean_skills([{"name": "verify"}, {"id": "audit"}, {"skill": "issue"}]) \
            == ["verify", "audit", "issue"]

    def test_non_strings_are_dropped_not_coerced(self):
        """A nested object under `name` would otherwise put a Python repr in
        the column."""
        assert _clean_skills([{"name": {"nested": 1}}, 42, None, ["x"]]) == []

    def test_duplicates_collapse(self):
        assert _clean_skills(["a", "a", "A"]) == ["a", "A"]

    def test_count_is_capped(self):
        assert len(_clean_skills([f"s{i}" for i in range(200)])) == MAX_SKILLS

    def test_name_length_is_capped(self):
        assert len(_clean_skills(["x" * 500])[0]) == MAX_SKILL_NAME

    def test_a_non_list_is_not_an_error(self):
        assert _clean_skills(None) == []
        assert _clean_skills("verify") == []
        assert _clean_skills({"a": 1}) == []


class TestAgentCard:
    def test_prefers_skills_then_capabilities_then_tools(self):
        assert skills_from_agent_card({"skills": ["a"], "capabilities": ["b"]}) == ["a"]
        assert skills_from_agent_card({"capabilities": ["b"], "tools": ["c"]}) == ["b"]
        assert skills_from_agent_card({"tools": ["c"]}) == ["c"]

    def test_a_card_with_nothing_usable_is_empty_not_an_error(self):
        assert skills_from_agent_card({}) == []
        assert skills_from_agent_card({"skills": []}) == []
        assert skills_from_agent_card("not a card") == []
        assert skills_from_agent_card(None) == []
