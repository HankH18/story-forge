"""Phase 7: the §14 eval harness.

Two kinds of test here, and the split matters.

The metric functions are PURE — `story_metrics`, `aggregate`, `criteria_verdict`,
`quantile`, `bin_decodability`, `moderation_class` — so they are tested against
hand-built `PipelineResult`s with no API call and no database. That is the only
way to prove the thing that matters most about a report: that a BAD run is
reported as bad. §0 forbids weakening the gate to make numbers look good, and the
corollary for an eval harness is that its verdict function must go red on
evidence it would be flattering to round off. `test_a_below_gate_story_fails_the
_criteria` is that test, and it is the reason the rest exist.

The second kind runs against real Postgres and the real API and answers one
question: can anything the harness writes reach a child? The harness writes
stories at `status='eval'` (DEVIATIONS 7.41), and "not servable" is asserted
through `POST /api/stories/request` and `GET /api/stories/{id}` rather than by
reading the constant, because the constant is not what serves stories.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

import storyforge.eval
from storyforge.api.stories import SERVABLE_STATUSES
from storyforge.db import SessionLocal
from storyforge.decoder import mastered_ids, validate_text
from storyforge.eval import (
    DECODABILITY_BINS,
    EVAL_STATUS,
    REWRITE_DEPTH_P95_TARGET,
    _provenance,
    _reprice,
    _smoke_reason,
    aggregate,
    bin_decodability,
    config_drift,
    criteria_verdict,
    decoder_limitation_rates,
    generation_latency_split,
    hazard_suspect,
    moderation_class,
    parse_stages,
    project_cost,
    quantile,
    remaining_cells,
    render_report,
    story_metrics,
    type_token_ratio,
    vocabulary_fingerprint,
)
from storyforge.llm.moderate import UNGRAMMATICAL_PREFIX, ModerationResult
from storyforge.llm.pricing import Usage
from storyforge.main import app
from storyforge.pipeline import DECODABILITY_GATE, Attempt, PipelineResult, Vocabulary
from tests.owned_pool import hide_servable_pool, owned_transaction

ON = date(2026, 7, 29)  # Sonnet 5 introductory pricing is live on this date.
POST_INTRO = date(2026, 9, 1)


def _vocab(stage: int = 3, *, lo: int = 30, hi: int = 60) -> Vocabulary:
    return Vocabulary(
        stage_id=stage,
        stage_name="Digraphs",
        target_min=lo,
        target_max=hi,
        allowed_words=["the", "cat", "sat", "ship", "shed"],
        sight_words=["the", "a"],
        story_words={"dragon": "a big lizard", "cave": "a hole in a hill"},
    )


def _usage(inp: int = 4000, out: int = 900, read: int = 0, write: int = 0) -> Usage:
    return Usage(
        model="claude-sonnet-5",
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=write,
    )


def _result(
    *,
    stage: int = 3,
    theme: str = "dragons",
    body: str = "A dragon sat in a cave. Sam did pat the dragon.",
    depth: int = 0,
    passed: bool = True,
    moderation: ModerationResult | None = None,
    sight: tuple[str, ...] = ("the", "a"),
    story_words: tuple[str, ...] = ("dragon", "cave"),
) -> PipelineResult:
    """A PipelineResult whose report comes from the REAL validator.

    Deliberately not a hand-written TextReport: the metric row is supposed to be
    a faithful projection of what the gate actually said, so a fabricated report
    would test the projection against a fiction.
    """
    report = validate_text(body, mastered_ids(stage), frozenset(sight), frozenset(story_words))
    attempts = [
        Attempt(
            n=i,
            kind="generate" if i == 0 else "rewrite",
            text=body,
            decodability=report.decodability,
            violations=[w.word for w in report.violations],
            usage=_usage(),
            latency_ms=5000.0 + i * 1000,
            validate_ms=0.5,
        )
        for i in range(depth + 1)
    ]
    r = PipelineResult(
        stage_id=stage,
        theme=theme,
        passed=passed,
        attempts=attempts,
        final_text=body,
        final_report=report,
        title="A Dragon Naps",
        story_words=list(report.distinct_story_words),
        moderation=moderation,
    )
    r.vocabulary_ms = 3.0
    r.moderation_ms = 700.0 if moderation else None
    r.total_ms = sum(a.latency_ms for a in attempts) + (700.0 if moderation else 0.0)
    return r


APPROVED = ModerationResult(
    approved=True,
    reason="fine",
    layer="model",
    usage=Usage(model="claude-haiku-4-5", input_tokens=1200, output_tokens=90),
)


#: What `run_full` passes into every metric row (DEVIATIONS 8.30, 8.35). The
#: helper passes them because the production call sites do; a row built without
#: them is the pre-8.30 row whose model was unauditable, and one test below builds
#: exactly that on purpose.
REQUESTED = {"generation": "claude-sonnet-5", "fast": "claude-haiku-4-5"}
TIMEOUT_S = 240.0


def _item(
    result: PipelineResult,
    outcome: str,
    *,
    index: int = 0,
    vocab=None,
    requested_models: dict | None = REQUESTED,
    per_call_timeout_s: float | None = TIMEOUT_S,
) -> dict:
    return story_metrics(
        result,
        vocab or _vocab(result.stage_id),
        index=index,
        outcome=outcome,
        on=ON,
        story_id=1000 + index,
        requested_models=requested_models,
        per_call_timeout_s=per_call_timeout_s,
        sdk_max_retries=2,
    )


# --- the CLI surface §14 documents -------------------------------------------


def test_parse_stages_accepts_the_range_form_the_spec_documents():
    """§14's own command line is `--stages 1..11`, so that has to parse."""
    assert parse_stages("1..11") == list(range(1, 12))
    assert parse_stages("3,6") == [3, 6]
    assert parse_stages("1..4,9..11,6") == [1, 2, 3, 4, 6, 9, 10, 11]
    assert parse_stages(" 5 ") == [5]


def test_parse_stages_refuses_input_it_cannot_honour():
    """A run over 9 stages reported as a run over 11 is the failure mode."""
    for bad in ("", "abc", "5..1", "1..x"):
        with pytest.raises(SystemExit):
            parse_stages(bad)


# --- distribution helpers ----------------------------------------------------


def test_quantile_is_nearest_rank_and_defined_on_one_observation():
    assert quantile([], 0.5) == 0.0
    assert quantile([7.0], 0.95) == 7.0
    assert quantile([0.0, 1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    # Nearest rank, never interpolated: p95 is an OBSERVED value.
    assert quantile([1.0, 2.0, 3.0, 4.0], 0.95) in (3.0, 4.0)
    # 0..99: the 95th of 100 observations, i.e. index 94.
    assert quantile(list(range(100)), 0.95) == 94
    assert quantile(list(range(100)), 0.95) in range(100)


def test_every_decodability_lands_in_exactly_one_bin():
    values = [0.0, 0.4999, 0.5, 0.79, 0.8, 0.899, 0.9, 0.949, 0.95, 0.999, 1.0]
    bins = bin_decodability(values)
    assert sum(bins.values()) == len(values)
    # 100% is its own bin: "95-100%" would hide a perfect run inside a mixed one.
    assert bins["100%"] == 1
    assert bins["95-<100%"] == 2
    assert bins["90-<95%"] == 2


def test_bins_cover_the_whole_unit_interval_with_no_gap():
    edges = [(lo, hi) for _, lo, hi in DECODABILITY_BINS]
    for (_, hi), (lo, _) in zip(edges, edges[1:], strict=False):
        assert hi == lo, f"gap or overlap between bins at {hi}/{lo}"
    assert edges[0][0] == 0.0
    assert edges[-1][1] > 1.0  # 1.0 must be inside the last bin


def test_type_token_ratio_survives_an_empty_story():
    assert type_token_ratio([]) == 0.0
    assert type_token_ratio(["a", "a", "b", "b"]) == 0.5


# --- moderation classification (§13, DEVIATIONS 7.46) -----------------------


def test_moderation_class_separates_blocklist_grammar_and_content():
    assert moderation_class(None) == "not_reached"
    assert moderation_class(APPROVED) == "approved"
    assert (
        moderation_class(
            ModerationResult(
                approved=False, reason="blocklisted words present: kill", layer="blocklist"
            )
        )
        == "blocklist"
    )
    # `moderate()` folds well_formed into approved and marks the reason. That
    # prefix is the ONLY deterministic signal separating unsafe from
    # ungrammatical after the fold, so the classifier must read it.
    assert (
        moderation_class(
            ModerationResult(
                approved=False,
                reason=f"{UNGRAMMATICAL_PREFIX}'He did not came back.' came -> come",
                layer="model",
                well_formed=False,
            )
        )
        == "grammar"
    )
    assert (
        moderation_class(
            ModerationResult(
                approved=False, reason="a character is hurt", layer="model", well_formed=True
            )
        )
        == "content"
    )


def test_a_story_failing_both_halves_is_counted_as_content_not_grammar():
    """A grammar failure must not be able to hide behind a safety one, and a
    safety failure must not be relabelled as a typo (warm.py makes the same
    distinction in `_rejected_on_content`)."""
    both = ModerationResult(
        approved=False,
        reason="a character is hurt",  # no UNGRAMMATICAL_PREFIX -> safety fired
        layer="model",
        well_formed=False,
        grammar_note="'he sit' sit -> sits",
    )
    assert moderation_class(both) == "content"
    item = _item(_result(passed=False, moderation=both), "moderation_fail")
    # The grammar note survives on the item, so the second defect is still
    # recoverable from the row.
    assert item["moderation"]["grammar_note"] == "'he sit' sit -> sits"
    assert item["moderation"]["well_formed"] is False


def test_hazard_heuristic_never_excuses_a_harm_reason():
    """STATE invariant 5: a dark cave is the premise, a hurt character is not.

    The heuristic exists to point a reader at reasons worth reading. It must
    never flag a reason that names actual harm as a suspected false positive.
    """
    assert hazard_suspect("the story leaves a scary situation unresolved")
    assert not hazard_suspect("a character is hurt in a dark cave")
    assert not hazard_suspect("the dragon kills the man")
    assert not hazard_suspect("mentions a real brand")


# --- the per-story metric row (§14) -----------------------------------------


def test_story_metrics_reports_every_metric_section_14_names():
    item = _item(_result(moderation=APPROVED), "accepted")
    for key in (
        "decodability",
        "oov",
        "distinct_story_words",
        "rewrite_depth",
        "outcome",
        "latency_ms",
        "cost_usd",
        "length_compliant",
        "type_token_ratio",
        # Renamed from `*_rate` to carry the denominator (DEVIATIONS 8.34). The
        # test was not wrong; the recorded contract moved deliberately, and the
        # old name was the one that let audit F6 through a whole report.
        "schwa_relaxed_rate_scored",
        "ambiguity_rate_scored",
    ):
        assert key in item, f"§14 names {key} and the metric row does not carry it"
    assert item["latency_ms"]["generating"] == 5000.0
    assert item["latency_ms"]["moderating"] == 700.0
    assert item["latency_ms"]["validating"] == [0.5]
    assert item["cost_usd"]["generating_and_rewriting"] > 0
    assert item["cost_usd"]["moderating"] > 0
    assert item["cost_usd"]["total"] == pytest.approx(
        item["cost_usd"]["generating_and_rewriting"] + item["cost_usd"]["moderating"]
    )


def test_the_metric_row_carries_the_decoders_disclosed_limitations():
    """§5.3: the decoder's limitations are logged in the eval report, not silent.

    "of" is in CMUdict and has no inventory path at any stage, so it is a
    VIOLATION with `alignable=False` — §18's "the inventory is too small" alarm.
    """
    item = _item(_result(body="The cat sat on top of the mat.", passed=False), "budget_exhausted")
    assert item["unalignable"] >= 1
    assert "of" in item["unalignable_words"]
    # The rate is over SCORED words, and the name says so (DEVIATIONS 8.34).
    # Asserted as arithmetic rather than as `> 0`: a rate over every word token —
    # which is what audit F6 found, and which halved §18's designated alarm — would
    # be strictly smaller here, because "the" and "a" are sight words that can
    # carry no flag and were nevertheless in the denominator.
    assert item["flag_rate_denominator"] == "scored_word_count"
    assert item["scored_word_count"] < item["word_count"]
    assert item["unalignable_rate_scored"] == pytest.approx(
        item["unalignable"] / item["scored_word_count"], abs=5e-5
    )
    assert item["unalignable_rate_scored"] > item["unalignable"] / item["word_count"]


def test_an_oov_word_is_counted_and_named():
    item = _item(_result(body="A zorp sat.", passed=False), "budget_exhausted")
    assert item["oov"] == 1
    assert item["oov_words"] == ["zorp"]
    # §7's gate requires ZERO OOV, so an OOV story can never be `accepted`.
    assert not item["meets_gate"] or item["oov"] == 0


def test_length_compliance_is_measured_against_the_stages_own_target():
    short = _result(body="Sam sat.")
    assert _item(short, "accepted", vocab=_vocab(lo=1, hi=10))["length_compliant"] is True
    assert _item(short, "accepted", vocab=_vocab(lo=30, hi=60))["length_compliant"] is False


def test_story_word_metrics_count_distinct_and_occurrences_separately():
    """STATE invariant 3: repetition is correct, so the CAP is on distinct words."""
    item = _item(
        _result(body="A dragon sat. The dragon sat in a cave. The dragon did nap."), "accepted"
    )
    assert item["distinct_story_words"] == ["cave", "dragon"]
    assert item["story_word_occurrences"] == 4  # dragon x3 + cave
    assert item["story_word_cap"] == 3


# --- aggregation and the §1 verdict -----------------------------------------


def _mixed_items() -> list[dict]:
    """Nine items: seven clean, one below the gate, one moderation reject."""
    items = [
        _item(_result(stage=s, moderation=APPROVED), "accepted", index=i)
        for i, s in enumerate([1, 1, 2, 2, 3, 3, 3])
    ]
    items.append(
        _item(
            _result(stage=4, body="The knight sees a beautiful dragon.", depth=3, passed=False),
            "budget_exhausted",
            index=7,
        )
    )
    items.append(
        _item(
            _result(
                stage=5,
                moderation=ModerationResult(
                    approved=False,
                    reason="a character is hurt",
                    layer="model",
                    usage=Usage(model="claude-haiku-4-5", input_tokens=1100, output_tokens=80),
                ),
                passed=False,
            ),
            "moderation_fail",
            index=8,
        )
    )
    return items


def test_aggregate_reports_a_distribution_not_only_a_mean():
    agg = aggregate(_mixed_items(), on=ON, cells_planned=9)
    d = agg["decodability"]
    for k in ("min", "p05", "p25", "p50", "mean", "p95", "max"):
        assert k in d
    assert sum(d["bins"].values()) == agg["produced"]
    assert d["min"] < d["mean"] <= d["max"]
    assert agg["by_stage"]["1"]["attempted"] == 2
    assert set(agg["by_theme"]) == {"dragons"}


def test_a_below_gate_story_fails_the_criteria_and_is_named_in_the_report():
    """The one test this file exists for.

    §0: the gate is never weakened to make generation easier, and a report that
    rounds a failure off is the same sin one layer up. A single story under 95%
    must flip the verdict to FAIL, must be exit-code-visible, and must appear in
    the report with the words and GPCs that failed — not as a decimal in a mean.
    """
    agg = aggregate(_mixed_items(), on=ON, cells_planned=9)
    assert agg["decodability"]["below_gate"] == 1
    detail = agg["decodability"]["below_gate_detail"]
    assert len(detail) == 1
    assert detail[0]["stage_id"] == 4
    assert detail[0]["violation_words"], "a failure with no named words is unfalsifiable"

    verdict = criteria_verdict(agg)
    gate_criterion = next(v for v in verdict if "decodability" in v[0])
    assert gate_criterion[1] is False
    assert not all(v[1] for v in verdict)

    report = render_report(_run_meta(agg), agg)
    assert "**FAIL**" in report
    assert "Overall: FAIL" in report
    assert "Every story below the gate" in report


def test_an_all_clean_run_passes_the_criteria():
    """The control. Without it the test above only proves the function can say no."""
    items = [
        _item(_result(stage=s, moderation=APPROVED), "accepted", index=i)
        for i, s in enumerate([1, 2, 3, 4, 5])
    ]
    agg = aggregate(items, on=ON, cells_planned=5)
    assert all(good for _, good, _ in criteria_verdict(agg))
    assert agg["gate_pass_rate"] == 1.0
    assert agg["fallback_rate"] == 0.0
    assert "Overall: PASS" in render_report(_run_meta(agg), agg)


def test_a_deep_rewrite_loop_fails_the_p95_criterion():
    """§1 item 2's second half. A run that only ever passes on the fourth attempt
    is not the thesis holding — it is retry pressure, which §6 says is not the
    primary lever."""
    items = [
        _item(_result(depth=3, moderation=APPROVED), "accepted", index=i) for i in range(5)
    ]
    agg = aggregate(items, on=ON, cells_planned=5)
    assert agg["rewrite_depth"]["p95"] > REWRITE_DEPTH_P95_TARGET
    p95_criterion = next(v for v in criteria_verdict(agg) if "rewrite" in v[0])
    assert p95_criterion[1] is False


def test_an_incomplete_matrix_cannot_report_as_a_pass():
    """A budget stop or a crash leaves fewer items than cells. Reporting that as
    a pass is exactly the false green DEVIATIONS 0.5 forbids."""
    items = [_item(_result(moderation=APPROVED), "accepted", index=i) for i in range(3)]
    agg = aggregate(items, on=ON, cells_planned=165)
    completed = next(v for v in criteria_verdict(agg) if "matrix" in v[0])
    assert completed[1] is False


def test_a_cell_that_never_produced_text_cannot_report_as_a_pass():
    """An api_fail is not a decodability failure — and a cell that produced no
    text was never measured. A matrix reported as covering 5 cells while one is
    empty is the false green DEVIATIONS 0.5 forbids, so it is its own criterion
    and its own exit code, separate from "the matrix completed"."""
    ok = [_item(_result(moderation=APPROVED), "accepted", index=i) for i in range(4)]
    dead = _item(PipelineResult(stage_id=3, theme="dragons", passed=False), "api_fail", index=4)
    agg = aggregate([*ok, dead], on=ON, cells_planned=5)
    attempted = next(v for v in criteria_verdict(agg) if "matrix" in v[0])
    produced = next(v for v in criteria_verdict(agg) if "produced a story" in v[0])
    assert attempted[1] is True  # all five cells were attempted
    assert produced[1] is False  # one of them yielded nothing to measure
    assert "1 api_fail" in produced[2]
    assert not all(good for _, good, _ in criteria_verdict(agg))


def test_an_empty_run_is_never_a_pass():
    agg = aggregate([], on=ON, cells_planned=165)
    assert not all(good for _, good, _ in criteria_verdict(agg))


def test_api_failures_are_counted_but_do_not_pollute_the_decodability_stats():
    """DEVIATIONS 7.47: an api_fail has a latency and a cost and NO text. Folding
    its 0.0 decodability into the mean would understate the model by an amount
    that has nothing to do with the model."""
    ok = [_item(_result(moderation=APPROVED), "accepted", index=i) for i in range(4)]
    dead = _item(PipelineResult(stage_id=3, theme="dragons", passed=False), "api_fail", index=4)
    agg = aggregate([*ok, dead], on=ON, cells_planned=5)
    assert agg["attempted"] == 5
    assert agg["produced"] == 4
    assert agg["decodability"]["mean"] == 1.0
    assert agg["outcomes"]["api_fail"] == 1
    # It IS a fallback: §7 would have served the warm pool instead.
    assert agg["fallback_rate"] == pytest.approx(1 / 5)


def test_moderation_rejects_are_split_by_reason_and_quoted_verbatim():
    items = _mixed_items()
    items.append(
        _item(
            _result(
                passed=False,
                moderation=ModerationResult(
                    approved=False,
                    reason=f"{UNGRAMMATICAL_PREFIX}'He did not came back.' came -> come",
                    layer="model",
                    well_formed=False,
                    grammar_note="'He did not came back.' came -> come",
                ),
            ),
            "moderation_fail",
            index=9,
        )
    )
    items.append(
        _item(
            _result(
                passed=False,
                moderation=ModerationResult(
                    approved=False,
                    reason="blocklisted words present: gun",
                    layer="blocklist",
                    blocked_words=["gun"],
                ),
            ),
            "moderation_fail",
            index=10,
        )
    )
    agg = aggregate(items, on=ON, cells_planned=11)
    m = agg["moderation"]
    assert m["classes"]["content"] == 1
    assert m["classes"]["grammar"] == 1
    assert m["classes"]["blocklist"] == 1
    assert m["content_rejects"][0]["reason"] == "a character is hurt"
    assert m["grammar_rejects"][0]["grammar_note"].startswith("'He did not came back.'")
    assert m["blocklist_rejects"][0]["blocked_words"] == ["gun"]
    report = render_report(_run_meta(agg), agg)
    # The verbatim reason is the evidence; a count alone cannot answer
    # hazard-vs-harm (DEVIATIONS 7.46).
    assert "a character is hurt" in report
    assert "came -> come" in report


def test_cost_per_accepted_story_is_the_whole_bill_over_accepted_stories_only():
    """The number a reader will quote. Rejected stories cost money too, so
    dividing only the accepted stories' cost by their count would flatter the
    figure by exactly the failure rate."""
    items = _mixed_items()
    agg = aggregate(items, on=ON, cells_planned=9)
    total = sum(i["cost_usd"]["total"] for i in items)
    accepted = sum(1 for i in items if i["outcome"] == "accepted")
    assert agg["cost"]["total_usd"] == pytest.approx(round(total, 6))
    assert agg["cost"]["per_accepted_usd"] == pytest.approx(round(total / accepted, 6))
    assert agg["cost"]["per_accepted_usd"] > agg["cost"]["per_attempted_usd"]


def test_the_report_states_the_pricing_date_it_used():
    """Sonnet 5's introductory rate ends 2026-08-31, so a cost figure without a
    date is not a measurement."""
    agg = aggregate(_mixed_items(), on=ON, cells_planned=9)
    assert agg["cost"]["pricing_date"] == ON.isoformat()
    assert ON.isoformat() in render_report(_run_meta(agg), agg)


def test_the_same_tokens_cost_more_after_the_intro_rate_expires():
    items = _mixed_items()
    intro = aggregate(items, on=ON, cells_planned=9)["cost"]["total_usd"]
    # `_reprice` returns the sheets it used alongside the total (DEVIATIONS 8.36),
    # because the total alone is what let one report price Opus tokens under a
    # config table saying Sonnet. Same assertions, one level deeper.
    priced = _reprice(items, POST_INTRO)
    after = priced["total_usd"]
    assert priced["prices"]["claude-sonnet-5"] == {
        "input_per_mtok": 3.0,
        "output_per_mtok": 15.0,
    }
    assert priced["unpriced_models"] == []
    assert priced["items_priced_from_fallback"] == 0
    assert after > intro
    # Sonnet goes $2/$10 -> $3/$15, a flat 1.5x. Haiku's price does not change,
    # so the whole-run ratio sits strictly between 1 and 1.5 — and where it sits
    # is the share of the bill that is generation rather than moderation.
    assert 1.0 < after / intro < 1.5


def test_the_projection_grows_with_the_matrix_and_never_undercuts_the_worst_case():
    v = [_vocab(1, lo=30, hi=60), _vocab(11, lo=120, hi=200)]
    small = project_cost(v[:1], 5, ON)
    big = project_cost(v, 5, ON)
    assert big["no_rewrites_usd"] > small["no_rewrites_usd"]
    assert (
        big["worst_case_all_budget_exhausted_usd"]
        > big["with_one_rewrite_each_usd"]
        > big["no_rewrites_usd"]
    )
    assert big["pricing_date"] == ON.isoformat()


def test_a_fresh_run_has_every_cell_left_and_a_resume_has_only_the_remainder():
    """The highest-consequence line in the resume rework: `run_full` now drives its
    loop from `remaining_cells` instead of decrementing a counter inline, so a bug
    here is a matrix that runs nothing (DEVIATIONS 8.31).

    Counted per (stage, theme) rather than by position, because items are committed
    as they complete and nothing guarantees the recorded order matches the plan.
    """
    stages, themes, n = [1, 2], ["dragons", "space"], 3
    assert len(remaining_cells(stages, themes, n, [])) == 12
    done = [
        {"stage_id": 1, "theme": "dragons"},
        {"stage_id": 1, "theme": "dragons"},
        {"stage_id": 2, "theme": "space"},
    ]
    left = remaining_cells(stages, themes, n, done)
    assert len(left) == 9
    assert sum(1 for s, t, _ in left if (s, t) == (1, "dragons")) == 1
    assert sum(1 for s, t, _ in left if (s, t) == (2, "space")) == 2
    # A complete run has nothing left, which is what licenses disclosing a config
    # drift instead of refusing it.
    every = [{"stage_id": st, "theme": th} for st in stages for th in themes for _ in range(n)]
    assert remaining_cells(stages, themes, n, every) == []


# --- provenance: what produced the numbers (AUDIT-PHASE7 F1/F2/F3) ------------


def test_the_metric_row_records_the_model_every_call_was_actually_made_with():
    """AUDIT-PHASE7 F1's aggravating half: `metrics ? 'model'` was 0 for all 180
    rows, so "this matrix ran on Sonnet" was unfalsifiable after the fact while
    `gate` and `pricing_date` were per-item and therefore checkable.

    Read off each call's own `Usage`, not off the environment: the model is the one
    the API was CALLED with, which is the distinction the audit's Opus resume turned
    on (DEVIATIONS 8.30).
    """
    item = _item(_result(moderation=APPROVED), "accepted")
    assert item["models"]["generation"] == ["claude-sonnet-5"]
    assert item["models"]["moderation"] == ["claude-haiku-4-5"]
    assert item["models"]["requested"] == REQUESTED
    # The price sheet applied, per model, so a cost can be re-derived without
    # trusting the shell that renders the report.
    assert item["prices"]["claude-sonnet-5"] == {"input_per_mtok": 2.0, "output_per_mtok": 10.0}
    assert item["per_call_timeout_s"] == TIMEOUT_S
    assert item["sdk_max_retries"] == 2


def test_an_api_fail_records_the_model_it_asked_for_and_claims_no_answer():
    """An `api_fail` has no response to read a model off. "We asked for X and got
    nothing" and "X answered" are different facts and are stored as different
    fields (DEVIATIONS 8.30)."""
    dead = PipelineResult(stage_id=3, theme="dragons", passed=False)
    dead.total_ms = 91_000.0
    item = _item(dead, "api_fail")
    assert item["models"]["generation"] == []
    assert item["models"]["requested"]["generation"] == "claude-sonnet-5"


def test_a_matrix_measured_on_two_models_cannot_report_as_one():
    """The failure mode F1 reproduced: one report naming a single model while
    pricing a run at another's rates. A mixed matrix has to say so."""
    items = _mixed_items()
    opus = _item(_result(stage=6), "accepted", index=9)
    opus["models"]["generation"] = ["claude-opus-4-8"]
    items.append(opus)
    agg = aggregate(items, on=ON, cells_planned=10)
    census = agg["models_observed"]["generation"]
    assert set(census) == {"claude-sonnet-5", "claude-opus-4-8"}
    assert census["claude-opus-4-8"] == 1
    assert "MIXED" in render_report(_run_meta(agg), agg)


def test_the_report_no_longer_claims_a_resume_enforced_the_model():
    """A regression guard on a SENTENCE, because the sentence was the defect.

    `render_report` emitted "`--resume` ran only the remainder against the SAME
    stored config — same model, same gate, same pricing date" into every resumed
    report, unconditionally, while neither the model nor the gate was restored
    (AUDIT-PHASE7 F1). A report that overstates its own rigour spends the
    credibility the rest of it earned, so the old claim must stay gone.
    """
    agg = aggregate(_mixed_items(), on=ON, cells_planned=9)
    meta = _run_meta(agg)
    meta["config"]["resumed"] = [
        {
            "at": "2026-07-29T23:16:23+00:00",
            "items_already_recorded": 4,
            "cells_remaining": 5,
            "git_commit": "deadbee",
            "concurrency": 1,
            "budget_usd": 15.0,
            "config_drift": None,
        }
    ]
    report = render_report(meta, agg)
    assert "same model, same gate, same pricing date" not in report
    assert "RESTORED" in report and "VERIFIED" in report
    assert "exit code 3" in report


def test_a_resumed_report_prints_the_drift_it_was_allowed_to_continue_through():
    """A complete run can be re-derived under a different live config with no API
    call — that is legitimate and is how run 2's report was regenerated twice. What
    it may not do is stay quiet about it (DEVIATIONS 8.31)."""
    agg = aggregate(_mixed_items(), on=ON, cells_planned=9)
    meta = _run_meta(agg)
    meta["config"]["resumed"] = [
        {
            "at": "2026-07-30T01:00:00+00:00",
            "items_already_recorded": 9,
            "cells_remaining": 0,
            "git_commit": "deadbee",
            "concurrency": 1,
            "budget_usd": 15.0,
            "config_drift": [
                {
                    "key": "models",
                    "stored": {"generation": "claude-sonnet-5"},
                    "live": {"generation": "claude-opus-4-8"},
                    "source": "ANTHROPIC_MODEL_GENERATE",
                    "missing": False,
                }
            ],
        }
    ]
    report = render_report(meta, agg)
    assert "CONFIG DRIFT" in report
    assert "`models`" in report


def test_config_drift_names_every_value_a_resume_cannot_restore():
    """The list is the fix: `models`, `prices`, the gate, the budgets, the timeouts
    and the vocabulary come from the environment or from a module constant, so a
    resume can only check them (DEVIATIONS 8.31)."""
    stored = {
        "models": {"generation": "claude-sonnet-5", "fast": "claude-haiku-4-5"},
        "gate": 0.95,
        "vocabulary": {"sight_word_count": 33},
    }
    live = {
        "models": {"generation": "claude-opus-4-8", "fast": "claude-haiku-4-5"},
        "gate": 0.95,
        "vocabulary": {"sight_word_count": 38},
    }
    drift = config_drift(stored, live)
    by_key = {d["key"]: d for d in drift}
    assert by_key["models"]["missing"] is False
    assert by_key["models"]["live"]["generation"] == "claude-opus-4-8"
    assert "ANTHROPIC_MODEL_GENERATE" in by_key["models"]["source"]
    # The vocabulary is checked too — F3's drift was 33 -> 38 sight words.
    assert by_key["vocabulary"]["missing"] is False
    # A value that MATCHES is not drift, so it is absent from the list entirely.
    assert "gate" not in by_key
    # A key the stored run never recorded is a fact about the older run, not a
    # difference; treating it as drift would make every pre-8.31 run unresumable.
    assert by_key["prices"]["missing"] is True
    assert all(d["missing"] for d in config_drift(live, live))


def test_provenance_records_whether_the_commit_is_the_code_that_ran():
    """AUDIT-PHASE7 F2: the recorded commit `559f102` holds a 102-line `eval.py`
    with only a `smoke` subparser, so it cannot run the command the report prints.
    A commit alone was never provenance (DEVIATIONS 8.32)."""
    prov = _provenance()
    assert set(prov) >= {"git_commit", "git_dirty", "git_dirty_paths", "code_sha256"}
    # A hash a reader can check against `git show <commit>:path | shasum -a 256`.
    here = Path(storyforge.eval.__file__).resolve()
    assert prov["code_sha256"]["storyforge/eval.py"] == hashlib.sha256(
        here.read_bytes()
    ).hexdigest()
    assert prov["git_dirty"] in (True, False, None)
    if prov["git_dirty"]:
        assert prov["git_dirty_path_count"] >= 1


def test_the_dirty_flag_reaches_the_report_next_to_the_commit():
    """Separating the two is how a commit gets read as provenance it is not."""
    agg = aggregate(_mixed_items(), on=ON, cells_planned=9)
    meta = _run_meta(agg)
    meta["config"]["provenance"] = {
        "git_commit": "deadbee",
        "git_dirty": True,
        "git_dirty_paths": ["services/api/storyforge/eval.py"],
        "git_dirty_path_count": 1,
        "code_sha256": {"storyforge/eval.py": "a" * 64},
    }
    report = render_report(meta, agg)
    assert "DIRTY" in report
    assert "including the harness itself" in report
    assert "NOT the code that ran" in report
    # And a run that never recorded the flag must not read as clean.
    del meta["config"]["provenance"]
    assert "not recorded" in render_report(meta, agg)


def test_the_gpc_fingerprint_moves_when_the_inventory_moves(monkeypatch):
    """The instrument, hashed. F3's 11 stale figures came from a 92 -> 93 GPC change
    plus 33 -> 38 sight words that `eval_runs.config` had no field for.

    Sabotaged rather than asserted: the inventory is shortened by one row and the
    hash has to move, or the fingerprint is decoration (DEVIATIONS 8.33).
    """
    real = storyforge.eval.load_gpcs()

    async def fp():
        async with SessionLocal() as s:
            return await vocabulary_fingerprint(s)

    base = asyncio.run(fp())
    assert base["gpc_count"] == len(real)
    monkeypatch.setattr(storyforge.eval, "load_gpcs", lambda: real[:-1])
    shrunk = asyncio.run(fp())
    assert shrunk["gpc_count"] == len(real) - 1
    assert shrunk["gpcs_sha256"] != base["gpcs_sha256"]
    # The DB-side fields are unaffected by a code-side sabotage, which is what
    # makes them separate fields rather than one blended hash.
    assert shrunk["sight_words_sha256"] == base["sight_words_sha256"]


async def test_the_vocabulary_fingerprint_matches_the_tables_it_claims_to_describe(session):
    fp = await vocabulary_fingerprint(session)
    sight = (await session.execute(text("SELECT count(*) FROM sight_words"))).scalar_one()
    allowed = (await session.execute(text("SELECT count(*) FROM allowed_words"))).scalar_one()
    assert fp["sight_word_count"] == sight
    assert fp["allowed_words_count"] == allowed
    assert fp["cmudict_entries"] > 100_000


# --- latency: a retry is not a generation (AUDIT-PHASE7 F7) -------------------


def test_a_retry_is_never_priced_as_generation_time():
    """23 of run 2's 169 stories burned a dead 30s attempt inside a number the
    report called "GENERATING (first attempt)", and p95 over the rest was 26.4s
    against the 58.3s printed (DEVIATIONS 8.35)."""
    items = []
    for i in range(9):
        it = _item(_result(), "accepted", index=i, per_call_timeout_s=30.0)
        # Three of nine spent longer than one 30s attempt could take, so each holds
        # at least one attempt that was killed by the clock and paid for.
        it["latency_ms"]["generating"] = 86_000.0 if i < 3 else 12_000.0
        items.append(it)
    split = generation_latency_split(items)
    assert split["timeout_source"] == "recorded"
    assert split["over_timeout"]["n"] == 3
    assert split["no_clock_retry"]["n"] == 6
    assert split["no_clock_retry"]["p95"] == 12_000.0
    assert split["wall_clock"]["p95"] > split["no_clock_retry"]["p95"]
    # Reported, not merely computed.
    agg = aggregate(items, on=ON, cells_planned=9)
    report = render_report(_run_meta(agg), agg)
    assert "retries INCLUDED" in report
    assert "no clock-driven retry" in report or "no_clock_retry" in report


def test_a_run_with_no_recorded_timeout_declines_to_report_a_retry_free_p95():
    """Judging a 30s-era measurement against today's 240s budget would call every
    old attempt retry-free. The budget is per item, and its absence is said out
    loud (DEVIATIONS 8.35)."""
    items = [_item(_result(), "accepted", index=i, per_call_timeout_s=None) for i in range(3)]
    split = generation_latency_split(items)
    assert split["timeout_source"] == "unknown"
    assert split["no_clock_retry"]["n"] == 0
    assert split["timeout_unknown"] == 3
    agg = aggregate(items, on=ON, cells_planned=3)
    assert "cannot be computed" in render_report(_run_meta(agg), agg)


# --- denominators (AUDIT-PHASE7 F6, F9) --------------------------------------


def test_the_flag_rates_divide_by_the_words_that_can_carry_a_flag():
    """F6, the finding with the most consequence for §18: sight and story words are
    classified before any alignment is attempted, so they can carry none of these
    flags and inflated nothing but the denominator. Over run 2's items the real
    ambiguity rate is 22.7%, not 12.4%, and the unalignable rate — §18's designated
    alarm — is 0.50%, not 0.19% (DEVIATIONS 8.34)."""
    items = _mixed_items()
    dl = aggregate(items, on=ON, cells_planned=9)["decoder_limitations"]
    scored = sum(i["scored_word_count"] for i in items if i["outcome"] != "api_fail")
    tokens = sum(i["word_count"] for i in items if i["outcome"] != "api_fail")
    assert dl["denominator"] == "scored_word_count"
    assert dl["scored_word_total"] == scored < tokens
    assert dl["ambiguity_rate_scored"] == pytest.approx(dl["ambiguity_total"] / scored)
    # The whole point: the corrected rate is strictly larger than the one the first
    # report printed, so §18's alarm reads at full strength.
    assert dl["ambiguity_rate_scored"] > dl["ambiguity_total"] / tokens
    # Derived from the recorded COUNTS, so a run written before the rename still
    # gets the corrected figure without anybody editing its rows.
    legacy = [{k: v for k, v in i.items() if not k.endswith("_rate_scored")} for i in items]
    assert decoder_limitation_rates(legacy) == dl


def test_a_cell_that_produced_nothing_stays_in_the_length_denominator():
    """F9's direction is unflattering, so nothing was inflated — but a reader has to
    be told which denominator a rate uses. Both are reported and the primary figure
    does not move (DEVIATIONS 8.34)."""
    # A target range the produced stories actually satisfy, so the only
    # non-compliant row is the cell that produced no text at all.
    wide = _vocab(9, lo=1, hi=100)
    items = [
        _item(_result(stage=9, moderation=APPROVED), "accepted", index=i, vocab=wide)
        for i in range(3)
    ]
    dead = PipelineResult(stage_id=9, theme="space", passed=False)
    dead.total_ms = 91_000.0
    items.append(_item(dead, "api_fail", index=9, vocab=wide))
    agg = aggregate(items, on=ON, cells_planned=4)
    assert agg["length"]["compliance_rate_produced"] == 1.0
    length = agg["length"]
    assert length["compliance_denominator"] == "attempted"
    assert length["compliance_rate"] < length["compliance_rate_produced"]
    stage9 = agg["by_stage"]["9"]
    assert stage9["gate_pass_rate"] <= stage9["gate_pass_rate_produced"]
    report = render_report(_run_meta(agg), agg)
    assert "of ATTEMPTED cells" in report and "of PRODUCED stories" in report


def test_an_unpriced_model_is_not_reported_as_free():
    """F13.4: `_reprice` returned 0.0 on a `KeyError`, so a model with no price sheet
    reported a $0.00 September cost. "We could not price this" is not "this was
    free" (DEVIATIONS 8.36)."""
    items = _mixed_items()
    items[0]["models"]["generation"] = ["claude-nonesuch-9"]
    priced = _reprice(items, POST_INTRO)
    assert priced["total_usd"] is None
    assert priced["unpriced_models"] == ["claude-nonesuch-9"]
    assert priced["partial_total_usd"] > 0
    agg = aggregate(items, on=ON, cells_planned=9)
    meta = _run_meta(agg)
    meta["post_intro"] = priced
    assert "could not be repriced" in render_report(meta, agg)


def test_reprice_uses_the_model_the_item_recorded_not_the_environment():
    """The exact reproduction in F1: run 2's tokens priced at Opus rates under a
    report whose config table said Sonnet at $2/$10."""
    items = _mixed_items()
    sonnet = _reprice(items, POST_INTRO)["total_usd"]
    for it in items:
        it["models"]["generation"] = ["claude-opus-4-8"]
    opus = _reprice(items, POST_INTRO)["total_usd"]
    assert opus > sonnet
    assert "claude-opus-4-8" in _reprice(items, POST_INTRO)["prices"]


# --- the smoke gate has to be able to say why (DEVIATIONS 8.38) ---------------


def test_the_smoke_gate_names_a_moderation_rejection_instead_of_blaming_the_gate():
    """A smoke run failed 3 of 4 on a 100%-decodable story and printed `FAIL 100.0%`
    with `violations: []` and no reason. Smoke persists nothing, so the cause was
    unrecoverable — the cheap CI tripwire produced a red with no diagnosis."""
    rejected = ModerationResult(
        approved=False,
        reason="The dragon sent fire at the man, which is a weapon used against a character.",
        layer="model",
        usage=Usage(model="claude-haiku-4-5", input_tokens=1100, output_tokens=80),
    )
    r = _result(moderation=rejected, passed=False)
    assert r.final_report.decodability == 1.0  # cleared the gate, rejected on content
    why = _smoke_reason(r)
    assert "weapon used against a character" in why
    assert "moderation content" in why
    assert "below the gate" not in why


def test_the_smoke_gate_still_names_a_real_gate_failure_as_one():
    """The other direction, or the fix would just relabel every failure."""
    r = _result(body="The knight sees a beautiful dragon.", passed=False)
    assert r.final_report.decodability < DECODABILITY_GATE
    why = _smoke_reason(r)
    assert "below the gate" in why
    assert "moderation" not in why


def _run_meta(agg: dict) -> dict:
    return {
        "id": 1,
        "started_at": "2026-07-29T00:00:00+00:00",
        "finished_at": "2026-07-29T00:10:00+00:00",
        "status": "completed",
        "config": {
            "stages_spec": "1..11",
            "themes_spec": "all",
            "stages": [1, 2, 3],
            "themes": ["dragons"],
            "n": 5,
            "effort": "medium",
            "cold": True,
            "enrich": False,
            "concurrency": 1,
            "budget_usd": 15.0,
            "pricing_date": ON.isoformat(),
            "models": {"generation": "claude-sonnet-5", "fast": "claude-haiku-4-5"},
            "prices": {
                "generation": {"input_per_mtok": 2.0, "output_per_mtok": 10.0},
                "fast": {"input_per_mtok": 1.0, "output_per_mtok": 5.0},
            },
            "git_commit": "deadbee",
            "python": "3.12.13",
        },
        "projection": None,
        "stories_written": agg["attempted"],
        "servable_before": 52,
        "servable_after": 52,
        "post_intro_cost_usd": None,
        "fatal": None,
    }


# --- the child-safety property (DEVIATIONS 7.41) -----------------------------


def test_the_eval_status_is_not_in_the_servable_allowlist():
    """Cheap, first, and structural: both serving paths filter on an ALLOWLIST,
    so a status they do not name is unreachable by construction rather than by
    anybody remembering to exclude it."""
    assert EVAL_STATUS not in SERVABLE_STATUSES
    assert SERVABLE_STATUSES == ("warm", "complete")


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


#: The stage the eval fixture builds at. Any stage inside §9's 1..11 does: the
#: fixture owns the visible pool, so the eval row is the only candidate on §7's
#: ladder whatever this is. It replaces `stage = min(12, int(top) + 1)` over the
#: live `max(stage_id)`, which resolved to **12, tied with nine live stories**, while
#: the docstring claimed the row was "placed ABOVE every servable one … alone at the
#: top of the ladder" (AUDIT-PHASE7 F2, DEVIATIONS 9.63).
EVAL_FIXTURE_STAGE = 6


async def _plant_eval_fixture_story(session, *, theme_id: int, status: str, title: str) -> int:
    """One row, with segments AND a question — servable but for its `status`.

    **The `questions` row is the whole repair and it is load-bearing.** Without it,
    `ANSWERABLE_SQL` (`api/stories.py:143`) refuses the row in `_SERVE_SQL`, in
    `GET /api/stories/{id}` and in `POST /api/readings` *before* `status` is ever
    consulted — so all three tests below were satisfied by answerability and never
    tested `status` at all. PROVED: with `_SERVE_SQL` sabotaged to
    `WHERE (status = ANY(:statuses) OR status = 'eval')`, `stories.py:730`'s status
    refusal replaced by `if False:` and `readings.py:604`'s deleted, all three
    PASSED — while the same build served a `rejected` story 200 by id and filed a
    reading against it 201 (DEVIATIONS 9.60).

    `decodability 1.0` and a body that really is decodable, because the serve-time
    gate must not be what refuses this row either. The only thing wrong with it is
    the status.
    """
    story_id = (
        await session.execute(
            text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (:st, :th, :status, :title, 1.0, "
                "ARRAY['dragon']::text[], '{\"source\": \"eval\"}'::jsonb) RETURNING id"
            ),
            {"st": EVAL_FIXTURE_STAGE, "th": theme_id, "status": status, "title": title},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO story_segments (story_id, idx, body, scene_tags) "
            "VALUES (:s, 0, 'A dragon sat in a cave. Sam did pat the dragon.', ARRAY[]::text[])"
        ),
        {"s": story_id},
    )
    await session.execute(
        text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:s, 0, 'tap_word', 'Tap: sit.', CAST(:p AS jsonb), CAST(:a AS jsonb))"
        ),
        {
            "s": story_id,
            "p": '{"options": ["sat", "did", "pat"], "cue_word": "sit"}',
            "a": '{"correct": "sat", "index": 0, "target_grapheme": "s"}',
        },
    )
    return story_id


@pytest.fixture
async def eval_story(client):
    """An eval-status story that is servable in every respect EXCEPT its status.

    Three changes from the version AUDIT-PHASE7 F2 found, and each closes one way
    the tests below could pass without observing anything:

    1. **A `questions` row**, so `ANSWERABLE_SQL` is not what refuses it
       (`_plant_eval_fixture_story`).
    2. **The visible pool is hidden** inside a rolled-back REPEATABLE READ
       transaction (DEVIATIONS 8.91's mechanism, shared from `tests/owned_pool.py`),
       so the eval row is not merely at the top of §7's ladder — it is the only row
       on it, whatever `make warm` last produced. The old fixture placed itself with
       `min(12, max(servable stage_id) + 1)`, which is **12** on a correctly warmed
       pool, tied with nine live stories.
    3. **A `warm` twin**, identical in stage, theme, segments and question and
       differing only in `status`. That is the positive control, and without it a 404
       is not attributable: three other refusals stand in front of the status one —
       answerability, `POST /api/readings`'s 422 stage check, and `auth.owned_kid`
       (every kid in the live database belongs to a real Clerk user, not to the
       bypass principal, so the old test's `SELECT id FROM kids ORDER BY id LIMIT 1`
       was refused for ownership before the story row was read — DEVIATIONS 9.60).

    **Nothing is committed, and that is deliberate rather than tidy.**
    `test_no_eval_story_carries_questions_a_child_could_meet` below sweeps every
    committed `eval` story for question rows and is a real guard (§14 does not
    enrich, DEVIATIONS 7.45). Committing the question row would have meant weakening
    that test to fix this one; inside the transaction it never sees it, so it is
    untouched (DEVIATIONS 9.63).

    The reader is created through `POST /api/kids`, so "a kid this caller owns" is
    true by construction, at the story's own stage so `readings.py`'s 422 cannot
    fire either.
    """
    async with owned_transaction() as session:
        hidden = await hide_servable_pool(session)
        theme_id = (
            await session.execute(
                text(
                    "INSERT INTO themes (slug, display_name, emoji) "
                    "VALUES ('zz-eval-fixture', 'Eval Fixture', '🧪') RETURNING id"
                )
            )
        ).scalar_one()
        story_id = await _plant_eval_fixture_story(
            session, theme_id=theme_id, status=EVAL_STATUS, title="Eval Artifact"
        )
        twin_id = await _plant_eval_fixture_story(
            session, theme_id=theme_id, status="warm", title="The Same Row But Warm"
        )
        await session.commit()

        kid = await client.post(
            "/api/kids", json={"display_name": "Ana", "stage_id": EVAL_FIXTURE_STAGE}
        )
        assert kid.status_code == 201, kid.text

        yield {
            "story_id": story_id,
            "twin_id": twin_id,
            "stage": EVAL_FIXTURE_STAGE,
            "theme_id": theme_id,
            "kid_id": kid.json()["id"],
            "hidden": hidden,
        }


async def test_an_eval_story_is_never_served_by_the_request_endpoint(client, eval_story):
    """The rule the harness must not break: nothing it writes reaches a child.

    Asserted through the endpoint, not through the constant. `SERVABLE_STATUSES`
    is not what serves stories — `_SERVE_SQL` is — and DEVIATIONS 6.14 records
    exactly what happens when a test and the endpoint disagree about which
    statuses count.

    justify-test-edit — VERDICT: TEST IS WRONG, and the assertion is TIGHTENED
    rather than loosened. It read `assert r.status_code in (200, 503)` with the id
    check inside an `if`, so a 503 satisfied it and any other story satisfied it —
    which was the honest shape while the fixture shared a ladder with the live pool
    and could not know what else was on it. ORIGIN: commit `538a08b`, DEVIATIONS
    7.41. REVERT CHECK: nothing to revert — it was green on pristine code and green
    with `eval` made servable (DEVIATIONS 9.60), because the fixture carried no
    question row. EVIDENCE the code is right: the auditor got 404 for an `eval` story
    live against a `demo_bypass_auth: false` server with a real Clerk token.

    Now the visible pool holds exactly two stories, identical but for `status`, and
    the eval row holds the LOWER id — so it wins `_SERVE_SQL`'s last tiebreak and a
    servable `eval` status has one unambiguous signature: this request returns the
    eval id instead of the twin's. That is the experiment `docs/EVAL.md:315` claims
    this test is, and after this change it is one.
    """
    # `stage_id` is the admin preview knob (DEVIATIONS 7.21); the suite's bypass
    # principal is an admin, so this is the one body that can name a stage
    # directly and therefore the one that can aim at the fixture.
    assert eval_story["story_id"] < eval_story["twin_id"], (
        "the eval row must hold the lower id: `_SERVE_SQL` breaks the final tie on "
        "`id ASC`, and that is what makes it the row a servable `eval` status wins with"
    )

    r = await client.post(
        "/api/stories/request",
        json={"stage_id": eval_story["stage"], "theme_id_or_slug": eval_story["theme_id"]},
    )
    # The pool is owned, so the served story is one of the fixture's two rows. The
    # 200 is itself the control: the twin is reachable, so nothing but `status`
    # separates the two outcomes.
    assert r.status_code == 200, (
        f"the warm twin should have been served ({eval_story['hidden']} real stories "
        f"hidden): {r.text}"
    )
    assert r.json()["id"] == eval_story["twin_id"], (
        f"served {r.json()['id']}; the warm twin is {eval_story['twin_id']} and the "
        f"eval artifact is {eval_story['story_id']}"
    )


async def test_an_eval_story_is_not_fetchable_by_id(client, eval_story):
    """`GET /api/stories/{id}` refuses it too — and the twin proves why.

    The assertion is unchanged; what changed is that the fixture now carries a
    question row, so `stories.py:734` (answerability) can no longer answer for
    `:730` (status). PROVED: with `:730` replaced by `if False:` this test passed
    while `GET` on a rejected-but-answerable story returned 200 (DEVIATIONS 9.60).
    """
    r = await client.get(f"/api/stories/{eval_story['story_id']}")
    assert r.status_code == 404, r.text

    twin = await client.get(f"/api/stories/{eval_story['twin_id']}")
    assert twin.status_code == 200, (
        "the twin differs from the eval row only in `status`, so if it is not "
        f"fetchable the 404 above proves nothing about `status`: {twin.text}"
    )


async def test_an_eval_story_cannot_be_read_by_a_kid(client, eval_story):
    """`POST /api/readings` is the only path that puts a story in a child's
    record. It must not accept one either.

    justify-test-edit — VERDICT: TEST IS WRONG, and the assertion is TIGHTENED.
    It read `assert r.status_code >= 400`, which is satisfied by four different
    refusals, three of which fire before the one the test names: `ANSWERABLE_SQL`'s
    404 (`readings.py:606` — the fixture had no question row), the 422 stage check
    (`readings.py:611` — the fixture sat at the top of the ladder, above every kid),
    and `auth.owned_kid`'s 404, because `SELECT id FROM kids ORDER BY id LIMIT 1`
    returns a kid belonging to a real Clerk user rather than to the bypass principal
    (**every** kid row in the live database does). ORIGIN: commit `538a08b`,
    DEVIATIONS 7.41 + 8.50. REVERT CHECK: nothing to revert — green on pristine code
    and green with `readings.py:604`'s status refusal deleted, while that same build
    filed a 201 reading against a `rejected` story (DEVIATIONS 9.60).

    So: `== 404`, a reader created through `POST /api/kids` at the story's own stage,
    and the `warm` twin as the control that makes the 404 attributable to `status`.
    """
    r = await client.post(
        "/api/readings", json={"kid_id": eval_story["kid_id"], "story_id": eval_story["story_id"]}
    )
    assert r.status_code == 404, r.text

    twin = await client.post(
        "/api/readings", json={"kid_id": eval_story["kid_id"], "story_id": eval_story["twin_id"]}
    )
    assert twin.status_code == 201, (
        "the twin differs from the eval row only in `status`, so if a reading cannot "
        f"be started against it either, the 404 above proves nothing: {twin.text}"
    )


async def _plant_run(session, *, config: dict, n_items: int) -> int:
    run_id = (
        await session.execute(
            text(
                "INSERT INTO eval_runs (config, metrics, status, started_at) "
                "VALUES (CAST(:c AS jsonb), '{}'::jsonb, 'running', now()) RETURNING id"
            ),
            {"c": json.dumps(config)},
        )
    ).scalar_one()
    for i in range(n_items):
        metrics = _item(_result(stage=3, theme="dragons", moderation=APPROVED), "accepted", index=i)
        await session.execute(
            text(
                "INSERT INTO eval_run_items (run_id, story_id, metrics) "
                "VALUES (:r, NULL, CAST(:m AS jsonb))"
            ),
            {"r": run_id, "m": json.dumps(metrics)},
        )
    await session.commit()
    return run_id


def _stored_config(*, n: int) -> dict:
    """A config shaped like the one a run writes, recording Sonnet."""
    return {
        "stages_spec": "3",
        "themes_spec": "dragons",
        "stages": [3],
        "themes": ["dragons"],
        "n": n,
        "effort": "medium",
        "cold": True,
        "enrich": False,
        "concurrency": 1,
        "budget_usd": 15.0,
        "pricing_date": ON.isoformat(),
        "models": {"generation": "claude-sonnet-5", "fast": "claude-haiku-4-5"},
        "prices": {
            "generation": {"input_per_mtok": 2.0, "output_per_mtok": 10.0},
            "fast": {"input_per_mtok": 1.0, "output_per_mtok": 5.0},
        },
        "gate": DECODABILITY_GATE,
        "max_rewrites": 3,
        "max_story_words": 3,
        "prompt_word_limit": 400,
        "cells_planned": n,
        "git_commit": "deadbee",
        "python": "3.12.13",
    }


async def _drop_run(session, run_id: int) -> None:
    await session.rollback()
    await session.execute(text("DELETE FROM eval_run_items WHERE run_id = :r"), {"r": run_id})
    await session.execute(text("DELETE FROM eval_runs WHERE id = :r"), {"r": run_id})
    await session.commit()


async def test_a_resume_refuses_to_continue_a_matrix_under_a_different_model(
    session, tmp_path, monkeypatch
):
    """AUDIT-PHASE7 F1, end to end, and the reason this is an experiment rather
    than a restatement: `run_pipeline` is replaced with something that raises if it
    is reached, so a resume that does NOT refuse fails the test instead of spending
    money on half a matrix measured against the wrong model.

    The audit's reproduction was `ANTHROPIC_MODEL_GENERATE=claude-opus-4-8
    python -m storyforge.eval run --resume 2`, which produced ONE report saying
    `generation model: claude-sonnet-5` while pricing the run at Opus rates. With
    cells left to run, that command now exits 3 (DEVIATIONS 8.31).
    """
    run_id = await _plant_run(session, config=_stored_config(n=2), n_items=1)

    def _no_api(*a, **k):
        raise SystemExit("a refused resume must not reach the API")

    monkeypatch.setattr(storyforge.eval, "run_pipeline", _no_api)
    monkeypatch.setattr(storyforge.eval, "generation_model", lambda: "claude-opus-4-8")
    try:
        code = await storyforge.eval.run_full(
            stages_spec="3",
            themes_spec="dragons",
            n=2,
            effort="medium",
            cold=True,
            budget_usd=15.0,
            concurrency=1,
            pricing_date=ON,
            estimate_only=False,
            enrich=False,
            out_dir=tmp_path,
            resume_run_id=run_id,
        )
        assert code == 3, "a model change with cells left to run must refuse, not continue"
        # Refused BEFORE touching the row: the run is still what it was, so a
        # refusal cannot corrupt the record it was protecting.
        row = (
            await session.execute(
                text("SELECT status, config FROM eval_runs WHERE id = :r"), {"r": run_id}
            )
        ).one()
        assert row.status == "running"
        assert row.config["models"]["generation"] == "claude-sonnet-5"
        assert "resumed" not in row.config
        assert not list(tmp_path.iterdir()), "a refused resume must not write a report"
    finally:
        await _drop_run(session, run_id)


async def test_a_complete_run_may_be_re_derived_under_drift_but_must_disclose_it(
    session, tmp_path, monkeypatch
):
    """The other half of 8.31, and the one that keeps the refusal honest.

    Re-deriving a report from a complete run costs nothing and calls no API — it is
    how run 2's report was regenerated twice — so refusing it would break a
    legitimate operation. What it may not do is stay quiet: the drift lands in the
    trail entry and at the top of the report.
    """
    run_id = await _plant_run(session, config=_stored_config(n=1), n_items=1)

    def _no_api(*a, **k):
        raise SystemExit("a complete run must not reach the API")

    monkeypatch.setattr(storyforge.eval, "run_pipeline", _no_api)
    monkeypatch.setattr(storyforge.eval, "generation_model", lambda: "claude-opus-4-8")
    try:
        code = await storyforge.eval.run_full(
            stages_spec="3",
            themes_spec="dragons",
            n=1,
            effort="medium",
            cold=True,
            budget_usd=15.0,
            concurrency=1,
            pricing_date=ON,
            estimate_only=False,
            enrich=False,
            out_dir=tmp_path,
            resume_run_id=run_id,
        )
        assert code == 0
        cfg = (
            await session.execute(text("SELECT config FROM eval_runs WHERE id = :r"), {"r": run_id})
        ).scalar_one()
        trail = cfg["resumed"]
        assert trail[-1]["cells_remaining"] == 0
        drifted = {d["key"] for d in trail[-1]["config_drift"]}
        assert "models" in drifted
        # A value this run never recorded is pinned on the SITTING that knows it,
        # never written into the run's config where it would imply the earlier
        # sitting had used it too (DEVIATIONS 8.31/8.33).
        unrecorded = trail[-1]["unrecorded_live_values"]
        assert unrecorded["vocabulary"]["gpc_count"] == len(storyforge.eval.load_gpcs())
        assert "vocabulary" not in cfg
        # The stored config still wins the RECORD — that part was always right.
        assert cfg["models"]["generation"] == "claude-sonnet-5"
        # And the per-sitting values are the ones this invocation actually used.
        assert trail[-1]["concurrency"] == 1
        report = next(p for p in tmp_path.iterdir() if p.suffix == ".md").read_text()
        assert "CONFIG DRIFT" in report
        assert "claude-opus-4-8" in report or "`models`" in report
    finally:
        await _drop_run(session, run_id)


async def test_a_resume_does_not_widen_the_matrix_when_a_theme_is_seeded(
    session, tmp_path, monkeypatch
):
    """Audit S2: `--themes all` re-resolved after a new theme appears grows
    `cells_planned` while the config records the old list, so "the matrix
    completed" compares a new denominator against an old config. The stored theme
    LIST wins (DEVIATIONS 8.31)."""
    config = _stored_config(n=1) | {"themes_spec": "all", "themes": ["dragons"]}
    run_id = await _plant_run(session, config=config, n_items=1)
    monkeypatch.setattr(
        storyforge.eval, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(SystemExit("no API"))
    )
    try:
        code = await storyforge.eval.run_full(
            stages_spec="3",
            themes_spec="all",
            n=1,
            effort="medium",
            cold=True,
            budget_usd=15.0,
            concurrency=1,
            pricing_date=ON,
            estimate_only=False,
            enrich=False,
            out_dir=tmp_path,
            resume_run_id=run_id,
        )
        # The live database holds more than one theme, so re-resolving `all` would
        # have planned 3 cells against 1 recorded item and reported an incomplete
        # matrix. Restoring the list keeps it at 1 and the run reads as complete.
        assert code == 0
        metrics = (
            await session.execute(
                text("SELECT metrics FROM eval_runs WHERE id = :r"), {"r": run_id}
            )
        ).scalar_one()
        assert metrics["cells_planned"] == 1
        assert metrics["attempted"] == 1
    finally:
        await _drop_run(session, run_id)


async def test_a_fresh_run_records_everything_a_later_reader_needs(session, tmp_path, monkeypatch):
    """The path `make eval` takes, with the API replaced and everything else real.

    Every other test here is either a pure function or a resume. This one drives
    `run_full` from nothing: it builds the config (provenance, vocabulary
    fingerprint, timeouts), loops over `remaining_cells`, persists a story at
    `status='eval'`, aggregates, reprices, renders and writes the report — so a
    provenance field that is computed but never reaches `eval_runs.config`, or a
    loop that runs zero cells, fails here rather than in a 90-minute live matrix.
    The generation call is the ONLY thing stubbed (DEVIATIONS 8.30-8.35).
    """
    result = _result(stage=3, theme="dragons", moderation=APPROVED)

    async def _fake_pipeline(_session, stage, theme, effort="medium", **kw):
        return result

    monkeypatch.setattr(storyforge.eval, "run_pipeline", _fake_pipeline)
    run_id = None
    try:
        code = await storyforge.eval.run_full(
            stages_spec="3",
            themes_spec="dragons",
            n=1,
            effort="medium",
            cold=True,
            budget_usd=15.0,
            concurrency=1,
            pricing_date=ON,
            estimate_only=False,
            enrich=False,
            out_dir=tmp_path,
        )
        assert code == 0
        row = (
            await session.execute(
                text("SELECT id, config, metrics FROM eval_runs ORDER BY id DESC LIMIT 1")
            )
        ).one()
        run_id, cfg, metrics = row.id, row.config, row.metrics
        # The loop ran: a fresh run has every cell remaining, and a bug that made
        # it zero would report an empty matrix rather than fail loudly.
        assert metrics["attempted"] == 1 and metrics["produced"] == 1

        # F2: a commit AND whether the tree that produced it was clean.
        assert cfg["provenance"]["git_commit"] == cfg["git_commit"]
        assert cfg["provenance"]["git_dirty"] in (True, False)
        assert len(cfg["provenance"]["code_sha256"]["storyforge/eval.py"]) == 64
        # F3: the instrument, pinned.
        assert cfg["vocabulary"]["gpc_count"] == len(storyforge.eval.load_gpcs())
        assert cfg["vocabulary"]["sight_word_count"] > 0
        # F7: the budget a latency has to be read against.
        assert cfg["generation_timeout_s"] > 0
        assert cfg["sdk_max_retries"] == 2

        # F1: the model, per item, from the call's own Usage.
        item = (
            await session.execute(
                text("SELECT metrics FROM eval_run_items WHERE run_id = :r"), {"r": run_id}
            )
        ).scalar_one()
        assert item["models"]["generation"] == ["claude-sonnet-5"]
        assert item["per_call_timeout_s"] == cfg["generation_timeout_s"]
        assert metrics["models_observed"]["generation"] == {"claude-sonnet-5": 1}

        report = next(p for p in tmp_path.iterdir() if p.suffix == ".md").read_text()
        assert "one model, confirmed per item" in report or "claude-sonnet-5` x1" in report
        assert "SCORED words" in report
        # And the story it wrote is not servable, which is the property the whole
        # harness rests on (DEVIATIONS 7.41).
        statuses = (
            await session.execute(
                text(
                    "SELECT DISTINCT s.status FROM stories s JOIN eval_run_items i "
                    "ON i.story_id = s.id WHERE i.run_id = :r"
                ),
                {"r": run_id},
            )
        ).scalars().all()
        assert statuses == [EVAL_STATUS]
    finally:
        await session.rollback()
        if run_id is not None:
            await session.execute(
                text(
                    "DELETE FROM story_segments WHERE story_id IN "
                    "(SELECT story_id FROM eval_run_items WHERE run_id = :r "
                    " AND story_id IS NOT NULL)"
                ),
                {"r": run_id},
            )
            await session.execute(
                text(
                    "DELETE FROM stories WHERE id IN (SELECT story_id FROM eval_run_items "
                    "WHERE run_id = :r AND story_id IS NOT NULL)"
                ),
                {"r": run_id},
            )
            await _drop_run(session, run_id)


async def test_the_gate_still_agrees_with_what_the_harness_recorded(session):
    """Re-run the pure gate over every story the harness wrote and compare.

    `stories.decodability` is a number the harness computed once. This re-derives
    it from `story_segments` with `validate_text`, which is the actual gate, so a
    harness that mis-recorded its own verdict is caught. Trivially true today —
    which is the point of asserting it rather than assuming it.
    """
    rows = (
        await session.execute(
            text(
                "SELECT s.id, s.stage_id, s.decodability, s.story_words, "
                "  string_agg(g.body, ' ' ORDER BY g.idx) AS body "
                "FROM stories s JOIN story_segments g ON g.story_id = s.id "
                "WHERE s.status = :st GROUP BY s.id ORDER BY s.id"
            ),
            {"st": EVAL_STATUS},
        )
    ).all()
    if not rows:
        pytest.skip("no eval stories in this database yet")
    sight = {
        stage: frozenset(
            r[0]
            for r in (
                await session.execute(
                    text("SELECT word FROM sight_words WHERE stage_id <= :s"), {"s": stage}
                )
            )
        )
        for stage in {r.stage_id for r in rows}
    }
    for row in rows:
        report = validate_text(
            row.body,
            mastered_ids(row.stage_id),
            sight[row.stage_id],
            frozenset(row.story_words or []),
        )
        assert report.decodability == pytest.approx(float(row.decodability), abs=5e-4), (
            f"story {row.id}: recorded {row.decodability}, gate says {report.decodability}"
        )


async def test_no_eval_story_has_ever_been_served(session):
    """`last_served_at` is stamped by `_SERVE_SQL`. A non-null one on an eval row
    would mean the serving path reached a status it does not name."""
    leaked = (
        await session.execute(
            text(
                "SELECT id FROM stories WHERE status = :st AND last_served_at IS NOT NULL"
            ),
            {"st": EVAL_STATUS},
        )
    ).all()
    assert not leaked, f"eval stories carry a serve stamp: {[r[0] for r in leaked]}"


async def test_no_eval_story_carries_questions_a_child_could_meet(session):
    """The harness does not enrich (DEVIATIONS 7.45), and an unenriched story in
    front of a child is the Phase 4 audit's defect. Nothing should have written
    question rows against these."""
    rows = (
        await session.execute(
            text(
                "SELECT q.id FROM questions q JOIN stories s ON s.id = q.story_id "
                "WHERE s.status = :st"
            ),
            {"st": EVAL_STATUS},
        )
    ).all()
    assert not rows, f"eval stories carry question rows: {[r[0] for r in rows]}"


def test_the_gate_constant_has_not_moved():
    """§0: the validator is never weakened to make generation easier. An eval
    harness whose own gate constant drifted would report a pass it did not earn."""
    assert DECODABILITY_GATE == 0.95
