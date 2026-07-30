# Phase 7 adversarial audit — the §14 eval harness and the deploy artifacts

Scope: `storyforge/eval.py`, `eval_runs`/`eval_run_items`, `docs/EVAL.md`,
`services/api/Dockerfile`, `.dockerignore`, `.do/app.yaml`, `docs/DEPLOY.md`,
§18's `DEMO_BYPASS_AUTH` guard. Report only — nothing outside this file was
changed.

**Method.** Every number in §14 was recomputed from `eval_run_items` with
arithmetic that does not import `storyforge.eval`, and every one of the 169
stored eval stories was re-validated by calling the decoder directly on the text
reconstructed from `story_segments`. Live HTTP work ran against my own uvicorn on
port 8050 with `DEMO_BYPASS_AUTH=false`; `garbage token → 401` and
`x-storyforge-auth: enforced` were confirmed before any conclusion was drawn from
a response, and the authenticated probes used a real Clerk session token
(`public_metadata.role = admin`) minted from a sign-in ticket. That session was
revoked and the revocation verified (`401` afterwards). The API image was built
and run. `--resume` was exercised against a throwaway copy of the database
(`sfaudit`, since dropped), never against the audited rows. No API spend: no
`make eval`, `make warm` or `make precompute`. Final state check: `stories` =
45 warm / 7 complete / 13 rejected / 169 eval, `eval_runs` = 1, `eval_run_items`
= 180 — identical to the state at the start.

---

## Verdict on §0's thesis

**Supported on decodability, and the gate really is the reason. The *durability of
the evidence* is materially weaker than the documents claim, and one of those
claims is false.**

What I independently established, not read:

- All 169 stories re-validate with `decoder.validate_text` at or above 95% at
  their own stage. min 95.0%, p05 96.0%, p50 100%, mean 99.358%, max 100%.
- 0 OOV instances. 0 stories over the 3-distinct-story-word cap. 0 stories carry
  a story word that was not in the offered bank for their (theme, stage) — the
  denominator-inflation attack from STATE invariant 2 does not land.
- 145 of 169 passed on the first draft at rewrite depth 0. p95 depth 1, max 2.
  `outcomes` contains no `budget_exhausted`: **not one story ever exhausted the
  rewrite budget**, so the gate was never the binding constraint. That is the
  §6 lever-order claim holding.
- The harness's decodability denominator is byte-for-byte the serving path's.
  Harness: `pipeline.validate_draft` → `validate_text(body, mastered_ids(stage),
  sight_words, claimed ∩ offered)`. Serve: `stories.py:532 _is_decodable_for` →
  `validate_text(body, mastered_ids(reader_stage), sight, dto.story_words)`. Both
  score `decodable + violation + oov` and exempt sight and story words
  (`decoder/validate.py:89`). No quiet difference.
- Total cost $2.7958 and per-item costs reproduce exactly from the recorded token
  counts (every one of the 180 items to under 2e-6). The 2026-09-01 repricing
  reproduces at $4.0545, 1.450x.
- The servable pool is independently clean: all 52 servable stories re-validate
  above the gate at their own stage, none carries an unoffered story word, none
  has `well_formed = false`, none is missing a `well_formed` field, none has zero
  questions. The grammar defect has not regrown.

The two caveats. **(1)** The claim covers 169/180 = 93.9% of the matrix and 100%
of the missing mass sits at the four hardest stages (F8). I quantified the
resulting bias and it is small — but EVAL.md asserted the independence instead of
measuring it. **(2)** The harness records no per-story model, pins no vocabulary
version, and names a git commit at which the harness does not exist; its
`--resume` path takes behaviour from the environment while overwriting the record
of it with the stored values. Fourteen minutes after I reproduced all 169
decodability figures exactly, 11 of them no longer reproduced, because a parallel
agent re-seeded `sight_words` (F3). The project's own test caught that. It is red
right now, and it is the only thing standing between this repo and its fourth
"the metric was computed and discarded" entry.

So: the gate works, and §14 is the best-evidenced claim in this repo. What is
**not** true is EVAL.md's stronger meta-claim that the numbers are protected from
drift by the code.

---

## The claim in EVAL.md / STATE.md / DEVIATIONS.md that is currently false

Two, with one root, plus a third that the harness *generates*.

> `docs/EVAL.md:13` — "`--resume` ran only the remainder, against the **stored**
> config — same model, same gate, same `n`, same pricing date, **enforced by the
> code rather than by care**."

> `docs/EVAL.md:15` — "re-finalising it reproduced the identical verdict and exit
> code — **which is the cheapest available check that the report is a pure
> function of the data**."

> `storyforge/eval.py:891-892` — `render_report` emits, unconditionally, into
> every resumed report: "`--resume` ran only the remainder against the SAME
> stored config — same model, same gate, same pricing date."

Neither the model nor the gate is restored on resume, and the report is not a
pure function of the data. See F1 for the reproduction.

A fourth, in `docs/STATE.md:15` — "`make warm` fills ≥3 per (stage,theme)" — is
false in a way that has direct user consequence on a first deploy. See F4.

---

# CONFIRMED findings, ranked by real user harm

## F1 — HIGH. `--resume` does not enforce the model or the gate; it overwrites the record of them. One supported command produces a self-contradicting report.

**Observed.** Copied the database to a throwaway `sfaudit`, then:

```
ANTHROPIC_MODEL_GENERATE=claude-opus-4-8 \
DATABASE_URL=postgresql+psycopg://…/sfaudit \
  python -m storyforge.eval run --resume 2 --out /tmp/sfaudit/out
```

Run 2 is complete (180/180 recorded), so no API call was possible and nothing was
spent. The regenerated report contains, in one file:

| line | says |
|---|---|
| config table | `generation model` = **`claude-sonnet-5`** |
| config table | `generation price` = **$2.0/$10.0 per Mtok** |
| cost section | "Same run priced after the Sonnet 5 introductory rate ends (2026-09-01, **$3/$15**): **$6.5718** (x2.35)" |
| cost section | "Pre-flight projection was **$7.45** with no rewrites, $14.90 with one each, **$29.80** worst case" |

$6.5718 is Opus pricing ($5/$25) on the recorded token counts; the committed
figure for the same tokens is $4.0545. $7.45/$29.80 is the Opus projection; the
stored config still holds $3.2159/$12.8634. The sentence hard-codes the string
"$3/$15" while printing a number computed at neither $2/$10 nor $3/$15. The
console banner printed `models claude-opus-4-8 (generation)` — so the invocation
knew, and the report it wrote does not.

**Expected.** Either the resume refuses when the environment's model differs from
the stored one, or the report says which model each half of the matrix used.

**Mechanism.**

- `eval.py:1462-1489` computes `models`, `prices`, `gate`, `max_rewrites`,
  `max_story_words`, `prompt_word_limit`, `concurrency`, `budget_usd`, `cold`,
  `enrich`, `git_commit` fresh from the environment and from module constants.
- `eval.py:1490-1502` then does `config = {**resumed_config, "resumed": trail}`,
  discarding every one of them. The stored config wins the **record**, not the
  **behaviour**.
- Only five values are actually restored (`eval.py:1439-1446`): `stages_spec`,
  `themes_spec`, `n`, `effort`, `pricing_date`.
- The behaviour comes from `llm/client.py:39-41 generation_model()` reading
  `ANTHROPIC_MODEL_GENERATE` on every call, reached through
  `_attempt → run_pipeline → _call` (`eval.py:1756`, `pipeline.py:336`,
  `pipeline.py:250`) — there is no model parameter anywhere in that chain — and
  from the module constant `pipeline.py:43 DECODABILITY_GATE`.
- `eval.py:1659 _reprice(items, date(2026,9,1))` → `eval.py:1714
  generation_model()`: the post-intro cost is a function of the current shell,
  not of the run. `eval.py:1117` hard-codes the "$3/$15" prose beside it.

**Aggravating: there is no per-item record of the model.** Verified:
`SELECT count(*) FROM eval_run_items WHERE run_id=2 AND metrics ? 'model'` = 0,
and `stories.gen_meta` for eval rows holds only
`{source, eval_run_id, passed_gate, rewrite_depth, cost_usd, attempts,
moderation, schwa_relaxed, heteronyms, unalignable}`. By contrast `gate` and
`pricing_date` **are** per-item (all 180 rows: `0.95` / `2026-07-29`), so a
mid-matrix gate change is auditable after the fact. A mid-matrix model change is
not — and this run was finalised **three** times, twice after the fact
(`config.resumed` has three entries).

**Why it matters despite the committed numbers being correct.** They are correct
— I verified $2.7958 and $4.0545 independently. The defect is that the guarantee
the documents advertise does not exist, and the mechanism advertised as the guard
actively destroys the one artifact that would reveal drift. This is the same
shape as the three failures the repo already logged: a verdict computed and then
discarded on write.

`file:line` — `eval.py:1439-1446`, `eval.py:1462-1502`, `eval.py:1659`,
`eval.py:1707-1730`, `eval.py:1117`, `eval.py:891-892`, `EVAL.md:13`, `EVAL.md:15`.

---

## F2 — HIGH. `eval_runs.config.git_commit` names a commit at which the harness cannot run.

**Observed.**

```
$ git show 559f102:services/api/storyforge/eval.py | wc -l
102
$ git show 559f102:services/api/storyforge/eval.py | grep add_parser
    s = sub.add_parser("smoke", help="§14 regression gate: 2 stages x 1 theme x 2 stories")
$ git show 538a08b:services/api/storyforge/eval.py | wc -l
1986
```

`eval_runs.config.git_commit` = `559f102`, printed in `docs/EVAL.md:35` as "git
commit `559f102`". At that commit `eval.py` is 102 lines and has **only a `smoke`
subparser** — the command EVAL.md §1 prints as the reproduction
(`python -m storyforge.eval run --stages 1..12 …`) exits with an argparse error
there. The 1986-line `run` harness landed in `538a08b`, the *next* commit.

**Expected.** A provenance commit that contains the code that produced the run,
or a recorded dirty-tree flag.

**Mechanism.** `_git_commit()` (`eval.py:1372-1382`) records
`git rev-parse --short HEAD` and does not consult the working tree. Every number
in `docs/EVAL.md` was produced by uncommitted code, and nothing in the record
says so. `docs/EVAL.md:329` offers `eval_runs.config` including "the git commit"
as the basis on which "two runs are comparable"; for this run that anchor is
wrong.

`file:line` — `eval.py:1372-1382`, `EVAL.md:37`, `EVAL.md:329`.

---

## F3 — HIGH. `eval_runs.config` pins no vocabulary version, and 11 of the 169 recorded decodability figures are already wrong against the live database. A test is red because of it.

**Observed, twice, fourteen minutes apart.**

At 00:44 UTC my independent re-validation of all 169 eval stories matched the
recorded `stories.decodability` and `eval_run_items.metrics.decodability`
exactly — 0 mismatches. At 00:58 UTC the same script reported **11 mismatches**:

| story | stage | theme | recorded | recomputed now |
|---|---|---|---|---|
| 3646 | 8 | animals | 0.9744 | 1.0000 |
| 3648 | 8 | animals | 0.9737 | 1.0000 |
| 3652 | 8 | dragons | 0.9623 | 0.9808 |
| 3733 | 8 | space | 0.9737 | 1.0000 |
| 3737 | 9 | animals | 0.9677 | 0.9783 |
| 3751 | 10 | animals | 0.9630 | 1.0000 |
| 3752 | 10 | animals | 0.9722 | 1.0000 |
| 3759 | 10 | space | 0.9792 | 1.0000 |
| 3761 | 11 | animals | 0.9714 | 0.9855 |
| 3777 | 12 | dragons | 0.9500 | 0.9661 |
| 3781 | 12 | space | 0.9592 | 0.9792 |

Cause: a parallel agent re-seeded `sight_words` from 33 rows to 38 (`of`, `so`,
`do`, `be`, `from`; DEVIATIONS 8.10/8.11) and `gpcs.csv` from 92 to 93 rows.

The project's own guard caught it and is currently failing:

```
FAILED tests/test_eval.py::test_the_gate_still_agrees_with_what_the_harness_recorded
E   AssertionError: story 3646: recorded 0.9744, gate says 1.0
```

**Expected.** `eval_runs.config` should carry the vocabulary the gate was
computed against — a `sight_words` count, a hash of `gpcs.csv`, an
`allowed_words` row count — so a report can detect its own staleness. It carries
the model, prices, gate, rewrite budget, prompt allowlist size, python version
and git commit, and none of those.

**In fairness:** all 11 moved *up*, so §14's numbers are conservative against the
current vocabulary, and the drift was caught by a test that exists precisely
because of the grammar incident. Two knock-ons:

1. `docs/EVAL.md:243`'s parenthetical — "checked directly against the
   `sight_words` table, **all 33 rows** — it is **not a sight word at any
   stage**" — is now false. The table has 38 rows and `of` is a stage-2 sight
   word (`SELECT * FROM sight_words WHERE word='of'` → `stage_id 2`). The §9
   recommendation has already been actioned; the doc's snapshot claim has not
   been updated. This one is *staleness from concurrent work*, not author error.
2. That test asserts an equality that cannot hold across any vocabulary change,
   so it stays red until `make eval` is re-run. A tripwire with no reset is the
   kind that gets "fixed" by weakening the assertion. It should be re-run or the
   assertion should be scoped to the vocabulary the run recorded — which requires
   fixing the recording first.

`file:line` — `eval.py:1464-1489` (config, no vocabulary key),
`tests/test_eval.py:727-766`, `EVAL.md:243`, `EVAL.md:329`.

---

## F4 — MEDIUM–HIGH. `make warm` fills 3 of 12 stages and 1 of 3 themes. This is the first-deploy step DEPLOY.md tells the operator to run.

**Observed.** `warm.py:1413-1414`:

```python
p.add_argument("--stages", default="1,2,3", help="comma-separated stage ids")
p.add_argument("--themes", default="dragons", help="comma-separated theme slugs")
```

`Makefile`'s `warm` target passes no arguments:
`cd $(API) && uv run python -m storyforge.warm`. With `TARGET_PER_PAIR = 3`
that is **9 stories, stages 1-3, dragons only**.

`docs/DEPLOY.md:128` tells the first-deploy operator to run exactly
`python -m storyforge.warm` and describes it as "pre-generate the story pool
(§7)". `docs/STATE.md:15` says "`make warm` fills ≥3 per (stage,theme)".

**Consequence.** On a fresh deploy every child at stage 4 or above, and every
child who picks animals or space, is served a fallback story. §17's demo item 3
is "pick dragons at stage 3" — precisely what the default covers — so the demo
passes while 9 of 12 stages hold no content. §16's Phase 7 DoD is "Hosted demo
passes §1 items 1-5".

**Live evidence of exactly that shape in the current database:** the servable
pool holds stages 1-5 only; stages 6-12 have zero servable stories. As an
authenticated admin against an auth-enforced server:

```
POST /api/stories/request {"stage_id":12,"theme_id_or_slug":"dragons"}
  -> story 1688, stage_id 5, status warm, fallback "lower_stage"
POST /api/stories/request {"stage_id":8, …}  -> story 19,  stage 5, "lower_stage"
POST /api/stories/request {"stage_id":6, …}  -> story 1688, stage 5, "lower_stage"
```

The ladder behaves correctly (it never climbs). The pool is the problem.

`file:line` — `warm.py:1413-1414`, `warm.py:85`, `Makefile` (`warm` target),
`DEPLOY.md:122-128`, `STATE.md:15`.

---

## F5 — MEDIUM. §9's Anthropic health check does not exist, and DEPLOY.md's verification step asks for a state the API cannot produce.

**Observed** on the real built image with `.do/app.yaml`'s exact production-shaped
environment:

```json
{"status":"ok","demo_bypass_auth":false,
 "components":{"database":{"status":"ok","detail":"postgres 16.14"},
 "decoder":{"status":"ok","detail":"cmudict 126052 entries, 93 gpcs"},
 "anthropic":{"status":"pending","detail":"reachability check lands in phase 3"}}}
```

`health.py:76-79` returns `pending` unconditionally — even with a key configured.
§15 specifies "`/api/health` checks DB + CMUdict load + **Anthropic reachability
(cheap models list call, cached 5 min)**". It is not implemented, and at the end
of Phase 7 the detail string still reads "lands in phase 3".

`docs/DEPLOY.md:162` instructs the operator:
`curl -s $APP/api/health | jq  # every component "ok"; "error" anywhere = broken`.
No deploy can satisfy that. An operator following it verbatim either concludes
the first deploy is broken, or learns to stop reading the health body — which is
the exact habit DEPLOY.md §6 spends a paragraph trying to instil ("Read the JSON
yourself after a deploy, do not trust the green dot").

`file:line` — `health.py:76-79`, `DEPLOY.md:162`, `DEPLOY.md:167-171`.

---

## F6 — MEDIUM. §14's "decoder's own disclosed limitations" rates are computed over a denominator that includes words the decoder never scores, so §18's designated alarm reads at roughly half strength.

**Observed.** `story_metrics` (`eval.py:311-317`) divides every flag count by
`n_words` — all non-punctuation word tokens. Sight and story words are
classified before any alignment is attempted (`decoder/validate.py:33-36`) and can
therefore carry none of these flags, but they are in the denominator. The
population that *can* carry them is the scored set, which is also the
decodability denominator.

Across the run: 10,690 word tokens, 5,597 scored words.

| flag | total | EVAL.md §9 reports | over SCORED words |
|---|---|---|---|
| `ambiguity_flag` | 1272 | 12.4% | **22.7%** |
| `schwa_relaxed` | 443 | 4.0% | **7.9%** |
| `heteronym_flag` | 377 | 3.1% | **6.7%** |
| `alignable=False` | 28 | 0.19% | **0.50%** |

**Why this is the one that matters.** §18 names aligner ambiguity and schwa the
highest technical risk, and says "a rising unalignable rate is the signal that the
inventory is too small". That signal is being reported at ~0.5x. EVAL.md §9's
column headers ("total word instances", "mean rate per story") do not say which
denominator, and the report uses one denominator for decodability and a different
one for these flags in the same document without naming either.

`file:line` — `eval.py:311-317`, `decoder/validate.py:87-90`, `EVAL.md:233-241`.

---

## F7 — MEDIUM. The latency table borrows the availability defect's number, which EVAL.md's own preamble promises it does not do.

**Observed.** `docs/EVAL.md:5-7` promises: "this report is written so that neither
reading can borrow the other's number". `docs/EVAL.md:177` then reports
"GENERATING (first attempt) p50 13016 ms / p95 **58279 ms**", and `EVAL.md:183`
concludes "the miss is concentrated in the long-story stages, where the target
length is 120-200 words".

`Attempt.latency_ms` is wall time around `_call` (`pipeline.py:335-337`), and
`_call` goes through an SDK client with `max_retries=2` (`llm/client.py:77`) under
a 30 s per-request timeout at run time. The row therefore measures up to **three
HTTP attempts**, not one generation. "first attempt" means the first *pipeline*
attempt (n=0); nothing in the report says so.

Measured from the recorded items:

| | value |
|---|---|
| produced stories with GENERATING > 30 s (≥1 dead timeout) | **23 / 169 (13.6%)** |
| same, stages 9-12 only | **20 / 49 (40.8%)** |
| max GENERATING | 86,554 ms — only reachable as 3 × ~29 s |
| GENERATING p95 over the 146 stories that needed no retry | **26,433 ms** (vs 58,279 reported) |
| whole-pipeline p95, no-retry subset | **30,477 ms** (vs 59,807 reported) |

So roughly half the reported p95 is dead time from the same 30 s timeout the
report separately and correctly files as the availability defect, and the stated
cause ("long stories are slow") is not the measured one. The §1 item 6 miss is
real either way — 26.4 s still fails a 12 s target — but the report attributes it
to the wrong thing and double-counts one defect as two.

`file:line` — `pipeline.py:335-337`, `llm/client.py:77`, `eval.py:341`,
`EVAL.md:5-7`, `EVAL.md:177`, `EVAL.md:183`.

---

## F8 — LOW–MEDIUM. The selection effect is real, is entirely concentrated in the hardest stages, and is smaller than it looks. EVAL.md asserts the independence rather than measuring it.

**Observed.** All 11 non-producing cells are `APITimeoutError` at stages 9-12:

| | attempted | api_fail |
|---|---|---|
| stages 1-8 | 120 | **0 (0.0%)** |
| stages 9-12 | 60 | **11 (18.3%)** |

per stage: 9 → 4, 10 → 2, 11 → 2, 12 → 3. Decodability was therefore measured on
**169/180 = 93.9%** of the matrix with 100% of the missing mass at the four
hardest stages.

There is a plausible upward-bias mechanism, and I tested it:

| | value |
|---|---|
| corr(word_count, decodability), stages 9-12 | **−0.268** — longer is less decodable |
| corr(word_count, latency), stages 9-12 | **+0.113** — only weakly |
| corr(latency, decodability), stages 9-12 | +0.012 |

The selection is on wall time, and wall time barely tracks length at those
stages, so the expected inflation is on the order of 0.03 SD. And 0 of the 49
produced high-stage stories finished below the gate, so a same-distribution draw
for the missing 11 predicts 0 below-gate.

For completeness, the bound: had all 11 scored 0%, mean decodability falls from
99.36% to **93.29%** and "0 below gate" becomes 11/180.

**The finding is about reasoning, not arithmetic.** `EVAL.md:7` says the
availability failure "is not evidence against §0's thesis" and moves on. It is a
testable proposition and the test was available in the recorded data. The
conclusion survives; the argument did not exist.

`file:line` — `EVAL.md:7`, `EVAL.md:64`, `eval.py:374`.

---

## F9 — LOW. Three more content metrics use a denominator that includes the 11 empty cells.

`eval.py:616-618` (`length.compliance_rate`) and `eval.py:438` / `eval.py:470-472`
(`_slice`'s `gate_pass_rate` and `length_compliance_rate`) divide by **all
attempted**, including the 11 `api_fail` cells whose `word_count` is 0 and
`length_compliant` is `False`.

- `EVAL.md:293` reports length compliance 52.2% = 94/180. Over produced stories
  it is **55.6%** (94/169).
- `EVAL.md:125`'s stage-9 "pass rate" of 73.3% is 11 accepted / 15 attempted,
  while 11 of the 11 stories that produced text were accepted — 100%.

Direction is unflattering, so nothing is inflated. But it is the same class as F7
in three more places, and it means the §4 per-stage table cannot be read as a
content-quality table.

`file:line` — `eval.py:438`, `eval.py:470-472`, `eval.py:616-618`,
`EVAL.md:117-128`, `EVAL.md:293`.

---

## F10 — LOW. `docs/EVAL.md:27` "`make eval` runs exactly that line" is false, in the one way that matters for cost.

The printed line carries `--effort medium --pricing-date 2026-07-29 --budget 15.0
--concurrency 1`. The Makefile's `eval` target passes only
`--stages 1..12 --themes all --n 5 --cold`. Three of those match the defaults;
`--pricing-date` does not — it defaults to `date.today()` (`eval.py:1959`). Run
`make eval` on or after 2026-09-01 and every cost figure moves 1.45x with no flag
change, which is exactly the silent disagreement `--pricing-date` was introduced
to prevent (`EVAL.md:329`).

Related: on a resume, `render_report`'s "Reproducing this run" block
(`eval.py:836-844`) prints `--budget` and `--concurrency` from the *stored*
config, while the resumed invocation used the CLI values for both — so the
reproduce line can misstate the concurrency part of the matrix ran at.

`file:line` — `EVAL.md:22-27`, `Makefile` (`eval` target), `eval.py:1959`,
`eval.py:836-844`.

---

## F11 — LOW. The §18 bypass guard holds against every DigitalOcean signal and is defeatable three other ways. Two of the three contradict a code comment.

**The guard is strong where it counts.** Tested against the *built image*
(`DEMO_BYPASS_AUTH=true` in each case) — all four **refused to boot**:

| configuration | result |
|---|---|
| `ENVIRONMENT=production` | REFUSED |
| `APP_URL` alone | REFUSED |
| remote DB host | REFUSED |
| **nothing else set at all** | REFUSED — the Dockerfile's own `ENV PORT=8080` trips `SERVING_HINT_VARS` |

In-process it also refuses on `ENVIRONMENT=prod-us`, `ENVIRONMENT=PRODUCTION`,
a `db`-hostname DSN, `DATABASE_URL` at `0.0.0.0`, an `sk_live_` Clerk key, and
`DEMO_BYPASS_AUTH=1`. `docs/DEPLOY.md:177-183`'s claim for DO is sound: the spec
supplies three independent tripwires (`ENVIRONMENT=production`, DO's
`APP_URL`/`APP_DOMAIN`, the remote database).

**Defeats.**

1. `docker run -e DEMO_BYPASS_AUTH=true -e PORT= -e DATABASE_URL=…localhost…`
   **boots a fully unauthenticated API.** Blanking `PORT` removes the one signal
   the image itself supplies (`(os.environ.get(name) or "").strip()` is empty →
   no signal, `auth.py:623-629`), and `--port ${PORT:-8080}` still serves.
   Reproduced: the container stayed up and had to be killed.
2. `ENVIRONMENT=prd` → BOOTED bypassed.
3. `ENVIRONMENT=pre-production` → BOOTED bypassed.

(2) and (3) are not in `DEPLOYED_ENV_VALUES` and do not match
`DEPLOYED_ENV_PREFIXES = ("prod","stag","live","preview")` (`auth.py:491-498`),
despite the comment there claiming the prefix list closed that family after
`prod-us`. `prd` and `pre-production` are ordinary spellings.

DEVIATIONS 7.36 already names the negative-signal design as the weakness and the
inverse control (assert-local + refuse on `0.0.0.0`) as the follow-up, so this is
an unclosed known gap, not an undisclosed one.

`file:line` — `auth.py:491-498`, `auth.py:551`, `auth.py:623-629`,
`Dockerfile` (`ENV PORT=8080`), `DEPLOY.md:177-183`.

---

## F12 — LOW. The one place §0's untrusted-generator rule bites cannot be audited from the data.

`docs/EVAL.md:270` presents the story-word intersection as the guard against
denominator inflation, and STATE invariant 2 records it as live-observed. The
model's raw `story_words_used` claim is written to the Langfuse span
(`pipeline.py:343`, `pipeline.py:350`) but to no column: `eval_run_items.metrics`
records only `distinct_story_words` (already intersected) and
`story_words_offered` (a count).

So the run cannot show that the guard fired even once. I confirmed independently
that it is *sound* — 0 of 169 stories carry a story word absent from the offered
bank for their (theme, stage), and 0 stories exceed the 3-distinct cap, so the
exemption is bounded by the cap and cannot be widened. But "the guard is sound"
and "the guard did work" are different claims, and only the first is checkable
from the tables §14 says the admin portal will read.

`file:line` — `pipeline.py:171-192`, `pipeline.py:343-350`, `eval.py:270-308`,
`EVAL.md:270`.

---

## F13 — LOW. Gaps `docs/EVAL.md` §14 does not admit.

§14 lists eight. Beyond F6, F7 and F9, these are absent from the list:

1. **No per-story model record** (F1) — so the model claim is unverifiable after
   the fact.
2. **No vocabulary version pinned** (F3) — the numbers are already stale.
3. **The harness never verifies its own write-back.** `persist_eval_story`
   (`eval.py:787-788`) writes `_split_segments(result.final_text)` and nothing
   checks that the segments reconstruct the text that was scored. That is the
   exact shape of the `warm.persist` grammar defect. I verified the round-trip
   holds for all 169 (decodability recomputed from segments matched to 4 dp at
   first measurement), so this is a missing guard, not a live defect — but
   `tests/test_eval.py:727` is the only thing that would catch it, and it does so
   only against the current vocabulary.
4. **`_reprice` fails silently.** `eval.py:1717-1718` returns `0.0` on `KeyError`,
   so an unpriced model reports a $0.00 September cost rather than declining to
   price it.

---

# Categories that are genuinely clean

Stated plainly rather than padded.

**Can an eval story reach a child? No. Attacked four ways on an auth-enforced
server with a real Clerk session, plus sabotage-verified.**

| attack | result |
|---|---|
| `GET /api/stories/3237` / `3500` / `3785` (eval) | **404** each; warm story 1661 → 200 |
| `POST /api/stories/request {stage_id: 6,8,10,12}` as admin — the warm pool is **empty above stage 5**, so the only stories at stages 6-12 are eval rows, and `_SERVE_SQL` orders `stage_id DESC` | served stage-5 **warm/complete** every time, `fallback: lower_stage`. Never an eval row. |
| `POST /api/readings {story_id: 3237}` / `{3785}` | **404** `"story … not found"` (`readings.py:600`) |
| warm-pool rotation | 0 of 169 eval rows has a `last_served_at`, 0 readings, 0 questions, before and after every probe |

Sabotage-verified, which is what makes it an experiment: I copied the tree to
`/tmp`, changed **only** `SERVABLE_STATUSES` to `("warm","complete","eval")`, and
three tests went red —
`test_the_eval_status_is_not_in_the_servable_allowlist`,
`test_an_eval_story_is_never_served_by_the_request_endpoint`,
`test_an_eval_story_is_not_fetchable_by_id` — the last returning the planted
fixture over HTTP: `{"id":4221,…,"status":"eval",…}`. DEVIATIONS 7.41's and
EVAL.md §13's claims hold. The repo working tree was not touched; test fixtures
rolled back cleanly (0 `zz-` themes, 0 stories above id 3785 afterwards).

**§8 mastery and the promotion signal: uncontaminated.** 0 readings, 0 questions
and 0 `gpc_stats` rows reference an eval story. `POST /api/readings` refuses a
non-servable status (`readings.py:600`) before the stage check.
`complete_reading` only promotes `warm → complete` (`readings.py:903`), so an
eval row cannot change status through the reading path. And the cross-
contamination path I went looking for is closed: `make warm --backfill` and
`--grammar-backfill` both default `target_status=SERVABLE_STATUSES`
(`warm.py:1152`, `warm.py:1286`), and the pool-fullness count filters
`status = 'warm'` (`warm.py:111-112`) — so the 169 eval rows do not make `make
warm` believe stages 6-12 are already full.

**Cost accounting: correct, everywhere I could check it.** DEVIATIONS 7.50's fix
is in place and complete. `PipelineResult.total_cost_usd` includes MODERATING
(`pipeline.py:97-101`); `story_metrics` records `generating_and_rewriting`,
`moderating` and their sum (`eval.py:350-354`); `aggregate` sums the item totals
(`eval.py:389-391`); `persist_eval_story` stores `total_cost_usd`
(`eval.py:756`). Independent verification: **every one of the 180 items'**
recorded cost equals a from-scratch token recompute to under 2e-6, and the run
total reproduces at $2.7958 with the September reprice at $4.0545 (1.450x).
Blocklist short-circuits honestly record $0 moderation (no `usage` → `0.0`,
`pipeline.py:107-111`); moderation uses no prompt cache, so `_reprice`'s use of
`total_input_tokens` at 1x is right. Enrichment exclusion is **hardcoded**
(`enrich=False`, `eval.py:1972`) rather than flag-dependent, and disclosed in
three places (`EVAL.md:9`, `:171`, `:318`). The intro-price cutover is correct:
`on <= date(2026,8,31)` (`pricing.py:46`), so 08-31 is intro and 09-01 is
standard. The only cost defect is `_reprice`'s environment dependence (F1).

**Secrets: clean, checked exhaustively.** 332 blobs reachable from all 94
commits, both branches, all 78 conductor checkpoint refs and the reflog were
scanned for `sk-ant-`, `sk_test_`, `sk_live_`, `pk_test_`, `pk_live_`, `lf_pk_`,
`lf_sk_`, `AVNS_`, `ghp_`, `glpat-`, `AKIA…` and for
`(KEY|SECRET|TOKEN|PASSWORD)\s*[=:]\s*<16+ chars>`. Zero credential hits; the ten
pattern matches are all the local dev DSN (`storyforge:storyforge@localhost`) or
Python identifiers named `token`. `.env` was never tracked — only `.env.example`.

**The image: builds, boots, leaks nothing.** `docker build -f
services/api/Dockerfile .` succeeds. The **real exported build context** contains
only `.dockerignore`, `.gitkeep`, `data/`, `services/` — no `.env`, no
`node_modules`, no `.venv`, no `*.pem`, no `*.key`. Inside the image: no
credential env var, nothing in `docker history` matching a key pattern, and the
`data/seed/gpcs.csv` relative symlink resolves (`# Master GPC inventory
(Appendix B)…`). Run against the compose database with `.do/app.yaml`'s exact
placeholder environment plus `ENVIRONMENT=production` and DO's
`APP_URL`/`APP_DOMAIN`, it answers `/api/health` 200 with `status: ok`, reports
`demo_bypass_auth: false` and `x-storyforge-auth: enforced`, 401s `/api/kids`,
and its own `HEALTHCHECK` reaches `healthy`. **A first deploy will come up
green before any secret is replaced** — which is what DEPLOY.md's create-then-set-
secrets order requires.

Two smaller notes on the deploy artifacts, neither a failure:

- `.do/app.yaml` ships `LANGFUSE_PUBLIC_KEY: REPLACE_WITH_LANGFUSE_PUBLIC_KEY`
  and `LANGFUSE_SECRET_KEY: REPLACE_IN_DO_DASHBOARD` — both **non-blank**.
  `obs._resolve_client` disables tracing only when a key is *blank*
  (`obs.py:59`), so the shipped spec yields `obs.enabled() → True` and logs
  "Langfuse tracing enabled (https://cloud.langfuse.com)" against placeholder
  credentials. Verified live. DEVIATIONS 4.17's "blank is a supported no-op" is
  not the path the spec takes; exports will fail on a background thread while
  the log says tracing is on. Shipping those two blank (or omitting the public
  key) would restore the documented behaviour.
- `doctl` is not installed on this machine, so `doctl apps spec validate
  .do/app.yaml` — the first command in DEPLOY.md §1, and offline with
  `--schema-only` — has never been run. Residual risk, not a found defect.

---

# SUSPECTED (reasoned, not triggered)

- **S1.** A resume with `--concurrency N > 1` would run part of the matrix
  concurrently while `config.concurrency` records the stored `1`, mixing two
  latency populations under a report whose §14 item 6 says "Sequential by design
  (DEVIATIONS 7.48) so the p95 answers §1 item 6". Same root as F1:
  `concurrency` is computed at `eval.py:1473` and discarded at `eval.py:1490-1502`
  while `eval.py:1538` builds the semaphore from the CLI value. Not triggered —
  it needs real API calls.
- **S2.** A resume with `--themes all` after a new theme is seeded recomputes
  `cells_planned` larger (`eval.py:1459`) while `config` restores the old value;
  `aggregate` is passed the new one (`eval.py:1646`). The "the matrix completed"
  criterion would then compare a new denominator against a config recording an
  old themes list. Needs a mid-run seed change.
- **S3.** `eval_runs` has a single row at `id = 2`; `eval_run_items` is at
  sequence 3184 for 180 rows. A prior run row and roughly 3000 item rows were
  deleted at some point. `EVAL.md:305`'s "`eval_runs` | 1 row — id 2" is true of
  the table; whether anything was lost that mattered is not recoverable. Noted,
  not diagnosed.

---

# Cleanup performed

Clerk session revoked and the revocation verified (the token 401s afterwards);
the sign-in ticket could not be revoked because it had already been consumed
(single-use, `sign_in_token_cannot_be_revoked_code`). Scratch database `sfaudit`
dropped. Audit uvicorn on port 8050 stopped. Audit containers removed and
`sfaudit-api:local` deleted. One accidental symlink I created (`data/data`)
removed. Database row counts verified identical to the start. The only repo
change is this file.
