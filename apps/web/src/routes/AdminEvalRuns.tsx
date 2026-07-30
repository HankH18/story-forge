/**
 * `/admin/evals` — the eval-run list, and the launch control (build doc §14, §11's
 * "Admin portal", §17 step 5; style guide §09).
 *
 * §14 is explicit about what this screen is: "The admin portal reads the same
 * tables — no duplicated logic (CLI stays canonical; portal is a view)." So every
 * number here is `eval_runs.metrics`, projected by the server (DEVIATIONS 9.3),
 * and nothing on this page recomputes an aggregate.
 *
 * **Launching a run spends real money, so it is a two-step confirmation and not a
 * button.** DEVIATIONS 9.4 made `POST /api/admin/eval-runs` estimate-by-default —
 * a request without `budget_usd`, or with `confirm` false, makes zero API calls
 * and returns the projection. This screen does not paper over that with one
 * control that posts twice: the estimate is requested and shown, and only then
 * does a second, differently-worded control post the launch. A full matrix is 180
 * cells, ~$5.41 and ~18 minutes (docs/EVAL.md, run 114); the ceilings are $8 per
 * run and $16 per process (9.5, 9.6) and both are printed next to the button
 * rather than discovered as a 422 (DEVIATIONS 9.34).
 */

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  fetchEvalRuns,
  normalizeEstimate,
  postEvalRun,
  type AdminResult,
  type EvalEstimate,
  type EvalLaunch,
  type EvalRunSummary,
  type EvalRunsPage,
} from '../lib/adminApi'
import { count, elapsed, pct } from '../lib/validatorView'
import AdminShell, { AdminError, AdminLabel, AdminPanel } from '../ui/AdminShell'

export default function AdminEvalRuns() {
  const [page, setPage] = useState<AdminResult<EvalRunsPage> | null>(null)

  const load = useCallback(() => {
    let live = true
    void fetchEvalRuns({ limit: 25 }).then((r) => {
      if (live) setPage(r)
    })
    return () => {
      live = false
    }
  }, [])

  useEffect(load, [load])

  const data = page?.ok ? page.data : null

  return (
    <AdminShell
      title="Eval runs"
      subtitle={<>GET /api/admin/eval-runs · §14 · the same tables `make eval` writes</>}
      meta={data ? <>{count(data.total)} runs</> : null}
    >
      {page === null ? (
        <p role="status" className="font-mono text-[12px] uppercase tracking-[0.14em] text-ink-500">
          loading…
        </p>
      ) : !page.ok ? (
        <AdminError status={page.status} detail={page.detail} where={page.where} retry={load} />
      ) : (
        <div className="flex flex-col gap-6">
          <AdminPanel>
            <AdminLabel>runs · newest first</AdminLabel>
            <RunTable runs={page.data.runs} />
            {page.data.stale_running_run_ids.length ? (
              <p className="mt-3 font-mono text-[12px] text-amber-700">
                stale `running` rows: {page.data.stale_running_run_ids.join(', ')} — a run
                interrupted before it finished. Reported, never blocking (9.7); `--resume` is the
                recovery path.
              </p>
            ) : null}
          </AdminPanel>

          {page.data.launches.length ? (
            <AdminPanel>
              <AdminLabel>launches from this API process · ephemeral (9.9)</AdminLabel>
              <LaunchList launches={page.data.launches} />
            </AdminPanel>
          ) : null}

          <LaunchControl budget={page.data.budget} busy={page.data.busy} onLaunched={load} />
        </div>
      )}
    </AdminShell>
  )
}

// --- the list ------------------------------------------------------------------

function RunTable({ runs }: { runs: EvalRunSummary[] }) {
  if (!runs.length) {
    return (
      <p className="font-mono text-[13px] text-ink-500">
        no `eval_runs` rows in this database. `make eval` writes them; `make eval-smoke` writes a
        small one.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto rounded-[4px] border border-paper-shade">
      <table className="w-full border-collapse font-mono text-[13px]">
        <caption className="sr-only">Eval runs, newest first</caption>
        <thead>
          <tr className="bg-paper-warm">
            <Th>run</Th>
            <Th>status</Th>
            <Th>§1</Th>
            <Th>started</Th>
            <Th align="right">cells</Th>
            <Th align="right">mean dec.</Th>
            <Th align="right">below gate</Th>
            <Th align="right">p95 rewrites</Th>
            <Th align="right">wall clock</Th>
            <Th align="right">cost</Th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r, i) => (
            <tr key={r.id} className={i % 2 ? 'bg-paper-card' : 'bg-transparent'}>
              <Td>
                <Link
                  to={`/admin/evals/${r.id}`}
                  className="inline-flex min-h-tap items-center text-ink-900 underline underline-offset-4"
                >
                  {r.id}
                </Link>
              </Td>
              <Td>
                <StatusWord status={r.status} />
              </Td>
              {/* The §1 verdict the server computed with `eval.criteria_verdict`
                  (9.10), so the list answers "did it pass" without opening the
                  detail — and answers it with `make eval`'s own arithmetic.
                  §11: never colour alone, so the glyph carries a word too. */}
              <Td>
                {r.criteria_passed === undefined ? (
                  '—'
                ) : r.criteria_passed ? (
                  <span className="text-leaf-700" title={criteriaTitle(r)}>
                    ✓ met
                  </span>
                ) : (
                  <span className="text-berry-700" title={criteriaTitle(r)}>
                    ✗ {r.criteria.filter((c) => !c.ok).length} unmet
                  </span>
                )}
              </Td>
              <Td>{when(r.started_at)}</Td>
              <Td align="right">
                {r.attempted === undefined ? '—' : count(r.attempted)}
                {r.accepted !== undefined ? ` · ${count(r.accepted)} ok` : ''}
              </Td>
              <Td align="right">
                {r.mean_decodability === undefined ? '—' : pct(r.mean_decodability)}
              </Td>
              {/* Zero below the gate is §1's whole claim, so it is coloured — and
                  the number says it too, which is what §11 requires. */}
              <Td align="right">
                {r.below_gate === undefined ? (
                  '—'
                ) : r.below_gate === 0 ? (
                  <span className="text-leaf-700">0</span>
                ) : (
                  <span className="text-berry-700">{count(r.below_gate)}</span>
                )}
              </Td>
              <Td align="right">
                {r.rewrite_p95 === undefined ? '—' : r.rewrite_p95}
                {r.rewrite_max !== undefined ? ` · max ${r.rewrite_max}` : ''}
              </Td>
              {/* Wall clock, not a p95 latency: the list row carries
                  `wall_clock_s` and no latency percentile, and the per-node
                  percentiles live on the detail where `metrics.latency_ms` is. */}
              <Td align="right">
                {r.wall_clock_s === undefined ? '—' : elapsed(r.wall_clock_s * 1000)}
              </Td>
              <Td align="right">{r.cost_usd === undefined ? '—' : usd(r.cost_usd)}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Every criterion and its evidence, for the `title` on the §1 cell. */
function criteriaTitle(r: EvalRunSummary): string {
  return r.criteria.map((c) => `${c.ok ? '✓' : '✗'} ${c.name} — ${c.evidence}`).join('\n')
}

function LaunchList({ launches }: { launches: EvalLaunch[] }) {
  return (
    <ul className="flex flex-col gap-2 font-mono text-[12px]">
      {launches.map((l, i) => (
        <li key={String(l.id ?? i)} className="flex flex-wrap gap-x-4 text-ink-700">
          <span className="text-ink-900">{String(l.id ?? `launch ${i + 1}`)}</span>
          <span>{String(l.status ?? 'unknown')}</span>
          {l.run_id ? (
            <Link to={`/admin/evals/${l.run_id}`} className="underline underline-offset-4">
              run {l.run_id}
            </Link>
          ) : (
            <span className="text-ink-500">run id not yet observed (9.8)</span>
          )}
          {l.budget_usd ? <span>budget {usd(l.budget_usd)}</span> : null}
          {l.error ? <span className="text-berry-700">{String(l.error)}</span> : null}
        </li>
      ))}
    </ul>
  )
}

// --- the launch control (9.4, 9.34) -------------------------------------------

function LaunchControl({
  budget,
  busy,
  onLaunched,
}: {
  budget: EvalRunsPage['budget']
  busy?: boolean
  onLaunched: () => void
}) {
  const [stages, setStages] = useState('1..11')
  const [themes, setThemes] = useState('all')
  const [n, setN] = useState(5)
  const [budgetUsd, setBudgetUsd] = useState('')

  const [estimate, setEstimate] = useState<EvalEstimate | null>(null)
  const [error, setError] = useState<{ status: number; detail: string; where: string } | null>(null)
  const [launched, setLaunched] = useState<string | null>(null)
  const [pending, setPending] = useState<'estimate' | 'launch' | null>(null)

  // 9.5's per-run ceiling and 9.6's per-process one. Shown next to the button
  // rather than discovered as a 422. Any name-tolerance lives in the normaliser —
  // a component reading two spellings of one field is the drift 9.37 is about.
  const maxBudget = budget.per_request_ceiling_usd
  const remaining = budget.process_remaining_usd

  /**
   * Step 1. No `confirm`, no `budget_usd` — 9.4 guarantees this makes zero API
   * calls. Any change to the matrix throws the estimate away, so a launch can
   * never echo a projection for a different matrix.
   */
  const runEstimate = useCallback(() => {
    setPending('estimate')
    setError(null)
    setLaunched(null)
    void postEvalRun({ stages, themes, n }).then((r) => {
      setPending(null)
      if (!r.ok) {
        setEstimate(null)
        setError({ status: r.status, detail: r.detail, where: r.where })
        return
      }
      setEstimate(normalizeEstimate(r.data))
    })
  }, [stages, themes, n])

  /** Step 2, and the ONLY place in this app that sets `confirm: true`. */
  const runLaunch = useCallback(() => {
    const amount = Number(budgetUsd)
    if (!Number.isFinite(amount) || amount <= 0) {
      setError({
        status: 0,
        detail: 'a budget in dollars is required to launch — 9.4 makes a run without one an estimate',
        where: 'client',
      })
      return
    }
    setPending('launch')
    setError(null)
    void postEvalRun({ stages, themes, n, budget_usd: amount, confirm: true }).then((r) => {
      setPending(null)
      if (!r.ok) {
        setError({ status: r.status, detail: r.detail, where: r.where })
        return
      }
      // The launch response is the same envelope with `mode: "launched"`, so it
      // is read by the same normaliser and its own `detail` is what is shown —
      // this screen does not compose a success sentence the server did not send.
      const launch = normalizeEstimate(r.data)
      setLaunched(
        launch.detail ??
          `${launch.mode} — the run executes inside the API process and writes its ` +
            '`eval_runs` row as it goes (9.8).',
      )
      setEstimate(null)
      onLaunched()
    })
  }, [budgetUsd, stages, themes, n, onLaunched])

  return (
    <AdminPanel>
      <AdminLabel>launch a run · this spends money</AdminLabel>

      <p className="mb-4 max-w-[90ch] text-[15px] leading-[1.6] text-ink-700">
        `make eval` is canonical (§14) and this is the same code path. A full matrix is 11 stages ×
        3 themes × 5 stories = 165 cells; run 114 (on the old twelve-stage ladder) measured{' '}
        <span className="font-mono">$5.41</span> and{' '}
        <span className="font-mono">17m49s</span>. The estimate below costs nothing —{' '}
        <span className="font-mono">POST</span> without a budget makes zero model calls.
      </p>

      <div className="mb-4 flex flex-wrap items-end gap-4">
        <Field label="stages">
          <input
            value={stages}
            onChange={(e) => {
              setStages(e.target.value)
              setEstimate(null)
            }}
            className="min-h-tap w-[130px] rounded-[4px] border border-ink-300 bg-paper-card px-3 font-mono text-[13px]"
          />
        </Field>
        <Field label="themes">
          <input
            value={themes}
            onChange={(e) => {
              setThemes(e.target.value)
              setEstimate(null)
            }}
            className="min-h-tap w-[130px] rounded-[4px] border border-ink-300 bg-paper-card px-3 font-mono text-[13px]"
          />
        </Field>
        <Field label="stories per cell">
          <input
            type="number"
            min={1}
            max={20}
            value={n}
            onChange={(e) => {
              setN(Number(e.target.value))
              setEstimate(null)
            }}
            className="min-h-tap w-[110px] rounded-[4px] border border-ink-300 bg-paper-card px-3 font-mono text-[13px]"
          />
        </Field>

        <button
          type="button"
          onClick={runEstimate}
          disabled={pending !== null}
          className="min-h-tap min-w-tap rounded-[4px] border border-ink-900 bg-paper-card px-5
                     font-mono text-[12px] uppercase tracking-[0.12em] text-ink-900
                     disabled:border-paper-shade disabled:text-ink-300"
        >
          {pending === 'estimate' ? 'estimating…' : 'estimate cost'}
        </button>
      </div>

      {estimate ? (
        <div className="mb-4 rounded-[4px] border border-paper-shade bg-paper-card p-4">
          <div className="font-mono text-[12px] text-ink-700">
            {count(estimate.cells_planned)} cells · {estimate.stages.length} stages ×{' '}
            {estimate.themes.length} themes × {estimate.n ?? '?'} · effort {estimate.effort ?? '?'}
            {estimate.concurrency ? ` · concurrency ${estimate.concurrency}` : ''}
          </div>

          {/* THREE scenarios, not one number. The server projects a no-rewrite
              floor, a one-rewrite-each middle and a worst case where every story
              exhausts its budget; showing only the middle would understate the
              thing an operator is being asked to authorise. */}
          <dl className="mt-3 grid gap-x-6 gap-y-1 font-mono text-[12px] sm:grid-cols-[auto_1fr]">
            <Projection label="no rewrites" value={estimate.projection.no_rewrites_usd} />
            <Projection
              label="one rewrite each"
              value={estimate.projection.with_one_rewrite_each_usd}
            />
            <Projection
              label="worst case"
              value={estimate.projection.worst_case_all_budget_exhausted_usd}
            />
            {estimate.projection.pricing_date ? (
              <>
                <dt className="text-ink-500">priced</dt>
                <dd className="text-ink-700">
                  {String(estimate.projection.pricing_date)}
                  {estimate.projection.models
                    ? ` · ${Object.values(estimate.projection.models).join(', ')}`
                    : ''}
                </dd>
              </>
            ) : null}
          </dl>

          {/* What the last real matrix actually cost. A projection beside a
              measurement is a different quality of claim from a projection alone. */}
          {estimate.reference_run ? (
            <p className="mt-3 font-mono text-[11px] text-ink-500">
              last real matrix — run {String(estimate.reference_run.id)} cost{' '}
              {usd(Number(estimate.reference_run.cost_usd) || 0)} in{' '}
              {elapsed((Number(estimate.reference_run.wall_clock_s) || 0) * 1000)} and accepted{' '}
              {String(estimate.reference_run.accepted)} stories
            </p>
          ) : null}

          <div className="mt-2 font-mono text-[11px] text-ink-500">
            ceilings —{' '}
            {maxBudget !== undefined
              ? `${usd(maxBudget)} per run (9.5)`
              : 'per-run ceiling not reported'}
            {remaining !== undefined ? ` · ${usd(remaining)} left in this API process (9.6)` : ''}
          </div>

          {/* The server's own sentence about what it needs, verbatim. It carries
              the exact budget window, so a UI that paraphrased it would be able to
              be wrong about the number the 422 is checked against. */}
          {estimate.detail ? (
            <p className="mt-3 max-w-[90ch] font-mono text-[12px] leading-[1.6] text-ink-900">
              {estimate.detail}
            </p>
          ) : null}
          {estimate.requires ? (
            <p className="mt-1 font-mono text-[11px] text-ink-500">
              requires ·{' '}
              {Object.entries(estimate.requires)
                .map(([k, v]) => `${k} ${String(v)}`)
                .join(' · ')}
            </p>
          ) : null}

          <div className="mt-4 flex flex-wrap items-end gap-3">
            <Field label="budget (usd) — required to launch">
              <input
                value={budgetUsd}
                onChange={(e) => setBudgetUsd(e.target.value)}
                placeholder={maxBudget !== undefined ? String(maxBudget) : '1.00'}
                className="min-h-tap w-[130px] rounded-[4px] border border-ink-300 bg-paper-card px-3 font-mono text-[13px]"
              />
            </Field>
            {/* Berry, and the only berry control on this surface: §02 reserves it
                for the primary action, and on an admin screen the action that
                spends money is the one that should be unmistakable. */}
            <button
              type="button"
              onClick={runLaunch}
              disabled={pending !== null || busy === true}
              className="min-h-tap min-w-tap rounded-[4px] border border-berry-700 bg-berry-500 px-5
                         font-mono text-[12px] uppercase tracking-[0.12em] text-paper-page
                         disabled:border-paper-shade disabled:bg-paper-warm disabled:text-ink-300"
            >
              {pending === 'launch'
                ? 'starting…'
                : busy
                  ? 'a run is already in flight'
                  : `spend up to ${budgetUsd ? usd(Number(budgetUsd) || 0) : 'this budget'} and run`}
            </button>
          </div>
        </div>
      ) : null}

      {launched ? (
        <p className="font-mono text-[12px] text-leaf-700">{launched}</p>
      ) : null}

      {error ? (
        <AdminError status={error.status} detail={error.detail} where={error.where} />
      ) : null}
    </AdminPanel>
  )
}

// --- shared table/format bits -------------------------------------------------

/** One row of the projection table. Absent scenarios say so rather than show 0. */
function Projection({ label, value }: { label: string; value?: number }) {
  return (
    <>
      <dt className="text-ink-500">{label}</dt>
      <dd className="text-ink-900">{value === undefined ? 'not projected' : usd(value)}</dd>
    </>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-500">
        {label}
      </span>
      {children}
    </label>
  )
}

export function Th({
  children,
  align = 'left',
}: {
  children: React.ReactNode
  align?: 'left' | 'right'
}) {
  return (
    <th
      scope="col"
      className={`whitespace-nowrap px-4 py-[10px] font-mono text-[11px] font-normal uppercase
                  tracking-[0.12em] text-ink-500 ${align === 'right' ? 'text-right' : 'text-left'}`}
    >
      {children}
    </th>
  )
}

export function Td({
  children,
  align = 'left',
}: {
  children: React.ReactNode
  align?: 'left' | 'right'
}) {
  return (
    <td
      className={`whitespace-nowrap border-t border-paper-rule px-4 py-[9px] text-ink-700
                  ${align === 'right' ? 'text-right' : 'text-left'}`}
    >
      {children}
    </td>
  )
}

/** §11: the status is a word, and the colour only reinforces it. */
export function StatusWord({ status }: { status: string }) {
  const s = status.toLowerCase()
  const tone =
    s === 'completed'
      ? 'text-leaf-700'
      : s === 'running'
        ? 'text-gold-700'
        : s === 'failed'
          ? 'text-berry-700'
          : 'text-ink-700'
  return <span className={tone}>{status}</span>
}

export function usd(v: number): string {
  return `$${v.toFixed(v < 1 ? 4 : 2)}`
}

/** An absolute timestamp, because an eval run is dated evidence, not news. */
export function when(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
