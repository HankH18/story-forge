# Story Forge — §14 eval report

**The question this document answers:** §0 claims that a constrained LLM plus a pure-function gate yields reliably decodable stories. Does it?

**Answer: YES on decodability, and the gate is the reason.** Of the 169 stories the model actually produced across all 12 stages and 3 themes, **169 reached the 95% gate and 0 did not**. The worst single story measured 95.0%. Median 100.0%, mean 99.4%. The rewrite loop did most of that work at depth 0 — p95 was 1, against §1's budget of 2 — so this is allowlist-constrained generation succeeding, not retry pressure grinding the model into compliance (§6's stated order of levers). No story in the matrix contained a single out-of-vocabulary word, and none exceeded the story-word cap.

**`make eval` nonetheless exits non-zero, and the reason is availability, not decodability.** 11 of 180 cells never produced any text: §7 sets the generation node's timeout at 30s, the SDK retries twice, and at the 120-200 word stages a single generation frequently needs longer than 30s — so those cells burned 3 x 30s and raised `APITimeoutError`. That is a real defect and it is reported as one. It is not evidence against §0's thesis, and this report is written so that neither reading can borrow the other's number: section 2 keeps the two kinds of criterion on separate lines.

**Total spend: $2.80** against a $15.00 cap, at 2026-07-29 prices. $0.0178 per accepted story, excluding enrichment (section 6).

Every figure below is read out of the run's own `reports/eval-<ts>.json` by `scripts/eval_doc.py`, not transcribed by hand. `reports/` is gitignored (run output, not source), which is why the numbers are duplicated into this committed file. The prose is a reading of them; the figures are not editable prose.

**Provenance.** This matrix was generated in 2 sitting(s) (DEVIATIONS 7.51). The first invocation was killed by its launcher at cell 154 of 180. Every `eval_run_items` row is committed as its story completes, so `--resume` ran only the remainder, against the **stored** config — same model, same gate, same `n`, same pricing date, enforced by the code rather than by care. It is one matrix, not two runs stapled together.

A further 2 invocation(s) re-derived this report from the recorded measurements without calling the API at all (`--resume` on a complete run is idempotent, and re-finalising it reproduced the identical verdict and exit code — which is the cheapest available check that the report is a pure function of the data). Full trail: `[{"at": "2026-07-29T23:16:23+00:00", "git_commit": "559f102", "items_already_recorded": 154}, {"at": "2026-07-29T23:37:13+00:00", "git_commit": "559f102", "items_already_recorded": 180}, {"at": "2026-07-29T23:38:08+00:00", "items_already_recorded": 180, "git_commit": "559f102"}]`

---

## 1. The run

```
cd services/api && python -m storyforge.eval run \
  --stages 1..12 --themes all --n 5 --cold \
  --effort medium --pricing-date 2026-07-29 --budget 15.0 --concurrency 1
```

`make eval` runs exactly that line.

| what | value |
|---|---|
| `eval_runs.id` | 2 |
| started / finished (UTC) | 2026-07-29T22:13:59+00:00 / 2026-07-29T23:38:08+00:00 |
| status | `completed` |
| pipeline time (summed per story) | 80 min |
| git commit | `559f102` |
| generation model | `claude-sonnet-5` |
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
| concurrency | 1 (sequential — DEVIATIONS 7.48) |
| cache bypassed (`--cold`) | True |
| ENRICHING run | False (DEVIATIONS 7.45) |
| python | 3.12.13 |
| spend cap | $15.00, enforced on the running actual |

## 2. §1 item 2 — the machine-checkable form of the thesis

§1 item 2: *"Eval harness reports >=95% decodability (excluding declared story words) across all 12 stages x 3 themes x 5 stories, with rewrite-loop depth p95 <= 2."* Checked as four assertions, each with its own exit code path (DEVIATIONS 7.49):

| criterion | result | evidence |
|---|---|---|
| every story reaches >=95% decodability | PASS | 169/169 stories at or above the gate (0 below) |
| p95 rewrite depth <= 2 | PASS | p95 = 1, max = 2 |
| the matrix completed | PASS | 180 of 180 planned cells attempted |
| every planned cell produced a story | **FAIL** | 169 of 180 cells produced text (11 api_fail) |

**Overall: FAIL.** `make eval` exited non-zero.

A moderation rejection is deliberately *not* one of these criteria. It is a content verdict on a story that already met the decodability criterion, so folding it in would make a §13 judgement look like a decoder failure. It is reported in full in section 8 instead.

## 3. Headline numbers

| metric | value |
|---|---|
| stories attempted | 180 |
| stories produced (the model returned text) | 169 |
| stories ACCEPTED (cleared gate **and** moderation) | 157 |
| gate + moderation pass rate | 87.2% |
| fallback rate (§7 — a live request would have served the warm pool) | 12.8% |
| mean decodability | 99.4% |
| median decodability | 100.0% |
| worst single story | 95.0% |
| **stories that never reached the 95% gate** | 0 |
| rewrite depth p50 / p95 / max | 0 / 1 / 2 |
| attempts per accepted story | 1.153 |
| total cost | **$2.7958** |
| cost per accepted story | **$0.0178** |
| cost per attempted story | $0.0155 |
| cold generation p50 / p95 | 16.3s / 59.8s |
| OOV word instances | 0 |
| stories over the story-word cap | 0 |

§7 outcome taxonomy: `{"accepted": 157, "api_fail": 11, "moderation_fail": 12}`

## 4. Decodability — the distribution, not the mean

A mean hides the shape: twenty stories at 100% and one at 20% has a healthy mean and one child who cannot read their story. Quantiles over every produced story:

| min | p05 | p25 | p50 | mean | p95 | max |
|---|---|---|---|---|---|---|
| 95.0% | 96.0% | 100.0% | 100.0% | 99.4% | 100.0% | 100.0% |

| decodability bin | stories | share |
|---|---|---|
| <50% | 0 | 0.0% |
| 50-80% | 0 | 0.0% |
| 80-90% | 0 | 0.0% |
| 90-<95% | 0 | 0.0% |
| 95-<100% | 32 | 18.9% |
| 100% | 137 | 81.1% |

**No story finished below the gate.** Every one of the 169 stories the model produced reached >=95% decodability within the rewrite budget.

### Per stage

| stage | n | accepted | pass rate | min | p50 | mean | max | below gate | rw p95 | mean words | in length range | TTR | $/accepted | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 15 | 13 | 86.7% | 95.5% | 100.0% | 99.5% | 100.0% | 0 | 0 | 35.5 | 86.7% | 0.44 | $0.0150 | 20s |
| 2 | 15 | 14 | 93.3% | 95.0% | 100.0% | 99.4% | 100.0% | 0 | 1 | 33.3 | 86.7% | 0.55 | $0.0114 | 16s |
| 3 | 15 | 14 | 93.3% | 96.2% | 100.0% | 99.5% | 100.0% | 0 | 0 | 36.1 | 86.7% | 0.61 | $0.0116 | 15s |
| 4 | 15 | 12 | 80.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0 | 2 | 36.1 | 93.3% | 0.61 | $0.0151 | 16s |
| 5 | 15 | 15 | 100.0% | 95.7% | 100.0% | 99.3% | 100.0% | 0 | 0 | 55.3 | 33.3% | 0.51 | $0.0175 | 30s |
| 6 | 15 | 14 | 93.3% | 96.6% | 100.0% | 99.4% | 100.0% | 0 | 1 | 66.8 | 73.3% | 0.54 | $0.0198 | 50s |
| 7 | 15 | 12 | 80.0% | 95.8% | 100.0% | 98.9% | 100.0% | 0 | 2 | 65.4 | 60.0% | 0.53 | $0.0246 | 29s |
| 8 | 15 | 15 | 100.0% | 96.2% | 100.0% | 99.0% | 100.0% | 0 | 1 | 68.3 | 66.7% | 0.55 | $0.0135 | 28s |
| 9 | 15 | 11 | 73.3% | 96.8% | 100.0% | 99.7% | 100.0% | 0 | 1 | 102.1 | 6.7% | 0.31 | $0.0219 | 91s |
| 10 | 15 | 12 | 80.0% | 96.3% | 100.0% | 99.3% | 100.0% | 0 | 0 | 89.1 | 6.7% | 0.38 | $0.0229 | 91s |
| 11 | 15 | 13 | 86.7% | 95.9% | 100.0% | 99.5% | 100.0% | 0 | 1 | 101.8 | 13.3% | 0.37 | $0.0212 | 91s |
| 12 | 15 | 12 | 80.0% | 95.0% | 100.0% | 99.0% | 100.0% | 0 | 0 | 94.2 | 13.3% | 0.37 | $0.0222 | 91s |

### Per theme

| theme | n | accepted | pass rate | min | p50 | mean | max | rw p95 | $/accepted |
|---|---|---|---|---|---|---|---|---|---|
| animals | 60 | 52 | 86.7% | 95.5% | 100.0% | 99.2% | 100.0% | 2 | $0.0169 |
| dragons | 60 | 52 | 86.7% | 95.0% | 100.0% | 99.5% | 100.0% | 1 | $0.0190 |
| space | 60 | 53 | 88.3% | 95.5% | 100.0% | 99.4% | 100.0% | 1 | $0.0175 |

## 5. The rewrite loop — how much retry pressure the gate needed

§6 is explicit that the primary lever is allowlist-constrained generation, not retry pressure. Depth 0 means the first draft passed the gate untouched.

| rewrite depth | stories |
|---|---|
| 0 | 145 |
| 1 | 17 |
| 2 | 7 |

mean 0.18 · p50 0 · p95 1 · max 2 · attempts per accepted story 1.153

## 6. Cost

| metric | value |
|---|---|
| pricing date (explicit, per `pricing.price_for`) | `2026-07-29` |
| **total actually spent** | **$2.7958** |
| spend cap | $15.00 — not reached |
| GENERATING + REWRITING (Sonnet) | $2.5174 |
| MODERATING (Haiku) | $0.2784 |
| per attempted story | $0.0155 |
| **per ACCEPTED story** | **$0.0178** |
| Sonnet uncached input tokens | 38,601 |
| Sonnet cache-WRITE tokens (1.25x) | 21,162 |
| Sonnet cache-READ tokens (0.1x) | 450,336 |
| Sonnet output tokens | 229,721 |
| Haiku input / output tokens | 237,009 / 8,274 |

**The pricing date matters.** Sonnet 5's introductory $2/$10 rate expires 2026-08-31. The same token counts re-priced at the standard $3/$15 (2026-09-01) cost **$4.0545**, a 1.45x increase — less than 1.5x because Haiku's price does not change, and the gap between the two ratios is the share of the bill that is generation rather than moderation.

Pre-flight projection (no API call, DEVIATIONS 7.44): $3.22 with no rewrites, $6.43 with one rewrite each, $12.86 worst case. Actual came in at $2.80.

**What this cost excludes.** The harness does not run §7's ENRICHING node (DEVIATIONS 7.45): no QUESTION_GEN, no SCENE_TAGGING, no question moderation. DEVIATIONS 5.15 measured enrichment at **45-48% of a warm story's total cost**, so the cost of a story a child could actually be *served* is close to double the $0.0178 above — call it $0.0338. Stated here rather than left for a reader to discover.

## 7. Latency per node

| §7 node | p50 | p95 |
|---|---|---|
| GENERATING (first attempt) | 13016 ms | 58279 ms |
| REWRITING (per rewrite) | 7266 ms | 20832 ms |
| VALIDATING (the gate — pure function) | 1.24 ms | 2.75 ms |
| MODERATING (Haiku) | 1553 ms | 2867 ms |
| **whole pipeline** | **16254 ms** | **59807 ms** |

§1 item 6 asks for cold generation p95 < 12s. Measured p95 is **59.8s**, which **does NOT meet** it. Per-stage p95 is in the table in section 4: the miss is concentrated in the long-story stages, where the target length is 120-200 words. Warm-pool serving is unaffected — §7 makes the demo path a database read, and that latency is not measured here.

The VALIDATING row is the thesis's economics in one line: the gate that makes the guarantee is roughly four orders of magnitude cheaper than the generation it judges, and costs nothing per call.

## 8. Moderation (§13)

| verdict class | stories |
|---|---|
| approved | 157 |
| blocklist | 3 |
| content | 1 |
| grammar | 8 |
| not_reached | 11 |

- `blocklist` — §13 layer 1, deterministic, free, no API call.
- `grammar` — the well-formedness half of the verdict (DEVIATIONS 4.12), which `moderate()` folds into `approved` and marks with a reason prefix.
- `content` — the layer-3 safety judgement.
- `not_reached` — the story never cleared the gate, so moderation never ran.

**Hazard versus harm cannot be read off the verdict.** STATE invariant 5 requires moderation to separate a storybook hazard (a fire-breathing dragon is the premise) from actual harm, and a spike either way is the signal. But the verdict carries no field for that distinction — the only record is a sentence the model wrote. So every content rejection's reason is quoted verbatim below and the `hazard?` column is a keyword HEURISTIC pointing at reasons worth reading, not a measurement (DEVIATIONS 7.46).

| stage | theme | hazard? (heuristic) | reason (verbatim) |
|---|---|---|---|
| 3 | dragons | harm-shaped | The dragon sent fire at the man, which is a weapon used against a character. |

### Grammar rejections, verbatim

| stage | theme | note |
|---|---|---|
| 1 | dragons | "Its wings sat" — sat should be replaced with are (wings cannot sit; should describe what they are). |
| 1 | space | "Sam sat and it sat past the moon." — two subjects joined by 'and' need parallel structure; should be "Sam sat and went past the moon" or "Sam sat as it went past the moon." |
| 2 | space | "The rocket ran fast." Rockets do not run; should be "went" or "flew". |
| 4 | animals | "It went back off and ran in the forest." — 'back off' is incorrect; should be 'back out' or simply 'back'. |
| 4 | dragons | "A dragon lived in a can." Should be "cave" not "can"; likely a word-list error, not a real word choice. |
| 4 | dragons | "It had a big lot." Should be "It had a big lot of gold" or similar; "lot" alone is incomplete. |
| 7 | animals | "The girl said, this rabbit got a home." Missing punctuation; should be a period or semicolon before "this". |
| 7 | animals | "The girl said, come here, little rabbit." Missing comma after said; should be "The girl said, 'Come here, little rabbit.'" |

### Blocklist rejections (layer 1)

| stage | theme | blocked words |
|---|---|---|
| 6 | space | high |
| 7 | dragons | fight |
| 10 | animals | high |

## 9. The decoder's own disclosed limitations (§5.3, §18)

§5.3 requires these to be *logged in the eval report, not silent behaviour*. §18 names the alignment/schwa problem as the highest technical risk and says a rising unalignable rate is the signal that the inventory is too small.

| flag | what it means | total word instances | mean rate per story |
|---|---|---|---|
| `schwa_relaxed` | §5.2.6 let an unstressed vowel match its citation GPC | 443 | 4.0% |
| `ambiguity_flag` | more than one legal alignment existed; any was taken | 1272 | 12.4% |
| `heteronym_flag` | multiple pronunciations, only some decodable — §5.2.5 may over-credit | 377 | 3.1% |
| `alignable=False` | **the §18 alarm** — the inventory cannot spell the word at any stage | 28 | 0.2% |
| non-minimal missing set | the minimal-missing-GPC search hit its bound and approximated | 0 | — |

Words the inventory cannot spell at any stage, with occurrences: `{"of": 7, "around": 2, "be": 2, "come": 2, "from": 2, "gold": 2, "near": 2, "quiet": 2, "white": 2, "friends": 1, "hi": 1, "knew": 1, "old": 1}`

**The one actionable item in that list is `of`.** It is among the most frequent words in English, the 92-GPC inventory cannot spell it at any stage, and — checked directly against the `sight_words` table, all 33 rows — it is **not a sight word at any stage**. So every story that reaches for it spends part of its 5% violation budget on a word a child will meet constantly. Of the words above, only `come` is a sight word (stage 6), and it appears here at stage 5, before it unlocks, which is the list working correctly. Appendix A’s sight-word list looks like the place to fix this; the fix is data, not code, and is not made here because a sight-word addition changes what every stage’s precompute admits and belongs with a re-run of `make precompute` and the §5.7 goldens.

### Per stage

| stage | schwa | ambiguity | heteronym | unalignable | unalignable words |
|---|---|---|---|---|---|
| 1 | 6.9% | 18.8% | 0.0% | 0.0% | — |
| 2 | 2.9% | 9.9% | 0.8% | 0.0% | — |
| 3 | 1.8% | 15.1% | 2.5% | 0.0% | — |
| 4 | 3.6% | 16.2% | 4.2% | 0.0% | — |
| 5 | 2.1% | 11.1% | 2.8% | 0.2% | come |
| 6 | 4.5% | 12.7% | 5.2% | 0.1% | around |
| 7 | 5.4% | 11.1% | 2.2% | 0.2% | hi, near |
| 8 | 4.8% | 12.2% | 4.2% | 0.5% | be, friends, from, gold, of |
| 9 | 2.9% | 8.2% | 3.0% | 0.1% | knew, of, old |
| 10 | 2.8% | 8.5% | 2.6% | 0.2% | be, of |
| 11 | 3.9% | 8.3% | 4.4% | 0.2% | around, of, quiet |
| 12 | 3.5% | 8.2% | 3.1% | 0.4% | gold, near, of, quiet, white |

## 10. OOV

§5.3: CMUdict is an adult lexicon, so a missing entry fails the word — which is the conservative direction, but it is a real limit. §7's gate requires **zero** OOV, so any OOV word forces a rewrite or a rejection.

0 OOV word instance(s) across 0 of 169 produced stories.

## 11. Story words (§4, §5.5)

The cap is 3 **distinct** words per story. STATE invariant 3: a decodable reader repeats deliberately, so occurrences are uncapped and only the number of new picture-chip words a child must absorb is limited. §0's untrusted-generator rule bites here — the model's claim about which story words it used is intersected with the words actually offered, or it could shrink its own decodability denominator (STATE invariant 2).

| metric | value |
|---|---|
| stories over the cap | **0** |
| stories using no story word | 16 |
| total story-word occurrences | 1475 |

| distinct story words used | stories |
|---|---|
| 0 | 16 |
| 1 | 8 |
| 2 | 17 |
| 3 | 139 |

- `animals`: {"rabbit": 48, "forest": 43, "burrow": 40, "whiskers": 1}
- `dragons`: {"cave": 56, "dragon": 56, "fire": 36, "wings": 10, "treasure": 8}
- `space`: {"moon": 52, "rocket": 52, "stars": 42, "astronaut": 12, "planet": 3}

## 12. Length compliance and register

| metric | value |
|---|---|
| stories inside the stage's target word range | 52.2% |
| median word count | 59 |
| mean type-token ratio | 0.512 |

§18 predicts the early stages will read "Sam sat. A cat sat." and calls that authentic decodable-reader register rather than a defect. The type-token ratio is reported because §14 asks for it, not as a quality target: a low ratio at stage 1 is the format working.

**Length compliance is the weakest number in this report, and it is a generation defect rather than a decoder one.** The per-stage table in section 4 shows it collapsing as the target grows: the stages asking for 120-200 words came back with a mean in the 89-102 range, so the model consistently under-delivers length at the top of the ladder while staying comfortably inside the gate. Decodability is unaffected — a short story is still a decodable story — but a stage-12 reader is being handed roughly half the text §6 specifies. §18’s knob order for generation compliance ends at “shorten target length”; this is the opposite problem and is not covered there.

## 13. What this run wrote to the database

| table | what |
|---|---|
| `eval_runs` | 1 row — id 2, status `completed` |
| `eval_run_items` | 180 rows, one per attempted story |
| `stories` | 169 rows at **`status='eval'`** (+ their `story_segments`) |
| `questions` / `readings` / `answers` / `gpc_stats` | **untouched** |

**Nothing this run wrote can be served to a child, whether it passed or not.** That is structural, not a promise: `api/stories.py::SERVABLE_STATUSES` is the allowlist `("warm", "complete")` that both `_SERVE_SQL` and `GET /api/stories/{id}` filter on, and it does not name `eval` (DEVIATIONS 7.41). The servable pool measured **52 stories before this run and 52 after**.

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

## 15. Re-running and comparing

The run is reproducible in configuration, not in output — the model is not deterministic. `eval_runs.config` holds the matrix, the model ids, the pricing date, the gate, the rewrite budget and the git commit, so two runs are comparable as distributions. `--pricing-date` is explicit precisely so a cost figure regenerated later cannot silently disagree with the one printed here.

```
make eval                                  # the full §1 matrix, ~$2.80, ~80 min of pipeline time
make eval EVAL_ARGS="--estimate-only"      # project the spend, no API call
make eval-smoke                            # 4-story regression gate, ~$0.06
```

