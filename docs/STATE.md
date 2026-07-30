# Story Forge — build state (end of Phase 4)

Spec: `.context/attachments/D8l1e3/story-forge-build-doc.md` — all § refs are to it.
Style: `.context/attachments/Lln7Af/Story Forge Style Guide.dc.html`.
Every deviation with rationale: `docs/DEVIATIONS.md` (~45 entries).
Branch `k5-learning-app-gstack-setup`, fast-forwarded into `master` each phase.

## Done — phases 0–4, every claim verified live

| Phase | State |
|---|---|
| 0 scaffold | `make dev` boots PG16 + FastAPI + Vite. Style-guide tokens confirmed in a real browser (computed styles, not just a 200). |
| 1 decoder | Pure validator, **93**-GPC inventory, vendored SHA-pinned CMUdict. 18/18 §5.7 goldens. Anti-golden suite + vacuity ratchet (**44.75%**, ceiling 50%) — both sabotage-tested, and the ratchet has now refused a real proposed row (DEVIATIONS 8.12). `wh`→/w/ added at stage 3 (8.13). |
| 2 precompute | `make seed` idempotent; `make precompute` → **64,290** rows / 12 stages in 1.5s (stage 1 = 157, the tightest, above §18's 150 floor). `POST /api/validate` reproduces the §17 demo sentence exactly. Blocklist **224** entries, **38** sight words (DEVIATIONS 8.10–8.17). |
| 3 generation | `make eval-smoke` PASSES live: 4/4, 99–100% decodability, p95 rewrite depth 0–1, ~$0.04/run. `make warm` fills ≥3 per (stage,theme), idempotent. Moderation = free blocklist + Haiku judge. |
| 4 enrichment + API | `POST /api/stories/request` returns a full StoryDTO with questions + assets — verified live. §11.3 questions prove their own correctness from GPC alignments. 42-tag closed scene vocabulary. Langfuse §15 tracing wired; trace ids on `stories`. |

**API: 643 passed, 1 skipped** (`cd services/api && uv run pytest`; the suite uses
pytest-randomly and has pre-existing order-dependent failures under a random seed
— use `-p no:randomly` for a clean baseline).
**Web: 110 tests** (`cd apps/web && bun test src`) over the pure `lib/` helpers.
`make test` = `test-api` + `test-web`, and **`test-web` is now typecheck AND
tests** — it was typecheck alone for five phases, which is why the "395 tests
green" line that used to sit here described 395 *Python* tests and zero lines of
web behaviour (DEVIATIONS 7.30). Component tests are still absent by decision;
the components are verified in a real browser.
Lint: `cd services/api && uv run ruff check storyforge tests`.

## Commands
```
make dev | test | seed | precompute | warm | eval | eval-smoke
cd services/api && uv run python -m storyforge.{eval,warm,precompute,seed}
```
`make eval` is the §14 harness and the §1-item-2 check: 12 stages x 3 themes x 5
stories against the live model, writing `eval_runs`/`eval_run_items` and
`reports/eval-{ts}.md`. It exits non-zero when the criteria are not met. Latest
numbers, model ids, pricing date and total cost: **`docs/EVAL.md`**.
`make eval EVAL_ARGS="--estimate-only"` projects the spend with no API call;
`--resume RUN_ID` continues a run that was interrupted (DEVIATIONS 7.51).
`.env` at `services/api/.env` (600, gitignored) holds Anthropic + Langfuse keys.

## Hard-won invariants — do not regress

1. **The validator is the gate; never weaken it** (§0). Fix prompts/allowlists instead.
2. **The LLM is untrusted.** `pipeline.validate_draft` intersects the model's
   `story_words_used` claim with the OFFERED bank — otherwise it shrinks its own
   decodability denominator and self-reports 100%. Live-observed.
3. **Story-word cap counts DISTINCT words**, not occurrences. Repetition is
   correct decodable-reader behaviour.
4. **Moderation fails closed** — unparseable verdict ⇒ `approved=False`.
5. **Moderation separates hazard from harm.** A fire-breathing dragon is the
   premise. The first prompt had a 55% false-positive rate. Any change must be
   re-tested in BOTH directions (3 approve + 3 reject cases).
6. **The fallback ladder never climbs stage.** A higher-stage story can contain
   untaught GPCs; a lower stage is always safe (cumulative inventory).
7. **Tailwind verdict CSS lives outside `@layer utilities`** — dynamic class
   names get purged, silently collapsing all four verdicts to plain ink.
8. **`data/seed/gpcs.csv` is a symlink** into the decoder package. A Phase 7
   Docker `COPY` must target the package path.
9. **Sonnet 5**: adaptive thinking is ON when `thinking` is omitted and counts
   against `max_tokens` (hence 8000); non-default `temperature`/`top_p`/`top_k`
   → 400. Model IDs are bare aliases, never date-suffixed.
10. Prompt is split for per-stage cache reuse: frozen rules + stage vocabulary in
    `system` (breakpoint after vocabulary), per-request material in `messages`.
11. **Enrichment runs only after the gate AND moderation** — never spend Haiku
    calls on a story that will not be served. Both nodes degrade, never raise.
12. **Tailwind CONFIG edits do not hot-reload dev** (6.5) — the inverse of 7.
    Restart Vite after touching `tailwind.config.ts`; verify tokens in `dist/`.
13. Async SQLAlchemy: never touch a lazy relationship (`story.segments`) —
    re-query instead, or it raises `MissingGreenlet`.

| 5 kid mode + mastery | Full kid session live in a real browser (dev AND production bundle): theme picker → reading → questions → celebration, TTS, §11 floors verified by computed style. `readings`/`answers` APIs, `gpc_stats`, stage promotion proven at the 79/80% boundary. |

## Recently closed
- **Phase 4 adversarial audit** — 8 findings, all fixed (DEVIATIONS 5.12–5.16).
  Question text bypassed every §13 layer; 9 of 20 servable stories were
  unenriched and the failure was silent; the `tap_word` answer key shipped to
  the client; enrichment cost (45–48% of the total) was discarded.
- **Grammar** (DEVIATIONS 4.12, 6.24–6.27). **The earlier entry here claimed
  "20–27% → 0% ungrammatical" and was wrong.** That number described new
  generation measured in the eval harness; it never described the database.
  `warm.persist()` wrote only three of the verdict's five fields, so
  `well_formed` was discarded on write for EVERY story — including ones
  generated after the fix. The check ran, passed, and was thrown away.
  A backfill over the real pool found **4 of 28 servable stories (14%)
  ungrammatical and still being served**, among them story 20 — the exact
  `"He did not came back."` case 4.12 was written about. All four are now
  `rejected`. Fixed at the source in `_body_verdict`, so the backlog cannot
  regrow after the next `make warm`; the backfill is idempotent (verified: a
  third run selected nothing and made no API call). Sweep cost $0.0468.
  **Lesson worth keeping: a metric measured in the harness is not evidence about
  production data.** Prevention is real (`SYSTEM_RULES`, 0 false positives);
  the claim that the pool was clean was never tested until it was.
- **The generation timeout and `warm.py`'s reaction to it** (DEVIATIONS 8.0–8.6).
  Two defects, one root. §7's 30s generation timeout was the cause of all 11
  `api_fail` cells in the §14 run, and `warm.py` treated the same exception as
  fatal to a whole run.
  - **The eval's latency numbers were censored and nobody noticed.** `max_retries=2`
    means one SDK call is up to three attempts, so a story recorded at 86.6s was
    30 + 30 + 26.6, and **27% of all generation attempts the run paid for were
    killed at 30s and discarded**. The 11 failures at 91.3–91.6s match the SDK's
    own backoff constants exactly, which is what proves it.
  - **Latency is driven by output tokens (r = +0.997), not by stage (r = +0.50).**
    Adaptive thinking, not story length, is what the tokens go on. So the planned
    per-stage ramp was abandoned: measured with `max_retries=0`, a **30-word
    stage-1 story took 24.60s** against the 30s budget. §7's number was too tight
    everywhere; stages 9–12 were only where it showed.
  - Timeout is now a single **240s**, and it is *derived*, not chosen:
    `client.timeout_for_tokens(MAX_OUTPUT_TOKENS)` = 16000 / 68 tok/s. A test
    asserts the two stay consistent, so raising the token ceiling again turns the
    suite red instead of surfacing as an intermittent api_fail. `connect=5s` is
    split out — a float timeout was setting the *connect* budget to 30s too, six
    times the SDK's own default.
  - **`pipeline._call` held a stale `max_tokens=8000`** while `llm/generate.py` had
    moved to 16000 (DEVIATIONS 8.25 flagged it, 8.7 fixed it). `_call` is the node
    that actually runs, so the live path kept the old ceiling — and both api_fails
    in the 24-cell after-run were that, not the clock: `stop_reason=max_tokens`
    and a truncated JSON body.
  - `warm.py` records an api_fail per story, continues, and exits non-zero at the
    END naming the cell. **Proved live**, not with a mock: a real
    `APITimeoutError` on generation 1, then a real stage-12 story at 97.7%
    persisted on generation 2 of the same run. The two guarding tests were
    sabotage-checked — both go red when the `try/except` is removed.
  - **`of` is still unfixed** and is still the one actionable item in `docs/EVAL.md`
    §9; it is data + a `make precompute` re-run, untouched here.

## Also outstanding
- **Outside-voice / cross-model review never completed** (content-filter error on
  the subagent). Phases 2–4 shipped on single-model review.
- **No git remote pushed** — GitLab project not created. `origin` is configured
  with GitLab fetch + GitLab/GitHub push URLs, credential-free.
- `_split_segments` breaks inside quoted dialogue (DEVIATIONS 4.13) — lossless,
  cosmetic.
- Rate limiting is in-process only (DEVIATIONS 4.10).

## Next: Phase 6 (§16)
Clerk auth both sides, roles, parent home + kid CRUD, parent dashboard (tables).
DoD: auth-scoped e2e — two parents cannot see each other's kids. Several Phase 5
columns were reserved for this: `kids.parent_id` (5.1) needs its FK to `users`,
and `GET /api/kids` currently returns every kid (5.9).

## Superseded: Phase 5 (§16)
Kid mode UI end-to-end: theme picker → read → branch → questions → celebration;
TTS via browser `speechSynthesis`; `readings`/`answers` APIs; `gpc_stats` and
stage promotion. **DoD: a full kid session on a tablet viewport, stats visibly
updating.** Style guide §07 gives the component build order: Button, Card,
WordToken, StoryWordChip, ProgressDots — then the reading view is assembly.
