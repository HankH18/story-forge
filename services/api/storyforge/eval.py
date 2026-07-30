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

**Provenance.** A number is evidence only if a later reader can tell what produced
it, and the Phase 7 audit found this harness could not say (F1, F2, F3). So every
item records the model each API call was made with, the price sheet applied, and
the per-call timeout in force; `eval_runs.config` records the vocabulary
fingerprint, a dirty-tree flag and a content hash of the code that ran; and
`--resume` verifies the values it cannot restore and refuses rather than claiming
to enforce them (DEVIATIONS 8.30-8.39). Exit codes: 0 pass, 1 criteria not met or
run aborted, 2 spend cap, 3 a resume refused on config drift.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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
from storyforge.decoder.resources import load_gpcs, load_lexicon
from storyforge.llm.client import SDK_MAX_RETRIES, fast_model, generation_model, generation_timeout
from storyforge.llm.generate import (
    MAX_OUTPUT_TOKENS,
    SYSTEM_RULES,
    build_request_block,
    build_vocabulary_block,
)
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
    requested_models: dict[str, str] | None = None,
    per_call_timeout_s: float | None = None,
    sdk_max_retries: int | None = None,
) -> dict:
    """§14's per-story metric row. Pure given a finished `PipelineResult`.

    `requested_models`, `per_call_timeout_s` and `sdk_max_retries` are properties
    of THIS measurement rather than of the report, and they are recorded per item
    for the same reason `gate` and `pricing_date` already were (DEVIATIONS 8.30,
    8.35): a claim about the whole matrix that cannot be checked row by row is a
    claim a later reader has to take on trust. Optional only so a caller with no
    live client can still build a row; every production call site passes them.
    """
    report = result.final_report
    words = word_tokens(report.words) if report else []
    counts = dict(report.counts) if report else {}
    n_words = len(words)
    # §5.1's denominator: sight and story words are classified before any
    # alignment is attempted, so they can carry none of the flags below. Dividing
    # by every word token halved §18's designated alarm (audit F6, DEVIATIONS
    # 8.34), so the flag rates divide by the population that can carry a flag —
    # which is the decodability denominator, spelled out in the field name.
    scored = report.scored_word_count if report else 0

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
        # Rates are over SCORED words (DEVIATIONS 8.34). The suffix is the
        # denominator: a column header that does not name one is how audit F6
        # went unnoticed through a whole report.
        "flag_rate_denominator": "scored_word_count",
        "schwa_relaxed": report.schwa_relaxed_count if report else 0,
        "schwa_relaxed_rate_scored": round(
            (report.schwa_relaxed_count / scored) if scored else 0.0, 4
        ),
        "ambiguity_flags": report.ambiguity_count if report else 0,
        "ambiguity_rate_scored": round((report.ambiguity_count / scored) if scored else 0.0, 4),
        "heteronym_flags": report.heteronym_count if report else 0,
        "heteronym_rate_scored": round((report.heteronym_count / scored) if scored else 0.0, 4),
        "unalignable": report.unalignable_count if report else 0,
        "unalignable_rate_scored": round(
            (report.unalignable_count / scored) if scored else 0.0, 4
        ),
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
        # --- what produced this row (DEVIATIONS 8.30) ---
        # `generation`/`moderation` are read off each call's own `Usage`, so they
        # are what the API was CALLED with rather than what the environment says
        # at report time — which is the difference audit F1 turned on. An
        # `api_fail` has no response to read, hence `requested` beside them.
        "models": {
            "generation": sorted({a.usage.model for a in result.attempts}),
            "moderation": (
                [mod.usage.model] if mod is not None and mod.usage is not None else []
            ),
            "requested": dict(requested_models) if requested_models else None,
        },
        "prices": {
            m: price_for(m, on).model_dump()
            for m in sorted(
                {a.usage.model for a in result.attempts}
                | ({mod.usage.model} if mod is not None and mod.usage is not None else set())
            )
        },
        # The per-HTTP-attempt budget in force for THIS measurement, not the
        # constant in force when the report is rendered (DEVIATIONS 8.35). Without
        # it, a wall-clock latency cannot be separated from a retry after the fact.
        "per_call_timeout_s": per_call_timeout_s,
        "sdk_max_retries": sdk_max_retries,
    }


def _mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _group(items: Sequence[dict], key: str) -> dict:
    out: dict = {}
    for it in items:
        out.setdefault(it[key], []).append(it)
    return out


def _model_census(items: Sequence[dict], role: str) -> dict:
    """`{model: item count}` for one role, counted from the items (DEVIATIONS 8.30).

    More than one key means the matrix is MIXED, which the report has to say out
    loud — one model name printed over a mixed matrix is audit F1's report.
    """
    return dict(
        sorted(Counter(m for i in items for m in (i.get("models") or {}).get(role) or []).items())
    )


#: The four §5.3/§18 flags, as (report label, item count field).
_FLAGS: tuple[tuple[str, str], ...] = (
    ("schwa_relaxed", "schwa_relaxed"),
    ("ambiguity", "ambiguity_flags"),
    ("heteronym", "heteronym_flags"),
    ("unalignable", "unalignable"),
)


def decoder_limitation_rates(items: Sequence[dict]) -> dict:
    """§5.3/§18's flag rates, over the population that can carry a flag.

    Derived from the recorded COUNTS rather than from the recorded rate fields
    (DEVIATIONS 8.34), which has two consequences worth stating. It reproduces the
    corrected figures for items written before the rename, so run 2's report can be
    re-derived without touching run 2's rows. And it reports the POPULATION rate —
    total flags over total scored words — beside the mean of per-story rates,
    because a mean weights a 30-word stage-1 story equally with a 200-word
    stage-12 one. That is defensible for "the typical story" and wrong for "how
    often does this happen", which is the question §18 asks.

    Pure. `items` are metric rows; nothing here reads a clock, a model or the DB.
    """
    produced = [i for i in items if i["outcome"] != "api_fail"]
    scored_total = sum(i.get("scored_word_count", 0) for i in produced)
    token_total = sum(i.get("word_count", 0) for i in produced)
    out: dict = {
        "denominator": "scored_word_count",
        "scored_word_total": scored_total,
        "word_token_total": token_total,
        "non_minimal_missing_total": sum(i.get("non_minimal_missing", 0) for i in items),
        "unalignable_words": dict(
            sorted(
                Counter(w for i in items for w in i.get("unalignable_words", [])).items(),
                key=lambda kv: (-kv[1], kv[0]),
            )
        ),
    }
    for label, field in _FLAGS:
        total = sum(i.get(field, 0) for i in items)
        per_story = [
            (i.get(field, 0) / i["scored_word_count"])
            for i in produced
            if i.get("scored_word_count")
        ]
        out[f"{label}_total"] = total
        out[f"{label}_rate_scored"] = round(total / scored_total, 6) if scored_total else 0.0
        out[f"{label}_rate_scored_mean"] = round(_mean(per_story), 6)
    return out


def generation_latency_split(
    items: Sequence[dict], *, fallback_timeout_s: float | None = None
) -> dict:
    """GENERATING latency as wall clock AND as the subset with no clock-driven retry.

    Audit F7: `Attempt.latency_ms` is wall time around one SDK call, and that call
    carries `SDK_MAX_RETRIES`, so a single row could be three HTTP attempts — 23 of
    run 2's 169 stories were, and the p95 the report quoted was roughly half dead
    time from the same timeout it separately filed as the availability defect.

    The split uses each item's OWN recorded `per_call_timeout_s`, so a report
    rendered today cannot re-judge a measurement taken under a different budget.
    `fallback_timeout_s` covers items written before that field existed and is
    reported as a supplied value, never as a recorded one.

    The subset is `no_clock_retry`, deliberately not `single_attempt`: a 429 or a
    5xx retried quickly still hides inside a fast call, so this is an upper bound
    on retry-free latency, not a proof of one attempt.
    """
    produced = [i for i in items if i["outcome"] != "api_fail"]
    wall = [i["latency_ms"]["generating"] for i in produced if i["latency_ms"]["generating"]]
    budgets = {i.get("per_call_timeout_s") for i in produced}
    recorded = {b for b in budgets if b}
    source = "recorded" if recorded else ("supplied" if fallback_timeout_s else "unknown")

    clean: list[float] = []
    over: list[float] = []
    unknown = 0
    for i in produced:
        ms = i["latency_ms"]["generating"]
        if not ms:
            continue
        budget = i.get("per_call_timeout_s") or fallback_timeout_s
        if not budget:
            unknown += 1
            continue
        (over if ms > budget * 1000 else clean).append(ms)

    retries = {i.get("sdk_max_retries") for i in produced if i.get("sdk_max_retries") is not None}
    return {
        "timeout_source": source,
        "per_call_timeout_s": (
            sorted(recorded) or ([fallback_timeout_s] if fallback_timeout_s else [])
        ),
        "sdk_max_retries": sorted(retries) or None,
        "wall_clock": {
            "n": len(wall),
            "p50": quantile(wall, 0.50),
            "p95": quantile(wall, 0.95),
            "max": max(wall) if wall else 0.0,
        },
        # Attempts whose wall clock is under the per-call budget, so no retry
        # driven by the clock can be inside them.
        "no_clock_retry": {
            "n": len(clean),
            "p50": quantile(clean, 0.50),
            "p95": quantile(clean, 0.95),
            "max": max(clean) if clean else 0.0,
        },
        "over_timeout": {
            "n": len(over),
            "share": round(len(over) / len(wall), 4) if wall else 0.0,
        },
        "timeout_unknown": unknown,
    }


def aggregate(
    items: Sequence[dict],
    *,
    on: date,
    cells_planned: int,
    fallback_timeout_s: float | None = None,
) -> dict:
    """§14's aggregates, plus the distributions the mean would hide.

    `fallback_timeout_s` is only consulted for items that recorded no per-call
    timeout of their own (DEVIATIONS 8.35) and is reported as supplied, not
    recorded.
    """
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
        g_prod = [i for i in group if i["outcome"] != "api_fail"]
        g_flags = decoder_limitation_rates(group)
        return {
            "attempted": len(group),
            "produced": len(g_prod),
            "accepted": len(g_acc),
            # Over ATTEMPTED cells, so a cell that produced no text counts as the
            # failure it is. The `_produced` twin is beside it because the two
            # answer different questions and an unlabelled denominator is audit
            # F9 (DEVIATIONS 8.34).
            "gate_pass_rate": (len(g_acc) / len(group)) if group else 0.0,
            "gate_pass_rate_produced": (len(g_acc) / len(g_prod)) if g_prod else 0.0,
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
            "length_compliance_rate_produced": (
                sum(1 for i in g_prod if i["length_compliant"]) / len(g_prod) if g_prod else 0.0
            ),
            "type_token_ratio_mean": round(_mean([i["type_token_ratio"] for i in group]), 4),
            "oov": sum(i["oov"] for i in group),
            # Population rates over SCORED words (DEVIATIONS 8.34), derived from
            # the counts so they hold for items written before the rename.
            "schwa_relaxed_rate_scored": g_flags["schwa_relaxed_rate_scored"],
            "ambiguity_rate_scored": g_flags["ambiguity_rate_scored"],
            "heteronym_rate_scored": g_flags["heteronym_rate_scored"],
            "unalignable_rate_scored": g_flags["unalignable_rate_scored"],
            "scored_word_total": g_flags["scored_word_total"],
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
            # The same GENERATING numbers, split so a retry can never be priced
            # as generation time (audit F7, DEVIATIONS 8.35).
            "generating_split": generation_latency_split(
                items, fallback_timeout_s=fallback_timeout_s
            ),
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
        # §5.3/§18's flags over the population that can carry one (DEVIATIONS
        # 8.34). `decoder_limitation_rates` owns the arithmetic so the same
        # function serves the report, the per-stage slices and `eval_doc.py`.
        "decoder_limitations": decoder_limitation_rates(items),
        # Which models produced this matrix, counted from the items rather than
        # asserted from the config (DEVIATIONS 8.30). More than one entry in
        # `generation` means the matrix is MIXED and the report says so.
        "models_observed": {
            "generation": _model_census(items, "generation"),
            "moderation": _model_census(items, "moderation"),
            "items_without_model_record": sum(1 for i in items if not i.get("models")),
            "items_with_no_api_response": sum(
                1
                for i in items
                if i.get("models") and not i["models"].get("generation")
            ),
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
            # Denominator named (DEVIATIONS 8.34): a cell that produced no text is
            # not length-compliant, and moving it out of the denominator would
            # flatter the figure by exactly the availability failure rate.
            "compliance_denominator": "attempted",
            "compliance_rate": (
                sum(1 for i in items if i["length_compliant"]) / attempted if attempted else 0.0
            ),
            "compliance_rate_produced": (
                sum(1 for i in produced if i["length_compliant"]) / len(produced)
                if produced
                else 0.0
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


_NOT_RECORDED = "**not recorded by this run** (DEVIATIONS 8.32-8.35)"


def _commit_cell(cfg: dict) -> str:
    """The commit AND whether the tree was dirty, in one cell.

    Audit F2: the recorded commit `559f102` cannot run the documented command —
    `eval.py` is 102 lines there. Printing a commit without the dirty flag is what
    let a whole report cite provenance it did not have, so the two are never
    separated (DEVIATIONS 8.32).
    """
    prov = cfg.get("provenance") or {}
    commit = cfg.get("git_commit", "unknown")
    dirty = prov.get("git_dirty")
    if dirty is None:
        return f"`{commit}` — dirty-tree state {_NOT_RECORDED}"
    if not dirty:
        return f"`{commit}` — working tree CLEAN, so this commit contains the code that ran"
    paths = prov.get("git_dirty_paths") or []
    harness = [p for p in paths if "storyforge/eval.py" in p or "storyforge/pipeline.py" in p]
    note = (
        " **including the harness itself**" if harness else " (none of them the harness)"
    )
    return (
        f"`{commit}` — **working tree DIRTY**: {prov.get('git_dirty_path_count', len(paths))} "
        f"modified path(s){note}, so this commit is NOT the code that ran"
    )


def _code_cell(cfg: dict) -> str:
    prov = cfg.get("provenance") or {}
    sha = (prov.get("code_sha256") or {}).get("storyforge/eval.py")
    if not sha:
        return _NOT_RECORDED
    return (
        f"`{sha[:16]}…` — check with `git show <commit>:services/api/storyforge/"
        f"eval.py | shasum -a 256`"
    )


def _timeout_cell(cfg: dict) -> str:
    t, r = cfg.get("generation_timeout_s"), cfg.get("sdk_max_retries")
    if t is None:
        return _NOT_RECORDED
    return (
        f"{t:.0f}s per HTTP attempt x up to {(r or 0) + 1} attempts "
        f"(`SDK_MAX_RETRIES={r}`) — a wall-clock latency can be that many calls"
    )


def _vocabulary_cell(cfg: dict) -> str:
    v = cfg.get("vocabulary")
    if not v:
        return _NOT_RECORDED
    return (
        f"{v['gpc_count']} GPCs `{v['gpcs_sha256'][:12]}…`, "
        f"{v['sight_word_count']} sight words `{v['sight_words_sha256'][:12]}…`, "
        f"{v['allowed_words_count']:,} allowed words, "
        f"{v['cmudict_entries']:,} CMUdict entries"
    )


def _models_observed_paragraph(mo: dict, cfg: dict) -> str:
    gen = mo.get("generation") or {}
    if not gen:
        return (
            "**No per-item model record.** This run wrote no model onto its items, "
            "so the generation model in the table above is the config's claim and "
            "nothing in the data can confirm it (audit F1, fixed in DEVIATIONS "
            "8.30).\n"
        )
    line = ", ".join(f"`{m}` x{c}" for m, c in gen.items())
    mixed = (
        "\n\n**This matrix is MIXED**: more than one generation model answered, so "
        "no single model name describes it. The per-item `metrics.models` field "
        "says which cells belong to which."
        if len(gen) > 1
        else ""
    )
    return (
        f"**Models the items themselves record** (DEVIATIONS 8.30, read off each "
        f"call's own `Usage` rather than off the environment at report time): "
        f"generation {line}. "
        f"{mo.get('items_with_no_api_response', 0)} item(s) carry no generation "
        f"model because no call returned.{mixed}\n"
    )


def _retry_budget_phrase(retries) -> str:
    """The SDK retry budget as prose, from a recorded set that may be empty.

    An unrecorded retry budget is not "0 retries" — the SDK's default is not zero —
    so an absent value says so rather than printing a number nobody measured.
    """
    vals = [v for v in (retries or []) if v is not None]
    if not vals:
        return "retries an unrecorded number of times"
    if len(vals) == 1:
        return f"retries up to {vals[0]} time(s)"
    return f"retries up to {min(vals)}-{max(vals)} times (the run is not uniform)"


def _latency_split_paragraph(split: dict) -> str:
    """Wall clock and the no-retry subset, side by side and separately labelled.

    Audit F7: the row labelled "GENERATING (first attempt)" was wall time around one
    SDK call carrying `SDK_MAX_RETRIES`, so up to three HTTP attempts were priced as
    one generation — and the report then blamed the §1.6 miss on long stories while
    filing the same timeout as the availability defect, borrowing one defect's
    number for two findings in a document that promises it does not.
    """
    if not split or split.get("timeout_source") == "unknown":
        return (
            "**The retry-free subset cannot be computed for this run**: no per-call "
            "timeout was recorded on its items, so wall clock is the only latency "
            "available and it includes any SDK-internal retries "
            "(DEVIATIONS 8.35).\n"
        )
    w, cl, ov = split["wall_clock"], split["no_clock_retry"], split["over_timeout"]
    budget = ", ".join(f"{b:.0f}s" for b in split.get("per_call_timeout_s") or [])
    retries = _retry_budget_phrase(split.get("sdk_max_retries"))
    src = (
        "recorded per item"
        if split["timeout_source"] == "recorded"
        else "SUPPLIED at report time, not recorded by the run"
    )
    return (
        f"**Two GENERATING numbers, because one was measuring two things** "
        f"(DEVIATIONS 8.35). The row above is wall clock around one SDK call, and "
        f"that call {retries}, so it can contain several HTTP attempts. Against a "
        f"per-call budget of "
        f"{budget} ({src}), **{ov['n']} of {w['n']}** generations "
        f"({_pct(ov['share'])}) ran longer than one attempt could, so they contain "
        f"at least one attempt that was killed and paid for. Over the {cl['n']} that "
        f"did not, p50 is **{cl['p50']:.0f} ms** and p95 **{cl['p95']:.0f} ms** "
        f"against the wall-clock p95 of {w['p95']:.0f} ms.\n\n"
        f"The subset is named `no_clock_retry`, not `single_attempt`: a fast 429 or "
        f"5xx retry still hides inside a fast call, so this is a bound on "
        f"retry-free latency and not a proof of one attempt.\n"
    )


def _resume_paragraph(trail: Sequence[dict], agg: dict) -> str:
    """What a resume did, in checkable terms.

    This paragraph used to assert, unconditionally, that `--resume` ran the
    remainder "against the SAME stored config — same model, same gate, same pricing
    date". It did not: the stored config won the record while the environment won
    the behaviour (audit F1). It now says which values were restored, which were
    verified, and prints any drift.
    """
    generating = [t for t in trail if t.get("cells_remaining", 1) != 0]
    first = trail[0].get("items_already_recorded", 0)
    restored = "stages, themes, n, effort, pricing date, cold, enrich"
    verified = ", ".join(k for k, _ in _VERIFIED_CONFIG_KEYS)
    drift = [d for t in trail for d in (t.get("config_drift") or [])]
    out = [
        f"**This matrix was gathered in {len(generating) + 1} sitting(s)** "
        f"(DEVIATIONS 7.51). The first invocation stopped at cell {first} of "
        f"{agg['cells_planned']}; every item is committed as it completes, so "
        f"`--resume` ran only the remainder.\n",
        f"On a resume the matrix parameters ({restored}) are RESTORED from the "
        f"stored config, which is enforcement because they are arguments. The "
        f"values that come from the environment or from a module constant "
        f"({verified}) cannot be restored by passing them, so they are VERIFIED "
        f"against the stored config and a difference REFUSES the resume with "
        f"exit code 3 while cells remain (DEVIATIONS 8.31). `concurrency` and "
        f"`budget_usd` are "
        f"per-sitting and are recorded on each trail entry rather than attributed "
        f"to the run. Trail: `{json.dumps(trail, default=str)}`\n",
    ]
    if drift:
        missing = sorted({d["key"] for d in drift if d.get("missing")})
        differs = sorted({d["key"] for d in drift if not d.get("missing")})
        out.append(
            "**CONFIG DRIFT on a re-derivation.** "
            + (
                f"Differed from the live process at report time: "
                f"{', '.join('`' + k + '`' for k in differs)}. "
                if differs
                else ""
            )
            + (
                f"Never recorded by this run, so uncheckable: "
                f"{', '.join('`' + k + '`' for k in missing)}. "
                if missing
                else ""
            )
            + (
                "The values in force on the sitting that ran are on its trail entry "
                "under `unrecorded_live_values`, so what the LATER half ran against "
                "is pinned even where the run as a whole has no field for it. "
                if missing
                else ""
            )
            + "The measurements above are the ones recorded at run time and are "
            "NOT restated against the current values.\n"
        )
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
                ["per-call generation timeout", _timeout_cell(cfg)],
                ["git commit", _commit_cell(cfg)],
                ["harness code sha256 (eval.py)", _code_cell(cfg)],
                ["vocabulary (the instrument, §5)", _vocabulary_cell(cfg)],
                ["python", cfg["python"]],
            ],
        )
        + "\n"
    )

    # Which models actually answered, counted from the items (DEVIATIONS 8.30).
    # The config row above is a claim about the whole matrix; this is the evidence.
    mo = agg.get("models_observed")
    if mo:
        L.append(_models_observed_paragraph(mo, cfg))

    trail = cfg.get("resumed") or []
    if trail:
        L.append(_resume_paragraph(trail, agg))

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
    # The price pair is derived from the sheet actually used, not spelled out:
    # `eval.py` used to hard-code "$3/$15" beside a number computed from whatever
    # model the shell named (audit F1, DEVIATIONS 8.36).
    pi = run.get("post_intro") or {}
    if pi.get("total_usd") is not None:
        pair = ", ".join(
            f"`{m}` ${p['input_per_mtok']}/${p['output_per_mtok']}"
            for m, p in (pi.get("prices") or {}).items()
            if p
        )
        fb = pi.get("items_priced_from_fallback") or 0
        L.append(
            f"Same token counts priced on {pi['on']}, after the Sonnet 5 "
            f"introductory rate ends: **${pi['total_usd']:.4f}** "
            f"(x{pi['total_usd'] / max(c['total_usd'], 1e-9):.2f}) at {pair}. "
            f"Priced per item against the model each item recorded"
            + (
                f"; {fb} item(s) had no model record and were priced from the "
                f"config's models."
                if fb
                else "."
            )
            + "\n"
        )
    elif pi.get("unpriced_models"):
        L.append(
            f"**This run could not be repriced for {pi['on']}**: no price sheet for "
            f"{', '.join('`' + m + '`' for m in pi['unpriced_models'])}. Reported as "
            f"unpriceable rather than as $0.00 (DEVIATIONS 8.36) — the partial total "
            f"over the models that DO have a sheet is "
            f"${pi.get('partial_total_usd') or 0:.4f}.\n"
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
                    "GENERATING (wall clock, retries INCLUDED)",
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
    L.append(_latency_split_paragraph(lat.get("generating_split") or {}))
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
        f"**Denominator: SCORED words** — {dl['scored_word_total']:,} of "
        f"{dl['word_token_total']:,} word tokens. Sight and story words are "
        f"classified before any alignment is attempted, so they can carry none of "
        f"these flags; counting them halved §18's designated alarm in the first "
        f"§14 report (audit F6, DEVIATIONS 8.34). The rate column is the population "
        f"rate (flags / scored words); the mean-per-story column is beside it "
        f"because the two answer different questions.\n"
    )
    L.append(
        _table(
            ["flag", "total instances", "rate over SCORED words", "mean rate per story"],
            [
                [
                    "schwa_relaxed (§5.2.6)",
                    dl["schwa_relaxed_total"],
                    _pct(dl["schwa_relaxed_rate_scored"]),
                    _pct(dl["schwa_relaxed_rate_scored_mean"]),
                ],
                [
                    "ambiguity_flag (§5.2.5)",
                    dl["ambiguity_total"],
                    _pct(dl["ambiguity_rate_scored"]),
                    _pct(dl["ambiguity_rate_scored_mean"]),
                ],
                [
                    "heteronym_flag",
                    dl["heteronym_total"],
                    _pct(dl["heteronym_rate_scored"]),
                    _pct(dl["heteronym_rate_scored_mean"]),
                ],
                [
                    "alignable=False (unalignable)",
                    dl["unalignable_total"],
                    _pct(dl["unalignable_rate_scored"]),
                    _pct(dl["unalignable_rate_scored_mean"]),
                ],
                ["non-minimal missing-GPC set", dl["non_minimal_missing_total"], "—", "—"],
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
    L.append("### Per stage (all rates over SCORED words)\n")
    L.append(
        _table(
            [
                "stage",
                "scored words",
                "schwa",
                "ambiguity",
                "heteronym",
                "unalignable",
                "unalignable words",
            ],
            [
                [
                    s,
                    v["scored_word_total"],
                    _pct(v["schwa_relaxed_rate_scored"]),
                    _pct(v["ambiguity_rate_scored"]),
                    _pct(v["heteronym_rate_scored"]),
                    _pct(v["unalignable_rate_scored"]),
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
                [
                    "within the stage's target range (of ATTEMPTED cells)",
                    _pct(agg["length"]["compliance_rate"]),
                ],
                [
                    "within the stage's target range (of PRODUCED stories)",
                    _pct(agg["length"].get("compliance_rate_produced", 0.0)),
                ],
                ["median word count", agg["length"]["word_count_p50"]],
                ["mean type-token ratio", f"{agg['length']['type_token_ratio_mean']:.3f}"],
            ],
        )
        + "\n"
    )
    L.append(
        "Both denominators are printed because they answer different questions and "
        "an unlabelled one is audit F9 (DEVIATIONS 8.34). A cell that produced no "
        "text is not length-compliant, so the ATTEMPTED figure is the one that "
        "counts availability honestly; the PRODUCED figure is the generation "
        "quality of the text that exists.\n"
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


def remaining_cells(
    stages: Sequence[int], themes: Sequence[str], n: int, done_items: Sequence[dict]
) -> list[tuple[int, str, int]]:
    """The cells a resume still has to run, derived from the recorded items.

    Counted per (stage, theme) rather than assumed from ordering, so an interrupted
    matrix resumes correctly regardless of the order the items were committed in.
    One function for both the run loop and the drift decision (DEVIATIONS 8.31) —
    "how many cells are left" has to mean the same thing in both places, because a
    resume with 0 cells left is allowed to disclose drift where a resume with work
    to do must refuse it.
    """
    done = Counter((it["stage_id"], it["theme"]) for it in done_items)
    out: list[tuple[int, str, int]] = []
    for stage, theme, i in _cells(stages, themes, n):
        if done[(stage, theme)] > 0:
            done[(stage, theme)] -= 1
            continue
        out.append((stage, theme, i))
    return out


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
            cwd=repo_root(),
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — provenance is recorded, never load-bearing
        return None


def _git_commit() -> str:
    return _git("rev-parse", "--short", "HEAD") or "unknown"


#: The code whose content decides what a run MEASURES: the harness, the state
#: machine it drives, the gate, and the client that prices it. Hashed so a reader
#: can settle by arithmetic what audit F2 had to settle by hand — the recorded
#: commit either contains this code or it does not.
_FINGERPRINTED_CODE: tuple[str, ...] = (
    "storyforge/eval.py",
    "storyforge/pipeline.py",
    "storyforge/llm",
    "storyforge/decoder",
)


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    if path.is_dir():
        for f in sorted(p for p in path.rglob("*.py")):
            h.update(f.relative_to(path).as_posix().encode())
            h.update(f.read_bytes())
    else:
        h.update(path.read_bytes())
    return h.hexdigest()


def _provenance() -> dict:
    """What produced this run, in a form a later reader can check.

    Audit F2: `config.git_commit` recorded a commit at which `eval.py` is 102 lines
    with only a `smoke` subparser, so the recorded provenance could not run the
    documented command — every §14 number came from uncommitted code and nothing in
    the record said so. A bare `git rev-parse` cannot say so, because it never
    looks at the working tree (DEVIATIONS 8.32).

    `git_dirty_paths` is capped and sorted so the interesting case — the harness
    itself was modified — is visible rather than buried in a count.
    """
    commit = _git_commit()
    porcelain = _git("status", "--porcelain")
    dirty_paths = (
        sorted({ln[3:].split(" -> ")[-1].strip() for ln in porcelain.splitlines() if ln.strip()})
        if porcelain
        else []
    )
    api = Path(__file__).resolve().parents[1]
    per_file = {}
    for rel in _FINGERPRINTED_CODE:
        try:
            per_file[rel] = _sha256_path(api / rel)
        except OSError:
            per_file[rel] = "unreadable"
    combined = hashlib.sha256()
    for rel in _FINGERPRINTED_CODE:
        combined.update(f"{rel}:{per_file[rel]}\n".encode())
    return {
        "git_commit": commit,
        # None (not False) when git could not be consulted at all: "we did not
        # look" and "we looked and it was clean" are different facts.
        "git_dirty": (bool(dirty_paths) if porcelain is not None else None),
        "git_dirty_paths": dirty_paths[:40],
        "git_dirty_path_count": len(dirty_paths),
        "code_sha256": per_file,
        "code_sha256_combined": combined.hexdigest(),
    }


async def vocabulary_fingerprint(session) -> dict:
    """The vocabulary the gate was computed against (audit F3, DEVIATIONS 8.33).

    An eval run is a MEASUREMENT, and 11 of run 2's 169 decodability figures
    stopped reproducing fourteen minutes after they were verified because a
    parallel agent re-seeded `sight_words` and added a GPC. `eval_runs.config`
    carried the model, prices, gate, rewrite budget, prompt allowlist size, python
    version and git commit — and nothing about the vocabulary, so the report could
    not detect its own staleness.

    Hashes, not contents: the goal is that a later reader can tell whether a
    re-derivation is comparable, not to rebuild the vocabulary from the record. The
    GPC hash is taken over `load_gpcs()` rather than the CSV bytes because
    `data/seed/gpcs.csv` is a symlink into the package (STATE invariant 8), so the
    path is not the artifact.
    """
    gpcs = load_gpcs()
    gpc_rows = "\n".join(
        f"{g.id}|{g.stage}|{g.kind}|{g.position}|{int(g.schwa_ok)}" for g in gpcs
    )
    gpc_hash = hashlib.sha256(gpc_rows.encode()).hexdigest()

    sight = (
        await session.execute(
            text("SELECT word, stage_id FROM sight_words ORDER BY word, stage_id")
        )
    ).all()
    sight_hash = hashlib.sha256("\n".join(f"{w}|{s}" for w, s in sight).encode()).hexdigest()

    allowed = (
        await session.execute(
            text("SELECT stage_id, count(*) FROM allowed_words GROUP BY stage_id ORDER BY stage_id")
        )
    ).all()
    allowed_hash = hashlib.sha256("\n".join(f"{s}|{c}" for s, c in allowed).encode()).hexdigest()

    return {
        "gpc_count": len(gpcs),
        "gpcs_sha256": gpc_hash,
        "sight_word_count": len(sight),
        "sight_words_sha256": sight_hash,
        "allowed_words_count": sum(c for _, c in allowed),
        "allowed_words_per_stage_sha256": allowed_hash,
        # §18 asks for the CMUdict snapshot to be vendored so the proof is
        # reproducible; the entry count is the cheapest check that the snapshot
        # that ran is the one that was pinned (`decoder/data/PINS.md`).
        "cmudict_entries": len(load_lexicon()),
    }


#: Config keys whose value comes from the ENVIRONMENT or from a module constant,
#: so a resume can never restore them by passing an argument — it can only check
#: them and refuse (DEVIATIONS 8.31). Audit F1 is exactly the absence of this list.
_VERIFIED_CONFIG_KEYS: tuple[tuple[str, str], ...] = (
    ("models", "ANTHROPIC_MODEL_GENERATE / ANTHROPIC_MODEL_FAST"),
    ("prices", "storyforge.llm.pricing.PRICES (and --pricing-date)"),
    ("gate", "storyforge.pipeline.DECODABILITY_GATE"),
    ("max_rewrites", "storyforge.pipeline.MAX_REWRITES"),
    ("max_story_words", "storyforge.pipeline.MAX_STORY_WORDS"),
    ("prompt_word_limit", "storyforge.pipeline.PROMPT_WORD_LIMIT"),
    ("generation_timeout_s", "ANTHROPIC_GENERATION_TIMEOUT_S"),
    ("sdk_max_retries", "storyforge.llm.client.SDK_MAX_RETRIES"),
    ("vocabulary", "make seed / make precompute (DEVIATIONS 8.33)"),
    ("themes", "the themes table"),
)


def config_drift(stored: dict, live: dict, *, keys=_VERIFIED_CONFIG_KEYS) -> list[dict]:
    """Every verified key on which the stored run and the live process disagree.

    A key the stored config never recorded is NOT drift — it is a run that predates
    the record, and reporting it as a difference would refuse every resume of every
    run written before DEVIATIONS 8.31. It is disclosed instead (`missing`).
    """
    out: list[dict] = []
    for key, source in keys:
        if key not in stored:
            out.append({"key": key, "stored": None, "live": live.get(key), "source": source,
                        "missing": True})
            continue
        if stored[key] != live.get(key):
            out.append({"key": key, "stored": stored[key], "live": live.get(key),
                        "source": source, "missing": False})
    return out


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
    not either.

    **What a resume does with the stored config** (DEVIATIONS 8.31; this used to be
    a claim rather than a behaviour — audit F1). Three kinds of value, three
    treatments:

    * RESTORED — real arguments, so restoring them IS enforcement: the matrix
      (`stages_spec`, `themes_spec`, the resolved `themes` list, `n`), `effort`,
      `pricing_date`, `cold`, `enrich`.
    * VERIFIED — environment reads and module constants no argument can carry
      (`_VERIFIED_CONFIG_KEYS`): the models, prices, gate, rewrite budget,
      allowlist size, timeouts, retry budget and vocabulary fingerprint. A
      difference REFUSES the resume with exit code 3 while cells remain, and is
      disclosed in the report when the run is already complete and nothing but the
      report can change.
    * PER-SITTING — `concurrency` and `budget_usd`, recorded on the resume trail
      entry rather than pretended to be the run's.
    """
    stages = parse_stages(stages_spec)
    started = datetime.now(UTC)
    resumed_items: list[dict] = []
    resumed_config: dict | None = None
    resumed_themes: list[str] | None = None

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
            cold = bool(resumed_config.get("cold", cold))
            enrich = bool(resumed_config.get("enrich", enrich))
            stages = parse_stages(stages_spec)
            pricing_date = date.fromisoformat(
                resumed_config.get("pricing_date", pricing_date.isoformat())
            )
            resumed_themes = list(resumed_config.get("themes") or []) or None
        theme_ids = await _theme_ids(session)
        themes_live = (
            sorted(theme_ids)
            if themes_spec.strip() == "all"
            else [t.strip() for t in themes_spec.split(",") if t.strip()]
        )
        # Audit S2: `--themes all` re-resolved after a new theme was seeded grows
        # `cells_planned` under a config that records the old list. The stored LIST
        # wins; the live resolution is kept only so the drift check can see it.
        themes = resumed_themes or themes_live
        unknown = [t for t in themes if t not in theme_ids]
        if unknown:
            raise SystemExit(f"unknown theme(s) {unknown} — run `make seed`")

        vocab_fingerprint = await vocabulary_fingerprint(session)
        vocabs = [await load_vocabulary(session, s, themes[0]) for s in stages]
        projection = project_cost(vocabs, n * len(themes), pricing_date)
        cells_planned = len(stages) * len(themes) * n
        servable_before = await servable_pool_size(session)

    gen_model, fast = generation_model(), fast_model()
    gp, fp = price_for(gen_model, pricing_date), price_for(fast, pricing_date)
    prov = _provenance()
    per_call_timeout_s = generation_timeout(MAX_OUTPUT_TOKENS).read
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
        # The per-HTTP-attempt budget and the retry budget that multiplies it.
        # Recorded because a latency number cannot be read without them (8.35).
        "generation_timeout_s": per_call_timeout_s,
        "sdk_max_retries": SDK_MAX_RETRIES,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        # The instrument, not just the subject (8.33).
        "vocabulary": vocab_fingerprint,
        "cells_planned": cells_planned,
        # Kept as a top-level key for every existing reader; `provenance` carries
        # the dirty-tree flag and the content hashes that make it checkable (8.32).
        "git_commit": prov["git_commit"],
        "provenance": prov,
        "python": platform.python_version(),
        "projection_usd": projection,
    }
    remaining = remaining_cells(stages, themes, n, resumed_items)
    if resumed_config is not None:
        # `themes` in `config` is the RESTORED list, so the drift check is handed
        # the LIVE resolution — otherwise it compares the stored value with itself.
        drift = config_drift(resumed_config, {**config, "themes": themes_live})
        # A key the stored run never recorded cannot be checked. That is a fact
        # about the older run, not a difference, so it is disclosed rather than
        # treated as drift — otherwise no run written before 8.31 is resumable.
        hard = [d for d in drift if not d["missing"]]
        if remaining and int(resumed_config.get("concurrency", concurrency)) != concurrency:
            hard.append(
                {
                    "key": "concurrency",
                    "stored": resumed_config.get("concurrency"),
                    "live": concurrency,
                    "source": "--concurrency",
                    "missing": False,
                }
            )
        if hard and remaining:
            print(
                f"\nRESUME REFUSED: {len(hard)} value(s) that decide what this "
                f"harness MEASURES differ from the ones eval_runs.id={resume_run_id} "
                f"recorded, and {len(remaining)} of {cells_planned} cells still have "
                f"to run. Two halves of one matrix measured against different values "
                f"are two runs, not one (§14, DEVIATIONS 8.31).",
                file=sys.stderr,
            )
            for d in hard:
                print(
                    f"  - {d['key']}: stored {json.dumps(d['stored'], default=str)} "
                    f"!= live {json.dumps(d['live'], default=str)}"
                    f"\n      set by: {d['source']}",
                    file=sys.stderr,
                )
            print(
                "\nRestore the values above and resume, or start a new run — a new "
                "`eval_runs` row is the honest way to measure something different.",
                file=sys.stderr,
            )
            return 3
        trail = list(resumed_config.get("resumed", []))
        trail.append(
            {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "items_already_recorded": len(resumed_items),
                "cells_remaining": len(remaining),
                "git_commit": prov["git_commit"],
                "git_dirty": prov["git_dirty"],
                "code_sha256_combined": prov["code_sha256_combined"],
                # Per-sitting, because they came from THIS command line and the
                # stored config's values were never in force here (audit S1, F10).
                "concurrency": concurrency,
                "budget_usd": budget_usd,
                "config_drift": drift or None,
                # A value the run never recorded is recorded HERE, on the sitting
                # that knows it, rather than being written into the run's config —
                # where it would imply the earlier sitting had used it too. Resuming
                # a pre-8.31 run therefore still pins what the NEW half ran
                # against, without back-dating a fingerprint onto the old half.
                "unrecorded_live_values": {
                    d["key"]: d["live"] for d in drift if d["missing"]
                }
                or None,
            }
        )
        # The stored config still wins the record — that part was right. What is
        # new is that the values it records were CHECKED against the live process
        # above, so recording them is a measurement rather than a hope.
        config = {**resumed_config, "resumed": trail}
        if drift:
            # Reached only when nothing HARD differs or nothing is left to run; the
            # refusal above owns the other case. The wording says which, because
            # "we continued anyway" and "there was nothing left to continue" are
            # different facts about the same message.
            why = (
                "the run is complete, so nothing but the report can change"
                if not remaining
                else "none of these is a value this run recorded, so none can be checked"
            )
            print(
                f"\nCONFIG DRIFT ({len(drift)} key(s)) — {why}, and the report will "
                f"say so:",
                file=sys.stderr,
            )
            for d in drift:
                label = "NOT RECORDED by this run" if d["missing"] else "differs"
                print(f"  - {d['key']}: {label}", file=sys.stderr)

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

        async def one(stage: int, theme: str, i: int) -> None:
            # A session PER CELL, not the enclosing one. `AsyncSession` is not
            # concurrency-safe: sharing it across `asyncio.gather` raises "This
            # session is provisioning a new connection; concurrent operations are
            # not permitted", which is why `--concurrency` above 1 had never
            # actually run despite being an advertised flag (DEVIATIONS 8.100).
            # Per-cell sessions also preserve the property `--resume` relies on —
            # each item commits as it completes, so an interrupted run keeps every
            # measurement it finished.
            async with sem, SessionLocal() as cell_session:
                await _attempt(
                    session=cell_session,
                    stage=stage,
                    theme=theme,
                    index=i,
                    theme_ids=theme_ids,
                    run_id=run_id,
                    effort=effort,
                    pricing_date=pricing_date,
                    items=items,
                    requested_models={"generation": gen_model, "fast": fast},
                    per_call_timeout_s=per_call_timeout_s,
                )

        t0 = time.perf_counter()
        try:
            if concurrency <= 1:
                for stage, theme, i in remaining:
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
                    batch = [c for c in remaining if c[0] == stage]
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
        agg = aggregate(
            items,
            on=pricing_date,
            cells_planned=cells_planned,
            # Only reached by items that recorded no budget of their own, i.e. rows
            # written before DEVIATIONS 8.35. Read off the CONFIG rather than off
            # the live process: resuming a pre-8.35 run has no recorded budget for
            # its items and must report the split as unavailable, not judge a 30s
            # measurement against today's 240s.
            fallback_timeout_s=config.get("generation_timeout_s"),
        )
        agg["wall_clock_s"] = round(wall_s, 1)

        status = "budget_stopped" if budget_stopped else ("failed" if fatal else "completed")
        servable_after = await servable_pool_size(session)
        finished = datetime.now(UTC)
        db_run = await session.get(EvalRun, run_id)
        db_run.metrics = agg
        db_run.status = status
        db_run.finished_at = finished
        await session.commit()

    # The same token counts re-priced after the Sonnet 5 intro rate expires,
    # against the models the ITEMS recorded rather than the current shell (8.36).
    post_intro_date = date(2026, 9, 1)
    post_intro = _reprice(items, post_intro_date, fallback_models=config.get("models"))

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
        "post_intro_cost_usd": post_intro["total_usd"],
        "post_intro": post_intro,
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
    # `reports/` is gitignored, so `docs/EVAL.md` is the committed record and it is
    # regenerated from this JSON, never hand-edited. Printed rather than run: a
    # failed matrix must not silently rewrite the committed report, and "someone
    # remembered to regenerate it" is what DEVIATIONS 8.39 is an entry about.
    print(
        f"\nregenerate the committed §14 report:\n"
        f"  services/api/.venv/bin/python scripts/eval_doc.py {json_path} docs/EVAL.md"
    )

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


def _reprice(items: Sequence[dict], on: date, *, fallback_models: dict | None = None) -> dict:
    """Total cost of the same token counts on another date.

    Exists because `price_for` takes an explicit date precisely so a report can
    say "and here is what this run costs in September". Recomputed from the token
    counts on the items rather than from a stored dollar figure.

    **Priced against the model each ITEM recorded** (DEVIATIONS 8.36). This used
    to call `generation_model()`, which made the September figure a function of the
    current shell: the audit's Opus-flavoured resume priced run 2's tokens at
    $6.5718 in a report whose config table said Sonnet at $2/$10. `fallback_models`
    covers items written before 8.30 recorded a model, and is reported as a
    fallback so a reader knows which rows were priced on an assumption.

    Returns the total, the sheets used, and — instead of audit F13.4's silent
    `0.0` — an explicit `unpriced` list when a model has no price sheet. An
    unpriced model reported as free is a cost defect dressed as a cost report.
    """
    fb = fallback_models or {}
    sheets: dict[str, object] = {}
    unpriced: set[str] = set()
    assumed = 0

    def sheet(model: str | None):
        if not model:
            return None
        if model in sheets:
            return sheets[model]
        try:
            sheets[model] = price_for(model, on)
        except KeyError:
            unpriced.add(model)
            sheets[model] = None
        return sheets[model]

    total = 0.0
    for it in items:
        t = it["tokens"]
        rec = it.get("models") or {}
        gen_models = rec.get("generation") or []
        mod_models = rec.get("moderation") or []
        if not gen_models and not mod_models:
            assumed += 1
        gp = sheet(gen_models[0] if gen_models else (rec.get("requested") or fb).get("generation"))
        fp = sheet(mod_models[0] if mod_models else (rec.get("requested") or fb).get("fast"))
        if gp is not None:
            total += (
                t.get("input", 0) * gp.input_per_mtok / 1e6
                + t.get("cache_read", 0) * gp.input_per_mtok / 1e6 * CACHE_READ_MULTIPLIER
                + t.get("cache_write", 0) * gp.input_per_mtok / 1e6 * CACHE_WRITE_MULTIPLIER
                + t.get("output", 0) * gp.output_per_mtok / 1e6
            )
        if fp is not None:
            total += (
                t.get("mod_input", 0) * fp.input_per_mtok / 1e6
                + t.get("mod_output", 0) * fp.output_per_mtok / 1e6
            )
    return {
        "on": on.isoformat(),
        # None, not 0.0: "we could not price this" is not "this was free".
        "total_usd": None if unpriced else round(total, 6),
        "partial_total_usd": round(total, 6) if unpriced else None,
        "prices": {
            m: (p.model_dump() if p is not None else None) for m, p in sorted(sheets.items())
        },
        "unpriced_models": sorted(unpriced),
        "items_priced_from_fallback": assumed,
    }


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
    requested_models: dict[str, str] | None = None,
    per_call_timeout_s: float | None = None,
) -> None:
    """One cell of the matrix. Never raises (DEVIATIONS 7.47).

    §7's failure taxonomy is `validation_fail | api_fail | moderation_fail |
    budget_exhausted | timeout`, and every one of them is a MEASUREMENT here. A
    matrix that dies at cell 140 of 180 because one HTTP call timed out throws
    away the evidence for 139 cells, and the fallback rate §14 asks for
    aggregates exactly these events.
    """
    vocab = await load_vocabulary(session, stage, theme)
    provenance = {
        "requested_models": requested_models,
        "per_call_timeout_s": per_call_timeout_s,
        "sdk_max_retries": SDK_MAX_RETRIES,
    }
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
            result,
            vocab,
            index=index,
            outcome="api_fail",
            on=pricing_date,
            story_id=None,
            **provenance,
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
        result,
        vocab,
        index=index,
        outcome=outcome,
        on=pricing_date,
        story_id=story.id,
        **provenance,
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
        f"  decoder flags    (over SCORED words) "
        f"schwa {agg['decoder_limitations']['schwa_relaxed_rate_scored']:.1%} · "
        f"ambig {agg['decoder_limitations']['ambiguity_rate_scored']:.1%} · "
        f"heteronym {agg['decoder_limitations']['heteronym_rate_scored']:.1%} · "
        f"unalignable {agg['decoder_limitations']['unalignable_rate_scored']:.1%}",
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


def _smoke_reason(r: PipelineResult) -> str:
    """Why this smoke story failed, in one line.

    Smoke does not persist anything, so whatever is not printed here is gone
    (DEVIATIONS 8.38). A run that failed 3 of 4 on a story measuring 100%
    decodable printed `FAIL 100.0%` with `violations: []` and no reason — the
    verdict was a moderation rejection and the harness had no way to say so.
    """
    if r.passed:
        return "passed"
    mod = r.moderation
    if mod is not None and not mod.approved:
        bits = [f"moderation {moderation_class(mod)} (layer {mod.layer})"]
        if mod.blocked_words:
            bits.append(f"blocked words {mod.blocked_words}")
        if mod.grammar_note:
            bits.append(f"grammar note: {mod.grammar_note}")
        bits.append(f"reason: {mod.reason}")
        return " · ".join(bits)
    if r.final_report is not None and r.final_report.decodability < DECODABILITY_GATE:
        return (
            f"below the gate at {r.final_report.decodability:.1%} · "
            f"violations {r.attempts[-1].violations[:8] if r.attempts else []}"
        )
    if r.final_report is not None and r.final_report.counts.get("oov"):
        return f"{r.final_report.counts['oov']} OOV word(s) — the gate requires zero"
    if r.final_report is not None and len(r.final_report.distinct_story_words) > MAX_STORY_WORDS:
        return (
            f"{len(r.final_report.distinct_story_words)} distinct story words over "
            f"the cap of {MAX_STORY_WORDS}"
        )
    return "rewrite budget exhausted without clearing the gate"


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
                        print(f"    why: {_smoke_reason(r)}", flush=True)
                        print(f"    violations: {r.attempts[-1].violations[:8]}", flush=True)
                        # Nothing here is persisted, so the text goes out too or
                        # the failure is unreproducible (DEVIATIONS 8.38).
                        print(f"    text: {r.final_text}", flush=True)

    print(_fmt(results, today))
    for r in results:
        if r.passed:
            print(f"--- stage {r.stage_id} / {r.theme} ---\n{r.final_text}\n")
            break

    # §14: the smoke gate must fail loudly in CI, not just print a table.
    failures = [r for r in results if not r.passed]
    if failures:
        # NOT "below gate": a moderation rejection is a verdict on a story that
        # already cleared the gate, and calling it a gate failure is the same
        # conflation DEVIATIONS 7.49 keeps out of the full run's exit code.
        why = Counter(_smoke_reason(r).split(" · ")[0] for r in failures)
        raise SystemExit(
            f"eval-smoke FAILED: {len(failures)}/{len(results)} did not pass — "
            + ", ".join(f"{k} x{v}" for k, v in sorted(why.items()))
        )
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
            "measurements. The stored matrix parameters are RESTORED over these "
            "flags; the models, gate, prices, timeouts and vocabulary are VERIFIED "
            "against the stored config and a difference exits 3 while cells remain "
            "(DEVIATIONS 8.31)"
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
