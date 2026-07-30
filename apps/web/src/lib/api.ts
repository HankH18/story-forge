/**
 * The API client — §9 shapes, §11.2 consumer.
 *
 * Phase 5 is being built against a moving backend: `GET /api/stories/{id}` and
 * `POST /api/stories/request` are live, while `kids`, `readings` and `answers`
 * are landing in parallel. So this file draws one hard line:
 *
 *   **Story endpoints may fail loudly. Everything else fails silently.**
 *
 * A missing story is the only thing that changes what a child sees, and even
 * that is handled by the caller as "the printer needs another moment", never as
 * an error (§07). Telemetry endpoints — starting a reading, recording a choice,
 * grading an answer, completing — return `null` when they are absent or broken,
 * and the session carries on. Losing a stats row is a worse outcome for the
 * parent dashboard than for the child in front of the screen, and the child is
 * who this mode is for.
 *
 * Dev goes through Vite's `/api` proxy (vite.config.ts), so URLs stay relative
 * and no origin is baked into the bundle.
 *
 * Every request carries the Clerk session token when there is one — see
 * `authHeader` below and `lib/auth.tsx` for how it gets here without a hook.
 */

// --- DTOs, mirroring services/api/storyforge/api/stories.py ------------------

export interface Asset {
  id: number
  url: string
  alt_text: string
  tags: string[]
}

export interface Segment {
  id: number
  idx: number
  body: string
  scene_tags: string[]
  /** §10 branching: set on the two segments hanging off a branch point. */
  parent_segment_id: number | null
  /** The tappable label for this branch arm; null on spine segments. */
  choice_label: string | null
  asset: Asset | null
}

export interface Question {
  id: number
  segment_id: number | null
  idx: number
  qtype: string
  prompt: string
  options: string[]
  /** tap_word carries `cue_word`; `target_grapheme` is redacted by the DTO. */
  payload: Record<string, unknown>
  /**
   * The parent story's distinct story words (§4), mirrored from `QuestionDTO`.
   * `mcq2` options and prompts are validated with the story-word exemption, so
   * one can legitimately contain "rocket" — and without this list the question
   * screen hands a stage-1 reader a word nobody has taught them.
   */
  story_words?: string[]
}

export interface Fallback {
  kind: 'other_theme' | 'lower_stage' | 'lower_stage_other_theme'
  requested_stage_id: number
  requested_theme_id: number
  requested_theme_slug: string
  detail: string
}

export interface Story {
  id: number
  stage_id: number
  theme_id: number
  theme_slug: string
  theme_display_name: string
  status: string
  title: string
  decodability: number
  story_words: string[]
  segments: Segment[]
  questions: Question[]
  fallback: Fallback | null
}

export interface Theme {
  id: number
  slug: string
  display_name: string
  emoji: string
  /**
   * Servable stories in this theme, across every stage — reported by
   * `GET /api/themes` and deliberately not read by any component.
   *
   * §7's ladder answers an empty (stage, theme) cell with a lower stage or
   * another theme, so a shallow theme is a preference miss, not a dead end, and
   * hiding the card would make it one: §07 fixes the picker at 3–6 cards and an
   * empty picker is a screen a child cannot leave (DEVIATIONS 8.68). Absent when
   * the list came from the seed fallback below, which knows nothing about the
   * pool.
   */
  servable_stories?: number
}

export interface Kid {
  id: number
  display_name: string
  stage_id: number
  created_at?: string
}

/** One row of `GET /api/kids/{id}/progress` → `gpc_accuracy`. */
export interface GpcAccuracy {
  gpc_id: string
  grapheme: string
  phonemes: string
  stage_id: number
  n_seen: number
  n_correct: number
  accuracy: number | null
  current_stage?: boolean
}

/** One clause of §8's promotion rule, with the server's own plain-English reason. */
export interface Criterion {
  name: string
  met: boolean
  detail: string
}

/**
 * §8's rule, its inputs and its verdict.
 *
 * Every field here is shown somewhere on the dashboard. That is deliberate: §8
 * is an *interpretable* model on purpose ("no BKT"), and a dashboard that
 * renders only `promote` throws away the entire reason it was built that way.
 */
export interface Promotion {
  promote: boolean
  from_stage: number
  to_stage: number | null
  stories_completed: number
  stories_required: number
  gpc_seen: number
  gpc_correct: number
  accuracy: number | null
  accuracy_threshold: number
  min_observations: number
  criteria: Criterion[]
}

export interface Choice {
  segment_id: number
  choice: string
  at: string
}

export interface RecentReading {
  id: number
  story_id: number
  story_title: string
  theme_slug: string
  story_stage_id: number
  stage_id: number
  started_at: string
  completed_at: string | null
  questions_answered: number
  questions_correct: number
  choices: Choice[]
}

/** §8's thresholds, shipped so the dashboard can show the rule, not just a bar. */
export interface Thresholds {
  stories_required: number
  accuracy_threshold: number
  min_observations: number
  max_stage: number
}

export interface Progress {
  kid: Kid
  stage_name: string
  stage_description: string
  readings_started: number
  readings_completed: number
  readings_completed_at_stage: number
  thresholds: Thresholds
  promotion: Promotion
  gpc_accuracy: GpcAccuracy[]
  recent_readings: RecentReading[]
}

export interface Stage {
  id: number
  name: string
  description: string
}

/** What this reading changed — the deltas the celebration is allowed to show. */
export interface GpcDelta {
  gpc_id: string
  n_seen: number
  n_correct: number
}

export interface CompleteResult {
  newly_completed: boolean
  gpc_updates: GpcDelta[]
  promotion?: Promotion
  promoted: boolean
}

// --- transport ---------------------------------------------------------------

const TIMEOUT_MS = 20_000

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

/**
 * §9: "Every `/api/*` route except `/api/health` requires a Clerk session
 * token", verified RS256 over JWKS by `services/api/storyforge/auth.py`.
 *
 * This module is plain async functions — called from effects, event handlers
 * and `lib/plan.ts` — so it cannot call `useAuth()`. `ClerkTokenBridge` in
 * `lib/auth.tsx` registers Clerk's `getToken` here instead. See the note there
 * for why the registration happens during render rather than in an effect: a
 * child's effects run before its ancestors', so the first request of every page
 * load would otherwise go out unauthenticated.
 */
type AuthTokenGetter = () => Promise<string | null>

let authTokenGetter: AuthTokenGetter | null = null

export function setAuthTokenGetter(getter: AuthTokenGetter | null): void {
  authTokenGetter = getter
}

/**
 * `Authorization: Bearer …`, or nothing at all.
 *
 * **A missing token is not an error here.** Three cases produce one and all
 * three are legitimate: the API may be running §18's `DEMO_BYPASS_AUTH` and not
 * want a token; Clerk may not have loaded yet; and the signed-out kid route
 * bounces to `/` rather than blocking (DEVIATIONS 6.18) — in the moment before
 * that redirect commits, a request may still be in flight. Refusing to send it
 * would turn a 401 the caller already knows how to render into a client-side
 * failure with no status at all, and would break the bypass outright. The
 * server decides what an anonymous request is worth; this file does not.
 *
 * Exported for `lib/adminApi.ts`, which has its own transport rather than reusing
 * `request` below. The reason is the error contract, not the happy path: an admin
 * needs the server's `detail` string ("admin role required (§9); set
 * public_metadata.role in Clerk", "stage 12 not seeded — run `make seed`"), and
 * `ApiError` deliberately does not carry a body — adding one here would change
 * what a parent reads on a proven screen. The token bridge is shared because
 * there is exactly one session and it must not be registered twice.
 */
export async function authHeader(): Promise<Record<string, string>> {
  if (!authTokenGetter) return {}
  try {
    const token = await authTokenGetter()
    return token ? { authorization: `Bearer ${token}` } : {}
  } catch (err) {
    if (import.meta.env.DEV) console.info('[api] no Clerk token available:', err)
    return {}
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    const res = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: {
        'content-type': 'application/json',
        ...(await authHeader()),
        ...(init?.headers ?? {}),
      },
    })
    if (!res.ok) throw new ApiError(`${init?.method ?? 'GET'} ${path} → ${res.status}`, res.status)
    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  } finally {
    window.clearTimeout(timer)
  }
}

/**
 * Best-effort call. Anything that goes wrong — 404 because the route does not
 * exist yet, a 500, a dropped connection — resolves to `null`.
 */
async function optional<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    return await request<T>(path, init)
  } catch (err) {
    if (import.meta.env.DEV) console.info(`[api] optional ${path} unavailable:`, err)
    return null
  }
}

// --- themes ------------------------------------------------------------------

/**
 * The fallback for `GET /api/themes`, which now exists (`api/catalog.py`,
 * DEVIATIONS 8.60) and used to 404 on every kid-session load.
 *
 * **It is kept, not deleted, and the reason is not nostalgia.** The route
 * existing does not make it reachable: the picker with an empty theme list is a
 * screen with nothing to tap, which is the stuck state §01 forbids, and the
 * moment a child is most likely to meet it is exactly when the API is
 * unreachable. Inventing themes would be worse — a slug the server cannot resolve
 * is a 404 on `POST /api/stories/request`, and a 404 is not retryable, so kid
 * mode burns its three attempts and lands on the printer screen.
 *
 * So the fallback is exactly `data/seed/themes.csv`, the same rows the server
 * returns, with ids in seed order. `lib/seeds.test.ts` parses that CSV and
 * asserts this constant equals it, field for field — the drift this mirrors was
 * silent before, and the test found a real one on its first run in
 * `SEEDED_STAGES` (DEVIATIONS 8.61). Exported for that test and for no other
 * caller.
 */
export const SEEDED_THEMES: Theme[] = [
  { id: 1, slug: 'dragons', display_name: 'Dragons', emoji: '🐲' },
  { id: 2, slug: 'space', display_name: 'Space', emoji: '🚀' },
  { id: 3, slug: 'animals', display_name: 'Animals', emoji: '🦊' },
]

export async function getThemes(): Promise<Theme[]> {
  const live = await optional<Theme[]>('/api/themes')
  // `.slice(0, 6)` is §07's card ceiling, not a filter: the route returns every
  // seeded theme, shallow ones included, because the ladder makes a shallow theme
  // a preference miss rather than a dead end (DEVIATIONS 8.68).
  if (Array.isArray(live) && live.length) return live.slice(0, 6) // §07: 3–6 cards
  return SEEDED_THEMES
}

// --- stories -----------------------------------------------------------------

/**
 * §9 `POST /api/stories/request {kid_id, theme_id}`.
 *
 * The reader is the request. This used to send `{stage_id}` — a number the
 * client picked — and the server used it as the ceiling on what a child was
 * allowed to read, which made the ceiling a client-side value. `/read/1648?stage=12`
 * served a stage-1 child a story measuring 13% decodable at their own stage.
 * The stage is now read from the kid row server-side, behind the same ownership
 * check every other kid route uses (DEVIATIONS 7.21).
 *
 * There is deliberately no way to override the stage from here. A caller that
 * wanted to would be asking to choose what a child can read, and that is the
 * one decision this product exists to take away from the caller.
 */
export async function requestStory(kidId: number, theme: string | number): Promise<Story> {
  return request<Story>('/api/stories/request', {
    method: 'POST',
    body: JSON.stringify({ kid_id: kidId, theme_id_or_slug: theme }),
  })
}

export async function getStory(id: number): Promise<Story> {
  return request<Story>(`/api/stories/${id}`)
}

// --- kid + reading telemetry (all optional) ----------------------------------

export async function listKids(): Promise<Kid[]> {
  return (await optional<Kid[]>('/api/kids')) ?? []
}

/**
 * Which reader this session belongs to — **that reader, or none.**
 *
 * §9 has no `GET /api/kids/{id}`: the list is parent-scoped and tiny, so the id
 * in the URL is resolved against it, and an id that is not in it belongs to
 * another family or to nobody. Either way the answer is `null`.
 *
 * This used to end `?? kids[0] ?? null` — substitute the caller's own first
 * reader — and it was documented and defended for a *typo*: "kid mode has no
 * error state, and a wrong id in the address bar is an adult's mistake that a
 * child would pay for." That reasoning is right about the child and wrong about
 * the family. `/read/162` signed in as an account that does not own kid 162
 * rendered the full kid shell greeting a DIFFERENT child by name, and filed the
 * reading — and its §8 mastery evidence — against that child's row (audit C9,
 * DEVIATIONS 7.33). A parent with two readers who hands over a tablet on a stale
 * URL is the ordinary case, not the adversarial one, and the product's answer was
 * to record the wrong child's reading rather than to ask.
 *
 * `null` is not an error surface. `KidSession` shows §07's printer and routes to
 * `/app`, the adult's own screen, which knows how to name the readers and how to
 * add one (DEVIATIONS 7.24). A child never sees a failure; an adult sees the
 * actual problem on the surface built to explain it. Kid mode never *creates* a
 * kid either — that is a parent action with a name attached to it (§13).
 */
export async function resolveKid(kidId: number): Promise<Kid | null> {
  if (!Number.isInteger(kidId) || kidId <= 0) return null
  const kids = await listKids()
  return kids.find((k) => k.id === kidId) ?? null
}

export async function getProgress(kidId: number): Promise<Progress | null> {
  return optional<Progress>(`/api/kids/${kidId}/progress`)
}

// --- parent surface -----------------------------------------------------------

/**
 * The parent surface is the third case in this file's rule.
 *
 * Kid mode may never show an error (§01) and telemetry may never interrupt a
 * session, so both of those swallow failures. A parent is a different reader
 * with a different job: they came to find out how their child is doing, and a
 * dashboard that renders "0 stories, 0 sounds" because the request 401'd has
 * told them something false about their child. So parent reads return a result
 * a caller must look at, and the routes render the failure as itself.
 *
 * This matters right now specifically because auth is landing in parallel: the
 * one status these screens must survive is a 401 that appears mid-phase.
 */
export type Result<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; message: string }

async function attempt<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  try {
    return { ok: true, data: await request<T>(path, init) }
  } catch (err) {
    if (err instanceof ApiError) {
      return { ok: false, status: err.status, message: err.message }
    }
    // AbortError (the 20s timeout) or a dropped connection: status 0 means
    // "never reached the server", which is a different sentence to a parent
    // than "the server said no".
    return { ok: false, status: 0, message: err instanceof Error ? err.message : String(err) }
  }
}

export async function fetchKids(): Promise<Result<Kid[]>> {
  return attempt<Kid[]>('/api/kids')
}

export async function fetchProgress(kidId: number): Promise<Result<Progress>> {
  return attempt<Progress>(`/api/kids/${kidId}/progress`)
}

/**
 * §9 `POST /api/kids {display_name, stage_id}` — and there is no third field.
 *
 * §13 in the type system: no email, no date of birth, no photo. The server
 * enforces the same shape (`KidCreate`), so a client that grew a field would be
 * a 422 rather than a quiet leak.
 */
export async function createKid(displayName: string, stageId: number): Promise<Result<Kid>> {
  return attempt<Kid>('/api/kids', {
    method: 'POST',
    body: JSON.stringify({ display_name: displayName, stage_id: stageId }),
  })
}

// --- stages -------------------------------------------------------------------

/**
 * The fallback for `GET /api/stages`, which now exists (`api/catalog.py`).
 *
 * The add-kid form has to name the twelve stages — "Stage 7" alone is not a
 * choice a parent can make — and `stageName()` below is called synchronously from
 * render, so this list is load-bearing even when the route answers. It is exactly
 * `data/seed/stages.csv`, in id order. Same pattern and same reasoning as
 * SEEDED_THEMES above: mirror the seed, never invent.
 *
 * Stage 1's description was `simple three-letter words` here and
 * `simple consonant-vowel-consonant words` in the CSV — the drift was real,
 * shipped, and invisible until `lib/seeds.test.ts` compared them (DEVIATIONS
 * 8.61). The CSV is the seed the database was built from, so it is the side that
 * is right by definition.
 */
export const SEEDED_STAGES: Stage[] = [
  {
    id: 1,
    name: 'CVC',
    description: 'Single letters s a t p i n m d c — simple consonant-vowel-consonant words',
  },
  { id: 2, name: 'Doubles & x', description: 'Remaining single letters, doubled consonants (ck ss ll ff), and x' },
  { id: 3, name: 'Digraphs', description: 'Two letters one sound: sh ch th ng qu, plus w x y z v j' },
  { id: 4, name: 'Endings', description: 'Consonant clusters and the -s -es -ed -ing endings' },
  { id: 5, name: 'Silent e', description: 'Split digraphs a_e i_e o_e u_e e_e, and silent final e' },
  { id: 6, name: 'Vowel teams', description: 'ai ay ee ea oa ow igh, plus er and y as a vowel' },
  { id: 7, name: 'R-controlled', description: 'ar or ir ur, and the wa pattern' },
  { id: 8, name: 'Diphthongs', description: 'oi oy ou ow au aw oo' },
  { id: 9, name: 'Soft & silent', description: 'Soft c and g, plus kn wr mb ph' },
  { id: 10, name: 'Two syllables', description: 'The -le ending; schwa relaxation becomes explicit curriculum' },
  { id: 11, name: 'Suffixes', description: 'tion ture sion' },
  { id: 12, name: 'Review', description: 'Cumulative review; syllable cap lifted' },
]

export async function getStages(): Promise<Stage[]> {
  const live = await optional<Stage[]>('/api/stages')
  if (Array.isArray(live) && live.length) return live
  return SEEDED_STAGES
}

export function stageName(stageId: number): string {
  return SEEDED_STAGES.find((s) => s.id === stageId)?.name ?? `Stage ${stageId}`
}

export async function startReading(kidId: number, storyId: number): Promise<number | null> {
  const res = await optional<{ id?: number; reading_id?: number }>('/api/readings', {
    method: 'POST',
    body: JSON.stringify({ kid_id: kidId, story_id: storyId }),
  })
  return res?.reading_id ?? res?.id ?? null
}

export async function postChoice(
  readingId: number,
  segmentId: number,
  choice: string,
): Promise<void> {
  if (!choice.trim()) return // the API requires a non-empty label; nothing to log
  await optional(`/api/readings/${readingId}/choice`, {
    method: 'POST',
    body: JSON.stringify({ segment_id: segmentId, choice }),
  })
}

/**
 * Grade an answer server-side (§9 — the answer key deliberately never ships to
 * the client). `null` means "the server did not tell us", and the caller falls
 * back to the local check in `grade.ts`.
 */
export async function postAnswer(
  readingId: number,
  questionId: number,
  response: string,
): Promise<boolean | null> {
  const res = await optional<Record<string, unknown>>(`/api/readings/${readingId}/answers`, {
    method: 'POST',
    body: JSON.stringify({ question_id: questionId, response }),
  })
  if (!res) return null
  // The exact field name is not pinned in §9; accept the three plausible ones
  // rather than guessing one and silently mis-grading every answer.
  for (const key of ['correct', 'is_correct', 'was_correct']) {
    if (typeof res[key] === 'boolean') return res[key] as boolean
  }
  return null
}

export async function completeReading(readingId: number): Promise<CompleteResult | null> {
  return optional<CompleteResult>(`/api/readings/${readingId}/complete`, { method: 'POST' })
}
