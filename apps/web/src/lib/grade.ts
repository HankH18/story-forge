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

/**
 * `true` / `false` when the property is decidable here, `null` when it is not
 * (an unknown question type, or a tap_word with no `target_grapheme`). `null`
 * must never be shown to a child as "wrong".
 */
export function localGrade(question: Question, response: string, storyText: string): boolean | null {
  const answer = response.trim().toLowerCase()
  if (!answer) return null

  if (question.qtype === 'tap_word') {
    const grapheme = String(question.payload?.target_grapheme ?? '')
      .trim()
      .toLowerCase()
    if (!grapheme) return null
    return answer.startsWith(grapheme)
  }

  if (question.qtype === 'mcq2') {
    if (!storyText.trim()) return null
    return storySupports(response, storyText)
  }

  return null
}
