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
