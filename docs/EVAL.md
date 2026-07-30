# Story Forge — §14 eval report

**The question this document answers:** §0 claims that a constrained LLM plus a pure-function gate yields reliably decodable stories. Does it?

**Answer: YES on decodability, and the gate is the reason.** Of the 180 stories the model actually produced across all 12 stages and 3 themes, **180 reached the 95% gate and 0 did not**. The worst single story measured 95.0%. Median 100.0%, mean 98.9%. The rewrite loop did most of that work at depth 0 — p95 was 2, against §1's budget of 2 — which is the budget exactly, with no headroom. The gate held and no story shipped below it, but at p95 = 2 the rewrite loop is carrying weight §6 assigns to the allowlist, and a further prompt change that costs one more rewrite would breach §1 rather than merely crowd it. Read this as a pass with the margin spent, not as a comfortable one. No story in the matrix contained a single out-of-vocabulary word, and none exceeded the story-word cap.

**Total spend: $5.41** against a $15.00 cap, at 2026-07-29 prices. $0.0315 per accepted story, excluding enrichment (section 6).

Every figure below is read out of the run's own `reports/eval-<ts>.json` by `scripts/eval_doc.py`, not transcribed by hand. `reports/` is gitignored (run output, not source), which is why the numbers are duplicated into this committed file. The prose is a reading of them; the figures are not editable prose.

---

## 1. The run

```
cd services/api && python -m storyforge.eval run \
  --stages 1..12 --themes all --n 5 --cold \
  --effort medium --pricing-date 2026-07-29 --budget 15.0 --concurrency 6
```

`make eval` runs the first two lines — the matrix and `--cold`. It does NOT pass `--effort`, `--budget` or `--concurrency` (the values above are the defaults it inherits) and it does NOT pass `--pricing-date`, which defaults to the day of the run: the date in the line above is the date THIS run priced at, not a constant. A run on or after 2026-09-01 will price at Sonnet 5's standard rate with no flag change, which is why the date is printed rather than assumed (AUDIT-PHASE7 F10).

| what | value |
|---|---|
| `eval_runs.id` | 114 |
| started / finished (UTC) | 2026-07-30T04:18:35+00:00 / 2026-07-30T04:36:24+00:00 |
| status | `completed` |
| pipeline time (summed per story) | 76 min |
| git commit | `83d1cc8` — **working tree DIRTY** (29 path(s), including the harness itself), so this commit is NOT the code that ran |
| harness code sha256 | `72fe5998efa33b9b…` (`storyforge/eval.py`; verify with `git show <commit>:… | shasum -a 256`) |
| vocabulary the gate ran against | 93 GPCs `acd967613cea…` · 38 sight words `2b450a0d4c0e…` · 64,296 allowed words · 126,052 CMUdict entries |
| per-HTTP-attempt generation timeout | 240s x up to 3 attempts (`SDK_MAX_RETRIES=2`) |
| generation model (config's claim) | `claude-sonnet-5` |
| generation model (the items' record) | `claude-sonnet-5` x180 — one model, confirmed per item |
| fast model (moderation) | `claude-haiku-4-5` |
| pricing date | `2026-07-29` — Sonnet 5 introductory rate, expires 2026-08-31 |
| generation price | $2.0 in / $10.0 out per Mtok |
| fast price | $1.0 in / $5.0 out per Mtok |
| stages | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 |
| themes | animals, dragons, space |
| stories per (stage, theme) | 5 |
| cells planned | 180 |
| gate (never lowered, §0) | 95% decodability AND 0 OOV AND <= 3 distinct story words |
| rewrite budget (§7) | 3 |
| prompt allowlist size (§6) | 400 |
| output effort | medium |
| concurrency | 6 concurrent cells, batched by stage so the per-stage cache prefix still pays — DEVIATIONS 8.100 |
| cache bypassed (`--cold`) | True |
| ENRICHING run | False (DEVIATIONS 7.45) |
| python | 3.12.13 |
| spend cap | $15.00, enforced on the running actual |

## 2. §1 item 2 — the machine-checkable form of the thesis

§1 item 2: *"Eval harness reports >=95% decodability (excluding declared story words) across all 12 stages x 3 themes x 5 stories, with rewrite-loop depth p95 <= 2."* Checked as four assertions, each with its own exit code path (DEVIATIONS 7.49):

| criterion | result | evidence |
|---|---|---|
| every story reaches >=95% decodability | PASS | 180/180 stories at or above the gate (0 below) |
| p95 rewrite depth <= 2 | PASS | p95 = 2, max = 3 |
| the matrix completed | PASS | 180 of 180 planned cells attempted |
| every planned cell produced a story | PASS | 180 of 180 cells produced text (0 api_fail) |

**Overall: PASS.** `make eval` exited 0.

A moderation rejection is deliberately *not* one of these criteria. It is a content verdict on a story that already met the decodability criterion, so folding it in would make a §13 judgement look like a decoder failure. It is reported in full in section 8 instead.

## 3. Headline numbers

| metric | value |
|---|---|
| stories attempted | 180 |
| stories produced (the model returned text) | 180 |
| stories ACCEPTED (cleared gate **and** moderation) | 172 |
| gate + moderation pass rate | 95.6% |
| fallback rate (§7 — a live request would have served the warm pool) | 4.4% |
| mean decodability | 98.9% |
| median decodability | 100.0% |
| worst single story | 95.0% |
| **stories that never reached the 95% gate** | 0 |
| rewrite depth p50 / p95 / max | 0 / 2 / 3 |
| attempts per accepted story | 1.471 |
| total cost | **$5.4130** |
| cost per accepted story | **$0.0315** |
| cost per attempted story | $0.0301 |
| cold generation p50 / p95 | 18.9s / 64.1s |
| OOV word instances | 0 |
| stories over the story-word cap | 0 |

§7 outcome taxonomy: `{"accepted": 172, "moderation_fail": 8}`

## 4. Decodability — the distribution, not the mean

A mean hides the shape: twenty stories at 100% and one at 20% has a healthy mean and one child who cannot read their story. Quantiles over every produced story:

| min | p05 | p25 | p50 | mean | p95 | max |
|---|---|---|---|---|---|---|
| 95.0% | 95.5% | 98.0% | 100.0% | 98.9% | 100.0% | 100.0% |

| decodability bin | stories | share |
|---|---|---|
| <50% | 0 | 0.0% |
| 50-80% | 0 | 0.0% |
| 80-90% | 0 | 0.0% |
| 90-<95% | 0 | 0.0% |
| 95-<100% | 65 | 36.1% |
| 100% | 115 | 63.9% |

**No story finished below the gate.** Every one of the 180 stories the model produced reached >=95% decodability within the rewrite budget.

### Per stage

| stage | n | accepted | pass rate | min | p50 | mean | max | below gate | rw p95 | mean words | in length range | TTR | $/accepted | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 15 | 14 | 93.3% | 96.0% | 100.0% | 99.2% | 100.0% | 0 | 0 | 38.5 | 100.0% | 0.47 | $0.0173 | 16s |
| 2 | 15 | 13 | 86.7% | 96.2% | 100.0% | 99.5% | 100.0% | 0 | 2 | 46.2 | 86.7% | 0.53 | $0.0202 | 22s |
| 3 | 15 | 15 | 100.0% | 95.0% | 100.0% | 98.5% | 100.0% | 0 | 1 | 38.9 | 93.3% | 0.59 | $0.0137 | 23s |
| 4 | 15 | 15 | 100.0% | 95.5% | 100.0% | 98.5% | 100.0% | 0 | 2 | 42.1 | 93.3% | 0.59 | $0.0201 | 38s |
| 5 | 15 | 13 | 86.7% | 95.1% | 100.0% | 98.8% | 100.0% | 0 | 2 | 82.5 | 100.0% | 0.47 | $0.0432 | 52s |
| 6 | 15 | 14 | 93.3% | 95.5% | 100.0% | 98.7% | 100.0% | 0 | 1 | 84.9 | 100.0% | 0.53 | $0.0250 | 40s |
| 7 | 15 | 15 | 100.0% | 95.2% | 100.0% | 98.8% | 100.0% | 0 | 1 | 94.2 | 93.3% | 0.49 | $0.0283 | 45s |
| 8 | 15 | 15 | 100.0% | 97.6% | 100.0% | 99.8% | 100.0% | 0 | 1 | 82.3 | 100.0% | 0.50 | $0.0217 | 29s |
| 9 | 15 | 14 | 93.3% | 95.1% | 100.0% | 98.3% | 100.0% | 0 | 2 | 167.1 | 93.3% | 0.42 | $0.0469 | 76s |
| 10 | 15 | 14 | 93.3% | 96.9% | 100.0% | 99.0% | 100.0% | 0 | 2 | 151.3 | 100.0% | 0.40 | $0.0458 | 65s |
| 11 | 15 | 15 | 100.0% | 95.6% | 100.0% | 99.2% | 100.0% | 0 | 1 | 152.3 | 100.0% | 0.40 | $0.0521 | 82s |
| 12 | 15 | 15 | 100.0% | 95.6% | 100.0% | 98.9% | 100.0% | 0 | 1 | 153.5 | 86.7% | 0.37 | $0.0439 | 62s |

### Per theme

| theme | n | accepted | pass rate | min | p50 | mean | max | rw p95 | $/accepted |
|---|---|---|---|---|---|---|---|---|---|
| animals | 60 | 56 | 93.3% | 95.0% | 100.0% | 99.0% | 100.0% | 2 | $0.0318 |
| dragons | 60 | 56 | 93.3% | 95.1% | 100.0% | 99.2% | 100.0% | 2 | $0.0335 |
| space | 60 | 60 | 100.0% | 95.3% | 100.0% | 98.7% | 100.0% | 2 | $0.0292 |

## 5. The rewrite loop — how much retry pressure the gate needed

§6 is explicit that the primary lever is allowlist-constrained generation, not retry pressure. Depth 0 means the first draft passed the gate untouched.

| rewrite depth | stories |
|---|---|
| 0 | 108 |
| 1 | 56 |
| 2 | 15 |
| 3 | 1 |

mean 0.49 · p50 0 · p95 2 · max 3 · attempts per accepted story 1.471

## 6. Cost

| metric | value |
|---|---|
| pricing date (explicit, per `pricing.price_for`) | `2026-07-29` |
| **total actually spent** | **$5.4130** |
| spend cap | $15.00 — not reached |
| GENERATING + REWRITING (Sonnet) | $4.7559 |
| MODERATING (Haiku) | $0.6571 |
| per attempted story | $0.0301 |
| **per ACCEPTED story** | **$0.0315** |
| Sonnet uncached input tokens | 114,866 |
| Sonnet cache-WRITE tokens (1.25x) | 171,923 |
| Sonnet cache-READ tokens (0.1x) | 549,950 |
| Sonnet output tokens | 398,638 |
| Haiku input / output tokens | 609,513 / 9,509 |

**The pricing date matters.** Sonnet 5's introductory $2/$10 rate expires 2026-08-31. The same token counts re-priced at the standard $3/$15 (2026-09-01) cost **$7.7909**, a 1.44x increase — less than 1.5x because Haiku's price does not change, and the gap between the two ratios is the share of the bill that is generation rather than moderation.

Pre-flight projection (no API call, DEVIATIONS 7.44): $3.25 with no rewrites, $6.49 with one rewrite each, $12.99 worst case. Actual came in at $5.41.

**What this cost excludes.** The harness does not run §7's ENRICHING node (DEVIATIONS 7.45): no QUESTION_GEN, no SCENE_TAGGING, no question moderation. DEVIATIONS 5.15 measured enrichment at **45-48% of a warm story's total cost**, so the cost of a story a child could actually be *served* is close to double the $0.0315 above — call it $0.0598. Stated here rather than left for a reader to discover.

## 7. Latency per node

| §7 node | p50 | p95 |
|---|---|---|
| GENERATING (wall clock, retries INCLUDED) | 8151 ms | 60224 ms |
| REWRITING (per rewrite) | 11603 ms | 45514 ms |
| VALIDATING (the gate — pure function) | 1.65 ms | 4.82 ms |
| MODERATING (Haiku) | 1505 ms | 2790 ms |
| **whole pipeline** | **18877 ms** | **64082 ms** |

**Two GENERATING numbers, because one number was measuring two things.** The row above is wall clock around a single SDK call, and that call retries up to 2 time(s), so it can contain several HTTP attempts. Against a per-attempt budget of 240s (recorded per item), **0 of 180** generations (0.0%) ran longer than one attempt could — each of those contains at least one attempt that was killed by the clock and paid for. Over the 180 that did not, p50 is **8151 ms** and p95 **60224 ms**, against the wall-clock p95 of 60224 ms.

The subset is named *no clock-driven retry*, not *single attempt*: a fast 429 or 5xx retry still hides inside a fast call, so it is a bound on retry-free latency rather than a proof of one attempt (DEVIATIONS 8.35). Exact per-attempt counts need `x-stainless-retry-count` off the raw response, which is a change to the generation path and is named there as deferred.

§1 item 6 asks for cold generation p95 < 12s. Measured p95 is **64.1s**, which **does NOT meet** it. Per-stage p95 is in the table in section 4. The miss is NOT attributed here to long stories: DEVIATIONS 8.6/8.8 measured latency as a function of output tokens (r = +0.997) and barely of stage (r = +0.50), and the split above shows how much of this figure is retry rather than generation. §1.6 is recorded as unmet and unreachable as written — 12s buys roughly 1,200 tokens and a stage-12 story is 1,700-3,300 — and the criterion is not relaxed to match. Warm-pool serving is unaffected either way: §7 makes the demo path a database read, and that latency is not measured here.

The VALIDATING row is the thesis's economics in one line: the gate that makes the guarantee is roughly four orders of magnitude cheaper than the generation it judges, and costs nothing per call.

## 8. Moderation (§13)

| verdict class | stories |
|---|---|
| approved | 172 |
| blocklist | 1 |
| content | 3 |
| grammar | 4 |

- `blocklist` — §13 layer 1, deterministic, free, no API call.
- `grammar` — the well-formedness half of the verdict (DEVIATIONS 4.12), which `moderate()` folds into `approved` and marks with a reason prefix.
- `content` — the layer-3 safety judgement.
- `not_reached` — the story never cleared the gate, so moderation never ran.

**Hazard versus harm cannot be read off the verdict.** STATE invariant 5 requires moderation to separate a storybook hazard (a fire-breathing dragon is the premise) from actual harm, and a spike either way is the signal. But the verdict carries no field for that distinction — the only record is a sentence the model wrote. So every content rejection's reason is quoted verbatim below and the `hazard?` column is a keyword HEURISTIC pointing at reasons worth reading, not a measurement (DEVIATIONS 7.46).

| stage | theme | hazard? (heuristic) | reason (verbatim) |
|---|---|---|---|
| 2 | animals | harm-shaped | The rabbit and elephant hit a rock and fell, which describes injury and physical harm to characters. |
| 2 | dragons | harm-shaped | A dog hit a rock next to the cat, which could be interpreted as the dog hitting the cat, causing harm. |
| 5 | animals | suspect | The rabbit 'went off to get fire' is unsafe: fire is a hazard that is never resolved or explained. |

### Grammar rejections, verbatim

| stage | theme | note |
|---|---|---|
| 1 | animals | "Sam and the rabbit spin." -> "Sam and the rabbit spins." Two subjects joined by 'and' require plural verb. |
| 5 | dragons | "The dragon did not like the case." -> "The dragon did not like the cave." Homophone or sound-alike word error. |
| 9 | dragons | The dragon use its wings. -> The dragon uses its wings. Subject-verb agreement: plural verb with singular subject. |
| 10 | animals | "Now the rabbit had this place, and it and had its house." -> "it and had" is a stray word; should be "it had" (extra conjunction). |

### Blocklist rejections (layer 1)

| stage | theme | blocked words |
|---|---|---|
| 6 | dragons | fight |

## 9. The decoder's own disclosed limitations (§5.3, §18)

§5.3 requires these to be *logged in the eval report, not silent behaviour*. §18 names the alignment/schwa problem as the highest technical risk and says a rising unalignable rate is the signal that the inventory is too small.

**Denominator: SCORED words — 9,568 of 17,005 word tokens.** A sight or story word is classified before any alignment is attempted (`decoder/validate.py`), so it can carry none of these flags; the first version of this report divided by every word token, which put roughly twice as many words in the denominator as could ever appear in the numerator and reported §18's designated alarm at about half strength (AUDIT-PHASE7 F6, DEVIATIONS 8.34). The rates below are recomputed from this run's own recorded counts; the recorded rows were not edited. The population rate is flags over scored words; the mean-per-story column is beside it because a mean weights a 30-word stage-1 story equally with a 200-word stage-12 one.

| flag | what it means | total instances | rate over SCORED words | mean per story |
|---|---|---|---|---|
| `schwa_relaxed` | §5.2.6 let an unstressed vowel match its citation GPC | 885 | 9.2% | 8.1% |
| `ambiguity_flag` | more than one legal alignment existed; any was taken | 2384 | 24.9% | 25.0% |
| `heteronym_flag` | multiple pronunciations, only some decodable — §5.2.5 may over-credit | 683 | 7.1% | 6.0% |
| `alignable=False` | **the §18 alarm** — the inventory cannot spell the word at any stage | 71 | 0.7% | 0.5% |
| non-minimal missing set | the minimal-missing-GPC search hit its bound and approximated | 0 | — | — |

Words the inventory cannot spell at any stage, with occurrences: `{"around": 10, "near": 7, "could": 5, "cold": 3, "hi": 3, "liked": 3, "many": 3, "tall": 3, "find": 2, "leaves": 2, "new": 2, "put": 2, "quiet": 2, "walk": 2, "above": 1, "astronaut": 1, "behind": 1, "come": 1, "feathers": 1, "friends": 1, "full": 1, "going": 1, "great": 1, "heard": 1, "knew": 1, "other": 1, "over": 1, "ready": 1, "scare": 1, "told": 1, "what": 1}`

**`of` is absent from that list, and that is the previous matrix's one actionable finding closing.** It was the most frequent word the 92-GPC inventory could not spell at any stage and was not a sight word either, so every story reaching for it spent part of its 5% violation budget; it is a stage-2 sight word now, with `so`, `do`, `be` and `from` (docs/DEVIATIONS.md 8.10–8.13). Whatever remains above is the honest residue — 8.18 records which of those words were measured against the inventory ratchet and deliberately NOT added.

### Per stage (every rate over SCORED words)

| stage | scored words | schwa | ambiguity | heteronym | unalignable | unalignable words |
|---|---|---|---|---|---|---|
| 1 | 376 | 10.4% | 29.8% | 0.0% | 0.0% | — |
| 2 | 459 | 5.2% | 24.2% | 2.4% | 0.0% | — |
| 3 | 356 | 1.7% | 29.8% | 2.0% | 0.0% | — |
| 4 | 389 | 6.9% | 25.2% | 4.1% | 0.3% | heard |
| 5 | 772 | 7.3% | 22.9% | 4.0% | 0.3% | cold, come |
| 6 | 760 | 7.4% | 23.0% | 9.7% | 0.3% | near, what |
| 7 | 809 | 14.3% | 28.7% | 7.0% | 0.6% | near, new, walk |
| 8 | 682 | 5.9% | 22.9% | 7.9% | 0.1% | around |
| 9 | 1436 | 11.4% | 26.0% | 8.6% | 1.7% | above, around, astronaut, behind, cold, could, friends, full, going, great, hi, knew, many, near, other, quiet, scare, tall, told |
| 10 | 1171 | 8.8% | 23.2% | 7.7% | 0.9% | around, hi, leaves, liked, near, ready, tall |
| 11 | 1216 | 11.3% | 25.7% | 8.9% | 0.9% | around, could, feathers, find, near, new, put |
| 12 | 1142 | 10.2% | 22.7% | 9.7% | 1.1% | around, cold, could, leaves, liked, many, over, put, quiet, tall, walk |

## 10. OOV

§5.3: CMUdict is an adult lexicon, so a missing entry fails the word — which is the conservative direction, but it is a real limit. §7's gate requires **zero** OOV, so any OOV word forces a rewrite or a rejection.

0 OOV word instance(s) across 0 of 180 produced stories.

## 11. Story words (§4, §5.5)

The cap is 3 **distinct** words per story. STATE invariant 3: a decodable reader repeats deliberately, so occurrences are uncapped and only the number of new picture-chip words a child must absorb is limited. §0's untrusted-generator rule bites here — the model's claim about which story words it used is intersected with the words actually offered, or it could shrink its own decodability denominator (STATE invariant 2).

| metric | value |
|---|---|
| stories over the cap | **0** |
| stories using no story word | 23 |
| total story-word occurrences | 1949 |

| distinct story words used | stories |
|---|---|
| 0 | 23 |
| 1 | 12 |
| 2 | 20 |
| 3 | 125 |

- `animals`: {"rabbit": 54, "forest": 48, "burrow": 45, "elephant": 1, "whiskers": 1}
- `dragons`: {"cave": 41, "dragon": 41, "fire": 19, "wings": 19, "treasure": 5, "knight": 1}
- `space`: {"rocket": 54, "moon": 44, "stars": 43, "astronaut": 10, "planet": 1}

## 12. Length compliance and register

| metric | value |
|---|---|
| inside the stage's target range (of ATTEMPTED cells) | 95.6% |
| inside the stage's target range (of PRODUCED stories) | 95.6% |
| median word count | 84 |
| mean type-token ratio | 0.478 |

Both denominators are printed because they answer different questions, and an unlabelled one is AUDIT-PHASE7 F9 (DEVIATIONS 8.34). A cell that produced no text is not length-compliant, so the ATTEMPTED figure is the one that counts availability honestly; the PRODUCED figure is the generation quality of the text that exists. The same caveat applies to the per-stage “pass rate” column in section 4, which is over attempted cells.

§18 predicts the early stages will read "Sam sat. A cat sat." and calls that authentic decodable-reader register rather than a defect. The type-token ratio is reported because §14 asks for it, not as a quality target: a low ratio at stage 1 is the format working.

**Length compliance is the weakest number in this report, and it is a generation defect rather than a decoder one.** The per-stage table in section 4 shows it collapsing as the target grows: the stages asking for 120-200 words came back with a mean in the 89-102 range, so the model consistently under-delivers length at the top of the ladder while staying comfortably inside the gate. Decodability is unaffected — a short story is still a decodable story — but a stage-12 reader is being handed roughly half the text §6 specifies. §18’s knob order for generation compliance ends at “shorten target length”; this is the opposite problem and is not covered there.

## 13. What this run wrote to the database

| table | what |
|---|---|
| `eval_runs` | 1 row — id 114, status `completed` |
| `eval_run_items` | 180 rows, one per attempted story |
| `stories` | 180 rows at **`status='eval'`** (+ their `story_segments`) |
| `questions` / `readings` / `answers` / `gpc_stats` | **untouched** |

**Nothing this run wrote can be served to a child, whether it passed or not.** That is structural, not a promise: `api/stories.py::SERVABLE_STATUSES` is the allowlist `("warm", "complete")` that both `_SERVE_SQL` and `GET /api/stories/{id}` filter on, and it does not name `eval` (DEVIATIONS 7.41). The servable pool measured **119 stories before this run and 119 after**.

Verified after the run rather than asserted: `tests/test_eval.py` plants an `eval` story alone at the top of the §7 ladder — the row `_SERVE_SQL` would pick first if the status were servable — and checks `POST /api/stories/request`, `GET /api/stories/{id}` and `POST /api/readings`. Adding `"eval"` to `SERVABLE_STATUSES` turns those tests red and the request endpoint hands back the fixture, which is what makes them an experiment. Separate tests assert that no `eval` row ever acquires a `last_served_at`, a question row or a reading.

## 14. What this eval does NOT measure

Stated rather than omitted, because a report that lists only what it found is not evidence about what it missed.

1. **Enrichment.** QUESTION_GEN, SCENE_TAGGING and question moderation are not run (DEVIATIONS 7.45), so their cost, latency and failure rate are absent, and so is the `sound_targets` tier fallback rate DEVIATIONS 5.16 wired `gen_meta` to expose. The cost figures understate a servable story by roughly the 45-48% DEVIATIONS 5.15 measured.
2. **Hazard versus harm.** Machine-underivable from the verdict (section 8). The verbatim reasons are the evidence; the heuristic column is not a measurement.
3. **Whether a moderation rejection was CORRECT.** The harness counts rejects. It has no ground truth, so a false positive and a true positive look identical in the numbers. Reading the quoted reasons is the only check, and section 8 does exactly that.
4. **Warm-pool serve latency.** §1 item 6's second half ("cache/warm-pool serve < 300ms") is a property of `api/stories.py`, not of the pipeline. This harness never calls that endpoint. DEVIATIONS 7.23 measured `validate_text` at a median 0.47 ms against that budget separately.
5. **Branching (§10).** The generator produces a linear spine, so there are no branch points to measure.
6. **Latency under load.** Sequential by design (DEVIATIONS 7.48) so the p95 answers §1 item 6. Concurrency, rate limits and the in-process limiter (DEVIATIONS 4.10) are untested here.
7. **Same-prompt variance.** Five stories per cell measure variance across *generations*, not the stability of one prompt: temperature is not controllable on this model (STATE invariant 9), so a re-run will produce different text and should be compared as a distribution, not diffed.
8. **The admin portal view.** §14 requires the portal to read these tables rather than duplicate the logic. The tables and the CLI exist; §9's `GET /api/admin/eval-runs` endpoints and the §11 admin views are Phase 8 and are **not built**.
9. **Whether the persisted segments reconstruct the text that was scored.** `persist_eval_story` writes `_split_segments(final_text)` and nothing checks the round-trip, which is the exact shape of the `warm.persist` grammar defect (STATE, DEVIATIONS 4.12). The Phase 7 audit verified the round-trip holds for all 169 stories, so this is a missing GUARD rather than a live defect — but the only thing that would catch a regression is `tests/test_eval.py::test_the_gate_still_agrees_with_what_the_harness_recorded`, and that test compares against the CURRENT vocabulary.
10. **Whether the untrusted-generator guard ever fired.** §11's intersection is what stops the model shrinking its own denominator, and the model's raw `story_words_used` claim goes to the Langfuse span but to no column, so the run can show the guard is sound (0 stories carry an unoffered story word) and cannot show it did work (AUDIT-PHASE7 F12).
11. **The selection effect of the cells that produced nothing.** Decodability is measured on the cells that produced text; when some did not, they are not a random sample — the Phase 7 audit measured the resulting bias for this matrix at roughly 0.03 SD and bounded the worst case, and that argument belongs with the numbers rather than being asserted (AUDIT-PHASE7 F8).

## 15. Re-running and comparing

The run is reproducible in configuration, not in output — the model is not deterministic. `eval_runs.config` holds the matrix, the model ids, the pricing date, the gate, the rewrite budget, the timeouts, the git commit **with a dirty-tree flag and a content hash of the code that ran**, and **a fingerprint of the vocabulary the gate was computed against** — so two runs are comparable as distributions, and a reader can tell when they are not (DEVIATIONS 8.32-8.33). Each item additionally records the model every call was made with and the price sheet applied to it, so no claim about the model rests on the config table alone (8.30). `--pricing-date` is explicit precisely so a cost figure regenerated later cannot silently disagree with the one printed here.

`--resume RUN_ID` restores the matrix parameters and **verifies** everything it cannot restore, refusing with exit code 3 rather than continuing one matrix under a different model, gate, price sheet, timeout or vocabulary (8.31). Comparing two runs starts with their `config.vocabulary` and `config.provenance.code_sha256`: if either differs, the two distributions are measurements of different things.

```
make eval                                  # the full §1 matrix, ~$5.41, ~76 min of pipeline time
make eval EVAL_ARGS="--estimate-only"      # project the spend, no API call
make eval-smoke                            # 4-story regression gate, ~$0.06

# this document, regenerated from the run's own JSON — never hand-edited
services/api/.venv/bin/python scripts/eval_doc.py reports/eval-<ts>.json docs/EVAL.md
```

_Generated by `scripts/eval_doc.py` from `eval-20260730T041835Z.json`._

