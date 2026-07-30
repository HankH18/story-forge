/**
 * Turning a `TextReport` into something a grader can read — the pure half of the
 * validator playground (build doc §5.6, §17 step 1; style guide §09).
 *
 * Everything here is a function of the response and the text that was submitted.
 * No fetch, no React, no DOM — which is what makes the three things that can be
 * silently wrong testable: which word a verdict belongs to, what a missing GPC is
 * called, and what to say when there is no missing GPC to name.
 *
 * DEVIATIONS 9.26, 9.28, 9.29.
 */

import { glossSound } from './phonics'

/** `decoder/types.py::WordVerdict` — six values, no seventh. */
export type Verdict = 'decodable' | 'sight' | 'story' | 'violation' | 'oov' | 'punct'

/**
 * §5.6: "Debounce client-side at 300ms."
 *
 * Exported and asserted by a test rather than written as a literal in the
 * component, because a number that came from the spec should not be able to drift
 * into 250 during a refactor without something going red. It debounces the TEXT
 * only — see the component for why a stage change revalidates at once
 * (DEVIATIONS 9.29).
 */
export const VALIDATE_DEBOUNCE_MS = 300

/**
 * §17 step 1, as the screen's opening state.
 *
 * `story_words: ['dragon']` is load-bearing and was measured, not assumed:
 * without it the same sentence at stage 3 returns 0.25 decodability, zero story
 * words and `dragon` as DECODABLE via §5.2.6's schwa relaxation — `dragon`
 * genuinely is decodable at stage 3. The repo's locked fixture
 * (`tests/decoder/golden.yaml::demo-sentence-stage-2`) is 0.0%, three violations
 * and one story word, and the story word is how it gets there (DEVIATIONS 9.24).
 */
export const DEMO_TEXT = 'The knight sees a beautiful dragon'
export const DEMO_STAGE_ID = 2
export const DEMO_STORY_WORDS = 'dragon'

// --- the report, as the API returns it ---------------------------------------

/** `decoder/types.py::WordReport`, field for field. */
export interface WordReport {
  word: string
  verdict: Verdict
  alignment: string[]
  missing_gpcs: string[]
  ambiguity_flag: boolean
  schwa_relaxed: boolean
  /** False when the 93-GPC inventory cannot spell the word at ANY stage. */
  alignable: boolean
  /** False when the minimal-missing-set search hit its bound and approximated. */
  missing_minimal: boolean
  heteronym_flag: boolean
}

/** `decoder/types.py::TextReport`, field for field. */
export interface TextReport {
  words: WordReport[]
  decodability: number
  violations: WordReport[]
  story_word_count: number
  distinct_story_words: string[]
  counts: Partial<Record<Verdict, number>>
  scored_word_count: number
  schwa_relaxed_count: number
  ambiguity_count: number
  heteronym_count: number
  unalignable_count: number
}

// --- 1. which word a verdict belongs to --------------------------------------

/**
 * One token as the screen draws it: the admin's own characters, the API's verdict.
 */
export interface DisplayToken {
  /** The slice of the submitted text this token came from — original casing. */
  text: string
  /** The whitespace and unmatched characters that preceded it, verbatim. */
  gap: string
  verdict: Verdict
  report: WordReport
  /**
   * False when the token could not be located in the submitted text and `text`
   * fell back to the API's own lowercased string. The screen does not draw this
   * differently — a lowercase word is not worth a warning — but a test can see
   * it, and it is the difference between "cosmetic" and "wrong word".
   */
  exact: boolean
}

/**
 * Characters the tokenizer normalises on its way out, mapped back so a search
 * against the raw input still finds the token.
 *
 * Every entry is ONE character replaced by ONE character, which is what makes
 * this safe: the normalised copy is index-for-index the same length as the
 * original, so an offset found in one is valid in the other. A rule that changed
 * length (stripping a character, expanding a ligature) would silently shift every
 * later slice by one, and the symptom would be a word rendered with a neighbour's
 * first letter.
 *
 * Curly apostrophes are the observed case: `O'Brien’s` comes back as the single
 * token `o'brien's`, with the curly apostrophe straightened.
 */
const CHAR_FOLD: Record<string, string> = {
  '‘': "'",
  '’': "'",
  '‛': "'",
  'ʼ': "'",
  '′': "'",
  '“': '"',
  '”': '"',
}

function fold(s: string): string {
  let out = ''
  for (const ch of s) out += CHAR_FOLD[ch] ?? ch
  return out.toLowerCase()
}

/**
 * Walk the report's tokens against the text that produced them.
 *
 * `POST /api/validate` returns tokens lowercased and punctuation as its own
 * token, in input order — so the sequence reconstructs the text, but not the
 * text an admin typed. Rendering the response's strings verbatim prints "the
 * knight…" back at someone who typed "The knight…", which reads as the app
 * having mangled the input rather than as a normalisation the validator does on
 * purpose.
 *
 * So: one forward cursor, one case-insensitive search per token, and the slice
 * comes from the ORIGINAL string. The gap between two tokens is carried along
 * too, which is what makes the admin's own line breaks and double spaces survive
 * — a rendering that re-inserted single spaces would quietly rewrite a
 * paragraph.
 *
 * **The fallback is the point.** A token that cannot be found (a fold this table
 * does not know about, a decomposed accent) renders the API's own string. So the
 * worst case is a lowercase word in an otherwise cased line; it is never a
 * different word, never a dropped word, and never a verdict attached to the wrong
 * token, because the verdict travels with the report entry either way.
 */
export function alignReportToInput(input: string, words: WordReport[]): DisplayToken[] {
  const haystack = fold(input)
  const out: DisplayToken[] = []
  let cursor = 0

  for (const report of words) {
    const needle = fold(report.word)
    const at = needle ? haystack.indexOf(needle, cursor) : -1

    if (at === -1) {
      out.push({
        text: report.word,
        gap: cursor === 0 ? '' : ' ',
        verdict: report.verdict,
        report,
        exact: false,
      })
      continue
    }

    out.push({
      text: input.slice(at, at + needle.length),
      gap: input.slice(cursor, at),
      verdict: report.verdict,
      report,
      exact: true,
    })
    cursor = at + needle.length
  }

  return out
}

// --- 2. what a missing GPC is called ----------------------------------------

/** A parsed `missing_gpcs` entry — `igh|AY` → `igh`, `/aɪ/`, as in "night". */
export interface NamedGpc {
  /** The raw id, kept so the mono line can fall back to it. */
  id: string
  /** The letters — `igh`, `kn`, `s`. */
  grapheme: string
  /** IPA in slashes, or `''` for a grapheme that spells no sound. */
  ipa: string
  /** The seed inventory's example word, when there is one. */
  example: string | null
  /** `igh /aɪ/` — what §17's "missing GPCs named" means on screen. */
  label: string
  /** One line for `title`/`aria-label`: `igh, the sound in "night"`. */
  spoken: string
}

/**
 * Parse one `missing_gpcs` id.
 *
 * The format is `decoder/types.py::GPC.id` — `grapheme|phoneme.phoneme`, with the
 * literal `SIL` where a grapheme spells nothing (stage 5's silent final `e`).
 * `lib/phonics.ts` already owns the ARPABET→IPA table and the inventory's example
 * words, keyed on a SPACE-separated phoneme string, so the dots are converted and
 * that table is reused rather than copied — a second copy is a second thing to
 * update when the inventory grows.
 *
 * An id in a shape this function does not recognise degrades to the raw id as the
 * label. §17 wants the GPCs named; a row that says `weird|thing` is worse than a
 * name and better than a blank.
 */
export function nameGpc(id: string): NamedGpc {
  const bar = id.indexOf('|')
  if (bar < 0) {
    return { id, grapheme: id, ipa: '', example: null, label: id, spoken: id }
  }

  const grapheme = id.slice(0, bar)
  const raw = id.slice(bar + 1)
  const phonemes = raw === 'SIL' ? '' : raw.split('.').join(' ')
  const gloss = glossSound(grapheme, phonemes)

  const label = gloss.ipa ? `${grapheme} /${gloss.ipa}/` : `${grapheme} (silent)`

  return {
    id,
    grapheme,
    ipa: gloss.ipa,
    example: gloss.example,
    label,
    spoken: gloss.spoken,
  }
}

/**
 * The whole reason a word is red, in one sentence.
 *
 * Three cases, and the third is the one that would make the demo look broken if
 * it were left to the generic path. `beautiful` at ANY stage is a VIOLATION with
 * an EMPTY `missing_gpcs` and `alignable: false`: the inventory cannot spell it at
 * all (`B Y UW T AH F AH L` needs an `eau`), so there is no set of GPCs whose
 * addition would fix it. "missing: nothing" is not an explanation. §5.3 declares
 * that non-goal and §18 names a rising unalignable rate as the signal that the
 * inventory is too small, so the honest sentence says which of the two facts this
 * is.
 *
 * `missing_minimal: false` is surfaced for the same reason: the search hit its
 * bound and the answer is an over-estimate, and eval is explicitly built never to
 * mistake an approximation for a proof.
 */
export function explainVerdict(report: WordReport): string {
  switch (report.verdict) {
    case 'violation': {
      if (!report.alignable) {
        return 'the 93-GPC inventory cannot spell this word at any stage — no set of sounds would fix it (§5.3)'
      }
      if (!report.missing_gpcs.length) {
        return 'violation with no missing set reported'
      }
      const named = report.missing_gpcs.map((id) => nameGpc(id).label).join(', ')
      const hedge = report.missing_minimal ? '' : ' (search bounded — may not be minimal)'
      return `missing ${named}${hedge}`
    }
    case 'oov':
      return 'not in CMUdict — no pronunciation to check (§5.3)'
    case 'sight':
      return 'sight word — allowlisted, not scored'
    case 'story':
      return 'story word — counts against the ≤3 picture-chip budget (§4)'
    case 'decodable': {
      const notes: string[] = []
      if (report.schwa_relaxed) notes.push('schwa relaxed (§5.2.6)')
      if (report.heteronym_flag) notes.push('heteronym — one pronunciation passes (§5.2.5)')
      if (report.ambiguity_flag) notes.push('ambiguous alignment')
      return notes.length ? `decodable · ${notes.join(' · ')}` : 'decodable'
    }
    case 'punct':
      return 'punctuation — never scored'
  }
}

/**
 * The `word → reason` lines style guide §09 prints under `violations`.
 *
 * Order is the response's, which is text order — the same order as the
 * highlighted line above it, so a red word and its reason can be read across.
 */
export function violationLines(report: TextReport): { word: string; reason: string }[] {
  return report.violations.map((v) => ({ word: v.word, reason: explainVerdict(v) }))
}

// --- 3. the numbers ---------------------------------------------------------

/** `0.0%` — one decimal, because the fixture's headline number IS `0.0%`. */
export function pct(value: number, digits = 1): string {
  if (!Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

/** `9,360` — thousands separated, because a 4-digit row count is the point. */
export function count(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return value.toLocaleString('en-US')
}

/**
 * `38ms` under a second, `4.1s` under two minutes, `17m48s` beyond — style guide
 * §09's pill format, plus one tier it did not need and this app does.
 *
 * Elapsed time is read as a magnitude, not a measurement, and `4100ms` makes a
 * reader do arithmetic during a demo. §09 drew only ms and s because a pipeline
 * node is seconds; an eval run's WALL CLOCK is not, and `1068.2s` for the §14
 * matrix is exactly the arithmetic this function exists to avoid — it is 17m48s,
 * which is the number `docs/EVAL.md` quotes for run 114.
 */
export function elapsed(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 120_000) return `${(ms / 1000).toFixed(1)}s`
  const totalSeconds = Math.round(ms / 1000)
  return `${Math.floor(totalSeconds / 60)}m${String(totalSeconds % 60).padStart(2, '0')}s`
}

/**
 * The words the §09 header line names: `schwa_relaxed: dragon`.
 *
 * Derived from the per-word reports rather than from `schwa_relaxed_count`,
 * because the count answers "how many" and the demo asks "which".
 */
export function wordsFlagged(report: TextReport, flag: 'schwa_relaxed' | 'ambiguity_flag' | 'heteronym_flag'): string[] {
  const seen = new Set<string>()
  for (const w of report.words) {
    if (w[flag]) seen.add(w.word)
  }
  return [...seen]
}

/** The words the inventory cannot spell at any stage — §18's signal, by name. */
export function unalignableWords(report: TextReport): string[] {
  const seen = new Set<string>()
  for (const w of report.words) {
    if (!w.alignable && w.verdict === 'violation') seen.add(w.word)
  }
  return [...seen]
}
