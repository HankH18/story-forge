/**
 * The SPA's copies of the seed CSVs must BE the seed CSVs.
 *
 * `SEEDED_THEMES` and `SEEDED_STAGES` exist because `GET /api/themes` and
 * `GET /api/stages` may fail, and an empty theme picker is a screen a child
 * cannot leave (§01). They were written by hand and mirror
 * `data/seed/{themes,stages}.csv`, which the database is built from — and until
 * this file there was nothing anywhere that could tell they had drifted.
 *
 * They had. This test's first run found `SEEDED_STAGES[0].description` reading
 * "simple three-letter words" where the CSV says "simple
 * consonant-vowel-consonant words" (DEVIATIONS 8.61). Every other field of all 11
 * stages and all 3 themes matched, which is exactly why the drift lasted: it is
 * one word on a form, and nobody diffs a comment against a CSV.
 *
 * The CSV is the authority here, not the TypeScript. `make seed` loads the CSV,
 * the API serves what the database holds, and the fallback exists to imitate the
 * API.
 */

import { describe, expect, test } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SEEDED_STAGES, SEEDED_THEMES } from './api'

/** Repo root from `apps/web/src/lib/` — four levels up. */
const REPO_ROOT = join(import.meta.dir, '..', '..', '..', '..')

/**
 * Minimal RFC4180-ish reader: enough for these two files, which use quotes only
 * to protect commas inside a description. A CSV library would be a dependency
 * for eleven lines.
 */
function parseCsv(text: string): Record<string, string>[] {
  const lines = text
    .split('\n')
    .filter((line) => line.trim() !== '' && !line.startsWith('#'))
  const cells = (line: string): string[] => {
    const out: string[] = []
    let cur = ''
    let quoted = false
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i]
      if (ch === '"') {
        if (quoted && line[i + 1] === '"') {
          cur += '"'
          i += 1
        } else quoted = !quoted
      } else if (ch === ',' && !quoted) {
        out.push(cur)
        cur = ''
      } else cur += ch
    }
    out.push(cur)
    return out.map((c) => c.trim())
  }
  const header = cells(lines[0]!)
  return lines.slice(1).map((line) => {
    const values = cells(line)
    return Object.fromEntries(header.map((key, i) => [key, values[i] ?? '']))
  })
}

function seedRows(name: string): Record<string, string>[] {
  return parseCsv(readFileSync(join(REPO_ROOT, 'data', 'seed', name), 'utf8'))
}

describe('SEEDED_THEMES mirrors data/seed/themes.csv', () => {
  const rows = seedRows('themes.csv')

  test('the CSV is actually being read — a silent empty parse would pass everything', () => {
    expect(rows.length).toBeGreaterThan(0)
    expect(Object.keys(rows[0]!).sort()).toEqual(['display_name', 'emoji', 'slug'])
  })

  test('same number of themes', () => {
    expect(SEEDED_THEMES.length).toBe(rows.length)
  })

  test('every field matches, in seed order', () => {
    // `themes.id` is a serial assigned in file order by `storyforge.seed`, and
    // the picker sends a slug — but `Theme.id` is what an admin caller would
    // send, so the ordering is asserted rather than assumed.
    expect(SEEDED_THEMES.map((t) => t.id)).toEqual(rows.map((_, i) => i + 1))
    expect(SEEDED_THEMES.map((t) => t.slug)).toEqual(rows.map((r) => r.slug))
    expect(SEEDED_THEMES.map((t) => t.display_name)).toEqual(rows.map((r) => r.display_name))
    expect(SEEDED_THEMES.map((t) => t.emoji)).toEqual(rows.map((r) => r.emoji))
  })
})

describe('SEEDED_STAGES mirrors data/seed/stages.csv', () => {
  const rows = seedRows('stages.csv')

  test('the CSV is actually being read', () => {
    expect(rows.length).toBe(11)
    expect(Object.keys(rows[0]!)).toContain('description')
  })

  test('same number of stages, and the renumbered ladder has eleven', () => {
    expect(SEEDED_STAGES.length).toBe(rows.length)
  })

  test('ids, names and descriptions match', () => {
    expect(SEEDED_STAGES.map((s) => s.id)).toEqual(rows.map((r) => Number(r.id)))
    expect(SEEDED_STAGES.map((s) => s.name)).toEqual(rows.map((r) => r.name))
    // The assertion that was red on the first run.
    expect(SEEDED_STAGES.map((s) => s.description)).toEqual(rows.map((r) => r.description))
  })
})
