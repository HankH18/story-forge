/**
 * Client-side answer check — the FALLBACK, never the source of truth.
 *
 * §9 keeps the answer key off the wire on purpose: grading happens at
 * `POST /api/readings/{id}/answers`, because that is the same call that writes
 * `answers` and `gpc_stats`. Shipping the key would buy no round trip and would
 * make the mastery signal forgeable.
 *
 * But that endpoint is being built in parallel, and a comprehension screen that
 * cannot tell right from wrong is a dead end for the child in front of it. So
 * when the server does not answer, this re-derives the verdict from the two
 * properties `llm/questions.py` *proved* before the question was allowed to
 * exist. Not a second opinion — a restatement of the first:
 *
 *   tap_word  the correct option starts with `target_grapheme`, and every
 *             distractor was rejected if it used that grapheme anywhere.
 *   mcq2      the true option's words appear in the story in the story's own
 *             order (`story_supports`); the false one's provably do not.
 *
 * Consequences worth stating. The check is a prefix match on spelling, not on
 * sound, so a grapheme that is spelled the same but sounds different would slip
 * through — acceptable, because the generator already guaranteed exactly one
 * option can be correct. And a child could read the answer out of the bundle,
 * which is why this never replaces the server once the server is there.
 *
 * **`target_grapheme` NEVER ARRIVES, so that branch was dead and every sound
 * question graded itself `null`** (DEVIATIONS 25.1). `_public_payload` redacts
 * the grapheme by design — it is the answer key — so `localGrade` answered
 * "undecidable" for every live `tap_word`, `Comprehension` turns undecidable
 * into ACCEPT, and any moment the server did not answer (a dropped connection,
 * a 401 from an expired Clerk token, a 500, a `readingId` that never opened)
 * turned the listening game into a screen where every tap is right. Measured
 * live: network down, cue `get`, options `up`/`got`/`sat`, tapped `up`, straight
 * to "You read the whole story!". The tests could not see it because their
 * fixture supplies a `target_grapheme` no server ever sends.
 *
 * **The cue is the recovery, and it ships no new information.** `cue_word` is
 * already in the payload, and the prompt on screen already says "Tap the word
 * that starts with the same sound as get." — the rule is printed, in English,
 * next to the options. What `llm/questions.py` proves is stronger than the
 * sentence: the cue's first grapheme IS the target grapheme (`onset_gpc(cue) ==
 * gpc_id`), the correct option starts with that grapheme (`graphemes[0] ==
 * grapheme`), and a distractor may not contain it ANYWHERE (`grapheme in
 * graphemes` is a rejection). So the correct option is the one sharing the
 * longest spelling prefix with the cue, and its prefix is strictly longer than
 * any distractor's — a distractor that matched the cue's first `len(grapheme)`
 * characters would contain the grapheme and could not have been offered.
 *
 * A tie, a zero-length best match, or a response that is not one of the offered
 * options is still `null`. This adds a way to be RIGHT about "wrong"; it does not
 * add a way to guess.
 */

import type { Question } from './api'
import { words } from './text'

/** Mirror of `story_supports` in services/api/storyforge/llm/questions.py. */
export function storySupports(option: string, storyText: string): boolean {
  const want = words(option)
  if (!want.length) return false
  const have = words(storyText)
  let i = 0
  for (const w of want) {
    while (i < have.length && have[i] !== w) i += 1
    if (i === have.length) return false
    i += 1
  }
  return true
}

/** How many leading characters two words share. */
function sharedPrefix(a: string, b: string): number {
  let i = 0
  while (i < a.length && i < b.length && a[i] === b[i]) i += 1
  return i
}

/**
 * The correct `tap_word` option, derived from the served `cue_word` alone.
 *
 * `null` whenever the derivation is not forced: no cue, fewer than two options,
 * nothing sharing an onset with the cue, or two options tied for the longest
 * shared prefix. A tie means the offer set is not one the generator could have
 * produced, and guessing between two options is exactly the thing this file
 * exists not to do.
 *
 * Exported so `grade.test.ts` can assert the derivation against the shapes
 * `check_tap_word` actually admits, rather than only through `localGrade`.
 */
export function cueAnswer(cue: string, options: readonly string[]): string | null {
  const target = cue.trim().toLowerCase()
  if (!target) return null
  const cleaned = options.map((o) => o.trim().toLowerCase()).filter(Boolean)
  if (cleaned.length < 2) return null

  let best = 0
  let winner: string | null = null
  let tied = false
  for (const option of cleaned) {
    const n = sharedPrefix(option, target)
    // An option sharing nothing with the cue is simply not a candidate; only
    // options that DO share an onset can tie with each other.
    if (n === 0) continue
    if (n > best) {
      best = n
      winner = option
      tied = false
    } else if (n === best) {
      tied = true
    }
  }
  if (best === 0 || tied) return null
  return winner
}

/**
 * `true` / `false` when the property is decidable here, `null` when it is not
 * (an unknown question type, or a tap_word with neither `target_grapheme` nor a
 * usable `cue_word`). `null` must never be shown to a child as "wrong".
 */
export function localGrade(question: Question, response: string, storyText: string): boolean | null {
  const answer = response.trim().toLowerCase()
  if (!answer) return null

  if (question.qtype === 'tap_word') {
    const grapheme = String(question.payload?.target_grapheme ?? '')
      .trim()
      .toLowerCase()
    if (grapheme) return answer.startsWith(grapheme)

    // The live path: the grapheme is redacted, so the cue carries the sound.
    const cue = String(question.payload?.cue_word ?? '')
    const correct = cueAnswer(cue, question.options)
    if (correct === null) return null
    // A response that is not one of the offered options is not a wrong answer,
    // it is a question this function was not asked about.
    if (!question.options.some((o) => o.trim().toLowerCase() === answer)) return null
    return answer === correct
  }

  if (question.qtype === 'mcq2') {
    if (!storyText.trim()) return null
    return storySupports(response, storyText)
  }

  return null
}
