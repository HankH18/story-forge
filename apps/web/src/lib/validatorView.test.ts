/**
 * The validator playground's pure half.
 *
 * Three properties are worth a test here, and they are the three that can be
 * wrong without looking wrong on screen:
 *
 *   1. a verdict lands on the word it belongs to, whatever the casing;
 *   2. a missing GPC id becomes a NAME (§17 step 1's actual requirement);
 *   3. an unalignable violation says so instead of reporting an empty set.
 *
 * Every fixture in this file was copied from a REAL `POST /api/validate` response
 * against a server reporting `demo_bypass_auth: false` — not composed by hand —
 * so a shape change on the API side breaks these tests rather than passing them.
 */

import { describe, expect, test } from 'bun:test'
import {
  DEMO_STAGE_ID,
  DEMO_STORY_WORDS,
  DEMO_TEXT,
  VALIDATE_DEBOUNCE_MS,
  alignReportToInput,
  count,
  elapsed,
  explainVerdict,
  nameGpc,
  pct,
  unalignableWords,
  violationLines,
  wordsFlagged,
  type TextReport,
  type Verdict,
  type WordReport,
} from './validatorView'

function word(w: string, verdict: Verdict, over: Partial<WordReport> = {}): WordReport {
  return {
    word: w,
    verdict,
    alignment: [],
    missing_gpcs: [],
    ambiguity_flag: false,
    schwa_relaxed: false,
    alignable: true,
    missing_minimal: true,
    heteronym_flag: false,
    ...over,
  }
}

/**
 * The §17 demo sentence, exactly as the live API returned it at stage 3 with
 * `story_words: ["dragon"]`. This is the locked fixture
 * (`tests/decoder/golden.yaml::demo-sentence-stage-3`) seen from the client.
 */
const DEMO_WORDS: WordReport[] = [
  word('the', 'sight'),
  word('knight', 'violation', { missing_gpcs: ['igh|AY', 'kn|N'] }),
  word('sees', 'violation', { missing_gpcs: ['ee|IY', 's|Z'] }),
  word('a', 'sight'),
  word('beautiful', 'violation', { alignable: false }),
  word('dragon', 'story'),
]

const DEMO_REPORT: TextReport = {
  words: DEMO_WORDS,
  decodability: 0.0,
  violations: [DEMO_WORDS[1], DEMO_WORDS[2], DEMO_WORDS[4]],
  story_word_count: 1,
  distinct_story_words: ['dragon'],
  counts: { decodable: 0, sight: 2, story: 1, violation: 3, oov: 0, punct: 0 },
  scored_word_count: 3,
  schwa_relaxed_count: 0,
  ambiguity_count: 0,
  heteronym_count: 0,
  unalignable_count: 1,
}

describe('§5.6 constants', () => {
  test('the debounce is the spec number, not a nearby one', () => {
    expect(VALIDATE_DEBOUNCE_MS).toBe(300)
  })

  test('the prefilled demo is §17 step 1 including the story word', () => {
    // Measured: without `story_words` the same sentence is 0.25 decodable with
    // zero story words, because `dragon` IS decodable at stage 3 (§5.2.6). The
    // fixture is only reproducible with the story word set (DEVIATIONS 9.24).
    expect(DEMO_TEXT).toBe('The knight sees a beautiful dragon')
    expect(DEMO_STAGE_ID).toBe(3)
    expect(DEMO_STORY_WORDS).toBe('dragon')
  })
})

describe('alignReportToInput — the verdict lands on the right word', () => {
  test("the admin's capital letter survives and every verdict is the report's", () => {
    const tokens = alignReportToInput(DEMO_TEXT, DEMO_WORDS)

    expect(tokens.map((t) => t.text)).toEqual([
      'The',
      'knight',
      'sees',
      'a',
      'beautiful',
      'dragon',
    ])
    expect(tokens.map((t) => t.verdict)).toEqual([
      'sight',
      'violation',
      'violation',
      'sight',
      'violation',
      'story',
    ])
    expect(tokens.every((t) => t.exact)).toBe(true)
  })

  test('re-joining text and gap reproduces the submitted string exactly', () => {
    // The property that makes this safe to render: nothing is invented and
    // nothing is dropped. A rendering that re-inserted single spaces would
    // silently rewrite a paragraph.
    const input = 'Sam  had a big red ship.\n\nThe Ship did not sink!'
    const words: WordReport[] = [
      word('sam', 'decodable'),
      word('had', 'decodable'),
      word('a', 'sight'),
      word('big', 'decodable'),
      word('red', 'decodable'),
      word('ship', 'decodable'),
      word('.', 'punct'),
      word('the', 'sight'),
      word('ship', 'decodable'),
      word('did', 'decodable'),
      word('not', 'decodable'),
      word('sink', 'decodable'),
      word('!', 'punct'),
    ]

    const tokens = alignReportToInput(input, words)
    expect(tokens.map((t) => t.gap + t.text).join('')).toBe(input)
    // The second "Ship" is the capitalised one, and it did not steal the first
    // occurrence: the cursor only moves forward.
    expect(tokens[8].text).toBe('Ship')
    expect(tokens[5].text).toBe('ship')
  })

  test('a token the fold cannot find degrades to lowercase, never to a wrong word', () => {
    // Real observed case: `O'Brien’s` — a straight apostrophe and a curly one in
    // one word — comes back as the single token `o'brien's`. The fold handles it.
    const folded = alignReportToInput("O'Brien’s dog", [
      word("o'brien's", 'violation', { missing_gpcs: ['ir|ER'] }),
      word('dog', 'decodable'),
    ])
    expect(folded[0].text).toBe("O'Brien’s")
    expect(folded[0].exact).toBe(true)

    // And when a token genuinely is not in the input, the API's own string is
    // shown with its own verdict rather than a neighbour's slice.
    const missed = alignReportToInput('cat sat', [
      word('cat', 'decodable'),
      word('elephant', 'oov'),
      word('sat', 'decodable'),
    ])
    expect(missed.map((t) => t.text)).toEqual(['cat', 'elephant', 'sat'])
    expect(missed.map((t) => t.verdict)).toEqual(['decodable', 'oov', 'decodable'])
    expect(missed[1].exact).toBe(false)
    // The cursor did not move, so `sat` is still found in the right place.
    expect(missed[2].exact).toBe(true)
  })

  test('an empty report and an empty input are both fine', () => {
    expect(alignReportToInput('', [])).toEqual([])
    expect(alignReportToInput('cat', [])).toEqual([])
  })
})

describe('nameGpc — §17 wants the GPCs NAMED', () => {
  test('the ids from the demo sentence become names with IPA', () => {
    expect(nameGpc('kn|N').label).toBe('kn /n/')
    expect(nameGpc('igh|AY').label).toBe('igh /aɪ/')
    expect(nameGpc('ee|IY').label).toBe('ee /iː/')
    expect(nameGpc('s|Z').label).toBe('s /z/')
  })

  test('the example word comes from the seed inventory, not from here', () => {
    expect(nameGpc('kn|N').example).toBe('knight')
    expect(nameGpc('igh|AY').example).toBe('night')
    expect(nameGpc('kn|N').spoken).toBe('kn, the sound in "knight"')
  })

  test('a multi-phoneme id splits on the dot the id format uses', () => {
    // `GPC.id` joins phonemes with `.`; `phonics.glossSound` keys on spaces.
    expect(nameGpc('x|K.S').grapheme).toBe('x')
    expect(nameGpc('x|K.S').ipa).toBe('ks')
    expect(nameGpc('x|K.S').example).toBe('fox')
    expect(nameGpc('ture|CH.ER').example).toBe('picture')
  })

  test('SIL is a silent grapheme, not a phoneme called SIL', () => {
    const silent = nameGpc('e|SIL')
    expect(silent.ipa).toBe('')
    expect(silent.label).toBe('e (silent)')
  })

  test('an unrecognised id degrades to itself rather than to a blank', () => {
    expect(nameGpc('nonsense').label).toBe('nonsense')
    expect(nameGpc('zz|QQ').grapheme).toBe('zz')
    // Unknown phoneme falls through phonics.ts's own lowercase fallback.
    expect(nameGpc('zz|QQ').label).toBe('zz /qq/')
  })
})

describe('explainVerdict — the unalignable case is the one that must not be blank', () => {
  test('a violation names its missing GPCs, in the order the API sent them', () => {
    // The API returns `missing_gpcs` sorted (`["igh|AY","kn|N"]`), which is NOT
    // the spelling order style guide §09's mock draws ("missing kn, igh"). The
    // response's order is what is rendered: re-sorting by position in the word
    // would be the UI inventing an order the validator did not report.
    expect(DEMO_WORDS[1].missing_gpcs).toEqual(['igh|AY', 'kn|N'])
    expect(explainVerdict(DEMO_WORDS[1])).toBe('missing igh /aɪ/, kn /n/')
    expect(explainVerdict(DEMO_WORDS[2])).toBe('missing ee /iː/, s /z/')
  })

  test('`beautiful` says the inventory cannot spell it, not "missing nothing"', () => {
    const reason = explainVerdict(DEMO_WORDS[4])
    expect(reason).toContain('cannot spell this word at any stage')
    // The assertion that matters: it never claims a missing set it does not have.
    expect(reason).not.toContain('missing')
  })

  test('a non-minimal missing set is hedged rather than reported as proof', () => {
    const bounded = word('whatever', 'violation', {
      missing_gpcs: ['sh|SH'],
      missing_minimal: false,
    })
    expect(explainVerdict(bounded)).toContain('may not be minimal')
  })

  test('the other four verdicts each say something true', () => {
    expect(explainVerdict(word('zorp', 'oov'))).toContain('CMUdict')
    expect(explainVerdict(word('the', 'sight'))).toContain('not scored')
    expect(explainVerdict(word('dragon', 'story'))).toContain('≤3')
    expect(explainVerdict(word('.', 'punct'))).toContain('never scored')
    expect(explainVerdict(word('cat', 'decodable'))).toBe('decodable')
    expect(explainVerdict(word('dragon', 'decodable', { schwa_relaxed: true }))).toContain(
      'schwa relaxed',
    )
  })
})

describe('the numbers on the header cards', () => {
  test('the fixture headline is 0.0%, with the decimal', () => {
    expect(pct(DEMO_REPORT.decodability)).toBe('0.0%')
    expect(pct(1)).toBe('100.0%')
    expect(pct(0.8181818181818182)).toBe('81.8%')
    expect(pct(Number.NaN)).toBe('—')
  })

  test('row counts are separated because four digits is the point', () => {
    expect(count(9360)).toBe('9,360')
    expect(count(157)).toBe('157')
  })

  test('elapsed switches unit at a second, per style guide §09', () => {
    expect(elapsed(38)).toBe('38ms')
    expect(elapsed(4100)).toBe('4.1s')
    expect(elapsed(null)).toBe('')
    expect(elapsed(undefined)).toBe('')
  })

  test("an eval run's wall clock reads as minutes, not as 1068.2s", () => {
    // Run 114's `wall_clock_s` is 1068.2, and it was rendering as `1068.2s` in a
    // real browser before this tier existed. `docs/EVAL.md` calls it 17m49s; this
    // rounds the seconds rather than truncating, so it says 17m48s — the same
    // measurement, one rounding apart, and never four digits of seconds.
    expect(elapsed(1068.2 * 1000)).toBe('17m48s')
    expect(elapsed(119_000)).toBe('119.0s')
    expect(elapsed(120_000)).toBe('2m00s')
  })

  test('violation lines read across from the highlighted line, in text order', () => {
    expect(violationLines(DEMO_REPORT).map((l) => l.word)).toEqual([
      'knight',
      'sees',
      'beautiful',
    ])
  })

  test('the §09 header names WHICH words carry a flag, not how many', () => {
    const withSchwa: TextReport = {
      ...DEMO_REPORT,
      words: [word('dragon', 'decodable', { schwa_relaxed: true }), word('cat', 'decodable')],
      schwa_relaxed_count: 1,
    }
    expect(wordsFlagged(withSchwa, 'schwa_relaxed')).toEqual(['dragon'])
    expect(wordsFlagged(DEMO_REPORT, 'schwa_relaxed')).toEqual([])
  })

  test('§18’s inventory signal is named, not counted', () => {
    expect(unalignableWords(DEMO_REPORT)).toEqual(['beautiful'])
  })
})
