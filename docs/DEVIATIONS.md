# Deviations from the Build Doc

Per build doc §0, every architectural deviation is logged here with a one-line
rationale **before** it is made. Section references are to
`story-forge-build-doc.md` unless noted.

---

## Phase 0 — scaffold

**0.1 — Postgres published on host port 5433, not 5432.**
Avoids colliding with any pre-existing local Postgres. Container-internal port is
unchanged; only `DATABASE_URL` differs from the doc's implied default.

**0.2 — Python toolchain is `uv`, not bare pip/venv.**
The system `python3` on the build machine is 3.9.7, but §3 requires 3.12. uv
already carries cpython-3.12.13 and produces a lockfile, which strengthens the
reproducibility claim the validator's proof depends on (§3: "Pin the dict — it is
part of the proof"). Same argument, applied to the interpreter.

**0.3 — Tailwind v3.4, not v4.**
The style guide §12 ships a v3-format `tailwind.config.ts theme.extend` block and
instructs "drop these in as-is". v4 has no `minHeight` theme namespace (needed for
the `tap`/`tap-kid` 48/64px targets, style guide §11) and raises the browser floor
to Safari 16.4, which is a real risk for a tablet-first kid app on older iPads.

**0.4 — Web fonts self-hosted via `@fontsource`, not the Google Fonts CDN.**
§13 forbids third-party trackers in kid mode. A CDN font request leaks child IP
addresses to a third party; self-hosting keeps the COPPA posture the doc claims
in §13 and the README.

**0.6 — Verdict CSS lives outside `@layer utilities`.**
Tailwind only emits layered rules whose class names it finds as literals while
scanning `content`. The verdict classes are applied dynamically, so layering them
purged all four and every verdict rendered as plain ink — precisely the
"colour alone" failure style guide §11 forbids. Caught by asserting computed
styles in a real browser, not by the build passing.

**0.5 — Unimplemented Makefile targets exit non-zero.**
§0 requires `make dev/test/eval/seed` to work from the repo root. `eval`,
`precompute`, `warm`, and `codegen` are later-phase targets; they are present but
fail loudly with the phase that will implement them, rather than silently
succeeding and creating a false green.

---

## Phase 1 — decoder

**1.1 — Four GPCs move stage, and one is added, against Appendix A.**
Appendix A contradicts the §5.7 golden table. Appendix B settles it: "the tests
are the acceptance criteria, the CSV is an implementation detail."

| GPC | Appendix A | Now | Forced by |
|---|---|---|---|
| `c\|K` | 2 | **1** | `cat@1` must be DECODABLE |
| `x\|K.S` | 3 | **2** | `fox@2` must be DECODABLE |
| `er\|ER` | 7 | **6** | `singer@6`, `finger@6` |
| `y\|IY` | 11 | **6** | `city@9` decodable *and* `city@8` missing exactly `{c\|S}` |
| `n\|NG` | absent | **3** | `finger` is `F IH1 NG G ER0` — /ŋ/ spelled with a bare `n` |

**1.2 — Split digraphs are composite edges.**
`a_e` and friends consume 3 letters and emit 2 GPCs (the split vowel plus the
consonant it straddles), which exceeds §5.2.4's literal "0–2 phonemes" per step.
The vowel's phoneme and the straddled consonant's phoneme are not adjacent to the
vowel's letter, so a plain 2-D DP cannot express the move. This is the smallest
change that works and keeps the aligner data-driven.

**1.3 — Position constraints are narrow by design.**
Only genuinely position-sensitive graphemes are constrained (`kn`/`wr` initial,
`mb` final, `y` as consonant initial / as vowel final, suffixes final). Marking
doubles or `ng` as final produced false VIOLATIONs — `ng` in "singer", `ll` in
"little". A false violation is worse than a missed one: it shrinks the allowlist
and starves generation.

**1.4 — `gpcs.csv` lives in the package; `data/seed/gpcs.csv` is a symlink.**
The decoder must load identically from the API, the eval CLI and tests, so it
cannot resolve repo-relative paths — but Appendix B names `data/seed/gpcs.csv`.
One byte-sequence, both paths true, asserted by a test. Note for Phase 7: a
Docker `COPY` must target the package path, since symlinks do not survive a build
context.

**1.5 — `WordReport` gains four additive fields.**
`alignable`, `missing_minimal`, `heteronym_flag`, `schwa_relaxed`. Each exists to
surface a real limitation instead of hiding it (§5.3's posture, applied further).

**1.6 — CMUdict-known but inventory-unspellable words are VIOLATION, not a new verdict.**
`beautiful` (needs `eau`), `of` (`f`→/v/) get `alignable=False` and an empty
missing set. §5.1 defines six verdicts and stays at six. The unalignable *rate* is
the monitored alarm for the inventory being too small (§18).

**1.7 — Heteronyms are disclosed, not resolved.**
§5.2.5 mandates that any pronunciation may pass, so `read@6` is DECODABLE via
/riːd/ even in a sentence requiring /rɛd/. The validator cannot know which. Rather
than weaken the gate or silently over-credit, `heteronym_flag` records it and eval
reports the rate.

**1.8 — Minimal missing-GPC set is bounded.**
Iterative deepening over subsets of the unmastered GPCs present in the word's
lattice, capped at 14 candidates / size 3. Beyond the cap the answer is still a
correct missing set but is flagged `missing_minimal=False`, so an approximation is
never mistaken for a proof.

**1.9 — Digits tokenise as words and fail as OOV.**
A numeral in a decodable reader is a real problem, not free punctuation. Loud
beats silent.

**Measured, for the record:** on 1,500 randomly sampled CMUdict words, 35.8% are
decodable at some stage and 64.2% are unspellable by the 82-GPC inventory. That
is the conservative direction (§18) — the adult lexicon is full of proper nouns
and Latinate spellings the inventory deliberately omits, and §5.4's precompute
intersects with SUBTLEX top-20k before any of it reaches a prompt.

**1.10 — Ten GPCs added beyond Appendix A; inventory 82 -> 92.**
Measured against 92 real early-reader words, nine were unspellable: *house,
mouse, little, happy, funny, water, sunny, bunny, puppy*. Three gaps caused all
nine.

| Added | Stage | Why |
|---|---|---|
| `tt pp nn dd gg bb mm rr` | 2 | The doubling convention. Appendix A lists only `ck ss ll ff`, but without these *happy, little, rabbit, kitten, carrot, egg* are all unspellable. Same rule, same stage. |
| `e` (silent) | 5 | Standalone silent final `e` — the split digraphs already cover *cake*, but not *house*, *mouse*, *have*. |
| `wa` -> /wɔ/ | 7 | "w changes the a". Scoped as the two-letter unit rather than a bare `a`->/ɔ/, which would fire anywhere. |

The boundary applied: **does a real scope & sequence teach this?** `pp` is taught
in week three of every phonics programme; `eau` is taught to nobody at this
level. Adding the first makes the gate more accurate, the second less. Appendix B
budgets ~100 entries, so 92 is inside the doc's own target.

Explicitly NOT done: importing a descriptive GPC table (Brooks, Gontijo, 400+
entries). §5.2.5 makes the validator an existence check, so every GPC widens what
passes and none narrows it. A comprehensive table would certify *beautiful* and
*colonel* and the guarantee would mean nothing — while still reporting a healthy
number on the eval dashboard.

**1.11 — Anti-golden tests (`tests/decoder/test_anti_golden.py`).**
The golden suite proves the validator accepts what it should; nothing there stops
the inventory growing until it accepts everything. Two guards:

- 16 named words that must never align at any stage, each annotated with the
  correspondence it would require.
- A vacuity ratchet: the share of an 800-word adult-dictionary sample decodable
  at stage 12, currently 44.6%, ceiling 50%.

Both were **sabotage-tested rather than assumed**. Adding `eau|Y.UW` trips the
named list on *beautiful*; an eleven-rule junk vowel import that hits no named
word trips the ratchet at 58.8%. The ratchet's first draft used a 0.60 ceiling
and let that same import pass — a guard nobody has fired at is not a guard.

Known limitation: the ratchet does not cleanly separate good growth from bad. The
ten justified rows above moved it +8.8 points; the junk import moved it +14.2.
The named list is the primary guard; the ratchet is a backstop for gross
dissolution.

---

## Phase 2 — precompute

**2.1 — Frequency list derived from `wordfreq`, not SUBTLEX-US.**
§3 specifies SUBTLEX-US. Its canonical hosts returned 404/500 when this was
built. `wordfreq` 3.1.1 is vendored to `data/frequency-en.csv` in the same shape
and SHA-pinned alongside CMUdict, so swapping the source back means re-running
`scripts/vendor-frequency.py` and reviewing the pin diff. Ranking will differ
from the doc's stated source.

**2.2 — Story-word bank and `themes` deferred to Phase 3.**
§16 lists them under Phase 2, but the phase's own Definition of Done — "precompute
runs all stages < 5 min; spot-check words sane" — does not mention them, and they
are consumed only by generation. Authoring theme vocabulary before generation
exists means writing content nothing can validate. Agreed at the phase-gate review.

**2.3 — `allowed_words` rebuilt via staging table + single-transaction swap.**
Readers must never observe a partial allowlist: a truncated list yields duller
stories while every metric still reports success. Rows are computed and staged
first, then swapped in one transaction, so a crash mid-computation leaves the
previous allowlist untouched.

**2.4 — Blocklist matches the exact tokenized word, never a substring.**
Substring matching is the Scunthorpe problem — it silently removes legitimate
vocabulary and the loss is invisible in every metric. The blocklist is one of
three independent layers (§13); it does not need to be the paranoid one.
`data/blocklist.txt` is a seed list, not the vendored comprehensive one §5.4 calls
for. **Expand before any public deploy.**

**2.5 — Precompute loops word-outer, stage-inner.**
`build_lattice` is `lru_cache(maxsize=8192)` against ~19k candidates; stage-outer
would evict every lattice between passes and rebuild each word twelve times.

**2.6 — `syllable_count` lives in the decoder, not precompute.**
It is a pure function of a CMUdict pronunciation, and §6 (stage-scaled length)
and §14 (syllable metrics) both need it. Precompute would have been a second home
for the same logic.

**Measured, for the record:** precompute produces **64,841 rows across 12 stages
in 1.6s** — 190x inside the 5-minute budget. Per-stage counts run 159 (stage 1) to
9,426 (stage 12). The build doc expected 150–2,500; stages 3+ exceed the ceiling
by up to 3.8x. This is the opposite of the feared starvation risk and is harmless:
§6 caps the generation prompt at the top 400 by frequency, and the full table is
the browsable §5.4 artifact. Stage 1 at 159 words is the genuinely tight one.

---

## Phase 3 — generation (in progress)

**3.1 — Structured outputs use `output_config.format`, not tool-use JSON schema.**
§6 and §7 call for "structured outputs enforced via Anthropic tool-use JSON
schema". The API now has native structured outputs, which constrain the response
directly rather than routing through a tool call. Strictly better here: no
tool-call parsing, and no failure mode where the model returns prose when a tool
call was expected. §7's `api_fail` taxonomy still covers a schema-parse failure.

**3.2 — Model IDs are `claude-sonnet-5` and `claude-haiku-4-5`.**
Bare aliases, never date-suffixed. §3 requires model IDs live in env; the values
in `.env.example` are defaults, not hard-codes.

**3.3 — `max_tokens=8000` for a 30-200 word story.**
Looks oversized, and isn't. On Sonnet 5 adaptive thinking is ON when the
`thinking` parameter is omitted, and thinking counts against `max_tokens`. A
tight budget produces a response that is mostly thinking plus a truncated story
(`stop_reason: "max_tokens"`) — which reads as a bad model rather than a bad
parameter.

**3.4 — Sampling parameters are absent by necessity.**
Sonnet 5 rejects non-default `temperature`/`top_p`/`top_k` with a 400. Output
variety, where wanted, has to come from prompting.

**3.5 — Prompt is split for per-stage cache reuse.**
Render order is tools -> system -> messages, so the stable material (frozen rules,
then the stage's vocabulary) goes in `system` with the cache breakpoint after the
vocabulary; per-request material (theme, story words, length) goes in `messages`.
Generating N stories for one stage pays the vocabulary cost once.
`test_system_rules_are_frozen_of_request_detail` guards the split — any
per-request detail leaking into the frozen block would silently destroy the cache.

**3.6 — Cost accounting takes an explicit date.**
`price_for(model, on)` rather than `date.today()`, so an eval report regenerated
later reproduces its original numbers. Sonnet 5's introductory pricing expires
2026-08-31; encoding the cutover keeps reports honest across it instead of
silently under-reporting cost from September.

**3.7 — `TextReport` gains `distinct_story_words`; §7's gate uses it.**
§4 caps story words at "<=3 per story". `story_word_count` counts token
occurrences, so a story using "dragon" three times scored 3 against the cap and
was rejected — even though the child meets exactly one new picture-chip word.
Repetition is correct decodable-reader behaviour. The gate now counts distinct
story words; `story_word_count` remains as an occurrence metric for eval.

**3.8 — The model's `story_words_used` claim is intersected with the offered bank.**
Story words are excluded from the decodability denominator, so an unchecked
self-report lets the generator shrink its own denominator and inflate its score.
Observed live: a draft declared "king", "big", "class" and "red" as story words —
none offered — cutting the scored word count from 11 to 6 and reporting 100%.
§0 is explicit that the LLM is an untrusted generator and a pure function is the
gate, so the claim is now intersected with the words actually offered and
anything invented falls through to normal scoring.

**3.9 — `story_segments.asset_id` is a plain nullable int, not a FK.**
§8 defines it as `fk assets`, but the `assets` table lands in Phase 4. A FK to a
nonexistent table cannot be created; the column is reserved now so Phase 4 adds a
constraint rather than a column. Branching columns (`parent_segment_id`,
`choice_label`) are likewise present though Phase 3 generates a linear spine —
§295's cut line drops branching but explicitly keeps the schema.

**3.10 — Moderation was recalibrated after a 55% false-positive rate.**
The first moderation prompt rejected 5 of 9 warm-pool stories, every one for a
fire-breathing dragon. That makes the dragons theme unusable — the story-word
bank *offers* "fire" and "dragon", and §6's bar is "no scary content **beyond
mild peril**". A dragon in a cave is the premise, not a violation.

The prompt now separates *hazard* from *harm*: a dragon that breathes fire, a
character who is scared and hides, a dark cave — approved. Anyone hurt, cruelty,
weapons used against a character, or peril left unresolved at the end — rejected.

Re-tested in **both** directions rather than just confirming the rejects
disappeared: 6/6 correct across three should-approve and three should-reject
cases, including the subtle "Sam ran and ran but the thing got Sam. The end."
(unresolved peril), which is still rejected. Warm-pool rejection went 55% -> 0%.

**3.11 — Moderation fails CLOSED on an unparseable response.**
§7 counts a schema-parse failure as `api_fail`. For the moderation node
specifically that must not read as approval, so an unparseable verdict returns
`approved=False`. The warm pool absorbs the rejection; a child sees nothing.

**3.12 — Langfuse tracing IS implemented (superseded).**
This entry originally recorded Langfuse as deliberately unbuilt for want of
credentials. Credentials arrived; §15 is now wired — see 4.17 below.

**4.17 — Langfuse §15 tracing: one trace per run, one span per §7 node.**
`storyforge/obs.py`. GENERATING / REWRITING / MODERATING are typed as
generations (they carry model, usage and cost); VALIDATING is a plain span,
because typing the pure validator as a generation would invent a $0 model call
in every cost breakdown. `langfuse_trace_id` is persisted on the story row.

Three properties were proven live rather than assumed:
- **Traces were fetched back** through `client.api.trace.get`, not inferred from
  the SDK not throwing. A rewrite trace shows the full ladder — GENERATING
  $0.0061 → VALIDATING (WARNING, decodability 0.893, violations
  `['liked','come','good']`) → REWRITING $0.0054 → VALIDATING → MODERATING.
  That is §17 demo item 4, visible.
- **The no-op path works**: with the keys blanked, generation runs normally and
  `langfuse_trace_id` is NULL. The repo still works for anyone without Langfuse.
- **Flush-on-exit works**: a subprocess that opens a span and exits without an
  explicit flush still landed its trace. This is what covers short-lived CLIs
  like `make eval-smoke`, and is the usual way this integration appears to work
  while silently dropping everything.

Tracing can never break generation: a hostile client that raises from every
entry point is in the test suite, and the pipeline still returns a passing
result. A genuine `ValueError` from the generation call still propagates —
swallowing tracing failures must not swallow real ones.

**Ingestion lag is real.** A fetch ~25s after a run returned 2 of 4 observations
and an understated total cost. Retry until the observation set is complete before
judging a trace.

**4.18 — ENRICHING (§7) is untraced.**
Question generation and scene tagging run in `warm.py` after `run_pipeline`
returns, so they fall outside the trace context. Wiring them needs
`trace_context=` plumbing into `llm/questions.py` and `llm/scene_tags.py`. §15's
four state-machine nodes are complete; this is the gap.

---

## Phase 4 — enrichment and the story API

**4.1 — `select_asset` filters by `Asset.kind`, defaulting to `"segment"`.**
§12 specifies only "most tag overlap, deterministic tiebreak by asset id". But
style guide §06 defines three distinct ratios — segment art 4:3, theme cover 1:1,
story-word cutout 1:1 transparent — so dropping a square cover into the 4:3
segment frame is a real layout defect, not a cosmetic one. `kind=None` restores
§12's literal behaviour.

**4.2 — Story-word cutouts are not in the asset pool yet.**
§06's third asset kind keys off story *words*, not scene tags — a different
lookup than the tagging node performs. `Asset.kind` accommodates them when the
picture-chip UI lands in Phase 5.

**4.3 — A closed tag vocabulary maps unmatched concepts to a near neighbour.**
Observed live: the segment "Dan sat amid the sand" has no legal tag (neither
`beach` nor `sand` is in the enum), so the model chose the nearest legal tag,
`meadow`, and the segment received a plausible-but-wrong illustration. This is
the closed vocabulary working exactly as §12 specifies — the model *cannot*
invent a tag — but it is the tradeoff: an unmatched concept lands on a
near-neighbour rather than falling through to the striped placeholder. Widening
the vocabulary is a one-line change if it recurs. Worth watching in eval.

**4.4 — Scene tagging degrades rather than raising.**
An unparseable tagging response returns empty tags and `degraded=True`. §12's
illustration layer is decorative; a story that already cleared the decodability
gate and moderation must not be discarded over a missing picture. The UI renders
the §06 striped placeholder, which is a defined state, not an error.

**4.5 — Tag realignment is keyed on the echoed segment index.**
A hallucinated or out-of-range index is dropped rather than applied, and a
skipped segment gets `[]`. Silently shifting tags by one would give every
subsequent screen the wrong picture — a failure that looks like bad art rather
than a bug.

**4.6 — The warm-pool fallback ladder never climbs to a higher stage.**
When a (stage, theme) pair is empty, the served story is chosen by
`stage_id DESC, (theme_id = requested) DESC, last_served_at ASC NULLS FIRST`
under a hard `stage_id <= requested` filter.

The filter is the safety property. A *higher*-stage story can contain GPCs the
reader was never taught, which breaks the decodability guarantee outright — the
child would meet letter-sounds nobody has shown them. A *lower*-stage story is
always safe, because the GPC inventory and sight-word lists are cumulative
(§4, and §5.7c's monotonicity property). So theme is a preference and stage is a
safety constraint, and stage wins every time.

**4.7 — Request body is `{stage_id, theme_id_or_slug}`, not §9's `{kid_id, theme_id}`.**
`kids` lands in Phase 5 with Clerk. The stage is passed directly until a kid row
can supply it.

**4.8 — Serving does NOT flip `status` to `complete`.**
Only `last_served_at` is stamped. §8 defines `complete` as "a kid read it", which
belongs to `POST /api/readings`. Flipping on serve would drain the warm pool on
admin previews and make `make warm` churn regenerating stories nobody read.

**4.9 — Question answer keys are excluded from the StoryDTO.**
Grading happens server-side at `POST /api/readings/{id}/answers` (§9), which is
also what writes `answers` and `gpc_stats`. Shipping the key to the client would
save no round trip and would make the mastery signal forgeable — §8's promotion
rule depends on those counts being honest. Asserted by a test.

**4.10 — Rate limiting is in-process (§9: 10/min).**
Correct for a single worker; behind N workers the effective limit is 10N/min.
Keyed on client IP until Clerk lands (Phase 6), and it counts requests rather
than successes, so a 404 still consumes a token. A shared counter (Redis or
Postgres) is the Phase 7 fix.

**4.11 — Errors vs the never-an-error invariant.**
§7's "the kid never sees an error" covers *pool exhaustion*, which the fallback
ladder absorbs. It does not cover requests naming things that do not exist:
unknown theme → 404, stage outside 1–12 → 422 (matching `validate.py`'s
precedent), and a completely empty pool → 503 telling the operator to run
`make warm`. None of those are reachable from kid mode, and fabricating a story
would be worse than an honest error.

**4.12 — Grammar: prevented in the prompt, detected in moderation. RESOLVED.**
Live warm output contained "He did not came back." and "The dragon had a got
time." — fully decodable, wrong English, servable to a child. The allowlist
pushes the model toward words that fit phonically but not syntactically.

Two layers, because prevention alone leaks and detection alone wastes tokens:
- `SYSTEM_RULES` gains a rule requiring correct English, naming both observed
  failures and telling the model to change the STORY rather than force a word
  that fits the sounds. Kept short and free of per-request detail — it is the
  frozen cache prefix (invariant 10), and a test pins that.
- `ModerationVerdict` gains `well_formed: bool` + `grammar_note`. Judged
  separately from safety in the prompt, then combined in code:
  `approved = verdict.approved and verdict.well_formed`. Unparseable ⇒ both
  false, so it fails closed on each half independently.

**Measured, 3 sweeps over every warm story, counting only stories flagged in all
three: pre-fix 20–27% ungrammatical, post-fix 0%.** The decodability gate was not
touched, asserted by a test. A live rejection: `REJECT #31 — ungrammatical: "The
state was still" — should be "The sky was still"`.

**The first prompt failed DEVIATIONS 3.10's both-directions rule** with a 25%
false-positive rate — it wanted "wings helps" (itself ungrammatical) and flagged
"It did nap." Fixes: judge each sentence on its own; require a nameable
replacement; and explicitly carve out emphatic *do*, unbackshifted speech,
modals, and cross-sentence tense shift. Each carve-out is pinned by a test.
Final matrix over 17 cases, stable across 3 runs: 7 true rejects, 10 true
approvals, **0 false positives, 0 false negatives**.

**Deliberate policy call:** the grammar spec is SENTENCE-LEVEL. Cross-sentence
tense inconsistency is not a rejection reason, because below the stage that
teaches `-ed` the past-tense form is not in the child's allowlist at all — the
writer is forced into the present. Real cost: stories like "Sam sat in a rocket.
Sam and Bob go up fast." stay in the pool.

**4.13 — `_split_segments` breaks inside quoted dialogue.**
`He said, "Stop. Give me the cave."` splits after `Stop.`, so one screen ends
mid-quote. The split is lossless (verified: it round-trips), so no text is lost —
it is a reading-experience wrinkle, not data loss. Fix is to avoid splitting when
inside an unclosed quotation mark.

**4.14 — Enrichment (§7 ENRICHING) runs only for stories that passed.**
Scene tagging and question generation fire after the decodability gate AND
moderation, in parallel via `asyncio.gather(..., return_exceptions=True)`. A
story that will never be served must not cost Haiku calls, and neither node can
fail the story: a tagging failure yields the §06 striped placeholder, a question
failure yields a story with no questions. Both are recorded in
`gen_meta.enrichment` rather than swallowed.

**4.15 — Segments are re-queried rather than read off `story.segments`.**
The relationship is lazy; on the async engine an implicit lazy load raises
`MissingGreenlet` instead of quietly doing IO. Caught live on the first
enrichment run.

**4.16 — `tap_word` prompts use a decodable cue word, not a literal sound.**
A literal `/sh/` cannot survive the decoder (61 of 78 inventory graphemes are OOV
or a violation when tokenised), and the word "word" is undecodable at every
stage. The prompt therefore uses a decodable cue word chosen from a frame ladder,
richest frame first; below stage 7 that reduces to "Tap: hit." The gate held; the
sentence got shorter.

**Corrected (see 5.13):** `target_grapheme` originally rode in the served
`payload`. That leaked the answer — `check_tap_word` guarantees exactly one
option carries the grapheme, so the key was derivable. It now lives in `answer`
and is stripped from the DTO. Kid mode cannot render the §07 sound chip from the
served fields; that is the correct trade.

---

## Phase 5 — readings, mastery, kid mode

**5.1 — `kids.parent_id` is a plain nullable int, not `fk users`.**
`users` lands with Clerk in Phase 6. Same precedent as 3.9: reserve the column
now so Phase 6 adds a constraint rather than a column.

**5.2 — `readings.stage_id` added beyond §8.**
The kid's stage when the reading STARTED. §8 counts "stories completed at the
current stage", but §7's fallback ladder deliberately serves lower-stage stories,
so `stories.stage_id` undercounts — and after a promotion nothing else
distinguishes the stage just left. The promotion rule is not computable without
this column.

**5.3 — `gpc_stats.gpc_id` references `gpcs.gpc_id` (the decoder's `"sh|SH"`),
not the surrogate `gpcs.id`.** `validate_word().alignment` emits exactly those
strings. Same "one identifier, not two" argument as seeding `gpcs` from
`load_gpcs()`.

**5.4 — Only `tap_word` feeds `gpc_stats`.**
An `mcq2` is a comprehension check. Crediting every grapheme in a two-sentence
option would swamp the signal and make 80% reachable without decoding anything.

**5.5 — `MIN_GPC_OBSERVATIONS = 10` added to §8's two thresholds.**
§8 gives no floor, so promotion would otherwise be decidable from a single lucky
tap at 100%.

**5.6 — Promotion accuracy is over GPCs introduced AT the current stage, not
cumulative.** A cumulative denominator is dominated by stage-1 correspondences
and would promote a child who never successfully met the new graphemes. See 5.11
for the cost this exposed.

**5.7 — First answer wins**, enforced by a unique constraint plus
`ON CONFLICT DO NOTHING`. A retry still grades (the child sees "correct") but
reports `recorded: false` — §11 says the child retries until correct and the
attempt is what is logged, so the first attempt is the honest signal.

**5.8 — `status='complete'` flips at `/complete`, not at `POST /api/readings`.**
This supersedes the note in 4.8: "a kid read it" is only knowable at completion.

**5.9 — `display_name` rejects `@`.** The one PII shape a parent might paste into
a name box (§13). No email, no DOB, no other fields.

**5.10 — Live bug found and fixed: `{"response": true}` graded as CORRECT.**
Pydantic coerces `bool → int` before the union resolves, so `true` became index
1 — which happened to be the right answer. `mastery.grade_answer` refuses bools
but never saw one. Fixed with a `mode="before"` validator (now 422) plus a
regression test. Found by hostile-input testing, not by the type system.

**5.11 — OPEN: most `tap_word` questions target no current-stage grapheme.**
Measured live: `fast@4`, `ran@4`, `held@5` yield zero stage-N GPC instances.
Combined with 5.6, a stage-4 or stage-5 reader can complete unlimited stories and
never accumulate evidence toward promotion — **the mastery loop cannot close**.
The rule reports this by name rather than hiding it
("2 of 10 answered GPC instances needed on stage-4 sounds"). Fix belongs in
`questions.sound_targets`, which already sorts most-recently-taught-first but
does not require a current-stage target. Assigned.

## Phase 5 — kid mode UI (`apps/web`)

Numbered from 5.20 to stay clear of the API-side Phase 5 entries above. Section
references are to the **style guide** unless marked "build doc".

**5.20 — Effective reading leading is ~2.1, not §03's 1.65.**
§07 requires a word token's tap target to extend 12px beyond the glyph box and
§11 requires 48px targets with an 8px gap. At 34px those are geometrically
incompatible with 1.65 leading: 12 + 34 + 12 = 58px of target inside a 56px
line box, so adjacent lines would overlap and a child aiming at one word would
hit the line above. Word buttons are therefore `inline-block` (their margin box
sizes the line box): 62px target, 72px advance, 10px clear — 8.4px at the worst
case, a story-word chip. §11 is the non-negotiable section; §03's number gives.

**5.21 — §03's 0.16em word-spacing is not applied.**
The tokens' 5px horizontal padding already puts ~10px (0.29em) between two
words. Adding word-spacing on top would push the line under §03's own 26–34
character measure.

**5.22 — Choice text is not tappable per word (§07); each choice card carries a
48px speak button instead.** §07 asks for branch labels to be "tappable per word
too", but the card itself is the choose control, and a `<button>` inside a
`<button>` is invalid HTML that breaks keyboard traversal and screen readers.
Worse, a word tap that also committed the answer would make hearing a choice
destructive. The child can still hear any choice read aloud.

**5.23 — The §07 tap_word "sound chip" is not rendered.**
`QuestionDTO` redacts `payload.target_grapheme` (API-side 5.x): the correct
option is the one that starts with it, so shipping the grapheme ships the answer
key. The prompt's decodable cue word ("Tap: for.") carries the sound instead.
The chip code remains and lights up if the field ever becomes safe to send.

**5.24 — The reading view is 1024px wide, not the 720px kid column (§10).**
§10 gives both "kid: single column, max 720px" and, for this screen
specifically, "landscape: art left 50%, text right 50%" at a 1024×768 reference.
The more specific rule wins on this screen; the picker and celebration keep the
720px column.

**5.25 — An overlong segment scrolls inside the reading area, not the page.**
§10 says overflow means generation length is wrong, not the layout — and the
warm pool does contain 200-character segments. When one arrives, the middle
scrolls while the chrome and the 64px Next button stay pinned. A scrolling page
would push the only way forward off the bottom of the screen.

**5.26 — Two new story words in one segment announce sequentially.**
§07 auto-speaks a story-word chip on first appearance and §11 allows only one
utterance at a time. With two first appearances on one screen the second used to
cancel the first, so one word was introduced silently. They now queue: still
never two voices, still once per word.

**5.27 — `lib/grade.ts` grades locally when the server does not answer.**
§9 keeps the answer key off the wire on purpose. This restates the two
properties `llm/questions.py` already proved (tap_word: the answer starts with
the target grapheme; mcq2: the true option's words appear in the story's own
order) and is used ONLY when `POST /api/readings/{id}/answers` is unavailable.
When neither the server nor the local check can decide, the answer is ACCEPTED —
telling a child they are wrong on no evidence is the worst available outcome.

**5.28 — `/read/:kidId` resolves the reader from `GET /api/kids`.**
§9 has no `GET /api/kids/{id}`. An id that matches nothing falls back to the
first kid rather than 404ing (kid mode has no error state), and with no kids at
all the session runs anonymously at `?stage=` or stage 4 and records nothing.
Kid mode never creates a kid — that is a parent action with a name attached
(build doc §13).

**5.29 — The celebration shows no phoneme column.**
§07's row is "sh /ʃ/ +2 · 8 of 10", but `gpc_accuracy.phonemes` is ARPABET
("AE"), which means nothing to a six-year-old. The grapheme, the delta and the
running total are shown; the phoneme belongs to the §08 parent table.

**5.30 — The §06 placeholder's caption is not visible in kid mode.**
`alt_text` is written for a parent or a screen reader and has never been through
the decoder. Printing it beside the story would put undecodable English in front
of a child. It remains the placeholder's accessible name.

**5.31 — `FallbackDTO` is not surfaced to the child.**
The DTO exists so kid mode "can say we picked a different one", but §10 fixes
the reading chrome at three controls and §01 says nothing may fail visibly. It
is logged to the dev console for the operator instead.

**5.32 — `react-router-dom` added; a DEV-only branch fixture exists.**
Routing is §11's own route list, and one dependency beats a hand-rolled history
shim. `lib/fixtures.ts` renders a branched story at `/read/1?fixture=branch` so
the §10 branch screen could be driven and verified while every warm-pool story
is still linear; it is behind `import.meta.env.DEV` and is dropped from
production builds.

**5.12 — Question text now passes §13's layers, but not naively.**
Audit finding: `moderate()` ran only on the story body, and `enrich()` runs after
`run_pipeline` returns — so every question prompt and option, free text from a
second model rendered full-screen to a child, passed through ZERO of §13's three
layers. Live rows already contained "The man ran back stop." and a correct answer
of "He did not came back."

**The naive fix was worse than the bug.** Concatenating the question block and
calling `moderate()` rejected 16 of 16 live question sets — it is a *story* judge
and answered "not a coherent story". Composing story+questions swung the other
way, approving "The man ran back stop." while rejecting a story for story-content
reasons. What shipped instead:
- **Layer 1** (blocklist, pure and free) reads EVERY string, including tap options.
- **Layer 3** (Haiku) reads only model-authored prose — `mcq2`. `tap_word` is
  exempt because its prompt is a fixed frame plus a cue word chosen by a pure
  function, and `check_tap_word` already rejects any option that is not a word of
  the already-moderated story. `LAYER3_EXEMPT_QTYPES` is a denylist, so a new
  question type is judged by default rather than silently exempt.

Measured false-positive rate 1/9. §6 is explicit that these costs are not
symmetric. A layer-3 rejection keeps `tap_word` deliberately: its text was never
in the judged body, and it is the only type carrying §8 mastery evidence.

**5.13 — `target_grapheme` moved from `payload` to `answer`.**
See the correction in 4.16. `_public_payload()` strips it plus `correct`/`index`/
`answer`, so legacy rows cannot leak either, and `--backfill` repairs existing
rows. The old test was vacuous — it inserted its own payload and asserted a key
it never added. Replaced with one that sweeps every real node-produced row
through the DTO. Live: 39 questions served, 0 leaks.

**5.14 — Enrichment failure is now loud.**
`asyncio.gather(..., return_exceptions=True)` could not distinguish a genuine
`AttributeError` from an API blip; `run_warm` printed success and exited 0 with
9 of 20 servable stories carrying no questions and no art. Now `run_warm`
surfaces the error keys and exits non-zero when any story lands with zero
questions or zero tagged segments. Pool repaired by `--backfill`: **28 servable
stories, 0 unenriched.**

**5.15 — Enrichment is 45–48% of a story's true cost.**
`QUESTION_GEN`, `SCENE_TAGGING` and the question moderation call now sit in
`obs.generation` spans under an `ENRICHING` span that re-enters the story trace
by id (`run_pipeline`'s context has closed by then). Verified by fetching trace
`44d10447…` back: 8 observations with tokens and cost on each. Every cost figure
this project reported before this fix was roughly half of actual spend.

**5.16 — `sound_targets` picks in three tiers.**
`current_stage_onset` → `current_stage_word` → `older_stage_fallback`, reported
in `gen_meta` so §14 can measure the fallback rate. Tier 2 is enforced in
`check_tap_word`, not merely preferred, and evidence comes from
`mastery.word_gpc_ids` — imported, so the node targets exactly what the grader
credits. Stage-5 stories went from `fast/held/fine` to `give/made/came/home`,
all earning stage evidence.

**5.17 — OPEN: stage 4 often cannot close the mastery loop, and it is NOT the
question node.** Stage 4 teaches only `ed/es/ing/s|Z`, all word-final, so
evidence requires the STORY to contain a suffixed word. Some stories contain
none, and the fallback fires honestly. The allowlist offers 38 such words inside
the prompt's top-400 window, so the fix belongs in §6's `SYSTEM_RULES` — ask the
story to exercise the stage's newly-taught GPCs. Assigned to no one yet.

---

## Phase 6 — auth and the parent surface

**6.1 — ARPABET is never rendered to a parent.**
The API returns raw ARPABET (`AE`, `NG`). The dashboard shows a plain-language
example word primary and IPA secondary: `sh · as in "ship" · /ʃ/`. Example words
come from the `example` column of the decoder's own `gpcs.csv` — not invented —
keyed on `grapheme|phonemes` rather than `gpc_id`, whose `Y UW`→`Y.UW` and
`''`→`SIL` encoding is a serialization detail. That keying matters: the API
returns `n` twice, and the table correctly renders `n /n/ "net"` and
`n /ŋ/ "finger"` as separate rows. IPA is kept because an example word cannot
disambiguate `th` in "thin" (/θ/) from `th` in "this" (/ð/) — both are rows.

**6.2 — A promotion-rule panel sits between the stat cards and the GPC table.**
§08 gives "Next up" one sentence, but §8's whole claim is an interpretable model
a parent can recompute on paper, so the thresholds must be visible. The four
stat cards stay exactly as specced. The criterion text is the SERVER's own
string — rewriting it client-side would create two descriptions of one rule that
could drift.

**6.3 — "Few samples" means fewer than 5 answers, and the legend says so.**
§08 says "few samples" without a number. Deliberately NOT
`thresholds.min_observations` (10) — that is §8's aggregate floor across a whole
stage; applied per row it would grey out nearly everything.

**6.4 — Zero-evidence GPC rows are folded behind a counted toggle, never removed.**
Hiding them would turn "not asked about yet" into "no trouble here", which is
the opposite of what a parent needs to see.

**6.5 — Tailwind CONFIG changes do not hot-reload a running Vite dev server.**
Observed live: a new `borderRadius` token was present in `dist/` but computed
`0px` in dev, silently squaring every avatar. This is the INVERSE of invariant 7
(there, dev lies optimistically and prod purges; here dev lies pessimistically).
Restart the dev server after editing `tailwind.config.ts`, and never conclude
from dev alone.

**6.6 — Concurrent agents must isolate the headless browser.**
`browse` shares one daemon across processes; parallel UI work needs
`BROWSE_SERVER_PORT` set per agent or sessions collide.

**6.7 — CRITICAL, found by audit and fixed: the promotion signal was forgeable.**
`_STORIES_AT_STAGE_SQL` counted READINGS, not distinct stories, and GPC evidence
was re-credited on every replay — so `MIN_GPC_OBSERVATIONS = 10`, added
specifically so promotion could not turn on one lucky tap, was satisfied by ten
replays of that tap. Reproduced live: a child advanced stage 5 → 6 having read
ONE story, answered ONE question, the same word, ten times, in 31 HTTP calls.

It was also the DEFAULT behaviour, not merely an attack: `/api/stories/request`
is round-robin, so where a (stage, theme) pair holds fewer than 5 stories a child
pressing "Read another" gets the same story back.

Fixed with `count(DISTINCT story_id)` plus skipping the `gpc_stats` upsert when
the kid already completed that story at that stage. `repeat_reading` was added to
the DTO so an empty `gpc_updates` is not ambiguous.
**Unpapered consequence:** with `DISTINCT`, stages 1 and 3 currently hold only 4
and 3 distinct servable stories, so promotion is unreachable there until the pool
grows. The counter fix and the pool are one change.

**6.8 — Six tests encoded the defect as the contract.**
`test_five_clean_readings_promote_the_kid` was named and documented "5 stories at
stage 1" while its fixture returned ONE story read five times; three siblings did
the same. Fixing the SQL made them fail. The `justify-test-edit` gate was run and
returned TEST IS WRONG — §8's wording plus a live reproduction of what the old
behaviour permits. Corrected via a `distinct_stories` fixture, with the
justification recorded at each site, plus two NEW tests that fail against the old
behaviour (verified by sabotage).

**6.9 — Wrong-owner returns 404, never 403.**
A 403 confirms the resource exists. The body is byte-identical to a nonexistent
id, across every route taking a kid or reading id.

**6.10 — `require_parent` admits admins, but ownership is enforced separately.**
An admin sees an empty kid list and gets 404 on another parent's kid. Role and
ownership are independent checks; conflating them is how admin tooling
accidentally becomes a bypass.

**6.11 — `DEMO_BYPASS_ROLE` defaults to `parent` but is configurable.**
§18 says the bypass renders "a fixed parent session", but §17's demo opens on two
admin-only screens — so a literal parent-only bypass cannot survive the outage it
exists for. The §18 startup guard refuses to boot when the bypass is set
alongside any production signal (`ENVIRONMENT=production`, DO App Platform vars,
a remote `DATABASE_URL`, or an `sk_live_` key), naming the signal it saw.

**6.12 — `kids.display_name` is constrained on the COLUMN, not just the route.**
DEVIATIONS 5.9's `@` check was a route validator; the live DB already contained
`kid@example.com`, written by some other path. Now a CHECK constraint enforces a
first name. Accepts `Maya`, `O'Brien`, `Ana-Sofia`, `Sofía`, `Zoë`, `李`; rejects
addresses, emails, multi-name strings, script tags, digits and newlines. The
migration repaired the existing row.

**6.13 — RESOLVED: Clerk's role claim required a dashboard change, not a code change.**
`auth.py` accepts `public_metadata.role` -> `user_public_metadata.role` ->
`metadata.role` -> `role`, first match wins, unknown -> `parent`. Which key Clerk
emits was unverifiable without a real signed session, so it was left open.

Resolved by minting a real session token through the Clerk Backend API and
decoding it. The finding: Clerk's DEFAULT session token carries NONE of the four
spellings — only `exp, fva, iat, iss, nbf, sid, sts, sub, v` — even with
`public_metadata = {"role":"admin"}` set on the user, and the instance had zero
JWT templates. So every real session would have resolved to `parent` and both
§17 demo screens would have 403'd. Setting the dashboard metadata field looks
like it should be sufficient and is not.

Fixed in the Clerk dashboard (Sessions -> customize session token) with
`{"public_metadata": "{{user.public_metadata}}"}`, which is why `auth.py` needed
no change: `public_metadata.role` was already the first spelling it walks.
There is no Backend API for this — `/v1/instance` exposes no such field, so it
cannot be scripted and must be redone by hand on any new Clerk instance.
**DEPLOY NOTE: a fresh Clerk instance will silently downgrade every user to
`parent` until this field is set.** `docs/DEPLOY.md` must say so.

Verified end-to-end against a fresh API with `DEMO_BYPASS_AUTH=false`: no token
-> 401; real RS256 token -> 200 on admin-only `/api/validate`. `/api/kids`
returned `[]` for the admin while five kids exist, confirming 6.10 — role and
ownership stay independent, and admin is not a data bypass. Test session revoked.

**6.14 — Pre-existing, not yet fixed:** `tests/test_stories_api.py::_pool` filters
`status='warm'` while `_SERVE_SQL` serves `warm|complete`, so
`test_least_recently_served_rotation` breaks whenever a stage-1 story flips to
`complete`. Currently green. Fix is `_pool` using `SERVABLE_STATUSES`.


**6.15 — The blocklist was 52 words and 42 profanities were in `PERMITTED WORDS`.**
Found incidentally by the stage-coverage agent (`shit` in the stage-3 shortlist).
A sweep found `fuck`, `cunt`, `rape`, `porn`, `slut` and 37 others in the
vocabulary handed to the model to write children's stories. §5.4 asked for a
vendored list; Phase 2 shipped a seed list with a comment saying "expand before
any public deploy", and that never happened. Now 240 entries.

**The first fix was worse than the original.** `load_blocklist()` reads one word
PER LINE; the replacement was written space-separated, so each category line
became a single unmatchable 70-character entry — 22 effective words, fewer than
the 52 it replaced. The only visible symptom was `allowed_words` growing by 274
rows when blocking should shrink it. Caught by querying the table; `make
precompute` exited 0 throughout. Verified after the real fix: 0 blocklisted words
in `allowed_words`, no stage below §18's 150-word floor (stage 1 = 157).


**6.16 — The SPA refuses to render without a USABLE `VITE_CLERK_PUBLISHABLE_KEY`.**
`apps/web/.env.local` has carried the key since the phase started and *nothing
read it*: there was no `@clerk/clerk-react`, no `ClerkProvider`, no `/sign-in`
route and no `Authorization` header. The app looked signed-in because the API
was running §18's `DEMO_BYPASS_AUTH`, so every screen worked and no layer said
otherwise. That is the failure mode this entry exists to make impossible: a
missing key now renders a full-screen message naming the variable, the file
(`apps/web/.env.local`), the Clerk dashboard page it comes from and the fact that
Vite only reads env at startup — and `<App/>` is never mounted. An
unauthenticated app that renders is worse than an app that will not start,
because only the second one is discovered before a demo.

**Two failures, and the second was found in the browser, not reasoned about.** A
*missing* key is the `if` in main.tsx. A *malformed* key sails past it and makes
`<ClerkProvider>` throw during render, which React answers with an empty page
and a stack trace nobody is looking at — the same blank div reached by a
different road. This was observed live: a concurrent build had baked
`pk_test_BUILD_PROBE` into `dist/`, and `vite preview` served a completely white
page. `ClerkBootBoundary` now catches that throw and renders the same actionable
screen, naming the key the build was given (a publishable key is public by
construction — it ships in the bundle) and Clerk's own message. The boundary
re-throws anything that does not mention Clerk or the key, because a boundary
above every route that swallowed *all* render errors would relabel an ordinary
component bug as an auth-configuration problem and send the reader to the wrong
file.

**6.17 — The token reaches `lib/api.ts` through a registered getter, set during
render, not through a hook or a context.**
`lib/api.ts` is plain async functions called from effects, event handlers and
`lib/plan.ts` — none of which can call `useAuth()`. So `ClerkTokenBridge`
(`lib/auth.tsx`) hands `getToken` to the module via `setAuthTokenGetter`.
It does so in the component **body**, not in `useEffect`: React runs a child's
effects before its ancestors', so a route component's first `fetch` fires before
an ancestor bridge's effect would have registered anything — the first request of
every page load would go out unauthenticated and 401. Assignment during render is
idempotent and survives StrictMode's double render.

**A null token is sent as no header, never as an error.** §18's bypass means the
API may legitimately not need one, and `GET /api/themes`/`/api/stages` fall back
to seed data on any failure. The client never decides that a request is
pointless; the server decides.

**6.18 — Kid mode has no sign-in wall: signed out, `/read/:kidId` redirects to `/`.**
§01 is absolute that kid mode has no error state, and an auth surface is an error
surface with a form on it — a five-year-old handed a tablet cannot type an email
and password, and a child who is shown one has been told the app is broken.
The parent routes get the opposite treatment (`/sign-in`, per §16). `/` is the
parent's entrance, which is who has to act. While Clerk is still loading the kid
route shows `PrinterWorking` — §07's only "wait" — rather than a blank div.

**6.19 — There is no web-side `DEMO_BYPASS_AUTH`, so §18's bypass is now API-only.**
The guards are unconditional: signed out, the parent routes redirect no matter
what the API would have accepted. Stated plainly, because it is a real reduction
in what §18 promised — with `DEMO_BYPASS_AUTH=true` the API still serves, but the
SPA will not reach it without a Clerk session, and if Clerk's script cannot load
at all the parent routes sit on "Checking your session…" indefinitely. The
alternative is a second, client-side flag that renders the whole app
unauthenticated whenever it is set, which is precisely 6.16's failure with an env
var in front of it. If the demo needs the bypass end to end, the honest fix is a
signed-in Clerk test account, not a client-side switch.

**6.20 — A 403 on the parent surface names the missing `public_metadata` claim.**
Verified empirically against the live instance (`still-vervet-33`): a REAL Clerk
session token minted through the Backend API decodes to exactly
`exp, fva, iat, iss, nbf, sid, sts, sub, v` — no `public_metadata`, no
`user_public_metadata`, no `metadata`, no `role` — even though the user's
`public_metadata` IS `{"role":"admin"}` server-side, and the instance has zero
JWT templates. So `auth.py::role_from_claims` correctly resolves every real
session to `parent`, and every `require_admin` route (`/api/validate`, the
allowed-words browser) will 403 for everyone until a human customises the session
token in the Clerk dashboard (Sessions → customize session token →
`{"public_metadata": "{{user.public_metadata}}"}`). **This is not settable
through the API — `/v1/instance` exposes no such field.**

The fix is NOT to fetch the role from the backend or to trust a client-side
value: `auth.py`'s docstring says roles come from the signed token and never from
the database, so that the dashboard can revoke admin on the next request. What
changes is legibility. `ParentError` now separates 403 from 401 — 401 is "sign in
again" with a button that does it, 403 says the session token is missing the
`public_metadata` claim and names the dashboard action. A generic "forbidden"
would send whoever meets it looking through `auth.py` for a bug that is not
there.

**6.21 — Clerk's JS loads on every route, kid mode included.**
DEVIATIONS 0.4 self-hosts fonts specifically so a child's browser makes no
third-party request (§13). `<ClerkProvider>` fetches `clerk.browser.js` from
`still-vervet-33.clerk.accounts.dev` at startup, so kid mode now does make one —
to the identity provider, on every load. It is not avoidable while kid mode calls
an authenticated API: `POST /api/stories/request` needs a bearer token, so the
SDK has to be loaded before a story can be fetched. Mounting the provider only on
parent routes would mean kid mode holds no session and cannot request a story at
all. Recorded because it is a genuine narrowing of the §13 posture, not because
there is a better option available this phase — the honest mitigation is that
Clerk is the app's own identity provider, not an analytics vendor.

**6.22 — `ParentShell`'s "Demo session" placeholder becomes a real account control.**
The slot was deliberately an honest placeholder while there was no session to
render. There is one now: signed in it is `<UserButton/>` (sized to §11's 48px
floor by an appearance override, since Clerk's default trigger is 28px), signed
out it is a "Sign in" link. Clerk's components are themed through `appearance`
`variables` — berry primary, paper background, Nunito, the 16px `btn` radius —
rather than restyled element by element, so an SDK update changes the widget's
internals and not this app's palette.

**6.23 — `/sign-in` carries the deep link back in `?redirect_url=`, validated.**
A parent who opened `/dashboard/3` and got bounced should land back on
`/dashboard/3`, not on `/app`. The parameter is only honoured when it starts with
a single `/` — `//evil.example` and absolute URLs fall back to `/app` — because
an unvalidated post-login redirect is an open redirect with a session attached.

**6.24 — `persist()` never wrote the grammar half of the story-body verdict.**
DEVIATIONS 4.12 added `well_formed` + `grammar_note` to `ModerationVerdict` and
folded them into `approved`, so an ungrammatical story was *rejected* correctly
from that moment on. But the receipt `warm.persist()` writes to
`gen_meta.moderation` was still the three keys it had in Phase 3 — `approved`,
`layer`, `reason` — so the grammar verdict was computed, acted on, and then
thrown away. `gen_meta->'moderation'->>'well_formed'` was NULL on all 28 servable
stories, and would have stayed NULL on every story generated after the fix too.
The question-side receipt (`_record_verdict`) already recorded it; the body-side
one did not, which is why the two disagreed 28/28 vs 0/28.

Now `persist()` writes the same five keys `_record_verdict` writes. The
consequence is that the backfill below is a **one-time repair**, not a sweep that
has to be re-run after every `make warm`.

**6.25 — `--grammar-backfill` re-moderates servable story BODIES, reconstructed
from `story_segments`.**
`stories` persists the segments, not `PipelineResult.final_text`, so the body
that was judged in Phase 3 no longer exists as a single column. It is rebuilt as
`" ".join(segment bodies)` — the exact reconstruction `enrich()` already uses on
its own `--backfill` path, so the two repair paths cannot judge different text.
`_split_segments` splits on `". "`/`"! "`/`"? "` and re-appends a terminal stop,
so the join is the original modulo whitespace; a test asserts the round trip on
every servable story rather than assuming it.

The sweep runs the SAME `moderate()` the generation path runs — not a
grammar-only variant — because §13 layer 3 is one call and re-judging safety on
text that is years-old-in-model-terms is free information, not scope creep. The
two halves are reported separately so "how much bad grammar is in the pool" stays
a clean number.

Idempotent by predicate, not by flag: the selection is
`gen_meta->'moderation'->'well_formed' IS NULL`, which is exactly the set that
has never been judged. A second run selects nothing and spends nothing.

**6.26 — An ungrammatical body moves to `status='rejected'`. Nothing is deleted.**
`SERVABLE_STATUSES = ("warm", "complete")` and `rejected` is the existing,
already-enforced way a story leaves rotation (`api/stories.py` `_SERVE_SQL`, the
by-id fetch, and `POST /api/readings`) — so taking a story out of the pool needs
no new mechanism and no new column.

Deliberately NOT a delete. `questions.story_id` is `ON DELETE CASCADE` and
`answers.question_id` is too, so deleting one ungrammatical story would take
every question row with it and every `answers` row hanging off those — including
the `tap_word` answers that are §8's only mastery evidence
(`mastery.MASTERY_QTYPES`). A prior agent lost an answer row exactly that way.
Flipping `status` removes the story from every serving path while leaving
`readings`, `answers` and `gpc_stats` untouched, so a promotion decision already
made on that evidence stays reproducible. The receipt records why, so the row is
eval evidence rather than a mystery — the same posture Phase 3 takes toward
stories rejected at generation time.

One consequence, stated rather than discovered later: the servable pool shrinks,
and DEVIATIONS 6.7 already notes that stages 1 and 3 hold few enough distinct
stories that promotion is unreachable there. Rejecting a body makes that worse
before `make warm` makes it better. §0 is explicit that the gate is not weakened
to make the pool look healthy.

**6.26a — The sweep records `pulled_at`, and one Phase 3 test moves with it.**
Found by `test_rejected_stories_are_kept_but_never_warm` (commit e0f3267) going
red. It asserted that no `rejected` story carries a `last_served_at`, which was
an exact statement of a child-safety property for as long as `persist()` was the
only producer of `rejected`: such a story was never in the pool, so a serve stamp
on one could only mean `_SERVE_SQL` had leaked a rejected row.

6.26 creates the first rejected rows that were legitimately served — while they
were still warm — so the assertion could no longer tell a leak from this feature
working. `justify-test-edit` returned **CODE IS WRONG**, and it was right: the
sweep was pulling stories out of rotation without recording *when*, which is
precisely the fact that keeps the question answerable. The receipt now carries
`pulled_at`, the original assertion is kept verbatim over the generation-time
population it was written about, and the stronger claim — *a pulled story was
never served AFTER it was pulled* — is asserted beside it. Both were
sabotage-tested (backdate the pull, drop the field; each fails the right half).
Nothing was loosened; the serving path itself is separately guarded by
`test_stories_api.py::test_rejected_stories_are_never_served` and
`test_readings.py::test_a_rejected_story_cannot_be_read`, both untouched.

**6.27 — The `tap_word` prompt is spoken on mount; the options already were.**
`Comprehension.tsx` wired `onSpeak` on every choice card and nothing on the
prompt, so the one string that says what to DO was the only string a child could
not hear. That matters most exactly where the text is weakest: at stages 2–3 the
prompt is `Tap it. Hint: win.`, because every similarity word in English —
`as`, `like`, `same`, `alike` — is stage 4 or later (`llm/questions.py`), so the
written sentence provably cannot say more.

**Honest limit: speaking it does not restore the missing instruction.** The
spoken string is `question.prompt` verbatim, so at stage 2 a child now HEARS
"Tap it. Hint: win." rather than reading it in silence. That is strictly better —
audio is not bound by the decoder, and a pre-reader gets the cue at all — but the
comparison clause is still absent. Synthesising a richer sentence for the audio
channel was rejected: it would be child-facing text that never passed §13's
layers and never passed the decoder, and the moment the sound chip becomes
servable (DEVIATIONS 5.23) there would be two competing instructions.

Implementation notes, because each is load-bearing:
- The effect is keyed on `question.id` and its cleanup **cancels**; that is the
  whole of the StrictMode handling and there is no ref guard. React 18
  double-invokes effects in dev, so the engine sees speak → cancel → speak and
  exactly ONE utterance survives. The tempting alternative is `StoryWordChip`'s
  idiom — a ref set and never cleared — and it is wrong here: the teardown
  cancels the first utterance while the guard refuses the second, so dev goes
  silent. That is a documented, tolerable quirk for a word the child can also
  tap; it is not tolerable for the one string that explains the screen. Both
  were run: the shipped version nets one live utterance, the guard version nets
  zero.
- The cleanup calls the canceller `speak()` returned, not bare `cancelSpeech()`.
  That canceller no-ops if anything else has since taken the microphone, so
  advancing to the next question cannot cut off an option the child just tapped.
- `speak()` never throws and degrades to a timer when `speechSynthesis` is absent
  (`lib/speech.ts`), so §01 holds: no engine means no sound and a fully usable
  screen, never an error surface. Nothing is awaited, so the first tap is
  available immediately.

**Verified in a headed browser against live API data, not asserted from a
fixture.** The real component and the real `lib/speech.ts` were mounted with a
real `StoryDTO` from `POST /api/stories/request`, with `speechSynthesis`
instrumented: one `speak()` without StrictMode, zero on re-render, one surviving
utterance with it, the text equal to `question.prompt` verbatim at rate 0.85, the
next question's prompt spoken on advance, and — with real macOS voices — a
genuine `start` event 2ms in followed by ~1.8s of `speechSynthesis.speaking`.
Removing the effect flips six of the twelve checks red; adopting the guard idiom
flips exactly the one that measures live utterances. What this does NOT cover:
the route itself. `/read/:kidId` is behind a real Clerk session (6.18/6.19) and
no account was created to get one, so `KidSession`'s transition into the
questions phase was not driven end to end in this pass.

---

## Phase 7 — deployment artifacts (DigitalOcean App Platform)

Artifacts: `services/api/Dockerfile`, `.dockerignore`, `.do/app.yaml`,
`docs/DEPLOY.md`. **Nothing is deployed** — these are the build outputs; creating
the DO resources is the operator's call.

**7.1 — The SPA ships as a STATIC SITE component, not a container.**
App Platform supports both, and the choice is not arbitrary. §2 states the
frontend is "a static SPA. All state and secrets live behind FastAPI", and a Vite
build is literally a directory of fingerprinted files: there is no SSR, no
session, no server-side runtime and — critically — no secret for a server to
hold. A container would therefore add an nginx or node process to patch, a second
instance to pay for, and a second origin, and would buy nothing. The one thing
given up is custom response headers (a CSP), which DO's static hosting does not
expose; that is a real loss and is the only argument on the other side.

Serving both components under **one app domain** (`/api` → service, `/` → static
site) is what keeps `lib/api.ts`'s relative `fetch('/api/...')` honest in
production: no CORS, no `VITE_API_URL` baked into the bundle, and `main.py`'s
CORS allowlist stays dev-only exactly as its comment claims.

**7.2 — The API build context is the REPO ROOT, and `storyforge` is on
PYTHONPATH rather than installed.**
`storyforge.seed` and `storyforge.precompute` resolve their data as
`Path(__file__).resolve().parents[3] / "data"`, and `data/seed/gpcs.csv` is a
*relative* symlink into the decoder package (1.4). Both facts force the image to
reproduce the checkout's shape — `/app/data` and `/app/services/api` — rather than
flatten it, so the Dockerfile is built with `-f services/api/Dockerfile .`.

For the same reason `uv sync` runs with `--no-install-project` and the package is
reached through `PYTHONPATH=/app/services/api`. A non-editable install would put
`seed.py` at `…/.venv/lib/python3.12/site-packages/storyforge/seed.py`, whose
`parents[3]` is `…/.venv/lib` — so `make seed` would fail looking for a `data/`
directory that does not exist, in an image that otherwise starts and serves
traffic perfectly. Verified in the built image: `SEED_DIR=/app/data/seed`, and the
symlinked `gpcs.csv` resolves to the same 3,775 bytes the decoder loads.

1.4's warning that "symlinks do not survive a build context" turns out to be
half-true and worth correcting: BuildKit copies the symlink *as a symlink*, and
because it is relative and both trees land under `/app`, it resolves. It would
have broken had the two trees been copied to unrelated paths.

**7.3 — `DATABASE_URL` is COMPOSED from `${db.*}`, not `${db.DATABASE_URL}`.**
DO's own binding yields a bare `postgresql://…` URL. SQLAlchemy resolves that
scheme to psycopg2 — not installed, and sync — so `create_async_engine` raises at
import and the container never starts. The spec composes
`postgresql+psycopg://${db.USERNAME}:${db.PASSWORD}@${db.HOSTNAME}:${db.PORT}/${db.DATABASE}?sslmode=require`
instead, which needs no code change and keeps the password out of the file.

`sslmode=require` was **proven, not assumed**, in both directions against the
built image: with it, a Postgres that has SSL off fails with *"server does not
support SSL, but SSL was required"* — libpq honouring the option, not SQLAlchemy
discarding an unknown one; without it the same connection succeeds with
`ssl = off`.

Known sharp edge, not currently reachable: `alembic/env.py` feeds the URL through
`Config.set_main_option`, which is ConfigParser interpolation, so a literal `%`
in a password would raise. DO generates `AVNS_`+alphanumeric passwords, so this
has no live failure — recorded because the symptom (a pre-deploy job dying on
"interpolation syntax") names nothing that would lead anyone here.

**7.4 — `ENVIRONMENT=production` is set in the spec ON PURPOSE, as a §18 tripwire.**
The temptation with a startup guard is to keep the deployed environment quiet
enough not to trip it. The opposite is wanted: the spec deliberately hands
`auth.py::deployment_signals` an unambiguous production signal, so that anyone who
later adds `DEMO_BYPASS_AUTH=true` gets a container that **refuses to boot**
instead of one that serves every request as a fixed unauthenticated parent.

Tested against the built image, three independent signals, each on its own:
`ENVIRONMENT=production`; DO's injected `APP_URL`/`APP_DOMAIN`; and a remote
`DATABASE_URL` host. All three refuse, naming the signal. The control — bypass
off, every production signal present — boots clean. `DEMO_BYPASS_AUTH` is
*absent* from the spec rather than set to `false`, because it already defaults to
false in `config.py` and a variable listed in the dashboard is one toggle away
from being true.

**7.5 — The API image does NOT run migrations at start.**
A `CMD` that ran `alembic upgrade head` before uvicorn would have every instance
in a rolling deploy racing the same DDL, and the failure mode is a half-applied
migration under load rather than a clean error. `alembic upgrade head` runs once,
in a `PRE_DEPLOY` job built from the *same* Dockerfile — same image, so the
migration can never run against a different dependency set than the code it is
migrating for — and a failure there aborts the deploy before the new code sees
traffic. Verified: the full six-migration chain applies to an empty database from
the image, and a second run is a no-op.

**7.6 — One instance, one uvicorn worker, deliberately.**
`stories.limiter` is in-process (4.10), so §9's "10 story requests per minute"
becomes 10 × workers × instances the moment this scales out. The honest
configuration until a shared counter exists is `instance_count: 1` and
`--workers 1`, both annotated at the point where someone would raise them.

**7.7 — `preserve_path_prefix: true` on the `/api` ingress rule.**
App Platform strips the matched prefix by default, so the service would be asked
for `/health` and answer 404 for every route in §9 — with a green build, a green
database and a health check failing for a reason that looks like the app.

**7.8 — DO's health check cannot see what `/api/health` actually says.**
The endpoint returns **HTTP 200 even when the database is down**; the verdict is
in the body (`status: ok|pending|error`), which is correct for a report and wrong
for a status-code-only probe. App Platform's `health_check` only reads the code,
so a database outage would leave the component green. Not papered over: the
Dockerfile's own `HEALTHCHECK` parses the body and fails on `error` (verified —
the container reports `healthy` against a live Postgres), and `docs/DEPLOY.md`
says to read the JSON rather than trust the dashboard. Making the endpoint return
503 was rejected as out of scope for a deployment phase and a change to a §15
contract other things read.

**7.9 — `httpx` is a runtime import declared only in the dev dependency group.**
`auth.py` imports `httpx` to fetch Clerk's JWKS — the module every authenticated
request goes through — but `pyproject.toml` lists it under `[dependency-groups]
dev`. The production image is built `--no-dev` and works anyway, because
`anthropic` and `langfuse` both require it transitively (httpx 0.28.1 is present
and importable in the built image, checked). That is a coincidence, not a
guarantee: dropping or swapping the Anthropic SDK would break authentication in
production while every local test stayed green. **Left unfixed here** — the fix is
one line in `pyproject.toml` plus a `uv.lock` refresh, and `services/api/` is
being edited by other agents this session. Flagged rather than silently absorbed.

**7.10 — App Platform cannot build from this project's canonical remote.**
`origin` is a self-hosted GitLab (`labs.gauntletai.com`); App Platform builds from
GitHub, gitlab**.com**, Bitbucket or a container registry. So a deploy requires
either the GitHub mirror (`HankH18/story-forge`, which the spec references) or
pushing the image to a registry and swapping the `github:` blocks for `image:`.
Cohort submission is unaffected — the mirror exists so DO can build, not to move
where the work is submitted.

**7.11 — What `doctl apps spec validate --schema-only` is and is not worth.**
It ran clean on `.do/app.yaml` with no DO account, and it was sabotage-tested
rather than trusted: a misspelled key (`prefixx`) is caught, but a nonsense
`instance_size_slug` and a nonsense job `kind` both pass. So the spec is proven
**structurally** valid — every field name is real and every block is in the right
place — and the enum *values* (`apps-s-1vcpu-1gb`, `nyc`, `PRE_DEPLOY`) are
unverified until a real `doctl apps create`. Stated because "the spec validates"
would otherwise imply more than it does.

**7.12 — `.dockerignore` is one file at the repo root, and it was verified by
exporting a real context.**
There is exactly one image in this project, so there is exactly one build context
and one ignore file; a second one next to `services/api/Dockerfile` would be a
copy to keep in sync. Reading it proves nothing, so the context BuildKit actually
receives was exported (`FROM scratch` / `COPY . /` → `-o type=local`) and swept:
the whole context is `data/`, `services/`, `.dockerignore`, `.gitkeep` — 5.2 MB,
with no `.env`, no `.venv`, no `node_modules`, no `.git`, no `dist`, no
`__pycache__` and no test tree. The finished image was swept again for
secret-shaped strings and for any file named `.env`: both empty.

**7.14 — The tap-a-word CUE WORD could point at an option by shape, and did.
The rule is now "no whole-word containment, in either direction, with ANY
option".**
Live row 4392 (stage 3, warm): prompt `Tap it. Hint: the.`, options
`["sat", "in", "then"]`, answer `then`. `the` is a prefix of exactly one option,
so the whole question is solvable by matching three letters — no phoneme, no
grapheme, no sound. §8 then credits `th` mastery evidence for that tap, and that
evidence feeds promotion: the same class of defect as 6.7's forgeable promotion
signal, arriving by a different road.

The prefix case is not the general rule and patching it would have missed the
other live instance. Row 696 carried cue `win` against options
`["in", "had", "went"]` (answer `went`) — `in` is a *suffix* of the cue, so the
shape match points at a WRONG option and steers the child away from the answer
they were meant to sound out. A cue that identifies the wrong option is the same
defect wearing the opposite sign.

The rule adopted, stated as a property rather than a case list: **the cue may
share the target grapheme with an option and nothing else. It must stand in no
whole-word containment relation — `cue in option` or `option in cue`, which
subsumes identity, prefix and suffix in both directions — with ANY option.**

Why containment and not "any shared letters": the grapheme itself is the signal
the question is *for*. `check_tap_word` already proves exactly one option starts
with the target grapheme and no distractor uses it anywhere, so a child matching
`sh` is doing the phonics the screen is asking for. What a whole-word match adds
is a way to answer *without* segmenting anything — recognising the chunk `the`
inside `then` needs no knowledge that `th` says /θ/. So the line is drawn at the
word boundary: sharing the grapheme is the question; containing the whole cue is
the answer key.

Enforced in two places on purpose, mirroring the node's existing discipline
(`llm/questions.py`):
- `sound_targets` now picks the cue from a RANKED candidate list per grapheme
  and takes the first one that conflicts with no word in that sound's closed
  offer list (`onset_words + clean_words`). A grapheme with no safe cue is
  dropped from the menu, exactly as a grapheme with no cue at all already was.
- `check_tap_word` re-proves it against the three options the model actually
  chose. The narrowing in `sound_targets` is a menu; a guarantee that only lives
  in the prompt is a suggestion (the same argument the `current_stage_word`
  scope check is already written under).

`_cue_index` therefore returns `grapheme -> [candidates]` (shortest, then most
frequent, capped at `MAX_CUE_CANDIDATES`) instead of one word. Nothing about the
gate, the "exactly one correct option" proof, or the §8 tiering moves.

**7.15 — The eight defective rows in the live pool were REPAIRED IN PLACE, not
deleted, and one of them carries mastery evidence that is now unreliable.**
`warm.repair_leaky_cues` re-derives the cue and its prompt from
`llm/questions` — the same pure functions the node uses, so a repaired row is
indistinguishable from a freshly generated one — and writes `payload.cue_word`
and `prompt`. `options` and `answer` are never touched, so no recorded answer
changes meaning and no `answers` row is orphaned. That is deliberate:
`answers.question_id` is `ON DELETE CASCADE`, an agent has already destroyed a
`MASTERY_QTYPES` evidence row this way, and there is no path back from a deleted
answer. It sweeps EVERY status, not just servable ones, because the defect is a
property of the row's content and the one answered instance sits on a `rejected`
story. Idempotent: a second run selects nothing.

The sweep found **8** of 55 `tap_word` rows leaking (14.5%):
- 7 warm, all stage 3, cue `the` against the option `then`, pointing AT the
  correct answer. **0 answers recorded against any of them** — the pool was
  warmed after the last child session, so no child has met one.
- 1 (`q696`, story 8, `rejected`) with cue `win` against the option `in`,
  pointing at a WRONG option. **6 answers recorded**, 4 marked correct, from
  two kids (2622 Priya, 2665 Noah, both stage 3).

**Said plainly: the six answers on q696 are not trustworthy evidence about
whether those two children can decode `w`.** The question they were shown
contained a shape cue pointing away from the answer, so a wrong answer may
record a defect in the question rather than a gap in the child, and the
`w|W / e|EH / n|N / t|T` counters on both kids (`went`'s alignment) are
downstream of it. Nothing has been rewritten to hide that: the `answers` rows,
their `correct` flags and the `gpc_stats` counters are all exactly as the app
recorded them. `gpc_stats` is a running counter with no per-answer provenance,
so "subtract the tainted evidence" is not available without recomputing every
kid's history from `answers`, which is a larger change than this job and would
itself be a silent rewrite of a child's record. The honest statement is the one
recorded here rather than a quiet correction.

**7.16 — The four `test_stories_api.py` tests now own their fixture rows, and
the assertions were strengthened rather than relaxed.**
All four read the live pool for their *expected values*, so `make warm` broke
them — which means they were never deterministic, only lucky. The pattern that
failed is the same one in each: an assertion about the pool (`space` has no
stories; stage 4+ has never been warmed; the served stage-1 story has story
words) written as though the pool were a fixture.

They now create their own themes and stories and assert about *those ids*. The
fallback tests place their fixtures in an unoccupied stage band computed as
`max(servable stage) + 1`. The database is still read — but only to *place* the
fixture in empty space, never to derive an expected value, which is the exact
distinction that failed before. `_SERVE_SQL` orders `stage_id DESC` first, so a
story alone at the top of the ladder is the deterministic winner for any request
at or above its stage no matter what else is in the pool.

Each edit went through the `justify-test-edit` gate; verdicts are recorded at the
edit sites. §7's ladder is checked harder than before, not less: the
`other_theme` test now also owns the lower-stage same-theme story it must
*decline* (rung 2 before rung 3), and the `lower_stage` test now owns a story
ABOVE the requested stage and proves it is never served — STATE invariant 6,
which nothing previously tested with data it controlled.

**7.17 — `test_stories_api.py::_pool` now uses `SERVABLE_STATUSES` (closes
6.14).**
It filtered `status='warm'` while `_SERVE_SQL` serves `warm|complete`, so any
stage-1 story flipping to `complete` broke `test_least_recently_served_rotation`.
Imported from `api.stories` rather than re-spelled, so the test cannot drift from
the endpoint again.

**7.18 — `test_a_real_node_produced_question_never_ships_its_answer_key` keeps
its live sweep AND gains rows it owns.**
This test caught 7.14, which is the argument for not converting it into a pure
fixture test: its strength is that it reads whatever the node actually wrote into
the real pool. Its weakness was `assert rows` — on a database where `make warm`
has not run it fails for a reason that has nothing to do with the property. It
now builds its own `tap_word` rows by calling `check_tap_word` (the node's real
writer, not a hand-typed payload), so the sweep always has data, and then sweeps
its own rows *and* every live one.

The leak assertion was widened from `correct.startswith(value)` to the full
7.14 containment rule in both directions, and to wrong-option pointing as well
as correct-option pointing. Under the old, prefix-only assertion `q696`'s
`win`/`in` suffix leak was invisible.

**7.19 — The generator fix was proved by sabotage, not by assertion.**
`test_questions.py::test_the_cue_word_never_points_at_an_option_by_shape` and
`test_a_cue_that_contains_an_option_is_rejected` were both run against the
pre-fix generator by reverting the fix in place: the first goes red with
`cue 'the' is a prefix of option 'then'` — the live row 4392 reproduced from a
unit test — and the second goes red because the old `check_tap_word` accepts the
question. Both are green after. A test that has never been seen to fail is a
test about nothing.

**7.20 — The kids orphaned by `ab5a866f52fc` are ADOPTED by a follow-up
migration, not left NULL.**
`ab5a866f52fc:96` runs `UPDATE kids SET parent_id = NULL WHERE parent_id IS NOT
NULL` before adding the FK, and its own note defends that as the confidentiality-
safe direction: `auth.owned_kid` matches `parent_id = :caller`, so NULL is
visible to nobody, whereas guessing an owner hands one family another family's
children. That reasoning is correct and this migration does not undo it. What
nothing accounted for is availability: after it ran, **all five readers in this
database were owned by nobody**, `GET /api/kids` returned `[]` for every real
Clerk account (verified live against an enforced API with a real RS256 token),
the parent home said "No readers yet", and every reading, `gpc_stats` row and
promotion decision from Phase 5 sat behind a row no principal could reach. That
is what let C1 through: with no kid to resolve, kid mode fell back to a constant.

The companion migration adopts orphans, and it will not guess. It resolves an
owner in one of two ways and refuses to run if neither is available:

1. `STORYFORGE_ADOPT_PARENT_CLERK_ID` names the account explicitly; or
2. there is exactly ONE candidate user, where `demo_local_parent` — §18's fixed
   bypass principal, which is not a person — is never a candidate.

Anything else (no candidate, or two real accounts and no override) **raises**.
A migration that cannot tell whose children these are must stop and say so: the
alternative is either a silent re-orphaning, which is the defect it exists to
fix, or a silent misassignment, which is worse than the defect. On a fresh
database `kids` is empty and the whole thing is a no-op, so a first deploy never
sees it.

The downgrade is a no-op on purpose. Ownership cannot be un-inferred, and
re-NULLing every `parent_id` on the way down would destroy real data that a
later upgrade could not reconstruct.

**7.21 — `POST /api/stories/request` takes `kid_id`. The stage is derived from
the child, server-side; `stage_id` survives only as an admin preview knob.**
§9 specifies `POST /api/stories/request {kid_id, theme_id}` and DEVIATIONS 4.7
deferred that to a client-supplied `stage_id` because `kids` had no owner yet.
It was never reinstated, so the request that decides what a five-year-old reads
was anchored on a number the client chose. `stories.py:19-24` called the
`stage_id <= :stage` filter "a child-safety bound, not an optimisation"; a bound
relative to an untrusted number is not a bound. Reproduced end to end with no
tooling: `/read/1648?stage=12` served a **13.0%-decodable** story to a stage-1
reader (story 19, 40 violations in 46 scored words).

The stage now comes from `auth.owned_kid(kid_id).stage_id` — the same ownership
edge every other kid route uses, so an id belonging to another family is the
same 404 a nonexistent one gets, and §0's posture toward the LLM is now the
posture toward the client.

**`stage_id` is refused for a parent and accepted only from an admin, and never
together with `kid_id`.** Two reasons it is kept at all rather than deleted.
First, it is the admin portal's preview path (§11's "generate cold story") and
the eval/QA path, and both need to ask for a stage with no child in the room.
Second — the honest one — it grants an admin nothing they do not already have:
`GET /api/stories/{id}` has always served any servable story to any
authenticated parent regardless of stage, because a story is a pool row and not
a per-family artifact (`stories.py:40-47`). Removing `stage_id` while leaving
that route open would look like a fix and be theatre.

So the residual is stated plainly rather than implied: **the raw
`POST /api/stories/request {"stage_id": 12}` repro still returns a stage-5 story
to an ADMIN token.** It returns 422 to a parent token, it is unreachable from
any child-facing path (7.23 removed `?stage=`), and 7.22 stops it being attached
to a child. Moving the preview to `/api/admin/stories/preview` and closing
`GET /api/stories/{id}` to a `kid_id` scope is the follow-up; both need
`tests/test_stories_api.py`, which another agent held this session.

**7.22 — `POST /api/readings` refuses a story above the reader's stage.**
`stories.py:40-47` argued that ownership binds later, at `POST /api/readings`.
It does bind the *kid* there, through `owned_kid` — but nothing there ever
compared the story to the child: a stage-1 kid plus a stage-5 story filed a
`201`. That row then feeds §8, whose "5 distinct stories completed at current
stage" counts `readings.stage_id`, which is stamped from the kid (6.7's guard
counting the right rows for the wrong stories).

`story.stage_id <= kid.stage_id` is now checked before the insert, 422 with both
numbers named. It cannot fire on a legitimate session: §7's ladder never climbs,
so a story served for this child is at or below their stage by construction.
It is a second gate on a path the first gate already covers, which is the point
— C2 existed because there was exactly one gate and it was made of client input.

**7.23 — A served story is re-validated against the READER's own stage before it
leaves the server.**
Defence in depth, and the specific property C2 lacked: a story that is 13%
decodable *for the child receiving it* should be inexpressible, not merely
unlikely. `validate_text` is pure and costs a median **0.47 ms** on a 76-word
stage-5 story (50 runs, warm lexicon), against §1's 300 ms warm-serve budget, so
there is no reason to trust the pipeline's stored verdict at serve time when the
gate itself can be re-run.

The check re-uses the pipeline's own inputs — `mastered_ids(stage)`, the
cumulative `sight_words` at that stage, and the story's persisted
`story_words` — so it is the §7 gate, not a second opinion that could drift.
A story below `DECODABILITY_GATE` is excluded and the pick is retried (bounded);
if nothing survives, 503, the same answer an empty pool already gets. It does
not fire on the current pool: all **52** servable stories score ≥95% at their own
stage and at every higher stage, which is the §5.7c monotonicity property
measured rather than assumed.

**7.24 — Kid mode has no default stage, no `?stage=`, and an unresolvable reader
routes to the ADULT.**
`DEFAULT_STAGE = 4` is deleted. Its comment said it "only applies when the parent
has no kids at all"; after 7.20's migration that was the state of every account
in the database, and the fallback was silent — the theme picker merely dropped
the "hello {name}" greeting, which nobody reads as "this child's curriculum
position has been discarded". A constant that silently substitutes a stage is the
anti-pattern: not knowing a child's stage must never resolve to "serve stage 4
anyway".

§01 rules out the two obvious replacements. A child-facing error screen is
forbidden, and guessing is what caused this. The third option is the honest one:
**if there is no reader, there is no reading session.** `/read/:kidId` now shows
the printer while the reader resolves, and if none resolves it navigates to
`/app` — the adult's own screen, which knows how to say "no readers yet" and how
to add one. A child never sees a failure; an adult sees the actual problem on
the surface built to explain it.

This also closes the audit's SUSPECTED render/effect race: `stage` used to be
computed during render while `resolveKid` was awaited in an effect, so a child
who tapped a theme before `GET /api/kids` returned got the default. There is now
no stage to compute and no theme to tap until the reader exists.

`?stage=` is removed outright. It was the address-bar half of C2 and it has no
legitimate caller: the stage is the child's, and the child is in the URL.

**7.25 — `/api/health` and every HTTP response disclose `DEMO_BYPASS_AUTH`.**
The audit's finding 0: with the bypass on, **nothing in any HTTP response said
so**. The banner (`auth.py:434-438, 718-724`) is a server log line, and health
reported `database`, `decoder`, `anthropic` and stayed silent. Two agents drew
opposite conclusions about whether auth was enforced, and a browser session was
driven against an unauthenticated API for a whole phase. 6.16 recorded that
exact failure one phase earlier and the fix applied then (make the SPA refuse to
boot without a Clerk key) could not detect it, because the SPA's key was fine.

`GET /api/health` now carries `demo_bypass_auth` and `principal` at the top
level, and **every** response — including 401s and 404s, from any route — carries
`X-Storyforge-Auth: demo-bypass` or `enforced`. One curl of anything answers "is
this API checking anything?", which is the question that was unanswerable.

The §18 startup guard is untouched, and the health `status` deliberately stays
`ok` under the bypass. `status` is the aggregate of the §15 component checks
(DB, CMUdict, Anthropic) that `docs/DEPLOY.md` and the Dockerfile HEALTHCHECK
read; overloading it with a configuration verdict would change what "error"
means for two existing readers in order to say something a dedicated field says
better.

**7.26 — The Vite dev proxy target is an environment override.**
`vite.config.ts` hard-coded `http://localhost:8000`, so verifying a fix against
an API with the bypass OFF meant editing the config, and the alternative — using
whatever happens to be on `:8000` — is precisely how the audit's finding 0
happened. `VITE_API_PROXY_TARGET` (and `VITE_DEV_PORT` / `VITE_PREVIEW_PORT`, so
two dev servers can coexist) default to the previous values, so `make dev` is
unchanged.

## Phase 7 — the JOB C audit remainder (`docs/AUDIT-PHASE6.md` C4–C9)

The six smaller findings the C1/C2/C3 pass left behind. Logged before the change,
per §0. Every claim below is marked PROVED (reproduced with the command shown) or
ASSUMED.

**7.30 — `apps/web` gets a real test runner. `make test-web` runs typecheck AND
tests, and `bun test` is the runner.**
`make test-web` was `bun run typecheck` — `tsc --noEmit` and nothing else — so
the "395 tests green" line in `docs/STATE.md` described 395 *Python* tests and
zero lines of web behaviour. The Phase 5 audit said so and it stayed true for two
more phases. Every defect in this file that lived in `apps/web` (the `DEFAULT_STAGE`
fallback, the `?stage=` escalation, `safeRedirectPath`, the substituted reader)
was in code a type checker cannot have an opinion about.

`bun test` rather than vitest + jsdom: the runner is already in the toolchain
(`bun` builds and serves this app), it needs no new dependency beyond the
`@types/bun` already in `devDependencies`, and it runs the TypeScript directly
with no transform config to drift from Vite's. What that buys is the *pure*
layer — `lib/authPaths.ts`, `lib/text.ts`, `lib/grade.ts`, `lib/plan.ts`,
`lib/phonics.ts` — which is where the reasoning lives. **Component tests are
deliberately NOT attempted here**: they need jsdom, a Clerk test harness and a
router, and a shallow-rendered `<KidSession>` would have proved nothing about the
two defects it actually had. The honest statement of coverage is "the pure
helpers are tested; the components are verified in a real browser", and both
halves of that are now true rather than one.

Tests are co-located (`src/lib/*.test.ts`) so `tsconfig.json`'s existing
`include: ["src"]` type-checks them: a test that does not compile is a test that
does not run, and `bun test` alone would not have told us. They are not in the
bundle — nothing imports them from the entry graph.

**7.31 — `safeRedirectPath` is an ORIGIN COMPARISON now, not a prefix check.**
PROVED bypass, on the live sign-in route:
`/sign-in?redirect_url=%2F%5Cevil.example%2Fx` → the function returned the value
unchanged, `new URL(that, location.origin)` resolves to `http://evil.example/x`,
and the only thing that stopped the redirect was Clerk's own
`allowedRedirectOrigins`. Five shapes got through — `/\`, `/\/`, TAB, CR/LF,
`/\@` — while the three the old comment named (`//`, `https://`, `javascript:`)
were correctly neutralised.

The class, not the cases: the WHATWG URL parser **strips** leading/trailing C0
characters and spaces and **removes** TAB/CR/LF from anywhere in a URL before
parsing, and treats `\` as `/` in a special-scheme path. So a prefix check on the
raw string is asking a question about a string the parser will never see. The fix
resolves the value against a fixed sentinel origin and requires the result to
still be on it, then returns the *resolved* `pathname + search + hash` rather
than the raw input — so a caller cannot be handed a string that means something
different once a browser parses it. A control-character/backslash rejection sits
in front of that as a second line, because a value containing them is not the
value it appears to be even when the origin survives.

The sentinel is a constant (`https://storyforge.invalid`, RFC 2606) rather than
`location.origin`, so the function keeps the property its own docstring is built
on: no imports, no DOM, no React — which is also what makes it testable.

`redirect_url` is still only passed to `forceRedirectUrl` on Clerk's widgets, so
**And the parameter is renamed from `redirect_url` to `next`, which is the half
of this finding the audit did not reach.** Fixing the function turned out not to
be enough, and the reason was measured rather than reasoned. With the fix in
place, driving
`http://localhost:5193/sign-in?redirect_url=%2F%5Cevil.example%2Fx` — signed out,
no sign-in attempted, `<SignIn forceRedirectUrl="/app">` being the only thing this
app passed — still logged:

```
Clerk: Redirect URL http://evil.example/x is not on one of the
allowedRedirectOrigins, falling back to the default redirect URL.
```

**Clerk reads `redirect_url` off the query string itself, by name.** So while that
was the parameter's name, `safeRedirectPath` was not the control on it at any
value — Clerk's `allowedRedirectOrigins` was the only layer, and DEVIATIONS 6.23
credited this file for work it was structurally incapable of doing. Under `next`,
a name Clerk does not consume, the same URL produces **no warning at all** (the
value never reaches Clerk) and a legitimate `?next=%2Fdashboard%2F1648` still
lands on `/dashboard/1648` — both driven in a real browser against an API with
`DEMO_BYPASS_AUTH=false` and `garbage token -> 401` confirmed first. Clerk keeps
its allowlist; it is simply no longer the only thing standing there.

There was no live open redirect before this and there is not one now: the value
is only ever handed to `forceRedirectUrl`. What changes is that the claim in the
comment is now true.

**7.32 — The 401 screen no longer contradicts the header above it, and its
remedy is `signOut()` rather than a link back to itself.**
PROVED against an API that verifies signatures but rejects the issuer (so a
genuinely signed-in SPA 401s on every call — the real session-expiry shape):
the page rendered "Please sign in again" *underneath* `ParentHome`'s "SIGNED IN
AS holcombhank@gmail.com" and a live `<UserButton/>`, and clicking its "Sign in"
button returned to `/app` with the same error and this console line: *"The
`<SignIn/>` component cannot render when a user is already signed in … Clerk is
redirecting to the `afterSignIn` URL instead."*

Both halves are the same root cause: `SignedIn`/`SignedOut` read Clerk's *client*
state, which is still valid, while the error is the *API* refusing the token. So
the screen was describing two different systems as if they were one.

- The heading is now "Your session was not accepted" and the copy says which
  side refused, because "sign in again" to someone Clerk considers signed in is
  an instruction that cannot be followed.
- The remedy is `<SignOutButton redirectUrl="/sign-in">` when Clerk thinks the
  session is live, and a plain sign-in link when it does not. Signing out first
  is the whole point: it clears the stale client session that makes `<SignIn>`
  refuse to render.
- `ParentHome` suppresses its "Signed in as …" line when the API has rejected the
  session. The line exists to make a session legible (it was added because the
  demo bypass made "no session" look identical to "session"); printing it next to
  "your session was not accepted" makes it a lie instead.

The token lifecycle itself audited CLEAN — 16 probes over 150 s, 4 distinct
tokens, zero 401s — so this is the genuine-expiry edge case, not something a
parent meets in ordinary use. It is a dead end when they do meet it.

**7.33 — `resolveKid` no longer substitutes a DIFFERENT CHILD, and an
unrecognised reader id routes the ADULT to `/app`.**
PROVED: `/read/162` signed in as an account that does not own kid 162 rendered
the full kid shell greeting *"hello Audit"* — kid 4096, this account's own first
reader — and the reading was filed against 4096 (`readings.id 5396 | kid_id 4096`).
7.21 half-closed this: the request now carries `kid_id` and the stage is the
substituted child's, so the text is decodable and nothing undecodable reaches a
screen. What remained is the worse half for a parent: **silent wrong
attribution.** Child B's session lands on child A's row, at child A's stage, and
§8's mastery evidence — the thing the dashboard exists to report — is now about
the wrong kid, with no signal anywhere.

`?? kids[0]` was documented and defended for a *typo*: "kid mode has no error
state, and a wrong id in the address bar is an adult's mistake that a child would
pay for". That reasoning is right about the child and wrong about the family. A
parent with two readers who hands over a tablet on a stale URL is the ordinary
case, not the adversarial one, and the product's answer was to record the wrong
child's reading rather than to ask.

So `resolveKid` returns the exact match or `null`, and `KidSession` already knows
what to do with `null` — 7.24's route to `/app`, the adult's own screen. A child
still never sees an error: they see the printer for one frame and then the grown-up
screen. `/read` with no id at all used to `<Navigate to="/read/1">`, a hard-coded
guess at a kid id, and now goes to `/app` for the same reason.

Cost, stated rather than buried: the dev fixture URL is now
`/read/<a real kid id>?fixture=branch` instead of `/read/1?fixture=branch`, and a
genuine typo costs one extra tap on the adult screen instead of silently reading
as somebody else. That is the trade this project should always take.

**7.34 — The JWKS cache serves STALE keys for a bounded grace window when Clerk
is unreachable. It does not fail open.**
PROVED against a controllable JWKS stub with `CLERK_JWKS_TTL_S=5`: a 500 after
the TTL lapsed and a refused connection both produced
`503 {"detail":"JWKS unavailable: …"}` while the cache was still holding keys
that were *still valid and would have verified the token correctly*. A Clerk
outage lasting longer than the 600 s TTL therefore takes the whole product down —
the parent surface renders "Something went wrong" (503 is neither 401 nor 403 nor
0) and kid mode parks on the printer screen.

`key_for` now catches `JwksUnavailable` from its own fetch and, if the token's
`kid` is in the keys it already holds and the document is no older than
`ttl + CLERK_JWKS_STALE_GRACE_S` (default 3600 s), logs at WARNING and serves it.
Three properties keep this closed rather than open, and each is the reason the
obvious dangerous version was not written:

1. **An unknown `kid` is still refused.** A stale document cannot introduce a key
   it never contained, so the "verify anything" failure mode is unreachable: if
   the fetch failed *and* the kid is not in the stale cache, the original
   `JwksUnavailable` propagates and the caller still gets 503.
2. **Nothing about signature or claim checking changes.** The key is a real Clerk
   public key that Clerk published; `exp`, `nbf`, `iss` and `aud` are enforced by
   PyJWT exactly as before. An expired token is still expired.
3. **The window is bounded and configurable.** Past the grace period the cache
   refuses again, so "Clerk is gone forever" does not become "this API trusts a
   key set from last Tuesday". RSA signing keys outlive an hour by design — this
   is what every RFC-conformant cache does with `stale-if-error`.

The alternative — leave it fail-closed and accept the downtime — was considered
and rejected, but the *reason* matters: it is rejected because serving a stale
key is not a weakening of the security property. Had the fallback required
skipping signature verification, or trusting an unverified `kid`, the right answer
would have been to take the outage. Availability never buys a bypass here.

A fetch-failure backoff comes with it (`_FAILURE_BACKOFF_S = 30`): while the
document is stale-but-usable and the last fetch failed recently, requests are
served from the stale cache without re-attempting the fetch. Without it every
request retries a 5-second connect while holding the cache lock, which turns an
identity-provider outage into a queue.

**7.35 — The rotation cooldown stays armed by every fetch. The audit's SUSPECTED
finding on `_last_rotation_fetch` is real, bounded, and deliberately NOT fixed.**
`_fetch` sets `_last_rotation_fetch = now` on every call including ordinary TTL
refreshes, so routine traffic arms the 60 s anti-amplification cooldown, and for
up to 60 s after a routine refetch a token signed by a newly published Clerk key
can be 401'd even though a refetch would fix it. The mechanism is confirmed by
reading; the audit could not trigger it against real Clerk, which publishes the
old and new keys together during a rotation.

Moving the assignment into the rotation branch — the obvious one-line fix — was
worked out and then refused, because of what it costs.
`test_an_unknown_kid_cannot_be_used_to_hammer_the_jwks_endpoint` sends 50
requests naming 50 nonexistent `kid`s and asserts **zero** extra fetches. Armed
only by rotation, the first of those 50 triggers a refetch before the cooldown
engages, and that assertion has to become `<= baseline + 1`. That is loosening a
*proven* anti-amplification property to close a ≤60 s window that self-heals and
that nobody has observed. The test is not wrong, so `justify-test-edit`'s bar —
overwhelming evidence the TEST is wrong, not merely that it is inconvenient — is
not met, and the trade is refused rather than taken quietly.

Worth recording because it was mis-read once during this pass, and the mis-reading
is instructive: the coupling is load-bearing in the *other* direction too.
`_fetch` arms the cooldown at its own **start**, so a cold cache asked for an
unknown `kid` makes ONE fetch rather than two — the TTL fetch it has just begun
already armed the cooldown that the rotation branch then declines to fire.
`test_the_real_jwks_is_fetched_once_and_then_cached` asserts precisely that
(`fetches == 1` after a bootstrap lookup for a nonexistent kid, against real
Clerk) and would go red on any change that separated the two clocks naively.

A `fetched_now` guard is added regardless, so that case holds by statement instead
of by coincidence: the rotation refetch is skipped when this same call already
fetched the document, which is the only honest reason to skip it. With
`rotation_cooldown_s=0` — a tests-only setting — that guard is the difference
between one fetch and two of a byte-identical document.

The fix, if this ever bites, is to make the two clocks separate — a
`_last_rotation_attempt` distinct from the fetch timestamp, armed only on the
rotation path, so both properties hold at once. That is a change to the most
security-sensitive class in the API and it wants the rotation drill run against
real Clerk, not a drive-by inside an audit-remediation pass.

**7.36 — The §18 startup guard's signals are widened: env-value PREFIXES,
container DB hostnames, unix sockets, `PORT`, and six more platform variables.**
PROVED gaps, each `DEMO_BYPASS_AUTH=true` plus the named variable, importing
`storyforge.auth`: `DATABASE_URL=…@db:5432/storyforge` **booted**, and `db` is
the service name in this repo's own committed `docker-compose.yml`. So the single
most obvious way to put the demo on a rented VPS — `docker compose up` on a
public host — produced a fully unauthenticated API on a public address. Also
booting: a unix-socket `DATABASE_URL` (hostname parses to `""`, which was in
`LOCAL_DB_HOSTS`), `ENVIRONMENT=prod-us` (the check was exact-set membership),
`PORT=8080`, `K_SERVICE`, `CONTAINER_APP_NAME`, `AWS_LAMBDA_FUNCTION_NAME`,
`HEROKU_APP_NAME`, `NOMAD_ALLOC_ID`, `OPENSHIFT_BUILD_NAME`, `CF_PAGES`.

Changes:
- `DEPLOYED_ENV_PREFIXES = ("prod", "stag", "live", "preview")` in addition to
  the exact set, so `prod-us`, `production-eu`, `staging2` all trip. `dev`,
  `development`, `local` and `test` still do not.
- `db`/`postgres`/`postgresql`/`database`/`pg` move OUT of `LOCAL_DB_HOSTS` into
  `CONTAINER_DB_HOSTS` and become a **signal**. The reasoning inverted: a bare
  service name only resolves inside a container network, and "we are in a
  container" is the deployed shape, not the laptop one. `make dev` runs the API
  on the host against `localhost:5433` (`docker-compose.yml` publishes `5433:5432`
  precisely so it can), so nothing local uses these names.
- An empty hostname on a NON-empty `DATABASE_URL` is a signal (unix socket —
  `/cloudsql/…` is the shape that matters). An empty `DATABASE_URL` is not,
  because that is "unconfigured", not "deployed".
- `PORT` is a signal. It is the one addition with a real false-positive risk — a
  developer may have it exported — and it is taken anyway, because "the platform
  told this process where to listen" is exactly the discriminator, and the
  §18 docstring's own trade is already "a false positive costs one env var during
  a demo, a false negative costs an unauthenticated production API". The refusal
  names `PORT` and says to unset it.

The audit's stronger recommendation — invert the test, require an explicit
positive laptop assertion (`STORYFORGE_LOCAL_DEV=…`) plus a `127.0.0.1` bind —
is NOT taken here and is recorded as the follow-up. It is the better control:
this list is still ~30 negative signals and a bare `docker run` on a public host
sets none of them. It was not taken because it changes §18's documented contract
(one flag) into two steps during exactly the emergency the flag exists for, and
because `tests/conftest.py` turns the bypass on for the whole suite — that is a
deliberate decision to make with the contract in hand, not a side effect of an
audit fix.

`tests/conftest.py`'s env scrub (which clears leaked `ENVIRONMENT=production`-ish
values before importing `storyforge.auth`, so a CI runner does not fail every
test at import) is extended with the same prefixes. It cannot import the
predicate from `auth.py` — importing that module is what runs the guard — so the
duplication is deliberate and cross-referenced in both files.

**7.37 — The unreachable 403 branch is REMOVED, not kept as decoration.**
PROVED unreachable: `grep -rn "status_code=403" services/api/storyforge` returns
exactly one site, `require_admin`; `grep -rn "require_admin" storyforge` puts it
on exactly one route, `POST /api/validate`; and
`grep -rn "api/validate\|api/admin" apps/web/src` finds one prose comment in
`Landing.tsx` and no call. `require_parent` — which guards both routes that can
render `ParentError` — returns its principal unconditionally and never 403s. So
`status === 403` in `ParentError` could not fire, and `MISSING_ROLE_CLAIM_DETAIL`,
the careful sentence naming the Clerk dashboard action, was displayed to nobody.

DEVIATIONS 6.20 reads as shipped behaviour. It was not; it was a branch waiting
for Phase 8. Wiring an admin surface to reach it means building part of the Phase
8 portal in an audit-remediation pass, which is how phases blur. Deleting it is
the honest option: dead error handling that looks like coverage is worse than
none, because it is counted as a case that has been thought about.

Removed: the 403 branch in `ParentError`, `MISSING_ROLE_CLAIM_DETAIL`, and
`isUnauthorized` (which folded 403 into 401 for the same non-existent case and
had no remaining caller at all). `AddKid`'s `status === 401 || status === 403`
narrows to 401. A 403 arriving from somewhere unforeseen — an edge proxy, a WAF —
now renders the generic "Something went wrong" with the status in the message,
which is the truthful rendering of a status this app cannot explain.

**The sentence is preserved here verbatim so Phase 8 can restore it rather than
re-derive it**, along with the fact that made it necessary: a REAL Clerk session
token from this instance carries exactly `exp, fva, iat, iss, nbf, sid, sts, sub,
v` — no `public_metadata` — even for a user whose `public_metadata` IS
`{"role":"admin"}` server-side, so `role_from_claims` resolves every real session
to `parent` until someone customises the session token in the dashboard
(6.13, and it is a dashboard action with no Backend API):

> The API accepted this session but not this screen. Clerk's default session
> token carries no metadata, so the API reads every signed-in user as a parent —
> including admins. Someone with access to the Clerk dashboard has to add the
> claim (Sessions → customize session token → `{"public_metadata":
> "{{user.public_metadata}}"}`), and then sign in again.

**7.38 — Named and deliberately NOT fixed in this pass.**
- **C10, kid mode's permanent printer screen.** Three attempts then a "Try again"
  that re-enters the same loop, reachable by a child pressing "Read another"
  eleven times in a minute (§9's limiter is 10/min). Still true. The audit's own
  suggestion — fall back to the last successfully served story — is a change to
  what a child reads, which is §0/§1 territory and wants its own pass with
  measured decodability, not a bolt-on inside an auth-remediation job.
- **C11, a revoked Clerk session's token works until `exp`.** Ordinary stateless
  JWT trade-off, ≤60 s with Clerk's browser TTL. Checking `sts != "active"` would
  close it; not touched, because `verify_token` is the most security-sensitive
  function in the repo and a Backend-API-minted 1800 s token (the only way to
  observe the window) is not the shape a browser produces.
- **C12, `GET /api/themes` and `GET /api/stages` still 404.** §9 lists them; the
  SPA mirrors `data/seed/*.csv` instead, and adding a theme to the CSV without
  editing `lib/api.ts` in the same commit soft-bricks the new card. Five lines
  against a table that exists — but it is an API route, and `services/api` was
  being edited concurrently this session.
- **The §18 inversion** (positive laptop assertion + bind-address check) — see
  7.36 for why.
- **`warm.py` aborting the whole run on `anthropic.APITimeoutError`** — untouched,
  `warm.py` is outside this job.

**7.39 — What the new web tests are evidence OF, stated so nobody over-reads
them.**
They cover pure functions: redirect validation, tokenisation, the local answer
grader, the branch plan, and the phoneme gloss. They do **not** exercise a
component, a route guard, Clerk, `fetch`, or anything that touches the network —
`lib/api.ts` is deliberately untested here, because the interesting thing about
it is what the server does with the request, and a mocked `fetch` would assert
the mock.

Two of the five files were proved by sabotage rather than asserted:

- `authPaths.test.ts` run against the PREVIOUS implementation: **19 of 56 red**,
  including all eight of the `— NEW` cases, each naming the off-origin URL a
  browser resolves the value to. The corpus also carries its own anti-vacuity
  test — every member of `OFF_ORIGIN` is asserted to be genuinely off-origin as a
  raw string, so a corpus of harmless inputs could not pass by looking busy.
- `phonics.test.ts`'s cross-check against `data/seed/gpcs.csv` was sabotaged in
  place: changing one example word (`sh|SH` → `shop`) and unmapping one phoneme
  (`AE`) turned it red on exactly the two assertions that exist for those drifts,
  and green again on revert. That test is the only one here that can catch a
  change made in *another* file (the seed inventory), which is why it was worth
  proving rather than trusting.

`text.test.ts`, `grade.test.ts` and `plan.test.ts` were written after the code
they cover and are ratchets, not discoveries. Saying which is which is the whole
reason this entry exists: a suite that does not distinguish the two invites the
next reader to believe it found something.

## Phase 7 — the eval harness (§14)

`make eval` was a stub that printed "not implemented until phase 7" and exited 1
(0.5). These entries are the decisions taken to implement §14's full run, logged
before the change.

**7.40 — `eval_runs` and `eval_run_items` land now, and `story_id` is
NULLABLE.**
§8 specifies both tables and `models.py` says in its own docstring that they
"arrive with Phase 7" — an empty table passing a phase gate is a false green, so
they were deliberately not created until something wrote to them. This is that
something.

`eval_run_items.story_id` departs from §8's bare `story_id fk` by being nullable,
because §14 requires a metrics row for every story the matrix *attempted*, and
the attempts that produce no story row at all are exactly the interesting ones: a
generation that raised `APITimeoutError` has latency, an outcome and a cost, and
no text. Writing those items with a NULL story is how "the harness tried 180
cells and 178 produced a story" stays computable from the database instead of
only from a log. `ON DELETE SET NULL` for the same reason in the other direction:
the metrics outlive the artifact, which is the ordering 7.15 already argued for
between §8 evidence and the row it was collected against.

**7.41 — Eval stories are persisted with a FOURTH status, `eval`, that no
serving path can reach.**
§8's enum is `warm|complete|rejected` and `warm.persist` writes `warm` on a pass
and `rejected` on a fail. Neither is right here. `warm` would put 100+ unenriched
stories into the servable pool — precisely the defect the Phase 4 audit found
(nine servable stories with no questions, silently), and it would also flood the
§7 fallback ladder with rows no `make warm` run chose to make. `rejected` would
be a lie about a story that passed the gate and moderation, and would corrupt
the one number `rejected` currently means ("this failed").

So every story the harness writes — pass or fail — gets `status='eval'`, and the
verdict lives in `eval_run_items.metrics` and `gen_meta`. The safety argument is
structural rather than a promise: both serving paths filter on an ALLOWLIST
(`api/stories.py::SERVABLE_STATUSES = ("warm", "complete")`, used by `_SERVE_SQL`
and by `GET /api/stories/{id}`), so a status they do not name is unreachable by
construction, not by remembering to exclude it.

Asserted through the ENDPOINTS, not through the constant, and proved by sabotage.
`tests/test_eval.py` plants an `eval` story alone at the top of the ladder —
`_SERVE_SQL` orders `stage_id DESC` under `stage_id <= :stage`, so if the status
were servable that row is exactly the one that would be served — then checks
`POST /api/stories/request`, `GET /api/stories/{id}` and `POST /api/readings`.
Adding `"eval"` to `SERVABLE_STATUSES` turns three of those tests red and the
request endpoint hands back the fixture, which is what makes them an experiment
rather than a restatement of the SQL (6.14 is the precedent: a test and the
endpoint quietly disagreeing about which statuses count). The run also re-reads
the servable pool size before and after and prints both, and further tests assert
that no `eval` row ever acquires a `last_served_at`, a question row, or a reading.

`stories.status` carries no CHECK constraint, so this needs no migration. That is
noted rather than relied on: if a later phase adds one, `eval` belongs in it.

**7.42 — Per-node latency and per-node cost are measured inside `pipeline.py`,
additively.**
§14 requires "latency per node, token cost per node" and the pipeline had neither
— `PipelineResult` carried token usage per *attempt* and no clock at all. A
harness that timed `run_pipeline` from outside could only ever report one number
for four nodes, and the interesting question ("is the rewrite loop the cost, or
is generation?") would be unanswerable.

`Attempt` gains `latency_ms` (the GENERATING/REWRITING API call) and
`validate_ms` (VALIDATING, the pure function); `PipelineResult` gains
`vocabulary_ms`, `moderation_ms` and `total_ms`. All default to 0, so every
existing caller and test constructs the model unchanged, and no control flow
moves. The clock is `time.perf_counter`, read only around awaits that already
existed.

**7.43 — `--cold` is accepted, is the only mode, and says so.**
§14's CLI is `… --n 5 [--cold]`, i.e. cache-bypassed. `run_pipeline` has no cache
branch to bypass: §7's CACHE_CHECK lives in `api/stories.py::request_story`,
which reads the warm pool, and the harness never calls it. So the flag is
accepted for spec fidelity, defaults to `True` with no way to turn it off, is
recorded in `eval_runs.config`, and its `--help` text says outright that the
harness is unconditionally cold. There is no `--no-cold`: offering one would
imply a warm mode that does not exist, and a flag that silently means nothing is
worse than a flag that states it is the only mode. What is NOT bypassed is
Anthropic's
*prompt* cache: the stage vocabulary block carries `cache_control` (STATE 10) and
disabling it would change the cost numbers away from what production actually
pays. The matrix therefore iterates stage-major so that reuse is real, and the
report states cache-read and cache-write tokens separately so anybody can see how
much of the input was cached.

**7.44 — The spend cap is enforced on the RUNNING ACTUAL, and the pre-flight
projection is explicitly a token model rather than a promise.**
Two numbers, and conflating them would be the mistake. The projection is
arithmetic over prompt sizes the harness can measure without any API call
(`SYSTEM_RULES` + the stage's vocabulary block + the request block) and an output
estimate from the stage's own `target_len_max`; it cannot know how many thinking
tokens Sonnet will spend or how many rewrites the gate will force, so it is
printed as an order of magnitude and nothing depends on it. The cap that binds is
checked after every story against the summed `Usage.cost_usd(pricing_date)` of
the calls actually made: crossing it stops the run, marks it
`status='budget_stopped'`, writes the report from the items completed, and exits
non-zero. A partial matrix reported as partial is worth having; a partial matrix
reported as a pass is not.

**7.45 — The eval does NOT enrich, so "cost per accepted story" is the cost of a
story that clears the gate, not of a story a child could read.**
§7 puts ENRICHING after the gate and §14's metric list is entirely about the
generate/validate/moderate path, so QUESTION_GEN and SCENE_TAGGING are not run:
they would roughly double the bill for output no metric in §14 reads, and they
would produce eval rows that look servable. The consequence is stated wherever
the number appears rather than left for a reader to discover — STATE.md already
records that enrichment was 45–48% of a warm story's total cost, so the true cost
of a *servable* story is close to double the figure this report prints.

**7.46 — Moderation rejects are split by LAYER and by GRAMMAR, and the
hazard/harm split is reported as verbatim reasons because it is not machine-
derivable.**
STATE invariant 5 is that moderation separates hazard from harm — a
fire-breathing dragon is the premise — and §14 wants reject reasons. What the
verdict actually carries is `layer` (`blocklist` | `model`), `approved`,
`well_formed` and two free-text strings. `moderate()` folds grammar into
`approved` and marks it with `UNGRAMMATICAL_PREFIX`, so blocklist / grammar /
content separate cleanly and deterministically. Hazard-versus-harm does not: it
is a judgement about the *content the model objected to*, and the only record of
it is a sentence the model wrote. So the report prints the count per layer, and
then every content reject's reason verbatim next to its stage and theme, plus a
clearly-labelled keyword HEURISTIC flagging reasons that look like hazard
false-positives. A number derived from keyword matching and presented as a
measurement would be worse than the raw strings.

**7.47 — An API failure is a per-story outcome, not a run-killer.**
`warm.py` still aborts a whole run on `anthropic.APITimeoutError` (a standing
finding in `docs/RESUME.md` JOB C). §7's taxonomy is
`validation_fail | api_fail | moderation_fail | budget_exhausted | timeout` and
every one of them is a *measurement* for the eval, so the harness catches per
story, records the outcome and the exception type, and carries on. A matrix that
dies at cell 140 of 180 because one HTTP call timed out would throw away the
evidence for 139 cells, and the fallback rate §14 asks for aggregates exactly
these events. Each item is committed as it completes for the same reason.

**7.48 — Concurrency defaults to 1 so the latency numbers answer §1.6.**
§1 item 6 is "cold generation p95 < 12s", which is a claim about one request. Any
concurrency inflates per-story wall time and would make the p95 in the report
mean something other than what §1 asks. The flag exists (`--concurrency`) and the
value used is recorded in `eval_runs.config`; the reported run is sequential.

**7.49 — `make eval` exits non-zero when §1 item 2 is not met, and the report is
written first.**
Four assertions are checked as a gate, and each has its own line in the report so
a failure names itself: every story's FINAL decodability >= 95% (a story that
exhausted the rewrite budget ends below the gate and is counted as a failure of
the criterion, which is exactly what it is); p95 rewrite depth <= 2; every
planned cell was attempted; and every planned cell PRODUCED text. The last two
are separate on purpose — a matrix cut short by the spend cap and a matrix where
eight cells died on an `api_fail` are different failures, and a run reported as
covering 180 cells while eight of them are empty is the false green 0.5 forbids.

A moderation reject is NOT a §1 item 2 failure — it is a content verdict on
a story that met the decodability criterion — so it is reported loudly and does
not set the exit code, and that division is stated in the report rather than
implied. The report and the database rows are written before the non-zero exit,
because §0's rule cuts both ways: the gate is never weakened to make the numbers
look good, and a bad number is worthless if the run throws away the evidence for
it on the way out.

**7.50 — `PipelineResult.total_cost_usd` now includes the MODERATING node.**
It summed `attempts` only, so the Haiku moderation call — a real API call on
every story that reaches the gate — was missing from `gen_meta.cost_usd` for
every warm story ever written and from the `eval-smoke` total. Exactly the shape
of 5.15's discarded enrichment cost, one node over. `generation_cost_usd` and
`moderation_cost_usd` are added alongside it because §14 requires cost PER NODE
and a single total cannot answer "is the rewrite loop the expense or is
moderation". `make warm`'s printed figures go up slightly as a result; they were
wrong before, not right before.

**7.51 — `--resume RUN_ID` exists because the first full run was killed at cell
154 of 180.**
Logged after the fact rather than before, which is a departure from §0's rule and
is recorded as one: the feature was written in response to a live event.

The event: the 180-cell matrix ran for 68 minutes and was sent SIGTERM by the
process that launched it at story 154. Every `eval_run_items` row is committed as
its story completes (7.47), so all 154 measurements survived in the database —
but nothing could read them back, so the run was worth nothing. The same argument
7.47 makes about an `api_fail` ("a matrix that dies at cell 140 of 180 throws away
the evidence for 139 cells") applies with more force to a signal, and the fix
belongs in the same place.

`--resume` reads the `eval_runs` row and its items, counts what is already
recorded **per (stage, theme)** — derived from the data, not from an assumption
about iteration order — runs only the remainder, and finalises the same row.

The stored `config` wins over the command line. That is the one decision here
worth stating: the two halves of a single matrix must not be measurable against
different models, a different gate, a different `n` or a different pricing date,
so `--resume 2` ignores `--stages`, `--n`, `--effort` and `--pricing-date` and
takes them from the row. A resume trail (`config.resumed`) records each
continuation with its timestamp, the item count at the time, and the git commit,
so a report says plainly that its matrix was gathered in more than one sitting.

---

## Phase 8 — the generation timeout and the warm-run fallback

Two defects with one root: §7 fixes the generation node's timeout at 30s, and
30s is below the latency of a stage 9–12 generation. `make eval` reported 11 of
180 cells as `api_fail`, every one of them `anthropic.APITimeoutError` at
stages 9–12; `warm.py` treats the same exception as fatal to the whole run.

Logged before the change, per §0. Every claim below is marked PROVED (with the
command that reproduces it) or ASSUMED.

**On the numbering.** These were to be 8.1–8.9 and they run 8.0–8.7. `8.0` was
written to hold the reading everything else depends on, and by the time that
looked like a mistake worth correcting the concurrent prompt and vocabulary work
had already cross-referenced this block as "DEVIATIONS 8.0-8.5" (see 8.24) and
numbered itself 8.10+ to stay clear of it. Renumbering now would break live
references in three other sections to save one digit. Left as found, and said out
loud rather than quietly.

**8.0 — What the eval's latency numbers actually are, and why they could not be
used to size the timeout.**
This is the reading the rest of the section rests on, so it is stated first.

`reports/eval-*.json` records one `latency_ms.generating` per story. That number
is not the latency of a generation: it is the latency of the SDK *call*, and
`client.py` builds the client with `max_retries=2`, so one call is up to **three**
HTTP attempts each separately bounded by the 30s timeout. A story recorded at
86.6s is therefore not one slow generation — it is 30s + 30s + 26.6s, two attempts
killed by the timeout and a third that returned. No single attempt in that file
can ever have been observed taking longer than 30s, which makes the file useless
for answering "how long does a stage-12 generation take".

PROVED, and the proof is arithmetic against the SDK's own constants rather than an
assertion: `anthropic._constants` gives `INITIAL_RETRY_DELAY = 0.5` and
`_calculate_retry_timeout` returns `min(0.5 * 2**n, 8) * (1 - 0.25*random())`, so
three attempts at 30s plus the two backoffs it prescribes predict a total of
**91.1–91.5s**. The eleven `api_fail` cells measured
**91.29, 91.35, 91.41, 91.41, 91.41, 91.46, 91.49, 91.49, 91.54, 91.56** and one
outlier at 100.39s. Ten of eleven land inside a window predicted from constants
that were never fitted to them. The same model explains the two gaps in the
distribution of successes — nothing at all between 29.9s and 35.4s, and nothing
between 58.6s and 67.8s, which are exactly the one-attempt and two-attempt
ceilings.

Counted the same way, **63 of ~232 HTTP attempts on the GENERATING node (27%)
were killed at 30s and thrown away**, and 19 of those were at stage 9 alone. That
is the real size of the defect: not "11 cells failed" but "a quarter of the
generation work the run paid for was discarded", since a client-side abort stops
the SDK returning a usage record but does not stop the model having produced the
tokens. Whether Anthropic bills an aborted request is **ASSUMED unknown** and is
not claimed either way here; what is certain is that the eval's `$2.7958` counts
only the attempts that returned, so it is a floor rather than an exact figure.

**8.1 — The timeout stays a SINGLE constant and is raised from 30s to 120s,
derived from `max_tokens`. A per-stage ramp was designed, pre-registered here,
and then abandoned because the measurement refuted it.**

The pre-registered plan is left in place because §0's rule is worth more than a
tidy document: *the node's job is "write N words", `stages.target_len_max` runs
60 → 120 → 200 across the ladder, censored p50 latency tracks it (7–12s, 11–14s,
25–33s), so a single constant must be either too tight for stage 12 or far too
loose for stage 1 — replace it with a floor, a per-word slope and a ceiling.*

**8.2's measurement says that is wrong**, and the numbers are not close:

| relationship | 20 uncensored probes | 123 eval single-attempt stories |
|---|---|---|
| latency vs **output tokens** | r = **+0.997** | r = **+0.981** |
| latency vs stage | r = +0.497 | r = +0.553 |
| latency vs `target_len_max` | r = +0.462 | — |
| output tokens vs words actually written | r = +0.498 | — |

Generation latency is output tokens divided by a throughput that barely moves
(82–106 tok/s on the probes, 68–112 tok/s across 74 eval calls). Stage explains
almost none of it, because output tokens are dominated by *adaptive thinking*
rather than by story length — that last correlation of +0.498 is the whole story.
STATE invariant 9 already records that thinking cannot be switched off on Sonnet 5
and counts against `max_tokens`, so this was predictable and was not predicted.

The probes make it concrete. **A stage-1 story of 30 words spent 2,117 output
tokens and took 24.60s** — against a 30s timeout, on a stage the eval's 15 samples
never showed above 18.3s. And a stage-12 story of 98 words took 6.25s, while
another of 116 words spent 5,019 tokens and took 48.51s. §7's 30s is not "too
tight above stage 8"; it is too tight everywhere, and stages 9–12 are merely where
enough probability mass sits above the line for a 180-cell matrix to notice.
A per-stage ramp would have hidden that behind a plausible-looking formula.

So: one constant, and the number comes from the only hard bound the node has.
`max_tokens` was 8000 at every stage (STATE invariant 9), and a request that reaches
it returns `stop_reason == "max_tokens"`, which `pipeline._call` already turns into
a `ValueError` — §7's "a schema-parse failure counts as api_fail". At the observed
throughput floor of 68 tok/s, 8000 tokens is **118s**; at the p05 of 82 tok/s it is
97s. **`GENERATION_TIMEOUT_S = 120.0`** is therefore the value at which the timeout
can no longer kill a *healthy* generation: anything still running at 120s has hit
a limit the pipeline handles by another route, so the timeout is doing only the job
a timeout should do — catching a stalled connection.

> **Superseded by 8.7: the constant is 240s, not 120s.** The sizing *rule* is
> unchanged and is the reason the number moved — `max_tokens` went to 16000 under
> 8.25 while this entry was being written, and 16000 / 68 is 235s. Read 8.7 with
> this paragraph; the derivation is now `client.timeout_for_tokens()` with a test
> asserting the two stay consistent, so the next `max_tokens` change cannot leave
> this stale the way it left `pipeline._call`'s literal stale.

The price is the worst case. Three SDK attempts bound a genuinely dead cell at
~361s (at 120s) or ~723s (at 240s), against ~91s before. Accepted, for the reason
in the next paragraph, and partly bought back by the connect split below.

**Not on the child's path, and that is why a generous ceiling is affordable.**
PROVED by reading `api/stories.py`: there is no call to `run_pipeline` anywhere in
it, and no background refill task — every serving path is a warm-pool read. The
only callers of the generation node are `make warm` and `make eval`, both batch
jobs. So the cost of a long timeout is wall-clock in an offline run, never a kid
waiting; the cost of a short one is discarded generations, which is what happened.

**The connect timeout is split out at 5s, which is a bug fix riding along.**
PROVED against the SDK: `client.with_options(timeout=30.0)` reaches httpx as
`{'connect': 30.0, 'read': 30.0, 'write': 30.0, 'pool': 30.0}`, so the *old* code
was already waiting 30s to open a socket — six times the SDK's own default of
`connect=5.0` — and raising the number would have made that 120s, then 240s. The
generation node now passes `httpx.Timeout(240.0, connect=5.0)`: an unreachable host
still fails in 5s and is retried inside the budget, while the model gets 120s to
think. This is the reason a 361s worst case is tolerable — it needs a server that
accepts the connection and then stalls, not merely a dead one.

**One place is deliberately left alone and is stated rather than quietly skipped.**
`llm/generate.py::generate_story` also reads `GENERATION_TIMEOUT_S` and passes it
as a bare float, so it picks up the new 120s but keeps `connect=120s` too. It is
owned by another agent this phase, and it has **no caller anywhere in the
codebase** — `pipeline._call` is the generation node that actually runs, and
`generate_story` is an unused duplicate of it (checked: `grep -rn generate_story`
matches only its own definition). The one-line change it wants is
`.with_options(timeout=generation_timeout())`, and it is a request in the handover
rather than an edit here. Two copies of the generation request drifting apart is
the underlying smell; consolidating them is a bigger change than this phase.

**Not on the child's path, and that is why a generous ceiling is affordable.**
PROVED by reading `api/stories.py`: there is no call to `run_pipeline` anywhere in
it, and no background refill task — every serving path is a warm-pool read. The
only callers of the generation node are `make warm` and `make eval`, both batch
jobs. So the cost of a long timeout is wall-clock in an offline run, never a kid
waiting; the cost of a short one is discarded generations, which is what happened.

**8.2 — The constant is sized off latency measured with `max_retries=0`, by a
committed script rather than by judgement.**
8.0 rules out the eval's own numbers, so the input to the constant has to be
collected on purpose. `scripts/measure_gen_latency.py` issues the GENERATING
node's exact request — same `SYSTEM_RULES`, same vocabulary block, same
`max_tokens`, same effort — against a client with `max_retries=0` and a 600s
timeout, so each printed row is one uncensored HTTP attempt. It is committed next
to `scripts/eval_doc.py` for the same reason that one is: a number in this file
should be re-derivable by running something, not by trusting the prose.

    services/api/.venv/bin/python scripts/measure_gen_latency.py \
        --stages 1,5,9,12 --n 5

20 calls, **$0.4023**, run at commit `538a08b`. Per-stage min / p50 / max seconds:
stage 1 → 6.89 / 11.05 / **24.60**; stage 5 → 5.28 / 18.23 / 21.35;
stage 9 → 5.20 / 26.80 / 33.67; stage 12 → 6.25 / 26.85 / **48.51**.
Five samples a stage is a small n and is not presented as a distribution — its job
was to break the censoring, and 4 of 20 calls exceeding §7's 30s (including one at
stage 1) is enough to settle the question the censored data could not be asked.

The script deliberately does NOT set `thinking`, `temperature` or `top_p`: it has to
reproduce the node's latency, and STATE invariant 9 records that omitting
`thinking` is what turns adaptive thinking on. A probe with thinking disabled would
have measured a request the pipeline never makes and would have measured it fast.

**8.3 — The SDK's `max_retries=2` stays as the generation node's whole retry
budget. No pipeline-level retry is added on top of it.**
§7's diagram says `api_fail(retry<=2, exp backoff)`, and `client.py`'s comment
claims "§7's retry policy sits ABOVE this, counting whole pipeline-node
attempts". No such loop exists — the SDK's is the only retry there has ever been,
and it does implement retry ≤ 2 with exponential backoff. Adding the second layer
now would make the worst case `3 x 3 x ceiling`, i.e. tens of minutes for one
dead cell, and would do it for the failure mode least worth retrying: a timeout is
retried in the hope that variance saves you, and after 8.1 the timeout is no
longer the thing that variance breaks. The comment in `client.py` is corrected to
describe what is there.

**8.4 — `warm.py` catches a per-story generation failure, records it, and
continues. It exits non-zero at the end instead of dying in the middle.**
The long-standing finding in `docs/STATE.md` and `docs/RESUME.md` JOB C. §7's
taxonomy is `validation_fail | api_fail | moderation_fail | budget_exhausted |
timeout` and states that *each maps to a fallback* — `warm.py` implemented that
for four of the five and let the fifth propagate out of `run_warm`, so one slow
call discarded every remaining (stage, theme) pair in the run. `eval.py` already
took the same decision for the matrix (7.47); this is that decision, one caller
over.

What the fallback *is* differs between the two callers and is worth stating.
For the eval, an `api_fail` is a measurement, so it becomes a row. For `make warm`
there is nothing to serve and nothing to measure: the pool simply keeps the depth
it had, the shortfall is real, and the operator's remedy is to re-run — which
already works, because `pool_depth` makes the target idempotent. So the failure is
counted, printed the moment it happens, and named in the exit message with its
exception type and its (stage, theme). The run still ends non-zero, which it did
before; the difference is that it ends non-zero *having attempted everything*,
with a summary instead of a traceback.

`except BaseException` with `KeyboardInterrupt`/`SystemExit` re-raised, matching
`eval.py`: the point is to survive anything the network or the SDK produces, not
to catch a signal. A `session.rollback()` guards the failure path even though
`run_warm` commits after every story and there is by construction nothing
uncommitted at that moment — cheap, and it stops one poisoned session from
turning a recoverable cell into a broken run.

**8.5 — An `ANTHROPIC_GENERATION_TIMEOUT_S` env override exists, and it exists
partly so that the 8.4 fix is testable against the real API.**
§3 puts model IDs in the environment; a latency budget is the same kind of
operational number, and an operator hitting a slow region should not need a code
change. The override pins the ramp to a single value and is recorded in
`eval_runs.config` when the eval runs under it.

Its second use is evidential. "`warm.py` no longer dies on `APITimeoutError`" is a
claim about an exception that a healthy API does not raise, so a test that never
triggers it proves nothing. With the override, `ANTHROPIC_GENERATION_TIMEOUT_S=2`
makes the real SDK raise a real `anthropic.APITimeoutError` from a real request,
and the run either continues or it does not.

**8.6 — §1 item 6's "cold generation p95 < 12s" is NOT met, is not reachable for
this pipeline, and is recorded as a missed target rather than engineered around.**
Measured p95 is 59.8s. That number is inflated by the defect in 8.1 and will fall
when it is fixed, but not to 12s, and the honest thing is to say why rather than
to keep re-measuring in the hope.

§1 item 6 is a budget for one API call. What §6 and §7 actually specify is a
pipeline: GENERATING, then VALIDATING, then up to three REWRITING passes each
followed by another VALIDATING, then MODERATING. §14's own per-node table settles
where the time goes — VALIDATING is 1.24ms at p50 and MODERATING is 1.55s, so
**the pure-function gate this project is about costs ~0.002% of the budget** and
essentially all of the 59.8s is the model generating tokens. The floor is
therefore set by output length: a stage-12 story is 120–200 words plus the
adaptive thinking Sonnet 5 spends whenever `thinking` is omitted (STATE invariant
9 — it cannot be turned off, and `temperature` cannot be pinned either), which
measures at 1,700–3,300 output tokens. At the ~100 tok/s the eval's own numbers
imply, 12s buys roughly 1,200 tokens. **A stage-12 story does not fit in §1.6's
budget at any timeout**, and one rewrite doubles the bill.

The three ways to reach 12s are all worse than missing it:
1. Drop the rewrite loop. That is the gate's only remedy for a failed draft, and
   §0 forbids trading decodability for anything.
2. Shorten the stories. §6's length targets are the curriculum; a stage-12 reader
   getting 60 words is a worse product, and §12 of `docs/EVAL.md` already reports
   under-delivered length as a defect.
3. Stream, and call time-to-first-token the latency. It would produce a number
   under 12s and it would be a lie, because the gate cannot run on a partial
   story — VALIDATING needs the whole text, so nothing can be shown to a child
   before the last token arrives.

**What §1 item 6 is right about is the number next to it.** "cache/warm-pool
serve < 300ms" is the latency a child experiences, and §7's whole warm-pool design
exists so that generation is never on that path (8.1). The claim this project can
honestly make is: *cold generation is 12–30s for early stages and 20–70s for the
long ones, and no child ever waits for it.* §1.6's first clause is recorded here
as **unmet and unreachable as written**; it is not restated as met, no acceptance
criterion is relaxed to cover it, and `docs/EVAL.md` keeps reporting the measured
p95 against the specified 12s so the gap stays visible.

**8.7 — Re-derived: 120s -> 240s, because `max_tokens` moved to 16000 underneath
this work. And `pipeline._call` now imports the ceiling instead of holding its own
stale copy of it.**
8.1 sized the timeout as `max_tokens / slowest-measured-throughput` = 8000 / 68.
While that was being written, 8.25 raised `MAX_OUTPUT_TOKENS` to 16000 in
`llm/generate.py` and 8.24 said explicitly that whoever moves the one must
re-derive the other. This is that re-derivation, and it is logged as a change to a
published number rather than folded silently into 8.1.

Two facts made it necessary rather than cosmetic:

1. **The ceiling was duplicated and only one copy had moved.** `pipeline._call`
   carried a literal `max_tokens=8000` while `llm/generate.py` was at 16000, and
   `_call` is the node that actually runs — `warm.py` and `eval.py` both reach
   generation through `run_pipeline`. 8.25 flagged this and could not fix it,
   because `pipeline.py` is owned here. Left alone, the live path would have kept
   the 8000 ceiling and kept the intermittent `api_fail` 8.25 observed: a
   generation that runs out of tokens returns `stop_reason="max_tokens"` and no
   parseable story at all, which §7 scores as api_fail — a lost cell, not a short
   story. `pipeline.py` now imports `MAX_OUTPUT_TOKENS` for both the request and
   the Langfuse span.
2. **A 16000-token ceiling can produce a generation the 120s clock would kill.**
   The point of the sizing rule is that the clock and the token ceiling fail the
   same request for the same reason instead of disagreeing about it. At 68 tok/s,
   16000 tokens is 235s. So 240s.

**The rule is now code, not a comment.** `client.timeout_for_tokens()` computes it
and `GENERATION_TIMEOUT_S` is asserted equal to `timeout_for_tokens(MAX_OUTPUT_TOKENS)`
by a test, so raising the ceiling again turns the suite red at the derivation
instead of surfacing as an intermittent api_fail in a warm run an hour later. That
test is the thing 8.24 asked for, in the only form that survives the next person
who does not read this file.

**What this costs, stated plainly.** `max_retries=2` triples it: a cell whose
server accepts the connection and then stalls three times now costs ~12 minutes
against ~91s before. That is defensible only because generation is off the child's
path (8.1) and because `connect=5s` is split out, so a *dead* host still fails in
5s. It is not defensible if anyone puts generation on a request path, and the
constant says so where it is defined. The measured effect on the eval runs the
other way: 27% of the old run's generation attempts were discarded work (8.0), and
recovering that is worth more wall clock than the rare stall costs.

The margin is honest rather than large. The biggest **successful** generation
measured is 6,939 output tokens / 62.4s (live, run 3, stage 9, accepted at 98.7%)
— so 240s is ~3.8x the worst real generation, and the headroom above that exists
for the 16000-token case the new ceiling now permits, which nothing has yet
observed. **ASSUMED, not proved: that throughput stays at or above 68 tok/s for a
16000-token response.** Every measurement behind that floor is of responses under
7,000 tokens; if long responses are slower per token, 240s is optimistic and the
symptom would be a timeout at exactly 240s, which is diagnosable from the number
itself.

**8.8 — The measured before/after, and what it does NOT license.**
The after-run is `eval_runs` id 3: stages 9–12, all themes, n=2, 24 cells, the
region where all 11 of run 2's failures were. `$0.8374`, 922s wall clock. Read
straight out of `eval_run_items`, both runs, same query.

| stages 9–12 only | run 2 — 30s | run 3 — 120s |
|---|---|---|
| cells attempted | 60 | 24 |
| **`APITimeoutError`** | **11 (18.3%)** | **0** |
| other `api_fail` | 0 | 2 — `max_tokens` / truncated body |
| HTTP attempts on GENERATING | 109 | 24 |
| **…killed by the clock and discarded** | **60 (55%)** | **0** |
| seconds burnt on clock-kills | 1,800 | 0 |
| **single attempts that took >30s** | **0 — impossible to observe** | **10 of 22** |
| GENERATING p50 / p95 / max | 25.4 / 78.7 / 86.6s | 14.1 / 62.2 / 62.4s |
| whole pipeline p50 / p95 | 43.9 / 91.5s | 36.2 / 68.3s |
| stories below the §0 gate | 0 | 0 |
| min decodability | 95.0% | 95.5% |

**The load-bearing row is the third from the bottom.** Ten of run 3's 22
generations took longer than 30s in a *single* attempt — 30.7, 31.7, 32.0, 33.3,
36.3, 39.1, 40.2, 48.9, 58.3, 62.0, 62.2, 62.4s. Under §7's 30s every one of those
needed at least a second attempt, and the six above 60s could not have completed in
three. That is an attribution-safe statement: the latency was measured, and 30s is
what the constant used to be. It does not depend on comparing failure counts
between two runs.

**What this does NOT license, stated because the tempting reading is wrong.**
Run 3 is **confounded**. Between the two runs the concurrent prompt work landed
8.22 (ask for the full specified length) and 8.20 (the grammar check), both in
files this phase does not own. So:

- The api_fail rate falling 18.3% → 8.3% is **not** attributable to the timeout
  alone, and neither is the p95 falling from 91.5s to 68.3s. The p95 improvement is
  partly just the disappearance of the 91s triple-timeout plateau, which *is* the
  timeout, and partly noise on n=24.
- **Cost per accepted story went UP, $0.0221 → $0.0419**, and the timeout is not
  why. Output tokens per single-attempt generation went from p50 2,002 to p50 3,584
  — the length fix asking for more story, plus more thinking. Some of the rise is
  also honest accounting: a clock-killed attempt returns no usage record, so run 2's
  figure omits 60 attempts' worth of tokens it had probably already paid for.
  Either way the direction is real and it is not this change's doing.
- Rewrite p95 went 1 → 2. Still inside §1's budget of 2, also the length change.

**Corroboration from outside this work:** 8.24 reports zero timeouts across 96
live generations at the 120s budget, measured independently by the prompt agent.
Two runs, two harnesses, same answer on the one question this change owns.

**The 16000 ceiling is verified as no-regression, not as a rescue.** After the
`pipeline._call` fix, 8 generations across stages 9–12 all succeeded (8.1–20.6s,
508–2,376 output tokens). **0 of the 8 exceeded 8,000 tokens**, so that probe does
not demonstrate the rescue — it shows the live path works at 16000/240s and nothing
broke. The evidence that the rescue is needed is run 3's two `max_tokens` failures
and its 6,939-token success, all at the 8000 ceiling.

**`make eval-smoke`, before and after.** Both PASS 4/4 with 0 stories below the
gate, which is what the gate is for.

| | before (`538a08b`) | after |
|---|---|---|
| verdict | PASSED 4/4 | PASSED 4/4 |
| mean decodability | 100.0% | 97.8% |
| p95 rewrite depth (§1 ≤ 2) | 0 | 1 |
| **cost** | **$0.0739** | **$0.0972** |

The +31% is **not** attributable to this work, and n=4 against a
non-deterministic model could not attribute it either way. The mechanism is
visible though: smoke runs stages 3 and 6, where nothing timed out before or
after, so the timeout constant cannot have changed a single token; `max_tokens` is
a ceiling and no smoke story came near even the old one. What did change is the
prompt — 8.22 asks for the full specified length, which is more output tokens and
more reach outside the allowlist, hence the rewrites that were not there before.
Recorded here because the number is the gate's cost and somebody will diff it, not
because this change caused it.

## Phase 8 — the two prompt defects the §14 eval surfaced

`docs/EVAL.md` answers §0's question — 169 of 169 produced stories cleared the
95% gate — and in doing so measures two things the gate cannot see. Both live in
the prompts, so both are logged here before the change. Numbers in these entries
are live measurements, not projections; the harness that produced them is 8.23.

**8.20 — §13's grammar check was rejecting on SENSE, not grammar. 6 of the 8
observed rejections were false positives, measured.**
`docs/EVAL.md` section 8 lists 8 grammar rejections out of 12 moderation
rejections. Reading them, the check was not answering the question it claims to
answer: *"Its wings sat"* was rejected because **wings cannot sit**, *"The rocket
ran fast."* because **rockets do not run**, *"A dragon lived in a can."* because
the writer should have picked "cave". Every one of those sentences is correct
English. The prompt's own final bullet already said *"A sentence that is
grammatical but strange, dull or illogical. Judge the grammar, not the sense."* —
it was there and it was not being followed, because it was the last line of a
nine-item list of things NOT to do and it named no test the judge could apply.

Re-running the eight rejected stories — pulled **verbatim** out of
`story_segments` for eval stories 3243, 3251, 3341, 3358, 3440, 3441, 3557 and
3558, not retyped — reproduced 6 of the 8 as rejections. That is the measurement
this entry rests on: 12.8% of the eval matrix fell back to the warm pool, 8 of
those 12 were grammar, and 6 of those 8 were correct, decodable, safe stories
being thrown away.

The fix is a **mechanical test the judge must run before it is allowed to flag
anything**, not another prohibition added to the list: swap every noun in the
sentence for a different noun and leave the grammar alone. *"Its wings sat."* ->
*"Its dogs sat."* is fine, so the sentence was already grammatical and only the
sense was odd. *"A dragon sit in a cave."* -> *"A cat sit in a box."* is still
broken, so that one is a real grammar error. The test is stated with the observed
false positives as its worked examples, so the rule arrives attached to the cases
that earned it.

**This is not a relaxation, and STATE invariant 5 is the reason the claim is
testable.** `"He did not came back."` shipped to a child and a later backfill
found 4 of 28 servable stories ungrammatical, so the check earns its place. The
live fixture in `tests/test_grammar.py` therefore grew in BOTH directions at
once: 8 must-approve cases (the eight above) and 3 further must-reject cases
(`"The man ran back stop."`, `"The cats runs fast."`, `"Sam had three cat."`),
each chosen to be noun-substitution-invariant — swap the nouns and it is still
broken — so the new rule is tested against more true rejects than the prompt had
before, not fewer. Measured over the 29-case fixture, before -> after:

| direction | before | after |
|---|---|---|
| false positives (a good story thrown away) | 6 of 18 must-approve | 0 of 18 |
| false negatives (a bad story reaching a child) | 0 of 11 must-reject | 0 of 11 |
| ungrammatical caught by the GRAMMAR half specifically | 8 of 8 | 8 of 8 |

A change that had bought the first row with the second would be a regression, and
that is exactly what the 3 added reject cases exist to catch.

**8.21 — Missing quotation marks around dialogue are explicitly NOT a grammar
failure. Stated as a policy call, with the cost named.**
Two of the eight rejections in 8.20 are punctuation, not semantics: *"The girl
said, come here, little rabbit."* and *"The girl said, this rabbit got a home."*
Both are readable; neither contains a wrong word. The prompt already requires the
judge to name **the one wrong word and the word that should replace it**, and a
missing quotation mark is not a word — so these rejections were also
self-inconsistent with the prompt's own gate, and the carve-out only makes the
existing rule bite.

The cost is real and is not hidden: a story with unquoted dialogue now ships, and
a child reads `The girl said, come here, little rabbit.` without the quotation
marks that would show them where the speech starts. That is accepted because the
alternative is throwing the whole story away and serving the child a fallback,
and because DEVIATIONS 4.13 already records that `_split_segments` breaks inside
quoted dialogue — the format does not currently handle quotation marks well
anyway. Punctuation is a **generation** improvement to make in `SYSTEM_RULES`, not
a rejection reason in the judge.

**8.22 — Length: `roughly {min}-{max} words` became a floor plus a plan, and
`SYSTEM_RULES` gained a frozen length rule.**
`docs/EVAL.md` section 12: 52.2% length compliance overall, and 6.7-13.3% at
stages 9-12 where §6 asks for 120-200 words and the model returned a mean of
89-102. A stage-12 reader was being handed about half the specified text.
Decodability is unaffected — a short story is still a decodable story — which is
precisely why the §7 gate never saw it.

Two causes, one in each half of the split prompt (STATE invariant 10):

- **The request block said `roughly`.** That word is a licence, and the model
  took it: measured per-stage means sit just above the floor at stages 1-4 and
  well below it from stage 5 up. The block now states the range as a floor with a
  midpoint to aim at, and a sentence count to plan against, because "short
  sentences" plus "120 words" is a plan the model has to be told how to make. The
  hyphenated `{min}-{max}` form is kept verbatim so the existing
  `tests/test_llm.py` assertion on it still means what it meant.
- **`SYSTEM_RULES` never mentioned length at all.** It gained a rule that is
  stage-independent by construction — no numbers, no theme, nothing per-request —
  so the frozen cache prefix stays frozen and the tests that pin that keep
  passing. The rule says the thing the model gets wrong: reaching a longer count
  means telling MORE of the story, not writing longer or more complicated
  sentences. Written that way round on purpose, because the cheap way for a model
  to add words is to add clauses, and that walks straight back through the gate.

It is ranked **below** the vocabulary and grammar rules, not above them. §0's
rule is not negotiable in either direction: length is never bought with
decodability, and the measurements below report the decodability distribution
next to the word counts for exactly that reason.

Measured over 48 first drafts per side (8 stages x 3 themes x 2, live model,
`--effort medium` as `make eval` uses), before -> after:

| | before | after |
|---|---|---|
| length compliance, 30-60 band | 91.7% | 100.0% |
| length compliance, 60-120 band | 25.0% | 100.0% |
| length compliance, 120-200 band | 33.3% | 100.0% |
| mean word count, 120-200 band | 109.3 | 156.1 |
| overall length compliance | 45.8% | 100.0% |
| stories OVER the range | 0 | 0 |
| OOV word instances | 0 | 0 |
| cells that produced text | 48 of 48 | 45 of 48 |

The before column reproduces `docs/EVAL.md` closely enough to trust the harness:
45.8% against the report's 52.2%, and a 120-200 band mean of 109.3 against the
report's per-stage 89-102. But three things ARE bought with something, and they are
8.25, 8.27 and 8.28 rather than footnotes to this entry — plus the cost below.

**Cost: this change roughly doubles the token bill, and that is the headline it
would be dishonest to omit.** Measured by the concurrent timeout work through the
eval harness: cost per accepted story **$0.0221 -> $0.0419**, `eval-smoke`
**$0.0739 -> $0.0972**, output tokens p50 **2,002 -> 3,584**. A timeout cannot move
a token count, so the attribution to this change is correct. Most of it is not
story text — a 200-word story is ~250-350 tokens — it is the extra reasoning the
model does to plan and check a length.

**Asked whether the target can be hit more cheaply, and measured rather than
assumed.** The suspected driver was the block's closing "count the words ... and
fix it" instruction, so it was A/B'd at stages 5-12, 12 first drafts per arm:

| stages 5,8,10,12 | with count-and-fix | without |
|---|---|---|
| length compliance | 91.7% | 91.7% |
| output tokens p50 / mean | 801 / 1,734 | 445 / 1,211 |
| cost per produced story | $0.0188 | $0.0135 |
| mean first-draft decodability | 0.9683 | 0.9486 |
| first drafts clearing the gate | 7 of 12 | 5 of 12 |

So the cheaper arm holds length at ~28% fewer output tokens — and moves
decodability the wrong way. **The instruction was kept**, because §0 makes that
trade one-directional: a cost saving paid for in decodability is not a saving.
Twelve cells per arm cannot settle either column, and the honest statement is that
the cheaper prompt is a live candidate which needs the full matrix to evaluate,
not a rejected one. What is settled is that the 2x is real and is the price of
delivering the length §6 specifies.

**8.23 — `scripts/measure_length.py` exists alongside `storyforge.eval`, and does
NOT reimplement its metric.**
§14 says the CLI stays canonical and everything else is a view of it. This script
obeys that by importing `eval.word_tokens` and reusing `pipeline.validate_draft`,
so "word count" and "decodability" here mean exactly what they mean in
`docs/EVAL.md` — a second definition of word count would let a prompt change look
like a win because the ruler moved.

What it does differently, and why each difference is necessary rather than
convenient:

- **One GENERATE call per cell — no rewrite loop, no moderation.** The quantity
  under test is what the *generation prompt* produces. A rewrite can change
  length (its instruction is "keep the same story and the same length"), so
  folding the loop in would let the loop absorb the signal.
- **A 240s timeout instead of §7's 30s.** This is the one that matters. The
  120-200 word stages already exceed 30s (8.24), so a length measurement taken
  through that timeout censors the sample in the direction of the thing being
  measured: the longest stories time out, and a prompt that produced MORE text
  would score BETTER on the survivors. The override is set on the module global
  the call site reads, so §7's constant is untouched for every other caller.
- **Decodability reported beside every word count.** Not decoration — it is the
  only thing that distinguishes a length fix from a gate trade.

**8.24 — §7's 30s generation timeout is now the binding constraint on the long
stages, and this change makes it tighter. Not fixed here.**
`docs/EVAL.md` section 2 already reports it as a defect: 11 of 180 cells produced
no text because a single generation at the 120-200 word stages needs longer than
30s, the SDK retries twice, and the cell burns 3 x 30s and raises
`APITimeoutError`. Measured cold generation p95 is 59.8s against §1 item 6's 12s
target, concentrated at exactly those stages.

Asking the model for the full specified length makes each of those calls longer,
so the api_fail rate at stages 9-12 will rise unless the timeout moves. That was
stated here as a handoff before the change; **it has since been fixed by the
concurrent timeout work — DEVIATIONS 8.0-8.5 raised `GENERATION_TIMEOUT_S` from
30s to 120s, split the connect budget out, and added
`ANTHROPIC_GENERATION_TIMEOUT_S`.** This entry stays as the record of the
prediction and of which way the two changes interact, and it is worth reading
alongside 8.1: that entry sizes 120s off `max_tokens / slowest-throughput` =
8000 / 68 tok/s, which is a bound on how long a HEALTHY generation can run — so
raising `max_tokens` (8.25) also moves the number that bound was derived from.
Whoever raises the one should re-derive the other.

Measured after the length change, with the 120s budget in force: **zero
timeouts** in 96 live generations. The api_fail that did appear came from
`max_tokens`, not from the clock — see 8.25, which is now the live half of this
concern.

**8.25 — `max_tokens` for a generation is 8000 -> 16000, and it is now a NAMED
constant because `pipeline._call` holds a second copy of it.**
Observed live, on the OLD prompt, during the before-measurement: a stage-9
generation returned `stop_reason="max_tokens"` with no parseable story. §7 counts
a schema-parse failure as `api_fail`, so that is a whole cell lost — not a short
story, nothing at all. On Sonnet 5 adaptive thinking is ON when `thinking` is
omitted and it counts against `max_tokens` (STATE invariant 9), so the story and
the reasoning compete for one budget; 8.22 makes the story half bigger, which
makes the collision likelier. `max_tokens` is a ceiling and not a reservation, so
raising it costs nothing when it is not reached and the model is never told the
number.

**RESOLVED.** The handoff below was taken up by the concurrent timeout work:
`pipeline._call` now imports `MAX_OUTPUT_TOKENS` and passes it both as `max_tokens`
and to `generation_timeout()`, so the live path carries the 16000 ceiling and the
budget is derived from it rather than guessed alongside it. Measured after: **0
`max_tokens` truncations in 54 further first drafts**, against 3 in the 48 taken
while the live path still had 8000. The original text stands below as the record of
what was handed over and why.

The honest half of this entry: **the number is duplicated and only one copy moved.**
`llm/generate.py::MAX_OUTPUT_TOKENS` is the copy this change owns.
`pipeline._call` carries its own literal `max_tokens=8000`, and `_call` is the node
that actually runs — `warm.py` and `eval.py` both reach generation through
`run_pipeline`. So until `pipeline.py` imports the constant, the live path keeps the
8000 ceiling and keeps the intermittent `api_fail`. The constant is exported and
named for exactly that import; `pipeline.py` already imports `SYSTEM_RULES`,
`StoryDraft`, `build_request_block` and `build_vocabulary_block` from the same
module, so the one-line change has a precedent to follow. Flagged rather than
reached for, because the file is owned elsewhere.

**8.26 — `llm.generate.generate_story` has no production caller. Found while
building 8.23, and it is why that harness does not use it.**
`grep` over `storyforge/`: nothing calls `generate_story`. `warm.py` and `eval.py`
both go through `pipeline.run_pipeline`, which calls the private
`pipeline._call` — a near-duplicate of `generate_story` that assembles the same
prompt from the same three pieces, with its own `max_tokens` literal (8.25) and
its own client construction.

This matters for the measurements in this phase and not only for tidiness. A
length harness pointed at `generate_story` would be a green number about a code
path no child's story ever travels — the two agree on the prompt **today**, and
nothing enforces that they keep agreeing. `scripts/measure_length.py` therefore
calls `pipeline._call`, reaching past a private name on purpose and saying so in
the docstring, because measuring the live node is worth more than respecting an
underscore. The duplication itself is not resolved here: collapsing `_call` into
`generate_story` is a `pipeline.py` change, and that file is owned elsewhere. Left
as one entry rather than silently worked around, so the next reader does not have
to rediscover that there are two generation nodes.

**8.27 — The length fix regressed a GATE criterion on its first pass, and the
story-word wording is the repair. Caught by reading the column, not by a test.**
The first version of 8.22 was the length section alone, and its word counts were a
clean sweep: 100.0% compliance in every band. The story-word column was not:
**5 of 45 first drafts used MORE than 3 DISTINCT story words, one of them 8, where
the before-measurement had none at all.** `passes_gate` caps distinct story words
at 3 (§7, `MAX_STORY_WORDS`), so those drafts fail the gate on a criterion that
has nothing to do with decodability, and the first-draft gate pass rate fell 91.7%
-> 77.8% with most of the fall from this and not from decodability.

The cause is not mysterious once seen: a 160-word story reaches further into the
offered bank than a 100-word one, and the request block said only "at most 3" —
not that the 3 is a count of DIFFERENT words, and not that repeating them is free.
STATE invariant 3 exists because repetition is correct decodable-reader behaviour
and occurrences are deliberately uncapped, but the prompt never said so, so
"write more" and "at most 3" read as a contradiction the model resolved the wrong
way. The block now says both, and says explicitly that needing more words is not a
licence for a fourth.

**The general lesson is the one worth keeping.** The headline number went from
45.8% to 100.0% and every band was green. Had the report stopped at the metric it
set out to move, it would have shipped a change that traded a gate criterion for a
word count — exactly the trade §0 forbids, in a column nobody was looking at.
Length compliance was never the gate; it was a number next to the gate.

**8.28 — First-draft decodability moved DOWN, and the honest reading is "more
rewrite pressure", not "a lower gate".**
Reported because the alternative is letting a favourable headline stand alone.
Both sets of stored texts were re-scored against ONE decoder inventory
(`--revalidate`, 8.23 — the runs straddled the `wh` addition of 8.13, and a bigger
inventory can only raise decodability, so the printed columns were two different
rulers):

| first drafts, one inventory | before | after |
|---|---|---|
| mean decodability | 0.9850 | 0.9748 |
| median | 1.0000 | 0.9875 |
| minimum | 0.7917 | 0.8795 |
| drafts below the 95% gate | 4 of 48 (8.3%) | 7 of 45 (15.6%) |

Three things have to be said about that table at once. **The gate did not move**
— it is 0.95, a test asserts it, and a first draft below it is not a story below
it: §7 sends it to the REWRITE node with a budget of 3, and `docs/EVAL.md` records
0 of 169 stories finishing below the gate at a rewrite p95 of 1. **The sample
cannot carry the claim either way** — 4/48 against 7/45 is not a difference this
many cells can distinguish from noise, and 5 of the 7 sit at stages 4 and 5, which
is where 3 of the before run's 4 sat too. **And the direction is still the wrong
one**: §6 is explicit that the primary lever is allowlist-constrained generation
rather than retry pressure, so a first-draft miss rate drifting up means the change
is leaning on the loop a little harder — paid in attempts per accepted story, in
latency, and in money, not in what a child reads. The next full `make eval` will
settle it at n=180 against the p95<=2 criterion §1 item 2 already checks; this
entry exists so that number is read as a follow-up and not as a surprise.

**8.29 — A body/appearance rule in `SYSTEM_RULES`, as the compensating control for
`fat` leaving the blocklist. Prevention, and verified by an adversarial probe
rather than by a rate.**
Handed over from the vocabulary work. 8.16 removed 15 innocuous words from the
blocklist including `fat`, and was right to: **a blocklist hit SHORT-CIRCUITS
`moderate()`**, so an innocuous entry does not merely narrow vocabulary, it stops
§13's layer-3 hazard-versus-harm judgement from ever running on that story. `fat`
is in every printed stage-2 word list and "the fat cat sat" is the archetypal
decodable sentence.

`fat` is nonetheless the weakest of the 15, and the reason is structural: every
other removal's hazard is an ACTION, and layer 3's REJECT bullets are all actions
— hurt, cruelty, weapons used against a character. `fat` is a DESCRIPTION, so
"Sam is fat. Sam is sad." can clear all three layers. The control therefore goes
where prevention belongs, which is the frozen prefix — the same layer and the same
argument as DEVIATIONS 4.12's grammar rule. It is ranked below the vocabulary and
grammar rules and above length: a story must never describe a body in order to
reach a word count.

**Two measurements, because a rate alone would have been a false green.** The
natural rate is already zero: 12 stories at stages 2-3 with no such rule contained
**no** character body description at all — the only appearance hits were "a big
rock" and "It had big wings". A before/after on that rate compares 0 with 0. The
reason is worth recording: `fat` sits at frequency rank **1440**, and §6 gives the
prompt only the top 400, so the model is never SHOWN the word. The exposure is not
the prompt offering it — it is that the validator would accept it if the model
reached for it anyway, and that the blocklist no longer stops it.

So efficacy is measured by an adversarial probe instead
(`tests/test_llm.py::test_live_appearance_probe_both_directions`, opt-in on
`STORYFORGE_LIVE_APPEARANCE=1`), which pushes three requests that name the harm
word outright. A first draft of those probes was too polite ("teased for how their
body looks") and the model steered around it unaided — a meaningless pass, and a
reminder that a probe which cannot fail is not a probe.

| direction | before | after |
|---|---|---|
| pushed requests producing a body description | 3 of 3 | **0 of 3** |
| body-description offences across them | 6 | **0** |
| ordinary stories still describing things | 2 of 3 | 2 of 3 |
| ordinary stories with a body description | 0 of 3 | 0 of 3 |

Before, verbatim: "Tom was a big man. Tom was fat."; "Kim is not big. Kim is
short. Kim is not fat."; "The man is short. The man is thin. The kid is tall. The
kid is fat." After, the same three requests produce stories about what people DO —
"The man had a plan. The kid had fun." — with no appearance word applied to a
person.

**The other direction is the one that would have been easy to skip.** A rule that
flattened every story would pass the first row and look like a success, so the
probe also asserts that ordinary requests still describe things: after the change
they still return "a big red thing", "The red thing had strong legs", "It had long
ears and a soft nose". Creatures, places and objects are exempt by construction —
the rule names a PERSON's body, so a dragon can still be big and a cave dark. And
on the same 12-cell stage-2/3 measurement, length compliance stayed 100% and the
share of first drafts below the gate did not worsen (7 of 12 without the rule, 3
of 6 with it), so the rule is not paid for in rewrites either.

**What this control does NOT do**, stated because the entry would otherwise imply
it: it is prevention only. `moderate()` still has no REJECT bullet for
appearance-based content, so a story that gets past the prompt is not caught
downstream. Adding one was deliberately not done — the hazard/harm prompt has a
55% false-positive history (3.10) and "a big dragon" is exactly the shape that
would trip it. If a body description is ever observed in real output, detection is
the next move, and it needs 3.10's both-directions treatment before it ships.

**8.19 — A vocabulary change makes stored `stories.decodability` stale. The test
that catches it was NOT relaxed; the rows were repaired.**
`tests/test_eval.py::test_the_gate_still_agrees_with_what_the_harness_recorded`
re-runs the pure gate over every story the harness wrote and compares it to the
stored number. Its docstring gives the requirement: "a harness that mis-recorded
its own verdict is caught." Adding `of` as a sight word turned it red on story
3646 — recorded 0.9744, gate now 1.0, because `of` was that story's single
violation. Which is to say the test caught precisely the thing 8.10 set out to
fix, and did it without being asked.

**`justify-test-edit` was run and returned CODE IS WRONG, so the test is
untouched.** The discriminating question — would it still fail if the change were
reverted? — is **no**: it was green before, and it is red because of what this
work did. That verdict forbids editing the assertion, and none of the five grounds
for a TEST IS WRONG verdict applies. Recording that here because a red test in a
data-change commit is exactly where an assertion quietly gets a wider tolerance.

The stale thing is the row. `stories.decodability` is a **cache** of the gate's
verdict on the story text, so a vocabulary change invalidates it the same way it
invalidates `allowed_words` — and `make precompute` was already re-run for the
same reason. `scripts/rescore_stored_stories.py` is the story-row half of that
rebuild: report-only by default, `--apply` to write, idempotent (a second run
selects the same rows, finds them in agreement and writes nothing — verified).

**What it must not touch, and does not: `eval_run_items.metrics`.** That JSONB
holds each story's measurement as recorded AT RUN TIME and is where
`docs/EVAL.md`'s figures come from. Rewriting it would make a committed report
unreproducible, which is the opposite of what §14 asks for. Verified after the
repair: story 3646's `eval_run_items` decodability is still
`0.9743589743589743`, and run 2's worst accepted story is still 95.0% over 157
items — the two numbers `docs/EVAL.md` reports. The history survives in the run's
evidence; the story row carries what the current gate says.

**Measured blast radius, which doubles as the proof the fix works.** Over all 169
stored eval stories: **12 disagreed, all 12 improved, 0 got worse.** Seven went to
exactly 100%. The matrix's worst story (3777, stage 12) went 0.9500 -> 0.9661 —
`docs/EVAL.md` reports 95.0% as the worst single story in the run, and that story
was one `of` away from 96.6%.

The script asserts the direction rather than assuming it: a vocabulary ADDITION can
only move a word OUT of §5.1's denominator, never into it, so a row that scored
LOWER would mean the gate narrowed — §0's forbidden direction. It refuses to write
and names the rows instead. That check is the reason the "all 12 improved" claim is
evidence and not an observation.
---

## Phase 8 — the two vocabulary/blocklist defects the §14 eval surfaced

Numbered 8.10-8.19 to stay clear of the concurrent prompt and timeout work above.
Logged before the changes were made. Every claim marked **PROVED** was measured in
this repo with the command shown; everything else is reasoning and is labelled as
such.

**8.10 — `of` becomes a sight word at stage 2. It is NOT fixed with a GPC, and the
repo's own anti-golden list had already settled that.**
`docs/EVAL.md` section 9 names `of` as the one actionable item in the unalignable
list: 7 instances across the matrix, the most of any word, and — checked against
all 33 `sight_words` rows — not a sight word at any stage. So every story reaching
for it spent part of §7's 5% violation budget on the second-commonest word in
written English.

**PROVED unalignable, not merely unmatched.** CMUdict gives `of` exactly one
pronunciation, `AH1 V`, so a complete alignment needs `f`→/v/. That correspondence
is not in the 92-entry inventory and no synthetic-phonics scope & sequence teaches
it — `of` is the standard example of a word taught by sight *because* the letters
lie. DEVIATIONS 1.6 recorded this shape in Phase 1 and used `of` as its example.

The decisive evidence is that the fix cannot be an inventory row:
`tests/decoder/test_anti_golden.py::NEVER_DECODABLE` already contains
`"of": "f -> /v/"`, so adding `f|V` would fail the acceptance criteria by name.
That file also states the correct alternative in its own docstring — "Several of
these ARE sight words in the real curriculum ("said", "one") — that is how a child
meets them, and it is the correct product behaviour. The point here is narrower:
the *aligner* must never claim to have sounded them out." `said` and `one` are in
`NEVER_DECODABLE` **and** in `sight_words`. `of` was in the first and missing from
the second. That asymmetry is the whole defect.

**Why this is not weakening the gate (§0).** A sight word does not widen what the
aligner will certify; `validate_word` classifies sight before it ever looks at a
pronunciation, and the anti-golden test checks alignment with no allowlist at all,
so it still fails if `f|V` is ever added. What changes is the §5.1 denominator:
`of` moves from VIOLATION to SIGHT and stops being scored. That is a real change to
the guarantee and is justified against §4, which defines sight words as exactly
"per-stage allowlist of high-frequency non-decodable words", and against Appendix
A, whose sight-word column is labelled "(sample)" — a sample is licence to extend,
not a closed set. The precedent is Appendix B's own rule, quoted in DEVIATIONS 1.1:
the tests are the acceptance criteria, the seed CSV is an implementation detail.

**Stage 2, not stage 1.** `to` (wordfreq rank 2) and `is` (rank 8) bracket `of`
(rank 4), and stage 2 is the tier Appendix A itself assigned to the top-10 function
words. Stage 1 is deliberately the three-word day-one set. Measured cost of the
choice: zero — the eval observed `of` only at stages 8-12, so stage 2 already
over-delivers on demand by six stages.

**8.11 — Four more sight words: `so`@2, `do`@2, `be`@3, `from`@4. Each completes a
set Appendix A had already opened.**
All four are PROVED unalignable at stage 12 by the same probe. None is a
convenience: each one is the missing member of a group the seed already committed
to, so leaving it out is an internal inconsistency rather than a policy.

| word | rank | the set it completes | why the aligner cannot have it |
|---|---|---|---|
| `so` | 33 | `go` (98), `no` (51) at stage 2 | `o`→/oʊ/ in an open syllable. `so` is MORE common than both words already admitted for that exact exception. |
| `do` | 50 | `to` (2), `go`, `no` at stage 2 | `o`→/uː/. See below — `to` only "decodes" by accident. |
| `be` | 17 | `he` (22), `she` (64), `we` (29), `me` (36) at stage 3 | `e`→/iː/ in an open syllable. `be` is more common than all four and is the fifth member of the set. |
| `from` | 26 | `said` (100), `you` (11), `they` (35) at stage 4 | `o`→/ʌ/. Placed at 4 rather than 2 because Appendix A introduces consonant clusters there, so `fr` stops looking alien at the stage the word arrives. |

**PROVED, and worth recording: `to` is decodable only because of a dictionary
accident.** `validate_word("to", mastered_ids(2))` returns DECODABLE, which looks
like the inventory handling `o`→/uː/. It is not. CMUdict lists three
pronunciations for `to` — `T UW1`, `T IH0`, `T AH0` — and §5.2.5's
any-pronunciation-passes rule takes the third, where the reduced vowel matches
`o|AA` under the §5.2.6 schwa relaxation. `do` and `two` have only the stressed
`UW1` and therefore fail. Nothing pedagogical distinguishes them; CMUdict's
variant coverage does. This is the §5.2.5/§5.2.6 interaction paying a dividend in
one place and not the next word over, and it is the reason `do` needs a seed row
while `to` did not.

**8.12 — The sibling sweep's real finding: a raw frequency sweep is not the signal,
and the ratchet says why.**
The obvious sweep — "top-N words by `wordfreq` that are neither decodable at stage
12 nor sight words" — returns **1,252 of the top 3,000**. That is not 1,252 bugs.
It re-derives the measurement already in DEVIATIONS 1.9: 64.2% of the adult lexicon
is unspellable by an inventory that is small on purpose (§18: "the guarantee's
strength is its smallness"). Used as a to-do list it would argue for dissolving the
gate. The full list is in the report, not here.

Two sharper criteria replaced it, both computable in this repo:

1. **Empirical demand** — the word appears in `docs/EVAL.md` section 9's
   unalignable set, i.e. the generator actually reached for it across 169 stories.
2. **Internal inconsistency** — the seed already admits an equally or less common
   word for the identical spelling-to-sound exception.

Every addition in 8.10 and 8.11 satisfies at least one; `of`, `be` and `from`
satisfy the first, `so`, `do` and `be` the second.

**PROVED that the alternative fix fails: the vacuity ratchet fires.** The tempting
one-row answer to `so`/`old`/`gold`/`told`/`cold`/`hold` is `o|OW` at stage 6.
Adding it moves the `test_gate_has_not_dissolved` ratio from **44.62% to 50.40%**,
past the 50.00% ceiling, and the suite goes red. Six words for 5.8 points of
vacuity is exactly the trade DEVIATIONS 1.10 refused, and this is the first time
the ratchet has fired on a change someone actually wanted to make rather than on a
deliberate sabotage. It is doing the job it was built for.

**8.13 — One GPC added: `wh`→/w/ at stage 3. Inventory 92 -> 93.**
This is the one place in the sweep where the honest fix IS an inventory row, and
the case is measured rather than argued.

Appendix A's stage-3 row reads "sh ch th(2) ng qu + w x y z v j" — the digraph
lesson — and omits `wh`, which every synthetic-phonics sequence teaches inside that
same lesson (Letters & Sounds Phase 3, UFLI, Wilson). DEVIATIONS 1.10 fixed the
boundary test for this decision as "does a real scope & sequence teach this?", and
for `wh` the answer is unambiguous. The seed's own data shows the gap being papered
over: `what` and `when` are sight words at stages 8 and 7, and `when` needs nothing
but this row.

**PROVED, against both anti-golden guards:**

| | before | after |
|---|---|---|
| vacuity ratchet (`test_gate_has_not_dissolved`) | 44.62% | **44.75%** (+0.13pt, ceiling 50.00%) |
| `NEVER_DECODABLE` words broken | 0 | **0** |
| new `allowed_words` candidates in the top-20k | — | **31** |

+0.13 points of vacuity buys seven words inside the top 500 — `when` (48),
`which` (58), `where` (102), `why` (120), `while` (136), `white` (275),
`whether` (459) — plus `whale`, `wheel`, `wheat`, `whisper` and their inflections.
`white` is one of the words `docs/EVAL.md` section 9 recorded the generator
reaching for. For comparison, the `o|OW` row rejected in 8.12 cost 5.8 points for
six words; this one costs 0.13 for thirty-one. The distributions DEVIATIONS 1.10
warned overlap do not overlap here.

**`wh`→/h/ is deliberately NOT added.** It is the correct second half of the
lesson ("wh sometimes says /h/"), and it unlocks nothing: `who`, `whose` and
`whole` still fail on their vowels (`o`→/uː/, `o`→/oʊ/), which are the absences
8.12 proved must stay. A row that widens the gate and certifies no new word is
pure downside, so the inventory gets one row, not two.

Two consequences stated rather than left to be found. `when` is now decodable at
stage 3 while remaining a sight word from stage 7 — not a conflict, because sight
classification short-circuits, and at stages 3-6 it is an improvement (`when` used
to score as a VIOLATION there). And `whip`/`whack`/`whipped` enter the allowlist;
all sit at rank >= 6975, outside §6's top-400 prompt window at every stage, and
`whisky` was already caught by the blocklist.

**8.14 — 15 words removed from `data/blocklist.txt`: 241 lines / 239 entries ->
224 lines / 224 entries.**
DEVIATIONS 6.15 took the list from 52 to 240 after finding 42 profanities in the
vocabulary handed to a model writing children's stories. That purge was necessary
and its output is 94% correct. What it also did was sweep up words whose only
hazard is a sense unreachable in this product, and the eval measured the bill:
**2 of the 3 layer-1 rejections in a 180-cell matrix were the single word `high`**,
one of them killing *"The stars are very high."* — a 100%-decodable, entirely safe
stage-6 story.

**`high` is a PROVED self-contradiction, not a judgement call.**
`storyforge/llm/moderate.py`'s own approve list, line 47, reads "Dark caves,
storms, deep water, **high places**." The blocklist short-circuits before that
prompt is ever sent, so layer 1 was rejecting stories for containing a word layer 3
is explicitly instructed to approve.

That short-circuit is the general argument, and it is stronger than "the allowlist
gets smaller". `moderate()` returns immediately on a blocklist hit, so an innocuous
word on this list does not merely narrow vocabulary — **it prevents layer 3 from
ever exercising the judgement it was calibrated for.** DEVIATIONS 3.10 and 4.12
spent two rounds and a both-directions test matrix getting hazard-versus-harm right
(55% false positives -> 0%). A broad layer-1 list discards that calibration for
every word it names, and does it invisibly: the story is gone, the metric says
"moderation_fail", and nothing distinguishes it from a real reject.

DEVIATIONS 2.4 wrote the correct principle in Phase 2 — "it is one of three
independent layers; it does not need to be the paranoid one" — and 6.15's header
comment then chose over-inclusion, naming "`crack` an egg, `hang` a coat" as
acceptable losses. The eval reprices that estimate. The loss is not a few innocent
words; it is whole clean stories, and 2 of 3 rejects.

**The test applied to all 239 entries, stated so it can be checked and disagreed
with.** *Is the hazard carried by the word, or only by the sentence it is in?*

- Hazard in the **word** — `fuck`, `cunt`, `rape`, `porn`, `slut`, `kill`, `gun`.
  No sentence makes them safe. Layer 1 is exactly the right layer. **Kept, all of
  them.** Every entry in the profanity (31) and sexual (34) sections is kept
  unchanged, including the dual-use ones where the innocuous sense has a standard
  synonym a children's book would use instead: `ass` (donkey), `cock` (rooster),
  `tit` (the bird).
- Hazard in the **sentence** — the word's dominant sense is innocuous and its
  hazardous sense is a *judgement about a sentence*, which is precisely what layer
  3's REJECT bullets already name. Layer 1 cannot read sentences; layer 3 can.
  **Removed, where the innocuous sense is also natural in this product's domain.**

Each removal below names the layer-3 REJECT bullet that still covers its hazard,
because "safe" here means "still caught, one layer down" and not "harmless".

| removed | rank / stage | why it is safe to remove | still covered by |
|---|---|---|---|
| `high` | 161 / 6 | Elevation. §13's approve list names "high places" verbatim. | "substances" — and `stoned`, `drug`, `drugs`, `weed`-sense words stay |
| `pig` | 5,627 / 2 | A farm animal, and a canonical stage-2 CVC word. The animals theme. | "Cruelty, bullying" (the insult use) |
| `rat` | 5,351 / 2 | An animal, and a canonical stage-1/2 CVC word. | "Cruelty, bullying" |
| `fat` | 1,440 / 2 | "The fat cat sat on the mat" is the archetypal decodable sentence; `fat` is in every stage-2 phonics word list ever printed. `obese` and `skinny` stay — those are only ever about a body. | "Cruelty" — but see 8.16, this is the weakest of the fifteen |
| `pot` | 3,577 / 2 | A cooking pot. It is the inventory's OWN example word for `o|AA` in `gpcs.csv`. The cannabis sense needs a frame (`smoking`, `dope`, `weed`-as-drug) that stays blocked. | "substances" |
| `crack` | 3,482 / 2 | Crack an egg; a crack in a rock. 6.15's comment named this as an accepted loss. | "substances" |
| `weed` | 4,482 / 6 | A weed in a garden. | "substances" |
| `mean` | 256 / 6 | "What does it mean", "did not mean to" — the dominant sense in general English, at rank 256, inside every prompt window from stage 6 up. | "Cruelty, bullying, or a character deliberately harming another" |
| `hang` | 2,431 / 3 | Bats hang upside down; hang a coat. `hanged` — the form reserved for execution — and `noose` both stay. | "Anyone or anything is hurt, injured, killed, or dies" |
| `kick` | 1,902 / 2 | Kick a ball. A canonical stage-2 `ck` word. | "Anyone or anything is hurt" |
| `kicked` | 3,693 / 4 | As `kick`. | as `kick` |
| `bury` | — / unalignable | A dog buries a bone. `burrow` is a story word for this theme. The funeral frame — `grave`, `funeral`, `coffin`, `corpse`, `dead`, `death`, `died` — stays blocked entirely. | "...killed, or dies" |
| `buried` | — / unalignable | As `bury`. | as `bury` |
| `creep` | 8,887 / 6 | The verb: a cat begins to creep. `creepy` stays — it is only ever pejorative or scary, and §6 bars scary content. | "Cruelty" |
| `weird` | — / unalignable | A weird sound. A descriptive adjective swept up by an appearance-insult category. | nothing needed; it is not a hazard |

Ten of the fifteen were removing a row from `allowed_words`; five (`bury`,
`buried`, `weird`, and the unalignable forms) cost no allowlist rows at all and
were purely killing stories at layer 1. `pot`, `crack`, `pig`, `rat`, `fat` and
`kick` are all stage-2 CVC words, which is where the vocabulary is tightest (157
words at stage 1, and §18's floor is the binding constraint there).

No test changed. `tests/test_phase3.py`'s layer-1 assertions are all on words that
stay blocked.

**8.15 — `shot`, `beat`, `blade`, `hurt`, `fight`, `smoking`, `butt`, `hate` were
examined and KEPT. What was not removed is part of the audit.**
The mandate's asymmetry is real: removing a genuine hazard to fix a false positive
is much worse than the false positive. Each of these has a defensible innocuous
sense and was still kept.

- **`shot`** (rank 582, stage 3) — the strongest rejected candidate. "The rocket
  shot up past the moon" is natural space-theme prose and `shot` is a canonical
  stage-3 `sh` word, so the cost is real. Kept for two reasons. `tests/test_phase3.py::test_blocklist_catches_violence_without_an_api_call`
  asserts `shot` is caught alongside `gun` and `dead` — an existing test naming it
  as a violence catch is evidence about intent that a rank number does not
  outweigh, and a removal that requires editing a violence test is the wrong
  instinct. And `went up` is available at stage 3, so the loss is a nicer verb, not
  a story.
- **`beat`** — "beat its wings" is natural dragon prose, but "beat" as strike is
  co-dominant, not recessive. The removal test in 8.14 requires the innocuous sense
  to be clearly dominant. It is not.
- **`hurt`, `fight`** — these are not false positives at all. Layer 3's REJECT
  list says "Anyone or anything is hurt" and §6's bar is "no scary content beyond
  mild peril", so layer 1 and layer 3 agree. `docs/EVAL.md`'s third layer-1
  rejection was `fight` at stage 7, and that one is a **true** positive; only the
  two `high` rejections were false. Worth separating, because "2 of 3 rejects were
  one word" reads as if all three were errors.
- **`blade`** ("a blade of grass"), **`smoking`** (a dragon), **`butt`** (a goat),
  **`hate`** ("the cat hates rain") — all co-dominant at best, all at rank >= 772,
  and `sword`/`knife` are kept on the same reasoning. `smoking` is the highest-risk
  remaining false positive and is flagged as such in the report; `smoke` itself is
  not blocked and is decodable from stage 5, so the natural sentence survives.

**8.16 — `fat` is the weakest of the fifteen removals and wants a compensating
control in §6, which is not owned here.**
Every other removal's hazardous sense is an *action* — hitting, harming, dying,
intoxication — and layer 3's REJECT bullets name actions. `fat` is a
**description**, and "Sam is fat. Sam is sad." is a sentence a Haiku judge tuned on
"anyone hurt / cruelty / weapons" may well approve. The residual risk is stated
rather than absorbed.

The removal still stands: `fat` is in every printed stage-2 word list, "a fat cat"
is the archetypal decodable sentence, and blocking it is the exact class of error
this entry exists to fix. The right compensating control is one line in
`generate.py`'s `SYSTEM_RULES` — do not describe a character's body — which is
prevention at the layer DEVIATIONS 4.12 established for exactly this shape
(prevented in the prompt, detected in moderation). `generate.py` is owned by a
concurrent agent, so this entry is the handoff, not the fix.

**8.17 — Two duplicate blocklist lines removed. DEVIATIONS 6.15's "240 entries"
was 239.**
`piss` appeared under both profanity and bodily, `bloody` under both profanity and
violence, so the file's 241 non-comment lines loaded as 239 entries. Harmless to
behaviour — `load_blocklist` returns a frozenset — but 6.15's own bug was a count
moving the wrong way being the *only* symptom of a broken list, and a file where
lines and entries disagree by two makes that check weaker than it looks. After this
change the file has 224 non-comment lines and `len(load_blocklist()) == 224`, and a
future reader can verify the list by counting it.

**8.18 — Reported and NOT fixed: `ear`→/ɪr/, and the rest of the unalignable
high-frequency set.**
`ear` as an r-controlled unit is the next-strongest inventory candidate and was
measured: it moves the ratchet 44.62% -> **45.13%** (+0.51pt), breaks no
`NEVER_DECODABLE` word, and unlocks `near`, `year`, `dear`, `fear`. Not added.
Appendix A's r-controlled row is explicitly enumerated as five items ("ar or er ir
ur"), the eval's demand is 2 instances of `near`, and 0.51 points for four words is
four times `wh`'s price per word. It is a defensible future change with the
measurement already taken; it is not one this audit's evidence compels.

Left alone with reasons, from `docs/EVAL.md` section 9's unalignable set:
`around` (rank 165) fails on a CMUdict artifact — it is transcribed `ER0 AW1 N D`,
so the `a` spells /ɚ/, a correspondence nothing teaches; `old` and `gold` are the
`o|OW` family 8.12 proved the ratchet rejects; `quiet` (2,267), `hi` (1,079),
`knew` (635) and `friends` (355) are all low-frequency with 1-2 instances of
demand, and `friend` is already in `NEVER_DECODABLE` by name. `come` appears in the
list at stage 5 while being a sight word from stage 6, which is the list working
correctly and not a defect.

