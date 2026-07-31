# Story Forge — build state (phases 0-7 done, phase 8 in flight)

**THE LADDER IS ELEVEN STAGES** since 2026-07-30 (docs/DECISION-stage-ladder.md,
DEVIATIONS 12.1-12.14, migration `f3a91c6d0e24`): the user retired Appendix A's
stage 1 — old stage N is new stage N-1, old stages 1+2 merged into stage 1, and
§17's demo is dragons at **stage 2**. Stage numbers in the historical rows below
are the numbers of their day; commands and invariants are current.

Spec: `.context/attachments/D8l1e3/story-forge-build-doc.md` — all § refs are to it.
Style: `.context/attachments/Lln7Af/Story Forge Style Guide.dc.html`.
Every deviation with rationale: `docs/DEVIATIONS.md` (406 entries, numbered by phase).
Branch `k5-learning-app-gstack-setup`, fast-forwarded into `master` each phase.

## Done — phases 0-7, every claim verified live

| Phase | State |
|---|---|
| 0 scaffold | `make dev` boots PG16 + FastAPI + Vite. Style-guide tokens confirmed in a real browser (computed styles, not just a 200). |
| 1 decoder | Pure validator, **93**-GPC inventory, vendored SHA-pinned CMUdict. 18/18 §5.7 goldens. Anti-golden suite + vacuity ratchet (**44.75%**, ceiling 50%) — both sabotage-tested, and the ratchet has now refused a real proposed row (DEVIATIONS 8.12). `wh`→/w/ added at the digraph stage (8.13; stage 2 since the renumber). |
| 2 precompute | `make seed` idempotent; `make precompute` → **34,366** rows / 12 stages in 1.9s, **4,397** distinct words. `POST /api/validate` reproduces the §17 demo sentence exactly. Blocklist **228** entries → **1,009** forms with inflections, **38** sight words (DEVIATIONS 8.10–8.17, 8.73–8.74, 8.81, **11.1–11.12**). **Two filters replaced §5.4's SUBTLEX intersection and the row counts moved a long way: 64,296 → 34,366.** (a) The blocklist match is **morphology-aware** — it was `if word in blocked` against a list of BASE forms, so the exact-match hit count read **0** while 50 inflections of blocked entries (`fucks`, `asses`, `killings`, `corpses`, `punching`, `addicts`) sat in the live table; `moderate.blocklist_check` deliberately still matches literals, because a layer-1 hit short-circuits layer 3 (11.6). (b) §5.4 named the **wrong corpus** — adult film-subtitle frequency proves a word is common in adult speech, not that a five-year-old knows it, and `amid` (rank 7,528), `lebron`, `nixon` and `nasa` were offered at stage 1. Dale-Chall familiar words now filter register and lowercase Hunspell headwords filter proper nouns. **Stage 1 is 78 and that is a measured CEILING**: its nine GPCs can spell only 78 child words, and its old 157 cleared §18's 150 floor only because 27 were proper nouns and 61 were adult register. It is the ONLY stage below the floor — every other stage is 771–4,397 — so the authorised remedy is **retiring stage 1** (inventories are cumulative, so new stage 1 = old stage 2 = 771 words). **That renumbering is now DONE** (12.1-12.14): stage rows, GPC/sight-word assignments, `MAX_STAGE` (canonical home now `storyforge.decoder`), the §5.7 goldens (re-derived, not decremented), the vacuity ratchet (re-measured: 44.75%, unmoved — the top stage's cumulative set is the same 93 GPCs), §17's demo (stage 2), and live `stage_id` data via migration `f3a91c6d0e24`, sabotage-verified per old stage. `make precompute` now writes **34,221** rows / 11 stages, **769**–4,397 per stage — every stage clears §18's 150 floor. **The 34,288/771 those numbers replace were measured with the syllable cap asking the wrong question** (DEVIATIONS 21.2): the cap was checked against `min_syllables(prons)` — the fewest syllables in ANY CMUdict pronunciation, including ones the aligner could not use at that stage — so **67 rows over 20 words (`every`, `family`, `several`, `towards`, `different`…) were offered at stages whose cap they broke**, and 199 rows carried a count from a reading no child there can produce. It is now `aligned_syllables`, counted per stage over the pronunciations that actually align, and `test_syllable_cap_is_honoured` could never see this because it compares the STORED number to the cap — precompute agreeing with itself. **The vacuity ratchet is also no longer a property of a stage number** (21.3): it measured `mastered_ids(MAX_STAGE)`, which equals the full inventory only by the convention that the top stage is review, so a re-stage moves the reading without moving permissiveness (simulated at `MAX_STAGE=8`: 44.00% and a quiet pass, against 44.75% unmoved). It measures `full_inventory()` now, and asserts the top stage teaches all 93. |
| 3 generation | `make eval-smoke` PASSES live: 4/4, 99–100% decodability, p95 rewrite depth 0–1, ~$0.04/run. `make warm` **targets** ≥3 per (stage,theme) over all 11 stages × 3 themes, idempotent — see the pool row below for what the database actually holds. Moderation = free blocklist + Haiku judge. |
| warm pool | **`make warm-check` is GREEN: 33 of 33 (stage,theme) pairs hold ≥3 servable stories, and 11 of 11 STAGES hold ≥5** — the pair count fell 36→33 with the stage-1 retirement, and the per-stage floor was added because the pair gate read green while 29 of 33 pairs held fewer than 5, which is §8's actual promotion requirement. Filled in three passes for **$0.35** total, because `make warm` fills only the shortfall — it is not a re-run. Two earlier versions of this row were wrong in opposite directions and both are worth remembering: the first claimed "`make warm` fills ≥3 per (stage,theme)" when the command defaulted to `--stages 1,2,3 --themes dragons`, so a first deploy got 9 stories across 3 stages — and §17's demo is dragons at a low stage, so **the demo would have passed with nine stages empty**. The second recorded the pool as 13/36 with the gate red, which was honest when written and is now stale. The lesson is the gate, not the number: `make warm-check` exits non-zero and is the only claim here that cannot rot (DEVIATIONS 8.40–8.48). |
| 4 enrichment + API | `POST /api/stories/request` returns a full StoryDTO with questions + assets — verified live. §11.3 questions prove their own correctness from GPC alignments. 42-tag closed scene vocabulary. Langfuse §15 tracing wired; trace ids on `stories`. |

**API: 1190 passed, 3 skipped** (`cd services/api && uv run pytest`; the suite uses
pytest-randomly and has pre-existing order-dependent failures under a random seed
— use `-p no:randomly` for a clean baseline). Web: **239 pass**, `tsc` clean,
`bun run build` clean. This row read "775 passed, 2 skipped" for long enough to be
stale by 415 tests; an agent flagged it rather than trusting it.

**Postgres is on port 5433, not 5432** — `services/api/.env` is the authority, and
source it BEFORE overriding `DATABASE_URL`, never after. Hardcoding 5432 gives
`connection refused`, which surfaces as a DIFFERENT set of failures every run
(each run dies wherever it first needs the database) and reads exactly like the
concurrent-writer problem described below. It is not. Prove which by connecting
once by hand before diagnosing a race.
**The suite shares one live Postgres, so a concurrent writer makes it lie.** Two
agents running it at once produced *moving* failures — `test_eval.py` x7, then
`test_readings`, then `test_auth` — none reproducible alone, because these tests
sweep real rows (`SELECT … WHERE status = ANY(SERVABLE_STATUSES)`) and delete by id
window. Before believing a red suite, re-run it against a private copy:
`docker exec storyforge-db sh -c 'pg_dump -U storyforge storyforge | psql -U storyforge -d sfscratch'`
then `DATABASE_URL=…/sfscratch uv run pytest` (note port 5433). The counts above are from exactly that
(DEVIATIONS 8.47).
**Web: 239 tests** (`cd apps/web && bun test src`) over the pure `lib/` helpers —
the line said 137 for several phases after the number had moved.
`make test` = `test-api` + `test-web`, and **`test-web` is now typecheck AND
tests** — it was typecheck alone for five phases, which is why the "395 tests
green" line that used to sit here described 395 *Python* tests and zero lines of
web behaviour (DEVIATIONS 7.30). Component tests are still absent by decision;
the components are verified in a real browser — and `bun test src` therefore
covers **no** component behaviour at all, which is how a chip could ship with no
picture and a page could ship clipped under a green suite (DEVIATIONS 13.4,
13.1). Two of this pass's fixes are held by the TYPE system instead
(`StoryWordChip.assetUrl`, `StoryText`'s all-or-nothing chip props) and one by
`make fit-check`, because those are the two things a suite of pure-helper tests
structurally cannot see.
Lint: `cd services/api && uv run ruff check storyforge tests`.

## Commands
```
make dev | test | seed | precompute | warm | warm-plan
make warm-check | promote-check | prompt-check | mcq2-check | fit-check \
     story-word-check | vocab-check | art-check                # the eight gates
make art-repair                        # free, no API call (DEVIATIONS 26.4)
cd services/api && uv run python -m storyforge.warm --story-word-repair     # free
cd services/api && uv run python -m storyforge.quarantine_story_words       # free
       # ^ the two halves of --check-story-words. The first DROPS a chip the reader
       #   never needed; the second PULLS a story whose chip no repair can fix (the
       #   word is in the text either way) and prints the PAID refill (31.3-31.4).
make eval | eval-smoke
cd services/api && uv run python -m storyforge.{eval,warm,precompute,seed}
```
`make warm` covers §7's whole matrix — **11 stages x every theme x 3 stories**, the
defaults are `--stages 1..11 --themes all`, and it is the only step that spends
money. `make warm-plan` (`--dry-run`) prices it first; **`make warm-check`
(`--check-coverage`) is the gate and exits non-zero while any (stage,theme) pair
holds fewer than 3 servable stories.** Run it after every warm run and after
anything that moves the pool: a partial pool is invisible at runtime, because §7's
ladder answers an empty pair by silently serving a lower stage (DEVIATIONS
8.40–8.48). **It now asserts a SECOND floor in the same command: ≥
`mastery.STORIES_REQUIRED_AT_STAGE` (5) servable stories per STAGE across every
theme, imported and never restated** (DEVIATIONS 14.31). The two numbers had never
met, and the live pool showed the cost: 33 of 33 pairs at target with the gate
green while **29 of those 33 pairs held fewer than 5** and stages 5-11 held exactly
3 in every one of their 21 pairs. Per stage the same pool is 24/10/11/11/9×7 —
every stage clear. Stages is the right unit because of the SORT KEY: since
`a3e5be2` `_SERVE_SQL` orders `stage_id DESC`, then *unread by this kid*, and only
then the requested theme, so a child whose theme is exhausted gets another theme AT
THEIR STAGE before the ladder drops one. **`TARGET_PER_PAIR` stays 3** — raising it
to 5 would demand 165 stories against today's 119 to buy what the sort key already
delivers free. Both floors GREEN today. **`make promote-check` (`--check-promotability`) is the SECOND gate
and measures the other floor** — the §8 evidence those stories can yield — and it
is RED today: six stages are terminal dead ends (invariant 15, DEVIATIONS 11.70).
**`make prompt-check` (`--check-prompts`) is the THIRD gate and the only one that
reads what a question SAYS** — non-zero while a servable `tap_word` row carries an
instruction no rung of the current `PROMPT_FRAMES` produces. GREEN today, and it was
120 of 120 RED until DEVIATIONS 11.82-11.85: 11.60 rebuilt the frame ladder, nothing
re-derives a prompt already in the pool, so for two phases every child was read a
retired sentence — 66 of 120 of which never said what the task was — under a green
suite and two green gates. Its remedy is `--backfill`, which re-derives prompts in
place with no API call and never DELETEs a row §8 evidence hangs off.
**`make mcq2-check` (`--check-mcq2`) is the FOURTH gate and `prompt-check`'s other
half** — non-zero while a servable `mcq2` row is one the current `check_mcq2` would
refuse: the question names the word distinguishing its own two cards, or the false
card is the true card with `not` in it. GREEN today, and it was **37 of 70 RED**
until DEVIATIONS 11.99-11.103, **7 of them pointing the child at the WRONG card**.
Same trap a third time: 11.64 fixed the generator, `--mcq2-repair` was written and
never run, and `_UNENRICHED_SQL` cannot see a leaking question because all its
clauses ask what a story is MISSING. Its remedy is `--mcq2-repair` (a DELETE whose
two refusals are SQL predicates: it cannot cascade a recorded answer and cannot
leave a story with no question) followed by `--backfill`. The two rows that carried
recorded answers were **resolved by operator decision** with
`--mcq2-archive-answered` (11.116): every answer copied onto
`stories.gen_meta.mcq2_answers_archived` first, then the question removed. Zero
rows pending today. **That archive lives at the TOP level of `gen_meta`, never
under `enrichment`** — `_record_enrichment` replaces that sub-object wholesale by
design, so the first version of the archive was erased by the very next
`--backfill`, four rows of two real children's history (11.117). A key that must
outlive an enrichment must not live inside one.
**`make fit-check` is the FIFTH gate and the first that looks at what a child
SEES rather than at what the database holds** — §10: "One segment per screen, no
scrolling on tablet — if text overflows, generation length is wrong, not the
layout." It renders every servable segment through the real `ReadingView` in the
real production bundle at both §10 viewports and both §11 reading sizes and
exits non-zero on any text below the clip edge. **RED today, and it has never
been green**: 49-68 of 763 segments are clipped, **every one of them `idx = 6`,
the last page**, which is the page carrying the ending — and because the clip
falls between lines nothing looked sliced, so a child pressed `Finish` on a story
they had not read the end of (DEVIATIONS 13.1-13.3). The spine is 20-31
characters at every stage while the last page averages 45 at stage 1 and 586 at
stage 9; the measured budget is <=131 chars at the §10 reference viewport.
**Portrait is no longer the binding configuration** (DEVIATIONS 25.2): it used to
hold ~81 chars at 40px against ~131 landscape, because §06's full-width 4:3 art
took 531 of the portrait box's 791 usable pixels and left three lines — the art
is now a flex item with a `min-h-[40%]` floor, so it yields height only on the
pages that would otherwise hide words, and portrait clipping fell 73→49 (34px)
and 75→57 (40px) while landscape stayed at 65/68, correctly. The remedy for what
is left is a per-stage length budget on
GENERATION — never a smaller font (§03 floors story text at 28px) and never a
shorter tap target (§11 floors it at 48px). Free: one SQL query, a build, and a
headless browser (`BROWSE_BIN=`), no API client. **It exits 2 when it cannot
run** — no browser, no database, no build are never a pass.
**`make story-word-check` (`--check-story-words`) is the SIXTH gate and the first
that reads what a picture CHIP is spent on** — §5.5: a story word is a word the
child is GIVEN instead of decoding, three slots per story and no more. Nothing
bounded which words could fill one; `story_word_bank.min_stage` is a hand-set
SEMANTIC floor with no relation to whether the reader can already read the word.
**RED today, and it has never been green: 248 of 326 live chips (76.1%) are
outside the band — 225 of them (69.0%) on a word the reader could ALREADY
decode.** `dragon` is decodable from stage 1, so every dragons story on the
ladder chipped a word its reader could sound out, while `castle` — which this
93-GPC inventory can never align at any stage — is the word the chip was invented
for. The band is `pipeline.STORY_WORD_MAX_LOOKAHEAD`: undecodable at the reader's
stage, and either permanently unalignable or decodable within 3 stages. Enforced
at GENERATION (`load_vocabulary`), so nothing generated since can carry one;
this gate audits the backlog. **Two remedies, not one** (14.40): `--story-word-repair` is FREE
and drops every chip whose word the reader can already decode AND that the stage's
`allowed_words`/`sight_words` permit — **182 of the first 225, run against the live
pool, $0.00**, idempotent, never editing story text and never reading a questions
row. The rest is regeneration: a chip ABOVE the reader's stage is load-bearing (the
word is in the story either way, so dropping it leaves a word the child cannot read
with no way in). Free: one SQL query and the decoder
(DEVIATIONS 14.20-14.21, 14.40-14.42).
**GREEN today, and it had never been green** (DEVIATIONS 31.1-31.6). The residual
14.42 left was two defects wearing one face. (a) `dragon`/`planet` were held back
by condition 2 — no stage's `allowed_words` permitted them — and **that was a
defect in the WORD LIST, not in the stories**: `dragon` is decodable, is what a
theme is named after, and Dale-Chall (1948) simply does not list it, so **38 of 38
servable dragons stories contained the word while 0 of 11 stages offered it.** The
exclusion prevented no occurrence; it forced each into either a wasted chip (35) or
a `--check-vocabulary` violation (3), and since 14.20 closed the chip route too, a
dragons story generated today had NO licensed way to say "dragon" at all. Two words
in `data/child-vocabulary-additions.txt` + `make precompute` let the FREE repair
reach them: **38 chips dropped from 38 stories, $0.00.** (b) The 16 far chips
(`moon` ×11, `stars` ×4, `giraffe`) have no free repair and no allowlist answer —
`moon` is already offered at stage 7, the defect is that stage-1 stories chipped
it. Quarantined with `python -m storyforge.quarantine_story_words` (**12 stories,
all 12 rows KEPT, 10 readings and 1 answer preserved**), which emptied (stage 1,
space) and (stage 2, space) entirely, then refilled for **$0.502206**. The
handed-down counts (43 + 23) were wrong in both directions; they were 38 + 16.
**`make vocab-check` (`--check-vocabulary`) is the SEVENTH gate and the first
that reads the pool against something OUTSIDE it** — every servable title and
body re-validated against the CURRENT `allowed_words`, because a precomputed
input changing must invalidate the artefacts built from it (DEVIATIONS 22.1-22.9).
**RED, at 23 of 106 stories / 6 words — but its `REGISTER` class, the only one
the gate itself calls a child-facing defect, is now EMPTY** (31.1, 31.7). What is
left is two classes that money cannot close, and the NAME class is the reason:
`sam`/`tom`/`dan` are refused by the proper-noun filter ANSWERING CORRECTLY — a
name is a name, and 11.9 exists to keep `nixon`/`nasa` out — while `SYSTEM_RULES`
rule 6 *demonstrates* `"Sam sat. A cat sat on Sam."` as correct output. **Measured:
~10% of newly generated servable stories carry a FRESH name offence (7 of the 67
most recent), so this is an unbounded SOURCE TERM, not a backlog** — regenerating
the 13 buys a smaller number that refills itself. The remedy is a decision, not a
spend: either a small curated decodable name list fed into the prompt AND into
`permitted_at`, or rule 6 stops demonstrating a name. Both are prompt changes and
invalidate every cached prefix (invariant 10). The `every`/`family`/`together`
class (11 stories) is pure 21.2 drift, costs ~$0.40, closes no gate while the NAME
class stands, and would risk stages 5-7 which sit at EXACTLY the coverage floor —
deliberately not spent.
**`make art-check` (`--check-art`) is the EIGHTH gate and the first whose unit is
the STORY** — §12's illustration. The owner's first complaint about this app was
"there are no images in the stories", and the art was never missing: **33 asset
rows, 33 files, HTTP 200 in production.** The defect was SELECTION. §12's
"deterministic tiebreak by asset id" degraded to lowest-id-always-wins, which is
not a tiebreak between two pictures but a standing preference for one of them,
re-applied on every page — so **4 servable stories rendered ONE picture on all
seven pages and 40 more rendered two**, with asset 24 alone on 160 of 763
segments. **No per-segment metric could see it: those stories record
`segments_with_art: 7` of 7, full marks, because the repeat does not exist at the
segment** — a metric denominated in the wrong unit is not a weak signal, it is a
green light. The rule is now (overlap, then an asset this story has NOT used,
then lowest id): novelty is a TIEBREAK and never a penalty, because demoting a
better match would put a *wrong* picture on the page. **GREEN today**, after
`make art-repair` (`--reselect-art`) — FREE, no API call, because it re-selects
over the `scene_tags` already stored: **226 screens across 108 of 110 servable
stories, run against the live pool, $0.00**, idempotent, never writing
`scene_tags`, never reading a questions row, never editing story text. Distinct
pictures per story went 1x4/2x40/3x34/4x20/5x12 -> 2x1/3x11/4x32/5x30/6x26/7x10,
and **5 of the 30 segment illustrations had never been shown to any child**.
**The remaining hole was the LIBRARY, not the selector, and it is now CLOSED at
the top of the demand curve** (DEVIATIONS 26.1-26.8 diagnosed it, 28.1-28.6 paid
for it in drawings). It was 30 segment images against a 42-tag vocabulary,
covering 22/20/15 of 42 tags per theme, with 3 tags (`climbing`, `eating`,
`storm`) having no asset anywhere and 5 screens rendering §06's placeholder for
want of one. **Ten more pictures now**: 40 segment + 3 cover = **43 asset rows,
43 files**, coverage **28/23/15**, **every one of the 42 tags reachable in some
theme**, and **0 screens tagged-but-unmatched** (was 5). Distinct pictures per
story went 2x1/3x11/4x32/5x33/6x19/7x10 -> **2x1/3x1/4x13/5x35/6x38/7x18** after
a free `--reselect-art` (185 screens, 65 stories, $0.00, idempotent). Every one
of the ten is in use — 163 screens between them. **§12's "~8-12 images per
theme" is the half of the spec that gave way**: it mandates a CLOSED 42-tag
vocabulary in the same sentence, and eleven pictures cannot answer forty-two
tags. The transcribed 12-ceiling in `test_each_seeded_theme_has_eight_to_twelve_
assets` is retired; the FLOOR of 8 is unchanged, and two stronger assertions
replaced the ceiling — a pool may not grow without broadening (segments <= tags
covered) and per-theme coverage is a RATCHET at 28/23/15. **The honest residual
is 8 (theme, tag) pairs over 25 screens** — `dragons water` 9, `dragons village`
6, then a tail of 1-2 — **none of which renders a placeholder**; they are
answered through another tag, so what is lost is match quality, not the picture.
The 18-19 untagged screens are unchanged and correct (the prompt invites an
empty list; invariant 11 says an enrichment node degrades).
All eight are free, and none implies the others.
`make eval` is the §14 harness and the §1-item-2 check: 11 stages x 3 themes x 5
stories against the live model, writing `eval_runs`/`eval_run_items` and
`reports/eval-{ts}.md`. It exits non-zero when the criteria are not met. Latest
numbers, model ids, pricing date and total cost: **`docs/EVAL.md`**.
`make eval EVAL_ARGS="--estimate-only"` projects the spend with no API call;
`--resume RUN_ID` continues a run that was interrupted (DEVIATIONS 7.51).
`.env` at `services/api/.env` (600, gitignored) holds Anthropic + Langfuse keys.

## Hard-won invariants — do not regress

1. **The validator is the gate; never weaken it** (§0). Fix prompts/allowlists instead.
   **And a vacuous pass is not a pass.** `passes_gate` refused NOTHING about a story
   with no words in it: `validate_text` scores `decodable / scored if scored else
   1.0`, so `""`, whitespace, `"... !!!"` and a `StoryDraft` with `segments=[]`
   (the schema has no `min_length`) all arrived at 100% decodable, 0 OOV, 0 story
   words — three clauses satisfied, none of them asked about anything. §1's promise
   is "every word is one the child can read" and zero words satisfies it while
   proving nothing; the story would have been moderated (a paid Haiku call on a
   story that can never be served, invariant 11) and persisted `warm`, and a child
   would press `Finish` on a blank screen while §8 recorded a perfect reading. It
   is a HARD REFUSAL now, in the gate rather than in `validate_text` — the report
   is a measurement and 1.0 is the honest value of an empty ratio; the gate is the
   judgement. The refusal reads `TextReport.word_count`, never
   `scored_word_count`: a text of nothing but sight and story words also scores
   1.0 on an empty denominator and must still PASS (DEVIATIONS 21.1).
2. **The LLM is untrusted.** `pipeline.validate_draft` intersects the model's
   `story_words_used` claim with the OFFERED bank — otherwise it shrinks its own
   decodability denominator and self-reports 100%. Live-observed.
3. **Story-word cap counts DISTINCT words**, not occurrences. Repetition is
   correct decodable-reader behaviour.
4. **Moderation fails closed** — unparseable verdict ⇒ `approved=False`.
5. **Moderation separates hazard from harm.** A fire-breathing dragon is the
   premise. The first prompt had a 55% false-positive rate. Any change must be
   re-tested in BOTH directions, against the live judge, over MULTIPLE runs —
   the 40-case fixture in `tests/test_grammar.py::FIXTURE` (24 must-approve,
   16 must-reject), run with `STORYFORGE_LIVE_MODERATION=1`. It costs ~$0.14 a
   run. **Every one of the four passes at the grammar prompt in DEVIATIONS
   8.70–8.82 broke a case that had been passing, and none was caught by testing
   the case being worked on** — naming a construction raises its salience in both
   directions, and tightening an output format silently deleted the evidence
   another rule depended on. Run the WHOLE fixture, more than once.
   The blocklist is in scope too: a layer-1 entry short-circuits `moderate()`, so
   an innocuous word there does not narrow vocabulary, it stops this judgement
   from ever running (8.73). The live test asserts every must-reject case dies at
   `layer == "model"` for that reason.
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
14. **"Servable" is `SERVABLE_STATUSES` AND `ANSWERABLE_SQL`** — a story with zero
    `questions` rows is a dead-end session (read to the end, no question screen, no
    §8 evidence, no promotion signal) and is refused by `_SERVE_SQL`, by `GET
    /api/stories/{id}` and by `POST /api/readings`. Both halves are defined once in
    `api/stories.py` and imported; `warm.pool_coverage`/`pool_depth` use the same
    predicate, because a gate that counts stories the serving path will not serve
    reports a pair as full while every child at it gets a silent §7 fallback. Six
    live `warm` stories were in exactly that state (DEVIATIONS 8.50–8.56).
15. **§7's floor and §8's floor are DIFFERENT floors, and only the first was ever
    measured.** `make warm-check` counts stories a child can be SERVED (≥3 per
    (stage, theme)); it says nothing about whether those stories carry the §8
    evidence that promotes a child OFF the stage. Measured with `make
    promote-check` on the live pool: **six of the eleven stages were terminal** —
    a perfect reader at stage 3, 6, 7, 8, 9 or 10 who read every story their
    stage held could never be promoted, because a re-read yields no evidence by
    design and the whole stage was worth fewer than `MIN_GPC_OBSERVATIONS`. Nine
    of the ten promotable stages failed DEVIATIONS 11.67's promise that the floor
    is reachable inside §8's five stories. **Never lower the floor to make this
    green** (5.5: it is what stops one lucky tap promoting a child) — the remedy
    is supply, and at stages 9-11 it is the allowlist, not the question count
    (DEVIATIONS 11.70-11.74). **Now five terminal, not six** (`[6, 7, 8, 9, 10]`;
    8 of 10 stages below the floor, stage 3 promotable and stage 5 clearing it):
    the `--backfill` that re-enriched 37 mcq2-stripped stories wrote 11.67's
    `extra_tap_words` and the pool gained 17 `tap_word` rows. Still RED, and the
    invariant is the shape, not the number (DEVIATIONS 11.99-11.103).
16. **Every sound `sound_targets` offers is provably answerable, and `tap_word` has
    a pure-function proposer of last resort.** A tier of `_prefer_current_stage`
    fires on answerability, not non-emptiness — "first non-empty tier" handed stage
    8 a menu of one impossible sound (`ou`/`outside`) and cost the story both
    questions. When the model's three attempts are spent, `tap_word_options`
    composes the triple and the UNCHANGED `check_tap_word` judges it. Measured:
    460/460 derived drafts accepted, 113/113 servable stories answerable, under the
    unchanged `TAP_LEN_SPREAD`. Never relax the length rule to manufacture a
    question — it was measured not to be the binding constraint (DEVIATIONS 8.53).
17. **A story word is BOUNDED ON BOTH SIDES, and the floor is the half that
    matters.** §5.5's chip is a word the child is GIVEN instead of decoding, three
    slots per story. `pipeline.STORY_WORD_MAX_LOOKAHEAD`: undecodable at the
    reader's own stage, and either permanently unalignable by this 93-GPC
    inventory or decodable within 3 stages. **225 of 326 live chips (69.0%) sat on
    a word the reader could already decode** — a slot spent, the decoding practice
    hidden, and a decodable word dropped out of the denominator, which is
    invariant 2's defect committed by the BANK instead of by the model. Never
    widen the band to make a theme fit: the bank is the thing that is short (7 of
    24 entries are decodable from stage 1 and offerable nowhere), and the measured
    counterweight is `test_no_stage_and_theme_is_left_with_nothing_to_offer` —
    §18 needs every (stage, theme) to keep at least one candidate, and stage-1
    space had none until `alien`/`astronaut` moved to `min_stage` 1. The band is
    enforced where the OFFER is built and never inside `validate_draft`: that is a
    measurement, and `_is_decodable_for` re-scores live stories with it at serve
    time (DEVIATIONS 14.20-14.21). **A chip is free to REMOVE only when the reader
    can already decode the word AND the stage's `allowed_words`/`sight_words`
    permit it — both, never either.** Dropping one above the reader's stage leaves
    a word they cannot read with no way in; dropping a decodable word the
    vocabulary does not permit trades a wasted chip for a §5.4 violation. 182 of
    225 met both and were repaired free against the live pool; 43 did not
    (14.40-14.42). **Condition 2's own worked example turned out to be a defect in
    the WORD LIST, and that is the lesson worth keeping** (31.1): `dragon`/`planet`
    were "in NO stage's `allowed_words`" because Dale-Chall (1948) does not list
    them, not because they are adult register — and the pool showed the exclusion
    cost 35 wasted chips and 3 vocabulary violations while preventing **zero**
    occurrences of the word (38 of 38 servable dragons stories contained it). The
    rule is unchanged and both conditions still hold; the DATA moved, via
    `data/child-vocabulary-additions.txt`, which is ratcheted by
    `test_the_local_additions_are_a_closed_deliberate_set` and never patched into
    the SHA-pinned vendored list. **When a condition keeps refusing the same word,
    ask whether the word or the list is wrong — and require a MEASUREMENT in the
    pool before widening, never a hunch about the word.**
18. **A client-side fallback is only real if it is tested against the payload the
    SERVER SENDS.** `lib/grade.ts` is what decides right from wrong when
    `POST /answers` does not answer, and `Comprehension` turns "undecidable" into
    ACCEPT on purpose — so a fallback that cannot decide is a screen that marks
    every tap correct. Its `tap_word` branch read `payload.target_grapheme`, which
    `_public_payload` **redacts by design** because it is the answer key, so the
    branch was dead for every live question and `grade.test.ts` was green
    throughout on a fixture that supplied the redacted field. Driven live with the
    network down: cue `get`, tapped `up`, straight to "You read the whole story!".
    Four doors reach it and they are one branch — a dropped socket, a 401 from a
    Clerk token whose lifetime is 60s, a 500, and a `readingId` that never opened.
    The recovery derives the answer from `cue_word`, which is already served and
    already printed on screen, through the three properties `check_tap_word`
    proves; it returns `null` on a tie, on no shared onset, and on a response
    outside the offered options, so it adds a way to be right about "wrong" and no
    way to guess. **Never let a test fixture invent a field the DTO omits**
    (DEVIATIONS 25.1).

| 5 kid mode + mastery | Full kid session live in a real browser (dev AND production bundle): theme picker → reading → questions → celebration, TTS, §11 floors verified by computed style. `readings`/`answers` APIs, `gpc_stats`, stage promotion proven at the 79/80% boundary. **§11 re-measured from RENDERED PIXELS at both §10 viewports and both reading sizes** (DEVIATIONS 25.3): no contrast, tap-target, gap or focus-order violation; narrowest margins are the earned foil seal at 7.24:1 (floor 7) and the kid primary button at 4.70:1 (floor 4.5); smallest gap between two targets 9.5px (floor 8); and `speechSynthesis` was wrapped for a whole session — 72 operations, every `speak()` preceded by a `cancel()`, never two at once. |

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
