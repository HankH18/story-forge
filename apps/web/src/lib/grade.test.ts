/**
 * `lib/grade.ts` — the client-side answer check, which is the FALLBACK.
 *
 * §9 keeps the answer key off the wire: grading happens at
 * `POST /api/readings/{id}/answers`, the same call that writes `answers` and
 * `gpc_stats`. This file only runs when the server did not answer, and the one
 * property it must never lose is that **`null` is not "wrong"**. A child who is
 * told they got it wrong because the network dropped has been lied to by the one
 * screen in the app that is supposed to be encouraging.
 *
 * `storySupports` is a deliberate mirror of `story_supports` in
 * `services/api/storyforge/llm/questions.py`, including its docstring's own
 * example: "Sam sat on the cat" supports itself, and the reversal "The cat sat on
 * Sam" has every word present but not in order, so it is provably not supported.
 * That example is asserted here so the mirror can be seen to still be a mirror.
 */

import { describe, expect, test } from 'bun:test'
import type { Question } from './api'
import { localGrade, storySupports } from './grade'

const STORY = 'Sam sat on the cat. The cat ran to the van.'

function question(over: Partial<Question>): Question {
  return { id: 1, segment_id: null, idx: 0, qtype: 'tap_word', prompt: '', options: [], payload: {}, ...over }
}

describe('storySupports', () => {
  test("the API docstring's own example, both directions", () => {
    expect(storySupports('Sam sat on the cat', STORY)).toBe(true)
    expect(storySupports('The cat sat on Sam', STORY)).toBe(false)
  })

  test('subsequence, not substring — the words may be far apart', () => {
    expect(storySupports('Sam van', STORY)).toBe(true)
    expect(storySupports('van Sam', STORY)).toBe(false)
  })

  test('a word the story never says is never supported', () => {
    expect(storySupports('Sam sat on the dog', STORY)).toBe(false)
  })

  test('case and punctuation are not the question', () => {
    expect(storySupports('SAM SAT!', STORY)).toBe(true)
  })

  test('an option with no words is not supported', () => {
    // Guarding the `!want.length` branch: an empty option must not vacuously
    // match, or a malformed question would grade every response as correct.
    expect(storySupports('', STORY)).toBe(false)
    expect(storySupports('...', STORY)).toBe(false)
    expect(storySupports('   ', STORY)).toBe(false)
  })

  test('a repeated word needs that many occurrences, in order', () => {
    expect(storySupports('the cat the van', STORY)).toBe(true)
    expect(storySupports('van van', STORY)).toBe(false)
  })
})

describe('localGrade', () => {
  test('tap_word is graded on the target grapheme, as the generator proved it', () => {
    const q = question({ qtype: 'tap_word', payload: { target_grapheme: 'sh' } })
    expect(localGrade(q, 'ship', STORY)).toBe(true)
    expect(localGrade(q, 'SHIP', STORY)).toBe(true)
    expect(localGrade(q, 'cat', STORY)).toBe(false)
  })

  test('mcq2 is graded by story support', () => {
    const q = question({ qtype: 'mcq2' })
    expect(localGrade(q, 'Sam sat on the cat', STORY)).toBe(true)
    expect(localGrade(q, 'The cat sat on Sam', STORY)).toBe(false)
  })

  test('null wherever the property is undecidable — never false', () => {
    // Each of these would show a child a red cross for something that is not
    // their mistake. `null` means "the server did not tell us", and the caller
    // is required to treat it as "no verdict".
    expect(localGrade(question({ qtype: 'tap_word', payload: {} }), 'ship', STORY)).toBe(null)
    expect(
      localGrade(question({ qtype: 'tap_word', payload: { target_grapheme: '  ' } }), 'ship', STORY),
    ).toBe(null)
    expect(localGrade(question({ qtype: 'mcq2' }), 'Sam sat', '')).toBe(null)
    expect(localGrade(question({ qtype: 'sequence' }), 'anything', STORY)).toBe(null)
    expect(localGrade(question({ qtype: 'mcq2' }), '   ', STORY)).toBe(null)
  })

  test('an empty response is no answer, not a wrong answer', () => {
    expect(localGrade(question({ qtype: 'tap_word', payload: { target_grapheme: 'sh' } }), '', STORY)).toBe(
      null,
    )
  })
})
