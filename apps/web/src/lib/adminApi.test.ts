/**
 * The tolerant seams in `lib/adminApi.ts` (DEVIATIONS 9.37).
 *
 * These functions exist because the admin backend (DEVIATIONS 9.1-9.19) was
 * written at the same time as this frontend: the entries pin the semantics of
 * every response and, in places, not the exact key. A guess in a component is a
 * guess nobody can see; a guess in a normaliser is a guess with a test beside it.
 *
 * So the tests pin two things per seam: the shape the backend entries describe is
 * read correctly, AND a plausible near-miss does not produce a screen full of
 * `undefined`. The second half is the point — the failure this file prevents is a
 * table that renders "—" in every cell and looks like an empty database.
 */

import { describe, expect, test } from 'bun:test'
import {
  COLD_POLL_CEILING_MS,
  MISSING_ROLE_CLAIM_DETAIL,
  adminErrorCopy,
  COLD_POLL_MS,
  MAX_STORY_WORDS_INPUT,
  MAX_VALIDATE_CHARS,
  WORDS_PAGE_MAX,
  WORDS_PAGE_SIZE,
  isTerminal,
  normalizeAllowedWords,
  normalizeColdGenJob,
  normalizeEstimate,
  normalizeEvalRun,
  normalizeEvalRuns,
  pickArray,
  readDetail,
  rewriteCatches,
  transitionVerdict,
  type ColdGenTransition,
} from './adminApi'

describe('the caps mirror the server, so a 422 is not how they are discovered', () => {
  test('§5.6 and ValidateRequest', () => {
    // `services/api/storyforge/api/validate.py`: text max_length=4000,
    // story_words max_length=8.
    expect(MAX_VALIDATE_CHARS).toBe(4000)
    expect(MAX_STORY_WORDS_INPUT).toBe(8)
  })

  test('DEVIATIONS 9.12: allowed-words limit defaults to 100, caps at 500', () => {
    expect(WORDS_PAGE_SIZE).toBe(100)
    expect(WORDS_PAGE_MAX).toBe(500)
  })

  test('DEVIATIONS 9.13: the poll interval is 750ms and the ceiling is finite', () => {
    expect(COLD_POLL_MS).toBe(750)
    expect(COLD_POLL_CEILING_MS).toBeGreaterThan(0)
    expect(COLD_POLL_CEILING_MS).toBeLessThan(30 * 60 * 1000)
  })
})

describe('readDetail — an admin is shown the server’s own words (9.36)', () => {
  test("FastAPI's `detail` string is what comes back", async () => {
    const res = new Response(
      JSON.stringify({ detail: 'admin role required (§9); set public_metadata.role in Clerk' }),
      { status: 403 },
    )
    expect(await readDetail(res)).toBe(
      'admin role required (§9); set public_metadata.role in Clerk',
    )
  })

  test("a 422's validation list is flattened with its field names, not stringified", async () => {
    const res = new Response(
      JSON.stringify({
        detail: [
          { loc: ['body', 'stage_id'], msg: 'Input should be less than or equal to 12' },
          { loc: ['body', 'text'], msg: 'String should have at most 4000 characters' },
        ],
      }),
      { status: 422 },
    )
    const detail = await readDetail(res)
    expect(detail).toContain('body.stage_id: Input should be less than or equal to 12')
    expect(detail).toContain('body.text')
    expect(detail).not.toContain('{')
  })

  test('a non-JSON body and an empty body both yield something printable', async () => {
    expect(await readDetail(new Response('Internal Server Error', { status: 500 }))).toBe(
      'Internal Server Error',
    )
    const empty = await readDetail(new Response('', { status: 502, statusText: 'Bad Gateway' }))
    expect(empty.length).toBeGreaterThan(0)
  })
})

describe('adminErrorCopy — the 403 surface 7.37 removed, with a POSITIVE control', () => {
  /**
   * The shape of this suite is the point, not just its assertions.
   *
   * `docs/AUDIT-PHASE7-FIXES.md` F2 found that adding a serving predicate made
   * FIVE existing guards vacuous, and the admin backend then found the same class
   * in its own forgery test: it asserted only that forgeries are refused, so with
   * token verification sabotaged to refuse EVERYTHING it stayed green while
   * proving nothing.
   *
   * "403 names the missing `public_metadata` claim" has exactly that failure mode.
   * It passes in a build where EVERY status names the role claim — which would tell
   * an operator staring at a 500 to go and edit their Clerk dashboard. So the
   * negative control (403 names it) is paired with a POSITIVE control (no other
   * status names it, and each says something different and true instead).
   */
  test('403 names the missing claim and the dashboard action, verbatim from 7.37', () => {
    const copy = adminErrorCopy(403)
    expect(copy.kind).toBe('role')
    expect(copy.namesRoleClaim).toBe(true)
    expect(copy.body).toBe(MISSING_ROLE_CLAIM_DETAIL)
    // The three facts 6.13/6.20 measured, each of which a paraphrase would lose.
    expect(copy.body).toContain('default session')
    expect(copy.body).toContain('every signed-in user as a parent')
    expect(copy.body).toContain('customize session token')
    expect(copy.body).toContain('public_metadata')
    // And it never says the word the entry exists to avoid.
    expect(copy.headline.toLowerCase()).not.toContain('forbidden')
    expect(copy.body.toLowerCase()).not.toContain('forbidden')
  })

  test('POSITIVE CONTROL: no other status names the role claim', () => {
    // Without this test, a `return {namesRoleClaim: true, body:
    // MISSING_ROLE_CLAIM_DETAIL}` at the top of `adminErrorCopy` — ignoring the
    // status entirely — would leave the test above green.
    for (const status of [0, 401, 404, 422, 409, 500, 502, 503]) {
      const copy = adminErrorCopy(status)
      expect(copy.namesRoleClaim).toBe(false)
      expect(copy.body).not.toBe(MISSING_ROLE_CLAIM_DETAIL)
      expect(copy.body).not.toContain('customize session token')
      expect(copy.kind).not.toBe('role')
    }
  })

  test('POSITIVE CONTROL: the five kinds are five DIFFERENT sentences', () => {
    // A build that collapsed the ladder to one generic message would pass both
    // tests above. This one requires the ladder to still be a ladder.
    const kinds = [403, 401, 404, 0, 500].map((s) => adminErrorCopy(s))
    expect(new Set(kinds.map((c) => c.kind)).size).toBe(5)
    expect(new Set(kinds.map((c) => c.headline)).size).toBe(5)
    expect(new Set(kinds.map((c) => c.body)).size).toBe(5)
  })

  test('each non-403 status says the thing an admin can act on', () => {
    // 401 is "the API refused the token while Clerk still thinks it is valid",
    // which is audit C4 (DEVIATIONS 7.32) — the bug where one screen said "please
    // sign in again" underneath a live `<UserButton/>`.
    expect(adminErrorCopy(401).body).toContain('sign out and in again')
    expect(adminErrorCopy(401).kind).toBe('session')
    // A 404 on `/api/admin/*` is a real possibility — the router may not be
    // mounted on the server this build is pointed at.
    expect(adminErrorCopy(404).body).toContain('/openapi.json')
    // Status 0 is "never reached the server", a different sentence to "the server
    // said no", and it must promise nothing was changed.
    expect(adminErrorCopy(0).headline).toContain('Could not reach')
    expect(adminErrorCopy(0).body).toContain('Nothing has been changed')
    // An unexplainable status prints itself rather than being relabelled.
    expect(adminErrorCopy(503).headline).toBe('The API returned 503')
  })
})

describe('normalizeAllowedWords (9.12)', () => {
  const opts = { stage: 3, limit: 100, offset: 0 }

  /**
   * The LIVE envelope, copied from
   * `GET /api/admin/allowed-words?stage=3&limit=3` against a server reporting
   * `demo_bypass_auth: false`. Two names in it are the corrections DEVIATIONS 9.37
   * anticipated: `stage` is an object, not a `stage_id` integer, and the prompt
   * cutoff is `prompt_word_limit`.
   */
  test('the LIVE envelope is read field for field', () => {
    const page = normalizeAllowedWords(
      {
        stage: {
          id: 3,
          name: 'Digraphs',
          description: 'Two letters one sound: sh ch th ng qu, plus w x y z v j',
          max_syllables: 2,
        },
        total: 2593,
        stage_total: 2593,
        limit: 3,
        offset: 0,
        order: 'freq',
        q: '',
        has_more: true,
        next_offset: 3,
        sight_word_count: 16,
        prompt_word_limit: 400,
        prompt_freq_rank_cutoff: 2221,
        words: [
          { word: 'the', syllables: 1, freq_rank: 1, in_prompt: true },
          { word: 'shed', syllables: 1, freq_rank: 4812, in_prompt: false },
        ],
      },
      opts,
    )
    expect(page.total).toBe(2593)
    expect(page.stage_total).toBe(2593)
    expect(page.stage.id).toBe(3)
    expect(page.stage.name).toBe('Digraphs')
    expect(page.stage.max_syllables).toBe(2)
    expect(page.sight_word_count).toBe(16)
    expect(page.prompt_word_limit).toBe(400)
    expect(page.prompt_freq_rank_cutoff).toBe(2221)
    expect(page.has_more).toBe(true)
    expect(page.next_offset).toBe(3)
    expect(page.words[0]).toEqual({ word: 'the', syllables: 1, freq_rank: 1, in_prompt: true })
    expect(page.words[1].in_prompt).toBe(false)
  })

  test('the DRAFTED names still resolve, so a rename is a "—" and not a blank screen', () => {
    // `stage_id` + `prompt_limit` were this file's first guess. Kept readable
    // because `admin.py` declares no response model, so nothing server-side would
    // turn a rename red (DEVIATIONS 9.37).
    const page = normalizeAllowedWords(
      { stage_id: 7, total: 5845, prompt_limit: 400, words: [{ word: 'car' }] },
      { stage: 7 },
    )
    expect(page.stage.id).toBe(7)
    expect(page.prompt_word_limit).toBe(400)
    expect(page.total).toBe(5845)
  })

  test('a bare array still fills the table, and `total` falls back to its length', () => {
    const page = normalizeAllowedWords(
      [{ word: 'cat', syllables: 1, freq_rank: 300 }],
      opts,
    )
    expect(page.words).toHaveLength(1)
    expect(page.total).toBe(1)
    expect(page.stage.id).toBe(3)
  })

  test('`items` and `results` are read too, and `syllable_count` is accepted', () => {
    expect(normalizeAllowedWords({ items: [{ word: 'a' }] }, opts).words).toHaveLength(1)
    expect(normalizeAllowedWords({ results: [{ word: 'a' }] }, opts).words).toHaveLength(1)
    // §8's column is `syllables`; §5.4's prose calls it `syllable_count`.
    expect(
      normalizeAllowedWords({ words: [{ word: 'a', syllable_count: 2 }] }, opts).words[0].syllables,
    ).toBe(2)
  })

  test('an unrecognised shape is an EMPTY page, never a throw inside a render', () => {
    const page = normalizeAllowedWords({ nothing: 'useful' }, opts)
    expect(page.words).toEqual([])
    expect(page.total).toBe(0)
    // Visibly wrong on screen, which is reportable. A throw is a blank screen.
    expect(normalizeAllowedWords(null, opts).words).toEqual([])
    expect(normalizeAllowedWords('nonsense', opts).words).toEqual([])
  })

  test('`in_prompt` stays undefined when absent — it is not guessed as false', () => {
    // "Every word a stage-3 reader may see" and "the 400 the generator is shown"
    // are different claims (9.12). Defaulting the flag would assert the second.
    expect(normalizeAllowedWords({ words: [{ word: 'a' }] }, opts).words[0].in_prompt).toBe(
      undefined,
    )
  })
})

describe('normalizeEvalRuns (9.2, 9.3, 9.9)', () => {
  /**
   * The LIVE envelope, copied from `GET /api/admin/eval-runs?limit=2` — run 114,
   * the §14 matrix `docs/EVAL.md` records. The summary is FLAT on the row and the
   * §1 `criteria` travel with it; `stale_running_run_ids` lives inside `budget`.
   */
  test('the LIVE envelope, summary flat and criteria on the row', () => {
    const page = normalizeEvalRuns({
      total: 5,
      limit: 2,
      offset: 0,
      runs: [
        {
          id: 114,
          started_at: '2026-07-30T04:18:35.791296+00:00',
          finished_at: '2026-07-30T04:36:24.032775+00:00',
          status: 'completed',
          item_count: 180,
          cells_planned: 180,
          stages: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
          themes: ['animals', 'dragons', 'space'],
          n: 5,
          effort: 'medium',
          concurrency: 6,
          pricing_date: '2026-07-29',
          models: { fast: 'claude-haiku-4-5', generation: 'claude-sonnet-5' },
          gate: 0.95,
          budget_usd: 15.0,
          git_commit: '83d1cc8',
          attempted: 180,
          produced: 180,
          accepted: 172,
          gate_pass_rate: 0.9555555555555556,
          fallback_rate: 0.044444444444444446,
          below_gate: 0,
          decodability_mean: 0.9894157467589966,
          decodability_min: 0.95,
          rewrite_depth_p95: 2.0,
          rewrite_depth_max: 3.0,
          cost_usd: 5.412963,
          wall_clock_s: 1068.2,
          pipeline_seconds: 4587.0,
          criteria: [
            {
              name: 'every story reaches >=95% decodability',
              ok: true,
              evidence: '180/180 stories at or above the gate (0 below)',
            },
            { name: 'p95 rewrite depth <= 2', ok: true, evidence: 'p95 = 2.0, max = 3.0' },
          ],
          criteria_passed: true,
        },
      ],
      launches: [],
      budget: {
        per_request_ceiling_usd: 8.0,
        process_ceiling_usd: 16.0,
        process_committed_usd: 0,
        process_remaining_usd: 16.0,
        cli_default_budget_usd: 15.0,
        in_flight_launch_id: null,
        stale_running_run_ids: [112],
      },
    })

    const run = page.runs[0]
    expect(page.total).toBe(5)
    expect(run.id).toBe(114)
    expect(run.mean_decodability).toBeCloseTo(0.98942, 5)
    expect(run.min_decodability).toBe(0.95)
    expect(run.below_gate).toBe(0)
    expect(run.rewrite_p95).toBe(2)
    expect(run.rewrite_max).toBe(3)
    expect(run.cost_usd).toBeCloseTo(5.412963, 6)
    expect(run.wall_clock_s).toBeCloseTo(1068.2, 1)
    expect(run.accepted).toBe(172)
    expect(run.criteria_passed).toBe(true)
    expect(run.criteria).toHaveLength(2)
    expect(run.criteria[0].evidence).toContain('180/180')
    expect(run.stages).toHaveLength(12)
    expect(run.themes).toEqual(['animals', 'dragons', 'space'])
    expect(run.git_commit).toBe('83d1cc8')

    // 9.5/9.6's ceilings under the live names, and the stale row LIFTED out of
    // `budget` so no caller has to know which object it arrived in.
    expect(page.budget.per_request_ceiling_usd).toBe(8)
    expect(page.budget.process_ceiling_usd).toBe(16)
    expect(page.stale_running_run_ids).toEqual([112])
    // No launch in flight → not busy. An empty launch list is 9.9's documented
    // state after a restart, not an error.
    expect(page.busy).toBe(false)
    expect(page.launches).toEqual([])
  })

  test('an in-flight launch makes the page busy, which disables the launch button', () => {
    const page = normalizeEvalRuns({
      runs: [],
      launches: [{ launch_id: 'lch_1', run_id: 115, state: 'running', budget_usd: 7 }],
      budget: { in_flight_launch_id: 'lch_1' },
    })
    expect(page.busy).toBe(true)
    expect(page.launches[0].run_id).toBe(115)
  })

  test('the DRAFTED shapes still resolve — a nested summary and the guessed ceilings', () => {
    const page = normalizeEvalRuns({
      runs: [{ id: 7, status: 'completed', summary: { attempted: 4, mean_decodability: 1 } }],
      budget: { max_budget_usd: 8, process_budget_usd: 16 },
      stale_running_run_ids: [99],
    })
    expect(page.runs[0].attempted).toBe(4)
    expect(page.runs[0].mean_decodability).toBe(1)
    expect(page.budget.per_request_ceiling_usd).toBe(8)
    expect(page.budget.process_ceiling_usd).toBe(16)
    expect(page.stale_running_run_ids).toEqual([99])
  })

  test('missing summary fields stay undefined so the table can print "—"', () => {
    // A `running` row has no aggregates yet, and that is exactly the row 9.7
    // says will be in this database (id 112, interrupted). It must render.
    const page = normalizeEvalRuns({ runs: [{ id: 9, status: 'running' }] })
    expect(page.runs[0].id).toBe(9)
    expect(page.runs[0].status).toBe('running')
    expect(page.runs[0].mean_decodability).toBe(undefined)
    expect(page.runs[0].criteria).toEqual([])
    // Zero and undefined must not be conflated: 0 below the gate is the good news.
    expect(normalizeEvalRuns({ runs: [{ id: 1, below_gate: 0 }] }).runs[0].below_gate).toBe(0)
  })

  test('a bare array of runs is accepted, and the ephemeral lists default to empty', () => {
    const page = normalizeEvalRuns([{ id: 1, status: 'completed' }])
    expect(page.runs).toHaveLength(1)
    expect(page.launches).toEqual([])
    expect(page.stale_running_run_ids).toEqual([])
  })
})

describe('normalizeEvalRun (9.10)', () => {
  test('metrics come back whole and criteria are read as objects', () => {
    const detail = normalizeEvalRun(
      {
        id: 114,
        status: 'completed',
        config: { stages: '1..12', themes: 'all', n: 5 },
        metrics: { decodability: { mean: 0.991 }, by_stage: { '1': { attempted: 15 } } },
        criteria: [
          { name: 'every story reaches >=95% decodability', ok: true, evidence: '180/180' },
        ],
        items: [{ id: 1, story_id: 42, metrics: { decodability: 1 } }],
        items_total: 180,
      },
      114,
    )
    expect((detail.metrics.decodability as { mean: number }).mean).toBe(0.991)
    expect(detail.criteria[0].ok).toBe(true)
    expect(detail.criteria[0].evidence).toBe('180/180')
    expect(detail.items_total).toBe(180)
    expect(detail.items[0].story_id).toBe(42)
  })

  test('a criteria TUPLE is read too — `criteria_verdict` returns tuples', () => {
    // `eval.criteria_verdict` -> list[tuple[str, bool, str]]. A JSON encoding of
    // that is an array, not an object, and the portal must not disagree with
    // `make eval`'s exit code because of an encoding detail.
    const detail = normalizeEvalRun({ criteria: [['p95 rewrite depth <= 2', false, 'p95 = 3']] }, 1)
    expect(detail.criteria[0]).toEqual({
      name: 'p95 rewrite depth <= 2',
      ok: false,
      evidence: 'p95 = 3',
    })
  })

  test('a nested `run` object is read, and a NULL story_id survives as null', () => {
    // 9.10/§14: `story_id` is nullable on purpose — an api_fail cell has a
    // latency, an outcome and a cost, and no story. Coercing it to 0 would
    // invent story 0.
    const detail = normalizeEvalRun(
      { run: { id: 3, status: 'failed', metrics: { a: 1 } }, items: [{ id: 5, story_id: null }] },
      3,
    )
    expect(detail.status).toBe('failed')
    expect(detail.metrics.a).toBe(1)
    expect(detail.items[0].story_id).toBe(null)
  })

  test('an empty response still identifies the run the URL asked for', () => {
    const detail = normalizeEvalRun({}, 42)
    expect(detail.id).toBe(42)
    expect(detail.items).toEqual([])
    expect(detail.criteria).toEqual([])
  })
})

describe('normalizeEstimate (9.4, 9.34)', () => {
  /**
   * The LIVE estimate, copied from `POST /api/admin/eval-runs` with no
   * `budget_usd` — the request 9.4 guarantees makes ZERO model calls. It is
   * unusually self-describing: three cost scenarios rather than one number, the
   * last real matrix as a reference, and a sentence naming the budget window it
   * will accept. All of it is rendered as the server sent it (DEVIATIONS 9.34).
   */
  const live = {
    stages: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    themes: ['animals', 'dragons', 'space'],
    n: 5,
    effort: 'medium',
    concurrency: 1,
    cells_planned: 180,
    pricing_date: '2026-07-30',
    projection: {
      models: { generation: 'claude-sonnet-5', moderation: 'claude-haiku-4-5' },
      pricing_date: '2026-07-30',
      no_rewrites_usd: 3.247,
      with_one_rewrite_each_usd: 6.4939,
      worst_case_all_budget_exhausted_usd: 12.9878,
      assumed_input_tokens: 335545,
      assumed_output_tokens: 273600,
    },
    reference_run: { id: 114, cost_usd: 5.412963, wall_clock_s: 1068.2, accepted: 172 },
    budget: {
      per_request_ceiling_usd: 8.0,
      process_ceiling_usd: 16.0,
      process_remaining_usd: 16.0,
      stale_running_run_ids: [112],
    },
    gate: 0.95,
    mode: 'estimate',
    launched: false,
    requires: { budget_usd: '> 6.4939 (the one-rewrite-each projection) and <= 8.0', confirm: true },
    detail:
      'No API call was made. Send the same body again with `confirm: true` and a `budget_usd` between $6.49 and $8.00 to start the run.',
  }

  test('the LIVE estimate is read, and `launched` is false', () => {
    const est = normalizeEstimate(live)
    expect(est.mode).toBe('estimate')
    expect(est.launched).toBe(false)
    expect(est.cells_planned).toBe(180)
    expect(est.stages).toHaveLength(12)
    expect(est.themes).toEqual(['animals', 'dragons', 'space'])
    expect(est.n).toBe(5)
    // Three scenarios, not one. Showing only the middle would understate what an
    // operator is being asked to authorise.
    expect(est.projection.no_rewrites_usd).toBe(3.247)
    expect(est.projection.with_one_rewrite_each_usd).toBe(6.4939)
    expect(est.projection.worst_case_all_budget_exhausted_usd).toBe(12.9878)
    expect(est.reference_run?.id).toBe(114)
    expect(est.budget.per_request_ceiling_usd).toBe(8)
    // The server's own sentence, which carries the exact window the 422 checks.
    expect(est.detail).toContain('No API call was made')
    expect(est.requires?.confirm).toBe(true)
  })

  test('a launch response is the same envelope with `launched: true`', () => {
    // Read by the same normaliser, so the screen shows the server's own `detail`
    // rather than composing a success sentence it was never sent.
    const launched = normalizeEstimate({
      ...live,
      mode: 'launch',
      launched: true,
      launch_id: 'lch_7f2a',
      run_id: 115,
      detail: 'Run launched. Poll GET /api/admin/eval-runs.',
    })
    expect(launched.launched).toBe(true)
    // `"launch"`, not `"launched"` — the server's word. The boolean beside it is
    // `launched`, and a branch on the wrong one of the two never fires.
    expect(launched.mode).toBe('launch')
    expect(launched.launch_id).toBe('lch_7f2a')
    expect(launched.run_id).toBe(115)
  })

  test('an unprojected scenario stays undefined, so the row says so instead of $0.00', () => {
    const est = normalizeEstimate({ cells_planned: 4, projection: {} })
    expect(est.projection.no_rewrites_usd).toBe(undefined)
    expect(est.cells_planned).toBe(4)
  })

  test('the raw body is kept, so a field this file cannot name is still showable', () => {
    const est = normalizeEstimate({ cells_planned: 4, something_new: true })
    expect(est.raw.something_new).toBe(true)
  })
})

describe('normalizeColdGenJob and rewriteCatches (9.13, 9.18, 9.32)', () => {
  /**
   * The LIVE snapshot shape, read off `ColdJob.snapshot()` in
   * `services/api/storyforge/api/admin.py`. Three names here are corrections to
   * this file's first draft: the job's own state is `state` (not `status`), the
   * change counter is `revision` (not `seq`), and the chain is `transitions[]`
   * whose entries carry `node` (not `states[].state`). `detail` is where a span's
   * `output` lands, and `duration_ms` is the node's own time while `elapsed_ms` is
   * its offset from the start of the job.
   */
  const snapshot = {
    job_id: 'cg_1a2b3c4d',
    state: 'done',
    stage_id: 5,
    theme: 'dragons',
    effort: 'medium',
    moderated: true,
    current_node: null,
    revision: 21,
    elapsed_ms: 31240.5,
    persisted: false,
    poll_after_ms: 750,
    langfuse: {
      enabled: true,
      trace_id: 'tr_abc',
      host: 'https://cloud.langfuse.com',
      url: null,
    },
    notes: ['Nothing is written to `stories` (DEVIATIONS 9.15)'],
    transitions: [
      {
        seq: 1,
        node: 'RECEIVED',
        source: 'job',
        status: 'done',
        attempt: null,
        elapsed_ms: 0.1,
        duration_ms: null,
        detail: { stage_id: 5, theme: 'dragons' },
        error: null,
      },
      {
        seq: 2,
        node: 'GENERATING',
        source: 'pipeline',
        status: 'done',
        attempt: 1,
        elapsed_ms: 120.4,
        duration_ms: 4100.2,
        detail: { output: { words: 62 } },
        error: null,
      },
      {
        seq: 3,
        node: 'VALIDATING',
        source: 'pipeline',
        status: 'done',
        attempt: 1,
        elapsed_ms: 4230.6,
        duration_ms: 6.1,
        detail: {
          output: { decodability: 0.912, passes_gate: false, violations: ['gleaming'] },
        },
        error: null,
      },
      {
        seq: 4,
        node: 'REWRITING',
        source: 'pipeline',
        status: 'done',
        attempt: 1,
        elapsed_ms: 4240.0,
        duration_ms: 2300.9,
        detail: { output: { text: 'The shiny dragon sat.' } },
        error: null,
      },
    ],
    /**
     * §17 step 4's diff, computed by `admin.py::_rewrite_loop` from
     * `PipelineResult.attempts` — NOT by this client (DEVIATIONS 9.32).
     */
    result: {
      outcome: 'accepted',
      passed: true,
      title: 'The Shiny Dragon',
      text: 'The shiny dragon sat.',
      decodability: 0.986,
      gate: 0.95,
      meets_gate: true,
      counts: { decodable: 4, sight: 1 },
      violations: [],
      rewrite_depth: 1,
      max_rewrites: 3,
      attempts: 2,
      story_words: ['dragon'],
      moderation: { approved: true, layer: 'model', reason: '' },
      cost_usd: 0.0315,
      latency_ms: { total: 31240.5, generating: 4100.2 },
      rewrite_loop: [
        {
          attempt: 1,
          kind: 'generate',
          decodability: 0.912,
          violations: ['gleaming'],
          latency_ms: 4100.2,
          text: 'The gleaming dragon sat.',
          caught: [],
          fixed: [],
          still_present: [],
          new_words: [],
        },
        {
          attempt: 2,
          kind: 'rewrite',
          decodability: 0.986,
          violations: [],
          latency_ms: 2300.9,
          text: 'The shiny dragon sat.',
          caught: ['gleaming'],
          fixed: ['gleaming'],
          still_present: [],
          new_words: ['shiny'],
        },
      ],
    },
  }

  test('the LIVE snapshot is read, including the trace id 9.18 publishes early', () => {
    const job = normalizeColdGenJob(snapshot)
    expect(job.job_id).toBe('cg_1a2b3c4d')
    expect(job.state).toBe('done')
    expect(job.revision).toBe(21)
    expect(job.transitions).toHaveLength(4)
    expect(job.poll_after_ms).toBe(750)
    expect(job.persisted).toBe(false)
    expect(job.langfuse.trace_id).toBe('tr_abc')
    expect(job.langfuse.enabled).toBe(true)
    expect(job.langfuse.host).toBe('https://cloud.langfuse.com')
    // 9.18 refuses to invent a console URL, and the client must not either.
    expect(job.langfuse.url).toBe(null)
  })

  test('a transition keeps BOTH clocks and its source', () => {
    const job = normalizeColdGenJob(snapshot)
    const gen = job.transitions[1]
    // `duration_ms` is the node's own time; `elapsed_ms` is its offset from the
    // job's start. Conflating them would print a 4-second RECEIVED pill.
    expect(gen.duration_ms).toBeCloseTo(4100.2, 1)
    expect(gen.elapsed_ms).toBeCloseTo(120.4, 1)
    expect(gen.source).toBe('pipeline')
    // RECEIVED is a fact about the endpoint, not a §7 span, and says so.
    expect(job.transitions[0].source).toBe('job')
    expect(job.transitions[0].duration_ms).toBe(null)
  })

  test('the VALIDATING span carries the gate refusal the demo is about', () => {
    const job = normalizeColdGenJob(snapshot)
    const output = job.transitions[2].detail.output as Record<string, unknown>
    expect(output.passes_gate).toBe(false)
    expect(output.decodability).toBe(0.912)
  })

  test('node names are upper-cased so a pill map cannot miss on case alone', () => {
    expect(normalizeColdGenJob({ transitions: [{ node: 'generating' }] }).transitions[0].node).toBe(
      'GENERATING',
    )
    // And the drafted `nodes[].state` spelling still resolves.
    expect(normalizeColdGenJob({ nodes: [{ state: 'moderating' }] }).transitions[0].node).toBe(
      'MODERATING',
    )
  })

  test('the rewrite CATCH is the server’s diff, filtered — not a client-side diff', () => {
    const catches = rewriteCatches(normalizeColdGenJob(snapshot))
    // Attempt 1 caught nothing (there was no previous attempt); attempt 2 is the
    // catch. The filter is the whole of this client's contribution (9.32).
    expect(catches).toHaveLength(1)
    expect(catches[0].attempt).toBe(2)
    expect(catches[0].kind).toBe('rewrite')
    expect(catches[0].caught).toEqual(['gleaming'])
    expect(catches[0].fixed).toEqual(['gleaming'])
    expect(catches[0].still_present).toEqual([])
    expect(catches[0].new_words).toEqual(['shiny'])
    expect(catches[0].text).toBe('The shiny dragon sat.')
    expect(catches[0].decodability).toBe(0.986)
  })

  test('a first-pass story has no catches, and that is not an error', () => {
    const clean = normalizeColdGenJob({
      state: 'done',
      result: {
        outcome: 'accepted',
        passed: true,
        rewrite_loop: [
          { attempt: 1, kind: 'generate', decodability: 1, text: 'Sam sat.', caught: [] },
        ],
      },
    })
    expect(rewriteCatches(clean)).toEqual([])
    expect(clean.result?.rewrite_depth).toBe(null)
  })

  test('a job with no result yet reads as null rather than as an empty story', () => {
    const running = normalizeColdGenJob({ job_id: 'cg_x', state: 'running', result: null })
    expect(running.result).toBe(null)
    expect(running.state).toBe('running')
  })

  test('admin.py’s terminal states stop the poll; the in-flight ones do not', () => {
    expect(isTerminal('done')).toBe(true)
    expect(isTerminal('error')).toBe(true)
    expect(isTerminal('DONE')).toBe(true)
    expect(isTerminal('queued')).toBe(false)
    expect(isTerminal('running')).toBe(false)
    expect(isTerminal(undefined)).toBe(false)
  })
})

describe('transitionVerdict — `status: done` is not `approved` (a real browser bug)', () => {
  /**
   * Every fixture here is copied from the raw snapshot of `cg_030512bb`, a REAL
   * stage-10 cold generation run through this screen: the validator refused the
   * first draft at 93.8%, the rewrite fixed it to 100%, and MODERATING then
   * rejected the rewrite as ungrammatical. Every one of those nodes reported
   * `status: "done"`, which is what painted a green `MODERATING rejected` and a
   * green `OUTCOME moderation_fail` on screen before this function existed.
   */
  const t = (node: string, status: string, detail: Record<string, unknown>): ColdGenTransition => ({
    seq: 1,
    node,
    source: node === 'RECEIVED' || node === 'OUTCOME' ? 'job' : 'pipeline',
    status,
    attempt: null,
    elapsed_ms: 0,
    duration_ms: 0,
    detail,
    error: null,
  })

  test('a node that RAN but refused reads as false, not as its `done` status', () => {
    // VALIDATING, first draft: `passes_gate: false` at 93.8%.
    expect(
      transitionVerdict(
        t('VALIDATING', 'done', {
          output: { decodability: 0.9375, passes_gate: false, violations: ['liked', 'opened'] },
        }),
      ),
    ).toBe(false)

    // MODERATING, after the rewrite reached 100%: `approved: false`. This is the
    // one the browser painted green.
    expect(
      transitionVerdict(
        t('MODERATING', 'done', {
          output: {
            approved: false,
            layer: 'model',
            reason: 'ungrammatical: "It like to fly up and away." like -> likes',
            well_formed: false,
          },
        }),
      ),
    ).toBe(false)

    // The job's terminal marker. §7's taxonomy: `accepted` is the only success.
    expect(transitionVerdict(t('OUTCOME', 'done', { outcome: 'moderation_fail' }))).toBe(false)
    expect(transitionVerdict(t('OUTCOME', 'done', { outcome: 'api_fail' }))).toBe(false)
    expect(transitionVerdict(t('OUTCOME', 'done', { outcome: 'budget_exhausted' }))).toBe(false)
  })

  test('POSITIVE CONTROL: an approving node reads as true, so the pill is not always red', () => {
    // Without this, `return false` would satisfy every assertion above and paint
    // the whole chain berry — the same bug in the other direction. Fixtures from
    // `cg_3169a9fa`, the clean stage-10 run.
    expect(
      transitionVerdict(t('VALIDATING', 'done', { output: { decodability: 0.966, passes_gate: true } })),
    ).toBe(true)
    expect(
      transitionVerdict(t('MODERATING', 'done', { output: { approved: true, layer: 'model' } })),
    ).toBe(true)
    expect(transitionVerdict(t('OUTCOME', 'done', { outcome: 'accepted' }))).toBe(true)
  })

  test('POSITIVE CONTROL: a node that judges NOTHING reports null, not a verdict', () => {
    // RECEIVED, GENERATING and REWRITING produce text; they do not judge it.
    // Returning `false` for them would paint three berry pills on a perfect run,
    // and returning `true` would paint the failures green again.
    expect(transitionVerdict(t('RECEIVED', 'done', { stage_id: 10, theme: 'dragons' }))).toBe(null)
    expect(
      transitionVerdict(t('GENERATING', 'done', { output: { title: 'The Dragon', text: 'A dragon…' } })),
    ).toBe(null)
    expect(
      transitionVerdict(t('REWRITING', 'done', { output: { text: 'A dragon lived in a cave.' } })),
    ).toBe(null)
    expect(transitionVerdict(t('MODERATING', 'running', {}))).toBe(null)
  })

  test('the gate verdict wins over the moderation one when a span carries both', () => {
    // Not observed, but the order has to be defined somewhere rather than depend
    // on key order in a dict: the gate is the guarantee (STATE invariant 1) and
    // moderation is belt-and-suspenders on top of it.
    expect(
      transitionVerdict(t('VALIDATING', 'done', { output: { passes_gate: false, approved: true } })),
    ).toBe(false)
  })
})

describe('pickArray — the reader every seam is built on', () => {
  test('the first key holding an array wins, and a scalar never throws', () => {
    expect(pickArray({ b: [1], a: 'x' }, ['a', 'b'])).toEqual([1])
    expect(pickArray({ a: 'not an array' }, ['a'])).toEqual([])
    expect(pickArray(null, ['a'])).toEqual([])
    expect(pickArray(7, ['a'])).toEqual([])
    expect(pickArray('s', ['a'])).toEqual([])
  })
})
