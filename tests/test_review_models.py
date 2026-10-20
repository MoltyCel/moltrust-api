"""Cross-review model IDs come from lib/models.py, one knob per provider (2026-10-19).

A retired or tier-blocked model ID fails every review call, so the IDs must not
sit at the call sites, and a label must not outlive the model it names.
"""
import importlib
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ["agents/ai_review.py", "agents/ai_review_v2.py", "scripts/concept_review.py"]
RETIRED = ["gpt-5", "gpt-4o", "gemini-3.1-pro-preview", "gemini-2.5-flash", "sonar-pro",
           "mistral-large-latest"]


def test_defaults(monkeypatch, tmp_path):
    import lib.models as m
    monkeypatch.setattr(m, "SECRETS_FILE", tmp_path / "none")
    for k in ("OPENAI", "GEMINI", "PERPLEXITY", "MISTRAL"):
        monkeypatch.delenv(f"{k}_REVIEW_MODEL", raising=False)
    assert m.resolve("OPENAI_REVIEW_MODEL", m.DEFAULT_OPENAI_REVIEW_MODEL) == "gpt-6-astra"
    assert m.DEFAULT_GEMINI_REVIEW_MODEL == "gemini-4-argon"
    assert m.DEFAULT_PERPLEXITY_REVIEW_MODEL == "sonar-reasoning-pro"
    assert m.DEFAULT_MISTRAL_REVIEW_MODEL == "mistral-medium-2604"


def test_env_overrides_the_default(monkeypatch):
    import lib.models as m
    monkeypatch.setenv("MISTRAL_REVIEW_MODEL", "mistral-small-latest")
    assert m.resolve("MISTRAL_REVIEW_MODEL", m.DEFAULT_MISTRAL_REVIEW_MODEL) == "mistral-small-latest"


def test_no_call_site_carries_a_model_id():
    for rel in SCRIPTS:
        text = (ROOT / rel).read_text()
        for mid in RETIRED:
            assert not re.search(rf'["/]{re.escape(mid)}["\:]', text), f"{rel} still names {mid}"


def test_labels_follow_the_model_and_fill_every_template():
    a = importlib.import_module("agents.ai_review")
    assert a.REVIEWER_LABELS[a.call_openai] == f"OpenAI {a.OPENAI_REVIEW_MODEL}"
    kw = dict(label="x", date="d", openai_review="o", gemini_review="g",
              perplexity_review="p", mistral_review="m", **a.LABELS)
    for tpl in (a.SYNTHESIS_PROMPT, a.SYNTHESIS_PROMPT_EU, a.SYNTHESIS_PROMPT_KYA):
        out = tpl.format(**kw)
        assert a.OPENAI_LABEL in out and "{" not in out.split("**Reviewer:**")[1].splitlines()[0]
