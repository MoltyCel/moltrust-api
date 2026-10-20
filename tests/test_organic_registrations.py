"""Which registrations count as reach working.

The milestone bot counted every row in `agents` and subtracted the test
fixtures. On 2026-09-21 that read 222, of which 98 came from the bounty market
at roughly 0.16 USDC each — the paid pool alone carried the total past 200 and
the bot offered to draft a post about it. A milestone that can be bought is a
measure of the budget.
"""

from app.funnel import (
    FUNNEL_EPOCH,
    INTERNAL_PLATFORMS,
    PAID_ACQUISITION_BUCKETS,
    PARTNER_PLATFORMS,
    is_internal,
    is_organic,
)


def test_bounty_market_registrations_do_not_count():
    assert is_organic("taskmarket") is False


def test_test_fixtures_do_not_count():
    assert is_organic("test") is False


def test_our_own_agents_do_not_count():
    assert is_organic("system") is False
    assert is_organic("moltrust-internal") is False


def test_moltrust_stays_a_public_example_value():
    """`moltrust` is what the public registry examples use, so an outsider
    reading the docs can send it; treating the string as ours would hide real
    registrations. Our four service agents carrying it are caught by
    agent_type instead."""
    assert is_organic("moltrust", agent_type="external") is True
    assert is_organic("moltrust", agent_type="system") is False


def test_system_agents_are_excluded_by_type_whatever_platform_they_claim():
    """`agent_type` is the authority on whose agent it is. A service agent that
    registered under some other platform value is still ours."""
    assert is_organic("clawhub", agent_type="system") is False
    assert is_organic("clawhub", agent_type="external") is True


def test_the_operator_ip_range_does_not_count():
    """`is_organic` reuses `is_internal`, so the OVH host that Harald works
    from is excluded here as well without a second list."""
    assert is_organic("clawhub", registration_ip="57.129.23.0") is False
    assert is_internal(registration_ip="57.129.23.0") is True


def test_partner_population_does_not_count():
    """Ownify agents arrive through a commercial arrangement, not through
    reach; counting them towards the goal would measure the contract twice."""
    assert is_organic("ownify") is False


def test_a_registry_that_found_us_counts():
    for platform in ("clawhub", "hermes", "a2a", "glama", "erc8004", "rnwy"):
        assert is_organic(platform) is True, platform


def test_an_unknown_platform_counts():
    """Same reasoning as canonical_platform: a pool nobody has classified is
    far more likely to be a stranger who found us than a category we forgot to
    exclude. Excluding by default would silently shrink the number that decides
    whether the acquisition programme worked."""
    assert is_organic("some-registry-nobody-mapped-yet") is True


def test_missing_platform_counts():
    assert is_organic(None) is True
    assert is_organic("") is True


def test_case_and_whitespace_do_not_smuggle_a_paid_row_back_in():
    assert is_organic("  TaskMarket ") is False
    assert is_organic("TEST") is False


def test_the_exclusion_classes_do_not_overlap():
    """An overlap would make the breakdown in the milestone message wrong: a
    row would be attributed to whichever class happened to be checked first."""
    overlaps = (PAID_ACQUISITION_BUCKETS & INTERNAL_PLATFORMS) \
        | (PAID_ACQUISITION_BUCKETS & PARTNER_PLATFORMS) \
        | (INTERNAL_PLATFORMS & PARTNER_PLATFORMS)
    assert not overlaps, f"a platform is in two classes: {sorted(overlaps)}"


# The live mix on the day this was written, as a regression pin. If a change to
# the classes moves these numbers, the milestone moved with them, and that
# should be a deliberate edit rather than a surprise.
#
# (platform, agent_type, internal, count) — `internal` is what
# funnel_is_internal() answers in the database, which is why the same platform
# string appears twice: 24 of the 29 ownify rows and 11 of the 13 klaw rows
# came from our own /24, and the platform column alone cannot see that.
POPULATION_2026_09_21 = [
    ("taskmarket", "external", False, 98),
    ("test", "external", True, 25),
    ("ownify", "external", True, 24),
    ("aeoess", "external", False, 18),
    ("klaw", "external", True, 11),
    ("moltbook", "external", False, 10),
    ("a2a", "external", False, 8),
    ("ownify", "external", False, 5),
    ("moltrust", "system", False, 4),
    ("agentnexus", "external", False, 3),
    ("base", "external", False, 2),
    ("klaw", "external", False, 2),
    ("moltrust", "external", False, 2),
    ("custom", "external", False, 1),
    ("solana", "external", False, 1),
    ("generic", "external", False, 1),
    ("kubernetes", "external", False, 1),
    ("openclaw-k8s", "external", False, 1),
    ("system", "system", True, 1),
    ("openclaw", "external", False, 1),
    ("moltbook", "external", True, 1),
    ("clawhub", "external", False, 1),
    ("github", "external", False, 1),
]


def _organic(row) -> bool:
    platform, agent_type, internal, _n = row
    return not internal and is_organic(platform, agent_type)


def test_the_live_population_reads_53_organic_of_222():
    total = sum(n for *_, n in POPULATION_2026_09_21)
    internal = sum(n for _, _, i, n in POPULATION_2026_09_21 if i)
    organic = sum(n for r in POPULATION_2026_09_21 if _organic(r) for n in (r[3],))
    assert (total, internal, organic) == (222, 62, 53)


def test_no_organic_milestone_has_been_reached_yet():
    """The old counter had already passed 200 on the same population, and
    offered to draft a post about it. The difference is the whole point."""
    organic = sum(r[3] for r in POPULATION_2026_09_21 if _organic(r))
    assert organic // 100 == 0
    assert sum(n for *_, n in POPULATION_2026_09_21) // 100 == 2


def test_the_epoch_is_unchanged():
    """The organic count and the 100-in-90-days goal are the same population
    seen over different windows; a silent epoch move would decouple them."""
    assert (FUNNEL_EPOCH.year, FUNNEL_EPOCH.month, FUNNEL_EPOCH.day) == (2026, 9, 19)
