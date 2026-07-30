/**
 * `make fit-check` — the §10 gate. FREE, and it exits non-zero.
 *
 * §10, verbatim: *"One segment per screen, no scrolling on tablet — if text
 * overflows, generation length is wrong, not the layout."* Nothing measured it.
 * `warm-check` counts servable stories, `promote-check` counts §8 evidence,
 * `prompt-check` reads what a question says — three green gates over a pool in
 * which 8-10% of pages did not fit the screen, including the LAST page of every
 * story at stages 8-11, which is the one carrying the ending (DEVIATIONS 13.1).
 *
 * What it does:
 *
 *   1. reads every servable segment — the same `SERVABLE_STATUSES` +
 *      `ANSWERABLE_SQL` predicate `_SERVE_SQL` and `warm.pool_coverage` use, so
 *      the gate and the serving path cannot disagree about which pages exist
 *      (STATE invariant 14);
 *   2. builds the app WITH the fit harness (`SF_FIT_HARNESS=1`) and serves
 *      `dist/` through `vite preview` — the production bundle, purged CSS,
 *      bundled Andika (STATE invariant 7);
 *   3. renders each segment through the real `ReadingView` at both §10 reference
 *      viewports and both §11 reading sizes, and measures
 *      `main.scrollHeight - main.clientHeight`;
 *   4. prints the per-stage character budget the generator would need, and exits
 *      1 if anything overflows.
 *
 * **It exits non-zero when it cannot run.** No browser, no database, no build:
 * all of those are `EXIT_CANNOT_RUN`, never a pass. A gate that reports green
 * because it did nothing is the failure this repo has now recorded four times.
 *
 * The browser is gstack `browse` (a shared headless Chromium). Override the path
 * with `BROWSE_BIN=` and the daemon port with `BROWSE_SERVER_PORT=` — two agents
 * sweeping at once on one port drive each other's tabs.
 *
 *   bun run fit-check                 # build, sweep, report
 *   bun run fit-check --no-build      # reuse an existing harness build
 *   bun run fit-check --limit 40      # smoke it
 *   bun run fit-check --json out.json # every measurement, for deriving a budget
 */

import { spawn, spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const REPO_DIR = resolve(WEB_DIR, '../..')
const DIST_DIR = join(WEB_DIR, 'dist')

const EXIT_OK = 0
const EXIT_OVERFLOW = 1
const EXIT_CANNOT_RUN = 2

/**
 * §10's reference viewport is the first row and the one the spec names. Portrait
 * is the second because §10 legislates a portrait layout too ("art on top at
 * 4:3, text below") and it is the *tighter* box — the same segment has less
 * width and therefore more lines.
 */
const VIEWPORTS = [
  { name: '1024x768 landscape', w: 1024, h: 768 },
  { name: '768x1024 portrait', w: 768, h: 1024 },
]

/** §11's dyslexia affordance: "the Aa toggle cycles 34 → 40px". Both count. */
const SIZES = [
  { name: '34px', size: 'default' },
  { name: '40px', size: 'large' },
]

const args = process.argv.slice(2)
const NO_BUILD = args.includes('--no-build')
const LIMIT = (() => {
  const i = args.indexOf('--limit')
  return i >= 0 ? Number(args[i + 1]) : 0
})()
const JSON_OUT = (() => {
  const i = args.indexOf('--json')
  return i >= 0 ? args[i + 1] : null
})()
const PREVIEW_PORT = Number(process.env.FIT_PREVIEW_PORT ?? 4193)

function die(code, message, hint) {
  console.error(`\nfit-check: ${message}`)
  if (hint) console.error(`           ${hint}`)
  process.exit(code)
}

// --- 1. the pool ------------------------------------------------------------

/**
 * The servable predicate, spelled once, from `api/stories.py`:
 * `SERVABLE_STATUSES = ("warm", "complete")` AND `ANSWERABLE_SQL`.
 *
 * `last_idx` is per story rather than a constant 6, because §10 caps segments at
 * 7 but does not promise 7 — and "the last page" is the row this gate exists to
 * find.
 */
const POOL_SQL = `
SELECT json_agg(row_to_json(r) ORDER BY r.stage_id, r.story_id, r.idx) FROM (
  SELECT g.id AS segment_id, s.id AS story_id, s.stage_id, t.slug AS theme_slug,
         g.idx, g.body, s.story_words,
         max(g.idx) OVER (PARTITION BY g.story_id) AS last_idx
  FROM story_segments g
  JOIN stories s ON s.id = g.story_id
  JOIN themes t ON t.id = s.theme_id
  WHERE s.status IN ('warm', 'complete')
    AND EXISTS (SELECT 1 FROM questions q WHERE q.story_id = s.id)
) r;
`

function loadPool() {
  const psqlArgs = ['-U', 'storyforge', '-d', 'storyforge', '-At', '-c', POOL_SQL]
  const attempts = process.env.DATABASE_URL
    ? [['psql', [process.env.DATABASE_URL, '-At', '-c', POOL_SQL]]]
    : []
  attempts.push(['docker', ['exec', 'storyforge-db', 'psql', ...psqlArgs]])

  for (const [cmd, argv] of attempts) {
    const out = spawnSync(cmd, argv, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
    if (out.status === 0 && out.stdout.trim()) {
      const rows = JSON.parse(out.stdout.trim())
      if (Array.isArray(rows) && rows.length) return rows
    }
  }
  die(
    EXIT_CANNOT_RUN,
    'could not read the segment pool from Postgres.',
    'Start it with `make db` (or set DATABASE_URL). A gate that cannot see the pool is not green.',
  )
}

// --- 2. the production bundle ----------------------------------------------

function build() {
  console.log('fit-check: building the production bundle with the fit harness…')
  const out = spawnSync('bun', ['run', 'build'], {
    cwd: WEB_DIR,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    env: { ...process.env, SF_FIT_HARNESS: '1' },
  })
  if (out.status !== 0) {
    console.error(out.stdout ?? '')
    console.error(out.stderr ?? '')
    die(EXIT_CANNOT_RUN, '`bun run build` failed.')
  }
}

function browseBin() {
  // An explicit override is a PIN, not a preference. Falling through to another
  // browser when `BROWSE_BIN` points at nothing would run the sweep against a
  // binary the caller did not choose and report it as theirs.
  if (process.env.BROWSE_BIN) {
    if (existsSync(process.env.BROWSE_BIN)) return process.env.BROWSE_BIN
    die(EXIT_CANNOT_RUN, `BROWSE_BIN is set to ${process.env.BROWSE_BIN}, which does not exist.`)
  }
  const candidates = [
    join(REPO_DIR, '.claude/skills/gstack/browse/dist/browse'),
    join(process.env.HOME ?? '', '.claude/skills/gstack/browse/dist/browse'),
  ]
  for (const path of candidates) if (existsSync(path)) return path
  die(
    EXIT_CANNOT_RUN,
    'no headless browser found.',
    'Set BROWSE_BIN= to a gstack `browse` binary. §10 is a fact about a rendered box; it cannot be checked without rendering it.',
  )
}

// --- 3. the sweep -----------------------------------------------------------

function main() {
  const pool = loadPool()
  const rows = LIMIT > 0 ? pool.slice(0, LIMIT) : pool
  console.log(`fit-check: ${rows.length} servable segments, ${VIEWPORTS.length} viewports x ${SIZES.length} reading sizes`)

  const bin = browseBin()
  if (!NO_BUILD) build()
  if (!existsSync(join(DIST_DIR, 'fit-check.html'))) {
    die(
      EXIT_CANNOT_RUN,
      'dist/fit-check.html is missing.',
      'Drop --no-build, or run `SF_FIT_HARNESS=1 bun run build` first.',
    )
  }

  const payload = join(DIST_DIR, 'fit-segments.json')
  writeFileSync(payload, JSON.stringify(rows))

  const preview = spawn('bun', ['run', 'preview', '--port', String(PREVIEW_PORT), '--strictPort'], {
    cwd: WEB_DIR,
    stdio: 'ignore',
    env: { ...process.env, VITE_PREVIEW_PORT: String(PREVIEW_PORT) },
  })

  // `/tmp` literally, not `os.tmpdir()`: the browser sandbox only writes under
  // /tmp or the repo, and on macOS `tmpdir()` is a per-user /var/folders path
  // that is neither.
  const scratch = mkdtempSync(existsSync('/tmp') ? '/tmp/sf-fit-' : join(REPO_DIR, '.sf-fit-'))
  const cleanup = () => {
    try {
      preview.kill('SIGTERM')
    } catch {
      /* already gone */
    }
    rmSync(payload, { force: true })
    rmSync(scratch, { recursive: true, force: true })
  }
  process.on('exit', cleanup)

  const browse = (...argv) =>
    spawnSync(bin, argv, {
      encoding: 'utf8',
      maxBuffer: 128 * 1024 * 1024,
      env: { ...process.env, BROWSE_SERVER_PORT: process.env.BROWSE_SERVER_PORT ?? '24837' },
    })

  // `vite preview` needs a moment; poll rather than sleep a magic number.
  const url = `http://localhost:${PREVIEW_PORT}/fit-check.html`
  let up = false
  for (let i = 0; i < 60 && !up; i += 1) {
    const probe = spawnSync('curl', ['-s', '-o', '/dev/null', '-w', '%{http_code}', url], {
      encoding: 'utf8',
    })
    up = probe.stdout === '200'
    if (!up) spawnSync('sleep', ['0.5'])
  }
  if (!up) die(EXIT_CANNOT_RUN, `vite preview did not answer on :${PREVIEW_PORT}.`)

  const results = []
  for (const vp of VIEWPORTS) {
    browse('viewport', `${vp.w}x${vp.h}`)
    browse('goto', url)
    for (const s of SIZES) {
      const outFile = join(scratch, `${vp.w}x${vp.h}-${s.size}.json`)
      const run = browse(
        'js',
        `window.__fitSweep(${JSON.stringify(s.size)}).then(r => JSON.stringify(r))`,
        '--out',
        outFile,
        '--raw',
      )
      if (!existsSync(outFile)) {
        die(EXIT_CANNOT_RUN, `the harness returned nothing for ${vp.name} @ ${s.name}.`, run.stderr?.trim())
      }
      const raw = readFileSync(outFile, 'utf8').trim()
      let rowsOut
      try {
        rowsOut = JSON.parse(raw.startsWith('"') ? JSON.parse(raw) : raw)
      } catch {
        die(EXIT_CANNOT_RUN, `unparseable harness output for ${vp.name} @ ${s.name}: ${raw.slice(0, 200)}`)
      }
      results.push({ vp, size: s, rows: rowsOut })
      const bad = rowsOut.filter((r) => r.over > 0)
      const words = bad.reduce((n, r) => n + r.hidden, 0)
      console.log(
        `  ${vp.name.padEnd(20)} ${s.name}  box ${String(rowsOut[0]?.box ?? '?').padStart(4)}px  ` +
          `${String(bad.length).padStart(3)} of ${rowsOut.length} clipped, ` +
          `${String(words).padStart(5)} words below the fold`,
      )
    }
  }

  report(results)
}

// --- 4. the report ----------------------------------------------------------

function report(results) {
  if (JSON_OUT) {
    writeFileSync(
      JSON_OUT,
      JSON.stringify(
        results.map(({ vp, size, rows }) => ({ viewport: vp.name, size: size.name, rows })),
      ),
    )
    console.log(`\nfit-check: every measurement written to ${JSON_OUT}`)
  }

  const overflowing = []
  for (const { vp, size, rows } of results) {
    for (const r of rows) if (r.over > 0) overflowing.push({ vp, size, ...r })
  }

  const stages = [...new Set(results[0].rows.map((r) => r.stage_id))].sort((a, b) => a - b)

  console.log('\n--- per stage: the character budget the §10 box actually has ---')
  console.log('stage  segs   longest that FITS / shortest that does NOT, per viewport x size')
  for (const stage of stages) {
    const cells = []
    for (const { vp, size, rows } of results) {
      const mine = rows.filter((r) => r.stage_id === stage)
      const fits = mine.filter((r) => r.over <= 0).map((r) => r.chars)
      const over = mine.filter((r) => r.over > 0).map((r) => r.chars)
      const worstFit = fits.length ? Math.max(...fits) : 0
      const firstOver = over.length ? Math.min(...over) : null
      cells.push(
        `${vp.w === 1024 ? 'L' : 'P'}${size.name} ${String(worstFit).padStart(4)}/${
          firstOver === null ? '   -' : String(firstOver).padStart(4)
        }`,
      )
    }
    const segs = results[0].rows.filter((r) => r.stage_id === stage).length
    console.log(`  ${String(stage).padStart(2)}  ${String(segs).padStart(4)}   ${cells.join('  ')}`)
  }

  if (!overflowing.length) {
    console.log('\nfit-check: GREEN — every servable segment fits the §10 box at both reading sizes.')
    process.exit(EXIT_OK)
  }

  console.log('\n--- the worst ten ---')
  overflowing
    .sort((a, b) => b.over - a.over)
    .slice(0, 10)
    .forEach((r) => {
      console.log(
        `  story ${String(r.story_id).padStart(5)} seg idx ${r.idx}  stage ${String(r.stage_id).padStart(2)}  ` +
          `${String(r.chars).padStart(4)} chars  ${String(r.over).padStart(5)}px below the clip edge  ` +
          `${String(r.hidden).padStart(3)} words a child never sees  [${r.vp.name} @ ${r.size.name}]`,
      )
    })

  const stories = new Set(overflowing.map((r) => r.story_id)).size
  const segments = new Set(overflowing.map((r) => r.segment_id)).size
  console.log(
    `\nfit-check: RED — ${segments} distinct segments across ${stories} stories do not fit the §10 box.`,
  )
  console.log(
    '§10: "if text overflows, generation length is wrong, not the layout." The remedy is a\n' +
      'per-stage length budget on generation, not a smaller font (§03 floors it at 28px) and not\n' +
      'a shorter tap target (§11 floors it at 48px).',
  )
  process.exit(EXIT_OVERFLOW)
}

main()
