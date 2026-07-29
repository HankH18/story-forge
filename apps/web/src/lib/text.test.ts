/**
 * `lib/text.ts` — the tap-target tokeniser (style guide §07).
 *
 * The property that matters is not "it finds words". It is **losslessness**:
 * concatenating every part in order must reproduce the input exactly. A reading
 * screen is built out of these parts, so a tokeniser that silently drops a comma
 * or a closing quote changes the text a five-year-old is asked to read, and the
 * validator upstream scored a string this one never rendered.
 *
 * The second property is §07's rule that "punctuation is never inside the
 * button" — the button's accessible name is spoken aloud, and `ship.` is read as
 * "ship period" by some engines, which teaches the child that the dot is part of
 * the word.
 */

import { describe, expect, test } from 'bun:test'
import { distinctWords, normalizeWord, splitText, words } from './text'

const rebuild = (text: string) => splitText(text).map((p) => p.text).join('')

describe('splitText', () => {
  test.each([
    'Sam sat.',
    'The cat sat on the mat.',
    '“Stop!” said Dad.',
    "The dog didn't run — it sat.",
    'A sun-hat, a fan, and a cat.',
    'Sam ran. Sam ran! Sam ran?',
    '   leading and trailing   ',
    'no punctuation at all',
    '...',
    '',
  ])('is lossless for %p', (text) => {
    expect(rebuild(text)).toBe(text)
  })

  test('punctuation is never inside a word token (§07)', () => {
    for (const part of splitText('“Stop!” said Dad. It ran, then sat...')) {
      if (part.kind === 'word') expect(part.text).toMatch(/^[A-Za-z]+(?:['’-][A-Za-z]+)*$/)
    }
  })

  test('word indices are contiguous and in reading order', () => {
    const parts = splitText('Sam sat on the mat.')
    const wordParts = parts.filter((p) => p.kind === 'word')
    expect(wordParts.map((p) => (p.kind === 'word' ? p.index : -1))).toEqual([0, 1, 2, 3, 4])
    expect(wordParts.map((p) => p.text)).toEqual(['Sam', 'sat', 'on', 'the', 'mat'])
  })

  test('an internal apostrophe or hyphen stays in the word, a trailing one does not', () => {
    // A `<button>` on `dogs'` would speak the apostrophe; `didn't` is one word a
    // child reads as one word. Both halves of that are asserted, because the
    // regex that gets one right by accident usually gets the other wrong.
    expect(words("didn't")).toEqual(["didn't"])
    expect(words('sun-hat')).toEqual(['sun-hat'])
    expect(words("dogs'")).toEqual(['dogs'])
    expect(rebuild("dogs' bones")).toBe("dogs' bones")
    expect(words('well-')).toEqual(['well'])
  })

  test('digits are not words — nothing decodable is spelled with them', () => {
    expect(words('Sam had 3 cats.')).toEqual(['sam', 'had', 'cats'])
    expect(rebuild('Sam had 3 cats.')).toBe('Sam had 3 cats.')
  })
})

describe('words / distinctWords', () => {
  test('words are lowercased in reading order, repeats kept', () => {
    expect(words('Sam sat. Sam sat.')).toEqual(['sam', 'sat', 'sam', 'sat'])
  })

  test('distinctWords is the celebration count, so repeats collapse', () => {
    // STATE invariant 3's sibling on the client: repetition is correct
    // decodable-reader behaviour, so "words you read" must not reward it.
    expect(distinctWords('Sam sat. Sam sat. Sam ran.')).toEqual(['sam', 'sat', 'ran'])
    expect(distinctWords('')).toEqual([])
  })

  test('case is not a distinction a child would recognise', () => {
    expect(distinctWords('The the THE tHe')).toEqual(['the'])
  })
})

describe('normalizeWord', () => {
  test('is the same normalisation the word list uses', () => {
    expect(normalizeWord('Sam')).toBe('sam')
    expect(words('Sam')[0]).toBe(normalizeWord('Sam'))
  })
})
