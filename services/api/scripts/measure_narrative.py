"""Measure whether the generation prompt produces STORIES, before and after.

The defect this exists for: *"the stories don't feel the need to follow any sort of
storyline. Instead, it's more like 'Adam sat, Adam ran'"* — reported from the live
app at stage 2 AND at stage 6, and invisible to every gate in the system because
§1 item 2 measures decodability and rewrite depth, `passes_gate` measures
decodability, OOV and the story-word cap, and §13's judge is told that plain
repetitive prose is the format working. See `docs/DEVIATIONS.md` 11.40-11.59.

**Why this is not `scripts/measure_length.py`.** That script measures ONE generate
call, deliberately, because the quantity under test there was the generation
prompt's word count. Two of the quantities under test here — **rewrite depth** and
**cost per accepted story** — do not exist upstream of the gate, so this one calls
`run_pipeline`: the same function `warm` and `eval` call, gate, rewrite loop,
moderation and all.

**Both arms run from ONE checkout, and the `before` arm is reconstructed rather
than checked out.** `--arm before` restores `SYSTEM_RULES`' original padding clause
by string replacement and stubs `build_narrative_block` to "", then ASSERTS that
both edits actually landed. A before-arm that silently measured the after-prompt
would be the exact false green this repo keeps logging (4.12's discarded verdict,
8.90's fixture, 9.60's quiet guards), so the reconstruction refuses to run rather
than run wrong. Two other agents are editing this tree, so a `git stash` arm would
be measuring their in-flight work as well as mine.

Judging is a SEPARATE subcommand from generating. The ruler and the sample must be
able to move independently: `measure_length.py --revalidate` exists for the same
reason, because two arms scored by two different rulers are not a comparison.

    # 1. generate both arms (spends money, ~$0.9 an arm at the default matrix)
    .venv/bin/python scripts/measure_narrative.py run --arm before --json /tmp/before.json
    .venv/bin/python scripts/measure_narrative.py run --arm after  --json /tmp/after.json

    # 2. score both with ONE ruler (spends a little; ~$0.002 a story)
    .venv/bin/python scripts/measure_narrative.py judge /tmp/before.json /tmp/after.json

    # 3. read the comparison (free, no API call)
    .venv/bin/python scripts/measure_narrative.py compare /tmp/before.json /tmp/after.json
    .venv/bin/python scripts/measure_narrative.py text /tmp/after.json --stages 1,2,6
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storyforge.config import get_settings  # noqa: E402
from storyforge.db import SessionLocal  # noqa: E402
from storyforge.eval import word_tokens  # noqa: E402 — the canonical word count
from storyforge.llm import client as client_mod  # noqa: E402
from storyforge.llm import generate as generate_mod  # noqa: E402
from storyforge.llm.narrative import SCORE_MAX, judge_narrative, narrative_signals  # noqa: E402
from storyforge.pipeline import (  # noqa: E402
    DECODABILITY_GATE,
    MAX_STORY_WORDS,
    run_pipeline,
)

#: See `measure_length.py`: measuring through a timeout the long stages already
#: exceed censors the sample in the direction of the thing being measured.
MEASURE_TIMEOUT_S = 240.0

#: `make eval` runs at medium, so the measurement does too. Effort is a real lever
#: on instruction-following, and a number no eval run can reproduce is not evidence.
DEFAULT_EFFORT = "medium"

#: 1 and 2 are the two stages §18 pre-excuses, 6 is the stage the user complained
#: about with thousands of allowed words and no excuse available, 8 and 11 are the 120-200
#: word band where the padding clause bites hardest.
DEFAULT_STAGES = (1, 2, 5, 8, 11)
DEFAULT_THEMES = ("animals", "dragons", "space")

#: The `before` arm's reconstruction, verbatim on both sides. DEVIATIONS 11.41: the
#: proximate cause of the defect was not a missing instruction but this present one.
#: Byte-exact on both sides, hard line breaks included, verified against
#: `git show HEAD:…/llm/generate.py` — so the `before` arm's rule 8 is character
#: for character the rule 8 that produced the stories the user complained about,
#: not a paraphrase of it.
AFTER_CLAUSE = """do not send the character off to do
   something else: go back and give them more trouble with the SAME problem — a
   second try, a setback, someone who helps — so the extra words are still the
   story you started. Count"""
BEFORE_CLAUSE = """give a character something else to do.
   Count"""


def install_before_arm() -> None:
    """Reconstruct the pre-change prompt in this process, and prove it.

    Both edits are asserted. A `before` arm that quietly measured the `after`
    prompt would report "no change" as a null result and bury the whole finding, so
    every failure here is a `SystemExit` and never a warning.
    """
    if AFTER_CLAUSE not in generate_mod.SYSTEM_RULES:
        raise SystemExit(
            "before-arm reconstruction FAILED: AFTER_CLAUSE is not in SYSTEM_RULES. "
            "The prompt has moved on; update AFTER_CLAUSE/BEFORE_CLAUSE in this "
            "script rather than measuring the wrong thing."
        )
    generate_mod.SYSTEM_RULES = generate_mod.SYSTEM_RULES.replace(AFTER_CLAUSE, BEFORE_CLAUSE)
    if BEFORE_CLAUSE not in generate_mod.SYSTEM_RULES:
        raise SystemExit("before-arm reconstruction FAILED: replacement did not land")
    generate_mod.build_narrative_block = lambda allowed_words: ""
    blocks = generate_mod.generation_system_blocks("Stage 1", ["cat", "sat"], ["the"])
    if len(blocks) != 2 or generate_mod.NARRATIVE_HEADER in json.dumps(blocks):
        raise SystemExit(
            f"before-arm reconstruction FAILED: {len(blocks)} system block(s) and the "
            f"narrative header is still present"
        )
    if "cache_control" not in blocks[-1]:
        raise SystemExit("before-arm reconstruction FAILED: no cache breakpoint")
    print("  before-arm reconstruction verified: old padding clause in, narrative block out")


def install_after_arm() -> None:
    """Assert the arm under test is actually installed. The mirror of the above."""
    if BEFORE_CLAUSE in generate_mod.SYSTEM_RULES:
        raise SystemExit("after arm: SYSTEM_RULES still holds the old padding clause")
    blocks = generate_mod.generation_system_blocks("Stage 1", ["cat", "sat"], ["the"])
    if generate_mod.NARRATIVE_HEADER not in json.dumps(blocks):
        raise SystemExit("after arm: the narrative block is not in the system prompt")
    print("  after-arm verified: new padding clause in, narrative block present")


async def one_cell(session, stage_id: int, theme: str, pricing_on: date, effort: str) -> dict:
    """One FULL pipeline run — generate, gate, rewrite loop, moderation.

    The narrative judge is NOT run here. Scoring is a separate subcommand so both
    arms can be scored by one ruler after the fact, and so a judge change never
    requires re-generating a sample that cost real money.
    """
    base = {"stage_id": stage_id, "theme": theme}
    t0 = time.perf_counter()
    try:
        r = await run_pipeline(session, stage_id, theme, effort=effort)
    except Exception as exc:  # noqa: BLE001 — §7 calls this api_fail; record it
        return {
            **base,
            "failed": True,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }
    report = r.final_report
    n_words = len(word_tokens(report.words)) if report else 0
    target = None
    async with SessionLocal() as s2:  # the vocabulary the run used, for the bounds
        from storyforge.pipeline import load_vocabulary

        v = await load_vocabulary(s2, stage_id, theme)
        target = [v.target_min, v.target_max]
    mod = r.moderation
    return {
        **base,
        "failed": False,
        "target": target,
        "passed": r.passed,
        "text": r.final_text,
        "title": r.title,
        "word_count": n_words,
        "length_compliant": target[0] <= n_words <= target[1],
        "under_target": n_words < target[0],
        "over_target": n_words > target[1],
        "decodability": report.decodability if report else 0.0,
        "meets_gate": bool(report and report.decodability >= DECODABILITY_GATE),
        "oov": report.counts.get("oov", 0) if report else 0,
        "distinct_story_words": len(report.distinct_story_words) if report else 0,
        "rewrite_depth": r.rewrite_depth,
        # First-draft decodability, separately: the rewrite loop can absorb a
        # decodability regression and report the same final number at a higher cost,
        # so the first draft is where a prompt change is actually visible.
        "first_draft_decodability": r.attempts[0].decodability if r.attempts else 0.0,
        "moderation_approved": bool(mod and mod.approved),
        "moderation_layer": mod.layer if mod else None,
        "moderation_reason": mod.reason if mod else None,
        "well_formed": bool(mod.well_formed) if mod else None,
        "cost_usd": r.total_cost_usd(pricing_on),
        "generation_cost_usd": r.generation_cost_usd(pricing_on),
        "moderation_cost_usd": r.moderation_cost_usd(pricing_on),
        "output_tokens": sum(a.usage.output_tokens for a in r.attempts),
        "latency_ms": round((time.perf_counter() - t0) * 1000),
        "signals": narrative_signals(r.final_text).model_dump(),
    }


def _line(r: dict) -> str:
    if r["failed"]:
        return f"  stage {r['stage_id']:>2} {r['theme']:<8} api_fail  {r['error']}"
    lo, hi = r["target"]
    mark = "ok " if r["length_compliant"] else ("SHORT" if r["under_target"] else "LONG ")
    return (
        f"  stage {r['stage_id']:>2} {r['theme']:<8} {'PASS' if r['passed'] else 'fail'} "
        f"{r['word_count']:>4}w ({lo}-{hi}) {mark:<5} dec {r['decodability']:.3f} "
        f"rw {r['rewrite_depth']} sw {r['distinct_story_words']} "
        f"{r['latency_ms']:>6}ms ${r['cost_usd']:.4f}"
    )


async def run(stages, themes, n: int, pricing_on: date, effort: str) -> list[dict]:
    """First cell of a stage alone, the rest of the stage in parallel.

    Cache economics, not politeness (the argument `measure_length.py` makes): the
    system prefix is only readable from cache once a first response has begun, so
    firing a whole stage at once would make every cell pay full input price and
    misreport the cost of the change.
    """
    out: list[dict] = []
    async with SessionLocal() as session:
        for stage_id in stages:
            cells = [(stage_id, t) for t in themes for _ in range(n)]
            r = await one_cell(session, *cells[0], pricing_on, effort)
            out.append(r)
            print(_line(r), flush=True)

            async def _cell(cell):
                async with SessionLocal() as s2:
                    return await one_cell(s2, *cell, pricing_on, effort)

            for r in await asyncio.gather(*(_cell(c) for c in cells[1:])):
                out.append(r)
                print(_line(r), flush=True)
    return out


# --- judging (a separate pass, over stored text) -------------------------------


async def judge_files(paths: list[str], runs: int, pricing_on: date) -> None:
    """Score every stored story with ONE ruler and write the scores back.

    `runs > 1` because the judge is stochastic (STATE invariant 5's discipline
    applied to a new model-judged check): the recorded score is the MEDIAN of the
    runs, and the spread is recorded beside it so a reader can see how much of a
    difference is noise.
    """
    cost = 0.0
    for path in paths:
        payload = json.loads(Path(path).read_text())
        items = [i for i in payload["items"] if not i["failed"]]
        print(f"\n{path}: judging {len(items)} stories x {runs} run(s)", flush=True)
        per_item: list[list] = [[] for _ in items]
        for r in range(runs):
            verdicts = await asyncio.gather(*(judge_narrative(i["text"]) for i in items))
            for slot, v in zip(per_item, verdicts, strict=True):
                slot.append(v)
                if v.usage:
                    cost += v.usage.cost_usd(pricing_on)
            print(f"  run {r + 1}/{runs} done", flush=True)
        for item, verdicts in zip(items, per_item, strict=True):
            scored = [v.score for v in verdicts if v.score > 0]
            errs = [v.error for v in verdicts if v.error]
            item["narrative"] = {
                "score": statistics.median(scored) if scored else None,
                "scores": [v.score for v in verdicts],
                "spine": verdicts[-1].spine,
                "has_want": verdicts[-1].has_want,
                "has_obstacle": verdicts[-1].has_obstacle,
                "has_change": verdicts[-1].has_change,
                "errors": errs,
            }
        payload["judge_runs"] = runs
        payload["judge_cost_usd"] = round(cost, 4)
        Path(path).write_text(json.dumps(payload, indent=1))
        print(f"  wrote scores back into {path}")
    print(f"\njudging cost ${cost:.4f}")


# --- summaries ----------------------------------------------------------------


def _scores(items: list[dict]) -> list[float]:
    return [
        i["narrative"]["score"]
        for i in items
        if i.get("narrative") and i["narrative"].get("score")
    ]


def summarise(all_items: list[dict]) -> dict:
    """Aggregate the PRODUCED cells; api_fails are reported on their own line.

    Never mixed, for the reason `docs/EVAL.md` section 2 keeps them apart: a cell
    that produced nothing and a story that came back short are different failures,
    and averaging over the survivors while calling it a quality figure is a false
    green.
    """
    failures = [i for i in all_items if i["failed"]]
    items = [i for i in all_items if not i["failed"]]
    if not items:
        return {"n": 0, "attempted": len(all_items), "failures": len(failures)}
    accepted = [i for i in items if i["passed"]]
    depths = sorted(i["rewrite_depth"] for i in items)

    def q95(xs: list) -> float:
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(len(xs) * 0.95))]

    per_stage = {}
    for stage_id in sorted({i["stage_id"] for i in items}):
        g = [i for i in items if i["stage_id"] == stage_id]
        sc = _scores(g)
        acc = [i for i in g if i["passed"]]
        per_stage[str(stage_id)] = {
            "n": len(g),
            "accepted": len(acc),
            "target": g[0]["target"],
            "mean_words": round(statistics.mean(i["word_count"] for i in g), 1),
            "length_compliance": sum(i["length_compliant"] for i in g) / len(g),
            "mean_decodability": round(statistics.mean(i["decodability"] for i in g), 4),
            "min_decodability": round(min(i["decodability"] for i in g), 4),
            "below_gate": sum(1 for i in g if not i["meets_gate"]),
            "rw_p95": q95([i["rewrite_depth"] for i in g]),
            "narrative_mean": round(statistics.mean(sc), 2) if sc else None,
            "narrative_scores": sorted(sc),
            "cost_per_accepted": round(sum(i["cost_usd"] for i in g) / len(acc), 4)
            if acc
            else None,
        }
    sc_all = _scores(items)
    return {
        "n": len(items),
        "attempted": len(all_items),
        "failures": len(failures),
        "failure_reasons": sorted({i["error"] for i in failures}),
        "accepted": len(accepted),
        "acceptance_rate": len(accepted) / len(items),
        "mean_decodability": round(statistics.mean(i["decodability"] for i in items), 4),
        "median_decodability": round(statistics.median(i["decodability"] for i in items), 4),
        "min_decodability": round(min(i["decodability"] for i in items), 4),
        "below_gate": sum(1 for i in items if not i["meets_gate"]),
        "mean_first_draft_decodability": round(
            statistics.mean(i["first_draft_decodability"] for i in items), 4
        ),
        "first_drafts_below_gate": sum(
            1 for i in items if i["first_draft_decodability"] < DECODABILITY_GATE
        ),
        "rewrite_depth_p50": statistics.median(depths),
        "rewrite_depth_p95": q95(depths),
        "rewrite_depth_max": max(depths),
        "rewrite_depth_mean": round(statistics.mean(depths), 3),
        # §1's budget is p95 <= 2 and DEVIATIONS 9.44 records the DECODABILITY-driven
        # p95 as 1, so the headroom is one rewrite. The raw p95 mixes in rewrites the
        # gate demanded for the story-word cap, which a narrative change is not
        # supposed to move and which are not what §1's budget is about. Both are
        # reported: a story whose FIRST draft already met the 95% threshold is
        # counted at depth 0 here, because whatever sent it round again, it was not
        # decodability.
        "rw_p95_decodability_driven": q95(
            [
                0 if i["first_draft_decodability"] >= DECODABILITY_GATE else i["rewrite_depth"]
                for i in items
            ]
        ),
        "rewrites_first_draft_already_decodable": sum(
            1
            for i in items
            if i["rewrite_depth"] > 0 and i["first_draft_decodability"] >= DECODABILITY_GATE
        ),
        "length_compliance": sum(i["length_compliant"] for i in items) / len(items),
        "over_target": sum(i["over_target"] for i in items),
        "under_target": sum(i["under_target"] for i in items),
        "oov_instances": sum(i["oov"] for i in items),
        "over_story_word_cap": sum(
            1 for i in items if i["distinct_story_words"] > MAX_STORY_WORDS
        ),
        "zero_story_words": sum(1 for i in items if i["distinct_story_words"] == 0),
        "moderation_rejects": sum(1 for i in items if not i["moderation_approved"]),
        "ungrammatical": sum(1 for i in items if i["well_formed"] is False),
        "total_cost_usd": round(sum(i["cost_usd"] for i in items), 4),
        "cost_per_accepted": round(sum(i["cost_usd"] for i in items) / len(accepted), 4)
        if accepted
        else None,
        "output_tokens_mean": round(statistics.mean(i["output_tokens"] for i in items)),
        "narrative_n": len(sc_all),
        "narrative_mean": round(statistics.mean(sc_all), 3) if sc_all else None,
        "narrative_median": statistics.median(sc_all) if sc_all else None,
        "narrative_hist": {
            str(s): sum(1 for x in sc_all if round(x) == s) for s in range(1, SCORE_MAX + 1)
        }
        if sc_all
        else None,
        "signals_opener_share_mean": round(
            statistics.mean(i["signals"]["top_opener_share"] for i in items), 4
        ),
        "signals_late_novelty_mean": round(
            statistics.mean(i["signals"]["late_novelty"] for i in items), 4
        ),
        "per_stage": per_stage,
    }


def print_summary(s: dict, label: str) -> None:
    print(f"\n=== {label} ===")
    print(
        f"attempted={s['attempted']}  produced={s['n']}  api_fail={s['failures']}"
        + (f"  {s.get('failure_reasons')}" if s.get("failure_reasons") else "")
    )
    if not s["n"]:
        return
    print(
        f"  accepted {s['accepted']}/{s['n']} ({s['acceptance_rate']:.1%})   "
        f"moderation rejects {s['moderation_rejects']} (ungrammatical {s['ungrammatical']})"
    )
    print(
        f"  decodability mean {s['mean_decodability']:.4f} median "
        f"{s['median_decodability']:.4f} min {s['min_decodability']:.4f}  "
        f"below gate {s['below_gate']}"
    )
    print(
        f"  first-draft decodability mean {s['mean_first_draft_decodability']:.4f}  "
        f"first drafts below gate {s['first_drafts_below_gate']}/{s['n']}"
    )
    print(
        f"  rewrite depth mean {s['rewrite_depth_mean']} p50 {s['rewrite_depth_p50']} "
        f"p95 {s['rewrite_depth_p95']} max {s['rewrite_depth_max']}"
    )
    print(
        f"  length compliance {s['length_compliance']:.1%}  "
        f"(short {s['under_target']}, long {s['over_target']})"
    )
    print(
        f"  OOV {s['oov_instances']}  over story-word cap {s['over_story_word_cap']}  "
        f"zero story words {s['zero_story_words']}"
    )
    print(
        f"  cost ${s['total_cost_usd']:.4f}  ${s['cost_per_accepted']}/accepted  "
        f"output tokens mean {s['output_tokens_mean']}"
    )
    if s.get("narrative_mean") is not None:
        print(
            f"  NARRATIVE mean {s['narrative_mean']} median {s['narrative_median']} "
            f"(n={s['narrative_n']}, 1-{SCORE_MAX})  hist {s['narrative_hist']}"
        )
    print(
        f"  signals: same-opening share {s['signals_opener_share_mean']:.3f}  "
        f"late-third novelty {s['signals_late_novelty_mean']:.3f}"
    )
    print(
        f"  {'stage':>5} {'target':>8} {'n':>3} {'acc':>4} {'words':>6} {'len%':>6} "
        f"{'dec':>7} {'min':>7} {'<gate':>6} {'rwp95':>6} {'narr':>5}  scores"
    )
    for stage_id, v in s["per_stage"].items():
        lo, hi = v["target"]
        print(
            f"  {stage_id:>5} {f'{lo}-{hi}':>8} {v['n']:>3} {v['accepted']:>4} "
            f"{v['mean_words']:>6.1f} {v['length_compliance']:>5.0%} "
            f"{v['mean_decodability']:>7.4f} {v['min_decodability']:>7.4f} "
            f"{v['below_gate']:>6} {v['rw_p95']:>6} "
            f"{str(v['narrative_mean']):>5}  {v['narrative_scores']}"
        )


def compare(before: dict, after: dict) -> None:
    b, a = before["summary"], after["summary"]
    print("\n=== before -> after ===")

    def row(label, key, fmt="{}"):
        bv, av = b.get(key), a.get(key)
        print(f"  {label:<34} {fmt.format(bv):>10} -> {fmt.format(av):>10}")

    row("produced / attempted", "n")
    row("api_fail", "failures")
    row("accepted", "accepted")
    row("mean decodability", "mean_decodability", "{:.4f}")
    row("min decodability", "min_decodability", "{:.4f}")
    row("stories below the 95% gate", "below_gate")
    row("mean FIRST-DRAFT decodability", "mean_first_draft_decodability", "{:.4f}")
    row("first drafts below the gate", "first_drafts_below_gate")
    row("rewrite depth mean", "rewrite_depth_mean")
    row("rewrite depth p95 (raw)", "rewrite_depth_p95")
    row("rewrite depth p95 (decodability)", "rw_p95_decodability_driven")
    row("rewrites, 1st draft decodable", "rewrites_first_draft_already_decodable")
    row("rewrite depth max", "rewrite_depth_max")
    row("length compliance", "length_compliance", "{:.1%}")
    row("OOV instances", "oov_instances")
    row("over the story-word cap", "over_story_word_cap")
    row("stories with zero story words", "zero_story_words")
    row("moderation rejects", "moderation_rejects")
    row("cost per accepted story", "cost_per_accepted", "${}")
    row("mean output tokens", "output_tokens_mean")
    row("NARRATIVE mean (1-5)", "narrative_mean")
    row("same-opening share", "signals_opener_share_mean", "{:.3f}")
    print("\n  per stage: narrative mean, then decodability mean, then length compliance")
    for stage_id in a["per_stage"]:
        bs = b["per_stage"].get(stage_id)
        as_ = a["per_stage"][stage_id]
        if not bs:
            continue
        print(
            f"    stage {stage_id:>2}  narrative {str(bs['narrative_mean']):>5} -> "
            f"{str(as_['narrative_mean']):>5}   dec {bs['mean_decodability']:.4f} -> "
            f"{as_['mean_decodability']:.4f}   len {bs['length_compliance']:>5.0%} -> "
            f"{as_['length_compliance']:>5.0%}   rw p95 {bs['rw_p95']} -> {as_['rw_p95']}"
        )


def show_text(path: str, stages: list[int] | None, limit: int) -> None:
    """Print the actual stories. This is a quality change; the text IS the evidence."""
    payload = json.loads(Path(path).read_text())
    shown = 0
    for i in payload["items"]:
        if i["failed"] or (stages and i["stage_id"] not in stages):
            continue
        nar = i.get("narrative") or {}
        print(
            f"\n--- {payload['label']} · stage {i['stage_id']} · {i['theme']} · "
            f"{i['word_count']}w · dec {i['decodability']:.3f} · rw {i['rewrite_depth']} · "
            f"narrative {nar.get('score')} {nar.get('scores', '')}"
        )
        if nar.get("spine"):
            print(f"    spine: {nar['spine']}")
        print(f'    "{i["title"]}"\n    {i["text"]}')
        shown += 1
        if limit and shown >= limit:
            return


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="generate an arm through the full pipeline")
    r.add_argument("--arm", choices=("before", "after"), required=True)
    r.add_argument("--stages", default=",".join(str(s) for s in DEFAULT_STAGES))
    r.add_argument("--themes", default=",".join(DEFAULT_THEMES))
    r.add_argument("--n", type=int, default=2, help="stories per (stage, theme)")
    r.add_argument("--label", default=None)
    r.add_argument("--json", required=True)
    r.add_argument("--effort", default=DEFAULT_EFFORT)
    r.add_argument("--timeout", type=float, default=MEASURE_TIMEOUT_S)
    r.add_argument("--pricing-date", default=date.today().isoformat())

    j = sub.add_parser("judge", help="score stored stories with one ruler")
    j.add_argument("paths", nargs="+")
    j.add_argument("--runs", type=int, default=3)
    j.add_argument("--pricing-date", default=date.today().isoformat())

    c = sub.add_parser("compare", help="before/after table, no API call")
    c.add_argument("before")
    c.add_argument("after")

    t = sub.add_parser("text", help="print the stories themselves, no API call")
    t.add_argument("path")
    t.add_argument("--stages", default=None)
    t.add_argument("--limit", type=int, default=0)

    a = p.parse_args()

    if a.cmd == "judge":
        asyncio.run(judge_files(a.paths, a.runs, date.fromisoformat(a.pricing_date)))
        return
    if a.cmd == "compare":
        before = json.loads(Path(a.before).read_text())
        after = json.loads(Path(a.after).read_text())
        # Re-summarise both arms from their stored items rather than reading the
        # summaries written at generation time. One ruler over both samples, always:
        # a summariser improved between the two runs would otherwise compare two
        # different metrics and call the difference an effect. Same argument as
        # `measure_length.py --revalidate` makes for the decoder.
        before["summary"] = summarise(before["items"])
        after["summary"] = summarise(after["items"])
        print_summary(before["summary"], f"before ({before['label']})")
        print_summary(after["summary"], f"after ({after['label']})")
        compare(before, after)
        return
    if a.cmd == "text":
        stages = [int(s) for s in a.stages.split(",")] if a.stages else None
        show_text(a.path, stages, a.limit)
        return

    # `ANTHROPIC_GENERATION_TIMEOUT_S` is the sanctioned override (DEVIATIONS 8.5)
    # and is read per call, so no production constant is touched. Only ever raises.
    settings = get_settings()
    configured = settings.anthropic_generation_timeout_s or client_mod.GENERATION_TIMEOUT_S
    if a.timeout > configured:
        os.environ["ANTHROPIC_GENERATION_TIMEOUT_S"] = str(a.timeout)
        get_settings.cache_clear()

    if a.arm == "before":
        install_before_arm()
    else:
        install_after_arm()

    stages = [int(s) for s in a.stages.split(",") if s.strip()]
    themes = [t.strip() for t in a.themes.split(",") if t.strip()]
    label = a.label or a.arm
    print(f"arm={a.arm} stages={stages} themes={themes} n={a.n} effort={a.effort}")
    items = asyncio.run(run(stages, themes, a.n, date.fromisoformat(a.pricing_date), a.effort))
    summary = summarise(items)
    print_summary(summary, label)
    Path(a.json).write_text(
        json.dumps({"label": label, "arm": a.arm, "items": items, "summary": summary}, indent=1)
    )
    print(f"\nwrote {a.json}")


if __name__ == "__main__":
    main()
