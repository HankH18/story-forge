/**
 * Splitting rendered text into tap targets — style guide §07.
 *
 * "Punctuation is never inside the button." That is not typographic fussiness:
 * the button's accessible name is read aloud as *the word*, and a `<button>` on
 * `ship.` would speak "ship period" on some engines and teach the child that the
 * dot is part of the word. So a segment becomes an alternating run of word
 * tokens (interactive) and inert text (spaces, commas, full stops, quotes).
 *
 * Deliberately not the Python tokenizer's twin. `storyforge.decoder.tokenizer`
 * decides what the *validator* scores; this decides what a finger can hit. The
 * story text arriving here has already passed the gate, so this only has to
 * split it, never judge it.
 */

export type TextPart =
  | { kind: 'word'; text: string; index: number }
  | { kind: 'gap'; text: string }

/**
 * Letters, plus apostrophes and hyphens *between* letters ("didn't", "sun-hat").
 * A trailing apostrophe is punctuation and falls out of the word, which is what
 * `dogs'` should do.
 */
const WORD_RE = /[A-Za-z]+(?:['’-][A-Za-z]+)*/g

export function splitText(text: string): TextPart[] {
  const parts: TextPart[] = []
  let cursor = 0
  let index = 0
  WORD_RE.lastIndex = 0
  for (const match of text.matchAll(WORD_RE)) {
    const at = match.index ?? 0
    if (at > cursor) parts.push({ kind: 'gap', text: text.slice(cursor, at) })
    parts.push({ kind: 'word', text: match[0], index })
    index += 1
    cursor = at + match[0].length
  }
  if (cursor < text.length) parts.push({ kind: 'gap', text: text.slice(cursor) })
  return parts
}

/** Every word in `text`, lowercased, in reading order. */
export function words(text: string): string[] {
  return splitText(text)
    .filter((p): p is Extract<TextPart, { kind: 'word' }> => p.kind === 'word')
    .map((p) => p.text.toLowerCase())
}

/** Distinct words, for the celebration's honest "words you read" count. */
export function distinctWords(text: string): string[] {
  return [...new Set(words(text))]
}

export function normalizeWord(word: string): string {
  return word.toLowerCase()
}
