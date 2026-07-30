# Decision: drop stage 1 and renumber to 11 stages — EXECUTED

**Status:** authorised by the user 2026-07-30, conditional on the vocabulary
filter measurement; condition checked and met; **EXECUTED 2026-07-30**
(DEVIATIONS 12.1-12.14, migration `f3a91c6d0e24`).

## The executed mapping

Old stage N -> new stage N-1 for N >= 2; old stages 1 and 2 merged into new
stage 1. Cumulative inventories make new stage K identical to old stage K+1.
Appendix A's stage table in the build doc is NOT rewritten (the spec is the
frozen mandate; every §-reference in five phases of DEVIATIONS resolves against
it) — this table is its successor:

| new | name | was (Appendix A) |
|---|---|---|
| 1 | Letters & doubles | 1 (CVC) + 2 (Doubles & x), merged |
| 2 | Digraphs | 3 |
| 3 | Endings | 4 |
| 4 | Silent e | 5 |
| 5 | Vowel teams | 6 |
| 6 | R-controlled | 7 |
| 7 | Diphthongs | 8 |
| 8 | Soft & silent | 9 |
| 9 | Two syllables | 10 |
| 10 | Suffixes | 11 |
| 11 | Review | 12 |

The kill condition was checked on measured counts before execution: under the
corrected vocabulary filter the twelve stages held 78 / 771 / 1,383 / 1,934 /
2,149 / 3,197 / 3,461 / 3,899 / 4,333 / 4,378 / 4,386 / 4,397 words — stage 1
alone below §18's 150 floor, every other stage clearing it by 5x or more. After
the renumber every stage holds 771-4,397. A second, independent ground surfaced
before execution (DEVIATIONS 12.1): old stage 1's inventory could not express
negation at all (no word contains `o`), so its stories could never state an
obstacle — the rung was structurally, not just lexically, unviable.

## The trigger
`data/frequency-en.csv` is SUBTLEX — adult film-subtitle frequency. It proves a
word is common in adult speech and says nothing about whether a five-year-old
knows it, which is why stage 2 currently offers `amid` (rank 7528, and it appeared
in nearly every sentence of a user's test story), `lebron`, `nixon`, `blunt`,
`classics`. §5.4 named that corpus and it is the wrong corpus for the audience.

Replacing it with a real child-vocabulary filter (Dale-Chall, Fry, or AoA norms)
shrinks every stage. §18 sets a floor of **150 words at stage 1**, and stage 1
currently holds **157** — so there is effectively no room.

## The decision
If the correct filter puts stage 1 below the floor: **delete stage 1 and renumber.
Old stage 2 becomes the new stage 1. Eleven stages total.**

This works because stage inventories are CUMULATIVE — old stage 2 already contains
every stage-1 GPC (`s a t p i n m d`) plus `g o c k e u r h b f l`, `ck ss ll ff`
and `x`. So the new stage 1 is a LARGER starting inventory, which is precisely what
buys back the word count.

**The accepted cost:** a true beginner who knows only `s a t p i n` loses a rung of
their own. The user has accepted this explicitly. It is defensible on the grounds
that a 157-word stage which needed `amid` to fill itself was not a viable rung.

**The kill condition:** if the filter also guts stage 2 and beyond, renumbering by
one does not save us — the FILTER needs rethinking, not the ladder. The vocabulary
agent has been asked to report counts for all 12 stages so this is decidable on
numbers rather than hope.

## Blast radius, if executed
Wider than it looks. Everything below has to move together or the guarantee breaks:

- `data/seed/stages.csv` — 12 rows to 11, and `target_len_min/max` + `max_syllables`
  shift with them
- `data/seed/gpcs.csv` — every `stage` value decrements; old stages 1 and 2 merge
- `data/seed/sight_words.csv` — same decrement
- `MAX_STAGE = 12` constants (`precompute.py`, `mastery.py`, tests, the eval CLI's
  `1..12` default)
- **§5.7 golden tests name specific stages** — `cat@1`, `fox@2`, `singer@6`,
  `city@9`, and `city@8` missing exactly `{c|S}`. These are the decoder's
  ACCEPTANCE CRITERIA per Appendix B. They must be re-derived, not merely edited,
  and `justify-test-edit` applies to every one.
- The anti-golden vacuity ratchet is measured AT STAGE 12 (44.75%, ceiling 50%) —
  the new top stage is a different set, so the number moves and the ceiling
  argument has to be re-made
- `docs/EVAL.md` and the §14 matrix (12 x 3 x 5 = 180 cells becomes 11 x 3 x 5)
- §17's demo is **stage 3** — it becomes stage 2 and the "611 words" figure
  (already wrong; the real number is 2,593) needs recomputing again
- The build doc's Appendix A stage table
- `docs/STATE.md`, `docs/DEPLOY.md`, `README.md`
- **Live production data**: `kids.stage_id`, `stories.stage_id`, `allowed_words`,
  `gpc_stats`, `eval_run_items`. Prod currently holds 44 stories and a handful of
  kids, so rebuilding is cheaper than migrating — but a migration is still needed
  for the kids, or a child silently jumps a level.

## Sequencing
1. Vocabulary agent reports per-stage counts under the new filter (in flight).
2. If stage 1 alone is short → execute the renumber as one atomic change, data and
   docs and goldens together, with the goldens re-derived from the inventory.
3. If stages 2+ are also short → do NOT renumber; the filter is wrong.
4. Re-precompute, re-warm, re-run `make eval`, re-verify the §17 demo live.
