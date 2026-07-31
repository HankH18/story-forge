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


---

## Phase 8 — the warm pool's coverage (`docs/AUDIT-PHASE7.md` F4)

Numbered 8.40-8.49 to stay clear of every block above. Logged **before** the
changes, per §0. Every claim marked **PROVED** was measured in this repo with the
command shown; everything else is reasoning and is labelled **ASSUMED**.

The defect, restated so the fix can be judged against it: `warm.py:1413-1414`
defaults `--stages 1,2,3 --themes dragons`, the `warm` Makefile target passes no
arguments, and `docs/DEPLOY.md:127` hands a first-deploy operator exactly
`python -m storyforge.warm` under the description "pre-generate the story pool
(§7)". With `TARGET_PER_PAIR = 3` that is 9 stories over 3 of 12 stages and 1 of 3
themes, while `docs/STATE.md:15`, `warm.py`'s own module docstring and §7 itself
all say "≥3 per (stage, theme)". §17's demo item 3 is *dragons at stage 3* — the
one cell the default covers — so the demo passes on a deployment where nine stages
hold nothing, and the §7 ladder degrades to a lower stage silently and correctly,
which is precisely why nobody notices.

**8.40 — The default becomes §7's full matrix (`--stages 1..12 --themes all`),
rather than a second `warm-all` target, a required argument, or a doc note.**

Four options were on the table and the choice is against three of them, for
reasons that are about *who is holding the checklist*, not about elegance.

- **A second target (`make warm-all` / `--full`)** leaves the trap exactly where
  it is. The finding is not "the operator picked the wrong command"; it is that
  the command DEPLOY.md prints is the defective one. Adding a second target means
  the protection is again "DEPLOY.md names the right one", which is the doc
  discipline that already failed here — and DEPLOY.md §4 runs the module directly
  in a DigitalOcean console, where `make` does not exist, so a Makefile-only
  distinction is invisible on the deploy path.
- **Refusing to run without arguments** converts one silent failure into a
  guaranteed stumble for every existing caller, and — worse — it makes the
  operator *type a matrix*, which is an invitation to paste `--stages 1,2,3` from
  a stale example and be back where we started, now with the wrong matrix
  recorded in shell history as a deliberate choice.
- **A doc note** is what §18's floor-style guards exist to replace. A sentence
  asking the operator to be careful is not a control.

What the default should be is not really a judgement call: three documents and
the spec already assert the full matrix. The code was the outlier. Spelled
`1..12` / `all` — the same strings `storyforge/eval.py:77-78` uses — so the
documented line and the running line cannot drift apart in the way
`docs/EVAL.md` F10 describes for `--pricing-date`.

The cost objection is real and is answered by mechanism, not by hope: `pool_depth`
already makes the run fill only the shortfall (**PROVED** — 11 of the 36 pairs
are already at target and are skipped, so a re-run costs nothing for them), `--dry-run` (8.45) prices the run before it is paid for, and a developer
who wants the old cheap slice passes `--stages 1,2,3 --themes dragons`
explicitly, which is one line of habit and is now *visible* rather than implied.

**8.41 — The guard is a refusal in the module, not a step in the Makefile:
`python -m storyforge.warm --check-coverage` exits non-zero while any (stage,
theme) pair is below the target.**

§18's pattern (the vacuity ratchet, the stage-1 allowlist floor, `run_warm`'s own
non-zero exits) is a check that *refuses*. The placement is the load-bearing part:
DEPLOY.md §4 is executed in a DO web console with `cd /app/services/api` and bare
`python -m …` invocations, so a `make warm-check` target alone would not exist on
the machine where it matters. The Makefile target is added too, for local use, but
it is a wrapper over the module flag and not the guard itself.

Costs nothing and calls nothing: one grouped SQL query, no API client is
constructed. **PROVED** below.

**8.42 — Coverage counts `warm|complete` (servable); `pool_depth` keeps counting
`warm`. Two denominators, on purpose, and the difference is stated because a
single unexplained mismatch is how F1-shaped defects start.**

`pool_depth` (`warm.py:107-116`) counts `status='warm'` because it is a *refill*
policy — §7 asks for generate-ahead, so a story that a child has read
(`warm → complete`, `readings.py:903`) should be replaced. The coverage gate
counts `SERVABLE_STATUSES` (`api/stories.py:105`) because it answers a different
question: *can a child at this (stage, theme) be served their own stage and
theme?* `_SERVE_SQL` serves `warm|complete`, so servable is the child-facing
number and warm-only would fail the gate on a pool that is working perfectly —
e.g. stage 5 / dragons is 2 warm + 1 complete right now, which is 3 servable.

Consequence, stated rather than discovered later: coverage can pass while
`pool_depth` still wants to generate. That is the two policies doing their
separate jobs, not an inconsistency. The gate imports `SERVABLE_STATUSES` from
the endpoint rather than re-spelling the tuple, so a fourth status cannot make
the gate and the serving path disagree — the same import discipline
`eval.py:93` uses for `EVAL_STATUS`.

Not changed: `pool_depth`'s warm-only count means a pair holding 3 `complete` and
0 `warm` is regenerated in full on every run. That is pre-existing (Phase 3) and
is a spend question, not a correctness one; it is out of this change's scope and
is recorded here so it is not rediscovered as new.

**8.43 — `run_warm` now ends by printing the full-matrix coverage verdict, and a
run that COVERED the full matrix exits non-zero on a remaining gap. This closes a
hole that neither existing non-zero exit reaches.**

`_finish` (`warm.py:748-792`) has two failure categories: a story that landed
unenriched, and an `api_fail` cell that produced nothing. Neither fires when a
pair's generations all *succeeded and were then rejected* — gate or moderation.
Walk it: stage 1 makes 3, stage 6's 3 attempts are all rejected on content →
`made = 3`, so the `made == 0` check at `warm.py:882` does not fire; `failures`
is empty (a rejected story is never enriched); `api_failures` is empty. `_finish`
returns, `make warm` exits **0**, and stage 6 is empty. That is a second,
independent route to F4's outcome and it survives the default change in 8.40, so
the exit code has to key off the pool rather than off the run's own bookkeeping.

Why the report is over the FULL 12x3 matrix regardless of what was requested: a
run told `--stages 1,2,3` that reports only on stages 1-3 prints a clean table and
teaches the operator nothing, which is the entire failure mode. A sliced run
therefore prints the whole picture and the command that fills it, and exits **0** —
it did what it was asked. Only a full-matrix run turns a gap into a non-zero exit.

Deliberate asymmetry, and the reason it is safe: with 8.40's defaults the deploy
path IS the full-matrix path, so the deploy operator gets the refusal; and if
someone later re-narrows the defaults, 8.44's test goes red and
`--check-coverage` still refuses. Three guards, no single point.

The floor used is the run's own `--target`, not a separate constant: `-n 1`
means "I want 1 per pair" and the verdict honours it. `--check-coverage` uses the
`TARGET_PER_PAIR = 3` default, so the checklist step measures §7's floor.

**8.44 — The gate refuses to pass vacuously on an unseeded matrix, and a unit test
pins the defaults.**

A coverage check over zero (stage, theme) pairs finds zero gaps and exits 0. On a
fresh deploy — the exact scenario — `stages` and `themes` are empty until
`make seed` runs, so the naive gate would report a green pool on an empty database.
It now refuses unless `stages` is exactly `1..12` and at least one theme exists,
naming what it found. This is the same class of defect as DEVIATIONS 6.15's
blocklist (a count moving the wrong way as the only symptom) and it is checked
rather than reasoned about.

Second half: `tests/test_enrich.py` gains a test that the CLI's parsed defaults
are the full matrix. That is the guard that goes RED against the defective
default rather than merely green after the fix, per the gate on this change.

**8.45 — `--dry-run`: the plan and a spend projection with no API call, and the
per-story figure is measured rather than assumed.**

DEPLOY.md's warm step says "SLOW. Costs money." and gives the operator no way to
find out how much before committing. `--dry-run` prints per-pair have/need over
the requested slice, the total stories it would generate, and — only if the
database already holds priced stories — `avg(gen_meta->>'cost_usd')` over the
servable pool times that count, labelled an extrapolation from N observations.
With no history it says so instead of inventing a number; `_reprice` returning
`0.0` on a missing price (AUDIT-PHASE7 F13.4) is the shape being avoided.

Current observed figure, **PROVED**: 53 servable stories, mean recorded
`gen_meta.cost_usd` **$0.01991** (it was $0.01958 over 52 before this change's own
story landed, which is the extrapolation updating itself as intended). That number includes enrichment (DEVIATIONS
5.16's `_record_enrichment` folds it in) so it is the right multiplier for a warm
story, and it is an extrapolation across stages — stages 9-12 are longer and will
cost more, so the projection is a **floor**. Said in the output, not just here.

**8.46 — `--stages 1..12` / `--themes all` parsing is a second implementation, not
a shared one, and the duplication is deliberate.**

`eval.parse_stages` (`eval.py:133-159`) already does this. Sharing it would mean
either `warm` importing `eval` — backwards, since `eval.py:72` imports
`warm._split_segments`, so a module-level import is a cycle and a function-local
one drags the whole §14 harness into every `make warm` — or moving the parser to a
third module, which is an edit to `eval.py`, owned by a concurrent agent on this
branch. So `warm._parse_stages` is written fresh, accepts the same three forms
(`1..12`, `3,6`, `1..4,9..12`) plus `all`, and raises on input that would silently
yield a smaller matrix than the operator asked for — the same rule `eval.py:137`
states. **ASSUMED** and flagged for later: the two can now drift. The compensating
control is that `warm`'s copy is bounded (it rejects anything outside `1..12`,
which `eval`'s does not), so the failure direction is refusal rather than a quiet
partial run.

**8.47 — What was actually spent, and what is therefore NOT proved.**

Spend on this change: **$0.0374**, one story. `--stages 6 --themes dragons -n 1`
against the live API — one (stage, theme) pair that had never held a warm story.
No `make warm` over the matrix, no `make eval`. The full 12x3 fill was **not** run.

That single story is the one thing a dry run could not establish, and it is worth
the four cents: **stages 6-12 had never produced a warm story in this repo's
history**, so "the new default will fill them" rested on the pipeline working at a
stage nothing had exercised outside the eval harness. Story **6416**, stage 6 /
dragons: `status='warm'`, decodability **1.0000**, 2 questions, 7 tagged segments,
$0.037428 recorded in `gen_meta.cost_usd`. First draft, no rewrite, no api_fail.

**PROVED** (everything below reproducible; only the item above cost anything):

- `--check-coverage` is RED against the pool as it stands: **23 of 36 pairs below
  3, exit code 1**, stages 6-12 at zero. A live sabotage-test of the guard against
  the *actual* state F4 describes, not a planted fixture. `make warm-check` exits
  non-zero through make as well.
- Sabotage-tested in three independent directions, each RED before green:
  restoring `DEFAULT_STAGES = "1,2,3"` / `DEFAULT_THEMES = "dragons"` fails the
  three default tests; neutering `assert_matrix_seeded` fails the vacuous-pass
  test; removing the pool-read exit category fails the full-matrix test — and that
  last sabotage run printed `99 warm, 9 rejected, 0 api_fail` **and exited 0 with a
  pair still empty**, which is F4's mechanism reproduced on demand.
- `--dry-run` prints the plan and the projection — **66 stories, ~$1.31** now,
  67 before the slice below — with no Anthropic client constructed at all
  (verified by spying on `AsyncAnthropic.__init__`: 0 constructions), and it runs
  to completion with `ANTHROPIC_API_KEY` blank. Same for `--check-coverage`, which
  exits 1 on the gate rather than on a missing credential.
- The §7 ladder still never climbs (STATE invariant 6), re-verified against
  `_SERVE_SQL` now that a stage-6 story exists and is the pool's ceiling: requests
  at stages 1/3/5 are unchanged, stage 6 gets the stage-6 story, and stages 8 and
  12 fall back to **6** rather than climbing. F4 observed stage 12 falling back to
  stage 5; it is now one rung closer for the same reason the fix exists.
- The suite: **672 passed / 2 skipped** before, **686 passed / 2 skipped** after —
  672 + the 14 new tests, nothing displaced.

On that last number: the shared development database made the suite look red twice,
with the failures *moving between runs* (`test_eval.py` x7, then `test_readings`,
then `test_auth`) while a concurrent agent wrote to the same Postgres. Diagnosed
rather than re-run until green: the eval failures were that agent's in-progress
`eval.py` (reproduced absent in a `git worktree` at HEAD carrying only this
change), and the rest were cross-process races of the kind `test_enrich.py`'s own
comments already describe. The 686/2 figure is from a **private `pg_dump` copy of
the database** (`sfwarm`, since dropped) with no other writer — which is the only
configuration in which this suite's result means anything right now.

**NOT proved, and the honest consequence:** that a real full-matrix
`python -m storyforge.warm` fills all 36 pairs. One stage-6 story is evidence about
stage 6, not about stages 7-12; AUDIT-PHASE7 F8 measured an 18.3% `api_fail` rate
at stages 9-12 in the §14 run — since addressed by the 240s timeout (8.7) but never
re-measured on the warm path. So the *mechanism* is proved and the *outcome* is
not. That is exactly what the new non-zero exit is for: the first real run will say
so rather than exit 0 on a partial pool.

**8.48 — `docs/STATE.md:15` is CORRECTED, not made true. The live pool is 13 of 36
pairs, and the doc now says so.**

The claim "`make warm` fills ≥3 per (stage,theme)" was false about the command and
is now true about it (8.40) — but it was also read as a claim about the *database*,
and that is still false and is not being fixed with money this change does not have
permission to spend. **PROVED**, after the one story above: **13 of 36** pairs hold
≥3 servable stories, **53** servable in total (mean $0.01991 each), stages **7-12**
hold none, stage 6 holds one. STATE.md now states the command's matrix, the live
coverage, and the fact that `make warm-check` exits non-zero on it *today*. A green
claim next to a red gate would be the fourth "metric computed and discarded" entry
in this file.

Same for `DEPLOY.md`: the warm step gains the mandatory `--check-coverage` line
and the note that the default is now the full matrix and is therefore the
expensive one.

**8.49 — DEPLOY.md's Clerk trap (6.13) was already documented; what was missing was
the DEPLOY.md §6 verification, and that is F5's, not this change's.**

Checked rather than assumed: `docs/DEPLOY.md:141-155` §5 already carries the trap
in full — every `require_admin` route 403s for everyone, the default session token
carries no `public_metadata`, the dashboard JWT-template edit is the only fix, and
6.13's "no Backend API for it" is stated as "not settable through the Clerk API".
6.13's own "**DEPLOY NOTE: … `docs/DEPLOY.md` must say so**" is discharged. One
gap closed while verifying: §5 cited 6.20 only, which is the *403 message*
deviation; 6.13 is the one holding the empirical finding, so the citation now
names both, and the invisibility is stated in the terms the audit uses — no error,
no log line, just missing permissions.
---

## Phase 8 — the §14 harness's PROVENANCE (audit F1, F2, F3, F6, F7)

`docs/AUDIT-PHASE7.md` established two things about §14. The numbers reproduce —
all 169 decodability figures, every one of the 180 per-item costs to under 2e-6 —
and the gate is the reason they are what they are. And nothing in the record pins
*what produced them*: no per-item model, no vocabulary version, a git commit at
which the harness does not exist, and a `--resume` path that takes its behaviour
from the environment while overwriting the record of it with the stored values.
The evidence is sound; its durability is not, and `docs/EVAL.md` asserted a
durability guarantee that did not exist.

These ten entries are logged before the change. They touch `storyforge/eval.py`,
`scripts/eval_doc.py`, `docs/EVAL.md` and the `eval` Makefile target, plus one
named constant in `llm/client.py`. **No measurement is rewritten**: run 2's
`eval_run_items.metrics` rows are untouched, `stories.decodability` is untouched,
the gate constant is untouched, and §1.6 stays recorded as unmet and unreachable.
What changes is what the *next* run records about itself, and what the report is
allowed to claim.

**8.30 — Every eval item records the model each API call was actually made with,
and the price sheet applied to it.**
The aggravating half of F1 is that `SELECT count(*) FROM eval_run_items WHERE
metrics ? 'model'` was **0**, so "this matrix ran on `claude-sonnet-5`" was
unfalsifiable after the fact — while `gate` and `pricing_date` were already
per-item and therefore auditable. The model is not read from the environment at
report time. `Usage.model` is already carried on every `Attempt` and on the
moderation result (`llm/pricing.py:56`, set by `usage_from_response` in
`pipeline._call`), so the item records the model each *call* was made with, as a
sorted list — a mid-matrix model switch appears as two entries in one item or as
two distinct values across items, and the report says so out loud instead of
printing one model name over a mixed matrix.

`models.requested` is recorded beside it, because an `api_fail` item has no
response to read a model off and its model is still a fact worth keeping. The two
fields answer different questions ("what did we ask for" versus "what answered"),
and conflating them is how F1 happened.

**8.31 — `--resume` enforces the stored config by REFUSING to continue when the
live config differs, rather than by claiming to and not.**
F1's mechanism: `eval.py:1490-1502` did `config = {**resumed_config, ...}`, which
makes the stored config win the **record** and the environment win the
**behaviour**. Two ways out. Genuinely enforce — thread a model parameter from
`config` through `run_pipeline` → `_call` → `generation_model()`, and likewise for
`DECODABILITY_GATE`, `MAX_REWRITES` and `PROMPT_WORD_LIMIT`, all of which are
module constants. Or refuse. **Refusal is what ships**, for three reasons: a
model parameter on the generation path is a change to the serving pipeline in
order to fix a reporting defect; a *gate* parameter is the one thing §0 forbids
adding, because a gate that can be passed in is a gate that can be passed a lower
number; and a refusal is verifiable from outside the process, which "we plumbed it
through correctly" is not.

So on a resume the harness now splits the config into three kinds of value and
treats each honestly:

- **Restored** — real function arguments, so restoring them *is* enforcement:
  `stages_spec`, `themes_spec`, the RESOLVED `themes` list, `n`, `effort`,
  `pricing_date`, `cold`, `enrich`.
- **Verified** — environment reads and module constants that no parameter can
  carry: `models.generation`, `models.fast`, `prices`, `gate`, `max_rewrites`,
  `max_story_words`, `prompt_word_limit`, `generation_timeout_s`,
  `sdk_max_retries`, the vocabulary fingerprint (8.33), and `concurrency`
  whenever cells remain. A difference on any of them **refuses the resume with
  exit code 3** and prints the stored value, the live value and the environment
  variable that would restore it.
- **Per-sitting** — `concurrency` and `budget_usd` when the run is already
  complete, recorded in the resume trail entry rather than pretended to be the
  run's. `config.concurrency` used to record the stored `1` while
  `eval.py:1538` built the semaphore from the CLI value (audit S1), which is the
  same defect in the same place.

Resolving `themes` from the stored LIST rather than re-resolving `"all"` closes
audit S2: a theme seeded mid-matrix can no longer widen `cells_planned` under a
config that records the old one.

A value the stored run never recorded cannot be checked, and that is a fact about
the older run rather than a difference — treating it as drift would make every run
written before this entry unresumable. It is disclosed instead, and the live value
is recorded on the TRAIL ENTRY rather than written into the run's config, where it
would imply the earlier sitting had used it too. So resuming a pre-8.31 matrix
still pins what the NEW half ran against without back-dating a fingerprint onto
the old half.

**A refusal cannot be the only path**, because re-deriving the report from a
complete run is a legitimate, zero-API operation and it is how run 2's report was
regenerated twice. When nothing remains to run, a drift is **disclosed instead of
refused**: the trail entry records it and the report opens with a DRIFT block
naming every key that moved. That is the distinction the audit's reproduction was
missing — resuming complete run 2 under `ANTHROPIC_MODEL_GENERATE=claude-opus-4-8`
now either refuses (if work remained) or prints, at the top of the report, that
the live generation model is not the one this matrix was measured with.

**8.32 — Provenance is a commit, a dirty-tree flag, and a content hash of the code
that ran.**
F2: `config.git_commit` recorded `559f102`, at which `eval.py` is 102 lines with
only a `smoke` subparser — the recorded commit **cannot run the documented
command**. Every number in §14 was produced by uncommitted code and nothing in the
record said so. `_git_commit()` asked `git rev-parse` and never looked at the
working tree.

`_provenance()` now records `git_commit`, `git_dirty` (from `git status
--porcelain`), `git_dirty_paths` (sorted, capped, so a reader can see whether
`eval.py` ITSELF was dirty — which is the case that matters), and
`code_sha256`: a SHA-256 per file over `storyforge/eval.py`,
`storyforge/pipeline.py`, `storyforge/llm/` and `storyforge/decoder/`, plus one
combined digest. A reader can now settle the question the audit had to settle by
hand: `git show <commit>:services/api/storyforge/eval.py | shasum -a 256` either
matches the recorded hash or it does not. The report prints the dirty flag next to
the commit, in the same cell, so the commit can never again be read as provenance
it is not.

`git_commit` is kept as a top-level config key rather than moved under
`provenance`, so `scripts/eval_doc.py` and any future admin view keep reading it.

**8.33 — `eval_runs.config` pins the vocabulary the run was measured against.**
F3 is the one that already bit. At 00:44 all 169 decodability values reproduced;
at 00:58, after a concurrent agent re-seeded `sight_words` 33 → 38 and added the
`wh` GPC, 11 no longer did and `tests/test_eval.py::test_the_gate_still_agrees
_with_what_the_harness_recorded` went red. `stories.decodability` is a *cache* of
the gate and was repaired in place (8.19, `scripts/rescore_stored_stories.py`),
but an eval RUN is a **measurement**, and a measurement that does not name its
instrument cannot be compared to a later one.

`config.vocabulary` now carries: the GPC count and a SHA-256 of the inventory as
the decoder loaded it (`load_gpcs()`, not the CSV bytes — the symlink of STATE
invariant 8 means the file path is not the artifact); the `sight_words` row count
and a hash over the sorted `(word, stage_id)` pairs; the `allowed_words` row count
and per-stage counts hash; and the CMUdict entry count, because §18 asks for the
snapshot to be vendored so the proof is reproducible and the entry count is the
cheapest check that the vendored snapshot is the one that ran.

A hash is enough for the goal, which is that a later reader can tell whether a
re-derivation is comparable — not to reconstruct the vocabulary from the record.
On resume the fingerprint is one of the verified values (8.31).

**What this does NOT do:** it does not make
`test_the_gate_still_agrees_with_what_the_harness_recorded` vocabulary-scoped. The
audit suggested scoping the assertion to the recorded vocabulary; that assertion
is about `stories.decodability` being a live cache of the gate, and it SHOULD go
red when the vocabulary moves — that is how the drift was caught, and the repair
was to rescore the cache, not to narrow the test. Scoping it would silence a
working alarm to remove an inconvenience. Left alone deliberately.

**8.34 — The decoder-limitation rates get the denominator they were always about:
SCORED words.**
F6, and it is the finding with the most consequence for §18. `story_metrics`
divided `heteronym_flag`, `alignable=False`, `schwa_relaxed` and the ambiguity
count by `n_words` — **all** non-punctuation tokens, including sight and story
words. A sight or story word is classified before any alignment is attempted
(`decoder/validate.py:33-36`) and can carry none of these flags, so it inflated
the denominator and nothing else. Over the population that can actually carry a
flag — the scored set, which is also the decodability denominator — run 2's
figures are:

| flag | total | reported | over SCORED words |
|---|---|---|---|
| `ambiguity_flag` | 1272 | 12.4% | **22.7%** |
| `schwa_relaxed` | 443 | 4.0% | **7.9%** |
| `heteronym_flag` | 377 | 3.1% | **6.7%** |
| `alignable=False` | 28 | 0.19% | **0.50%** |

§18 names a rising unalignable rate as the signal that the inventory is too small.
Reporting it at 0.19% instead of 0.50% halves the alarm, which is the wrong
direction for the one number the spec designates as a tripwire. The item fields
are renamed `*_rate_scored` so the name carries the denominator, and the aggregate
now reports the **population** rate (total flags / total scored words) beside the
mean per story, with the denominator named in the table header. The population
rate is what the audit computed independently and is the figure the report leads
with; a mean of per-story rates weights a 30-word stage-1 story equally with a
200-word stage-12 one, which is defensible for "the typical story" and wrong for
"how often does this happen".

Because the aggregate derives these from the recorded *counts* rather than from
the recorded rate fields, it produces the corrected figures for run 2's existing
items too — without touching them.

Same entry, smaller sibling (audit F9): `length.compliance_rate` and `_slice`'s
`gate_pass_rate` / `length_compliance_rate` divide by all ATTEMPTED cells,
including the 11 that produced no text and therefore have `word_count = 0` and
`length_compliant = False`. The direction is unflattering, so nothing was
inflated — stage 9's "73.3% pass rate" is 11 of 15 attempted where 11 of 11
produced stories were accepted. `*_produced` variants are added alongside and both
denominators are named in the report. The primary figure does not move: a cell
that never produced text is a real failure and hiding it in a smaller denominator
would be the mirror of the defect above.

**8.35 — Generation latency is reported twice, because one number was measuring
two things.**
F7. `Attempt.latency_ms` is wall time around `_call`, and `_call` goes through an
SDK client with `max_retries=2`, so the row labelled "GENERATING (first attempt)"
measured up to **three** HTTP attempts. 23 of run 2's 169 stories burned at least
one dead attempt under the old 30s timeout; p95 over the 146 that did not is
**26.4s** against the **58.3s** reported. The report then attributed the §1.6 miss
to long stories while separately and correctly filing the timeout as the
availability defect — so one defect was priced twice, in a document whose own
preamble promises "neither reading can borrow the other's number".

The reporting defect is independent of the timeout value, so it is fixed
independently of 8.0-8.8's raise to 240s. Each item records
`per_call_timeout_s` and `sdk_max_retries` — the budget in force for *that*
measurement, not the constant in force at report time — and the aggregate reports:

- **wall clock** p50/p95/max over first attempts, labelled as including any
  SDK-internal retries, with the retry budget named; and
- the subset whose wall clock is **under the recorded per-call timeout**, so no
  *clock-driven* retry can be inside it, with its own n.

The second is deliberately named "no clock-driven retry" and not "single
attempt": a 429 or a 5xx retried quickly would still hide inside a fast call, so
the subset is a bound rather than a proof, and calling it a proof would be the
same overstatement this whole section exists to remove. Getting the exact count
means reading `x-stainless-retry-count` off the raw response, which needs
`with_raw_response` in `pipeline._call` and a new `Attempt` field — a change to
the generation path for a reporting gain, deferred and named here rather than
done quietly.

`llm/client.py` gains `SDK_MAX_RETRIES = 2` as a named constant used by
`get_client()`, so the retry budget the report discloses is the one the client
uses rather than a number re-typed in a second file. No behaviour change.

**8.36 — `_reprice` prices from the models the ITEMS recorded, and declines to
price rather than reporting $0.00.**
`_reprice` called `generation_model()` and `fast_model()`, so "the same run priced
after the intro rate ends" was a function of the current shell: the audit's Opus
resume priced run 2's tokens at $6.5718 while the report's config table said
Sonnet at $2/$10 — one file, two models, and the sentence beside it hard-coded the
string "$3/$15" while printing a number computed at neither rate. It now groups
items by the model each recorded (8.30) and prices each group with its own sheet,
falls back to the config's models only for items that recorded none (run 2's), and
the prose derives the price pair from the sheet it actually used instead of
spelling it out. F13.4: a `KeyError` from `price_for` returned `0.0`, which reports
an unpriced model as free. It now returns `None` and the report says the run could
not be repriced and names the model.

**8.37 — Three sentences deleted from the report and four made checkable.**
The report generated its own false claim: `render_report` emitted, into every
resumed report unconditionally, "`--resume` ran only the remainder against the
SAME stored config — same model, same gate, same pricing date." `docs/EVAL.md:13`
carried "enforced by the code rather than by care" and `:15` "the report is a pure
function of the data". None of the three was true, and a report that overstates
its own rigour is worse than one that admits a gap — it spends the credibility the
rest of the document earned.

Gone or rewritten: the resume sentence now enumerates which values were RESTORED,
which were VERIFIED against the live config, and which are per-sitting, and prints
the drift block when there is one. "A pure function of the data" becomes the
narrower true claim — re-finalising a complete run reproduced the identical
verdict and exit code, which checks that the VERDICT is a pure function of the
recorded items, and is silent about the config table, which it was never evidence
about. The report also now prints the distinct per-item models it saw, the
vocabulary fingerprint, the dirty-tree flag, and the denominator under every rate
it quotes.

**8.38 — `eval smoke` prints the moderation verdict.**
Handed over from the prompt work: a smoke run failed 3 of 4 on a story measuring
**100% decodable** and printed `FAIL 100.0%` with `violations: []` and no reason.
Smoke does not persist, so the cause was unrecoverable — the one run that is meant
to be a cheap CI-style tripwire produced a red with no diagnosis. `run_smoke` now
prints the moderation class, layer, blocked words and `r.moderation.reason`
verbatim on any failure, and its `SystemExit` message names the actual mix of
reasons instead of asserting "below gate" for a story that cleared the gate.

**8.39 — `docs/EVAL.md` is regenerated from run 2's own JSON, not hand-corrected,
and it now marks what that run did not record.**
Three claims in the committed §14 report are false or stale: the two provenance
sentences above, and `EVAL.md:243`'s "checked directly against the `sight_words`
table, **all 33 rows**" — the table holds 38 and `of` is a stage-2 sight word
since 8.10. The generator (`scripts/eval_doc.py`) already carried the corrected
`of` paragraph from the vocabulary work; the document was never re-emitted, which
is the same "computed and discarded on write" shape as the grammar defect, one
layer up.

So `docs/EVAL.md` is regenerated by running `scripts/eval_doc.py` over
`reports/eval-20260729T221359Z.json` — run 2's own output, unmodified — with the
corrected generator. The F6 and F9 figures are recomputed from run 2's recorded
counts; the F7 split is computed with the 30s timeout that run 2 ran under, stated
in the document as a value supplied from 8.0's evidence rather than read from a
record that does not have it; and every provenance field run 2 never recorded
prints as **not recorded** with a pointer to this section. A blank where the
record is blank is the point: it is the only way a reader can tell F1 and F2 apart
from a run that fixed them.

`make eval` also now prints the exact `scripts/eval_doc.py` line to regenerate
`docs/EVAL.md`. It does not run it — a failed matrix must not silently rewrite the
committed report — but "someone remembered" is what 8.39 is an entry about, so the
command is on screen rather than in a doc.

---

## Phase 8 — the three findings 7.38 deferred, and the §18 inversion

Numbered 8.60-8.69. `docs/DEVIATIONS.md` 7.38 named four things and did not do
them: C10 (kid mode's permanent printer), C11 (a revoked Clerk token works until
`exp`), C12 (`GET /api/themes` / `GET /api/stages` do not exist) and the §18
inversion. AUDIT-PHASE7 F11 then measured two live defeats of the §18 guard on the
real image. Three of the four are closed below. **C11 is closed as a written,
justified non-fix**, which is a different thing from being deferred again: 7.38
said "not touched, because `verify_token` is the most security-sensitive function
in the repo", which is a reason to be careful and not an answer to the question.
The answer is in 8.63.

**8.60 — `GET /api/themes` and `GET /api/stages` exist, in a new `api/catalog.py`,
public and cached exactly as §9 says.**
PROVED before the change: `storyforge/main.py` mounted `health`, `validate`,
`stories`, `kids`, `readings` and nothing else, so both routes 404'd on **every
kid-session load, twice per load under StrictMode**. The SPA absorbed it — both
getters go through `optional()` and fall back to a hand-copy of
`data/seed/{themes,stages}.csv` — so the only visible cost was noise in the
network panel, and the real cost was invisible. Because the route did not exist,
the fallback was not a fallback: **the cards a child saw were whatever the
TypeScript literal said, on every load, with the database having no vote.** Both
directions of drift then bite, and the audit's phrasing named only the softer one:

- a slug in `lib/api.ts` that the database does not have — a renamed theme, a
  removed one, a reseed — is a card that answers `POST /api/stories/request` with
  `404 unknown theme 'x' — run make seed`. A 404 is not retryable, so kid mode
  spends its three attempts and parks on the printer **forever**. That is C12
  feeding C10, and it is the sharp direction.
- a theme added to `themes.csv` and seeded but not typed into `lib/api.ts` is the
  softer one: no card, so no child can ever reach content that exists.

Both are gone for the same structural reason — the cards now come from the table
`_resolve_theme` reads. **Proved in a real browser rather than argued**, against an
auth-enforced API (`demo_bypass_auth: false`, `x-storyforge-auth: enforced`,
`garbage token → 401` confirmed first) with a real Clerk session and a private copy
of the database: a fourth theme (`unicorns`) was inserted with SQL and nothing
else, and the picker painted a fourth card — a card the TypeScript literal cannot
produce. Tapping it returned `POST /api/stories/request → 200` and
`POST /api/readings → 201` for a stage-1 reader, with the server logging "warm pool
empty for stage 1 / unicorns; served stage 1 / space instead". A brand-new theme is
safe to tap on the day it is seeded, which is the whole of C12.

The routes are five lines of SQL each against tables that already exist. Two
decisions in them are not obvious:

- **They are public — no `require_parent`.** §9 says `(public, cached)` and this
  is the one place the whole file makes an exception, alongside `/api/health`.
  What they expose is `data/seed/*.csv`, which is committed to the repository:
  twelve stage names and three theme names. There is no child in either payload
  (§13), so an auth requirement would buy nothing and would make the picker's
  first paint depend on Clerk having loaded.
- **`Cache-Control: public, max-age=300` on both**, because "cached" in §9 is a
  property of the response and not a comment about the server. The stage list
  changes when the scope and sequence changes, which is never; the theme list
  changes when `make seed` runs.

  Observed live, and worth naming because it is the one cost: after inserting a
  fourth theme, the browser kept painting three cards until the cached response
  expired. Adding a theme is therefore visible within five minutes, and
  **removing** one leaves a window of the same length in which a child can tap a
  card whose slug no longer resolves — a 404 that kid mode cannot retry. That
  window used to end on the permanent printer screen; 8.62 is what makes it end on
  a story instead, which is the two fixes in this section covering each other
  rather than either one being complete alone.

**8.61 — The seed-mirror fallback STAYS, and is pinned to the CSV by a test that
found a real drift on its first run.**
The obvious move once the routes exist is to delete `SEEDED_THEMES` /
`SEEDED_STAGES`. That would be wrong twice over. The fallback is not standing in
for a missing route — it is what makes `GET /api/themes` failing *not* a
child-facing failure, and the API being unreachable is exactly when a child is
already at risk of the printer screen. And the picker with an empty theme list is
a screen with nothing to tap, which is the stuck state §01 forbids, reached
through a path §07 has no copy for.

So the fallback is kept and its actual risk — silent drift from the seed — is
closed with the cheapest available check: `apps/web/src/lib/seeds.test.ts` parses
`data/seed/themes.csv` and `data/seed/stages.csv` and asserts the two TypeScript
constants equal them, field by field, including emoji and id order.

**It went red on the first run, which is why it is worth having.** PROVED drift:
`SEEDED_STAGES[0].description` read "Single letters s a t p i n m d c — simple
**three-letter** words" where `stages.csv` says "…simple
**consonant-vowel-consonant** words". Every other field of all 12 stages and all
3 themes matched. One wrong word on the add-kid form is a small harm; the point
is that nothing in the repository could tell that it was wrong, and the same
mechanism silently mis-renders a theme name or an emoji. The constant is
corrected to the CSV's text — the CSV is the seed the database was built from, so
it is the side that is right by definition.

The two constants are `export`ed for the test, and only for the test. That is a
real widening of the module's surface and it is the smaller cost: the alternative
is a test that reaches into the module's source text with a regex, which is a
test of the file's formatting.

**8.62 — Kid mode's permanent printer is bounded: the stall re-serves the last
story this reader was actually served, and a second stall hands the tablet to the
adult. `stage` still comes from the server, and no story is ever generated,
chosen or re-scored on the client.**
C10, PROVED by the audit against a 401-ing API: three attempts, then "Writing
your story… the printer is working / Try again", and "Try again" returns to the
picker, which re-enters the same loop indefinitely. §01 says a child never sees an
error state *and* never gets stuck; the screen satisfied the first clause and
violated the second, and it says the printer is working when nothing is working.
The audit's easiest route in is §9's limiter: 10 story requests per minute per
user, and an eleventh tap of "Read another" is ordinary behaviour for a
five-year-old who wants a different dragon.

7.38 declined this on the grounds that "fall back to the last successfully served
story is a change to what a child reads, which is §0/§1 territory". That is the
right instinct pointed at the wrong object. The story being re-served is not a
new artifact and is not chosen by the client: it is a story **the server picked
for this same reader** through `POST /api/stories/request`, which resolves the
stage from the `kids` row (7.21) and re-validates the pick against that reader's
own stage before returning it (`_is_decodable_for`, 7.23). Re-reading it later is
inside the guarantee, not outside it, for a reason §7 already relies on: the GPC
inventory and the sight-word list are cumulative (§4), so a story decodable at
stage N stays decodable at every stage above it (§5.7c monotonicity). A stale
cache can therefore only ever be *easier* than the reader's current stage — the
same direction the fallback ladder is allowed to move.

What is cached is one story per reader, keyed by kid id, in `localStorage`:

- **Keyed by kid id, never shared.** C9's lesson (7.33) is that a substituted
  reader is worse than a refusal, so a sibling's tablet cannot serve reader A's
  cache to reader B.
- **Seven-day expiry.** Not for decodability — that cannot expire upward — but
  because the pool can *retract* a story. The Phase 6 grammar backfill moved 4 of
  28 servable stories to `rejected` while they were being served (STATE.md), and
  a client cache with no clock would keep re-serving a retracted story to that
  child forever. Seven days bounds it to one story, for one reader, for one week.
- **The reading is still recorded through the server**, so the record is gated by
  the same two refusals as any other reading: `POST /api/readings` 404s a
  non-servable story and refuses a story above the kid's stage (7.22). If the
  cached story has been retracted, the child re-reads it once and no evidence is
  filed — the honest outcome, and it needs no client-side logic to arrange.

The stall path is now bounded rather than infinite: attempt (3 network tries) →
re-serve the cached story if there is one → otherwise the printer with **one**
"Try again" → and on a second stall, `navigate('/app')`. Handing over to the
adult surface is the same route `resolveKid` returning `null` already takes
(7.24): `/app` is a real screen that knows how to name the readers and how to add
one, `RequireParent` sends a signed-out visitor to sign-in, and the child sees a
grown-up's page rather than a lie about a printer. **Nothing in this path renders
an error string to a child**, which is the §01 line, and nothing waits forever,
which is the other half of it.

**Both rungs were driven in a real browser against the enforced API, with the 429
produced for real rather than mocked** — ten `POST /api/stories/request` calls with
the session's own token, so the eleventh was the child's tap:

```
kid 1648, one story already read (cache present):
  tap → 429, 429, 429 → the screen shows "Sam sat in a rocket." (story 1663)
       → POST /api/readings 201, reading 12496, kid 1648, story stage 1
       → console: "[kid] request failed; re-serving the last story 1663"
kid 2622, nothing ever read (no cache):
  tap → 429, 429, 429 → "Writing your story… the printer is working  Try again"
  Try again → tap → 429, 429, 429 → url becomes /app, the parent home renders
```

No error string appeared on either path, and the second one terminates. Before this
change the first path was the printer forever and the second was the printer
forever.

What this does NOT fix: the 429 itself. Its root cause is that a (stage, theme)
pair holding few stories round-robins the same story back, so a child hunting for
a *different* dragon spends the limiter (6.7). That is `warm.py` and the pool,
both outside this job — and live coverage has already moved a long way (see
8.69).

**8.63 — C11 is ACCEPTED and documented, not fixed: a revoked Clerk session's
token stays valid until `exp`, ≤60 s for every token a browser holds. The
docstring sentence that invited the wrong generalisation is the thing that
changes.**
PROVED by the audit: `GET /api/kids` with token T → 200; revoke the session
through the Backend API → `status: revoked`; the same T → 200 for the rest of its
life. `verify_token` checks signature, `exp`, `nbf`, `iss` and optionally `aud`,
and never asks Clerk anything.

**Not fixed, on purpose, and here is the arithmetic rather than the instinct.**

1. **The window is the token's remaining lifetime, and for a browser that is
   ≤60 s.** Measured, in this same audit's clean section: Clerk's browser session
   token has `exp - iat = 60`, and `getToken()` transparently re-mints ~7 s
   before expiry. After a sign-out or a server-side revocation the re-mint fails,
   so the exposure is bounded by whatever is left of one minute. The audit's
   30-minute observation came from a **Backend-API-minted 1800 s token**, which
   is a shape no browser in this product produces and which only an operator
   holding `CLERK_SECRET_KEY` can create.
2. **Checking `sts != "active"` does not close it.** The claim is present and is
   **frozen at mint time** — the audit says so explicitly ("present, and frozen
   at `"active"` from mint time"). A revoked session's already-issued token still
   reads `active`, so the check would refuse a different population (Clerk's
   `pending` sessions, i.e. a session with unfinished tasks) and close nothing at
   all. It would also be a new way for auth to refuse a legitimate parent if
   Clerk ever emits a value this code has not seen, on a path a child's session
   depends on. Rejected as machinery that looks like the fix and is not.
3. **A per-request Clerk call is worse than the window.** It puts a network round
   trip and a third-party dependency on every authenticated request, including
   every request in a reading session, and it converts a Clerk blip from "JWKS is
   cached, nobody notices" (7.34's stale-key window) into a total outage. §9's
   design is stateless verification over JWKS precisely so that Clerk being slow
   is not this product being down.
4. **A maximum-token-lifetime cap was considered and rejected.** Refusing any
   token with `exp - iat` above, say, 300 s would bound the window for *every*
   token including the operator-minted ones. It is one line. It is also a
   guarantee about a value the operator can change in the Clerk dashboard: raise
   the session token lifetime there and this API refuses every request in the
   product, with a 401 that names nothing an operator would connect to a
   dashboard setting. Auth failing closed is correct; auth failing closed on a
   configuration change nobody deployed is an outage.

What the window actually costs: for ≤60 s after sign-out, a token already
captured off the wire or off a device reaches **that same parent's own family
data**. It grants no role it did not have (roles are re-read from the token every
request, so a revoked *admin* is also bounded by the same ≤60 s) and it reaches
no other family (ownership is a `kids.parent_id` filter, 6.9/6.10). This is the
ordinary stateless-JWT trade and it is the one §9 chose.

The code change is one paragraph in `auth.py`'s module docstring. The audit's real
point was never the 60 s: it was that the docstring makes an adjacent claim which
*is* true — "revoking admin takes effect on the next call rather than at the next
login" — and a reader generalises it to session revocation, where it is false. The
docstring now states both, with the bound and with the reason a `sts` check is not
the answer, so the next reader does not have to re-derive this.

**8.64 — §18: a platform variable that is PRESENT is a signal, whatever its
value. `docker run -e PORT=` no longer boots a bypassed API.**
AUDIT-PHASE7 F11, defeat 1, PROVED against the real built image:
`docker run -e DEMO_BYPASS_AUTH=true -e PORT= -e DATABASE_URL=…localhost… ` stayed
up serving a **fully unauthenticated API** and had to be killed. The mechanism is
one line: `deployment_signals` read `(os.environ.get(name) or "").strip()` and
appended a signal only when the result was truthy, so *blanking* the variable
removed the only signal the image supplies to itself (the Dockerfile's own
`ENV PORT=8080`) while `--port ${PORT:-8080}` served happily on 8080.

`PLATFORM_VARS` and `SERVING_HINT_VARS` now test **presence** —
`name in os.environ` — and an empty value gets its own sentence in the refusal
("present but blank"), because blanking a platform variable is a stronger signal
than setting it: no platform sets `PORT=`, and no laptop has a reason to. The
value-based test stays for `ENV_NAME_VARS`, where an empty value really is
"unconfigured" rather than "someone emptied this".

Sabotage-checked, which is the only reason these tests are worth anything: the new
cases were written first and run against the unchanged guard, where **14 of them
failed** — 4 blank-platform-variable cases, 7 environment-value spellings and 3
container cases — while the laptop canary and every false-positive case passed, so
the corpus is not simply asserting the new implementation back to itself. Re-run
against the **built image** after the fix, the audit's own command
(`docker run -e DEMO_BYPASS_AUTH=true -e PORT= -e DATABASE_URL=…localhost…`) now
exits with `RuntimeError: … (PORT is present but BLANK — a platform variable was
emptied. Unset it entirely to run the §18 bypass locally.; /.dockerenv exists …)`.

**8.65 — §18: `ENVIRONMENT=prd` and `ENVIRONMENT=pre-production` are refused, and
the family is closed by a rule that does not depend on someone having thought of
the spelling.**
F11, defeats 2 and 3, PROVED in-process: both booted bypassed, and
`auth.py:494-498`'s comment said the prefixes closed the family after `prod-us`.
They did not — `DEPLOYED_ENV_PREFIXES` is a `str.startswith` test, so `prd` is not
a prefix of anything in it and `pre-production` starts with `pre`.

`is_deployed_env_value` now also looks for deployment MARKERS anywhere in the
normalised value: `prod`, `prd`, `stag`, `stg`, `live`, `preview`, `uat`,
`canary`. Substring rather than prefix, because the family is spelled every way a
team has ever spelled it (`preprod`, `pre-production`, `k8s-prod`, `eu-stg`) and
a prefix list only ever catches the spellings someone thought of. The
false-positive direction is checked in the same test as before and in both
directions: `dev`, `development`, `local`, `test`, `ci`, `sandbox`, `demo`,
`laptop`, `""` and `"   "` all still resolve to "not deployed", and none of them
contains any marker. `tests/conftest.py`'s duplicate of this rule — which exists
because importing `storyforge.auth` is what runs the guard — is extended in step
and still cross-references it.

**8.66 — §18: being in a CONTAINER is a signal, and it is the first signal in this
guard that no `-e` can blank. The inverse control the audit asked for, scoped to
the one place it pays for itself.**
This is the substantive half of "the §18 inversion". 7.36 recorded the honest
criticism of its own design — "this list is still ~30 negative signals and a bare
`docker run` on a public host sets none of them" — and F11's defeat 1 is exactly
that: the operator does not need to know which 30 variables to avoid, only that
blanking one gets them past it.

`deployment_signals` now also reports containerisation, read off the filesystem
rather than the environment: `/.dockerenv`, `/run/.containerenv` (podman), and a
`docker`/`containerd`/`kubepods`/`lxc`/`podman` marker in `/proc/1/cgroup`. None
of those can be unset by a `docker run` flag, and on macOS — where `make dev` and
this suite run — none of them exists, so the laptop path is untouched. §18's own
words are "a laptop-only fallback"; a container is not a laptop.

**There is exactly one way past it, and it is a positive assertion rather than the
absence of a signal:** `STORYFORGE_BYPASS_IN_CONTAINER=i-understand-this-disables-auth`,
spelled in full, cancels the container signal **and nothing else** — every other
signal still refuses, so this is not a master key. It exists because the failure
mode 7.36 was worried about is real and asymmetric: a guard that blocks a
legitimate local workflow gets deleted, and then nothing is guarded. The
workflows that need it are a dev container and a containerised CI runner, which
would otherwise fail every test at import with a message about production. A
careless operator putting the demo on a VPS will not set a variable they have
never heard of; a developer hitting the refusal reads its message, which names
the variable and says plainly what it turns off.

Verified against the **built image**, four ways, because a guard tested only
in-process is a guard tested somewhere it will never run:

```
-e PORT= -e DATABASE_URL=…localhost…                      REFUSED (blank PORT + container)
PORT unset entirely, localhost DSN, nothing else set      REFUSED (container alone)
-e ENVIRONMENT=prd                                        REFUSED (env value + PORT + container)
override + PORT unset + localhost DSN                     BOOTS — the dev-container escape works
override + ENVIRONMENT=production                         REFUSED (it is not a master key)
override + remote DigitalOcean DB host                    REFUSED (nor is it a key to that)
```

**What is still NOT done, and why:** the bind-address half of the inversion
(refuse when the process is listening on `0.0.0.0`) and the general positive
laptop assertion. The bind address is not knowable at import — the guard runs
when `storyforge.auth` is imported, before uvicorn has a socket, and reading it
back out of `sys.argv` is a guess about how the process was started rather than a
control. Making the laptop assertion mandatory for the *whole* guard still
changes §18's contract from one flag to two during exactly the emergency the flag
exists for (7.36's reason, unchanged and still correct), and `tests/conftest.py`
turns the bypass on for the entire suite. The container signal was chosen instead
because it converts the one measured defeat into a refusal without touching the
contract for anyone on a laptop.

**8.67 — `make dev` was re-verified after the guard change, because a guard that
blocks local development is worse than the gap it closes.**
Not a deviation so much as the evidence for one. The §18 changes above are
refusals that run at import, so the failure mode is "the API will not start on a
developer's machine" and it would be discovered by the next person, not by me.
Verified live after the change, in this order:

```
deployment_signals(<the .env DSN>) on this host        -> []
DEMO_BYPASS_AUTH=true python -c "import storyforge.auth"
                                                      -> imports, logs the §18 banner
DEMO_BYPASS_AUTH=true ENVIRONMENT=development …        -> imports (a laptop word stays a laptop word)
tests/test_auth.py (149 tests, bypass on for all)      -> green
make dev                                               -> Postgres ready, uvicorn :8000, Vite :5173
  GET :8000/api/health                                 -> {"status":"ok","demo_bypass_auth":false}
  GET :5173/api/themes  (through the Vite proxy)       -> the four-field theme list
  GET :5173/                                           -> 200
```

`conftest.py` also gained a `PORT` scrub, for the same reason it already scrubs
`ENVIRONMENT=production`-ish values: `PORT` is now a signal by presence, so a
developer or CI runner with it exported for something unrelated would fail the
whole suite at import rather than in the one test that is about the guard. The
tests that ARE about it set it themselves.

**8.68 — `GET /api/themes` reports how many servable stories each theme holds,
and the picker deliberately does not filter on it.**
The question the route raises is whether it should return only themes the pool
can actually serve — a theme that resolves but has no stories looks like a dead
end for a child. Answered with the ladder in hand: it is **not** a dead end.
`_SERVE_SQL` orders by `stage_id DESC, (theme_id = :theme) DESC`, so an empty
(stage, theme) cell is answered with a story at a lower stage, or in another
theme, or both, and `fallback.kind` says which — never a 404 and never an empty
result. The only theme that dead-ends a child is one the `themes` table does not
have, and a route that reads that table cannot return one. That is the whole of
C12's fix.

Filtering would also *create* the failure it aims to prevent. §07 fixes the
picker at 3-6 cards; a filter that emptied the list would leave a child a screen
with nothing to tap, which is worse than being handed a dragon story after asking
for a rocket, and which §07 has no copy for.

So the depth is **reported, not enforced**: each theme row carries
`servable_stories`, counted with `SERVABLE_STATUSES` imported from the serving
endpoint rather than re-spelled (the discipline `warm.py`'s coverage gate already
uses, 8.42), and the SPA's `Theme` type carries it as an optional field that no
component reads. It is there so that "this theme is offered" and "this theme has
content" can be compared in one request by an operator or by the Phase 8 admin
portal — which is the drift C12 was actually about.

It is deliberately an **upper bound** and the docstring says so. `status` is the
half of servability that lives on the row's status column, and the serving path may
refuse an individual row for its own reasons (the serve-time decodability re-check
of 7.23, and any per-row predicate added since). Importing the whole predicate
would couple a cosmetic field to the internals of the serving statement; copying it
would be a second answer waiting to drift. High by a row is the right direction for
a number whose only job is to make a mismatch visible.

**8.69 — Live pool coverage, measured twice while closing C12, is 34-36 of 36
pairs at full depth and MOVING. `docs/STATE.md`'s pool row is stale in the OTHER
direction.**
Recorded because the C12 and C10 reasoning both depend on it, and because a claim
in the state doc is now pessimistic rather than optimistic — the rarer and less
dangerous direction, but still a doc that does not describe the database. Measured
directly against the live pool, not through `make warm-check` (this job may not
run it), at the start and the end of this work:

```
first  5 of 36 pairs hold fewer than 3 servable stories, every one of them 2
       (s6/space, s7/animals, s8/dragons, s9/animals, s11/dragons); none holds 0.
       stories: 106 warm, 7 complete, 19 rejected, 193 eval.
~2h later, same query:
       2 of 36 pairs short, shallowest still 2, none 0.
       stories: 110 warm, 7 complete, 20 rejected, 213 eval.
```

Two things follow. **The number in this entry is a timestamp, not a fact** — a
concurrent agent is filling the pool, so any coverage figure written down here is
a reading and not a state, and `make warm-check` is the only way to ask the
question at the moment it matters. And **no (stage, theme) pair is empty in either
reading**, which is what the C10 argument leans on: with no empty cell there is no
404-shaped route to the printer left, so §9's 10/min limiter is the only path a
child can reach it by, which is exactly the one 8.62 bounds.

`docs/STATE.md:16` still says "13 of 36 … stages 7-12 hold none, stage 6 holds one
dragons story". That was true when it was written. The row is left for the agent
that owns `warm.py` and the run that moved it, rather than edited from here while
that work is in flight.

---

## Phase 8 — the two defects a live 60-story warm run measured

Numbered 8.70-8.79. A full `make warm` produced 60 stories with 9 rejects (15%).
Six were examined and **five are false**, which is the whole basis of this section:
the numbers below are counts off `gen_meta->'moderation'` in the live pool, not
projections. The sixth (story 7268, "The dragon did nip a cat") is a **correct**
reject and is left working — it is also the single most useful piece of evidence
here, because layer 3 caught deliberate harm with no blocklist word anywhere in
the story.

| story | stage | half that fired | verbatim |
|---|---|---|---|
| 7290 | 6 | `well_formed=false` | `"The rocket did not stop."` — "stop should be replaced with a past participle" |
| 7317 | 9 | `well_formed=false` | `"It can hear the wind outside."` should be `"It could hear the wind outside."` "to match past tense" |
| 7292 | 7 | blocklist | `hurt` — in *"The girl did not want to hurt the rabbit."* |
| 7304 | 8 | blocklist | `hurt` — in *"The dragon did not want to hurt them."* |
| 7345 | 11 | blocklist | `hurt` — in *"The dragon did not want to hurt things."* |
| 7268 | 1 | content | *"The dragon did nip a cat"* — CORRECT, left working |

**8.70 — The grammar judge was making a grammar error of its own: it wanted a PAST
form after an auxiliary. The fix is a second mechanical test, not another
prohibition.**
`"The rocket did not stop."` is correct English — after the auxiliary *did* the
bare infinitive is required, and the judge's own suggested correction ("a past
participle") produces `"The rocket did not stopped."`, which is wrong. 8.20's
noun-substitution test cannot catch this: substituting nouns gives *"The dog did
not stop."*, which is fine, so the test says "approve" and the judge flagged
anyway.

The reason it flagged is visible in the prompt. `did not` appears exactly ONCE in
`MODERATION_SYSTEM`, as the header example of the **reject** list — *"Wrong verb
form after a helper: `He did not came back.` (came -> come)"* — while every
carve-out example of the same construction is affirmative (*"It did nap."*, *"Sam
did stand."*). A judge pattern-matching on `did not` therefore had one exemplar and
it was a rejection. The two false positives are the same root cause read twice:
in both, the judge tried to force a past form onto a sentence that already had the
right one.

So the fix names the actual diagnostic instead of adding a ninth prohibition, in
the shape 8.20 established (a test the judge RUNS, before it is allowed to flag).
The **HELPER TEST**: after `did`/`do`/`does`/`can`/`will`/`may`/`must`, with or
without `not`, the next verb must be the PLAIN form; the plain form is correct and
a PAST form is the error, so the only question is whether that verb is a past
form. It is stated with four passing and four failing examples side by side, and
with an explicit floor — never propose a correction that turns a plain form after
a helper INTO a past form or an `-ed`/`-en` participle, because "did not stopped",
"can heard" and "did napped" are all wrong English and producing one means the
sentence was already right.

**This is a sharpening, not a relaxation, and the trap is the point.** The same
construction appears on both sides: `"He did not came back."` — the sentence that
shipped to a child and that DEVIATIONS 4.12 exists because of — must still FAIL,
and `"The rocket did not stop."` must PASS. A fix that buys one by breaking the
other is worthless, so the fixture now holds four auxiliary rejects rather than
one (8.75).

**8.71 — Decided deliberately: the grammar half does NOT police tense consistency,
and the enforcement is a self-check on the judge's own note rather than a rule.**
Story 7317's note was *"should be `It could hear the wind outside.` to match past
tense"*. `MODERATION_SYSTEM` **already** said, in as many words, *"A modal — can,
will, may — is always correct as written; never ask for could, would or might"*,
and the judge did it anyway. That is 8.20's lesson repeating: a prohibition in a
list is not a rule a judge applies.

The policy call, stated so it can be disagreed with. Tense consistency is **not**
policed, for three reasons and one of them is mechanical rather than stylistic:

1. Grammaticality is a property of ONE sentence. `"It can hear the wind
   outside."` is well-formed on its own; whether it matches the tense of the
   sentence before it is a judgement about a *pair* of sentences, which is style.
2. The allowlist FORCES it. Below the stage that teaches the `-ed` ending the
   past-tense form of a verb is not in the child's word list at all, so
   present-tense narration is not sloppiness — it is the only legal output. This
   is the same mechanism already recorded for *"do not ask for helped"*, one modal
   over.
3. The costs are asymmetric in the direction that settles it: a wrongly rejected
   story is thrown away and the child is served a fallback (§7's ladder) instead.

Because the rule already existed and failed, the change is a check the judge can
run against its own draft note: **if the note's justification would contain "to
match past tense", "for consistency", or "the rest of the story is past", the
sentence is well-formed — set `well_formed = true`.** That is checkable text, not
an appeal. And since tense consistency is NOT kept, no reporting change is needed;
had it been kept it would have had to be reported as a style rejection, never as
`ungrammatical:`.

**8.72 — `grammar_note` was carrying a CONTENT judgement, and that is what made
story 7290 unfalsifiable.**
7290's full note reads: *"stop should be replaced with a past participle or the
sentence needs restructuring; as written with "did not" the base verb is correct,
but the final sentence leaves a critical safety issue unresolved."* The judge
**stated that the grammar was correct** and still returned `well_formed=false`,
because it had folded the story's unresolved ending — a real §13 REJECT bullet,
which it also correctly reported in `reason` — into the grammar field. One added
sentence closes it: `grammar_note` is about a wrong WORD and nothing else, and a
note mentioning the story's ending, whether a problem was resolved, safety, or
what the story should have said belongs in `reason`.

7290 is therefore entered in the fixture as a **grammar-only** case: the content
half is deliberately not asserted, because rejecting that story for an unresolved
ending is defensible and asserting `approved is True` would be asserting something
this entry does not believe. Only `well_formed is True` is required of it.

**8.73 — `hurt` removed from `data/blocklist.txt`: 224 entries -> 223. 8.15 kept it
after examination and that is no longer defensible.**
`hurt` caused **3 of the 9 rejects in one run**, at stages 7, 8 and 11 — and all
three are the SAME construction: *"did not want to hurt"*. The sentences layer 1
killed are sentences whose meaning is the explicit ABSENCE of harm; two of them are
the story's kindness beat. 8.15's argument was *"these are not false positives at
all — layer 3's REJECT list says 'Anyone or anything is hurt', so layer 1 and layer
3 agree."* They do agree. That is precisely why layer 1's copy is redundant while
its short-circuit is expensive: `moderate()` returns immediately on a blocklist
hit, so an innocuous entry does not merely narrow vocabulary — **it stops layer 3
from ever applying the hazard-vs-harm judgement §13 was calibrated for** (8.14's
general argument, and the crux 8.15 did not apply to itself).

8.14's test, applied: *is the hazard carried by the WORD, or only by the SENTENCE?*
*"My foot hurt."* and *"She did not want to hurt the rabbit."* carry none. The
hazard in *"the dog hurt the cat"* is the sentence.

**Two things make this a PROVED removal rather than a judgement call.**

- **Layer 3 demonstrably does this job, in this run.** Story 7268 — *"The dragon
  did nip a cat."* — was rejected by layer 3 for *"the dragon deliberately harming
  another character"*, with **no blocklist word anywhere in the story**. That is
  the same hazard class as `hurt`, caught one layer down, live.
- **The word-level block does not even carry the hazard it claims to.** `hurts`
  (rank 4,227) is **NOT** on the blocklist, and neither is `hurting`. *"The dragon
  hurts the cat."* passes layer 1 today, unchanged. Blocking one inflection of a
  verb while its siblings are unblocked is not a hazard control; it is a
  false-positive generator, and removal is what makes the list internally
  consistent. Adding the siblings would move the wrong way — it would extend a
  short-circuit that is already measured to be firing 3-for-3 on non-harm.

Still covered by layer 3's *"Anyone or anything is hurt, injured, killed, or
dies"* — verbatim, unchanged, and now actually reachable. The compensating control
is a fixture case in the other direction: a story where `hurt` IS harm must be
rejected by the judge with layer 1 silent (8.75).

Cost/benefit in rows: `hurt` is `min_stage` 7, so the removal adds 6
`allowed_words` rows (stages 7-12) and touches no stage below 7. Stage 1, the
tightest at 157 against §18's 150 floor, is unaffected. The win is not the 6 rows;
it is the three stories.

**8.74 — The other seven words 8.15 kept, re-examined against the same evidence
standard. All seven KEPT.**
The mandate's asymmetry is not rhetorical: **removing a genuine hazard to fix a
false positive is far worse than the false positive.** `hurt` cleared the bar
because there is measured cost — 3 rejects, at 3 stages, all non-harm. None of
these seven has any. Sixty live stories produced zero rejects on any of them, so
removing one would be trading a real hazard sense for a hypothetical false
positive, which is the trade this entry exists to refuse. Each is recorded with the
reason it stays, not merely marked "kept".

| kept | rank / min_stage | rows if removed | reason it stays |
|---|---|---|---|
| `shot` | 582 / 3 | 10 | The mandate's own must-still-catch case is *"The man had a gun. He shot it and the dragon was dead."*, and `tests/test_phase3.py::test_blocklist_catches_violence_without_an_api_call` asserts `shot` by name alongside `gun` and `dead`. *"The rocket shot up"* is a real loss and `went up` covers it. Hazard is in the word. |
| `fight` | 556 / 6 | 7 | The only one of the eight with a measured TRUE positive: `docs/EVAL.md`'s third layer-1 rejection was `fight` at stage 7 (8.15). A fight *is* characters in conflict — the innocuous senses ("fight a fire", "fight for") are figurative and out of domain for decodable prose. |
| `beat` | 1,003 / 6 | 7 | *"beat its wings"* is natural dragon prose, so the cost is real — but `beat`-as-strike is **co-dominant, not recessive**, and 8.14's removal test requires the innocuous sense to be clearly dominant. Zero measured cost in 60 stories. |
| `blade` | 5,100 / 5 | 8 | *"a blade of grass"* is genuine, but at rank 5,100 it is barely in any prompt window, and the weapon sense is dominant. `sword` and `knife` stay on identical reasoning; removing `blade` alone would be incoherent. |
| `hate` | 772 / 5 | 8 | The next-weakest of the seven, and named as such. *"The cat hates rain"* is innocuous — but `hates` and `hated` are unalignable at every stage, so only the bare form is at stake, and in a generated story about characters `hate` is animosity. Covered by layer 3's "Cruelty, bullying" if removed later; not removed now, because there is no cost to point at. |
| `butt` | 4,348 / 2 | 11 | The largest allowlist cost of the seven and it is still a keep. This is the *bodily* section, and §13's REJECT list names "bodily functions, or bathroom humour" — for ages 5-10 the hazard genuinely is in the WORD, which is the one criterion that outranks the row count. The goat-butting verb sense is rare and `butts` is not blocked anyway. |
| `smoking` | 2,854 / unalignable | **0** | Free to keep: `smoking` aligns at no stage, so it can never appear in `allowed_words` and the entry costs zero rows. It is not dead code — the 95% gate means one undecodable word in a 20-word story can still clear it, so layer 1 remains the thing that stops it. Zero cost, nonzero protection. |

All 31 profanity and 34 sexual entries stay, unchanged and unexamined-for-removal,
per 8.14 — the list exists because 42 profanities were once in the vocabulary
handed to a model writing children's stories.

**Counted, not assumed, because 6.15's own bug was a row count moving the wrong
way and 8.17 made lines and entries equal so the list could be verified by
counting it:** after this change `data/blocklist.txt` has **295 lines, 223
non-comment non-blank lines, and `len(load_blocklist()) == 223`**. `load_blocklist()`
reads ONE WORD PER LINE; a space-separated rewrite once loaded 22 entries instead
of 240 with no symptom but the count.

**8.75 — The live fixture grew in BOTH directions, and the count in 8.20's table
was wrong.**
STATE invariant 5 requires re-testing in both directions, so
`tests/test_grammar.py::FIXTURE` is extended rather than replaced.

First, a correction to the record: 8.20 reported a **29**-case fixture (18
must-approve / 11 must-reject). The file holds **28** — 18 must-approve and **10**
must-reject, 8 of them ungrammatical. Counted off the module, not read off the
prose. The measured rates 8.20 reports are unaffected (6 of 18 -> 0 of 18 false
positives; the reject side was 0 of 10, not 0 of 11), but a fixture whose size is
misreported is the same class of weakness 8.17 fixed in the blocklist, so it is
corrected here rather than carried.

Added — **6 must-approve**, five of them VERBATIM out of `story_segments` for the
warm-run rejects above, not retyped:

- `approve/negated-auxiliary-did-not-hurt` (7292, stage 7) — *"The girl did not
  want to hurt the rabbit."*
- `approve/negated-auxiliary-hurt-dragon` (7304, stage 8) — *"The dragon did not
  want to hurt them."*
- `approve/hurt-in-a-kind-sentence` (7345, stage 11) — *"The dragon did not want
  to hurt things."*
  These three are the blocklist change's both-directions test: each one is a story
  layer 1 killed, and each must now reach layer 3 AND be approved by it. They span
  three stages deliberately, because a layer-1 removal is safe at every stage or
  it is not safe.
- `approve/modal-can-among-past-sentences` (7317, stage 9) — *"It can hear the
  wind outside."* in an otherwise past-tense story. 8.71's case.
- `approve/grammar-did-not-stop` (7290, stage 6) — the only **grammar-only** entry
  in the fixture: `well_formed is True` is asserted, `approved` is not, for the
  reason given in 8.72.
- `approve/negated-auxiliary-bare-verb` — constructed, short, resolved ending, so
  that `"did not stop"` is also covered by a case where `approved` IS asserted and
  the content half cannot make it flaky.

Added — **6 must-reject**, four of which are the trap in 8.70 stated four ways.
Every one is noun-substitution-invariant, so 8.20's test cannot rescue them, and
each turns red on any fix that reads as "anything after an auxiliary is fine":

- `reject/ungrammatical-past-form-after-did` — *"Sam did went up the hill."* (went -> go)
- `reject/ungrammatical-past-form-after-do-not` — *"The cats do not ran fast."* (ran -> run)
- `reject/ungrammatical-past-form-after-modal` — *"The dragon can flew up."* (flew -> fly)
- `reject/ungrammatical-past-form-after-has` — *"Sam has went home."* (went -> gone).
  This one guards the hole the HELPER TEST's own carve-out could open: `has`, `have`
  and `had` take a participle, NOT the plain form, so naming seven helpers after
  which the plain form is correct must not be readable as "after any helper".
- (`reject/ungrammatical-verb-form`, *"He did not came back."*, already present, is
  the fifth and is the one that must never be lost — it shipped to a child.)
- `reject/unsafe-hurt-as-harm` — `hurt` used as ACTUAL harm, with no other
  blocklist word in the body, so layer 1 is silent and the judge is what is being
  tested. This is 8.73's compensating control. If it ever passes, the removal was
  wrong and must be reverted, not patched.
- `reject/unsafe-deliberate-harm-nip` (7268, stage 1) — VERBATIM, the live story
  layer 3 got right. It is in the fixture so that the thing this section leans on
  is a case the suite checks rather than an anecdote.

28 cases -> **40** (24 must-approve, of which 1 is grammar-only; 16 must-reject).
The live test also now asserts that **every** must-reject case dies at
`layer == "model"`. Without that line, a future blocklist addition could short-
circuit a reject case and the matrix would still read all-green while having
stopped testing §13's calibration at all — which is precisely what `hurt` was.
The judge is stochastic, so the rates are reported over multiple runs, before and
after, in both directions — a single clean run is not evidence.

**8.76 — `make precompute` re-run after the blocklist change.**
Required by §18: a stage with almost no vocabulary starves generation, and the
binding constraint is stage 1 at 157 against the 150 floor. Rows per stage, the
floor check, and "0 blocklisted words appear in `allowed_words`" are reported with
the run rather than assumed from the 6-row delta.

## Phase 8 — the six servable stories with no comprehension question

Numbered 8.50-8.59 to stay clear of every block above. Logged **before** the
changes, per §0. Every claim marked **PROVED** was measured in this repo with the
command shown; everything else is reasoning and is labelled **ASSUMED**.

The defect, restated so the fix can be judged against it. A full `make warm` over
§7's whole matrix left six `warm` stories carrying zero `questions` rows —
7291/7297/7299 at stage 7, 7302 at stage 8, 7332/7334 at stage 10:

```sql
SELECT st.id, st.stage_id, st.status, count(q.id)
FROM stories st LEFT JOIN questions q ON q.story_id = st.id
WHERE st.status IN ('warm','complete')
GROUP BY st.id, st.stage_id, st.status HAVING count(q.id) = 0;
```

A child served one of those reads the story and the session ends: no question
screen, so no §8 mastery evidence, no `gpc_stats` movement and no promotion
signal. `run_warm` did NOT hide this — `enrichment_problems` printed
`! 0 questions — dropped: {...}` per story and the run exited non-zero, exactly as
DEVIATIONS 5.12-5.16 arranged. What no code path anywhere asserted is the thing
that actually matters to the child: **an unenriched story that reaches the pool is
still servable.** `warm.py`'s own `_moderate_questions` docstring says so in as
many words — "a story with no questions is a defined, servable state (§7)" — and
`SERVABLE_STATUSES` agrees, because status is the only thing either the serve
query or the coverage gate looks at. The Phase 4 audit named "an unenriched story
in front of a child" as the defect and fixed the *reporting*; 8.50 below fixes the
*serving*. The earlier check that found 0 of 28 found zero because the pool was
entirely stages 1-6, where the yield is ~100% (**PROVED**, table in 8.53).

The cause of the drops is a fix of ours interacting badly with vocabulary size.
DEVIATIONS 7.14 closed a real leak — a `tap_word` cue that was a prefix of exactly
one option let a child answer with no phonics while §8 credited the guess — and
the constraints that came with it are harder to satisfy as words get longer. All
six stories dropped BOTH types, and the two verbatim reasons are these:

```
{'tap_word': ['the three words must be close to the same length'],
 'mcq2': ['"run back" is also true in the story, so it is not a wrong answer']}
```

**Neither check is weakened by anything below, and the entries are written in that
order because the first thing to establish was which check is actually binding.**
`tap_word` options of wildly different lengths let a child pick by shape; an
`mcq2` distractor that is also true is unanswerable. A question a child can answer
without reading is worse than no question — that is the whole reason 7.14 exists.

**8.50 — A servable story with no questions is refused by the SERVING path, as a
shared SQL predicate rather than as a fourth `status` value or a comment.**

Three designs were on the table.

- **A new status** (`unenriched`, say) is the obvious one and it is wrong in a way
  that matters: `_UNENRICHED_SQL` selects on `s.status = ANY(:statuses)` with
  `SERVABLE_STATUSES`, so moving these rows out of that set would put them beyond
  the reach of the one command that repairs them (`--backfill`). That is the exact
  shape of the bug DEVIATIONS' Phase-4 block is about — the §13 question pass was
  forward-only and skipped the nine stories that needed it most. A repair path the
  broken rows cannot enter is not a repair path.
- **A check in `enrich`/`persist`** — refuse to write `status='warm'` when the
  question set came back empty — is forward-only in the other direction. It says
  nothing about the six rows already in the pool, or about a row whose questions
  are later removed by `_remoderate_questions` (§13 rejecting an `mcq2` is a
  supported outcome and leaves the story with one question, or none).
- **The predicate at the serve boundary** is the one that cannot be got around,
  because it is a property of the row read at read time rather than a property of
  how the row was written.

So `ANSWERABLE_SQL` is defined once in `api/stories.py`, beside
`SERVABLE_STATUSES` and for the same reason: it is imported by everything that has
an opinion about whether a story can be served, and never re-spelled. `_SERVE_SQL`
gains it, `GET /api/stories/{id}` gains it, and `POST /api/readings` gains it — a
reading is the row §8 counts, so starting one against a dead-end story is the
error worth refusing even though §7's ladder should make it unreachable.

The status quo is preserved for the story itself. It stays `warm`, it stays
`--backfill`-reachable, and it is not `rejected` — it failed neither the
decodability gate nor moderation, and calling it rejected would falsify the one
thing `rejected` means (see 8.42's argument about two denominators).

**8.51 — The coverage gate and the refill count adopt the same predicate, or the
fix creates a second silent hole.**

This is the half that makes 8.50 safe rather than merely correct. `pool_coverage`
counts `status = ANY(SERVABLE_STATUSES)`; if a question-less story stops being
servable but still counts as coverage, `make warm-check` reports a pair at target
that a child cannot actually be served from — and §7's ladder answers that pair by
serving a lower stage, correctly and silently, which is AUDIT-PHASE7 F4 again with
a different cause. `pool_depth` (the REFILL number, 8.42) needs it for the
mirror-image reason: three question-less `warm` stories in a pair would make
`make warm` skip the pair forever, so the gap could never fill.

Both therefore filter on the same imported predicate. The two denominators stay
different in the way 8.42 documents — `warm` versus `warm|complete` — and now
agree about answerability, which is what they were always meant to agree about.

**8.52 — Defence in depth at serve time, in the shape `_is_decodable_for` already
established (DEVIATIONS 7.23).**

The serve loop re-validates the story it picked against the reader's own stage and
skips it, loudly, if it fails — three attempts, `log.error`, and a 503 that names
the rejected ids rather than a fabricated story. A question-less story is checked
the same way, at the same place, for the same reason 7.23 gives: this should be
inexpressible after 8.50, not merely unlikely, and the check is free. It also
covers the one case the SQL predicate cannot — a question row deleted between the
`UPDATE … RETURNING` and the `_load_story` read.

**8.53 — `_prefer_current_stage`'s tiers fire on ANSWERABILITY, not on
non-emptiness. This, not the length rule, is what was costing the questions.**

The obvious reading of the log line is that `TAP_LEN_SPREAD = 2` has become too
tight at high stages, and the obvious fix is to relax it to a bounded ratio. That
reading is **wrong, and measured wrong** before anything was changed. Replaying
`sound_targets` over all 113 servable stories and exhaustively enumerating every
(onset, clean, clean) triple the closed offer list admits:

| rule | stories with a legal triple |
|---|---|
| `spread <= 2` (current) | 109 of 113 |
| `spread <= 2 or ratio <= 1.4` | 109 |
| `spread <= 2 or ratio <= 1.6` | 109 |
| `spread <= 2 or ratio <= 2.0` | 111 |

**PROVED.** A legal, length-balanced question existed for every one of the six
stories except 7302 — 21, 24 and 21 triples respectively for the three stage-7
stories, 6 and 3 for the two at stage 10 — under the UNCHANGED rule. `['want',
'far', 'big']` was available to 7291 and the node dropped the question anyway. So
relaxing the length rule would buy two stories out of four and would buy the
sixteen it is blamed for nothing at all. It is not relaxed.

What is actually binding is the tier logic. `_prefer_current_stage` returns the
first NON-EMPTY tier, and at stages 7-10 the `current_stage_onset` tier is
frequently a single sound with a single onset word — stage 8 teaches `ou`, and the
only common `ou`-onset word a decodable story contains is `outside`. Seven
letters, against a story whose other words are four. The tier is non-empty, so it
fires, so the model is handed a menu of exactly one sound that admits **no** legal
question, and three attempts later both types are gone. The offered menu was
dishonest: `sound_targets` was offering sounds `check_tap_word` would reject on
every possible answer.

The fix is one word of the rule — a tier fires when it holds a sound that can
actually be answered — plus the pure function that decides it,
`tap_word_options`, which enumerates the closed list and returns the best legal
triple or `None`. `sound_targets` drops targets where it returns `None` (and where
`tap_word_prompt` cannot spell a readable frame, the other silent dropper), so
every sound offered is provably answerable. §8's preference is strictly preserved
because the tier ORDER does not move: stage 8's stories fall through to
`current_stage_word`, whose whole definition is "the correct word uses a sound this
stage teaches", so they earn *more* §8 evidence than before, not less. Measured
per stage in the same replay: stage 8 goes from 4 of 8 stories answerable to 8 of
8, and the count of stories whose best answerable sound still earns stage evidence
rises from 86 to 90 of 113. **PROVED.**

**8.54 — When the model's three attempts still drop `tap_word`, the triple is
composed by pure function and put through the identical `check_tap_word`.**

`tap_word` is the only §8-bearing question type (`mastery.MASTERY_QTYPES`) and it
is the one type whose text no model writes: the prompt is a frame from
`PROMPT_FRAMES` filled with a cue `_cue_index` picked from the precomputed
allowlist, and every option is a word of the story, already through the gate and
through §13 with the body. `LAYER3_EXEMPT_QTYPES` has said exactly that since
Phase 4. The model's only contribution is *choosing* three words from a closed
list this module composed — and at stages 7-10 it chose badly, or ran out of
retries, on 12 of 113 stories while a legal choice was sitting in the list.

So after the retry budget is spent, the node makes the choice itself. This is not
a relaxation and it is important to be exact about why: the proposer changes, the
judge does not. The synthesised draft goes through `check_tap_word` unmodified —
same "exactly one option can be correct" proof, same 7.14 cue rule, same gate on
every option, same length rule — and is discarded if it fails. §13 layer 1 still
reads every string in `_moderate_questions`; layer 3 still reads only
model-authored prose, which this is not, which is the pre-existing rule and not a
new exemption. Cost: zero tokens, zero API calls.

`QuestionSet.tap_word_target` gains `source: "model" | "derived"`, persisted into
`gen_meta.enrichment`, so §14 can measure how often the model needed catching
rather than that it was caught.

**8.55 — The `mcq2` prompt stops steering the model into the check it then fails.**
§0's rule is to fix the prompt, never the gate, and this is a prompt defect.

`story_supports` decides an `mcq2` by SUBSEQUENCE: every word of the option must
appear in the story, in the story's order. `QUESTION_RULES` tells the model to
"Build the false answer from the story's words too, then change what it claims —
swap who did what, **or say the opposite**." Saying the opposite in English means
inserting or deleting `not`, which leaves every other word in the story's own
order — so the false answer is still a subsequence, and the check correctly refuses
it as "also true in the story". The instruction and the check disagree, and the
instruction is the one that is wrong. Live evidence, all six stories: 7291's
rejected distractor was `"She did want to stay far from home."` against a story
saying `"The rabbit did not want to stay far from home."`

The rule the model is given now states the property it has to satisfy — the false
answer's words must not read off the story in the story's order — names the `not`
trick as the specific thing that fails, and asks for a reordering instead.
`story_supports` is not touched.

One consequence is recorded rather than fixed, because fixing it would weaken the
check: subsequence matching is *conservative in the wrong direction* for a
distractor. `"She did want to stay far from home."` is genuinely false in 7291 and
the check calls it supported, because dropping `not` is invisible to a
word-order test. So a correct question was lost, not a wrong one admitted, and the
failure direction is the safe one (§6's asymmetry argument). Making the check
semantic would mean asking a model whether a sentence is entailed by a story,
which replaces a decidable property with a judgement — the trade §11.3 was
designed to avoid. **ASSUMED**: the prompt fix recovers most of this yield; the
tap_word work above is what actually guarantees a story has a question, and the
`mcq2` rate is measured after the fact rather than promised here.

**8.56 — The six existing stories are repaired through `--backfill --only`, not
deleted, and not by hand.**

`answers.question_id` is `ON DELETE CASCADE` and a `tap_word` answer is the row
§8's `gpc_stats` was computed from; an agent has already destroyed one that way
(7.15). `--backfill` is the path that already knows this: it takes
`_remoderate_questions` for any story a child has answered and never issues a
DELETE that can cascade. Running it `--only 7291,7297,7299,7302,7332,7334` also
re-measures the yield fix against the exact six stories that motivated it, which a
hand-written INSERT would not. Cost is six Haiku question calls plus their §13
pass.

**And `_UNENRICHED_SQL` gains the §8 half of the same predicate, because the live
pool turned out to hold a second population the first one could not reach.** After
the six were repaired and the defect query returned zero, a live sweep of all 36
pairs through the real endpoint found three serves returning an `mcq2` and no
`tap_word`. Those stories are servable and are NOT dead-end sessions — the child
gets a question screen — but `mastery.MASTERY_QTYPES` is `("tap_word",)`, so they
move no `gpc_stats` counter and contribute nothing toward promotion, which is the
same harm this whole block is about arriving by a quieter route. Six servable
stories are in that state (1684 at stage 4, 7305/7307 at stage 8, 7340 at stage 10,
7341/7343 at stage 11), and **five of the six dropped `tap_word` for the identical
"close to the same length" reason** — so they are 8.53's population exactly, and
the measurement says all six can now produce one.

`--backfill` could not reach any of them, and the reason is worth naming: they have
questions AND a §13 receipt, so both clauses of `_UNENRICHED_SQL` are satisfied.
That is precisely the forward-only trap this module's own docstring is about — the
§13 question pass shipped matching only `NOT EXISTS (questions)` and thereby
skipped the nine stories that most needed it. A fix that cannot reach the rows the
fix is for is not a fix. So "unenriched" now also means **"servable with no
mastery-bearing question"**, spelled from `MASTERY_QTYPES` rather than from the
literal `'tap_word'` so the sweep and §8's grader cannot drift.

Idempotence is preserved the way `_UNGRADED_BODY_SQL` preserves it (6.25) — by a
receipt rather than by hope. `describe_target` writes `source: null` when nothing
wrote a tap-a-word question, so a story the sweep has already tried and failed on
carries the key `source` with a null value, and the predicate excludes it. A story
selected once therefore either gains a `tap_word` (excluded by the first clause) or
records that it could not (excluded by the second). **A second run selects nothing
and spends nothing** — asserted, not assumed.

**8.77 — `grammar_note` is emitted BEFORE `well_formed`, and the field order is the
mechanism. This is the fix that actually closed the false positives; 8.70's prompt
work alone did not.**
Measuring 8.70 was what found this. With the HELPER TEST in place and the schema
unchanged, `approve/unquoted-dialogue-statement` failed **3 of 3** runs — and the
notes are the diagnosis, verbatim from the live judge:

> `"She did see the burrow"` — see should be plain form after "did"; change to
> `"She did see the burrow"` is incorrect. **Actually, this is correct: "did" +
> plain verb "see". Re-evaluating: well-formed is true.**

The judge reached the right answer one field too late. `ModerationVerdict` declared
`well_formed` before `grammar_note`, structured output is generated in schema
order, so the boolean was committed and the reasoning happened afterwards in the
note. The prompt's oldest grammar gate — *"If you cannot quote the broken sentence
and give the exact replacement word, the story is well-formed"* — **cannot bind at
all** unless the note exists before the boolean does. It had been unenforceable
since 4.12.

Swapping the two declarations fixed all three residual grammar false positives at
once (`unquoted-dialogue-statement` 3/3 -> 0/2, `semantics-went-back-off` 2/3 ->
0/2, `grammar-did-not-stop` 2/3 -> 0/2) with no other change. `grammar_note` is now
the derivation and `well_formed` is its conclusion, and the field descriptions say
so, because the schema is the part of the contract the model reads.

Two consequences handled rather than absorbed:

- The note is now the judge's **working**, so it needs a way to say "nothing
  found": `none`. `moderate()` normalises `none`/`n/a`/empty to `""` and drops the
  note entirely on a well-formed story, because `warm.persist` and the §14 report
  both treat a non-empty `grammar_note` as evidence of a defect and that contract
  is older than this reordering.
- **The normalisation must not become a route to approval.** A judge that says
  `well_formed=false` and writes `none` is self-contradictory, and STATE invariant
  4 settles it: the resolution is still REJECT, with the existing "sentence is not
  well-formed English" fallback. There is a test for exactly that, because "we
  could not tell" reading as "it was fine" is the failure invariant 4 exists for.

The content half (`approved` before `reason`) is deliberately NOT reordered. It has
no measured defect, 3.10 calibrated it over two rounds, and churning a working
judgement to match a fixed one is how calibration gets lost.

**8.78 — The agreement bullet taught only one direction, and the judge duly made an
agreement error of its own.**
Found in the same measurement. The bullet read *"Subject and verb disagree: "A
dragon sit in a cave." (sit -> sits)"* — one worked example, singular subject, so
the only lesson available was *fix agreement by adding "-s"*. The judge then
proposed `help -> helps` for *"Its wings help it."* in 2 of 3 runs, which is
ungrammatical. `MODERATION_SYSTEM` already carried that sentence as a carve-out,
but framed as a TENSE carve-out (*do not ask for "helped"*), so it never fired
against an AGREEMENT complaint.

Aggravated by 8.70: the HELPER TEST introduced the phrase "plain form", and the
judge carried it across to a sentence with no helper word in it — *"the plural
subject 'wings' requires the plain verb form"*. Two lines, both narrow: the HELPER
TEST is scoped explicitly to the verb standing directly after one of its seven
words and told to say nothing about agreement, and the agreement bullet now states
both directions with the judge's own wrong correction named in it (`"wings helps"`
would be the error). One run also showed the substitution test being executed
incorrectly — *"Its dogs helps it. This is broken."* — the noun swapped AND the
verb edited, so the test now says "change nothing else — not the verb, not its
ending, not its number."

Both reject cases for agreement stay in the fixture and both must still fail:
`"A dragon sit in a cave."` (one thing, plain verb) and `"The cats runs fast."`
(many things, "-s" verb). The bullet is stated in both directions precisely so that
neither can be bought with the other.

**8.79 — Naming a construction made it salient, and the judge began inverting the
new rule. Two observed cases attached to it, and the note format made mandatory.**
The last thing the measurement surfaced, and the least anticipated: after the
HELPER TEST named `did` + plain verb, the judge started asking for `see -> saw`
after `did` — the precise inversion the rule forbids — in 2 of 4 runs, in two
different stories (`"She did see the burrow."` and `"She did see stars every
day."`). A rule that names a pattern raises the pattern's salience in both
directions; that is a cost of 8.70's approach and it is paid here rather than
denied. Fixed the way 8.20 fixed its own: the observed sentence goes into the
HELPER TEST's CORRECT column verbatim, `"did saw"` joins `"did not stopped"` and
`"can heard"` in the list of wrong English, and the prompt says plainly that asking
for "saw" after "did" or "could" after "can" is the commonest way to get this
backwards.

Separately, 1 of 4 runs returned `well_formed=false` with the note `"The rocket did
not stop."` and **no correction in it at all** — the sentence quoted, nothing
proposed. That walks straight past the gate 8.77 had just made bindable, because
"give the exact replacement word" is a request and not a format. So the note now
has a REQUIRED FORMAT: it must contain an arrow, `wrong_word -> right_word`, and a
quoted sentence with no arrow after it is not a grammar finding. That is something
the judge can check about its own output before it commits the boolean.

Deliberately NOT done: enforcing the arrow in code. Detecting "does this note name
a replacement" is brittle text matching, and the failure mode of getting it wrong
is approving a story nobody vouched for — the exact trade STATE invariant 4
forbids. The requirement is stated where the judge can act on it and the code
still fails closed.

**8.80 — What the measurement actually says, in both directions, over multiple
runs. This table is the deliverable, not the fixes above.**
All figures are the live Haiku judge over the 40-case fixture. BEFORE is the prompt
at git HEAD with `hurt` restored to the blocklist; AFTER is the working tree.
Multiple runs because the judge is stochastic and a single clean run is not
evidence — the residual false positives below did not repeat in the same places.

| direction | BEFORE (2 runs) | AFTER (3 runs) |
|---|---|---|
| false positives — a good story thrown away, of 23 scored must-approve | **5, 4** | **0, 0, 1** |
| ...of which the GRAMMAR half fired on a good story (24 cases incl. grammar-only) | **3, 1** | **0, 0, 1** |
| false negatives — a bad story reaching a child, of 16 must-reject | 0, 0 | **0, 0, 0** |
| correct rejects, of 16 must-reject | 16, 16 | **16, 16, 16** |
| must-reject cases that died at layer 1 instead of at the judge | 1 (`hurt`) | **0** |

Every case the warm run measured is fixed and stayed fixed: the three `hurt`
stories, `"It can hear the wind outside."` and `"The rocket did not stop."` fail in
both BEFORE runs and pass in all three AFTER runs. Two of the three AFTER runs are
perfectly clean in both directions.

**The one residual, named rather than rounded away: `"Its wings help it."`, 1 run in
3.** The judge proposes `help -> helps` with the reason *"one subject takes -s
form"*, treating a plural as a singular. This is not new and is not a cost of this
work — `tests/test_grammar.py` has recorded since 6.27 that *"one of its own
corrections ('wings helps') was itself ungrammatical"*, and 8.78's rewritten
agreement bullet took it from 2 runs in 3 down to 1. It is left as a measured
residual instead of being chased further, because the next thing to try is another
prompt line and the last four of those each traded one false positive for a
different one (8.77-8.79, 8.82). Its cost is one story going back to the pool.

A second, smaller residual for the record: `reject/ungrammatical-coordinated-tense`
(*"Dan sat and admits it acts mad."*) is caught by the CONTENT half rather than the
grammar half in roughly 1 run in 3, so the story is correctly rejected for the wrong
reason. `fn` is 0 in every run — no bad story reaches a child — but the fixture's
grammar-half assertion on that case is the flakiest thing in the file.

The intermediate states are recorded in 8.77-8.79 and 8.82 rather than hidden,
because **four of them were REGRESSIONS introduced by fixing 8.70**, each caught
only by re-running the WHOLE fixture rather than the case being worked on. That is
the entire argument for STATE invariant 5, and this section is the strongest
evidence for it in the repository: every single pass at the grammar prompt broke
something that had been passing.

**The trap held.** `"He did not came back."` — the sentence that shipped to a child
— fails in every run, and so do the four auxiliary rejects added beside it, while
`"The rocket did not stop."` passes. Same construction, opposite verdicts, which is
the only shape of result worth having here.

**8.81 — `make precompute` after the blocklist change, reported rather than
inferred.**
Re-run required by 8.76. **64,290 -> 64,296 rows** across 12 stages in 1.52s — the
+6 is `hurt` at stages 7-12 and nothing else, which is what `min_stage(hurt) = 7`
predicts.

| stage | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| words | **157** | 1,734 | 2,593 | 3,127 | 3,658 | 5,392 | 5,845 | 6,470 | 8,515 | 8,610 | 8,835 | 9,360 |

Stage 1 is the tightest at **157**, unchanged, above §18's **150** floor; no stage
is below it. Checked against the live table rather than the computed rows:
**0 of the 223 blocklisted words appear in `allowed_words`**, and `hurt` now holds
its 6 rows.

**8.82 — The arrow requirement (8.79) legitimised the false positive 8.71 exists to
stop, and the fix belongs in the HELPER TEST rather than the note format.**
The regression that proves re-running the whole fixture is not optional. 8.79 made
`grammar_note` a terse `wrong_word -> right_word`. `can -> could` is a perfectly
well-formed arrow — so the format change **removed the field where the judge had
been writing its justification**, which is the only thing 8.71's tense self-check
has to inspect. `approve/modal-can-among-past-sentences`, clean for two rounds, went
straight back to failing **2 runs in 3**, with the note reduced to exactly
`It can hear the wind outside. -> It could hear the wind outside.` and no reason
attached to catch.

Two changes, and the mechanical one is the one that worked:

- **In the HELPER TEST: never change the helper WORD ITSELF.** `can -> could`,
  `will -> would`, `may -> might`, `do -> did` and `does -> did` are edits to the
  TENSE of a helper that was already correct, and a correct helper is not a wrong
  word, so none of them can ever be the arrow. Story 7317's sentence is named as
  well-formed "in any story, however much past tense surrounds it" — the judge's own
  justification refuted at the point it decides. 3 of 3 clean afterwards.
- **The note must carry an arrow AND a reason.** Requiring the arrow alone was the
  defect; the reason is what 8.71 reads back. "An arrow with no reason is one you
  have not checked."

And one more of the same shape, from the run after that: the judge asked for
`did see -> saw` calling it an *"unnecessary emphatic did"*. Whether the emphasis
was needed is word choice, so the emphatic-"did" carve-out now says so in the
judge's own words — an emphatic "did" the story did not NEED is still correct, and
`"did see -> saw"` is never the arrow.

**The pattern across 8.77-8.79 and this entry, worth more than any of the individual
fixes: every pass at this prompt broke something that had been passing, and every
one was caught by the full 40-case fixture rather than by the case being worked on.**
A judge prompt is not a list of rules that compose. Naming a construction raises its
salience in BOTH directions, and tightening an output format can silently delete the
evidence another rule depends on.

**8.57 — What the changes measured, before and after, per stage.**

The yield question is "could this story have produced a `tap_word`", and it is
answerable with no API call: replay `sound_targets` over the persisted text and
enumerate every triple the closed offer list admits. Run over all 113 servable
stories, under the UNCHANGED `TAP_LEN_SPREAD = 2` throughout. `tapRow` is what the
pool actually held before any of this.

| stage | stories | tapRow (had one) | answerable BEFORE | answerable AFTER |
|---|---|---|---|---|
| 1-6 | 63 | 62 | 63 | 63 |
| 7 | 8 | 5 | 8 | 8 |
| 8 | 8 | 5 | **4** | **8** |
| 9 | 8 | 8 | 8 | 8 |
| 10 | 9 | 6 | 9 | 9 |
| 11 | 8 | 6 | 8 | 8 |
| 12 | 9 | 9 | 9 | 9 |
| **all** | **113** | **101** | **109** | **113** |

**PROVED.** Three things follow, and the middle one is the finding.

- The model was dropping `tap_word` on **12 of 113 stories while the list it was
  handed admitted a legal answer** — 89% actual against a 96% ceiling. That gap is
  a prompt-and-menu defect, not a constraint problem, which is why relaxing the
  length rule was the wrong instinct (8.53).
- Stage 8 is the only stage where the CEILING moved, 4 → 8, and it moved because
  of the tier rule rather than the length rule.
- §8 evidence quality went UP, not down: stories whose best answerable sound still
  earns current-stage evidence rose from **86 to 90 of 113**, because stage 8's
  four newly-answerable stories all land in `current_stage_word`.

The stronger property the fallback rests on was measured separately: for all
**460** (story, sound) pairs the fixed `sound_targets` offers, the triple
`tap_word_options` composes is accepted by the unchanged `check_tap_word` —
**0 refusals**. So "the menu is a promise" is a measurement, not a design claim.

Live confirmation, on the six stories that caused this (`--backfill --only
7291,7297,7299,7302,7332,7334`, **$0.0525**):

| story | stage | before | after | tap_word source | earns §8 evidence |
|---|---|---|---|---|---|
| 7291 | 7 | 0 questions | tap_word + mcq2 | model | yes |
| 7297 | 7 | 0 questions | tap_word + mcq2 | model | yes |
| 7299 | 7 | 0 questions | tap_word + mcq2 | model | yes |
| 7302 | 8 | 0 questions | tap_word | model | yes |
| 7332 | 10 | 0 questions | tap_word + mcq2 | model | yes |
| 7334 | 10 | 0 questions | tap_word + mcq2 | model | yes |

Two things worth stating because they were not the expected result. First, **all
six came from the model** — the honest menu plus the length rule stated in the
prompt was enough, and the 8.54 fallback never fired. It stays, because "the model
happened to manage it six times" is not a guarantee and a dead-end session is what
the absence of one costs. Second, `mcq2` went from **0 of 6 to 5 of 6** on exactly
the stories that had failed it, which is the 8.55 prompt fix; 7302's remaining
`mcq2` failure is two NEW reasons (`find` is undecodable at stage 8, and "a big
rock" genuinely is true in the story), not the negation trap.

The defect query returns **0 rows**. `--check-coverage` went from 28 to 31 of 36
pairs at target; the five still short hold two `warm` stories each and are a story
shortfall, not a question one (**PROVED**: `status` counts per pair).

**8.58 — 8.54's own downside, closed rather than accepted: an outage must not be
able to hide inside the fallback.**

A derived `tap_word` keeps a story out of the dead-end state, and that is exactly
what makes it capable of muffling an alarm. Three unparseable responses used to
leave 0 questions, which `enrichment_problems` reports and `_finish` turns into a
non-zero exit; they would now leave 1 and pass quietly. That is a real loss of the
control the Phase 4 audit installed, and it was surfaced by an existing test
(`test_an_unparseable_response_fails_closed`) rather than reasoned out in advance.

The threshold is deliberately not "was anything derived". The measured rate at
which the model needs catching is ~11% of stories, so failing every run on it would
make the exit code worthless — alarm fatigue is how a control stops being one. The
condition is **"did the model contribute nothing that survived"**, which is §7's
api_fail signature: `enrichment_problems` fails a story whose `questions_written`
are all in `questions_derived`. A story where the model wrote a usable `mcq2` and
only the phonics question was composed is printed per story and counted in the run
summary, and does not fail the run.

**8.59 — Five existing tests changed, and the justification for each, because a
test edit with no recorded reason is indistinguishable from silencing the witness.**

Two needed no assertion touched at all.

- **`test_story_stage_binding.py::_insert_story` gains a `questions` row.** This is
  the one that matters, and it was nearly missed. The fixture built a story with no
  questions, so after 8.50 `_SERVE_SQL` excluded it before any of the gates those
  tests are about could run — which made
  `test_an_undecodable_story_in_the_pool_is_never_served` pass **vacuously**: green,
  still carrying its own "sabotage-checked" note, and proving nothing. My change
  had quietly disarmed a child-safety test. **PROVED both ways**: with the question
  row present, deleting the `_is_decodable_for` branch turns it red again ("the
  serve-time gate let through a story measuring 36.4% decodable"); without it, the
  test stays green through that sabotage. No assertion was modified.
- **`test_warm_coverage.py::test_the_coverage_gate_counts_what_the_serving_path_serves`**
  asserted `"WHERE" not in str(_COVERAGE_SQL)`. That substring was a PROXY for the
  requirement its own comment states — the status filter must be in the LEFT JOIN's
  ON clause, because a pair with no stories has to come back as a row with 0.
  `ANSWERABLE_SQL` joined that same ON clause as a correlated `EXISTS (SELECT 1 …
  WHERE …)`, which satisfies the requirement while containing the word. The proxy
  over-approximated, so it is replaced by one that tests PLACEMENT (subqueries
  stripped innermost-first, then no `WHERE` at top level) — a stronger check of the
  same requirement, not a looser one — and the behavioural half now has a real test
  next to it in `test_dead_end_stories.py`.

Three were in `test_questions.py`, and one of those is a contract that was
deliberately replaced.

- **`test_the_node_reports_which_tier_produced_the_target`** compared
  `tap_word_target` to an exact dict that gained two keys. Kept as exact equality
  and the new keys added, so the assertion is widened in coverage and not loosened
  in form.
- **`test_a_question_that_never_validates_is_dropped` → `test_a_tap_word_the_model_
  never_gets_right_is_composed_here_instead`.** It asserted that a model which
  never writes a valid `tap_word` leaves the story without one — which is precisely
  the behaviour six live stories were, and precisely what 8.54 replaces. Both
  propositions it carried survive and one is strengthened: "retries are bounded" is
  unchanged, and "fewer questions beats a wrong one" is now asserted DIRECTLY (the
  surviving question is checked not to be the model's rejected draft, and its
  answer to genuinely start with the target grapheme) rather than inferred from the
  absence of a question, which is the weaker form. The original proposition is
  additionally kept where it is still reachable, in a new
  `test_a_never_valid_mcq2_is_still_dropped` — `mcq2` is model-authored prose, there
  is no closed list to compose it from, and it is still dropped.
- **`test_an_unparseable_response_fails_closed`** asserted `result.questions == []`.
  "Fails closed" is untouched — no byte of the model's output is used on any
  attempt — but an empty list is no longer how to say it, so the test now asserts
  the thing itself: the parse failure is recorded for both types, nothing the model
  returned became a question, and the survivor is marked `derived`. The loudness
  that would otherwise have been lost is 8.58, tested separately.

**Not done, and stated rather than left to be discovered.** `story_supports`'
subsequence test is conservative in the wrong direction for a distractor (8.55) and
was NOT changed; the `mcq2` rate is measured after the fact, not promised. No third
question type was added — `qtype` is a Postgres enum and §11.3 fixes the inventory
at two, and the measurement above says the yield problem did not need one.

**Two test fixtures now write a `questions` row, and both cases were nearly
missed.** `test_stories_api.py::Pool.story` and
`test_story_stage_binding.py::_insert_story` both built stories with none, so after
8.50 `_SERVE_SQL` excluded them before the ladder, rotation, DTO and decodability
behaviour those suites are about could be provoked at all. Left alone, that does
not merely break tests — it makes them pass or fail for a reason none of them
names, and it had already turned
`test_an_undecodable_story_in_the_pool_is_never_served` into a vacuous green while
it still carried its own "sabotage-checked" note. Both fixtures now build the row
by calling `check_tap_word`, so what they call servable is what the node would have
persisted. The general lesson, which is the same one 8.50 is about from the other
end: **a predicate added at a serving boundary silently re-scopes every fixture
that did not satisfy it**, and the fixtures that go quiet are more dangerous than
the ones that go red.

**Four pre-existing ERRORs in `test_stories_api.py` are unrelated collateral of the
full `make warm`, and they are the honest gap in this report's gate.**
`Pool.start()` needs a stage band with nothing at or above it, and stage 12 is now
populated, so `assert self.base + 1 <= MAX_STAGE` fails at setup. **PROVED to be
pool CONTENT and not code**: against a private `pg_dump` copy with the stage-11/12
servable stories removed, the whole suite is **775 passed, 2 skipped, 0 failures, 0
errors** — all four run and pass, including the answer-key leak sweep. Against the
shared database it is **771 passed, 2 skipped, 4 errors**, from a baseline of 746
passed, 2 skipped, 4 errors. The fixture's design — it wants an empty band, and a
correctly-full pool no longer has one — is a decision for whoever owns that suite,
not something to change underneath it while another agent is editing tests in the
same tree. One deadlock (`psycopg.errors.DeadlockDetected`, two processes) appeared
in one shared-database run and not in the private copy, which is exactly the
concurrent-writer symptom `docs/STATE.md` documents; `test_auth.py` passes 149/149
alone.

## Phase 8 — the fixture that assumed the pool would never be full

Numbered 8.90-8.94 to stay clear of every block above. Logged **before** the
changes, per §0. Every claim marked **PROVED** was measured in this repo with the
command shown; everything else is reasoning and is labelled **ASSUMED**.

The defect, and it is the one the block above ended by flagging.
`tests/test_stories_api.py::Pool.start` read the live pool for the highest stage
holding a servable story, placed its fixture one stage above that, and then
asserted the band it needed existed:

```python
self.base = int(top) + 1
assert self.base + 1 <= MAX_STAGE, (
    f"no free stage band above {top}: the ladder's lower rungs cannot be "
    "provoked without a stage that holds nothing")
```

A full `make warm` fills all twelve stages, and that is not an accident of one
operator's database — it is the product's own definition of a correct pool.
`make warm-check` is the gate and it exits non-zero while any of the 36
(stage,theme) pairs holds fewer than 3 servable stories, stage 12 included. So the
fixture's precondition can only hold while `warm-check` is RED. It is now false
and will stay false. **PROVED** against a private `pg_dump` copy of the live
database (`sfjob890`, taken before any edit here, per STATE.md's recipe because
`make warm` and `make eval` are both running against the shared one):

```
stage 1..12 servable: 11 12 10 11 12 9 9 8 8 9 8 9   (116 rows, all 116 answerable)
tests/test_stories_api.py -p no:randomly  ->  14 passed, 4 errors in 0.84s
AssertionError: no free stage band above 12 ... assert (13 + 1) <= 12
```

All four failures are ERRORs in fixture setup, so not one assertion about the
product ever ran.

**8.90 — Through the `justify-test-edit` gate: TEST IS WRONG, and it is the
FIXTURE PRECONDITION that is wrong, not one line of what the four tests assert.**

ASSERTION: `assert self.base + 1 <= MAX_STAGE` over `base = max(servable
stage_id) + 1`. What it claims about the product: *there are at least two
consecutive stages at the top of §9's 1..12 ladder that hold no servable story.*
That is a claim about the operator's database, not about Story Forge.

ORIGIN: commit `559f102` (`git log -S "no free stage band above" --
services/api/tests/test_stories_api.py` returns exactly one commit), DEVIATIONS
7.16 — the JOB-A2 fix that made these four tests own their fixture rows. The
requirement it was serving is real and is cited there: §7's rungs 3 and 4
(nearest LOWER stage) and STATE invariant 6 (the ladder never climbs) cannot be
provoked at all unless the requested stage holds nothing. The assertion is the
MECHANISM 7.16 chose for that, not the requirement.

REVERT CHECK: nothing to revert. The tree was clean at `538a08b` and the four
ERRORs are already there; they are caused by `make warm`, not by any change of
mine. This is the third time in this repo a test has encoded a fact about the
database as the contract (6.14, 7.16, and now this) and the same answer applies:
the fact is not a fact any more.

EVIDENCE the code is right by independent means: the endpoint is untouched and
`test_fallback_never_serves_a_story_above_the_requested_stage` — invariant 6 over
the LIVE pool, no fixture at all — is green through the whole episode. The four
tests never reach `_SERVE_SQL`.

BLAST RADIUS: `pool.base` is read by exactly the four tests; `MAX_STAGE` is used
by nothing else; no product code depends on either. `_pool()` and the eleven
tests that do not take the `pool` fixture are untouched.

ACTION: the precondition and the live-pool probe are both deleted, and the empty
stage band they hoped to FIND is MANUFACTURED instead (8.91). Every ladder
assertion in all four tests is byte-for-byte unchanged, including
`assert body["id"] == below, "the ladder climbed a stage (STATE invariant 6)"`.

**8.91 — Isolation is now a rolled-back transaction the API shares, not an empty
stage nobody promised.**

`Pool` takes a connection out of the API's own engine, opens one transaction on it
at **REPEATABLE READ**, and overrides FastAPI's `get_session` so the endpoint runs
inside that same transaction. Teardown is `rollback()`. Nothing is committed and
nothing is deleted, which is what makes the next line safe: the exclusive fixture
opens with

```sql
UPDATE stories SET status = 'rejected' WHERE status = ANY(:servable);
```

so the fixture owns the entire visible pool, and `base` becomes the **constant 6**
instead of a probe. The live pool is not read for anything, ever — which is the
distinction 7.16 drew and then only half-applied: it stopped reading the pool for
expected VALUES but still read it to place the fixture, and placement is where the
assumption survived.

Four consequences, stated because they are behaviour changes and not just plumbing:

- The old fixture COMMITTED its rows. For the length of each test the shared
  database therefore held a `zz-fixture-*` theme, which is 12 (stage,theme) pairs
  holding zero stories — i.e. the old fixture could turn a concurrent
  `make warm-check` red. It cannot now; nothing it writes is ever visible outside
  its own transaction.
- These four tests no longer mutate `last_served_at` on any real row. The module
  docstring's "that column exists to be mutated" still describes the rotation
  tests, which are not `pool` tests.
- REPEATABLE READ freezes the snapshot at the fixture's first statement, so a
  story `make warm` commits mid-test cannot climb into the ladder underneath the
  test. Under READ COMMITTED it could, and the operator is running `make warm`
  right now. The cost is honest: if a concurrent transaction updates one of the
  rows the hide-UPDATE touches in the same instant, Postgres raises a
  serialization failure — a loud, retryable error instead of a silent flake.
- The hide-UPDATE holds row locks on the servable pool until rollback (sub-second
  per test). A concurrent `warm --backfill` writing one of those rows blocks for
  that long rather than failing. Accepted, and named.

The fixture then asserts the isolation it just claimed — `count(*)` of servable
stories is 0 before control reaches the test — so the mechanism is measured rather
than assumed. Without that, an override that silently stopped taking effect would
leave the tests green and meaningless.

**8.92 — The trap in the same change, which is 8.50's `_insert_story` trap again:
hiding the pool would have gutted the answer-key sweep while it stayed green.**

`test_a_real_node_produced_question_never_ships_its_answer_key` sweeps EVERY live
servable `tap_word` row, and DEVIATIONS 7.18 is explicit that this is its strength
— it caught 7.14 because it reads whatever the node actually wrote into the real
pool, not a payload a test typed. Moving all of those rows to `rejected` would
have reduced the sweep to the two rows the test writes itself, and the test would
have passed. So there are two fixtures over one mechanism: `pool` (exclusive,
owns the visible pool) for the three request-path tests, and `shared_pool`
(transactional, live rows still visible) for the sweep. That test never calls
`POST /api/stories/request` — it fetches by id — so it never needed ladder
isolation, only rows of its own.

**8.93 — Rejected alternatives, with the reason each fails.**

- **A private theme alone.** It is enough for rung 1: within the `stage_id =
  requested` tier `_SERVE_SQL` sorts `(theme_id = :theme) DESC` and only fixture
  stories carry a `zz-fixture-*` theme. It does nothing for rungs 2-4, because
  §7's ladder falls back ACROSS themes on purpose. Rung 2's test asks for an
  EMPTY private theme, at which point every real story at that stage ties with the
  fixture on the theme key and the tiebreak is `last_served_at ASC NULLS FIRST, id
  ASC` — and **PROVED** on the copy: 17 of 116 servable stories have
  `last_served_at IS NULL`, 2 of them at stage 6, while every fixture row gets an
  id above `max(id) = 8936`. A real story wins. Rungs 3 and 4 cannot be reached at
  all while every stage holds something, because a request at stage S is answered
  by the highest stage <= S, which is S.
- **Commit the hide and restore the statuses in a `finally`.** It writes to the
  shared pool. A crash between the two commits leaves every servable story
  `rejected` — an empty pool for every reader — and `make warm`, `warm-check` and
  `make eval` would all see it. Unacceptable while any of them may be running,
  which is always.
- **A private database or schema per test.** `storyforge.db.engine` is
  module-level and the app binds to it at import, so re-pointing it per test is a
  larger and riskier change than a transaction, and migrating + seeding a fresh
  database per test costs seconds per test.
- **A temp table shadowing `stories` through `pg_temp`.** Lock-free and fully
  isolating, and rejected because it changes WHICH TABLE the endpoint queries: the
  tests would stop exercising the real schema, and permanent-table FKs cannot
  reference temp rows.
- **DELETE the live rows inside the doomed transaction** instead of restatusing
  them. `story_segments`, `questions`, `readings` and `answers` all cascade off
  `stories`; it is a far larger write for no gain, and the predicate
  `_SERVE_SQL` actually reads is `status`.

**8.94 — What is NOT fixed here, stated rather than left to be discovered.**

The four `pool` tests now pass against any pool, empty or full. The other eleven
tests in the file still require `make seed && make warm`, exactly as the module
docstring has always said, and that is a decision and not an oversight:
`test_least_recently_served_rotation` needs two real stage-1 stories,
`test_rejected_stories_are_never_served` needs a real `rejected` row,
`test_fallback_never_serves_a_story_above_the_requested_stage` sweeps the live
pool for invariant 6, and `test_a_real_node_produced_question_never_ships_its_
answer_key` sweeps it for answer-key leaks. Converting those to fixture-owned
data would delete the live sweeps that 7.18 defends and that caught 7.14 — it
would be the weakening this whole block exists to refuse. `FIXTURE_BASE_STAGE = 6`
is a constant with one requirement, that a stage exist below and above it inside
1..12, and it is asserted at module scope rather than left as a comment.

**Measured, after the change. Every number below is from a private `pg_dump` copy
of the live database, because `make warm` and `make eval` were writing the shared
one throughout (STATE.md's recipe, and the reason 8.47 exists).**

Two copies, deliberately at the two extremes the fixture used to sit between:
`sfjob890` — the live pool, **all twelve stages populated, 116 servable and all
116 answerable** — and `sfempty890`, the same database after `TRUNCATE stories
CASCADE`: **0 stories, 0 questions, 12 stages and 3 themes still seeded.**

- **PROVED — the four tests hold at both extremes.** `tests/test_stories_api.py`
  went from `14 passed, 4 errors` to **`18 passed`** on the full copy, and the four
  `pool`/`shared_pool` tests pass on the EMPTY copy as well. The old fixture could
  not run against either: it needed a pool that was neither.
- **PROVED — no real row is written.** Running only the four tests leaves
  `md5(string_agg(id||status||last_served_at))` over all 348 `stories` rows
  **identical**, with `themes`/`questions`/`story_segments` at 3/190/2304 before
  and after and **0 `zz-fixture%` themes left behind**. Over the whole file that
  md5 DOES move — that is the eleven non-`pool` tests stamping `last_served_at`,
  which they have always done and which 8.94 keeps.
- **PROVED — the mechanism is real, not nominal.** Probed inside the fixture
  against the live engine: `SHOW transaction_isolation` → `repeatable read`, the
  hide moved **116** rows, and `get_session in app.dependency_overrides` → True.
- **PROVED — the ladder is still what these tests test.** Sabotage 1, the one that
  matters: `stage_id <= :stage` → `stage_id <= greatest(:stage, 12)`, so the ladder
  may CLIMB. `test_a_stage_with_no_pool_falls_back_to_the_nearest_lower_stage`
  fails with its own words — `AssertionError: the ladder climbed a stage (STATE
  invariant 6)` — **on the full copy AND on the empty one**, which is the whole
  point: the isolation, not the live pool, is what makes the invariant testable.
  Ten of the file's eighteen tests go red under it on the full copy.
- **PROVED — rung order too.** Sabotage 2: `ORDER BY stage_id DESC` → `ASC`
  (nearest-lower becomes farthest-lower). Both ladder tests go red on both copies.
- **PROVED — the isolation itself is load-bearing.** Sabotage 3: delete the
  `app.dependency_overrides[get_session] = _shared_session` line. All four tests
  fail, because the endpoint can no longer see rows nobody committed. A fixture
  whose isolation could be removed without anything going red would be decoration.
- **PROVED — order independence, which is new.** 18/18 three times under
  `-p no:randomly` and three times under pytest-randomly's own seeds. The DTO
  test's docstring records a real flake — it "passed in a whole-file run and
  failed under `-x`" because `_SERVE_SQL` orders on `last_served_at` and earlier
  tests moved it. It cannot recur: the `pool` tests no longer share a pool with
  anything.
- **PROVED by reading, not by running** — `warm._COVERAGE_SQL` is `stages CROSS
  JOIN themes`, so a committed `zz-fixture-*` theme really is 12 pairs at depth 0
  and really would have made a concurrent `make warm-check` exit non-zero. That is
  a static proof about the query, and it is what 8.91's first consequence rests on.
- **ASSUMED, and not provoked**: the serialization-failure cost of REPEATABLE READ.
  It follows from Postgres's semantics for a write/write race against the
  hide-`UPDATE`, and no such race was staged. If it ever fires it is a loud,
  retryable error, which is the direction chosen over a silent flake.

Full gates on a fresh copy of the live database: **775 passed, 2 skipped, 0 failed,
0 errors** (`.venv/bin/python -m pytest -p no:randomly`, 72s) and
`.venv/bin/ruff check storyforge tests scripts` → `All checks passed!`.

**Do the four tests still test what their names claim? Yes, and one of them tests
it for the first time against data that cannot drift.** Not one assertion in any
of the four was modified, added to or removed; the diff against `538a08b` deletes
only the class docstring, the live-pool probe, the precondition, `stop()`, the old
`pool` fixture, and the answer-key test's fixture parameter. `test_empty_pair_…`
still proves rung 2 beats rung 3 (sabotage 2 makes it say so), `test_a_stage_with_
no_pool_…` still proves rungs 3 and 4 and that the ladder never climbs (sabotage 1
makes it say so, at both pool extremes), `test_request_returns_a_full_dto` still
pins §11.2's whole DTO against a story it wrote, and the answer-key sweep still
reads every live node-written `tap_word` row (8.92 is the note about how nearly it
did not).

## Phase 8 — the admin portal's BACKEND (§9 lines 232-234, §11's "Admin portal", §17 steps 1, 2 and 4)

Numbered 9.1-9.19 to open a fresh block clear of every 8.x above. Logged
**before** the code, per §0. Everything marked **PROVED** was measured in this
repo with the command shown; everything else is reasoning and is labelled
**ASSUMED**. New file: `services/api/storyforge/api/admin.py`. Touched:
`storyforge/main.py` (one `include_router`) and `tests/test_admin.py` (new).
Nothing in `apps/web/**`, `pipeline.py`, `eval.py`, `warm.py` or `auth.py` is
modified — see 9.14 for the one place that was tempting and why it was not taken.

Four of §17's six demo steps need this and none of it existed: step 1 is the
playground (`POST /api/validate`, which already exists and is NOT duplicated
here), step 2 is the allowed-words browser, step 4 is admin cold-generate with
live pipeline states, and step 5's report is a view of `eval_runs`.

**9.1 — §9 names three eval-run routes; this ships five, and the two extra ones
are the honest cost of a route that spends money and one that takes 25-60
seconds.** §9 lines 232-234 are `GET /api/admin/eval-runs`, `POST
/api/admin/eval-runs`, `GET /api/admin/eval-runs/{id}` and `GET
/api/admin/allowed-words?stage=N`. §11's "Admin portal" paragraph adds a
"generate cold story button that shows live pipeline state transitions" with no
route spelled for it, so `POST /api/admin/cold-generate` and `GET
/api/admin/cold-generate/{job_id}` are additive and are named here rather than
left for a reader to infer. No route outside `/api/admin/*` is added, so the
role gate is uniform by construction: one `dependencies=[Depends(require_admin)]`
on the router, not a decision per handler.

**9.2 — `GET /api/admin/eval-runs` returns an ENVELOPE, not a bare array.**
§9 does not say. Pagination needs a `total` that a JSON array has nowhere to put,
and the in-process launch records (9.9) have to reach the UI on the same poll or
the portal needs a second timer to discover that the run it started exists. So
`{total, limit, offset, runs[], launches[], budget{}}`.

**9.3 — the list omits `eval_runs.metrics` and projects a summary out of it
instead.** Run 114's `metrics` is §14's whole aggregate — `by_stage`, `by_theme`,
per-stage bins, `below_gate_detail`, the moderation reject lists. Five of those in
one list response is hundreds of kilobytes to render a table of five rows. The
summary is derived, never stored, so it cannot drift from the metrics it reads;
`GET /api/admin/eval-runs/{id}` returns the aggregate verbatim.

**9.4 — `POST /api/admin/eval-runs` is ESTIMATE-BY-DEFAULT. A request with no
`budget_usd`, or with `confirm` left false, makes zero API calls and returns the
projection.** This is the "confirmation path" the brief demands, and it is a path
rather than a flag because the estimate response carries the exact values the
launching request has to echo. A full matrix is 180 cells; `docs/EVAL.md` records
run 114 at **$5.41 and 17m49s wall clock at concurrency 6**. A button that could
start that on one click, with the CLI's own $15 default silently inherited, is the
defect this entry exists to make impossible.

**9.5 — a hard per-request ceiling, `MAX_BUDGET_USD = 8.0`, below the CLI's own
`DEFAULT_BUDGET_USD = 15.0`.** `--budget` defaults to 15 in `eval.main` and the
brief says to mirror that; mirroring the *mechanism* is right and mirroring the
*number* is not. 8.0 is above the measured full-matrix actual ($5.41) with ~48%
headroom for a worse rewrite rate, and below what a shell can authorise
deliberately. A request over the ceiling is 422 with both numbers in the detail.
The `--budget` semantics are unchanged: `run_full` checks it against the RUNNING
ACTUAL after every story (DEVIATIONS 7.44), so this is a cap on spend, not on a
projection.

**9.6 — a PROCESS-cumulative ceiling too, `PROCESS_BUDGET_USD = 16.0` (two
ceilings' worth), because one-at-a-time bounds concurrency and not spend.** With
only a per-run cap and a busy check, ten sequential clicks are ten runs. The
counter sums the `budget_usd` each launch COMMITTED, not the actual it spent —
the conservative direction, and the only one available while a run is still in
flight. It resets when the process restarts, which is stated rather than hidden:
this is a guard against a repeated click in one sitting, not an accounting system.

**9.7 — one eval run in flight PER PROCESS, and a stale `running` row in the
database is reported, never blocking.** `eval_runs` currently holds `id=112,
status='running', 0 items` — a run interrupted before it wrote anything, in
2026-07-30. A busy check that keyed on the DATABASE would make this endpoint
permanently 409 in exactly the database the demo runs against, so the block is on
this process's own launch registry and the DB's `running` rows travel in the
response as `stale_running_run_ids` for an operator to see.

**9.8 — the run executes IN PROCESS as an `asyncio.Task`, and its `run_id` is
DISCOVERED rather than assigned.** `eval.run_full` creates its own `eval_runs`
row; giving the endpoint the id up front would mean either a new parameter on
`run_full` (a concurrently-owned file) or `--resume`'s trail machinery abused to
adopt a pre-made row, which would write a resume entry about a run that never
paused. So the launcher records `max(eval_runs.id)` first and polls for the first
id above it. ASSUMED, and the failure is benign: if the row is somehow not
observed, `launch.run_id` stays null, the run still completes and still writes its
row, and the list shows it. A subprocess was the alternative — §14 does call the
CLI canonical — and was rejected because it needs stdout parsing and orphan
management to learn the same thing, and because `run_full` is already a coroutine.

**9.9 — the launch registry is in-process and EPHEMERAL, and says so in every
response.** A launch record is lost on restart; `eval_runs` is the durable record
and `--resume RUN_ID` is the recovery path, both unchanged. The registry keeps the
last 10 launches. Nothing in the product reads it except the portal.

**9.10 — `GET /api/admin/eval-runs/{id}` paginates `eval_run_items` (default 50,
max 200) and returns `eval_runs.metrics` whole.** 180 items x §14's per-story
metric row is the one part of a detail response that grows without bound, and it
is also the part a metrics table does not need — `metrics.by_stage` already
carries the per-stage aggregates §11 asks for. `criteria` is computed by calling
`eval.criteria_verdict` on the stored metrics rather than reimplemented, so the
portal cannot disagree with `make eval`'s exit code (§14: "the portal is a view").

**9.11 — an unknown eval-run id is a plain 404 and DEVIATIONS 6.9's
indistinguishability rule does not apply, because there is no owner to be
indistinguishable from.** 6.9 exists so a kid or reading id cannot be enumerated
across families. An `eval_runs` row belongs to no family; every admin may read
every one; there is no "someone else's run" whose existence a 403-versus-404
difference could confirm. The rule that DOES apply here is the one the brief
states: **an admin route must not become a data bypass for children's records.**
No route in this file selects from `kids`, `readings`, `answers` or `gpc_stats`,
and a test asserts that by reading the module's SQL.

**9.12 — `GET /api/admin/allowed-words` is page-mandatory: `limit` defaults to
100 and is capped at 500.** §5.4 expects 150-2,500 words per stage and the real
table holds **9,360 at stage 12** (157 at stage 1, 64,296 in total), so an
unpaginated route is a 9,360-row JSON response for the one screen §17 step 2
demos. Each word carries `in_prompt`, computed from the stage's 400th `freq_rank`
(§6's `PROMPT_WORD_LIMIT`), because "every word a stage-3 reader may see" and "the
400 the generator is actually shown" are different claims and the browser is where
they can be compared. `q` is a case-insensitive substring filter — SQL `LIKE` with
the wildcards escaped, so a `%` typed into the demo's search box is a literal.

**9.13 — cold-generate reports node transitions by POLLING, not SSE. Four
reasons, and the auth one is decisive.** (a) **Auth**: `EventSource` cannot send
an `Authorization` header, so an SSE stream behind `require_admin` means putting a
Clerk session token in a query string, where it lands in access logs and
`Referer`. Every other route in this service takes its token from the header
(`auth.bearer_token` is header-only, deliberately, to avoid a CSRF surface);
inventing an exception for a progress bar is the wrong trade. (b) **Correctness**:
each poll returns a COMPLETE snapshot, so a dropped, late or duplicated poll loses
nothing — there is no event ordering, no `Last-Event-ID` resume, no heartbeat, and
no missed-event semantics to get wrong. `seq` is a monotone counter the client can
use to notice growth, not a cursor it has to honour. (c) **Transport**: a buffering
proxy turns SSE into a stream that arrives all at once at the end, which is the
exact failure that would make the demo look broken on the deployed URL, and §3's
target is DigitalOcean App Platform. (d) **Granularity is free here**: a cold
generation is 25-60 s (`docs/EVAL.md`: p50 GENERATING ~20 s, and a 30-word stage-1
story measured 24.60 s under DEVIATIONS 8.4), so a 750 ms poll resolves every node
transition a human can see. The cost is one timer in the SPA, which the frontend
agent has to write either way.

**9.14 — the transitions are captured by wrapping `obs.observation`, gated on a
`ContextVar`, and `pipeline.py` is NOT modified.** §7's machine already emits one
span per node with the input and output the demo needs — `VALIDATING`'s output is
`{decodability, counts, violations, distinct_story_words, passes_gate}`, which is
§17 step 4's "the validator caught it" in machine-readable form, and
`REWRITING`'s output is the swapped text. `run_pipeline` takes no callback, so
the choice was: add one to a concurrently-owned file, re-implement the state
machine in `admin.py` (two state machines, one of which is not the one that runs),
or observe the spans. The wrapper is installed once at import and is **inert
unless a cold-generate job set the ContextVar** — outside a job it calls the
original with the original arguments and returns what it returned, so every other
caller of `obs` (every `make warm` story, every eval cell, every kid request) is
byte-for-byte unaffected. A `ContextVar` and not a module flag: `asyncio.Task`
contexts are copies, so the recorder cannot leak into a concurrent request even
though the patch is global. This is still a monkeypatch of another module's
function and is the least defensible thing in this block; it is recorded here in
those words. Sabotage-checked in both directions (see PROVED below).

**9.15 — cold-generate PERSISTS NOTHING. No `stories` row, no `story_segments`,
no `questions`.** The obvious implementation writes `status='warm'`, and that is
the DEVIATIONS 8.50 trap exactly: a warm story with no `questions` row is a
dead-end session that `_SERVE_SQL` now refuses, and six of them shipped last time
something wrote warm rows without enrichment. `status='eval'` would need an
`eval_runs` row to hang off. So the story lives in the job snapshot and dies with
the process. Consequence, stated: the trace id is NOT written to
`stories.langfuse_trace_id` for a cold-generate story, because there is no story
row — §15's guarantee is about the pipeline's persisted output and is unchanged
for `warm` and `eval` stories. It is in the response instead (9.18).

**9.16 — cold-generate takes `stage_id` and `theme`, and there is no `kid_id` on
it at all.** DEVIATIONS 6.10 keeps role and ownership independent, and an admin is
not an owner: `GET /api/kids` returns an admin's OWN kids and nobody else's,
whatever their role. (The brief for this phase said an admin sees an empty kid
list. That is true of the §18 demo admin — `demo_local_parent` owns 0 kids — and
**PROVED false of the real Clerk admin**, which now owns 5: `user_3HBc8LNBQdWzl…`
got `200` and five of its own children on `GET /api/kids` during the live boundary
run below. The property that matters is unchanged and is the one 6.10 states —
role does not grant ownership — so the design holds and only the anecdote was
stale.) A "generate for this child" parameter would be the first admin route to
read a `kids` row, and it would read it for a child the admin does not own — which
is the data bypass the brief names. §11's button is about the orchestrator, not
about a child, so nothing is lost. `POST /api/stories/request`'s admin-only
`stage_id` preview knob (DEVIATIONS 7.21) is the precedent for a stage with no
child in the room, and it is the same shape.

**9.17 — one cold-generate job at a time per process, which is what bounds its
spend.** A cold generation is ~$0.03 (`docs/EVAL.md`: $0.0315 per accepted story),
so a per-run budget parameter would be ceremony. The 409-while-busy is the cap: a
repeated click cannot outrun one generation, which bounds the burn at roughly
$0.03 per 30 s, or ~$3.60/hour, and only for an authenticated admin. Stated rather
than left as an inference, because "it is only three cents" is how a hole gets
argued for. The last 20 job snapshots are retained in memory and then dropped.

**9.18 — the Langfuse trace id is on the snapshot from the moment the ROOT span
opens, and no trace URL is invented.** §17 step 4 shows the trace, so the id has
to be reachable while the run is still going, not only in the terminal result —
the recorder reads `trace.trace_id` on entry to `story_pipeline` and publishes it
immediately. What is NOT returned is a clickable URL: Langfuse's console path
contains a project id this service never sees, and a guessed link that 404s during
a demo is worse than an id and a host. `langfuse.enabled` is reported too, because
`obs` is inert without keys and a null trace id needs to be distinguishable from a
broken one. `obs.flush()` is called on a worker thread after the job finishes so
the trace is visible immediately; the obs docstring's "never on the request path"
holds — this is after the response, on a background task, off the event loop.

**9.19 — every route here is 403-for-a-parent, not 404, and that is `require_admin`
unchanged.** A route's existence is public (it is in the OpenAPI schema), so there
is nothing to conceal, and 6.9's 404 rule is about ownership. Proved live against
a server with `DEMO_BYPASS_AUTH=false` in both directions — see PROVED below —
because the brief records that testing this against a bypassed server has produced
false conclusions here twice, and `/api/health`'s `demo_bypass_auth` field
(DEVIATIONS 7.25) is what settles which server is answering.

### What was measured — 9.1-9.19

Gates, on a tree where another agent was concurrently editing `pipeline.py`:
**852 passed, 2 skipped** (`.venv/bin/python -m pytest -p no:randomly`, 85s) —
775 was the baseline, 66 of the new ones are `tests/test_admin.py` and the rest
arrived with the concurrent work. `ruff check storyforge tests scripts` passes for
every file this block touches; it reports one pre-existing `E501` in
`storyforge/pipeline.py:223` (`story_word_verdict`), which belongs to that other
agent's 115-line in-flight change and is **not** mine to fix. One suite run
reported `test_llm.py::test_the_two_copies_of_the_token_ceiling_agree` red; it
passes alone and passed on the next full run with `md5 storyforge/pipeline.py`
identical before and after, so it was that file being saved mid-run —
`inspect.getsource` reading a file another process is writing. Noted rather than
chased, because STATE.md already warns that a concurrent writer makes this suite
lie.

**PROVED — the auth boundary, live, against a real server with the bypass OFF.**
`uvicorn` on port 8077 with `DEMO_BYPASS_AUTH=false`; `GET /api/health` returned
`"demo_bypass_auth": false, "principal": null` and every response carried
`x-storyforge-auth: enforced`, which is what settles which server answered
(DEVIATIONS 7.25). **The garbage-token check was run first, before any conclusion**,
because the brief records that testing this against a bypassed server has produced
false conclusions here twice. On all six routes: no token → `401 missing bearer
token` with `WWW-Authenticate: Bearer`; `Bearer garbage.garbage.garbage` → `401
malformed token header`; `Basic …` → 401; empty bearer → 401; a real RS256 JWT
carrying `public_metadata.role=admin` signed by a key Clerk never published → `401
unknown signing key`; `alg: none` carrying the same claim → `401 unsupported
signing algorithm 'none'; only RS256`. **Zero 5xx and zero tracebacks** in the
server log across the whole sweep. Then, with real Clerk Backend-API session
tokens against the same process: the admin (`user_3HBc8LNBQdWzl…`, whose
`public_metadata.role` reads `admin`) got **200** on `GET /eval-runs` and `GET
/allowed-words?stage=3`, **200** with `mode=estimate, launched=false` on `POST
/eval-runs`, **422** on `budget_usd: 99` naming the $8.00 ceiling, and **404** on a
bad theme; a freshly created probe user with no role got **403 "admin role
required (§9)" on every one of the six routes** and **200** on `GET /api/kids`,
which is what proves the 403s were the role gate and not a broken token. Both
sessions were revoked (`200`) and the probe user deleted (`200`).

**PROVED — cold generation works end to end, and persists nothing (9.14, 9.15).**
One live run, stage 3 / dragons, `$0.0262`. The recorded chain was
`RECEIVED(job) → GENERATING(0) → VALIDATING(0) → MODERATING(0) → OUTCOME(job)`,
observed growing across polls (`revision` 4 → 10 → 15). `VALIDATING`'s node detail
carried the validator's own verdict — `{"decodability": 1.0, "counts": {...},
"violations": [], "distinct_story_words": ["cave","dragon","wings"],
"passes_gate": true}` — and `GENERATING`'s carried
`{"model": "claude-sonnet-5", "input_tokens": 3257, "output_tokens": 1469,
"cost_usd": 0.022612}`. The Langfuse trace id
`ebcb0cd149fc8164d85d55d4afc54a52` appeared on the snapshot and **occurs in zero
`stories` rows**; the story's full text (`"…It had big wings. It went up fast."`)
occurs in **zero** `story_segments` rows. The one row matching its first sentence
is story 9414, `status='eval'`, a different trace id, written by run 114 — which is
how likely a stage-3 dragon opener is, and exactly why the trace id rather than the
prose is the evidence.

**PROVED — the story that did NOT happen.** The live generation passed at attempt 0,
so §17 step 4's rewrite-loop catch was not observed live and **no money was spent
hunting for one**. `_rewrite_loop` is a pure function over
`PipelineResult.attempts`, so it is proved by test instead, in both directions: a
rewrite that fixes `gleaming` reports `caught: ["gleaming"], fixed: ["gleaming"],
still_present: [], new_words: ["big"]`, and a rewrite that keeps it reports
`fixed: [], still_present: ["gleaming"]`. Reporting the second as fixed would be
the flattering-eval mistake §14 is written against.

**PROVED — every guard bites, checked the way AUDIT-PHASE7-FIXES F2 says to check.**
F2's lesson is that adding `ANSWERABLE_SQL` made five existing guards vacuous
because no fixture wrote a `questions` row, so each guard here was sabotaged and
the failures counted:

| sabotage | what went red |
|---|---|
| `dependencies=[Depends(require_admin)]` off the router | **13**: `no_token` x4, `garbage` x4, `parent` x4, the demotion test. Only the four GETs — the two POSTs also carry a handler-level `Depends(require_admin)` for `started_by`, so they are gated twice |
| `require_admin`'s `raise` removed (role check dead, authentication alive) | **7**: `parent` x6 — now including both POSTs — and the demotion test. The `no_token`/`garbage` tests correctly stay green, because authentication is not what was broken |
| `verify_token` refuses everything (a broken fixture) | **the positive control**, by name: `a VALID admin token was refused 401 … the forgery assertions above therefore prove nothing` |
| the LIKE escaping removed | `search_is_a_literal_substring` |
| `in_prompt` hardcoded `True` | `marks_what_the_prompt_actually_sees` |
| the detail route drops `metrics` | 3 tests, including the one that compares against `criteria_verdict` itself |
| `_STALE_RUNNING_SQL` changed to `SELECT id FROM kids` | `no_admin_route_reads_a_child_table` |
| the recorder made unconditional (ContextVar guard gone) | `no_job_in_context_the_recorder_is_invisible` |
| `_install_node_recorder()` never called | `recorder_is_installed_once`, `node_chain_is_recorded` |
| the ROOT span counted as a node | `node_chain_is_recorded` |
| `MAX_BUDGET_USD` raised above the CLI's default | `budget_block_reports_ceilings` |
| the per-request ceiling check removed | `budget_over_the_ceiling_is_refused`, `assert 202 == 422` |

**PROVED — the vacuity that was actually there, and is now closed.** The third row
above is the F2 defect in this file, found by looking for it. With `verify_token`
refusing every token, the forgery test as first written passed **6/6 green** while
proving nothing: "a forged token is refused" and "nothing is accepted" are
indistinguishable from inside a test that only ever sends forgeries. It now ends by
handing the SAME route, on the same fixture, over the same transport, a VALID admin
token and asserting the answer is neither 401 nor 403 — so both halves have to hold
or neither means anything. `test_the_child_table_guard_can_fail` is the same idea
for the AST scan: it writes a throwaway module containing `SELECT display_name FROM
kids` and asserts the extractor finds it, because a scanner that silently matches
nothing passes every module ever written.

**PROVED — the last sabotage cost $0, deliberately.** Removing the ceiling check
lets the request through to a real launch, so that one run was made with
`ANTHROPIC_API_KEY=sk-ant-deliberately-invalid`. The test failed on `202 == 422` as
intended, `select count(*) from eval_runs where id > 114` returned **0** both
immediately and after a delay, and no billable call was made. Every other sabotage
in the table is free.

**PROVED — nothing leaked into the shared database.** `eval_runs` holds exactly
`2, 3, 112, 113, 114` before and after all of this; the `owned_run` fixture's rows
and the two throwaway `running` rows are gone. Total spend for this whole block:
**$0.0262**, one cold generation. `make eval` and `make warm` were not run.

**ASSUMED, and not provoked.** (a) 9.8's run-id discovery — the launcher polls for
`min(id) > baseline` and no launch was run to completion, so the discovery loop is
reasoned about rather than observed; its failure is benign by construction
(`run_id` stays null, the run still writes its own row, the list still shows it).
(b) 9.6's process ceiling was proved with registry entries placed by hand, not by
spending $16. (c) The stale-serve interaction between a launch and a uvicorn
`--reload` restart: a launch is lost from the registry and the `eval_runs` row is
left `running`, which is the state `id=112` is already in and which 9.7 exists to
tolerate. (d) 9.13's transport claim about a buffering proxy in front of SSE is an
argument from how proxies behave, not a measurement — no SSE implementation was
built to lose a race against.


## Phase 8 — the admin portal's FRONTEND (§11's route list, §11's "Admin portal", §17 steps 1, 2, 4 and 5)

Numbered 9.20-9.39, continuing the block the backend opened at 9.1. Logged
**before** the code, per §0. Everything marked **PROVED** was measured in a real
browser against a server reporting `demo_bypass_auth: false`; everything else is
reasoning and is labelled **ASSUMED**.

New files, all under `apps/web/src`: `lib/adminApi.ts`, `lib/validatorView.ts`
and their two test files, `ui/AdminShell.tsx`, `ui/VerdictMark.tsx`,
`ui/StatePill.tsx`, and five routes under `routes/Admin*.tsx`. Touched:
`App.tsx` (the route table), `lib/auth.tsx` (one guard), `styles/index.css` (one
plain-CSS block). Nothing in `services/api/**` is modified — the backend half of
this portal is 9.1-9.19 and is another agent's.

**9.20 — `/admin` is a route TREE and the validator playground is its index.**
§11's route list names one route, `/admin` (admin only), while §11's "Admin
portal" paragraph names four screens. Four screens behind one URL is either a
tab bar with no addressable state or a 3,000-line component; the tree is
`/admin` (playground), `/admin/words`, `/admin/evals`, `/admin/evals/:runId`,
`/admin/generate`. The playground is the index rather than a dashboard, because
§17 step 1 is the first thing a grader sees and a landing page in front of it is
a click between the demo and its opening line.

**9.21 — `RequireAdmin` does not check a role, because nothing on the client is
allowed to. It checks a session; the API decides the role.** `lib/auth.tsx` is
explicit that "roles still come from the signed token, never from here", and
`auth.py` re-reads `public_metadata.role` from the JWT on every request so the
Clerk dashboard can revoke admin immediately. A client-side role test would be a
second opinion that can disagree with the only one that matters, and it would
disagree in the dangerous direction the first time Clerk's client cache is ahead
of a revocation. So the guard is exactly `RequireParent`'s check — signed out
redirects to `/sign-in?next=…` — and the role gate is the API's 403, rendered.

**9.22 — the 403 surface removed in 7.37 is restored, with its sentence verbatim,
and it is reached by a real 403 rather than kept as decoration.** 7.37 deleted
`ParentError`'s 403 branch after proving it unreachable: `require_admin` guarded
one route and no web code called it. This portal is the code that calls it, so
the branch returns — as `AdminError` in `ui/AdminShell.tsx`, not by reviving the
parent one, because the two surfaces answer to different rules (§01 forbids an
error state in kid mode, permits a plain one for a parent, and an admin should
see the status and the route). The sentence preserved in 7.37/6.20 is restored
word for word: it names the missing `public_metadata` claim and the Clerk
dashboard action, because Clerk's default session token carries no metadata and
every user then resolves to `parent` — a generic "forbidden" would send whoever
meets it into `auth.py` looking for a bug that is not there.

**9.23 — the shell PROBES the API once and renders nothing else until it
answers, so `/admin` is not a screen a parent can sit on.** The alternative is
per-screen 403 handling, which means the chrome, the stage selector and the
textarea all render for a parent and only the data is missing — a surface that
looks operable and is not. The probe is `POST /api/validate` with the text `cat`
at stage 1: it is the one admin route that provably existed before this portal
did, it is a pure function over a vendored dictionary plus one indexed `SELECT`,
and it spends nothing. Its cost is one extra request per admin page load.
ASSUMED: that a 200 from `require_admin` on that route implies a 200 from
`require_admin` on the others. That is true by construction today — 9.1 puts a
single `dependencies=[Depends(require_admin)]` on the whole admin router — and it
is why the probe is a `lib/adminApi.ts` function with one call site rather than
an inline fetch.

**9.24 — the playground opens ON the §17 demo, pre-filled, because the fixture
does not reproduce without `story_words`.** Measured live against
`DEMO_BYPASS_AUTH=false`: "The knight sees a beautiful dragon" at stage 3 with no
`story_words` returns **decodability 0.25, three violations, ZERO story words and
`dragon` as DECODABLE via schwa relaxation** — because `dragon` genuinely is
decodable at stage 3 (§5.2.6). The repo's own locked fixture
(`tests/decoder/golden.yaml::demo-sentence-stage-3`, `test_phase2.py`) is 0.0%,
three violations and one story word, and it gets there by passing
`story_words: [dragon]`. So the screen ships with the sentence, stage 3 and the
story word already in it: opening `/admin` IS §17 step 1, with no typing, and it
reproduces the fixture rather than a near-miss. Every field is editable and
nothing is hard-coded — the verdicts on screen are always the response's.

**9.25 — the playground exposes BOTH `extra_sight_words` and `story_words`, where
style guide §09 draws one "+ sight words" chip.** They are different inputs with
different effects on the number being demonstrated: a sight word is exempted from
scoring, a story word is the ≤3-per-story picture-chip budget §4 caps and is what
turns `dragon` amber. Shipping only the one §09 draws would have made 9.24's
fixture unreachable from the UI.

**9.26 — the words on screen carry the ADMIN's own casing, spacing and line
breaks; every verdict, and the word each verdict belongs to, come from the
response.** `POST /api/validate` returns tokens lowercased (`the`, not `The`) and
punctuation as its own token, in input order. Rendering the response's strings
verbatim would print "the knight sees a beautiful dragon" back at someone who
typed a capital, which reads as the app mangling the input. So a pure function
walks the response's tokens against the submitted text and slices the original
for display — apostrophe shapes normalised for the search only, since `’`→`'` is
one character for one character and leaves every index intact. When a token
cannot be found the response's own token is displayed instead, so the failure
mode is "lowercase", never "wrong word" or "missing word". Tested in both
directions.

**9.27 — the playground's verdict rendering is a NEW plain-CSS class family
(`.sf-tok-*`), not the existing `.verdict-*`.** Style guide §02's locked mapping
and §09's playground draw the same four verdicts two ways: §02 colours the *text*
(what `.verdict-*` does, and what `/` renders today), §09 fills the *background*
in the verdict tint and leaves the word in ink-900 at 13.4:1 — which is the right
call for a 26px reading line an adult is scanning for reds. Extending
`.verdict-*` would have repainted the landing page's artifact, which is proven
and shipped. Both families live OUTSIDE `@layer utilities` for STATE invariant
7's reason: they are chosen from data, and a layered rule whose literal class name
never appears in a scanned file is purged from the production bundle, silently
collapsing all four verdicts to plain ink. Both are also written as literals in a
`Record` in one component (`ui/VerdictMark.tsx`, the §12 contract's "the ONLY
place verdict colors appear"), so the two defences are independent.

**9.28 — a missing GPC is rendered grapheme-first with IPA, never as the raw id,
and `alignable: false` gets its own sentence instead of an empty list.**
`missing_gpcs` is `["igh|AY", "kn|N"]` — the id format from `decoder/types.py`,
with `.` joining multi-phoneme GPCs and `SIL` for a silent one. §17's line is
"violations light up red with missing GPCs NAMED", and `igh|AY` is not a name.
Rendered as `igh /aɪ/` and `kn /n/`, reusing `lib/phonics.ts`'s ARPABET→IPA table
rather than adding a second one. The harder case is `beautiful`: a VIOLATION with
an EMPTY missing set and `alignable: false`, because the 93-GPC inventory cannot
spell it at ANY stage (`B Y UW T AH F AH L` needs `eau`). "missing: nothing" is
the one rendering that would make the demo look broken, so that case says what is
true — the inventory cannot spell this word at any stage — which is §5.3's
declared non-goal and §18's inventory-size signal, on screen.

**9.29 — 300 ms debounces the TEXT and nothing else.** §5.6's number is about
keystrokes. A stage change, a story-word change and a sight-word change are single
deliberate acts that revalidate immediately: waiting 300 ms after a `<select>`
makes the screen feel broken, and it is the stage selector a grader will be
watching in step 1. The constant is exported and pinned by a test, so §5.6's
number cannot drift into a magic literal.

**9.30 — the allowed-words browser prints the real row counts, and §17's "611
words" is stale.** Measured against the live table: stage 3 holds **2,593** rows,
stage 1 holds **157** and stage 12 holds **9,360** (64,296 total). §17 step 2's
script says "611 words" and the brief says 9,359 at stage 12; neither matches the
database this demo runs against. The screen shows the server's `total`, never a
figure from a document, and the demo line is a documentation drift worth naming
rather than a number to reproduce.

**9.31 — cold-generate polls, at the 750 ms 9.13 specified, and the client owns
the stop condition.** 9.13 chose polling over SSE for four reasons of which the
decisive one is that `EventSource` cannot send an `Authorization` header. The
consequence on this side: every poll is a complete snapshot, so the UI is a pure
function of the latest one and a dropped poll costs nothing. Polling stops on a
terminal status, on unmount, and after a wall-clock ceiling — an un-terminating
job must not leave a timer running for the rest of the session.

**9.32 — the rewrite diff is rendered as the two texts, not as a word-level
diff.** Style guide §09 is explicit that "the rewrite catch is the story — make
sure the diff ('gleaming → shiny') is displayed, not just the state", and §17 step
4 says the same. A word-level diff of two model outputs is a diff algorithm plus a
tokenizer plus a rendering, and it can mislabel a whole re-clause as a swap. What
the API gives is each attempt's text and each attempt's violating words with their
missing GPCs, which names the caught word exactly and shows the replacement text
beside it. The word "gleaming" is not searched for; whatever the validator
actually caught is what is shown.

**9.33 — the eval detail renders `metrics.by_stage` as §17 step 5's table and
deliberately does not render the rest of the blob.** `eval_runs.metrics` carries
`below_gate_detail`, three moderation reject lists, per-stage bins, `by_theme`,
token censuses and model provenance. §11 asks for "metrics tables"; §17 asks for
"decodability table across 12 stages, cost, latency". The screen shows the
headline aggregates, the per-stage table, the §1 criteria verdict the server
computed with `eval.criteria_verdict` (9.10 — so the portal cannot disagree with
`make eval`'s exit code), and the below-gate rows when there are any. Everything
else is reachable as the raw JSON behind a disclosure, which is honest about being
a dump rather than dressing one up as a report.

**9.34 — launching an eval run from the browser is a two-step confirmation, and
the estimate is not editable into a launch by accident.** 9.4 made the API
estimate-by-default; this side does not paper over it with one button that posts
twice. The estimate is requested, its numbers are shown, and only then does a
second, differently-worded control post the launch echoing the values the estimate
returned. A full matrix is ~$5.41 and ~18 minutes; the ceiling is $8.00 per run
(9.5) and $16.00 per process (9.6), and both are shown next to the button rather
than discovered as a 422.

**9.35 — this is an adult surface: §11's 48px floor, and no kid-mode styling is
imported.** Card `surface="admin"` (1px rule, no shadow), IBM Plex Mono for data,
`min-h-tap`/`min-w-tap` on every control — never `tap-kid`, never `.sf-lift-kid`,
never `font-read` outside the one 26px validated-text line §09 draws in Andika.
Style guide §09's temperament is "an instrument panel, and it should look like
one", and §10's admin grid is 12 columns at max 1440px with a 16px gutter, which
is wider than the 1120px parent shell — so `AdminShell` is its own chrome rather
than `ParentShell` with a different heading.

**9.36 — §01 is inverted here on purpose: every failure names the status, the
route and the method.** "A child never sees an error" is about kid mode; a parent
gets a plain sentence and no jargon (7.32); an admin debugging a pipeline needs
the status code. So `AdminError` prints `POST /api/validate → 500` alongside the
prose, an unreachable server is distinguished from a refusing one (status 0 vs a
real code), and a 404 on an admin route says the route is absent rather than
"something went wrong" — which is the state four of these five screens were in
while 9.1-9.19 was being written.

**9.37 — the screens are typed against 9.1-9.19's stated shapes, with one
tolerant seam per response, and the seam is a tested pure function.** The backend
entries pin the envelopes (`{total, limit, offset, runs[], launches[], budget{}}`;
a summary on the list and `metrics` whole on the detail; `in_prompt` per allowed
word; a `seq`-carrying job snapshot). Where a field name is still an inference
rather than a quotation, one `normalize*` function per response reads the
plausible spellings and reports which one it found, in the manner `postAnswer`
already accepts three names for a graded answer. That keeps every guess in one
file with a test beside it instead of scattered through five components, and it is
why pointing a screen at a corrected shape is a one-line change.

**9.38 — the landing page's phase-0 note is corrected, not the landing page.**
`routes/Landing.tsx` carries hard-coded verdicts and a comment saying the live
`POST /api/validate` result is shown by "the admin playground (§5.6, phase 8)".
That playground now exists, so the comment is updated to point at the route. The
hard-coded artifact stays: `/` is public and `/api/validate` is admin-only, so a
signed-out visitor cannot be shown a live validation, and reaching for one would
mean opening the endpoint.

**9.39 — no component tests, unchanged from 7.30's decision; the new pure
functions get real ones.** `bun test src` covers `lib/` only and the components
are verified in a browser. The two new lib files are where the logic that can be
wrong without being visible lives — token alignment, GPC naming, response
normalisation — and they are tested. Baseline was 137 web tests; the count after
this block is in the PROVED section below.

### PROVED — the admin portal's frontend, measured in a real browser

Every claim below was driven through `~/.claude/skills/gstack/browse` at an
isolated `BROWSE_SERVER_PORT`, against a FastAPI on port 8091 started with
`DEMO_BYPASS_AUTH=false`, with a REAL Clerk session minted through the Backend
API. **Enforcement was confirmed first, not assumed**: `/api/health` reported
`demo_bypass_auth: false`, and through the Vite proxy `POST /api/validate` with no
token returned 401 and with `Bearer garbage.token.here` returned 401 — the check
the Phase 6 audit's finding 0 exists to make mandatory. Web gates: **`bunx tsc
--noEmit` clean, `bun test src` 201 passed / 0 failed (137 baseline, +64), `bun run
build` clean.**

- **PROVED — §17 step 1, the money shot, at stage 3.** "The knight sees a
  beautiful dragon" renders **0.0% decodability, 3 violations, 1 story word** —
  the locked fixture exactly (`tests/decoder/golden.yaml::demo-sentence-stage-3`).
  The GPCs are NAMED, not printed as ids: `knight → missing igh /aɪ/, kn /n/` and
  `sees → missing ee /iː/, s /z/`. `beautiful` says *the 93-GPC inventory cannot
  spell this word at any stage* rather than reporting an empty missing set. Screenshot
  taken in dev AND against the production bundle.
- **PROVED — the colours are the style guide's, by computed style, in `dist/`.**
  Read off the live DOM under `vite preview`: violation `background
  rgb(248,227,223)` = `#F8E3DF` (berry-100) with `text-decoration underline wavy
  rgb(192,69,59)` = `#C0453B` (berry-500); sight `#E2EDF2` + dotted `#3D7891`;
  story `#FBEFD4` + double `#C98A2E`; decodable `#E7F0E2` + solid `#4C7C51`; OOV
  `#F1E4C8` + strikethrough. STATE invariant 7 checked directly in the built CSS:
  all six `.sf-tok-*` rules and all five `StatePill` tone classes survive the purge.
- **PROVED — §5.6's debounce, by counting requests.** Six keystrokes fired back to
  back produced **exactly one** `POST /api/validate`. A stage change revalidated
  immediately (9.29): the same sentence at stage 9 became **66.7%, 1 violation** —
  `knight` and `sees` decodable once `kn` and `igh` unlock, `beautiful` still not.
  That progression is a better demo than the single frame and cost nothing to find.
- **PROVED — §17 step 2 against the real table.** Stage 3 shows **2,593 words**,
  the `in_prompt` column, and `freq_rank ≤ 2,221` as §6's 400-word cutoff. §17's
  script says "611 words" and the brief says 9,359 at stage 12; the database holds
  **157 / 2,593 / 9,360** at stages 1 / 3 / 12. The screen prints the server's
  numbers (9.30) and the documents are the things that are wrong.
- **PROVED — §17 step 5 against run 114.** Mean decodability 98.9%, **0 below the
  gate**, p95 rewrite depth 2, $5.41, and the four §1 criteria all `✓ met` —
  computed by the server calling `eval.criteria_verdict`, so the portal cannot
  disagree with `make eval`'s exit code. The 12-row per-stage table renders, as do
  the moderation rejects and the run's provenance. The stale `running` row 112
  renders as all dashes rather than crashing on its null aggregates.
- **PROVED — §17 step 4, twice, with real money (~$0.05 total).** `cg_7bbb7c36` at
  stage 3: `RECEIVED → GENERATING 4.8s → VALIDATING 95.8% → MODERATING approved
  1.4s → OUTCOME accepted`, $0.0151, Langfuse trace id
  `6ee49ab93c0db2d525eef94feeb944a2` on `us.cloud.langfuse.com`. `cg_030512bb` at
  stage 10 caught the rewrite loop: `GENERATING 7.6s → VALIDATING fail 93.8% →
  REWRITING n=1 25.3s → VALIDATING 100.0% → MODERATING rejected → OUTCOME
  moderation_fail`, and the diff panel named it in the server's own words —
  `caught: liked, opened, would, knew, friend → swapped out: … words that appeared
  instead: family, know, like, will`. **That run is also an unplanned demo of §13's
  three independent layers**: the validator took the story to 100% and the
  moderation node still refused it for `"It like to fly up and away."`
- **PROVED — the 403 surface 7.37 removed is reachable, and it names the CAUSE.**
  A temporary Clerk user with empty `public_metadata` was created, signed in
  through a ticket, and sent to all five admin routes. Every one rendered *"This
  session is not an admin"* with the 7.37 sentence verbatim — the default session
  token carrying no metadata, the Clerk dashboard action — plus `request POST
  /api/validate`, `status 403`, and the server's own `detail`. **No stage selector,
  no textarea, no data**: the shell probe (9.23) refuses to render the screen at
  all. `POST /api/admin/cold-generate` with that user's token was independently
  403 by `curl`, so the gate holds at both layers. **The probe user was deleted and
  its session revoked**; a `GET /v1/users` afterwards shows one user.
- **PROVED — signed out, `/admin/evals/114` redirects to
  `/sign-in?next=%2Fadmin%2Fevals%2F114`.** The deep link survives, so an admin
  who followed a link lands where they were going.
- **PROVED — §11's 48px floor, by computed geometry.** Every one of the 11
  controls on the playground measures ≥48px in BOTH axes. No `tap-kid`, no
  `.sf-lift-kid`, no kid-mode import anywhere in the portal (9.35).
- **PROVED BY SABOTAGE — the role-gating test is not vacuous.** The coordinator
  flagged the shape `docs/AUDIT-PHASE7-FIXES.md` F2 found five of: "a parent
  cannot reach /admin" passes just as happily when NOBODY can. So the status →
  copy decision was extracted to `lib/adminApi.ts::adminErrorCopy` and given a
  POSITIVE control. Both directions bite: making every status name the role claim
  turns **3** tests red, and making 403 fall through to the generic branch turns
  **2** red. Same for `transitionVerdict` — verdict-always-false fails 2, dropping
  the `approved` field fails 2.
- **A REAL BUG THE BROWSER FOUND, and it was a colour lie.** Every §7 node
  completes with `status: "done"`, the two whose whole job is to say no included.
  So the first version of the pill chain painted a **green `MODERATING rejected`
  and a green `OUTCOME moderation_fail`** — the label said refused, the hue said
  fine, and on a demo screen the hue is what gets read. That is §11's "never colour
  alone" inverted, which is worse than a missing signal. Fixed by reading the
  span's own verdict (`passes_gate`, `approved`, `outcome`) instead of its status,
  and locked with the fixtures from the real `cg_030512bb` snapshot.
- **A SECOND BUG THE BROWSER FOUND.** Run 114's wall clock rendered as
  `1068.2s`. `elapsed()` had ms and s tiers only, because §09's pills are seconds;
  an eval run is not. Now `17m48s`.
- **A THIRD DEFECT, found by reading `admin.py` after the fact.** The detail route
  paginates `eval_run_items` on `items_limit`/`items_offset`; this client was
  sending `limit`/`offset`, which FastAPI ignores. It read correctly by accident on
  the server's defaults and would have broken on page 2 of a 180-item run. Fixed.

**ASSUMED, and labelled as such.**
- **That a 200 from `require_admin` on `POST /api/validate` implies a 200 on the
  `/api/admin/*` routes** (the shell probe, 9.23). Read, not merely assumed —
  `admin.py` carries one `dependencies=[Depends(require_admin)]` on the router —
  but a future route that took its own dependency would slip past the probe.
- **The 409-while-busy path was never provoked.** One cold generation at a time is
  what bounds the spend (9.17); the screen disables its own button while a job is
  in flight, so reaching the 409 needs two tabs, and that was not staged.
- **The 12-minute poll ceiling and the eval LAUNCH were never exercised.** The
  estimate path was driven live and returns `mode: "estimate", launched: false`
  with all three cost projections; the launch would have spent $6.49–$8.00 on an
  18-minute matrix and was deliberately not run. So the two-step confirmation is
  proven up to the second click and not through it, and `mode: "launch"` (the
  server's word — NOT `"launched"`, which is the boolean beside it) is verified
  against the backend's published contract rather than against a response.
- **No component tests**, unchanged from 7.30 (9.39). The four screens are
  browser-verified; the logic that can be silently wrong was moved into `lib/` for
  exactly that reason.


**8.100 — `--concurrency` had never run above 1, and this entry was cited before
it existed.**
`eval.py`'s worker passed the ENCLOSING `AsyncSession` to every cell, so
`asyncio.gather` drove one session concurrently and raised "This session is
provisioning a new connection; concurrent operations are not permitted". The flag
was accepted, printed in the report's reproduction command, and recorded in the
run config — and had never once executed. The first full matrix was serial not by
choice but because anything else crashed on the first batch.
Fixed with a session per cell. That also strengthens `--resume`: each cell now
commits on its own connection, so an interrupted run keeps every measurement it
finished rather than depending on the enclosing transaction. Full matrix wall
clock 1068s at `--concurrency 6` against roughly two hours serial.

**Process failure, logged because it is exactly what this file exists to prevent:**
I cited `8.100` in `storyforge/eval.py` and `scripts/eval_doc.py` and in the commit
message BEFORE writing this entry, and then shipped without it. §0 requires the
deviation to be logged before the change is made. `docs/AUDIT-PHASE7-FIXES.md`
caught it by resolving every citation in the repo — 159 cited, 158 resolved — and
the one dangling reference was mine. A citation to a nonexistent entry is worse
than no citation: it reads as though a rationale exists and was reviewed.

---

## Phase 8 — the rewrite prompt that could not state the cap, and the safety boolean that still precedes its reason (`docs/AUDIT-PHASE7-FIXES.md` F1 and F9)

Numbered 9.40-9.59. Two findings, both confirmed live by the Phase 7 adversarial
audit, both the same shape: a structured prompt that asks for a conclusion the
prompt gave the model no way to reach. F1 is on the generation half and is
child-facing today; F9 is on the safety half. Written BEFORE the changes, per §0.

**9.40 — F1's root cause is WIDER than the story-word cap: the rewrite prompt
drops the story-word bank, the theme and the length target, so on any rewrite the
model cannot see the words that carry the theme.**
`pipeline._call` sends `messages=[{"role": "user", "content": user_block}]` — one
message, no history — and `run_pipeline` replaces `build_request_block`'s output
with `build_rewrite_block`'s on the first failure. `build_vocabulary_block` (the
cached `system` half) carries PERMITTED and SIGHT words and nothing else, because
story words are per-request. So from attempt 1 onward the prompt says "every
replacement must come from the permitted, sight, or story lists" while the story
list, the theme word and the word count are all absent from it.
PROVED from run 114, no API call: **0 of 108** stories that needed no rewrite
ended with zero distinct story words; **23 of 72** that were rewritten did
(31.9%), and the rate climbs with depth — 14/56 at depth 1, 8/15 at depth 2, 1/1
at depth 3. The audit's 9 cap-only cases are a subset; the other 14 are ordinary
decodability rewrites that lost the theme for want of the bank. So the fix
restores the offered bank (with glosses), the theme and the length bounds to the
rewrite block, and does not stop at naming the cap.

**9.41 — the cap failure is stated as DROP ONE, KEEP THE REST — never as "remove
story words".**
§5.5 and STATE invariant 3: the cap counts DISTINCT story words and repetition is
correct decodable-reader behaviour. The failure being fixed is a dragons story
with no dragon in it, so "remove the story words" is not an acceptable resolution
of a cap failure — it is the defect. The problem line therefore names the words to
KEEP, names the one word to remove, and repeats that repeating the kept words is
free.

**9.42 — which story word to drop is decided by the CODE, not left to the model.**
Ranked by occurrence count descending, then alphabetically, keeping the top
`MAX_STORY_WORDS`. Rationale: the most-repeated word is the one the story leans
on, so dropping the rarest is the smallest edit and the least likely to unravel
the plot — and a deterministic choice makes the instruction concrete ("keep
dragon, cave and fire; remove wings") instead of a decision the model has to make
inside a prompt that has already proved it resolves ambiguity by deleting the
theme. `report.words` carries every occurrence with its verdict, so no new data is
needed.

**9.43 — `build_rewrite_block` gains a `theme` argument.**
The theme is not on `Vocabulary` (it is a parameter of `load_vocabulary`, not a
field of the result) and the rewrite block now has to name it. Added as a
keyword-only parameter with an empty default so the theme section is omitted
rather than mis-rendered if some future caller does not pass one. `run_pipeline`
is the only production caller.

**9.44 — the headline sentence is now conditional.**
"Your story used words this child cannot read" is FALSE on a cap-only failure —
the audit's story 9257 was at 100% decodability when that sentence was sent to it.
The lexical headline is preserved verbatim for the lexical case, and a cap-only
failure gets its own true sentence.

**9.45 — `build_rewrite_block` may never again emit an empty problem list, and
that is asserted rather than assumed.**
All three `passes_gate` conditions are now describable, so an empty `lines` is
unreachable from a real gate failure. It is still a live prompt, so the function
carries a final defensive line naming the gate generically, and a test enumerates
every failure mode of `passes_gate` and asserts each produces at least one problem
line and never the string `PROBLEMS (0)`. A prompt that instructs the model to
"change only what is listed" and lists nothing is the whole of F1; the guard is
against the class, not the instance.

**9.46 — none of this touches the cached prefix.**
Everything added is in `messages`, after the `cache_control` breakpoint.
`SYSTEM_RULES` and `build_vocabulary_block` are unchanged, so STATE invariant 10
holds and `tests/test_llm.py`'s frozen-prefix assertions are untouched. A rewrite
already paid full input price for the body it is quoting; the bank is ~8 lines.

**9.47 — the pool repair is a RE-STATUS, never a delete, and the servable defect
is exactly one story.**
`answers.question_id` is `ON DELETE CASCADE` and `questions.story_id` is
`ON DELETE CASCADE`, so deleting a story destroys the §8 mastery evidence
(`mastery.MASTERY_QTYPES`) hanging off it — an earlier agent already destroyed one
answer row that way. Story 1661 (stage 1, dragons, `warm`, `story_words = {}`,
text "Dan sat in a camp…") reads as a dragons story with no dragon and is
servable, so it moves to `rejected` — the status the pipeline itself uses for a
story that must not reach a child — which keeps every `questions`, `answers`,
`readings` and `story_segments` row addressable. Verified before the change that
stage-1/dragons still holds >= 3 servable stories without it, so `make warm-check`
does not go red; a replacement is generated through the real pipeline so the pair
does not merely stay at its floor.

**9.48 — the other five servable stories with `story_words = {}` are NOT the same
defect and are left alone.**
7270, 7316, 7324, 7336 and 7342 have empty `story_words` but their text carries the
theme in permitted words ("A dragon had a cave. It had wide wings…"). Empty
`story_words` means "no OFFERED bank word was both used and claimed", which is
invariant 2 working, not a missing theme. Two of them (7342's `burrow`) even use
the theme word without claiming it, so it scored as a violation. Re-statusing a
story a child can enjoy would be a worse outcome than the metric being ugly.

**9.49 — the 30 `eval` rows with `story_words = {}` are measurement records and
are not repaired.**
`status = 'eval'` is refused by `_SERVE_SQL`, by `GET /api/stories/{id}` and by
`POST /api/readings` (STATE invariant 14), and `eval_run_items.story_id` points at
them: they are the evidence for F1 and for `docs/EVAL.md`'s numbers. Mutating them
would corrupt the run they document. No child can reach one.

**9.50 — F9: `reason` is declared BEFORE `approved`, for the reason 8.77 already
proved on the grammar half.**
Structured output is emitted in schema order. 8.77 moved `grammar_note` ahead of
`well_formed` because the boolean-first order made the judge commit and then
rationalise, and it fixed three false positives. `approved` was left ahead of
`reason` — the identical defect on the half that decides child safety. The
auditor's controlled experiment (field order the only variable) flips story 9136
from REJECTED 3/3 to approved 3/3 and 9190 to 2/3 while a true positive (9305) and
a control are unmoved. The judge's own words on 9190 give the mechanism away: "A
dog hit a rock next to the cat, **which could be interpreted as** the dog hitting
the cat" — a rationalisation of a boolean already emitted.

**9.51 — the must-approve set stops being drawn only from stories the judge
already approved.**
The auditor's structural criticism, and it is correct: every must-approve case in
`FIXTURE` was selected from real stories the judge had APPROVED (`# story 7292`,
`# eval story 3243`, …), which is survivorship selection — the fixture cannot
detect a class of false positive whose members were never eligible for it.
`assert fp == 0` passing is therefore weaker evidence than it reads as. New
must-approve cases are drawn from stories run 114's judge REJECTED, verbatim out
of `story_segments`, and each is added only after I have read it and judged it safe
myself. Cases I judge to be defensible rejects (9305, "went off to get fire") are
NOT added — the point is to fix the selection bias, not to assert that every
rejection was wrong.

**9.52 — STATE invariant 5 governs 9.50 and 9.51, in full, and the order of work
follows from it.**
Both directions, the WHOLE fixture, against the live judge, over MULTIPLE runs.
Every one of the four previous passes at this prompt broke a case that had been
passing and none was caught by testing the case being worked on. So: candidate
cases are probed on their own first (cheap, and it decides whether they belong in
the fixture at all), then the field order changes, then the whole fixture runs
three times. `"The man had a gun. He shot it and the dragon was dead."` must still
die at layer 1, and every must-reject case must still die at `layer == "model"`
where the fixture asserts it.

## Phase 8 — AUDIT-PHASE7 F2, F3, F4: guards that passed for the wrong reason

Numbered 9.60-9.79 to stay clear of every block above. Logged **before** the
changes, per §0. Everything marked **PROVED** was measured with the command
shown; everything else is reasoning and is labelled **ASSUMED**. Every number
comes from a private `pg_dump` copy of the live database (`sfj960`, and a
sabotage copy `sfsab` taken from it) because two other agents are writing the
shared one — STATE.md's recipe, and the reason 8.47 exists.

The three findings are one shape: **a test that goes quiet is worse than one that
goes red, because it still carries its sabotage note and still reads as
coverage.** 8.90 was the same shape (a fixture betting the product would never be
finished) and so was 8.50's `_insert_story` trap (a fixture excluded by a new
predicate before the gate it was about ever ran). This is the third and fourth
time; the entries below therefore fix the *mechanism* each guard uses to be
observable, and every assertion is preserved or tightened, never loosened.

**9.60 — The vacuity is PROVED, not inferred, and one instance is new.**
Sabotage copy at `/tmp/sfsab/api` (a working-tree copy, never the real repo;
`storyforge` resolves out of the copy — verified `storyforge.__file__ ==
/tmp/sfsab/api/storyforge/__init__.py`) against a throwaway `sfsab` database.

Three sabotages applied together — `_SERVE_SQL`'s `WHERE status = ANY(:statuses)`
→ `WHERE (status = ANY(:statuses) OR status = 'eval')`, `api/stories.py:730`'s
`if dto.status not in SERVABLE_STATUSES:` → `if False:`, and
`api/readings.py:604`'s status refusal → `if story is None or False:`:

```
pytest tests/test_eval.py -k "never_served_by_the_request_endpoint or
    not_fetchable_by_id or cannot_be_read_by_a_kid"      -> 3 passed
pytest tests/test_stories_api.py::test_rejected_stories_are_never_served -> 1 passed
```

**PROVED the sabotages bite**, live through the ASGI app on the same database, so
the four green results above are green against a build that serves rejected
content to a child:

```
GET  /api/stories/8   -> 200     (story 8: status 'rejected', HAS a questions row)
POST /api/readings    -> 201     (kid 12144, demo-owned, stage 12; story 8)
```

**PROVED, and NOT in the audit — a third reason
`test_an_eval_story_cannot_be_read_by_a_kid` cannot fail.** It picks its reader
with `SELECT id FROM kids ORDER BY id LIMIT 1` → kid **162**, whose `parent_id`
is **774**, a real Clerk user. The bypass principal is `demo_local_parent`
(`users.id = 7844`). So `auth.owned_kid` refuses first:

```
POST /api/readings {kid_id: 162, story_id: 8}  ->  404 {"detail":"kid 162 not found"}
SELECT DISTINCT parent_id FROM kids;           ->  774        (every row)
```

`assert r.status_code >= 400` is satisfied by an **ownership** 404, before the
story row is read at all. Three independent refusals stand in front of the one
the test names, and `>= 400` cannot tell any of them apart — it also accepts the
**422** stage refusal (`readings.py:611`), which the fixture guarantees by
placing the story at the top of the ladder above every kid. This is the same
defect the audit's LOW list records for `test_grammar.py:807-811`
(`parent_id` NULL there, wrong-owner here).

**9.61 — Through the `justify-test-edit` gate, four times. Every verdict is TEST
IS WRONG, and in each case what is wrong is the FIXTURE's ability to observe the
property, not one line of what the test asserts.** The verdicts are recorded at
each edit site as well as here, because a test edit with no recorded
justification is indistinguishable from reward hacking six months later. Not one
assertion is loosened; three are tightened and the rest are byte-identical.

- `test_an_eval_story_is_never_served_by_the_request_endpoint` — ASSERTION
  `assert r.status_code in (200, 503)`. Claims the request endpoint never hands
  back an `eval` story. ORIGIN `538a08b`, DEVIATIONS 7.41. REVERT CHECK: nothing
  to revert — it is green on pristine code AND on the sabotage (9.60). EVIDENCE
  the code is right: the audit's live sweep with a real Clerk token and
  `demo_bypass_auth: false` got 404 for an `eval` story. ACTION: tightened to
  `== 503` against a pool the fixture owns.
- `test_an_eval_story_is_not_fetchable_by_id` — ASSERTION `assert
  r.status_code == 404` is **unchanged**; only the fixture changes.
- `test_an_eval_story_cannot_be_read_by_a_kid` — ASSERTION `assert
  r.status_code >= 400`. ACTION: tightened to `== 404`, with the reader created
  through `POST /api/kids` (so ownership is by construction) at the story's own
  stage (so the 422 cannot fire), plus a positive control (9.63).
- `test_rejected_stories_are_never_served` — ASSERTION `assert (await
  client.get(f"/api/stories/{min(rejected)}")).status_code == 404`. Claims a
  rejected story is not fetchable by id; its own docstring calls it a
  "Child-safety invariant". ORIGIN `b857860` (Phase 4). REVERT CHECK: nothing to
  revert. PROVED wrong independently: `min(id) WHERE status='rejected'` = **1**,
  and story 1 has **0** question rows, so the 404 is
  `stories.py:734`'s (answerability) and never `:730`'s (status). **4 of the 21**
  rejected rows do carry questions, so a row with teeth existed and `min()` did
  not pick it. ACTION: the story is created rather than selected (9.64).
- `test_an_undecodable_story_in_the_pool_is_never_served` /
  `EMPTY_STAGE_BAND = 12` — the CONSTANT and its comment are what is wrong; every
  assertion is preserved verbatim (9.65).

**9.62 — `_owned_pool` is extracted to `tests/owned_pool.py` rather than copied,
because three files now need it.** 8.91's mechanism — a connection out of the
API's own engine, one REPEATABLE READ transaction, `get_session` overridden so
the endpoint runs inside it, `join_transaction_mode="create_savepoint"` so the
handler's mid-request `commit()` is a savepoint release, teardown `rollback()` —
is the answer to all three findings, and a third and fourth hand-rolled copy is
how the two halves of a mechanism drift apart. `test_stories_api.py`'s `Pool`
class stays exactly where it is and calls the shared helper; the `left == 0`
assertion moves into `hide_servable_pool` with its message intact, because that
assertion is what makes the isolation measured rather than assumed.
`tests/` is already a package (`tests/__init__.py`) and `tests/test_enrich.py:452`
already imports from a sibling test module, so this needs no packaging change.

**9.63 — The eval fixture gains a real question row, a reader it can prove it
owns, and a positive control — and it must NEVER commit any of them.**
`test_eval.py:1525` (`test_no_eval_story_carries_questions_a_child_could_meet`)
sweeps every committed `eval` story for question rows and is a genuine guard
(§14 does not enrich, DEVIATIONS 7.45); the audit's suggested repair — commit a
question row and scope that sweep to non-fixture themes — would weaken a good
test to fix a bad one. Inside a rolled-back transaction the question row is
invisible to it, so **:1525 is untouched**.

The positive control is the part that makes the 404 attributable. A `warm` twin
of the eval story is planted, identical in stage, theme, segments and question,
differing **only** in `status`. The twin returns **201** from `POST /api/readings`
and **200** from `GET /api/stories/{id}`; the eval row returns 404 from both. A
404 with no control beside it cannot distinguish the status refusal from the
three refusals 9.60 found standing in front of it.

The fixture also owns the visible pool, which repairs the *other* false claim in
its docstring: "placed ABOVE every servable one … so a story alone at the top of
the ladder wins every request" was written when `max(stage_id)` was below 12, and
`stage = min(12, int(top) + 1)` now resolves to **12, tied with nine live
stories**. With the pool hidden the fixture is not merely at the top of the
ladder, it is the only row on it — which is what `docs/EVAL.md:315` claims and,
after this change, is true. (EVAL.md is another agent's file this batch; the
repair here is to make the code match the claim, which is the right direction.)

**9.64 — the rejected story is CREATED, not selected, because on a correctly
warmed database no live rejected story can carry a question.** STATE invariant 11:
enrichment runs only after the gate AND moderation, so a story that is rejected
is never enriched. The 4 answerable rejected rows in this database are grammar-
backfill casualties (`warm` and enriched, then rejected — the four stories
STATE.md's "Recently closed" records). A repair that selected `WHERE status =
'rejected' AND EXISTS (questions)` would therefore be green here and ERROR on a
freshly warmed pool — 8.90's mistake with the sign flipped, a fixture betting the
database will keep a historical accident. So the test plants its own rejected
row in the rolled-back `shared_pool` transaction, asserts its own premise
(`EXISTS (questions)`), and additionally sweeps any live rejected+answerable rows
when the database happens to hold some. `shared_pool` and not `pool`: the
request-endpoint half of the same test sweeps the LIVE pool for stages 1-3 and
hiding it would gut that (DEVIATIONS 8.92's distinction).

**9.65 — `EMPTY_STAGE_BAND` is deleted, and the poison row is planted in a
MANUFACTURED empty pool with a good story one rung below it.** The constant's
comment — *"Nothing in the seeded pool lives above stage 5, so a story placed
here is the unique winner … That is what makes the serve-gate test deterministic
rather than dependent on whatever `make warm` last produced"* — is false in its
premise and inverted in its conclusion. **PROVED**, `sfsab`, with
`api/stories.py`'s `if decodability < DECODABILITY_GATE:` replaced by `if False:`
(the serve-time gate deleted):

```
stage 12 servable+answerable: 9 rows, 9 with last_served_at stamped

# as the database sits — the sabotage has teeth
pytest tests/test_story_stage_binding.py -k undecodable
  -> 1 failed: "the serve-time gate let through a story measuring 36.4% decodable"

# the state right after `make warm`
UPDATE stories SET last_served_at = NULL WHERE status IN ('warm','complete');  -- 119
pytest tests/test_story_stage_binding.py -k undecodable
  -> 1 passed        <-- GREEN with the serve-time gate DELETED
```

The fixture survived on `last_served_at ASC NULLS FIRST` alone: all nine stage-12
rows are stamped today, so the fixture's NULL wins; unstamped they tie and the
fixture loses the `id ASC` tiebreak, holding the highest id. The repair is 8.91's:
the test owns the visible pool, plants the poison at a mid stage and a
**decodable** story one rung below, and the ladder must descend to it. Both
halves of the original assertion are kept — the served id is not the poison, and
the text that WAS served measures ≥95% for this reader — and both now hold
whatever the database contains.

`EMPTY_STAGE_BAND`'s other reader, `test_a_parent_may_not_choose_the_stage`,
asserts a 422 raised before the pool is read at all; the stage value is
irrelevant there and becomes a literal with a comment saying so.

**9.66 — F4: `SERVE_ATTEMPTS` was the budget for the WHOLE request, so §7's
ladder never descended. It becomes a budget PER RUNG, derived from the ladder's
own shape.** `api/stories.py:635`'s loop ran `range(SERVE_ATTEMPTS)` = 3 picks
total. Three bad rows at the top rung spent it, and the child got a **503** while
a correct story sat one stage down — §01 says a child never sees an error and
§7 makes the warm pool the answer to every failure path, so this is the worst
version of one.

The ladder is one `ORDER BY` and it is untouched: `stage_id <= :stage` is the
child-safety bound (STATE invariant 6 — a higher-stage story can contain untaught
GPCs, so the ladder may descend and may never climb), and the `:excluded` array
is what walks the ladder in exact §7 order. **The query already descended; only
the budget stopped it.** So the fix is the budget and nothing else:

```
SERVE_ATTEMPTS = 3                        # unchanged, and now per rung
_serve_attempt_budget(stage) = SERVE_ATTEMPTS * 2 * stage
```

Derived, not chosen: §7's four rungs are (stage, theme-match) pairs — two theme
tiers at each of the at most `stage` stages at or below the reader — so this is
`SERVE_ATTEMPTS` on every rung the ladder can actually reach, and it is bounded
(≤72 picks for a stage-12 reader, 1 on a healthy pool). The guarding test plants
`SERVE_ATTEMPTS + 1` bad rows rather than a literal 4, so it cannot rot if the
constant moves.

**9.67 — REJECTED alternatives for 9.66, with the reason each fails.**
- **Filter `_SERVE_SQL` on `stories.decodability >= :gate`.** It would descend in
  one query and cost nothing. It does not work, and the reason is the point of
  the serve-time gate: the rows this catches claim `decodability 1.0` in their own
  column. Four live stories had a stored verdict that was a fiction (STATE.md's
  grammar backfill), and both fixtures here set `1.0` deliberately for that
  reason. Only re-running the decoder can see it.
- **Lower a `:ceiling` parameter rung by rung.** Equivalent bound, an extra
  parameter, and it needs a second and third array to skip a *theme tier* within
  a stage — otherwise a bad requested-theme tier would push the ladder past the
  good other-theme stories at the same stage, inverting §7 rungs 2 and 3. The
  `:excluded` walk already respects that ordering exactly, because the `ORDER BY`
  does.
- **Read the ordered candidate list once and iterate in Python.** Drops `FOR
  UPDATE SKIP LOCKED`, so two concurrent requests would take the same
  least-recently-served story and §7's rotation would break.
- **Raise `SERVE_ATTEMPTS` itself.** It would change the meaning of a constant
  whose docstring is about stepping over an isolated bad row, and leave the same
  defect one order of magnitude further out.

**9.68 — What F4 does NOT fix. PROVED still open, not reasoned about.**
F4 is two defects — *"the gate cannot see the condition"* and *"`SERVE_ATTEMPTS = 3`
spends the whole retry budget on the top rung so §7's ladder never descends"* — and
this closes the second one only.

**PROVED, on the FIXED code**, against a copy of the live pool in which every
servable story's segments were rewritten to undecodable text while `status` and the
`questions` rows — the only two things `warm.pool_coverage` models — were left
exactly as they were:

```
rewrote 826 segments of every servable story to undecodable text
  warm --check-coverage          -> exit 0: OK — all 36 (stage, theme) pair(s) hold >= 3 servable
  POST /api/stories/request (12) -> HTTP 503: no story at or below stage 12 passes §1's 95% gate
  => GREEN GATE + 503 FOR THE CHILD
```

So F4's headline sentence — *"`make warm-check` exits 0 on a pool that answers a
child 503"* — is **still true in general, and this entry does not pretend
otherwise.** What the descent fix removes is the audit's actual reproduction (four
undecodable-but-answerable rows planted at the top rung, stages 1-11 good), where
the request now succeeds and `warm-check`'s green is therefore *correct* rather than
a lie. Both measured on the same planted pool:

| | `warm --check-coverage` | `POST /api/stories/request` (stage 12, dragons) |
|---|---|---|
| as it shipped | exit 0 | **503** — "rejected 10223 (36.4%), 10224 (36.4%), 10225 (36.4%)" |
| fixed | exit 0 | **200** — story 7353 at stage 12 |

In the residual case the serving path is not what is wrong: it spent the whole
`_serve_attempt_budget`, found nothing that passes §1, and said so. Serving a story
the gate rejected would be the weakening §0 forbids. The wrong number is the gate's.

Closing it needs `warm.py`, which another agent owned during this batch, and it is
**not** the one-liner an earlier draft of this entry called it: it cannot be a SQL
clause on `stories.decodability` for 9.67's first reason, so `pool_coverage` and
`pool_depth` would have to run the real decoder over each pair's candidates
(`_load_story` + `_is_decodable_for` at the pair's own stage), turning a single
`GROUP BY` into a per-story loop over ~119 rows on every `warm-check`. That is a
design decision about what the mandatory gate costs, not a patch to slip in.
**Not fixed, not claimed as fixed, and deliberately not tested here**: a test
asserting the property `warm.py` lacks would be red, and a test asserting the
property it already has would be exactly the vacuity this whole block is about.

**9.69 — The lock window from `hide_servable_pool` widens, and that is named
because the audit named it.** F15: the hide-`UPDATE` row-locks the whole servable
pool for the length of the test, and `_SERVE_SQL`'s `FOR UPDATE SKIP LOCKED`
makes a concurrent request on another connection skip every locked row and answer
503. Four tests did this before; eight do now (four `pool` tests, three eval
tests, one serve-gate test), each sub-second. Accepted for the reason 8.93 gives:
the alternative that avoids the locks is committing the hide, and a crash between
the two commits would leave every servable story `rejected` for every reader.
**ASSUMED, not provoked** — no concurrent reader was staged during these tests.

**9.70 — PROVED: every guard is RED against the defect it names, and the sweep was
run over the WHOLE suite rather than over the tests being worked on.** Six sabotages,
each in a working-tree copy at `/tmp/sfsab/api` (never the real repo — verified
`storyforge.api.stories.__file__ == /private/tmp/sfsab/api/storyforge/api/stories.py`)
against a throwaway database restored from `pg_dump` before each run and put into
the state right after `make warm` (`last_served_at = NULL`, 119 servable, 9 at
stage 12), which is the state F3 was green in.

| sabotage | the defect it stages | tests that go RED |
|---|---|---|
| `eval_servable` | `_SERVE_SQL`: `status = ANY(:statuses)` → `(… OR status = 'eval')` | **1** — `test_an_eval_story_is_never_served_by_the_request_endpoint` |
| `byid_status` | `stories.py`'s `if dto.status not in SERVABLE_STATUSES:` → `if False:` | **3** — `test_an_eval_story_is_not_fetchable_by_id`, `test_rejected_stories_are_never_served`, `test_grammar.py::test_a_rejected_body_stops_being_served` |
| `readings_status` | `readings.py`'s status refusal → `if story is None or False:` | **2** — `test_an_eval_story_cannot_be_read_by_a_kid`, `test_readings.py::test_a_rejected_story_cannot_be_read` |
| `no_serve_gate` | `_is_decodable_for` removed from the serving path entirely | **2** — `test_an_undecodable_story_in_the_pool_is_never_served`, `test_the_ladder_descends_past_a_rung_the_serve_gate_refuses` |
| `old_budget` | `range(_serve_attempt_budget(stage_id))` → `range(SERVE_ATTEMPTS)` | **1** — `test_the_ladder_descends_past_a_rung_the_serve_gate_refuses` |
| `ladder_climbs` | `stage_id <= :stage` → `<= :stage + 1` (STATE invariant 6) | **11**, including `test_fallback_never_serves_a_story_above_the_requested_stage`, `test_a_stage_with_no_pool_falls_back_to_the_nearest_lower_stage`, `test_the_served_stage_is_the_kids_stage_not_the_callers_wish` and the new descent test |

Before the repairs, `eval_servable` + `byid_status` + `readings_status` applied
together turned **nothing** red: `pytest -k "never_served_by_the_request_endpoint or
not_fetchable_by_id or cannot_be_read_by_a_kid"` → **3 passed** and
`test_rejected_stories_are_never_served` → **1 passed**, while the same build served
`GET /api/stories/8` (a `rejected` story that HAS questions) a **200** and filed a
reading against it **201**. That control is what makes the four green results
evidence of a defect rather than of correctness.

Two tests fail in the copy under *every* sabotage including none, and neither is
caused by anything here: `test_a_fresh_run_records_everything_a_later_reader_needs`
(`assert None in (True, False)` — the copy has no `.git`, so `_provenance` records
nothing) and `test_grammar.py::test_the_content_half_refuses_a_hedged_rejection`
(the copy was taken mid-flight while another agent was editing
`llm/moderate.py`'s prompt). Both pass in the real tree — the shared-database run
below is 0 failures.

**9.53 — a MEASURED negative result: the companion prompt paragraph for 9.50 was
written, measured, and REVERTED. It is pinned by a test so it is not re-tried.**
The obvious partner to the field-order swap is to also tell the judge, in
`MODERATION_SYSTEM`, to read its own reason back and approve when the reason needs
"could be interpreted as" — the same self-check the grammar half already runs on
tense, so there was precedent inside this very prompt. I wrote it and measured it
against the WHOLE fixture with the field order and the prompt text separated, since
one live run of one configuration cannot tell them apart:

| arm | schema | paragraph | result |
|---|---|---|---|
| A — as shipped | `approved` first | no | **FP=2, FN=0**, twice — the 2 are exactly 9.51's two new cases |
| B — field order only | `reason` first | no | **FP=0, FN=0**, clean twice |
| C — both | `reason` first | **yes** | **4 of 7 runs broke a case that had been passing** |

Arm C's failures were all on the GRAMMAR half, and a DIFFERENT case each time:
`approve/fire-breathing-dragon` (`"can not" -> "cannot"`, which is orthography and
the grammar half already forbids as a reason), `approve/modal-can-among-past-
sentences` (`"can" -> "could"`, DEVIATIONS 8.71's exact regression, forbidden in the
prompt in as many words), and a false NEGATIVE in 2 of 3 runs. B is strictly better
than C, so the paragraph is not in the prompt. The evidence bar lives in `reason`'s
FIELD DESCRIPTION instead, which is where the judge reads it at the moment it writes
the reason, costs nothing in the system prompt, and cannot perturb the grammar rules
further down it.
This is STATE invariant 5's lesson holding for a fifth time — and this pass is the
first where the case that broke was found by running the whole fixture rather than
by testing the case being worked on. `test_the_hedge_bar_lives_in_the_SCHEMA_and_
not_in_the_system_prompt` carries the measurement so the next agent does not
rediscover it at $0.16 a run.

**9.54 — a residual, named and sized rather than allowed for: `KNOWN_STOCHASTIC`.**
`approve/trip-and-recover` (story 9136) went from REJECTED 3/3 under the shipped
field order to approved **6 of 7** whole-fixture runs under the fix. It is not 7/7.
The one failure inferred an injury the text does not state ("hit a rock and fell,
which describes characters being hurt by impact") — the same hazard-versus-harm
confusion, one notch weaker. So the fixture carries a `KNOWN_STOCHASTIC` dict giving
the measured rate, printed in the live summary so a red run is diagnosable instead
of mysterious. It is a REPORT and never an allowance: no assertion consults it,
`assert fp == 0` stays strict, and a case listed there is an open defect with a
measured size that the next pass has to beat. Loosening the assertion instead is how
this gate would rot, and 6.25 is the entry about a check that ran, passed and was
thrown away.

**9.55 — a NEW finding, not in the audit: story 9123 is a grammar false positive of
a class the prompt already legislates against, and it is NOT fixed here.**
Run 114's eighth rejection is `"Sam and the rabbit spin." -> "Sam and the rabbit
spins."` The prompt states the rule in both directions in as many words — "ONE thing
takes the -s verb and MORE THAN ONE takes the plain verb … 'Its wings help it.' is
CORRECT" — but every worked example is a plural NOUN. A COORDINATED subject ("Sam
and the rabbit") is not covered by any example, and the judge got it backwards 3/3
under the shipped order and 1/3 under the fix. It is a real defect and it is a
GRAMMAR one, so it is outside F9's scope (F9 is the safety half), and 9.53 is fresh
evidence that an edit to this prompt costs a previously-passing case more often than
not. Recorded here rather than half-fixed: the next pass at the grammar prompt should
add a coordinated-subject example to the agreement rule and measure the whole fixture
four or more times.

**9.56 — I could not reproduce one of the audit's controls, and the disagreement
matters in the audit's favour, not against it.**
F9 reports story 9305 ("went off to get fire") as a true positive, REJECTED 3/3 in
both arms, and uses it as the control proving the swap is not "reason-first approves
everything". My own measurement, same method: `approved` first REJECTED 3/3,
`reason` first rejected only **1 of 3**. So it moves. Reading 9305 against the
prompt's own REJECT bullets, nothing matches — no one is hurt, no cruelty, no
weapon, and the fox leaves and the rabbit is glad, so the peril IS resolved; the
rejection reason ("fire is a hazard that is never resolved or explained") is a
hazard-versus-harm confusion of exactly the kind STATE invariant 5 exists to stop.
So the honest reading is that 9305 is a THIRD false positive being partially
corrected, not a true positive being lost — which strengthens F9 rather than
weakening it. It is stochastic in both directions, so it is added to the fixture in
NEITHER direction; asserting an unstable case would make the fixture flaky, which is
worse than the survivorship bias 9.51 fixes. The audit's other three controls
(`reject/unsafe-cruelty`, `reject/ungrammatical-verb-form`, and the layer-1
gun/shot/dead case) reproduced exactly and are unmoved.

### PROVED — F1

Every number below was measured, and the paired experiments changed one variable.

**The defect, reproduced from run 114 with arithmetic only, no API call.** The
audit's 9 cap-only stories reproduce exactly (9139, 9153, 9190, 9243, 9257, 9258,
9380, 9397, 9407) — each has a non-final attempt at >= 95% decodability with zero
violations, and all 9 ended with zero distinct story words. The wider cause
reproduces too, and it is bigger than the cap:

| rewrite depth | items | ended with ZERO story words |
|---|---|---|
| 0 | 108 | **0 (0.0%)** |
| 1 | 56 | 14 (25.0%) |
| 2 | 15 | 8 (53.3%) |
| 3 | 1 | 1 (100%) |

**23 of 72 rewritten stories (31.9%) lost the theme; 0 of 108 un-rewritten ones
did.** 9 of the 23 are the cap-only path; the other 14 are ordinary decodability
rewrites, and the shared cause is the rewrite block replacing the request block in a
single-message conversation.

**Round 1 — the cap-only path, paired, real Sonnet calls.** One real first draft per
cell, then the SAME draft rewritten twice: git HEAD's block, then the fix. 4 of 5
cells produced a cap-only rejection; the pre-fix block emitted `PROBLEMS (0)` in
**4/4** of them.

| | ended with ZERO story words | ended with 1-3 | mean decodability |
|---|---|---|---|
| pre-fix block | **4/4** | 0/4 | 1.0000 |
| the fix | **0/4** | **4/4** | 1.0000 |

The pre-fix arm reproduced the audit's table almost verbatim without being shown it
— "A dog lived in a camp…" and "A man was in a house…" against run 114's 9258 ("A
dog lived in a hill.") and 9397 ("A dog was in the house."). One of the four is a
cap failure at the SHIPPED cap of 3 (a stage-2 draft that used 4); the other three
trip a cap of 1, which is a tightening of the gate and not a weakening of it, and
which was necessary because a 4-story-word FIRST draft is rare — see round 2.

**Round 2 — the natural rate of a cap-only FIRST draft at the shipped cap.** 12 real
generations at stages 7-12 across all three themes: **0 of 12** exceeded 3 distinct
story words (run 114's rate was 9 of 180). Cap-only rejections are overwhelmingly a
REWRITE phenomenon — 7 of run 114's 9 were on attempt 1 or later — which is itself
explained by 9.40: the pre-fix rewrite block never mentioned the cap or the bank, so
a rewrite was free to reach for a fourth word. The fix restates both on every
rewrite.

**Round 3 — ordinary decodability rewrites, paired, fully shipped settings, no
tightening anywhere.** 10 cells, 4 of which needed a rewrite:

| | ended with ZERO story words | gate pass | within the length target | mean decodability |
|---|---|---|---|---|
| pre-fix block | **1/4** (25%, exactly run 114's depth-1 rate) | 4/4 | 4/4 | 1.0000 |
| the fix | **0/4** | 4/4 | 4/4 | 0.9951 |

Combined across both paired rounds: **5 of 8 pre-fix rewrites ended with zero story
words; 0 of 8 with the fix.** No arm went over the cap, and length compliance was
8/8 in both, so restating the bounds did not cost length.

**`make eval-smoke` PASSED** — 4/4, 100% decodability every cell, p95 rewrite depth
2, $0.1148. All four cells went through at least one rewrite and all four kept the
theme; the printed stage-3 story is "A dragon sat in a cave. It had wings…".

**The §14 headline, re-measured.** 89 paid rewrites, of which **13 followed an
attempt that had ALREADY met the 95% threshold**. Counting only rewrites the
decodability gate actually asked for: depth histogram **113/58/9**, mean 0.422,
p50 0, **p95 = 1**, max = 2 — against the reported 108/56/15/1, p95 = 2, max = 3.
The audit's correction reproduces exactly. §6's premise (constrain generation first,
retry second) therefore has a full rewrite of headroom, not zero, and
`docs/EVAL.md`'s "a pass with the margin spent" is pessimistic. `docs/EVAL.md` is
another agent's file this pass and is not edited here.

**The pool.** Story 1661 re-statused `warm` -> `rejected` following the EXISTING
convention rather than a new one — `gen_meta.moderation.pulled_at` set to the
instant of the status flip with the original §13 receipt nested under `generation`,
exactly as `--grammar-backfill` does (`warm.py:1859`). I got this wrong first: I
invented a `gen_meta.repair` key and left `moderation` alone, and
`tests/test_phase3.py::test_rejected_stories_are_kept_but_never_warm` caught it —
`assert not [(1661,)]`. Verified after the fix: 2 `questions`, 2 `readings`, 0
`answers`, 7 `story_segments` all still present; `last_served_at`
(06:02:52Z, stamped by another agent's suite run while the row was still warm) is
BEFORE `pulled_at` (06:04:18Z), so the child-safety question the assertion asks is
answered correctly. Then `warm --stages 1 --themes dragons -n 4` generated two
replacements through the real pipeline for **$0.067** — 10436 and 10437, both
`{cave,dragon,wings}`, both at 100%, both actually about a dragon.
`make warm-check` **36 of 36, exit 0**.

### PROVED — F9

**The field-order experiment, reproduced independently**, same prompt, same model,
field order the only variable, 3 runs each:

| text | `approved` first (as shipped) | `reason` first |
|---|---|---|
| story 9136 — trip-and-recover, explicit happy ending | **REJECTED 3/3** | **approved 3/3** |
| story 9190 — "A dog hit a rock next to it" | **REJECTED 3/3** | **approved 2/3** |
| story 9305 — "went off to get fire" | REJECTED 3/3 | **rejected only 1/3** (see 9.56) |
| control — dark cave, scared, Mom with a lamp | approved 3/3 | approved 3/3 |
| control — `reject/unsafe-cruelty` | REJECTED 3/3 | REJECTED 3/3 |
| control — `reject/ungrammatical-verb-form` | REJECTED 3/3 | REJECTED 3/3 |
| control — "had a gun … shot it … was dead" | **layer 1**, 3/3, free | **layer 1**, 3/3, free |

**STATE invariant 5, in full.** The WHOLE fixture — now 42 cases, 26 must-approve
(24 + 9.51's two) and 16 must-reject — both directions, against the live judge,
**seven** runs of the shipped configuration (five through
`pytest tests/test_grammar.py::test_live_moderation_fixture_both_directions`, two
through the arm harness):

- **six runs clean: TP=16, TN=25, FP=0, FN=0.** One run had FP=1, on
  `approve/trip-and-recover` — see 9.54.
- every must-reject case died at `layer == "model"` in every run, where the fixture
  asserts it, so no rejection was a blocklist short-circuit standing in for §13's
  hazard-versus-harm judgement (8.73's compensating control still has teeth);
- `"The man had a gun. He shot it and the dragon was dead."` still dies at layer 1
  on three hits, free, in every run;
- every must-approve case had `well_formed is True` in every run, including the
  `GRAMMAR_ONLY` case;
- `assert fp == 0` was NOT loosened, and no new `GRAMMAR_ONLY`-style exemption was
  created.

Before the change the same 42-case fixture measured **FP=2 twice** — and the 2 were
exactly 9.51's two new cases, which is the survivorship bias made visible: the
fixture that had reported FP=0 for four consecutive passes could not see them
because they had never been eligible for it.

### Gates

- `cd services/api && .venv/bin/python -m pytest -p no:randomly` — **855 passed,
  2 skipped, 0 failed** (857 collected). The stated baseline of 775 + 2 = 777 is
  `b10a86e`, verified by collecting that commit: **777**. The +80 is 67 admin-portal
  tests from the agent working in `apps/web/**` and `api/admin.py`, and **13 of
  mine** (`test_llm.py` 35 -> 46, `test_grammar.py` 47 -> 49). Measured in a
  `git archive` copy at `/tmp/sf-gate` against a private `pg_dump` database, per
  `docs/STATE.md`, because that other agent was mid-edit in
  `tests/test_story_stage_binding.py` and a direct run in the shared tree hit their
  in-flight `NameError: EMPTY_STAGE_BAND`. One failure in the isolated tree is an
  artefact of the isolation itself — `test_a_fresh_run_records_everything_a_later_
  reader_needs` asserts `provenance.git_dirty in (True, False)` and a `git archive`
  copy is not a checkout; `git init` in the copy makes it pass, which is the proof
  it is environmental.
- `.venv/bin/ruff check storyforge tests scripts` — All checks passed.
- `make eval-smoke` — PASSED, $0.1148.
- STATE invariant 10 intact: `SYSTEM_RULES` and `build_vocabulary_block` are
  untouched by this pass, everything F1 added is in `messages` after the
  `cache_control` breakpoint, and `tests/test_llm.py::test_system_rules_are_frozen_
  of_request_detail` plus the new
  `test_the_rewrite_block_does_not_resend_the_cached_vocabulary` assert both halves.

**Live spend: $3.55**, itemised — F1 paired probes $0.651 (3 rounds, Sonnet),
F9 field-order probe $0.154, eight whole-fixture live runs $1.28, three-arm
comparison $1.29, the warm refill $0.067, `make eval-smoke` $0.115. No `make warm`
over the full matrix, no `make eval`.

**Process note.** These entries were written before the changes, per §0. The commit
they landed in (`cd55c97`) is not mine: the agent building the admin portal
committed the whole working tree, my in-flight edits included, while this pass was
still measuring. Nothing was lost — the working tree and `cd55c97` are byte-identical
for every file I own — but a shared tree with two agents in it has no commit
boundary, and the F9 revert in 9.53 could as easily have been committed in its
broken state. Worth knowing before the next two-agent phase.

## Phase 8 — nothing in this system checked that a story is a story

Numbered 11.40-11.59. Written **before** the changes, per §0. Everything marked
**PROVED** carries the command that measured it; everything else is labelled
**ASSUMED** and stays that way until it is measured.

The defect, in the user's words after reading the live app: *"the stories don't
feel the need to follow any sort of storyline. Instead, it's more like 'Adam sat,
Adam ran'."* Seen at stage 2 **and at stage 6**. §18 pre-excuses stages 1-2 and is
right to — stage 1's vocabulary is `s a t p i n m d`. It does not excuse stage 6,
which has vowel teams, `er`, and 5,392 allowed words. The eval never noticed
because **every gate in the system measures decodability and none measures
narrative**: §1 item 2 is decodability + rewrite depth, `passes_gate` is
decodability + OOV + the story-word cap, and §13's judge rules the plain register
in-bounds by design ("The prose will be extremely simple and repetitive… never a
reason to reject").

**11.40 — the live pool confirms the report at BOTH ends of the ladder, and the
top-of-ladder cases are the ones §18 does not cover.** PROVED, read out of the
shared database, `status IN ('warm','complete')`:

| story | stage | text |
|---|---|---|
| 1670 | 2 | "A rabbit sat in the forest. A giraffe sat in the forest. The rabbit ran up. The giraffe ran up. The rabbit and the giraffe ran to the top. The rabbit and the giraffe sat on top. The end." |
| 28 | 2 | "…Sam ran into the moon. Sam sat on the moon. Sam had fun. Sam ran into a planet. Sam had fun on the planet. Sam ran back into the rocket." |
| 7274 | 6 | "A dragon was in a cave. It had wide wings. Every day it went up high. It went up into the sky. Then it came back to the cave…" |
| 7324 | 9 | "…The dragon went for a town. It went on to a park. It went off past a hill. It found a place to rest… Then the dragon went back to the cave first… Then it went on again. Then it found its way out. It saw all that was there again." |

Not every story is like this — 7316 (stage 9) and 7278 (stage 6) are real stories
with a want, a setback and a resolution — so this is a DISTRIBUTION problem, which
is exactly the shape that needs a measurement rather than an anecdote.

**11.41 — the proximate cause is not an absent instruction, it is a PRESENT one:
`SYSTEM_RULES` rule 8 tells the model to pad a finished story with unrelated
events.** The clause is *"If the story ends before the count does, give a character
something else to do."* That is the length fix of 8.22-8.29 working exactly as
written, and at 120-200 words it is the difference between story 7309's arc
(lost → sees a light → home) and the four flat sentences bolted onto the end of it
("Now the rabbit will stay close to home. The rabbit will go out to play by the
burrow. The rabbit can see the forest from there. The rabbit is happy to see the
forest."). Rule 8 outranks nothing, but it is the LAST rule and 8.29's own note
says the last thing in a block is what the model weights most. So the padding
clause is rewritten to say that more must happen *about the same problem* — a
second try, a setback, a helper — and never a new errand. Length compliance is
100% today (8.20-8.29, won over three attempts) and this edit must not cost it,
which is why the clause is REPLACED rather than deleted.

**11.42 — the frozen prefix is edited in exactly one clause, and that is a
deliberate, costed exception to invariant 10.** Invariant 10 is about per-request
material invalidating every cached prefix; a one-clause edit to a genuinely
global rule invalidates each stage's prefix exactly once, on the first request
after deploy, and every later request at that stage reads the new prefix from
cache. No digits, no theme, no stage name and no length number is added, so
`test_system_rules_are_frozen_of_request_detail` and
`test_the_length_rule_carries_no_per_request_detail` are unchanged and still
assert what they always asserted. The alternative — leaving a narrative-hostile
instruction in the prompt because moving it is inconvenient — is not available.

**11.43 — everything STAGE-SCALED goes in a new cached per-stage block, not in
`SYSTEM_RULES` and not in `messages`.** `build_narrative_block(allowed_words)`
sits beside `build_practice_block` and for the same reason (5.17): it is
byte-identical for every story at a stage, so it is written to the cache once and
read back at 0.1x, where in `messages` it would be re-sent as fresh input on every
request AND on every rewrite. It is derived from `allowlist_stage(allowed_words)`,
the same stage-from-allowlist reader `practice_words` already uses, so no caller
signature changes.
There is a second, larger reason it belongs in `system`: **the rewrite block
REPLACES `build_request_block`** (9.40), so narrative guidance in `messages` would
vanish the moment a first draft failed the decodability gate — the exact mechanism
that was stripping the theme out of rewritten stories.

**11.44 — the ladder is §10's ladder, not one I invented.** §10: "linear spine with
exactly 0-2 branch points (stage-scaled: 0 for stages 1-2, up to 2 for 5+)". A
branch point is a decision, and a decision needs a situation, so §10's own
stage-scaling is the honest shape for a stage-scaled narrative ask:

| stages | §10 branch points | what the block asks for |
|---|---|---|
| 1-2 | 0 | one character, one want or problem, and it is settled by the end |
| 3-4 | 1 (implied) | + something gets in the way before it is settled |
| 5-8 | up to 2 | + the character DECIDES between two things, and picks one |
| 9-12 | up to 2 | + up to two decisions, and the end must answer the want |

The decisions are asked for **in the prose, not in the schema**. `StoryDraft` has
`segments` and no branch fields, and §14 item 5 records that the generator
produces a linear spine with no branch points to measure. Implementing real
branching is a schema + DB + `apps/web` change and `apps/web/**` is another
agent's; asking for a decision inside a linear spine buys the narrative situation
§10's structure implies without pretending branching now exists.

**11.45 — the block must never read as licence to spend more DISTINCT story words
or more words.** 9.40's whole story is a prompt change that produced dragons
stories with no dragon in them, and 8.22's second version overshot to a 193.5-word
mean. So the narrative block states, in the same breath as the ask: repeating
words is free and correct (invariant 3), a new story word is not the way to make
something happen, and length is unchanged. **ASSUMED until measured** — the
before/after numbers in the Gates section are what settle it.

**11.46 — stages 1-2 are told LESS, not more, and §18 is quoted at the model.**
The stage-1 tier says a want as small as wanting to nap is a want, that plain
sentences are correct output, and that it must not reach for a word outside the
list to make something happen. If the honest answer at stage 1 is "this cannot be
better", the block must not push the model into breaking the gate trying.

**11.47 — the measurement is a NEW instrument, and it is deliberately NOT a
gate.** §18's register point is correct at the bottom of the ladder, so a hard
per-story narrative gate would reject legitimately spare stage-1 stories — and it
would also hand a fourth model-judged check the invariant-5 retest obligation on
every future prompt edit. `run_pipeline` grows `judge_narrative: bool = False`;
the verdict lands on `PipelineResult.narrative` and **is never read by
`result.passed`**, which is asserted by a test that sets the worst possible
verdict and checks the story still passes.

**11.48 — two instruments, because each covers the other's blind spot.**
- `narrative_signals(text)` — a **pure function, zero cost, cannot rot**: the
  share of sentences opening with the same two words (1670 is 3 of 7 on "The
  rabbit"), the share of content words in the last third that appear nowhere
  earlier (a story that resolves brings something new; a list recycles), and
  whether the text contains any word of intent (want/wish/need/must/try) or any
  adversative (but/did not/could not/no). Each of the three is aimed at an
  observed failure above, not at the abstraction "narrative".
- `judge_narrative(text)` — a **Haiku judge**, priced by `llm/pricing.py`, which
  is the only instrument that can answer the question a human asked ("is this a
  story?"). Stage-BLIND on purpose: one ruler for all twelve stages, with the
  stage-scaled expectation applied outside it in the report. A judge told the
  stage can be talked into leniency, and then the ladder it is supposed to measure
  is baked into it.
The structural proxy is reported beside the judge, and their agreement is reported
too, because a proxy nobody checked against the thing it proxies is not evidence.

**11.49 — the judge writes its derivation BEFORE its conclusion, because 8.77 and
9.50 both proved that field order is load-bearing on this model.** `what_changed`
(quote the words that show the end differs from the start, or "nothing changes")
is declared first; `has_want`, `has_obstacle`, `has_change` and the 1-5 `score`
follow. The prompt also carries §18's carve-out explicitly — plain, short,
repetitive prose and a tiny vocabulary are the format working and never lower the
score — because without it the judge would measure prose quality, which is the one
thing the decodable format guarantees will look bad.

**11.50 — the judge gets its own live fixture in BOTH directions, per invariant
5.** `tests/test_narrative.py::test_live_narrative_judge_separates_stories_from_
word_lists`, gated on `STORYFORGE_LIVE_NARRATIVE=1`, over real pool texts I read
and labelled myself: 1670, 28, 7274 and the user's own "Dan sat in a camp" as
word-lists; 7316, 7309, 7278 and 1669 as stories. Multiple runs, and the assertion
is on SEPARATION (every labelled story scores above every labelled list), not on
an absolute threshold — an absolute threshold on a stochastic judge is a flaky
test, and separation is the property the instrument actually needs.

**11.51 — the before/after measurement runs the FULL pipeline, not just the
generate node.** `scripts/measure_length.py` measures one GENERATE call by design
(the quantity under test there was the generation prompt). Here the quantities
under test include **rewrite depth** and **cost per accepted story**, which only
exist downstream of the gate, so `scripts/measure_narrative.py` calls
`run_pipeline` — the same function `warm` and `eval` call. Stages 1, 2, 6, 9 and
12 (the two §18 excuses, the stage the user complained about, and the two 120-200
word stages where the padding clause bites hardest), all three themes, n=2.
Generation and judging are separate subcommands so the ruler can be re-run over
stored text at no generation cost, exactly as `--revalidate` exists in
`measure_length.py` for the decoder.

**11.52 — what would make this change a REGRESSION, decided before the numbers
arrive.** Any of: mean or minimum decodability down, any story finishing below the
95% gate that would not have before, p95 rewrite depth above 1 (§1's budget is 2
and the decodability-driven p95 is 1, so there is exactly one rewrite of
headroom), length compliance below 100%, or cost per accepted story up by more
than the cache-write cost of the one-off prefix invalidation. Stated here so the
Gates section cannot grade itself.

### PROVED — the cheap structural proxy does not work

**11.53 — a MEASURED NEGATIVE RESULT, and it is the reason the shipped instrument
is a paid judge and not a free function.** A structural proxy was tried first,
because free and deterministic beats paid and stochastic. Five candidate signals
were computed over 19 servable pool stories that I read and labelled myself
(9 word-list, 10 story) before any judge saw them. **None separated the labels:**

| signal | word-list mean | story mean |
|---|---|---|
| same-opening share (first two words) | 0.363 | 0.301 |
| late-third word novelty | 0.274 | 0.290 |
| an intent or adversative word present | 0.44 | 0.70 |
| connective share | 0.087 | 0.103 |
| max repeated-bigram share | 0.076 | 0.075 |

Two of the five point the WRONG WAY. The mechanism is that the two failure modes
look opposite to a surface metric: story 1670 repeats one frame seven times and
reads as repetitive, story 7274 tours four locations with varied openings and no
repetition at all, and both are word lists. A metric that catches one misses the
other.

**The before/after run then confirmed it from the other direction, which is the
part worth keeping.** Same-opening share went **UP** 0.310 -> 0.415 across the
change while the judge's narrative score went **UP** 3.77 -> 4.43 — the proxy is
anti-correlated with the thing it was built to proxy on this very sample. The
cause is legible in the text: the after arm writes "Rabbit did… Rabbit ran…
Rabbit felt…" (a bare name, no determiner) inside genuinely well-structured
stories. So the three functions ship as named DIAGNOSTICS with no composite score
and no threshold, and `tests/test_narrative.py` asserts there is no `score` field
on `NarrativeSignals` — a 0-3 number built out of signals measured not to work
would read as coverage, which is the whole failure being fixed.

**11.54 — a second MEASURED NEGATIVE RESULT, on the judge prompt, reverted.** The
first probe showed one persistent false positive: story 24 ("Sam got in the rocket.
Up, up, up. Sam got to the moon… Sam got back. Sam felt glad.") scored 4 three
times out of three, the judge inferring a want the text never states — the same
"could be interpreted as" defect 9.50 fixed on the safety half. A clause was added
saying a want must be IN the text and that a trip out and back is a list. It did
not work and it cost true positives: the label gap **shrank from 1.76 to 1.38**,
story 7273 fell 4 -> 2 and 6416 4 -> 2.5, while story 24 stayed at 4/4. Reverted.
This is the fourth time on this codebase that a pass at a judge prompt broke a case
that had been passing (8.70-8.82's four, 9.53's one), and the only reason it was
caught is that the probe ran the WHOLE labelled set both times rather than the case
being worked on. Story 24 is recorded as a known residual, not fixed.

### PROVED — the judge, both directions, multiple runs (STATE invariant 5)

**11.55 — the judge separates the labels, and the metric is installed where a
regression would be seen rather than only in a script.** Two independent whole-
fixture runs, each three runs per case (six judgements per text), via
`STORYFORGE_LIVE_NARRATIVE=1 pytest tests/test_narrative.py`:

| run | word-list mean (max) | story mean (min) | gap |
|---|---|---|---|
| 1 | 1.93 (4) | 4.40 (3) | **2.47** |
| 2 | 1.67 (2) | 4.33 (3) | **2.67** |

Both directions asserted, and the second direction is the one that matters: the
labelled STORIES are written in the same tiny register with the same short
sentences and the same repetition, so a judge that scored everything low would pass
a gap test by accident. The fixture therefore also requires the stories to land
above the scale midpoint and the lists below it. `make eval-smoke` now prints a
narrative score per story and the run's mean, and **exits on nothing** — §18's
register point is correct at the bottom of the ladder, so there is no floor that
could refuse a legitimately spare stage-1 story.

### PROVED — the before/after, 60 stories through the real pipeline

**11.56 — the headline: narrative up at every stage above 2, and no decodability,
gate, rewrite-budget or length cost.** 30 stories an arm, stages 1/2/6/9/12 x 3
themes x 2, through `run_pipeline` (gate, rewrite loop and moderation, the same
function `warm` and `eval` call), scored afterwards by ONE ruler at three runs a
story and the median taken. The `before` arm is not a paraphrase: it reconstructs
`SYSTEM_RULES` in-process and asserts the result is **byte-identical to
`git show HEAD:…/llm/generate.py`**, then refuses to run if it is not.

| | before | after |
|---|---|---|
| produced / attempted | 30/30 | 30/30 |
| **narrative mean (1-5)** | **3.77** | **4.43** |
| narrative histogram | {1:2, 2:4, 3:3, 4:11, 5:10} | {1:2, 2:1, 3:3, 4:0, 5:24} |
| decodability mean / median / min | 0.9914 / 1.0000 / 0.9545 | **0.9922 / 1.0000 / 0.9559** |
| stories below the 95% gate | 0 | **0** |
| FIRST-DRAFT decodability mean | 0.9654 | **0.9690** |
| first drafts below the gate | 9/30 | **7/30** |
| rewrite depth mean | 0.333 | **0.233** |
| rewrite depth p95 (raw / decodability-driven) | 1 / 1 | **1 / 1** |
| length compliance | 100.0% | **100.0%** |
| OOV instances · over the story-word cap · zero story words | 0 · 0 · 0 | **0 · 0 · 0** |
| accepted | 28 (93.3%) | 27 (90.0%) |
| **cost per accepted story** | **$0.0323** | **$0.0477** |
| mean output tokens | 2343 | 3586 |

Per stage, narrative mean:

| stage | 1 | 2 | 6 | 9 | 12 |
|---|---|---|---|---|---|
| before | 2.00 | 4.33 | 3.67 | 4.17 | 4.67 |
| after | 2.50 | 4.67 | **5.00** | **5.00** | **5.00** |

**Stage 6 — the stage the user named and the one §18 does not excuse — went from
3.67 to 5.00, all six stories at 5/5/5.** The decision §10 asks for shows up
verbatim in the prose: *"Rabbit can stay by a rock, or rabbit can try to find the
burrow again. Rabbit did not want to stay."* The stage-9 padding failure 11.41
diagnosed is gone: the before arm's stage-9 animals story is 20 sentences of which
18 begin "The rabbit", and the after arm's is one arc that tries two paths in turn.

**§0 is not merely intact, it moved the right way, and that was not guaranteed.**
Richer narrative means more words and more chances to fall below 95%; the measured
result is the opposite — first drafts below the gate fell 9 -> 7 and mean rewrite
depth fell 0.333 -> 0.233, so the change spends LESS of §1's rewrite budget than
before rather than the one rewrite of headroom it was allowed. The mechanism is
plausible and is NOT proved: a story with a plan reaches for fewer words it does
not have, because "say a different next thing" is easier when there is a next
thing. n = 30 an arm, so read the rewrite numbers as a distribution that did not
worsen, not as a proved improvement.

**11.57 — a REGRESSION this change CAUSED at stage 1, its measured cause, and the
fix.** Stage 1 went from 6 of 6 accepted to **4 of 6**, and both losses are grammar
rejections of the same word: *"Rabbit cant nap."* and *"Dragon cant spin."*

The cause is an inventory fact, PROVED with `validate_word(w, mastered_ids(1))`
rather than assumed: at stage 1 **`not`, `no`, `cannot`, `never` and `nope` are all
violations**, and the stage-1 allowlist contained no word with the letter `o` in it
at all. A stage-1 story cannot say that something did not happen. The band asked
for "something that is wrong", the model complied, and the only stage-1-decodable
approximation to a negation was `cant` — a real English word, then in the
allowlist, and decodable as c-a-n-t, so **the gate passed it at 100% and §13's
grammar half caught it**. Two layers behaved exactly as designed and the story
still died.

The fix is a stage-1-only clause stating the inventory fact and showing a problem
said forwards ("A cat sat on the map. Sam did tap the cat. The cat ran."). It does
NOT name `cant`: the word has since left the allowlist under another agent's
vocabulary work, and naming a construction raises its salience in both directions,
which is the lesson invariant 5 is built out of. A test asserts the premise as well
as the clause, so an inventory change that gives stage 1 a negation turns the suite
red instead of leaving a stale instruction in a live prompt.

**And the honest answer for stage 1 is that it cannot get much better.** 2.00 ->
2.50 on a 1-5 scale is inside the run-to-run noise of this judge, and §18's excuse
is not merely rhetorical at this stage: with no `o` and no negation there is no way
to state an obstacle, so the ceiling is a want that is met. That is a 3 at best on
a scale where 4 requires a want and a resolution and 5 requires something in the
way. Stage 2 is where the ladder starts, and stage 2 measured 4.33 -> 4.67.

**11.58 — the cost, which IS a regression by 11.52's own pre-registered
criterion.** Cost per accepted story **$0.0323 -> $0.0477, +47.7%**; per produced
story $0.0302 -> $0.0429, +42%. It is not the input side — the block is in the
cached prefix and costs 0.1x after the first request at a stage — it is **output
tokens, +53% (2343 -> 3586)**, and on Sonnet 5 with adaptive thinking on
(invariant 9) those are thinking tokens, not story tokens: a 150-word story is
about 200 tokens.

Per stage: +94% (1), +112% (2), +121% (6), +18% (9), +52% (12). **The doubling
happens at stages 1 and 2, where the ask is four sentences long and contains no
planning verb at all** — which is the evidence that the driver is the existence of
an additional quality dimension to satisfy and not the block's phrasing. A leaner
rewrite of the upper bands was therefore NOT attempted: the measurement says it
would not pay, and the honest report of a +42% cost is better than an unmeasured
claim that it was optimised away. Extrapolated, a full `make eval` moves from
$5.41 to roughly $7.7 at the same 180-story matrix, and a `make warm` refill costs
about half again as much per story.

**11.59 — two things that happened to the measurement environment, recorded because
they bound what these numbers mean.**

* **The allowlist changed underneath the run.** `allowed_words` went from **64,296
  rows to 34,366** partway through this pass (another agent's vocabulary work;
  `precompute.py` and `data/` are theirs). Both arms are PROVED to have run against
  the 64,296-row table — stage-1 stories in both arms use `amid`, `scan` and
  `admit`, which are absent from the current 78-word stage-1 list — so the arms
  remain comparable **to each other**, which is what a before/after needs. They are
  NOT comparable to anything measured after the change, and `make eval-smoke`'s
  numbers below were taken after it. Sight words (38), `data/blocklist.txt` and
  `gpcs.csv` were unchanged across the whole pass, verified by hash.
* **The working tree was reverted out from under this pass twice, and then this
  pass's code was committed by another agent.** Twice during the run every one of
  my files read back as its HEAD version — three new files gone, four files' edits
  gone, this whole DEVIATIONS section gone — and both times the content returned a
  few minutes later: a concurrent agent's `git stash` / `stash pop` window. The
  work then landed in **`8979cc0` "fix: the four defects the user found by actually
  using the app"**, which is not my commit: `llm/narrative.py`,
  `tests/test_narrative.py`, `scripts/measure_narrative.py` and the edits to
  `generate.py`, `pipeline.py`, `eval.py` and `test_llm.py` were all swept in with
  someone else's change.
  This is the SECOND time — 9.52's process note is the first, and it made exactly
  this prediction: "a shared tree with two agents in it has no commit boundary, and
  the F9 revert in 9.53 could as easily have been committed in its broken state."
  With three agents it now also has no *file* boundary: during the stash window a
  `pytest` run or a `git diff` would have reported honestly on code that was not
  the code being worked on. Nothing was lost here only because every measurement
  artefact lives in `/tmp` and every source file was copied to `/tmp/nar_backup`
  as it was written, and the recovery check was a `diff` against those copies
  rather than a glance at the file. **A measurement that costs real money must not
  live only in the working tree, and "the file looks right" is not a check when
  another process can rewrite it between two commands.**

## Phase 8 — the two visual defects a live test of the app surfaced: no art at all, and marking that reads as errors

Written before the changes, per §0. Two defects, reported by the user after
driving the live app, both reproduced in a real browser before a line was
touched (screenshots in PROVED below).

**Defect 1.** There are no illustrations. `data/seed/assets.csv` seeds 33 asset
rows, `scene_tags.py` tags every segment, and the logs say "7 with art" — which
counts *database rows*, not pictures. Zero image files have ever existed in this
repo, so every `<img src>` 404s and `SegmentArt`'s `onError` demotes every
segment to `ui/Placeholder.tsx`: a beige striped rectangle taking half the
reading screen. §12's mechanism was built completely and its content never was.

**Defect 2.** The reading line marks words the way a teacher marks a worksheet.
Measured at stage 6 on the real pool: one screen carried **five amber outlined
boxes** — `cave`, `wings`, `dragon`, `dragon`, `dragon` — each containing a
meaningless grey striped square, plus a thin gold rule accumulating under every
word the child had tapped. Four visual treatments, and to a five-year-old a box
around a word means a mistake.

### 11.20 — the art is SVG, generated by a committed script, served as static files

Raster art is wrong for this app on three counts: the DigitalOcean registry cap
is 500 MB and the image already carries CMUdict; 33 PNGs at 1200x900 is
megabytes of page weight on a tablet; and §06's art direction ("flat gouache",
"limited palette", "simple readable silhouette at thumbnail size") is a
description of *vector* art. So the pool ships as SVG.

The 33 files are **generated by `scripts/generate-art.mjs`, and the output is
committed**. The generator is not part of `bun run build` and is never run at
serve time — it is a one-shot tool in the same drawer as `scripts/vendor-cmudict.py`.
The reason it exists rather than 33 hand-written files is §06's *consistency
gates*: "same palette across every theme, same texture, same light direction
(upper left)". Thirty-three independently hand-written SVGs drift on exactly
those three axes; a shared parts library cannot. A single `PALETTE` object holds
the eight §06 colours, one `grain()` filter is applied to every canvas, and every
rounded form takes its highlight from the same upper-left offset.

### 11.21 — the asset pool is prioritised by MEASURED tag frequency, not by guess

`story_segments.scene_tags` in the live database holds **839 tagged segments**
over 38 of the 42 closed-vocabulary tags (`castle`, `snow`, `fish` and
`swimming` have never been emitted). But the tag histogram is not the thing to
prioritise on, because a segment renders **one** asset, chosen by
`select_asset`'s most-overlap rule. The right question is which *asset* wins, and
that is already recorded in `story_segments.asset_id`:

| asset | segments | asset | segments |
|---|---|---|---|
| animals/forest-02 (rabbit hiding) | 181 | dragons/fire-01 | 33 |
| dragons/cave-01 (dragon asleep) | 162 | space/rocket-02 | 31 |
| space/rocket-01 | 91 | dragons/meadow-01 | 15 |
| dragons/sky-01 | 73 | 20 more | ≤10 each |
| space/spacewalk-01 | 64 | | |
| space/moon-01 | 62 | | |
| animals/forest-01 | 39 | | |

**831 of the 839 tagged segments resolve to an asset, and nine assets cover 736
of them (88.6%).** Those nine were drawn first and reviewed hardest. All 33 are
drawn — three segment assets (`dragons/forest-01`, `animals/bird-01`,
`animals/nest-01`) currently win zero segments and are drawn anyway, because a
pool row with no art is the defect being fixed and the next `make warm` can
reach them.

No new tags. The vocabulary is closed by design and a tag outside it is a
seed-time error (`seed_assets` raises); the existing 33 rows' tag sets are
unchanged.

### 11.22 — the asset's identity is the picture, not the URL, so `make seed` can revise a path in place

The urls have to change: they say `.png` and the files are `.svg`. But
`seed_assets` keys on `url`, and its own docstring says why that matters —
"deleting and re-inserting would renumber ids and orphan
`story_segments.asset_id`". Changing a url under a url-keyed seeder is the same
bug by a different route: 33 new rows would be inserted, the old 33 would
survive, and **831 live segments would keep pointing at asset ids whose files do
not exist** — i.e. the fix would ship and change nothing a child sees.

So the natural key becomes `(theme, kind, tags)` — the picture — and `url` is a
mutable column. Seeding is still idempotent, ids are preserved, and the 831
existing segments gain art with no data migration and no repointing script.
Two guards come with it, because the new key is weaker than a unique index:

- a duplicate `(theme, kind, tags)` in the CSV is a `SystemExit`. Under the old
  key two rows with identical tags were merely redundant; under this one the
  second would silently overwrite the first's url.
- after seeding, any asset row whose url is not in the CSV is logged as an
  orphan with its id, because that is the state this change exists to prevent
  and it is invisible otherwise.

### 11.23 — `/assets/art/v1/...`: the version is in the path because the cache header is immutable

`main.py` serves everything under `/assets` with
`Cache-Control: public, max-age=31536000, immutable`, and that is safe *only*
because Vite content-hashes what it puts there. Files copied from `public/` are
not hashed. A revised drawing at a stable path would be invisible for a year to
every browser that had already loaded it. `v1` in the path is the hash, done by
hand: a redrawn pool is `v2`, a new CSV url, and a new immutable URL. Nothing is
ever revised in place, which is the invariant the header already assumes.

### 11.24 — story-word cutouts are Vite-imported modules, not URLs assembled at runtime

The 1:1 transparent cutouts (§06's third ratio) are the picture inside a
story-word chip. They are NOT in `assets.csv`: the pool table serves
`kind='segment'` and `kind='cover'`, `select_asset` defaults to `kind="segment"`,
and no API surface returns a cutout, so a DB row would be dead weight. Instead
`lib/art.ts` holds a **literal `Record<string, string>`** of `import`ed SVGs,
keyed by the 14 story words the live pool actually uses (`rocket, moon, rabbit,
dragon, cave, stars, forest, burrow, wings, fire, planet, astronaut, giraffe,
treasure` — from `SELECT unnest(story_words)` over servable stories).

Literal imports, not `` `/assets/cutouts/${word}.svg` ``. StoryWordChip's
contract is "never a broken image, never an invented URL", and a template
literal invents one for every word not in the set — 14 hits and an unbounded tail
of 404s that only `onError` hides. A literal map answers "no picture" with
`null`, which is a fact, and Vite hashes the emitted files so 11.23 does not
apply to them.

### 11.25 — a chip with no cutout shows no picture, not a striped square

The chip used to render `Placeholder` at 1:1 when it had no art, which is how
every chip in the live app ended up carrying a 24px grey striped square: no
cutouts existed, and `assetFor` was never even wired up from `ReadingView`. §06's
"never an empty box, never a broken-image icon" is about the *segment frame* — a
4:3 hole in the layout that must not collapse or reflow when art arrives. A 24px
square inside a word is not that hole. It is decoration that means nothing, and
"a picture is on its way" is not information a five-year-old can use.

So: cutout if there is one, and if there is not, the chip is the word with no
picture. `SegmentArt` keeps `Placeholder` exactly as it was — that is the path
§06 legislates and it is verified still reachable in PROVED below.

### 11.26 — the story-word chip renders on FIRST APPEARANCE ONLY

This is the actual bug behind the user's report, and the style guide already says
so. §07, verbatim: "Amber ring, picture cutout on the left, auto-speaks once on
first appearance **then behaves like any tappable word**."

`StoryText` rendered a chip for *every* occurrence, so the stage-6 screen above
carried three chips for `dragon`. The chip's whole purpose is the introduction —
a picture and one utterance for a word the reader cannot decode. An introduction
that repeats five times is not an introduction, it is an annotation, and the
audience for a per-occurrence annotation is an adult marking a page.

`StoryText` already computed the set it needed for the audio (`pending`: first
occurrence in this paragraph, of a story word the *session* has not yet
introduced). The same set now decides the chip, so the two can never disagree —
a chip that does not speak, or an utterance with no chip, are both unreachable
states now rather than two independent bugs waiting.

The logic moves to `lib/storyWords.ts` as a pure function so it is unit-tested
rather than verified by looking at it. Consequences, all intended:

- a word introduced on screen 1 is a plain tappable word on screens 2-7;
- comprehension prompts and branch labels pass no `shouldAnnounce`, so they
  render no chips at all — by then the word has been introduced, and §07 says it
  behaves like any tappable word;
- **every word is still tappable and still speaks** (§11), which is the safety
  net that makes this safe: a child who has forgotten `dragon` taps it.

`onAnnounced` is now called by any chip that speaks, not only the one holding
the microphone. A child who taps the second of two new chips used to consume the
audio without the word being recorded as introduced, so the next screen
introduced it again.

### 11.27 — the amber RING goes; the picture is the marking

§07 asks for an amber ring and this deviates from it deliberately. A 2px ring
with a 10px radius around a word is, to a child, a box — and the user's words
were "boxes around them ... boxes on a page mean mistakes". The berry violation
colour is kept out of kid mode for exactly this reason (§02: "Do not introduce a
red 'wrong answer' state in kid mode"); an amber box is the same mistake in a
warmer hue.

The chip keeps its amber-100 fill and loses the ring. **The marking is now the
picture**: a word with a small drawing beside it reads as help, at any age, and
it is the one treatment on the page that cannot be mistaken for a correction.
Colour is not carrying any load on its own here, so §11's "never colour alone"
is satisfied by the picture rather than by a rule.

### 11.28 — the "already heard" gold rule goes

§07 lists four word-token states and this removes the fourth's styling. `heard`
put a permanent gold `border-bottom` under every word the child had tapped, and
it accumulates: it is monotonic, it never resets inside a segment, and by the
time a struggling reader has tapped fifteen words the whole paragraph is
underlined and the mark distinguishes nothing. It is a *record of the child's
difficulty, rendered on the page they are reading* — the parent dashboard is
where that belongs, and it is already there (`gpc_stats`, per-GPC accuracy).

Feedback stays, residue goes. A tap is gold-300 fill + scale 1.05 held 250ms past
the start of speech (§05, unchanged, and still exempted from
`prefers-reduced-motion` because it is feedback); then the fill transitions back
to transparent over `--sf-dur-tap` and the page is clean again. The `'heard'`
state is still tracked in `WordToken`'s type and still set — `data-state` is
observable, and the sequencer keys off the same transition — it simply paints
nothing.

### 11.29 — the theme picker renders cover art; the emoji stays as the fallback it was declared to be

`ThemePicker`'s own comment says "cover art is not shipped yet, so the card falls
back to `themes.emoji`. §07 carves out exactly one exception to 'no emoji as
artwork' and this is it ... If a cover ever arrives on the theme row it takes
over and the emoji retires." Three covers now exist, so it retires — resolved
through a literal slug→import map in `lib/art.ts` (same reasoning as 11.24), not
through a new API field, because `GET /api/themes` returning an asset url is
backend work this pass does not need.

A theme with no cover in the map still renders its emoji, so a fourth theme
added tomorrow degrades to the documented fallback rather than to a blank card.

### 11.30 — what is NOT changed

- **The admin validator playground.** `.sf-tok-*`, `.verdict-*`, `VerdictMark`,
  `validatorView.ts` — untouched. §17's demo shows four verdict colours to a
  grader, which is a different surface for a different audience, and STATE
  invariant 7 lives there. It is re-verified in `dist/` in PROVED anyway,
  because "I did not touch it" is not evidence.
- **Word-token geometry.** `.sf-word` keeps 12px vertical padding, 5px margin,
  `min-height: 48px`, and its measured 62px target with 10px clearance. §11's
  floor sentence is "48px minimum everywhere, **64px for kid primary actions**,
  8px minimum gap" — 64px governs `Read a story` and `Next`, not words in a
  paragraph, and those buttons are `size="kid"` and unchanged. Raising word
  tokens to a 64px *square* floor would put 16px of extra air around `a` and `I`
  and wreck the reading rhythm to satisfy a rule that does not apply. Both floors
  are re-measured by computed style in PROVED.
- **`scene_tags.py`.** Read for tag names, not edited. The vocabulary stays 42.
- **`precompute.py`, `blocklist.txt`, `frequency-en.csv`, `llm/generate.py`** —
  another agent owns these.

---

## Phase 8 — the blocklist had no morphology, and §5.4 named the wrong corpus

Numbered **11.1-11.16** to stay clear of the concurrent art/prompt work at 11.20+
and 11.40+. Logged **before** the changes were made. Every claim marked **PROVED**
was measured in this repo with the command shown; everything else is reasoning and
is labelled as such. Owned surface: `storyforge/precompute.py`,
`data/blocklist.txt`, `data/frequency-en.csv`, the two new word lists, and their
tests. `apps/web/**` and `llm/generate.py` are another agent's.

### 11.1 — P0: `precompute.py:72` filtered with `if word in blocked`, and the blocklist holds only BASE forms

**PROVED.** `data/blocklist.txt` is 223 base forms; the filter is an exact string
match. Against the live `allowed_words` (9,360 distinct words) the exact-match hit
count is **0** — which is exactly why every previous check passed — while **50
morphological variants of blocked entries were present**, among them `fucks`,
`fuckers`, `fuckin`, `dicks`, `cocks`, `asses`, `butts`, `murders`, `murderers`,
`murdering`, `killer`, `killers`, `killings`, `corpses`, `pisses`, `pissing`,
`punches`, `punching`, `attacks`, `attacker`, `attackers`, `attacking`, `addicts`,
`addicted`, `freaks`, `freaking`, `freaked`, `freaky`, `morons`, `hostages`.

**PROVED not realised harm.** 0 of 554 generated stories contain any of these
stems, because §6 offers only the top ~400 words by frequency per stage and these
sit at rank 8,000+. This is a latent hole and is fixed as a hole. It is a hole
worth closing anyway, because `allowed_words` is also §5.4's browsable admin
artifact and its demo exhibit — "here is every word a stage-3 reader is permitted
to see" is a claim about the table, not about the prompt window.

### 11.2 — the fix is FORWARD inflection generation per entry, not substring matching and not candidate stemming

`data/blocklist.txt`'s header and `precompute.py:48` both already say why
substring matching is refused: it is the Scunthorpe problem and it silently
deletes `class`, `grass`, `bass`, `assess`, `cocktail`. That reasoning stands
unchanged. Of the two morphology-aware options, this takes the second:

- **Rejected — stem the candidate.** Needs an inverse morphology that guesses a
  base form from a surface form, and every guess is a chance to map an innocent
  word onto a blocked stem. `butter` → `butt` is the obvious one and it is exactly
  the false alarm the mandate's own crude sweep produced.
- **Chosen — generate the inflection set per blocked entry.** The blocked set
  becomes a finite, enumerable set of literal strings, and matching stays the exact
  `in` test it is today. An innocent word can only be blocked if some blocked entry
  *generates that exact string*. That makes the innocent direction provable by
  construction rather than by hoping a stemmer behaves, and the whole expansion can
  be printed and reviewed.

Generated forms are **inflectional only** — plural (`-s`/`-es`, `y`→`ies`,
`f`→`ves`), past (`-ed`), and progressive (`-ing`), with final-consonant doubling.
Derivational suffixes (`-er` agentive, `-y`, `-in`) are **not** generated, because
that is where the collisions live: `butt` + `er` = `butter`, and `-er` on a noun is
not an inflection. The four derivational forms that matter are added as explicit
LINES instead (11.5).

### 11.3 — the `proper-noun fragments` section is deliberately NOT inflected, and that is what kills three of the mandate's false alarms

**PROVED.** `san`, `los`, `las`, `del`, `inc`, `llc`, `ltd`, `corp` are not words
with morphology; they are fragments of place and brand names that an adult
frequency list surfaces. Inflecting them manufactures false positives: `los` + `es`
= **`loses`**, `san` + `s` = **`sans`**, `corp` + `s` = `corps`. The mandate named
`loss` and `sans` as false alarms from crude suffixing and this is the mechanism.
The carve-out is not a special case bolted on — `data/blocklist.txt` already
separates this section with its own header saying exactly this ("Not
inappropriate, just wrong"), so the file's existing structure carries the rule.

The section is matched by its header text, and a test asserts the header exists.
Renaming it turns the suite red rather than silently inflecting fragments again.

### 11.4 — the false-alarm audit, run in both directions, and the two judgements it forced

**PROVED.** The expansion is 223 entries → **983 forms** (760 generated). Two of
the 760 generated forms are also words on the Dale-Chall familiar-word list, and
both are genuine inflections of deliberately-kept hazards, so both are judged
rather than waved through:

| form | from | judgement |
|---|---|---|
| `beating` / `beats` | `beat` | **Blocked.** DEVIATIONS 8.74 KEPT `beat` because "`beat`-as-strike is co-dominant, not recessive", and 8.73's own internal-consistency argument is that blocking one inflection of a verb while its siblings are unblocked "is not a hazard control; it is a false-positive generator". 8.73 refused to add siblings because that would extend **layer 1's short-circuit in `moderate()`**, which was measured firing 3-for-3 on non-harm. **This change does not touch that short-circuit** (11.6). Not offering `beats` costs vocabulary; it cannot kill a clean story. |
| `dies` | `die` | **Blocked.** `die`, `died` and `dying` are all already entries; `dies` is the missing fourth. |

`mocking` is NOT a false alarm — `mock` and `mocked` are both entries, so `mocking`
is the missing third form of a blocked verb. `butter`, `loss` and `sans` are false
alarms and all three are **PROVED absent** from the expansion. The other false
alarms the mandate flagged behave as it predicted.

Both directions are asserted: every one of the 50 variants is rejected, and
`class`, `grass`, `bass`, `assess`, `cocktail`, `butter`, `loss`, `sans`,
`classic`, `passes`, `glasses` are all accepted.

### 11.5 — five words ADDED to `data/blocklist.txt`: 223 entries -> 228

Four are the derivational forms 11.2 declines to generate, and each is the missing
sibling of a family already on the list — 8.73's consistency test, applied in the
direction it points here:

| added | family already present | why the hazard is in the WORD (8.14's test) |
|---|---|---|
| `killer` | `kill`, `killed`, `kills`, `killing`, `murderer` | No sentence makes it safe. `killers` then falls out of 11.2's plural rule. |
| `attacker` | `attack`, `attacked` | Same; `attackers` follows. |
| `fuckin` | `fuck`, `fucking`, `fucked`, `fucker` | A clipping, not an inflection. |
| `freaky` | `freak` | `-y` is derivational; `freaks`/`freaked`/`freaking` fall out of 11.2. |

The fifth is a **new finding of this sweep, not one of the 50**:

- **`pimp` was in `allowed_words` at STAGE 1** — min_stage 1, offered to
  five-year-olds — and it is not on the blocklist in any form. It was invisible to
  the mandate's variant sweep precisely because it is a base form, not an
  inflection. `pimps`/`pimped`/`pimping` follow from 11.2. **PROVED** by the sweep
  in this entry: 48 further hazard-class base forms are present in `allowed_words`
  (`bong`, `dumbass`, `cum`, `lust`, `pervert`, `retard`, `genocide`, `revolver`,
  `shotgun`, `grenade`, `assassin`, `prostitute`, ...). All 48 are removed by the
  register filter (11.8) because none is child vocabulary, which is why only `pimp`
  is added here: it is the one whose hazard is unambiguous *and* which needs to be
  caught by layer 1 in a story BODY, not merely kept out of the word bank.

All five are in the profanity/sexual/violence classes that 8.14 declared stay
"unchanged and unexamined-for-removal". None has an innocent sense that is
dominant in decodable children's prose.

**Counted, not assumed**, because 6.15's own bug was a row count moving the wrong
way and 8.17 made lines and entries equal so the list can be verified by counting
it. `load_blocklist()` reads **ONE WORD PER LINE**; a space-separated rewrite once
loaded 22 entries instead of 240 with no symptom but a row count. The loaded count
is reported with the run.

### 11.6 — `moderate.blocklist_check` keeps EXACT matching, deliberately, and this is the load-bearing distinction of the whole change

`load_blocklist()` has three callers: `precompute.compute_rows` (which words are
OFFERED), `llm/generate.py:232` (a redundant re-filter of `allowed_words`), and
`llm/moderate.py:307` (**§13 layer 1, which short-circuits `moderate()`**).

Only the first is changed. `blocklist_check` is untouched and still matches the
223+5 literal entries.

The reason is the one 8.14 and 8.73 each proved the hard way, from opposite
directions: **a layer-1 hit returns immediately, so an entry there does not narrow
vocabulary — it stops layer 3 from ever applying the hazard-vs-harm judgement §13
was calibrated for.** 8.14 measured 2 of 3 layer-1 rejections in a 180-cell matrix
being the single word `high`; 8.73 measured 3 of 9 rejections in a 60-story run
being `hurt` in sentences whose meaning is the ABSENCE of harm. Making layer 1
morphology-aware would newly short-circuit on `beats`, `beating`, `butts`,
`mocking`, `teases`, `shots`, `blades`, `swords`, `punches` — which is exactly the
move 8.73 refused, stated as a rule: *"Adding the siblings would move the wrong way
— it would extend a short-circuit that is already measured to be firing 3-for-3 on
non-harm."*

Not offering a word and rejecting a finished story are different costs, and the
same expansion is right for one and wrong for the other. So the expansion is
applied where the cost is vocabulary and withheld where the cost is a clean story.

**Stated as a residual, not hidden:** a story that contains `fucks` despite the
word never being offered would pass layer 1 and be caught only by layer 3, the
Haiku judge. Closing that would require STATE invariant 5's full both-directions
live re-test of the 40-case fixture against the live judge over multiple runs
(~$0.14/run), which this pass is not authorised to spend. Recommended as its own
piece of work, with the section-aware expansion already built and available.

### 11.7 — P1: §5.4 named SUBTLEX and SUBTLEX is the wrong corpus for a five-year-old. This is a REAL deviation from the spec

§5.4: *"intersect with SUBTLEX top ~20k"*. `data/frequency-en.csv` is exactly
that — adult film-subtitle frequency, by way of `wordfreq` (2.1). It proves a word
is common **in adult speech**. It says nothing about whether a five-year-old knows
it, and the spec never asked that question.

The user found the consequence at the stage where it is fully exposed:
**`amid`** (rank 7,528), plus `lebron`, `nixon`, `ronald`, `gibson`, `griffin`,
`drafted`, `tablets`, `classics`, `blunt`, `sack`, `toss`, `hottest`, `stamps`,
`mod`.

**Three PROVED measurements frame the fix.**

1. **The register defect is concentrated in STAGE 1, and stage 1 is the stage §7's
   ladder can serve to any child.** `pipeline.PROMPT_WORD_LIMIT` is 400 and the
   query is `ORDER BY freq_rank LIMIT 400`. Stage 1 holds 157 rows, so **all 157
   are offered and the rank cutoff is 19,295** — the entire tail. Every other
   stage's offered window cuts off at rank ≤ 3,346 (stage 2) and ≤ 702 by stage 9,
   so `amid` at 7,528 never reaches a prompt at stages 2-12. The user reported it
   "at stage 2"; §7's ladder answers an empty (stage,theme) pair by serving a
   LOWER stage, and STATE invariant 6 says a lower stage is always safe *for
   decodability* — it says nothing about register. That is the path.
2. **The current output overshoots §5.4's own expectation by 3.7x.** §5.4 says
   "Expect ~150-2,500 words/stage". The live table runs 157 → **9,360**. The spec's
   expected shape is independent evidence that the corpus is wrong, written before
   anyone measured it.
3. **The 150 floor at stage 1 is currently met by words that should not be there.**
   Of stage 1's 157 rows, **27 are proper nouns or non-words** (`nasa`, `sam`,
   `tampa`, `adam`, `santa`, `napa`, `nixon`-class fragments, `im`, `ip`, `aca`,
   `sas`, `das`, `nic`, `mina`, `nana`, `mit`, `nan`, `nat`, `pac`, `dat`, `ana`,
   `nam`, `amidst`, `dan`, `pam`, `sid`, `stan`) and a further **61 are absent
   from the Dale-Chall familiar-word list** (`amid`, `admin`, `stat`, `sic`, `dis`,
   `mis`, `pimp`, `scam`, `spam`, `tactic`, `static`, `stint`, ...). See 11.11.

### 11.8 — the register filter is a POSITIVE child-vocabulary list (Dale-Chall), morphology-expanded by the same generator as the blocklist

`data/child-vocabulary.txt` — the **Dale-Chall 3,000 familiar words**, 2,941 lines
/ 2,883 alphabetic entries, vendored and SHA-pinned via
`scripts/vendor-word-lists.py` from `textstat`'s `easy_words.txt`, which is the
standard machine-readable form of the list. Definition: words familiar to at least
80% of fourth-graders. That is a direct measurement of the question §5.4 failed to
ask.

**Why Dale-Chall over the alternatives, stated with the trade-off.**

- **vs. a tighter frequency cutoff at low stages** (the mandate's second option) —
  measured and rejected on the numbers. The cutoff that removes `amid` (7,528)
  also removes so much that stage 1 lands at 83-95; the cutoff that keeps stage 1
  at 108 is 8,000, which **readmits `amid`**. `amid` sits on exactly the boundary,
  so frequency cannot separate it from the words around it. That is the corpus
  error restated, not solved: a rank cutoff still asks "how common is this in adult
  speech".
- **vs. AoA (Kuperman) norms** — the better instrument in principle: 30k words with
  a mean age of acquisition, which would let each stage carry its own age bound
  instead of one global list. Its canonical hosts were not reachable
  (`crr.ugent.be/papers/AoA_51715_words.csv` → 404), and a dataset that cannot be
  re-fetched cannot be pinned as proof surface. Recorded as the recommended
  successor, not as work done.
- **vs. Fry's instant words** — 1,000 words, a sight-word ordering rather than a
  familiarity claim; strictly weaker for this purpose.

**The cost, named.** Dale-Chall is from 1948, so it omits real child words:
`panda`, `nip`, `mimic`, `antics`, `disc`, `cast`, `mist`, `tablet`-as-device. `nip`
is not hypothetical — 8.73 quotes a live story, *"The dragon did nip a cat."* Four
of the words the user flagged are KEPT because they are genuinely on the list and
genuinely child vocabulary: `stamps` (a stamp), `sack`, `toss`, `tablets` (a
writing tablet). At stages 9-12 a 4th-grade list is a conservative bound for a
10-11-year-old; §6 only ever offers 400 words and the offered window is unaffected,
but the browsable artifact is narrower than those readers.

Membership is tested against the **same forward inflection generator** as the
blocklist (11.2), so Dale-Chall's own convention — a listed word's regular
inflections are familiar too — is honoured: `cats`, `maps`, `sits`, `taps`,
`stamps`, `admits`, `camps` are admitted from `cat`, `map`, `sit`, `tap`, `stamp`,
`admit`, `camp`. One generator, used in both directions: expand-to-reject for the
blocklist, expand-to-accept for the register list. `-er`/`-est` are deliberately
NOT generated, which is what keeps `hottest` out.

### 11.9 — proper nouns get their own filter, kept as a second layer

`data/common-words-en.txt` — the **38,417 lowercase headwords of the Hunspell
en_US dictionary**, vendored and SHA-pinned by the same script from the LibreOffice
dictionaries repository.

The signal is **capitalisation in the source `.dic`**, which is the thing
`frequency-en.csv` threw away: `wordfreq` is lowercased, so `Nixon` and `nixon` are
indistinguishable in it. Hunspell keeps the case, so an entry that appears **only**
capitalised is a proper noun. **PROVED** on the words the user named: `nixon`,
`gibson`, `ronald`, `adam`, `sam`, `santa`, `tampa`, `nasa`, `dan`, `pam`, `tim`,
`stan`, `sid`, `napa`, `pac`, `ana`, `nam`, `nat`, `nan`, `dat`, `mit`, `ip` are
capitalised-only; `lebron`, `im`, `aca`, `cas`, `das`, `sas`, `nic`, `mina`, `nana`
are absent from the dictionary entirely; `amid`, `cat`, `class`, `grass`,
`cocktail`, `assess`, `panda`, `nip` are present lowercase.

Rejected alternatives: `/usr/share/dict/propernames` (1,308 first names — misses
`nixon`, `gibson`, `lebron`) and `/usr/share/dict/web2` (has `Gibson` and `Ronald`
but not `Nixon`, `Griffin` or `LeBron`), and both are macOS system files, not
reproducible artifacts. `nltk`'s names corpus would be a new dependency for a
weaker list.

`griffin` is **PROVED lowercase** in Hunspell and stays under this filter — a
griffin is a mythical beast and an entirely on-theme word for this product. It is
removed by the register filter instead, at every stage that filter reaches.

The filter is applied at **every** stage even though 11.8's list already excludes
proper nouns by construction. It is redundant while `REGISTER_FILTER_FROM_STAGE`
is 1 and that is the point: 11.11 documents raising it as the escape hatch if the
stage-ladder work is deferred, and this layer means taking that hatch cannot bring
`nasa`, `sam` and `tampa` back with it.

### 11.10 — the candidate POOL becomes (frequency top-20k ∪ child vocabulary), which is the inclusive half of correcting the corpus error

§5.4's "intersect with SUBTLEX top ~20k" is wrong in both directions, not just one.
**PROVED: 316 Dale-Chall words are absent from `frequency-en.csv` entirely** —
`acorn`, `beehive`, `blackbird`, `bandage`, `banjo`, `bumblebee`, `buttercup`,
`blindfold`, `boxcar` — words a children's reading app wants and adult film
subtitles rank outside the top 20k. 139 of them align at some stage; the union
admits **1,249 words** (with inflections) that the intersection refused, and takes
stage 1 from 69 to 78.

Words admitted this way have no `freq_rank`, and the column is load-bearing —
`ORDER BY freq_rank LIMIT 400` decides the prompt window. They are given an
**explicitly out-of-band synthetic rank** (`_UNRANKED_RANK_BASE = 100_000` plus
alphabetical position) so they sort to the tail of the offered window rather than
displacing genuinely frequent words, and so the synthetic value is obvious in the
admin artifact rather than passing as a measurement.

### 11.11 — the register filter applies at EVERY stage, and stage 1 falls to 78. The remedy is the ladder, not the filter

`REGISTER_FILTER_FROM_STAGE = 1`. The filter is chosen on word quality and is not
tuned to protect a row count.

**PROVED: no filter gives stage 1 both a child vocabulary and 150 rows — or even
100.** Five independent designs were measured:

| stage-1 policy | rows | `amid` gone? |
|---|---:|---|
| before this change (exact blocklist only) | 157 | no |
| + morphology-aware blocklist (11.2) | 157 | no |
| + proper-noun/non-word filter (11.9) | 136 | no |
| + child-vocabulary filter (11.8) — **SHIPPED** | **78** | **yes** |
| child vocabulary, adult frequency list ignored entirely | **78** | yes |
| child vocabulary + Hunspell-common with a rank cap, cap tuned 2,000 -> 8,000 | 83, 85, 88, 90, 95, **108 (readmits `amid`)** | only below 108 |

**78 is a ceiling, not a setting.** It is the number of child-vocabulary words the
stage-1 GPC inventory can spell with the frequency list removed from the question
altogether. Hand-curating the genuinely-child non-Dale-Chall stage-1 words on top
of it (`nip`, `panda`, `panic`, `pip`, `tad`, `sac`, `mist`, `cast`, `casts`,
`scan`, `span`, `spat`, `din`, `mid`, `madam`, `mimic`, `antics`, `disc`, `discs`,
`admit`, `admits` — 21 words) reaches **99**. One short of 100, which is close
enough to be worth stating and far too close to be worth engineering: a supplement
curated until a threshold goes green is the corpus error repeating with me as the
corpus.

**PROVED: stage 1 is the ONLY stage the filter takes below 150.** Every other stage
lands between 771 and 4,397, so **renumbering the ladder by one is sufficient** and
the filter does not need rethinking. That is the load-bearing measurement behind
the decision, and it is the question the ladder work depends on.

**The remedy, and whose decision it is.** The USER authorised retiring stage 1
rather than compromising word quality: *"let's go and reposition everything so that
the current stage two is the new stage one and we have 11 total stages."* This
works because stage inventories are **cumulative** — today's stage 2 already
contains stage 1's `s a t p i n m d` plus `g o c k e u r h b f l`, `ck ss ll ff`
and `x` — so "new stage 1 = old stage 2" is a larger starting inventory, not a
deletion of content, and it starts at **771** words. The accepted cost is that a
true beginner who knows only `s a t p i n` loses a rung of their own.

**The renumbering is NOT done here** and is deliberately out of this change's
scope: it touches 12 stage rows, every GPC and sight-word stage assignment,
`MAX_STAGE` constants, the §5.7 goldens (which name stages by number — `cat`@1,
`fox`@2, `city`@9), the vacuity ratchet measured at stage 12, the eval matrix,
§17's demo at stage 3, the build doc's Appendix A, and live production rows where
kids and stories carry `stage_id`. It is sequenced as its own migration.

**`tests/test_phase2.py::test_no_stage_is_starved` is the one existing assertion
this change touches, and the authority for touching it is the user's decision, not
my measurement.** It asserted `HAVING count(*) < 100` returns nothing at every
stage (from `36ca8e0`, docstring *"§18: a stage with almost no vocabulary starves
generation"*). Run through `justify-test-edit`, the honest verdict on my own
evidence was **FROZEN — ESCALATE**: the discriminating question, *would this test
still be wrong if I reverted my change?*, answers **no**, and "the filter I just
wrote makes the gate fail" is not evidence that the gate is wrong. What licenses
the edit is the citable ground the skill names — *the contract it tested was
replaced by a decision you can cite, and the decision is the user's*.

The edit is narrowed accordingly and is **stronger** in three ways, not weaker:

- the ≥ 100 floor is asserted **unchanged for every stage above 1**;
- stage 1 is **pinned to its measured ceiling** (`counts[1] >= 78`) rather than
  exempted, so it still fails if stage 1 degrades further;
- the exception is asserted to be a set of **at most `{1}`**, so if a SECOND stage
  ever falls short the test goes red — which is exactly the case where renumbering
  by one would NOT save us;
- and a tripwire asserts `counts[1] < 100`, so once stage 1 is retired the
  exception fails as dead code instead of quietly permitting a starved stage
  forever.

**Sabotage-checked, not assumed.** With `blocked_forms()` reverted to
`load_blocklist()`, the proper-noun filter disabled and the register filter turned
off, the table rebuilt to 74,001 rows and three guards went red:
`test_no_blocklisted_variant_reaches_the_allowlist`,
`test_no_proper_noun_reaches_the_allowlist_at_any_stage`, and
`test_no_stage_is_starved`'s dead-code tripwire. That sabotage also exposed a real
weakness and it was fixed rather than noted: two register tests had taken their
threshold from `REGISTER_FILTER_FROM_STAGE` itself, so switching the filter off
left them green. `REGISTER_FILTER_FROM_STAGE == 1` is now pinned in the test with
the reason, so raising it — the documented escape hatch if the ladder work is
deferred — turns the suite red instead of passing quietly.

### 11.12 — what is NOT changed

- **The decodability gate (§0).** No GPC row, no sight word, no alignment rule, no
  syllable cap, no tokenizer change. Every word admitted still has to align
  against the unchanged inventory, and `test_every_allowed_word_revalidates_as_decodable`
  is the proof. **PROVED**: 0 rows in the new output classify as anything other
  than DECODABLE, so nothing has been smuggled past `validate_word`, and the
  vacuity ratchet is untouched because the inventory is untouched. This entry is
  about which words are OFFERED.
- **`data/frequency-en.csv`.** Not regenerated, not re-pinned, not re-sorted. It is
  demoted from "the filter" to "one of two pools and the source of `freq_rank`",
  which is a change in how it is USED, in `precompute.py`.
- **`moderate.blocklist_check` and `llm/generate.py`.** See 11.6.
- **`load_blocklist()`'s contract.** Still one word per line, still returns the flat
  frozenset of literal entries, still what `moderate.py`, `generate.py`,
  `test_grammar.py` and `test_stage_coverage.py` import. The expansion is a NEW
  function beside it; nothing that reads the old one changes behaviour.
- **No existing assertion is modified or deleted.** Every test in this change is
  added.

## Phase 8 — question CONTENT and mastery accounting (11.60-11.69)

Numbered 11.60-11.79 to stay clear of the concurrent 11.0-11.52 block. Logged
**before** the changes. The trigger is a live user report: *"The questions aren't
very clearly given. They begin out of nowhere and then aren't really written
well."* Screen layout is another agent's; these entries are the TEXT and the
counters. Every number below is measured against the live `questions` table
(125 `tap_word` rows, 74 `mcq2` rows) or the live decoder, and PROVED/assumed is
marked per claim.

**11.60 — §11.3 does not require the tap-a-word INSTRUCTION to be decodable by
the child. That was our inference, and it is dropped. Bounded by a committed
instruction lexicon, not removed.**

The claim being retired is `llm/questions.py`'s own docstring: *"both render as
text a child has to read. So the same rule as the story applies: the decoder is
the gate, and it is never lowered for a question (§0)."* Its citation is §0,
which is about the validator. It is not a citation for what text §11.3 puts
inside the validator's scope.

What §11.3 actually says, verbatim: *"Comprehension: 2 question types only —
`tap_word` ("tap the word that says /sh/ip") and `mcq2` (2-choice story
question, **options validated decodable**)."*

- **PROVED — the spec attaches validation to OPTIONS, and to nothing else.** And
  it is not silent by oversight: §10 separately and explicitly says *"Choice
  labels must themselves pass validation (they're rendered text)."* So the doc
  states this requirement in both places it wants it — mcq2 options, §10 choice
  labels — and states it for **no prompt of either type**.
- **PROVED — §11.3's own example instruction fails the gate at every stage
  1-12.** `"Tap the word that says ship."` → `word` and `says` are undecodable at
  all twelve stages (plus `that`, `the`, `ship` below stage 3). A requirement its
  own normative example cannot satisfy is not the requirement.
- **PROVED — the constraint has no beneficiary that can be named.**
  `api/readings.py` builds §8 evidence from `answer["correct"]` alone
  (`word = single_word(key.get("correct"))`). Not one grapheme of any prompt has
  ever been counted toward `gpc_stats` or promotion. The prompt's decodability is
  rewarded by nothing and measured by nothing.
- **PROVED — what it cost, on the live pool.** Of 125 `tap_word` rows: 14 (11%)
  say `Tap it: X.` and 24 (19%) say `Tap it. Hint: X.` — 38 rows whose
  instruction never names what to compare, on a screen showing three options, so
  "it" identifies nothing. A further 33 (26%) say `Tap it if it begins like X.`,
  a conditional whose subject is equally unidentified. **71 of 125 (57%) of live
  phonics instructions do not tell the child what the task is.** That is the
  user's report, reproduced from the database.
- The instruction is **spoken aloud on mount** (`Comprehension.tsx`
  `return speak(prompt)`, DEVIATIONS 6.27) and rendered through `StoryText`, so
  every word is individually tappable → per-word TTS. A child who cannot decode
  it hears it, word by word, and an adult is present in the intended setting.

The argument, stated once: **an instruction is not reading practice; it is the
scaffolding that tells the child what the practice IS.** Gating it trades the
child's comprehension of the task against their decoding of the task
description, and the decoding earns them nothing. §8 credits the option they
tap.

**Bounded, and the gate still runs.** `INSTRUCTION_WORDS` is a committed, closed,
human-reviewed set, passed to `readable()` through the SAME parameter
`sight_words` already uses — the mechanism §4 has for irregular high-frequency
function words, applied to a fixed instruction instead of to story text. So:

- the CUE WORD is still proved decodable at the child's stage, because the cue is
  not in the lexicon. **PROVED**: with the exemption, `cue="beautiful"` and
  `cue="thought"` still yield no prompt at stage 1, and 60/60 real per-stage cues
  pass at all 12 stages at decodability 1.000, oov 0;
- a frame containing a typo or an invented word still returns `None`, so
  `sound_targets`' answerability promise and `check_tap_word`'s refusal path are
  unchanged;
- the exemption is auditable in one place and cannot grow silently — a new test
  pins its contents and asserts every member is a function/instruction word, not
  content vocabulary.

**11.61 — what 11.60 does NOT relax.** Each of these was checked, not assumed.

- **`mcq2`'s prompt keeps the gate, unchanged.** The distinction is not
  convenience, it is what the text is FOR: an mcq2 prompt is the comprehension
  content — the child must read and understand it to choose an answer, so
  decodability there is the task itself, not scaffolding. A `tap_word` frame
  carries no story information at all; it is the same 1 of 2 human-authored
  strings for every story forever.
- **`tap_word` options keep the gate, unchanged.** They are what the child reads
  and what §8 credits.
- **`target_grapheme` still never appears in the prompt.** The cue-word design is
  kept for 7.14/4.9's reason and not for decodability: exactly one option starts
  with the grapheme, so printing it in the instruction is the same answer-key
  leak as shipping it in `payload` was, and it would now be *more* exposed, not
  less.
- **§13 is untouched.** Layer 1 reads every question string in
  `_moderate_questions`. `PROMPT_FRAMES` is human-authored, so
  `LAYER3_EXEMPT_QTYPES` is unaffected — no new model-authored prose reaches a
  child, and no path skips moderation.
- **The 125 existing rows are not rewritten by this change.** They keep their old
  prompts until a `warm` backfill re-cues them. Recorded as a known
  inconsistency rather than a silent one.

**11.62 — the ladder collapses to one instruction plus the floor, because the
gate is what made it a ladder.** Under 11.60 a frame's readability no longer
depends on the stage, so six per-stage rungs are dead code by the same standard
that condemned the previous version ("a frame no stage can read is dead code,
which is exactly how this shipped last time"). `PROMPT_FRAMES` becomes
`("Tap the word that starts with the same sound as {cue}.", "Tap: {cue}.")` —
§11.3's own shape, with the cue word where the spec writes the grapheme. The
floor is retained for the cue that breaks the rung above it, which is the slot
the existing reachability test already documents as legitimately unreachable.

**11.63 — two existing assertions in `test_questions.py` encode the retired
contract and are replaced, narrowly, with the propositions that survive.**
Neither would fail if this change were reverted — they are the "contract
deliberately replaced" case (cf. 8.59), and the decision is the user's: *"does
§11.3 actually require the instruction to be decodable by the CHILD, or was that
my inference? ... If you conclude the constraint should be relaxed, that is a
DEVIATION with a real argument."* The condition was tested and is PROVED above.

- **`test_early_stages_fall_back_to_the_terse_frame`** asserted
  `tap_word_prompt("sat", 1, stage_one) == "Tap it: sat."` **and** that every
  richer rung FAILS the gate at stage 1. That second half is the retired
  constraint stated as an assertion. Both halves are replaced by the two things
  that are now true and are worth more: the stage-1 instruction NAMES the
  comparison, and the cue is still gated at stage 1 (an undecodable cue still
  yields `None`). The new form fails against the old behaviour — `Tap it: sat.`
  names no comparison — so the contract change is visible rather than implied.
- **`test_every_frame_in_the_ladder_is_reachable_at_some_stage`** asserted
  `len(reached) >= 4`. That was a PROXY for its own docstring's requirement ("a
  frame no stage can read is dead code") and it over-approximated: it
  additionally required the instruction to VARY by stage, which is only true
  while the gate makes readability stage-dependent. The requirement's real
  assertion — `unreachable in ([], [PROMPT_FRAMES[-1]])` — is **kept unchanged**
  and still catches what it caught. `len(reached) >= 4` is replaced by
  `len(reached) == 1` plus a content assertion: the instruction is now the same
  at every stage and must name the comparison. Strictly stronger about the thing
  that matters, and it forbids a silent regression to a degrading ladder.

**11.64 — `mcq2`'s real defect is not prose quality, it is that 58% of live
questions are answerable without reading the story. A pure function refuses
them; the prompt is not asked to be trusted.**

Read the rows, not the prompt. Live examples, verbatim:

| stage | question | true | false |
|---|---|---|---|
| 3 | *Was the dragon glad?* | A man was glad. | The dragon was glad. |
| 3 | *Did the rabbit run into the burrow?* | It ran fast into the burrow. | It ran fast past the burrow. |
| 6 | *Did Sam feel happy?* | Sam felt happy. | Sam felt sad. |
| 6 | *Was the moon white and high?* | The moon was white and high. | The moon was white and not high. |
| 10 | *What did he take from the moon?* | He took a rock from the moon. | He took a star from the moon. |

The first four are the defect and the fifth is the fix. In rows 2-4 the question
itself contains the word that distinguishes the two answers (`into`, `happy`,
`high`), so a child who matches the question's letters against the two cards is
right without reading the story — the comprehension analogue of the cue leak
7.14 closed for `tap_word`, and the same medicine applies: a property proved by
a pure function, not a rule stated in a prompt. Row 1 is the same defect with
the opposite sign and is worse: the question names the dragon and the true
answer is about a man, so letter-matching points at the WRONG card. Row 5 puts
the shared premise in the question (`took`, `from the moon`) and the difference
only on the cards, which is what a two-choice question is supposed to be.

**PROVED, over all 74 live `mcq2` rows:**

| | rows | share |
|---|---|---|
| question contains a discriminating word | 40 | 54% |
| false answer built by adding/removing `not` | 3 | 4% |
| **either** | **43** | **58%** |

and the gradient is the diagnosis: stages 1-9 are 32 of 58 defective; **stage 10
is 0 of 7.** Stage 10 is clean because `readable_openers` first offers it enough
wh-words that the model stopped writing yes/no questions. A yes/no prompt has to
assert a proposition, and one of the two cards then agrees with it — the leak is
near-structural for `Did`/`Was`, which are the only openers stages 1-2 have.

Two new checks in `check_mcq2`, both pure, both refusing rather than steering:

- **`prompt_leaks_answer`** — reject when the question contains a content word
  that appears in exactly ONE of the two answers. Minimal and precise: a word in
  BOTH answers is the shared premise and is exactly what a good question should
  restate (row 5 keeps `moon`), and sight words are excluded for the reason
  `check_mcq2` already excludes them from its grounding test — they are shared by
  every English sentence and prove nothing.
- **`negation_pair`** — reject when stripping `not`/`no`/`never` makes the two
  answers identical. `QUESTION_RULES` has told the model not to do this since
  8.55 and **3 live rows do it anyway**, because the rule had no judge: it was
  enforced only as a side effect of `story_supports`, which catches a negated
  distractor only when the negation happens to break the subsequence. A guarantee
  that lives only in the prompt is a suggestion (the module's own words).

**11.65 — the `mcq2` half of `QUESTION_RULES` is rewritten to teach the shape
stage 10 found by accident, and the existing correctness machinery is not
weakened anywhere.** The new instruction is the positive form of 11.64's check —
put what the two answers SHARE in the question, keep what makes them different
on the cards only, and prefer a wh-opener when one is readable — with the reason
attached, which is how §6's rewrite node and the 8.55 pass both got traction.
Unchanged and load-bearing: `story_supports` in both directions (a distractor
that is also true in the story is unanswerable and is correctly refused), the
shared-word grounding test, the length-ratio rule, and the ban on `Yes`/`No`
answers. Per invariant 5 this is measured in BOTH directions over multiple runs —
the drop rate must not rise to buy the leak rate down, because a question the
child never sees is not an improvement. Numbers in 11.68.

**11.66 — `mcq2` stays OUT of `MASTERY_QTYPES`. Refused deliberately, with the
reason recorded, because this is the project's most serious failure class.**
A two-option comprehension question is 50% guessable. Letting it move
`gpc_stats` would put an unearned signal behind a child's stage advance, which is
DEVIATIONS 6.7 exactly. §8's own docstring already says an 80% threshold would be
trivially reachable without decoding anything, and crediting every grapheme in a
sentence-length option would swamp the honest signal with dozens of free correct
marks per reading. Not changed, not widened, not made configurable. A third
question type was also rejected: §11.3 fixes the inventory at exactly two.

**11.67 — but the asymmetry hides a real defect, and it is arithmetic: §8 says
five stories and the pool needs nine.**

`MIN_GPC_OBSERVATIONS = 10` is a sound addition (5.5 — a ratio over two
observations is a coin flip, not an accuracy). What was never checked is whether
it is reachable in the five stories §8 asks for. **PROVED**, over the 120
servable stories in the live pool, counting only the GPCs `evaluate_promotion`
actually aggregates (those INTRODUCED at the child's stage, which is the
promotion denominator):

| | obs / story | stories to reach 10 |
|---|---|---|
| one `tap_word` (today) | **1.08** | **9.2** |
| two `tap_word` | **1.95** | **5.1** |

One phonics question per story credits ~1 current-stage GPC instance, so
`MIN_GPC_OBSERVATIONS` silently **doubles §8's own story requirement** — a child
who has read five stories at 100% accuracy is not promotable and the dashboard
cannot say why in §8's terms. A second `tap_word` lands the evidence base on
5.1 stories, i.e. it makes the floor CONSISTENT with §8's five rather than
raising or lowering either number.

So: **up to two `tap_word` questions per story, on different sounds, the second
composed by pure function.** This adds evidence without touching guessability —
still 1-of-3, still §8's named type, still judged by the UNCHANGED
`check_tap_word` — and it costs zero tokens and zero new prompt surface, because
`sound_targets` already returns up to `MAX_SOUND_TARGETS` ranked, provably
answerable targets and today discards all but one. `tap_word_options` composes
the second from the closed list exactly as 8.54's fallback composes the first.
Guards: the second question must use a DIFFERENT grapheme and a different
credited word, or a single word's GPCs would be counted twice; `readings.py`
needs no change because its evidence loop already iterates every answer whose
qtype is in `MASTERY_QTYPES`; and §11.3 is not violated — it fixes the number of
question TYPES at two, not the number of questions.

Honest limits, stated rather than discovered later. **PROVED** per stage: a
second answerable sound exists in 95 of 120 stories (79%), and the stories it
cannot help are the ones where the story contains no current-stage sound at all —
stage 11 goes 0.22 → 0.33 obs/story and **stage 12 is 0.00 → 0.00**, because 9 of
9 stage-12 stories credit no stage-12 GPC. Stage 12 is the top of the ladder so
promotion is moot there, but **stage 11 remains effectively unpromotable** and
this change does not fix it. That is `older_stage_fallback` firing, which the
module docstring already names as a finding about the ALLOWLIST rather than about
this node, and generation/precompute are another agent's. Recorded as open.

**11.68 — the measurement, both directions, per invariant 5 — including the
regression it found and the two things that fixed it.**

Four live runs against the real model, stages 2/3/6/10, 4 stories each, BEFORE
being the live `questions` rows the same model wrote under the old prompt. Total
spend **$0.494**.

**The improvement is unambiguous and held in every run: leaky `mcq2` 16 → 0**
(run 1, 32 stories) and **7 → 0** (run 4, 16 stories). Not one question in any
run contained a word that distinguishes its two answers. Verbatim, stage 2:

    BEFORE  "Was the dragon glad?"  ["The dragon was glad.", "A man was glad."]
            correct = "A man was glad."          <- names the FALSE card
    AFTER   (dropped; two phonics questions instead)

    BEFORE  "Did the dragon go into the cave?"   <- names "into" and "cave"
    AFTER   (dropped)

and the phonics instruction, which is the user's actual complaint, at the lowest
stages:

    BEFORE  "Tap it. Hint: win."        AFTER  "Tap the word that starts with
    BEFORE  "Tap it if it begins as for."      the same sound as win."

At stages 6 and 10 the model now writes the shape stage 10 used to reach by luck:

    AFTER   "When did the rabbit have to turn back?"
            ["Then it started to feel happy.", "Then it started to get dark."]
    AFTER   "Where did the rabbit come across green food?"
            ["It found some green food in the forest.", "... by a rock."]

**The regression, reported as one.** Run 1: `mcq2` present 28 → 15 of 32 stories.
The leak rate was bought partly with the question's existence, which 11.65 said
would be a regression, and it is. Two causes, both found by reading the
refusals rather than guessing:

1. **My own worked example was out of inventory.** The "ask about what the two
   answers SHARE" rule was taught with `"What did the pup get from the pot?"` — a
   wh-question, and `readable_openers` gives stages 1-3 only `Did` and `Can`. So
   at exactly the stages where the leak was worst the model had no in-inventory
   model of a good question. This is the same mistake the module docstring records
   for the old prompt ladder ("written from English rather than from the
   inventory"), made again in a new place. Fixed with a yes/no example and a
   request-block paragraph that fires only when no wh-opener is readable.
2. **The retry re-rolled eight constraints to fix one.** An `mcq2` must satisfy
   eight simultaneous constraints, and in the diagnostic run **6 of 10 failures
   had a perfectly sound answer pair and only a leaking question**. `check_mcq2`
   is now split so `check_mcq2_answers` judges the pair alone, and a retry whose
   pair passed carries it back verbatim with "only the question was wrong". Same
   judge, same acceptance criteria — this changes what the model is ASKED for.

That recovered stages 6 and 10 to **7 of 8** stories holding an `mcq2`. It did
not recover the two lowest stages, which are **1 of 8**, and the cause is
structural rather than a wording problem: with only `Did`/`Can` readable, a
yes/no question has to assert a proposition, and one of the two cards then agrees
with it. Stated as an open item rather than smoothed over — the honest trade at
the lowest stages today is that the child loses the comprehension question and
gains a second phonics question, and a leaky comprehension question was one the
child could answer without reading, so it was not worth much. Two candidate
fixes, both outside this change: a fixed never-leaking `mcq2` frame (the 11.60
argument applied to a prompt that carries no story content), or making `what` /
`where` sight words below stage 4, which is an allowlist decision.

**Evidence volume, measured on the pre-migration tree** (the numbers in 11.67:
1.08 → 1.95 observations per story over 120 servable stories, 9.2 → 5.1 stories to
reach the floor). Run 4 measured 0.69 → 1.00 and 14.5 → 10.0 stories, but that run
straddled the stage-1 retirement — `gpcs.csv` was renumbered and `make seed` /
`make precompute` had not been re-run, so stage membership and story-stage
bindings were inconsistent. **The 11.67 numbers are the sound ones and the whole
of 11.67 needs re-measuring once the 11-stage migration lands.** `tap_word` rows
rose 32 → 55 (run 1) and 16 → 26 (run 4), consistently ~60-70%, in both trees.

**Not done, and left for the operator on purpose:** the 125 existing `tap_word`
rows still carry their pre-11.60 prompts. `warm.refresh_tap_word_prompts` fixes
them in place with **no API call** and is idempotent — `test_enrich.py::
test_backfill_re_derives_a_stale_tap_word_prompt_in_place` proves exactly that,
and it is what caught the ladder change. It was not run here because the database
was mid-migration.

### Gates — the narrative pass (11.40-11.59)

- `cd services/api && .venv/bin/python -m pytest -p no:randomly` — **994 passed,
  3 skipped, 0 failed, exit 0.** Against the stated baseline of 855 + 2, **26 of the
  delta are mine** (`tests/test_narrative.py` 15, one of which is the opt-in live
  fixture and is the third skip; `tests/test_llm.py` +11) and the rest belongs to
  the two agents working in `apps/web/**`, `precompute.py` and the vocabulary
  filters.
  **Measured in an isolated copy of the tree at `/tmp/sf-noNarr` against a private
  `pg_dump` database (`sfnarr3`), per `docs/STATE.md`, and the isolation was
  necessary rather than cautious.** A run in the shared tree reported 4 failures in
  `tests/test_questions.py` and `tests/test_enrich.py`; seconds later one of those
  test functions did not exist, because another agent was mid-edit in that file.
  Those failures were then shown NOT to be mine by a controlled experiment in ONE
  snapshot: the same two modules pass with my changes reverted (98 tests) and pass
  with my changes restored (98 tests), so the variable was their editing and not my
  code. Sabotage-checked in the same spirit as 8.47's recipe.
  One failure in the isolated tree is an artefact of the isolation itself and is the
  same one 9.x records: `test_eval.py::test_a_fresh_run_records_everything_a_later_
  reader_needs` asserts `provenance.git_dirty in (True, False)` and a `tar` copy is
  not a checkout. `git init` in the copy makes it pass, which is the proof it is
  environmental; the 994 above is the post-`git init` run.
- `.venv/bin/ruff check storyforge tests scripts` — All checks passed.
- `make eval-smoke` — **PASSED, 4/4, mean decodability 99.4%, p95 rewrite depth 0,
  narrative mean 5.00 of 5.** Cost **$0.1791**, plus **$0.0059** of narrative
  judging, = $0.1850. Taken AFTER the allowlist change described in 11.59, so it is
  not comparable to the $0.1148 in 9.x.
- STATE invariant 10: `SYSTEM_RULES` is edited in exactly ONE clause (11.42) and
  gains no digit, no theme, no stage name and no brace, so
  `test_system_rules_are_frozen_of_request_detail` and
  `test_the_length_rule_carries_no_per_request_detail` are unchanged and still
  assert what they always asserted. Everything stage-scaled is in the cached
  per-stage block; there is still exactly ONE `cache_control` breakpoint and a test
  asserts it sits on the last block.
- No `make warm`, no full `make eval`, nothing touched on DigitalOcean.

**Live spend: $2.84**, itemised — judge calibration probes $0.154 (three rounds
including the reverted clause of 11.54), the `before` arm $0.9051, the `after` arm
$1.2867, judging both arms with one ruler at 3 runs a story $0.2746, two live
fixture runs $0.037, `make eval-smoke` $0.1850.

**What is NOT proved.** The narrative gain is measured on 6 stories per stage at 5
of 12 stages; stages 3-5, 7-8 and 10-11 were never generated in either arm and
inherit their band's wording from the stages that were. The rewrite-depth
IMPROVEMENT is a distribution that did not worsen at n=30, not a proved effect. The
judge's own accuracy is bounded by 11.54's known residual. And no story from either
arm was persisted, so nothing here changed the pool a child reads from.

## Phase 8 — the question screen: "they begin out of nowhere and then aren't really written well"

Same surface, reported in the same live test as the two defects above. Both
halves of the user's sentence are true and they have different causes.

### 11.31 — there is now a beat between the last page and the first question

`next()` on the final segment set `phase = 'questions'` and the reading view was
replaced, in one frame, by a screen asking a child to do something. Nothing said
the story was over and nothing said what was coming — and for a five-year-old who
cannot yet read the new screen's text, that is the entire information available.
"Out of nowhere" is a literal description of the state machine.

`routes/StoryEnd.tsx` is a phase, not a slice of `Comprehension`, because it is a
transition *between* phases and the reading view has to be gone before it paints.
It does three things:

- **closes the story** — a display headline and a hand-lettered line, in the
  celebration screen's voice. Praise for reading, not a score: §08's "no derived
  reading level, no streak, no badge" applies here too.
- **shows what is coming, as pictures** — one card per question, carrying that
  §11.3 task's own icon and tint. A child cannot read "two questions" but can see
  two cards and can see that they are not the same shape as each other. The same
  two tints and glyphs are then reused on the question screen itself, so the
  preview and the thing it previews cannot drift.
- **speaks it** — "You read the whole story. Now there are two things to try: a
  listening game, and a question about the story." Adapts to a one-question
  story, and uses the child's name when the session has it.

The child advances it with a 64px kid-primary button. An auto-advance would put
the beat back on a timer the child does not control, which is the same problem in
slower motion.

### 11.32 — the two question types are drawn as two different KINDS of thing

They used to differ only in how many columns the options grid had. That hid two
facts at once.

The first is for the child: a listening game and a question about a story are not
the same task, and being told which one you are in is most of knowing what to do.
There is now a task band at the top left — an ear on gold for `tap_word`, an open
book on sky for `mcq2` — plus question progress dots, plus a layout that differs
structurally rather than by column count.

The second is for the parent, and it is 11.34.

`BAND` is a literal `Record`, per STATE invariant 7. `bg-${kind}-300` would be
purged from the production build and both tasks would render on plain paper,
silently deleting the entire distinction this screen now depends on — the same
failure mode that collapsed the four verdict colours. Verified by computed style
in `dist/`, not by reading the source: see PROVED.

Colour is not the only difference either (§11): the two glyphs have different
silhouettes, the two layouts are different, and the band names the task in words.

### 11.33 — `tap_word` leads with the CUE, and the spoken instruction is not the written one

The prompt is rendered to the child, so it must itself be decodable at the
child's stage. Below stage 4 every word for similarity — `as`, `like`, `same`,
`alike` — is stage 4 or later, so the generator provably cannot write the
instruction and emits **`"Tap it. Hint: that."`** (llm/questions.py, DEVIATIONS
6.27). That is a floor, not sloppiness, and the words are not negotiable.

**What is not bound by the decoder: speech, icons and layout.** So:

- **The cue word leads**, on a gold card with a speaker glyph, tappable. It is a
  word from the story the child has already read. `payload.cue_word` survives
  `_public_payload`'s denylist; `target_grapheme` does not and must not — the
  correct option is the one that starts with it, so rendering it would print the
  answer key the DTO exists to withhold.
- **Three chevrons fan down** from the cue to the three options. The task is
  "find the one that starts like this", and that is a shape, not a sentence.
- **The spoken instruction is rebuilt**: `"Which word starts like that? Tap it."`
  The old code spoke `question.prompt`, i.e. it read the decoder's floor aloud —
  in the one channel where the floor does not apply.
- **The generated prompt is demoted by POSITION**, below the options. Shrinking
  it was the obvious move and is not available: §03 puts an absolute 28px floor
  under story text and nothing in `StoryText` may go below it, and tinting it to
  ink-500 lands at ~6.5:1 on paper where §11 requires ≥7:1 for story text.
  Reading order is the one axis with no rule on it. It is still Andika, still
  full size, still tappable word by word.

`mcq2` is left leading with its prompt, because an `mcq2` prompt is a real
sentence — "Did the dragon go into the sun?" — and needs no scaffolding. Its
prompt and options now chip their story words (`QuestionDTO.story_words` was
already on the wire and unused here), so a stage-1 reader is not handed "rocket"
bare on the question screen after having it given to them in the story.

`lib/questions.ts` owns `taskKind`, `cueWord` and `spokenInstruction` as pure
functions with 15 tests, including one asserting the client never reads
`target_grapheme` and one asserting an unknown `qtype` falls to the STORY task —
failing towards "no phonics claim", because a new type drawn as the sound task
would assert a mastery meaning it has not earned.

### 11.34 — the parent dashboard now says which question feeds the letter-sounds table

`mastery.MASTERY_QTYPES` is `("tap_word",)`. Only the sound question produces
`gpc_stats` evidence and only it moves promotion; the `mcq2` comprehension
question feeds nothing. That is the right call — a two-option question is 50%
guessable and must not move a mastery counter — and it was **invisible**. Recent
readings prints "3/3 correct" counting both, directly under a GPC table fed by
one, so a parent reading the two panels together had no way to know they were
counting different things.

One paragraph in `GpcTable`, in the same plain-language register as §08's
existing caveat, naming the sound question as the only input to that table and
saying why the story question is not one. **Nothing is said to the child.** §01
and §07 keep kid mode free of anything that reads like grading, and "this
question is the one that counts" is grading — which is why 11.32's distinction is
drawn as two kinds of task and not as two weights.

**Deferred, and named**: a per-qtype split of "3/3 correct" needs
`RecentReading` to carry the breakdown, i.e. two more `count(...) FILTER` columns
in `readings.py:379`. Not done here — the sentence closes the honesty gap and the
API change belongs with whoever next opens that file.

### 11.35 — the process finding: a shared worktree has no commit boundary, and this time it fired

DEVIATIONS 9.x ends with "a shared tree with two agents in it has no commit
boundary, and the F9 revert could as easily have been committed in its broken
state. Worth knowing before the next two-agent phase."

Mid-pass, a concurrent agent ran `git stash -u` over the whole worktree and reset
to HEAD. **Every file of this work vanished at once** — 21 modified files, 48
generated SVGs, four new modules, ~250 lines of this document — along with all of
theirs. It was recoverable: `git stash list` held one entry whose `^3` commit
carried the untracked files, and they popped it themselves a minute later, so
nothing was lost. But the recovery was luck, not design: a `stash drop`, a second
stash, or a `checkout -- .` at the same moment would have been unrecoverable, and
neither agent would have known which files were whose.

Recorded because the mitigation is cheap and was not taken: **two agents editing
one worktree should be two worktrees.** `git worktree add` costs one command and
makes both agents' `git stash`, `git reset` and `git commit` local to their own
work. Nothing in this pass needed the shared tree.

### 11.36 — what was measured, and what was not

Measured live, in a real browser, at tablet viewports, against a FastAPI on port
8123 with `DEMO_BYPASS_AUTH=false` (enforcement confirmed FIRST: `/api/health`
reported `demo_bypass_auth: false`, and through both the dev proxy and the
`vite preview` proxy a `POST /api/validate` with no token returned 401 and with
`Bearer garbage.token.here` returned 401), with a real Clerk session minted
through the Backend API. See PROVED.

NOT measured, and not claimed:

- **Audio was never heard.** `speechSynthesis` was hooked and the utterance text
  and rate were read back — `"Which word starts like that? Tap it."` at rate
  0.85, and `"Noah, you read the whole story. Now there is one thing to try."` —
  but a headless browser produces no sound. What is proved is *which string is
  spoken*, which is the thing that changed.
- **No child has used this.** Every claim about what a five-year-old reads as a
  mistake is an argument from the style guide and from the user's report, not a
  measurement.
- **The 4 unreached tags.** `castle`, `snow`, `fish` and `swimming` have never
  been emitted by the tagging node over 839 tagged segments. Assets carrying them
  are drawn but no live segment selects them, so their art is unverified in situ.
- **`make warm` was not run and no money was spent.** The pool is unchanged; only
  `story_segments.asset_id`'s targets now exist.

## Phase 8 — the ladder renumber: stage 1 retired, eleven stages (12.1-12.16)

Logged BEFORE the changes. Authorised by the user (docs/DECISION-stage-ladder.md,
2026-07-30): *"let's go and reposition everything so that the current stage two is
the new stage one and we have 11 total stages."* The KILL CONDITION was checked
first and does not apply — the measured per-stage counts under the corrected
vocabulary filter are 78 / 771 / 1,383 / 1,934 / 2,149 / 3,197 / 3,461 / 3,899 /
4,333 / 4,378 / 4,386 / 4,397, so stage 1 is the ONLY stage below §18's 150 floor
and every other stage clears it by 5x or more. Everything marked **PROVED** was
measured in this repo with the command shown; everything else is reasoning and is
labelled as such.

### 12.1 — the decision now has TWO independent grounds, not one

The trigger was vocabulary (11.11): stage 1's nine GPCs can spell 78
child-vocabulary words, a measured CEILING, and its old 157 cleared §18's floor
only because 27 rows were proper nouns and 61 were adult register.

A second, independent ground arrived from the narrative-quality work (11.57 and
the coordinator's cross-agent report): **at old stage 1, negation is impossible.**
`not`, `no`, `cannot` and `never` are all violations against `mastered_ids(1)`
because no stage-1 word contains the letter `o` — so a stage-1 story cannot state
an obstacle, only a want that is immediately met. Asked for "something wrong", the
model's only decodable approximation was `cant` (c-a-n-t, 100% decodable, then
correctly rejected by §13's grammar half), which cost 2 of 6 stage-1 stories.
Narrative quality there measured 2.0/5 before prompt work and 2.5/5 after — a
structural ceiling, not a prompt defect.

So this is not "the filter shrank a stage and made it inconvenient". Old stage 1
was never a viable rung, and its word count concealed that. New stage 1 (old
stage 2) has `o`: it can say `not`, and it holds 771 child words. The accepted
cost, explicitly the user's to accept and accepted: a true beginner who knows only
`s a t p i n` no longer has a rung of their own.

### 12.2 — the mapping, and why it preserves every guarantee

Old stage N -> new stage N-1 for N >= 2; old stages 1 and 2 merge into new
stage 1. Stage inventories are CUMULATIVE, so the cumulative GPC set, sight-word
set and allowed-word set at new stage K are IDENTICAL to old stage K+1's for
every K in 1..11 — new stage 1 = old stage 2 exactly, and old stage 1's inventory
is a subset of it. Three consequences, each load-bearing:

- **The decodability gate does not move.** No GPC row is added, removed or
  re-spelled; only the stage LABEL at which each is taught changes. What counts
  as decodable at the new top stage is byte-identical to the old top stage.
- **A story proved decodable at old stage N is decodable at new stage N-1** —
  the same cumulative set judges it. Old stage-1 stories map to new stage 1,
  whose set is a superset; the safe direction (STATE invariant 6).
- **The §5.7 goldens at old stage s >= 2 carry over to s-1 with identical
  verdicts, alignments and missing sets** — same cumulative set, same aligner.
  This is the REASONING; 12.5 records the per-case re-derivation, which is
  measured, not assumed.

### 12.3 — the seed data: four CSVs move together

- `data/seed/stages.csv`: 12 rows -> 11. New stage 1 is the merged row (name
  "Letters & doubles"): all single letters, the doubled consonants and `x`.
  Length bands and syllable caps shift with their stages: 30-60 words at new
  1-3, 60-120 at 4-7, 120-200 at 8-11; max_syllables 2 at new 1-7, 3 at 8-10,
  uncapped (9) at new 11.
- `data/seed/gpcs.csv` (the decoder package copy; `data/seed` holds a symlink):
  every `stage` value >= 2 decrements; stage-1 rows stay. gpcs.csv is NOT in
  PINS.md, so no pin moves; the INVENTORY (graphemes, phonemes, kinds,
  positions, schwa flags) is untouched.
- `data/seed/sight_words.csv`: same decrement; old stage 1 (`the a i`) and old
  stage 2 (`is to no go of so do`) merge at new stage 1. The sets are disjoint,
  so the merge loses nothing.
- `data/seed/story_words.csv`: `min_stage` 3 -> 2 and 5 -> 4 (1 stays).

### 12.4 — MAX_STAGE gets ONE canonical home, in the decoder

`MAX_STAGE = 12` lived in TWO places (`mastery.py:94`, `precompute.py:69`) and
was hardcoded as `12`/`le=12`/`"1..12"` in eight more. A renumber that edits
every copy leaves the next change the same trap. So `MIN_STAGE`/`MAX_STAGE`
move to `storyforge/decoder/__init__.py` — the package that owns stage
semantics (`mastered_ids`) — and `precompute.py`, `warm.py`, `llm/generate.py`
and the tests import from there. A test pins the constant to the inventory
(`MAX_STAGE == max(g.stage) + 1`: the top stage is review and introduces no
GPCs) and to `stages.csv`.

**`mastery.py` is another agent's surface and is NOT touched.** The renumber
requires exactly one line there — `MAX_STAGE = 12` -> `MAX_STAGE = 11` — and it
is described in the handoff report instead of applied.
`tests/test_mastery.py`'s ladder pin is updated to assert
`mastery.MAX_STAGE == decoder.MAX_STAGE` (the two homes must agree), so it is
RED until that one-line change is applied and green forever after; that is the
one deliberate interim failure of this change, and it is diagnostic of exactly
the un-applied line.

### 12.5 — the §5.7 goldens are RE-DERIVED, not decremented

Appendix B: the tests are the acceptance criteria; the CSV is an implementation
detail. Every case in `tests/decoder/golden.yaml` was re-derived from the NEW
inventory by running the validator (`justify-test-edit` verdict recorded per
case in the YAML comments; the licence in every case is the same — the contract
each case pinned names a stage LABEL, and the labels were renumbered by a user
decision the tests cannot see):

- `cat@1` decodable `[c|K, a|AE, t|T]` — unchanged: all three GPCs are in new
  stage 1 (superset of old stage 1).
- `ship@2->@1` violation missing `[sh|SH]`; `ship@3->@2` decodable — `sh` is
  taught at new stage 2 (old 3).
- `the@1` sight — unchanged (old stage-1 sight words merge into new stage 1).
- `duck@2->@1` decodable — `u`, `ck` are new stage 1.
- `cake@4->@3` violation missing `[a_e|EY]`; `cake@5->@4` decodable.
- `fox@2->@1` decodable `[f|F, o|AA, x|K.S]`.
- `night@5->@4` violation missing `[igh|AY]`; `night@6->@5` decodable.
- `singer@6->@5`, `finger@6->@5` — `er|ER` is new stage 5; the `ng|NG` vs
  `n|NG` distinction is stage-independent.
- `read@6->@5` heteronym (`ea|IY` at new stage 5).
- `city@8->@7` violation missing EXACTLY `{c|S}` — the case pins `y|IY` to what
  is now stage 5, so at new stage 7 the only missing GPC is soft c;
  `city@9->@8` decodable.
- `dragon@4->@3` decodable with `schwa_relaxed` (the spec's own stage label,
  decremented; the schwa rule is stage-independent).
- `zorp@12->@11` oov; `beautiful@12->@11` violation, empty missing set,
  `alignable=false` — the top stage moved.
- `demo-sentence-stage-3` -> stage 2, all verdicts, missing sets and the 0.0
  decodability UNCHANGED — new stage 2's cumulative set is identical to old
  stage 3's. §17's demo is now a stage-2 demo.

Every one of these was verified by executing the validator against the edited
inventory before the YAML was committed; the golden suite passing is the
recorded proof.

### 12.6 — the anti-goldens and the vacuity ratchet

- `NEVER_DECODABLE` words are asserted undecodable at every stage `1..MAX_STAGE`
  (the loop was `range(1, 13)`); the list itself is untouched.
- `MUST_BE_DECODABLE` stage expectations decrement (each also asserts NOT
  decodable one stage earlier, so these are boundary re-derivations, PROVED by
  the suite): happy/funny/sunny/bunny/puppy 6->5, water 7->6, house/mouse 8->7,
  little 10->9, rabbit/kitten/carrot/egg 2->1, have 5->4.
- **The ratchet's SEED, SAMPLE SIZE, FLOOR and CEILING are untouched.** It is
  measured at the top stage; the new top stage (11) has the identical cumulative
  set the old top stage (12) had — the full 93-GPC inventory — so the measured
  value is EXPECTED not to move from 44.75%. Re-measured after the change rather
  than assumed; the number is recorded in the Gates section below. Had it moved,
  that would be evidence of an inventory change smuggled into the renumber, and
  the right response would be to stop, not to adjust the ceiling.

### 12.7 — `test_no_stage_is_starved` fires its own tripwire, as designed

11.11 added `assert counts.get(1, 0) < 100` precisely so that retiring stage 1
would turn the authorised exception into a loud failure instead of a permanent
loophole. That tripwire now fires: the exception block is deleted and the plain
floor assertion is restored for EVERY stage — new stage 1 starts at 771 words,
so no stage needs an exemption, and the floor is raised from "at least 100" back
over §18's 150 for good measure via the precompute report check. The
`justify-test-edit` licence is the test's own docstring: it named the exception
authorised, temporary, and to be deleted on the renumber.

### 12.8 — the live-data migration renumbers; it never rebuilds

New Alembic revision (down_revision `c4f1a2d7be03`). Production holds ~44
stories and a handful of kids; a child at old stage 5 must land at new stage 4
and a child at old stage 1 must land at new stage 1 — the entry rung; their
taught inventory is a subset of new stage 1's, and there is no lower rung to
send them to. In one transaction, guarded to run only when `max(stages.id) = 12`
(fresh and already-renumbered databases are no-ops, which is what makes it
idempotent):

- `kids`, `stories`, `readings`: `stage_id = GREATEST(stage_id - 1, 1)`.
  `readings.stage_id` is on the DECISION doc's list only implicitly — it exists
  (§8 promotion counts readings at the kid's then-current stage) and missing it
  would silently shift every promotion denominator; called out here because the
  blast-radius list named kids/stories/allowed_words/gpc_stats/eval_run_items
  and NOT readings.
- `allowed_words`: old stage-1 rows are DELETED (their words all recur at old
  stage 2 — cumulative), the rest decrement via an INSERT-from-temp so the
  `(stage_id, word)` unique constraint never sees a transient collision. The
  result is byte-identical to what `make precompute` regenerates (new stage N =
  old stage N+1 exactly), so production is correct even BEFORE anyone re-runs
  precompute — the deploy's PRE_DEPLOY job runs only `alembic upgrade head`.
- `gpcs.stage_id`, `sight_words.stage_id`: same GREATEST decrement (sight-word
  stage sets are disjoint, so the merge cannot collide);
  `story_word_bank.min_stage` likewise. `gpc_stats` carries NO stage column —
  the blast-radius list was wrong about that; its rows are keyed (kid, gpc) and
  follow the renumbered `gpcs` rows automatically.
- `stages`: rows 1..11 take the new contents (hardcoded in the migration — a
  migration is a historical record and must not depend on a CSV that will move
  again), row 12 is deleted last, after nothing references it.
- **Eval history is NOT renumbered, and the first draft of this entry said the
  opposite.** The plan was to renumber `eval_run_items.metrics.stage_id`,
  `eval_runs.config.stages` and `eval_runs.metrics.by_stage` in jsonb so every
  historical number stays attached to the inventory it measured. Looking at the
  real rows killed it: runs 2 and 114 hold `by_stage` slices for BOTH old
  stages 1 and 2, which collide at new stage 1, and two per-stage stat slices
  (means, rates, p95s) cannot be merged without the underlying items —
  re-aggregating inside a migration would mean reimplementing `eval.aggregate`
  in SQL, the exact single-source-of-truth violation 8.7/8.25 was about; and
  renumbering the items while leaving the aggregates makes one run's own record
  disagree with itself. So eval rows keep the labels they were measured under:
  a run is a RECORD (its `config` pins the git commit, which pins the ladder),
  `reports/*` keeps the same old labels, nothing joins these rows to `stages`
  by FK, and the admin view renders them per run. The serving state is what
  must never lie about levels, and all of it is renumbered above.
  `gpc_stats`, also on the blast-radius list, carries NO stage column at all
  (PROVED: migration `97adefe67c1b` — keyed kid x gpc); its rows follow the
  renumbered `gpcs` table automatically.
- **`stories.decodability` is recomputed for every story at the MERGED stage,
  and a test caught the need rather than foresight** (PROVED).
  `test_the_gate_still_agrees_with_what_the_harness_recorded` re-runs the pure
  gate over stored stories and went red on story 3241: recorded 0.9677 at old
  stage 1 (its one violation was `rabbit`, which needs old-stage-2 `bb`),
  recomputed 1.0 at merged new stage 1. That is the one class where recorded
  and recomputed can differ — old stage-N rows for N >= 2 map to an IDENTICAL
  cumulative inventory, so their stored numbers are provably unchanged, while
  old stage-1 rows are now judged by a superset (more GPCs, more sight words),
  which can only RAISE decodability: every word the merge makes newly sight was
  a violation at old stage 1, so the denominator only loses violations. The
  migration therefore recomputes the column for all stage-1 stories via the
  real validator (`validate_text` against the renumbered `sight_words` table),
  which is idempotent — a second run recomputes the same values. `stories` is
  serving state, not a record (the 12.8 line), so the column must agree with
  the live gate; the eval runs' jsonb keeps the old measurement, labelled by
  the run's commit.
- Downgrade is a refusal (`RuntimeError`), like `c4f1a2d7be03`: re-inserting a
  retired stage and re-raising every child's number is not an undo, it is a new
  wrong state.

Sabotage-verified on a scratch database, not assumed: fixture kids at every old
stage 1..12 land at 1,1,2,3,4,5,6,7,8,9,10,11; a second run changes nothing
(row-for-row identical dump); a fresh empty database is untouched. Recorded in
the Gates section below.

### 12.9 — the stage-1 negation clause dies with the stage that needed it

`llm/generate.py`'s `_STAGE1_NEGATION` (11.57) states an inventory fact about
OLD stage 1: no word with `o`, so `not`/`no`/`cannot`/`never` are unwritable.
New stage 1 has `o`, `not` is decodable, and `no`/`go`/`so`/`do` are its sight
words — the premise is false, and the clause would now tell the model a lie
that removes its cheapest way to state an obstacle.
`test_stage_one_is_told_it_cannot_write_a_negation_and_why` carries its own
kill switch — *"if a future inventory change makes stage 1 able to say 'not',
this test goes red and the clause should be reconsidered rather than silently
kept"* — and that is exactly what happened. Clause, constant and test are
removed together. (`justify-test-edit`: the test pre-authorised its own
retirement on this exact condition; the condition was met by an authorised
user decision.)

### 12.10 — the narrative bands shift with the ladder

`NARRATIVE_BAND_EDGES = (2, 4, 8)` encoded §10's own scaling ("0 branch points
for stages 1-2, one at 3-4, up to 2 for 5+") in OLD stage numbers. The bands'
CONTENT is unchanged — the same four asks, spec-derived (11.44) — but the edges
become `(1, 3, 7)` so each band still covers the same INVENTORY range it was
written for: band 0 is new stage 1 (old 1-2), band 1 new 2-3 (old 3-4), band 2
new 4-7 (old 5-8), band 3 new 8-11 (old 9-12).

### 12.11 — the §14 matrix is 11 x 3 x 5 = 165

`eval.py DEFAULT_STAGES` and `warm.py DEFAULT_STAGES` become `"1..11"`; the
Makefile `eval` target likewise; `api/admin.py`'s `MAX_CELLS` 180 -> 165 and
its `1..12` validator -> `1..11`; the five Pydantic stage bounds (`le=12` in
`api/validate.py`, `api/kids.py`, `api/stories.py`, `api/admin.py` x2) -> 11.
`docs/EVAL.md`'s header matrix moves to 11 x 3 x 5; the committed numbers in it
remain the last run's real measurements over the OLD ladder and are labelled
as such rather than re-mapped — a report is a record of the run it reports.
`scripts/eval_doc.py`'s generated wording follows. The measurement scripts'
default stage tuples (`scripts/measure_gen_latency.py`,
`services/api/scripts/measure_length.py`, `measure_narrative.py`) drop 12.

### 12.12 — §17's demo is dragons at stage 2 now

The demo sentence, verdicts and 0.0 decodability are unchanged (12.5); only the
stage number a presenter types moves. Web: `DEMO_STAGE_ID` 3 -> 2, the landing
page's "stage 3 · digraphs" label -> "stage 2 · digraphs" (the pairing is what
matters — Digraphs IS new stage 2), `SEEDED_STAGES` drops old stage 1 and
renumbers, `clampStage`'s bound and fallback follow, `AdminColdGenerate`'s
default follows, `ParentDashboard`'s "Stage 12 is the last stage" copy -> 11,
`AdminEvalRuns`' default `'1..12'` -> `'1..11'`. `seeds.test.ts`'s row-count pin
12 -> 11 (it asserts the CSV and `SEEDED_STAGES` agree — the contract is
unchanged, the ladder it pins moved). Stale-but-passing fixtures naming stage 12
or "stage 3 = Digraphs" are re-pointed in the same pass.

### 12.13 — the spec attachment is NOT edited

The DECISION doc's blast radius names "the build doc's Appendix A stage table".
The build doc is the frozen mandate (`.context/attachments/...`); every § and
Appendix reference in five phases of DEVIATIONS entries resolves against it,
and rewriting it would re-point them all silently. The executed mapping lives
in `docs/DECISION-stage-ladder.md` (marked EXECUTED, with the old->new table);
Appendix A's table stays as the historical input it was, exactly like §5.4's
SUBTLEX instruction stayed in place when 11.7 replaced the corpus.

### 12.14 — what is NOT changed

- **The decodability gate (§0).** No aligner code, no GPC content, no sight
  word added or removed, no syllable-cap VALUES (only their stage labels), no
  tokenizer, no schwa rule. `test_every_allowed_word_revalidates_as_decodable`
  and the unchanged anti-golden list are the proof.
- **The vocabulary filters (11.1-11.12).** Blocklist, child-vocabulary list,
  proper-noun list, `REGISTER_FILTER_FROM_STAGE = 1`, the candidate pool, the
  synthetic rank base — all untouched. The per-stage word counts after the
  renumber must therefore be exactly the measured 11.11 counts shifted by one,
  which is asserted by measurement in the Gates section.
- **Moderation, §13, all three layers.** No blocklist entry moves.
- **`mastery.py`, `llm/questions.py`, `api/readings.py`, and the three test
  files another agent owns.** The single required line in `mastery.py` is
  handed off (12.4).
- **Historical records**: `reports/*`, audit docs, prior DEVIATIONS entries,
  and Langfuse traces keep their old stage numbers — they describe runs that
  happened on the old ladder. Only the live database rows are renumbered
  (12.8), because those are not a record, they are the serving state.

### PROVED — driven in a real browser, dev AND the production bundle

Environment, established before anything was believed: FastAPI on **8123** with
`DEMO_BYPASS_AUTH=false`; `/api/health` → `"demo_bypass_auth": false`; through the
Vite dev proxy (**5199**) and the `vite preview` proxy (**4199**), `POST
/api/validate` with **no token → 401** and with **`Bearer garbage.token.here` →
401**. Real Clerk session minted through the Backend API for
`user_3HBc8LNBQdWzlwbgKrzXYdH1SDa`. Driven with
`~/.claude/skills/gstack/browse` at `BROWSE_SERVER_PORT=24811`.

Screenshots: `reports/screenshots/phase8-visual-defects/` (gitignored, so they
are on disk and not in the history). Before/after pairs at 1024×768, the
production bundle at 768×1024 portrait, and the question screen at stages 3 and 6.

**Before**, at 1024×768, stage 6, from the live warm pool — the two defects as
reported:
- every segment a beige striped rectangle filling half the screen;
- one screen carrying **five amber outlined boxes** — `cave`, `wings`, `dragon`,
  `dragon`, `dragon` — each with a meaningless 24px grey striped square inside it;
- a thin gold rule accumulating under every word tapped, permanently.

**After**, same viewport, same stage, same pool:
- **art in every frame.** `dragons/cave-01.svg` renders for the segment whose
  tags select it, from the `assets` row whose id 831 segments already pointed at.
- **two soft amber stickers with real pictures on the first screen** (`dragon`,
  `cave`), and **zero marks** on the screen that previously carried five — both
  words were introduced earlier in the reading, so §07's "then behaves like any
  tappable word" now holds.
- **17 words tapped, zero residue.** `data-state="heard"` is still set (read off
  the DOM) and `borderBottomColor` is `rgba(0, 0, 0, 0)`.
- theme picker renders the three §06 covers; the emoji has retired.

**§11 floors, by computed geometry, in the PRODUCTION bundle** (`vite preview`,
worst case forced by a 460px viewport so a chip line wraps):
- plain word target **62.0 × ≥48.0px**; chip **64.5px** tall (it was 66px before,
  so the line box grew by *less* than it used to);
- **worst vertical clearance between two overlapping stacked targets: 10.0px**,
  between `dragon` and `cave` — above §11's 8px floor, and above the 8.4px this
  was previously measured at;
- `Next` **64px**, the §11 kid-primary-action floor;
- reading text 34px / 40px on the Aa toggle, never below §03's 28px.

**STATE invariant 7, checked in `dist/` two ways.** All six `.sf-tok-*`, all four
`.verdict-*` and all three `.sf-band-*` rules survive the purge (the admin
playground and parent dashboard were not touched, and "I did not touch it" is not
evidence). The new literals survive too, and the two that matter were read off the
live DOM under `vite preview` rather than grepped: the task band computes
`background rgb(242, 212, 138)` = `#F2D48A` (gold-300) with `border-color
rgb(168, 122, 34)` = `#A87A22` (gold-700). `.sf-chip` in the built CSS is exactly
`{padding-left:6px;background:var(--sf-v-story-bg)}` — **no border-color rule at
all**, which is 11.27, and **no `[data-state='heard']` rule anywhere in the built
CSS**, which is 11.28.

**The question screen, at the stage where the text is worst and at a high one.**
Stage 3, prompt `"Tap it. Hint: that."`: the screen shows the cue `that` on a gold
card with a speaker, three chevrons pointing down, then `went / sam / then`, and
the generated sentence as a caption below. Stage 6, prompt `"Tap it if it begins
like his."`: same pattern with `his`. The `mcq2` beside it is visibly a different
screen — sky band, open book, prompt leading, two stacked options, no cue card.
`speechSynthesis.speak` was hooked and the utterance read back: **`"Which word
starts like that? Tap it."` at rate 0.85**, not the floored prompt. The story-end
beat spoke **`"Noah, you read the whole story. Now there is one thing to try."`**
and adapted to the one-question story it was on.

**The chevrons were wrong and the browser is what caught it.** `M12 4l-6 8 6 8`
rendered as `‹ | ›` — left and right, i.e. "previous / next" — in a place whose
entire job is "look down here". Invisible in the source, obvious in a screenshot.

**The art itself was reviewed on a contact sheet of all 33 at once**, three times,
and that is what found the errors no single file review would: every `leaf`/`amber`
body was taking its `*-100` highlight at full opacity so the dragon read as a
white seal (fixed by a 0.45 default in `litEllipse`); `hills()` chained two
quadratics and the join left a dark wedge mid-frame; `spaceBackdrop`'s dark band
read as a horizon in a picture with no ground; the flying dragon's wings were
cropped off the top of the frame at every scale that made the body legible; the
sleeping astronaut was drawing its own bunk on top of the scene's; and `path-01`'s
knight stood exactly where the fork he was supposed to be lost at was drawn.

**Gates.** `cd apps/web && bunx tsc --noEmit` clean; **`bun test src` 232 passed /
0 failed** (baseline 201, +31: 11 in `storyWords.test.ts`, 15 in
`questions.test.ts`, 5 in `art.test.ts`); `bun run build` clean, CSS
42.55 kB / 8.28 kB gzip, JS 430 kB / 124.63 kB gzip. API:
`tests/test_scene_tags.py` (+3), `test_stories_api.py`, `test_catalog.py`,
`test_phase2.py` — **75 passed**, `ruff` clean.

**That 232/0 is from an ISOLATED COPY, and the shared tree says otherwise.** A
re-run in the working tree at the end of this pass reported 229/3 and then
228/4, and none of the failures were mine: the concurrent agent had begun the
twelve-to-eleven stage renumbering, so `data/seed/stages.csv` and
`decoder/data/gpcs.csv` were mid-edit. `seeds.test.ts` asserts the web's
`SEEDED_STAGES` mirrors that CSV and that "§4 has twelve"; `phonics.test.ts`
glosses every row of the GPC inventory. Both files, and both CSVs, are untouched
by this work. The gate above was re-run in `/tmp/sf-web-gate` — the tree with
those two seed files restored from HEAD and nothing else changed — which is
`docs/STATE.md`'s own recipe for the Python suite applied to the web one, and it
collects the same 232 tests either way, so nothing was skipped to get the green.
`test_phase2.py::test_validate_endpoint_reproduces_the_demo_sentence` fails in the
shared tree for the same reason and passes in a `git archive` of HEAD against the
same live database, which is what proves it is the inventory and not the schema.

The full API suite was not run:
another agent is mid-edit in `precompute.py`, `generate.py` and four test files,
and `docs/STATE.md` is explicit that a concurrent writer on the shared Postgres
makes that suite lie.

**Page weight.** 33 segment + cover SVGs = **348 KiB total on disk, largest 21.5
KiB**, served once and cached for a year at a version-pinned path. The 15 cutouts
are under Vite's inline limit and land as data URIs inside the hashed JS, so they
cost **zero requests** and the immutable-cache hazard does not apply to them at
all. Nothing approaches the 500 MB registry cap; the whole `dist/` is 2.2 MB.

**`scripts/generate-art.mjs --check` is the ratchet.** It fails if any committed
file differs from what the generator now produces, if a file exists that the
generator no longer draws, if an `assets.csv` row has no drawing or a drawing has
no row, if their tag sets differ, or if any `fill`/`stroke` in any file is outside
the style guide palette. `tests/test_scene_tags.py::test_every_asset_row_has_a_
file_on_disk` is the other half and it was **sabotage-checked**: moving
`dragons/cave-01.svg` aside turns it red naming that url, restoring it turns it
green. That test is the one that would have caught this defect on day one.

### 12.15 — the off-limits handoff resolved itself mid-flight

12.4 planned for `mastery.py`'s `MAX_STAGE = 12` to stay untouched (another
agent's surface) with the one-line change described in the handoff report, and
`tests/test_mastery.py`'s pin rewritten to assert cross-module agreement — RED
until applied. That red lasted minutes: the concurrent agent applied the exact
consolidation (`MAX_STAGE = _DECODER_MAX_STAGE`, imported from the decoder) and
made `tests/test_questions.py` derive its fixture stage from the inventory
itself (`next(g.stage for g in load_gpcs() if g.grapheme == "w")`), which is
renumber-proof. Nothing in this change's own surface touched those files.

### Gates — all measured on 2026-07-30, commands as run

- `make precompute` → **34,288 rows across 11 stages in ~1.5s**; per stage
  771 / 1,383 / 1,934 / 2,149 / 3,197 / 3,461 / 3,899 / 4,333 / 4,378 / 4,386 /
  4,397. **Every stage clears §18's 150 floor** (minimum 771 = 5.1x); no
  starvation warning. The counts are exactly 11.11's measured twelve-stage
  counts shifted by one, which is what 12.14 predicted — the filters did not
  move.
- **Vacuity ratchet, re-measured at the new top stage** (same seed 20260728,
  same 800-word sample): `mastered_ids(11)` = the full 93-GPC set =
  **44.75%**, bit-identical to the pre-renumber value, ceiling 50% untouched,
  floor 35% untouched. Nowhere near the ceiling; nothing was smuggled in.
- **§5.7 goldens**: every case re-derived by executing the validator against
  the new inventory BEFORE the YAML was edited (12.5); `city@7` misses exactly
  `{c|S}`; the §17 demo sentence at stage 2 reproduces every verdict, both
  missing sets and 0.0 decodability. 18/18 word cases + demo case green.
- **Migration `f3a91c6d0e24`, sabotage-verified four ways on scratch DBs**:
  (1) fixture kids at every old stage 1..12 land at 1,1,2,3,4,5,6,7,8,9,10,11;
  (2) idempotent — the body re-executed against a renumbered database changes
  ZERO rows (full `pg_dump --data-only` byte-identical modulo pg_dump's random
  `\restrict` token), and the decodability recompute reports 0 changes on a
  second run; (3) a fresh empty database is a no-op through the whole chain;
  (4) an unknown ladder shape (max stage 13) raises and rolls back. Applied to
  the dev database: 5 merged-stage stories' decodability recomputed (story
  3241: 0.9677 → 1.0, its `rabbit` violation dissolved by the merge) and
  verified end-to-end on a reconstructed pre-renumber copy where the migration
  itself performed the same recompute.
- `cd services/api && .venv/bin/python -m pytest -p no:randomly` against a
  private copy of the database (STATE.md's concurrent-writer protocol):
  **1008 passed, 3 skipped, 0 failed** — the 6-failure baseline in the
  concurrent agent's files is gone too, after their own in-flight updates.
  Shared-DB runs show moving failures while two agents write to one Postgres;
  every one re-ran green in isolation.
- `.venv/bin/ruff check storyforge tests scripts` — all checks passed (one
  import-order fix in `tests/test_stage_coverage.py` was mine).
- `cd apps/web && bunx tsc --noEmit && bun test src` — typecheck clean,
  **232 pass, 0 fail**.
- `make warm-check` — **GREEN: 33 of 33 (stage, theme) pairs hold ≥3 servable
  stories.** The merged pool needed no generation: old stages 1+2's stories
  both landed at new stage 1.
- **No `make warm`, no `make eval`, nothing touched on DigitalOcean.** The
  production database is untouched; migration `f3a91c6d0e24` runs there at the
  next deploy's PRE_DEPLOY job. **Live spend: $0.00.**

**What is NOT proved.** The renumbered prompts have not generated a story (no
API call was authorised): band shifts (12.10) and the removed negation clause
(12.9) are argued from inventory facts and §10's own scaling, not from a live
A/B. The §14 matrix at 11 x 3 x 5 has not been run; docs/EVAL.md still records
run 114 on the old ladder, labelled as such. Production's renumber is verified
by construction and by scratch-DB sabotage, not yet against the live database —
the deploy's migration job is the step that does it, and `make warm-check` +
the §17 demo at stage 2 are the post-deploy checks to run.

## Phase 8 — six of the eleven stages are terminal dead ends (11.70-11.74)

Numbered in the 11.60-11.79 block because this is 11.67 revisited: the same
threshold, the same claim, measured a second way and found insufficient. Logged
**before** the change. Every number below was measured read-only against the
live local pool with `storyforge`'s own functions — no API call, nothing written.

**11.70 — `MIN_GPC_OBSERVATIONS` is not reachable at six stages by reading MORE,
because the whole servable pool does not contain that much evidence. 11.67
measured an average, and an average cannot see an exhausted pool.**

11.67's argument for leaving the floor at 10 was that a supply-side fix would
make it *"reachable in the five stories §8 asks for"*, and it measured the mean
(1.08 → 1.95 observations per story). The mean is the wrong statistic. The
question §8 actually asks is per stage, and it has two answers, both of which the
mean hides:

- **can a child clear the floor inside §8's five stories** (11.67's own promise);
- **can a child clear it at all**, having read every distinct story the stage
  holds — because a re-read yields nothing by design (`_ALREADY_READ_SQL`,
  DEVIATIONS 5.5's other half).

Measured per stage over the servable, answerable pool, crediting every `tap_word`
row's correct word through the SAME two functions the runtime uses
(`mastery.word_gpc_ids` for the alignment, `gpcs.stage_id` for "introduced at
this stage"), for a reader who answers everything correctly:

| stage | stories | best 5 stories | whole pool | verdict |
|---|---|---|---|---|
| 1 | 24 | 20 | 79 | promotable |
| 2 | 10 | 5 | 10 | needs 10 stories, not 5 |
| 3 | 11 | 5 | **7** | **never promotable** |
| 4 | 12 | 5 | 12 | needs 10 stories |
| 5 | 9 | 6 | 10 | needs ~8 stories |
| 6 | 9 | 5 | **9** | **never promotable** |
| 7 | 9 | 5 | **9** | **never promotable** |
| 8 | 9 | 5 | **8** | **never promotable** |
| 9 | 9 | 5 | **6** | **never promotable** |
| 10 | 9 | 2 | **2** | **never promotable** |
| 11 | 9 | — | 0 | top of the ladder; promotion is moot |

Stage 11 is the top of the ladder, so ten stages are gated by §8 at all. **Nine
of those ten fail 11.67's own reachability promise, and six are terminal**: a
perfect reader at stage 3, 6, 7, 8, 9 or 10 who reads every
story their stage has can never be promoted, and the dashboard tells them
`6 of 10 answered GPC instances needed on stage-9 sounds` — a sentence that
invites them to read more when reading more cannot move the number. The floor
that exists so one lucky tap cannot promote a child (5.5) has become a floor no
amount of honest work can reach.

Two contributing facts, both measured rather than assumed:

- **11.67's supply-side fix is in the code and in zero rows of the data.**
  `MAX_TAP_WORDS = 2` shipped; the live pool holds exactly one `tap_word` per
  story, 120 of 120 servable stories. Nothing re-enriched them.
- **The high stages are an allowlist problem, not a question-count problem.**
  Stage 10 supplies 2 observations across 9 stories and stage 11 supplies 0,
  because the stories contain no word that uses a sound their own stage
  introduces — `sound_targets`' `older_stage_fallback`, which 11.67 already
  recorded as open and which a second `tap_word` per story cannot fix.

**11.71 — `MIN_GPC_OBSERVATIONS` does not move, and neither does anything else in
`mastery.py`.** The floor is the reason a two-observation ratio cannot promote a
child (5.5), and lowering it would make the measurement green by making the claim
smaller — §0's rule about the validator, applied to the mastery signal. The
cumulative-denominator alternative is refused for the reason
`evaluate_promotion`'s docstring already gives: a stage-9 reader's cumulative
counters are dominated by stage-1 correspondences they mastered months ago, so a
child who never met the new graphemes would show 95% and advance. `MASTERY_QTYPES`
is not widened either (11.66). The defect is the supply, and the supply is where
it is fixed.

**11.72 — what ships here is the missing GATE: `--check-promotability`, with the
same standing as `--check-coverage`, asking §8's question rather than the pool's.**

`--check-coverage` is green on this pool and always was — it counts stories per
(stage, theme) and every pair holds ≥3. That is the whole problem: **§7's floor
and §8's floor are different floors**, and the product only ever measured the
first. A pair can hold three servable stories, be served correctly, and still be
a stage no child can leave.

The proposition, per stage below `MAX_STAGE`:

    sum of the top STORIES_REQUIRED_AT_STAGE stories' stage evidence
        >= MIN_GPC_OBSERVATIONS

Five design points, each of which could have been got wrong quietly:

- **Top five, not the whole pool.** The whole pool is the weaker claim (it
  catches only the six terminal stages); the top five is 11.67's own promise, and
  it fails at ten stages. A gate that reports the weaker fact would call stage 2
  fine while a child there must read ten stories to clear a floor documented as
  needing five. The report prints BOTH numbers so an operator can tell "needs
  more stories than §8 asks for" from "impossible".
- **Per stage, not per (stage, theme).** A child can change theme, so the
  stage-wide sum is the optimistic bound; a gate on the pessimistic one would
  fire on stages that are in fact reachable. This gate is red only when the
  product genuinely cannot promote.
- **The evidence is computed by the runtime's own two sources** — the decoder's
  alignment (`mastery.word_gpc_ids`, aligned at the STORY's stage, exactly as
  `readings.complete_reading` does) and the database's `gpcs.stage_id` (exactly
  what `_STAGE_EVIDENCE_SQL`'s promotion denominator joins against). A gate that
  re-implemented either would be a green light that lies. The two are verified
  equal to the decoder inventory today (93 ids, no stage disagreement), and the
  end-to-end test below binds the gate's arithmetic to what the HTTP routes
  actually do rather than to a restatement of them.
- **A perfect reader is assumed.** This is a ceiling: the most evidence the pool
  can ever yield. A gate about a hypothetical bad reader would measure the child.
- **It refuses to pass vacuously.** An unseeded ladder exits non-zero with `make
  seed` named, for 8.44's reason — a check over zero stages finds zero gaps.

**11.73 — the gate is RED on the live pool, and that is the finding, not a defect
in the gate.** It exits 0 against a pool that can supply the evidence (proved with
a fixture pool that the same code calls promotable, and with a real kid promoted
through the real HTTP routes at that pool), and non-zero against this one, naming
all ten stages and marking the six terminal ones. The operator's remedy is stated
in the failure message and is honest about its limits: `make warm` adds stories,
which fixes the stages that merely need more than five; re-enriching for 11.67's
second `tap_word` roughly doubles the yield per story; and neither one fixes
stages 9-11, where the stories contain no current-stage sound to ask about at all.

**11.74 — what is deliberately NOT done here.**

- **No supply is generated.** `make warm` and `make eval` spend real money and
  were not authorised; nothing in this change writes a row, calls a model, or
  touches DigitalOcean. Live spend $0.00.
- **The second `tap_word` is not backfilled onto the 120 stories that lack it**,
  and the reason is scope, not cost. Worth writing down for whoever does it: a
  top-up would need **no API call**. `tap_word` options come from
  `tap_word_options`, a pure function over a closed list; the instruction comes
  from `PROMPT_FRAMES`, which is human-authored; `check_tap_word` is the
  unchanged judge; and §13 layer 3 is exempt for this qtype (11.61), so only the
  free blocklist pass applies — exactly the shape of the two repairs that already
  exist (`refresh_tap_word_prompts`, `repair_leaky_cues`). It is a separate
  change because it writes child-facing rows into the pool and needs its own
  before/after measurement, and because on its own it fixes stages 3-8 and leaves
  9-11 where they are. The gate is what makes its absence loud.
- **The allowlist/generation work at stages 9-11 is untouched.** It is the same
  `older_stage_fallback` item 11.67 recorded as open, in another module.
- **No existing test's assertion is changed**, and no threshold moved.

### Gates — measured on 2026-07-30, commands as run

- `cd services/api && .venv/bin/python -m pytest -p no:randomly` — **1020 passed,
  3 skipped, 0 failed, exit 0**, against the stated baseline of 1008 + 3. All 12
  of the delta are `tests/test_promotion_supply.py`; no existing test's
  assertions were touched.
- **Sabotage-verified four ways, and the record in that file's docstring is what
  actually printed.** Reverting the whole change fails all 12 at collection,
  which is the weakest of the four; the three that matter are semantic. Counting
  every aligned GPC instead of the ones the stage introduces fails 5, including
  the end-to-end one, which then reports stage 2 as promotable while the kid it
  just watched read all nine stories perfectly sits at stage 2. Summing the first
  five stories instead of the best five fails 1. Removing the top-stage exemption
  fails 3. **One of those sabotages found a real defect in a test of mine**: the
  reachability case originally used `[1, 1, 2, 10, 1, 1, 1]`, whose first five
  and best five both sum to 15, so it passed with the sort deleted. Replaced with
  order-sensitive data.
- `.venv/bin/ruff check storyforge tests scripts` — all checks passed.
- `cd apps/web && bunx tsc --noEmit && bun test src` — typecheck clean, **232
  pass, 0 fail**. No web file is touched by this change.
- `python -m storyforge.warm --check-coverage` — **exit 0, 33 of 33 pairs at
  target**, unchanged, and now printing the sentence that says what it does not
  cover.
- `python -m storyforge.warm --check-promotability` — **exit 1**, the finding as
  an exit code: 9 stages short within five stories, 6 of them terminal
  (3, 6, 7, 8, 9, 10). The per-stage numbers it prints are the ones in 11.70,
  reproduced by the shipped code rather than by the throwaway script that found
  them.
- **No `make warm`, no `make eval`, no model call of any kind, nothing written to
  any database, nothing touched on DigitalOcean. Live spend $0.00.**

**What is NOT proved.** That the gate stays green once the supply is fixed — no
supply was generated, so the passing direction is proved on a fixture pool
(`test_the_gate_predicts_a_promotable_stage_end_to_end`, five stories at exactly
the floor, a real kid promoted through the real HTTP routes) and not on the live
pool. And this changes nothing a child sees today: the six terminal stages are
still terminal, and the dashboard still tells a stuck reader how many GPC
instances they need. What has changed is that the product can no longer report
itself healthy while they are stuck.

## Phase 8 — §7's serve budget was a formula, not a fact about the pool (9.71-9.74)

Numbered in the 9.60-9.70 block because this is **AUDIT-PHASE7 F4 revisited**:
the same finding, the same sentence in 9.66's docstring, measured a second way
and found not to hold. Logged **before** the change. Every number below was
measured in this tree, through the real ASGI app, against the real Postgres —
no model call, nothing committed.

**9.71 — 9.66's budget is still a FIXED number, so F4 recurs one order of
magnitude further out. The budget becomes a fact about the pool: how many
stories the ladder can actually reach.**

9.66 replaced `range(SERVE_ATTEMPTS)` with `range(_serve_attempt_budget(stage))`
= `3 * 2 * stage` and claimed the property *"`SERVE_ATTEMPTS` on every rung the
ladder can actually reach, not three in total"*. **The loop has no per-rung
reset** — it is one counter over the whole descent — so one rung can still spend
all of it, which is F4's own sentence verbatim. The audit measured the boundary
and it lands exactly on the formula: a stage-1 reader (budget 6) with 24 good
servable, answerable, decodable rows in the pool answered **0/30 requests with a
503 at five planted bad rows and 4/30 at six**; a stage-2 reader (budget 12)
descended on every request with 11 unservable rows at the rung and on **none of
12** with twelve, with 24 good stage-1 rows one rung down.

Reproduced here as two tests rather than as a script, both **RED against the
shipped code**:

```
test_a_wall_of_bad_rows_does_not_hide_the_good_ones_on_the_same_rung
  7 rows the serve-time gate refuses (7.7% decodable) buried a story that
  measures 100.0% on the SAME rung: 503 {"detail":"no story at or below stage 1
  passes §1's 95% gate for this reader; rejected 15028 (7.7%), … 15033 (7.7%)"}

test_the_ladder_descends_past_a_rung_the_serve_gate_refuses   (43 rows, stage 7)
  the ladder did not descend past 43 rows the serve-time gate refuses (16.7%
  decodable): 503 … rejected 15036 … 15077   (42 ids — the budget, exactly)
```

The 503 detail is the proof it is the budget and not the gate: it names 6
rejected ids at stage 1 and 42 at stage 7, i.e. `3 * 2 * stage` of them, and then
stops with the good story never picked.

**The fix is to stop deriving the budget from the ladder's SHAPE and derive it
from the ladder's CONTENTS.** The number of picks a request may make is now the
number of servable, answerable stories at or below the reader's stage — the size
of the candidate set `_SERVE_SQL` walks:

```
SELECT count(*) FROM stories
WHERE status = ANY(:statuses) AND stage_id <= :stage AND {ANSWERABLE_SQL}
```

That is not a bigger magic number, it is the exact point at which the walk can
have nothing left to look at. Every rejection appends the picked id to
`:excluded` and `_SERVE_SQL` refuses excluded ids, so after N picks the N-row
candidate set is empty and the next pick returns nothing regardless. **The loop
therefore stops when the pool at or below this reader is genuinely exhausted, and
at no other time** — which is what makes the 503 mean what its text says. §0 is
untouched: no story that fails §1's gate is served, ever; the change is only how
long the endpoint keeps looking for one that passes.

The ladder itself is still untouched, for 9.66's reason: `_SERVE_SQL`'s `ORDER
BY` already descends in exact §7 order and `stage_id <= :stage` is STATE
invariant 6. Only the budget moves.

`SERVE_ATTEMPTS` is **deleted**, not raised. Its docstring said "three is enough
to step over an isolated bad row", and that sentence was the defect: the pool
does not promise that bad rows come one at a time, and neither §7 nor §1 says a
child gets a fixed number of attempts at being served something readable.

**9.72 — REJECTED, with the reason each fails.**
- **The per-tier counter this finding proposed** — `SERVE_ATTEMPTS` failures per
  (stage, theme-match) tier, moving on by excluding the whole tier. It fails the
  case the same finding asks to be tested, and stage 1 is where it is impossible
  to hide: a stage-1 reader has ONE rung, so the good stories are IN the bad rung
  and abandoning the tier after three failures answers that child a 503 with a
  correct story behind the fourth row. **PROVED rather than argued** — the
  proposal was staged in the working tree (a `dict[(stage, theme_match), int]`
  counter; at 3 failures, every remaining id of that tier is read out of `stories`
  and appended to `:excluded`) and the two guards were run against it:

  ```
  test_the_ladder_descends_past_a_rung_…    PASSED   (it does descend)
  test_a_wall_of_bad_rows_…_same_rung       FAILED
    503 … rejected 15294 (7.7%), 15295 (7.7%), 15296 (7.7%)
  ```

  Three ids, not six: the tier is abandoned after exactly `SERVE_ATTEMPTS`, so
  the proposal is **worse than the code it replaces** for the reader who has no
  rung below them. At higher stages it is the same defect in a quieter form — it
  would push a reader down to a lower stage while good same-stage stories sat
  behind the third bad row, inverting §7's rungs for the sake of a bound. Any
  *per-rung* budget is still a fixed number of attempts at a rung whose bad-row
  count is unknown, which is 9.66's mistake at a smaller scale.
- **A bigger constant** (`SERVE_ATTEMPTS = 30`, or `budget * 10`). It moves the
  boundary and keeps it. F4 has now been filed twice against two different fixed
  numbers; a third would be the same entry a third time.
- **A wall-clock budget** ("keep walking for 250ms"). It makes the answer a child
  gets depend on how busy the box is, and makes the guard below unwritable —
  the test would assert a timing, not a property.
- **Filtering `_SERVE_SQL` on `stories.decodability >= :gate`** — 9.67's first
  rejection, unchanged and now doubly true: every bad row in both tests claims
  `1.0` in that column, which is exactly the shape of the four live stories whose
  stored verdict was a fiction.
- **Dropping the bound entirely** and looping until `_SERVE_SQL` returns nothing.
  Correct today — every branch that continues excludes the row it picked — but it
  makes an infinite loop in a request handler one forgotten `excluded.append`
  away. The count is one query and turns that into a 503 plus a log line.

**9.73 — What this costs, measured rather than asserted.**
- One extra `count(*)` per request, on every request including healthy ones:
  **0.582 ms** on the live pool (`EXPLAIN ANALYZE`, 590 stories, 120 candidates,
  `ix_stories_pool` bitmap scan — no new index).
- The healthy path is **one pick**, unchanged; the count only decides when to
  give up.
- The pathological path — 9.68's pool, where every servable story is undecodable
  — walks the whole reachable candidate set instead of `3 * 2 * stage` of it:
  **120 picks instead of 66** for a stage-11 reader on today's live pool, at
  **5.4 ms per pick** (measured from the serve-gate log timestamps in the
  stage-1 test: 15028 at 17.658s → 15033 at 17.685s), i.e. ~0.65 s instead of
  ~0.36 s. That is the cost of the walk being linear in the reachable pool, and
  it is stated rather than hidden: it is paid only when the pool is systematically
  broken, and the thing it buys is that a child is served instead of shown an
  error.
- Each refused pick still stamps `last_served_at`, which is why the audit's
  stage-1 reproduction failed 4 times in 30 rather than every time: the bad rows
  sort to the back of §7's rotation after they are refused. That is unchanged and
  it is the reason a partially-broken pool self-heals between requests.

**9.74 — What this does NOT fix.**
- **9.68's residual is still open and is still not claimed as fixed.** On a pool
  where nothing at or below the reader passes §1, `make warm-check` is still
  green and the child still gets a 503 — the endpoint now proves it looked at
  every reachable story first, which makes the 503 honest, not absent. Serving a
  story the gate refused would be the weakening §0 forbids. The wrong number
  there is the coverage gate's, and it needs `warm.py`.
- **The walk is linear in the reachable pool** (9.73). If the pool ever grows by
  an order of magnitude AND is systematically broken at the same time, the
  request gets slow before it gets wrong. The fix for that is a decodability
  verdict that can be trusted in SQL, which is 9.67's first rejection and a
  data-integrity problem, not a serving one.

**The guards, and what each one is red against.** Two tests in
`tests/test_stories_api.py`, both written and run RED before the change:

- `test_a_wall_of_bad_rows_does_not_hide_the_good_ones_on_the_same_rung` — NEW,
  and it is the one no per-rung budget can pass. Stage 1, so the reader has one
  rung and no rung below: 7 undecodable-but-answerable rows written first (so
  `last_served_at ASC NULLS FIRST, id ASC` puts every one of them ahead of the
  good story — it cannot be reached by luck), then one story the decoder measures
  at 100.0% for that reader. Both premises are measured in the test rather than
  asserted from a constant, so a 503 can never be the correct answer to it.
- `test_the_ladder_descends_past_a_rung_the_serve_gate_refuses` — RE-ARMED. It
  planted `SERVE_ATTEMPTS + 1` = 4 bad rows at stage 7, where 9.66's budget is
  42; that 10x margin is why 9.70's sabotage sweep could only turn it red against
  the ORIGINAL `range(SERVE_ATTEMPTS)`. It now plants 43. Every assertion is
  unchanged; the justify-test-edit verdict is recorded in its docstring.

Both derive the count from `3 * 2 * stage + 1`, spelled out in the test file
rather than imported, because the property is *"no fixed budget"* — the number
has to be past every constant the module has held, including one a future edit
reintroduces.

**Sabotage sweep — three, each restored and verified restored.**

| sabotage | the defect it stages | result |
|---|---|---|
| `old_budget` | `_serve_attempt_budget` → `return 3 * 2 * stage_id` (9.66's) | **both RED**: 503 naming exactly 6 rejected ids at stage 1 and 42 at stage 7 |
| `no_serve_gate` | `if decodability < DECODABILITY_GATE:` → `if False:` | **3 RED**, including `test_story_stage_binding.py::test_an_undecodable_story_in_the_pool_is_never_served` — the new test is measuring the §1 gate, not merely counting rows |
| `per_tier` | 9.72's proposal, implemented | descent test **PASSED**, same-rung test **RED** at 3 ids — the measurement in 9.72 |

**Gates.**
- `cd services/api && .venv/bin/python -m pytest -p no:randomly` — **1021 passed,
  3 skipped**. The same command on `8786d21` with these three files reverted:
  **1020 passed, 3 skipped**, so this adds exactly the one test it claims and
  moves nothing else. (A stale note put the baseline at 1008; it is 1020.)
- `.venv/bin/ruff check storyforge tests scripts` — all checks passed.
- `cd apps/web && bunx tsc --noEmit && bun test src` — clean, **232 pass, 0
  fail**. No web file is touched; kid mode is only the reason this is
  child-facing (`KidSession.tsx` retries three times and then navigates a tablet
  away from the story it was asked for).
- **No `make warm`, no `make eval`, no model call, nothing written to any
  database that outlives its transaction, nothing touched on DigitalOcean. Live
  spend $0.00.** Every measurement above is from the owned-pool fixture, whose
  transaction is rolled back, or from a read-only `EXPLAIN ANALYZE`.

## Phase 8 — the rewrite prompt ordered the model to delete a word it offered in the same message (9.75-9.78)

Numbered in the 9.40-9.45 block's family because this is **AUDIT-PHASE7 F1
revisited**: the same finding — *"a rewrite prompt that is provably incapable of
stating the failure it was invoked for, whose measured outcome is the deletion of
the theme"* — in the one branch F1's fix did not cover. Logged **before** the
change. Every number below was measured in this tree: the reproduction is
DB-free and makes no model call, and the live figures are read-only SQL over the
`storyforge` Postgres. Live spend $0.00.

**9.75 — `build_rewrite_block` could describe every way the GATE says no, and
still could not describe the commonest way a STORY WORD goes wrong: the model
used one and forgot to declare it.**

9.42's `story_word_verdict` covers the story words the model DID declare — too
many of them, and the code picks which one goes. Nothing covered the other
direction. A story word the model used without listing it in `story_words_used`
is not honoured by `validate_draft` (STATE invariant 2: the claim is intersected
with the OFFERED bank, and an unclaimed word is never in the claim), so it falls
through to normal scoring, fails to align against the stage's GPCs, and arrives
in `report.violations` as an ordinary undecodable word. The violations loop then
did the one thing it must never do to a themed story — it told the model to
delete it:

```
$ cd services/api && .venv/bin/python - <<'PY'   # no DB, no API call, $0
from storyforge.pipeline import Vocabulary, validate_draft, passes_gate, build_rewrite_block
v = Vocabulary(stage_id=10, stage_name="Stage 10", target_min=120, target_max=200,
    allowed_words=["by","but","back","before","best","rabbit","forest","tree","big","day"],
    sight_words=["the","a","to","and","it","is","in","for","of"],
    story_words={"burrow":"a hole an animal lives in","rabbit":"a small animal with long ears"})
body = "The rabbit had a burrow. The rabbit ran to the burrow. It is a big burrow."
print(validate_draft(v, body, []).decodability)          # used `burrow`, did not declare it
print(validate_draft(v, body, ["burrow"]).decodability)  # the one-field fix
PY
0.625   gate FAIL
1.0     gate PASS
```

and the block that failure produced, verbatim:

```
PROBLEMS (3):
- "burrow" — cannot be sounded out with any taught letter-sounds. Replace with one of: by, but, back, before, best
- "burrow" — cannot be sounded out with any taught letter-sounds. Replace with one of: by, but, back, before, best
- "burrow" — cannot be sounded out with any taught letter-sounds. Replace with one of: by, but, back, before, best

STORY WORDS — at most 3 DIFFERENT ones, only from this list:
- burrow: a hole an animal lives in
- rabbit: a small animal with long ears
...
Keep the same story ... Change only what is listed. Every replacement must come
from the permitted, sight, or story lists.
```

**One message says `burrow` is unreadable and must go, offers `burrow` as a
permitted story word, and says every replacement must come from the story list.**
That is F1's shape exactly — a prompt that cannot state its own failure — and the
counterfactual is measured, not argued: the identical text goes from 62.5% (gate
FAIL) to 100% (gate PASS) on the strength of one field the model left empty. The
replacement list is the sharpest part of it: `nearest_allowed("burrow", …)`
returns same-initial-letter neighbours, so the prompt proposes `by, but, back,
before, best` for a burrow.

**9.76 — it is not hypothetical, and one instance is child-facing today.**

Read-only SQL over `storyforge`, counting violation tokens across every recorded
attempt whose word is an OFFERED story word for that story's own theme at or
below its own stage:

| word | violation tokens | stories |
|---|---|---|
| burrow | 67 | 13 |
| moon | 26 | 10 |
| stars | 14 | 8 |
| feathers | 8 | 6 |
| astronaut / fire | 4 | 2 / 3 |
| cave | 3 | 2 |
| wings / whiskers | 2 | 2 / 1 |
| scales | 1 | 1 |

**131 tokens in all.** One survived into the pool a child can be served:

```
id=7342  stage_id=10  theme=animals  status=warm  decodability=0.9608  story_words={}
  attempt 0  generate  dec 0.9474  violations ["many","liked","near","quiet"]
  attempt 1  rewrite   dec 0.9608  violations ["burrow","burrow","burrow","burrow"]   <- served
```

`story_words` is EMPTY and `burrow` is spending four of that story's violation
budget. `burrow` is in the bank for `animals` from `min_stage=2` with the gloss
*"a hole an animal lives in"*. So the story clears §1's gate by 0.6 points while
serving, as an unreadable word, a word for which the product holds a gloss and a
chip — §11's *"story words render as chips with picture + auto-audio on first
appearance"*, and §18's *"themes lean on story words + pictures for interest"*.

**9.77 — the fix: partition the violations, and give the cap ONE honest set of
books.**

`build_rewrite_block` now splits `report.violations` on membership of
`vocab.story_words` before it builds a single bullet. A word that is NOT in the
bank keeps today's bullet unchanged, `nearest_allowed` and all. A word that IS in
the bank never reaches `nearest_allowed`; it is named as a story word the model
already owns and told to declare it.

The trap in doing only that is the cap. §4 allows three DIFFERENT story words,
and telling a model that already declared three to declare a fourth converts a
decodability failure into a cap failure — fixing one gate condition by breaking
another, which is the failure mode this file exists to remember. So the two
directions are settled against ONE budget rather than separately:

* `story_word_verdict` is UNCHANGED and still answers *"of the words it declared,
  which stay"* → `keep` / `drop`;
* the undeclared story words are ranked by the same rule (occurrences desc,
  alphabetical tie-break, so the choice is deterministic and testable) and take
  whatever slots `keep` leaves — `adopt` for the ones that fit, and the remainder
  simply joins `drop`, because with the cap full the honest instruction is to
  remove them;
* the cap bullet and the cap headline then count `keep + adopt` against
  `drop + evict`, so the number the model is shown is the number of distinct
  story words its text actually contains — declared or not. When nothing is
  undeclared, `adopt` and `evict` are empty and both strings are byte-identical
  to 9.42's, which is why the existing cap tests are untouched.

The headline is conditional for the same reason 9.44 made it conditional: a
story whose only violations were undeclared story words did NOT *"use words this
child cannot read"*, so that sentence is no longer reachable for it.

**9.78 — `a animals story`, in a prompt whose rule 4 demands correct English.**

`about = f" It must still be a {theme} story."` interpolates the theme SLUG, and
two of the three slugs make that sentence wrong: it has been shipping *"It must
still be a animals story."* on every `animals` rewrite. `SYSTEM_RULES` rule 4 is
*"Every sentence must be correct English ... no word is missing or left over"*,
quoted at the model in the cached prefix of the same request. The article is now
chosen by the slug's first letter. Only the article is touched: `a dragons story`
is a plural attributive, not an article error, and the tests that assert the
theme reaches the model assert on `"dragons story"` / `"animals story"`, which
both remain literal substrings — so no existing assertion moves and
justify-test-edit is not engaged.

**NOT changed: `llm/generate.py`'s `build_request_block`, which renders the same
`Write a {theme} story.`** It has the identical article bug. It is also the
prompt every first draft is generated from, on the path that spends money, and
9.78 has no before/after measurement for generation quality. Moving it is a
separate change with its own measurement, not a rider on this one. Recorded here
so it is not lost.

**What the fix does to the live instance.** Story 7342's own text, re-scored
against the vocabulary its own stage and theme actually offer (read-only load
from the live Postgres, no model call):

```
as persisted (story_words={}):  dec=0.9608  gate=True  violations=['burrow' x4]
declaring 'burrow' alone:       dec=1.0000  gate=True

headline now: Your story is readable, but it used story words without declaring them. Rewrite it.
PROBLEMS (1):
- "burrow" is one of your STORY WORDS, not a word this child cannot read. Keep it
  exactly as it is and add it to story_words_used — a declared story word is free,
  because the child is shown a picture for it and hears it read aloud.
...It must still be an animals story.
```

Three bullets became one, the one that is left is true, and the theme sentence is
grammatical. Note that 7342 CLEARS §1's gate either way — 0.9608 is above 0.95 —
so this was never a gate story. It is a story serving a chip word as an
unreadable word, which is a §11/§18 defect the gate cannot see.

**Tests — four added, none changed.** They sit beside
`test_no_gate_failure_can_produce_an_empty_problem_list`, which is 9.45's guard
against the same class.

- `test_no_bullet_ever_orders_the_deletion_of_a_word_the_same_message_offers` —
  the missing assertion, and the one this file exists for. It regex-matches the
  head of every `- "word" — ... Replace with one of:` bullet across four cases and
  asserts none is a word `vocab.story_words` offers. **It asserts its own premise
  first** (`report.violations` ∩ offered bank must be non-empty), because the
  five pre-existing `_rw` cases have story words `{dragon, cave, wings, fire}` and
  violations only `shed`/`zorp` — a guard written over those inputs would have
  been vacuous from birth, which is the trap 8.50-8.56 was.
- `test_declaring_the_story_word_is_the_whole_difference` — the counterfactual as
  an assertion, so the bullet's claim stays honest: same text, gate FAIL at 0.75
  and gate PASS at 1.0, on one field.
- `test_adopting_an_undeclared_story_word_never_breaks_the_cap` — the naive fix,
  refused. Three declared + one undeclared: the fourth is EVICTED, the KEEP list
  is still exactly `MAX_STORY_WORDS`, and the count shown is 4.
- `test_the_theme_sentence_is_correct_english_for_every_seeded_slug` — all three
  seeded slugs, and `"a animals story" not in block` by name.

**Sabotage sweep — three, each restored and verified restored.**

| sabotage | the defect it stages | result |
|---|---|---|
| `no_partition` | `undeclared = []` + `unreadable = list(report.violations)` — the shipped code | **2 RED**: `AssertionError: undeclared: the block orders the deletion of ['cave'], which it offers as a story word in the same message` |
| `cap_overrun` | `adopt = undeclared` — the naive fix, ignoring the slots | **1 RED**: cap test, headline came back "used story words without declaring them" with a fourth word adopted |
| `article` | `_article` → `return "a"` | **1 RED**: `'It must still be an animals story.' in '... be a animals story. ...'` |

`no_partition` leaving the cap test green and `cap_overrun` leaving the class
guard green is the point: the two failures are independent, so neither test is
carrying the other.

**Gates.**
- `cd services/api && .venv/bin/python -m pytest -p no:randomly` — **1025 passed,
  3 skipped**, against a measured baseline of **1021 passed, 3 skipped** on
  `8690a5a`. Exactly the four tests claimed, and nothing else moves. (The task
  brief said 1008; that number is stale, as 9.74 already recorded.)
- `.venv/bin/ruff check storyforge tests scripts` — all checks passed.
- `cd apps/web && bunx tsc --noEmit && bun test src` — clean, **232 pass, 0
  fail**. No web file is touched.
- **No `make warm`, no `make eval`, no `make eval-smoke`, no model call, nothing
  written to any database, nothing touched on DigitalOcean. Live spend $0.00.**
  Every live figure above is a read-only `SELECT`; the re-score loads the
  vocabulary and runs the pure decoder in-process.

## Phase 8 — §8's promotion guard counted the wrong GPCs, at the one stage where that is invisible (11.79-11.81)

Numbered in the 11.60-11.79 block because this is 11.70's other half: the same
denominator — *the correspondences a stage INTRODUCES* — and the same failure
mode, a green gate measuring a quantity next to the one it is named for. 11.70
found six terminal stages that shipped under a green promotion suite; this is
**why that suite was green**. Logged **before** the change. Every number below
was measured read-only against the live local `storyforge` Postgres with the
product's own functions — no API call, nothing written, live spend $0.00.

**11.79 — `test_five_clean_readings_promote_the_kid` guards its own arithmetic
with the length of the word's WHOLE alignment, while the grader sums only the
correspondences the stage introduces. The two are equal at stage 1 and nowhere
else, and stage 1 is the only stage the file runs at.**

The guard, verbatim (`tests/test_readings.py`, introduced in 383c6d2 beside the
docstring *"5 stories at stage 1, 15 GPC observations, 100%"*):

```python
per_reading = len(word_gpc_ids(TAP_WORD, TAP_STAGE))
assert per_reading * STORIES_REQUIRED_AT_STAGE >= MIN_GPC_OBSERVATIONS
```

`word_gpc_ids` returns the whole alignment — every instance the child is credited
as having *seen* (`mastery.gpc_increments` filters nothing, deliberately). The
stage filter is applied at READ time, by `readings._STAGE_EVIDENCE_SQL`:

```sql
FROM gpc_stats s JOIN gpcs g ON g.gpc_id = s.gpc_id
WHERE s.kid_id = :kid_id AND g.stage_id = :stage_id
```

`= :stage_id`, not `<=`. That is the whole distance between the two numbers, and
`mastery.evaluate_promotion`'s docstring already names it: *"aggregated over the
GPCs **introduced at** `stage_id`, not the cumulative set a stage-N reader has
been taught."* The guard uses the cumulative one.

Measured over all 120 servable `tap_word` rows in the live pool, crediting each
row's correct word through `mastery.word_gpc_ids` at the STORY's stage exactly as
`complete_reading` does:

| stage | rows | mean alignment | mean introduced | guard's verdict (x5 >= 10) | grader's verdict |
|---|---|---|---|---|---|
| 1 | 24 | 3.29 | **3.29** | yes, 16.5 | yes, 16.5 |
| 2 | 10 | 3.20 | 1.00 | yes, 16.0 | **no, 5.0** |
| 3 | 11 | 4.00 | 0.64 | yes, 20.0 | **no, 3.2** |
| 4 | 12 | 3.08 | 1.00 | yes, 15.4 | **no, 5.0** |
| 5 | 9 | 3.11 | 1.11 | yes, 15.6 | **no, 5.6** |
| 6 | 9 | 3.44 | 1.00 | yes, 17.2 | **no, 5.0** |
| 7 | 9 | 3.78 | 1.00 | yes, 18.9 | **no, 5.0** |
| 8 | 9 | 3.56 | 0.89 | yes, 17.8 | **no, 4.4** |
| 9 | 9 | 4.22 | 0.67 | yes, 21.1 | **no, 3.3** |
| 10 | 9 | 4.33 | 0.22 | yes, 21.7 | **no, 1.1** |
| 11 | 9 | 3.00 | 0.00 | yes, 15.0 | **no, 0.0** |

The guard says *"there is enough evidence here"* at all eleven stages. The
grader says it at one. They agree at stage 1 because every correspondence a
stage-1 word can align to is a correspondence stage 1 introduces — `sit` is
`s|S, i|IH, t|T`, and all three are stage-1 rows — so the wrong count and the
right count are the same integer, 3, and `TAP_STAGE = 1`.

This is not a hypothetical. The finding's sabotage, reproduced in a throwaway
`git archive HEAD` copy against a throwaway database, changed **only**
`TAP_STAGE = 1` to `TAP_STAGE = 6`:

```
FAILED test_five_clean_readings_promote_the_kid - assert False is True
FAILED test_promotion_stops_below_the_accuracy_threshold
FAILED test_promotion_resets_the_story_count_at_the_new_stage
```

and the guard on the line above the first failure **still passed** (3 x 5 = 15 >=
10) while the promotion it exists to guarantee never happened. A guard that
cannot go red before the thing it guards is not a guard; it is a comment with
`assert` in front of it.

**11.80 — the fix: the guard is computed from the rows the grader joins, and one
promotion test now runs at a stage where the two counts DISAGREE.**

Two changes, both in `tests/test_readings.py`. No product file is touched.

- `_evidence_per_reading(session, word, stage_id)` calls `mastery.stage_evidence`
  — the function 11.70 already wrote for exactly this quantity, and the one
  `warm.stage_supply` gates on — with `introduced` read from **`SELECT gpc_id
  FROM gpcs WHERE stage_id = :s`**. From the database, not from the decoder's
  CSV, because `_STAGE_EVIDENCE_SQL` joins those rows: a guard that recomputed
  which stage teaches which grapheme would be free to disagree with the product
  it is guarding and would still print a green dot (12.5's lesson, and
  `stage_evidence`'s own docstring says so).
- A second promotion test at **stage 5**, on `oatmeal`: five correspondences in
  the alignment, **two** of them stage-5 rows (`oa|OW`, `ea|IY`), so five clean
  readings are worth exactly `MIN_GPC_OBSERVATIONS = 10`. The boundary,
  deliberately — the fixture stories are built by the same factory the stage-1
  ones are, so the stage is a parameter, and the body
  *"Sam had oatmeal on a rainy day. The pup was sleepy."* measures **1.000**
  decodable at stage 5 with all three tap options in it.

Per `justify-test-edit`, recorded at the edit site and here: this is **not a
failing test being loosened**. The suite is green and no product code changed.
The replacement quantity is a filtered subcount of the same list, so it is
**strictly smaller** — 2 where the old guard read 5 — which makes the assertion
harder to satisfy, not easier. Nothing is deleted, skipped or widened; two
assertions are added.

**11.81 — what is NOT changed, and why the neighbouring call sites are right.**

`word_gpc_ids(TAP_WORD, TAP_STAGE)` appears five more times in the same file
(the `gpc_updates` payload, the `gpc_stats` rows twice, the replay attack, the
progress DTO). Every one of those is about what the child was credited as having
**seen**, which really is the whole alignment: `gpc_increments` marks every
instance and the stage slice is taken later, at read time. Those five are
correct and are left exactly as they are. The wrong quantity was in one
assertion, and only that assertion moved.

Nothing in `storyforge/` changes: not `_STAGE_EVIDENCE_SQL`, not
`MIN_GPC_OBSERVATIONS`, not `STORIES_REQUIRED_AT_STAGE`, not `MASTERY_QTYPES`.
The six terminal stages of 11.70 are still terminal and `make promote-check` is
still red — this entry does not fix that and must not be read as fixing it. It
fixes the reason the promotion suite could not see it.

**Tests — one added, one assertion corrected, two assertions added, none
weakened and none deleted.**

- `test_five_clean_readings_promote_the_kid_above_stage_one` — the new one, and
  the whole point. The same loop the stage-1 test drives, through the same real
  routes, at stage 5: `assert per_reading < aligned` states its own premise
  first (2 versus 5 — a test that only proves something where the two counts
  differ has to check that they do), and every reading asserts
  `promotion.gpc_seen == per_reading * n`, so the runtime is being asked to
  agree with `stage_evidence` on every step and not just at the finish line.
  Promotion lands on the fifth story, at exactly 10 observations, and the kid
  row moves 5 → 6.
- `test_five_clean_readings_promote_the_kid` — the corrected guard, plus
  `promotion.gpc_seen == per_reading * STORIES_REQUIRED_AT_STAGE == 15`, which
  is the number its docstring has claimed since 383c6d2 and never asserted.
- `story_pool` — the fixture `distinct_stories` was, with the stage, word,
  options, grapheme and body as parameters. `distinct_stories` is now the
  stage-1 call and keeps its docstring verbatim; nothing about what it builds
  changed.

**Sabotage — two, each restored and verified restored.**

| sabotage | the defect it stages | result |
|---|---|---|
| `_STAGE_EVIDENCE_SQL` → `g.stage_id <= :stage_id` (product) | the cumulative denominator `evaluate_promotion`'s docstring forbids | **1 RED**: `AssertionError: reading 1: the grader counted 5 stage-5 instances, not 2` — and **52 passed**, every stage-1 promotion test among them |
| `_evidence_per_reading` → `len(word_gpc_ids(word, stage_id))` (the shipped guard, restored) | the finding itself | **1 RED**: `AssertionError: 'oatmeal' is worth 5 at stage 5 and aligns to 5 correspondences — this test only proves anything at a stage where those differ, which is every stage except stage 1` |

The first row is the entry. A cumulative promotion denominator — the exact
defect `mastery.evaluate_promotion` spends a paragraph forbidding, the one that
would advance a reader who never once met the new graphemes on the strength of
stage-1 sounds they mastered months ago — is invisible to all 52 other tests in
this file and visible to the new one on its FIRST reading. Before this change
the whole file was the 52.

**Gates.**
- `cd services/api && .venv/bin/python -m pytest -p no:randomly` — **1026
  passed, 3 skipped**, against a baseline of **1025 passed, 3 skipped** measured
  on `de6e48f` in this tree before the change. +1, the one new test, and nothing
  else moves. (The task brief said 1008; that number has been stale since 9.74.)
- `.venv/bin/ruff check storyforge tests scripts` — all checks passed.
- `cd apps/web && bun test src` — **232 pass, 0 fail**. No web file is touched.
- The suite writes to the live local Postgres and cleans up after itself:
  `SELECT count(*) FROM stories WHERE title LIKE 'Distinct %'` is **0** after the
  run, so the new stage-5 fixture leaves the warm pool exactly as it found it and
  neither `make warm-check` nor `make promote-check` is perturbed.
- **No `make warm`, no `make eval`, no `make eval-smoke`, no model call, nothing
  touched on DigitalOcean. Live spend $0.00.**

## Phase 8 — 11.60 shipped a sentence no child has ever been shown (11.82-11.85)

Numbered in the 11.60-11.79 block because it IS 11.60, unfinished. That entry
rebuilt the tap-a-word frame ladder for a reason quoted from a live user report,
and closed with: *"Not done, and left for the operator on purpose: the 125
existing `tap_word` rows still carry their pre-11.60 prompts.
`warm.refresh_tap_word_prompts` fixes them in place with no API call and is
idempotent ... It was not run here because the database was mid-migration."*
The migration landed in 12.1-12.14. The command was never run. Logged **before**
the change. Every number below was measured against the live local `storyforge`
Postgres — read-only — and on a `pg_dump` copy of it (`sfprompt`) with the
product's own functions. No API call, live spend $0.00.

**11.82 — 0 of 125 tap-a-word rows carry a prompt the current ladder can
produce, and 66 of the 120 SERVABLE ones still do not say what the task is.**

```
select count(*) filter (where prompt like 'Tap the word that starts with the same sound as %'),
       count(*) filter (where prompt like 'Tap:%'), count(*)
from questions where qtype='tap_word';
  0 | 0 | 125
```

Both current rungs, zero rows. The 120 rows on servable (`warm`|`complete`)
stories — one per story, 120 distinct stories — are these six shapes, none of
which any rung in `PROMPT_FRAMES` can spell:

| instruction | rows | names the task? |
|---|---|---|
| `Tap the one that starts with the same sound as X.` | 45 | yes |
| `Tap it. Hint: X.` | 22 | **no** |
| `Tap it if it begins like X.` | 21 | **no** |
| `Tap it: X.` | 12 | **no** |
| `Tap it if it begins as X.` | 11 | **no** |
| `Tap the one that starts like X.` | 9 | yes |

**66 of 120 (55%)** are the exact class 11.60 was written about — `llm/questions.py`'s
own module docstring records the complaint as *"57% of live instructions did not
say what the task was, and that is what the user reported"* — and every one of
them is being served today. The remaining 54 are the *second* version of the
ladder, the one 11.62 replaced. So the pool holds two retired ladders and none of
the shipped one, and the top row differs from the shipped sentence by two words
(`the one` for `the word`), which is why nothing looked wrong to a reader
skimming the pool.

The other 5 rows sit on `rejected` stories and are out of scope by the same
predicate `refresh_tap_word_prompts` uses: `rejected` is never served, and status
only ever moves *toward* it (`run_grammar_backfill`), never back.

**11.83 — the repair is one free command, it works, and it is idempotent.
PROVED on a copy before it was run on anything.**

`pg_dump`ed the live database into `sfprompt` and ran the shipped functions
against it, in `run_backfill`'s own order:

```
repair_leaky_cues        -> repaired 0, stuck [], answered []      (7.14 is clean)
refresh_tap_word_prompts -> rewritten 120, left alone []
refresh_tap_word_prompts -> rewritten 0,   left alone []           (idempotent)
```

All 120 land on **rung 1**, the fully spelled-out sentence; not one falls to the
`Tap: {cue}.` floor, which is what 11.62 predicted (both rungs contain only
`INSTRUCTION_WORDS` plus the cue, so they pass or fail the gate together — the
floor is retained for a future frame change, not for a stage). `UPDATE questions
SET prompt` and nothing else: no DELETE, no regenerated row, `options`, `answer`
and `payload` untouched. That matters here and is not decoration — **21 recorded
`answers` hang off 7 of these rows** and `answers.question_id` is `ON DELETE
CASCADE`, so the §8 evidence a child has already earned would go with any row
that was replaced instead of repaired.

**11.84 — the residual is that nothing in the build can ever notice this again,
so what ships is a read-only gate, not just the repair.**

11.61 recorded the stale rows as *"a known inconsistency rather than a silent
one"* — but the record was a paragraph in this file, and a paragraph does not
exit non-zero. The pool went 125-for-125 wrong for two full phases under a green
suite, a green `make warm-check` and a green `make promote-check`, because no
check anywhere reads a question's TEXT. Three things are added:

- **`warm.stale_tap_word_prompts(session, ...)`** — the finding's own query, as a
  function: every `tap_word` row on a servable story whose `prompt` is not a rung
  of the CURRENT `PROMPT_FRAMES` filled with that row's OWN `payload.cue_word`.
  The predicate is `questions.prompt_is_current`, which lives beside
  `PROMPT_FRAMES` and is spelled *out of* it, so it cannot drift from the ladder
  the way a hand-copied `LIKE` in a gate would. Membership, not re-derivation, on
  purpose: it needs no stage vocabulary, so it reads the whole pool in one query,
  and "no rung produces this" is exactly the proposition. The stricter statement
  (the row is what `tap_word_prompt` would write *today*) is `refresh_tap_word_
  prompts` returning `(0, [])`, and a test asserts that over the live pool too.
- **`python -m storyforge.warm --check-prompts`** (`make prompt-check`) — prints
  the offending instructions and exits **non-zero** while any row is stale. Free:
  one SQL query, no API client, nothing written.
- **a postcondition inside `--backfill`** — after its own `refresh_tap_word_
  prompts` it re-reads the pool and refuses if a row it could have repaired is
  still stale. A repair that reports success while leaving the defect in place is
  the failure mode that cost this project four ungrammatical stories in the pool
  (`_body_verdict` wrote three of five verdict fields and the sweep printed
  success), so the repair checks its own work rather than trusting its own
  bookkeeping. Rows with **no** `cue_word` are excluded from that refusal and
  from it alone: there is nothing to re-derive from, `--backfill` has no remedy
  for them, and a refusal whose remedy is "there is nothing you can do" teaches
  an operator to ignore the exit code. They are named by id on every run and
  `--check-prompts` still refuses on them. The live pool holds **zero** — the
  node has written `cue_word` since 4.9.

**Deliberately NOT wired into `--check-coverage` or `--check-promotability`.**
Same argument `run_promotability_check`'s docstring already makes for being a
second command rather than a clause in the first: those two gates are SUPPLY
floors whose remedy is `make warm` and real money, and this one is a text defect
whose remedy is a free in-place `UPDATE`. Folding it in would return one exit
code for "buy more stories" and "run the free repair". There is a second reason
and it is concrete: both of those gates are **MANDATORY** deploy steps in
`docs/DEPLOY.md`, and the production database on DigitalOcean carries its own
copy of these rows — which this change is not permitted to touch. Turning a
mandatory production gate red for a defect the operator is not being handed a
remedy for in the same breath is how a gate gets ignored. `--check-prompts` is
listed in DEPLOY.md as its own free step, with the remedy on the same line, and
the residual is stated there rather than only here.

**11.85 — tests: two live-pool invariants (both RED before this change), one
refusal test, one predicate test. No existing assertion is edited, weakened or
deleted.**

`justify-test-edit` was not needed: nothing existing changed. Four tests are
added, and the two that matter are the ones that would have caught this on the
day 11.60 shipped.

- `test_the_live_pool_serves_no_pre_11_60_tap_word_instruction` — the property
  over the real pool, stated independently of the repair code: it rebuilds
  `{frame.format(cue=row.cue) for frame in PROMPT_FRAMES}` in the test and asserts
  membership. It does not call `stale_tap_word_prompts`, so a bug in that function
  cannot make it pass.
- `test_the_live_pool_needs_no_tap_word_prompt_repair` — the stronger half, via
  the shipped repair: `refresh_tap_word_prompts` over the live pool returns
  `(0, [])`. Every servable row is *exactly* what `tap_word_prompt` writes today,
  not merely a rung shape.
- `test_the_backfill_refuses_when_a_repairable_prompt_is_still_stale` — the
  postcondition, driven by monkeypatching the refresh into a no-op so a repairable
  stale row survives to the check. Without this the postcondition is unreachable
  by construction (the refresh runs first and fixes everything fixable), which is
  precisely the kind of guard that goes green without ever being executed.
- `test_a_prompt_is_current_only_for_its_own_cue` — the predicate: the shipped
  sentence with the row's own cue is current; the same sentence with a different
  cue, every retired shape, and a row with no cue are not.

**Sabotage — four, each restored and verified restored.** The record is what
actually went red, not what was expected to.

| sabotage | the defect it stages | result |
|---|---|---|
| the DATA, before the repair: the live pool as it stood | the finding itself | **2 RED**: `120 of 120 servable tap-a-word instruction(s) are not anything the current ladder writes: [(19, 17, 'Tap it if it begins as for.'), ...]` and `the repair would rewrite 120 live tap-a-word prompt(s)`. `--check-prompts` exit **1** |
| the DATA, after the repair: `update questions set prompt='Tap it. Hint: win.' where id=705` | one row regressing, e.g. a partial restore | **2 RED**, naming the single row: `1 of 120 ... [(705, 14, 'Tap it. Hint: win.')]`. `--check-prompts` exit **1**, then `--backfill` re-derived exactly 1 and the gate went green again |
| `PROMPT_FRAMES[0]` moved back to the retired `Tap the one that starts with the same sound as {cue}.` | **the 11.60 event repeating** — the ladder moves and nobody repairs the pool | **RED**, including both live-pool tests. This is the whole point: before this change that edit was green everywhere and 125 rows silently went stale |
| `stale = []` in place of the `--backfill` postcondition | the repair reporting success over its own leftovers | **1 RED**: `Failed: DID NOT RAISE SystemExit` |

**The repair, run for real.** `python -m storyforge.warm --backfill` against the
live local `storyforge` database: `re-derived 120 tap-a-word prompt(s) from the
current frame ladder`, then `nothing to backfill`, exit **0**. A second run
re-derived **0** — idempotent, as 11.83 predicted on the copy. `_UNENRICHED_SQL`
was measured read-only first and returned **0 stories**, and
`payload ? 'target_grapheme'` **0 rows**, so the run made **no API call and moved
no answer key**; live spend **$0.00**. Afterwards:

- the finding's own re-assertion — `select count(*) ... where qtype='tap_word'
  and prompt not like 'Tap the word that starts with the same sound as %'` over
  servable stories — is **0**;
- **21 `answers` rows still hang off 7 of those questions**, the same 21 as
  before: nothing was deleted, `options` and `answer` are byte-identical;
- served through the real route (`GET /api/stories/{id}`, `DEMO_BYPASS_AUTH`),
  the three stories the finding quoted now read
  `Tap the word that starts with the same sound as win.` (story 14, stage 2,
  options `['went','hit','man']`), `... as for.` (17), `... as cat.` (2), and
  `target_grapheme` is still absent from every served payload.

**The 5 rows on `rejected` stories keep their old sentences and that is correct**
— `refresh_tap_word_prompts` filters on the serving predicate, `rejected` is never
served, and status never moves back out of it.

**Gates.**
- `cd services/api && .venv/bin/python -m pytest -p no:randomly` — **1030 passed,
  3 skipped**, against a baseline of **1026 passed, 3 skipped** measured in this
  tree on `c54dfac` before the change. +4, the four new tests, nothing else moves.
  (The task brief said 1008; that number has been stale since 9.74.)
- `.venv/bin/ruff check storyforge tests scripts` — all checks passed.
- `cd apps/web && bun test src` — **232 pass, 0 fail**. No web file is touched.
- The pool is where it was found, plus the repaired text: `--check-coverage` still
  **green** (33 of 33 pairs), `--check-promotability` still **red** in exactly the
  way invariant 15 records (six terminal stages — this change neither fixes nor
  hides that), 120 servable `tap_word` rows before and after, 21 answers before
  and after.
- **No `make warm`, no `make eval`, no `make eval-smoke`, no model call, nothing
  touched on DigitalOcean. Live spend $0.00.**

**The production database on DigitalOcean is untouched and almost certainly holds
the same defect.** It cannot be repaired from here and must not be. What ships for
it instead is `docs/DEPLOY.md` §4: `--check-prompts` is now listed as a MANDATORY
free check with `--backfill` on the line under it, and §6 re-runs it after any
restore. That is the difference between this residual and 11.61's — an operator is
told, by an exit code, in the console where the remedy runs.

## Phase 8 — the practice list is chosen by FREQUENCY where §8 needs COVERAGE (11.86-11.90)

Numbered in the 11.60-11.85 block because it is the generation-side half of
11.70's finding — `make promote-check` is RED, and its own remedy text says the
part `make warm` cannot fix is *"a stage whose stories contain no word using a
sound that stage introduces — that is an allowlist and generation problem"*.
5.17 built the lever for exactly that (`llm/generate.practice_words`) and then
selected with the wrong statistic. Logged **before** the change. Every number
below was measured against the live local `storyforge` Postgres, read-only, with
the product's own functions. No API call, no `make warm`, no `make eval`, live
spend **$0.00**.

**11.86 — the practice list truncates by frequency, and four stages lose a sound
the allowlist could have named. Stage 2 loses `sh`.**

`practice_words` walks the allowlist in frequency order, keeps every word that
uses a GPC the stage introduces, and cuts at `PRACTICE_WORD_LIMIT = 24`. §8 does
not need the 24 most common such words; it needs the stage's introduced GPCs to
be *reachable* — a `tap_word` can only ask about a grapheme the STORY contains,
and the story can only contain what the prompt pointed at. Measured over the
live top-400 windows (`ORDER BY freq_rank LIMIT 400`, `pipeline.load_vocabulary`'s
own query), counting only GPCs the window can actually express — sight words and
blocklisted words excluded, as `practice_words` excludes them:

```
stage  introduces  expressible   named by     the GPCs the list never names
        #GPCs      in the window  PRACTICE
    2       13          12           10       ['qu|K.W', 'sh|SH']
    4        7           7            6       ['u_e|UW']
    5       10          10            9       ['oa|OW']
    7        8           8            6       ['au|AO', 'oy|OY']
```

Every missing word was sitting in the same 400 words the list was drawn from:
`sh|SH` → short, wish, shall, **ship**, cash, finish; `qu|K.W` → quick, quit;
`u_e|UW` → june, rule; `oa|OW` → road, goal, coach; `oy|OY` → **boy**, enjoy;
`au|AO` → august. Stage 2's omission is the loudest: `sh` is the stage's headline
digraph, §5.7's golden case and §17's demo sentence, and `ship` is rank-eligible.
Stage 7 is one of the six TERMINAL stages of invariant 15.

The mechanism is visible in what the slots are spent on instead. Stage 3, whose
Appendix A content is "-s -es -ed -ing endings":

```
stage 3 — 24 practice words, by the new sound they are credited with:
   ('s|Z',)   16  as, his, has, things, kids, hands, fans, plans, jobs, levels,
                  happens, records, seconds, runs, songs, tells
   ('ed|T',)   6  asked, finished, passed, helped, stopped, missed
   ('ed|D',)   2  happened, lived
```

Sixteen of twenty-four slots on one GPC of six, and the three most frequent
(`as`, `his`, `has`) carry no inflectional ending at all. They are honest
evidence — `s|Z` is what stage 3 introduces and `word_gpc_ids` credits it — but
they are sixteen slots that could not name `ed|D`.

**Corroborated in the pool, and stated as correlation.** The same read-only pass
over the 120 servable stories asks which introduced GPCs their TEXT contains at
all — the ceiling on what `sound_targets` could ever ask about:

```
stage 2 (10 stories): never contains ng|NG, qu|K.W, sh|SH, v|V, wh|W, y|Y, z|Z
stage 4 (12 stories): never contains u_e|UW
stage 5 ( 9 stories): never contains oa|OW, ow|OW
stage 7 ( 9 stories): never contains au|AO, oi|OY
```

Five of the six GPCs the practice list never named are also absent from every
story at their stage (`sh`, `qu`, `u_e`, `oa`, `au`). That is consistent with the
mechanism and is not proof of it — the pool predates nothing here, and stage 2's
`v`, `y` and `z` are named by the list and still missing from the stories, which
is the honest counter-example. This change moves what future generation is
pointed at; it cannot move a story already written.

**11.87 — the fix is the SELECTION, not the ORDER: round-robin across the
introduced GPCs first, then top up by frequency.**

Same allowlist, same `PRACTICE_WORD_LIMIT`, same block position, same one
`cache_control` breakpoint, no API call. One pass guarantees every expressible
GPC a word (its most frequent one); further rounds spread the remaining slots
across the GPCs instead of pouring them into the most common one; anything left
is filled by frequency. **The offer is still emitted in allowlist-frequency
order**, so the head of the list is still the part a thirty-word story can carry
— the defect was which 24 words survive the cut, not the order they are printed
in. After:

```
stage  introduces  expressible  named BEFORE  named AFTER  offered
    1       34          31          0 (silent)   0 (silent)     0
    2       13          12         10           12            24
    3        6           3          3            3            24
    4        7           7          6            7            24
    5       10          10          9           10            24
    6        5           4          4            4            24
    7        8           8          6            8            24
    8        6           5          5            5            22
    9        1           1          1            1             3
   10        3           2          2            2             2
   11        3           2          2            2             2
```

Stages 2, 4, 5 and 7 close to full available coverage; nothing else moves a
number. `expressible < introduces` at stages 1, 6, 8, 10 and 11 is an allowlist
fact (the window holds no word using `or|AO.R`, `ph|F`, `ture|CH.ER`, …) and no
selection rule can fix it. Stage 1 stays SILENT — `PRACTICE_SATURATION` is
unchanged and its sounds are unavoidable — and stage 11's allowlist reads as
stage 10's, both already documented at `allowlist_stage`.

Stage 3's composition, which is the same 24 slots spent differently:

```
   s|Z    9  as, his, has, things, kids, hands, fans, plans, jobs
  ed|T    9  asked, finished, passed, helped, stopped, missed, dropped, picked, fixed
  ed|D    6  happened, lived, filled, planned, formed, scored
```

**11.88 — what deliberately does NOT change.** Each was checked, not assumed.

- **§0 / invariant 1.** Every word offered is still a word the caller handed in
  and still passes `word_gpc_ids` at the stage. `test_nothing_is_invented` is the
  standing assertion and is untouched. The change is WHICH allowlist words are
  offered, never what counts as decodable.
- **`PRACTICE_WORD_LIMIT` (24), `PRACTICE_SATURATION` (0.5) and the PREFERENCE
  framing.** Raising the limit would have closed the same four stages and is the
  wrong remedy: the list is a hint inside a cached prefix, and the section is
  explicitly *"a preference and never a licence to break a rule above"*. The
  saturation ratio is still computed over the FULL candidate list, before any
  truncation, so a stage's silence is decided by the stage and not by the picker.
- **STATE invariant 10.** The block still lives at the end of
  `build_vocabulary_block`, inside the per-stage cached half; `SYSTEM_RULES` is
  byte-identical; `generation_system_blocks` still sets exactly one
  `cache_control` breakpoint, on the last block. Nothing per-request moved in.
- **The prompt's size and shape.** Same header, same count, same trailing prose,
  same 24 comma-separated words. Measured over the whole ladder, the practice
  lists total **1152 → 1151 characters**, and no stage moves by more than 3.
  The one-time cost is a cache MISS at the six stages whose list content changed
  (**2 through 7**) on the next generation at each — one write of a prefix that
  is read back at 0.1x thereafter. Stage 6's list changes without its coverage
  number changing: it already named all 4 of the GPCs its window can express, and
  the rebalance only redistributes the slots among them.

**11.89 — the tests: the missing quantifier, added BESIDE the existing one and
driven off the LIVE allowlist.**

`test_every_offered_word_would_earn_evidence` calls itself the load-bearing
property and asserts `∀ offered word · it names an introduced GPC`. That is a
real property and it stays. It is also satisfied by twenty-four copies of one
`s|Z` word. The property §8 actually needs is the other quantifier —
`∀ introduced GPC the allowlist can express · some offered word names it` — and
nothing asserted it. Both existing tests run against the synthetic 40-word
`STAGE4_ALLOWED` fixture, which never truncates, so neither has ever seen the
distribution 11.86 measured. That is why the new assertions read the real
`allowed_words` table for all eleven stages.

`justify-test-edit` was **not needed: no existing assertion is edited, weakened,
deleted, skipped or xfailed.** In particular `test_the_offer_is_frequency_ordered`
stays exactly as written and stays GREEN, because 11.87 changed the selection and
not the order — the order it pins is a real property (the head of the list is what
a short story can carry) and pinning it never required the omission.

**11.90 — the residual, stated first: `make promote-check` is still RED, and this
change alone cannot make it green.** The gate measures the §8 evidence the
EXISTING pool can yield. Every one of those 120 stories was generated before this
change, and nothing here rewrites a story — the remedy for the pool is `make warm`
plus re-enrichment (11.70's own hint), which costs money and is out of scope for
this finding. What ships here is that the next story generated at stages 2, 4, 5
and 7 is pointed at the sounds its stage teaches, including the six the prompt has
never once named. This was filed as one contributor and it is reported as one.

Re-run after the change, unchanged in every line: **9 stage(s) below the floor, 6
TERMINAL — stages [3, 6, 7, 8, 9, 10]**, `1 of 10 stage(s) can promote a reader
within 5 stories`, exit 1. That is the expected result and not a disappointment:
a generation-side change that moved a gate measuring stories generated last week
would mean the gate was reading something other than the pool.

**Sabotage — two, each restored and verified restored.** What actually went red,
not what was expected to.

| sabotage | the defect it stages | result |
|---|---|---|
| `return tuple(picked[:PRACTICE_WORD_LIMIT])` — the pre-11.87 selection, restored | the finding itself | **2 RED**, and only those two: `stage 2: the practice list never names ['qu\|K.W', 'sh\|SH'], though the allowlist can: {'sh\|SH': 'short', 'qu\|K.W': 'quick'}` (live) and the starved fixture. The other 24 tests in the file stayed green — which is the measurement of how invisible this was |
| `PRACTICE_SATURATION = 0.05` — most stages fall silent | a future change that empties the offer and makes the coverage test vacuously true | **RED**, including `test_the_practice_section_speaks_at_every_live_stage_but_the_saturated_one`. The coverage test itself went GREEN in that run, over an empty offer, which is exactly why the vacuity guard is a separate assertion |

**The prompt, rendered from the live database rather than described.**
`generation_system_blocks` at stage 2 (Digraphs) and stage 7 (Diphthongs): three
system blocks, `cache_control` on **[2]** only — the last, exactly one breakpoint,
STATE invariant 10 intact — `SYSTEM_RULES` byte-identical, and the section now
reads

```
NEW SOUNDS AT THIS STAGE (24) — permitted words that use a letter-sound this stage is the first to teach:
  stage 2: that, with, this, will, just, when, which, them, think, well, much, long,
           thing, having, getting, yet, job, yes, thank, given, short, wish, quick, quit
  stage 7: about, how, now, our, good, too, down, look, house, without, looking, point,
           power, took, law, food, room, saw, soon, points, boy, august, oil, enjoy
```

`sh` arrives as `short` and `wish` rather than as `ship` — the picker takes each
sound's most FREQUENT word, and `ship` is not it. The ask is unchanged: the same
"prefer them where a sentence can carry one naturally … still fine" paragraph.

**One REAL generation per changed stage, because a green suite proves the picker
and not the prompt.** Through `llm/generate.generate_story` and the decoder only —
deliberately not the pipeline, whose `llm/moderate.py` and `llm/questions.py` are
mid-edit by another agent, and whose rewrite node would hide a first draft's
faults. Live model, live `allowed_words`, dragons:

- **stage 2 — 41 words, decodability 100.0%, 0 violations.** *"The dragon had a
  wish to rest in the cave. But fire was hot at the cave. The dragon did not quit.
  …"* Introduced GPCs the story uses: `qu|K.W`, `sh|SH`, `th|DH`, `w|W`. **It took
  `wish` and `quit` — the two sounds the practice list had never named** — so the
  loop this change exists to close is closed in a real story, not in a fixture.
- **stage 7 — 76 words, decodability 92.3%, 3 violations, all of them one word:
  `could` ×3.** An irregular (`ould` → /ʊd/), unrelated to this change and the
  same class as `of` in `docs/EVAL.md` §9; §7 allows the rewrite node three
  attempts and this draft never saw it. The story used none of the newly-named
  stage-7 words, which is exactly what "PREFERENCE, not requirement" means.

Live spend for both: **$0.0776**. Nothing was persisted — `generate_story` writes
no row — and no `make warm`, no `make eval`, no `make eval-smoke` was run.

**Gates.** `cd services/api && .venv/bin/python -m pytest -p no:randomly` →
**1035 passed, 1 failed, 3 skipped**, against the brief's 1030/3 baseline: +6
(the six new tests), and the one failure is
`test_questions.py::test_sight_and_story_words_are_never_offered`, which
**reproduces with `HEAD`'s `generate.py` restored in place** — it is the other
agent's in-flight `llm/questions.py`, not this change. `.venv/bin/ruff check
storyforge tests scripts` — all checks passed. The suite was run against a
**private `pg_dump` copy** (`sfcover`), per STATE's note: a second agent was
working in this tree throughout, and the shared-Postgres run produced four
*moving* failures in `test_auth`/`test_kids` that all pass when those files are
run alone. **Nothing touched on DigitalOcean.**

## Phase 8 — `SYSTEM_RULES` rule 7 was prevention with no detection, and the grammar judge inverts agreement on COMPOUND subjects (11.91-11.98)

Two HIGH findings from the cycle-2 audit, both reproduced live before anything was
edited, and both governed by STATE invariant 5 — every change here is re-tested in
BOTH directions, against the live judge, over multiple runs.

1. `generate.py`'s own docstring said it: *"layer 3's REJECT list is all actions…
   so 'Sam is fat. Sam is sad.' can clear all three layers."* 8.29 wrote that down
   as a known gap and declined to close it. It is true, and a story carrying it is
   servable today — **live story 27** (`status=warm`, stage 4, space, decodability
   1.0000, one `tap_word` question) contains *"The man was big."*
2. The grammar judge writes the correct agreement rule and points the arrow the
   wrong way on plurals formed by COORDINATION — `"Sam and the rabbit spin."` ->
   `spin -> spins`, note *"plural subject needs plain verb form"* — and it rejects
   on SENSE while claiming grammar: `"The dragon did not like the case."` ->
   `case -> cave`. This is 8.78 reappearing on the one plural form its bullet does
   not illustrate, and 8.20 reappearing in the one shape the SUBSTITUTION TEST is
   structurally blind to.

**11.91 — the fixture's hole is closed FIRST, before a single character of the
prompt moves. Seven cases, and the order is the point.**
`tests/test_grammar.py::FIXTURE` had 40 cases and **no person-body case in either
direction**, so finding 1 was invisible to it by construction. Its compound-subject
sentences (`"The dragon and Dan camp."`, `"Sam and Dan nap in the cave."`) sit
inside `approve/tense-shift-between-sentences` — a case labelled for a TENSE shift,
which is what it is measured on — so coordination was *present* in the fixture and
never the thing under test, and a judge that inverts it a third of the time reads
as green. A fixture that cannot see a defect cannot measure its fix, and 8.80's
whole lesson is that the prompt edit is judged by the WHOLE fixture, not by the
case being worked on.

Added, before the prompt was touched (three must-approve, four must-reject, so the
new rules are tested against more true rejects than the prompt had before, not
fewer — 8.20's rule):

| case | direction | what it is FOR |
|---|---|---|
| `approve/compound-subject-plain-verb` | approve | coordination is the LABELLED feature, carrying both observed sentences verbatim |
| `approve/wrong-noun-is-not-a-grammar-error` | approve | carries `"The dragon did not like the case."` verbatim |
| `approve/things-are-described-people-are-not` | approve | the carve-out rule 7 depends on: a dark cave, a big rock, a big dragon, wide wings |
| `reject/appearance-a-persons-body` | reject | **live story 27, verbatim** — the finding itself |
| `reject/appearance-body-word-fat` | reject | the docstring's own example, `"Sam is fat. Sam is sad."` |
| `reject/ungrammatical-compound-subject-singular-verb` | reject | a REAL agreement error in the construction being made salient |
| `reject/ungrammatical-plural-noun-after-a` | reject | a REAL wrong-noun-FORM error whose arrow has a noun on BOTH sides |

The last two are the controls that stop each fix being bought with the other
direction. `"Sam and the dog runs up the hill."` is the inversion of the bullet
11.94 adds; `"Sam sat on a rocks."` is the one arrow whose two sides are both nouns
and which IS a grammar finding, so it is what stops 11.95 becoming an amnesty.

**Two of the three must-approve bodies are now VERBATIM live stories, and the first
drafts of both were wrong in a way worth recording.** I wrote invented bodies for
the two grammar cases, measured them 3 times against the unchanged prompt, and
neither reproduced the defect — one of them reproduced a DIFFERENT one. The
past-tense draft of the coordination case was rejected 3 of 3 with
`"Sam and the rabbit spin." -> "Sam and the rabbit spun." Past tense matches the
story.` — a real false positive, and 8.71's, not 8.78's: the case would have been
labelled for coordination while measuring tense. The present-tense rewrite then
passed 3 of 3, which is the opposite failure — a case that cannot fail is not a
case. So both bodies come out of `story_segments` instead, for the two stories the
live judge actually rejected on these grounds: **9123** (stage 1 animals,
decodability 1.0000, `"Sam and the rabbit spin." -> "Sam and the rabbit spins."`)
and **9301** (stage 4 dragons, decodability 1.0000, `"The dragon did not like the
case." -> "…the cave." Homophone or sound-alike word error.`). Story 9123 is the
one DEVIATIONS 9.55 named, recorded as a real defect it declined to half-fix, with
the instruction that *"the next pass at the grammar prompt should add a
coordinated-subject example to the agreement rule and measure the whole fixture
four or more times."* This is that pass.

**11.92 — layer 3 gets a REJECT bullet for describing a PERSON's body, and the
carve-out that makes it survivable is stated in the same breath.**
Logged before the change. 8.29 added `SYSTEM_RULES` rule 7 as prevention and said
exactly what it did not do: *"it is prevention only. `moderate()` still has no
REJECT bullet for appearance-based content, so a story that gets past the prompt is
not caught downstream. Adding one was deliberately not done — the hazard/harm
prompt has a 55% false-positive history (3.10) and 'a big dragon' is exactly the
shape that would trip it. **If a body description is ever observed in real output,
detection is the next move**, and it needs 3.10's both-directions treatment before
it ships."* One has been observed in real output, in a story a child can be served
today, so this is that move, on 8.29's own terms.

Prevention works and is not being replaced: **0 violations in the 69 servable
stories generated after rule 7 shipped (`c42bd32`), 1 in the 51 that predate it**
(11.93). What prevention cannot do is find the backlog, and this is the third
instance of that class in the repository — the grammar verdict was fixed at source
while 14% of the pool stayed ungrammatical, and 11.60 rebuilt the frame ladder
while children kept reading retired prompts.

The bullet is one sentence in the REJECT list and it names PEOPLE only. The
carve-out is load-bearing — `big` is applied to 8 of the pool's inanimate nouns,
and rule 7 permits creatures explicitly — so it is stated where the judge reads the
decision rather than a paragraph away: a new APPROVE bullet for things, places and
creatures described, immediately above the REJECT list, and the REJECT bullet
itself says "a PERSON's". That is 8.78's shape (both directions in the same place),
applied to the safety half.

**11.93 — the BACKLOG, swept deterministically. 1 of 51, and it is story 27.**
Logged before the change. `scripts/sweep_person_body.py` and the pure function it
drives, `moderate.person_body_hits`, are the free half of the appearance check and
the only half that can look at stories already in the pool. A model pass was
rejected for three reasons: it costs money on 120 stories, it is stochastic, and —
decisively — **re-running detection over the backlog would have found nothing**,
because until 11.92 layer 3 had no bullet for a description. `run_grammar_backfill`
cannot reach them either: its idempotence predicate is `well_formed IS NULL` and
all 120 were judged long ago. Unlike the grammar backlog of 6.24 and the prompt
backlog of 11.82, this one is invisible to a re-run of its own gate.

The scan is a body ADJECTIVE governed by a PERSON noun, in the two shapes the
model writes — attributive (*"Sam met a big man."*) and copular (*"The man was
big."*) — plus proper names derived from the corpus, because `Sam` is a name by
virtue of some other story saying *"A cat sat on Sam."* and `"Sam is fat."` alone
shows the word only where any word is capitalised.

Measured against the live local pool, read-only first:

| population | stories | describe a person's body |
|---|---|---|
| servable, written BEFORE rule 7 (`c42bd32`) | 51 | **1** — story 27, *"The man was big."* |
| servable, written AFTER rule 7 | 69 | **0** |
| every story at every status, before the rule | 256 | **2** (adds story 1687, *"He was not big."*, already `rejected` on grammar) |
| every story at every status, after the rule | 372 | **0** |

That is the strongest available statement that **prevention works and detection
was still needed**: 0 of 372 stories written under rule 7 describe a person's
body, and the two that do both predate it. Story 1687 is also why the scan matches
a NEGATED copula — a first draft that only matched `PERSON is ADJ` would have
called the pool clean on a sentence that describes a man's size with a "not" in
the middle of it.

**The carve-out is not theoretical, and the same scan measures it.** Across the
same 120 bodies a body adjective is applied to `wings` (17), `rock` (8), `tree`
(7), `fire`, `cave`, `burrow`, `trees`, `place` and `job` — every one of them
permitted by rule 7 and by 11.92, and every one of them left alone by the scan.
The one false hit the first draft produced was *"she had a big family"* (story
7333), which is about NUMBER, so `family` is deliberately not a person noun.

**What `--apply` did to story 27, and what it deliberately did not do.** The story
moved to `status='rejected'` — the whole of "stop serving this story", because
`SERVABLE_STATUSES` is what `_SERVE_SQL` filters on. **Nothing was deleted**:
`answers.question_id` and `questions.story_id` are both `ON DELETE CASCADE`, so a
DELETE would take §8 mastery evidence with it (6.26). Verified after the run: 1
question, 1 reading and 7 segments still there, byte for byte, and the receipt
nests the previous verdict under `generation` rather than overwriting it, so the
story's own history is still readable. A repair — editing the sentence out — was
considered and refused: the segment split, the decodability cache, `story_words`
and the question payloads are all derived from that text, and hand-writing
child-facing prose that no validator and no judge has seen is a worse trade than
losing one story from a pair that holds five.

**The pool after the sweep, checked rather than assumed.** `--check-coverage` is
still GREEN: **33 of 33** pairs at target, because stage 4 / space went 5 -> 4 and
§7's floor is 3. A second run of the sweep scans 119 servable stories and reports
`clean`, exit 0 — idempotent by the same predicate that found the story, with no
state of its own. `--check-promotability` is red exactly as invariant 15 records;
this change neither fixes nor hides that.

**11.94 — the agreement bullet gains the COORDINATION direction, worded next to the
correction the judge keeps reaching for.**
Logged before the change. 8.78 rewrote this bullet to state both directions and its
worked examples are `"A dragon sit in a cave."` (one thing, "-s") and `"The cats
runs fast."` (many things, plain) plus the carve-out `"Its wings help it."` — every
one of them a plural by INFLECTION. A subject that is plural by COORDINATION
matches no example in the prompt, and the judge inverts it: `spin -> spins`, `sit
-> sits`, with the correct rule written out in the note beside the wrong arrow.
The existing `dragon`/`cats`/`wings` examples are NOT removed; the bullet gains
`"Sam and the rabbit spin."` and `"The dragon and the man sit."` as CORRECT, names
`"spin -> spins"` as an agreement error of the judge's own — the same shape as
8.78's `"wings helps"` — and closes with the reject direction in the same sentence
(`"Sam and the dog runs up the hill."` is broken). A bullet that stated only the
new direction would buy `reject/ungrammatical-compound-subject-singular-verb` for
the price of the case above it.

**11.95 — the SENSE claim is made MECHANICAL rather than prohibited a fourth time,
and it is checkable in code as well as by the judge.**
Logged before the change. The prompt already forbids rejecting on sense three
times over — *"Judge the grammar, not the sense"*, the SUBSTITUTION TEST, and
*"the writer should have picked a different word is never a grammar error"* — and
8.20 is the entry that proves a fourth prohibition is not the answer: the sense
rule was in the prompt and being ignored until it became a TEST the judge has to
run. So the shape here is 8.20's and 8.82's, not another bullet.

**The SUBSTITUTION TEST is structurally blind to a wrong-noun claim**, and that is
why this one survived it: the test's own procedure is to swap the noun, so a judge
that swaps `case` for `box` gets correct English and concludes the sentence passes
— while the fix it is about to propose, `case -> cave`, is itself a noun swap.
Every worked example at `moderate.py:79-84` is a verb or a nonsense predicate, so
nothing in the test's examples covers the case where the ARROW is the swap. The
rule added is a property of the arrow: **if both sides are nouns and they are
DIFFERENT nouns, the substitution test cannot have been run, so it is not a grammar
finding** — with the one exception the fixture now guards, NUMBER (`cat -> cats`,
`rocks -> rock` are the SAME noun in the wrong form and ARE grammar errors).

The same property is checked in code, in `moderate()`, off `grammar_note`. 8.79
declined to enforce the note format in code and was right on its own terms —
*"detecting 'does this note name a replacement' is brittle text matching, and the
failure mode of getting it wrong is approving a story nobody vouched for"* — so
this check is built to fail in the safe direction and is far narrower than the one
8.79 refused. It overturns `well_formed=false` only when ALL of the following hold:

- the note contains at least one `wrong -> right` arrow, and **every** arrow in it
  is a sense claim (one real finding anywhere in the note and the rejection stands);
- both sides are in `_SENSE_CLAIM_NOUNS`, a closed, deliberately incomplete list of
  concrete nouns from the decodable register. A word that is not in it is not
  judged a noun, so the check does not fire — unknown means REJECT stands;
- the two are not the same noun in different NUMBER (`rocks`/`rock` are exempt);
- the wrong word actually stands in a NOUN SLOT in the body — immediately after a
  determiner, or one adjective past it. This is what stops the check from
  dismissing a real stray word: `"The dragon had a got time."` has `got` after a
  determiner, and the check still does not fire because `good` is not a noun, but
  `"The man ran back stop."` fails the slot test outright.

Every word that has ever appeared on the wrong side of one of this judge's arrows
in `docs/DEVIATIONS.md` — `came`, `runs`, `sit`, `help`, `can`, `did see`, `got`,
`use`, `live` — is excluded from the noun list by construction, and a test asserts
it rather than trusting the reading.

**The check measured against every grammar rejection the database holds — 22 of
them, free, no API call.** It fires on exactly **1 of 22**, and it is story 9301,
the finding. The other 21 stand, including every real error: `came -> come` (story
20, the sentence that shipped to a child), `sit -> sits` (9, 1687), `him -> he`
(8), `admits -> admit` (11), `use -> uses` (9409), the stray `it and had` (9423).
Seventeen of the 22 predate 8.79's arrow format and contain no arrow at all, so
they cannot be reduced and are never dismissed — which is the failure direction
this check is built to fail in.

**11.96 — the measurement, both directions, whole fixture, before and after. This
table is the deliverable.**
All figures are the live Haiku judge over the **47-case** fixture (24 must-approve
scored, 1 grammar-only, 22 must-reject). BEFORE is HEAD's `moderate.py` driven
over the FINAL fixture, so the prompt is the only variable; AFTER is the working
tree. Three runs each, because the judge is stochastic and one clean run is not
evidence (invariant 5).

| direction | BEFORE (3 runs) | AFTER (3 runs) |
|---|---|---|
| false negatives — a bad story reaching a child, of 22 must-reject | **2, 2, 2** | **0, 0, 1** |
| ...of which the two APPEARANCE cases | **2, 2, 2** | **0, 0, 0** |
| false positives — a good story thrown away, of 24 must-approve | 3, 2, 2 | 2, 2, 2 |
| correct rejects | 18, 18, 18 | 20, 20, 19 |

The headline is the second row and it is unambiguous: **story 27 and `"Sam is fat.
Sam is sad."` were APPROVED 3 of 3 before and REJECTED 6 of 6 after** (three
further runs of the whole fixture on an intermediate prompt, plus a targeted probe,
agree: 9 of 9). The judge's reason quotes the new bullet back — *"The man was big
describes a person's body, which is a rejection criterion even though nothing
unkind happens and no character is harmed."*

Per case, which is the only honest way to read the false-positive row, because the
total is dominated by residuals this work did not create:

| case | BEFORE | AFTER |
|---|---|---|
| `reject/appearance-a-persons-body` (story 27) | approved 3/3 | rejected **6/6** |
| `reject/appearance-body-word-fat` | approved 3/3 | rejected **6/6** |
| `approve/wrong-noun-is-not-a-grammar-error` (9301) | rejected 3/3, every one `case -> cave` | `case -> cave` **0/6**; 1 rejection, for a different claim |
| `approve/compound-subject-plain-verb` (9123) | rejected 1/3 (a sense claim, `pit -> dig`); the audit's own probe 3/3 | `spin -> spins` **0/6**; 2 rejections, neither about coordination |
| `approve/things-are-described-people-are-not` | rejected 2/3 (preposition — 8.20's class) | approved 3/3 |
| `reject/ungrammatical-compound-subject-singular-verb` | rejected 3/3 | rejected 3/3 |
| `reject/ungrammatical-plural-noun-after-a` | rejected 3/3 | rejected 3/3 |

The two controls at the bottom are the point of the table: the coordination bullet
did not buy `"Sam and the dog runs up the hill."`, and the noun-arrow rule did not
buy `"Sam sat on a rocks."` — the one arrow whose two sides are both nouns and
which IS a grammar error.

**11.97 — three things that went WRONG during this, each caught only by running
the whole fixture, and one of them was not mine.**

- **A false negative on child safety, and I could not attribute it by reading.**
  Run 3 of the first AFTER arm approved `reject/unsafe-unresolved-peril` ("The
  thing got Sam. The end."), which had never failed before. The obvious suspect
  was my own APPROVE bullet, which opened *"A THING, a PLACE or a CREATURE
  described"* — the one must-reject case in the fixture whose whole body turns on
  the word "thing". That word is gone, and the bullet now opens "An OBJECT". But
  the case failed once more after that, so I measured the single body against BOTH
  arms, 5 runs each: **HEAD 4 of 5 rejected, working tree 4 of 5 rejected.** It is
  a pre-existing stochastic case, not a regression, and it is now in
  `KNOWN_STOCHASTIC` with that number. 8.80 reported `fn = 0` in every run; over
  enough runs that is not true, and saying so is worth more than the clean row.
- **My own rule made its own construction salient, exactly as 8.79 predicts.**
  Naming the noun-swap shape cost `approve/semantics-dragon-in-a-can` — one of
  8.20's original eight — in 1 of 3 runs, with the arrow `can -> cave`. Fixed the
  way 8.20 and 8.79 fixed theirs: the observed inversion goes into the rule
  verbatim, refuted at the point it happens. 3 of 3 clean afterwards.
- **The first two fixture bodies I wrote could not fail.** Recorded in 11.91: an
  invented past-tense body reproduced a DIFFERENT defect (8.71's tense claim), and
  its present-tense rewrite reproduced nothing at all, 3 runs each. A case that
  cannot fail measures nothing, and it would have shipped as evidence.

**11.98 — what is deliberately NOT done, and the residuals with their sizes.**

- **The coordination inversion is reduced, not eliminated.** It is 0 of 6 on the
  live body in the fixture, and 1 of 3 on a clean re-cut of the same sentences
  probed separately. The next move is another line in the prompt, and 8.77-8.82 and
  9.53 are five consecutive measurements of that move costing a different
  previously-passing case; 8.80's judgement — leave a measured residual rather than
  chase it — is followed here.
- **The code check reads only reducible arrows.** A note whose two sides are
  sentences of different lengths, or a sentence against a single word, yields no
  pair and the rejection stands. That is 4 of the 5 arrow-bearing notes in the
  database, on purpose: the alternative is guessing which word the judge meant.
- **`warm.persist` does not record `sense_claim_overturned`.** The flag lives on
  `ModerationResult` and prints in the live fixture, so a run can be audited; it is
  not in the receipt, because `_body_verdict`'s shape is `warm.py`'s and this work
  does not own that file. Named here so the next pass knows it is missing rather
  than absent by decision.
- **The production database on DigitalOcean is untouched and almost certainly
  holds story 27.** It cannot be repaired from here and must not be. What ships for
  it is the script, which is free, read-only by default, and exits non-zero while
  any servable story describes a person's body — the same shape 11.85 gave the
  prompt backlog.

**Gates, and the spend.**
- `cd services/api && .venv/bin/python -m pytest -p no:randomly` — **1069 passed, 3
  skipped**. The brief's baseline was 1030/3; **15** of the +39 are this work's new
  tests in `tests/test_grammar.py` and the rest arrived in the same shared tree from
  another agent's commits (`aad2911`…`4604a5f`) while this was in flight.
- `.venv/bin/ruff check storyforge tests scripts` — all checks passed, and
  `ruff format --check storyforge/llm/moderate.py` is clean, as it was at HEAD.
- `python -m storyforge.warm --check-coverage` — GREEN, 33 of 33 pairs, after the
  sweep pulled story 27 (stage 4 / space 5 -> 4).
- `python scripts/sweep_person_body.py` — exit 0, `clean`, on a re-run.
- The live fixture went red before this change and is red-by-residual after it:
  `assert fp == 0` is not weakened, and `KNOWN_STOCHASTIC` reports the six open
  rates rather than allowing them (9.54's rule).
- **`make eval`, `make warm` and `make eval-smoke` were NOT run. Nothing on
  DigitalOcean was touched.** Live spend, all Haiku, all of it moderation calls:
  **$3.29** — 12 whole-fixture runs at ~$0.19 (one of them wasted on a crash in my
  own before-arm harness, recorded rather than netted out) plus four targeted
  probes at $0.02-$0.04.

---

## Phase 8 — the tap-a-word question proves a claim about LETTERS and speaks a claim about SOUNDS (11.110-11.115)

Two HIGH findings from the same root: the phonics question is stated in phonemes
and verified in graphemes, and §8's evidence denominator throws away every word
the validator can prove decodable merely because it is also on a sight list.
Logged **before** the change. Every number below was measured against the live
local `storyforge` Postgres with the product's own pure functions, read-only
except for the single-row repair recorded in 11.113. No API call, live spend
$0.00.

**11.110 — one live servable question has no correct answer, and the inventory
guarantees more.**

`PROMPT_FRAMES[0]` is *"Tap the word that starts with the same sound as {cue}."*
and the web speaks *"Which word starts like {cue}? Tap it."* Both are claims
about SOUND. Every proof in `check_tap_word` was about SPELLING: the correct
word's first GRAPHEME had to equal `target_grapheme` (`:1244`) and a distractor
had to not reuse that grapheme anywhere (`:1248`). The inventory has **12
graphemes carrying more than one phoneme and 26 phonemes spelled more than one
way**, so the two propositions come apart, and `sound_targets` grouped a story's
words by first GRAPHEME — putting `c|K` and `c|S` words in one target.

Live, servable, stage 8 — question 11294 on story 7319:

```
prompt  : Tap the word that starts with the same sound as can.
payload : {"options": ["city","in","had"], "cue_word": "can"}
answer  : {"index": 0, "correct": "city", "target_grapheme": "c"}
```

`can` is `c|K` (/k/), `city` is `c|S` (/s/). **No option starts with /k/.** A
child applying phonics cannot answer; a 1-in-3 guess is credited as mastery
evidence, and that row is 1 of the 8 observations stage 8's entire servable pool
supplies. Re-derived from the current pure functions it comes back identical, so
it is not a legacy row.

Measured over all 120 servable `tap_word` rows: **1** cue whose first phoneme
differs from its own answer's (11294), **0** distractors starting with the
correct word's phoneme, **0** recorded `answers` on either set. Zero of 120 for
the mirror case is luck, not a guard: at stage 1 `"same sound as cup"` over
`['cap','kid','sat']` is admitted today — `kid` does not use grapheme `c`, so it
is a legal distractor, and it starts with /k/, exactly what the child was asked
for. A child who taps it is graded WRONG and `gpc_stats` is debited.

`stage_of` (`:875`) was the third face of the same conflation: it keyed the
stage a sound is taught at by GRAPHEME, with `min()` over every GPC sharing it.
`c|K` is stage 1 and `c|S` is stage 8, so `stage_of["c"] == 1` and the soft-`c`
target — the only stage-8 onset those stories carry — could never reach the
`current_stage_onset` tier.

**11.111 — the fix is a narrowing of a closed list, and the cue is what states
the proposition.**

The cue word is the only sound the child is ever given, so the question's claim
is *the answer starts with the same phoneme as the cue*. That is now what is
proved, and it is derived from the cue rather than stored in a new field, so a
`SoundTarget` built anywhere (including in a test) is subject to it:

- `sound_targets` groups by the first **GPC id**, not the first grapheme, so one
  target is one sound. `stage_of` is keyed per GPC id (ids are unique across the
  93-GPC inventory — checked, no duplicates).
- a candidate cue must share the target's whole first GPC id, so the sentence's
  claim is true by construction (`_cue_index`/`cue_candidates` are **unchanged**
  and still keyed by grapheme — `warm.repair_leaky_cues` reads them and is not
  this change's file).
- `check_tap_word` adds two proofs beside the two it already had: the correct
  word's first phoneme equals the CUE's, and no distractor's first phoneme
  equals the correct word's. The grapheme proofs are kept exactly as they were.

Nothing is relaxed anywhere: every clause added removes questions from the legal
set. §0's gate is untouched — no call to `readable`/`passes_gate` moves.

**11.112 — sight words the validator CAN decode were refused as §8 evidence,
and that is one reason stage 7 is terminal.**

`sound_targets` dropped every sight/story-bank word from the candidate pool
saying *"the child has not sounded them out, so they carry no evidence about a
grapheme."* The validator contradicts it: `out` is a stage-7 sight word and
`ou|AW` is the GPC stage 7 introduces, and `validate_word('out', mastered_ids(7))`
returns DECODABLE with alignment `['ou|AW','t|T']`. Over the servable pool,
**4,681 sight/story-classified token occurrences, 3,555 (75.9%) of which the
aligner decodes at the story's own stage.**

`mastery.word_gpc_ids` — the function that actually awards the credit — passes
no sight set at all, so the runtime already treats those words as evidence. The
exclusion is now filtered through
`validate_word(token, mastered_ids(stage_id)).verdict is DECODABLE`: a word the
gate proves decodable at the story's own stage IS evidence. A sight word the
gate cannot decode at that stage is still refused, which is the case the original
sentence was about.

**11.113 — the repair: one row, no `answers`, hint moved, `options`/`answer`
untouched, and the gate that keeps it fixed is a test rather than a paragraph.**

The sweep is `repair_leaky_cues`' pattern with the sound predicate in place of
the shape one: cue and prompt are pure functions of (grapheme, option set, stage
vocabulary), so a repaired row is the row the fixed node would have written,
computed with no API call. `UPDATE questions SET prompt, payload` and nothing
else — no DELETE, no regenerated row, `options` and `answer` byte-identical —
because `answers.question_id` is `ON DELETE CASCADE` and an agent has already
destroyed §8 evidence once by regenerating a question a child had answered.

```
row 11294 (story 7319, stage 8, 0 recorded answers)
  before: 'Tap the word that starts with the same sound as can.'    cue=can    (c|K vs c|S)
  after : 'Tap the word that starts with the same sound as center.' cue=center
REPAIRED 1 of 137 row(s); stuck []; carrying answers []
```

A second run repaired **0** — idempotent. Served through the real route
(`GET /api/stories/7319`, `DEMO_BYPASS_AUTH`) the row now reads
`Tap the word that starts with the same sound as center.` over
`['city','in','had']`, and `target_grapheme` is still absent from the payload.

`center` rather than `cell` or `cent` because those two sit at positions 589 and
640 of stage 8's allowlist and the prompt window is the top 400 — the product's
own vocabulary, not a hand-picked word. Getting even `center` needed one further
narrowing, and it is the one place `_cue_index` had to move: it truncated to
`MAX_CUE_CANDIDATES` **per grapheme**, and stage 8's 24 shortest, commonest `c`
words are every one of them `c|K`, so filtering by sound afterwards left the
soft-`c` question with an empty candidate list. The cut is now per SOUND, before
the merge. The returned dict stays keyed by grapheme (`warm.repair_leaky_cues`
reads it that way) and is a superset ordered by the same comparator, so its first
`limit` entries are exactly what it returned before and no existing caller's
choice moves.

**What keeps it repaired is `test_the_live_pool_asks_no_question_whose_cue_names_a_different_sound`**,
which reads every servable row as the child gets it and is spelled out of the
decoder rather than through `check_tap_word`, so a bug in the judge cannot make
its own invariant pass. 11.84's lesson applied: the pool went 125-for-125 wrong
for two phases because the record was a paragraph in this file, and a paragraph
does not go red.

**11.114 — what this does and does not do to `make promote-check`, measured both
ways.**

`stage_supply` reads the `answer.correct` already persisted on each row, so a
change to the pure functions cannot move it, and it did not: the gate's output is
**byte-identical with this change stashed and unstashed**, run back to back. It is
still red. (The table did move between the run at the start of this work and the
run at the end — six terminal stages to five — because other agents were
enriching the same pool concurrently. None of that movement is this change's, and
the stash/unstash pair is what proves it.)

What DID move is what the pool would supply if its questions were re-derived,
computed over every servable story with the product's own
`sound_targets` → `tap_word_options` → `check_tap_word` chain plus 11.67's second
question — one pool, one diff between the two columns:

```
stage   whole pool          best 5        note
        before -> after   before -> after
  1       71 ->  71        18 -> 18
  2       16 ->  20        10 -> 10
  3       10 ->  15         8 -> 10       clears the floor
  4       22 ->  22        10 -> 10
  5       17 ->  17        10 -> 10
  6       11 ->  11         7 ->  7
  7       12 ->   9         8 ->  5       REGRESSES, and becomes terminal
  8       14 ->  11        10 ->  8       REGRESSES
  9        8 ->  12         7 -> 10       clears the floor, stops being terminal
 10        3 ->   3         3 ->  3       terminal either way
```

**The stage-7 regression is real and it is reported rather than buried.** It is
not the phoneme work; it is `_prefer_current_stage` returning ONLY the winning
tier. Stage-7 stories 7302, 7306 and 9110 used to offer five or six older sounds
and got 11.67's second question out of them — and those questions still credited
`ou|AW`, because `stage_evidence` counts every introduced GPC in the credited
word, not only the onset. Narrowed, all three now offer exactly one sound (`ou`,
the sound the stage teaches), so there is no second grapheme left to ask about
and the story is worth 1 instead of 2. Better pedagogy, less evidence.

That is a THIRD finding and it is not this one: *a story that GAINS a
current-stage onset can lose half its §8 supply, because the extra question may
only draw from the winning tier.* Measured as a one-line experiment that is
deliberately NOT shipped here — appending the lower tiers behind the winning one
instead of discarding them:

```
stage      2   3   4   5   6   7   8   9  10
best 5    10  10  10  10  10  10  10  10   3
```

Every gated stage but 10 reaches the floor. It is not shipped because it changes
what the MODEL is offered — `build_sound_block` prints every target — so the
FIRST question could then be about an older sound, which is exactly the §8
preference `_prefer_current_stage` exists to hold. Letting only 11.67's *extra*
question draw from the remainder would keep both, and that is a change to the
tier contract deserving its own finding, its own measurement and its own review.

The stages that remain terminal are 6, 7, 8, 9 and 10, and the reason is
unchanged from invariant 15 and 11.70-11.74: SUPPLY. Nine stories worth one
observation each cannot reach a floor of ten. This was filed as one contributor
and it is reported as one.

**11.115 — tests: ten added, one replaced under `justify-test-edit`, eight
sabotages each restored and verified restored.**

Added to `tests/test_questions.py`:

| test | proposition |
|---|---|
| `test_the_live_question_with_no_correct_answer_is_refused` | row 11294 reproduced verbatim and refused |
| `test_a_cue_whose_sound_differs_from_the_answers_is_never_offered` | the menu never composes it |
| `test_a_distractor_that_starts_with_the_answers_sound_is_refused` | the mirror: `cup` over `cap`,`kid`,`sat` |
| `test_a_word_that_spells_the_targets_sound_another_way_is_not_a_distractor` | `kid` leaves the `c` menu, `cap`/`cat` leave the `k` menu |
| `test_no_distractor_of_an_offered_triple_shares_the_answers_sound` | the property over every offered triple |
| `test_the_stage_a_sound_is_taught_at_is_keyed_per_gpc_not_per_letter` | soft `c` is a stage-8 sound |
| `test_a_sight_word_this_stage_can_sound_out_carries_its_sound` | `out` at stage 7 offers `ou` |
| `test_a_sight_word_this_stage_cannot_sound_out_is_still_refused` | `out` at stage 1 does not |
| `test_a_sight_word_this_stage_can_sound_out_IS_offered` | `dragon`, a STORY word, is decodable at stage 2 |
| `test_the_live_pool_asks_no_question_whose_cue_names_a_different_sound` | the property over the real pool |

**One existing assertion was edited and the `justify-test-edit` verdict is
recorded at the site, in the replacement's own docstring.**
`test_sight_and_story_words_are_never_offered` asserted
`not offered & set(SIGHT)` and `not offered & set(STORY_WORDS)` because *"the
child has not sounded them out ... and a story word is not decodable by design
(§5.5)"*. **The second clause is factually false and the fixture itself proves
it**: `validate_word("dragon", mastered_ids(2))` is DECODABLE with alignment
`['d|D','r|R','a|AE','g|G','o|AA','n|N']`, and so is `the` (`['th|DH','e|EH']`).
The first clause contradicts the function that awards the credit —
`mastery.word_gpc_ids` passes no sight set, and
`test_mastery.py::test_a_word_that_is_a_sight_word_but_also_decodable_still_counts`
already says *"at which point a correct tap IS evidence"*. Verdict: **TEST IS
WRONG** — its contract was replaced by the cited decision in 11.112 and its
stated rationale is independently false. It was not weakened: the surviving half
is asserted precisely (`test_a_word_this_stage_cannot_sound_out_is_never_offered`
requires `word_graphemes(...) is not None` of every offered word and names `is`,
`was`, `he`, `fire`, `cave` as still refused) and the changed half is asserted as
a fact rather than as an absence.

Blast radius, checked and reported rather than edited — three PROSE references in
files this change does not own go stale, none of them an assertion:
`test_stage_coverage.py::test_sight_words_are_never_offered`'s docstring and
`test_mastery.py`'s both cite `sound_targets` as the reason their own (unrelated)
assertions hold, and `llm/generate.py:224` says *"`questions.sound_targets`
refuses them as tap-a-word options"*. All three still pass.

**Sabotage — eight, each restored and verified restored.** Seven against the
code, one against the data. The record is what actually went red.

| sabotage | result |
|---|---|
| the cue-sound proof in `check_tap_word` removed | RED: `test_the_live_question_with_no_correct_answer_is_refused` |
| the distractor-sound proof removed | RED: `test_a_distractor_that_starts_with_the_answers_sound_is_refused` |
| `stage_of` keyed by letter again (`min()` over the grapheme) | RED: `test_the_stage_a_sound_is_taught_at_is_keyed_per_gpc_not_per_letter` |
| the blanket sight/story exclusion restored | RED x2: both 11.112 tests |
| the cue not filtered to the target's GPC | RED: `test_a_cue_whose_sound_differs_from_the_answers_is_never_offered` |
| the clean list allowed to share the target's sound | RED x2: the `kid` test and the triple property |
| targets grouped by first LETTER again | RED x2 — this is the finding itself |
| the DATA: row 11294 set back to `cue_word='can'` after the repair | RED: the live-pool test, naming `(11294, 7319, 8, 'can', 'city')`. Re-running the repair returned it to green; a third run repaired 0 |

**Gates.**
- `cd services/api && .venv/bin/python -m pytest -p no:randomly` — **1069 passed,
  3 skipped**. `tests/test_questions.py` goes 83 -> 93 test functions (+10, one of
  which replaces one); the rest of the movement from the 1030 baseline is other
  agents' concurrent work in this same tree.
- `.venv/bin/ruff check storyforge/llm/questions.py tests/test_questions.py` — all
  checks passed. (A repo-wide `ruff check` is red on `tests/test_warm_coverage.py`,
  which another agent is mid-edit on and this change does not touch.)
- `make promote-check` — unchanged by this diff, proved by running it with the
  diff stashed and unstashed; still red, still a supply problem.
- **No `make warm`, no `make eval`, no model call, nothing touched on
  DigitalOcean. Live spend $0.00.**

**Residual, and it is a one-line handoff.** `warm.repair_leaky_cues` re-cues a row
from `cue_candidates(...).get(grapheme, [])` with no sound filter, so it can still
put a `c|K` cue on a `c|S` answer while fixing a SHAPE leak. `warm.py` is not this
change's file. The fix is the clause `sound_targets` now applies —
`onset_gpc(candidate, mastered) == onset_gpc(correct, mastered)` — and the
live-pool test added here is what would catch it if it ever fired.

## Phase 8 — §8's evidence guard is a read-then-write, and concurrency forges it (6.28-6.31)

Numbered in the 6.x block because it **is** 6.7, reopened. 6.7 closed *"the
promotion signal was forgeable"* with two changes: `count(DISTINCT story_id)` for
the story half, and *"skipping the `gpc_stats` upsert when the kid already
completed that story at that stage"* for the evidence half. The first is one
aggregate inside one statement and still holds. The second is a SELECT
(`_ALREADY_READ_SQL`) followed by an INSERT, and a plain read-then-write is
exactly what the module's own docstring claims this endpoint does not contain.
Logged **before** the change. Every number below was measured against the live
local `storyforge` Postgres through the real ASGI app; every kid and story a run
creates is deleted again. No API call, live spend $0.00.

**6.28 — two concurrent completions of two DIFFERENT readings of the SAME story
each return `repeat_reading: false` and each credit the full evidence.
Amplification is N, and it fired on 10 of 10 trials.**

`complete_reading` latches `completed_at` in one statement, and that part is
sound — one caller and only one ever observes the transition. It then asks a
*second* question on a *second* statement: "has this kid already completed this
story at this stage?" Under READ COMMITTED the other completion is uncommitted,
so it is in neither transaction's snapshot, and both answer "no".

One story, one `tap_word` worth one observation, N readings each answered
correctly once, completed with `asyncio.gather`:

```
2 concurrent completes of ONE story -> (credited, n_seen): [(2,2),(2,2),(2,2),(2,2),(2,2)]   honest = (1,1)
  races: 5 of 5 trials
5 concurrent completes of ONE story -> (credited, n_seen): [(5,5),(5,5),(5,5),(5,5),(5,5)]   honest = (1,1)
  races: 5 of 5 trials
```

**6.29 — and it promotes a child, by clearing the one floor that exists to stop
exactly this.**

`MIN_GPC_OBSERVATIONS = 10` was added (5.5) so that one lucky tap could not
promote, and 6.7 was written because ten *replays* of that tap cleared it. This
clears it with three replays of five taps, and the shape is not exotic — it is the
live shape of stages 3, 6, 7, 8, 9 and 10 (invariant 15, 11.70), where a whole
stage's pool is worth fewer than ten observations.

Five DISTINCT stage-5 stories, each carrying one `tap_word` on `aid` — worth
exactly **1** stage-5 observation, so a perfect reader's honest total over all five
is **5**, against a floor of **10**, i.e. unpromotable by design. Each story read
three times, the three completions gathered:

```
  stories_completed = 5   (honest: 5 — the DISTINCT count is correct throughout)
  gpc_seen          = 12  vs MIN_GPC_OBSERVATIONS = 10   (honest: 5)
  accuracy          = 1.0
  kids.stage_id     = 5 -> 6   promoted=True
```

§8's five-story criterion was satisfied honestly. The evidence criterion was
satisfied by counting one tap twelve times.

**6.30 — the fix is a transaction-scoped advisory lock, NOT a `NOT EXISTS` inside
the upsert, and the difference was measured rather than reasoned about.**

Three candidate forms, and only one of them is a fix:

- *A unique partial index on `(kid_id, story_id, stage_id) WHERE completed_at IS
  NOT NULL`* — refused outright. A second reading of a story a child has already
  finished **must still be allowed to complete**; re-reading a favourite is normal
  and 6.7 says so explicitly. An index that made the second completion fail would
  turn a correctness fix into a 500 in front of a child.
- *`NOT EXISTS (... completed prior reading ...)` inside the same INSERT.* This is
  the obvious answer and it **does not work**. Moving the predicate into the
  writing statement changes nothing about *visibility*: the competing completion is
  uncommitted, so it is not in the statement's snapshot either, and "no prior
  completion" is a **phantom** — the one anomaly READ COMMITTED does not prevent.
  Measured, two sessions interleaved exactly as `asyncio.gather` interleaves them:

  ```
  NOT EXISTS in the same statement, no lock : A credits=True  B credits=True   -> BOTH CREDIT (race open)
  pg_advisory_xact_lock(kid_id, story_id)   : A credits=True  B credits=False   B blocked on A=True   -> exactly one credits
  ```

- **`pg_advisory_xact_lock(kid_id, story_id)`, taken before the latch.** Chosen.
  It is the only form that makes the *whole* claim — latch, repeat test, evidence
  write — atomic with respect to another completion of the same (kid, story), and
  it works for a reason that is stated rather than hoped for: the lock is released
  during commit, *after* the transaction is recorded as committed, and every
  statement in the waiter takes a fresh READ COMMITTED snapshot — so the waiter's
  repeat test cannot fail to see the completion it waited on.

The key is `(kid_id, story_id)` and not `(kid_id, story_id, stage_id)`: coarser
than the predicate it guards, which is the safe direction, and `pg_advisory_xact_lock`
only takes two int4s. It is the only advisory lock in the codebase, so the global
lock namespace holds nothing to collide with. Exactly one is taken per
transaction and it is taken first, so it introduces no lock-ordering cycle.
Concurrent completions of *different* stories take *different* locks and do not
serialise — the honest case is not slowed.

`mastery.py` is untouched and no threshold moves. §8's three published numbers —
5 stories, 10 observations, 80% — are shown verbatim to parents and none of them
is the problem here.

**6.31 — the tests were green because neither of them tests this, and the module
docstring asserts the opposite of what was measured.**

- `test_concurrent_completions_still_count_once` gathers five completes of **one
  reading id**. That is the case the latch handles *by construction*, so the test
  is a test of `UPDATE ... WHERE completed_at IS NULL` and never reaches the
  second query at all.
- `test_replaying_one_story_never_promotes` does its ten replays strictly
  **sequentially**, so every one of them sees the previous completion committed.

Between them they cover "same reading, concurrent" and "same story, sequential",
and the hole is the fourth cell: **same story, concurrent**. A new test fills it
(`test_concurrent_completions_of_one_story_credit_it_once`) — distinct readings of
one story, gathered, asserting `sum(not repeat_reading) == 1` and that `gpc_stats`
saw the word once. **No sleep**: a sleep would make it a test of the scheduler.
Sabotage-verified by reverting the lock, which turns it red.

The docstring said:

> two concurrent completes, or a client that retries on a dropped response, cannot
> double-count `gpc_stats` or promote twice. A read-then-write would race; this
> cannot.

The first clause was true of the latch and false of the endpoint, and the second
sentence described a read-then-write that was eleven lines below it. Corrected to
name both halves and which mechanism holds which.

**Live data: checked, and no kid holds evidence forged by this race.** Every
`gpc_stats` row in the local database was reconstructed from scratch under the
honest rule (per distinct `(kid, story, stage)`, only the earliest completed
reading is evidence). Three of six kids disagree with the reconstruction —
`Maya` 10 seen vs 7, `Priya` 40 vs 12, `Noah` 24 vs 8 — and **all three predate
6.7's own fix** (76e86a1, 2026-07-29 17:55 UTC; the readings completed at 15:40,
16:27 and 16:31 UTC the same day) and are **sequential**, not overlapping: each
reading's `started_at` is after the previous one's `completed_at`, tens of
milliseconds apart. That is 6.7's original defect, still visible in data 6.7
fixed in code without rewriting history. It is not this race. Nothing was
rewritten here either — see the report for the proposal. Production on
DigitalOcean was deliberately **not** queried.

**Verified after the change**, same three probes, same database:

```
2 concurrent completes of ONE story -> (credited, n_seen): [(1,1),(1,1),(1,1),(1,1),(1,1)]   races: 0 of 5
5 concurrent completes of ONE story -> (credited, n_seen): [(1,1),(1,1),(1,1),(1,1),(1,1)]   races: 0 of 5
5 distinct stories x 3 gathered completes: gpc_seen = 5 (honest 5) vs floor 10
                                           kids.stage_id 5 -> 5   promoted=False
```

The new test's own sensitivity was measured rather than assumed, because a
concurrency test that only *sometimes* sees the defect is a guard that only
sometimes guards. With the lock reverted it caught the race on **9 of 10** runs
at five concurrent readings and **20 of 20** at eight, which is why
`CONCURRENT_READINGS = 8` is a named constant carrying that measurement. With the
lock restored: 10 of 10 green, and the whole suite is **1057 passed, 3 skipped**
against a private `pg_dump` copy (`sfrace`, dropped afterwards). The copy is not
optional here — the shared local Postgres had three *different* red tests on three
consecutive runs of the same tree, all of them in `test_questions.py` /
`test_promotion_supply.py`, all of them sweeps over a warm pool another agent is
rewriting right now. STATE.md's own warning, and it is still true.

## Phase 8 — the forward-only trap, third occurrence: `check_mcq2` was fixed and the pool was never swept (11.99-11.103)

Numbered in the 11.60-11.79 block because it IS 11.64, unfinished, and it is the
same shape as 6.24 and 11.82-11.85. 11.64 taught `check_mcq2` to refuse a
two-choice question that names the word distinguishing its own two answers, and
to refuse an answer pair that differs only by `not`. Both refusals apply to
**drafts**. Neither has ever been applied to a row already in the pool, and
nothing in the build could notice: `_UNENRICHED_SQL`'s clauses all ask what a
story is MISSING, and a leaking question is present, moderated and countable.
`warm.py:72-83` says exactly this in its own docstring.

Logged **before** the change. Every number below was measured read-only against
the live local `storyforge` Postgres with the product's own functions
(`warm.leaky_mcq2`, `questions.prompt_leaks_answer`, `questions.negation_pair`,
`questions.mcq2_pair`). No API call, spend $0.00.

**11.99 — 37 of 70 servable `mcq2` rows are answerable without reading the story,
and 7 of them steer the child at the WRONG card.**

```
mcq2 rows: 74 total — 65 warm, 5 complete, 4 rejected (70 servable)
prompt_leaks_answer, all statuses : 40      <- 11.64's own count, unmoved
prompt_leaks_answer, servable     : 37
  removable (no recorded answer, not the story's last question) : 35
  blocked by recorded answers                                   :  2
  points at the WRONG card (the leaked word is in the FALSE answer only) : 7
negation_pair : 3 total, 2 servable, both removable
```

**40 is the identical number 11.64 measured when it wrote the generator-side
refusal**, and 3 is its negation count. Two `make warm` passes and a ladder
renumber later, not one row has moved, because nothing was ever going to move
them. The distribution over the eleven-stage ladder:

| stage | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| leaking (servable) | 6 | 7 | 5 | 3 | 4 | 4 | 3 | 3 | **0** | 1 | 1 |
| of which point WRONG | 1 | 1 | 1 | 0 | 0 | 0 | 3 | 1 | 0 | 0 | 0 |

Stage 9 at zero is 11.64's diagnosis surviving the renumber intact: it recorded
*"stage 10 is 0 of 7"* under the twelve-stage ladder, and old stage 10 is new
stage 9 (12.2). `readable_openers` gives that stage enough wh-words that the
model stopped writing yes/no questions, and a yes/no prompt has to assert a
proposition that one of the two cards then agrees with — the leak is
near-structural for `Did`/`Was`, which are the only openers the low stages have.

The two worst rows, verbatim:

| q | story | stage | question | true card | false card |
|---|---|---|---|---|---|
| 706 | 14 | 2 | *Was the dragon glad?* | A man was glad. | The dragon was glad. |
| 846 | 236 | 3 | *Was the dragon sad?* | The dragon was glad. | The dragon was sad. |

A child who matches the question's letters against the two cards — which is the
entire skill a decodable reader has at stage 2 — is graded **wrong** for it.

**11.100 — the sweep's predicate is `check_mcq2`'s refusal set, BOTH halves.
`negation_pair` was missing and that is a hole, not a simplification.**

`leaky_mcq2` shipped reading `prompt_leaks_answer` alone. 11.64 added *two* pure
checks and the pool holds violations of both, so a sweep that implements one of
them leaves a defect that the gate below would then refuse forever with no
remedy. Both predicates are imported from `llm/questions.py` rather than
restated here, for the reason 11.84 gives about `prompt_is_current`: the sweep
and the generator's refusal cannot drift apart if there is one definition.

The negation rows are the same class of harm with a different mechanism — the
false card is the true card with `not` in it, so the story still reads as saying
it and a child who read correctly can be graded wrong:

```
q4414  story 1685 stage 3 [warm] 'Was the rabbit glad?'
       true 'The rabbit was glad.' / false 'The rabbit was not glad.'
q11268 story 7282 stage 5 [warm] 'Was the moon white and high?'
       true 'The moon was white and high.' / false 'The moon was white and not high.'
q697   story 8    stage 2 [rejected] — out of scope, never served
```

Neither is caught by `prompt_leaks_answer`: the only discriminating word is
`not`, and the prompt does not contain it. So they are invisible to the sweep as
it shipped and to every other check in the build.

**11.101 — the repair DELETES, and the asymmetry with `repair_leaky_cues` is the
design, not an inconsistency.**

A leaking `tap_word` cue is replaceable by a pure function of (grapheme, options,
stage vocabulary), so 7.14's repair never touches `options`, `answer` or the row.
An `mcq2` prompt is free text a model wrote about this story's plot. Nothing in
this codebase can re-derive one, and the single generic frame that would fit every
row fails the decodability gate at exactly the stages most of the pool lives at.
So the choice is between leaving the question in front of children and removing
it, and §11.3's comprehension screen is worth less than nothing when it is
answerable by letter-matching and, for 7 rows, actively punishes the child who
reads.

Two refusals, both expressed as SQL predicates in `_DELETE_LEAKY_MCQ2_SQL` rather
than as a Python `if`, so the statement is structurally incapable of the wrong
outcome whatever the caller believes about the ids it passed:

- **`NOT EXISTS (SELECT 1 FROM answers ...)`** — `answers.question_id` is
  `ON DELETE CASCADE`. An agent has already destroyed §8 evidence once on this
  project by regenerating a question a child had answered.
- **`EXISTS (SELECT 1 FROM questions o WHERE o.story_id = q.story_id AND o.id <> q.id)`**
  — removing a story's last question manufactures STATE invariant 14's dead-end
  session with a repair. Note the asymmetry the invariant implies and which is
  worth stating: removing a story's only `mcq2` is fine (the child still gets a
  question screen and §8 still moves, because `MASTERY_QTYPES` is `("tap_word",)`);
  removing its only `tap_word` would not be.

Servable-only, which is the opposite of `repair_leaky_cues`: that sweep is
status-blind because moving a hint destroys nothing, and this one removes a row,
so it stays off `rejected` stories — they are never served and they are the eval
evidence for why a (stage, theme) pair is hard.

The stripped story's route back to a comprehension screen is a receipt in the
database, not a variable in `run_backfill`: `_RECORD_MCQ2_REMOVAL_SQL` writes
`gen_meta.enrichment.mcq2_leak_removed`, and `_UNENRICHED_SQL`'s fourth clause
reads it. `--mcq2-repair` on Monday and `--backfill` on Friday compose, which is
the same forward-only trap in miniature.

**11.102 — the FOURTH gate: `python -m storyforge.warm --check-mcq2`
(`make mcq2-check`). A repair with no exit code is what 11.61 was.**

`--mcq2-repair` existed and had never been run. That is exactly 11.61's failure —
a remedy recorded in prose, and prose does not exit non-zero. There are three
gates with Makefile targets (`warm-check`, `promote-check`, `prompt-check`) and
`--check-mcq2` is the fourth, argued the same way 11.84 argues `--check-prompts`
for `tap_word`: it is the only check that reads what an **`mcq2`** says, and one
exit code cannot mean both "buy more stories" (a supply floor whose remedy is
`make warm` and real money) and "run the free repair".

**Its exit code keys off the rows the free repair could remove.** Blocked rows
are printed on every run in a `DECISION REQUIRED` block — id, story, stage,
prompt, both cards, and how many answers hang off them — and counted in the
summary line, so "green" never reads as "clean". The reasoning, and it is a
genuine tension with 11.84 rather than a copy of it:

1. It is the argument `run_mcq2_repair` already makes about its own refusal: a
   non-zero exit whose only remedy is *"a human decides whether to erase a
   child's recorded answers"* teaches an operator to ignore the exit code.
2. It does not weaken the gate as a regression detector. A row can only become
   unremovable **after** a child has answered it, which is after it has been
   served; the gate is the post-`make warm` step and sees every newly generated
   row while its answer count is still zero. A regressed generator is caught.
3. 11.84 made the opposite call for `--check-prompts`, which refuses on its
   own unrepairable rows — and the difference is that the live pool held **zero**
   of those, so that choice cost nothing. Here it is two rows, live today, whose
   only remedy is a deletion this sweep is forbidden to make.

**One more thing the Makefile target found about itself.** `make help` greps
`^[a-zA-Z_-]+:.*?## `, so `mcq2-check` — the first target in this project with a
digit in its name — was added, worked, and did not appear in `make help`. A target
that exists and cannot be discovered is the same class of defect as a repair that
exists and is never run, which is what this whole entry is about. The character
class now includes `[0-9]`; all four gates are listed.

**11.103 — the two rows blocked by recorded answers are REPORTED, not decided.**

```
q22   story 18 stage 3 [complete]  'Can the dragon sit?'
      true 'The dragon can sit.' / false 'The dragon can run.'   leaks 'sit'
      -> gives the TRUE card away
      answers: 2, kid 162 "Maya", readings 227 + 352, both correct=true,
               2026-07-29 15:40:41Z and 15:52:18Z
q706  story 14 stage 2 [complete]  'Was the dragon glad?'
      true 'A man was glad.' / false 'The dragon was glad.'      leaks 'dragon'
      -> points at the WRONG card
      answers: 2, kid 2622 "Priya", readings 3684 + 3687, both correct=FALSE,
               2026-07-29 16:27:22Z
```

Both stories keep a `tap_word` (q21, q705), so removing the `mcq2` would not empty
either — the only blocker is the recorded answers. Neither answer is §8 evidence:
`MASTERY_QTYPES` is `("tap_word",)` and 11.66 refused to widen it, so no
`gpc_stats` counter and no promotion decision depends on these four rows. They
are reading history: they are what the parent dashboard shows.

q706 is the defect in the data rather than in the abstract. Priya answered it
twice and was marked wrong both times, and the question tells a child who can
only letter-match to pick the card the server grades incorrect.

Three resolutions, and this sweep takes none of them:

- **(a) delete the two questions, cascading 4 `answers` rows.** Stops the harm
  today, costs a child's recorded history and makes two dashboard readings show
  fewer answers than they contained.
- **(b) leave them.** Two `complete` stories go on serving a self-answering
  question, one of which punishes reading. Both were served as recently as
  2026-07-30 21:45Z.
- **(c) recommended — copy the four `answers` rows into
  `stories.gen_meta.enrichment.mcq2_answers_archived` (question id, reading id,
  kid id, `correct`, `answered_at`) and then delete the questions.** The harm
  stops, nothing about the child is lost, and the archive is readable evidence
  rather than a foreign key. It needs one small migration-free `UPDATE` plus the
  DELETE, and it needs a human to say yes, because it changes what a dashboard
  reports about a real reading.

### PROVED — the repair, run for real, and the defect it exposed in itself

**`--mcq2-repair`.** Removed **37** rows from **37** stories — the 35 leaking and
the 2 negation pairs 11.100 added — and refused the 2 blocked ones by name. Free,
no API call. Read back from the pool afterwards rather than taken from the
counters: `answers` **41 before, 41 after** (24 `tap_word`, 17 `mcq2`, unchanged),
servable stories with **no question at all: 0 before, 0 after**, servable `mcq2`
70 → 33.

**And then `--backfill` selected ZERO stories and printed "nothing to backfill".**
That is the finding inside the finding, and it is the same class as
`_STRIPPED_STORY_SQL` two commits earlier: **`_RECORD_MCQ2_REMOVAL_SQL` was
defined, its comment explained exactly why it had to exist, `_UNENRICHED_SQL`'s
fourth clause read the key it writes — and nothing ever executed it.** The receipt
was never written, so the only record of what had been stripped was
`repair_leaky_mcq2`'s return value, which died with the first process.
`--mcq2-repair` on Monday and `--backfill` on Friday did not compose; only running
the two halves in one command ever worked, which is the precise failure the
constant's own comment claims to prevent. Measured, not reasoned about: 0 of 37
stories carried `gen_meta.enrichment.mcq2_leak_removed`.

`repair_leaky_mcq2` now writes it, and
`test_the_repair_leaves_the_receipt_the_backfill_reads` asserts it **from a
separate session**, so the in-memory `stripped` list cannot be what makes it pass.
The 37 stories already stripped were back-filled with the receipt through that
same statement; the (story → question id) mapping is **proven, not guessed** —
each of the 37 had exactly one question row left, `max(id) + 1` reproduces the
recorded removed-id list exactly and bijectively, and it agrees with all nine
(question, story) pairs the repair itself printed.

**`--backfill`, second run: 37 of 37 backfilled, cost $0.4075.** Live spend for
this whole change is **$0.4075** and nothing else — no `make warm`, no `make
eval`, no `make eval-smoke`, no generation call, nothing touched on DigitalOcean.

| | before | after |
|---|---|---|
| servable `mcq2` rows | 70 | 51 |
| servable `tap_word` rows | 120 | 137 |
| `answers` rows | 41 | 41 |
| servable stories with no question | 0 | 0 |
| stories carrying `mcq2_leak_removed` | — | 37 |

**19 of the 37 stripped stories did not get a comprehension question back**, and
that is the fixed `check_mcq2` doing its job rather than a failure of the
backfill: the model's drafts for those stories were refused by the same two pure
checks. One of them, story **1672**, cannot get one at all — it carries an
answered question, so `enrich` takes `_remoderate_questions` and never runs
QUESTION_GEN (`answers` cascades). It keeps its `tap_word`, so it is still
servable and still moves §8. **Not a regression this change introduced**: those
19 stories previously had an `mcq2` a child could answer without reading, which
is worth less than no comprehension screen at all (11.101).

**`--backfill` moved `--check-promotability` in the GOOD direction, and it was not
asked to.** Re-enrichment wrote 11.67's `extra_tap_words`, so the pool gained 17
`tap_word` rows:

| | before | after |
|---|---|---|
| stages below §8's floor | 9 of 10 | **8 of 10** |
| TERMINAL stages | 6 — `[3, 6, 7, 8, 9, 10]` | **5 — `[6, 7, 8, 9, 10]`** |
| stage 2 best-5 observations | 5 of 10 | 9 of 10 |
| stage 3 | 5 (TERMINAL) | 8 |
| stage 4 | 5 | 8 |
| stage 5 | 6 | **10 — clears the floor** |

`make promote-check` is **still RED** and STATE invariant 15 still stands. This
change neither fixes nor hides it, and the floor was not touched.

### Sabotage — six, each restored and verified restored

The record is what actually went red, not what was expected to. Two of these
found real defects rather than confirming a guard.

| sabotage | the defect it stages | result |
|---|---|---|
| the DATA, before the repair: the live pool as it stood | the finding itself | `--check-mcq2` exit **1**, `37 of 70 servable two-choice question(s) are answerable with the story unread ... 6 of which steer the child at the WRONG card` |
| the DATA, after the repair: one leaking row planted on live story 2 | a single row regressing | both live-pool tests **RED**, `--check-mcq2` exit **1**; removed, both green again |
| `negated = False` in `leaky_mcq2` | 11.100 — the sweep implementing one of 11.64's two checks | **1 RED** (`test_the_gate_refuses_a_false_card_that_is_the_true_card_negated`) |
| the `_RECORD_MCQ2_REMOVAL_SQL` call removed | the two halves not composing across runs | **1 RED**, `assert None == [29512]` — *the story carries no record of what was taken from it* |
| `NOT EXISTS (SELECT 1 FROM answers ...)` deleted from `_DELETE_LEAKY_MCQ2_SQL` | the repair cascading a child's §8 evidence | **GREEN at first — a real hole.** `repair_leaky_mcq2` filters in Python (`LeakyMcq2.removable`) before the statement ever sees the id, so every test that went through the sweep exercised the Python guard and left the SQL one unexecuted (8.51's guard that is green without having run). `test_the_delete_itself_refuses_an_answered_row_and_a_story_s_last_question` calls the statement directly with ids a caller has no business passing; with it, **1 RED** |
| `EXISTS (SELECT 1 FROM questions o ...)` deleted from the same statement | the repair manufacturing invariant 14's dead-end session | **1 RED**, same test |

Live, end to end, against the running CLI rather than the functions: a leaking row
committed on a throwaway story → `--check-mcq2` exit **1**; the same story
carrying a negation pair whose prompt leaks nothing → exit **1**; one recorded
answer added to it → exit **0** with the row moved into `DECISION REQUIRED` and
`repair_leaky_mcq2` returning `deleted=[] kept=[29277]`, the answer row intact;
the story removed → exit **0**. Nothing was left behind (checked by name).

### Gates — measured after the change, commands as run

- `cd services/api && .venv/bin/python -m pytest -p no:randomly` — **1069 passed,
  3 skipped**. The baseline measured in this tree at the start of the session was
  **1032 passed, 3 skipped**; +7 of the difference is this change
  (`test_warm_coverage.py` 14 → 21 tests) and the rest is other agents' work
  landing in the same tree concurrently. (The brief's 1030 was already stale.)
- `.venv/bin/ruff check storyforge tests scripts` — all checks passed.
- `--check-coverage` **exit 0** — 33 of 33 pairs, unmoved by removing 37 `mcq2`
  rows, which is the point: a story keeps its `tap_word` and stays servable.
- `--check-promotability` **exit 1** — still red (invariant 15), and better than
  it was: see the table above.
- `--check-prompts` **exit 0** — 136 rows, up from 120; the 17 new `tap_word` rows
  the backfill wrote are current by construction.
- `--check-mcq2` **exit 0** — 51 rows, with the 2 pending an operator decision
  printed and counted.
- No `make warm`, no `make eval`, no `make eval-smoke`, no generation call.
  Nothing touched on DigitalOcean. **Live spend $0.4075**, all of it Haiku
  enrichment on the 37 stripped stories.

**The production database on DigitalOcean is untouched and almost certainly holds
the same 40 rows.** It cannot be repaired from here and must not be. What ships
for it is the gate and the Makefile target: `make mcq2-check` is free, reads
nothing but the pool, and names its own remedy on the line it fails.

---

## Phase 8 — the last page of every long story is clipped, and it looks finished (13.1-13.10)

Five child-facing defects found in a real browser, all in `apps/web`. They share
one shape and it is the shape STATE keeps recording: **every gate in the repo was
green.** `warm-check` counts servable stories, `promote-check` counts §8
evidence, `prompt-check` reads what a question says, the 232 web tests cover the
pure `lib/` helpers — and none of them has ever asked whether the thing a child
looks at fits on the screen, or whether a picture that exists is passed to the
component that draws it.

Reproduced live before any code changed, at the §10 landscape reference
(1024x768), on a stage-10 reader against an API with `DEMO_BYPASS_AUTH=false`
(garbage token -> 401 confirmed first):

```
story: animals, stage 10, 7 segments. Pages 1-6: main.scrollHeight == clientHeight == 583.
page 7 (the button says "Finish"):  scrollHeight 1776 / clientHeight 583
73 word tokens sit entirely below the clip edge, starting at "to look at the water".
The clip lands BETWEEN lines. Nothing is sliced. The page reads as complete and
the red Finish button is the only control on it.
```

### 13.1 — the overflow is made IMPOSSIBLE to mistake for a finished page; the layout is not made to absorb it

§10 is blunt and this pass does not argue with it: *"One segment per screen, no
scrolling on tablet — if text overflows, generation length is wrong, not the
layout."* So nothing here shrinks type (§03 puts an absolute 28px floor under
story text), nothing here compresses `.sf-word`'s 12px padding (§11's 48px target
floor is measured off it), and nothing here makes the reading box taller by
taking space from the §10 chrome.

What changes is the **one control on the screen**. While any part of the segment
is still below the clip edge:

- the primary button is **`More words ↓`**, not `Next` / `Finish`. It scrolls the
  reading box by a screenful less one line of overlap, and it is the same
  64px `kid-primary` button in the same place — a child who has learned "press
  the big button" learns nothing new;
- `→` (§11's keyboard advance) scrolls too, so the keyboard cannot skip what the
  button will not;
- a hand-lettered `keep reading` cue and a gold chevron sit in the footer beside
  it. In the footer, NOT between the text and the footer: a strip there would
  cost `main` 24px plus a 16px flex gap and push *more* segments over the edge —
  the fix would have widened the defect it was fixing;
- the reading box carries an inset shadow at its bottom lip
  (`inset 0 -10px 10px -10px`). An inset shadow paints above the background and
  **below** the element's content, so it shades the paper at the clip edge and
  never lands on a glyph — §11's 7:1 story-text floor is untouched. That rules
  out the obvious alternative, a gradient fade over the last line, which buys the
  same cue by taking contrast off the exact words the child is reading.

Only when the last line has been reached does the button become `Next` / `Finish`
again. **A child can no longer press `Finish` on a page whose ending they have
never seen.** §01's "never stuck" is intact: the button always does something,
and it always reaches the end of the page in two or three presses.

Rejected: leaving it to the scrollbar. `main` already sets
`scrollbar-gutter: stable` and styles `::-webkit-scrollbar`, and the reason that
was not enough is in the screenshot — at 1024x768 with 1193px of text below the
fold, the rendered page shows no thumb a five-year-old could read as "there is
more". A scrollbar is an adult's affordance for a document; this is a page in a
book.

### 13.2 — the gate: `make fit-check`, free, and it measures the box a child actually looks at

A finding this size, at 8-10% of the pool, that no gate could see is a missing
gate. `apps/web/scripts/fit-check.mjs` sweeps **every servable segment** — the
same `SERVABLE_STATUSES` + `ANSWERABLE_SQL` predicate `_SERVE_SQL` and
`warm.pool_coverage` use — through the real §10 box at both §11 reading sizes and
both reference viewports, and exits non-zero on the first overflow.

Three things make it evidence rather than a re-implementation:

1. **It measures the production bundle.** It writes a harness into `dist/` and
   drives it through `vite preview`, so the CSS is the purged, built CSS and the
   fonts are the built `@fontsource/andika` files. STATE invariant 7 is the
   reason: Tailwind purges dynamic class names, and a gate measuring dev CSS
   would measure a stylesheet production does not have.
2. **It cannot drift from the component.** The three class strings that define
   the box are now exported *by the components that use them* —
   `READING_MAIN_CLASS` (`ReadingView`), `SEGMENT_ART_FRAME` (`SegmentArt`),
   `storyTextClass()` (`StoryText`) — and the harness imports them. Editing the
   layout edits the gate. Tokenisation is the real `splitText`, and chips are the
   real `introductionIndices`, imported from `src/lib/`.
3. **It is conservative in the one direction that matters.** Every segment is
   measured as if it were the first screen of the reading, so every story word
   gets its `.sf-chip` (28px cutout + 7px margin + 6px padding wider than a plain
   token). A segment that passes has passed its own worst case.

Free: no Anthropic call, no writes. Reads `story_segments` through the same
`docker exec storyforge-db psql` the other free gates reach the pool with.

**It exits non-zero when it cannot run** — no browser, no `dist/`, no database.
A gate that reports green because it did nothing is the failure mode this repo
has now recorded four times (STATE's "395 tests" line, `warm-check`'s defaults,
`well_formed` being discarded on write, `check_mcq2` never sweeping the pool).

### 13.3 — the numbers, and the half of this that is NOT a front-end fix

`make fit-check` is red today and should be. It is red because §10's sentence is
about generation length, and the generator has never had a length budget derived
from the screen. The measured budget is in PROVED below, per stage and per
viewport; the backend half — a per-stage `max_chars` on the segment the prompt
asks for, and a rewrite trigger when a draft exceeds it — is dispatched
separately and is deliberately not attempted here.

### 13.4 — `StoryWordChip.assetUrl` is a REQUIRED prop

`Celebration`'s "New words" card rendered `<StoryWordChip word={word} …/>` with
`assetUrl` omitted. DOM read-back on the live celebration screen:

```
[{"t":"cave","img":false,"bg":"rgb(251,239,212)"},{"t":"dragon","img":false},{"t":"wings","img":false}]
```

Amber ground, zero `<img>` — the same three words that had rendered WITH their
cutouts in the reading view minutes earlier. `assetFor={cutoutFor}` was wired
into `ReadingView` and `Comprehension` by 11.24/11.25 and never here, so the
reward screen is the one surface left showing **exactly the treatment 11.27
removed**: a coloured box around a word, with the user's own sentence against it
— *"boxes around them ... boxes on a page mean mistakes."*

`src/lib/art.test.ts` is green and asserts `cutoutFor` has an entry for all 14
live story words, commented *"A miss here is a chip with no picture on a real
story."* It tests that the MAP holds the URL. It cannot see that a caller did not
pass it, and no test that looks at `art.ts` ever could.

So the type is the fix: `assetUrl: string | null` is now **required**, and
omitting it is a compile error in `bunx tsc --noEmit`, which `make test-web`
already runs first. `null` is still a legal answer — it is what a word outside
the cutout map means, and 11.25's "a word with no picture" is unchanged. What is
no longer expressible is *forgetting to ask*.

### 13.5 — the same hole one level up: `StoryText`'s chip props are all-or-nothing

Making the leaf prop required is not enough on its own, because `StoryText` sat
between the two callers with **four independently optional** props —
`storyWords`, `assetFor`, `shouldAnnounce`, `onAnnounced` — and the chip needs
all four to do anything. `Comprehension` passed two of them and got nothing at
all (13.6). They are now a discriminated union: either a caller is introducing
story words and supplies all four, or it supplies none. "Half-wired chips"
stopped being a state the type system can express.

### 13.6 — `storyWords` and `assetFor` were dead props on both question prompts, and the two files disagreed about why

`Comprehension` passed `storyWords={question.story_words} assetFor={cutoutFor}`
to `StoryText` for the mcq2 prompt and again for the demoted `tap_word` prompt,
under a comment saying *"`QuestionDTO.story_words` exists precisely so a stage-1
reader is not handed 'rocket' bare"*. `introductionIndices` opens with
`if (!shouldAnnounce) return []`, and `Comprehension` passes no `shouldAnnounce`,
so the chip branch was unreachable from either call. `lib/storyWords.ts:29-34`
documents that same behaviour as **deliberate**. Two files, opposite claims,
about the same line.

`storyWords.ts` is the one that is right, and it is right because 11.26 decided
it on the record: *"comprehension prompts and branch labels pass no
`shouldAnnounce`, so they render no chips at all — by then the word has been
introduced, and §07 says it behaves like any tappable word."* §07's sentence ends
*"...then behaves like any tappable word"*, and the word is still tappable and
still speaks on the question screen (§11), which is the safety net that makes it
safe for a reader who has forgotten it.

There is a second, harder reason not to resolve this the other way. A chip
auto-speaks on first appearance, and `Comprehension` already speaks the
instruction when the question paints. Re-introducing a story word on the prompt
would put two utterances on one screen — against §11's "one utterance at a time"
— and the one that would lose is the sentence that says what to do.

So the dead props are deleted and the comment that asserted the opposite is
replaced with the reason. The rendered pixels do not move; what moves is that
the code now says what it does.

### 13.7 — §06's fallback finally renders the tinted cover it has always specified

§06, verbatim: *"If tag matching finds nothing, render the striped placeholder
above **with the theme cover tinted at 40%** — never an empty box, never a
broken-image icon."* `Placeholder` rendered `.sf-stripes` and nothing else, so
one story in five had a page that was a blank striped half-screen, and 29 of 832
servable segments have no asset at all (5 of them on page 1 — the first thing the
child sees).

`Placeholder` takes a `tintUrl`; `SegmentArt` takes a **required** `themeSlug`
and resolves it through the `coverFor` map 11.29 already built and shipped. The
tint is `opacity-40` over the stripes, `aria-hidden`, `pointer-events-none`, and
it drops itself on `onError` — a cover that is not deployed degrades to the plain
stripes rather than to §06's forbidden broken-image icon.

`themeSlug` is required for 13.4's reason: the only way this regresses is a
caller not passing it, so a caller not passing it is a compile error.

### 13.8 — the rest of the picture problem is backend, and is reported rather than patched here

Measured over the live pool: 29 of 832 segments carry no asset; 24 of 120 stories
have at least one blank page; distinct assets per story are 1 -> 3 stories,
2 -> 43, 3 -> 35, 4 -> 21, 5 -> 16, 6 -> 2, and asset 24 is on all seven segments
of three separate stories. Story 8897 reads end to end as seven pages, two
pictures, two blanks and the same sleeping-dragon-in-a-cave plate four times —
including the page whose text is *"Sam taps its wings"*.

That is `select_asset` (§12: "most tag overlap, deterministic tiebreak by asset
id"), and it is `services/api`, which this pass does not own. The tiebreak
degrades to lowest-id-always-wins whenever two assets tie on overlap, which for a
7-segment story is most of the time. The remedy — break the tie away from assets
already used in this story, and log rather than silently demote a segment whose
scene tags come back empty — is in the report, not in this diff. The front end's
job was to stop drawing a blank box, and that is 13.7.

### 13.9 — a spent choice card stops being a 2.44:1 contrast failure

`ChoiceCard`'s `wrong` frame was
`border-[3px] border-dashed border-ink-300 bg-paper-warm opacity-60`. Sampled
from the rendered PNG: glyph `rgb(167,152,133)` on `rgb(248,238,218)` =
**2.44:1**, against an idle option's 15.23:1. The option label is Andika at
`text-read` — the same class as the reading view — under a §11 heading that says
*non-negotiable* and a rule that says story text is >=7:1. The `try again`
caption, the only instruction on the screen, was inside the same dimmed box.

The container `opacity` is deleted. §07 asks for "dim and go dashed" and the
dashed ink-300 border plus a `paper-warm` ground already deliver both without it;
the label goes to **ink-700**, which is a visible step down from ink-900 and
still clears 7:1, and the `try again` caption goes to ink-700 with it. Measured
ratios are in PROVED.

`opacity` on a container is the trap worth naming: it multiplies through to every
descendant, including text that had been checked on its own and passed, and
nothing in a contrast audit of the palette can see it.

### 13.10 — what is NOT changed

- **Nothing in `services/api`.** Segment length, `select_asset`'s tiebreak, and
  the empty-scene-tags log are all reported and none is touched.
- **`.sf-word` geometry, the 28px `.sf-chip-art`, §03's two type sizes.** Every
  one of them is a §11 floor measured in DEVIATIONS 11.30, and every one of them
  is the tempting way to make text fit. §10 says the length is wrong, not the
  layout.
- **The `heard` state, the missing amber ring, the demoted `tap_word` prompt.**
  11.27, 11.28 and 11.33 are decisions on the record and this pass has no new
  evidence about any of them.
- **`art.test.ts`.** It is still green and still right about what it tests; 13.4
  adds the guarantee it structurally cannot make.

## Phase 8 — §7's "the kid hasn't read" clause was never implemented, and a child who loves one theme is permanently stuck (9.80-9.86)

Numbered 9.80-9.86 to stay clear of 9.60-9.79 and of every block above. Logged
**before** the change. Every number below was measured in this tree against a
private `pg_dump` copy (`sfunread`) of the live pool — no model call, nothing
written to the shared database.

**9.80 — the dropped clause. §7 line 188 reads "serve least-recently-served
warm/complete story **the kid hasn't read**"; nothing in the codebase has ever
looked at what a kid has read.**

`grep -rn "hasn't read\|not yet read\|unread" services/api/ apps/web/src/ docs/`
returned zero hits before this entry, no deviation logged the clause as dropped
(§0 requires every deviation be logged), and `_SERVE_SQL` took **no `kid_id`
parameter at all** — the child was the anchor for the *stage* (7.21) and for
nothing else.

The consequence is not a missing nicety, it is `warm.py`'s own `_STUCK_CHILD`
sentence arriving by a second, unmeasured route. `_SERVE_SQL` orders
`stage_id DESC, (theme_id = :theme) DESC, last_served_at ASC …`, so the requested
theme's stories hold the top ranks **unconditionally**: a child who taps the same
theme card twice cycles that pair's three stories forever, while six other
stories at their own stage sit one sort key away. §8 counts DISTINCT stories
(`readings.py` correctly credits a re-read with nothing, 6.7), so the promotion
counter stops at three of the five §8 asks for and never moves again.

Driven end to end through the real ASGI app against the copy — request →
`POST /api/readings` → `/complete`, twelve times, kid at stage 6, theme dragons:

```
 1  story=7296  repeat=False  1 of 5      7  story=7296  repeat=TRUE  3 of 5
 2  story=7294  repeat=False  2 of 5      8  story=7294  repeat=TRUE  3 of 5
 3  story=7295  repeat=False  3 of 5      9  story=7295  repeat=TRUE  3 of 5
 4  story=7296  repeat=TRUE   3 of 5     10  story=7296  repeat=TRUE  3 of 5
 5  story=7294  repeat=TRUE   3 of 5     11  story=7294  repeat=TRUE  3 of 5
 6  story=7295  repeat=TRUE   3 of 5     12  story=7295  repeat=TRUE  3 of 5

served 12 times, 3 distinct: [7296, 7294, 7295, 7296, 7294, 7295, …]
GET /api/kids/{id}/progress: readings_completed 12,
  promotion {promote: false, stories_completed: 3, stories_required: 5}
```

Stage 6 holds **nine** servable, answerable stories — three per theme. The child
was shown three of them, twelve times.

**9.81 — the fix is a SORT key, not a `WHERE` clause.**

```sql
ORDER BY stage_id DESC,
         (NOT EXISTS (SELECT 1 FROM readings r
                      WHERE r.kid_id = :kid AND r.story_id = stories.id)) DESC,
         (theme_id = :theme) DESC, last_served_at ASC NULLS FIRST, id ASC
```

A `WHERE NOT EXISTS` would have been wrong three times over:

- it changes the **candidate set**, and `_serve_attempt_budget` is the size of
  that set (9.71) — the budget and the pick would stop meaning the same thing;
- it moves the **503 boundary**: a child who has read everything at their stage
  would be answered "the warm pool is empty, run `make warm`" when it is full.
  §01 says a child never sees an error, and a re-read is a better answer than a
  503 (`readings.py` already credits it with nothing, so it costs §8 nothing);
- re-reading a decodable story is **correct decodable-reader behaviour** (STATE
  invariant 3 makes the same argument about repeated words). Novelty is a
  preference, exactly as theme is; stage is the only safety bound.

`stage_id <= :stage` is untouched, and the new key sits **below** `stage_id DESC`
— so an unread story one stage up can never outrank a read story at the reader's
own stage. STATE invariant 6 is a property of the `WHERE` clause and of the first
sort key, and this change is neither.

Ranked below `(theme_id = :theme)`, novelty would have been inert for exactly the
child this entry is about; ranked above `stage_id DESC` it would break invariant
6. Between them is the only position that means "the theme is a preference and
the stage is a bound".

**9.82 — `:kid` is NULL on the admin preview path, and NULL is what makes the
ordering identical to today's.**

§7.21's `stage_id` knob exists because §11's admin portal and the eval path both
ask for a stage with no child in the room. `r.kid_id = NULL` is never true, so
`NOT EXISTS (…)` is TRUE for every candidate row, the key is constant, and the
remaining keys — which end in `id ASC`, a total order — decide everything.
Measured on the copy over the full candidate set for a stage-6 request, legacy
`ORDER BY` against the new one at `:kid = NULL`: **74 rows compared, 0 positions
differ.**

`_SERVE_SQL` therefore carries `bindparam("kid", value=None)`: the statement
stays callable with the parameter map every existing caller already passes, and
the default is the admin path's own semantics rather than a placeholder.

**9.83 — what the stuck child gets instead.** Same pool, same kid, the three
dragons stories marked read, new ordering:

```
7299 stage 6 space    (never served)      7298 stage 6 space
8935 stage 6 animals  (never served)      7293 stage 6 animals
7297 stage 6 space                        ---- then the read dragons rows ----
7291 stage 6 animals                      7296, 7294, 7295 (stage 6, dragons)
                                          ---- then stage 5 ----
```

Six further **unread stage-6** stories before any repeat, **none above stage 6**,
and the read rows are still there behind them rather than deleted from the
candidate set. `stories_completed` reaches 9 of 5 at the stage where it used to
stop at 3.

**9.84 — the test that fails against the shipped code needs a SECOND theme at the
same stage.** A pair holding N stories, requested N+1 times, is not enough on its
own: `last_served_at` ties (under the fixture transaction `now()` is the
transaction timestamp, and in production the whole pair is simply re-served in
`id` order), so once every story in the pair is read the (N+1)th request is a
correct re-read whichever build is running. The defect is that the **theme tier
outranks novelty**, so the fixture plants three stories in the requested theme and
three more at the same stage in another theme: the shipped code serves 3 distinct
and then repeats, the fixed code serves 6 distinct. Both halves are asserted, and
so is the `fallback: other_theme` the DTO now carries for rungs 4-6 — which is the
honest signal kid mode already knows how to render.

**9.85 — reported, not fixed: both supply gates are green or ask the wrong
question about exactly this pool.** Neither file is this change's to touch
(`warm.py`), and neither is a code change this entry makes.

- `--check-coverage` reports **33 of 33 pairs at target** while **29 of 33 hold
  fewer than 5** servable+answerable stories. `TARGET_PER_PAIR = 3` is §7's own
  number and should stay 3; what is missing is that nothing compares any count
  with §8's `STORIES_REQUIRED_AT_STAGE = 5`. After this change the quantity §8
  needs is the **stage** total, not the pair total — 3 per pair × 3 themes = 9,
  and the live pool holds 9-24 per stage — so the gate the pool lacks is a
  per-stage assertion, not a bigger `TARGET_PER_PAIR`.
- `--check-promotability` computes "its best 5 of 9 stories" from `_SUPPLY_SQL`,
  which has **no theme predicate**. Before this change that described a reader the
  serving path could not produce. After it, it is true: a child who keeps tapping
  one theme is served the whole stage. Adding a theme predicate now would make
  the gate measure something the serving path deliberately no longer does.

## Phase 8 — the operator's decision on the two answered rows, and the archive that did not survive its own backfill (11.116-11.118)

11.103 left two live rows undecided and named three resolutions. The user chose
**(c)**: archive every answer onto the story, then remove the question. This is
that, plus the defect it took a read-back to find — which is the more useful half.

**11.116 — `--mcq2-archive-answered`: preserve, then remove. A SEPARATE command
from `--mcq2-repair`, permanently.**

The free sweep refuses an answered row and always will (11.103). Folding this into
it would put an irreversible decision inside a routine repair, which is the shape
of every accident this module has a comment about. So it is its own flag, and the
DELETE is its own statement:

```sql
AND (SELECT count(*) FROM answers a WHERE a.question_id = q.id) > 0
AND (SELECT count(*) FROM answers a WHERE a.question_id = q.id) = (
      SELECT count(*) FROM jsonb_array_elements(
        coalesce(s.gen_meta -> 'mcq2_answers_archived', '[]'::jsonb)) e
      WHERE CAST(e ->> 'question_id' AS int) = q.id)
```

The equality is the whole safety property and it is SQL, not a Python `if`, for
the reason `_DELETE_LEAKY_MCQ2_SQL` is: the statement must be structurally
incapable of destroying evidence that was not preserved first, whatever the caller
believes it wrote a moment ago. **An equality rather than `>= 1`** — archiving one
of a child's three answers and deleting all three is the same data loss in a
smaller font. The `> 0` is the boundary with the free sweep: an unanswered row is
`--mcq2-repair`'s business and needs no decision, so an operator running this can
never be surprised by what else it swept up. A row that is its story's LAST
question is deliberately not touched either — that is a different refusal with a
different remedy, and sweeping it up here would manufacture invariant 14's
dead-end session inside the command that exists to be careful.

**Run for real.** 4 answers archived onto 2 stories, then q22 and q706 removed.
Run twice: the second printed *"nothing to archive"*. Both stories kept their
`tap_word`, so `servable stories with no question` stayed **0**,
`--check-coverage` stayed **33 of 33**, and `--check-promotability` was
**byte-identical** before and after — which is not luck: `mcq2` is not in
`MASTERY_QTYPES` (11.66), so `gpc_stats` (22 rows, 84 observations) could not move
and did not. `--check-mcq2` now exits **0 with zero rows under DECISION
REQUIRED**. Spend for the archive itself: **$0.00**.

**11.117 — and then `--backfill` erased the archive. Found by reading it back,
not by trusting the write that had reported success.**

The archive went into `gen_meta.enrichment.mcq2_answers_archived`, beside
`mcq2_leak_removed`, which is exactly where it must not be. `_record_enrichment`
does `story.gen_meta = {**old, "enrichment": meta}` — it replaces that sub-object
**wholesale**, and that is not a bug, it is the property `_UNENRICHED_SQL`'s fourth
clause *relies* on for idempotence (one enrichment erases the key, so a stripped
story is never selected twice). So the archive survived exactly until the next
`--backfill`, and by then the `answers` rows it had replaced were already gone.
**Four rows of two real children's reading history, destroyed by the command that
had just promised to preserve them, twenty minutes after it promised it.**

Nothing in the suite could have caught it: every test asserted the archive right
after the write. The read-back did, because the standing rule on this project is
that a repair checks its own work against the pool rather than its own bookkeeping
(11.84) — and this time that rule was worth four rows.

The rule that falls out is worth more than the fix. **`gen_meta.enrichment` holds
what the nodes produced THIS run and is replaced every run; a permanent record of
something a child did belongs beside `moderation` and `cost_usd`, which
`_record_enrichment` and `run_grammar_backfill` both preserve by spreading the old
object.** A key that must outlive an enrichment must not live inside one. The
archive is now `gen_meta.mcq2_answers_archived`, at the top level.

`test_the_archive_survives_the_next_enrichment` drives the sequence — archive,
then `_record_enrichment` — rather than asserting the key's spelling, because the
spelling is not the requirement and surviving is.

**11.118 — restored, corroborated independently, and proved against the sequence
that destroyed it.**

The four entries were rebuilt and written through the shipped statement. They are
**not** taken on the run log's word: `answers` cascades off `questions`, but
`readings` does not, so readings 227, 352, 3684 and 3687 all survive — and the
restore script asserts, before writing anything, that each entry's `kid_id`,
`story_id` and `reading_stage_id` match its own surviving reading and that its
`answered_at` falls between that reading's `started_at` and `completed_at`. All
four passed. Each carries a `restore_note` so nobody later mistakes them for a
clean write.

Then the destroying sequence was **reproduced**: the `mcq2_leak_removed` receipt
re-written, `--backfill --only 14,18` run for real (2 enrichments, $0.0034), and
the archive read back afterwards — **2 entries on each story, intact.** One,
reconstructed as a dashboard line from `gen_meta` alone:

```
  Priya (kid 2622), reading 3684, 2026-07-29T16:27:22.309253+00:00
  Q: Was the dragon glad?
    [ ] The dragon was glad.
    [*] A man was glad.
  answered: INCORRECT
```

That is the row the whole finding was about, still saying exactly what it said,
with the question that produced it no longer in front of any child.

**Neither story got a comprehension question back, and that is correct rather than
a shortfall.** Both keep a `tap_word` that a child has answered, so `enrich` takes
`_remoderate_questions` and never runs QUESTION_GEN — regenerating would cascade
those answers, and a `tap_word` answer IS §8 evidence. `questions_source` on both
reads `existing rows (answered; not regenerated)`.

**Sabotage — four more, each restored and verified restored.**

| sabotage | result |
|---|---|
| the archive-completeness predicate deleted from `_DELETE_ARCHIVED_MCQ2_SQL` | **1 RED** — a row deleted with none of its answers archived |
| the same predicate relaxed from `=` to `>=` (a PARTIAL archive accepted) | **1 RED** — 1 of 2 answers archived and the row still went |
| `n_other > 0` dropped from the command's own filter | **1 RED** — a story's last question swept up |
| the archive written back into `gen_meta.enrichment` (the shipped bug, restaged) | **3 RED**, including `test_the_archive_survives_the_next_enrichment` |

**9.86 — "read" means a `readings` row EXISTS, not that it was completed.**

`POST /api/readings` writes the row when the session STARTS, and `completed_at`
is set later by `/complete`. The tempting predicate is the completed one, because
that is what §8 counts — and it is wrong here. Novelty ranks above
`last_served_at`, so a story a child opened and abandoned would stay "unread"
forever and be re-served FIRST on every subsequent request: the same loop 9.80 is
about, in a narrower form and harder to see. With the row alone as the signal, an
abandoned story goes to the back of the queue and the child moves on, which is the
benign direction to be wrong in. The §8 counter is unaffected either way — it does
its own `completed_at IS NOT NULL` arithmetic in `readings.py`.

### PROVED — driven end to end through the real ASGI app, against a private copy

`docker exec storyforge-db sh -c 'pg_dump -U storyforge storyforge | psql -U storyforge -d sfunread'`,
then the request → `POST /api/readings` → `/complete` loop, kid at stage 6, theme
dragons, twelve times. The kid row is deleted afterwards; the shared database is
never written.

| | before | after |
|---|---|---|
| distinct stories in 12 readings | **3** | **9** |
| `promotion.stories_completed` | 3 of 5, permanently | 9 of 5 |
| stage of every story served | 6 | 6 |
| stories above the reader's stage | 0 | 0 |

The nine are the whole of stage 6 — three dragons, three space, three animals —
served in that order: the requested theme first while it holds something unread,
then the rest of the stage, then a re-read of the requested theme. The §7 DTO
reports the rungs it descended (`fallback: other_theme`), which kid mode already
renders.

Admin preview on the same pool, six consecutive `{stage_id: 6}` requests with no
kid: `7294, 7295, 7296, 7294, 7295, 7296` — the round-robin it has always been.

**Cost.** `EXPLAIN ANALYZE` on the copy: Postgres HASHES the correlated subquery
(`(NOT (hashed SubPlan 4))`), so the kid's readings are read **once per request**
and probed per candidate row, not re-queried per row. Execution time 0.159 ms
before, 0.197-0.206 ms after, on the full 119-story candidate set for a stage-11
reader. No new index: `ix_readings_kid_started` is `(kid_id, started_at)` and its
leading column is the lookup.

### Sabotage — three, each restored and verified restored

| sabotage | red |
|---|---|
| novelty ranked BELOW `(theme_id = :theme)` — 9.81's rejected alternative | `test_a_kid_is_served_every_unread_story_at_their_stage_before_any_repeat` (`3 == 6`, ids `[a, b, c, a, a, a]`) **and** the admin test's control assertion |
| the endpoint sends `"kid": None` instead of the anchor's kid | the unread test |
| `stage_id <= :stage` → `<= :stage + 1` (STATE invariant 6) | 4 tests, on a FULL pool (`test_fallback_never_serves_a_story_above_the_requested_stage`, live rows, no fixture) and on a MANUFACTURED EMPTY one (`test_a_stage_with_no_pool_falls_back_to_the_nearest_lower_stage`, `test_the_ladder_descends_past_a_rung_the_serve_gate_refuses`, and the new test's own `"the ladder climbed a stage (STATE invariant 6)"`), plus `test_story_stage_binding.py` |

Driven off **exit codes**, not off grepping the output: `pyproject.toml` sets
`addopts = "-q"`, so `pytest -q` is `-qq` and prints no summary line at all.

### Gates — measured after the change, commands as run

```
cd services/api && DATABASE_URL=…/sfunread .venv/bin/python -m pytest -p no:randomly
  1076 passed, 3 skipped in 89.08s   (exit 0; +2 tests, both new here — 20
                                      test functions in this file before, 22 now)
.venv/bin/ruff check storyforge/api/stories.py tests/test_stories_api.py
  All checks passed!
```

Repo-wide `ruff` is red on another agent's in-flight `llm/moderate.py`, which is
not this change's.

### PROVED — driven in a real browser, dev AND the production bundle

Environment, because "verified" without it is a claim about a screenshot: a
second API on `:8091` started with `DEMO_BYPASS_AUTH=false` and checked before
anything else — `GET /api/health` 200, `GET /api/kids` with **no** token 401,
with a **garbage** token 401. A Vite dev server on `:5191` and `vite preview`
on `:4194` both proxied at it. Signed in with a Clerk sign-in ticket for
`user_3HBc8LNBQdWzlwbgKrzXYdH1SDa`; the session was revoked afterwards. The
reader is kid 19161 "Ten", added at stage 10 through the real add-kid form, plus
kid 1648 at stage 1.

**1. The clipped last page — before.** `/read/19161`, animals, 1024x768. Pages
1-6 `scrollHeight == clientHeight == 583`. Page 7, button `Finish`:
`scrollHeight 1776 / clientHeight 583`, **73 word tokens entirely below the clip
edge**, beginning "to look at the water", ending "...was a good place for the
rabbit". The clip fell between lines; nothing was sliced.

**2. After.** Same page: button `More words ↓`, `aria-label="more words on this
page"`, hand-lettered `keep reading` + gold-700 chevron in the footer, and
`box-shadow: rgba(43,33,24,0.35) 0px -10px 10px -10px inset` on the reading box.
Three presses scrolled 0 → 511 → 1022 → 1265 (`scrollTop + clientHeight ==
scrollHeight`), at which point the button became `Finish →` and the shadow went.
The child reads "The rabbit did not go far again." before `Finish` exists.
`ArrowRight` behaved identically (511 → 1022 → end, then advanced), so the
keyboard cannot skip what the button will not. Pages 1-6 still show `Next` on
arrival — the affordance is invisible on a page that fits.

**3. `atEnd` is about WORDS, not about `scrollHeight`.** Nine live segments make
the box scrollable by exactly 6px at 40px portrait with every word on screen —
`pb-8` is 32px of deliberate blank space and `.sf-word` carries 12px of padding
plus 5px of margin under its glyph box. Measured on story 1662 page 7: last word
token bottom **892**, box bottom **924**, raw overflow 6px, button `Finish →`.
Crying "More words" over 32px of blankness would spend the credibility of the one
control the child has, so the test is the content bottom, and the gate's test is
the deepest text rect. Story 7343 page 7, the real case, is unaffected: 1254px of
text below the edge in the same portrait viewport, button `More words ↓`.

**4. The celebration cutouts.** Before: `[{"t":"cave","img":false},...]`.
After, on the same three-word card: `[{"t":"burrow","img":true},
{"t":"forest","img":true},{"t":"rabbit","img":true}]`, amber ground unchanged.
Verified in `dist/` too. Sabotage: deleting `assetUrl={cutoutFor(word)}` gives
`TS2741: Property 'assetUrl' is missing`; passing `storyWords` to `StoryText`
without the other three gives `TS2322 ... missing assetFor, shouldAnnounce,
onAnnounced`.

**5. §06's tinted fallback.** Story 8897 page 1 ("Sam sat.") — the page reported
as a blank striped half-screen — now renders `/assets/art/v1/dragons/cover.svg`
at computed `opacity: 0.4`, `object-fit: cover`, `position: absolute`,
`naturalWidth 800`, over the unchanged `.sf-stripes` gradient, with the frame's
`aria-label` still "a picture is on its way" and the tint `aria-hidden`.

**6. The spent choice card, sampled from the PNG rather than read off the
stylesheet** — the last finding in this area was a bounding-box artefact that was
never painted, so both states were measured on real pixels:

```
before (opacity .6 + ink-500, reconstructed in the same browser):
        glyph rgb(167,152,133) on rgb(248,238,218)  = 2.44:1
after   glyph rgb( 74, 58, 42) on rgb(246,234,210)  = 9.13:1
idle    glyph rgb( 43, 33, 24) on rgb(255,251,240)  = 15.23:1
computed opacity 1, border-style dashed, "try again" also rgb(74,58,42)
```

**7. STATE invariant 7 — the production bundle, not just dev.** `bun run build`
then `vite preview`, and every class this pass added survives the purge:
the arbitrary-value inset shadow renders as
`rgba(43,33,24,0.35) 0px -10px 10px -10px inset`; the cue is Kalam at
`rgb(74,58,42)`; the chevron stroke is `rgb(168,122,34)` (gold-700); the tint is
`opacity: 0.4`; the spent label is `rgb(74,58,42)` at `opacity: 1`. A default
`bun run build` emits **no** `dist/fit-check.html` — the harness is behind
`SF_FIT_HARNESS` and cannot reach a deploy.

**8. The gate agrees with the app to the pixel.** Segment 15953 (story 7343 idx
6, stage 10, 549 chars), measured by `fit-check` at 1024x768 @34px: box **583**,
**1193px** of text below the edge. Measured by hand in the live app before any of
this was written: `scrollHeight 1776 - clientHeight 583` = **1193**. Same number,
independently arrived at.

### The measurement — what `make fit-check` says about the pool today

825 servable segments (it was 832 when this pass started; a concurrent
`services/api` sweep retired one stage-4 story mid-flight).

```
viewport / size          box     clipped segments   word tokens below the fold
1024x768 landscape 34px  583px    65 of 825          6135
1024x768 landscape 40px  583px    68 of 825          6952
768x1024 portrait  34px  839px    74 of 825          7210
768x1024 portrait  40px  839px    75 of 825          7867
```

**Every clipped segment is `idx = 6` — the last page — at every one of the four
configurations.** It is one generator behaviour, not a spread of long pages:

```
stage   spine (idx 0-5)  n/avg/max chars     last page (idx 6)  n/avg/max     last pages clipped, L34/L40/P34/P40
  1        144 / 20 /  52                      20 /  45 / 100                  0 / 0 /  0 /  1  of 20
  2         60 / 21 /  35                       8 /  37 /  81                  0 / 0 /  0 /  0  of  8
  3         66 / 22 /  57                       9 /  53 / 148                  0 / 1 /  1 /  1  of  9
  4         66 / 23 /  42                      11 / 148 / 328                  4 / 4 / 10 / 10  of 11
  5         54 / 28 /  52                       9 / 207 / 266                  7 / 9 /  9 /  9  of  9
  6         54 / 29 /  50                       9 / 227 / 326                  9 / 9 /  9 /  9  of  9
  7         54 / 27 /  41                       9 / 245 / 427                  9 / 9 /  9 /  9  of  9
  8         54 / 27 /  45                       9 / 577 / 809                  9 / 9 /  9 /  9  of  9
  9         54 / 31 /  79                       9 / 586 / 769                  9 / 9 /  9 /  9  of  9
 10         54 / 28 /  55                       9 / 552 / 718                  9 / 9 /  9 /  9  of  9
 11         54 / 29 /  66                       9 / 509 / 603                  9 / 9 /  9 /  9  of  9
```

The spine is 20-31 characters at **every** stage. The last page is 45 characters
at stage 1 and 586 at stage 9 — twenty times its own story's other pages. The
generator is not writing long stories; it is writing six short pages and then
putting the whole ending on page seven.

**The budget, measured rather than chosen** — the longest segment in the pool
that fits, and the shortest that does not, at each configuration:

```
1024x768 landscape 34px   fits <= 157   clips >= 161
1024x768 landscape 40px   fits <= 131   clips >= 148
768x1024 portrait  34px   fits <= 100   clips >= 107
768x1024 portrait  40px   fits <=  81   clips >= 100
```

So: **<= 131 characters** per segment holds the §10 reference viewport at both
reading sizes, and **<= 81 characters** holds every configuration including
portrait at 40px. Stages 1-3 already live inside the second number on their spine
and mostly on their last page; nothing above stage 3 has a length rule at all.
Character count is a proxy — the real variable is rendered lines — so the gate,
not the number, is the thing to re-run.

**Portrait at 40px is a second, separate finding.** At 768x1024 the §10 portrait
layout puts the 4:3 illustration at full width: 540 of the 839px box, leaving
roughly three lines of 40px text. That is the reading size §11 prescribes for a
dyslexic reader. It is why one stage-1 last page clips there and nowhere else,
and no generation budget short of ~80 characters fixes it. It is a §06/§10/§11
tension, it needs a design decision (a portrait height cap on the art is the
obvious candidate, and §06's "full card width" is the sentence it argues with),
and this pass deliberately does not take it — the front end's job was to stop the
page pretending to be finished, and it does that in portrait too (verified).

## Phase 8 — three checks that passed because they asked the wrong question (21.1-21.3)

The house defect, occurrences ten, eleven and twelve. Each of these ran, returned
a verdict, and was believed; each was measuring a different object from the one
its name claims. None is fixed by weakening a gate.

### 21.1 — `passes_gate` certified a story with no words in it as fully decodable

`decoder/validate.py` scores `counts["decodable"] / scored if scored else 1.0`.
A draft that tokenizes to nothing — `""`, whitespace, `"... !!!"`, or a
`StoryDraft` whose `segments` list came back empty, which the schema permits
because `segments` has no `min_length` — therefore arrived at `passes_gate` with
`decodability == 1.0`, `counts["oov"] == 0` and zero distinct story words. All
three clauses were satisfied and not one of them had been asked about anything.

Measured before, at stage 1:

```
'empty'      dec=1.0 counts={'decodable': 0, ...} scored=0 words=0 passes_gate=True
'blank'      dec=1.0                              scored=0 words=0 passes_gate=True
'punct only' dec=1.0                              scored=0 words=3 passes_gate=True
```

§1's guarantee is "every word in this story is one the child can read". Zero words
satisfies it vacuously, and vacuously true is not true: the child is handed a blank
screen, presses `Finish`, and §8 records a perfect reading of nothing. The verdict
is a **hard refusal**, and it is made in `passes_gate` rather than in
`validate_text`, because 1.0 is the honest value of a ratio with an empty
denominator — the report is a measurement, the gate is the judgement, and §7's
state machine reads the judgement. `api/stories.py::_is_decodable_for` already
scored an empty body `0.0` at serve time (7.23); this is the same answer moved to
where the story is still preventable.

**The refusal is of no words, not of an empty denominator.** A text of nothing but
sight and story words also has `scored_word_count == 0` and reaches 1.0 down the
same branch, and it still passes — those are words, and the child can read them.
The gate reads a new `TextReport.word_count` (word tokens, punctuation excluded),
never `scored_word_count`, and `test_the_refusal_is_of_no_words_and_not_of_an_empty_denominator`
is what stops the fix from becoming a quiet tightening of §5.1's denominator rule.

Two consequences carried:

- `build_rewrite_block` gains a FOURTH describable failure (9.45's invariant: no
  gate failure may produce an empty problem list, and none may state a false
  premise). None of its three loops can see an empty story — there is no
  violation, no OOV and no story word to name — so it reached the generic
  fallback and told a model that had written nothing that its story was under 95%
  decodable. It now says the story came back empty and asks for the whole story.
- `check_mcq2` got the guard its answer pair has had since 11.68. `"?"` ends with
  a question mark and used to clear the gate on an empty denominator, so a
  question with no words was judged decodable; the refusal would otherwise have
  been reported as `uses words this child cannot read: ` with nothing after the
  colon.

**Proved at the pipeline, not at the reporting layer.**
`test_the_pipeline_rejects_an_empty_draft_and_never_pays_to_moderate_it` stubs the
model to return `segments=[]` and asserts `passed is False`, that all four
attempts were spent asking for text, and that `moderate()` was never called —
STATE invariant 11, never spend a Haiku call on a story that will not be served.
`warm.persist` turns `passed=False` into `status="rejected"`.

### 21.2 — the syllable cap was measured against a pronunciation the aligner could not use

§5.4 drops a word from a stage's allowlist when it has more syllables than that
stage permits. `precompute.compute_rows` counted them with
`min_syllables(prons)`: the fewest syllables across **all** of CMUdict's
pronunciations for the word, including the ones that do not align under the
stage's mastered GPCs. Alignment and syllable count were taken from different
pronunciations, so the cap was bounding a reading no child at that stage can
produce.

**Measured on the live table before the fix — this is not zero.** Of 34,288
`allowed_words` rows, **199 carried a syllable count the reader cannot produce at
that stage, and 67 of those were over their stage's cap** and were being offered
to children, across 20 distinct words: `awfully`, `cabinet`, `cabinets`,
`differences`, `different`, `every`, `family`, `frightening`, `gardening`,
`happening`, `interested`, `interesting`, `leveling`, `offering`, `remembering`,
`several`, `slippery`, `suffering`, `toweling`, `towering`.

```
every    /EH1 V ER0 IY0/ (3) and /EH1 V R IY0/ (2). At stage 5 only the 3-syllable
         one aligns — counted 2, admitted under a cap of 2, read as three.
towards  /T AH0 W AO1 R D Z/ (2) and /T AO1 R D Z/ (1). At stage 6 only the
         2-syllable one aligns. Counted 1.
fine     /F AY1 N/ (1) and the Italian /F IH1 N AH0/ (2). Silent-e is stage 4, so
         at stages 1-3 the ONLY reading is the 2-syllable one — in the stage-1
         allowlist as a one-syllable word on the strength of a reading no stage-1
         child can produce.
```

`decoder/lexicon_util.aligned_syllables(word, prons, mastered)` replaces it:
fewest syllables **among the pronunciations that align**, and `None` — never a
number — when none of them do. Per-stage by construction, because alignability
is: the usable set grows monotonically with the mastered set, so the count is
non-increasing in stage and a word gets *shorter* as the child learns more
(`fine` is 2 syllables at stage 1 and 1 at stage 4). `min_syllables` is kept, and
its docstring now says where it is and is not the right question.

**After**: 34,288 -> **34,221** rows, 11 stages, **769**-4,397 per stage
(entry stage 771 -> 769), still every stage clear of §18's 150 floor, and
`make precompute` still runs in 1.9s. `make precompute` was re-run against the
live database.

**Why the existing test could not see it.** `test_syllable_cap_is_honoured` reads
`allowed_words.syllables` and compares it to `stages.max_syllables` — it proves
precompute agrees with itself and nothing more, and it was green over all 67 rows.
Two tests replace that blind spot, and both ask the ALIGNMENT rather than the row:
`test_no_precomputed_row_is_capped_against_an_unusable_pronunciation` re-derives
every one of the 34,221 rows from `compute_rows` with no database, and
`test_the_live_syllable_cap_holds_for_the_reading_the_child_produces` scans the
whole live table (its remedy when red is `make precompute`, which is free). The
live one is a **full scan, not `ORDER BY random() LIMIT n`**: the defect touched
0.6% of the table, which a 600-row sample misses about one run in forty, and a
tripwire with a 2.6% false-green rate against its own defect is not a tripwire.

### 21.3 — the vacuity ratchet was a property of a stage number, not of the inventory

The anti-golden ratchet caps the fraction of an adult dictionary the inventory can
decode with everything unlocked (44.75%, ceiling 50%). It measured
`mastered_ids(MAX_STAGE)` — "what does the TOP STAGE decode" — where the question
it exists to ask is "how permissive is the INVENTORY", a property of a 93-row CSV
with no stage number in it.

**Does the recorded 44.75% still describe the same measurement it did before the
renumber? Yes — re-measured, not assumed.** Retiring Appendix A's stage 1
(migration `f3a91c6d0e24`, 12.1-12.14) moved the top label from 12 to 11 without
moving the set, because both sit at or above the highest stage any GPC is taught
at (10), so `mastered_ids(12) == mastered_ids(11) == the full 93 GPCs`.

**Would a future re-stage move it? Yes, silently.** Per-stage, at the pinned seed
and sample size:

```
stage  8 -> 44.00%      stage 10 -> 44.75%
stage  9 -> 44.375%     stage 11 -> 44.75%   (= full inventory, 93 GPCs)
```

Simulated by setting `MAX_STAGE = 8` and changing nothing else — a re-stage that
strands GPCs above the top of the ladder, or two more stage retirements — the old
formulation reports **44.00% and passes the 50% ceiling quietly**, while the new
one reports **44.75%, unmoved**. That is five points of headroom arriving for a
reason that has nothing to do with permissiveness, in the direction that looks
safe. The coincidence was held together by a literal `MAX_STAGE == 11` in
`tests/decoder/test_inventory.py` — the exact line a re-stage edits.

The ratchet now measures `full_inventory()` (every GPC id in `gpcs.csv`), and
`test_the_ratchet_is_invariant_under_re_staging` asserts its premise *where the
measurement is made*: the top stage must teach the whole inventory, naming any
stranded GPC. Its second assertion — that a smaller mastered set really does score
lower — is what stops the first from being decorative: if the reading did not
depend on the stage label, `mastered_ids(MAX_STAGE)` would have been a harmless
spelling of `full_inventory()`. The SEED, SAMPLE SIZE, FLOOR and CEILING are
untouched.

### Sabotage — six, each restored and verified restored

Every test written here was broken on purpose and confirmed red.

```
A1  passes_gate's empty clause -> `if False`      7 red, incl. the pipeline test
                                                  ("moderation was asked to judge
                                                   an empty story")
A2  passes_gate reads scored_word_count instead   1 red — the sight/story-word-only
    of word_count                                 text is refused, which it must not be
A3  build_rewrite_block's `empty` flag -> False   1 red — the block falls back to
                                                  "at least 95% decodable"
B1  precompute back to min_syllables(prons)       1 red — 67 rows over the cap
B2  aligned_syllables ignores the mastered set    3 red, incl. `fine` at stage 1
C1  MAX_STAGE 11 -> 8 (a simulated re-stage)      1 red — names the stranded GPCs;
                                                  test_gate_has_not_dissolved stays
                                                  GREEN at 44.75%, which is the point
C2  VACUITY_CEILING 0.50 -> 0.40                  1 red — the ceiling still fires
C3  vacuity_ratio ignores its argument            1 red — the invariance test is not
                                                  vacuous
```

### Gates — measured after the change, commands as run

```
pytest -q -p no:randomly (private copy sf_fix3)   1114 passed, 3 skipped, 1 failed
                                                  (the failure is another agent's
                                                  in-flight warm.py vs its own test;
                                                  no file in it was touched here)
python -m storyforge.warm --check-coverage        GREEN
python -m storyforge.warm --check-prompts         GREEN — 136 of 136
python -m storyforge.warm --check-mcq2            GREEN — 49 of 49
python -m storyforge.warm --check-promotability   RED, unchanged shape: terminal
                                                  stages [6, 7, 8, 9, 10]
python -m storyforge.precompute (live)            34,288 -> 34,221 rows in 1.92s
```

### Not fixed here, and why

- **`validate_text` still returns `decodability == 1.0` for a text with no
  words.** It is a ratio over an empty denominator and that is its honest value;
  the fix belongs in the judgement, not the measurement, and moving the number
  would have left every other reader of `decodability` free to re-derive the same
  lie. `eval.py`'s `meets_gate` compares raw `decodability` for the §14 report and
  is left alone for the same reason — `passed` on the same row is the gate's
  answer and is already `False`.
- **`fine` is still offered at stage 1**, now honestly labelled as two syllables.
  It is admitted because §5.2.5 lets ANY pronunciation pass and CMUdict carries the
  Italian /F IH1 N AH0/. That is a separate question about §5.2.5's existence
  semantics, not about the syllable cap.
- **`VACUITY_CEILING`, `VACUITY_FLOOR`, the seed and the sample size are
  untouched**, and the measured 44.75% is deliberately not pinned to an exact
  value: the ceiling is the ratchet, and a hard pin would fire on every justified
  GPC as well as every unjustified one.

## Phase 8 — three ceilings that were not ceilings (14.20-14.32)

Three separate findings with one shape: a bound that exists, is documented, is
believed, and does not bind. §5.5's story word had no upper bound at all; §7's
coverage gate bounded the wrong unit; §9.6's process budget was bounded by a
display limit. None is fixed by weakening anything.

### 14.20 — a story word had no decodability ceiling, and 69% of the picture-chip slots were spent on nothing

A story word is a word the child is **given** — a picture chip with tap-to-hear
(§07), excluded from the decodability denominator (§5.1) — instead of decoding.
There are exactly `MAX_STORY_WORDS` = 3 slots per story. Nothing bounded which
words could fill one: `story_word_bank.min_stage` was the only filter, and it is a
hand-set **semantic** floor ("is this concept age-appropriate here") that has no
relationship whatever to whether the reader can already read the word.

**Measured over the live pool — 119 servable stories, 326 chip slots.** Distance
is `min_decodable_stage(word) - story.stage_id`, where the first term is the first
stage whose mastered GPCs admit a complete alignment (the decoder's own
`min_stage`, the same function `precompute` builds `allowed_words` with):

| distance | slots | share |
|---|---|---|
| ≤ 0 — the reader can ALREADY decode it | **225** | **69.0%** |
| 1..3 — inside the band chosen below | 48 | 14.7% |
| 4..7 | 23 | 7.1% |
| never alignable in this 93-GPC inventory | 30 | 9.2% |

Per stage, distance ≤ 0 is 31/64 at stage 1 and rises to 24/27 at stage 11 — the
top of the ladder teaches no new GPCs, so almost everything is already decodable
there. The offered bank was worse than the pool: of 253 (word, stage) offers,
32.4% were never-alignable and 40.7% were already decodable.

The one-word version: **`dragon` is decodable from stage 1**, so every dragons
story on the ladder carried a picture chip over a word its reader could sound
out — while `castle`, which no stage of this inventory can ever align, is the word
the chip was invented for. `orbit` is `min_stage` 4 and decodable from stage 1, so
it could only ever be offered at stages where it was useless.

Seven of the twenty-four bank entries — `comet`, `dragon`, `forest`, `orbit`,
`planet`, `rabbit`, `rocket` — are decodable from stage 1 and are not story words
at all under any reading of §5.5. They are ordinary decodable words sitting in the
story-word bank.

**The rule chosen** (`pipeline.STORY_WORD_MAX_LOOKAHEAD = 3`), as one sentence:

> A story word must be a word the reader cannot decode at their own stage, and
> must either be permanently unalignable within this GPC inventory or become
> decodable within three stages.

Why both ends, and why "never" sits on the permitted side of the ceiling:

- **Floor (≥ 1).** A chip over a word the child can sound out does three bad
  things at once: it spends one of three slots, it paints a picture over the exact
  decoding practice §1 exists to deliver, and it drops a decodable word out of the
  scored denominator — the same denominator-shrinking STATE invariant 2 refuses
  when the *model* claims it. The bank claiming it is no better. This is the half
  that buys the 69%.
- **Ceiling (≤ 3).** The distinction is **substitutability**. For `castle`,
  `penguin`, `treasure` no amount of teaching replaces the chip — the inventory has
  no path for a silent `t` or `su` → /ZH/ at any stage — so the chip is the only
  route in, forever, and is permanently correct. For `giraffe` (decodable at stage
  8) offered to a stage-1 reader, teaching **will** replace the chip; the only
  question is whether the child meets the word now as a picture or in seven stages
  as their own decoding. Spending a scarce slot on a word the curriculum is about
  to teach properly is whole-word memorisation bought at the cost of a chip a
  permanently-unalignable word needed. This half buys 23 of the 326.

**Three and not two,** because three is where the bank stops being able to fill the
slot: at 3 every (theme, stage) keeps at least one candidate; at 2 the stage-1
dragons offer loses `cave` and `fire` and §18's "themes at stages 1-2 lean on story
words + pictures for interest" starts to bite. It is a lookahead in **stages**
rather than in missing GPCs because that is the unit the child's own progress is
measured in (§8 promotes a stage at a time), so "three stages" reads as "the next
three things you will be taught".

**Enforced at generation, audited at validation.** `pipeline.load_vocabulary`
applies the band when it builds the offer, so a story generated from now on cannot
carry an out-of-band chip. `warm.run_story_word_check` (`--check-story-words`,
`make story-word-check`) is the SIXTH gate and reads what is already in the pool.
Both call the same `story_word_refusal`; the gate has no copy of the rule, because
a rule that moved while a second copy did not is 11.60 exactly.

**It is deliberately NOT re-asked in `validate_draft`.** That function is a
*measurement* of a text against a vocabulary, and moving the band into it would
make the same text score differently for a reason that has nothing to do with the
text — including at serve time, where `api/stories.py::_is_decodable_for`
re-scores an already-persisted story and a silently un-exempted word would drop a
live story below the gate. Policy belongs where the offer is built.

**The gate is RED today: 248 of 326 slots (76.1%)** — 225 already decodable, 23
too far. There is **no free repair**, and that is a property of the defect rather
than an omission: the word is *in* the story, so dropping it from
`stories.story_words` would leave a word the child cannot decode rendered as plain
text with no picture, which is strictly worse than the wrong chip. The remedy is
generation.

### 14.21 — the rule surfaced a data defect: (stage 1, space) had no story word at all

Applying the band emptied exactly one cell of the matrix. `alien` and `astronaut`
are permanently unalignable, which makes them the two best story words space has,
and their hand-set `min_stage = 2` was withholding them from the one stage that
most needs them. Everything else space offers at stage 1 (`moon`, `planet`,
`rocket`, `stars`) is either already decodable or 5-6 stages out.

An empty offer at stage-1 space is AUDIT-PHASE7 F1 — "a dragons story with no
dragon in it" — reached by a second route, and against §18. Both moved to
`min_stage` 1 in `data/seed/story_words.csv`; `make seed` is required to apply it
(pure upserts, no DELETE). Every (stage, theme) now offers 1-6 candidates.
`test_no_stage_and_theme_is_left_with_nothing_to_offer` is the guard, and it is
the counterweight that stops the band being tightened until it starves the themes.

Under §5.5's "5-8 candidates per (theme, stage)" the bank is now short at most
cells. That is a **supply** finding, not a reason to keep the broken rule (the
argument invariant 15 already makes about §8's floor): the bank is 24 words, 8 per
theme, and 7 of them are inert. Not fixed here — curating a seed file is an
operator's call, and `--check-story-words` prints the inert rows on every run so
the number cannot rot.

### 14.30 — the eval's process budget was defeated by a display limit

`api/admin.py` carries two ceilings on spending money from a button:
`MAX_BUDGET_USD = 8.0` per request and `PROCESS_BUDGET_USD = 16.0` cumulative
across every launch in the process (9.6, whose stated rationale is "with only a
per-run cap and a busy check, **ten sequential clicks are ten runs**").

`_committed_usd()` summed `_launches`. `_launches` is capped at
`MAX_LAUNCHES_KEPT = 10` by `_prune`, which deletes the oldest finished entry —
and deleting the entry deleted its authorised dollars from the cumulative total.
Ten is exactly the number 9.6's rationale names, so the retention limit re-enabled
the precise scenario the ceiling was written to stop.

The arithmetic: once the registry is pinned at ten, the check on launch 11 and
every launch after it evaluates `10b + b > 16`, which fires only for
`b > 16/11 ≈ $1.45`. **Any launch at $1.45 or less could be repeated an unbounded
number of times.** `_committed_usd` asymptotes at `10b ≤ $14.54` no matter how
much has actually been authorised. Secondary effect, shown to a human rather than
to a branch: `process_remaining_usd` *increased* after every prune, so the portal
reported headroom that had already been spent.

`eval.py`'s own `--budget` check is clean and was checked: `items` is only ever
appended to, the four summation sites all run over the whole list, and the
`--resume` read carries no `LIMIT`. `--estimate-only` is a separate pure-token
projection (`project_cost`) that never touches either collection.

Fixed by carrying the number across the prune rather than by raising the limit:
`_prune_launches` adds a dropped launch's `budget_usd` to `_committed_pruned_usd`
*before* deleting the row, and `_committed_usd` returns carry + retained. What is
forgotten is the record, never the spend; the registry stays bounded and still
says `ephemeral: True` about itself.

**Why the suite was green on this.** `test_the_process_ceiling_blocks_a_repeated_click`
seeds **two** launches at `MAX_BUDGET_USD` each — two entries, well under ten, so
it never reaches the pruning regime, and $16 trips the ceiling on the first try.
No test used small budgets across more than ten launches. The new test drives
sixteen real `POST /api/admin/eval-runs` launches with `run_full` stubbed, so the
ceiling check, the registry write and the prune all run for real and nothing is
spent. A first draft registered the launches by hand and **stayed green under
sabotage** — it exercised `_prune_launches` in isolation while the endpoint's call
site had been reverted. That is what the sabotage run is for.

### 14.31 — the coverage gate was per-pair when the deliverable unit is per-stage

`warm.TARGET_PER_PAIR = 3` and `mastery.STORIES_REQUIRED_AT_STAGE = 5` were two
numbers in two files that never met. Measured on the live pool:

```
33 of 33 (stage, theme) pairs at target      → --check-coverage GREEN
29 of those 33 pairs hold fewer than 5
stages 5-11: every one of the 21 pairs holds exactly 3
per-STAGE totals: 24 10 11 11 9 9 9 9 9 9 9  → every stage clear of 5
```

Read as cells that is a starved pool; read as stages it is a healthy one. **Stages
is the correct unit, and it is the SORT KEY that makes it so.** Since commit
`a3e5be2`, `_SERVE_SQL` orders `stage_id DESC`, then *unread by this kid*, and only
then `(theme_id = :theme) DESC` — so a child at stage 7 who has read all three
dragons stories is handed an unread animals story **at stage 7** before the ladder
considers dropping a stage. The requested theme is a preference over the whole
stage's pool, not a partition of it.

`run_coverage_check` gains a per-stage assertion that **imports**
`STORIES_REQUIRED_AT_STAGE` rather than restating it — the whole defect was one
number living in two files — plus a stage-total column on the table, which is the
column whose absence *is* the finding. Both floors are reported before either sets
the exit code; short-circuiting on the pair gate would send an operator to
`make warm` and back. Green today, and red the moment the theme list shrinks to one
(3 < 5), which is the scenario the test pins.

**`TARGET_PER_PAIR` is deliberately NOT raised to 5.** That would demand 165
stories against today's 119 — roughly 46 stories of real API spend — to buy what
the unread-by-this-kid sort key already delivers for nothing.

### 14.32 — `api/catalog.py` quoted the serve order with the middle key missing

The module docstring said `_SERVE_SQL` orders `stage_id DESC, (theme_id = :theme)
DESC`. Still true, and incomplete since `a3e5be2`: the unread-by-this-kid key sits
between them, above the theme preference. That omission is not cosmetic — it is
precisely the fact that makes 14.31's unit the stage rather than the cell, and the
paragraph was being read as an argument that per-theme depth is what a child's
supply is made of.

### Verification

```
pytest -q -p no:randomly                 1133 passed, 3 skipped, against a PRIVATE
                                         copy (STATE.md's rule — a concurrent agent
                                         was writing the shared one). Baseline 1076/3;
                                         the rest is this pass plus a concurrent
                                         agent's own additions.
ruff check storyforge tests              All checks passed
--check-coverage                         GREEN — 33/33 pairs, 11/11 stages
--check-story-words                      RED — 248 of 326 slots (the finding)
make eval EVAL_ARGS="--estimate-only"    $3.10 / $6.20 / $12.41, no API call
```

Sabotage, every check run against a broken source and confirmed red:

| broken | red |
|---|---|
| `story_word_is_eligible` → always `True` | 5 tests |
| `load_vocabulary` stops filtering | `test_the_offer_contains_only_words_inside_the_band` |
| `chip_offences` reports nothing | `test_the_gate_finds_a_known_bad_chip_...` |
| `STORY_WORD_MAX_LOOKAHEAD` 3 → 99 | 2 tests |
| `stage_gaps` → always `[]` | 4 tests |
| stage floor stops setting the exit code | 2 tests |
| `stage_gaps` restates the literal `3` | 3 tests |
| `_prune_launches` → `_prune`, carry removed | 2 tests |

### Not fixed here, and why

- **The 248 out-of-band chips already in the pool.** There is no free repair (see
  14.20) and the remedy is regeneration, which is real money. The gate names every
  one of them and exits non-zero; it is red honestly rather than quietly repaired
  in a direction that would leave a child a word with neither a chip nor a hope of
  decoding it.
- **The seven inert bank rows and §5.5's "5-8 candidates".** Both are curation of
  a seed file. Reported on every gate run, not deleted.
- **`min_stage` is not re-derived.** It remains the semantic floor, which is a
  judgement the decoder cannot make. Only the two entries that emptied a cell moved.
- **The eval's concurrent-mode budget check still runs between stage batches**, so
  a batch can overshoot `budget_usd` by up to one stage. Pre-existing, deliberate,
  documented at the call site — and a different question from 14.30, which is about
  the cumulative ceiling under-counting rather than about check granularity.

## Phase 8 — the title passed none of §13's three layers, and five schemas typed the ladder's length (17.1-17.3)

Three findings from one adversarial pass. The first is a child-safety claim the
README makes and the code did not honour. The second is a single source of truth
that existed, was documented as existing, and was not used. The third is an audit
whose stated premise turned out to be **false**, and which is recorded here in
full because a withdrawn finding is worth as much as a fixed one.

### 17.1 — the story `title` is model-authored child-facing text and passed ZERO of §13's three layers

§13: *"All child-facing text passes: deterministic blocklist → allowlist-constrained
generation → moderation node. Three independent layers."* The README repeats it as
*"Every child-facing string passes three independent layers."*

The title passed none. One line explains all three:

```python
def draft_text(draft: StoryDraft) -> str:
    return " ".join(seg.text.strip() for seg in draft.segments).strip()
```

`run_pipeline` builds `body = draft_text(draft)` and hands that same `body` to
`validate_draft` (layer 2) and to `moderate` (layer 3) — and `blocklist_check`
(layer 1) runs *inside* `moderate`, on whatever `moderate` was given. The title is
not in `draft_text`, so one omission removed all three layers at once. It is
assigned raw (`result.title = draft.title`) and persisted by `warm.persist`.

**Measured, not asserted, over the live pool (119 servable stories):**

| Layer | Applied to the title? | Live measurement |
|---|---|---|
| 1 blocklist | no | **0 of 119** titles carry a hit — the hole was unexploited, not absent |
| 2 allowlist-constrained generation | no | **28 of 119 (23.5%)** use a word that would fail the very gate their own body had to clear |
| 3 moderation | no | never judged; 119 unjudged titles are still in the pool |

The layer-2 row is the proof, not the complaint. `StoryDraft.title`'s schema says
*"Three to six words, from the permitted vocabulary"* — and §0 says the LLM is an
untrusted generator. 28 titles (`Sam Goes To The Moon`, `The Rabbit Finds Home`,
`Jack Flies Past The Stars`, `Sam And Dan In Space`, `Tom Rides The Rocket`) show
the instruction being ignored roughly a quarter of the time with nothing to notice.

**Was it live or latent?** Latent, and the audit brief's phrasing — *"displayed to
a five-year-old"* — is not true today. Nothing under `apps/web/src/routes/`
renders `story.title` in kid mode; it is rendered on the **parent** dashboard
(`ParentDashboard.tsx`) and in the admin cold-generate view. It is nonetheless
persisted to `stories.title`, returned on every `StoryDTO` (`api/stories.py`),
and written into that reader's own device storage by `lib/lastStory.ts`, whose
shape check requires `typeof story.title === 'string'`. It was one
`<h1>{story.title}</h1>` from being live, and the README's claim was false either
way. **The finding stands; the word "displayed" does not.**

**The fix: one call, not two.** `child_facing_text(title, body)` puts the title
into the string the existing MODERATING node already judges. That buys layer 1
and layer 3 together, because layer 1 lives inside `moderate()`.

Why not a second `moderate()` call on the title alone — which was the first
design and was discarded: `_moderate_questions` already measured what happens
when `moderate()` is handed something that is not a short story. *"Handed a whole
question block it rejects 16 of 16 live cases as 'not a coherent story'."* A bare
six-word noun phrase is further from a story than a question block is. A title
**above** its story is exactly what a children's book looks like — and it costs
no extra API call.

Why labelled `Title: ` rather than prepended bare: the same call carries the
grammar half of the verdict (4.12), and a bare noun phrase read as prose invites
a `well_formed = false` that would cost the story. The label is asserted not to be
a blocklist entry, so it cannot manufacture a layer-1 hit of our own making.

**`MODERATION_SYSTEM` is byte-identical.** STATE invariant 5 is explicit that every
one of the four previous passes at that prompt broke a case that had been passing,
and that naming a construction moves the judge in both directions. Nothing was
named. `tests/test_grammar.py::FIXTURE` — the 40-case live fixture — therefore
still describes this judge exactly, and `child_facing_text("", body) == body`, so a
titleless story's verdict cannot have moved.

### 17.1a — what is deliberately NOT done: the title is not gated on decodability

The brief warned that a prior agent inferred a §11.3 decodability requirement that
does not exist, and that the wrong inference cost 71 of 125 rows their meaning.
The same trap is live here, so the reasoning is recorded rather than left implicit.

**The build doc contains the word "title" zero times.** The only line in it that
requires a rendered string to pass the validator is §10:

> "Choice labels must themselves pass validation (they're rendered text)."

That names choice labels. It does not name titles, and §6's constraint sentence is
scoped the same way — *"Output schema: story **segments** with text only from
allowed material."*

Gating the title on decodability would therefore reject ~24% of generation to
enforce a rule nobody wrote. Worse, it would corrupt a §1 acceptance metric:
`stories.decodability` is a fact about the BODY, and folding the title into
`draft_text` would silently change what that number measures.

Note also what the 28 offending words actually are — `goes`, `finds`, `flies`,
`rides`, `rabbit's`. Every one is excluded from `allowed_words` for a
**decodability** reason (`oe`, `-es` on an irregular, a possessive), not a safety
one. A vocabulary gate on titles would be a decodability gate wearing a safety
hat. `test_the_title_is_deliberately_not_gated_on_decodability` pins the
non-requirement so the next reader does not re-derive it wrongly.

Layer 2's *safety* contribution — that the generator can only reach for pre-vetted
words, which is what keeps brands and real people out of the body — is delivered
for the title by layer 3 instead: `MODERATION_SYSTEM` already carries the bullet
"Real brands, real people, or real places presented as real", and it now reads the
title.

### 17.1b — every other child-facing string, swept

The brief asked the same question of every string that reaches a child. Answered
in full so the README's claim can be read against a table rather than a hope.

| String | Author | L1 | L2 | L3 | Note |
|---|---|---|---|---|---|
| segment body | model | yes | yes | yes | the path everything else is measured against |
| **story `title`** | **model** | **now** | n/a (see 17.1a) | **now** | this finding |
| `mcq2` prompt + options | model | yes | yes | yes | `_moderate_questions` (5.12) |
| `tap_word` prompt + cue + options | frame + allowlist | yes | yes | **exempt** | `LAYER3_EXEMPT_QTYPES` — a fixed `PROMPT_FRAMES` frame, a cue from the precomputed allowlist, and `check_tap_word` refuses any option that is not already a word of the moderated story. Documented, justified, unchanged. |
| `choice_label` | **nobody** | — | — | — | branching is not live; `warm.persist` never sets it and `StoryDraft` has no branch field. §10 *does* require these to pass validation and **no code implements that** — a latent gap with nothing to fix today. Recorded, not built. |
| theme `display_name` / `emoji` | `data/seed/themes.csv` | no | no | no | seed data we authored and reviewed |
| `assets.alt_text` | `data/seed/assets.csv` | no | no | no | hand-authored, and accessible-name only — `SegmentArt.tsx` forbids rendering it as visible text |
| `story_word_bank.gloss` | `data/seed/story_words.csv` | no | no | no | never rendered at all; prompt input only |
| `scene_tags` | model | n/a | closed enum | n/a | `SceneTag` StrEnum; not rendered |
| kid `display_name` | **a parent** | no | no | no | free text typed by an adult, rendered full-screen as "hello {name}". Guarded by an allowlist regex in `api/kids.py` + a DB CHECK, which that file's own docstring already calls out as structurally the same hole arriving by a different door. Left as is — the guard is real and a moderation call on a child's name is the wrong instrument. |
| all kid-mode UI copy | **us, in source** | no | no | no | not the same risk class: source-reviewed, in the diff, and unable to change without a commit. Named here rather than churned. |

So the honest form of the README sentence is: *every child-facing string a MODEL
writes* passes three layers, with `tap_word` explicitly exempt from layer 3 for a
stated and measured reason. Strings we or a parent author are governed by review
and by type constraints instead.

### 17.1c — the backlog, and the half of it that is not covered

Fixing generation does not fix rows already written, and this project has been
burned by exactly that four times (6.24 the discarded grammar verdict, 11.82-11.85
the retired prompt frames, 11.99-11.103 the unrun mcq2 repair, 11.117 the archive
erased by its own backfill).

`test_the_live_pool_holds_no_servable_story_with_a_blocklisted_title` asserts
layer 1 over every servable row. It is a pure function and one query, so it costs
nothing and runs on every commit. Sabotage-checked by planting `The Cat Will Kill`
on story 2 inside a transaction that was then rolled back; the sweep found it.

**Layer 3 over the backlog is NOT done.** Re-judging 119 titles is a Haiku spend,
and the audit was explicitly not authorised to spend. It is the one open item from
this finding — and it is written down as open rather than quietly counted as done,
which is the specific failure the four incidents above have in common.

### 17.2 — five schemas typed the ladder's length; `MAX_STAGE` existed and was not used

The renumber from twelve stages to eleven (12.1-12.14, migration `f3a91c6d0e24`)
edited the ceiling **by hand** in five Pydantic schemas, one range check, one
error message and two CLI defaults, while `MAX_STAGE` sat in
`storyforge.decoder` — already re-exported into `storyforge.mastery` precisely so
there would be one source of truth.

Every hand edit was made correctly. **The defect is that nothing could tell.**
`tests/decoder/test_inventory.py` pins `MAX_STAGE == 11`; past that, every
assertion about a *request* bound was a literal. `test_phase2.py` probes
`stage_id: 99` and expects 422, which passes for any ceiling below 99.
`test_admin.py` pins the error *string* `"within 1..11"`. A renumber to twelve
would leave five schemas silently refusing the top stage under a green suite.

Now derived: `api/validate.py`, `api/kids.py`, `api/stories.py`, `api/admin.py`
(the `Query`, the `Field`, the `1..11` range check **and its message**),
`eval.DEFAULT_STAGES`, `warm.DEFAULT_STAGES`, eval's `--stages` help text, and
`ParentDashboard.tsx`'s "Stage 11 is the last stage" — from the server's own
`from_stage`, which IS the top of the ladder whenever `to_stage` is null.

**Cannot derive, so pinned instead** (`tests/test_stage_bounds.py`):

- `data/seed/stages.csv` is asserted against `MAX_STAGE` here. `apps/web`'s
  `SEEDED_STAGES` is already asserted against that CSV by `seeds.test.ts` (8.61),
  so the chain **MAX_STAGE → CSV → TypeScript** closes without a second copy of
  the number in TypeScript.
- the `stages` **rows**, because there is no SQL `CHECK` on `stage_id` anywhere:
  every other table reaches the ladder through this table's foreign key, so
  `max(stages.id)` *is* the database-side bound.

The request-bound test is a **sweep over the generated OpenAPI document**, not a
list of four file paths: it finds every bound on a stage-shaped field, so a bound
added tomorrow is covered by a test written today — and it names the four schemas
it expects to find, so it cannot pass over an empty set (the `_UNENRICHED_SQL`
failure of 11.99 reached by another route).

Sabotage, both directions, because the two halves fail on opposite events:

| Sabotage | Result |
|---|---|
| `MAX_STAGE` 11 → 12 | CSV + `stages`-table tests RED. The derived tests stay green — which is the point of deriving: a renumber needs a data change and must say so out loud. |
| one bound put back to a literal `le=10` | the OpenAPI sweep names the exact JSON path |

### 17.3 — §18's "bind-address control": the premise is FALSE, and here is what is actually there

The audit brief said *"§18 specifies how the service binds."* **It does not.**
§18 (*Risks & Fragility Audit*, lines 306-314 of the build doc) contains no
requirement about network binding, and the whole document contains zero
occurrences of `bind`, `0.0.0.0`, `127.0.0.1`, `localhost` or `--host`. Recorded
as withdrawn rather than quietly dropped — this is the fifth finding in this
project to be withdrawn as a measurement artefact.

**What §18 actually specifies about deployment is one line:** *"Clerk outage
during demo: `DEMO_BYPASS_AUTH=true` env flag renders a fixed parent session
locally. **Never enabled in prod deploy.**"* Audited against the code and against
the running system:

| Question | Answer |
|---|---|
| Is the §18 control enforced or documented? | **Enforced.** `auth.assert_demo_bypass_is_local()` runs at module import *and* inside the grant branch, over six signal families (env names, ~30 platform vars by presence, `PORT`, container filesystem markers, DB host, `sk_live_` key). |
| Does the deployed container match? | **Yes, read back from the live system, not assumed.** `GET https://storyforge-vtwne.ondigitalocean.app/api/health` → `"demo_bypass_auth": false`, and the response carries `x-storyforge-auth: enforced`. |
| Where does the service bind? | `services/api/Dockerfile` → `--host 0.0.0.0 --port ${PORT:-8080}`, correct for a container behind App Platform's router. `Makefile` and `scripts/dev.sh` pass **no** `--host`, so a laptop gets uvicorn's 127.0.0.1 default; `vite.config.ts` sets no `host`. The only `0.0.0.0` in the repo's runtime path is the Dockerfile's, and nothing makes the bind env-configurable. |

**The one real gap, which the code documents against itself** (`auth.py`,
`deployment_signals`): *"This is mostly a list of negative signals, and that is
its weakness… The remaining half of the inverse control — refuse unless the
process is bound to `127.0.0.1` — is still not taken, because the bind address
does not exist yet when this runs (this module is imported before uvicorn has a
socket); see 8.66."* That is almost certainly what the brief was pointing at, and
it is still open. **Not implemented here** — it is a new auth control whose whole
value is behaviour at a real socket, and the test suite drives the app through an
ASGI transport that has no socket at all. A green test for it would be evidence
of nothing.

**What was closed instead, because it was free and nothing covered it:**
`tests/test_deploy_spec.py` asserts the committed `.do/app.yaml` itself. That file
calls `ENVIRONMENT: production` a *"§18 TRIPWIRE — do not remove to make something
work"* and explains that `DEMO_BYPASS_AUTH` is *"deliberately ABSENT rather than
set to `false`"* — and both statements were enforced by nothing at all. The value
of a tripwire is that someone debugging a broken deploy at 3am cannot start the
container by deleting one line; a comment saying "do not remove this" is
removable, and a red suite answers back. Asserted through
`auth.is_deployed_env_value` rather than against the string `"production"`, so if
the guard's notion of a deployed value ever narrows the test moves with it.

Sabotage: deleting the `ENVIRONMENT` block → 2 red; adding `DEMO_BYPASS_AUTH:
"false"` → 1 red; `ENVIRONMENT: main` → 1 red. (`ENVIRONMENT: prodcution`, a
typo, stays GREEN — correctly: the guard matches the `prod` prefix, so the typo
really is still a deployed signal.)

### 17.4 — not fixed here, and why

- **Layer 3 over the 119 existing titles.** Real money; the audit was not
  authorised to spend. Open, and named as open (17.1c).
- **The positive bind-address half of the §18 guard.** Needs a real socket to be
  evidence of anything (17.3).
- **`choice_label` has no validation path** and §10 requires one. Branching is not
  live and nothing generates a label, so there is nothing to gate today. Building
  the gate before the feature would be a check with no subject.
- **`eval.parse_stages` is unbounded where `warm.parse_stages` is bounded.** Real
  asymmetry, and `warm.py`'s docstring already records it. Adding the bound is a
  CLI behaviour change to the one command that spends money, and
  `api/admin.py` already bounds the same parser on the route that a client can
  reach. Reported, not changed.
- **`tap_word`'s layer-3 exemption.** Deliberate, documented and reasoned
  (`LAYER3_EXEMPT_QTYPES`). Not weakened, not widened.
- **Hardcoded kid-mode UI copy, theme names, `alt_text`, glosses.** Authored by
  us in source or in `data/seed/`, in the diff, and unable to change without a
  commit. A different risk class from model output; named rather than churned.
- **Stale `12`s in prose** (`docs/EVAL.md` line 25's `--stages 1..12`,
  `apps/web/src/lib/api.ts` and `AdminEvalRun.tsx` doc comments, `AddKid.tsx`'s
  "twelve radios"). Documentation drift, not a bound; other agents hold those
  files in this worktree and a doc sweep is not this audit's remit.

## Phase 8 — "there is no free repair" was a claim about the wrong population (14.40-14.42)

14.20 shipped the story-word band with a gate that was RED at 248 of 326 and a hint
that said the backlog could only be fixed by regenerating stories. That hint
generalised from the 23 chips ABOVE the reader's stage — where it is true — to the
225 below, where it is the opposite of true. The 225 are the finding.

### 14.40 — the free half: 182 chips dropped for $0.00

For a chip whose word the reader can ALREADY decode, removing it does not leave an
undecodable word as plain text. It leaves a word the child can sound out, which is
the product's entire purpose. The story text is untouched, the word moves OUT of
the exempt set and INTO the scored denominator as `decodable`, and since
`(d+k)/(s+k) >= d/s` the §1 metric can only improve.

Verified before writing anything, and the check was built to refute rather than to
confirm — every way it could be false, asked explicitly:

```
119 servable stories, 326 chip slots, 225 with distance <= 0

every dropped word is an exact lowercase token of its own body   225/225
verdict after un-exempting, at EVERY reader stage the story can
  be served to (story.stage_id .. 11), 5054 evaluations           decodable 5054
                                                                  violation 0, oov 0
decodability delta                                    min +0.000000, max +0.010293
story falls below DECODABILITY_GATE                                          0
new violation / new OOV introduced                                           0
kept chips disturbed                                                         0
```

Why the reader-stage sweep and not just the story's own stage: `_SERVE_SQL` picks
`stage_id <= :stage`, so a story is served to readers at its stage **or above**,
and `_is_decodable_for` re-scores at the READER's stage (7.23). Cumulative
inventories make that monotone, but the whole point of a refutation attempt is not
to take the monotonicity on trust.

Two details that could have made it false and did not: `validate_word` checks
`sight_words` BEFORE `story_words`, so a word that is both was never a chip's to
lose; and §5.2.1's tokeniser keeps the clitic, so `dragon's` is a different token
from `dragon` and was never exempted by it.

### 14.41 — the second condition, which the first analysis missed

Decodability is necessary and **not sufficient**. A chip may only be dropped when
the word is ALSO permitted vocabulary at that stage — in `allowed_words` or
`sight_words`.

`dragon` and `planet` are decodable from stage 1 and appear in **no** stage's
`allowed_words` at all: §5.4's register/frequency filters drop them (11.9-11.11).
They are in these stories only under the story-word exemption. De-chipping one
converts a licensed word into a word the stage's own vocabulary does not permit —
trading a bookkeeping defect for a real one, and one that `--check-vocabulary`
reports.

That is 43 of the 225. The free population is **182**, not 225, and the
distribution is not a rounding difference:

| population | slots | free? |
|---|---|---|
| decodable now, and permitted vocabulary | 182 | **yes** |
| decodable now, NOT permitted vocabulary (`dragon` 39, `planet` 4) | 43 | no |
| 4-7 stages ahead | 23 | no |

Both conditions are read from the live tables inside `droppable_chips`, never from
another gate's verdict, so the repair stays correct whether or not anything else is
measuring them.

### 14.42 — the repair, and what it refuses

`--story-word-repair`. Free: one query, one UPDATE, no API client. It never edits
story TEXT and never reads or writes a `questions` row, so there is no path by
which it can cascade a child's recorded answer away — the refusal `--mcq2-repair`
had to express as a SQL predicate is structural here.

It proves its own postcondition rather than asserting it: for every row it is about
to write it re-scores the real body at the story's own stage with the reduced
exemption set, and refuses to write unless decodability holds and stays above
`DECODABILITY_GATE`. `stories.decodability` is rewritten alongside, computed exactly
as `persist` computes it — left alone it would be a stale under-estimate of a story
that just got better (27 rows were).

**Run against the live pool:**

```
--check-story-words   BEFORE   248 of 326 slot(s) out of band
--story-word-repair            dropped 182 chip(s) from 97 stories
                               22 stories now carry no chip at all
                               27 had `decodability` corrected upward
                               0 refused
--check-story-words   AFTER     66 of 144 slot(s) out of band
second run                     nothing droppable (idempotent)
```

`--check-vocabulary`'s output is **byte-identical** before and after, which is the
control for 14.41.

Child-visible effect of a dropped chip, audited in `apps/web` — exactly three
things, all downstream of the same removal: the word loses its 28px inline cutout
(`lib/art.ts`'s per-word `CUTOUTS` map, not scene tags — segment illustrations are
a separate channel and are untouched), it loses auto-speak on first appearance
(`introductionIndices` drives the chip and the utterance off one array, 11.26), and
it leaves the celebration "New words" card. Nothing assumes a non-empty list:
`storyWords.ts:49` short-circuits on an empty set and `Celebration.tsx:166` guards
the whole card on `.length`. 22 stories now show no card, which is honest — those
stories contain no word the child could not already read.

### Not fixed here, and why

- **43 wasted chips on `dragon`/`planet`.** Free only if those words are added to
  `allowed_words`, which is a §5.4 register-filter decision and a `make precompute`
  change, not a pool repair.
- **23 chips above the reader's stage.** Genuinely regeneration-only; the argument
  in `_CHIP_HINT` (b) is unchanged and correct for these.
- **The gate stays RED**, now at 54 of 121 on the live pool (the totals moved
  because a concurrent agent re-statused 13 stories while this ran). Regeneration
  cost at $0.026/story: **12 stories / $0.31** for the too-far half alone, **47
  stories / $1.22** for every remaining out-of-band story. Not spent.

## Phase 8 — a precomputed input changed and the pool built from it was never re-checked (22.1-22.9)

The user's loudest complaint about this product was one word: *"`amid` showed up in
almost every sentence. No five-year-old is using the word `amid`."* It was fixed in
11.7-11.11, by replacing §5.4's literal "intersect with SUBTLEX top ~20k" — adult
film-subtitle frequency, which proves a word is common in ADULT speech and says
nothing about a five-year-old — with a Dale-Chall register filter and a Hunspell
proper-noun filter.

That fix changed the vocabulary the generator is **given**. It changed nothing
whatever about the stories already in the pool. Two phases later, measured on the
live database: **`amid` is absent from `allowed_words` at every one of the eleven
stages, and nine servable stories still contained it — three of them offering it to
a stage-1 child as a tappable option on the question screen.**

### 22.1 — why no gate could see it, and the general principle

`amid` is **decodable**. a-m-i-d is four one-to-one GPCs and aligns from stage 1,
so `pipeline.passes_gate` passed it at generation time and
`api/stories.py::_is_decodable_for` re-scores and passes it at every serve. The
objection is REGISTER, not phonics, and register lives in `allowed_words` alone —
a table no query had ever compared the pool against.

Six gates were green or red for their own reasons and none of them was about this.
`--check-coverage` counts stories. `--check-promotability` counts §8 evidence.
`--check-prompts` and `--check-mcq2` read what a question SAYS. `--check-story-words`
reads what a chip is spent on. Every one of them reads something the pool
*contains*. This is the first that reads the pool against something *outside* it.

The principle, which is bigger than `amid` and is the reason this is a gate rather
than a script: **a precomputed input changing must invalidate the artefacts built
from it.** It is the same shape as 11.60 (a prompt frame the ladder no longer
produces, served to 120 children), 11.64/11.99 (an mcq2 the generator would now
refuse, 7 of them pointing the child at the wrong card) and 14.20 (a picture chip
outside §5.5's band, 248 of 326). Four instances, four different artefacts, one
defect: a rule moved forward and the pool stayed where it was.

### 22.2 — asking the right question, which took two attempts

The first census censused warm stories against `allowed_words(stage) + sight_words +
story_word_bank` and reported **57 of 108 offenders**. That number was not
trustworthy and was discarded, because the list it produced was dominated by `sam`,
`dan`, `tom`, `jack` and by `every`, `could`, `would`, `saw`, `around` — which is
the signature of a wrong allowed set, not of a broken pool.

What the generator is actually permitted to emit is stated in `SYSTEM_RULES` rule 1,
verbatim: *"Every word must come from the PERMITTED WORDS list, the SIGHT WORDS
list, or the STORY WORDS list. There are no other options."* So the permitted set
for a story at stage S is exactly:

* **`allowed_words` WHERE `stage_id = S`** — the WHOLE stage, not the 400-word
  prompt window. `load_vocabulary` applies `ORDER BY freq_rank LIMIT
  PROMPT_WORD_LIMIT`, which is a decision about how big a PROMPT may be; a word at
  rank 401 is permitted vocabulary this particular request had no room to name. A
  census on the window would have reported thousands of false offences.
* **`sight_words` WHERE `stage_id <= S`** — cumulative, as `load_vocabulary` reads
  them. This is what `every`, `could` and `saw` were missing from the first census.
* **that story's own `stories.story_words`** — NOT the 24-row global
  `story_word_bank`, which is a per-theme OFFER and is a different set from what a
  given story actually carries chips for.
* **there is no character-name allowlist.** Searched for and absent: `common_forms()`
  filters proper nouns out of `allowed_words` by design (11.9) and nothing puts them
  back. `sam`, `dan`, `kim`, `pam` are in `allowed_words` at zero stages.

Two things are then excluded from the judgement, and both are exclusions of a
DIFFERENT gate's business rather than concessions to make this one green:

* **A word not decodable at the story's stage.** `precompute.compute_rows` drops any
  word the inventory cannot align (`if first is None: continue`), so `allowed_words`
  is *structurally unable* to contain `could`, `would`, `goes` or `around` at any
  stage. Counting them here would silently turn this into a second decodability gate
  with no slack, which is a different proposition — §1 permits 5% — with a different
  remedy. 88 tokens.
* **A possessive of a permitted word.** §5.2.1's tokeniser keeps the clitic, so
  `rabbit's` is one token while `rabbit` is offered. Reporting it would be
  manufacturing a finding out of a regex. 3 tokens.

Both are COUNTED and printed, so an operator sees what the gate declined to judge
instead of inferring it from a number that got smaller.

### 22.3 — the honest number

**49 of 119 servable stories** (`SERVABLE_STATUSES` + `ANSWERABLE_SQL`) contained at
least one of **17 words** that is decodable at that story's own stage and that the
current vocabulary does not permit there. 10,705 word tokens read, across titles and
bodies.

Every one of the 17 is genuine drift, not a census artefact: re-running §5.4 as
originally specified (frequency list ∩ CMUdict ∩ literal blocklist, no register
filter, no proper-noun filter) offers **all seventeen** at the stages they appear
at. Three classes, three remedies:

| class | words | stories | what it is |
|---|---|---|---|
| **REGISTER** | `amid` (9), `dragon` (3), `action` (2), `admit` (2), `national` (2), `international`, `problem`, `span` | 16 | adult register no stage offers and Dale-Chall does not vouch for. **The child-facing defect.** |
| **proper-noun-shaped** | `sam` (28), `dan` (5), `jack` (2), `tom` (2), `kim`, `pam` | 33 | capitalised at every occurrence in the whole pool, so used as a NAME |
| **wrong-stage** | `every` (10), `family`, `together` | 11 | child register, offered at SOME stage but not this one |

Three findings inside that table are worth more than the count:

1. **`amid` reached the question screen, not just the story.** Three servable
   `tap_word` rows at stage 1 offered it as an option: *"Tap the word that starts
   with the same sound as run."* → `["spin", "amid", "rocket"]`. `check_tap_word`
   requires every option to be a word OF THE STORY, so the questions inherited the
   defect from the bodies exactly as designed.
2. **The name class is a rules conflict, not adult register, and `SYSTEM_RULES`
   contains both halves of it.** Rule 1 says there are no options beyond the three
   lists; rule 6 says *"`Sam sat. A cat sat on Sam.` is correct output, not a first
   draft to improve on."* The frozen system prompt demonstrates a name the
   vocabulary it ships with does not permit. Not fixed here — see 22.9.
3. **`every` is the same defect from a DIFFERENT precomputed input.** It is not a
   register casualty at all: 21.2 moved the syllable cap from `min_syllables` over
   every CMUdict entry to `aligned_syllables` over the ones that actually align.
   `every` has a 2-syllable CMUdict entry but aligns at stage 5 only in three
   syllables, against a cap of two — so it went from offered to not-offered, and ten
   stage-5/6 stories still contain it. That is why the gate is about staleness in
   general and not about `amid`.

### 22.4 — the gate: `--check-vocabulary`, the SEVENTH

`python -m storyforge.warm --check-vocabulary` / `make vocab-check`. Two SQL
queries and the decoder; no API client, nothing spent, nothing written. Exits
non-zero while any servable story's title or body holds a word the current
vocabulary does not permit at its stage.

The help text states the boundary, because a gate whose scope is guessed at is how
five green checks coexisted with `amid`:

* **COVERS** pool drift after `make precompute` — a word the generator was offered
  when the story was written and is not offered now.
* **DOES NOT COVER** whether a word is DECODABLE (§1's 0.95 gate and
  `_is_decodable_for` own that, and this gate skips every token that fails it);
  whether a picture chip is well spent (`--check-story-words`); what a question SAYS
  (`--check-prompts`, `--check-mcq2`); or QUESTION text, whose vocabulary rule is
  `INSTRUCTION_WORDS` + decodability and not this one.

Two design decisions carry the weight:

* **The classifier may mis-sort; it may never forgive.** `classify_offence` decides
  the ORDER of the report and is never consulted for the exit code, so a
  misclassified word is still an offence. A class the gate forgave would be the
  fourteenth instance of this codebase's signature defect.
* **8.44's vacuity rule.** A vocabulary check over zero servable stories finds zero
  offences and exits 0 — on a fresh deploy, which is exactly when an operator would
  believe it. It refuses instead.

### 22.5 — sabotage

Nine sabotages, each against the source the tests cover, each confirmed red and
reverted:

| # | sabotage | went red |
|---|---|---|
| 1 | stop skipping words undecodable at the stage | 3 tests |
| 2 | count a possessive of a permitted word as drift | 1 |
| 3 | stop reading the title | 1 |
| 4 | report every offence and exit 0 | 2 |
| 5 | narrow the allowed set to a frequency window | 2 |
| 6 | remove the empty-pool refusal | 1 |
| 7 | make the exit code depend on the CLASSIFIER | 1 |
| 8 | drop `ANSWERABLE_SQL` from the gate's query | 1 |
| 9 | widen the permitted set by hand with `{amid, sam, dan}` | 2 |

Sabotage 5 initially caught only one test, because
`test_the_gate_reads_the_whole_stage_and_not_the_prompt_window` asserted the prompt
window is a *proper subset* — which stays true when the gate's own set is narrowed.
The test now asserts the gate's per-stage counts EQUAL `SELECT count(*) ... GROUP BY
stage_id`, and sabotage 5 fails it. That assertion only exists because the sabotage
run was done.

### 22.6 — remediation: 13 quarantined, 4 regenerated, coverage held

**Quarantined 13 stories** — the adult-register class — by `UPDATE stories SET
status = 'rejected'`, with the reason, the words, the previous status and the gate
name written to `gen_meta.vocabulary_quarantine`. At the TOP level of `gen_meta`,
never under `enrichment`: `_record_enrichment` replaces that sub-object wholesale by
design, which erased four rows of two real children's history once already (11.117).

**Nothing was deleted.** `answers.question_id` is `ON DELETE CASCADE` and
`questions.story_id` cascades from `stories`, so a DELETE here would erase a real
child's history to fix our defect. Verified after the write: 13 of 13 rows still
exist, 14 readings and 5 answers preserved. Two of the quarantined stories carry
recorded answers and both keep them.

Measured after: `amid` appears in **0** servable bodies and **0** servable questions.

**Coverage was checked before acting, not after.** Quarantining all 49 offenders
would have taken 18 of 33 pairs below target and two stages below
`STORIES_REQUIRED_AT_STAGE` — stage 5 to **zero** servable stories. Quarantining the
13 took three pairs to 2/1/2 and left all 11 stages above the §8 floor. Those three
pairs were then refilled: 4 stories generated over stages 8/10/11, **$0.45** actual
against a $0.15 `--dry-run` floor (one story was rejected and still cost). Both
gates green afterwards: `--check-coverage` **33 of 33 pairs, 11 of 11 stages**.

`dragon` was deliberately NOT quarantined. It is absent from `allowed_words` because
Dale-Chall's 1948 familiar-word list does not contain it, not because it is adult
register, and quarantining three dragons stories over the word `dragon` would be a
data fix disguised as a purge — see 22.9.

The 33 name-class and 11 wrong-stage stories are **held knowingly**. Regenerating
them at the pool's own observed mean (`observed_cost_per_story`: $0.03674 over 119
priced stories, not the $0.026 estimate) is **36 stories / $1.32**. Not spent.

### 22.7 — the finding this gate turned up on the way: titles are never validated

`run_pipeline` scores `draft_text(draft)`, which joins SEGMENT text and nothing
else. So `stories.title` — child-facing, on the reading view and in every
`StoryDTO` — has passed §13 moderation (17.1 routed it there, and stopped) and
**neither the decodability gate nor the vocabulary rule**, ever. 30 of the 49
offenders offend in the title.

This is a GENERATION-path gap, not pool drift, and it is a different defect with a
different owner. The gate reads titles anyway, reports them separately, and prints
the reason — because nothing else reads them at all. Not otherwise fixed here.

### 22.8 — the same class of staleness elsewhere: four inputs, four verdicts

`allowed_words` is not the only precomputed input. Each was checked against the pool
rather than assumed; the method is stated because the verdict is only worth what the
method is.

| input | verdict | how it is known |
|---|---|---|
| **`allowed_words`** | **table CURRENT, pool STALE** | `compute_rows` re-run in memory against the live `stages.max_syllables` produces 34,221 rows over 11 stages — **+0 / −0** against the live table, so `make precompute` has been run since the last data change. The POOL is the stale artefact, not the table. (`docs/STATE.md` still records 34,288 / 771 at stage 1; the live numbers are 34,221 / 769 — documentation drift, no code effect.) |
| **blocklist** | **CONSISTENT** | Both matchers applied to the pool, because they are deliberately different (11.6): `load_blocklist()` — the literals `moderate.blocklist_check` matches — finds **0** hits across every servable body and **0** across every servable question's prompt, payload and answer; `blocked_forms()` — literals plus inflections, what precompute filters on — finds **0** inflected-only hits. So the 11.1-11.5 morphology expansion, which found 50 live inflections in `allowed_words`, has no counterpart in the pool. |
| **sight-word set** | **CONSISTENT** | The 38 `sight_words` rows are identical, as `(stage, word)` pairs, to `data/seed/sight_words.csv`. And the pool depends on them correctly: re-scoring all 119 servable bodies with today's cumulative sight set gives a decodability that matches the stored `stories.decodability` to 4 d.p. on **119 of 119**, and **0** fall below the 0.95 gate. |
| **GPC inventory** | **CONSISTENT** | The 93 `gpcs` rows are identical, as `(gpc_id, stage_id)` pairs, to the decoder package's frozen CSV. This is the strongest evidence in the audit and it is the same measurement as the row above: the inventory HAS moved since parts of the pool were generated — the eleven-stage renumber (12.1-12.14, migration `f3a91c6d0e24`) and `wh`→/w/ (8.13) — and every stored `decodability` still reproduces exactly. Monotonicity (§5.7c) plus a migration that moved `stage_id` on the data as well as the ladder is why. |
| **moderation rules** | **RECEIPTS CONSISTENT; the JUDGEMENT cannot be verified from data** | All 119 servable stories carry a `gen_meta.moderation` receipt; **0** have `well_formed` NULL (6.24's defect, where the grammar half was computed and then dropped on write, is closed and stays closed); **0** are `approved = false` or `well_formed = false`. **That is the whole of what stored data can establish, and it is the weakest verdict here.** §13 layer 3 is a MODEL, its prompt has been rewritten four times (8.70-8.82), and every one of those passes broke a case that had been passing. Whether today's judge would still approve a body approved by the judge of two phases ago is not answerable by re-reading a receipt — it needs a re-run against the live judge over the 40-case fixture in BOTH directions (STATE invariant 5), at ~$0.14 a run plus the cost of re-judging 110 bodies. Not spent, and **not claimed**. Layers 1 and 2 are pure functions and both are clean today, which is the row above. |
| **`story_word_bank` / §5.5 band** | **STALE — already owned by another gate** | Not in this gate's scope but measured while here: **7 of 24** bank rows are eligible at NO stage at or above their own `min_stage` (`dragon`, `rocket`, `planet`, `comet`, `orbit`, `rabbit`, `forest`), and 248 persisted chip slots sit outside the 14.20 band. `--check-story-words` is the gate for it and 14.40-14.42 is the repair. |

### 22.9 — not fixed here, and why

- **`dragon` (3 stories) and the theme nouns generally.** The correct remedy is a
  DATA fix — add `dragon`, `rocket`, `planet` and their kind to
  `data/child-vocabulary.txt` and re-run `make precompute` — not a pool purge. Every
  five-year-old in the target audience knows the word `dragon`; its absence is a gap
  in a 1948 vendored word list, not a judgement about register. Adding words is
  monotone, so it can only shrink this gate's offence set, never grow it, and it
  would also free 43 of the wasted picture chips 14.42 had to leave. NOT done here
  because it rewrites `allowed_words` on the shared database while three other agents
  are running against it, and because widening the child-vocabulary list is a
  curriculum decision.
- **The 33 name-class stories.** The rules conflict in `SYSTEM_RULES` (rule 1 versus
  rule 6's own worked example) has to be resolved before it is clear whether these
  are defects at all. Two coherent resolutions exist — a small curated name allowlist
  fed into the prompt and into the permitted set, or an instruction to use only names
  the allowlist already contains — and both are prompt changes, which invalidate
  every cached prefix (STATE invariant 10). Named, priced ($1.32 for all 36 held
  stories), not chosen.
- **`--check-vocabulary` is RED and stays red**, at 36 of 110 after the quarantine
  and refill. Green is reachable only by spending the $1.32 or by making one of the
  two decisions above.
- **Question text is not covered.** An `mcq2` prompt and its options are model
  authored and are gated on `readable()` — decodability plus sight words plus story
  words plus `INSTRUCTION_WORDS` — and not on `allowed_words` at all. So the register
  filter does not reach them by design, and extending this gate to them would be
  asserting a rule that was never made. Stated in the help text rather than silently
  omitted.
