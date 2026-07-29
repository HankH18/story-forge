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
  /** tap_word carries `target_grapheme` and `cue_word`; mcq2 only `options`. */
  payload: Record<string, unknown>
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
}

export interface Kid {
  id: number
  display_name: string
  stage_id: number
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

export interface Promotion {
  promote: boolean
  from_stage: number
  to_stage: number | null
  stories_completed: number
  stories_required: number
}

export interface Progress {
  kid: Kid
  stage_name: string
  readings_completed: number
  promotion?: Promotion
  gpc_accuracy: GpcAccuracy[]
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    const res = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
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
 * `GET /api/themes` is in §9 but not yet routed. The picker must still show
 * something, and inventing themes would let a child pick one the warm pool has
 * never heard of — so the fallback is exactly `data/seed/themes.csv`, the same
 * rows the server would return. Ids match the seed order.
 */
const SEEDED_THEMES: Theme[] = [
  { id: 1, slug: 'dragons', display_name: 'Dragons', emoji: '🐲' },
  { id: 2, slug: 'space', display_name: 'Space', emoji: '🚀' },
  { id: 3, slug: 'animals', display_name: 'Animals', emoji: '🦊' },
]

export async function getThemes(): Promise<Theme[]> {
  const live = await optional<Theme[]>('/api/themes')
  if (Array.isArray(live) && live.length) return live.slice(0, 6) // §07: 3–6 cards
  return SEEDED_THEMES
}

// --- stories -----------------------------------------------------------------

export async function requestStory(stageId: number, theme: string | number): Promise<Story> {
  return request<Story>('/api/stories/request', {
    method: 'POST',
    body: JSON.stringify({ stage_id: stageId, theme_id_or_slug: theme }),
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
 * Which reader this session belongs to.
 *
 * §9 has no `GET /api/kids/{id}` — the list is parent-scoped and will be tiny,
 * so the id in the URL is resolved against it. A url naming a kid who does not
 * exist falls back to the first reader rather than 404ing: kid mode has no
 * error state, and "wrong id in the address bar" is an adult's mistake that a
 * child would pay for. Returns null only when the parent has no kids at all,
 * which the caller runs as an anonymous session — kid mode never *creates* a
 * kid, because that is a parent action with a name attached to it (§13).
 */
export async function resolveKid(kidId: number): Promise<Kid | null> {
  const kids = await listKids()
  return kids.find((k) => k.id === kidId) ?? kids[0] ?? null
}

export async function getProgress(kidId: number): Promise<Progress | null> {
  return optional<Progress>(`/api/kids/${kidId}/progress`)
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
