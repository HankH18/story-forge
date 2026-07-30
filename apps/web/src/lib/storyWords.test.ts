import { describe, expect, it } from 'bun:test'
import { introductionIndices, storyWordSet } from './storyWords'

/** The session's answer: every listed word is still owed an introduction. */
const owes = (...words: string[]) => {
  const set = new Set(words.map((w) => w.toLowerCase()))
  return (word: string) => set.has(word.toLowerCase())
}

describe('introductionIndices', () => {
  it('marks the first occurrence and no later one — the actual defect', () => {
    // The live stage-6 screen. Five chips before, one after.
    const text = 'On a day a dog came by the cave. The dog did not have wings like the dragon.'
    const at = introductionIndices(text, {
      storyWords: ['cave', 'dragon', 'wings'],
      shouldAnnounce: owes('cave', 'dragon', 'wings'),
    })
    expect(at).toHaveLength(3)
  })

  it('gives three occurrences of one word exactly one chip', () => {
    const text = 'The dragon let the dog stay. The dragon flew. Up went the dragon.'
    expect(
      introductionIndices(text, {
        storyWords: ['dragon'],
        shouldAnnounce: owes('dragon'),
      }),
    ).toEqual([1])
  })

  it('returns reading order, which is also the order they take the microphone', () => {
    const text = 'A dragon was in a cave.'
    const at = introductionIndices(text, {
      storyWords: ['cave', 'dragon'],
      shouldAnnounce: owes('cave', 'dragon'),
    })
    // `dragon` is word 1, `cave` is word 5 — the array must not be alphabetical
    // or set-ordered, or the second chip would speak first.
    expect(at).toEqual([1, 5])
  })

  it('skips a word the session has already introduced on an earlier screen', () => {
    const text = 'Then it came back to the cave. The dragon was happy.'
    expect(
      introductionIndices(text, {
        storyWords: ['cave', 'dragon'],
        // `cave` was introduced on screen 1; only `dragon` is still owed.
        shouldAnnounce: owes('dragon'),
      }),
    ).toHaveLength(1)
  })

  it('returns nothing when the session owes nothing — the whole screen is plain', () => {
    const text = 'Then it came back to the cave.'
    expect(
      introductionIndices(text, { storyWords: ['cave'], shouldAnnounce: owes() }),
    ).toEqual([])
  })

  it('returns nothing without `shouldAnnounce` — question prompts and branch labels', () => {
    // Comprehension and BranchChoice pass no session callback, so they get no
    // chips at all: by then the word has been introduced, and §07 says it then
    // behaves like any tappable word.
    expect(introductionIndices('Go in the cave.', { storyWords: ['cave'] })).toEqual([])
  })

  it('matches case-insensitively, in both directions', () => {
    expect(
      introductionIndices('Dragon met a CAVE.', {
        storyWords: ['DRAGON', 'cave'],
        shouldAnnounce: owes('dragon', 'CAVE'),
      }),
    ).toEqual([0, 3])
  })

  it('ignores punctuation attached to the word', () => {
    // `splitText` keeps punctuation outside the token; "cave." must still match.
    expect(
      introductionIndices('He saw a cave, and a dragon!', {
        storyWords: ['cave', 'dragon'],
        shouldAnnounce: owes('cave', 'dragon'),
      }),
    ).toEqual([3, 6])
  })

  it('does not match a story word inside a longer word', () => {
    // "caveman" is not "cave". A substring chip would put a picture on a word
    // the child does have to decode.
    expect(
      introductionIndices('The caveman ran.', {
        storyWords: ['cave'],
        shouldAnnounce: owes('cave'),
      }),
    ).toEqual([])
  })

  it('is empty for a story with no story words', () => {
    expect(
      introductionIndices('A dog ran fast.', { storyWords: [], shouldAnnounce: owes('dog') }),
    ).toEqual([])
  })
})

describe('storyWordSet', () => {
  it('lowercases, so the render-time membership test agrees with the chip list', () => {
    const set = storyWordSet(['Dragon', 'CAVE'])
    expect(set.has('dragon')).toBe(true)
    expect(set.has('cave')).toBe(true)
    expect(set.has('Dragon')).toBe(false)
  })
})
