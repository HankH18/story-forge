/**
 * Parent dashboard — `/dashboard/:kidId` (build doc §11; style guide §08, §10).
 *
 * §10's order, kept: header, kid picker row, then the four stat cards, then the
 * GPC table, then recent readings.
 *
 * One block is added to that order — the promotion rule, between the cards and
 * the table. §8 builds an *interpretable* model on purpose ("Interpretable by
 * design — no BKT") and ships every input to its own verdict in the API
 * response: the thresholds, the counts, and a per-criterion reason. A dashboard
 * that renders only the outcome throws all of that away and becomes the opaque
 * score §08 forbids. The four stat cards cannot hold it — §08 gives "Next up"
 * one sentence — so the rule gets its own panel and the cards stay as specified.
 * Logged as a deviation.
 *
 * Everything on this screen is a measurement the API returned. There is no
 * derived "reading level", no streak, no badge, and no projection of when a
 * child will move up. §08: "no invented scores, no gamified streaks."
 */

import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  fetchKids,
  fetchProgress,
  type Kid,
  type Progress,
  type RecentReading,
  type Result,
} from '../lib/api'
import GpcTable from '../ui/GpcTable'
import KidPicker from '../ui/KidPicker'
import ParentShell, { ParentError } from '../ui/ParentShell'
import StatCard from '../ui/StatCard'

export default function ParentDashboard() {
  const { kidId } = useParams()
  const id = Number(kidId)

  const [progress, setProgress] = useState<Result<Progress> | null>(null)
  const [kids, setKids] = useState<Kid[]>([])

  const load = useCallback(() => {
    let live = true
    setProgress(null)
    void fetchProgress(id).then((r) => {
      if (live) setProgress(r)
    })
    // The switcher is secondary: if the kid list fails, the dashboard the parent
    // actually asked for still renders. Failing both because one failed would be
    // the tail wagging the dog.
    void fetchKids().then((r) => {
      if (live && r.ok) setKids(r.data)
    })
    return () => {
      live = false
    }
  }, [id])

  useEffect(load, [load])

  if (!Number.isFinite(id) || id <= 0) {
    return (
      <ParentShell>
        <ParentError status={404} message={`"${kidId}" is not a reader id`} />
      </ParentShell>
    )
  }

  if (progress === null) {
    return (
      <ParentShell>
        <p className="font-mono text-[13px] uppercase tracking-[0.14em] text-ink-500">Loading…</p>
      </ParentShell>
    )
  }

  if (!progress.ok) {
    return (
      <ParentShell>
        <ParentError status={progress.status} message={progress.message} retry={load} />
      </ParentShell>
    )
  }

  const p = progress.data
  const name = p.kid.display_name

  return (
    <ParentShell>
      {kids.length > 1 ? (
        <nav aria-label="Choose a reader" className="mb-8">
          <KidPicker kids={kids} currentKidId={p.kid.id} size="sm" showAdd={false} />
        </nav>
      ) : null}

      <header className="mb-7 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-[40px] leading-[1.1] text-ink-900">
            How {name} is doing
          </h1>
          <p className="mt-2 text-[16px] text-ink-700">
            {p.readings_completed} {p.readings_completed === 1 ? 'story' : 'stories'} finished
            {p.readings_started > p.readings_completed
              ? ` · ${p.readings_started - p.readings_completed} started and not finished`
              : null}
          </p>
        </div>
        <Link
          to={`/read/${p.kid.id}`}
          className="sf-lift-parent inline-flex min-h-tap min-w-tap items-center justify-center
                     rounded-btn border-2 border-berry-700 bg-berry-500 px-6 font-ui text-[17px]
                     font-bold text-paper-page transition-transform duration-[90ms] ease-out"
        >
          Read a story
        </Link>
      </header>

      <StatCards progress={p} />

      <PromotionRule progress={p} />

      <div className="mt-6">
        <GpcTable rows={p.gpc_accuracy} kidName={name} currentStage={p.kid.stage_id} />
      </div>

      <RecentReadings readings={p.recent_readings} kidName={name} />
    </ParentShell>
  )
}

// --- the four stat cards (§08) ------------------------------------------------

function StatCards({ progress: p }: { progress: Progress }) {
  const { promotion: pr, thresholds: th } = p
  const name = p.kid.display_name
  const threshPct = Math.round(th.accuracy_threshold * 100)
  const accPct = pr.accuracy === null ? null : Math.round(pr.accuracy * 100)
  const atMaxStage = pr.to_stage === null

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label="Stage"
        value={p.kid.stage_id}
        detail={
          <>
            <span className="font-bold">{p.stage_name}</span>
            {p.stage_description ? ` — ${p.stage_description}` : null}
          </>
        }
      />

      <StatCard
        label="Stories read"
        value={
          <>
            {pr.stories_completed}
            <span className="text-[20px] text-ink-500">/{pr.stories_required}</span>
          </>
        }
        detail={
          atMaxStage
            ? `finished at stage ${pr.from_stage}`
            : `at stage ${pr.from_stage}, to unlock stage ${pr.to_stage}`
        }
      />

      <StatCard
        label="Accuracy"
        value={accPct === null ? '—' : `${accPct}%`}
        tone={accPct !== null && accPct >= threshPct ? 'good' : 'default'}
        detail={
          accPct === null
            ? `no stage-${pr.from_stage} sounds answered yet · ${threshPct}% needed`
            : `on stage-${pr.from_stage} sounds · ${threshPct}% needed`
        }
      />

      <StatCard
        label="Next up"
        tone="highlight"
        value={nextUp(p)}
        detail={
          pr.promote
            ? 'Both parts of the rule are met.'
            : atMaxStage
              ? // DEVIATIONS 17.2: the ceiling comes from the server's own
                // answer, never a literal. `atMaxStage` IS `to_stage === null`,
                // so `from_stage` is the top of the ladder by construction — and
                // the previous 'Stage 11' would have gone on saying 11 through
                // the next renumber, on a parent's screen, with a green suite.
                `Stage ${pr.from_stage} is the last stage in the scope & sequence.`
              : `${name} moves up on both counts together, never on one alone.`
        }
      />
    </div>
  )
}

/**
 * §08's fourth card: one sentence naming the thing that has not happened yet.
 *
 * Composed from the promotion payload rather than written per case, so it cannot
 * drift from the rule the server is actually applying. It never guesses at a
 * date — "she'll be ready next week" would be an invented score with a calendar
 * attached.
 */
function nextUp(p: Progress): string {
  const { promotion: pr } = p
  const name = p.kid.display_name
  const threshPct = Math.round(pr.accuracy_threshold * 100)

  if (pr.promote) return `${name} is ready for stage ${pr.to_stage}`
  if (pr.to_stage === null) return `${name} is at the last stage`

  const storiesLeft = Math.max(0, pr.stories_required - pr.stories_completed)
  const evidenceLeft = Math.max(0, pr.min_observations - pr.gpc_seen)
  const accuracyMet = pr.accuracy !== null && pr.accuracy >= pr.accuracy_threshold

  if (storiesLeft > 0 && !accuracyMet) {
    return `${storiesLeft} more ${storiesLeft === 1 ? 'story' : 'stories'} at ${threshPct}%+ on stage-${pr.from_stage} sounds moves ${name} up`
  }
  if (storiesLeft > 0) {
    return `${storiesLeft} more ${storiesLeft === 1 ? 'story' : 'stories'} moves ${name} up`
  }
  if (evidenceLeft > 0) {
    return `${evidenceLeft} more answered questions on stage-${pr.from_stage} sounds will settle it`
  }
  return `Stage-${pr.from_stage} accuracy needs to reach ${threshPct}%`
}

// --- §8's rule, shown ---------------------------------------------------------

/**
 * The promotion rule with its thresholds and its per-criterion verdict.
 *
 * The `detail` strings are the server's own words. Rewriting them here would
 * mean two descriptions of one rule, and the one on screen would be the one
 * nobody tests.
 */
function PromotionRule({ progress: p }: { progress: Progress }) {
  const { promotion: pr } = p
  const name = p.kid.display_name
  const threshPct = Math.round(pr.accuracy_threshold * 100)

  return (
    <section
      aria-labelledby="rule-heading"
      className="mt-6 rounded-card border-2 border-paper-rule bg-paper-card p-6 shadow-lift-parent"
    >
      <h2 id="rule-heading" className="font-display text-[22px] text-ink-900">
        {pr.to_stage === null
          ? `${name} is at the last stage`
          : `How ${name} moves up to stage ${pr.to_stage}`}
      </h2>
      <p className="mt-1 max-w-[80ch] text-[15px] leading-[1.5] text-ink-500">
        There is no hidden score. {name} moves up when {pr.stories_required} stories have been
        finished at stage {pr.from_stage} <i>and</i> at least {threshPct}% of the answered
        stage-{pr.from_stage} letter-sounds were right, over at least {pr.min_observations}{' '}
        answered instances. That is the whole rule.
      </p>

      <ul className="mt-5 flex flex-col gap-3">
        {pr.criteria.map((c) => (
          <li key={c.name} className="flex items-start gap-3">
            {/* §11: never colour alone. The glyph, the word "met"/"not yet",
                and the hue all say the same thing independently. */}
            <span
              aria-hidden="true"
              className={[
                'mt-[2px] flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-pill',
                'border-2 text-[12px] font-bold leading-none',
                c.met
                  ? 'border-leaf-500 bg-leaf-100 text-leaf-700'
                  : 'border-paper-shade bg-paper-warm text-ink-500',
              ].join(' ')}
            >
              {c.met ? '✓' : '·'}
            </span>
            <span className="text-[16px] leading-[1.5] text-ink-700">
              <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-500">
                {c.met ? 'met' : 'not yet'}
              </span>
              {/* Decorative separator. ink-300 measured 3.2:1 here, which §02
                  permits only for disabled states and 12px metadata — this is
                  neither, so it takes ink-500 (5.9:1) and is hidden from the
                  accessibility tree, where a spoken "middle dot" is noise. */}
              <span aria-hidden="true" className="mx-2 text-ink-500">
                ·
              </span>
              {c.detail}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

// --- recent readings (§08) ----------------------------------------------------

function RecentReadings({ readings, kidName }: { readings: RecentReading[]; kidName: string }) {
  return (
    <section
      aria-labelledby="recent-heading"
      className="mt-6 rounded-card border-2 border-paper-rule bg-paper-card p-6 shadow-lift-parent"
    >
      <h2 id="recent-heading" className="font-display text-[22px] text-ink-900">
        Recent readings
      </h2>

      {readings.length === 0 ? (
        <p className="mt-3 max-w-[60ch] text-[15px] leading-[1.55] text-ink-700">
          Nothing yet. Stories appear here as soon as {kidName} opens one.
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-4">
          {readings.slice(0, 8).map((r) => (
            <li key={r.id} className="flex items-center gap-[14px]">
              {/* §06's striped fallback rather than a broken image: this row has
                  no asset of its own, and an empty box would read as a bug. */}
              <span
                aria-hidden="true"
                className="sf-stripes h-[52px] w-[52px] shrink-0 rounded-chip border-2 border-paper-rule"
              />
              <div className="min-w-0">
                <div className="text-[16px] font-bold leading-tight text-ink-900">
                  {r.story_title || `Story ${r.story_id}`}
                </div>
                <div className="mt-[2px] text-[14px] leading-[1.45] text-ink-500">
                  {readingSummary(r)}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** "Tue · dragons · chose the cave path · 3/3 correct" (§08). */
function readingSummary(r: RecentReading): React.ReactNode {
  const parts: React.ReactNode[] = []

  parts.push(dayLabel(r.completed_at ?? r.started_at))
  if (r.theme_slug) parts.push(r.theme_slug)
  parts.push(`stage ${r.story_stage_id}`)

  // §08 shows both shapes: a chosen path, or the word "linear" when the story
  // had no branch. `path` is empty on an abandoned reading too, so the two are
  // told apart by `completed_at` rather than by the choice list alone.
  if (r.choices.length > 0) {
    parts.push(
      <>
        chose <i>{r.choices.map((c) => c.choice).join(' → ')}</i>
      </>,
    )
  } else if (r.completed_at) {
    parts.push('linear')
  }

  if (!r.completed_at) {
    parts.push('started, not finished')
  } else if (r.questions_answered > 0) {
    parts.push(`${r.questions_correct}/${r.questions_answered} correct`)
  } else {
    parts.push('no questions answered')
  }

  return parts.map((part, i) => (
    <span key={i}>
      {i > 0 ? ' · ' : null}
      {part}
    </span>
  ))
}

/**
 * A weekday inside the last week, a date beyond it. §08 shows "Tue"/"Mon", which
 * is the right resolution for a parent glancing at a list — but "Tue" for
 * something read five weeks ago is actively misleading.
 */
function dayLabel(iso: string): string {
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return ''
  const ageDays = (Date.now() - then.getTime()) / 86_400_000
  if (ageDays < 1) return 'today'
  if (ageDays < 7) return then.toLocaleDateString(undefined, { weekday: 'short' })
  return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
