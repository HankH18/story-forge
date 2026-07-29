"""Generation-layer tests that need no API key.

Prompt assembly and cost accounting are pure functions — they get real tests.
The live call is exercised by `make eval-smoke`, which is the only thing that can
actually prove the thesis (that a constrained model hits >=95% decodability).
"""

from __future__ import annotations

from datetime import date

import pytest

from storyforge.llm.generate import (
    PRACTICE_HEADER,
    SYSTEM_RULES,
    build_request_block,
    build_vocabulary_block,
)
from storyforge.llm.pricing import Usage, price_for


# --- prompt assembly ---------------------------------------------------------
def test_vocabulary_block_contains_every_word():
    block = build_vocabulary_block("Stage 3", ["cat", "shed", "ship"], ["the", "was"])
    for word in ("cat", "shed", "ship", "the", "was"):
        assert word in block
    assert "Stage 3" in block


def test_vocabulary_block_states_list_sizes():
    """The counts are load-bearing: they tell the model the list is exhaustive."""
    block = build_vocabulary_block("Stage 1", ["a"] * 40, ["the"] * 3)
    assert "(40)" in block
    assert "(3)" in block


def test_request_block_offers_story_words_with_glosses():
    block = build_request_block("dragons", {"dragon": "a big lizard"}, 30, 60)
    assert "dragons" in block and "30-60" in block
    assert "dragon: a big lizard" in block


def test_request_block_handles_no_story_words():
    """Stages 1-2 may have no offerable story words — must not emit an empty list."""
    assert "(none)" in build_request_block("space", {}, 30, 60)


def test_system_rules_are_frozen_of_request_detail():
    """Anything per-request in SYSTEM_RULES would invalidate the cache prefix on
    every call, silently destroying the per-stage cache."""
    for leak in ("dragon", "Stage 3", "30-60", "{", "}"):
        assert leak not in SYSTEM_RULES


def test_request_block_stays_free_of_stage_material():
    """The other half of the cache argument (DEVIATIONS 5.17). The stage's
    newly-taught words are identical for every story at a stage, so they belong
    in the cached vocabulary block; put here they would be re-sent as fresh
    input on every request and on every rewrite for no benefit at all.
    `tests/test_stage_coverage.py` holds the positive side of this."""
    block = build_request_block("dragons", {"dragon": "a big lizard"}, 30, 60)
    assert PRACTICE_HEADER not in block
    for leak in ("PERMITTED WORDS", "SIGHT WORDS", "letter-sound"):
        assert leak not in block


# --- cost accounting ---------------------------------------------------------
def test_sonnet_intro_pricing_expires():
    assert price_for("claude-sonnet-5", date(2026, 7, 29)).input_per_mtok == 2.00
    assert price_for("claude-sonnet-5", date(2026, 9, 1)).input_per_mtok == 3.00


def test_unknown_model_raises_rather_than_costing_zero():
    """A missing price sheet must fail loudly — silently costing $0.00 would
    corrupt every eval report in the same direction."""
    with pytest.raises(KeyError):
        price_for("claude-nonexistent", date(2026, 7, 29))


def test_total_input_counts_cached_tokens():
    """`input_tokens` is the uncached remainder only; reading it alone
    under-reports prompt size on any cached request."""
    u = Usage(
        model="claude-sonnet-5",
        input_tokens=100,
        cache_read_input_tokens=900,
        cache_creation_input_tokens=50,
    )
    assert u.total_input_tokens == 1050


def test_cache_reads_are_cheaper_than_fresh_input():
    on = date(2026, 9, 1)  # post-intro, $3/$15
    fresh = Usage(model="claude-sonnet-5", input_tokens=1_000_000).cost_usd(on)
    cached = Usage(model="claude-sonnet-5", cache_read_input_tokens=1_000_000).cost_usd(on)
    assert fresh == pytest.approx(3.00)
    assert cached == pytest.approx(0.30)  # 0.1x


def test_cache_writes_cost_a_premium():
    on = date(2026, 9, 1)
    write = Usage(model="claude-sonnet-5", cache_creation_input_tokens=1_000_000).cost_usd(on)
    assert write == pytest.approx(3.75)  # 1.25x


def test_output_tokens_dominate_cost():
    on = date(2026, 9, 1)
    u = Usage(model="claude-sonnet-5", input_tokens=1000, output_tokens=1000)
    assert u.cost_usd(on) == pytest.approx(1000 * 3 / 1e6 + 1000 * 15 / 1e6)


def test_haiku_is_cheaper_than_sonnet():
    on = date(2026, 9, 1)
    args = {"input_tokens": 10_000, "output_tokens": 10_000}
    assert Usage(model="claude-haiku-4-5", **args).cost_usd(on) < Usage(
        model="claude-sonnet-5", **args
    ).cost_usd(on)


# --- client guardrails -------------------------------------------------------
def test_missing_api_key_fails_with_an_actionable_message(monkeypatch):
    from storyforge.config import get_settings
    from storyforge.llm import client as client_mod

    get_settings.cache_clear()
    client_mod.get_client.cache_clear()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        client_mod, "get_settings", lambda: type("S", (), {"anthropic_api_key": None})()
    )
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        client_mod.get_client()
    client_mod.get_client.cache_clear()
    get_settings.cache_clear()
