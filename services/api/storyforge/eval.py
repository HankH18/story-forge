"""Eval harness (§14).

`python -m storyforge.eval smoke` is the §14 regression gate: 2 stages x 1 theme
x 2 stories. It runs the REAL pipeline against the REAL API — that is the entire
point. A green suite built on a fake client would prove nothing about the one
question that matters: can a constrained model actually hit the gate?
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from datetime import date

from storyforge.db import SessionLocal
from storyforge.pipeline import DECODABILITY_GATE, PipelineResult, run_pipeline


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
    a = p.parse_args()

    asyncio.run(
        run_smoke(
            [int(x) for x in a.stages.split(",")],
            a.themes.split(","),
            a.n,
            a.effort,
        )
    )


if __name__ == "__main__":
    main()
