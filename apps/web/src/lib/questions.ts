/**
 * What each of §11.3's two question types IS, and how to say it out loud.
 *
 * The user's report on the live app was "the questions aren't very clearly
 * given. They begin out of nowhere and then aren't really written well." Both
 * halves are true and the second one has a cause that cannot be fixed by
 * rewriting anything:
 *
 * **A `tap_word` prompt is rendered to the child, so it must itself be decodable
 * at the child's stage.** Below stage 4 every word for similarity — `as`,
 * `like`, `same`, `alike` — is stage 4 or later, so the generator cannot write
 * "Tap the word that starts like for." and emits `"Tap it. Hint: for."` instead
 * (llm/questions.py, DEVIATIONS 6.27). That floor is real and the words are not
 * negotiable.
 *
 * What is NOT bound by the decoder: **speech, icons, and layout.** So the
 * instruction a child actually receives is spoken in an adult voice and shown as
 * a visual pattern (cue card → three options), and the generated sentence is
 * demoted to a caption. Nothing here rewrites server text; it decides what is
 * primary.
 *
 * Pure functions, in `lib/`, because "which of two questions is the phonics one"
 * is exactly the sort of thing that can be quietly wrong: `MASTERY_QTYPES` in
 * `mastery.py` is `("tap_word",)`, and a UI that drew both questions the same
 * way was hiding that from the only person who could act on it.
 */

import type { Question } from './api'

export type TaskKind = 'sound' | 'story'

/**
 * `tap_word` is the sound task, `mcq2` the story task.
 *
 * This is the same split as `mastery.MASTERY_QTYPES`, and it is deliberately
 * NOT phrased as "the one that counts". §01 and the style guide keep kid mode
 * free of anything that reads like grading, so the child sees two DIFFERENT
 * KINDS of thing — a listening game and a question about the story — never a
 * scored one and an unscored one. The parent dashboard is where the distinction
 * is stated in words.
 */
export function taskKind(question: Pick<Question, 'qtype'>): TaskKind {
  return question.qtype === 'tap_word' ? 'sound' : 'story'
}

/**
 * The decodable word whose initial sound the child is matching.
 *
 * `payload.cue_word` survives `_public_payload`'s denylist — `target_grapheme`
 * does not, and must not: the correct option is the one that starts with it, so
 * shipping it would ship the answer key the DTO exists to withhold. The cue word
 * gives no answer away on its own; it is a word from the story the child has
 * already read, and finding the match is the task.
 */
export function cueWord(question: Pick<Question, 'payload'>): string | null {
  const cue = question.payload?.cue_word
  if (typeof cue !== 'string') return null
  const trimmed = cue.trim()
  return trimmed ? trimmed : null
}

/**
 * What the screen says out loud when a question appears.
 *
 * The old behaviour spoke `question.prompt`, so at stage 2 a child heard "Tap
 * it. Hint: for." — the decoder's floor read aloud, which is the one place the
 * floor did not have to apply. Speech has no allowlist.
 *
 * Falls back to the prompt whenever there is nothing better to say, so a
 * `tap_word` row with no `cue_word` (older builds) still explains itself.
 */
export function spokenInstruction(question: Pick<Question, 'qtype' | 'prompt' | 'payload'>): string {
  if (taskKind(question) === 'sound') {
    const cue = cueWord(question)
    if (cue) return `Which word starts like ${cue}? Tap it.`
  }
  return question.prompt
}

/** Progress through the question run, for the dots. Never rendered numerically. */
export function questionOrdinal(index: number, total: number): { current: number; total: number } {
  return { current: Math.min(Math.max(index, 0), Math.max(total - 1, 0)), total: Math.max(total, 1) }
}
