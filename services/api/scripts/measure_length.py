"""Measure the GENERATION prompt's length compliance against the live model.

docs/EVAL.md section 12 reports 52.2% length compliance overall and 6.7-13.3% at
stages 9-12, where §6 asks for 120-200 words and the model returned a mean of
89-102. That is a defect in `llm/generate.py`, not in the decoder — a short story
is still a decodable story, so the §7 gate never sees it.

`storyforge.eval` is the canonical harness and stays canonical: this script does
NOT reimplement its metric. It imports `eval.word_tokens` and reuses
`pipeline.validate_draft`, so "word count" and "decodability" here mean exactly
what they mean in docs/EVAL.md. What it deliberately does differently:

  * **One GENERATE call per cell, no rewrite loop and no moderation.** The
    quantity under test is what the generation prompt produces. Folding in the
    rewrite loop would let a rewrite's length change absorb the signal, and
    moderation is a content verdict that has nothing to say about length.
  * **A generous timeout, via `ANTHROPIC_GENERATION_TIMEOUT_S`.** A length
    measurement taken through a timeout the long stages already exceed censors the
    sample in the direction of the thing being measured: the longest stories die,
    and a prompt producing MORE text would score BETTER on the survivors. The
    environment variable is the sanctioned override (DEVIATIONS 8.5), so no module
    global is monkeypatched and no production constant is touched. It is a no-op
    when the configured budget is already larger.
  * **Decodability is reported alongside**, because §0's rule is that length must
    never be bought with decodability. A length fix that moves the first-draft
    decodability distribution down is a regression however good the word counts.
  * **`--revalidate`, which re-scores stored texts with no API call.** The decoder
    is a pure function of the GPC inventory, and that inventory is under active
    change (a `wh` digraph landed between this measurement's before and after
    runs). Two runs taken either side of an inventory change are NOT comparable on
    decodability: a larger inventory can only raise it, which would mask exactly
    the regression the decodability column exists to catch. Re-scoring both files
    against one decoder restores the comparison, and costs nothing because the
    validator is pure.

Usage:

    .venv/bin/python scripts/measure_length.py --stages 1,4,5,8,9,10,11,12 --n 2
    .venv/bin/python scripts/measure_length.py --json /tmp/after.json
    .venv/bin/python scripts/measure_length.py --compare /tmp/before.json /tmp/after.json
    .venv/bin/python scripts/measure_length.py --revalidate /tmp/before.json /tmp/after.json
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

from storyforge import pipeline as pipeline_mod  # noqa: E402
from storyforge.config import get_settings  # noqa: E402
from storyforge.db import SessionLocal  # noqa: E402
from storyforge.eval import word_tokens  # noqa: E402  — the canonical word count
from storyforge.llm import client as client_mod  # noqa: E402
from storyforge.llm.generate import build_request_block  # noqa: E402
from storyforge.pipeline import (  # noqa: E402
    DECODABILITY_GATE,
    draft_text,
    load_vocabulary,
    passes_gate,
    validate_draft,
)

#: Long stories legitimately take longer than §7's 30s generation timeout. See
#: the module docstring: measuring through that timeout censors the sample in the
#: direction of the thing being measured.
MEASURE_TIMEOUT_S = 240.0

#: `make eval` runs at `--effort medium`, so the measurement does too. Effort is
#: a real lever on instruction-following and therefore on length; measuring at a
#: different one would report a number no eval run can reproduce.
DEFAULT_EFFORT = "medium"

DEFAULT_STAGES = (1, 4, 5, 8, 9, 10, 11, 12)
DEFAULT_THEMES = ("animals", "dragons", "space")


async def one_cell(session, stage_id: int, theme: str, pricing_on: date, effort: str) -> dict:
    """One GENERATING node, through `pipeline._call` — the node production uses.

    Deliberately NOT `llm.generate.generate_story`. That function has no caller
    outside this script (grep: `warm.py` and `eval.py` both go through
    `run_pipeline`, which calls `pipeline._call`), so a measurement taken through
    it would be evidence about a code path no child's story ever travels. The two
    assemble the same prompt today; measuring the live one means they cannot
    silently drift apart and leave this harness reporting on the wrong prompt.
    """
    vocab = await load_vocabulary(session, stage_id, theme)
    user_block = build_request_block(theme, vocab.story_words, vocab.target_min, vocab.target_max)
    base = {"stage_id": stage_id, "theme": theme, "target": [vocab.target_min, vocab.target_max]}
    t0 = time.perf_counter()
    try:
        draft, usage = await pipeline_mod._call(vocab, theme, user_block, effort)
    except Exception as exc:  # noqa: BLE001
        # §7 counts this as api_fail and the warm pool absorbs it. Recording it
        # as a datum rather than letting it kill the run is the whole point: a
        # matrix that dies at cell 26 of 48 throws away the evidence for 25 cells
        # (the argument DEVIATIONS 7.47 makes about the eval harness).
        return {
            **base,
            "failed": True,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }
    latency_ms = (time.perf_counter() - t0) * 1000
    body = draft_text(draft)
    report = validate_draft(vocab, body, draft.story_words_used)
    n_words = len(word_tokens(report.words))
    return {
        **base,
        "failed": False,
        "word_count": n_words,
        "length_compliant": vocab.target_min <= n_words <= vocab.target_max,
        "under_target": n_words < vocab.target_min,
        "over_target": n_words > vocab.target_max,
        "decodability": report.decodability,
        "meets_gate": report.decodability >= DECODABILITY_GATE,
        "passes_gate": passes_gate(report),
        "oov": report.counts.get("oov", 0),
        "distinct_story_words": len(report.distinct_story_words),
        "cost_usd": usage.cost_usd(pricing_on),
        # Output tokens, separately from cost, because they are the thing the
        # length change actually moves and cost mixes them with cached input. A
        # 120-200 word story is only ~250-350 tokens of text, so a much larger
        # figure here is thinking, not story — and that distinction decides
        # whether a cost rise is the spec being met or the prompt being wasteful.
        "output_tokens": usage.output_tokens,
        "latency_ms": round(latency_ms),
        "text": body,
    }


async def run(stages, themes, n: int, pricing_on: date, effort: str) -> list[dict]:
    """Sequential within a stage's FIRST call, parallel for the rest of the stage.

    Cache economics, not politeness: the vocabulary block is the cached prefix and
    a cache entry is only readable once the first response has begun. Firing every
    cell of a stage at once would make all of them pay full input price (see the
    prompt-caching rules), which would misreport the cost of the change.
    """
    out: list[dict] = []
    async with SessionLocal() as session:
        for stage_id in stages:
            cells = [(stage_id, t) for t in themes for _ in range(n)]
            first, rest = cells[0], cells[1:]
            r = await one_cell(session, *first, pricing_on, effort)
            out.append(r)
            print(_line(r), flush=True)
            if rest:
                # Fresh sessions: asyncio.gather over one AsyncSession would
                # interleave queries on a single connection.
                async def _cell(stage_and_theme):
                    async with SessionLocal() as s2:
                        return await one_cell(s2, *stage_and_theme, pricing_on, effort)

                for r in await asyncio.gather(*(_cell(c) for c in rest)):
                    out.append(r)
                    print(_line(r), flush=True)
    return out


def _line(r: dict) -> str:
    lo, hi = r["target"]
    if r["failed"]:
        return f"  stage {r['stage_id']:>2} {r['theme']:<8} api_fail  {r['error']}"
    mark = "ok " if r["length_compliant"] else ("SHORT" if r["under_target"] else "LONG ")
    return (
        f"  stage {r['stage_id']:>2} {r['theme']:<8} {r['word_count']:>4} words "
        f"({lo}-{hi}) {mark:<5} dec {r['decodability']:.3f} "
        f"{'GATE' if r['passes_gate'] else 'fail'} "
        f"{r['latency_ms']:>6}ms ${r['cost_usd']:.4f}"
    )


def summarise(all_items: list[dict]) -> dict:
    """Aggregate the PRODUCED cells, and report the failures on their own line.

    The two are never mixed. `docs/EVAL.md` section 2 keeps "the matrix completed"
    and "every cell produced a story" on separate lines for the same reason: an
    api_fail and a short story are different failures, and averaging over only the
    survivors while calling it a length figure is the false green §0.5 forbids.
    """
    failures = [i for i in all_items if i["failed"]]
    items = [i for i in all_items if not i["failed"]]
    if not items:
        return {"n": 0, "attempted": len(all_items), "failures": len(failures)}

    def band(lo: int) -> list[dict]:
        return [i for i in items if i["target"][0] == lo]

    per_stage = {}
    for stage_id in sorted({i["stage_id"] for i in items}):
        g = [i for i in items if i["stage_id"] == stage_id]
        per_stage[stage_id] = {
            "n": len(g),
            "target": g[0]["target"],
            "mean_words": round(statistics.mean(i["word_count"] for i in g), 1),
            "min_words": min(i["word_count"] for i in g),
            "max_words": max(i["word_count"] for i in g),
            "compliance": sum(i["length_compliant"] for i in g) / len(g),
            "under": sum(i["under_target"] for i in g),
            "over": sum(i["over_target"] for i in g),
            "mean_decodability": round(statistics.mean(i["decodability"] for i in g), 4),
            "min_decodability": round(min(i["decodability"] for i in g), 4),
            "gate_pass": sum(i["passes_gate"] for i in g) / len(g),
            "p95_latency_ms": round(
                statistics.quantiles([i["latency_ms"] for i in g], n=20)[-1]
                if len(g) > 1
                else g[0]["latency_ms"]
            ),
        }
    return {
        "n": len(items),
        "attempted": len(all_items),
        "failures": len(failures),
        "failure_reasons": sorted({i["error"] for i in failures}),
        "compliance_overall": sum(i["length_compliant"] for i in items) / len(items),
        "by_band": {
            f"{lo}-{items_[0]['target'][1]}": {
                "n": len(items_),
                "compliance": sum(i["length_compliant"] for i in items_) / len(items_),
                "mean_words": round(statistics.mean(i["word_count"] for i in items_), 1),
            }
            for lo in (30, 60, 120)
            if (items_ := band(lo))
        },
        "mean_decodability": round(statistics.mean(i["decodability"] for i in items), 4),
        "min_decodability": round(min(i["decodability"] for i in items), 4),
        "gate_pass_rate": sum(i["passes_gate"] for i in items) / len(items),
        "oov_instances": sum(i["oov"] for i in items),
        "over_story_word_cap": sum(1 for i in items if i["distinct_story_words"] > 3),
        "total_cost_usd": round(sum(i["cost_usd"] for i in items), 4),
        "cost_per_produced": round(sum(i["cost_usd"] for i in items) / len(items), 4),
        "output_tokens_p50": round(statistics.median(i.get("output_tokens", 0) for i in items)),
        "output_tokens_mean": round(statistics.mean(i.get("output_tokens", 0) for i in items)),
        "per_stage": per_stage,
    }


def print_summary(s: dict, label: str) -> None:
    print(f"\n=== {label} ===")
    print(
        f"attempted={s['attempted']}  produced={s['n']}  api_fail={s['failures']}"
        + (f"  {s['failure_reasons']}" if s.get("failure_reasons") else "")
    )
    if not s["n"]:
        return
    print(f"n={s['n']}  overall length compliance {s['compliance_overall']:.1%}")
    for k, v in s["by_band"].items():
        print(
            f"  target {k:>7}: {v['compliance']:6.1%}  "
            f"mean {v['mean_words']:>6.1f} words  (n={v['n']})"
        )
    print(
        f"  decodability mean {s['mean_decodability']:.4f} min {s['min_decodability']:.4f}"
        f"  first-draft gate pass {s['gate_pass_rate']:.1%}"
        f"  OOV {s['oov_instances']}  over story-word cap {s['over_story_word_cap']}"
    )
    print(
        f"  spend ${s['total_cost_usd']:.4f}  "
        f"${s['cost_per_produced']:.4f}/story  "
        f"output tokens p50 {s['output_tokens_p50']} mean {s['output_tokens_mean']}"
    )
    print(f"  {'stage':>5} {'target':>8} {'n':>3} {'mean':>6} {'min':>5} {'max':>5} "
          f"{'compl':>7} {'short':>5} {'long':>4} {'dec':>7} {'p95 ms':>7}")
    for stage_id, v in s["per_stage"].items():
        lo, hi = v["target"]
        print(
            f"  {stage_id:>5} {f'{lo}-{hi}':>8} {v['n']:>3} {v['mean_words']:>6.1f} "
            f"{v['min_words']:>5} {v['max_words']:>5} {v['compliance']:>6.1%} "
            f"{v['under']:>5} {v['over']:>4} {v['mean_decodability']:>7.4f} "
            f"{v['p95_latency_ms']:>7}"
        )


async def revalidate(paths: list[str]) -> None:
    """Re-score every stored text against TODAY's decoder. No API call.

    Why this exists: the before and after runs of the length measurement straddled
    a change to `decoder/data/gpcs.csv`, and decodability is a pure function of
    that inventory. Comparing the two as printed would compare two different
    rulers, in the direction that flatters the later run.

    Story words are handled identically for both files by honouring the WHOLE
    offered bank, because the model's own `story_words_used` claim was not stored.
    That is an upper bound on decodability rather than the gate's own figure — the
    gate intersects the claim with the bank (STATE invariant 2) — but it is the
    SAME upper bound on both sides, which is what a before/after needs. Read these
    numbers as "did the distribution move", never as "does this story pass".
    """
    from storyforge.decoder import mastered_ids, validate_text

    async with SessionLocal() as session:
        vocabs: dict[tuple[int, str], object] = {}
        for path in paths:
            payload = json.loads(Path(path).read_text())
            scores, below_gate = [], []
            for item in payload["items"]:
                if item["failed"]:
                    continue
                key = (item["stage_id"], item["theme"])
                if key not in vocabs:
                    vocabs[key] = await load_vocabulary(session, *key)
                v = vocabs[key]
                report = validate_text(
                    item["text"],
                    mastered_ids(v.stage_id),
                    frozenset(v.sight_words),
                    frozenset(w.lower() for w in v.story_words),
                )
                scores.append(report.decodability)
                if report.decodability < DECODABILITY_GATE:
                    below_gate.append((item["stage_id"], item["theme"], report.decodability))
            print(
                f"\n{payload['label']}\n"
                f"  n={len(scores)}  mean {statistics.mean(scores):.4f}  "
                f"median {statistics.median(scores):.4f}  min {min(scores):.4f}\n"
                f"  first drafts below the {DECODABILITY_GATE:.0%} gate: "
                f"{len(below_gate)}  {below_gate}"
            )


def compare(before: dict, after: dict) -> None:
    b, a = before["summary"], after["summary"]
    print("\n=== before -> after ===")
    print(f"  produced / attempted  {b['n']}/{b['attempted']} -> {a['n']}/{a['attempted']}")
    print(f"  api_fail              {b['failures']} -> {a['failures']}")
    print(f"  overall compliance    {b['compliance_overall']:.1%} -> {a['compliance_overall']:.1%}")
    for k in a["by_band"]:
        if k in b["by_band"]:
            print(
                f"  target {k:>7}        {b['by_band'][k]['compliance']:.1%} -> "
                f"{a['by_band'][k]['compliance']:.1%}   mean "
                f"{b['by_band'][k]['mean_words']} -> {a['by_band'][k]['mean_words']} words"
            )
    print(f"  mean decodability     {b['mean_decodability']:.4f} -> {a['mean_decodability']:.4f}")
    print(f"  min decodability      {b['min_decodability']:.4f} -> {a['min_decodability']:.4f}")
    print(f"  first-draft gate pass {b['gate_pass_rate']:.1%} -> {a['gate_pass_rate']:.1%}")
    print(f"  OOV instances         {b['oov_instances']} -> {a['oov_instances']}")
    print(f"  spend                 ${b['total_cost_usd']:.4f} -> ${a['total_cost_usd']:.4f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stages", default=",".join(str(s) for s in DEFAULT_STAGES))
    p.add_argument("--themes", default=",".join(DEFAULT_THEMES))
    p.add_argument("--n", type=int, default=2, help="stories per (stage, theme)")
    p.add_argument("--label", default="measured")
    p.add_argument("--json", help="write every measurement to this path")
    p.add_argument("--pricing-date", default=date.today().isoformat())
    p.add_argument("--timeout", type=float, default=MEASURE_TIMEOUT_S)
    p.add_argument("--effort", default=DEFAULT_EFFORT)
    p.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    p.add_argument(
        "--revalidate",
        nargs="+",
        metavar="JSON",
        help="re-score stored texts against today's decoder; no API call",
    )
    args = p.parse_args()

    if args.revalidate:
        asyncio.run(revalidate(args.revalidate))
        return

    if args.compare:
        before = json.loads(Path(args.compare[0]).read_text())
        after = json.loads(Path(args.compare[1]).read_text())
        print_summary(before["summary"], f"before ({before['label']})")
        print_summary(after["summary"], f"after ({after['label']})")
        compare(before, after)
        return

    # See MEASURE_TIMEOUT_S. `ANTHROPIC_GENERATION_TIMEOUT_S` is the sanctioned
    # override (DEVIATIONS 8.5) and is read per call, so this touches no
    # production constant and leaves every other caller alone. Only ever raises:
    # a harness must not measure through a tighter budget than production uses.
    settings = get_settings()
    configured = settings.anthropic_generation_timeout_s or client_mod.GENERATION_TIMEOUT_S
    if args.timeout > configured:
        os.environ["ANTHROPIC_GENERATION_TIMEOUT_S"] = str(args.timeout)
        get_settings.cache_clear()
    effective = get_settings().anthropic_generation_timeout_s or client_mod.GENERATION_TIMEOUT_S

    stages = [int(s) for s in args.stages.split(",") if s.strip()]
    themes = [t.strip() for t in args.themes.split(",") if t.strip()]
    pricing_on = date.fromisoformat(args.pricing_date)
    print(
        f"stages={stages} themes={themes} n={args.n} "
        f"effort={args.effort} generation timeout={effective}s"
    )
    items = asyncio.run(run(stages, themes, args.n, pricing_on, args.effort))
    summary = summarise(items)
    print_summary(summary, args.label)
    if args.json:
        Path(args.json).write_text(
            json.dumps({"label": args.label, "items": items, "summary": summary}, indent=1)
        )
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
