/**
 * `/admin/evals/:runId` — one eval run's metrics (build doc §14, §11's "metrics
 * tables from `eval_runs`", §17 step 5; style guide §09).
 *
 * §17 step 5 is one sentence long and it is the whole brief for this screen:
 * "decodability table across 12 stages, cost, latency. 'The guarantee is tested,
 * not asserted.'"
 *
 * **What is deliberately not rendered.** `eval_runs.metrics` is §14's entire
 * aggregate — `by_stage`, `by_theme`, per-stage bins, `below_gate_detail`, three
 * moderation reject lists, a token census, model provenance. Dumping it would be a
 * screen nobody reads. This shows the headline aggregates, the per-stage table, the
 * §1 criteria verdict, the below-gate rows when there are any, and then the raw
 * JSON behind a disclosure — labelled as a dump rather than dressed up as a report
 * (DEVIATIONS 9.33).
 *
 * **The criteria come from the server.** 9.10 computes them by calling
 * `eval.criteria_verdict` on the stored metrics, so this screen cannot disagree
 * with `make eval`'s exit code. §14: "the portal is a view."
 */

import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchEvalRun, type AdminResult, type EvalRunDetail } from '../lib/adminApi'
import { count, elapsed, pct } from '../lib/validatorView'
import AdminShell, { AdminError, AdminLabel, AdminMetric, AdminPanel } from '../ui/AdminShell'
import { StatusWord, Td, Th, usd, when } from './AdminEvalRuns'

/** §1/§7's gate. Read from the metrics when the run recorded it, else the spec. */
const DEFAULT_GATE = 0.95

export default function AdminEvalRun() {
  const { runId } = useParams()
  const id = Number(runId)
  const [detail, setDetail] = useState<AdminResult<EvalRunDetail> | null>(null)

  const load = useCallback(() => {
    let live = true
    setDetail(null)
    void fetchEvalRun(id, { limit: 50 }).then((r) => {
      if (live) setDetail(r)
    })
    return () => {
      live = false
    }
  }, [id])

  useEffect(() => {
    if (!Number.isInteger(id) || id <= 0) return
    return load()
  }, [id, load])

  if (!Number.isInteger(id) || id <= 0) {
    return (
      <AdminShell title="Eval run" subtitle="GET /api/admin/eval-runs/{id}">
        <AdminError
          status={400}
          detail={`"${runId}" is not a run id`}
          where="client"
        />
      </AdminShell>
    )
  }

  const data = detail?.ok ? detail.data : null
  const m = data?.metrics ?? {}

  return (
    <AdminShell
      title={`Eval run ${id}`}
      subtitle={
        <>
          GET /api/admin/eval-runs/{id} · §14 · `eval_runs.metrics` verbatim ·{' '}
          <Link to="/admin/evals" className="underline underline-offset-4">
            back to the list
          </Link>
        </>
      }
      meta={
        data ? (
          <>
            <StatusWord status={data.status} /> · {when(data.started_at)}
            {data.finished_at ? ` → ${when(data.finished_at)}` : ' · not finished'}
          </>
        ) : null
      }
    >
      {detail === null ? (
        <p role="status" className="font-mono text-[12px] uppercase tracking-[0.14em] text-ink-500">
          loading…
        </p>
      ) : !detail.ok ? (
        <AdminError
          status={detail.status}
          detail={detail.detail}
          where={detail.where}
          retry={load}
        />
      ) : (
        <div className="flex flex-col gap-6">
          <Headline metrics={m} />
          <Criteria criteria={detail.data.criteria} />
          <ByStage metrics={m} />
          <BelowGate metrics={m} />
          <Moderation metrics={m} />
          <Config config={detail.data.config} />
          <RawMetrics metrics={m} items={detail.data} />
        </div>
      )}
    </AdminShell>
  )
}

// --- readers over the metrics blob --------------------------------------------
//
// `metrics` is `Record<string, unknown>` on purpose: it is `eval_runs.metrics`
// exactly as the harness wrote it (9.10), and the harness owns its shape. So this
// screen reads it defensively and prints "—" for anything absent, rather than
// declaring a nested interface that would go stale the next time §14 grows a
// field and would render `undefined` when it did.

function dig(obj: unknown, path: string): unknown {
  let cur: unknown = obj
  for (const key of path.split('.')) {
    if (cur === null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[key]
  }
  return cur
}

function n(obj: unknown, path: string): number | undefined {
  const v = dig(obj, path)
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

function showPct(v: number | undefined): string {
  return v === undefined ? '—' : pct(v)
}

// --- §17 step 5's headline ----------------------------------------------------

function Headline({ metrics }: { metrics: Record<string, unknown> }) {
  const meanDec = n(metrics, 'decodability.mean')
  const belowGate = n(metrics, 'decodability.below_gate')
  const gate = n(metrics, 'config.decodability_gate') ?? DEFAULT_GATE

  return (
    <section>
      <AdminLabel>
        the guarantee is tested, not asserted — §1 item 2, over the whole matrix
      </AdminLabel>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <AdminMetric
          label="mean decodability"
          value={showPct(meanDec)}
          tone={meanDec !== undefined && meanDec >= gate ? 'good' : 'bad'}
        >
          <span className="text-ink-500">
            min {showPct(n(metrics, 'decodability.min'))} · p05{' '}
            {showPct(n(metrics, 'decodability.p05'))}
          </span>
        </AdminMetric>

        <AdminMetric
          label="stories below the gate"
          value={belowGate === undefined ? '—' : count(belowGate)}
          tone={belowGate === 0 ? 'good' : 'bad'}
        >
          <span className="text-ink-500">
            gate {pct(gate, 0)} · {count(n(metrics, 'accepted') ?? 0)} accepted of{' '}
            {count(n(metrics, 'attempted') ?? 0)} attempted
          </span>
        </AdminMetric>

        <AdminMetric label="p95 rewrite depth" value={fmt(n(metrics, 'rewrite_depth.p95'))}>
          <span className="text-ink-500">
            max {fmt(n(metrics, 'rewrite_depth.max'))} · mean{' '}
            {fmt(round(n(metrics, 'rewrite_depth.mean'), 2))}
          </span>
        </AdminMetric>

        <AdminMetric label="p95 latency" value={elapsed(n(metrics, 'latency_ms.total_p95')) || '—'}>
          <span className="text-ink-500">
            p50 {elapsed(n(metrics, 'latency_ms.total_p50')) || '—'} · generating p95{' '}
            {elapsed(n(metrics, 'latency_ms.generating_p95')) || '—'}
          </span>
        </AdminMetric>

        <AdminMetric label="total cost" value={money(n(metrics, 'cost.total_usd'))}>
          <span className="text-ink-500">
            {money(n(metrics, 'cost.per_accepted_usd'))} per accepted story · priced{' '}
            {String(dig(metrics, 'cost.pricing_date') ?? '—')}
          </span>
        </AdminMetric>

        <AdminMetric
          label="fallback rate"
          value={showPct(n(metrics, 'fallback_rate'))}
          tone={(n(metrics, 'fallback_rate') ?? 0) > 0 ? 'bad' : 'good'}
        >
          <span className="text-ink-500">
            §7: every failure terminates in a warm-pool serve, so a child sees a story either way
          </span>
        </AdminMetric>

        <AdminMetric label="gate pass rate" value={showPct(n(metrics, 'gate_pass_rate'))}>
          <span className="text-ink-500">over ATTEMPTED cells — a cell with no text counts</span>
        </AdminMetric>

        <AdminMetric label="oov words" value={count(n(metrics, 'oov.total') ?? 0)}>
          <span className="text-ink-500">
            in {count(n(metrics, 'oov.stories_with_oov') ?? 0)} stories · §5.3: OOV fails, period
          </span>
        </AdminMetric>
      </div>
    </section>
  )
}

// --- §1's criteria, as the server computed them --------------------------------

function Criteria({ criteria }: { criteria: EvalRunDetail['criteria'] }) {
  if (!criteria.length) return null
  return (
    <AdminPanel>
      <AdminLabel>§1 acceptance criteria · computed by `eval.criteria_verdict`, not here</AdminLabel>
      <ul className="flex flex-col gap-2">
        {criteria.map((c, i) => (
          <li key={i} className="flex flex-wrap items-baseline gap-x-3 font-mono text-[13px]">
            {/* §11: never colour alone — glyph, word and hue all say it. */}
            <span
              className={
                c.ok
                  ? 'text-leaf-700'
                  : 'text-berry-700'
              }
            >
              {c.ok ? '✓ met' : '✗ not met'}
            </span>
            <span className="text-ink-900">{c.name}</span>
            <span className="text-ink-500">{c.evidence}</span>
          </li>
        ))}
      </ul>
    </AdminPanel>
  )
}

// --- §17's per-stage table ----------------------------------------------------

function ByStage({ metrics }: { metrics: Record<string, unknown> }) {
  const raw = dig(metrics, 'by_stage')
  if (raw === null || typeof raw !== 'object') return null
  const rows = Object.entries(raw as Record<string, unknown>).sort(
    (a, b) => Number(a[0]) - Number(b[0]),
  )
  if (!rows.length) return null
  const gate = n(metrics, 'config.decodability_gate') ?? DEFAULT_GATE

  return (
    <AdminPanel>
      <AdminLabel>decodability across the stages · §17 step 5's table</AdminLabel>
      <div className="overflow-x-auto rounded-[4px] border border-paper-shade">
        <table className="w-full border-collapse font-mono text-[13px]">
          <caption className="sr-only">Per-stage aggregates for this run</caption>
          <thead>
            <tr className="bg-paper-warm">
              <Th>stage</Th>
              <Th align="right">cells</Th>
              <Th align="right">accepted</Th>
              <Th align="right">mean dec.</Th>
              <Th align="right">min dec.</Th>
              <Th align="right">below gate</Th>
              <Th align="right">p95 rewrites</Th>
              <Th align="right">p95 latency</Th>
              <Th align="right">words</Th>
              <Th align="right">cost</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([stage, slice], i) => {
              const mean = n(slice, 'decodability.mean')
              const below = n(slice, 'below_gate')
              return (
                <tr key={stage} className={i % 2 ? 'bg-paper-card' : 'bg-transparent'}>
                  <Td>{stage}</Td>
                  <Td align="right">{count(n(slice, 'attempted') ?? 0)}</Td>
                  <Td align="right">{count(n(slice, 'accepted') ?? 0)}</Td>
                  {/* §09: "gold = below 95% gate". The number and the tint agree. */}
                  <Td align="right">
                    <span
                      className={
                        mean === undefined
                          ? ''
                          : mean >= gate
                            ? 'text-leaf-700'
                            : 'text-gold-700'
                      }
                    >
                      {showPct(mean)}
                    </span>
                  </Td>
                  <Td align="right">{showPct(n(slice, 'decodability.min'))}</Td>
                  <Td align="right">
                    <span className={below ? 'text-berry-700' : 'text-ink-700'}>
                      {below === undefined ? '—' : count(below)}
                    </span>
                  </Td>
                  <Td align="right">{fmt(n(slice, 'rewrite_depth.p95'))}</Td>
                  <Td align="right">{elapsed(n(slice, 'latency_ms.p95')) || '—'}</Td>
                  <Td align="right">{fmt(n(slice, 'word_count_mean'))}</Td>
                  <Td align="right">{money(n(slice, 'cost_usd'))}</Td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-3 font-mono text-[11px] text-ink-500">
        one row per stage · gold marks a mean below the {pct(gate, 0)} gate
      </p>
    </AdminPanel>
  )
}

// --- the rows that failed, which are the interesting ones ----------------------

function BelowGate({ metrics }: { metrics: Record<string, unknown> }) {
  const rows = dig(metrics, 'decodability.below_gate_detail')
  if (!Array.isArray(rows) || !rows.length) return null

  return (
    <AdminPanel>
      <AdminLabel>every story below the gate, with the words that put it there</AdminLabel>
      <ul className="flex flex-col gap-3 font-mono text-[12px]">
        {rows.map((r, i) => {
          const row = (r ?? {}) as Record<string, unknown>
          const violations = Array.isArray(row.violation_words) ? row.violation_words : []
          const missing = Array.isArray(row.missing_gpcs) ? row.missing_gpcs : []
          return (
            <li key={i} className="text-ink-700">
              <span className="text-ink-900">
                stage {String(row.stage_id ?? '?')} · {String(row.theme ?? '?')}
              </span>{' '}
              — {showPct(typeof row.decodability === 'number' ? row.decodability : undefined)} after{' '}
              {String(row.rewrite_depth ?? '?')} rewrite(s)
              {violations.length ? (
                <div className="text-berry-700">violations: {violations.join(', ')}</div>
              ) : null}
              {missing.length ? (
                <div className="text-ink-500">missing: {missing.join(', ')}</div>
              ) : null}
            </li>
          )
        })}
      </ul>
    </AdminPanel>
  )
}

function Moderation({ metrics }: { metrics: Record<string, unknown> }) {
  const classes = dig(metrics, 'moderation.classes')
  if (classes === null || typeof classes !== 'object') return null
  const entries = Object.entries(classes as Record<string, unknown>)
  if (!entries.length) return null

  const rejects = (['content_rejects', 'grammar_rejects', 'blocklist_rejects'] as const).flatMap(
    (key) => {
      const list = dig(metrics, `moderation.${key}`)
      return Array.isArray(list) ? list.map((r) => ({ kind: key, row: r })) : []
    },
  )

  return (
    <AdminPanel>
      <AdminLabel>moderation · §13's third layer, reported not gated (DEVIATIONS 7.49)</AdminLabel>
      <p className="font-mono text-[12px] text-ink-700">
        {entries.map(([k, v]) => `${k} ${String(v)}`).join(' · ')}
      </p>
      {rejects.length ? (
        <ul className="mt-3 flex flex-col gap-2 font-mono text-[12px] text-ink-700">
          {rejects.map(({ kind, row }, i) => {
            const r = (row ?? {}) as Record<string, unknown>
            return (
              <li key={i}>
                <span className="text-berry-700">{kind.replace('_rejects', '')}</span> · stage{' '}
                {String(r.stage_id ?? '?')} · {String(r.theme ?? '?')} —{' '}
                {String(r.reason ?? r.grammar_note ?? (r.blocked_words as string[])?.join(', ') ?? '')}
              </li>
            )
          })}
        </ul>
      ) : null}
    </AdminPanel>
  )
}

/**
 * The run's own configuration.
 *
 * DEVIATIONS 8.30/7.51 are about provenance: which models produced a matrix, at
 * what pricing date, under which timeout, against which vocabulary. A metrics
 * table with no provenance beside it is a number without a claim attached, so the
 * config travels with it.
 */
function Config({ config }: { config: Record<string, unknown> }) {
  const entries = Object.entries(config)
  if (!entries.length) return null
  return (
    <AdminPanel>
      <AdminLabel>run configuration · what produced these numbers</AdminLabel>
      <dl className="grid gap-x-6 gap-y-1 font-mono text-[12px] md:grid-cols-[auto_1fr]">
        {entries.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-ink-500">{k}</dt>
            <dd className="break-words text-ink-700">
              {typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)}
            </dd>
          </div>
        ))}
      </dl>
    </AdminPanel>
  )
}

/**
 * The dump, labelled as one.
 *
 * §14's aggregate has more in it than any screen should render, and pretending
 * otherwise means either an unreadable page or a quiet omission. A `<details>`
 * that says "raw" is the honest third option — and it is the only thing on this
 * screen that can never be out of date with the harness.
 */
function RawMetrics({
  metrics,
  items,
}: {
  metrics: Record<string, unknown>
  items: EvalRunDetail
}) {
  return (
    <details className="rounded-[4px] border border-paper-shade bg-paper-card p-4">
      <summary className="min-h-tap cursor-pointer font-mono text-[12px] uppercase tracking-[0.12em] text-ink-500">
        raw `eval_runs.metrics` and the first {items.items.length} of {count(items.items_total)}{' '}
        `eval_run_items` — a dump, not a report
      </summary>
      <pre className="mt-3 max-h-[420px] overflow-auto rounded-[4px] bg-paper-page p-3 font-mono text-[11px] leading-[1.5] text-ink-700">
        {JSON.stringify(metrics, null, 2)}
      </pre>
      <pre className="mt-3 max-h-[420px] overflow-auto rounded-[4px] bg-paper-page p-3 font-mono text-[11px] leading-[1.5] text-ink-700">
        {JSON.stringify(items.items, null, 2)}
      </pre>
    </details>
  )
}

// --- tiny formatters ----------------------------------------------------------

function fmt(v: number | undefined): string {
  return v === undefined ? '—' : String(v)
}

function round(v: number | undefined, digits: number): number | undefined {
  return v === undefined ? undefined : Number(v.toFixed(digits))
}

function money(v: number | undefined): string {
  return v === undefined ? '—' : usd(v)
}
