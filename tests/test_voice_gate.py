"""Pre-send scan: the patterns it must catch, and the prose it must let through.

The false-negative half is obvious. The false-positive half matters just as
much: a gate that blocks ordinary sentences gets switched off, and then nothing
is checked at all. Every "clean" case below is either a real published MolTrust
sentence or one built from the same material.

The rules live in moltrust-web's docs/pre-send-scan.md. These tests run against
a fixture copy so CI does not depend on a shallow clone being present.
"""
import re
from pathlib import Path

import pytest

from agents import voice_gate

SPEC = Path(__file__).parent / "fixtures" / "pre-send-scan.md"
ANTI_KI = Path(__file__).parent / "fixtures" / "anti-KI-Sprech.md"


@pytest.fixture(autouse=True)
def _rules(monkeypatch):
    """Load the rules from the fixtures, never from the network or the clone."""
    voice_gate._CACHE.clear()
    monkeypatch.setattr(voice_gate, "DOC_SCAN", SPEC)
    monkeypatch.setattr(voice_gate, "DOC_ANTI_KI", ANTI_KI)
    monkeypatch.setattr(voice_gate, "refresh_docs", lambda: None)
    yield
    voice_gate._CACHE.clear()


def failing(result) -> set:
    out = set()
    for key in ("gate1", "gate2"):
        out |= {k for k, v in result[key].items() if v == "fail"}
    return out


def scan(parts, **kw):
    return voice_gate.scan(parts, refresh=False, **kw)


# ── the spec itself ──

def test_spec_parses_into_rules_and_lexicons():
    spec = voice_gate.load_rules(refresh=False)
    ids = {r["id"] for r in spec["rules"]}
    assert {"g1a", "g1b", "g1c", "g1d", "g1e", "g1f"} <= ids
    assert {"g2a", "g2b", "g2c", "g2d", "g2e", "g2f", "g2g"} <= ids
    assert spec["lexicons"]["copula"]
    assert "smart" in spec["lexicons"]["eval_core"]


def test_every_pattern_in_the_spec_compiles():
    for rule in voice_gate.load_rules(refresh=False)["rules"]:
        for pattern in rule.get("patterns", []):
            re.compile(pattern)


def test_banned_words_come_from_anti_ki_sprech():
    banned = voice_gate.load_rules(refresh=False)["banned"]
    for term in ("delve", "seamless", "leverage", "game-changer", "maßgeschneidert"):
        assert term in banned, term
    assert "Signpost-/Räuspern-Wörter" not in banned
    assert all(len(t) >= 3 for t in banned)


def test_missing_spec_raises_rather_than_passing_everything(monkeypatch):
    voice_gate._CACHE.clear()
    monkeypatch.setattr(voice_gate, "DOC_SCAN", Path("/nonexistent/pre-send-scan.md"))
    with pytest.raises(RuntimeError):
        voice_gate.load_rules(refresh=False)


# ── Gate 1, the six ──

def test_a_counterpoint_en_and_de():
    assert "g1a" in failing(scan(["This is not a reputation score, but a receipt "
                                  "anyone can recompute. 40 of them so far."]))
    assert "g1a" in failing(scan(["Das ist nicht ein Score, sondern ein Beleg. 40 Stück."]))


def test_a_counterpoint_catches_the_old_herald_cadence():
    """A real 2026-09-20 post. The em-dash counterpoint was the house style
    before the gate existed; it must not pass now."""
    assert "g1a" in failing(scan([
        "Someone just dumped $6.2M into a Russian election market in 24h. The price "
        "barely moved. That's not conviction—that's coordination. "
        "https://moltrust.ch/integrity.html"], mode="post"))


def test_b_validation_opener_anywhere_in_the_thread():
    r = scan(["Polymarket moved $6.2M in 24h on one market.",
              "Great question. The signal is the price that did not move.",
              "Full data: https://moltrust.ch/integrity.html"])
    assert "g1b" in failing(r)


def test_c_parallel_negation_needs_three():
    assert "g1c" in failing(scan(["No registry, no receipts, no recourse. 50 markets."]))
    assert "g1c" not in failing(scan(["No registry, no receipts. 50 markets scanned."]))


def test_d_identity_coda_only_at_the_end():
    assert "g1d" in failing(scan(["We scanned 50 markets today. That's who we are."]))
    # The same words in a non-final sentence are not the coda pattern. (In a
    # one-sentence tweet the only sentence is both opener and coda, so the
    # negative case needs a second sentence after it.)
    assert "g1d" not in failing(scan(["That's who we are was the old tagline. "
                                      "50 markets scanned says more."]))


def test_e_evaluative_copula_core_and_context():
    assert "g1e" in failing(scan(["The spec is clever. 9 steps, all checkable."]))
    assert "g1e" in failing(scan(["Your approach is right for 3 of the 9 steps."]))
    # A factual predicate with the same shape must survive.
    assert "g1e" not in failing(scan(["The signal is a $6.2M swing with no price move."]))
    assert "g1e" not in failing(scan(["Volume is 50 markets deep and still flat."]))


def test_f_judgement_filler_blocks_only_without_evidence():
    assert "g1f" in failing(scan(["An interesting pattern in prediction markets. 70/100."]))
    ok = scan(["The pattern matters because 3 of 50 markets carried a $6.2M swing "
               "with no price move."])
    assert "g1f" not in failing(ok)


# ── Gate 1, the extras ──

def test_superlative_chain():
    assert "g1x_superlative" in failing(
        scan(["The biggest and fastest registry of the 50 we scanned."]))
    assert "g1x_superlative" not in failing(
        scan(["The biggest swing of the day was $6.2M across 50 markets."]))


def test_rhetorical_opener():
    assert "g1x_rhetorical_opener" in failing(
        scan(["Ever wondered who checks the agents? 50 markets say nobody."]))


def test_fragment_coda():
    assert "g1x_fragment_coda" in failing(
        scan(["Someone moved $6.2M in 24h and the price held. The proof gap."]))
    assert "g1x_fragment_coda" not in failing(
        scan(["Someone moved $6.2M in 24h. Three of 50 markets flagged."]))


def test_triad_running_into_a_question():
    assert "g1x_triad_question" in failing(scan([
        "Identity says who. Reputation says what happened. Neither says allowed. "
        "So who checks the 50?"]))


# ── Gate 2 ──

def test_link_discipline_by_mode():
    thread = ["A $6.2M swing with no price move.", "Two NFL games did the same.",
              "https://moltrust.ch/integrity.html"]
    assert "g2e" not in failing(scan(thread, mode="thread"))
    assert "g2e" in failing(scan(thread, mode="reply"))
    single = ["A $6.2M swing, no price move. https://moltrust.ch/integrity.html"]
    assert "g2e" not in failing(scan(single, mode="post"))


def test_g_numbers_must_exist_in_the_source():
    src = "The market moved 6200000 dollars across 50 markets."
    assert "g2g" in failing(scan(["A $9.9M swing across 50 markets."], source_text=src))
    assert "g2g" not in failing(scan(["6200000 moved across 50 markets."], source_text=src))
    assert scan(["50 markets."])["gate2"]["g2g"].startswith("skipped")


def test_g_scaled_figures_are_checked_even_at_two_digits():
    """"$9.9M" shows two digits and claims 9,900,000 — the digit-count floor
    must not let it through."""
    src = "volume changed by 6188051.748 dollars in 24h"
    assert voice_gate.ungrounded_numbers(["A $9.9M swing."], src) == ["$9.9M".lstrip("$")]
    # Correct rounding of the same figure is grounded, not blocked.
    assert voice_gate.ungrounded_numbers(["A $6.2M swing."], src) == []


def test_g_thousands_and_decimal_separators():
    src = "1,250,000 in volume and 3.5M agents"
    assert voice_gate.ungrounded_numbers(["1,250,000 in volume"], src) == []
    assert voice_gate.ungrounded_numbers(["3.5M agents"], src) == []
    assert voice_gate.ungrounded_numbers(["4.9M agents"], src) == ["4.9M"]


def test_substance_floor_catches_length_and_missing_numbers():
    assert "g2f" in failing(scan(["A market moved and the price held flat."]))
    assert "g2f" in failing(scan(["x" * 281 + " 50"]))


def test_opener_may_not_open_on_us():
    assert "g2d" in failing(scan(["We scanned 50 markets today.",
                                  "https://moltrust.ch/integrity.html"]))


# ── the shape that must get through ──

def test_a_real_digest_passes_both_gates():
    tweet = ("A Russian parliamentary election market took a $6.2M swing in 24h volume "
             "while price held flat. Two NFL games show the same divergence. All three "
             "scored 70/100 across 50 markets scanned.\n\n"
             "https://moltrust.ch/integrity.html")
    result = scan([tweet], mode="post")
    assert result["ok"], voice_gate.format_report(result)


def test_a_real_thread_passes_both_gates():
    parts = [
        "Over 1,000 agents coordinated an attack on Hugging Face, some of them "
        "sacrificing themselves so the group would get through. Nobody ordered it.",
        "The agents had a goal, broad access, and no enforceable boundary. That was "
        "enough. Bad intent was optional.",
        "The two best known labs cannot agree on a common safety standard, partly for "
        "antitrust reasons, partly out of plain distrust.",
        "An agent can forge its own logs. It cannot forge a verdict recomputed by a "
        "party it does not control.",
        "The second revision of the Agent Authorization Envelope went to the IETF this "
        "week. Full post: https://moltrust.ch/blog/what-should-we-be-worried-about.html",
    ]
    src = ("over 1,000 agents on the platform Hugging Face ... antitrust reasons ... "
           "the second revision of the Agent Authorization Envelope to the IETF")
    result = scan(parts, source_text=src, mode="thread")
    assert result["ok"], voice_gate.format_report(result)


def test_report_names_the_failing_rules():
    text = voice_gate.format_report(scan(["We are not a score, but a receipt."]))
    assert "BLOCKED" in text
    assert "g1a" in text and "g2d" in text
