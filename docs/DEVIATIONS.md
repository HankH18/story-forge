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
