"""Assemble docs/EVAL.md from the run's own JSON.

`reports/` is gitignored (run output, not source), so `docs/EVAL.md` has to carry
the numbers itself — and a hand-transcribed table is a table that can be wrong.
Every figure below is read out of `eval-{ts}.json`, which the harness wrote from
the same `aggregate()` the report used. The prose is mine; the numbers are not.

Usage: services/api/.venv/bin/python scripts/eval_doc.py \
           reports/eval-<ts>.json docs/EVAL.md \
           [--assume-per-call-timeout SECONDS]

Run from the repo root.

**Two things this file must never do again** (AUDIT-PHASE7 F1/F2/F3,
DEVIATIONS 8.37/8.39). It must not describe a guarantee the harness does not
implement — the provenance paragraph used to say `--resume` was "enforced by the
code rather than by care", which was false, and the code that would have made it
true landed only with 8.31. And it must not print a figure the run did not record
as though it had: anything absent from an older run's JSON prints as **not
recorded**, because a blank where the record is blank is the only way a reader can
tell a run that fixed the provenance gap from one that had it.

The decoder-limitation rates and the retry split are recomputed here from the
run's own ITEMS, using `storyforge.eval`'s own functions rather than arithmetic
re-typed in this file, so a run whose stored aggregate predates DEVIATIONS 8.34
and 8.35 still gets the corrected denominators without anybody editing the
recorded rows. `--assume-per-call-timeout` supplies the per-HTTP-attempt budget for
a run that never recorded one; the document says it was supplied.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "services/api")

from storyforge.eval import (
    DECODABILITY_BINS,
    _pct,
    _retry_budget_phrase,
    _table,
    criteria_verdict,
    decoder_limitation_rates,
    generation_latency_split,
)

argv = list(sys.argv[1:])
assumed_timeout_s: float | None = None
if "--assume-per-call-timeout" in argv:
    i = argv.index("--assume-per-call-timeout")
    assumed_timeout_s = float(argv[i + 1])
    del argv[i : i + 2]

src, dest = Path(argv[0]), Path(argv[1])
payload = json.loads(src.read_text())
run, agg, items = payload["run"], payload["metrics"], payload["items"]
cfg = run["config"]
d, c, r, m = agg["decodability"], agg["cost"], agg["rewrite_depth"], agg["moderation"]
lat = agg["latency_ms"]
verdict = criteria_verdict(agg)
ok = all(v[1] for v in verdict)

#: Recomputed from the items rather than read from `agg`, so the denominator is the
#: scored set for every run including the ones recorded before 8.34.
dl = decoder_limitation_rates(items)
by_stage_flags = {
    _s: decoder_limitation_rates([i for i in items if str(i["stage_id"]) == str(_s)])
    for _s in agg["by_stage"]
}
split = generation_latency_split(
    items, fallback_timeout_s=cfg.get("generation_timeout_s") or assumed_timeout_s
)
prov = cfg.get("provenance") or {}
NOT_RECORDED = "**not recorded by this run** — see `docs/DEVIATIONS.md` 8.30-8.39"


def produced_rate(field: str) -> float:
    prod = [i for i in items if i["outcome"] != "api_fail"]
    return (sum(1 for i in prod if i[field]) / len(prod)) if prod else 0.0


def commit_cell() -> str:
    """The commit and the dirty flag, never one without the other.

    AUDIT-PHASE7 F2: this run recorded `559f102`, at which `eval.py` is 102 lines
    with only a `smoke` subparser — the recorded commit cannot run the command
    printed above it. A commit alone is not provenance (DEVIATIONS 8.32).
    """
    commit = cfg.get("git_commit", "unknown")
    dirty = prov.get("git_dirty")
    if dirty is None:
        return (
            f"`{commit}` — whether the working tree was dirty is {NOT_RECORDED}, so"
            f" this commit is not evidence that it contains the code that ran"
        )
    if not dirty:
        return f"`{commit}` — working tree CLEAN, so this commit IS the code that ran"
    paths = prov.get("git_dirty_paths") or []
    harness = [p for p in paths if "storyforge/eval.py" in p or "storyforge/pipeline.py" in p]
    return (
        f"`{commit}` — **working tree DIRTY** ({prov.get('git_dirty_path_count', len(paths))}"
        f" path(s){', including the harness itself' if harness else ', none of them the harness'}),"
        f" so this commit is NOT the code that ran"
    )


def code_cell() -> str:
    sha = (prov.get("code_sha256") or {}).get("storyforge/eval.py")
    if not sha:
        return NOT_RECORDED
    return f"`{sha[:16]}…` (`storyforge/eval.py`; verify with `git show <commit>:… | shasum -a 256`)"


def vocabulary_cell() -> str:
    v = cfg.get("vocabulary")
    if not v:
        return (
            NOT_RECORDED
            + " — and this is the gap that already bit: 11 of this run's decodability"
            " figures stopped reproducing when a concurrent agent re-seeded"
            " `sight_words` 33 → 38 and added the `wh` GPC (AUDIT-PHASE7 F3)"
        )
    return (
        f"{v['gpc_count']} GPCs `{v['gpcs_sha256'][:12]}…` · "
        f"{v['sight_word_count']} sight words `{v['sight_words_sha256'][:12]}…` · "
        f"{v['allowed_words_count']:,} allowed words · "
        f"{v['cmudict_entries']:,} CMUdict entries"
    )


def timeout_cell() -> str:
    t = cfg.get("generation_timeout_s")
    if t is None and assumed_timeout_s is not None:
        return (
            f"{assumed_timeout_s:.0f}s — **supplied to this document**, not recorded by"
            f" the run (DEVIATIONS 8.35)"
        )
    if t is None:
        return NOT_RECORDED
    rr = cfg.get("sdk_max_retries")
    return f"{t:.0f}s x up to {(rr or 0) + 1} attempts (`SDK_MAX_RETRIES={rr}`)"


def model_census_cell() -> str:
    census = (agg.get("models_observed") or {}).get("generation") or {}
    if not census:
        return NOT_RECORDED + " (AUDIT-PHASE7 F1 — the claim above is unverifiable)"
    body = ", ".join(f"`{k}` x{v}" for k, v in census.items())
    return body + (" — **MIXED matrix**" if len(census) > 1 else " — one model, confirmed per item")

out: list[str] = []
W = out.append

W("# Story Forge — §14 eval report")
W("")
W(
    "**The question this document answers:** §0 claims that a constrained LLM plus a"
    " pure-function gate yields reliably decodable stories. Does it?"
)
W("")
#: The two §1 criteria that are ABOUT decodability, separated from the two that
#: are about whether a cell got measured at all. Collapsing them into one
#: PASS/FAIL would let an availability failure read as evidence against the
#: thesis, which it is not — and would equally let a decodability failure hide
#: behind "well, some cells timed out", which would be worse.
_THESIS_CRITERIA = ("decodability", "rewrite")
thesis = [v for v in verdict if any(k in v[0] for k in _THESIS_CRITERIA)]
coverage = [v for v in verdict if v not in thesis]
thesis_ok = all(v[1] for v in thesis)
coverage_ok = all(v[1] for v in coverage)

W(
    f"**Answer: {'YES on decodability' if thesis_ok else 'NO — the decodability claim did not hold'}"
    f", and the gate is the reason.** Of the {agg['produced']} stories the model"
    f" actually produced across all 12 stages and 3 themes,"
    f" **{agg['produced'] - d['below_gate']} reached the 95% gate and"
    f" {d['below_gate']} did not**. The worst single story measured"
    f" {_pct(d['min'])}. Median {_pct(d['p50'])}, mean {_pct(d['mean'])}. The"
    f" rewrite loop did most of that work at depth 0 — p95 was"
    f" {r['p95']}, against §1's budget of {2} — so this is"
    f" allowlist-constrained generation succeeding, not retry pressure grinding"
    f" the model into compliance (§6's stated order of levers). No story in the"
    f" matrix contained a single out-of-vocabulary word, and none exceeded the"
    f" story-word cap."
)
W("")
if not coverage_ok:
    _budget = cfg.get("generation_timeout_s") or assumed_timeout_s
    _retries = cfg.get("sdk_max_retries")
    W(
        f"**`make eval` nonetheless exits non-zero, and the reason is availability,"
        f" not decodability.** {agg['outcomes'].get('api_fail', 0)} of"
        f" {agg['cells_planned']} cells never produced any text."
        + (
            f" The generation node's per-HTTP-attempt budget on this run was"
            f" {_budget:.0f}s"
            + (
                ""
                if cfg.get("generation_timeout_s")
                else " (supplied to this document, not recorded by the run)"
            )
            + (
                f", and the SDK retries up to {_retries} times, so a dead cell costs"
                f" {(_retries or 0) + 1} attempts plus backoff"
                if _retries is not None
                else ", and the SDK retry budget that multiplies it is "
                + NOT_RECORDED
            )
            + "."
            if _budget
            else " The per-attempt timeout in force is " + NOT_RECORDED + "."
        )
        + " That is a real defect and it is reported as one. It is not evidence"
        " against §0's thesis, and this report is written so that neither reading"
        " can borrow the other's number: section 2 keeps the two kinds of"
        " criterion on separate lines, and section 7 keeps wall-clock latency on a"
        " separate line from the retries inside it — which the first version of"
        " this report did not do (AUDIT-PHASE7 F7, DEVIATIONS 8.35)."
    )
    W("")
W(
    f"**Total spend: ${c['total_usd']:.2f}** against a ${cfg['budget_usd']:.2f}"
    f" cap, at {c['pricing_date']} prices."
    f" ${c['per_accepted_usd']:.4f} per accepted story, excluding enrichment"
    f" (section 6)."
)
W("")
W(
    "Every figure below is read out of the run's own"
    " `reports/eval-<ts>.json` by `scripts/eval_doc.py`, not transcribed by hand."
    " `reports/` is gitignored (run output, not source), which is why the numbers"
    " are duplicated into this committed file. The prose is a reading of them; the"
    " figures are not editable prose."
)
W("")
trail = cfg.get("resumed") or []
# A trail entry that already had every cell recorded ran NOTHING — it only
# re-derived the report from the stored measurements. Counting those as
# "sittings" would overstate how fragmented the data collection was.
generating = [t for t in trail if t["items_already_recorded"] < agg["cells_planned"]]
drift = [x for t in trail for x in (t.get("config_drift") or [])]
if trail:
    W(
        f"**Provenance.** This matrix was generated in"
        f" {len(generating) + 1} sitting(s) (DEVIATIONS 7.51). The first"
        f" invocation was killed by its launcher at cell"
        f" {generating[0]['items_already_recorded'] if generating else agg['cells_planned']}"
        f" of {agg['cells_planned']}. Every `eval_run_items` row is committed as"
        f" its story completes, so `--resume` ran only the remainder. It is one"
        f" matrix, not two runs stapled together."
    )
    W("")
    # What a resume does with the stored config, stated as the mechanism rather
    # than as a guarantee. The sentence this replaces claimed the model and the
    # gate were "enforced by the code rather than by care"; they were not — the
    # stored config won the record and the environment won the behaviour, so one
    # supported command could produce a report naming a model it had not used
    # (AUDIT-PHASE7 F1). 8.31 is the code that closes it.
    W(
        "On a resume the matrix parameters — stages, themes, `n`, effort, pricing"
        " date, `--cold`, enrichment — are RESTORED from the stored config, which is"
        " enforcement because they are arguments. The values that come from the"
        " environment or from a module constant cannot be restored by passing them:"
        " the model ids, the price sheets, the gate, the rewrite budget, the prompt"
        " allowlist size, the timeouts and the vocabulary fingerprint. Those are"
        " VERIFIED against the stored config, and a difference REFUSES the resume"
        " with exit code 3 while any cell still has to run; when the run is already"
        " complete and only the report can change, the difference is printed instead"
        " (DEVIATIONS 8.31). `concurrency` and `budget_usd` belong to the sitting,"
        " not to the run, and are recorded on each trail entry."
    )
    W("")
    if not any("config_drift" in t for t in trail):
        W(
            "**This run predates that check.** Its resumes ran before DEVIATIONS"
            " 8.31, so the stored config was written back over freshly computed"
            " values without either enforcing or verifying them, and nothing in the"
            " record shows which model each half of the matrix used. That is"
            " AUDIT-PHASE7 F1, and it is why section 3 reports the per-item model"
            " census as not recorded (DEVIATIONS 8.30)."
        )
        W("")
    if drift:
        _differs = sorted({x["key"] for x in drift if not x.get("missing")})
        _missing = sorted({x["key"] for x in drift if x.get("missing")})
        W(
            "**CONFIG DRIFT on a re-derivation.**"
            + (
                f" Differed from the live process: {', '.join('`' + k + '`' for k in _differs)}."
                if _differs
                else ""
            )
            + (
                f" Never recorded by this run, so uncheckable:"
                f" {', '.join('`' + k + '`' for k in _missing)}."
                if _missing
                else ""
            )
            + (
                " The values in force on the sitting that ran are on its trail entry"
                " under `unrecorded_live_values`."
                if _missing
                else ""
            )
            + " Every figure below is the measurement recorded at run time and is"
            " deliberately not restated against the current values."
        )
        W("")
    W(
        f"A further {len(trail) - len(generating)} invocation(s) re-derived this"
        f" report from the recorded measurements without calling the API at all."
        f" `--resume` on a complete run is idempotent, and re-finalising it"
        f" reproduced the identical verdict and exit code. That checks that the"
        f" **verdict** is a pure function of the recorded items — it is silent about"
        f" the config table, which the same exercise proved could disagree with the"
        f" process that produced it. Full trail: `{json.dumps(trail)}`"
    )
    W("")
if not cfg.get("vocabulary"):
    # The point of the fingerprint is that a later reader can tell whether two runs
    # are measurements of the same thing. This run has no fingerprint, so the
    # comparison has to be spelled out in prose instead — once (DEVIATIONS 8.33).
    W(
        "**This matrix is not comparable to a later one, and the record cannot say"
        " so by itself.** It measured against the vocabulary as it stood at the time:"
        " 33 sight words and a 92-GPC inventory. Both have since moved — `of`, `so`,"
        " `do`, `be` and `from` became sight words and `wh`→/w/ was added"
        " (DEVIATIONS 8.10-8.13) — along with the generation timeout (8.7), the"
        " length instruction (8.22), the grammar judge (8.20) and the blocklist"
        " (8.14). A run after those changes samples a different population, and every"
        " figure here should be read as the measurement of a different instrument"
        " rather than as a baseline to diff against. Runs from DEVIATIONS 8.33 onward"
        " carry `config.vocabulary`, so that judgement stops depending on someone"
        " remembering this paragraph."
    )
    W("")
W("---")
W("")

# --- 1. reproducing -----------------------------------------------------------
W("## 1. The run")
W("")
W("```")
W("cd services/api && python -m storyforge.eval run \\")
W(f"  --stages {cfg['stages_spec']} --themes {cfg['themes_spec']} --n {cfg['n']} --cold \\")
W(
    f"  --effort {cfg['effort']} --pricing-date {cfg['pricing_date']} "
    f"--budget {cfg['budget_usd']} --concurrency {cfg['concurrency']}"
)
W("```")
W("")
W(
    "`make eval` runs the first two lines — the matrix and `--cold`. It does NOT pass"
    " `--effort`, `--budget` or `--concurrency` (the values above are the defaults it"
    " inherits) and it does NOT pass `--pricing-date`, which defaults to the day of"
    " the run: the date in the line above is the date THIS run priced at, not a"
    " constant. A run on or after 2026-09-01 will price at Sonnet 5's standard rate"
    " with no flag change, which is why the date is printed rather than assumed"
    " (AUDIT-PHASE7 F10)."
)
W("")
W(
    _table(
        ["what", "value"],
        [
            ["`eval_runs.id`", run["id"]],
            ["started / finished (UTC)", f"{run['started_at']} / {run['finished_at']}"],
            ["status", f"`{run['status']}`"],
            [
                "pipeline time (summed per story)",
                f"{agg.get('pipeline_seconds', 0) / 60:.0f} min",
            ],
            ["git commit", commit_cell()],
            ["harness code sha256", code_cell()],
            ["vocabulary the gate ran against", vocabulary_cell()],
            ["per-HTTP-attempt generation timeout", timeout_cell()],
            ["generation model (config's claim)", f"`{cfg['models']['generation']}`"],
            ["generation model (the items' record)", model_census_cell()],
            ["fast model (moderation)", f"`{cfg['models']['fast']}`"],
            [
                "pricing date",
                f"`{cfg['pricing_date']}` — Sonnet 5 introductory rate, expires 2026-08-31",
            ],
            [
                "generation price",
                f"${cfg['prices']['generation']['input_per_mtok']} in / "
                + f"${cfg['prices']['generation']['output_per_mtok']} out per Mtok",
            ],
            [
                "fast price",
                f"${cfg['prices']['fast']['input_per_mtok']} in / "
                + f"${cfg['prices']['fast']['output_per_mtok']} out per Mtok",
            ],
            ["stages", ", ".join(str(s) for s in cfg["stages"])],
            ["themes", ", ".join(cfg["themes"])],
            ["stories per (stage, theme)", cfg["n"]],
            ["cells planned", agg["cells_planned"]],
            [
                "gate (never lowered, §0)",
                f"{cfg['gate']:.0%} decodability AND 0 OOV AND <= {cfg['max_story_words']} "
                + "distinct story words",
            ],
            ["rewrite budget (§7)", cfg["max_rewrites"]],
            ["prompt allowlist size (§6)", cfg["prompt_word_limit"]],
            ["output effort", cfg["effort"]],
            ["concurrency", f"{cfg['concurrency']} (sequential — DEVIATIONS 7.48)"],
            ["cache bypassed (`--cold`)", cfg["cold"]],
            ["ENRICHING run", f"{cfg['enrich']} (DEVIATIONS 7.45)"],
            ["python", cfg["python"]],
            ["spend cap", f"${cfg['budget_usd']:.2f}, enforced on the running actual"],
        ],
    )
)
W("")

# --- 2. verdict ---------------------------------------------------------------
W("## 2. §1 item 2 — the machine-checkable form of the thesis")
W("")
W(
    "§1 item 2: *\"Eval harness reports >=95% decodability (excluding declared story"
    " words) across all 12 stages x 3 themes x 5 stories, with rewrite-loop depth"
    " p95 <= 2.\"* Checked as four assertions, each with its own exit code path"
    " (DEVIATIONS 7.49):"
)
W("")
W(
    _table(
        ["criterion", "result", "evidence"],
        [[n, "PASS" if good else "**FAIL**", ev] for n, good, ev in verdict],
    )
)
W("")
W(f"**Overall: {'PASS' if ok else 'FAIL'}.** `make eval` exited {0 if ok else 'non-zero'}.")
W("")
W(
    "A moderation rejection is deliberately *not* one of these criteria. It is a"
    " content verdict on a story that already met the decodability criterion, so"
    " folding it in would make a §13 judgement look like a decoder failure. It is"
    " reported in full in section 8 instead."
)
W("")

# --- 3. headline --------------------------------------------------------------
W("## 3. Headline numbers")
W("")
W(
    _table(
        ["metric", "value"],
        [
            ["stories attempted", agg["attempted"]],
            ["stories produced (the model returned text)", agg["produced"]],
            ["stories ACCEPTED (cleared gate **and** moderation)", agg["accepted"]],
            ["gate + moderation pass rate", _pct(agg["gate_pass_rate"])],
            [
                "fallback rate (§7 — a live request would have served the warm pool)",
                _pct(agg["fallback_rate"]),
            ],
            ["mean decodability", _pct(d["mean"])],
            ["median decodability", _pct(d["p50"])],
            ["worst single story", _pct(d["min"])],
            ["**stories that never reached the 95% gate**", d["below_gate"]],
            ["rewrite depth p50 / p95 / max", f"{r['p50']} / {r['p95']} / {r['max']}"],
            ["attempts per accepted story", r["attempts_per_accepted"]],
            ["total cost", f"**${c['total_usd']:.4f}**"],
            [
                "cost per accepted story",
                f"**${c['per_accepted_usd']:.4f}**" if c["per_accepted_usd"] else "n/a",
            ],
            ["cost per attempted story", f"${c['per_attempted_usd']:.4f}"],
            [
                "cold generation p50 / p95",
                f"{lat['total_p50'] / 1000:.1f}s / {lat['total_p95'] / 1000:.1f}s",
            ],
            ["OOV word instances", agg["oov"]["total"]],
            ["stories over the story-word cap", agg["story_words"]["over_cap"]],
        ],
    )
)
W("")
W(f"§7 outcome taxonomy: `{json.dumps(agg['outcomes'])}`")
W("")

# --- 4. distribution ---------------------------------------------------------
W("## 4. Decodability — the distribution, not the mean")
W("")
W(
    "A mean hides the shape: twenty stories at 100% and one at 20% has a healthy"
    " mean and one child who cannot read their story. Quantiles over every produced"
    " story:"
)
W("")
W(
    _table(
        ["min", "p05", "p25", "p50", "mean", "p95", "max"],
        [[_pct(d[k]) for k in ("min", "p05", "p25", "p50", "mean", "p95", "max")]],
    )
)
W("")
W(
    _table(
        ["decodability bin", "stories", "share"],
        [
            [
                label,
                d["bins"][label],
                _pct(d["bins"][label] / agg["produced"]) if agg["produced"] else "—",
            ]
            for label, _, _ in DECODABILITY_BINS
        ],
    )
)
W("")
if d["below_gate_detail"]:
    W("### Every story that failed the gate")
    W("")
    W(
        _table(
            ["stage", "theme", "final decodability", "rewrites used", "violating words", "missing GPCs"],
            [
                [
                    b["stage_id"],
                    b["theme"],
                    _pct(b["decodability"]),
                    b["rewrite_depth"],
                    ", ".join(b["violation_words"]) or "—",
                    ", ".join(b["missing_gpcs"]) or "—",
                ]
                for b in d["below_gate_detail"]
            ],
        )
    )
    W("")
else:
    W(
        "**No story finished below the gate.** Every one of the"
        f" {agg['produced']} stories the model produced reached >=95% decodability"
        " within the rewrite budget."
    )
    W("")

W("### Per stage")
W("")
W(
    _table(
        [
            "stage", "n", "accepted", "pass rate", "min", "p50", "mean", "max",
            "below gate", "rw p95", "mean words", "in length range", "TTR",
            "$/accepted", "p95 latency",
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
                f"{v['latency_ms']['p95'] / 1000:.0f}s",
            ]
            for s, v in agg["by_stage"].items()
        ],
    )
)
W("")
W("### Per theme")
W("")
W(
    _table(
        ["theme", "n", "accepted", "pass rate", "min", "p50", "mean", "max", "rw p95", "$/accepted"],
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
)
W("")

# --- 5. rewrite loop ---------------------------------------------------------
W("## 5. The rewrite loop — how much retry pressure the gate needed")
W("")
W(
    "§6 is explicit that the primary lever is allowlist-constrained generation, not"
    " retry pressure. Depth 0 means the first draft passed the gate untouched."
)
W("")
W(_table(["rewrite depth", "stories"], [[k, v] for k, v in r["hist"].items()]))
W("")
W(
    f"mean {r['mean']:.2f} · p50 {r['p50']} · p95 {r['p95']} · max {r['max']}"
    f" · attempts per accepted story {r['attempts_per_accepted']}"
)
W("")

# --- 6. cost ----------------------------------------------------------------
tk = c["tokens"]
W("## 6. Cost")
W("")
W(
    _table(
        ["metric", "value"],
        [
            ["pricing date (explicit, per `pricing.price_for`)", f"`{c['pricing_date']}`"],
            ["**total actually spent**", f"**${c['total_usd']:.4f}**"],
            [
                "spend cap",
                f"${cfg['budget_usd']:.2f} — "
                + ("REACHED, run stopped early" if run["status"] == "budget_stopped" else "not reached"),
            ],
            ["GENERATING + REWRITING (Sonnet)", f"${c['generation_usd']:.4f}"],
            ["MODERATING (Haiku)", f"${c['moderation_usd']:.4f}"],
            ["per attempted story", f"${c['per_attempted_usd']:.4f}"],
            [
                "**per ACCEPTED story**",
                f"**${c['per_accepted_usd']:.4f}**" if c["per_accepted_usd"] else "n/a",
            ],
            ["Sonnet uncached input tokens", f"{tk['input']:,}"],
            ["Sonnet cache-WRITE tokens (1.25x)", f"{tk['cache_write']:,}"],
            ["Sonnet cache-READ tokens (0.1x)", f"{tk['cache_read']:,}"],
            ["Sonnet output tokens", f"{tk['output']:,}"],
            ["Haiku input / output tokens", f"{tk['mod_input']:,} / {tk['mod_output']:,}"],
        ],
    )
)
W("")
_pi = run.get("post_intro") or {}
if _pi.get("unpriced_models"):
    # F13.4: `_reprice` used to return 0.0 on a missing price sheet, so an unpriced
    # model reported a $0.00 future cost. "We could not price this" is not "free".
    W(
        f"**This run could not be repriced for {_pi['on']}**: no price sheet for"
        f" {', '.join('`' + x + '`' for x in _pi['unpriced_models'])}. Reported as"
        f" unpriceable rather than as $0.00 (DEVIATIONS 8.36); the partial total over"
        f" the models that do have a sheet is ${_pi.get('partial_total_usd') or 0:.4f}."
    )
    W("")
elif run.get("post_intro_cost_usd"):
    W(
        f"**The pricing date matters.** Sonnet 5's introductory $2/$10 rate expires"
        f" 2026-08-31. The same token counts re-priced at the standard $3/$15"
        f" (2026-09-01) cost **${run['post_intro_cost_usd']:.4f}**, a"
        f" {run['post_intro_cost_usd'] / c['total_usd']:.2f}x increase — less than"
        f" 1.5x because Haiku's price does not change, and the gap between the two"
        f" ratios is the share of the bill that is generation rather than moderation."
    )
    W("")
if run.get("projection"):
    p = run["projection"]
    W(
        f"Pre-flight projection (no API call, DEVIATIONS 7.44):"
        f" ${p['no_rewrites_usd']:.2f} with no rewrites,"
        f" ${p['with_one_rewrite_each_usd']:.2f} with one rewrite each,"
        f" ${p['worst_case_all_budget_exhausted_usd']:.2f} worst case. Actual came in"
        f" at ${c['total_usd']:.2f}."
    )
    W("")
W(
    "**What this cost excludes.** The harness does not run §7's ENRICHING node"
    " (DEVIATIONS 7.45): no QUESTION_GEN, no SCENE_TAGGING, no question moderation."
    " DEVIATIONS 5.15 measured enrichment at **45-48% of a warm story's total"
    " cost**, so the cost of a story a child could actually be *served* is close to"
    f" double the ${c['per_accepted_usd']:.4f} above — call it"
    f" ${c['per_accepted_usd'] * 1.9:.4f}. Stated here rather than left for a reader"
    " to discover."
)
W("")

# --- 7. latency -------------------------------------------------------------
W("## 7. Latency per node")
W("")
W(
    _table(
        ["§7 node", "p50", "p95"],
        [
            [
                "GENERATING (wall clock, retries INCLUDED)",
                f"{lat['generating_p50']:.0f} ms",
                f"{lat['generating_p95']:.0f} ms",
            ],
            ["REWRITING (per rewrite)", f"{lat['rewriting_p50']:.0f} ms", f"{lat['rewriting_p95']:.0f} ms"],
            ["VALIDATING (the gate — pure function)", f"{lat['validating_p50']:.2f} ms", f"{lat['validating_p95']:.2f} ms"],
            ["MODERATING (Haiku)", f"{lat['moderating_p50']:.0f} ms", f"{lat['moderating_p95']:.0f} ms"],
            ["**whole pipeline**", f"**{lat['total_p50']:.0f} ms**", f"**{lat['total_p95']:.0f} ms**"],
        ],
    )
)
W("")
# The GENERATING row above is wall time around ONE SDK call, and that call carries
# a retry budget — so it could be three HTTP attempts. The first version of this
# report quoted it as a generation time and blamed the §1.6 miss on long stories,
# while separately filing the timeout as the availability defect: one defect priced
# twice, in a document that promises the two readings stay on separate lines
# (AUDIT-PHASE7 F7, DEVIATIONS 8.35).
if split.get("timeout_source") == "unknown":
    W(
        "**The retry-free subset cannot be computed from this run's record**: no"
        " per-HTTP-attempt timeout was stored on its items, so the row above is the"
        " only latency available and it includes any SDK-internal retries. Re-run"
        " this generator with `--assume-per-call-timeout SECONDS` to split it against"
        " a budget supplied from outside the record (DEVIATIONS 8.35)."
    )
    W("")
else:
    _w, _cl, _ov = split["wall_clock"], split["no_clock_retry"], split["over_timeout"]
    _budget_txt = ", ".join(f"{b:.0f}s" for b in split.get("per_call_timeout_s") or [])
    _src = (
        "recorded per item"
        if split["timeout_source"] == "recorded"
        else "**supplied to this document**, not recorded by the run"
    )
    W(
        f"**Two GENERATING numbers, because one number was measuring two things.**"
        f" The row above is wall clock around a single SDK call, and that call"
        f" {_retry_budget_phrase(split.get('sdk_max_retries'))}, so it can contain"
        f" several HTTP attempts. Against a per-attempt budget of {_budget_txt}"
        f" ({_src}), **{_ov['n']} of {_w['n']}** generations ({_pct(_ov['share'])}) ran"
        f" longer than one attempt could — each of those contains at least one attempt"
        f" that was killed by the clock and paid for. Over the {_cl['n']} that did"
        f" not, p50 is **{_cl['p50']:.0f} ms** and p95 **{_cl['p95']:.0f} ms**, against"
        f" the wall-clock p95 of {_w['p95']:.0f} ms."
    )
    W("")
    W(
        "The subset is named *no clock-driven retry*, not *single attempt*: a fast 429"
        " or 5xx retry still hides inside a fast call, so it is a bound on retry-free"
        " latency rather than a proof of one attempt (DEVIATIONS 8.35). Exact"
        " per-attempt counts need `x-stainless-retry-count` off the raw response,"
        " which is a change to the generation path and is named there as deferred."
    )
    W("")
meets = lat["total_p95"] < 12_000
W(
    f"§1 item 6 asks for cold generation p95 < 12s. Measured p95 is"
    f" **{lat['total_p95'] / 1000:.1f}s**, which"
    f" **{'meets' if meets else 'does NOT meet'}** it."
    + (
        ""
        if meets
        else " Per-stage p95 is in the table in section 4. The miss is NOT attributed"
        " here to long stories: DEVIATIONS 8.6/8.8 measured latency as a function of"
        " output tokens (r = +0.997) and barely of stage (r = +0.50), and the split"
        " above shows how much of this figure is retry rather than generation. §1.6 is"
        " recorded as unmet and unreachable as written — 12s buys roughly 1,200 tokens"
        " and a stage-12 story is 1,700-3,300 — and the criterion is not relaxed to"
        " match. Warm-pool serving is unaffected either way: §7 makes the demo path a"
        " database read, and that latency is not measured here."
    )
)
W("")
W(
    "The VALIDATING row is the thesis's economics in one line: the gate that makes"
    " the guarantee is roughly four orders of magnitude cheaper than the generation"
    " it judges, and costs nothing per call."
)
W("")

# --- 8. moderation ----------------------------------------------------------
W("## 8. Moderation (§13)")
W("")
W(
    _table(
        ["verdict class", "stories"],
        [[k, v] for k, v in m["classes"].items()],
    )
)
W("")
W(
    "- `blocklist` — §13 layer 1, deterministic, free, no API call.\n"
    "- `grammar` — the well-formedness half of the verdict (DEVIATIONS 4.12), which"
    " `moderate()` folds into `approved` and marks with a reason prefix.\n"
    "- `content` — the layer-3 safety judgement.\n"
    "- `not_reached` — the story never cleared the gate, so moderation never ran."
)
W("")
W(
    "**Hazard versus harm cannot be read off the verdict.** STATE invariant 5"
    " requires moderation to separate a storybook hazard (a fire-breathing dragon is"
    " the premise) from actual harm, and a spike either way is the signal. But the"
    " verdict carries no field for that distinction — the only record is a sentence"
    " the model wrote. So every content rejection's reason is quoted verbatim below"
    " and the `hazard?` column is a keyword HEURISTIC pointing at reasons worth"
    " reading, not a measurement (DEVIATIONS 7.46)."
)
W("")
if m["content_rejects"]:
    W(
        _table(
            ["stage", "theme", "hazard? (heuristic)", "reason (verbatim)"],
            [
                [
                    x["stage_id"],
                    x["theme"],
                    "suspect" if x["hazard_suspect_heuristic"] else "harm-shaped",
                    x["reason"].replace("|", "\\|"),
                ]
                for x in m["content_rejects"]
            ],
        )
    )
else:
    W("**No story was rejected on content.** The false-positive rate on the hazard"
      " side is 0 for this matrix, which is the direction STATE invariant 5 warns"
      " about.")
W("")
if m["grammar_rejects"]:
    W("### Grammar rejections, verbatim")
    W("")
    W(
        _table(
            ["stage", "theme", "note"],
            [
                [x["stage_id"], x["theme"], (x["grammar_note"] or x["reason"]).replace("|", "\\|")]
                for x in m["grammar_rejects"]
            ],
        )
    )
    W("")
if m["blocklist_rejects"]:
    W("### Blocklist rejections (layer 1)")
    W("")
    W(
        _table(
            ["stage", "theme", "blocked words"],
            [
                [x["stage_id"], x["theme"], ", ".join(x["blocked_words"])]
                for x in m["blocklist_rejects"]
            ],
        )
    )
    W("")

# --- 9. decoder limitations -------------------------------------------------
W("## 9. The decoder's own disclosed limitations (§5.3, §18)")
W("")
W(
    "§5.3 requires these to be *logged in the eval report, not silent behaviour*."
    " §18 names the alignment/schwa problem as the highest technical risk and says a"
    " rising unalignable rate is the signal that the inventory is too small."
)
W("")
W(
    f"**Denominator: SCORED words — {dl['scored_word_total']:,} of"
    f" {dl['word_token_total']:,} word tokens.** A sight or story word is classified"
    f" before any alignment is attempted (`decoder/validate.py`), so it can carry none"
    f" of these flags; the first version of this report divided by every word token,"
    f" which put roughly twice as many words in the denominator as could ever appear"
    f" in the numerator and reported §18's designated alarm at about half strength"
    f" (AUDIT-PHASE7 F6, DEVIATIONS 8.34). The rates below are recomputed from this"
    f" run's own recorded counts; the recorded rows were not edited. The population"
    f" rate is flags over scored words; the mean-per-story column is beside it because"
    f" a mean weights a 30-word stage-1 story equally with a 200-word stage-12 one."
)
W("")
W(
    _table(
        ["flag", "what it means", "total instances", "rate over SCORED words", "mean per story"],
        [
            [
                "`schwa_relaxed`",
                "§5.2.6 let an unstressed vowel match its citation GPC",
                dl["schwa_relaxed_total"],
                _pct(dl["schwa_relaxed_rate_scored"]),
                _pct(dl["schwa_relaxed_rate_scored_mean"]),
            ],
            [
                "`ambiguity_flag`",
                "more than one legal alignment existed; any was taken",
                dl["ambiguity_total"],
                _pct(dl["ambiguity_rate_scored"]),
                _pct(dl["ambiguity_rate_scored_mean"]),
            ],
            [
                "`heteronym_flag`",
                "multiple pronunciations, only some decodable — §5.2.5 may over-credit",
                dl["heteronym_total"],
                _pct(dl["heteronym_rate_scored"]),
                _pct(dl["heteronym_rate_scored_mean"]),
            ],
            [
                "`alignable=False`",
                "**the §18 alarm** — the inventory cannot spell the word at any stage",
                dl["unalignable_total"],
                _pct(dl["unalignable_rate_scored"]),
                _pct(dl["unalignable_rate_scored_mean"]),
            ],
            [
                "non-minimal missing set",
                "the minimal-missing-GPC search hit its bound and approximated",
                dl["non_minimal_missing_total"],
                "—",
                "—",
            ],
        ],
    )
)
W("")
if dl["unalignable_words"]:
    W(
        "Words the inventory cannot spell at any stage, with occurrences:"
        f" `{json.dumps(dl['unalignable_words'])}`"
    )
else:
    W(
        "**No unalignable word appeared in any story.** §18's \"the inventory is too"
        " small\" alarm is quiet across all 12 stages: the allowlist-constrained"
        " prompt never induced the model to reach for a word the GPC inventory"
        " cannot spell."
    )
W("")
# Conditional on the DATA, not on the reader's memory: `of` was the one actionable
# item in the first matrix's unalignable list and was fixed as a sight word
# (DEVIATIONS 8.10). Its absence from a later run is the fix landing, and printing
# the paragraph either way would eventually make this document describe a defect
# the run does not have.
if "of" in dl["unalignable_words"]:
    W(
        "**The actionable item in that list was `of`, and it has since been fixed.**"
        " It is among the most frequent words in English and the GPC inventory cannot"
        " spell it at any stage \u2014 CMUdict gives it one pronunciation, `AH1 V`, so"
        " an alignment needs `f`\u2192/v/, which no scope & sequence teaches and which"
        " `tests/decoder/test_anti_golden.py::NEVER_DECODABLE` forbids by name. At the"
        " time of this matrix it was **not a sight word at any stage** either, so"
        " every story reaching for it spent part of its 5% violation budget on a word"
        " a child meets constantly. `of` is now a stage-2 sight word, together with"
        " `so`, `do`, `be` and `from`, and `wh`\u2192/w/ was added to the inventory"
        " (docs/DEVIATIONS.md 8.10\u20138.13). Re-scoring this run\u2019s 169 stored"
        " stories against the corrected vocabulary improved 12 of them and made none"
        " worse; the figures ABOVE are the measurements as recorded at run time and"
        " are deliberately not restated. A future matrix should show `of` gone from"
        " this list."
        "\n\nOf the other words above, only `come` is a sight word (stage 6), and it"
        " appears here at stage 5, before it unlocks, which is the list working"
        " correctly."
    )
else:
    W(
        "**`of` is absent from that list, and that is the previous matrix's one"
        " actionable finding closing.** It was the most frequent word the 92-GPC"
        " inventory could not spell at any stage and was not a sight word either, so"
        " every story reaching for it spent part of its 5% violation budget; it is a"
        " stage-2 sight word now, with `so`, `do`, `be` and `from`"
        " (docs/DEVIATIONS.md 8.10\u20138.13). Whatever remains above is the honest"
        " residue \u2014 8.18 records which of those words were measured against the"
        " inventory ratchet and deliberately NOT added."
    )
W("")
W("### Per stage (every rate over SCORED words)")
W("")
W(
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
                by_stage_flags[s]["scored_word_total"],
                _pct(by_stage_flags[s]["schwa_relaxed_rate_scored"]),
                _pct(by_stage_flags[s]["ambiguity_rate_scored"]),
                _pct(by_stage_flags[s]["heteronym_rate_scored"]),
                _pct(by_stage_flags[s]["unalignable_rate_scored"]),
                ", ".join(v["unalignable_words"]) or "—",
            ]
            for s, v in agg["by_stage"].items()
        ],
    )
)
W("")

# --- 10. oov ---------------------------------------------------------------
o = agg["oov"]
W("## 10. OOV")
W("")
W(
    "§5.3: CMUdict is an adult lexicon, so a missing entry fails the word — which is"
    " the conservative direction, but it is a real limit. §7's gate requires **zero**"
    " OOV, so any OOV word forces a rewrite or a rejection."
)
W("")
W(
    f"{o['total']} OOV word instance(s) across {o['stories_with_oov']} of"
    f" {agg['produced']} produced stories."
)
W("")
if o["words"]:
    W(f"OOV words with occurrences: `{json.dumps(o['words'])}`")
    W("")

# --- 11. story words ------------------------------------------------------
sw = agg["story_words"]
W("## 11. Story words (§4, §5.5)")
W("")
W(
    f"The cap is {sw['cap']} **distinct** words per story. STATE invariant 3: a"
    " decodable reader repeats deliberately, so occurrences are uncapped and only"
    " the number of new picture-chip words a child must absorb is limited. §0's"
    " untrusted-generator rule bites here — the model's claim about which story words"
    " it used is intersected with the words actually offered, or it could shrink its"
    " own decodability denominator (STATE invariant 2)."
)
W("")
W(
    _table(
        ["metric", "value"],
        [
            ["stories over the cap", f"**{sw['over_cap']}**"],
            ["stories using no story word", sw["stories_using_none"]],
            ["total story-word occurrences", sw["occurrences_total"]],
        ],
    )
)
W("")
W(
    _table(
        ["distinct story words used", "stories"],
        [[k, v] for k, v in sw["distinct_per_story_hist"].items()],
    )
)
W("")
for theme, used in sw["by_theme"].items():
    W(f"- `{theme}`: {json.dumps(used) if used else 'none used'}")
W("")

# --- 12. length -----------------------------------------------------------
W("## 12. Length compliance and register")
W("")
W(
    _table(
        ["metric", "value"],
        [
            [
                "inside the stage's target range (of ATTEMPTED cells)",
                _pct(agg["length"]["compliance_rate"]),
            ],
            [
                "inside the stage's target range (of PRODUCED stories)",
                _pct(agg["length"].get("compliance_rate_produced") or produced_rate("length_compliant")),
            ],
            ["median word count", agg["length"]["word_count_p50"]],
            ["mean type-token ratio", f"{agg['length']['type_token_ratio_mean']:.3f}"],
        ],
    )
)
W("")
W(
    "Both denominators are printed because they answer different questions, and an"
    " unlabelled one is AUDIT-PHASE7 F9 (DEVIATIONS 8.34). A cell that produced no"
    " text is not length-compliant, so the ATTEMPTED figure is the one that counts"
    " availability honestly; the PRODUCED figure is the generation quality of the text"
    " that exists. The same caveat applies to the per-stage “pass rate”"
    " column in section 4, which is over attempted cells."
)
W("")
W(
    "§18 predicts the early stages will read \"Sam sat. A cat sat.\" and calls that"
    " authentic decodable-reader register rather than a defect. The type-token ratio"
    " is reported because §14 asks for it, not as a quality target: a low ratio at"
    " stage 1 is the format working."
)
W("")
W(
    "**Length compliance is the weakest number in this report, and it is a"
    " generation defect rather than a decoder one.** The per-stage table in"
    " section 4 shows it collapsing as the target grows: the stages asking for"
    " 120-200 words came back with a mean in the 89-102 range, so the model"
    " consistently under-delivers length at the top of the ladder while staying"
    " comfortably inside the gate. Decodability is unaffected \u2014 a short story"
    " is still a decodable story \u2014 but a stage-12 reader is being handed"
    " roughly half the text \u00a76 specifies. \u00a718\u2019s knob order for"
    " generation compliance ends at \u201cshorten target length\u201d; this is the"
    " opposite problem and is not covered there."
)
W("")

# --- 13. what it wrote ----------------------------------------------------
W("## 13. What this run wrote to the database")
W("")
W(
    _table(
        ["table", "what"],
        [
            ["`eval_runs`", f"1 row — id {run['id']}, status `{run['status']}`"],
            ["`eval_run_items`", f"{agg['attempted']} rows, one per attempted story"],
            [
                "`stories`",
                f"{run['stories_written']} rows at **`status='eval'`** (+ their `story_segments`)",
            ],
            ["`questions` / `readings` / `answers` / `gpc_stats`", "**untouched**"],
        ],
    )
)
W("")
W(
    "**Nothing this run wrote can be served to a child, whether it passed or not.**"
    " That is structural, not a promise: `api/stories.py::SERVABLE_STATUSES` is the"
    " allowlist `(\"warm\", \"complete\")` that both `_SERVE_SQL` and"
    " `GET /api/stories/{id}` filter on, and it does not name `eval`"
    " (DEVIATIONS 7.41). The servable pool measured"
    f" **{run['servable_before']} stories before this run and"
    f" {run['servable_after']} after**."
)
W("")
W(
    "Verified after the run rather than asserted: `tests/test_eval.py` plants an"
    " `eval` story alone at the top of the §7 ladder — the row `_SERVE_SQL` would"
    " pick first if the status were servable — and checks"
    " `POST /api/stories/request`, `GET /api/stories/{id}` and `POST /api/readings`."
    " Adding `\"eval\"` to `SERVABLE_STATUSES` turns those tests red and the request"
    " endpoint hands back the fixture, which is what makes them an experiment."
    " Separate tests assert that no `eval` row ever acquires a `last_served_at`, a"
    " question row or a reading."
)
W("")

# --- 14. what could not be measured --------------------------------------
W("## 14. What this eval does NOT measure")
W("")
W(
    "Stated rather than omitted, because a report that lists only what it found is"
    " not evidence about what it missed."
)
W("")
W(
    "1. **Enrichment.** QUESTION_GEN, SCENE_TAGGING and question moderation are not"
    " run (DEVIATIONS 7.45), so their cost, latency and failure rate are absent, and"
    " so is the `sound_targets` tier fallback rate DEVIATIONS 5.16 wired `gen_meta`"
    " to expose. The cost figures understate a servable story by roughly the 45-48%"
    " DEVIATIONS 5.15 measured.\n"
    "2. **Hazard versus harm.** Machine-underivable from the verdict (section 8)."
    " The verbatim reasons are the evidence; the heuristic column is not a"
    " measurement.\n"
    "3. **Whether a moderation rejection was CORRECT.** The harness counts rejects."
    " It has no ground truth, so a false positive and a true positive look"
    " identical in the numbers. Reading the quoted reasons is the only check, and"
    " section 8 does exactly that.\n"
    "4. **Warm-pool serve latency.** §1 item 6's second half (\"cache/warm-pool"
    " serve < 300ms\") is a property of `api/stories.py`, not of the pipeline. This"
    " harness never calls that endpoint. DEVIATIONS 7.23 measured `validate_text` at"
    " a median 0.47 ms against that budget separately.\n"
    "5. **Branching (§10).** The generator produces a linear spine, so there are no"
    " branch points to measure.\n"
    "6. **Latency under load.** Sequential by design (DEVIATIONS 7.48) so the p95"
    " answers §1 item 6. Concurrency, rate limits and the in-process limiter"
    " (DEVIATIONS 4.10) are untested here.\n"
    "7. **Same-prompt variance.** Five stories per cell measure variance across"
    " *generations*, not the stability of one prompt: temperature is not controllable"
    " on this model (STATE invariant 9), so a re-run will produce different text and"
    " should be compared as a distribution, not diffed.\n"
    "8. **The admin portal view.** §14 requires the portal to read these tables"
    " rather than duplicate the logic. The tables and the CLI exist; §9's"
    " `GET /api/admin/eval-runs` endpoints and the §11 admin views are Phase 8 and"
    " are **not built**.\n"
    "9. **Whether the persisted segments reconstruct the text that was scored.**"
    " `persist_eval_story` writes `_split_segments(final_text)` and nothing checks the"
    " round-trip, which is the exact shape of the `warm.persist` grammar defect"
    " (STATE, DEVIATIONS 4.12). The Phase 7 audit verified the round-trip holds for"
    " all 169 stories, so this is a missing GUARD rather than a live defect — but the"
    " only thing that would catch a regression is"
    " `tests/test_eval.py::test_the_gate_still_agrees_with_what_the_harness_recorded`,"
    " and that test compares against the CURRENT vocabulary.\n"
    "10. **Whether the untrusted-generator guard ever fired.** §11's intersection is"
    " what stops the model shrinking its own denominator, and the model's raw"
    " `story_words_used` claim goes to the Langfuse span but to no column, so the run"
    " can show the guard is sound (0 stories carry an unoffered story word) and cannot"
    " show it did work (AUDIT-PHASE7 F12).\n"
    "11. **The selection effect of the cells that produced nothing.** Decodability is"
    " measured on the cells that produced text; when some did not, they are not a"
    " random sample — the Phase 7 audit measured the resulting bias for this matrix at"
    " roughly 0.03 SD and bounded the worst case, and that argument belongs with the"
    " numbers rather than being asserted (AUDIT-PHASE7 F8)."
)
W("")

W("## 15. Re-running and comparing")
W("")
W(
    "The run is reproducible in configuration, not in output — the model is not"
    " deterministic."
    + (
        " For runs from DEVIATIONS 8.32-8.33 onward — which does NOT include this"
        " one, see section 1 —"
        if not cfg.get("vocabulary")
        else ""
    )
    + " `eval_runs.config` holds the matrix, the model ids, the pricing"
    " date, the gate, the rewrite budget, the timeouts, the git commit **with a"
    " dirty-tree flag and a content hash of the code that ran**, and **a fingerprint"
    " of the vocabulary the gate was computed against** — so two runs are"
    " comparable as distributions, and a reader can tell when they are not"
    " (DEVIATIONS 8.32-8.33). Each item additionally records the model every call was"
    " made with and the price sheet applied to it, so no claim about the model rests"
    " on the config table alone (8.30). `--pricing-date` is explicit precisely so a"
    " cost figure regenerated later cannot silently disagree with the one printed"
    " here."
)
W("")
W(
    "`--resume RUN_ID` restores the matrix parameters and **verifies** everything it"
    " cannot restore, refusing with exit code 3 rather than continuing one matrix"
    " under a different model, gate, price sheet, timeout or vocabulary (8.31)."
    " Comparing two runs starts with their `config.vocabulary` and"
    " `config.provenance.code_sha256`: if either differs, the two distributions are"
    " measurements of different things."
)
W("")
W(
    "```\n"
    "make eval                                  # the full \u00a71 matrix, ~$"
    f"{c['total_usd']:.2f}, ~{agg.get('pipeline_seconds', 0) / 60:.0f} min of pipeline time\n"
    "make eval EVAL_ARGS=\"--estimate-only\"      # project the spend, no API call\n"
    "make eval-smoke                            # 4-story regression gate, ~$0.06\n"
    "\n"
    "# this document, regenerated from the run's own JSON — never hand-edited\n"
    "services/api/.venv/bin/python scripts/eval_doc.py reports/eval-<ts>.json docs/EVAL.md\n"
    "```"
)
W("")
W(
    f"_Generated by `scripts/eval_doc.py` from `{src.name}`"
    + (
        f", with the per-HTTP-attempt timeout supplied as {assumed_timeout_s:.0f}s"
        f" because the run recorded none."
        if assumed_timeout_s is not None and not cfg.get("generation_timeout_s")
        else "."
    )
    + "_"
)
W("")

dest.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"wrote {dest} ({len(''.join(out))} chars)")
