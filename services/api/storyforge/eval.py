"""Eval harness (§14).

Two entry points, one implementation:

    python -m storyforge.eval smoke                      # §14 regression gate
    python -m storyforge.eval run --stages 1..12 --themes all --n 5 [--cold]

Both run the REAL pipeline against the REAL API — that is the entire point. A
green suite built on a fake client would prove nothing about the one question
that matters: can a constrained model actually hit the gate?

**What this harness is evidence for.** §0's thesis is that a constrained LLM plus
a pure-function gate yields reliably decodable stories. The gate is
`storyforge.decoder`, and it is never touched here: `run_pipeline` is called
exactly as `make warm` calls it, so a story that fails, fails, and the number
gets printed. §0's rule is that the validator is never weakened to make
generation easier, and the corollary for a report is that a bad pass rate is
reported as bad. A flattering eval is worthless.

**What it writes to the database.** `eval_runs` + `eval_run_items` (§8, §14) and
one `stories` row per attempted story, at `status='eval'` — a status neither
serving path names (DEVIATIONS 7.41). Nothing this harness writes can be served
to a child, whether it passed or not, and that is structural rather than a
promise: `api/stories.py::SERVABLE_STATUSES` is an allowlist.

**Spend.** `--budget` (default $15) is checked against the running actual after
every story. The pre-flight projection is a token model over prompt sizes the
harness can measure with no API call and is explicitly not a promise
(DEVIATIONS 7.44).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select, text

from storyforge import obs
from storyforge.api.stories import SERVABLE_STATUSES
from storyforge.db import SessionLocal
from storyforge.decoder import WordVerdict
from storyforge.llm.client import fast_model, generation_model
from storyforge.llm.generate import SYSTEM_RULES, build_request_block, build_vocabulary_block
from storyforge.llm.moderate import UNGRAMMATICAL_PREFIX
from storyforge.llm.pricing import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    price_for,
)
from storyforge.models import EvalRun, EvalRunItem, Story, StorySegment
from storyforge.pipeline import (
    DECODABILITY_GATE,
    MAX_REWRITES,
    MAX_STORY_WORDS,
    PROMPT_WORD_LIMIT,
    PipelineResult,
    Vocabulary,
    load_vocabulary,
    run_pipeline,
)
from storyforge.warm import _split_segments

#: §14's own default matrix is §1 item 2's acceptance matrix: 12 stages x 3
#: themes x 5 stories. Spelled as the spec spells it (`1..12`, `all`) so the
#: documented command line is the one that actually runs.
DEFAULT_STAGES = "1..12"
DEFAULT_THEMES = "all"
DEFAULT_N = 5

#: Hard spend cap in USD, checked against the RUNNING ACTUAL after every story.
#: Crossing it stops the run, marks it `budget_stopped`, writes the report from
#: the items that completed, and exits non-zero (DEVIATIONS 7.44).
DEFAULT_BUDGET_USD = 15.0

#: §1 item 2's second half. p95 over the whole matrix, not per cell.
REWRITE_DEPTH_P95_TARGET = 2

#: §1 item 6. Compared against per-story wall time, which is only meaningful
#: because the reported run is sequential (DEVIATIONS 7.48).
COLD_LATENCY_P95_TARGET_MS = 12_000

#: The story that a fourth `status` value must never become. Imported from the
#: endpoint rather than re-spelled, so the assertion below cannot drift from it.
EVAL_STATUS = "eval"

#: Decodability bins for the DISTRIBUTION §14 asks for. A mean hides the shape:
#: 20 stories at 100% and one at 20% has a healthy-looking mean and one child
#: who cannot read their story.
DECODABILITY_BINS: tuple[tuple[str, float, float], ...] = (
    ("<50%", 0.0, 0.50),
    ("50-80%", 0.50, 0.80),
    ("80-90%", 0.80, 0.90),
    ("90-<95%", 0.90, 0.95),
    ("95-<100%", 0.95, 1.0),
    ("100%", 1.0, 1.0001),
)

#: Words that make a §13 content rejection *look* like a hazard false positive
#: (STATE invariant 5: a fire-breathing dragon is the premise, not a defect).
#: This is a HEURISTIC over free text the model wrote and is labelled as one
#: everywhere it appears — the verbatim reasons are the actual evidence
#: (DEVIATIONS 7.46).
_HAZARD_HINTS = (
    "scary",
    "scared",
    "frighten",
    "fright",
    "peril",
    "unresolved",
    "distress",
    "dark",
    "fire",
    "danger",
    "lost",
)


# --- pure metric helpers ------------------------------------------------------


def parse_stages(spec: str) -> list[int]:
    """`"1..12"`, `"3,6"`, `"1..4,7,9..12"` -> a sorted, deduplicated list.

    §14 writes the range form (`--stages 1..12`) so it has to parse. Bad input
    raises rather than silently yielding a smaller matrix than the operator
    asked for — a run over 9 stages reported as a run over 12 is the exact class
    of quiet mis-measurement this file exists to avoid.
    """
    out: set[int] = set()
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        if ".." in part:
            lo, _, hi = part.partition("..")
            try:
                lo_i, hi_i = int(lo), int(hi)
            except ValueError as exc:
                raise SystemExit(f"bad --stages range {part!r}") from exc
            if hi_i < lo_i:
                raise SystemExit(f"bad --stages range {part!r}: {hi_i} < {lo_i}")
            out.update(range(lo_i, hi_i + 1))
        else:
            try:
                out.add(int(part))
            except ValueError as exc:
                raise SystemExit(f"bad --stages value {part!r}") from exc
    if not out:
        raise SystemExit("--stages selected no stages")
    return sorted(out)


def quantile(values: Sequence[float], q: float) -> float:
    """Nearest-rank quantile. Deterministic and defined on n=1.

    Deliberately not `statistics.quantiles`, which interpolates and needs n>=2:
    a p95 that invents a value between two observations is not a measurement,
    and a cell with one story still has a p95.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def bin_decodability(values: Iterable[float]) -> dict[str, int]:
    """Count values into `DECODABILITY_BINS`. Every value lands in exactly one."""
    out = {label: 0 for label, _, _ in DECODABILITY_BINS}
    for v in values:
        for label, lo, hi in DECODABILITY_BINS:
            if lo <= v < hi:
                out[label] += 1
                break
    return out


def word_tokens(result_words) -> list[str]:
    """The scored+unscored WORD tokens of a report, punctuation dropped."""
    return [w.word for w in result_words if w.verdict is not WordVerdict.PUNCT]


def type_token_ratio(words: Sequence[str]) -> float:
    """§14's type-token ratio. 0.0 on an empty story rather than a ZeroDivision.

    Note for the reader of the report: a decodable reader repeats deliberately
    (STATE invariant 3), so a LOW ratio here is the format working, not a defect.
    It is reported because §14 asks for it, not as a quality target.
    """
    return len(set(words)) / len(words) if words else 0.0


def moderation_class(mod) -> str:
    """`approved` | `blocklist` | `grammar` | `content` (DEVIATIONS 7.46).

    `moderate()` folds the grammar half into `approved` and marks it with
    `UNGRAMMATICAL_PREFIX`, which is the only deterministic signal separating
    "unsafe" from "ungrammatical" after the fold. Hazard-versus-harm is NOT
    derivable here and is not guessed — see `hazard_suspect`.
    """
    if mod is None:
        return "not_reached"
    if mod.approved:
        return "approved"
    if mod.layer == "blocklist":
        return "blocklist"
    if not mod.well_formed and mod.reason.startswith(UNGRAMMATICAL_PREFIX):
        return "grammar"
    if not mod.well_formed:
        # Both halves objected. Counted as content, and the grammar note is kept
        # on the item so it cannot hide behind the safety reason (warm.py's
        # `_rejected_on_content` makes the same distinction).
        return "content"
    return "content"


def hazard_suspect(reason: str) -> bool:
    """HEURISTIC: does this content rejection read like a hazard false positive?

    STATE invariant 5 says moderation must separate hazard from harm, and a
    spike either way is the signal. The verdict carries no field for it — only a
    sentence the model wrote — so this keyword scan is offered as a pointer at
    reasons worth reading, never as a measurement. Every content reject's reason
    is printed verbatim next to it.
    """
    low = reason.lower()
    harmed = any(k in low for k in ("hurt", "injur", "kill", "dies", "died", "cruel", "weapon"))
    return not harmed and any(k in low for k in _HAZARD_HINTS)


def story_metrics(
    result: PipelineResult,
    vocab: Vocabulary,
    *,
    index: int,
    outcome: str,
    on: date,
    story_id: int | None,
) -> dict:
    """§14's per-story metric row. Pure given a finished `PipelineResult`."""
    report = result.final_report
    words = word_tokens(report.words) if report else []
    counts = dict(report.counts) if report else {}
    n_words = len(words)

    gen_cost = result.generation_cost_usd(on)
    mod_cost = result.moderation_cost_usd(on)
    tok = Counter()
    for a in result.attempts:
        tok["input"] += a.usage.input_tokens
        tok["output"] += a.usage.output_tokens
        tok["cache_read"] += a.usage.cache_read_input_tokens
        tok["cache_write"] += a.usage.cache_creation_input_tokens
    if result.moderation is not None and result.moderation.usage is not None:
        mu = result.moderation.usage
        tok["mod_input"] += mu.total_input_tokens
        tok["mod_output"] += mu.output_tokens

    mod = result.moderation
    return {
        "stage_id": result.stage_id,
        "theme": result.theme,
        "index": index,
        "outcome": outcome,
        "passed": result.passed,
        "story_id": story_id,
        "langfuse_trace_id": result.langfuse_trace_id,
        # --- the thesis ---
        "decodability": report.decodability if report else 0.0,
        "gate": DECODABILITY_GATE,
        "meets_gate": bool(report and report.decodability >= DECODABILITY_GATE),
        "rewrite_depth": result.rewrite_depth,
        "attempts": len(result.attempts),
        "decodability_by_attempt": [a.decodability for a in result.attempts],
        # --- §14 per-story metrics ---
        "counts": counts,
        "oov": counts.get("oov", 0),
        "oov_words": sorted(
            {w.word for w in (report.words if report else []) if w.verdict is WordVerdict.OOV}
        ),
        "violations": counts.get("violation", 0),
        "violation_words": sorted({w.word for w in (report.violations if report else [])}),
        "missing_gpcs": sorted(
            {g for w in (report.violations if report else []) for g in w.missing_gpcs}
        ),
        "non_minimal_missing": sum(
            1 for w in (report.violations if report else []) if not w.missing_minimal
        ),
        "scored_word_count": report.scored_word_count if report else 0,
        "word_count": n_words,
        "distinct_word_count": len(set(words)),
        "type_token_ratio": round(type_token_ratio(words), 4),
        "length_target": [vocab.target_min, vocab.target_max],
        "length_compliant": bool(vocab.target_min <= n_words <= vocab.target_max),
        "story_word_occurrences": report.story_word_count if report else 0,
        "distinct_story_words": list(report.distinct_story_words) if report else [],
        "story_words_offered": len(vocab.story_words),
        "story_word_cap": MAX_STORY_WORDS,
        # --- the decoder's own disclosed limitations (§5.3, §18) ---
        "schwa_relaxed": report.schwa_relaxed_count if report else 0,
        "schwa_relaxed_rate": round((report.schwa_relaxed_count / n_words) if n_words else 0.0, 4),
        "ambiguity_flags": report.ambiguity_count if report else 0,
        "ambiguity_rate": round((report.ambiguity_count / n_words) if n_words else 0.0, 4),
        "heteronym_flags": report.heteronym_count if report else 0,
        "heteronym_rate": round((report.heteronym_count / n_words) if n_words else 0.0, 4),
        "unalignable": report.unalignable_count if report else 0,
        "unalignable_rate": round((report.unalignable_count / n_words) if n_words else 0.0, 4),
        "unalignable_words": sorted(
            {w.word for w in (report.violations if report else []) if not w.alignable}
        ),
        # --- moderation (§13) ---
        "moderation": (
            {
                "class": moderation_class(mod),
                "approved": mod.approved,
                "layer": mod.layer,
                "reason": mod.reason,
                "well_formed": mod.well_formed,
                "grammar_note": mod.grammar_note,
                "blocked_words": mod.blocked_words,
                "hazard_suspect_heuristic": (
                    hazard_suspect(mod.reason) if moderation_class(mod) == "content" else False
                ),
            }
            if mod is not None
            else {"class": "not_reached"}
        ),
        # --- §14 latency per node (ms) ---
        "latency_ms": {
            "vocabulary": round(result.vocabulary_ms, 2),
            "generating": round(result.attempts[0].latency_ms, 2) if result.attempts else 0.0,
            "rewriting": [round(a.latency_ms, 2) for a in result.attempts[1:]],
            "validating": [round(a.validate_ms, 3) for a in result.attempts],
            "moderating": (
                round(result.moderation_ms, 2) if result.moderation_ms is not None else None
            ),
            "total": round(result.total_ms, 2),
        },
        # --- §14 token cost per node (USD at `on`) ---
        "cost_usd": {
            "generating_and_rewriting": round(gen_cost, 6),
            "moderating": round(mod_cost, 6),
            "total": round(gen_cost + mod_cost, 6),
        },
        "tokens": dict(tok),
        "pricing_date": on.isoformat(),
    }


def _mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _group(items: Sequence[dict], key: str) -> dict:
    out: dict = {}
    for it in items:
        out.setdefault(it[key], []).append(it)
    return out


def aggregate(items: Sequence[dict], *, on: date, cells_planned: int) -> dict:
    """§14's aggregates, plus the distributions the mean would hide."""
    attempted = len(items)
    produced = [i for i in items if i["outcome"] != "api_fail"]
    accepted = [i for i in items if i["outcome"] == "accepted"]
    decs = [i["decodability"] for i in produced]
    depths = [i["rewrite_depth"] for i in produced]
    totals = [i["latency_ms"]["total"] for i in produced]

    gen_latencies = [
        i["latency_ms"]["generating"] for i in produced if i["latency_ms"]["generating"]
    ]
    rw_latencies = [ms for i in produced for ms in i["latency_ms"]["rewriting"]]
    val_latencies = [ms for i in produced for ms in i["latency_ms"]["validating"]]
    mod_latencies = [
        i["latency_ms"]["moderating"] for i in produced if i["latency_ms"]["moderating"]
    ]

    cost_total = sum(i["cost_usd"]["total"] for i in items)
    gen_total = sum(i["cost_usd"]["generating_and_rewriting"] for i in items)
    mod_total = sum(i["cost_usd"]["moderating"] for i in items)

    mod_classes = Counter(i["moderation"]["class"] for i in items)
    content_rejects = [
        {
            "stage_id": i["stage_id"],
            "theme": i["theme"],
            "reason": i["moderation"]["reason"],
            "well_formed": i["moderation"].get("well_formed"),
            "grammar_note": i["moderation"].get("grammar_note", ""),
            "hazard_suspect_heuristic": i["moderation"].get("hazard_suspect_heuristic", False),
            "story_id": i["story_id"],
        }
        for i in items
        if i["moderation"]["class"] == "content"
    ]
    grammar_rejects = [
        {
            "stage_id": i["stage_id"],
            "theme": i["theme"],
            "grammar_note": i["moderation"].get("grammar_note", ""),
            "reason": i["moderation"]["reason"],
            "story_id": i["story_id"],
        }
        for i in items
        if i["moderation"]["class"] == "grammar"
    ]
    blocklist_rejects = [
        {
            "stage_id": i["stage_id"],
            "theme": i["theme"],
            "blocked_words": i["moderation"].get("blocked_words", []),
            "story_id": i["story_id"],
        }
        for i in items
        if i["moderation"]["class"] == "blocklist"
    ]

    def _slice(group: Sequence[dict]) -> dict:
        g_dec = [i["decodability"] for i in group if i["outcome"] != "api_fail"]
        g_acc = [i for i in group if i["outcome"] == "accepted"]
        g_below = [i for i in group if i["outcome"] != "api_fail" and not i["meets_gate"]]
        g_words = [i["word_count"] for i in group if i["outcome"] != "api_fail"]
        return {
            "attempted": len(group),
            "produced": len([i for i in group if i["outcome"] != "api_fail"]),
            "accepted": len(g_acc),
            "gate_pass_rate": (len(g_acc) / len(group)) if group else 0.0,
            "below_gate": len(g_below),
            "decodability": {
                "min": min(g_dec) if g_dec else 0.0,
                "p05": quantile(g_dec, 0.05),
                "p50": quantile(g_dec, 0.50),
                "mean": _mean(g_dec),
                "max": max(g_dec) if g_dec else 0.0,
            },
            "bins": bin_decodability(g_dec),
            "rewrite_depth": {
                "mean": _mean([i["rewrite_depth"] for i in group if i["outcome"] != "api_fail"]),
                "p95": quantile(
                    [i["rewrite_depth"] for i in group if i["outcome"] != "api_fail"], 0.95
                ),
                "hist": dict(
                    sorted(
                        Counter(
                            i["rewrite_depth"] for i in group if i["outcome"] != "api_fail"
                        ).items()
                    )
                ),
            },
            "cost_usd": round(sum(i["cost_usd"]["total"] for i in group), 6),
            "cost_per_accepted_usd": (
                round(sum(i["cost_usd"]["total"] for i in group) / len(g_acc), 6) if g_acc else None
            ),
            "latency_ms": {
                "p50": quantile([i["latency_ms"]["total"] for i in group], 0.50),
                "p95": quantile([i["latency_ms"]["total"] for i in group], 0.95),
            },
            "word_count_mean": round(_mean(g_words), 1),
            "length_compliance_rate": (
                sum(1 for i in group if i["length_compliant"]) / len(group) if group else 0.0
            ),
            "type_token_ratio_mean": round(_mean([i["type_token_ratio"] for i in group]), 4),
            "oov": sum(i["oov"] for i in group),
            "schwa_relaxed_rate": round(_mean([i["schwa_relaxed_rate"] for i in group]), 4),
            "ambiguity_rate": round(_mean([i["ambiguity_rate"] for i in group]), 4),
            "heteronym_rate": round(_mean([i["heteronym_rate"] for i in group]), 4),
            "unalignable_rate": round(_mean([i["unalignable_rate"] for i in group]), 4),
            "unalignable_words": sorted({w for i in group for w in i["unalignable_words"]}),
            "story_words_mean": round(_mean([len(i["distinct_story_words"]) for i in group]), 2),
        }

    below_gate = [i for i in produced if not i["meets_gate"]]
    return {
        "cells_planned": cells_planned,
        # Total time the pipeline spent, summed over the items. Unlike a
        # wall-clock reading this is a pure function of the measurements, so it
        # survives a `--resume`: `wall_clock_s` covers only the invocation that
        # happened to finalise the run, and after a resume that can be seconds.
        "pipeline_seconds": round(sum(i["latency_ms"]["total"] for i in items) / 1000, 1),
        "attempted": attempted,
        "produced": len(produced),
        "accepted": len(accepted),
        "gate_pass_rate": (len(accepted) / attempted) if attempted else 0.0,
        "outcomes": dict(sorted(Counter(i["outcome"] for i in items).items())),
        # §14's "fallback rate": every §7 failure taxonomy member terminates in a
        # warm-pool serve, so this is the rate at which a live request would have
        # fallen back instead of serving fresh text.
        "fallback_rate": (
            sum(1 for i in items if i["outcome"] != "accepted") / attempted if attempted else 0.0
        ),
        "decodability": {
            "min": min(decs) if decs else 0.0,
            "p05": quantile(decs, 0.05),
            "p25": quantile(decs, 0.25),
            "p50": quantile(decs, 0.50),
            "mean": _mean(decs),
            "p95": quantile(decs, 0.95),
            "max": max(decs) if decs else 0.0,
            "bins": bin_decodability(decs),
            "below_gate": len(below_gate),
            "below_gate_detail": [
                {
                    "stage_id": i["stage_id"],
                    "theme": i["theme"],
                    "decodability": i["decodability"],
                    "rewrite_depth": i["rewrite_depth"],
                    "violation_words": i["violation_words"][:12],
                    "missing_gpcs": i["missing_gpcs"][:12],
                    "story_id": i["story_id"],
                }
                for i in below_gate
            ],
        },
        "rewrite_depth": {
            "mean": _mean(depths),
            "p50": quantile(depths, 0.50),
            "p95": quantile(depths, 0.95),
            "max": max(depths) if depths else 0,
            "hist": dict(sorted(Counter(depths).items())),
            "attempts_per_accepted": (
                round(sum(i["attempts"] for i in accepted) / len(accepted), 3) if accepted else None
            ),
        },
        "cost": {
            "pricing_date": on.isoformat(),
            "total_usd": round(cost_total, 6),
            "generation_usd": round(gen_total, 6),
            "moderation_usd": round(mod_total, 6),
            "per_attempted_usd": round(cost_total / attempted, 6) if attempted else 0.0,
            "per_accepted_usd": round(cost_total / len(accepted), 6) if accepted else None,
            "tokens": {
                k: sum(i["tokens"].get(k, 0) for i in items)
                for k in ("input", "output", "cache_read", "cache_write", "mod_input", "mod_output")
            },
        },
        "latency_ms": {
            "total_p50": quantile(totals, 0.50),
            "total_p95": quantile(totals, 0.95),
            "total_max": max(totals) if totals else 0.0,
            "generating_p50": quantile(gen_latencies, 0.50),
            "generating_p95": quantile(gen_latencies, 0.95),
            "rewriting_p50": quantile(rw_latencies, 0.50),
            "rewriting_p95": quantile(rw_latencies, 0.95),
            "validating_p50": quantile(val_latencies, 0.50),
            "validating_p95": quantile(val_latencies, 0.95),
            "moderating_p50": quantile(mod_latencies, 0.50),
            "moderating_p95": quantile(mod_latencies, 0.95),
        },
        "moderation": {
            "classes": dict(sorted(mod_classes.items())),
            "reached": sum(v for k, v in mod_classes.items() if k != "not_reached"),
            "content_rejects": content_rejects,
            "grammar_rejects": grammar_rejects,
            "blocklist_rejects": blocklist_rejects,
            "hazard_suspect_heuristic_count": sum(
                1 for r in content_rejects if r["hazard_suspect_heuristic"]
            ),
        },
        "decoder_limitations": {
            "schwa_relaxed_total": sum(i["schwa_relaxed"] for i in items),
            "schwa_relaxed_rate_mean": round(_mean([i["schwa_relaxed_rate"] for i in produced]), 4),
            "ambiguity_total": sum(i["ambiguity_flags"] for i in items),
            "ambiguity_rate_mean": round(_mean([i["ambiguity_rate"] for i in produced]), 4),
            "heteronym_total": sum(i["heteronym_flags"] for i in items),
            "heteronym_rate_mean": round(_mean([i["heteronym_rate"] for i in produced]), 4),
            "unalignable_total": sum(i["unalignable"] for i in items),
            "unalignable_rate_mean": round(_mean([i["unalignable_rate"] for i in produced]), 4),
            "unalignable_words": dict(
                sorted(
                    Counter(w for i in items for w in i["unalignable_words"]).items(),
                    key=lambda kv: (-kv[1], kv[0]),
                )
            ),
            "non_minimal_missing_total": sum(i["non_minimal_missing"] for i in items),
        },
        "oov": {
            "total": sum(i["oov"] for i in items),
            "stories_with_oov": sum(1 for i in items if i["oov"]),
            "words": dict(
                sorted(
                    Counter(w for i in items for w in i["oov_words"]).items(),
                    key=lambda kv: (-kv[1], kv[0]),
                )
            ),
        },
        "story_words": {
            "cap": MAX_STORY_WORDS,
            "distinct_per_story_hist": dict(
                sorted(Counter(len(i["distinct_story_words"]) for i in items).items())
            ),
            "occurrences_total": sum(i["story_word_occurrences"] for i in items),
            "stories_using_none": sum(1 for i in items if not i["distinct_story_words"]),
            "over_cap": sum(1 for i in items if len(i["distinct_story_words"]) > MAX_STORY_WORDS),
            "by_theme": {
                theme: dict(
                    sorted(
                        Counter(w for i in group for w in i["distinct_story_words"]).items(),
                        key=lambda kv: (-kv[1], kv[0]),
                    )
                )
                for theme, group in sorted(_group(items, "theme").items())
            },
        },
        "length": {
            "compliance_rate": (
                sum(1 for i in items if i["length_compliant"]) / attempted if attempted else 0.0
            ),
            "word_count_p50": quantile([i["word_count"] for i in produced], 0.50),
            "type_token_ratio_mean": round(_mean([i["type_token_ratio"] for i in produced]), 4),
        },
        "by_stage": {str(k): _slice(v) for k, v in sorted(_group(items, "stage_id").items())},
        "by_theme": {k: _slice(v) for k, v in sorted(_group(items, "theme").items())},
    }


def criteria_verdict(agg: dict) -> list[tuple[str, bool, str]]:
    """§1's acceptance criteria as (name, ok, evidence). Drives the exit code.

    Item 2 only. A moderation rejection is a content verdict on a story that met
    the decodability criterion, so it is reported loudly and does NOT set the
    exit code (DEVIATIONS 7.49).
    """
    below = agg["decodability"]["below_gate"]
    p95 = agg["rewrite_depth"]["p95"]
    produced = agg["produced"]
    return [
        (
            f"every story reaches >={DECODABILITY_GATE:.0%} decodability",
            below == 0 and produced > 0,
            f"{produced - below}/{produced} stories at or above the gate ({below} below)",
        ),
        (
            f"p95 rewrite depth <= {REWRITE_DEPTH_P95_TARGET}",
            p95 <= REWRITE_DEPTH_P95_TARGET,
            f"p95 = {p95}, max = {agg['rewrite_depth']['max']}",
        ),
        (
            "the matrix completed",
            agg["attempted"] == agg["cells_planned"],
            f"{agg['attempted']} of {agg['cells_planned']} planned cells attempted",
        ),
        # An api_fail is not a decodability failure, but a cell that never
        # produced text was never measured — and a matrix reported as covering
        # 180 cells while 8 of them are empty is the false green DEVIATIONS 0.5
        # forbids. Separate from the criterion above so the report says WHICH
        # kind of incompleteness happened.
        (
            "every planned cell produced a story",
            agg["produced"] == agg["cells_planned"],
            f"{agg['produced']} of {agg['cells_planned']} cells produced text"
            f" ({agg['outcomes'].get('api_fail', 0)} api_fail)",
        ),
    ]


# --- cost projection ----------------------------------------------------------

#: Characters per token, used ONLY by the pre-flight projection. Anthropic's
#: tokenizer is not local; ~3.7 chars/token is the usual English figure for this
#: kind of text. The projection is an order of magnitude, not a promise — the cap
#: that binds is the running actual (DEVIATIONS 7.44).
_CHARS_PER_TOKEN = 3.7
#: Tokens per story word of OUTPUT, including the JSON scaffolding of
#: `StoryDraft` and the thinking budget Sonnet 5 spends when `thinking` is
#: omitted (STATE invariant 9). Calibrated off the smoke run rather than guessed.
_OUTPUT_TOKENS_PER_TARGET_WORD = 12.0
_OUTPUT_TOKENS_FLOOR = 700.0


def project_cost(vocabs: Sequence[Vocabulary], n: int, on: date) -> dict:
    """Order-of-magnitude spend for a matrix, with no API call.

    One generation attempt per story is assumed, plus one Haiku moderation call;
    rewrites are added as a flat uplift because the whole question the eval
    answers is how often they happen.
    """
    gen_model, mod_model = generation_model(), fast_model()
    gp, mp = price_for(gen_model, on), price_for(mod_model, on)
    total_in = total_out = 0.0
    per_stage: dict[int, float] = {}
    for v in vocabs:
        system_chars = len(SYSTEM_RULES) + len(
            build_vocabulary_block(v.stage_name, v.allowed_words, v.sight_words)
        )
        req_chars = len(build_request_block("dragons", v.story_words, v.target_min, v.target_max))
        in_tok = (system_chars + req_chars) / _CHARS_PER_TOKEN
        out_tok = max(_OUTPUT_TOKENS_FLOOR, v.target_max * _OUTPUT_TOKENS_PER_TARGET_WORD)
        # First call at the stage writes the cache; the rest read it.
        cost = (
            in_tok * gp.input_per_mtok / 1e6 * CACHE_WRITE_MULTIPLIER
            + in_tok * gp.input_per_mtok / 1e6 * CACHE_READ_MULTIPLIER * max(0, n - 1)
            + out_tok * gp.output_per_mtok / 1e6 * n
            # Haiku moderation: the story plus the ~900-token system prompt.
            + n * ((out_tok / 4 + 1200) * mp.input_per_mtok / 1e6 + 120 * mp.output_per_mtok / 1e6)
        )
        per_stage[v.stage_id] = per_stage.get(v.stage_id, 0.0) + cost
        total_in += in_tok * n
        total_out += out_tok * n
    base = sum(per_stage.values())
    return {
        "models": {"generation": gen_model, "moderation": mod_model},
        "pricing_date": on.isoformat(),
        "no_rewrites_usd": round(base, 4),
        # §7 allows 3 rewrites; the smoke gate has historically run at p95 0-1.
        "with_one_rewrite_each_usd": round(base * 2.0, 4),
        "worst_case_all_budget_exhausted_usd": round(base * (MAX_REWRITES + 1), 4),
        "assumed_input_tokens": int(total_in),
        "assumed_output_tokens": int(total_out),
    }


# --- persistence --------------------------------------------------------------


async def _theme_ids(session) -> dict[str, int]:
    return {
        slug: tid
        for tid, slug in (await session.execute(text("SELECT id, slug FROM themes"))).all()
    }


async def persist_eval_story(
    session, result: PipelineResult, theme_id: int, run_id: int, on: date
) -> Story:
    """Write the story at `status='eval'` — never servable (DEVIATIONS 7.41).

    Mirrors `warm.persist` deliberately, including `_split_segments`, so the text
    round-trips exactly as a warm story's does and `warm.story_body` can rebuild
    it. What it does NOT do is set `warm`/`rejected`: an eval story is neither.
    """
    mod = result.moderation
    story = Story(
        stage_id=result.stage_id,
        theme_id=theme_id,
        status=EVAL_STATUS,
        title=result.title[:128],
        decodability=round(result.final_report.decodability, 4) if result.final_report else 0,
        story_words=result.story_words,
        langfuse_trace_id=result.langfuse_trace_id,
        gen_meta={
            "source": "eval",
            "eval_run_id": run_id,
            "passed_gate": result.passed,
            "rewrite_depth": result.rewrite_depth,
            "cost_usd": round(result.total_cost_usd(on), 6),
            "attempts": [
                {
                    "n": a.n,
                    "kind": a.kind,
                    "decodability": a.decodability,
                    "violations": a.violations,
                    "input_tokens": a.usage.total_input_tokens,
                    "output_tokens": a.usage.output_tokens,
                    "latency_ms": round(a.latency_ms, 2),
                }
                for a in result.attempts
            ],
            "moderation": (
                {
                    "approved": mod.approved,
                    "layer": mod.layer,
                    "reason": mod.reason,
                    "well_formed": mod.well_formed,
                    "grammar_note": mod.grammar_note,
                }
                if mod
                else None
            ),
            "schwa_relaxed": result.final_report.schwa_relaxed_count if result.final_report else 0,
            "heteronyms": result.final_report.heteronym_count if result.final_report else 0,
            "unalignable": result.final_report.unalignable_count if result.final_report else 0,
        },
    )
    session.add(story)
    await session.flush()
    for i, body in enumerate(_split_segments(result.final_text)):
        session.add(StorySegment(story_id=story.id, idx=i, body=body))
    return story


async def servable_pool_size(session) -> int:
    """How many stories a child could actually be served, right now."""
    return (
        await session.execute(
            text("SELECT count(*) FROM stories WHERE status = ANY(:s)"),
            {"s": list(SERVABLE_STATUSES)},
        )
    ).scalar_one()


# --- report -------------------------------------------------------------------


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _meets(ok: bool) -> str:
    """Report language for a §1 target. Bolds the failure, never the pass."""
    return "meets it" if ok else "**does not meet it**"


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render_report(run: dict, agg: dict) -> str:
    """The §14 markdown report. Every number comes from `agg`; nothing is prose
    that a reader has to take on trust."""
    cfg = run["config"]
    verdict = criteria_verdict(agg)
    ok = all(v[1] for v in verdict)

    L: list[str] = []
    L.append("# Story Forge — eval report (§14)\n")
    L.append(
        f"Run `{run['id']}` · started `{run['started_at']}` · finished "
        f"`{run.get('finished_at')}` · status **{run['status']}**\n"
    )

    L.append("## Reproducing this run\n")
    L.append(
        "```\n"
        f"cd services/api && python -m storyforge.eval run \\\n"
        f"  --stages {cfg['stages_spec']} --themes {cfg['themes_spec']} "
        f"--n {cfg['n']} --cold \\\n"
        f"  --effort {cfg['effort']} --pricing-date {cfg['pricing_date']} "
        f"--budget {cfg['budget_usd']} --concurrency {cfg['concurrency']}\n"
        "```\n"
    )
    L.append(
        _table(
            ["what", "value"],
            [
                ["generation model", f"`{cfg['models']['generation']}`"],
                ["fast model (moderation)", f"`{cfg['models']['fast']}`"],
                ["pricing date", f"`{cfg['pricing_date']}` (Sonnet 5 intro rate ends 2026-08-31)"],
                [
                    "generation price",
                    f"${cfg['prices']['generation']['input_per_mtok']}/"
                    f"${cfg['prices']['generation']['output_per_mtok']} per Mtok in/out",
                ],
                [
                    "fast price",
                    f"${cfg['prices']['fast']['input_per_mtok']}/"
                    f"${cfg['prices']['fast']['output_per_mtok']} per Mtok in/out",
                ],
                ["stages", ", ".join(str(s) for s in cfg["stages"])],
                ["themes", ", ".join(cfg["themes"])],
                ["stories per (stage, theme)", cfg["n"]],
                ["cells planned", agg["cells_planned"]],
                [
                    "gate",
                    f"{DECODABILITY_GATE:.0%} decodability, 0 OOV, "
                    f"<= {MAX_STORY_WORDS} story words",
                ],
                ["rewrite budget", MAX_REWRITES],
                ["prompt allowlist size", PROMPT_WORD_LIMIT],
                ["effort", cfg["effort"]],
                ["concurrency", cfg["concurrency"]],
                ["cold (cache bypassed)", cfg["cold"]],
                ["enrichment run", cfg["enrich"]],
                ["git commit", f"`{cfg['git_commit']}`"],
                ["python", cfg["python"]],
            ],
        )
        + "\n"
    )

    trail = cfg.get("resumed") or []
    if trail:
        L.append(
            f"**This matrix was gathered in {len(trail) + 1} sitting(s)** "
            f"(DEVIATIONS 7.51). The first invocation was killed by its launcher "
            f"at cell {trail[0]['items_already_recorded']} of "
            f"{agg['cells_planned']}; every item is committed as it completes, so "
            f"`--resume` ran only the remainder against the SAME stored config — "
            f"same model, same gate, same pricing date. Trail: "
            f"`{json.dumps(trail)}`\n"
        )

    L.append("## Does the thesis hold?\n")
    L.append(
        "§0's claim is that a constrained LLM plus a pure-function gate yields "
        "reliably decodable stories. §1 item 2 is the machine-checkable form of "
        "that claim. Verdict on this run:\n"
    )
    L.append(
        _table(
            ["§1 item 2 criterion", "result", "evidence"],
            [[name, "PASS" if good else "**FAIL**", ev] for name, good, ev in verdict],
        )
        + "\n"
    )
    L.append(
        f"**Overall: {'PASS' if ok else 'FAIL'}** — `make eval` exits {0 if ok else 'non-zero'}.\n"
    )

    L.append("## Headline numbers\n")
    d, c, r = agg["decodability"], agg["cost"], agg["rewrite_depth"]
    L.append(
        _table(
            ["metric", "value"],
            [
                ["stories attempted", agg["attempted"]],
                ["stories produced (an API call returned text)", agg["produced"]],
                ["stories ACCEPTED (gate + moderation)", agg["accepted"]],
                ["gate + moderation pass rate", _pct(agg["gate_pass_rate"])],
                ["fallback rate (§7: would have served the warm pool)", _pct(agg["fallback_rate"])],
                ["mean decodability", _pct(d["mean"])],
                ["median decodability", _pct(d["p50"])],
                ["worst story", _pct(d["min"])],
                ["stories below the gate", d["below_gate"]],
                ["rewrite depth p50 / p95 / max", f"{r['p50']} / {r['p95']} / {r['max']}"],
                ["attempts per accepted story", r["attempts_per_accepted"]],
                ["total cost", f"${c['total_usd']:.4f}"],
                [
                    "cost per accepted story",
                    f"${c['per_accepted_usd']:.4f}" if c["per_accepted_usd"] else "n/a",
                ],
                [
                    "cold latency p50 / p95",
                    f"{agg['latency_ms']['total_p50'] / 1000:.1f}s / "
                    f"{agg['latency_ms']['total_p95'] / 1000:.1f}s "
                    f"(§1.6 target p95 < {COLD_LATENCY_P95_TARGET_MS // 1000}s)",
                ],
                [
                    "pipeline time (summed per story)",
                    f"{agg.get('pipeline_seconds', 0) / 60:.0f} min",
                ],
            ],
        )
        + "\n"
    )
    L.append(f"Outcome taxonomy (§7): `{json.dumps(agg['outcomes'])}`\n")

    L.append("## Decodability distribution\n")
    L.append("A mean hides the shape, so here is the shape. All produced stories:\n")
    L.append(
        _table(
            ["min", "p05", "p25", "p50", "mean", "p95", "max"],
            [[_pct(d[k]) for k in ("min", "p05", "p25", "p50", "mean", "p95", "max")]],
        )
        + "\n"
    )
    L.append(
        _table(
            ["bin", "stories"],
            [[label, d["bins"][label]] for label, _, _ in DECODABILITY_BINS],
        )
        + "\n"
    )
    if d["below_gate_detail"]:
        L.append("### Every story below the gate\n")
        L.append(
            _table(
                [
                    "stage",
                    "theme",
                    "decodability",
                    "rewrites",
                    "violating words",
                    "missing GPCs",
                    "story",
                ],
                [
                    [
                        b["stage_id"],
                        b["theme"],
                        _pct(b["decodability"]),
                        b["rewrite_depth"],
                        ", ".join(b["violation_words"]) or "—",
                        ", ".join(b["missing_gpcs"]) or "—",
                        b["story_id"],
                    ]
                    for b in d["below_gate_detail"]
                ],
            )
            + "\n"
        )

    L.append("### Per stage\n")
    L.append(
        _table(
            [
                "stage",
                "n",
                "accepted",
                "pass rate",
                "min",
                "p50",
                "mean",
                "max",
                "below gate",
                "rw p95",
                "words",
                "len ok",
                "TTR",
                "cost/accepted",
            ],
            [
                [
                    s,
                    v["attempted"],
                    v["accepted"],
                    _pct(v["gate_pass_rate"]),
                    _pct(v["decodability"]["min"]),
                    _pct(v["decodability"]["p50"]),
                    _pct(v["decodability"]["mean"]),
                    _pct(v["decodability"]["max"]),
                    v["below_gate"],
                    v["rewrite_depth"]["p95"],
                    v["word_count_mean"],
                    _pct(v["length_compliance_rate"]),
                    f"{v['type_token_ratio_mean']:.2f}",
                    f"${v['cost_per_accepted_usd']:.4f}" if v["cost_per_accepted_usd"] else "n/a",
                ]
                for s, v in agg["by_stage"].items()
            ],
        )
        + "\n"
    )

    L.append("### Per theme\n")
    L.append(
        _table(
            [
                "theme",
                "n",
                "accepted",
                "pass rate",
                "min",
                "p50",
                "mean",
                "max",
                "rw p95",
                "cost/accepted",
            ],
            [
                [
                    t,
                    v["attempted"],
                    v["accepted"],
                    _pct(v["gate_pass_rate"]),
                    _pct(v["decodability"]["min"]),
                    _pct(v["decodability"]["p50"]),
                    _pct(v["decodability"]["mean"]),
                    _pct(v["decodability"]["max"]),
                    v["rewrite_depth"]["p95"],
                    f"${v['cost_per_accepted_usd']:.4f}" if v["cost_per_accepted_usd"] else "n/a",
                ]
                for t, v in agg["by_theme"].items()
            ],
        )
        + "\n"
    )

    L.append("## The rewrite loop\n")
    L.append(
        "How many attempts an accepted story took. Depth 0 means the first draft "
        "passed; the gate was never lowered to get there (§0).\n"
    )
    L.append(_table(["rewrite depth", "stories"], [[k, v] for k, v in r["hist"].items()]) + "\n")
    L.append(
        f"mean {r['mean']:.2f} · p50 {r['p50']} · p95 {r['p95']} · max {r['max']} · "
        f"attempts per accepted story {r['attempts_per_accepted']}\n"
    )

    L.append("## Cost\n")
    tk = c["tokens"]
    L.append(
        _table(
            ["metric", "value"],
            [
                ["pricing date used", f"`{c['pricing_date']}`"],
                ["total", f"${c['total_usd']:.4f}"],
                ["GENERATING + REWRITING (Sonnet)", f"${c['generation_usd']:.4f}"],
                ["MODERATING (Haiku)", f"${c['moderation_usd']:.4f}"],
                ["per attempted story", f"${c['per_attempted_usd']:.4f}"],
                [
                    "per ACCEPTED story",
                    f"${c['per_accepted_usd']:.4f}" if c["per_accepted_usd"] else "n/a",
                ],
                ["Sonnet input tokens (uncached)", f"{tk['input']:,}"],
                ["Sonnet cache-write tokens", f"{tk['cache_write']:,}"],
                ["Sonnet cache-read tokens", f"{tk['cache_read']:,}"],
                ["Sonnet output tokens", f"{tk['output']:,}"],
                ["Haiku input / output tokens", f"{tk['mod_input']:,} / {tk['mod_output']:,}"],
            ],
        )
        + "\n"
    )
    L.append(
        "**What this cost does NOT include.** The harness does not run ENRICHING "
        "(DEVIATIONS 7.45): no QUESTION_GEN, no SCENE_TAGGING, no question "
        "moderation. `docs/STATE.md` records enrichment as 45-48% of a warm "
        "story's total cost, so the cost of a story a child could actually be "
        "served is roughly double the per-accepted figure above.\n"
    )
    if run.get("post_intro_cost_usd") is not None:
        L.append(
            f"Same run priced after the Sonnet 5 introductory rate ends "
            f"(2026-09-01, $3/$15): **${run['post_intro_cost_usd']:.4f}** "
            f"(x{run['post_intro_cost_usd'] / max(c['total_usd'], 1e-9):.2f}).\n"
        )
    if run.get("projection"):
        p = run["projection"]
        L.append(
            f"Pre-flight projection was ${p['no_rewrites_usd']:.2f} with no "
            f"rewrites, ${p['with_one_rewrite_each_usd']:.2f} with one each, "
            f"${p['worst_case_all_budget_exhausted_usd']:.2f} worst case. "
            f"Cap was ${cfg['budget_usd']:.2f}.\n"
        )

    L.append("## Latency per node\n")
    lat = agg["latency_ms"]
    L.append(
        _table(
            ["node", "p50", "p95"],
            [
                [
                    "GENERATING (first attempt)",
                    f"{lat['generating_p50']:.0f} ms",
                    f"{lat['generating_p95']:.0f} ms",
                ],
                [
                    "REWRITING (per rewrite)",
                    f"{lat['rewriting_p50']:.0f} ms",
                    f"{lat['rewriting_p95']:.0f} ms",
                ],
                [
                    "VALIDATING (pure function)",
                    f"{lat['validating_p50']:.2f} ms",
                    f"{lat['validating_p95']:.2f} ms",
                ],
                [
                    "MODERATING (Haiku)",
                    f"{lat['moderating_p50']:.0f} ms",
                    f"{lat['moderating_p95']:.0f} ms",
                ],
                ["whole pipeline", f"{lat['total_p50']:.0f} ms", f"{lat['total_p95']:.0f} ms"],
            ],
        )
        + "\n"
    )
    L.append(
        f"§1 item 6 asks for cold generation p95 < "
        f"{COLD_LATENCY_P95_TARGET_MS / 1000:.0f}s: measured p95 "
        f"{lat['total_p95'] / 1000:.1f}s — "
        f"{_meets(lat['total_p95'] < COLD_LATENCY_P95_TARGET_MS)}. "
        f"Run sequentially (`--concurrency {cfg['concurrency']}`) so this number "
        "is one request's wall time.\n"
    )

    L.append("## Moderation (§13)\n")
    m = agg["moderation"]
    L.append(
        _table(
            ["verdict class", "stories"],
            [[k, v] for k, v in m["classes"].items()],
        )
        + "\n"
    )
    L.append(
        "`blocklist` is §13 layer 1 (deterministic, free, no API call). "
        "`grammar` is DEVIATIONS 4.12's well-formedness half, which `moderate()` "
        "folds into `approved` and marks with a reason prefix. `content` is the "
        "layer-3 safety judgement.\n"
    )
    L.append(
        "**Hazard vs harm cannot be read off the verdict** (STATE invariant 5, "
        "DEVIATIONS 7.46). The verdict has no field for it; the only record is a "
        "sentence the model wrote. So every content rejection's reason is printed "
        "verbatim below, and the `hazard?` column is a keyword HEURISTIC pointing "
        "at reasons worth reading — not a measurement.\n"
    )
    if m["content_rejects"]:
        L.append(
            _table(
                ["stage", "theme", "story", "hazard? (heuristic)", "reason (verbatim)"],
                [
                    [
                        x["stage_id"],
                        x["theme"],
                        x["story_id"],
                        "suspect" if x["hazard_suspect_heuristic"] else "harm-shaped",
                        x["reason"].replace("|", "\\|"),
                    ]
                    for x in m["content_rejects"]
                ],
            )
            + "\n"
        )
    else:
        L.append("No content rejections in this run.\n")
    if m["grammar_rejects"]:
        L.append("### Grammar rejections (§13 + DEVIATIONS 4.12)\n")
        L.append(
            _table(
                ["stage", "theme", "story", "note (verbatim)"],
                [
                    [
                        x["stage_id"],
                        x["theme"],
                        x["story_id"],
                        (x["grammar_note"] or x["reason"]).replace("|", "\\|"),
                    ]
                    for x in m["grammar_rejects"]
                ],
            )
            + "\n"
        )
    else:
        L.append("No grammar rejections in this run.\n")
    if m["blocklist_rejects"]:
        L.append("### Blocklist rejections (layer 1)\n")
        L.append(
            _table(
                ["stage", "theme", "story", "blocked words"],
                [
                    [x["stage_id"], x["theme"], x["story_id"], ", ".join(x["blocked_words"])]
                    for x in m["blocklist_rejects"]
                ],
            )
            + "\n"
        )

    L.append("## The decoder's own disclosed limitations (§5.3, §18)\n")
    dl = agg["decoder_limitations"]
    L.append(
        "§5.3 says these are logged in the eval report, not silent behaviour. "
        'A rising `unalignable` rate is §18\'s "the inventory is too small" '
        "alarm; a rising `heteronym` rate means §5.2.5's any-pronunciation-passes "
        "rule is over-crediting words.\n"
    )
    L.append(
        _table(
            ["flag", "total word instances", "mean rate per story"],
            [
                [
                    "schwa_relaxed (§5.2.6)",
                    dl["schwa_relaxed_total"],
                    _pct(dl["schwa_relaxed_rate_mean"]),
                ],
                ["ambiguity_flag (§5.2.5)", dl["ambiguity_total"], _pct(dl["ambiguity_rate_mean"])],
                ["heteronym_flag", dl["heteronym_total"], _pct(dl["heteronym_rate_mean"])],
                [
                    "alignable=False (unalignable)",
                    dl["unalignable_total"],
                    _pct(dl["unalignable_rate_mean"]),
                ],
                ["non-minimal missing-GPC set", dl["non_minimal_missing_total"], "—"],
            ],
        )
        + "\n"
    )
    if dl["unalignable_words"]:
        L.append(
            "Words the inventory cannot spell at ANY stage (the §18 alarm), with "
            f"occurrences: `{json.dumps(dl['unalignable_words'])}`\n"
        )
    else:
        L.append("No unalignable word appeared in any story — the §18 alarm is quiet.\n")
    L.append("### Per stage\n")
    L.append(
        _table(
            ["stage", "schwa", "ambiguity", "heteronym", "unalignable", "unalignable words"],
            [
                [
                    s,
                    _pct(v["schwa_relaxed_rate"]),
                    _pct(v["ambiguity_rate"]),
                    _pct(v["heteronym_rate"]),
                    _pct(v["unalignable_rate"]),
                    ", ".join(v["unalignable_words"]) or "—",
                ]
                for s, v in agg["by_stage"].items()
            ],
        )
        + "\n"
    )

    L.append("## OOV (§5.3: CMUdict is adult-lexicon; an OOV fail is conservative)\n")
    o = agg["oov"]
    L.append(
        f"{o['total']} OOV word instance(s) across {o['stories_with_oov']} story/stories. "
        f"The gate requires ZERO OOV, so any OOV story is a story that had to be "
        f"rewritten or rejected.\n"
    )
    if o["words"]:
        L.append(f"OOV words with occurrences: `{json.dumps(o['words'])}`\n")

    L.append("## Story words (§4, §5.5)\n")
    sw = agg["story_words"]
    L.append(
        f"Cap is {sw['cap']} DISTINCT words per story (STATE invariant 3: "
        f"repetition is correct decodable-reader behaviour, so occurrences are "
        f"not capped). Over cap: **{sw['over_cap']}**. Stories using none: "
        f"{sw['stories_using_none']}. Total occurrences: {sw['occurrences_total']}.\n"
    )
    L.append(
        _table(
            ["distinct story words in a story", "stories"],
            [[k, v] for k, v in sw["distinct_per_story_hist"].items()],
        )
        + "\n"
    )
    for theme, used in sw["by_theme"].items():
        L.append(f"- `{theme}`: {json.dumps(used) if used else 'none used'}")
    L.append("")

    L.append("## Length compliance and register\n")
    L.append(
        _table(
            ["metric", "value"],
            [
                ["within the stage's target word range", _pct(agg["length"]["compliance_rate"])],
                ["median word count", agg["length"]["word_count_p50"]],
                ["mean type-token ratio", f"{agg['length']['type_token_ratio_mean']:.3f}"],
            ],
        )
        + "\n"
    )
    L.append(
        "§18 predicts a low type-token ratio at the early stages and calls it "
        "authentic decodable-reader register. It is reported, not treated as a "
        "defect.\n"
    )

    L.append("## What this run wrote to the database\n")
    L.append(
        f"- `eval_runs`: 1 row (id {run['id']}, status `{run['status']}`).\n"
        f"- `eval_run_items`: {agg['attempted']} rows, one per attempted story.\n"
        f"- `stories`: {run.get('stories_written', 0)} rows at "
        f"`status='{EVAL_STATUS}'` plus their `story_segments`. "
        f"**None of them is servable**: `api/stories.py::SERVABLE_STATUSES` is "
        f"`{list(SERVABLE_STATUSES)}` and does not name `{EVAL_STATUS}`, so "
        f"neither `_SERVE_SQL` nor `GET /api/stories/{{id}}` can reach one "
        f"(DEVIATIONS 7.41).\n"
        f"- Servable pool size before: {run.get('servable_before')}; "
        f"after: {run.get('servable_after')}.\n"
        f"- No `questions`, `readings`, `answers` or `gpc_stats` row was touched.\n"
    )
    return "\n".join(L) + "\n"


# --- orchestration ------------------------------------------------------------


def _cells(stages: Sequence[int], themes: Sequence[str], n: int) -> Iterator[tuple[int, str, int]]:
    """Stage-major so the per-stage prompt cache is actually reused (STATE 10)."""
    for stage in stages:
        for theme in themes:
            for i in range(n):
                yield stage, theme, i


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — provenance is nice to have, not load-bearing
        return "unknown"


def repo_root() -> Path:
    """The checkout root. Same derivation `seed.py`/`precompute.py` use (7.2)."""
    return Path(__file__).resolve().parents[3]


async def run_full(
    *,
    stages_spec: str,
    themes_spec: str,
    n: int,
    effort: str,
    cold: bool,
    budget_usd: float,
    concurrency: int,
    pricing_date: date,
    estimate_only: bool,
    enrich: bool,
    out_dir: Path | None,
    resume_run_id: int | None = None,
) -> int:
    """§14's full run. Returns the process exit code.

    `resume_run_id` continues an `eval_runs` row that never finished. Every item
    is committed as it completes (DEVIATIONS 7.47), so a 70-minute matrix killed
    at cell 154 still holds 154 real measurements — and the same argument that
    says an `api_fail` must not throw away 179 other cells says a SIGTERM must
    not either. The stored `config` wins over the command line on a resume, so
    the two halves of the matrix cannot be measured against different models, a
    different gate or a different pricing date.
    """
    stages = parse_stages(stages_spec)
    started = datetime.now(UTC)
    resumed_items: list[dict] = []
    resumed_config: dict | None = None

    async with SessionLocal() as session:
        if resume_run_id is not None:
            prior = await session.get(EvalRun, resume_run_id)
            if prior is None:
                raise SystemExit(f"no eval_runs row with id {resume_run_id}")
            resumed_config = dict(prior.config or {})
            resumed_items = [
                row.metrics
                for row in (
                    await session.execute(
                        select(EvalRunItem)
                        .where(EvalRunItem.run_id == resume_run_id)
                        .order_by(EvalRunItem.id)
                    )
                )
                .scalars()
                .all()
            ]
            started = prior.started_at
            stages_spec = resumed_config.get("stages_spec", stages_spec)
            themes_spec = resumed_config.get("themes_spec", themes_spec)
            n = int(resumed_config.get("n", n))
            effort = resumed_config.get("effort", effort)
            stages = parse_stages(stages_spec)
            pricing_date = date.fromisoformat(
                resumed_config.get("pricing_date", pricing_date.isoformat())
            )
        theme_ids = await _theme_ids(session)
        themes = (
            sorted(theme_ids)
            if themes_spec.strip() == "all"
            else [t.strip() for t in themes_spec.split(",") if t.strip()]
        )
        unknown = [t for t in themes if t not in theme_ids]
        if unknown:
            raise SystemExit(f"unknown theme(s) {unknown} — run `make seed`")

        vocabs = [await load_vocabulary(session, s, themes[0]) for s in stages]
        projection = project_cost(vocabs, n * len(themes), pricing_date)
        cells_planned = len(stages) * len(themes) * n
        servable_before = await servable_pool_size(session)

    gen_model, fast = generation_model(), fast_model()
    gp, fp = price_for(gen_model, pricing_date), price_for(fast, pricing_date)
    config = {
        "stages_spec": stages_spec,
        "themes_spec": themes_spec,
        "stages": stages,
        "themes": themes,
        "n": n,
        "effort": effort,
        "cold": cold,
        "enrich": enrich,
        "concurrency": concurrency,
        "budget_usd": budget_usd,
        "pricing_date": pricing_date.isoformat(),
        "models": {"generation": gen_model, "fast": fast},
        "prices": {
            "generation": gp.model_dump(),
            "fast": fp.model_dump(),
        },
        "gate": DECODABILITY_GATE,
        "max_rewrites": MAX_REWRITES,
        "max_story_words": MAX_STORY_WORDS,
        "prompt_word_limit": PROMPT_WORD_LIMIT,
        "cells_planned": cells_planned,
        "git_commit": _git_commit(),
        "python": platform.python_version(),
        "projection_usd": projection,
    }
    if resumed_config is not None:
        # The stored config is authoritative — the two halves of one matrix must
        # not be measured against different models or a different gate. Only the
        # resume trail is added.
        trail = list(resumed_config.get("resumed", []))
        trail.append(
            {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "items_already_recorded": len(resumed_items),
                "git_commit": _git_commit(),
            }
        )
        config = {**resumed_config, "resumed": trail}

    print(
        f"\n§14 eval run — {len(stages)} stage(s) x {len(themes)} theme(s) x {n} = "
        f"{cells_planned} stories"
    )
    print(f"  models      {gen_model} (generation) / {fast} (moderation)")
    print(
        f"  pricing     {pricing_date.isoformat()} — "
        f"${gp.input_per_mtok}/${gp.output_per_mtok} per Mtok in/out"
    )
    print(
        f"  projection  ${projection['no_rewrites_usd']:.2f} no rewrites · "
        f"${projection['with_one_rewrite_each_usd']:.2f} one rewrite each · "
        f"${projection['worst_case_all_budget_exhausted_usd']:.2f} worst case"
    )
    print(f"  spend cap   ${budget_usd:.2f} (checked against the RUNNING ACTUAL)")

    if projection["with_one_rewrite_each_usd"] > budget_usd:
        print(
            f"\nSTOP: projected spend ${projection['with_one_rewrite_each_usd']:.2f} "
            f"exceeds the ${budget_usd:.2f} cap before a single API call. "
            "Shrink the matrix or raise --budget deliberately.",
            file=sys.stderr,
        )
        return 2
    if estimate_only:
        print("\n--estimate-only: no API calls made.")
        print(json.dumps(projection, indent=2))
        return 0

    items: list[dict] = []
    stories_written = 0
    running_cost = 0.0
    budget_stopped = False
    fatal: str | None = None
    sem = asyncio.Semaphore(max(1, concurrency))

    async with SessionLocal() as session:
        if resume_run_id is not None:
            run = await session.get(EvalRun, resume_run_id)
            run.config = config
            run.status = "running"
            run.finished_at = None
            run_id = resume_run_id
            items.extend(resumed_items)
            stories_written = sum(1 for it in items if it.get("story_id"))
            running_cost = sum(it["cost_usd"]["total"] for it in items)
            await session.commit()
            print(
                f"  RESUMING eval_runs.id = {run_id} — {len(items)} of "
                f"{cells_planned} cells already recorded, "
                f"${running_cost:.4f} already spent\n"
            )
        else:
            run = EvalRun(config=config, metrics={}, status="running")
            session.add(run)
            await session.flush()
            run_id = run.id
            await session.commit()
            print(f"  eval_runs.id = {run_id}\n")

        # Cells already recorded, counted per (stage, theme) so the remainder is
        # derived from the DATA rather than from an assumption about ordering.
        done = Counter((it["stage_id"], it["theme"]) for it in items)

        async def one(stage: int, theme: str, i: int) -> None:
            async with sem:
                await _attempt(
                    session=session,
                    stage=stage,
                    theme=theme,
                    index=i,
                    theme_ids=theme_ids,
                    run_id=run_id,
                    effort=effort,
                    pricing_date=pricing_date,
                    items=items,
                )

        t0 = time.perf_counter()
        try:
            if concurrency <= 1:
                for stage, theme, i in _cells(stages, themes, n):
                    if done[(stage, theme)] > 0:
                        done[(stage, theme)] -= 1
                        continue
                    await one(stage, theme, i)
                    stories_written += 1 if items and items[-1]["story_id"] else 0
                    running_cost = sum(it["cost_usd"]["total"] for it in items)
                    it = items[-1]
                    print(
                        f"  [{len(items):>3}/{cells_planned}] stage {stage:>2} "
                        f"{theme:<8} {it['outcome']:<18} "
                        f"{it['decodability']:>6.1%} rw={it['rewrite_depth']} "
                        f"{it['latency_ms']['total'] / 1000:>5.1f}s "
                        f"${it['cost_usd']['total']:.4f} "
                        f"| running ${running_cost:.4f}",
                        flush=True,
                    )
                    if running_cost > budget_usd:
                        budget_stopped = True
                        print(
                            f"\nBUDGET STOP: ${running_cost:.4f} spent exceeds the "
                            f"${budget_usd:.2f} cap after {len(items)} of "
                            f"{cells_planned} stories. Reporting what completed.",
                            file=sys.stderr,
                        )
                        break
            else:
                # Batched by stage so the cache-warming argument still holds and
                # the budget is checked between batches rather than never.
                for stage in stages:
                    batch = []
                    for t in themes:
                        for i in range(n):
                            if done[(stage, t)] > 0:
                                done[(stage, t)] -= 1
                                continue
                            batch.append((stage, t, i))
                    if not batch:
                        continue
                    await asyncio.gather(*(one(s, t, i) for s, t, i in batch))
                    running_cost = sum(it["cost_usd"]["total"] for it in items)
                    stories_written = sum(1 for it in items if it["story_id"])
                    print(
                        f"  stage {stage} done — {len(items)}/{cells_planned} "
                        f"stories, running ${running_cost:.4f}",
                        flush=True,
                    )
                    if running_cost > budget_usd:
                        budget_stopped = True
                        print(
                            f"\nBUDGET STOP: ${running_cost:.4f} exceeds the "
                            f"${budget_usd:.2f} cap after {len(items)} stories.",
                            file=sys.stderr,
                        )
                        break
        except BaseException as exc:  # noqa: BLE001 — the report is the deliverable
            fatal = f"{type(exc).__name__}: {exc}"
            print(f"\nRUN ABORTED: {fatal}", file=sys.stderr)

        stories_written = sum(1 for it in items if it["story_id"])
        wall_s = time.perf_counter() - t0
        agg = aggregate(items, on=pricing_date, cells_planned=cells_planned)
        agg["wall_clock_s"] = round(wall_s, 1)

        status = "budget_stopped" if budget_stopped else ("failed" if fatal else "completed")
        servable_after = await servable_pool_size(session)
        finished = datetime.now(UTC)
        db_run = await session.get(EvalRun, run_id)
        db_run.metrics = agg
        db_run.status = status
        db_run.finished_at = finished
        await session.commit()

    # The same token counts re-priced after the Sonnet 5 intro rate expires.
    post_intro = _reprice(items, date(2026, 9, 1))

    run_meta = {
        "id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "status": status,
        "config": config,
        "projection": projection,
        "stories_written": stories_written,
        "servable_before": servable_before,
        "servable_after": servable_after,
        "post_intro_cost_usd": post_intro,
        "fatal": fatal,
    }

    md = render_report(run_meta, agg)
    out = out_dir or (repo_root() / "reports")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"eval-{started.strftime('%Y%m%dT%H%M%SZ')}.md"
    path.write_text(md, encoding="utf-8")
    json_path = path.with_suffix(".json")
    json_path.write_text(
        json.dumps({"run": run_meta, "metrics": agg, "items": items}, indent=2, default=str),
        encoding="utf-8",
    )

    print(_console_summary(agg, run_meta))
    print(f"report: {path}")
    print(f"raw:    {json_path}")

    verdict = criteria_verdict(agg)
    if fatal:
        print(f"\nmake eval FAILED: run aborted ({fatal})", file=sys.stderr)
        return 1
    if budget_stopped:
        print("\nmake eval FAILED: spend cap reached, matrix incomplete", file=sys.stderr)
        return 2
    failures = [f"{name}: {ev}" for name, good, ev in verdict if not good]
    if failures:
        print("\nmake eval FAILED — §1 item 2 not met:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("\nmake eval PASSED — §1 item 2 met across the whole matrix.")
    return 0


def _reprice(items: Sequence[dict], on: date) -> float:
    """Total cost of the same token counts on another date.

    Exists because `price_for` takes an explicit date precisely so a report can
    say "and here is what this run costs in September". Recomputed from the token
    counts on the items rather than from a stored dollar figure.
    """
    gen, fast = generation_model(), fast_model()
    try:
        gp, fp = price_for(gen, on), price_for(fast, on)
    except KeyError:
        return 0.0
    total = 0.0
    for it in items:
        t = it["tokens"]
        total += (
            t.get("input", 0) * gp.input_per_mtok / 1e6
            + t.get("cache_read", 0) * gp.input_per_mtok / 1e6 * CACHE_READ_MULTIPLIER
            + t.get("cache_write", 0) * gp.input_per_mtok / 1e6 * CACHE_WRITE_MULTIPLIER
            + t.get("output", 0) * gp.output_per_mtok / 1e6
            + t.get("mod_input", 0) * fp.input_per_mtok / 1e6
            + t.get("mod_output", 0) * fp.output_per_mtok / 1e6
        )
    return round(total, 6)


async def _attempt(
    *,
    session,
    stage: int,
    theme: str,
    index: int,
    theme_ids: dict[str, int],
    run_id: int,
    effort: str,
    pricing_date: date,
    items: list[dict],
) -> None:
    """One cell of the matrix. Never raises (DEVIATIONS 7.47).

    §7's failure taxonomy is `validation_fail | api_fail | moderation_fail |
    budget_exhausted | timeout`, and every one of them is a MEASUREMENT here. A
    matrix that dies at cell 140 of 180 because one HTTP call timed out throws
    away the evidence for 139 cells, and the fallback rate §14 asks for
    aggregates exactly these events.
    """
    vocab = await load_vocabulary(session, stage, theme)
    t0 = time.perf_counter()
    try:
        result = await run_pipeline(session, stage, theme, effort=effort)
    except BaseException as exc:  # noqa: BLE001 — an api_fail is a datum
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        elapsed = (time.perf_counter() - t0) * 1000
        result = PipelineResult(stage_id=stage, theme=theme, passed=False)
        result.total_ms = elapsed
        item = story_metrics(
            result, vocab, index=index, outcome="api_fail", on=pricing_date, story_id=None
        )
        item["api_error"] = f"{type(exc).__name__}: {exc}"
        items.append(item)
        session.add(EvalRunItem(run_id=run_id, story_id=None, metrics=item))
        await session.commit()
        return

    if result.passed:
        outcome = "accepted"
    elif result.moderation is not None and not result.moderation.approved:
        outcome = "moderation_fail"
    else:
        outcome = "budget_exhausted"

    story = await persist_eval_story(session, result, theme_ids[theme], run_id, pricing_date)
    item = story_metrics(
        result, vocab, index=index, outcome=outcome, on=pricing_date, story_id=story.id
    )
    items.append(item)
    session.add(EvalRunItem(run_id=run_id, story_id=story.id, metrics=item))
    await session.commit()


def _console_summary(agg: dict, run: dict) -> str:
    d, r, c = agg["decodability"], agg["rewrite_depth"], agg["cost"]
    lines = [
        "",
        "=" * 72,
        f"  matrix           {agg['attempted']}/{agg['cells_planned']} stories, "
        f"{agg['wall_clock_s']:.0f}s wall clock",
        f"  accepted         {agg['accepted']}/{agg['attempted']} "
        f"({agg['gate_pass_rate']:.1%})   fallback rate {agg['fallback_rate']:.1%}",
        f"  decodability     min {d['min']:.1%} · p05 {d['p05']:.1%} · "
        f"p50 {d['p50']:.1%} · mean {d['mean']:.1%} · max {d['max']:.1%}",
        f"  below gate       {d['below_gate']}  (gate {DECODABILITY_GATE:.0%})",
        f"  bins             {json.dumps(d['bins'])}",
        f"  rewrite depth    p50 {r['p50']} · p95 {r['p95']} · max {r['max']} · "
        f"hist {json.dumps(r['hist'])}",
        f"  moderation       {json.dumps(agg['moderation']['classes'])}",
        f"  decoder flags    schwa {agg['decoder_limitations']['schwa_relaxed_rate_mean']:.1%} · "
        f"ambig {agg['decoder_limitations']['ambiguity_rate_mean']:.1%} · "
        f"heteronym {agg['decoder_limitations']['heteronym_rate_mean']:.1%} · "
        f"unalignable {agg['decoder_limitations']['unalignable_rate_mean']:.1%}",
        f"  oov              {agg['oov']['total']} instance(s) in "
        f"{agg['oov']['stories_with_oov']} story/stories",
        f"  latency          p50 {agg['latency_ms']['total_p50'] / 1000:.1f}s · "
        f"p95 {agg['latency_ms']['total_p95'] / 1000:.1f}s "
        f"(§1.6 target < {COLD_LATENCY_P95_TARGET_MS // 1000}s)",
        f"  cost             ${c['total_usd']:.4f} total · "
        f"${c['per_accepted_usd'] or 0:.4f}/accepted story"
        f"  (excludes ENRICHING — 7.45)",
        f"  wrote            {run['stories_written']} stories at status='{EVAL_STATUS}' "
        f"(NOT servable); servable pool {run['servable_before']} -> "
        f"{run['servable_after']}",
        "=" * 72,
    ]
    return "\n".join(lines)


# --- smoke (unchanged behaviour, shared metrics) ------------------------------


def _fmt(results: list[PipelineResult], today: date) -> str:
    lines = [
        "",
        f"{'stage':>5} {'theme':<9} {'pass':>5} {'decodability':>13} {'rewrites':>9} {'cost':>9}",
        "-" * 56,
    ]
    for r in results:
        lines.append(
            f"{r.stage_id:>5} {r.theme:<9} {'YES' if r.passed else 'no':>5} "
            f"{r.final_report.decodability:>12.1%} {r.rewrite_depth:>9} "
            f"${r.total_cost_usd(today):>8.4f}"
        )

    decs = [r.final_report.decodability for r in results]
    depths = sorted(r.rewrite_depth for r in results)
    passed = sum(1 for r in results if r.passed)
    p95 = depths[min(len(depths) - 1, int(len(depths) * 0.95))]

    lines += [
        "-" * 56,
        f"  passed          {passed}/{len(results)}",
        f"  mean decodability {statistics.mean(decs):.1%}  (gate {DECODABILITY_GATE:.0%})",
        f"  p95 rewrite depth {p95}  (§1 target <= 2)",
        f"  total cost      ${sum(r.total_cost_usd(today) for r in results):.4f}",
        "",
    ]
    return "\n".join(lines)


async def run_smoke(stages: list[int], themes: list[str], n: int, effort: str):
    today = date.today()
    results: list[PipelineResult] = []
    async with SessionLocal() as session:
        for stage in stages:
            for theme in themes:
                for i in range(n):
                    print(f"  stage {stage} / {theme} / story {i + 1}...", flush=True)
                    r = await run_pipeline(session, stage, theme, effort=effort)
                    results.append(r)
                    mark = "PASS" if r.passed else "FAIL"
                    print(
                        f"    {mark} {r.final_report.decodability:.1%} "
                        f"after {r.rewrite_depth} rewrite(s)",
                        flush=True,
                    )
                    if not r.passed:
                        print(f"    violations: {r.attempts[-1].violations[:8]}", flush=True)

    print(_fmt(results, today))
    for r in results:
        if r.passed:
            print(f"--- stage {r.stage_id} / {r.theme} ---\n{r.final_text}\n")
            break

    # §14: the smoke gate must fail loudly in CI, not just print a table.
    failures = [r for r in results if not r.passed]
    if failures:
        raise SystemExit(f"eval-smoke FAILED: {len(failures)}/{len(results)} below gate")
    print("eval-smoke PASSED")


def main() -> None:
    p = argparse.ArgumentParser(prog="storyforge.eval")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("smoke", help="§14 regression gate: 2 stages x 1 theme x 2 stories")
    s.add_argument("--stages", default="3,6")
    s.add_argument("--themes", default="dragons")
    s.add_argument("-n", type=int, default=2)
    s.add_argument("--effort", default="medium")

    f = sub.add_parser("run", help="§14 full run: writes eval_runs + a markdown report")
    f.add_argument("--stages", default=DEFAULT_STAGES, help="'1..12', '3,6', '1..4,9..12'")
    f.add_argument("--themes", default=DEFAULT_THEMES, help="'all' or comma-separated slugs")
    f.add_argument("-n", "--n", type=int, default=DEFAULT_N, dest="n")
    f.add_argument("--effort", default="medium")
    f.add_argument(
        "--cold",
        action="store_true",
        default=True,
        help=(
            "§14's cache-bypass flag. This harness is UNCONDITIONALLY cold — §7's "
            "CACHE_CHECK lives in api/stories.py and is never called here — so the "
            "flag is accepted for spec fidelity and recorded in eval_runs.config "
            "(DEVIATIONS 7.43)."
        ),
    )
    f.add_argument(
        "--budget",
        type=float,
        default=DEFAULT_BUDGET_USD,
        help="hard spend cap in USD, checked against the RUNNING ACTUAL "
        f"(default {DEFAULT_BUDGET_USD})",
    )
    f.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="stories in flight at once. 1 (default) keeps the latency numbers answering §1.6",
    )
    f.add_argument(
        "--pricing-date",
        default=None,
        help="YYYY-MM-DD for pricing.price_for; defaults to today. "
        "Sonnet 5's intro rate ends 2026-08-31",
    )
    f.add_argument("--estimate-only", action="store_true", help="project the spend and exit")
    f.add_argument(
        "--resume",
        type=int,
        default=None,
        metavar="RUN_ID",
        help=(
            "continue an eval_runs row that never finished. Every item is "
            "committed as it completes, so a matrix killed mid-flight keeps its "
            "measurements; the STORED config wins over these flags"
        ),
    )
    f.add_argument("--out", default=None, help="directory for the report (default <repo>/reports)")

    a = p.parse_args()
    try:
        if a.cmd == "smoke":
            asyncio.run(
                run_smoke(
                    [int(x) for x in a.stages.split(",")],
                    a.themes.split(","),
                    a.n,
                    a.effort,
                )
            )
            return
        on = date.fromisoformat(a.pricing_date) if a.pricing_date else date.today()
        code = asyncio.run(
            run_full(
                stages_spec=a.stages,
                themes_spec=a.themes,
                n=a.n,
                effort=a.effort,
                cold=a.cold,
                budget_usd=a.budget,
                concurrency=a.concurrency,
                pricing_date=on,
                estimate_only=a.estimate_only,
                # ENRICHING is deliberately not run (DEVIATIONS 7.45).
                enrich=False,
                out_dir=Path(a.out) if a.out else None,
                resume_run_id=a.resume,
            )
        )
        if code:
            raise SystemExit(code)
    finally:
        # §15: Langfuse batches exports on a background thread. A CLI that runs
        # for seconds can exit with every span still queued.
        obs.flush()


if __name__ == "__main__":
    main()
