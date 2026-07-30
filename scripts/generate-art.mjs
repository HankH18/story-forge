#!/usr/bin/env node
/**
 * Generate the §12 illustration pool and the §06 story-word cutouts.
 *
 *   node scripts/generate-art.mjs           # write the files
 *   node scripts/generate-art.mjs --check   # verify the committed files are current
 *
 * NOT part of `bun run build` and never run at serve time. The output is
 * committed, exactly like `scripts/vendor-cmudict.py`'s vendored dictionary: the
 * app serves static files, and this is the tool that drew them (DEVIATIONS 11.20).
 *
 * Two things are asserted before anything is written, because both are failures
 * that never raise at runtime and never show up in a metric:
 *
 * 1. **Every file this writes has a row in `data/seed/assets.csv`, and every row
 *    in that CSV has a file here** — with the SAME tag set. An asset row whose
 *    url points at nothing renders the striped placeholder forever (the defect
 *    this whole pass exists to fix), and a drawing whose tags have drifted from
 *    its row is a picture that no longer matches the scene it is chosen for.
 * 2. **Every colour used is in the style guide's palette.** §06's consistency
 *    gate is "same palette across every theme"; a hand-typed hex that is 2%
 *    off passes every review and is visible in a strip of thumbnails.
 */

import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

import { P } from './art/kit.mjs'
import { COVERS, CUTOUTS, SEGMENTS, W, H, CW, cutout, doc } from './art/scenes.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')
const ART_DIR = join(ROOT, 'apps/web/public/assets/art/v1')
const CUTOUT_DIR = join(ROOT, 'apps/web/src/assets/cutouts')
const CSV = join(ROOT, 'data/seed/assets.csv')

/** §11.23: the version lives in the URL because the cache header is immutable. */
const URL_PREFIX = '/assets/art/v1'

const check = process.argv.includes('--check')

/* -------------------------------------------------------------------------- */

const PALETTE = new Set(Object.values(P).map((c) => c.toUpperCase()))
PALETTE.add('NONE')

function assertPalette(name, svg) {
  const bad = new Set()
  for (const m of svg.matchAll(/(?:fill|stroke)="([^"]+)"/g)) {
    const v = m[1].toUpperCase()
    if (v.startsWith('URL(')) continue
    if (!PALETTE.has(v)) bad.add(m[1])
  }
  if (bad.size) {
    throw new Error(
      `${name}: colour(s) outside the style guide palette: ${[...bad].join(', ')} — ` +
        '§06 consistency gate "same palette across every theme".',
    )
  }
}

/** Minimal CSV reader — the file has quoted-free fields and one header row. */
function readCsvRows() {
  const lines = readFileSync(CSV, 'utf8')
    .split('\n')
    .filter((l) => l.trim() && !l.startsWith('#'))
  const header = lines.shift().split(',')
  return lines.map((line) => {
    // alt_text is the last column and may contain commas.
    const parts = line.split(',')
    const row = {}
    header.forEach((h, i) => {
      row[h] = i === header.length - 1 ? parts.slice(i).join(',') : parts[i]
    })
    return row
  })
}

/* -------------------------------------------------------------------------- */

const files = new Map() // absolute path -> contents
const drawn = new Map() // csv url -> tags

for (const [name, { tags, draw }] of Object.entries(SEGMENTS)) {
  const svg = doc(W, H, draw())
  assertPalette(name, svg)
  files.set(join(ART_DIR, `${name}.svg`), svg)
  drawn.set(`${URL_PREFIX}/${name}.svg`, tags)
}

for (const [name, { tags, draw }] of Object.entries(COVERS)) {
  const svg = doc(CW, CW, draw())
  assertPalette(name, svg)
  files.set(join(ART_DIR, `${name}.svg`), svg)
  drawn.set(`${URL_PREFIX}/${name}.svg`, tags)
}

for (const [word, draw] of Object.entries(CUTOUTS)) {
  const svg = cutout(draw())
  assertPalette(`cutout/${word}`, svg)
  files.set(join(CUTOUT_DIR, `${word}.svg`), svg)
}

/* --- gate 1: the pool and the CSV agree, both ways ------------------------ */

const rows = readCsvRows()
const csvTags = new Map(
  rows.map((r) => [r.url.trim(), r.tags.split('|').map((t) => t.trim()).filter(Boolean)]),
)

const problems = []
for (const [url, tags] of drawn) {
  const fromCsv = csvTags.get(url)
  if (!fromCsv) {
    problems.push(`drawn but not in assets.csv: ${url}`)
    continue
  }
  const a = [...tags].sort().join('|')
  const b = [...fromCsv].sort().join('|')
  if (a !== b) problems.push(`tags differ for ${url}: drawn [${a}] vs assets.csv [${b}]`)
}
for (const url of csvTags.keys()) {
  if (!drawn.has(url)) problems.push(`in assets.csv but no drawing: ${url}`)
}
if (problems.length) {
  console.error('generate-art: the pool and data/seed/assets.csv disagree:')
  for (const p of problems) console.error(`  - ${p}`)
  process.exit(1)
}

/* --- write, or check ------------------------------------------------------ */

const sha = (s) => createHash('sha256').update(s).digest('hex').slice(0, 12)

if (check) {
  let stale = 0
  for (const [file, contents] of files) {
    let on = null
    try {
      on = readFileSync(file, 'utf8')
    } catch {
      /* missing */
    }
    if (on !== contents) {
      stale++
      console.error(
        `stale: ${relative(ROOT, file)} (on disk ${on === null ? 'missing' : sha(on)}, ` +
          `generator says ${sha(contents)})`,
      )
    }
  }
  // A file on disk that the generator no longer draws is stale too — it would
  // still be served and would still be cached for a year (11.23).
  for (const dir of [ART_DIR, CUTOUT_DIR]) {
    for (const f of walk(dir)) {
      if (!files.has(f)) {
        stale++
        console.error(`orphan: ${relative(ROOT, f)} is not drawn by the generator`)
      }
    }
  }
  if (stale) {
    console.error(`\n${stale} file(s) out of date — run: node scripts/generate-art.mjs`)
    process.exit(1)
  }
  console.log(`generate-art --check: ${files.size} file(s) current`)
} else {
  let bytes = 0
  for (const [file, contents] of files) {
    mkdirSync(dirname(file), { recursive: true })
    writeFileSync(file, contents)
    bytes += Buffer.byteLength(contents)
  }
  const seg = Object.keys(SEGMENTS).length
  const cov = Object.keys(COVERS).length
  const cut = Object.keys(CUTOUTS).length
  console.log(
    `generate-art: ${seg} segment + ${cov} cover + ${cut} cutout = ${files.size} files, ` +
      `${(bytes / 1024).toFixed(1)} KiB total (largest ${largest()})`,
  )
}

function largest() {
  let best = ['', 0]
  for (const [file, contents] of files) {
    const n = Buffer.byteLength(contents)
    if (n > best[1]) best = [relative(ROOT, file), n]
  }
  return `${best[0]} ${(best[1] / 1024).toFixed(1)} KiB`
}

function* walk(dir) {
  let entries
  try {
    entries = readdirSync(dir)
  } catch {
    return
  }
  for (const e of entries) {
    const full = join(dir, e)
    if (statSync(full).isDirectory()) yield* walk(full)
    else if (e.endsWith('.svg')) yield full
  }
}
