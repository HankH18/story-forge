# Adversarial audit — the Phase 7 fix batch (DEVIATIONS 8.x) and the §14 re-run

Scope: `eval_runs.id = 114` (the 180-cell matrix), DEVIATIONS 8.0–8.94, the new
`--concurrency` path, `ANSWERABLE_SQL`, the `Pool` fixture rewrite, the moderation
field reordering, the `hurt` blocklist removal, `/api/themes` + `/api/stages`,
`make warm`'s guards, and the deploy artifacts.

**Method.** Every number below was recomputed from `eval_runs` / `eval_run_items`
with arithmetic that does not import `eval.py`, and every stored eval story was
re-scored by calling `storyforge.decoder.validate_text` on text rebuilt from
`story_segments`. The API used for serving claims was my own uvicorn on port 8060
with `DEMO_BYPASS_AUTH=false`; `/api/health` reported `demo_bypass_auth: false`
and a garbage bearer token returned **401** on `/api/kids`, `/api/stories/{id}`
and `/api/stories/request` **before** any conclusion was drawn from a response.
One Clerk session was minted and **revoked** (verified `revoked` via
`GET /v1/sessions`); two other sessions on that user were not mine and were left
alone. Sabotage tests ran in a `git archive HEAD` copy at `/tmp/sf-sabotage`
against a throwaway `sfscratch` database, never the real repo or database.

**Live spend: ~$0.80**, all Haiku (219 moderation calls: 3 full runs of the
40-case `test_grammar.py::FIXTURE`, 3 runs of a 25-case battery of new cases, and
3 runs of a controlled field-order experiment). No `make eval`, no `make warm`, no
generation-model call. Repo unchanged (`git status` is the two Phase 8 agents'
work); `HEAD` still `b10a86e`; `stories`/`story_segments`/`questions`/`readings`
counts and `eval_runs.id=114` status identical before and after.

---

## Verdict on §0's thesis

**Supported — and more strongly than `docs/EVAL.md` argues, though not for the
reasons EVAL.md gives.**

The evidence that carries it is not the test suite. It is this: I re-scored all
**119** servable, answerable stories in the live pool with the decoder, at their
own stage and at **every** reader stage `_SERVE_SQL` could hand them to. Result:
**0 below the 95% gate anywhere, 0 monotonicity breaks, and `stories.decodability`
agrees with the decoder on all 119 rows.** Pool minimum 95.12%, mean 99.11%. No
child is being served text they cannot decode. That is the thesis, and it holds.

The §14 headline reproduces exactly. Independently: 180 items, min 95.0000%, mean
98.9416%, median 100%, **0 below the gate**, bins 115 at 100% / 65 in 95–<100%,
depth histogram 108/56/15/1, p95 = 2, max = 3, total $5.412963, 0 OOV, 0 over the
story-word cap, 9,568 scored words, 71 unalignable. Re-validating the persisted
segments produced **0 decodability mismatches on 180/180 stories**. The harness
and the serving path compute decodability the same way: `_is_decodable_for`
(`services/api/storyforge/api/stories.py:571`) uses `mastered_ids(reader_stage)`,
cumulative sight words and the persisted `story_words` — the same denominator, and
since `_SERVE_SQL` only serves `stage_id <= reader_stage` the reader always holds
a superset of the GPCs. No hidden denominator.

**But p95 = 2 is not the number EVAL.md thinks it is, and the correction changes
the conclusion.** 13 of the 89 paid rewrites (14.6%) followed an attempt that had
**already met the 95% threshold**. Counting only rewrites the decodability
threshold actually asked for:

| | hist | mean | p50 | **p95** | max |
|---|---|---|---|---|---|
| as reported | 108/56/15/1 | 0.494 | 0 | **2** | 3 |
| decodability-driven only | 113/58/9 | 0.422 | 0 | **1** | 2 |

So §6's premise — allowlist first, retry second — is intact: the decodability
lever's p95 is **1**, with a full rewrite of headroom, not zero. EVAL.md's "a pass
with the margin spent" is pessimistic about the gate and, more importantly, it
misattributes the second rewrite. The other half of p95 = 2 is manufactured by
finding **F1** below: a rewrite prompt that is provably incapable of stating the
failure it was invoked for. Fix F1 and both the inflated p95 and a live
child-facing content defect go away together.

## The false claim you asked me to find

There are **four**. The load-bearing one is `docs/EVAL.md:315`:

> `tests/test_eval.py` plants an `eval` story alone at the top of the §7 ladder —
> the row `_SERVE_SQL` would pick first if the status were servable — and checks
> `POST /api/stories/request`, `GET /api/stories/{id}` and `POST /api/readings`.
> Adding `"eval"` to `SERVABLE_STATUSES` turns those tests red and the request
> endpoint hands back the fixture, **which is what makes them an experiment.**

Both halves are false, and this is the sentence EVAL.md offers as the evidence for
"Nothing this run wrote can be served to a child." See **F2**. The other three:
`EVAL.md:326` ("Sequential by design … Concurrency … untested here" — the run was
`--concurrency 6`, **F6**); `EVAL.md:302` (the length paragraph, **F10**); and
`STATE.md:16` (the warm-pool row, now wrong in the opposite direction, **F12**).

---

# CONFIRMED findings, ranked by real user harm

## F1 — HIGH — The rewrite prompt cannot say "too many story words", so it deletes the theme instead. A child who picks Dragons is served "Dan sat in a camp."

**Reproduced with pure functions, no API call.**

`passes_gate` (`services/api/storyforge/pipeline.py:198`) fails on three
conditions: decodability, OOV, and `len(distinct_story_words) > MAX_STORY_WORDS`.
`build_rewrite_block` (`services/api/storyforge/pipeline.py:218`) can only describe
the first two — it iterates `report.violations` and the OOV words. When the gate
fails on the **story-word cap alone**, `lines` is empty and the model receives:

```
Your story used words this child cannot read. Rewrite it.

THE STORY YOU WROTE:
The dragon sat in the cave. It had big wings. The dragon had fire. ...

PROBLEMS (0):


Keep the same story and the same length. Change only what is listed. Every
replacement must come from the permitted, sight, or story lists.
```

Three things are wrong in one prompt: the premise is false (the story is 100%
decodable), no problem is listed, and "change only what is listed" instructs the
model to change nothing.

Reproduce:
```bash
cd services/api && .venv/bin/python - <<'PY'
from storyforge.pipeline import Vocabulary, validate_draft, passes_gate, build_rewrite_block
v = Vocabulary(stage_id=4, stage_name="s4", target_min=30, target_max=60,
    allowed_words=["dog","cat","hill","ran","big"],
    sight_words=["the","a","to","and","it","is"],
    story_words={"dragon":"g","cave":"g","wings":"g","fire":"g"})
b = ("The dragon sat in the cave. It had big wings. The dragon had fire. "
     "The cat ran up the hill to the cave. The dragon and the cat had fun.")
r = validate_draft(v, b, ["dragon","cave","wings","fire"])
print(r.decodability, r.counts["oov"], r.distinct_story_words, passes_gate(r))
print(build_rewrite_block(r, v, b))
PY
# 1.0 0 ['cave', 'dragon', 'fire', 'wings'] False
# ... PROBLEMS (0):
```

**Observed in run 114:** 9 stories hit this. Each has a non-final attempt at
**exactly 1.0** decodability — with zero violations and zero OOV the story-word cap
is the only condition that can fail — and **every one of the 9 ended with zero
story words**:

| story | stage | theme | depth | `decodability_by_attempt` | final story words | final text (opening) |
|---|---|---|---|---|---|---|
| 9139 | 2 | dragons | 1 | `[1.0, 1.0]` | `[]` | "A cat sat on a hill. It had a rock." |
| 9153 | 2 | dragons | 2 | `[0.964, 1.0, 1.0]` | `[]` | "A cat sat in a spot. It had a big rock." |
| 9190 | 2 | dragons | 2 | `[0.95, 1.0, 1.0]` | `[]` | "A cat sat on a hill. It had a big rock." |
| 9243 | 3 | dragons | 1 | `[1.0, 1.0]` | `[]` | "A fox ran up a hill. It was big." |
| **9257** | **4** | **dragons** | **3** | `[0.926, 0.963, 1.0, 1.0]` | `[]` | "A dog lived on the hill. It had a big rock." |
| 9258 | 4 | dragons | 2 | `[0.846, 1.0, 1.0]` | `[]` | "A dog lived in a hill. It ran fast." |
| 9380 | 7 | animals | 2 | `[0.878, **1.0**, 0.980]` | `[]` | "A dog was in a home near the park." |
| 9397 | 8 | dragons | 2 | `[0.957, 1.0, 1.0]` | `[]` | "A dog was in the house." |
| 9407 | 9 | dragons | 2 | `[0.949, 1.0, 1.0]` | `[]` | "A man was in a house. He had a fire." |

**This is the depth-3 story you asked me to find.** Story 9257, stage 4, dragons.
Attempt 0 failed legitimately at 92.6%. Attempt 1 reached 96.3% and failed on
OOV-or-cap. Attempt 2 reached **100%** and was rejected on the story-word cap, so
attempt 3 ran against a "PROBLEMS (0)" prompt. Final text: *"A dog lived on the
hill. It had a big rock. It ran fast up the hill. The sun helped the dog."* — a
dragons story with no dragon, no cave, and no wings. It is recorded as `accepted`.

Story **9380** is the sharpest case: attempt 1 was at **100%**, the empty-problem
rewrite fired, and attempt 2 came back **worse** (98.04%). The loop actively
degraded a perfect story.

At stages 2–4, **6 of 15 dragons stories (40%)** shipped with zero dragon content.
§18 names exactly this: *"Themes at stages 1–2 lean on story words + pictures for
interest."* Story words are the only thing carrying the theme there, and this is
the code path that removes them.

**It reaches a child.** `run_pipeline` is shared by `make warm`. Read-only replay
of `_SERVE_SQL`'s inner SELECT for a stage-1 reader who picked Dragons:

```sql
SELECT id, stage_id, last_served_at FROM stories
WHERE status = ANY(ARRAY['warm','complete']) AND stage_id <= 1
  AND EXISTS (SELECT 1 FROM questions q WHERE q.story_id = stories.id)
ORDER BY stage_id DESC, (theme_id = 1) DESC, last_served_at ASC NULLS FIRST, id ASC;
-- 8897, 2, 10, 1661   <- story 1661 is 4th of 4
```

Story **1661** is `warm`, answerable, stage 1, theme dragons, and its `gen_meta`
carries the same signature (`decodability_by_attempt = [0.895, 1.0, 1.0]`). Live,
against my `demo_bypass_auth: false` server with a real Clerk token:

```
GET /api/stories/1661 -> 200
  stage 1 theme dragons status warm dec 1.0 story_words []
  TEXT: Dan sat in a camp. Dan can nap. A cat sat at Dan. Dan can pat the cat.
        The cat can nap. Dan did nap. Dan can nap.
```

That is 1 of only 4 stage-1 dragons stories — 25% of that child's dragons library.
It is the only themeless story in the servable pool today, because the pool is
thin at low stages; the eval matrix shows the rate is 40% at the stages where it
matters most.

**Expected:** a gate failure the rewrite node cannot describe should either not
consume a rewrite, or the block should name the cap (`"you used 4 story words;
keep at most 3 — drop one and keep the other three"`). **Observed:** a paid rewrite
against a self-contradictory prompt, whose measured outcome is the deletion of the
theme, and which pushes the reported p95 to §1's ceiling.

`file:line` — `services/api/storyforge/pipeline.py:198` (`passes_gate`),
`services/api/storyforge/pipeline.py:218-245` (`build_rewrite_block`),
`services/api/storyforge/pipeline.py:425` (the unconditional
`user_block = build_rewrite_block(...)`).

---

## F2 — HIGH — `ANSWERABLE_SQL` made three eval-isolation tests and one child-safety test pass for the wrong reason. `EVAL.md:315` names one of them as the proof.

The zero-question agent disclosed one instance of this class
(`test_story_stage_binding.py::test_an_undecodable_story_in_the_pool_is_never_served`,
DEVIATIONS 8.59). There are **five** more. All CONFIRMED against deliberately
broken code in `/tmp/sf-sabotage`, with each sabotage independently proved to have
teeth first.

The mechanism: `ANSWERABLE_SQL` (`services/api/storyforge/api/stories.py:143`) is
now an `AND` in `_SERVE_SQL` and an independent 404 in both `GET
/api/stories/{id}` (`stories.py:734`) and `POST /api/readings`
(`api/readings.py:606`). A fixture story with **no `questions` row** is therefore
refused whatever its `status` is — so an assertion about `status` is satisfied by
answerability and never tests `status` at all.

`test_eval.py`'s `eval_story` fixture (`services/api/tests/test_eval.py:1062-1108`)
inserts a story and its segments and **no questions row** — verified by reading it.

| test | asserts | now passes because |
|---|---|---|
| `test_eval.py:1114` `test_an_eval_story_is_never_served_by_the_request_endpoint` | request never returns the fixture | `_SERVE_SQL` excludes it on answerability |
| `test_eval.py:1137` `test_an_eval_story_is_not_fetchable_by_id` | 404 | `stories.py:734`, not `:730` |
| `test_eval.py:1142` `test_an_eval_story_cannot_be_read_by_a_kid` | 404 | `readings.py:606`, not `:604` |
| `test_stories_api.py:720` in `test_rejected_stories_are_never_served` | `GET /api/stories/{min(rejected)}` is 404 | `min(rejected)` is story **1**, which has 0 questions |

Sabotage 1 — make `eval` servable (`api/stories.py:344`):
`WHERE (status = ANY(:statuses) OR status = 'eval') AND stage_id <= :stage`.
Control proves the sabotage works: with a questions row added, `POST
/api/stories/request` **served the eval artifact** (`id == eval_story`). With the
fixture exactly as written: `pytest tests/test_eval.py::test_an_eval_story_is_never_served_by_the_request_endpoint -p no:randomly` → **PASSED**.

Sabotage 2 — delete the status refusal (`api/stories.py:730` → `if False:`).
Control: `GET /api/stories/8` (a `rejected` story that *does* have questions) → **200**.
`test_an_eval_story_is_not_fetchable_by_id` → **PASSED**.
`test_rejected_stories_are_never_served` → **PASSED**, while a rejected story
returns 200. Its own docstring calls that a "Child-safety invariant."

Sabotage 3 — delete `readings.py:604`'s status refusal. Control: `POST
/api/readings` for rejected story 8 → **201**. `test_an_eval_story_cannot_be_read_by_a_kid` → **PASSED**.

Verified independently: `SELECT min(id) FROM stories WHERE status='rejected'` → **1**;
story 1 has no questions row; **4** of the 21 rejected stories do have one, so a
test with teeth was available and not chosen.

Two secondary staleness bugs in the same fixture. `eval_story`'s docstring says
the row is *"placed ABOVE every servable one … so a story alone at the top of the
ladder wins every request"*, but line 1077 is `stage = min(12, int(top) + 1)` and
`top` is now 12 — the row sits **at** stage 12, tied with nine live stories. This
is DEVIATIONS 8.90's defect (a fixture betting the pool would never be full) in a
file 8.90 did not touch.

**One-line fixes, both cheap:** give `eval_story` a `questions` row (note
`test_eval.py:1525` currently forbids that against the live DB, so it needs the
sweep scoped to non-fixture themes), and select the rejected id with
`AND EXISTS (SELECT 1 FROM questions …)`.

**Fixtures that go quiet are worse than ones that go red** — and the whole of
`EVAL.md` §13 rests on these four.

---

## F3 — HIGH — the serve-time decodability gate's test is green against a build with the gate deleted, in exactly the state `make warm` leaves behind

`services/api/tests/test_story_stage_binding.py:42`:

```python
#: Nothing in the seeded pool lives above stage 5, so a story placed here is the
#: unique winner of `_SERVE_SQL`'s `ORDER BY stage_id DESC` for a stage-12
#: reader. That is what makes the serve-gate test deterministic rather than
#: dependent on whatever `make warm` last produced.
EMPTY_STAGE_BAND = 12
```

Verified false: stage 12 holds **9** servable, answerable stories. The comment's
own stated goal is inverted — the test is now *entirely* dependent on what
`make warm` last produced.

It survives today only by luck of `last_served_at`:
`SELECT count(*), count(last_served_at) FROM stories WHERE status IN
('warm','complete') AND stage_id=12;` → **9, 9**. All nine are stamped, so the
fixture (NULL) wins `last_served_at ASC NULLS FIRST`. **Immediately after a
`make warm` those rows are NULL**, the fixture ties on `NULLS FIRST` and loses the
`id ASC` tiebreak (it holds the highest id), and the gate is never reached.

Sabotage 4 — delete the serve-time gate (`api/stories.py:676` → `if False:`), both
directions:

```bash
# current pool state — the sabotage has teeth
pytest tests/test_story_stage_binding.py -p no:randomly -k undecodable
#  AssertionError: the serve-time gate let through a story measuring 36.4% decodable  -> 1 failed

# the state right after `make warm`
psql -d sfscratch -c "UPDATE stories SET last_served_at = NULL
                      WHERE status IN ('warm','complete');"   -- UPDATE 119
pytest tests/test_story_stage_binding.py -p no:randomly -k undecodable
#  1 passed        <-- GREEN with _is_decodable_for removed
```

The test still carries its "Sabotage-checked" note at `:314-315`. DEVIATIONS 8.59
says this was "PROVED both ways" — the proof was state-dependent, and the state it
depends on is the one the product is supposed to be in.

---

## F4 — HIGH — `make warm-check` exits 0 on a pool that answers a child with HTTP 503

`warm.py`'s coverage predicate is a strict subset of the serving path's. Status and
answerability match exactly and are imported, as claimed. What coverage does **not**
model is `_is_decodable_for` against `DECODABILITY_GATE`
(`services/api/storyforge/api/stories.py:675-690`) inside a loop bounded by
`SERVE_ATTEMPTS = 3` (`stories.py:152`, `:635`).

With 4 undecodable-but-answerable `warm` stories planted at stage 12 / dragons
(`last_served_at IS NULL`), stages 1–11 full of good stories:

```
POST /api/stories/request (stage 12, dragons) -> HTTP 503
  {"detail":"no story at or below stage 12 passes §1's 95% gate for this reader;
             rejected 9470 (36.4%), 9471 (36.4%), 9472 (36.4%)"}
python -m storyforge.warm --check-coverage -> exit 0
  OK — all 36 (stage, theme) pair(s) hold >= 3 servable story/stories.
```

Two defects in one: the gate cannot see the condition, and `SERVE_ATTEMPTS = 3`
spends the whole retry budget on the top rung so §7's ladder **never descends** to
the good stage-11 stories. §7 and `warm.py:23-25` both promise the kid never sees
an error; here the kid sees an error while a correct fallback existed.

The live pool is clean on this (I swept all 119 through `_load_story` +
`_is_decodable_for`: 0 failures), so this is a latent hole in the gate rather than
a live defect. `stories.py:678` calls the branch "Should be unreachable"; the
finding is that if it is reached, the gate reports green and the child gets a 503.

## F5 — CRITICAL for a first deploy — `.do/app.yaml` points every component at a branch that does not exist

`.do/app.yaml:82`, `:183`, `:223` — `branch: main`.

```
git ls-remote --heads https://github.com/HankH18/story-forge.git
  b10a86e  refs/heads/k5-learning-app-gstack-setup
  b10a86e  refs/heads/master
git ls-remote --symref … HEAD  ->  ref: refs/heads/master
```

No `main` exists on the GitHub mirror, on the GitLab canonical, or locally. The
mirror's default is `master`. `doctl apps create --spec .do/app.yaml` fails before
the first build. `docs/DEPLOY.md` §0 discusses GitLab-vs-GitHub drift at length and
never notices the branch name is wrong on both.

Same root cause, second order: the **local** `master` is stale at `383c6d2` and
does not contain `.do/app.yaml`, `services/api/Dockerfile`, `.dockerignore` or
`docs/DEPLOY.md` (`git cat-file -e master:<path>` fails for all four). Remote
`master` is at `b10a86e`, so the remotes are fine today — but a push from the
canonical checkout would regress the deploy artifacts off the branch DO builds.

## F6 — MEDIUM-HIGH — `EVAL.md` says the run was sequential; the same document says `--concurrency 6`; the generated report says both in one sentence

`docs/EVAL.md:326`:
> **Latency under load.** Sequential by design (DEVIATIONS 7.48) so the p95
> answers §1 item 6. Concurrency, rate limits and the in-process limiter
> (DEVIATIONS 4.10) are **untested here**.

`docs/EVAL.md:18` and `:47` record `--concurrency 6` and "6 concurrent cells".
`reports/eval-20260730T041835Z.md:165` is self-refuting in a single sentence:

> §1 item 6 asks for cold generation p95 < 12s: measured p95 64.1s — **does not
> meet it**. **Run sequentially (`--concurrency 6`)** so this number is one
> request's wall time.

Root cause: `scripts/eval_doc.py:433` made the *concurrency row* data-driven (with
a `(sequential)` branch for `concurrency <= 1`) and left the §14 item-6 paragraph
at `scripts/eval_doc.py:1194` and the §1.6 sentence at
`services/api/storyforge/eval.py:1626` as static strings.

This matters because the code states the precondition itself.
`services/api/storyforge/eval.py:106`:

```python
#: §1 item 6. Compared against per-story wall time, which is only meaningful
#: because the reported run is sequential (DEVIATIONS 7.48).
COLD_LATENCY_P95_TARGET_MS = 12_000
```

The run was not sequential, nothing asserted it, and the §1.6 verdict was printed
anyway. **In fairness the direction is benign** — I measured it and concurrency did
not inflate latency. Normalising by output tokens (DEVIATIONS 8.6 found
r = +0.997 there), serial run 2 vs concurrent run 114: ms per 1,000 output tokens
p50 11,031 → **9,175**, p95 40,575 → **18,436**. §1.6 is still legitimately missed
at 64.1s. But the guard is absent, so the next person to change `--concurrency`
gets no warning, and `EVAL.md`'s own list of "what this eval does NOT measure"
disclaims the one variable the run actually changed.

## F7 — MEDIUM-HIGH — `DEVIATIONS 8.100` is cited three times and does not exist

```
docs/EVAL.md:47                        … still pays — DEVIATIONS 8.100
scripts/eval_doc.py:434                … still pays — DEVIATIONS 8.100
services/api/storyforge/eval.py:2361   … an advertised flag (DEVIATIONS 8.100).
```

`docs/DEVIATIONS.md` has no `8.100`, no `8.95`–`8.99`; the highest 8.x entry is
**8.94**. I resolved every `DEVIATIONS N.N` citation in the repo against the file:
**159 distinct ids cited, 158 resolve, 1 does not — and it is the concurrency
change.**

§0 of the build doc: *"Every architectural deviation from this doc gets logged in
`docs/DEVIATIONS.md` with a one-line rationale **before it is made**."* The one
change in this batch with no deviation entry is the one that broke STATE invariant
10 (F8) and produced the "Sequential by design" contradiction (F6). Those share a
root cause: the entry that would have recorded the reasoning was never written.

## F8 — MEDIUM-HIGH — `--concurrency 6` measurably degraded the per-stage prompt cache; "the cache prefix still pays" overstates it

`EVAL.md:47` claims the cells are "batched by stage so the per-stage cache prefix
still pays". Batching is real (`services/api/storyforge/eval.py:2408-2413`), but
six cells of a stage fire simultaneously, so all six miss the prefix and all six
**write** it. Measured, same 180-cell matrix, from the runs' own recorded tokens:

| | prompt tokens | uncached | cache **READ** | cache **WRITE** | input-side $ per 1k prompt tok |
|---|---|---|---|---|---|
| run 2 (`--concurrency 1`) | 510,099 | 7.6% | **88.3%** | **4.1%** | $0.00043 |
| run 114 (`--concurrency 6`) | 836,739 | 13.7% | **65.7%** | **20.5%** | **$0.00092** |

Cache-write share up **5.0x** (≈ the concurrency factor); hit rate down a quarter;
input-side unit cost **2.1x**. Re-pricing run 114's prompt volume at run 2's cache
mix: **$0.3612 vs $0.7695 actual — a $0.41 concurrency premium, 7.5% of the
$5.41 total.** Not ruinous, but STATE invariant 10 exists to make the prompt split
pay, and the report presents it as preserved while never comparing the two runs'
token profiles.

Wall clock: 17m48s for 76.5 min of summed pipeline time — a genuine 4.3x speedup,
and `EVAL.md:28` labels the 76 min "summed per story" honestly.

Secondary, same code: under concurrency the spend cap is checked only **between
stage batches** (`eval.py:2414`), so up to 15 concurrent cells can overshoot.
`EVAL.md:51` says "enforced on the running actual"; the real granularity is one
stage. Harmless at $5.41 against $15, misleading near the cap.

## F9 — MEDIUM — the *content* half of the verdict still commits before it reasons. Two of the run's eight rejections are reproducible false positives, and the fixture cannot see the class.

DEVIATIONS 8.77 moved `grammar_note` before `well_formed` because *"structured
output is emitted in schema order, so with the boolean first the judge committed to
it and then reasoned in the note."* The same file leaves
`services/api/storyforge/llm/moderate.py:231-232` as:

```python
approved: bool = Field(description="True only if the story is safe for ages 5-10.")
reason: str   = Field(description="One sentence. If rejecting, name the specific problem.")
```

The safety boolean is still declared **before** its reason. Controlled experiment:
same prompt, same model, same texts, only the field order changed (local schema
copy; the repo was not modified), 3 runs:

| text | as shipped (`approved` first) | `reason` first |
|---|---|---|
| story **9136** — "hit a rock and fell … got up … felt glad" | **REJECTED 3/3** | **approved 3/3** |
| story **9190** — "A dog hit a rock next to it" | **REJECTED 3/3** | **approved 2/3** |
| story 9305 — "went off to get fire" (defensible reject) | REJECTED 3/3 | REJECTED 3/3 |
| control — "scared in the dark cave … Mom came with a lamp" | approved 3/3 | approved 3/3 |

Reproduce: `services/api/.venv/bin/python /tmp/audit7/fp_probe.py 3`.

The true positive and the control are unmoved, so this is not "reason-first
approves everything" — it is the boolean being committed before the reasoning
exists, exactly as 8.77 diagnosed for grammar. The judge's own words give it away:
*"A dog hit a rock next to the cat, **which could be interpreted as** the dog
hitting the cat"* — a rationalisation, not a finding.

Story 9136 is a trip-and-recover with an explicit happy resolution. STATE invariant
5 requires moderation to separate a storybook hazard from actual harm, and the
prompt's own APPROVE list includes "A problem that gets resolved."

Consequences: (a) `EVAL.md`'s "gate + moderation pass rate 95.6%" and "fallback
rate 4.4%" are **pessimistic by ~1.1pp** — 2 of the 8 rejections are false; (b) the
40-case fixture's `assert fp == 0` passes only because **the fixture contains no
trip-and-recover case**. Its must-approve set was built from real eval stories the
judge had *already approved* (`# eval story 3243`, `# story 7292`, …), which is
survivorship selection: story 9136 is a real eval story the judge **rejected** and
it was never added. The fixture is structurally unable to detect this class.

## F10 — MEDIUM — `EVAL.md` §12's length paragraph contradicts `EVAL.md` §4's own table

`docs/EVAL.md:302`, hardcoded at `scripts/eval_doc.py:1113-1124`:

> **Length compliance is the weakest number in this report** … The per-stage table
> in section 4 shows it **collapsing as the target grows**: the stages asking for
> 120-200 words came back with a **mean in the 89-102 range** … a stage-12 reader
> is being handed **roughly half the text** §6 specifies.

Recomputed from the same items, stages 9–12 (target 120–200):

| stage | mean words | min | max | in range |
|---|---|---|---|---|
| 9 | **167.1** | 124 | 204 | 14/15 |
| 10 | **151.3** | 124 | 187 | 15/15 |
| 11 | **152.3** | 129 | 191 | 15/15 |
| 12 | **153.5** | 127 | 207 | 13/15 |

Every mean is comfortably **inside** the range, and `EVAL.md:123-126` prints those
same four numbers eight rows above the paragraph that denies them. Nor does
compliance "collapse as the target grows": the joint worst stages are **2** (13/15,
target 30–60) and **12** (13/15). The "89-102 range" describes the *previous*
matrix — run 2's stages 9–12 means were 74.9 / 77.2 / 88.3 / 75.4 — so this is
stale hand-written prose surviving a data regeneration, in a document whose own
preamble (`EVAL.md:9`) says *"The prose is a reading of them; the figures are not
editable prose."*

Costs: it sends a reader chasing a fixed bug, and it mislabels the weakest number.
The weakest number is §1 item 6 — 64.1s against a 12s target, a **5.3x** miss.

## F11 — MEDIUM — `--resume` verifies ten config keys and not `cold`, `effort` or `pricing_date`

The previous auditor's finding #5 is **genuinely fixed** — I confirmed all ten
declared keys trip a refusal, and that the refusal (`eval.py:2218-2247`, `return 3`)
happens before any row mutation or API call:

```
models · prices · gate · max_rewrites · max_story_words · prompt_word_limit
generation_timeout_s · sdk_max_retries · vocabulary · themes   -> all 10 detected
unmutated config -> drift = []
```

But enumerating `_VERIFIED_CONFIG_KEYS` against the stored config shows what is
**not** checked:

```
budget_usd cells_planned cold concurrency effort git_commit max_output_tokens
n pricing_date projection_usd provenance python stages stages_spec themes_spec
```

Three matter, and `EVAL.md` makes claims about all three:
- **`cold`** — resuming without `--cold` makes the second half a cache-served run. `EVAL.md:48` reports "cache bypassed: True" for the whole matrix.
- **`effort`** — `--effort high` on the second half changes tokens, cost and latency.
- **`pricing_date`** — `EVAL.md:335` says it is explicit *"precisely so a cost figure regenerated later cannot silently disagree"*; on a resume it can.

Also asymmetric: `themes` is verified and **`stages` is not**, which reads as an
oversight rather than a decision. CONFIRMED as a key-list omission (enumerated
programmatically); the end-to-end consequence is reasoned, since triggering it
requires interrupting a paid run.

## F12 — MEDIUM — `STATE.md`'s warm-pool row is now wrong in the other direction

`docs/STATE.md:16` still reads:

> **`make warm-check` is RED and that is the honest state: 13 of 36 (stage,theme)
> pairs hold ≥3 servable stories (53 servable in all); stages 7–12 hold none.**

Live: `make warm-check` → **exit 0**, `36 of 36 pair(s) at target`, **119**
servable. Verified independently — all 36 pairs hold ≥3 (min exactly 3, max 6):

```sql
SELECT count(*) FROM (SELECT st.id, t.slug FROM stages st CROSS JOIN themes t
  LEFT JOIN stories s ON s.stage_id=st.id AND s.theme_id=t.id
    AND s.status IN ('warm','complete')
    AND EXISTS(SELECT 1 FROM questions q WHERE q.story_id=s.id)
  GROUP BY 1,2 HAVING count(s.id) < 3) x;   -- 0
```

The row written specifically to stop `STATE.md` lying about the pool is now lying
about it. `docs/STATE.md:1` also still says "build state (end of Phase 4)" at the
end of Phase 7.

## F13 — MEDIUM-HIGH — a deployment with unreplaced placeholders reports `"status":"ok"`

`.do/app.yaml:161-163` ships `CLERK_JWKS_URL: https://REPLACE_WITH_YOUR_CLERK_SUBDOMAIN…`
and `:154-157` `ANTHROPIC_API_KEY: REPLACE_IN_DO_DASHBOARD`. Booted with those
exact values in the built image:

```
GET /api/health -> 200 {"status":"ok", "database":"ok","decoder":"ok","anthropic":"pending"}
GET /api/kids   -> 401
```

`services/api/storyforge/api/health.py:75-79` only tests `anthropic_api_key is
None`, so the literal string `REPLACE_IN_DO_DASHBOARD` reads as *pending*, never
*error*, and nothing at boot rejects a `REPLACE_*` value. An operator who skips
DEPLOY.md §2 gets a green dot, a green health check, a product nobody can sign in
to, and every generation 401ing at Anthropic.

Companion: `services/api/storyforge/obs.py:59` gates Langfuse on the keys being
*truthy*, and the spec's placeholders are non-blank, so `obs.enabled()` returns
**True** and a background exporter retries against cloud.langfuse.com for every
generation — skipping the "absent/blank keys are a supported no-op" path DEVIATIONS
4.17 documents. Fix: `""` in the spec, or reject `REPLACE_*`.

## F14 — MEDIUM — §18's guard is defeated on any cgroup-v2 or non-Docker runtime, which is DO's own shape

`docs/DEPLOY.md` §7 claims the bypass "cannot be turned on in ANY container".
Inside the built image `/proc/1/cgroup` is `0::/` — cgroup v2 gives a container no
`docker`/`containerd`/`kubepods` string, so `CONTAINER_CGROUP_MARKERS`
(`services/api/storyforge/auth.py:628-636`) never matches. The whole filesystem
signal then rests on `/.dockerenv`, which only the Docker daemon writes —
containerd, CRI-O, Kubernetes and gVisor do not.

```bash
docker run --rm --user root -e DEMO_BYPASS_AUTH=true -e PORT \
  -e DATABASE_URL=postgresql+psycopg://storyforge:storyforge@localhost:5433/storyforge \
  --entrypoint sh <img> -c 'rm -f /.dockerenv; python -c "
from storyforge.auth import deployment_signals, container_signals
import storyforge.main; print(\"BOOTED WITH AUTH BYPASSED\")"'
# container_signals: []   deployment_signals: []   BOOTED WITH AUTH BYPASSED
```

No `STORYFORGE_BYPASS_IN_CONTAINER` needed. Two sub-defeats: bare `-e PORT` with
the var unset on the host **deletes** the image's own `ENV PORT=8080` (the
AUDIT-PHASE7 F11 hardening covers *blanking*, not removal, `auth.py:742-747`), and
`host.docker.internal` sits on `LOCAL_DB_HOSTS` (`auth.py:601-602`) while being by
definition a container reaching out of itself. End to end on a published port:
`GET /api/kids` with **no token → 200 `[]`**, `x-storyforge-auth: demo-bypass`.

Not reachable through `.do/app.yaml` as committed (DO injects PORT, the DB host is
remote), so not a live production hole — but the documented **absolute** invariant
is false, and the residual protection is the env-var list `auth.py:726-733` itself
admits "can be removed by whoever starts the process." Fix direction: a *positive*
signal (refuse unless bound to `127.0.0.1`), not a longer negative list.

## F15 — MEDIUM-HIGH — the `Pool` fixture row-locks the entire servable pool; a concurrent reader gets a silent 503

`Pool.start()` (`services/api/tests/test_stories_api.py:207-212`) runs
`UPDATE stories SET status='rejected' WHERE status = ANY(:s)` and holds the
transaction open for the whole test. `_SERVE_SQL` uses `FOR UPDATE SKIP LOCKED`
(`stories.py:352`), so a request on any **other** connection skips every locked
row:

```
A (the `pool` fixture): hid 119 servable stories, transaction STILL OPEN
B (concurrent request, stage-12 reader): _SERVE_SQL returned None
   -> 503 "the warm pool is empty" for a real child
B sees all 119 rows (A's UPDATE is uncommitted) — every one ROW-LOCKED, SKIP LOCKED skips them
```

`test_stories_api.py:353-355` / DEVIATIONS 8.91 claim a race surfaces "as a loud
serialization failure rather than as a silent flake." For a concurrent *reader* it
is neither: a silent 503 indistinguishable from a genuinely empty pool. The stated
goal does hold (REPEATABLE READ stops a concurrent writer polluting the fixture's
snapshot); the cost is inverted from what is documented — these tests no longer lie
about the pool, but for their duration they make every other process lie. Given
`STATE.md:20-23`'s note about two agents producing moving failures, and two agents
live in this tree now, this is worth naming.

## F16 — MEDIUM — a sliced `make warm` leaves the cell it was told to fill empty and exits 0

`full_matrix` (`warm.py:1302`) requires **all** 12 stages *and* **all** themes, and
`warm.py:1341` suppresses the gap report with `gaps if full_matrix else ()`. A run
told `--stages 1..12 --themes animals` where (12, animals) is empty and all three
generations **succeed and are then rejected**:

```
  stage 12 / animals: have 0, generating 3
    REJECT #9470 — unresolved peril   (x3)
  This run covered 12 of 12 stage(s) and 1 of 3 theme(s) by request,
  so the gaps above are expected.
0 warm, 3 rejected, 0 api_fail, 11 pair(s) already full.   -> exit 0
```

The gap is exactly the cell the invocation was asked to fill, and the run calls it
"expected". `made == 0 and skipped == 0` does not fire because 11 other pairs
skipped. Contained by `--check-coverage`, which then exits 1 — so this only bites
an operator who trusts the run's own exit code, which `docs/DEPLOY.md:145-147`
explicitly invites. Correct guard: `gaps ∩ requested pairs`, not `gaps if full_matrix`.

## F17 — MEDIUM — Alembic dies on a `%` anywhere in the DB password

`services/api/storyforge/migrations/env.py:23` passes `DATABASE_URL` through
ConfigParser interpolation:

```
DATABASE_URL='postgresql+psycopg://storyforge:pa%25ss@…' alembic current
-> ValueError: invalid interpolation syntax in '…' at position 34
```

Even a correctly percent-encoded password crashes, so "URL-encode it" is not a
workaround. `DATABASE_URL` is composed from `${db.PASSWORD}` at deploy time, so a
rotated password containing `%` kills the PRE_DEPLOY job and aborts the deploy.
`DEPLOY.md` §2 calls it out and leaves the one-line fix (`.replace("%","%%")`)
undone.

## LOW

- **The per-stage `rw p95` column hides the depth-3 story.** `eval.py:179`'s
  `quantile` docstring says "Nearest-rank" but computes `round(q*(n-1))`. At n=15,
  q=0.95 that is index 13, not the nearest rank's index 14 — so `EVAL.md:118`
  reports stage 4's rewrite p95 as **2** when the stage's max is **3**. The overall
  p95 (the number §1 gates on) is index 170 either way and is unaffected. A reader
  cannot locate the depth-3 story from the table that is supposed to show it.
- **`GET /api/themes` reports `servable_stories` without `ANSWERABLE_SQL`**
  (`api/catalog.py:129`). Documented as a deliberate upper bound, and the counts
  coincide today (41/42/36 with and without). But the docstring's argument —
  "high by a row or two is the right failure direction for a field whose only job
  is to make a drift visible" — is backwards: an inflated count *hides* the
  shortfall STATE invariant 14 exists to surface. Nothing reads it, so: low.
- **`eval_runs.id = 112`** has sat at `status='running'` with **0 items** for 15
  hours. Nothing reaps it; `--resume 112` would run a full 180-cell matrix
  (~$5.41); §14's admin portal "reads the same tables" and will render a
  permanently-running run.
- **The rolled-back `Pool` fixture leaves no row trace but burns ~37 sequence
  values per run** (`stories_id_seq` +6, `story_segments_id_seq` +18,
  `questions_id_seq` +7, `themes_id_seq` +6). No row, no `last_served_at` stamp, no
  leaked `zz-fixture-*` theme. `test_stories_api.py:357-359`'s "leaves the database
  exactly as they found it" is true of rows and false of sequences. Harmless
  (`int` surrogate keys, no test asserts on an id value).
- **`--forwarded-allow-ips='*'`** in the Dockerfile `CMD` makes every logged client
  IP caller-controlled (`X-Forwarded-For: 1.2.3.4` → logged verbatim). Bounded on
  DO, broader than needed.
- **Unauthenticated `/api/health` discloses the exact Postgres version**
  (`api/health.py:63` → `"postgres 16.14"`).
- **`GET /api/stories/{id}` is not owner-scoped** despite §9 listing it as such
  (`api/stories.py:720-741` checks status and answerability only). No PII in a
  story and the pool is shared content, so informational.
- **`.do/app.yaml:39-42`**: the comment argues `production: false` is "cheaper and
  adequate for the §17 demo"; the committed value is `production: true` (see S1).
- **The API is handed a live Clerk *secret* key it never uses** with
  `DEMO_BYPASS_AUTH` absent (`config.py:52`; sole consumer `auth.py:787`, inside
  the bypass guard). Blast radius for zero benefit.
- **`_DEPTH_SQL` re-spells `'warm'` as a literal** (`warm.py:206-209`) despite
  `warm.py:279-282`'s "never re-spelled" claim. Deliberate per 8.42 (two
  denominators), but the claim is absolute.
- **`--check-coverage -n 1`** weakens the mandatory gate; `docs/DEPLOY.md:130`
  prints the step without pinning `-n 3`.
- **`docs/DEPLOY.md`'s local-rehearsal block hardcodes `--network delhi_default`**
  — this Conductor workspace's directory name. In the canonical checkout it is
  `story-forge_default` and every command in that section fails.
- **`test_grammar.py:807-811`** ("a reading of it cannot be started either")
  passes because `pool_story` creates its kid with a raw `INSERT INTO kids
  (display_name, stage_id)` (`:692-696`), leaving `parent_id` NULL, so `owned_kid`
  404s any story. Proven on pristine code: that kid + a perfectly good `warm` story
  → `404 {"detail":"kid … not found"}`. The GET half of the same test is genuine.

---

# SUSPECTED (reasoned, not triggered)

- **S1 — HIGH.** `.do/app.yaml`'s `production: true` + `cluster_name:
  storyforge-pg` most likely references an **existing** managed cluster rather than
  provisioning one inline. If so, `doctl apps create --spec` fails
  cluster-not-found and `DEPLOY.md` §1's "The first deploy provisions the Postgres
  cluster" is wrong for the committed value. Also needs the app in the cluster's
  trusted sources. No DO account to test.
- **S2 — MEDIUM.** `ANSWERABLE_SQL` means "any question row exists", not "a
  **mastery-bearing** question exists". Only `tap_word` moves `gpc_stats`
  (`warm.py:108-111`), so a story carrying only an `mcq2` satisfies coverage *and*
  the serve gate while producing precisely the outcome 8.50 exists to prevent. Live
  pool: 119 servable, **119 with a `tap_word`**, 69 with an `mcq2`, **0** without a
  `tap_word` — latent only. `--backfill`'s third `_UNENRICHED_SQL` clause catches
  it; neither the coverage gate nor the serving boundary does.
- **S3 — MEDIUM.** `npm install --global bun@1.3.13` inside DO's node-js
  buildpack needs a writable npm prefix; a CNB/paketo builder running non-root may
  EACCES, and `build_command` has no fallback.
- **S4 — MEDIUM.** Node is the one unpinned link in an otherwise fully pinned
  chain: no `engines`, no `.nvmrc`, no `.node-version`, while bun is pinned to the
  patch. `react-router-dom@7` declares `engines.node >= 20`, so an 18.x default
  builder gives EBADENGINE.
- **S5 — MEDIUM.** The API image is built **twice** per deploy —
  `.do/app.yaml:74-76` (service) and `:220-221` (job) each declare
  `dockerfile_path: services/api/Dockerfile` from a `github:` source. The
  "same image by construction" comment at `:216-218` is a convention, not a
  guarantee.
- **S6 — LOW.** Nothing automated notices an unhealthy deployment: `/api/health`
  returns 200 with `"status":"error"` when the DB is down (honestly documented), DO
  reads only the status code, `alerts:` covers only `DEPLOYMENT_FAILED` /
  `DOMAIN_FAILED`, and seed/precompute/warm are manual console commands. A first
  deploy that skips them serves 503 on every story request.
- **S7 — LOW.** `doctl apps console <app-id> --component api` (`DEPLOY.md` §4):
  doctl's console signature is positional, so the flag likely does not exist. The
  first-deploy operator meets this at the seed step.

---

# Genuinely clean — stated plainly, not padded

1. **The live servable pool.** 119/119 above the 95% gate at their own stage **and
   at every higher reader stage**; `stories.decodability` agrees with the decoder on
   every row; min 95.12%, mean 99.11%. No monotonicity break. This is the strongest
   result in the audit.
2. **The §14 matrix's arithmetic.** Every headline number reproduced independently,
   including the two that could most easily have been flattered: min = **95.0000%**
   (story 9195, stage 3 animals, one violation — `out`, missing `ou|AW`, 19/20
   scored) and total cost $5.412963. `attempts per accepted story = 1.471` is
   `sum(attempts)/len(accepted)` over accepted items only — correct as labelled.
3. **The decoder agrees with the record.** 180/180 stories re-scored from
   `story_segments` with 0 mismatches against the item metrics *and* against the
   `stories.decodability` column; scored-word total, schwa (885), ambiguity (2384),
   heteronym (683) and unalignable (71) counts all reproduce exactly. `EVAL.md` §9's
   own disclosed-limitation table is honest, including its recomputed denominator.
4. **The `hurt` removal is sound — the reasoning holds and layer 3 covers it.**
   `"The man had a gun. He shot it and the dragon was dead."` still dies at layer 1,
   free, on three hits (`dead`, `gun`, `shot`). Three new `hurt`-only genuine-harm
   cases (deliberate cruelty; the inflected `hurts`, which was never on the list;
   unresolved peril) were **correctly rejected 3/3 runs each, all at `layer=model`**.
   No hazard got through. I could not construct a `hurt` sentence the judge misses.
5. **The grammar false-positive fix is real.** Three full live runs of the 40-case
   fixture: **FP=0, FN=0 every time** (TP=16, TN=23). `"He did not came back."`
   fails 3/3 with `well_formed=False` at `layer=model`. My own 12 new
   must-approve constructions (passive, cleft, `Had X known…` inversion,
   neither/nor + singular, `None of the cats was`, appositive, trip-and-recover,
   emphatic `did` + plain form, modal among past sentences) were approved in
   **36/36 trials**. The only crack I found in 75 new-case judgements is one
   *false negative*, stochastic: `"Sam listened the bird."` (missing preposition)
   was approved with `well_formed=True` in **1 of 3** runs. Marginal, but the
   substitution test should catch it — `"Sam listened the dog."` is still broken.
6. **`--resume` config enforcement.** The previous auditor's finding #5 is fixed
   for real, for the ten declared keys, with the refusal ahead of any mutation or
   API call. See F11 for what is outside the list.
7. **The `Pool` fixture still tests §7's ladder, including invariant 6.** Sabotaging
   `_SERVE_SQL` to `stage_id <= :stage + 1` turns `test_stories_api.py:794` **red**
   against a full 36/36 pool: `AssertionError: the ladder climbed a stage (STATE
   invariant 6)`. The rewrite preserved the teeth. Rollback leaves no row trace, no
   `last_served_at` stamp and no leaked fixture theme (see LOW for sequences).
8. **`make warm`'s guards G3 and G4 hold, driven through the real SQL rather than
   the monkeypatched `pool_coverage` `test_warm_coverage.py:250-296` uses.** An
   all-rejected cell on a full matrix exits non-zero naming the cell ("its stories
   are being REJECTED, not lost"). Hollow (`questions`-less) rows are caught twice:
   `_report_enrichment` fires and real `pool_coverage` reports the cell as 0 with
   `pool_depth` still 0, so it stays refillable. A `KeyboardInterrupt` mid-cell
   leaves **no** partial state — `persist` only `flush()`es and the loop's single
   `commit()` is after `enrich` — so it cannot make a later `warm-check` pass.
   Defaults confirmed `1..12` / `all`: `make warm-plan` reports all 36 pairs.
9. **`ANSWERABLE_SQL` at the serving boundary is not bypassable.** Live, with a real
   Clerk token and `demo_bypass_auth: false`: a `rejected` story → **404**, an
   `eval` story → **404**. Zero servable stories have zero questions; zero questions
   in the pool are degenerate (no missing options array, none with <2 options, none
   with a null answer, no empty prompt); all 119 carry a `tap_word`, so the §8
   mastery-evidence path exists for every servable story. The failure is in the
   *tests* (F2), not in the code.
10. **A theme cannot strand a child.** All 36 (stage, theme) pairs hold ≥3, and
    `_SERVE_SQL`'s `ORDER BY stage_id DESC, (theme_id = :theme) DESC` degrades to a
    lower stage or another theme rather than returning empty. `/api/themes` and
    `/api/stages` leak no PII — twelve stage names, three theme names, all committed
    to the repo — and correctly send `Cache-Control: public, max-age=300`.
11. **No secret in the image or in git history, across all refs.** 105 commits, 407
    blobs, grepped for `sk-ant-*`, `sk_live`/`sk_test`, `pk_live`/`pk_test`,
    `pk-lf-`/`sk-lf-`, `AVNS_*`, JWTs, private keys, `KEY=<value>` and
    `postgres://user:pw@`: every hit is the local dev credential, a `${db.PASSWORD}`
    template, or a `REPLACE_*` placeholder. `.env` was never committed. `docker
    inspect` env and full `docker history` carry nothing; `find / -name .env` in the
    image finds nothing. The exported build context is 5.5 MB and contains no
    `.git`, `node_modules`, `.venv` or `.env`.
12. **STATE invariant 8 holds in a real image.** `data/seed/gpcs.csv` is a
    *relative* symlink inside the build context; in the built image `readlink -f`
    resolves to `/app/services/api/storyforge/decoder/data/gpcs.csv` and reads 118
    lines through the link. `python -m storyforge.seed` inside the image seeded
    12 stages / 93 gpcs / 38 sight words; `precompute` built 64,296 rows in 1.78s.
    Not a dangling link.
13. **Migrations and cold start.** `alembic upgrade head` ran all 8 revisions clean
    on an empty DB via the spec's exact `run_command`; no scale race (PRE_DEPLOY
    job, `instance_count: 1`). First `GET /api/health` 200 at t+1.41s; 147 MiB peak
    on `--memory 1g`. `CMD` honours `$PORT` and matches `http_port: 8080`.
14. **SPA routing, API base and CORS are correct.** `catchall_document: index.html`
    is present so `/read/:kidId` survives a refresh; `apps/web/src/lib/api.ts:266`
    fetches relative `/api/...` with no baked origin, so `main.py:47`'s CORS
    allowlist is genuinely dev-only. `tsc --noEmit` exits 0 and
    `bun install --frozen-lockfile --dry-run` reconciles clean.

---

# What to fix first

1. **F1** — one prompt change closes a live child-facing content defect *and*
   removes the manufactured half of the p95 = 2 headline. Highest value per line in
   this batch.
2. **F2 / F3** — five guards are green against broken code. Two one-line fixture
   fixes (`eval_story` gets a `questions` row; the rejected id is chosen with
   `AND EXISTS (questions)`), and `EMPTY_STAGE_BAND` needs a fixture that does not
   bet on pool shape. Then re-run the sabotages.
3. **F5**, then **S1** — the deploy cannot start otherwise.
4. **F13** — a deploy that looks healthy and is not.
5. **F6 / F7 / F10 / F12** — four false documentation claims, one missing deviation
   entry. Cheap, and they are the reason this batch's changes went unreviewed.
6. **F9** — swap `approved` and `reason`, then re-run the whole fixture more than
   once (STATE invariant 5), and add story 9136 to the fixture as a must-approve
   case so the class stops being invisible.
