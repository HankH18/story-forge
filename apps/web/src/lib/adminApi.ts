/**
 * The admin portal's API client — §9's `(admin)` routes, and the one place their
 * shapes are pinned.
 *
 * Five screens read six routes, and this file is deliberately the only module
 * that knows their field names.
 *
 * **Every interface below was reconciled against a LIVE response**, not against
 * the backend's design notes: `services/api/storyforge/api/admin.py` landed while
 * this file was being written, so the first draft here was typed from DEVIATIONS
 * 9.1-9.19's prose and then corrected field by field against a server reporting
 * `demo_bypass_auth: false`. Four names moved in that pass and each is noted where
 * it sits — `stage` is an object not an id, `prompt_word_limit` not
 * `prompt_limit`, `revision` not `seq`, `transitions[].node` not
 * `states[].state`. DEVIATIONS 9.37 predicted exactly this and asked for one seam
 * per response instead of guesses scattered through components.
 *
 * The tolerant readers are KEPT after that reconciliation, and not as decoration:
 * `admin.py` returns `dict[str, Any]` from every handler with no response model,
 * so nothing on the server side turns a rename red. These normalisers plus their
 * fixtures in `adminApi.test.ts` are this app's only contract check, and under
 * them a rename shows up as a "—" in one column rather than as a blank screen.
 *
 * **The transport is separate from `lib/api.ts` on purpose.** That module's rule
 * is "story endpoints may fail loudly, everything else fails silently", and its
 * `ApiError` carries a status and no body — correct for a parent, who is shown a
 * plain sentence (DEVIATIONS 7.32). An admin needs the opposite: the server's own
 * `detail`, the method and the path, because §01's "a child never sees an error"
 * does not apply to an instrument panel (DEVIATIONS 9.36). The Clerk token getter
 * IS shared, via `authHeader` — there is one session and registering it twice
 * would be two sources of truth for the same thing.
 */

import { authHeader } from './api'
import type { TextReport } from './validatorView'

export type { TextReport, WordReport, Verdict } from './validatorView'

// --- result and transport -----------------------------------------------------

/**
 * Every call in this file returns one of these. There is no `optional()` twin:
 * an admin screen that silently renders nothing because a route 404'd is the
 * failure mode this portal exists to remove (four of §17's six steps had no
 * surface at all, and a blank one looks the same as a missing one).
 */
export type AdminResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; detail: string; where: string }

const TIMEOUT_MS = 30_000

/**
 * A single admin request.
 *
 * `status` is `0` for "never reached the server" — a dropped connection or the
 * abort below — which is a different sentence to an operator than "the server
 * said no", and the two are told apart nowhere else.
 *
 * `detail` is FastAPI's own `detail` when there is one, because on these routes
 * that string is frequently the whole answer: `require_admin` returns "admin role
 * required (§9); set public_metadata.role in Clerk", `cold-generate` returns "cold
 * generation cg_1a2b is already running (node GENERATING) — one at a time, which
 * is what bounds the spend", and `POST /eval-runs` returns the exact budget
 * window it will accept. A 422 arrives as a list of Pydantic errors instead, so
 * that shape is flattened rather than JSON-stringified into noise.
 */
async function call<T>(
  method: string,
  path: string,
  body?: unknown,
  timeoutMs: number = TIMEOUT_MS,
): Promise<AdminResult<T>> {
  const where = `${method} ${path}`
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const res = await fetch(path, {
      method,
      signal: controller.signal,
      headers: { 'content-type': 'application/json', ...(await authHeader()) },
      body: body === undefined ? undefined : JSON.stringify(body),
    })

    if (!res.ok) {
      return { ok: false, status: res.status, detail: await readDetail(res), where }
    }
    if (res.status === 204) return { ok: true, data: undefined as T }
    return { ok: true, data: (await res.json()) as T }
  } catch (err) {
    const detail =
      err instanceof DOMException && err.name === 'AbortError'
        ? `no response within ${Math.round(timeoutMs / 1000)}s`
        : err instanceof Error
          ? err.message
          : String(err)
    return { ok: false, status: 0, detail, where }
  } finally {
    window.clearTimeout(timer)
  }
}

/** FastAPI's `detail`, a 422's validation list, or the raw body. */
export async function readDetail(res: Response): Promise<string> {
  let raw = ''
  try {
    raw = await res.text()
  } catch {
    return res.statusText || `HTTP ${res.status}`
  }
  if (!raw) return res.statusText || `HTTP ${res.status}`

  try {
    const parsed = JSON.parse(raw) as unknown
    const detail = (parsed as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // Pydantic: `[{loc: [...], msg: "...", type: "..."}]`. Rendered as
      // `body.stage_id: input should be less than or equal to 11` — the field
      // name is the useful half and JSON.stringify buries it.
      return detail
        .map((e) => {
          const item = e as { loc?: unknown[]; msg?: string }
          const loc = Array.isArray(item.loc) ? item.loc.join('.') : ''
          return loc ? `${loc}: ${item.msg ?? ''}` : (item.msg ?? '')
        })
        .filter(Boolean)
        .join(' · ')
    }
  } catch {
    /* not JSON — fall through to the raw body */
  }
  return raw.slice(0, 400)
}

// --- 1. POST /api/validate (§5.6) — the one route that already existed --------

export interface ValidateRequest {
  text: string
  stage_id: number
  extra_sight_words?: string[]
  story_words?: string[]
}

export async function validateText(req: ValidateRequest): Promise<AdminResult<TextReport>> {
  return call<TextReport>('POST', '/api/validate', {
    text: req.text,
    stage_id: req.stage_id,
    extra_sight_words: req.extra_sight_words ?? [],
    story_words: req.story_words ?? [],
  })
}

/** §5.6's own cap, mirrored so the textarea can say so before a 422 does. */
export const MAX_VALIDATE_CHARS = 4000
/** `ValidateRequest.story_words` is `max_length=8`; sight words is 64. */
export const MAX_STORY_WORDS_INPUT = 8
export const MAX_SIGHT_WORDS_INPUT = 64

/**
 * "Is this session an admin?" asked of the only authority there is.
 *
 * `lib/auth.tsx` never reads a role and must not start (DEVIATIONS 9.21): the
 * signed token is the source of truth and `auth.py` re-reads it per request so
 * the Clerk dashboard can revoke admin immediately. So the shell asks the API,
 * and this is the question.
 *
 * `POST /api/validate` with three letters is the probe because it is the one
 * admin route that provably existed before this portal did, it is a pure function
 * over a vendored dictionary plus one indexed `SELECT`, and it spends nothing.
 * `admin.py` puts a single `dependencies=[Depends(require_admin)]` on the whole
 * router — read, not assumed — so a 200 here is a 200 there. DEVIATIONS 9.23
 * states that dependency and it is confined to this one call site.
 */
export async function probeAdmin(): Promise<AdminResult<TextReport>> {
  return call<TextReport>('POST', '/api/validate', { text: 'cat', stage_id: 1 }, 15_000)
}

// --- the 403 surface DEVIATIONS 7.37 removed, restored as a pure function -----

/**
 * The sentence DEVIATIONS 7.37 preserved, restored verbatim.
 *
 * 7.37 deleted `ParentError`'s 403 branch after PROVING it unreachable —
 * `require_admin` guarded one route and no web code called it — and quoted this
 * wording into the log "so Phase 8 can restore it rather than re-derive it". This
 * is Phase 8, and this portal is the code that makes 403 reachable.
 *
 * It is a constant, and it is not to be paraphrased. The wording was arrived at
 * empirically — by minting a real Clerk session token and decoding it (6.13, 6.20)
 * — and the finding behind it is still true and still surprising: Clerk's DEFAULT
 * session token carries NO metadata, so `role_from_claims` resolves every
 * signed-in user, an admin whose `public_metadata` IS `{"role":"admin"}` included,
 * to `parent`. The Clerk dashboard path in the third sentence is the fix. A
 * generic "Forbidden" would send whoever meets it into `auth.py` hunting a bug
 * that is not there.
 */
export const MISSING_ROLE_CLAIM_DETAIL =
  'The API accepted this session but not this screen. Clerk’s default session ' +
  'token carries no metadata, so the API reads every signed-in user as a parent — ' +
  'including admins. Someone with access to the Clerk dashboard has to add the ' +
  'claim (Sessions → customize session token → {"public_metadata": ' +
  '"{{user.public_metadata}}"}), and then sign in again.'

/** Which failure an admin is looking at. Five cases, five different actions. */
export type AdminErrorKind = 'role' | 'session' | 'route' | 'unreachable' | 'server'

export interface AdminErrorCopy {
  kind: AdminErrorKind
  headline: string
  body: string
  /** True only for the 403 branch — the one case with a named, actionable cause. */
  namesRoleClaim: boolean
}

/**
 * Status → what to say. A pure function, extracted from `ui/AdminShell` for one
 * reason: it is the security-relevant copy decision on this surface, and a
 * decision inside a component that has no test is a decision nobody checks.
 *
 * `adminApi.test.ts` tests it with a POSITIVE and a NEGATIVE control, because "403
 * names the role claim" passes just as happily in a build where EVERY status names
 * the role claim — which would tell an operator with a 500 to go and edit their
 * Clerk dashboard. That is the vacuous-assertion shape `docs/AUDIT-PHASE7-FIXES.md`
 * F2 found five of.
 */
export function adminErrorCopy(status: number): AdminErrorCopy {
  if (status === 403) {
    return {
      kind: 'role',
      headline: 'This session is not an admin',
      body: MISSING_ROLE_CLAIM_DETAIL,
      namesRoleClaim: true,
    }
  }
  if (status === 401) {
    return {
      kind: 'session',
      headline: 'The API did not accept this session',
      body:
        'The token this app sent was rejected. Clerk’s client may still consider the session ' +
        'valid — those are two systems with two opinions (DEVIATIONS 7.32) — so the way back is ' +
        'to sign out and in again rather than to reload.',
      namesRoleClaim: false,
    }
  }
  if (status === 404) {
    return {
      kind: 'route',
      headline: 'That route is not on this server',
      body:
        'The API answered, and it has no handler for this path. Either the admin router is not ' +
        'mounted on the server this build is pointed at, or the path moved. Check /openapi.json ' +
        'on that server.',
      namesRoleClaim: false,
    }
  }
  if (status === 0) {
    return {
      kind: 'unreachable',
      headline: 'Could not reach the API',
      body:
        'No response at all. Nothing has been changed by this request — every screen in this ' +
        'portal reads, except the two that say they spend money.',
      namesRoleClaim: false,
    }
  }
  return {
    kind: 'server',
    headline: `The API returned ${status}`,
    body: 'The server answered with an error rather than data. Its own words are below.',
    namesRoleClaim: false,
  }
}

// --- 2. GET /api/admin/allowed-words?stage=N (§5.4, §17 step 2) ---------------

export interface AllowedWord {
  word: string
  syllables: number
  freq_rank: number
  /**
   * Whether this word is among the 400 by frequency the generator is actually
   * shown (§6's `PROMPT_WORD_LIMIT`). DEVIATIONS 9.12: "every word a stage-3
   * reader may see" and "the 400 the generator is shown" are different claims,
   * and this browser is where they can be compared.
   */
  in_prompt?: boolean
}

/** The stage row the response carries, so the screen need not re-derive it. */
export interface AllowedWordsStage {
  id: number
  name: string
  description: string
  max_syllables?: number
}

export interface AllowedWordsPage {
  stage: AllowedWordsStage
  /** Rows matching the current filter. */
  total: number
  /** Rows at this stage with NO filter — the "every word" number §17 step 2 quotes. */
  stage_total: number
  limit: number
  offset: number
  words: AllowedWord[]
  /** The server's own cursor, preferred over arithmetic on `total`. */
  has_more: boolean
  next_offset: number | null
  /** §6's 400. Named `prompt_word_limit` on the wire — corrected from a live response. */
  prompt_word_limit?: number
  /** The `freq_rank` at that cutoff, which is what `in_prompt` is computed from. */
  prompt_freq_rank_cutoff?: number
  /** Cumulative sight words at this stage — allowlisted, so not in this table. */
  sight_word_count?: number
  order?: string
  q?: string
}

/** 9.12: `limit` defaults to 100 and is capped at 500 server-side. */
export const WORDS_PAGE_SIZE = 100
export const WORDS_PAGE_MAX = 500

export async function fetchAllowedWords(opts: {
  stage: number
  limit?: number
  offset?: number
  q?: string
}): Promise<AdminResult<AllowedWordsPage>> {
  const params = new URLSearchParams({
    stage: String(opts.stage),
    limit: String(Math.min(opts.limit ?? WORDS_PAGE_SIZE, WORDS_PAGE_MAX)),
    offset: String(opts.offset ?? 0),
  })
  if (opts.q?.trim()) params.set('q', opts.q.trim())

  const res = await call<unknown>('GET', `/api/admin/allowed-words?${params}`)
  if (!res.ok) return res
  return { ok: true, data: normalizeAllowedWords(res.data, opts) }
}

/**
 * The tolerant seam for the allowed-words page (DEVIATIONS 9.37).
 *
 * The live response was the first correction this file took: `stage` is an OBJECT
 * (`{id, name, description, max_syllables}`) where the first draft expected a
 * `stage_id` integer, and the prompt cutoff is `prompt_word_limit` rather than
 * `prompt_limit`. Both real names are read first and the drafted ones are kept as
 * fallbacks, because `admin.py` declares no response model and so nothing on the
 * server side would turn a rename red.
 *
 * A shape with no recognisable array yields an EMPTY page rather than throwing:
 * the screen then says "no rows", which is visibly wrong and therefore reportable,
 * where a throw inside a render is a blank screen with a stack trace in a console
 * nobody has open during a demo.
 */
export function normalizeAllowedWords(
  raw: unknown,
  opts: { stage: number; limit?: number; offset?: number },
): AllowedWordsPage {
  const fallbackLimit = opts.limit ?? WORDS_PAGE_SIZE
  const fallbackOffset = opts.offset ?? 0

  const rows = Array.isArray(raw) ? raw : pickArray(raw, ['words', 'items', 'results'])
  const words: AllowedWord[] = rows.map((r) => {
    const row = (r ?? {}) as Record<string, unknown>
    return {
      word: String(row.word ?? ''),
      syllables: num(row.syllables ?? row.syllable_count, 0),
      freq_rank: num(row.freq_rank ?? row.freq, 0),
      in_prompt: typeof row.in_prompt === 'boolean' ? row.in_prompt : undefined,
    }
  })

  const env = (Array.isArray(raw) ? {} : ((raw ?? {}) as Record<string, unknown>)) || {}
  const stageObj =
    env.stage !== null && typeof env.stage === 'object'
      ? (env.stage as Record<string, unknown>)
      : {}

  const total = num(env.total ?? env.count, words.length)
  const offset = num(env.offset, fallbackOffset)

  return {
    stage: {
      id: num(stageObj.id ?? env.stage_id ?? env.stage, opts.stage),
      name: String(stageObj.name ?? ''),
      description: String(stageObj.description ?? ''),
      max_syllables: maybeNum(stageObj.max_syllables),
    },
    total,
    stage_total: num(env.stage_total, total),
    limit: num(env.limit, fallbackLimit),
    offset,
    words,
    // Prefer the server's own cursor; fall back to arithmetic only if it is absent.
    has_more: typeof env.has_more === 'boolean' ? env.has_more : offset + words.length < total,
    next_offset: maybeNum(env.next_offset) ?? null,
    prompt_word_limit: maybeNum(env.prompt_word_limit ?? env.prompt_limit),
    prompt_freq_rank_cutoff: maybeNum(env.prompt_freq_rank_cutoff),
    sight_word_count: maybeNum(env.sight_word_count),
    order: env.order === undefined ? undefined : String(env.order),
    q: env.q === undefined ? undefined : String(env.q),
  }
}

// --- 3. eval runs (§14, §17 step 5) ------------------------------------------

/** The §1 criteria verdict the server computes with `eval.criteria_verdict`. */
export interface EvalCriterion {
  name: string
  ok: boolean
  evidence: string
}

/**
 * 9.3: the list projects a summary out of `metrics` and omits the blob.
 *
 * Verified against run 114: every field below is FLAT on the row, and the §1
 * `criteria` travel with it — so the list can show the verdict without opening
 * the detail, which is what makes it a table worth looking at.
 */
export interface EvalRunSummary {
  id: number
  status: string
  started_at: string | null
  finished_at: string | null
  attempted?: number
  produced?: number
  accepted?: number
  item_count?: number
  cells_planned?: number
  mean_decodability?: number
  min_decodability?: number
  below_gate?: number
  gate_pass_rate?: number
  fallback_rate?: number
  rewrite_p95?: number
  rewrite_max?: number
  cost_usd?: number
  /** Wall clock is what the list carries; there is no p95 latency on this row. */
  wall_clock_s?: number
  pipeline_seconds?: number
  gate?: number
  n?: number
  effort?: string
  stages?: number[]
  themes?: string[]
  /** §14 provenance (DEVIATIONS 8.30): which models produced this matrix. */
  models?: Record<string, unknown>
  pricing_date?: string
  git_commit?: string
  budget_usd?: number
  criteria: EvalCriterion[]
  criteria_passed?: boolean
}

/** 9.9: ephemeral, in-process, and it says so in every response. */
export interface EvalLaunch {
  launch_id?: string
  run_id?: number | null
  state?: string
  budget_usd?: number
  error?: string | null
  [k: string]: unknown
}

/**
 * 9.5/9.6's ceilings, so the launch control can show them, not discover them.
 *
 * Live names: `per_request_ceiling_usd`, `process_ceiling_usd`. The first draft
 * guessed `max_budget_usd`/`process_budget_usd`; both spellings are read.
 * `stale_running_run_ids` lives INSIDE this object on the wire, which is why the
 * page below lifts it out — a caller should not have to know that.
 */
export interface EvalBudget {
  per_request_ceiling_usd?: number
  process_ceiling_usd?: number
  process_committed_usd?: number
  process_remaining_usd?: number
  cli_default_budget_usd?: number
  in_flight_launch_id?: string | null
  max_cells?: number
  max_n?: number
  max_concurrency?: number
  stale_running_run_ids?: number[]
  [k: string]: unknown
}

export interface EvalRunsPage {
  total: number
  limit: number
  offset: number
  runs: EvalRunSummary[]
  /** 9.9. Empty after an API restart, which is the documented behaviour. */
  launches: EvalLaunch[]
  budget: EvalBudget
  /** 9.7: a `running` row from an interrupted run — reported, never blocking. */
  stale_running_run_ids: number[]
  /** True while this process holds a launch — from `budget.in_flight_launch_id`. */
  busy: boolean
}

export async function fetchEvalRuns(opts: { limit?: number; offset?: number } = {}): Promise<
  AdminResult<EvalRunsPage>
> {
  const params = new URLSearchParams({
    limit: String(opts.limit ?? 20),
    offset: String(opts.offset ?? 0),
  })
  const res = await call<unknown>('GET', `/api/admin/eval-runs?${params}`)
  if (!res.ok) return res
  return { ok: true, data: normalizeEvalRuns(res.data) }
}

/** The tolerant seam for the run list (DEVIATIONS 9.37). */
export function normalizeEvalRuns(raw: unknown): EvalRunsPage {
  const env = (Array.isArray(raw) ? {} : ((raw ?? {}) as Record<string, unknown>)) || {}
  const rows = Array.isArray(raw) ? raw : pickArray(raw, ['runs', 'items', 'results'])

  const runs: EvalRunSummary[] = rows.map((r) => {
    const row = (r ?? {}) as Record<string, unknown>
    // Live: the summary is flat on the row. The first draft expected it nested
    // under `summary`; both are read, the row winning where they overlap.
    const s = ((row.summary ?? row.metrics_summary ?? {}) as Record<string, unknown>) || {}
    const get = (...keys: string[]): unknown => {
      for (const k of keys) {
        if (row[k] !== undefined && row[k] !== null) return row[k]
        if (s[k] !== undefined && s[k] !== null) return s[k]
      }
      return undefined
    }
    return {
      id: num(row.id, 0),
      status: String(row.status ?? 'unknown'),
      started_at: str(row.started_at),
      finished_at: str(row.finished_at),
      attempted: maybeNum(get('attempted')),
      produced: maybeNum(get('produced')),
      accepted: maybeNum(get('accepted')),
      item_count: maybeNum(get('item_count')),
      cells_planned: maybeNum(get('cells_planned')),
      mean_decodability: maybeNum(get('decodability_mean', 'mean_decodability')),
      min_decodability: maybeNum(get('decodability_min', 'min_decodability')),
      below_gate: maybeNum(get('below_gate')),
      gate_pass_rate: maybeNum(get('gate_pass_rate')),
      fallback_rate: maybeNum(get('fallback_rate')),
      rewrite_p95: maybeNum(get('rewrite_depth_p95', 'rewrite_p95')),
      rewrite_max: maybeNum(get('rewrite_depth_max')),
      cost_usd: maybeNum(get('cost_usd', 'total_usd')),
      wall_clock_s: maybeNum(get('wall_clock_s')),
      pipeline_seconds: maybeNum(get('pipeline_seconds')),
      gate: maybeNum(get('gate')),
      n: maybeNum(get('n')),
      effort: get('effort') === undefined ? undefined : String(get('effort')),
      stages: pickArray(row, ['stages']).map((v) => num(v, 0)),
      themes: pickArray(row, ['themes']).map((v) => String(v)),
      models: (row.models ?? undefined) as Record<string, unknown> | undefined,
      pricing_date: str(row.pricing_date) ?? undefined,
      git_commit: str(row.git_commit) ?? undefined,
      budget_usd: maybeNum(get('budget_usd')),
      criteria: readCriteria(row),
      criteria_passed: typeof row.criteria_passed === 'boolean' ? row.criteria_passed : undefined,
    }
  })

  const budget = normalizeBudget(env.budget)
  // Live: `stale_running_run_ids` is nested in `budget`. Lifted here, because
  // which object it arrived in is not something four call sites should know.
  const stale = [
    ...pickArray(budget, ['stale_running_run_ids']),
    ...pickArray(env, ['stale_running_run_ids', 'stale_running']),
  ]
    .map((v) => num(v, 0))
    .filter((v) => v > 0)

  return {
    total: num(env.total ?? env.count, runs.length),
    limit: num(env.limit, 20),
    offset: num(env.offset, 0),
    runs,
    launches: pickArray(raw, ['launches']) as EvalLaunch[],
    budget,
    stale_running_run_ids: [...new Set(stale)],
    busy: typeof env.busy === 'boolean' ? env.busy : Boolean(budget.in_flight_launch_id),
  }
}

/**
 * The budget block, with the two ceilings normalised to the live names.
 *
 * The first draft of this file guessed `max_budget_usd`/`process_budget_usd`; the
 * server says `per_request_ceiling_usd`/`process_ceiling_usd`. The alias is
 * resolved HERE rather than at the two call sites — a component reading two
 * spellings of one field is exactly the drift DEVIATIONS 9.37 exists to keep out
 * of components.
 */
function normalizeBudget(raw: unknown): EvalBudget {
  const b = ((raw ?? {}) as Record<string, unknown>) || {}
  return {
    ...(b as EvalBudget),
    per_request_ceiling_usd: maybeNum(b.per_request_ceiling_usd ?? b.max_budget_usd),
    process_ceiling_usd: maybeNum(b.process_ceiling_usd ?? b.process_budget_usd),
    process_committed_usd: maybeNum(b.process_committed_usd),
    process_remaining_usd: maybeNum(b.process_remaining_usd),
    cli_default_budget_usd: maybeNum(b.cli_default_budget_usd),
    in_flight_launch_id: str(b.in_flight_launch_id),
  }
}

/**
 * `criteria` in either encoding.
 *
 * `eval.criteria_verdict` returns `list[tuple[str, bool, str]]`; `admin.py`
 * converts it to objects. Both are read so the portal cannot disagree with `make
 * eval`'s exit code over a JSON encoding detail (§14: "the portal is a view").
 */
function readCriteria(raw: unknown): EvalCriterion[] {
  return pickArray(raw, ['criteria']).map((c) => {
    if (Array.isArray(c)) {
      return { name: String(c[0] ?? ''), ok: Boolean(c[1]), evidence: String(c[2] ?? '') }
    }
    const row = (c ?? {}) as Record<string, unknown>
    return {
      name: String(row.name ?? row.criterion ?? ''),
      ok: Boolean(row.ok ?? row.met ?? row.passed),
      evidence: String(row.evidence ?? row.detail ?? ''),
    }
  })
}

/** One row of `eval_run_items` — §14's per-story metric row, verbatim. */
export interface EvalRunItem {
  id: number
  story_id: number | null
  metrics: Record<string, unknown>
}

export interface EvalRunDetail {
  id: number
  status: string
  started_at: string | null
  finished_at: string | null
  config: Record<string, unknown>
  /** 9.10: `eval_runs.metrics` returned whole — §14's whole aggregate. */
  metrics: Record<string, unknown>
  /** 9.10: computed by calling `eval.criteria_verdict`, never reimplemented. */
  criteria: EvalCriterion[]
  criteria_passed?: boolean
  items: EvalRunItem[]
  items_total: number
  items_limit: number
  items_offset: number
  /** The CLI invocation that reproduces this run — §14 keeps the CLI canonical. */
  report_command?: string
}

export async function fetchEvalRun(
  id: number,
  opts: { limit?: number; offset?: number } = {},
): Promise<AdminResult<EvalRunDetail>> {
  // `items_limit`/`items_offset`, NOT `limit`/`offset`: the detail route paginates
  // `eval_run_items` and names its parameters for them. The wrong names are
  // silently ignored, so this read correctly by accident on the server's defaults
  // and would have broken on the second page of a 180-item run.
  const params = new URLSearchParams({
    items_limit: String(opts.limit ?? 50),
    items_offset: String(opts.offset ?? 0),
  })
  const res = await call<unknown>('GET', `/api/admin/eval-runs/${id}?${params}`)
  if (!res.ok) return res
  return { ok: true, data: normalizeEvalRun(res.data, id) }
}

/** The tolerant seam for the run detail (DEVIATIONS 9.37). */
export function normalizeEvalRun(raw: unknown, id: number): EvalRunDetail {
  const env = ((raw ?? {}) as Record<string, unknown>) || {}
  // Live: flat. Kept tolerant of a `run` wrapper, since 9.10 pins the contents
  // and not the nesting.
  const runObj = ((env.run ?? env) as Record<string, unknown>) || {}
  const items = pickArray(raw, ['items', 'run_items']).map((r) => {
    const row = (r ?? {}) as Record<string, unknown>
    return {
      id: num(row.id, 0),
      // §14 makes `story_id` nullable on purpose: an api_fail cell has a latency,
      // an outcome and a cost, and no story. Coercing it to 0 would invent one.
      story_id: row.story_id === null || row.story_id === undefined ? null : num(row.story_id, 0),
      metrics: ((row.metrics ?? {}) as Record<string, unknown>) || {},
    }
  })

  return {
    id: num(runObj.id, id),
    status: String(runObj.status ?? 'unknown'),
    started_at: str(runObj.started_at),
    finished_at: str(runObj.finished_at),
    config: ((runObj.config ?? {}) as Record<string, unknown>) || {},
    metrics: ((runObj.metrics ?? env.metrics ?? {}) as Record<string, unknown>) || {},
    criteria: readCriteria(env),
    criteria_passed: typeof env.criteria_passed === 'boolean' ? env.criteria_passed : undefined,
    items,
    items_total: num(env.items_total ?? env.total, items.length),
    items_limit: num(env.items_limit ?? env.limit, 50),
    items_offset: num(env.items_offset ?? env.offset, 0),
    report_command: str(env.report_command) ?? undefined,
  }
}

// --- 4. POST /api/admin/eval-runs — estimate first, then launch (9.4) --------

/**
 * The estimate response, which is unusually self-describing and is therefore
 * rendered almost verbatim.
 *
 * `mode: "estimate" | "launch"` — `"launch"`, not `"launched"`; the boolean beside
 * it is `launched`, and confusing the two is how a branch on one of them silently
 * never fires. Plus a `projection` with THREE
 * cost scenarios rather than one number, a `reference_run` (the last real matrix
 * and what it actually cost), `requires` naming the budget window in words, and a
 * `detail` sentence. The screen shows the server's numbers and the server's
 * sentence: a UI that re-derived "about five dollars" from a token count would be
 * a second estimate, able to disagree with the one the ceiling is checked against.
 */
export interface EvalProjection {
  no_rewrites_usd?: number
  with_one_rewrite_each_usd?: number
  worst_case_all_budget_exhausted_usd?: number
  models?: Record<string, unknown>
  pricing_date?: string
  assumed_input_tokens?: number
  assumed_output_tokens?: number
  [k: string]: unknown
}

export interface EvalEstimate {
  mode: string
  launched: boolean
  cells_planned: number
  stages: number[]
  themes: string[]
  n?: number
  effort?: string
  concurrency?: number
  gate?: number
  projection: EvalProjection
  reference_run?: Record<string, unknown>
  budget: EvalBudget
  /** `{budget_usd: "> 6.4939 … and <= 8.0", confirm: true}` — shown as given. */
  requires?: Record<string, unknown>
  /** 200 for an estimate, 202 for a launch. Recorded, not branched on. */
  detail?: string
  /** The launch response's own ids, when this is a launch rather than an estimate. */
  launch_id?: string
  run_id?: number | null
  raw: Record<string, unknown>
}

export interface EvalRunRequest {
  stages?: string
  themes?: string
  n?: number
  effort?: string
  budget_usd?: number
  confirm?: boolean
}

/**
 * 9.4: a request with no `budget_usd`, or with `confirm` left false, makes ZERO
 * API calls and returns the projection. Both steps go through this one function
 * with a different body, so the UI cannot accidentally construct a launch — the
 * `confirm` flag is set in exactly one place (`AdminEvalRuns`'s second control).
 */
export async function postEvalRun(body: EvalRunRequest): Promise<AdminResult<unknown>> {
  // 9.8 runs the matrix as a background task and answers immediately, so the
  // ordinary request timeout applies even though the run itself takes ~18 minutes.
  return call<unknown>('POST', '/api/admin/eval-runs', body)
}

/** The tolerant seam for the estimate/launch response (DEVIATIONS 9.37). */
export function normalizeEstimate(raw: unknown): EvalEstimate {
  const env = ((raw ?? {}) as Record<string, unknown>) || {}
  const projection = ((env.projection ?? {}) as EvalProjection) || {}
  return {
    // `"launch"` is the server's word for a real run; `"estimate"` for a dry one.
    // Nothing in this app branches on it — `launched` is the boolean for that —
    // but the string is shown, so it must be the server's and not an invention.
    mode: String(env.mode ?? (env.launched ? 'launch' : 'estimate')),
    launched: Boolean(env.launched),
    cells_planned: num(env.cells_planned ?? env.cells, 0),
    stages: pickArray(env, ['stages']).map((v) => num(v, 0)),
    themes: pickArray(env, ['themes']).map((v) => String(v)),
    n: maybeNum(env.n),
    effort: env.effort === undefined ? undefined : String(env.effort),
    concurrency: maybeNum(env.concurrency),
    gate: maybeNum(env.gate),
    projection,
    reference_run: (env.reference_run ?? undefined) as Record<string, unknown> | undefined,
    budget: normalizeBudget(env.budget),
    requires: (env.requires ?? undefined) as Record<string, unknown> | undefined,
    detail: str(env.detail) ?? undefined,
    launch_id: str(env.launch_id) ?? undefined,
    run_id: maybeNum(env.run_id) ?? null,
    raw: env,
  }
}

// --- 5. cold generate (§11's button, §17 step 4) -----------------------------

/**
 * One §7 node transition, as the job snapshot reports it.
 *
 * `node` is a STRING and this file does not enumerate the state machine. §7 owns
 * that, `StatePill` renders an unknown name as itself, and a node added to the
 * pipeline should appear in the chain rather than vanish from a diagram whose
 * whole purpose is to be complete.
 *
 * Two fields are easy to confuse and both are needed: `elapsed_ms` is the OFFSET
 * from the start of the job (when this node opened) and `duration_ms` is how long
 * the node itself took. Style guide §09's pills print a duration; the chain reads
 * left to right in offsets.
 *
 * `source` separates a real §7 span from a job-level marker — `RECEIVED` and
 * `OUTCOME` are facts about the endpoint, not observations of the state machine,
 * and the snapshot says which is which rather than blurring them into one chain.
 */
export interface ColdGenTransition {
  seq: number
  node: string
  source: string
  /** `running` | `done` | `error`. */
  status: string
  attempt: number | null
  at?: string | null
  elapsed_ms: number | null
  duration_ms: number | null
  /** `span.update(...)` — where the demo's evidence is: `output`, `usage`, level. */
  detail: Record<string, unknown>
  error: string | null
}

/**
 * The VERDICT a transition reported, which is not the same thing as whether it ran.
 *
 * **Every §7 node completes with `status: "done"`, including the two whose whole
 * job is to say no.** So a pill keyed on `status` alone paints a green
 * `MODERATING rejected` and a green `OUTCOME moderation_fail`. That was observed
 * in a real browser on a real stage-10 rejection: the word said "rejected" while
 * the colour said "fine", and on a demo screen the colour is what gets read. It is
 * §11's "never colour alone" inverted — the label was right and the hue was wrong,
 * which is worse than either being missing.
 *
 * Three verdict fields exist across the machine and all three are read here:
 *
 *   `output.passes_gate`  VALIDATING refusing a draft — §17 step 4's catch
 *   `output.approved`     MODERATING refusing a story that passed the gate (§13)
 *   `detail.outcome`      the job's terminal marker; `accepted` is §7's only success
 *
 * `null` means "this transition reported no verdict", which is the honest answer
 * for RECEIVED, GENERATING and REWRITING — they produce text, they do not judge it.
 *
 * A pure function in this file rather than a branch in the component, because it
 * is read off the response contract and because `bun test src` covers `lib/`
 * (DEVIATIONS 7.30, 9.39): the same reasoning that moved `adminErrorCopy` here.
 */
export function transitionVerdict(t: ColdGenTransition): boolean | null {
  const output = t.detail.output
  if (output !== null && typeof output === 'object') {
    const o = output as Record<string, unknown>
    if (typeof o.passes_gate === 'boolean') return o.passes_gate
    if (typeof o.approved === 'boolean') return o.approved
  }
  const outcome = t.detail.outcome
  if (typeof outcome === 'string') return outcome === 'accepted'
  return null
}

/** §17 step 4's rewrite diff, computed server-side from `PipelineResult.attempts`. */
export interface ColdGenAttempt {
  attempt: number
  kind: string
  decodability: number | null
  violations: string[]
  latency_ms: number | null
  text: string
  /** The violations the PREVIOUS attempt carried — what the validator caught. */
  caught: string[]
  /** Which of those are gone from this attempt's text. */
  fixed: string[]
  still_present: string[]
  /** Words that appeared in this attempt and not the last — the swap, by name. */
  new_words: string[]
}

export interface ColdGenResult {
  outcome: string
  passed: boolean
  title: string
  text: string
  decodability: number | null
  gate: number | null
  meets_gate: boolean
  counts: Record<string, number>
  violations: string[]
  rewrite_depth: number | null
  max_rewrites: number | null
  attempts: number | null
  story_words: string[]
  moderation: Record<string, unknown> | null
  cost_usd: number | null
  latency_ms: Record<string, unknown>
  rewrite_loop: ColdGenAttempt[]
  raw: Record<string, unknown>
}

export interface ColdGenJob {
  job_id: string
  /** `queued` | `running` | `done` | `error`. */
  state: string
  stage_id?: number
  theme?: string
  effort?: string
  moderated?: boolean
  started_at?: string | null
  finished_at?: string | null
  elapsed_ms?: number
  current_node: string | null
  /**
   * 9.13's change counter, bumped on EVERY mutation of the snapshot.
   *
   * This is the field to watch, not `transitions[].seq`. A node visit appends an
   * entry and then MUTATES it in place when the node closes — `running` becomes
   * `done`, `duration_ms` is filled in — so the highest `seq` can be unchanged
   * across a poll in which the chain's meaning changed completely. This component
   * sidesteps the question by replacing the whole snapshot on every poll (9.13(b):
   * each poll is complete, so there is nothing to merge), and `revision` is what a
   * caller that wanted to skip a render would compare.
   */
  revision: number
  transitions: ColdGenTransition[]
  result: ColdGenResult | null
  error: string | null
  langfuse: {
    enabled?: boolean
    trace_id?: string | null
    host?: string | null
    /** Always null: 9.18 refuses to invent a console URL. */
    url?: string | null
  }
  persisted?: boolean
  /** The server's own suggested poll interval, preferred over the constant. */
  poll_after_ms?: number
  notes: string[]
  raw: Record<string, unknown>
}

/** 9.13's own number: a cold generation is 25-60s, so 750ms resolves every node. */
export const COLD_POLL_MS = 750
/**
 * When to stop polling regardless of what the server says.
 *
 * §7's generation timeout is 240s (derived from the token ceiling, DEVIATIONS
 * 8.6) and a run may rewrite up to three times, so a job that has not terminated
 * after twelve minutes is not going to. A timer left running for the rest of the
 * session is the failure this bounds.
 */
export const COLD_POLL_CEILING_MS = 12 * 60 * 1000

/** `admin.py`'s terminal job states. Polling stops; nothing is retried. */
const TERMINAL = new Set(['done', 'error'])

export function isTerminal(state: string | undefined | null): boolean {
  return TERMINAL.has(String(state ?? '').toLowerCase())
}

export async function startColdGenerate(body: {
  stage_id: number
  theme: string | number
  effort?: string
  moderate?: boolean
}): Promise<AdminResult<ColdGenJob>> {
  const res = await call<unknown>('POST', '/api/admin/cold-generate', body)
  if (!res.ok) return res
  return { ok: true, data: normalizeColdGenJob(res.data) }
}

export async function fetchColdGenerate(jobId: string): Promise<AdminResult<ColdGenJob>> {
  const res = await call<unknown>(
    'GET',
    `/api/admin/cold-generate/${encodeURIComponent(jobId)}`,
    undefined,
    // A poll that outlives its own interval is a queue, so its timeout is short.
    10_000,
  )
  if (!res.ok) return res
  return { ok: true, data: normalizeColdGenJob(res.data) }
}

/** The tolerant seam for a job snapshot (DEVIATIONS 9.37). */
export function normalizeColdGenJob(raw: unknown): ColdGenJob {
  const env = ((raw ?? {}) as Record<string, unknown>) || {}
  const job = ((env.job ?? env) as Record<string, unknown>) || {}
  const lf = ((job.langfuse ?? env.langfuse ?? {}) as Record<string, unknown>) || {}

  const transitions: ColdGenTransition[] = pickArray(job, [
    'transitions',
    'nodes',
    'states',
    'spans',
  ]).map((t, i) => {
    const row = (t ?? {}) as Record<string, unknown>
    return {
      seq: num(row.seq, i + 1),
      node: String(row.node ?? row.state ?? row.name ?? '').toUpperCase(),
      source: String(row.source ?? 'pipeline'),
      status: String(row.status ?? ''),
      attempt: maybeNum(row.attempt) ?? null,
      at: str(row.at),
      elapsed_ms: maybeNum(row.elapsed_ms) ?? null,
      duration_ms: maybeNum(row.duration_ms) ?? null,
      detail: ((row.detail ?? {}) as Record<string, unknown>) || {},
      error: str(row.error),
    }
  })

  return {
    job_id: String(job.job_id ?? job.id ?? ''),
    state: String(job.state ?? job.status ?? 'unknown'),
    stage_id: maybeNum(job.stage_id),
    theme: job.theme === undefined ? undefined : String(job.theme),
    effort: job.effort === undefined ? undefined : String(job.effort),
    moderated: typeof job.moderated === 'boolean' ? job.moderated : undefined,
    started_at: str(job.started_at),
    finished_at: str(job.finished_at),
    elapsed_ms: maybeNum(job.elapsed_ms),
    current_node: str(job.current_node),
    revision: num(job.revision ?? job.seq, 0),
    transitions,
    result: normalizeColdResult(job.result),
    error: str(job.error),
    langfuse: {
      enabled: typeof lf.enabled === 'boolean' ? lf.enabled : undefined,
      trace_id: str(lf.trace_id ?? job.langfuse_trace_id),
      host: str(lf.host),
      url: str(lf.url),
    },
    persisted: typeof job.persisted === 'boolean' ? job.persisted : undefined,
    poll_after_ms: maybeNum(job.poll_after_ms),
    notes: pickArray(job, ['notes']).map((v) => String(v)),
    raw: env,
  }
}

function normalizeColdResult(raw: unknown): ColdGenResult | null {
  if (raw === null || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  return {
    outcome: String(r.outcome ?? 'unknown'),
    passed: Boolean(r.passed),
    title: String(r.title ?? ''),
    text: String(r.text ?? ''),
    decodability: maybeNum(r.decodability) ?? null,
    gate: maybeNum(r.gate) ?? null,
    meets_gate: Boolean(r.meets_gate),
    counts: ((r.counts ?? {}) as Record<string, number>) || {},
    violations: pickArray(r, ['violations']).map((v) => String(v)),
    rewrite_depth: maybeNum(r.rewrite_depth) ?? null,
    max_rewrites: maybeNum(r.max_rewrites) ?? null,
    attempts: maybeNum(r.attempts) ?? null,
    story_words: pickArray(r, ['story_words']).map((v) => String(v)),
    moderation: (r.moderation ?? null) as Record<string, unknown> | null,
    cost_usd: maybeNum(r.cost_usd) ?? null,
    latency_ms: ((r.latency_ms ?? {}) as Record<string, unknown>) || {},
    rewrite_loop: pickArray(r, ['rewrite_loop']).map((a, i) => {
      const row = (a ?? {}) as Record<string, unknown>
      return {
        attempt: num(row.attempt, i + 1),
        kind: String(row.kind ?? ''),
        decodability: maybeNum(row.decodability) ?? null,
        violations: pickArray(row, ['violations']).map((v) => String(v)),
        latency_ms: maybeNum(row.latency_ms) ?? null,
        text: String(row.text ?? ''),
        caught: pickArray(row, ['caught']).map((v) => String(v)),
        fixed: pickArray(row, ['fixed']).map((v) => String(v)),
        still_present: pickArray(row, ['still_present']).map((v) => String(v)),
        new_words: pickArray(row, ['new_words']).map((v) => String(v)),
      }
    }),
    raw: r,
  }
}

/**
 * The rewrite CATCHES in a finished job — §17 step 4's "the model tried
 * 'gleaming'; the validator caught it; the rewrite swapped it".
 *
 * This is a FILTER, not a derivation (DEVIATIONS 9.32). `admin.py::_rewrite_loop`
 * already computes the diff from `PipelineResult.attempts` — which words the
 * previous attempt was refused for, which of them are gone, and which words
 * appeared instead — so the client's whole job is to pick the attempts that
 * actually caught something. An earlier draft of this file reimplemented that diff
 * on the client from the VALIDATING spans; it was deleted rather than kept as a
 * fallback, because two implementations of one diff means the one on screen is the
 * one nobody tests.
 */
export function rewriteCatches(job: ColdGenJob): ColdGenAttempt[] {
  return (job.result?.rewrite_loop ?? []).filter((a) => a.caught.length > 0)
}

// --- small readers, shared by every seam above -------------------------------

/** The first of `keys` holding an array, or `[]`. Never throws on a scalar. */
export function pickArray(raw: unknown, keys: string[]): unknown[] {
  if (raw === null || typeof raw !== 'object') return []
  const obj = raw as Record<string, unknown>
  for (const k of keys) {
    if (Array.isArray(obj[k])) return obj[k] as unknown[]
  }
  return []
}

function num(v: unknown, fallback: number): number {
  const n = typeof v === 'string' ? Number(v) : v
  return typeof n === 'number' && Number.isFinite(n) ? n : fallback
}

function maybeNum(v: unknown): number | undefined {
  const n = typeof v === 'string' ? Number(v) : v
  return typeof n === 'number' && Number.isFinite(n) ? n : undefined
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v ? v : null
}
