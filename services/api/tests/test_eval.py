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

from datetime import date

import httpx
import pytest
from sqlalchemy import text

from storyforge.api.stories import SERVABLE_STATUSES
from storyforge.db import SessionLocal
from storyforge.decoder import mastered_ids, validate_text
from storyforge.eval import (
    DECODABILITY_BINS,
    EVAL_STATUS,
    REWRITE_DEPTH_P95_TARGET,
    _reprice,
    aggregate,
    bin_decodability,
    criteria_verdict,
    hazard_suspect,
    moderation_class,
    parse_stages,
    project_cost,
    quantile,
    render_report,
    story_metrics,
    type_token_ratio,
)
from storyforge.llm.moderate import UNGRAMMATICAL_PREFIX, ModerationResult
from storyforge.llm.pricing import Usage
from storyforge.main import app
from storyforge.pipeline import DECODABILITY_GATE, Attempt, PipelineResult, Vocabulary

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


def _item(result: PipelineResult, outcome: str, *, index: int = 0, vocab=None) -> dict:
    return story_metrics(
        result,
        vocab or _vocab(result.stage_id),
        index=index,
        outcome=outcome,
        on=ON,
        story_id=1000 + index,
    )


# --- the CLI surface §14 documents -------------------------------------------


def test_parse_stages_accepts_the_range_form_the_spec_documents():
    """§14's own command line is `--stages 1..12`, so that has to parse."""
    assert parse_stages("1..12") == list(range(1, 13))
    assert parse_stages("3,6") == [3, 6]
    assert parse_stages("1..4,9..12,6") == [1, 2, 3, 4, 6, 9, 10, 11, 12]
    assert parse_stages(" 5 ") == [5]


def test_parse_stages_refuses_input_it_cannot_honour():
    """A run over 9 stages reported as a run over 12 is the failure mode."""
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
        "schwa_relaxed_rate",
        "ambiguity_rate",
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
    assert item["unalignable_rate"] > 0


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
    agg = aggregate(items, on=ON, cells_planned=180)
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
    agg = aggregate([], on=ON, cells_planned=180)
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
    after = _reprice(items, POST_INTRO)
    assert after > intro
    # Sonnet goes $2/$10 -> $3/$15, a flat 1.5x. Haiku's price does not change,
    # so the whole-run ratio sits strictly between 1 and 1.5 — and where it sits
    # is the share of the bill that is generation rather than moderation.
    assert 1.0 < after / intro < 1.5


def test_the_projection_grows_with_the_matrix_and_never_undercuts_the_worst_case():
    v = [_vocab(1, lo=30, hi=60), _vocab(12, lo=120, hi=200)]
    small = project_cost(v[:1], 5, ON)
    big = project_cost(v, 5, ON)
    assert big["no_rewrites_usd"] > small["no_rewrites_usd"]
    assert (
        big["worst_case_all_budget_exhausted_usd"]
        > big["with_one_rewrite_each_usd"]
        > big["no_rewrites_usd"]
    )
    assert big["pricing_date"] == ON.isoformat()


def _run_meta(agg: dict) -> dict:
    return {
        "id": 1,
        "started_at": "2026-07-29T00:00:00+00:00",
        "finished_at": "2026-07-29T00:10:00+00:00",
        "status": "completed",
        "config": {
            "stages_spec": "1..12",
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


@pytest.fixture
async def eval_story(session):
    """An eval-status story placed ABOVE every servable one, then deleted.

    Above, deliberately: `_SERVE_SQL` orders `stage_id DESC` under
    `stage_id <= :stage`, so a story alone at the top of the ladder wins every
    request at or above its stage. If `status='eval'` were servable, THIS row is
    the one that would be served — which makes the assertion below a real
    experiment rather than a restatement of the SQL.
    """
    top = (
        await session.execute(
            text("SELECT coalesce(max(stage_id), 0) FROM stories WHERE status = ANY(:s)"),
            {"s": list(SERVABLE_STATUSES)},
        )
    ).scalar_one()
    stage = min(12, int(top) + 1)
    theme_id = (
        await session.execute(
            text(
                "INSERT INTO themes (slug, display_name, emoji) "
                "VALUES ('zz-eval-fixture', 'Eval Fixture', '🧪') RETURNING id"
            )
        )
    ).scalar_one()
    story_id = (
        await session.execute(
            text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (:st, :th, :status, 'Eval Artifact', 1.0, "
                "ARRAY['dragon']::text[], '{\"source\": \"eval\"}'::jsonb) RETURNING id"
            ),
            {"st": stage, "th": theme_id, "status": EVAL_STATUS},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO story_segments (story_id, idx, body, scene_tags) "
            "VALUES (:s, 0, 'A dragon sat.', ARRAY[]::text[])"
        ),
        {"s": story_id},
    )
    await session.commit()
    try:
        yield {"story_id": story_id, "stage": stage, "theme_id": theme_id}
    finally:
        await session.rollback()
        await session.execute(text("DELETE FROM stories WHERE id = :i"), {"i": story_id})
        await session.execute(text("DELETE FROM themes WHERE id = :i"), {"i": theme_id})
        await session.commit()


async def test_an_eval_story_is_never_served_by_the_request_endpoint(client, eval_story):
    """The rule the harness must not break: nothing it writes reaches a child.

    Asserted through the endpoint, not through the constant. `SERVABLE_STATUSES`
    is not what serves stories — `_SERVE_SQL` is — and DEVIATIONS 6.14 records
    exactly what happens when a test and the endpoint disagree about which
    statuses count.
    """
    # `stage_id` is the admin preview knob (DEVIATIONS 7.21); the suite's bypass
    # principal is an admin, so this is the one body that can name a stage
    # directly and therefore the one that can aim at the fixture.
    r = await client.post(
        "/api/stories/request",
        json={"stage_id": eval_story["stage"], "theme_id_or_slug": eval_story["theme_id"]},
    )
    # Its theme has nothing else in it and it is the top of the ladder, so a
    # servable `eval` status would show up as this story. Either the pool falls
    # back to another story or it 503s; both mean the eval row was skipped.
    assert r.status_code in (200, 503), r.text
    if r.status_code == 200:
        assert r.json()["id"] != eval_story["story_id"]


async def test_an_eval_story_is_not_fetchable_by_id(client, eval_story):
    r = await client.get(f"/api/stories/{eval_story['story_id']}")
    assert r.status_code == 404, r.text


async def test_an_eval_story_cannot_be_read_by_a_kid(client, session, eval_story):
    """`POST /api/readings` is the only path that puts a story in a child's
    record. It must not accept one either."""
    kid_id = (
        await session.execute(
            text("SELECT id FROM kids ORDER BY id LIMIT 1"),
        )
    ).scalar()
    if kid_id is None:
        pytest.skip("no kid rows in this database")
    r = await client.post(
        "/api/readings", json={"kid_id": kid_id, "story_id": eval_story["story_id"]}
    )
    assert r.status_code >= 400, r.text


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
