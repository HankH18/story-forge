/**
 * `lib/phonics.ts` — ARPABET → something a parent can read (§08).
 *
 * The load-bearing test here is the one that reads `data/seed/gpcs.csv`.
 * `phonics.ts` says its example words "are NOT invented here — they are the
 * `example` column of `data/seed/gpcs.csv`, the same 92-row inventory the decoder
 * is built on". That is a claim about two files agreeing, which is exactly the
 * kind of claim that stops being true the first time the inventory grows and
 * nobody remembers this table exists. §08 forbids invented numbers on the parent
 * surface; inventing the *words* is the same failure one layer down, and it would
 * be invisible — a plausible-looking word next to the wrong sound.
 *
 * So the table is checked against the CSV in both directions: every gloss must
 * come from the data, and every row of the data must have a gloss. The CSV is
 * reached through the repo's own symlink (`data/seed/gpcs.csv` →
 * `services/api/storyforge/decoder/data/gpcs.csv`, DEVIATIONS 1.4), so the test
 * reads the same bytes the decoder loads.
 */

import { describe, expect, test } from 'bun:test'
import { BAND_LABEL, FEW_SAMPLES_BELOW, bandFor, glossSound } from './phonics'

const CSV = new URL('../../../../data/seed/gpcs.csv', import.meta.url)

interface Row {
  grapheme: string
  phonemes: string
  stage: number
  example: string
}

async function inventory(): Promise<Row[]> {
  const text = await Bun.file(CSV).text()
  const lines = text
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
  const header = lines[0].split(',')
  const at = (name: string) => {
    const i = header.indexOf(name)
    if (i < 0) throw new Error(`gpcs.csv has no ${name} column`)
    return i
  }
  const [g, p, s, e] = [at('grapheme'), at('phonemes'), at('stage'), at('example')]
  return lines.slice(1).map((line) => {
    const cells = line.split(',')
    return {
      grapheme: cells[g],
      phonemes: cells[p],
      stage: Number(cells[s]),
      example: cells[e] ?? '',
    }
  })
}

describe('glossSound against the real GPC inventory', () => {
  test('the inventory loaded is the one the decoder loads', async () => {
    const rows = await inventory()
    // Anti-vacuity: an empty or truncated read would make every assertion below
    // pass while proving nothing.
    expect(rows.length).toBeGreaterThan(80)
    expect(rows.every((r) => r.grapheme.length > 0)).toBe(true)
  })

  test('every row of the inventory has a gloss, and it is the CSV example word', async () => {
    const missing: string[] = []
    const wrong: string[] = []
    for (const row of await inventory()) {
      const gloss = glossSound(row.grapheme, row.phonemes)
      if (gloss.example === null) {
        missing.push(`${row.grapheme}|${row.phonemes}`)
        continue
      }
      if (row.example && gloss.example !== row.example) {
        wrong.push(`${row.grapheme}|${row.phonemes}: table says ${gloss.example}, csv says ${row.example}`)
      }
    }
    expect(missing).toEqual([])
    expect(wrong).toEqual([])
  })

  test('no multi-letter ARPABET token survives into the rendered IPA', async () => {
    // `ipaFor`'s fallback is `token.toLowerCase()`, so an unmapped `AE` renders
    // as `ae` — plausible enough to survive a screenshot and exactly the failure
    // this module exists to prevent ("AE looks like a typo, and a parent who
    // guesses reads it as the letters A-E, which is a DIFFERENT GPC").
    //
    // Restricted to tokens of two letters or more on purpose: for `S`, `B`, `D`
    // and friends the correct IPA genuinely IS the lowercased letter, so those
    // are indistinguishable from the fallback and asserting on them would be
    // asserting nothing. Every ARPABET token that is not a single letter has an
    // IPA form that is not its own spelling, which makes this decidable.
    const leaked: string[] = []
    let multiLetterTokensChecked = 0
    for (const row of await inventory()) {
      if (!row.phonemes.trim()) continue // silent final e, covered below
      const ipa = glossSound(row.grapheme, row.phonemes).ipa
      for (const token of row.phonemes.trim().split(/\s+/)) {
        const bare = token.replace(/\d+$/, '')
        if (bare.length < 2) continue
        multiLetterTokensChecked += 1
        if (ipa.includes(bare.toLowerCase())) leaked.push(`${row.grapheme}|${token} → ${ipa}`)
      }
    }
    expect(multiLetterTokensChecked).toBeGreaterThan(50)
    expect(leaked).toEqual([])
  })

  test('a silent grapheme says so instead of claiming a sound', async () => {
    const silent = (await inventory()).filter((r) => !r.phonemes.trim())
    expect(silent.length).toBeGreaterThan(0)
    for (const row of silent) {
      const gloss = glossSound(row.grapheme, row.phonemes)
      expect(gloss.silent).toBe(true)
      expect(gloss.ipa).toBe('')
      expect(gloss.spoken).toContain('silent')
    }
  })
})

describe('glossSound', () => {
  test('the example word is the primary rendering, IPA the secondary', () => {
    const gloss = glossSound('sh', 'SH')
    expect(gloss.example).toBe('ship')
    expect(gloss.ipa).toBe('ʃ')
    expect(gloss.spoken).toBe('sh, the sound in "ship"')
  })

  test('the two `th` rows are two different sounds, not one', () => {
    // The reason IPA is kept at all: `thin` and `this` are separate rows in the
    // inventory and an example word alone cannot tell a parent which is which.
    expect(glossSound('th', 'TH').ipa).toBe('θ')
    expect(glossSound('th', 'DH').ipa).toBe('ð')
    expect(glossSound('th', 'TH').example).toBe('thin')
    expect(glossSound('th', 'DH').example).toBe('this')
  })

  test('a multi-phoneme grapheme concatenates its IPA', () => {
    expect(glossSound('x', 'K S').ipa).toBe('ks')
    expect(glossSound('qu', 'K W').ipa).toBe('kw')
  })

  test('a CMUdict stress digit does not silently unglosss a row', () => {
    expect(glossSound('a', 'AE1').ipa).toBe('æ')
  })

  test('an unknown GPC still renders something a parent can read', () => {
    const gloss = glossSound('zzz', 'SH')
    expect(gloss.example).toBe(null)
    expect(gloss.ipa).toBe('ʃ')
    expect(gloss.spoken).toBe('zzz, the sound written ʃ in IPA')
  })
})

describe('bandFor (§08 legend)', () => {
  test('the legend thresholds, at their boundaries', () => {
    expect(bandFor(0.8, 10)).toBe('mastered')
    expect(bandFor(0.79, 10)).toBe('practising')
    expect(bandFor(0.5, 10)).toBe('practising')
    expect(bandFor(0.49, 10)).toBe('building')
    expect(bandFor(1, 10)).toBe('mastered')
    expect(bandFor(0, 10)).toBe('building')
  })

  test('too few samples is never "mastered", however good the ratio looks', () => {
    // A child who answered one question correctly has not mastered anything, and
    // §08 rules out saying so. The floor is printed in the legend, not hidden.
    expect(bandFor(1, FEW_SAMPLES_BELOW - 1)).toBe('building')
    expect(bandFor(1, FEW_SAMPLES_BELOW)).toBe('mastered')
    expect(bandFor(null, 100)).toBe('building')
  })

  test('every band has a word, because colour is never alone (§11)', () => {
    for (const band of ['mastered', 'practising', 'building'] as const) {
      expect(BAND_LABEL[band]).toBe(band)
    }
  })
})
