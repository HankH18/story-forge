/**
 * Turning the API's phonemes into something a parent can read.
 *
 * `GET /api/kids/{id}/progress` returns `phonemes` in **ARPABET** — "AE", "IH Z",
 * "CH ER". That is the decoder's internal alphabet (CMUdict is ARPABET, so the
 * validator, the aligner and `gpc_stats` all speak it), and it is exactly the
 * wrong thing to put in front of a parent: "AE" looks like a typo, and a parent
 * who guesses reads it as the letters A-E, which is a *different* GPC in this
 * very table (`a_e`, stage 5).
 *
 * THE DECISION: **a plain-language example word is the primary rendering, with
 * IPA in slashes as the secondary.**
 *
 *   sh   the sound in "ship"   /ʃ/
 *
 * Why that order. §08 says parent copy "explains phonics in plain language", and
 * the example word is the only one of the three notations that needs no training
 * at all — "the sound in ship" is self-teaching, and it is also how a teacher
 * would say it out loud at a parents' evening. IPA is kept because it is
 * unambiguous where an example word is not (`th` in "thin" vs `th` in "this" are
 * two rows in this table and two different sounds; /θ/ and /ð/ say so exactly),
 * and because it is a real, standard alphabet a parent can look up — which
 * ARPABET, an ASCII transcription format for speech software, is not.
 *
 * The example words are NOT invented here. They are the `example` column of
 * `data/seed/gpcs.csv` — the same 92-row inventory the decoder is built on,
 * keyed by `grapheme|phonemes` so a row can never be glossed with a word that
 * demonstrates a different correspondence. §08 forbids invented numbers on this
 * surface; inventing the *words* would be the same failure one layer down.
 *
 * Lookup falls back rather than throwing: an unknown GPC still renders its IPA,
 * and an unknown phoneme still renders its grapheme. A table row that appears
 * blank because the inventory grew is worse than one that is merely terse.
 */

/**
 * ARPABET phoneme → IPA. Covers all 39 tokens in the seed inventory.
 *
 * Stress digits are stripped before lookup — CMUdict writes "AE1" and the API
 * strips them today, but a future `phonemes` value carrying stress must not
 * silently become an unglossed row.
 */
const IPA: Record<string, string> = {
  // vowels
  AA: 'ɑ',
  AE: 'æ',
  AH: 'ʌ',
  AO: 'ɔ',
  AW: 'aʊ',
  AY: 'aɪ',
  EH: 'ɛ',
  ER: 'ɜr',
  EY: 'eɪ',
  IH: 'ɪ',
  IY: 'iː',
  OW: 'oʊ',
  OY: 'ɔɪ',
  UH: 'ʊ',
  UW: 'uː',
  // consonants
  B: 'b',
  CH: 'tʃ',
  D: 'd',
  DH: 'ð',
  F: 'f',
  G: 'ɡ',
  HH: 'h',
  JH: 'dʒ',
  K: 'k',
  L: 'l',
  M: 'm',
  N: 'n',
  NG: 'ŋ',
  P: 'p',
  R: 'r',
  S: 's',
  SH: 'ʃ',
  T: 't',
  TH: 'θ',
  V: 'v',
  W: 'w',
  Y: 'j',
  Z: 'z',
  ZH: 'ʒ',
}

/**
 * `grapheme|phonemes` → the example word from `data/seed/gpcs.csv`.
 *
 * Generated from that file, not hand-typed. The key uses the raw space-separated
 * `phonemes` string the API returns, NOT `gpc_id`: the id encodes the same pair
 * differently (`Y UW` → `Y.UW`, `''` → `SIL`), and depending on that encoding
 * would couple this table to a serialisation detail rather than to the data.
 */
const EXAMPLE: Record<string, string> = {
  's|S': 'sat',
  'a|AE': 'cat',
  't|T': 'tap',
  'p|P': 'pin',
  'i|IH': 'sit',
  'n|N': 'net',
  'm|M': 'mat',
  'd|D': 'dad',
  'c|K': 'cat',
  'g|G': 'got',
  'o|AA': 'pot',
  'o|AO': 'dog',
  'k|K': 'kit',
  'e|EH': 'pet',
  'u|AH': 'duck',
  'r|R': 'run',
  'h|HH': 'hat',
  'b|B': 'bat',
  'f|F': 'fan',
  'l|L': 'lip',
  'ck|K': 'duck',
  'ss|S': 'miss',
  'll|L': 'bell',
  'ff|F': 'off',
  'zz|Z': 'buzz',
  'tt|T': 'little',
  'pp|P': 'happy',
  'nn|N': 'funny',
  'dd|D': 'ladder',
  'gg|G': 'egg',
  'bb|B': 'rabbit',
  'mm|M': 'hammer',
  'rr|R': 'carrot',
  'x|K S': 'fox',
  'sh|SH': 'ship',
  'ch|CH': 'chip',
  'th|TH': 'thin',
  'th|DH': 'this',
  'ng|NG': 'sing',
  'n|NG': 'finger',
  'qu|K W': 'quick',
  'w|W': 'win',
  'y|Y': 'yes',
  'z|Z': 'zip',
  'v|V': 'van',
  'j|JH': 'jam',
  's|Z': 'dogs',
  'es|IH Z': 'boxes',
  'ed|T': 'jumped',
  'ed|D': 'filled',
  'ed|IH D': 'wanted',
  'ing|IH NG': 'jumping',
  'a_e|EY': 'cake',
  'i_e|AY': 'bike',
  'o_e|OW': 'bone',
  'u_e|UW': 'tube',
  'u_e|Y UW': 'cube',
  'e_e|IY': 'these',
  'e|': 'house',
  'ai|EY': 'rain',
  'ay|EY': 'day',
  'ee|IY': 'see',
  'ea|IY': 'read',
  'oa|OW': 'boat',
  'ow|OW': 'snow',
  'igh|AY': 'night',
  'er|ER': 'singer',
  'y|IY': 'city',
  'y|AY': 'my',
  'ar|AA R': 'car',
  'or|AO R': 'for',
  'ir|ER': 'bird',
  'ur|ER': 'turn',
  'wa|W AO': 'water',
  'oi|OY': 'coin',
  'oy|OY': 'boy',
  'ou|AW': 'out',
  'ow|AW': 'cow',
  'au|AO': 'haul',
  'aw|AO': 'saw',
  'oo|UW': 'moon',
  'oo|UH': 'book',
  'c|S': 'city',
  'g|JH': 'gem',
  'kn|N': 'knight',
  'wr|R': 'write',
  'mb|M': 'lamb',
  'ph|F': 'phone',
  'le|AH L': 'little',
  'ti|SH': 'action',
  'si|ZH': 'vision',
  'ture|CH ER': 'picture',
}

export interface SoundGloss {
  /** IPA in slashes — `/ʃ/`. Empty string for a silent grapheme. */
  ipa: string
  /** The word that demonstrates the sound — `ship`. Null when unknown. */
  example: string | null
  /** True for a grapheme that spells no sound at all (silent final e). */
  silent: boolean
  /** One line, for `aria-label` and for the stacked mobile row. */
  spoken: string
}

/** ARPABET may carry a CMUdict stress digit; IPA does not. */
function ipaFor(phonemes: string): string {
  const tokens = phonemes.trim().split(/\s+/).filter(Boolean)
  if (!tokens.length) return ''
  return tokens.map((t) => IPA[t.replace(/\d+$/, '')] ?? t.toLowerCase()).join('')
}

/**
 * Gloss one row of the GPC table.
 *
 * @param grapheme the letters, e.g. `sh`
 * @param phonemes the ARPABET string exactly as the API returned it, e.g. `SH`
 */
export function glossSound(grapheme: string, phonemes: string): SoundGloss {
  const ipa = ipaFor(phonemes)
  const example = EXAMPLE[`${grapheme}|${phonemes.trim()}`] ?? null
  const silent = ipa === ''

  if (silent) {
    // Stage 5's final `e`. It has no sound, so "the sound in house" would be a
    // lie; what it does is change the vowel before it, which is the whole point
    // of the stage and is worth saying rather than leaving the cell empty.
    return {
      ipa: '',
      example,
      silent: true,
      spoken: example
        ? `${grapheme}, silent — as in "${example}"`
        : `${grapheme}, silent`,
    }
  }

  const spoken = example
    ? `${grapheme}, the sound in "${example}"`
    : `${grapheme}, the sound written ${ipa} in IPA`

  return { ipa, example, silent: false, spoken }
}

// --- accuracy bands (style guide §08 legend) ---------------------------------

export type Band = 'mastered' | 'practising' | 'building'

/**
 * §08's legend is "80%+ mastered · 50–79% practising · under 50% or few samples".
 *
 * "Few samples" is not defined by the style guide or by the API, so the UI has
 * to pick a number — and then *show* it, because an undefined "few" is exactly
 * the kind of invisible rule §08 rules out. Five answered instances is the floor
 * used here and it is printed in the legend, not hidden in this file.
 *
 * It is deliberately NOT `thresholds.min_observations` (10). That threshold is
 * §8's *aggregate* evidence floor across every GPC the current stage teaches;
 * reusing it per-row would push almost every individual correspondence into the
 * grey band and make the table say nothing. Two different questions, two
 * different floors — and the promotion panel shows the real one.
 */
export const FEW_SAMPLES_BELOW = 5

export const MASTERED_AT = 0.8
export const PRACTISING_AT = 0.5

export function bandFor(accuracy: number | null, nSeen: number): Band {
  if (accuracy === null || nSeen < FEW_SAMPLES_BELOW) return 'building'
  if (accuracy >= MASTERED_AT) return 'mastered'
  if (accuracy >= PRACTISING_AT) return 'practising'
  return 'building'
}

/**
 * §11: never colour alone. Every band carries a word and a bar fill pattern in
 * addition to its hue, and these labels are the same words the legend uses so
 * the two can never drift into describing different things.
 */
export const BAND_LABEL: Record<Band, string> = {
  mastered: 'mastered',
  practising: 'practising',
  building: 'building',
}
