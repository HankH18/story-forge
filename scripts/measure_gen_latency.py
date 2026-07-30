"""Measure the TRUE single-attempt GENERATING latency, uncensored by the timeout.

Why this exists: every generation latency in `reports/eval-*.json` is censored.
The SDK is configured `max_retries=2` and the node passes a per-call timeout, so
one HTTP attempt can never be observed taking longer than that timeout — a story
recorded at 86.6s is three attempts, not one slow call. Sizing a timeout off
those numbers would be sizing it off itself.

So this runs the same request the GENERATING node runs, with `max_retries=0` and
a timeout large enough that nothing is cut off, and prints one row per call.

    services/api/.venv/bin/python scripts/measure_gen_latency.py \
        --stages 1,5,8,11 --n 3

Run from the repo root, like `scripts/eval_doc.py`. Cost is real: 12 calls at the
top-stage rate is roughly $0.20.
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

sys.path.insert(0, "services/api")
os.chdir("services/api")  # `.env` and the settings loader are relative to it

from anthropic import AsyncAnthropic  # noqa: E402, I001
from storyforge.config import get_settings  # noqa: E402
from storyforge.db import SessionLocal  # noqa: E402
from storyforge.llm.client import generation_model  # noqa: E402
from storyforge.llm.generate import (  # noqa: E402
    SYSTEM_RULES,
    StoryDraft,
    build_request_block,
    build_vocabulary_block,
)
from storyforge.llm.pricing import usage_from_response  # noqa: E402
from storyforge.pipeline import load_vocabulary  # noqa: E402

# Far above anything plausible, so the measurement is the model's latency and
# not this script's patience.
UNCENSORED_TIMEOUT_S = 600.0


async def one_call(client, vocab, theme: str, effort: str):
    model = generation_model()
    t0 = time.perf_counter()
    response = await client.messages.parse(
        model=model,
        max_tokens=8000,
        output_config={"effort": effort},
        system=[
            {"type": "text", "text": SYSTEM_RULES},
            {
                "type": "text",
                "text": build_vocabulary_block(
                    vocab.stage_name, vocab.allowed_words, vocab.sight_words
                ),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[
            {
                "role": "user",
                "content": build_request_block(
                    theme, vocab.story_words, vocab.target_min, vocab.target_max
                ),
            }
        ],
        output_format=StoryDraft,
    )
    elapsed = time.perf_counter() - t0
    usage = usage_from_response(model, response.usage)
    words = 0
    if response.parsed_output is not None:
        words = len(" ".join(s.text for s in response.parsed_output.segments).split())
    return elapsed, usage, words, response.usage.output_tokens


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="1,5,8,11")
    ap.add_argument("--themes", default="animals,dragons,space")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    stages = [int(s) for s in a.stages.split(",")]
    themes = a.themes.split(",")
    today = date.today()

    client = AsyncAnthropic(
        api_key=get_settings().anthropic_api_key,
        max_retries=0,  # the whole point: one HTTP attempt per row
        timeout=UNCENSORED_TIMEOUT_S,
    )

    rows: list[dict] = []
    total_cost = 0.0
    print(f"{'stage':>5} {'theme':<8} {'i':>2} {'sec':>7} {'out_tok':>8} {'words':>6} {'tok/s':>7}")
    async with SessionLocal() as session:
        for stage in stages:
            vocab = await load_vocabulary(session, stage, themes[0])
            for i in range(a.n):
                theme = themes[i % len(themes)]
                vocab = await load_vocabulary(session, stage, theme)
                try:
                    elapsed, usage, words, out_tok = await one_call(
                        client, vocab, theme, a.effort
                    )
                except Exception as exc:  # noqa: BLE001 — a failed probe is a datum
                    print(f"{stage:>5} {theme:<8} {i:>2}   ERROR {type(exc).__name__}: {exc}")
                    rows.append({"stage": stage, "theme": theme, "error": repr(exc)})
                    continue
                cost = usage.cost_usd(today)
                total_cost += cost
                print(
                    f"{stage:>5} {theme:<8} {i:>2} {elapsed:>7.2f} {out_tok:>8} "
                    f"{words:>6} {out_tok / elapsed:>7.1f}"
                )
                rows.append(
                    {
                        "stage": stage,
                        "theme": theme,
                        "i": i,
                        "seconds": round(elapsed, 3),
                        "output_tokens": out_tok,
                        "words": words,
                        "target": [vocab.target_min, vocab.target_max],
                        "cost_usd": round(cost, 6),
                    }
                )

    print(f"\ntotal cost ${total_cost:.4f} over {len(rows)} call(s)")
    print(f"{'stage':>5} {'n':>3} {'min':>7} {'p50':>7} {'max':>7}")
    for stage in stages:
        secs = sorted(r["seconds"] for r in rows if r.get("stage") == stage and "seconds" in r)
        if not secs:
            continue
        print(
            f"{stage:>5} {len(secs):>3} {secs[0]:>7.2f} "
            f"{statistics.median(secs):>7.2f} {secs[-1]:>7.2f}"
        )

    if a.out:
        with open(a.out, "w") as fh:
            json.dump({"rows": rows, "total_cost_usd": total_cost}, fh, indent=1)
        print(f"wrote {a.out}")


if __name__ == "__main__":
    asyncio.run(main())
