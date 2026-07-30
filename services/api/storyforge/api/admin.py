"""Admin portal backend (§9 lines 232-234, §11's "Admin portal", §17 steps 2 and 4).

Five routes, all under ``/api/admin/*`` behind one router-level
``require_admin`` — the gate is a property of the router, not a thing each
handler remembers (DEVIATIONS 9.1):

    GET  /api/admin/eval-runs                 list + this process's launches
    POST /api/admin/eval-runs                 estimate by default; spends only on confirm
    GET  /api/admin/eval-runs/{id}            §14's metrics tables + paginated items
    GET  /api/admin/allowed-words?stage=N     paginated (§5.4)
    POST /api/admin/cold-generate             §11's "live pipeline states" button
    GET  /api/admin/cold-generate/{job_id}    the poll (DEVIATIONS 9.13)

``POST /api/validate`` (§5.6) already exists in ``api/validate.py`` and powers
§17 step 1's playground. It is not duplicated here.

**No route in this file reads a child.** `kids`, `readings`, `answers` and
`gpc_stats` do not appear in any statement below, and `tests/test_admin.py`
asserts that by reading the module. DEVIATIONS 6.10 keeps role and ownership
independent: an admin is not an owner, so `GET /api/kids` returns an admin's OWN
children and no others whatever their role, and an admin route that reached another
family's children would be the bypass that design exists to prevent (9.11, 9.16).

**Spending money from a button.** `POST /api/admin/eval-runs` starts real
Anthropic calls: `docs/EVAL.md` records the full 180-cell matrix at **$5.41 and
17m49s**. So the endpoint is estimate-by-default (9.4), carries a hard per-request
ceiling below the CLI's own `--budget` default (9.5), a process-cumulative ceiling
(9.6), and a one-in-flight check that does not deadlock on the stale `running` row
already in the database (9.7). `run_full`'s own cap is unchanged and is the one
that binds: it is checked against the RUNNING ACTUAL after every story
(DEVIATIONS 7.44).

**§14 stays canonical.** Nothing here recomputes a metric. The list projects a
summary out of `eval_runs.metrics`, the detail returns that aggregate verbatim,
and the pass/fail verdict is `eval.criteria_verdict` — the same function
`make eval`'s exit code comes from.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storyforge import obs
from storyforge.auth import Principal, require_admin
from storyforge.config import get_settings
from storyforge.db import SessionLocal, get_session
from storyforge.decoder import MAX_STAGE, MIN_STAGE
from storyforge.eval import (
    DEFAULT_BUDGET_USD,
    DEFAULT_N,
    DEFAULT_STAGES,
    DEFAULT_THEMES,
    criteria_verdict,
    parse_stages,
    project_cost,
    run_full,
)
from storyforge.pipeline import (
    DECODABILITY_GATE,
    MAX_REWRITES,
    MAX_STORY_WORDS,
    PROMPT_WORD_LIMIT,
    PipelineResult,
    load_vocabulary,
    run_pipeline,
)

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    # §9's `(admin)`. On the ROUTER, so a route added later cannot forget it.
    dependencies=[Depends(require_admin)],
)

# --- ceilings (DEVIATIONS 9.5, 9.6, 9.12) ------------------------------------

#: The most one request may authorise. Deliberately BELOW `eval.DEFAULT_BUDGET_USD`
#: (15.0): the measured full-matrix actual is $5.41, so 8.0 is that plus ~48% for a
#: worse rewrite rate, and the CLI's 15 stays reachable only from a shell (9.5).
MAX_BUDGET_USD = 8.0
#: Committed budget across every launch in THIS process. Two ceilings' worth. Sums
#: what was authorised, not what was spent — the conservative direction, and the
#: only one available while a run is in flight (9.6).
PROCESS_BUDGET_USD = 2 * MAX_BUDGET_USD
#: §14's matrix is 11 x 3 x 5. Nothing bigger can be asked for from a button.
MAX_N = 5
MAX_CELLS = 165
#: `concurrency` mirrors the CLI's default of 1 (which keeps the latency numbers
#: answering §1.6) and is capped at what run 114 used.
MAX_CONCURRENCY = 6

LIST_DEFAULT_LIMIT = 20
LIST_MAX_LIMIT = 100
ITEMS_DEFAULT_LIMIT = 50
ITEMS_MAX_LIMIT = 200
WORDS_DEFAULT_LIMIT = 100
WORDS_MAX_LIMIT = 500

#: How long the client should wait before polling again (9.13). 750 ms resolves
#: every node transition in a 25-60 s cold generation.
POLL_AFTER_MS = 750

#: In-process retention. Both registries are ephemeral and say so in the response.
#:
#: `MAX_LAUNCHES_KEPT` is a display bound and nothing more. It used to bound the
#: BUDGET too, because `_committed_usd` summed the surviving rows: `_prune`
#: deleted a finished launch and its authorised dollars left the cumulative
#: ceiling with it (DEVIATIONS 14.30). Ten is exactly the number 9.6's rationale
#: names — "ten sequential clicks are ten runs" — so the retention limit
#: re-enabled the precise scenario the ceiling was written to stop: with the
#: registry pinned at ten entries, the check on launch 11 and every launch after
#: it evaluates `10b + b > 16`, which only ever fires for `b > $1.45`. Anything
#: cheaper was unbounded. The count is now carried across the prune
#: (`_committed_pruned_usd`), so what is forgotten is the record, never the spend.
MAX_LAUNCHES_KEPT = 10
MAX_JOBS_KEPT = 20


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


# --- the cold-generate node recorder (DEVIATIONS 9.14) -----------------------
#
# §7's machine already emits one span per node with the input and output §17 step
# 4 needs. `run_pipeline` takes no callback, and `pipeline.py` is not modified, so
# the spans are observed instead: `obs.observation` is wrapped once at import and
# is INERT unless a cold-generate job set the ContextVar below. Outside a job it
# calls the original with the original arguments and returns what it returned.
#
# A ContextVar and not a module flag: an `asyncio.Task` gets a COPY of the
# context, so the recorder cannot leak into a concurrent request even though the
# patch itself is global.

_JOB: ContextVar[ColdJob | None] = ContextVar("storyforge_admin_cold_job", default=None)

#: Span fields worth publishing. `input` is deliberately absent for generation
#: nodes: it is the whole 400-word allowlist prompt, which is a demo of nothing.
_RECORDED_UPDATE_FIELDS = ("output", "level", "status_message")


def _usage_summary(usage: Any, on: date) -> dict[str, Any] | None:
    """A `pricing.Usage` reduced to what a progress view can show."""
    if usage is None:
        return None
    try:
        return {
            "model": usage.model,
            "input_tokens": usage.total_input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": round(usage.cost_usd(on), 6),
        }
    except Exception:  # noqa: BLE001 — an unpriced model costs a number, not a job
        return {"model": getattr(usage, "model", None)}


class _NodeProxy:
    """Stands in for an `obs.Span` for the duration of one node, recording as it
    delegates. Every method forwards; nothing here can change what the span does."""

    __slots__ = ("_entry", "_job", "_span")

    def __init__(self, span: Any, job: ColdJob, entry: dict[str, Any] | None) -> None:
        self._span = span
        self._job = job
        self._entry = entry

    @property
    def trace_id(self) -> str | None:
        return self._span.trace_id

    def update(self, **fields: Any) -> None:
        if self._entry is not None:
            self._job.record(self._entry, fields)
        self._span.update(**fields)


def _install_node_recorder() -> None:
    """Wrap `obs.observation` once. Idempotent; safe to call at every import."""
    current = getattr(obs, "observation", None)
    if current is None or getattr(current, "_storyforge_admin_recorder", False):
        return
    original = current

    @contextmanager
    def observation(name: str, *, as_type: str = "span", **kwargs: Any):
        job = _JOB.get()
        if job is None:
            # The overwhelmingly common path: every eval cell, every `make warm`
            # story, every kid request. One ContextVar read, then the original.
            with original(name, as_type=as_type, **kwargs) as span:
                yield span
            return

        entry = job.enter(name, kwargs.get("metadata"))
        try:
            with original(name, as_type=as_type, **kwargs) as span:
                if name == obs.TRACE_NAME:
                    job.note_trace(span.trace_id)
                yield _NodeProxy(span, job, entry)
        except BaseException as exc:
            job.leave(entry, error=f"{type(exc).__name__}: {exc}")
            raise
        job.leave(entry)

    observation._storyforge_admin_recorder = True  # type: ignore[attr-defined]
    obs.observation = observation  # type: ignore[assignment]


_install_node_recorder()


# --- cold-generate jobs ------------------------------------------------------
class ColdJob:
    """One cold generation, observed while it runs (DEVIATIONS 9.13, 9.15).

    Mutated in place by the recorder and read whole by the poll. Both happen on
    the one event-loop thread with no `await` between a mutation and a snapshot,
    so no lock is needed and none would help.
    """

    def __init__(
        self,
        *,
        job_id: str,
        stage_id: int,
        theme_slug: str,
        theme_id: int,
        effort: str,
        moderate: bool,
        started_by: str,
    ) -> None:
        self.job_id = job_id
        self.stage_id = stage_id
        self.theme_slug = theme_slug
        self.theme_id = theme_id
        self.effort = effort
        self.moderate = moderate
        self.started_by = started_by
        self.state: str = "queued"
        self.started_at = _now()
        self.finished_at: datetime | None = None
        self._t0 = time.perf_counter()
        self.trace_id: str | None = None
        self.transitions: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        #: Increments on EVERY mutation, so a client can tell a snapshot changed
        #: without diffing it. Not a cursor — see 9.13 on why there isn't one.
        self.revision = 0
        self._pricing_date = date.today()

    # -- recorder hooks (called from the wrapped `obs.observation`) -----------
    def _elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000

    def touch(self) -> None:
        """Bump `revision`. Every mutation goes through this."""
        self.revision += 1

    def enter(self, name: str, metadata: Any) -> dict[str, Any] | None:
        """Open a node. Returns None for the ROOT span, which is not a node."""
        self.touch()
        if name == obs.TRACE_NAME:
            return None
        meta = metadata if isinstance(metadata, dict) else {}
        entry: dict[str, Any] = {
            "seq": len(self.transitions) + 1,
            "node": name,
            "source": "pipeline",
            "status": "running",
            "attempt": meta.get("attempt"),
            "at": _iso(_now()),
            "elapsed_ms": round(self._elapsed_ms(), 2),
            "duration_ms": None,
            "detail": {},
            "error": None,
        }
        self.transitions.append(entry)
        return entry

    def record(self, entry: dict[str, Any], fields: dict[str, Any]) -> None:
        """A node's `span.update(...)` — where the demo's evidence actually is."""
        detail = entry["detail"]
        for key in _RECORDED_UPDATE_FIELDS:
            if key in fields and fields[key] is not None:
                detail[key if key != "output" else "output"] = fields[key]
        if "usage" in fields:
            detail["usage"] = _usage_summary(fields["usage"], self._pricing_date)
        self.touch()

    def leave(self, entry: dict[str, Any] | None, *, error: str | None = None) -> None:
        if entry is None:
            self.touch()
            return
        entry["status"] = "error" if error else "done"
        entry["error"] = error
        entry["duration_ms"] = round(self._elapsed_ms() - entry["elapsed_ms"], 2)
        self.touch()

    def note_trace(self, trace_id: str | None) -> None:
        """§15's id, published the moment the root span opens (DEVIATIONS 9.18)."""
        if trace_id:
            self.trace_id = trace_id
        self.touch()

    # -- job-level markers ---------------------------------------------------
    def mark(self, node: str, detail: dict[str, Any] | None = None) -> None:
        """A JOB-level marker, `source="job"`, never a §7 span.

        `RECEIVED` and the terminal outcome are facts about this endpoint, not
        observations of the state machine, and the field says which.
        """
        self.transitions.append(
            {
                "seq": len(self.transitions) + 1,
                "node": node,
                "source": "job",
                "status": "done",
                "attempt": None,
                "at": _iso(_now()),
                "elapsed_ms": round(self._elapsed_ms(), 2),
                "duration_ms": None,
                "detail": detail or {},
                "error": None,
            }
        )
        self.touch()

    @property
    def current_node(self) -> str | None:
        for entry in reversed(self.transitions):
            if entry["status"] == "running":
                return str(entry["node"])
        return None

    def snapshot(self) -> dict[str, Any]:
        settings = get_settings()
        return {
            "job_id": self.job_id,
            "state": self.state,
            "stage_id": self.stage_id,
            "theme": self.theme_slug,
            "theme_id": self.theme_id,
            "effort": self.effort,
            "moderated": self.moderate,
            "started_by": self.started_by,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "elapsed_ms": round(
                self._elapsed_ms()
                if self.finished_at is None
                else (self.finished_at - self.started_at).total_seconds() * 1000,
                2,
            ),
            "current_node": self.current_node,
            "revision": self.revision,
            "transitions": self.transitions,
            "result": self.result,
            "error": self.error,
            "langfuse": {
                "enabled": obs.enabled(),
                "trace_id": self.trace_id,
                "host": settings.langfuse_host,
                "url": None,
            },
            "persisted": False,
            "poll": f"/api/admin/cold-generate/{self.job_id}",
            "poll_after_ms": POLL_AFTER_MS,
            "notes": [
                "Nothing is written to `stories`, `story_segments` or `questions` "
                "(DEVIATIONS 9.15) — a warm row with no question is a dead-end "
                "session (8.50), so this story lives here and dies with the process.",
                "`source` separates a real §7 span from a job-level marker.",
                "No trace URL is invented: Langfuse's console path carries a project "
                "id this service never sees (9.18).",
            ],
        }


_jobs: OrderedDict[str, ColdJob] = OrderedDict()


def _prune(registry: OrderedDict[str, Any], keep: int, live: str) -> None:
    """Drop the oldest finished entries. A live one is never pruned."""
    while len(registry) > keep:
        for key, value in list(registry.items()):
            if getattr(value, "state", "") != live:
                del registry[key]
                break
        else:  # pragma: no cover — every entry is live
            return


def _cold_job_in_flight() -> ColdJob | None:
    for job in _jobs.values():
        if job.state in ("queued", "running"):
            return job
    return None


def _word_tokens(body: str) -> list[str]:
    """Lowercased word-ish tokens, for the rewrite-loop diff only."""
    out: list[str] = []
    token = []
    for ch in body.lower():
        if ch.isalpha() or ch == "'":
            token.append(ch)
        elif token:
            out.append("".join(token))
            token = []
    if token:
        out.append("".join(token))
    return out


def _rewrite_loop(result: PipelineResult) -> list[dict[str, Any]]:
    """§17 step 4 in machine-readable form.

    "The model tried 'gleaming', the validator caught it, the rewrite swapped it"
    is a diff between two consecutive attempts: the violations the previous attempt
    carried, which of them are GONE, and the words that appeared instead. Pure —
    derived from `PipelineResult.attempts`, which §7 already records.
    """
    rows: list[dict[str, Any]] = []
    previous = None
    for attempt in result.attempts:
        tokens = set(_word_tokens(attempt.text))
        row: dict[str, Any] = {
            "attempt": attempt.n,
            "kind": attempt.kind,
            "decodability": attempt.decodability,
            "violations": attempt.violations,
            "latency_ms": round(attempt.latency_ms, 2),
            "text": attempt.text,
            "caught": [],
            "fixed": [],
            "still_present": [],
            "new_words": [],
        }
        if previous is not None:
            caught = list(previous.violations)
            prior_tokens = set(_word_tokens(previous.text))
            row["caught"] = caught
            row["fixed"] = [w for w in caught if w.lower() not in tokens]
            row["still_present"] = [w for w in caught if w.lower() in tokens]
            row["new_words"] = sorted(tokens - prior_tokens)
        rows.append(row)
        previous = attempt
    return rows


def _outcome(result: PipelineResult) -> str:
    """§7's failure taxonomy, or `accepted`. Never `served` — nothing is served."""
    if result.passed:
        return "accepted"
    if result.moderation is not None and not result.moderation.approved:
        return "moderation_fail"
    return "budget_exhausted"


def _cold_result(result: PipelineResult) -> dict[str, Any]:
    on = date.today()
    report = result.final_report
    mod = result.moderation
    return {
        "outcome": _outcome(result),
        "passed": result.passed,
        "title": result.title,
        "text": result.final_text,
        "decodability": report.decodability if report else None,
        "gate": DECODABILITY_GATE,
        "meets_gate": bool(report and report.decodability >= DECODABILITY_GATE),
        "counts": dict(report.counts) if report else {},
        "violations": sorted({w.word for w in report.violations}) if report else [],
        "rewrite_depth": result.rewrite_depth,
        "max_rewrites": MAX_REWRITES,
        "attempts": len(result.attempts),
        "story_words": result.story_words,
        "max_story_words": MAX_STORY_WORDS,
        "moderation": (
            {
                "approved": mod.approved,
                "layer": mod.layer,
                "reason": mod.reason,
                "well_formed": mod.well_formed,
                "grammar_note": mod.grammar_note,
                "blocked_words": mod.blocked_words,
            }
            if mod is not None
            else None
        ),
        "cost_usd": round(result.total_cost_usd(on), 6),
        "pricing_date": on.isoformat(),
        "latency_ms": {
            "vocabulary": round(result.vocabulary_ms, 2),
            "generating": round(result.attempts[0].latency_ms, 2) if result.attempts else None,
            "rewriting": [round(a.latency_ms, 2) for a in result.attempts[1:]],
            "validating": [round(a.validate_ms, 3) for a in result.attempts],
            "moderating": (
                round(result.moderation_ms, 2) if result.moderation_ms is not None else None
            ),
            "total": round(result.total_ms, 2),
        },
        "langfuse_trace_id": result.langfuse_trace_id,
        "rewrite_loop": _rewrite_loop(result),
    }


async def _run_cold_job(job: ColdJob) -> None:
    """The background task. Sets the ContextVar so the recorder wakes up."""
    _JOB.set(job)
    job.state = "running"
    try:
        async with SessionLocal() as session:
            result = await run_pipeline(
                session,
                job.stage_id,
                job.theme_slug,
                effort=job.effort,
                moderate_output=job.moderate,
            )
        job.result = _cold_result(result)
        if result.langfuse_trace_id:
            job.trace_id = result.langfuse_trace_id
        job.state = "done"
        job.mark("OUTCOME", {"outcome": job.result["outcome"], "persisted": False})
    except BaseException as exc:  # noqa: BLE001 — the snapshot is the deliverable
        job.error = f"{type(exc).__name__}: {exc}"
        job.state = "error"
        # §7's taxonomy: a generation that never returned text is `api_fail`.
        job.mark("OUTCOME", {"outcome": "api_fail", "error": job.error})
        log.warning("cold-generate %s failed: %s", job.job_id, job.error)
    finally:
        job.finished_at = _now()
        job.touch()
        # §15: exports are batched on a background thread and a demo wants the
        # trace NOW. Off the event loop, after the response, so obs.py's "never on
        # the request path" holds (DEVIATIONS 9.18).
        try:
            await asyncio.to_thread(obs.flush)
        except Exception:  # noqa: BLE001 — telemetry never fails a job
            log.debug("cold-generate %s: langfuse flush failed", job.job_id, exc_info=True)


# --- eval-run launches -------------------------------------------------------
class EvalLaunch:
    """One `POST /api/admin/eval-runs` that actually spent money (9.8, 9.9)."""

    def __init__(
        self,
        *,
        launch_id: str,
        stages_spec: str,
        themes_spec: str,
        n: int,
        effort: str,
        concurrency: int,
        budget_usd: float,
        cells_planned: int,
        projection: dict[str, Any],
        baseline_run_id: int,
        started_by: str,
    ) -> None:
        self.launch_id = launch_id
        self.stages_spec = stages_spec
        self.themes_spec = themes_spec
        self.n = n
        self.effort = effort
        self.concurrency = concurrency
        self.budget_usd = budget_usd
        self.cells_planned = cells_planned
        self.projection = projection
        self.baseline_run_id = baseline_run_id
        self.started_by = started_by
        self.state = "running"
        self.run_id: int | None = None
        self.exit_code: int | None = None
        self.error: str | None = None
        self.started_at = _now()
        self.finished_at: datetime | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "launch_id": self.launch_id,
            "run_id": self.run_id,
            "state": self.state,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "elapsed_s": round(
                (
                    (self.finished_at or _now()) - self.started_at
                ).total_seconds(),
                1,
            ),
            "stages_spec": self.stages_spec,
            "themes_spec": self.themes_spec,
            "n": self.n,
            "effort": self.effort,
            "concurrency": self.concurrency,
            "budget_usd": self.budget_usd,
            "cells_planned": self.cells_planned,
            "projection": self.projection,
            "exit_code": self.exit_code,
            "exit_meaning": (
                None if self.exit_code is None else _EXIT_MEANING.get(self.exit_code)
            ),
            "error": self.error,
            "started_by": self.started_by,
            "ephemeral": True,
        }


#: `eval.run_full`'s documented exit codes, so the portal reads them the same way
#: the Makefile comment does.
_EXIT_MEANING: dict[int, str] = {
    0: "PASSED — §1 item 2 met across the whole matrix",
    1: "criteria not met, or the run aborted",
    2: "spend cap reached, matrix incomplete",
    3: "a --resume was refused because the live config differs from the stored one",
}

_launches: OrderedDict[str, EvalLaunch] = OrderedDict()

#: Authorised dollars belonging to launches `_prune` has already dropped.
#:
#: DEVIATIONS 14.30. `_committed_usd` is documented as "committed budget across
#: every launch in THIS process", and it summed `_launches` — a registry capped at
#: `MAX_LAUNCHES_KEPT`. Every prune therefore *refunded* the pruned launch's
#: budget to the ceiling it was supposed to have consumed, and
#: `process_remaining_usd` went UP after a launch. Monotonic on purpose: this
#: number may only ever grow within a process, exactly like the ceiling it feeds.
_committed_pruned_usd: float = 0.0


def _reset_launch_registry() -> None:
    """Both halves of the launch record, cleared together. Tests only.

    Kept as one function because clearing `_launches` alone would leave the carry
    behind and make one test's spend bind the next test's ceiling — the mirror
    image of the defect this carry exists to fix.
    """
    global _committed_pruned_usd
    _launches.clear()
    _committed_pruned_usd = 0.0


def _prune_launches() -> None:
    """`_prune`, plus the one thing a launch leaves behind when it is forgotten.

    The dollars are added to `_committed_pruned_usd` BEFORE the entry is dropped,
    so there is no window in which a launch is neither in the registry nor in the
    carry — `_committed_usd` is called from a request handler and from the portal
    poll, and a total that dips would be the same lie in miniature.
    """
    global _committed_pruned_usd
    while len(_launches) > MAX_LAUNCHES_KEPT:
        for key, value in list(_launches.items()):
            if value.state != "running":
                _committed_pruned_usd += value.budget_usd
                del _launches[key]
                break
        else:  # pragma: no cover — every entry is live
            return


def _eval_launch_in_flight() -> EvalLaunch | None:
    for launch in _launches.values():
        if launch.state == "running":
            return launch
    return None


def _committed_usd() -> float:
    """Every dollar this process has authorised, whether or not it still has the row.

    The sum is over the retained launches PLUS the carry from pruned ones. It is
    what was authorised, not what was spent — the conservative direction, and the
    only one available while a run is in flight (DEVIATIONS 9.6, 14.30).
    """
    return round(_committed_pruned_usd + sum(launch.budget_usd for launch in _launches.values()), 4)


_MAX_RUN_ID_SQL = text("SELECT coalesce(max(id), 0) FROM eval_runs")
_NEW_RUN_ID_SQL = text("SELECT min(id) FROM eval_runs WHERE id > :baseline")


async def _discover_run_id(launch: EvalLaunch, task: asyncio.Task[int]) -> None:
    """Watch for the row `run_full` creates for itself (DEVIATIONS 9.8)."""
    while launch.run_id is None:
        await asyncio.sleep(0.4)
        try:
            async with SessionLocal() as session:
                found = (
                    await session.execute(_NEW_RUN_ID_SQL, {"baseline": launch.baseline_run_id})
                ).scalar()
            if found is not None:
                launch.run_id = int(found)
                return
        except Exception:  # noqa: BLE001 — discovery is a convenience, not the run
            log.debug("launch %s: run id lookup failed", launch.launch_id, exc_info=True)
        if task.done():
            return


async def _run_eval_launch(launch: EvalLaunch, pricing_date: date | None) -> None:
    task = asyncio.ensure_future(
        run_full(
            stages_spec=launch.stages_spec,
            themes_spec=launch.themes_spec,
            n=launch.n,
            effort=launch.effort,
            cold=True,
            budget_usd=launch.budget_usd,
            concurrency=launch.concurrency,
            pricing_date=pricing_date or date.today(),
            estimate_only=False,
            # ENRICHING is deliberately not run by the harness (DEVIATIONS 7.45).
            enrich=False,
            out_dir=None,
            resume_run_id=None,
        )
    )
    watcher = asyncio.ensure_future(_discover_run_id(launch, task))
    try:
        launch.exit_code = await task
        launch.state = "done"
    except BaseException as exc:  # noqa: BLE001 — the row is the durable record
        launch.error = f"{type(exc).__name__}: {exc}"
        launch.state = "error"
        log.warning("eval launch %s failed: %s", launch.launch_id, launch.error)
    finally:
        watcher.cancel()
        launch.finished_at = _now()
        if launch.run_id is None:
            try:
                async with SessionLocal() as session:
                    found = (
                        await session.execute(
                            _NEW_RUN_ID_SQL, {"baseline": launch.baseline_run_id}
                        )
                    ).scalar()
                if found is not None:
                    launch.run_id = int(found)
            except Exception:  # noqa: BLE001
                log.debug("launch %s: final run id lookup failed", launch.launch_id)
        try:
            await asyncio.to_thread(obs.flush)
        except Exception:  # noqa: BLE001
            log.debug("launch %s: langfuse flush failed", launch.launch_id, exc_info=True)


# --- GET /api/admin/eval-runs ------------------------------------------------
#
# Targeted jsonb extraction rather than `SELECT config, metrics`: run 114's
# `metrics` is §14's whole aggregate and its `config` carries the provenance
# hashes and the vocabulary fingerprint. Five of those to render a five-row table
# is the reason DEVIATIONS 9.3 exists. Every field the summary and the §1 verdict
# need is pulled by name, so `criteria_verdict` still decides the verdict.
_RUNS_SQL = text(
    """
    SELECT r.id, r.started_at, r.finished_at, r.status,
           r.config -> 'stages'          AS cfg_stages,
           r.config -> 'themes'          AS cfg_themes,
           r.config -> 'n'               AS cfg_n,
           r.config ->> 'effort'         AS cfg_effort,
           r.config -> 'cold'            AS cfg_cold,
           r.config ->> 'pricing_date'   AS cfg_pricing_date,
           r.config -> 'models'          AS cfg_models,
           r.config -> 'gate'            AS cfg_gate,
           r.config -> 'budget_usd'      AS cfg_budget_usd,
           r.config -> 'concurrency'     AS cfg_concurrency,
           r.config ->> 'git_commit'     AS cfg_git_commit,
           r.config -> 'cells_planned'   AS cfg_cells_planned,
           r.metrics -> 'cells_planned'  AS m_cells_planned,
           r.metrics -> 'attempted'      AS m_attempted,
           r.metrics -> 'produced'       AS m_produced,
           r.metrics -> 'accepted'       AS m_accepted,
           r.metrics -> 'gate_pass_rate' AS m_gate_pass_rate,
           r.metrics -> 'fallback_rate'  AS m_fallback_rate,
           r.metrics -> 'outcomes'       AS m_outcomes,
           r.metrics -> 'decodability' -> 'mean'       AS m_dec_mean,
           r.metrics -> 'decodability' -> 'min'        AS m_dec_min,
           r.metrics -> 'decodability' -> 'below_gate' AS m_below_gate,
           r.metrics -> 'rewrite_depth' -> 'p95'       AS m_rw_p95,
           r.metrics -> 'rewrite_depth' -> 'max'       AS m_rw_max,
           r.metrics -> 'cost' -> 'total_usd'          AS m_cost_usd,
           r.metrics -> 'wall_clock_s'                 AS m_wall_clock_s,
           r.metrics -> 'pipeline_seconds'             AS m_pipeline_seconds,
           (SELECT count(*) FROM eval_run_items i WHERE i.run_id = r.id) AS item_count
    FROM eval_runs r
    ORDER BY r.id DESC
    LIMIT :limit OFFSET :offset
    """
)

_RUNS_COUNT_SQL = text("SELECT count(*) FROM eval_runs")
_STALE_RUNNING_SQL = text("SELECT id FROM eval_runs WHERE status = 'running' ORDER BY id")


def _as_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _as_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _criteria_from(row: Any) -> list[dict[str, Any]] | None:
    """§1 item 2's verdict, from `eval.criteria_verdict` and nowhere else.

    Reconstructs the minimum aggregate that function reads, so the portal's
    PASS/FAIL is the same computation `make eval`'s exit code is (§14, 9.10).
    `None` when the run recorded no metrics — a `running` row has none, and an
    invented verdict about it would be worse than an absent one.
    """
    if row.m_attempted is None or row.m_produced is None:
        return None
    agg = {
        "decodability": {"below_gate": _as_int(row.m_below_gate) or 0},
        "rewrite_depth": {
            "p95": _as_float(row.m_rw_p95) or 0,
            "max": _as_float(row.m_rw_max) or 0,
        },
        "produced": _as_int(row.m_produced) or 0,
        "attempted": _as_int(row.m_attempted) or 0,
        "cells_planned": _as_int(row.m_cells_planned)
        or _as_int(row.cfg_cells_planned)
        or 0,
        "outcomes": row.m_outcomes or {},
    }
    try:
        return [
            {"name": name, "ok": bool(ok), "evidence": evidence}
            for name, ok, evidence in criteria_verdict(agg)
        ]
    except Exception:  # noqa: BLE001 — an unreadable metrics blob is not a 500
        return None


def _summary(row: Any) -> dict[str, Any]:
    criteria = _criteria_from(row)
    return {
        "id": int(row.id),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "status": row.status,
        "item_count": int(row.item_count),
        "cells_planned": _as_int(row.m_cells_planned) or _as_int(row.cfg_cells_planned),
        "stages": row.cfg_stages or [],
        "themes": row.cfg_themes or [],
        "n": _as_int(row.cfg_n),
        "effort": row.cfg_effort,
        "cold": row.cfg_cold,
        "concurrency": _as_int(row.cfg_concurrency),
        "pricing_date": row.cfg_pricing_date,
        "models": row.cfg_models or {},
        "gate": _as_float(row.cfg_gate),
        "budget_usd": _as_float(row.cfg_budget_usd),
        "git_commit": row.cfg_git_commit,
        "attempted": _as_int(row.m_attempted),
        "produced": _as_int(row.m_produced),
        "accepted": _as_int(row.m_accepted),
        "gate_pass_rate": _as_float(row.m_gate_pass_rate),
        "fallback_rate": _as_float(row.m_fallback_rate),
        "below_gate": _as_int(row.m_below_gate),
        "decodability_mean": _as_float(row.m_dec_mean),
        "decodability_min": _as_float(row.m_dec_min),
        "rewrite_depth_p95": _as_float(row.m_rw_p95),
        "rewrite_depth_max": _as_float(row.m_rw_max),
        "cost_usd": _as_float(row.m_cost_usd),
        "wall_clock_s": _as_float(row.m_wall_clock_s),
        "pipeline_seconds": _as_float(row.m_pipeline_seconds),
        "criteria": criteria,
        "criteria_passed": None if criteria is None else all(c["ok"] for c in criteria),
    }


async def _budget_block(session: AsyncSession) -> dict[str, Any]:
    stale = [int(r[0]) for r in (await session.execute(_STALE_RUNNING_SQL)).all()]
    in_flight = _eval_launch_in_flight()
    committed = _committed_usd()
    return {
        "per_request_ceiling_usd": MAX_BUDGET_USD,
        "process_ceiling_usd": PROCESS_BUDGET_USD,
        "process_committed_usd": committed,
        "process_remaining_usd": round(max(0.0, PROCESS_BUDGET_USD - committed), 4),
        "cli_default_budget_usd": DEFAULT_BUDGET_USD,
        "in_flight_launch_id": None if in_flight is None else in_flight.launch_id,
        # `eval_runs` rows still marked `running`. Reported, never blocking: one
        # of them is a 2026-07-30 run interrupted before it wrote an item, and a
        # busy check keyed on the database would 409 forever (DEVIATIONS 9.7).
        "stale_running_run_ids": stale,
        "max_cells": MAX_CELLS,
        "max_n": MAX_N,
        "max_concurrency": MAX_CONCURRENCY,
    }


@router.get("/eval-runs")
async def list_eval_runs(
    limit: int = Query(LIST_DEFAULT_LIMIT, ge=1, le=LIST_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """§9: `GET /api/admin/eval-runs`. Newest first.

    An envelope, not an array (DEVIATIONS 9.2): pagination needs a `total`, and
    this process's launches ride along so the portal learns the id of the run it
    started on the poll it was already making.
    """
    total = (await session.execute(_RUNS_COUNT_SQL)).scalar_one()
    rows = (await session.execute(_RUNS_SQL, {"limit": limit, "offset": offset})).all()
    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "runs": [_summary(r) for r in rows],
        "launches": [launch.snapshot() for launch in reversed(_launches.values())],
        "budget": await _budget_block(session),
    }


# --- POST /api/admin/eval-runs -----------------------------------------------
class EvalRunRequest(BaseModel):
    """§14's command line as a body, with the spend gate on top (9.4).

    The defaults are §14's own matrix (`1..11`, `all`, 5) so the documented run is
    the one the portal describes. Safety comes from `confirm` and `budget_usd`
    being absent by default, not from a small default matrix — a portal whose
    default is a smoke run would describe a matrix nobody can start.
    """

    stages: str = DEFAULT_STAGES
    themes: str = DEFAULT_THEMES
    n: int = Field(default=DEFAULT_N, ge=1, le=MAX_N)
    effort: Literal["low", "medium", "high"] = "medium"
    concurrency: int = Field(default=1, ge=1, le=MAX_CONCURRENCY)
    pricing_date: date | None = None
    #: REQUIRED to spend. Absent ⇒ estimate, and no API call is made.
    budget_usd: float | None = Field(default=None, gt=0)
    #: Must be true to spend. False ⇒ estimate.
    confirm: bool = False


_REFERENCE_RUN_SQL = text(
    """
    SELECT id,
           metrics -> 'cost' -> 'total_usd' AS cost_usd,
           metrics -> 'wall_clock_s'        AS wall_clock_s,
           metrics -> 'accepted'            AS accepted
    FROM eval_runs
    WHERE status = 'completed'
      AND config -> 'cells_planned' = to_jsonb(cast(:cells AS int))
    ORDER BY id DESC
    LIMIT 1
    """
)


async def _plan(session: AsyncSession, body: EvalRunRequest) -> dict[str, Any]:
    """Everything decidable with no API call: the matrix, the projection, the
    reference run. `parse_stages` and `load_vocabulary` both raise `SystemExit`
    on bad input — a `BaseException` that would leave a request handler and could
    take the worker with it — so both are converted to 4xx here."""
    try:
        stages = parse_stages(body.stages)
    except SystemExit as exc:
        raise HTTPException(status_code=422, detail=f"bad stages spec: {exc}") from exc
    # DEVIATIONS 17.2: the bound AND the message derive from the decoder. The
    # message was its own hardcoded copy of the ceiling and a test pinned the
    # literal string, so a renumber left both saying 11 with a green suite.
    if any(s < MIN_STAGE or s > MAX_STAGE for s in stages):
        raise HTTPException(
            status_code=422,
            detail=f"stages must be within {MIN_STAGE}..{MAX_STAGE} (§4)",
        )

    rows = (await session.execute(text("SELECT id, slug FROM themes"))).all()
    known = {slug: tid for tid, slug in rows}
    if body.themes.strip() == "all":
        themes = sorted(known)
    else:
        themes = [t.strip() for t in body.themes.split(",") if t.strip()]
    unknown = [t for t in themes if t not in known]
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"unknown theme(s) {unknown} — run `make seed`"
        )
    if not themes:
        raise HTTPException(status_code=422, detail="--themes selected no themes")

    cells = len(stages) * len(themes) * body.n
    if cells > MAX_CELLS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{cells} cells exceeds the {MAX_CELLS}-cell ceiling (§14's matrix is "
                f"11 x 3 x 5). Shrink the matrix, or run the CLI: "
                f"`make eval EVAL_ARGS=\"--budget N\"`."
            ),
        )

    on = body.pricing_date or date.today()
    try:
        vocabs = [await load_vocabulary(session, s, themes[0]) for s in stages]
    except SystemExit as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    projection = project_cost(vocabs, body.n * len(themes), on)

    reference = (await session.execute(_REFERENCE_RUN_SQL, {"cells": cells})).first()
    return {
        "stages": stages,
        "themes": themes,
        "cells_planned": cells,
        "pricing_date": on,
        "projection": projection,
        "reference_run": (
            None
            if reference is None
            else {
                "id": int(reference.id),
                "cost_usd": _as_float(reference.cost_usd),
                "wall_clock_s": _as_float(reference.wall_clock_s),
                "accepted": _as_int(reference.accepted),
            }
        ),
    }


@router.post("/eval-runs")
async def create_eval_run(
    body: EvalRunRequest,
    response: Response,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """§9: `POST /api/admin/eval-runs`. **Estimate unless told twice.**

    This is the one route in the service that spends money on a click, so the
    order below is the safety argument (DEVIATIONS 9.4-9.7):

    1. no `budget_usd`, or `confirm` false → 200, a projection, and zero API calls;
    2. `confirm` true with no `budget_usd` → 422, because a confirmation of an
       unbounded run is not a confirmation;
    3. `budget_usd` over `MAX_BUDGET_USD` → 422 with both numbers;
    4. projection over the requested budget → 422 before a single call (`run_full`
       would exit 2 here; a caller deserves the HTTP error instead);
    5. a launch already in flight → 409;
    6. this process's committed budget exhausted → 409.

    Only then is the run started, and `run_full`'s own cap — the RUNNING ACTUAL
    after every story (DEVIATIONS 7.44) — is what finally binds.
    """
    plan = await _plan(session, body)
    budget_block = await _budget_block(session)
    projection = plan["projection"]
    likely = float(projection["with_one_rewrite_each_usd"])

    common: dict[str, Any] = {
        "stages": plan["stages"],
        "themes": plan["themes"],
        "n": body.n,
        "effort": body.effort,
        "concurrency": body.concurrency,
        "cells_planned": plan["cells_planned"],
        "pricing_date": plan["pricing_date"].isoformat(),
        "projection": projection,
        "reference_run": plan["reference_run"],
        "budget": budget_block,
        "gate": DECODABILITY_GATE,
        "max_rewrites": MAX_REWRITES,
        "prompt_word_limit": PROMPT_WORD_LIMIT,
    }

    if not body.confirm or body.budget_usd is None:
        if body.confirm and body.budget_usd is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "confirm=true requires an explicit budget_usd — a confirmation "
                    "of an unbounded run is not a confirmation (DEVIATIONS 9.4). "
                    f"The ceiling is ${MAX_BUDGET_USD:.2f}; the projection for this "
                    f"matrix is ${likely:.2f} with one rewrite each."
                ),
            )
        return {
            **common,
            "mode": "estimate",
            "launched": False,
            "requires": {
                "budget_usd": (
                    f"> {likely:.4f} (the one-rewrite-each projection) and "
                    f"<= {MAX_BUDGET_USD}"
                ),
                "confirm": True,
            },
            "detail": (
                "No API call was made. Send the same body again with "
                f"`confirm: true` and a `budget_usd` between ${likely:.2f} and "
                f"${MAX_BUDGET_USD:.2f} to start the run."
            ),
        }

    budget = float(body.budget_usd)
    if budget > MAX_BUDGET_USD:
        raise HTTPException(
            status_code=422,
            detail=(
                f"budget_usd ${budget:.2f} exceeds this endpoint's ${MAX_BUDGET_USD:.2f} "
                f"ceiling (DEVIATIONS 9.5). The CLI's own default is "
                f"${DEFAULT_BUDGET_USD:.2f} and stays reachable from a shell: "
                f'`make eval EVAL_ARGS="--budget {budget:g}"`.'
            ),
        )
    if likely > budget:
        raise HTTPException(
            status_code=422,
            detail=(
                f"the projection for {plan['cells_planned']} cells is ${likely:.2f} "
                f"with one rewrite each, over the ${budget:.2f} you authorised. "
                "Raise budget_usd or shrink the matrix — this is refused before any "
                "API call, exactly as `run_full` would exit 2."
            ),
        )
    busy = _eval_launch_in_flight()
    if busy is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"eval launch {busy.launch_id} is still running (run_id="
                f"{busy.run_id}) — one at a time per process (DEVIATIONS 9.7)."
            ),
        )
    committed = _committed_usd()
    if committed + budget > PROCESS_BUDGET_USD:
        raise HTTPException(
            status_code=409,
            detail=(
                f"this API process has already committed ${committed:.2f} of its "
                f"${PROCESS_BUDGET_USD:.2f} cumulative ceiling (DEVIATIONS 9.6); "
                f"${budget:.2f} more would exceed it. Restart the process, or run "
                "the CLI deliberately."
            ),
        )

    baseline = int((await session.execute(_MAX_RUN_ID_SQL)).scalar_one())
    launch = EvalLaunch(
        launch_id=f"el_{secrets.token_hex(4)}",
        stages_spec=body.stages,
        themes_spec=body.themes,
        n=body.n,
        effort=body.effort,
        concurrency=body.concurrency,
        budget_usd=budget,
        cells_planned=plan["cells_planned"],
        projection=projection,
        baseline_run_id=baseline,
        started_by=principal.clerk_id,
    )
    _launches[launch.launch_id] = launch
    # NOT `_prune`: dropping a launch row must not drop its authorised dollars
    # out of `_committed_usd` (DEVIATIONS 14.30).
    _prune_launches()
    log.warning(
        "ADMIN EVAL LAUNCH %s by %s — %d cells, budget $%.2f, projection $%.2f",
        launch.launch_id,
        principal.clerk_id,
        launch.cells_planned,
        budget,
        likely,
    )
    asyncio.ensure_future(_run_eval_launch(launch, body.pricing_date))

    response.status_code = status.HTTP_202_ACCEPTED
    return {
        **common,
        "mode": "launch",
        "launched": True,
        "budget_usd": budget,
        "launch": launch.snapshot(),
        "poll": "/api/admin/eval-runs",
        "poll_after_ms": 2000,
        "detail": (
            "Started. `run_id` is null until the harness has written its own "
            "`eval_runs` row (DEVIATIONS 9.8) — poll GET /api/admin/eval-runs and "
            "read it off `launches[]`. The launch record is in-process and is lost "
            "on restart; the `eval_runs` row is the durable one, and "
            "`--resume RUN_ID` is the recovery path."
        ),
    }


# --- GET /api/admin/eval-runs/{id} -------------------------------------------
_RUN_DETAIL_SQL = text(
    """
    SELECT r.id, r.started_at, r.finished_at, r.status, r.config, r.metrics,
           (SELECT count(*) FROM eval_run_items i WHERE i.run_id = r.id) AS item_count
    FROM eval_runs r
    WHERE r.id = :id
    """
)

_RUN_ITEMS_SQL = text(
    """
    SELECT id, story_id, metrics
    FROM eval_run_items
    WHERE run_id = :id
    ORDER BY id
    LIMIT :limit OFFSET :offset
    """
)


@router.get("/eval-runs/{run_id}")
async def get_eval_run(
    run_id: int,
    items_limit: int = Query(ITEMS_DEFAULT_LIMIT, ge=0, le=ITEMS_MAX_LIMIT),
    items_offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """§9: `GET /api/admin/eval-runs/{id}` — "detail with the metrics tables".

    `metrics` is §14's aggregate VERBATIM: `by_stage`, `by_theme`, the decodability
    bins, `below_gate_detail`, the moderation reject lists, the latency split. The
    portal renders it; it does not recompute any of it (§14: the CLI is canonical).

    `items` is paginated (DEVIATIONS 9.10) because 180 per-story metric rows is the
    one part of this response that grows without bound, and `metrics.by_stage`
    already carries the per-stage aggregates §11's tables need.

    A nonexistent id is a plain 404. DEVIATIONS 6.9's wrong-owner/nonexistent
    indistinguishability does not apply: an `eval_runs` row has no owner, so there
    is no "someone else's run" whose existence a status code could confirm (9.11).
    """
    row = (await session.execute(_RUN_DETAIL_SQL, {"id": run_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"eval run {run_id} not found")

    metrics = row.metrics or {}
    criteria: list[dict[str, Any]] | None
    try:
        criteria = [
            {"name": name, "ok": bool(ok), "evidence": evidence}
            for name, ok, evidence in criteria_verdict(metrics)
        ]
    except Exception:  # noqa: BLE001 — a `running` row has no metrics to judge
        criteria = None

    items: list[dict[str, Any]] = []
    if items_limit:
        items = [
            {"id": int(r.id), "story_id": _as_int(r.story_id), "metrics": r.metrics or {}}
            for r in (
                await session.execute(
                    _RUN_ITEMS_SQL,
                    {"id": run_id, "limit": items_limit, "offset": items_offset},
                )
            ).all()
        ]

    return {
        "id": int(row.id),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "status": row.status,
        "config": row.config or {},
        "metrics": metrics,
        "criteria": criteria,
        "criteria_passed": None if criteria is None else all(c["ok"] for c in criteria),
        "items_total": int(row.item_count),
        "items_limit": items_limit,
        "items_offset": items_offset,
        "items": items,
        "report_command": (
            "services/api/.venv/bin/python scripts/eval_doc.py "
            "reports/eval-<ts>.json docs/EVAL.md"
        ),
    }


# --- GET /api/admin/allowed-words?stage=N ------------------------------------
#
# §5.4 expects 150-2,500 words per stage. The renumbered table (DEVIATIONS 12.x)
# runs 771 at stage 1 to ~4,400 at stage 11, so this route is
# page-MANDATORY rather than page-capable (DEVIATIONS 9.12): `limit` defaults to
# 100 and is capped at 500.
_WORDS_SQL_TEMPLATE = """
    SELECT word, syllables, freq_rank
    FROM allowed_words
    WHERE stage_id = :stage {filter}
    ORDER BY {order}
    LIMIT :limit OFFSET :offset
"""
_WORDS_COUNT_TEMPLATE = """
    SELECT count(*) FROM allowed_words WHERE stage_id = :stage {filter}
"""
_WORDS_ORDER = {
    # `freq_rank` first is the order §6 draws the prompt allowlist in, so page 1
    # is literally what the generator sees.
    "freq": "freq_rank ASC, word ASC",
    "alpha": "word ASC, freq_rank ASC",
}

#: The `freq_rank` of the 400th word at this stage — §6's `PROMPT_WORD_LIMIT`
#: boundary. One small indexed query, and it is what makes "every word a stage-2
#: reader may see" and "the 400 the model is actually shown" comparable in one
#: screen (DEVIATIONS 9.12).
_PROMPT_CUTOFF_SQL = text(
    """
    SELECT max(freq_rank) FROM (
        SELECT freq_rank FROM allowed_words WHERE stage_id = :stage
        ORDER BY freq_rank LIMIT :n
    ) top_n
    """
)

_STAGE_SQL = text("SELECT id, name, description, max_syllables FROM stages WHERE id = :id")
_SIGHT_COUNT_SQL = text("SELECT count(*) FROM sight_words WHERE stage_id <= :stage")


def _like_pattern(raw: str) -> str:
    """A literal substring match. `%`, `_` and `\\` typed into the demo's search
    box are characters, not wildcards."""
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/allowed-words")
async def list_allowed_words(
    stage: int = Query(..., ge=MIN_STAGE, le=MAX_STAGE),
    limit: int = Query(WORDS_DEFAULT_LIMIT, ge=1, le=WORDS_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=64),
    order: Literal["freq", "alpha"] = "freq",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """§9: `GET /api/admin/allowed-words?stage=N` `(admin, paginated)`.

    §17 step 2's screen: "every word a stage-2 reader is permitted to see —
    machine-derived". `total` is the honest count for the filter, `stage_total` the
    count for the stage, and `in_prompt` marks the top `PROMPT_WORD_LIMIT` by
    frequency — the subset §6 actually puts in the generation prompt.
    """
    stage_row = (await session.execute(_STAGE_SQL, {"id": stage})).first()
    if stage_row is None:
        raise HTTPException(
            status_code=404, detail=f"stage {stage} not seeded — run `make seed`"
        )

    needle = q.strip()
    filter_sql = " AND word LIKE :pattern" if needle else ""
    params: dict[str, Any] = {"stage": stage, "limit": limit, "offset": offset}
    count_params: dict[str, Any] = {"stage": stage}
    if needle:
        params["pattern"] = _like_pattern(needle.lower())
        count_params["pattern"] = params["pattern"]

    total = (
        await session.execute(
            text(_WORDS_COUNT_TEMPLATE.format(filter=filter_sql)), count_params
        )
    ).scalar_one()
    stage_total = (
        total
        if not needle
        else (
            await session.execute(
                text(_WORDS_COUNT_TEMPLATE.format(filter="")), {"stage": stage}
            )
        ).scalar_one()
    )
    rows = (
        await session.execute(
            text(
                _WORDS_SQL_TEMPLATE.format(filter=filter_sql, order=_WORDS_ORDER[order])
            ),
            params,
        )
    ).all()
    cutoff = (
        await session.execute(_PROMPT_CUTOFF_SQL, {"stage": stage, "n": PROMPT_WORD_LIMIT})
    ).scalar()
    sight_count = (await session.execute(_SIGHT_COUNT_SQL, {"stage": stage})).scalar_one()

    return {
        "stage": {
            "id": int(stage_row.id),
            "name": stage_row.name,
            "description": stage_row.description,
            "max_syllables": int(stage_row.max_syllables),
        },
        "total": int(total),
        "stage_total": int(stage_total),
        "limit": limit,
        "offset": offset,
        "order": order,
        "q": needle,
        "has_more": offset + len(rows) < int(total),
        "next_offset": (offset + limit) if offset + len(rows) < int(total) else None,
        # Cumulative sight words at or below this stage (§4). Counted, not listed:
        # they are a different allowlist with a different rule, and conflating the
        # two is how "every word a stage-2 reader may see" becomes ambiguous.
        "sight_word_count": int(sight_count),
        "prompt_word_limit": PROMPT_WORD_LIMIT,
        "prompt_freq_rank_cutoff": _as_int(cutoff),
        "words": [
            {
                "word": r.word,
                "syllables": int(r.syllables),
                "freq_rank": int(r.freq_rank),
                "in_prompt": cutoff is not None and int(r.freq_rank) <= int(cutoff),
            }
            for r in rows
        ],
    }


# --- cold generate (§11's "generate cold story" button, DEVIATIONS 9.13-9.18) --
class ColdGenerateRequest(BaseModel):
    """§11: "generate cold story button that shows live pipeline state transitions".

    **There is no `kid_id` here and there will not be** (DEVIATIONS 9.16). An admin
    is not an owner (6.10); a "generate for this child" field would be the first
    admin route to read a `kids` row, and it would read one the admin does not own.
    `POST /api/stories/request`'s admin-only `stage_id` preview knob (7.21) is the
    precedent: a stage with no child in the room.
    """

    stage_id: int = Field(..., ge=MIN_STAGE, le=MAX_STAGE)
    #: Slug or id, resolved against `themes`.
    theme: int | str
    effort: Literal["low", "medium", "high"] = "medium"
    #: §7 puts MODERATING between the gate passing and PERSISTED. On by default so
    #: the demo shows the whole machine; a Haiku call is ~$0.001.
    moderate: bool = True


_THEME_SQL = text(
    """
    SELECT id, slug FROM themes
    WHERE slug = :slug OR id = :id
    ORDER BY (slug = :slug) DESC
    LIMIT 1
    """
)


@router.post("/cold-generate")
async def cold_generate(
    body: ColdGenerateRequest,
    response: Response,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Start one cold generation and return the first snapshot (202).

    Poll `GET /api/admin/cold-generate/{job_id}` — polling and not SSE, for the
    four reasons in DEVIATIONS 9.13, of which the decisive one is that
    `EventSource` cannot send an `Authorization` header and this route is
    `require_admin`.

    One job at a time per process (409 otherwise). That is also what bounds the
    spend: ~$0.03 per story and a repeated click cannot outrun one generation
    (9.17). Nothing is persisted (9.15).
    """
    stage_row = (await session.execute(_STAGE_SQL, {"id": body.stage_id})).first()
    if stage_row is None:
        raise HTTPException(
            status_code=404, detail=f"stage {body.stage_id} not seeded — run `make seed`"
        )
    raw = str(body.theme).strip()
    theme_row = (
        await session.execute(
            _THEME_SQL,
            {"slug": raw.lower(), "id": int(raw) if raw.isdigit() else None},
        )
    ).first()
    if theme_row is None:
        raise HTTPException(status_code=404, detail=f"unknown theme {raw!r} — run `make seed`")

    busy = _cold_job_in_flight()
    if busy is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cold generation {busy.job_id} is already {busy.state} "
                f"(node {busy.current_node}) — one at a time, which is what bounds the "
                f"spend (DEVIATIONS 9.17). Poll /api/admin/cold-generate/{busy.job_id}."
            ),
        )

    job = ColdJob(
        job_id=f"cg_{secrets.token_hex(4)}",
        stage_id=body.stage_id,
        theme_slug=str(theme_row.slug),
        theme_id=int(theme_row.id),
        effort=body.effort,
        moderate=body.moderate,
        started_by=principal.clerk_id,
    )
    # Marked HERE and not in the task: `RECEIVED` is a fact about the request, so
    # the first snapshot the caller gets already carries it rather than an empty
    # chain that fills in a moment later.
    job.mark("RECEIVED", {"stage_id": job.stage_id, "theme": job.theme_slug})
    _jobs[job.job_id] = job
    _prune(_jobs, MAX_JOBS_KEPT, "running")
    log.info(
        "ADMIN COLD GENERATE %s by %s — stage %d / %s (persists nothing)",
        job.job_id,
        principal.clerk_id,
        job.stage_id,
        job.theme_slug,
    )
    asyncio.ensure_future(_run_cold_job(job))

    response.status_code = status.HTTP_202_ACCEPTED
    return job.snapshot()


@router.get("/cold-generate/{job_id}")
async def get_cold_generate(job_id: str) -> dict[str, Any]:
    """The poll. A complete snapshot every time — see DEVIATIONS 9.13(b).

    404 for an id this process does not hold, which includes every id from before
    the last restart. There is no owner here to conceal and therefore no
    enumeration oracle (9.11): a job id is 8 hex characters minted by this process
    and reachable only by an admin.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"cold-generate job {job_id!r} not found — jobs live in this API "
                "process only and are lost on restart (DEVIATIONS 9.9, 9.15)"
            ),
        )
    return job.snapshot()
