# Story Forge — build state (end of Phase 4)

Spec: `.context/attachments/D8l1e3/story-forge-build-doc.md` — all § refs are to it.
Style: `.context/attachments/Lln7Af/Story Forge Style Guide.dc.html`.
Every deviation with rationale: `docs/DEVIATIONS.md` (~45 entries).
Branch `k5-learning-app-gstack-setup`, fast-forwarded into `master` each phase.

## Done — phases 0–4, every claim verified live

| Phase | State |
|---|---|
| 0 scaffold | `make dev` boots PG16 + FastAPI + Vite. Style-guide tokens confirmed in a real browser (computed styles, not just a 200). |
| 1 decoder | Pure validator, 92-GPC inventory, vendored SHA-pinned CMUdict. 18/18 §5.7 goldens. Anti-golden suite + vacuity ratchet (44.6%, ceiling 50%) — both sabotage-tested. |
| 2 precompute | `make seed` idempotent; `make precompute` → 64,841 rows / 12 stages in 1.6s. `POST /api/validate` reproduces the §17 demo sentence exactly. |
| 3 generation | `make eval-smoke` PASSES live: 4/4, 99–100% decodability, p95 rewrite depth 0–1, ~$0.04/run. `make warm` fills ≥3 per (stage,theme), idempotent. Moderation = free blocklist + Haiku judge. |
| 4 enrichment + API | `POST /api/stories/request` returns a full StoryDTO with questions + assets — verified live. §11.3 questions prove their own correctness from GPC alignments. 42-tag closed scene vocabulary. Langfuse §15 tracing wired; trace ids on `stories`. |

**395 tests green, 1 skipped.** `make test` = pytest + web typecheck. Lint: `cd services/api && uv run ruff check storyforge tests`.

## Commands
```
make dev | test | seed | precompute | warm | eval-smoke
cd services/api && uv run python -m storyforge.{eval,warm,precompute,seed}
```
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
12. Async SQLAlchemy: never touch a lazy relationship (`story.segments`) —
    re-query instead, or it raises `MissingGreenlet`.

| 5 kid mode + mastery | Full kid session live in a real browser (dev AND production bundle): theme picker → reading → questions → celebration, TTS, §11 floors verified by computed style. `readings`/`answers` APIs, `gpc_stats`, stage promotion proven at the 79/80% boundary. |

## Recently closed
- **Phase 4 adversarial audit** — 8 findings, all fixed (DEVIATIONS 5.12–5.16).
  Question text bypassed every §13 layer; 9 of 20 servable stories were
  unenriched and the failure was silent; the `tap_word` answer key shipped to
  the client; enrichment cost (45–48% of the total) was discarded.
- **Grammar** (DEVIATIONS 4.12): prevented in `SYSTEM_RULES`, detected via
  `well_formed` on moderation. 20–27% → 0% ungrammatical, 0 false positives.

## Also outstanding
- **`warm.py` aborts the whole run on `anthropic.APITimeoutError`** instead of
  falling through. §7 maps `timeout` to a fallback like any other failure. Hit
  once live on a stage-5 generation. Pre-existing; not yet fixed.
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
