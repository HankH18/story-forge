# RESUME — pick up here

Written at a hard stop on 2026-07-29. Everything below is committed and pushed to
both remotes. Read `docs/AUDIT-PHASE6.md` before touching anything.

**State:** commit `76e86a1`, branch `k5-learning-app-gstack-setup`, mirrored to
`master` on GitLab (`labs.gauntletai.com/hankholcomb/story-forge`, the submission
target) and GitHub (`HankH18/story-forge`, private, build source for DO). Both
remotes verified identical at `76e86a1`.

**Test baseline:** 552 passed / 1 skipped, PLUS 4 known failures in
`tests/test_stories_api.py` described in JOB A. ruff clean, `tsc` clean,
production build clean with all five verdict classes surviving the purge.

---

## Read this first: the app is NOT demo-ready

Two CRITICAL, confirmed, child-facing defects break §0's central guarantee. The
decoder is correct and is **not** the problem — the serving path never asks it
about the right child. Do not demo, and do not deploy, until JOB B is done.

Also: **do not trust a green test run as evidence the product works.** Three of
the worst defects in this project (the forgeable promotion signal, the discarded
grammar verdict, these two) were live while the suite was green, and in two cases
tests actively encoded the defect as the contract. Every one was found by an
adversarial pass. Keep that pattern.

---

## JOB B (do first — CRITICAL, child-facing)

Full instructions are in `docs/AUDIT-PHASE6.md` under C1/C2. Summary:

### C1 — every kid session runs at stage 4 regardless of the child's real stage
The Phase 6 migration
`services/api/storyforge/migrations/versions/ab5a866f52fc_phase_6_users_and_kids_parent_id_fk.py:96`
NULLs `parent_id` for all pre-existing kids. So `GET /api/kids` returns `[]`,
`resolveKid` returns null, and `apps/web/src/routes/KidSession.tsx:109` falls back
to `DEFAULT_STAGE = 4`.

Confirmed in a browser: `/read/1648`, a **stage-1** reader, posts
`{"stage_id": 4}` and is served a story measuring **17.6% decodability at stage
1**. §1 requires >=95%. A five-year-old is shown text they cannot read. All 5
existing readers are also unreachable from the dashboard, so the §17 demo cannot
be shown at all.

Fix the orphaned `parent_id` rows AND delete the silent stage fallback. A
`DEFAULT_STAGE` constant that substitutes a wrong stage is the anti-pattern:
not knowing a child's stage must never resolve to "serve stage 4 anyway".
§01 forbids a child-facing error screen, so "show an error" and "guess a stage"
are BOTH wrong — the honest move is probably to not enter a reading session and
route back to the adult. Decide and justify it.

### C2 — the client-supplied `stage_id` is unchecked at every layer
`POST /api/stories/request` takes `stage_id` from the body with no `kid_id` and
no ownership check. `{"stage_id": 12}` returns a stage-5 story measuring **13.0%
decodable** for a stage-1 reader, reachable with no tooling via
`/read/1648?stage=12`. `storyforge/api/stories.py:40-47` argues ownership binds
later at `POST /api/readings`; that endpoint does not check the stage either — a
stage-1 kid + a stage-5 story files a reading with `201`.

Derive the stage from the child server-side. Treat the client exactly as §0
treats the LLM: untrusted. Probably means the request carries `kid_id`; update
`apps/web/src/lib/api.ts` and callers. Close the `POST /api/readings` half too.

Then add defence in depth: a story served at 13% decodable *for its reader*
should be inexpressible, not merely unlikely. `validate_text` / `mastered_ids`
are pure and fast enough to check at serve time.

### C3 — make the bypass visible (this is why C1/C2 hid so long)
With `DEMO_BYPASS_AUTH=true`, **nothing in any HTTP response discloses it** — a
log line only; `/api/health` is silent. Two agents reached opposite conclusions
about whether auth was enforced because of this. Surface it in `/api/health` and
consider a response header. **Do not weaken the §18 startup guard.**

Verification bar for JOB B: re-run the audit's exact reproductions and report
**measured decodability before/after**. Code reading is not evidence. Start your
own API on a spare port with `DEMO_BYPASS_AUTH=false` — testing against a
bypassed server is the mistake that produced this whole confusion.

---

## JOB A — the cue leak and 4 failing tests

Two different problems; do not conflate them.

**A1 — real bug.** `make warm` produced live question 4392:
```
prompt:  "Tap it. Hint: the."
payload: {"options": ["sat", "in", "then"], "cue_word": "the"}
answer:  {"index": 2, "correct": "then", "target_grapheme": "th"}
```
The cue `the` is a prefix of exactly one option. A child taps it correctly with
no phonics, and §8 then credits `th` mastery evidence for a guess — evidence that
feeds promotion. Same class as the forgeable promotion signal (DEVIATIONS 6.7).

Fix the GENERATOR (`storyforge/llm/questions.py`), not the test. Handle prefix,
suffix, substring and identity — and note a cue that identifies a WRONG option is
also a defect. Sweep the live DB for existing instances and repair in place;
`answers.question_id` is `ON DELETE CASCADE` and rows carrying
`mastery.MASTERY_QTYPES` evidence must survive (an agent already lost one row
this way). Report the count, and say plainly whether any recorded mastery
evidence is now suspect. Add a test that fails against the old generator, proven
by sabotage.

**A2 — 4 tests read the live database instead of a fixture**, so `make warm`
broke them: `test_request_returns_a_full_dto` (line 79, `assert []`),
`test_a_real_node_produced_question_never_ships_its_answer_key` (this one caught
A1 — it is doing its job), `test_empty_pair_falls_back_to_the_same_stage_and_
another_theme` (its docstring says "`space` has no stories at all" — true of the
DB when written, false now), `test_a_stage_with_no_pool_falls_back_to_the_
nearest_lower_stage`.

Make them own their fixture data. **Do not weaken the assertions** — the §7
ladder behaviour is real and must still be checked, especially that it NEVER
climbs stage (STATE invariant 6). Run `justify-test-edit` on each and record the
verdict at the edit site.

---

## JOB C — smaller audit findings (see AUDIT-PHASE6.md)

- `safeRedirectPath` misses `/\`, TAB, CR, LF — `/\evil.example/x` becomes
  `http://evil.example/x`. Only Clerk's `allowedRedirectOrigins` stops it today.
- The 401 screen says "sign in again" under a header reading "signed in as…",
  and its Sign-in button loops back.
- A non-owner at `/read/162` silently gets their OWN first reader, and the
  reading is filed against that child — silent wrong attribution.
- JWKS fails closed (correct) but has no stale-key fallback, so a Clerk outage
  past the TTL locks every user out.
- The §18 guard boots with this repo's own compose `db` hostname, a unix socket,
  `PORT`, `K_SERVICE`, and `ENVIRONMENT=prod-us`.
- The 403 branch (DEVIATIONS 6.20) is unreachable — no web route calls an admin
  route.
- ~~`warm.py` still aborts the whole run on `anthropic.APITimeoutError` instead of
  taking the §7 fallback.~~ **FIXED** — DEVIATIONS 8.4. Proved live against the
  real API, not with a mock: a real `APITimeoutError` on the first generation, a
  real stage-12 story at 97.7% on the second, one non-zero exit at the end naming
  the failed cell. The two guarding tests go red when the `try/except` is removed.
- DEVIATIONS 6.14: `test_stories_api.py::_pool` filters `status='warm'` while
  `_SERVE_SQL` serves `warm|complete`.

## JOB D — Phase 7 remainder

### `make eval` + the §14 report — DONE. Read `docs/EVAL.md`.
Full 12 stages x 3 themes x 5 stories against the live model. **169 of 169
stories that the model produced cleared the 95% gate; 0 finished below it.** p95
rewrite depth 1 (§1 budget 2), 0 OOV, 0 over the story-word cap. Cost **$2.7958**
at 2026-07-29 pricing (Sonnet 5 intro rate), $0.0178 per accepted story excluding
enrichment. Deviations 7.40-7.51.

`make eval` **exits non-zero**, on availability rather than decodability: 11 of
180 cells never produced text because §7's 30s generation timeout is too tight at
the 120-200 word stages. Four findings worth acting on, all in `docs/EVAL.md`:

1. ~~**The 30s generation timeout (§7) fails ~7% of cells at stages 9-12** and
   pushes cold p95 to 59.8s against §1.6's 12s. Successes there are landing on
   SDK retry. `warm.py` still dies outright on the same exception (below).~~
   **FIXED — DEVIATIONS 8.0-8.2, and the diagnosis above is half wrong.** Those
   latencies are *censored*: `max_retries=2` makes one SDK call up to three
   30s attempts, so 27% of all generation attempts the run paid for were killed
   and discarded, not 7% of cells. And the defect is not specific to stages 9-12
   — measured with `max_retries=0`, a **30-word stage-1 story took 24.60s**
   against the 30s budget. Latency tracks output tokens (r = +0.997), not stage
   (r = +0.50), because adaptive thinking is what the tokens go on. Timeout is now
   one constant, 120s = `max_tokens` / slowest measured throughput.
   §1.6's 12s target is separately recorded as **unmet and unreachable as
   written** (DEVIATIONS 8.6) — it budgets one API call for what §6/§7 specify as
   a multi-node pipeline with rewrites.
2. **`of` is neither decodable nor a sight word at any stage.** One of the
   commonest words in English costs every story that uses it part of its 5%
   violation budget. Appendix A's 33-row sight-word list is the fix; it needs
   `make precompute` and the §5.7 goldens re-run, so it was not made here.
3. **`data/blocklist.txt` blocks `high`** (drugs section), which killed a 100%
   decodable, entirely safe stage-6 space story — *"The stars are very high."*
   §13's own approve list names "high places". Two of 3 layer-1 rejects are this
   word. Do NOT just delete it without re-testing both directions (STATE 5).
4. **The grammar half of §13 rejects on SEMANTICS, not grammar**, in 5 of 8
   cases ("Rockets do not run", "wings cannot sit", "should be cave not can").
   Its own prompt says "Judge the grammar, not the sense." 8 of 12 moderation
   rejects are grammar, so this is most of the 12.8% fallback rate.

Also: **length compliance is 52% overall and 7-13% at stages 9-12** — the model
returns ~90-102 words against a 120-200 target. Decodability is unaffected.

Eval artifacts: `reports/eval-*.md` + `.json` (gitignored), `eval_runs` id 2 and
its 180 `eval_run_items`. The 169 stories it wrote are `status='eval'`, a fourth
status no serving path names (7.41) — verified live, 36/36 (stage, theme) probes
served a non-eval story and all 169 are 404 by id. Servable pool 52 -> 52.

### Deploy — still not done
Artifacts exist and were exercised locally (`services/api/Dockerfile`,
`.do/app.yaml`, `docs/DEPLOY.md`), **nothing is deployed and no DO resources
exist**. That is the user's call.

---

## Things that are DONE and verified — do not redo

- **Auth works end to end.** Verified in a browser with a real Clerk session:
  admin token -> 200 on admin-only `/api/validate`; no token -> 401; another
  parent's kid -> 404 identical in shape to a nonexistent id. Token lifecycle is
  clean: 16 probes over 150 s, 4 distinct tokens, **zero 401s** — long reading
  sessions do not fail mid-story.
- **The Clerk role claim needed a DASHBOARD change, not code** (DEVIATIONS 6.13).
  Session token customization is set to `{"public_metadata":
  "{{user.public_metadata}}"}`. There is no Backend API for this — a fresh Clerk
  instance will silently downgrade every user to `parent` until someone sets it
  by hand.
- **Pool warmed**: distinct servable per stage 1-5 = 9 / 12 / 10 / 10 / 11, no
  `(stage,theme)` pair below 2, **zero servable stories with no questions**.
  Promotion is reachable at every stage for the first time. Cost $0.575.
- **Grammar is fixed at the source.** It rejected stories live during the warm
  run ("He runs to bed" tense shift; "The dragon and the man sit."). Previously
  `warm.persist()` wrote 3 of the verdict's 5 fields, so `well_formed` was
  discarded for EVERY story — the check ran, passed, and was thrown away. 4 of 28
  servable stories were ungrammatical and still being served.
- **`httpx` moved to runtime deps** and proven by resolving a `--no-dev` env and
  importing `storyforge.auth`.
- Blocklist 52 -> 240 entries; 42 profanities are out of `PERMITTED WORDS`.

## Environment notes
- Chrome MCP extension is NOT connected. Use gstack's headless browser:
  `~/.claude/skills/gstack/browse/dist/browse`, isolated `BROWSE_SERVER_PORT`.
- `services/api/.env` is mode 600, gitignored; git history holds zero real keys
  (only a fake `sk_live_deadbeef` fixture). Keep it that way.
- Push with a one-off credential URL; never store a PAT. `git push origin master`
  reaches BOTH remotes — the DO spec's `deploy_on_push: true` depends on that.
