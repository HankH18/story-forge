/**
 * `make kid-check` — the NINTH gate, and the first that measures what the
 * BROWSER does rather than what the app renders (DEVIATIONS 27.7).
 *
 * §13, verbatim: *"Kids: rows under parent, display-name only. No email, DOB,
 * mic, camera, free-text input, or third-party trackers in kid mode."* Nothing
 * measured whether a child could LEAVE kid mode, and one platform back gesture
 * did it: `/read/:kidId` is a single component with the phase in React state, so
 * a session pushed no history of its own and `back` landed a five-year-old on
 * `/app` — the parent's email, every reader's name and stage, and `Add a reader`
 * one tap from `<input id="kid-name">`.
 *
 * The previous audit named the reason it did not fix it: *"it cannot be covered
 * by a pure-helper test"*. That was true of `bun test src`, which is
 * `lib/`-only by decision (DEVIATIONS 7.30), and false of the problem. History
 * is a browser fact. This drives one.
 *
 * Eight assertions, four containing the child and four freeing the adult,
 * because a back button that never leaves is a trap and a trap is worse than the
 * leak:
 *
 *   A1  back at the theme picker stays inside kid mode
 *   A2  back mid-story returns to the picker, inside kid mode
 *   A3  no email and no text input is reachable by backing out
 *   A4  a reload mid-story resumes the same story on the same page
 *   A5  a reload does not deepen the guard (history stays the same length)
 *   A6  a TAP on the grown-up control does not leave — only a HOLD does
 *   A7  the grown-up control's hold reaches `/app`
 *   A8  the address bar and a new tab always reach `/app`
 *
 * **It exits 2 when it cannot run**, following `fit-check`: no browser, no
 * enforced API, no Clerk secret, no reader are never a pass. And it refuses to
 * run against `DEMO_BYPASS_AUTH` at all — the Phase 6 audit's finding 0 was a
 * browser session driven against a bypassed API by someone who did not check,
 * and this file's whole subject is a screen that is only wrong when a real
 * session is on it.
 *
 * **It opens ONE `readings` row per run** for the reader it drives, and leaves
 * it uncompleted — that is what a child who walks away produces, and there is no
 * way to drive a kid session without producing one.
 *
 *   make kid-check                    # build, drive, report
 *   bun run kid-check --no-build      # reuse dist/
 *   KID_CHECK_API=http://localhost:8097 make kid-check
 */

import { spawn, spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const REPO_DIR = resolve(WEB_DIR, '../..')
const DIST_DIR = join(WEB_DIR, 'dist')
const API_ENV = join(REPO_DIR, 'services/api/.env')

const EXIT_OK = 0
const EXIT_LEAK = 1
const EXIT_CANNOT_RUN = 2

const args = process.argv.slice(2)
const NO_BUILD = args.includes('--no-build')
const PREVIEW_PORT = Number(process.env.KID_PREVIEW_PORT ?? 4194)
const API_BASE = process.env.KID_CHECK_API ?? 'http://localhost:8000'
/** §10's reference viewport — the tablet this whole surface is designed for. */
const VIEWPORT = '1024x768'
/** `ui/GrownUpExit.HOLD_MS`, plus slack for a headless frame. Read, not guessed. */
const HOLD_MS = readHoldMs()

function die(code, message, hint) {
  console.error(`\nkid-check: ${message}`)
  if (hint) console.error(`           ${hint}`)
  process.exit(code)
}

/**
 * The hold duration, read out of the component.
 *
 * A check that holds for its own idea of long enough proves nothing about the
 * control: raise `HOLD_MS` to ten seconds and a hard-coded 1.9s here would go
 * red for the wrong reason, or — worse — lower it to 50ms and the check would
 * still pass while a child's tap opened the door.
 */
function readHoldMs() {
  const src = readFileSync(join(WEB_DIR, 'src/ui/GrownUpExit.tsx'), 'utf8')
  const m = src.match(/export const HOLD_MS = (\d+)/)
  if (!m) die(EXIT_CANNOT_RUN, 'could not read HOLD_MS out of src/ui/GrownUpExit.tsx.')
  return Number(m[1])
}

// --- preconditions ----------------------------------------------------------

function envValue(name) {
  if (process.env[name]) return process.env[name]
  if (!existsSync(API_ENV)) return null
  for (const line of readFileSync(API_ENV, 'utf8').split('\n')) {
    const m = line.match(new RegExp(`^\\s*${name}\\s*=\\s*(.*)$`))
    if (m) return m[1].trim().replace(/^["']|["']$/g, '')
  }
  return null
}

function curlJson(url, headers = []) {
  const argv = ['-s', '-m', '10']
  for (const h of headers) argv.push('-H', h)
  argv.push(url)
  const out = spawnSync('curl', argv, { encoding: 'utf8' })
  try {
    return JSON.parse(out.stdout)
  } catch {
    return null
  }
}

function curlStatus(url, headers = []) {
  const argv = ['-s', '-o', '/dev/null', '-w', '%{http_code}', '-m', '10']
  for (const h of headers) argv.push('-H', h)
  argv.push(url)
  return spawnSync('curl', argv, { encoding: 'utf8' }).stdout.trim()
}

/**
 * The API must be REAL. `demo_bypass_auth` reported false is not enough on its
 * own — a flag is a claim — so a garbage token is sent and must be refused.
 * `docs/RESUME.md`: "Never test auth against a bypassed server."
 */
function requireEnforcedApi() {
  const health = curlJson(`${API_BASE}/api/health`)
  if (!health) {
    die(
      EXIT_CANNOT_RUN,
      `no API answered ${API_BASE}/api/health.`,
      'Start one (`make api`) or point KID_CHECK_API at it. A gate with no API is not green.',
    )
  }
  if (health.demo_bypass_auth !== false) {
    die(
      EXIT_CANNOT_RUN,
      `${API_BASE} is running DEMO_BYPASS_AUTH.`,
      'This gate is about a screen that is only wrong when a real session is on it.',
    )
  }
  const refused = curlStatus(`${API_BASE}/api/kids`, ['Authorization: Bearer garbage.token.here'])
  if (refused !== '401') {
    die(
      EXIT_CANNOT_RUN,
      `a garbage token got ${refused} from ${API_BASE}/api/kids, not 401.`,
      'The flag said enforced and the behaviour disagreed. Believe the behaviour.',
    )
  }
  console.log(`kid-check: ${API_BASE} enforced (health false, garbage token 401)`)
}

const READER_SQL = `
SELECT json_build_object('kid_id', k.id, 'stage_id', k.stage_id, 'clerk_id', u.clerk_id)
FROM kids k JOIN users u ON u.id = k.parent_id
WHERE u.clerk_id LIKE 'user\\_%'
ORDER BY k.id LIMIT 1;
`

/** A real reader owned by a real Clerk account. Never invented — §13, and 7.33. */
function findReader() {
  const psqlArgs = ['-U', 'storyforge', '-d', 'storyforge', '-At', '-c', READER_SQL]
  const attempts = process.env.DATABASE_URL
    ? [['psql', [process.env.DATABASE_URL, '-At', '-c', READER_SQL]]]
    : []
  attempts.push(['docker', ['exec', 'storyforge-db', 'psql', ...psqlArgs]])
  for (const [cmd, argv] of attempts) {
    const out = spawnSync(cmd, argv, { encoding: 'utf8' })
    if (out.status === 0 && out.stdout.trim()) {
      try {
        return JSON.parse(out.stdout.trim())
      } catch {
        /* try the next connection */
      }
    }
  }
  die(
    EXIT_CANNOT_RUN,
    'no reader belonging to a Clerk account was found in Postgres.',
    'Start it with `make db`, and add one reader through the parent home first.',
  )
}

/** A one-shot Clerk sign-in ticket, so the browser holds a REAL session. */
async function mintTicket(clerkId) {
  const secret = envValue('CLERK_SECRET_KEY')
  if (!secret) {
    die(
      EXIT_CANNOT_RUN,
      'CLERK_SECRET_KEY is not set and services/api/.env does not carry one.',
      'The gate signs in for real; there is no bypass to fall back on.',
    )
  }
  const res = await fetch('https://api.clerk.com/v1/sign_in_tokens', {
    method: 'POST',
    headers: { Authorization: `Bearer ${secret}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: clerkId, expires_in_seconds: 900 }),
  })
  const body = await res.json().catch(() => null)
  if (!res.ok || !body?.token) {
    die(
      EXIT_CANNOT_RUN,
      `Clerk refused a sign-in token for ${clerkId} (${res.status}).`,
      typeof body?.errors?.[0]?.message === 'string' ? body.errors[0].message : undefined,
    )
  }
  return body.token
}

function browseBin() {
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
    'Set BROWSE_BIN=. History is a browser fact; it cannot be checked without a browser.',
  )
}

function build() {
  console.log('kid-check: building the production bundle…')
  const out = spawnSync('bun', ['run', 'build'], {
    cwd: WEB_DIR,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  if (out.status !== 0) {
    console.error(out.stdout ?? '')
    console.error(out.stderr ?? '')
    die(EXIT_CANNOT_RUN, '`bun run build` failed.')
  }
}

// --- the drive --------------------------------------------------------------

async function main() {
  requireEnforcedApi()
  const reader = findReader()
  console.log(`kid-check: reader ${reader.kid_id} (stage ${reader.stage_id}) under ${reader.clerk_id}`)
  const bin = browseBin()
  const ticket = await mintTicket(reader.clerk_id)
  if (!NO_BUILD) build()
  if (!existsSync(join(DIST_DIR, 'index.html'))) {
    die(EXIT_CANNOT_RUN, 'dist/index.html is missing.', 'Drop --no-build.')
  }

  const preview = spawn(
    'bun',
    ['run', 'preview', '--port', String(PREVIEW_PORT), '--strictPort'],
    {
      cwd: WEB_DIR,
      stdio: 'ignore',
      env: {
        ...process.env,
        VITE_PREVIEW_PORT: String(PREVIEW_PORT),
        VITE_API_PROXY_TARGET: API_BASE,
      },
    },
  )
  const scratch = mkdtempSync(existsSync('/tmp') ? '/tmp/sf-kid-' : join(REPO_DIR, '.sf-kid-'))

  let evalSeq = 0
  const browse = (...argv) =>
    spawnSync(bin, argv, {
      encoding: 'utf8',
      maxBuffer: 32 * 1024 * 1024,
      env: { ...process.env, BROWSE_SERVER_PORT: process.env.BROWSE_SERVER_PORT ?? '24838' },
    })

  // Registered AFTER `browse` exists: an exit handler that reaches a `const` in
  // its temporal dead zone turns any early `die()` into a ReferenceError, which
  // is a gate that cannot report why it could not run.
  let tabOpened = false
  const cleanup = () => {
    try {
      if (tabOpened) browse('closetab')
      preview.kill('SIGTERM')
    } catch {
      /* already gone */
    }
    rmSync(scratch, { recursive: true, force: true })
  }
  process.on('exit', cleanup)

  /** Evaluate in the page and get the value back, via a file — stdout is chatty. */
  const js = (expr) => {
    evalSeq += 1
    const out = join(scratch, `eval-${evalSeq}.json`)
    browse('js', expr, '--out', out, '--raw')
    if (!existsSync(out)) return null
    const raw = readFileSync(out, 'utf8').trim()
    try {
      return JSON.parse(raw.startsWith('"') ? JSON.parse(raw) : raw)
    } catch {
      return raw
    }
  }

  const sleep = (ms) => spawnSync('sleep', [String(ms / 1000)])

  const base = `http://localhost:${PREVIEW_PORT}`
  let up = false
  for (let i = 0; i < 60 && !up; i += 1) {
    up = curlStatus(base) === '200'
    if (!up) sleep(500)
  }
  if (!up) die(EXIT_CANNOT_RUN, `vite preview did not answer on :${PREVIEW_PORT}.`)

  /**
   * What is on the screen, in §13's own terms.
   *
   * The child-facing assertions are denominated in what §13 forbids — an email
   * address, a text input — rather than in a page title, because "am I still on
   * /read/:id" is the question this whole defect answered YES to while the
   * parent's email was on screen. The route is checked too; neither alone is the
   * claim.
   */
  const surface = () =>
    js(`(() => {
      const t = document.body.innerText || '';
      return JSON.stringify({
        path: location.pathname,
        historyLength: history.length,
        emails: (t.match(/[\\w.+-]+@[\\w-]+\\.[a-zA-Z]{2,}/g) || []),
        inputs: document.querySelectorAll('input, textarea, [contenteditable]').length,
        themeCards: document.querySelectorAll('button[aria-label^="read a story about"]').length,
        readingBack: document.querySelectorAll('[aria-label="go back to choosing a story"]').length,
        exit: document.querySelectorAll('[data-testid="grown-up-exit"]').length,
        page: (document.querySelector('main') || document.body).innerText.replace(/\\s+/g, ' ').trim().slice(0, 120)
      })
    })()`)

  const waitFor = (predicate, ms = 20000) => {
    const deadline = Date.now() + ms
    let last = surface()
    while (Date.now() < deadline) {
      if (last && predicate(last)) return last
      sleep(250)
      last = surface()
    }
    return last
  }

  const results = []
  const check = (id, claim, ok, detail) => {
    results.push({ id, claim, ok: Boolean(ok), detail })
    console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${id}  ${claim}${ok ? '' : `\n         ${detail}`}`)
  }
  /** A child-facing screen: the kid route, no email, no text input. */
  const contained = (s, kidId) =>
    s.path === `/read/${kidId}` && s.emails.length === 0 && s.inputs === 0

  /**
   * Kid mode's own storage for this reader, cleared.
   *
   * Not hygiene — correctness. A resume is a feature now, so a run that starts
   * within thirty minutes of the last one opens straight into the story the last
   * run abandoned, and A1's "the theme picker" never renders. Cleared before the
   * first assertion, before every re-entry, and after the last, so the gate
   * neither depends on nor leaves a session behind.
   */
  const clearKidStorage = () =>
    js(
      `(() => { localStorage.removeItem('sf.resume.v1.${reader.kid_id}');` +
        ` localStorage.removeItem('sf.lastStory.v1.${reader.kid_id}'); return 'cleared' })()`,
    )

  /**
   * Put the browser back at the theme picker, whatever the last assertion did to
   * it.
   *
   * A gate must be able to report ALL of its findings. Without this, the first
   * sabotage of the containment (the guard never installed) leaves the browser
   * on `/app`, and the run dies with "no story was served" — a `cannot run` that
   * hides the two assertions after it, which is exactly the shape of a check
   * that stops asking once it has found something.
   */
  const ensureKidMode = () => {
    const now = surface()
    if (now.path === `/read/${reader.kid_id}` && now.themeCards > 0) return now
    // Without this the resume does its job and hands back the STORY, not the
    // picker — correct product behaviour, and not the state the next assertion
    // needs to start from.
    clearKidStorage()
    browse('goto', `${base}/read/${reader.kid_id}`)
    return waitFor((x) => x.themeCards > 0)
  }

  browse('viewport', VIEWPORT)

  // --- sign in, and confirm the premise --------------------------------------
  browse('goto', `${base}/sign-in?__clerk_ticket=${ticket}`)
  const home = waitFor((s) => s.path === '/app')
  if (home.path !== '/app') {
    die(
      EXIT_CANNOT_RUN,
      `the sign-in ticket did not land on /app (stopped at ${home.path}).`,
      'Is VITE_CLERK_PUBLISHABLE_KEY the same Clerk instance as CLERK_SECRET_KEY?',
    )
  }
  if (home.emails.length === 0) {
    die(
      EXIT_CANNOT_RUN,
      'the parent home shows no email address, so there is nothing here for §13 to keep out.',
      'The premise moved. Re-derive this gate against what /app now shows.',
    )
  }
  browse('goto', `${base}/app/add-kid`)
  const addKid = waitFor((s) => s.path === '/app/add-kid' && s.inputs > 0)
  if (addKid.inputs === 0) {
    die(EXIT_CANNOT_RUN, 'the add-kid form has no text input; the premise moved.')
  }
  console.log(
    `kid-check: premise confirmed — /app shows ${home.emails.length} email address(es), ` +
      `/app/add-kid has ${addKid.inputs} text input(s)`,
  )
  console.log(`kid-check: driving ${VIEWPORT}, hold ${HOLD_MS}ms\n`)

  // --- A1: back at the picker ------------------------------------------------
  clearKidStorage()
  browse('goto', `${base}/read/${reader.kid_id}`)
  let s = waitFor((x) => x.themeCards > 0)
  if (s.themeCards === 0) {
    die(EXIT_CANNOT_RUN, `the theme picker never rendered for reader ${reader.kid_id}.`, s.page)
  }
  browse('back')
  sleep(1500)
  s = surface()
  check(
    'A1',
    'back at the theme picker stays inside kid mode',
    contained(s, reader.kid_id) && s.themeCards > 0,
    `landed on ${s.path} with ${s.emails.length} email(s), ${s.inputs} input(s): ${s.page}`,
  )

  // --- A2/A3: back mid-story -------------------------------------------------
  ensureKidMode()
  js(`Array.from(document.querySelectorAll('button[aria-label^="read a story about"]'))[0].click()`)
  s = waitFor((x) => x.readingBack > 0, 30000)
  if (s.readingBack === 0) {
    die(EXIT_CANNOT_RUN, 'no story was served, so there is nothing to back out of.', s.page)
  }
  js(`(Array.from(document.querySelectorAll('button')).find(b => /Next/.test(b.textContent)) || {click(){}}).click()`)
  sleep(800)
  const midStory = surface()
  browse('back')
  sleep(1500)
  s = surface()
  check(
    'A2',
    'back mid-story returns to the theme picker, inside kid mode',
    contained(s, reader.kid_id) && s.themeCards > 0 && s.readingBack === 0,
    `landed on ${s.path}: ${s.page}`,
  )
  check(
    'A3',
    'no email and no free-text input is reachable by backing out of a story (§13)',
    s.emails.length === 0 && s.inputs === 0,
    `saw ${s.emails.length} email(s) [${s.emails.join(', ')}] and ${s.inputs} input(s) on ${s.path}`,
  )

  // --- A4/A5: reload mid-story ----------------------------------------------
  // **In a NEW TAB, and that is not tidiness.** A5 counts history entries, and
  // Chrome caps a tab's history at 50: driven in the tab that has already run
  // A1-A3, the count is pinned at the ceiling and "the length did not change"
  // becomes true no matter what the code does. Sabotaging `needsGuardEntry` to
  // always-yes came back GREEN exactly once for that reason, which is this
  // project's signature defect (a check that passes because it asks a question
  // the environment has already answered). A fresh tab starts at 1.
  clearKidStorage()
  browse('newtab', `${base}/read/${reader.kid_id}`)
  tabOpened = true
  s = waitFor((x) => x.themeCards > 0)
  if (s.themeCards === 0) die(EXIT_CANNOT_RUN, 'the picker never rendered in the reload tab.', s.page)
  js(`Array.from(document.querySelectorAll('button[aria-label^="read a story about"]'))[0].click()`)
  s = waitFor((x) => x.readingBack > 0, 30000)
  js(`(Array.from(document.querySelectorAll('button')).find(b => /Next/.test(b.textContent)) || {click(){}}).click()`)
  sleep(800)
  const before = surface()
  const lengthBefore = before.historyLength
  browse('reload')
  s = waitFor((x) => x.readingBack > 0 || x.themeCards > 0, 30000)
  check(
    'A4',
    'a reload mid-story resumes the same story on the same page',
    s.readingBack > 0 && s.page === before.page,
    s.themeCards > 0
      ? `the reload landed on the theme picker; the reading was lost (was: "${before.page}")`
      : `resumed onto "${s.page}", expected "${before.page}"`,
  )
  browse('reload')
  waitFor((x) => x.readingBack > 0 || x.themeCards > 0, 30000)
  browse('reload')
  s = waitFor((x) => x.readingBack > 0 || x.themeCards > 0, 30000)
  check(
    'A5',
    `reloading does not deepen the guard (history ${lengthBefore} → ${s.historyLength} over 3 reloads)`,
    s.historyLength === lengthBefore && lengthBefore < 40,
    lengthBefore >= 40
      ? `history was already ${lengthBefore} deep — at Chrome's cap of 50 this assertion cannot fail`
      : `history.length went ${lengthBefore} → ${s.historyLength}`,
  )
  browse('closetab')
  tabOpened = false

  // --- A6/A7: the grown-up door ---------------------------------------------
  s = ensureKidMode()
  if (s.exit === 0) {
    die(EXIT_CANNOT_RUN, 'the grown-up exit is not on the picker; there is no door to test.', s.page)
  }
  js(`(() => { const b = document.querySelector('[data-testid="grown-up-exit"]');
      for (let i = 0; i < 3; i++) { b.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}));
                                    b.dispatchEvent(new PointerEvent('pointerup', {bubbles:true})); } })()`)
  sleep(HOLD_MS + 800)
  s = surface()
  check(
    'A6',
    'three taps on the grown-up control do not leave kid mode — only a hold does',
    contained(s, reader.kid_id),
    `three taps landed on ${s.path}`,
  )

  js(`document.querySelector('[data-testid="grown-up-exit"]').dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}))`)
  sleep(HOLD_MS + 800)
  s = surface()
  check(
    'A7',
    'holding the grown-up control leaves kid mode for the parent home',
    s.path === '/app',
    `after a ${HOLD_MS}ms hold the browser is on ${s.path}`,
  )

  // --- A8: the routes that need no affordance at all -------------------------
  browse('goto', `${base}/read/${reader.kid_id}`)
  waitFor((x) => x.themeCards > 0)
  browse('goto', `${base}/app`)
  // The parent home is only "reached" once it is the parent's own screen, which
  // is one round trip after the route matches — so the wait is for the SIGNED-IN
  // home, and a timeout leaves the surface unchanged and the assertion red.
  const viaBar = waitFor((x) => x.path === '/app' && x.emails.length > 0)
  browse('newtab', `${base}/app`)
  tabOpened = true
  const viaTab = waitFor((x) => x.path === '/app' && x.emails.length > 0)
  browse('closetab')
  tabOpened = false
  check(
    'A8',
    'the address bar and a new tab always reach the parent home',
    viaBar.path === '/app' &&
      viaBar.emails.length > 0 &&
      viaTab.path === '/app' &&
      viaTab.emails.length > 0,
    `address bar → ${viaBar.path} (${viaBar.emails.length} email(s)), ` +
      `new tab → ${viaTab.path} (${viaTab.emails.length} email(s))`,
  )

  clearKidStorage()

  const failed = results.filter((r) => !r.ok)
  console.log(`\nkid-check: ${results.length - failed.length} of ${results.length} passed`)
  if (failed.length) {
    console.error(
      `kid-check: ${failed.map((f) => f.id).join(', ')} FAILED — ` +
        'kid mode does not contain the child, or it traps the adult.',
    )
    process.exit(EXIT_LEAK)
  }
  process.exit(EXIT_OK)
}

main().catch((err) => die(EXIT_CANNOT_RUN, `unexpected: ${err?.stack ?? err}`))
