import { describe, expect, it } from 'bun:test'
import { cueWord, questionOrdinal, spokenInstruction, taskKind } from './questions'

const tapWord = (prompt: string, payload: Record<string, unknown> = {}) => ({
  qtype: 'tap_word',
  prompt,
  payload,
})
const mcq2 = (prompt: string) => ({ qtype: 'mcq2', prompt, payload: {} })

describe('taskKind', () => {
  it('splits the two §11.3 types the same way mastery.py does', () => {
    // mastery.MASTERY_QTYPES == ("tap_word",) — this is the UI's half of that.
    expect(taskKind({ qtype: 'tap_word' })).toBe('sound')
    expect(taskKind({ qtype: 'mcq2' })).toBe('story')
  })

  it('treats an unknown qtype as the story task, not the phonics one', () => {
    // Fail towards "no phonics claim". A future qtype drawn as the sound task
    // would assert a mastery meaning it has not earned.
    expect(taskKind({ qtype: 'something_new' })).toBe('story')
  })
})

describe('cueWord', () => {
  it('reads `cue_word`, which survives the DTO denylist', () => {
    expect(cueWord(tapWord('Tap it. Hint: for.', { cue_word: 'for' }))).toBe('for')
  })

  it('is null when absent, so the screen falls back rather than rendering an empty card', () => {
    expect(cueWord(tapWord('Tap it.'))).toBeNull()
  })

  it('is null for a blank or non-string cue', () => {
    expect(cueWord(tapWord('x', { cue_word: '   ' }))).toBeNull()
    expect(cueWord(tapWord('x', { cue_word: 42 }))).toBeNull()
  })

  it('never reads `target_grapheme` — that is the answer key', () => {
    // The correct option is the one that starts with the grapheme, so a screen
    // that rendered it would be printing the answer. `_public_payload` strips
    // it server-side; this asserts the client would not use it anyway.
    expect(cueWord(tapWord('x', { target_grapheme: 'f' }))).toBeNull()
  })
})

describe('spokenInstruction', () => {
  it('replaces the decoder-floored prompt with a full sentence', () => {
    // The written prompt below stage 4 is provably "Tap it. Hint: for." because
    // as/like/same/alike are all stage 4+. Speech has no allowlist.
    const said = spokenInstruction(tapWord('Tap it. Hint: for.', { cue_word: 'for' }))
    expect(said).toBe('Which word starts like for? Tap it.')
    expect(said).not.toContain('Hint')
  })

  it('names the cue, so a listener knows which word to match', () => {
    expect(spokenInstruction(tapWord('Tap it. Hint: win.', { cue_word: 'win' }))).toContain('win')
  })

  it('falls back to the prompt when there is no cue word', () => {
    // An older row, or a future generator. Better a floored sentence than none.
    expect(spokenInstruction(tapWord('Tap the word that starts like for.'))).toBe(
      'Tap the word that starts like for.',
    )
  })

  it('speaks an mcq2 prompt verbatim — it is already a real question', () => {
    expect(spokenInstruction(mcq2('Will he help the dragon?'))).toBe('Will he help the dragon?')
  })

  it('ignores a cue word on an mcq2 row', () => {
    const q = { qtype: 'mcq2', prompt: 'Did Sam go?', payload: { cue_word: 'go' } }
    expect(spokenInstruction(q)).toBe('Did Sam go?')
  })
})

describe('questionOrdinal', () => {
  it('clamps into range so the dots cannot be asked for a position that does not exist', () => {
    expect(questionOrdinal(0, 2)).toEqual({ current: 0, total: 2 })
    expect(questionOrdinal(5, 2)).toEqual({ current: 1, total: 2 })
    expect(questionOrdinal(-1, 2)).toEqual({ current: 0, total: 2 })
  })

  it('never reports a total of zero', () => {
    expect(questionOrdinal(0, 0)).toEqual({ current: 0, total: 1 })
  })
})
