/**
 * AdminShell — the admin portal's chrome, its role gate, and its error surface
 * (build doc §11's "Admin portal"; style guide §09, §10; DEVIATIONS 9.20-9.23,
 * 9.35, 9.36).
 *
 * Three jobs, and the middle one is the reason this is a shell rather than a
 * header component.
 *
 * **1. §10's admin grid.** 12 columns, max 1440px, gutter 16 — wider than the
 * 1120px `ParentShell`, so this is its own chrome and not that one with a
 * different heading. §09's temperament: "Paper background retained for family
 * resemblance; everything else is mono, 1px rules, tight rows. This is an
 * instrument panel, and it should look like one."
 *
 * **2. The role gate, asked of the only authority there is.** `lib/auth.tsx` does
 * not read roles and must not start: `auth.py` re-reads `public_metadata.role`
 * from the signed token on every request so the Clerk dashboard can revoke admin
 * immediately, and a client-side copy is a second opinion that can disagree with
 * that one — in the dangerous direction, the first time Clerk's client cache is
 * ahead of a revocation. So `RequireAdmin` checks only for a session, and this
 * shell asks the API. Nothing below it renders until the API has answered
 * (DEVIATIONS 9.23): the alternative is per-screen 403 handling, which leaves a
 * parent looking at a working-looking instrument panel whose gauges are all
 * empty.
 *
 * **3. The 403 surface DEVIATIONS 7.37 removed.** That entry deleted
 * `ParentError`'s 403 branch after proving it unreachable — `require_admin`
 * guarded one route and no web code called it — and preserved its sentence
 * verbatim "so Phase 8 can restore it rather than re-derive it". This is Phase 8.
 * The sentence is restored word for word below, because the fact behind it is
 * still true and still surprising: Clerk's DEFAULT session token carries no
 * metadata at all, so `role_from_claims` resolves EVERY signed-in user —
 * including an admin whose `public_metadata` is `{"role":"admin"}` server-side —
 * to `parent`, until someone customises the session token in the Clerk dashboard.
 * A generic "Forbidden" sends whoever meets it into `auth.py` hunting a bug that
 * is not there.
 */

import { UserButton, useAuth } from '@clerk/clerk-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { adminErrorCopy, probeAdmin, type AdminResult } from '../lib/adminApi'
import { SIGN_IN_PATH } from '../lib/authPaths'

// --- chrome -------------------------------------------------------------------

/** The four screens §11's "Admin portal" paragraph names, plus the playground. */
const NAV: { to: string; label: string; end?: boolean }[] = [
  { to: '/admin', label: 'playground', end: true },
  { to: '/admin/words', label: 'allowed words' },
  { to: '/admin/evals', label: 'eval runs' },
  { to: '/admin/generate', label: 'cold generate' },
]

/**
 * Literal class strings, because `NavLink`'s className is a function of `isActive`
 * and the two variants must both survive the production purge (STATE invariant 7).
 * `min-h-tap` / `min-w-tap` are §11's floors — 48px, the ADULT number. Kid mode's
 * 64px `tap-kid` is deliberately not used anywhere in this portal (DEVIATIONS
 * 9.35).
 */
const NAV_BASE =
  'inline-flex min-h-tap min-w-tap items-center rounded-[4px] border px-4 ' +
  'font-mono text-[12px] uppercase tracking-[0.12em] transition-colors'
const NAV_ON = `${NAV_BASE} border-ink-900 bg-paper-warm text-ink-900`
const NAV_OFF = `${NAV_BASE} border-paper-shade bg-paper-card text-ink-500 hover:text-ink-900`

export interface AdminShellProps {
  children: React.ReactNode
  /** Shown at the right of the title row — a count, a stage, a job id. */
  meta?: React.ReactNode
  title: string
  /** One mono line under the title: what this screen is and where it reads from. */
  subtitle?: React.ReactNode
}

/**
 * The chrome alone, with no gate. Used by the gate itself (so a 403 renders
 * inside the same frame as a 200 and the surface does not appear to change
 * identity) and by `AdminGate` for everything that passes.
 */
export function AdminFrame({ children, title, subtitle, meta }: AdminShellProps) {
  return (
    <div className="min-h-screen bg-paper-page">
      <header className="border-b border-paper-shade bg-paper-card">
        <div className="mx-auto flex max-w-[1440px] flex-wrap items-center justify-between gap-4 px-4 py-3">
          <div className="flex flex-wrap items-center gap-4">
            <Link
              to="/admin"
              className="inline-flex min-h-tap items-center rounded-[4px] px-1 font-display text-[20px] text-ink-900"
            >
              Story Forge
              <span className="ml-2 font-mono text-[11px] uppercase tracking-[0.16em] text-berry-500">
                admin
              </span>
            </Link>
            <nav aria-label="Admin sections" className="flex flex-wrap gap-2">
              {NAV.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) => (isActive ? NAV_ON : NAV_OFF)}
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>

          <div className="flex items-center gap-3">
            <Link
              to="/app"
              className="inline-flex min-h-tap items-center rounded-[4px] px-3 font-mono text-[12px]
                         uppercase tracking-[0.12em] text-ink-500 hover:text-ink-900"
            >
              parent view
            </Link>
            <UserButton />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1440px] px-4 py-7">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3 border-b border-paper-shade pb-3">
          <div>
            <h1 className="font-display text-[30px] leading-tight text-ink-900">{title}</h1>
            {subtitle ? (
              <p className="mt-1 font-mono text-[12px] text-ink-500">{subtitle}</p>
            ) : null}
          </div>
          {meta ? <div className="font-mono text-[12px] text-ink-500">{meta}</div> : null}
        </div>

        {children}
      </main>
    </div>
  )
}

// --- the gate -----------------------------------------------------------------

/**
 * Ask the API whether this session is an admin, and render nothing else until it
 * answers.
 *
 * The probe is `POST /api/validate` with the text `cat` at stage 1 — the one admin
 * route that provably existed before this portal did, a pure function over a
 * vendored dictionary plus one indexed `SELECT`, and free. 9.1 puts a single
 * `require_admin` on the whole admin router, so a 200 here is a 200 there; that
 * assumption is stated in DEVIATIONS 9.23 and lives at this one call site.
 */
export default function AdminShell(props: AdminShellProps) {
  const { isLoaded, isSignedIn } = useAuth()
  const [probe, setProbe] = useState<AdminResult<unknown> | null>(null)

  const check = useCallback(() => {
    let live = true
    setProbe(null)
    void probeAdmin().then((r) => {
      if (live) setProbe(r)
    })
    return () => {
      live = false
    }
  }, [])

  // Re-probed when the session changes: signing in as a different user must not
  // inherit the previous verdict.
  useEffect(() => {
    if (!isLoaded || !isSignedIn) return
    return check()
  }, [check, isLoaded, isSignedIn])

  if (!isLoaded || (isSignedIn && probe === null)) {
    return (
      <AdminFrame title={props.title} subtitle={props.subtitle}>
        <p role="status" className="font-mono text-[12px] uppercase tracking-[0.14em] text-ink-500">
          checking this session against the API…
        </p>
      </AdminFrame>
    )
  }

  // `RequireAdmin` has already redirected a signed-out visitor; reaching here
  // signed out means Clerk changed its mind mid-render. Say so rather than
  // showing an empty instrument panel.
  if (!isSignedIn) {
    return (
      <AdminFrame title={props.title} subtitle={props.subtitle}>
        <AdminError
          status={401}
          detail="no Clerk session"
          where="client"
          retry={check}
        />
      </AdminFrame>
    )
  }

  if (probe && !probe.ok) {
    return (
      <AdminFrame title={props.title} subtitle={props.subtitle}>
        <AdminError status={probe.status} detail={probe.detail} where={probe.where} retry={check} />
      </AdminFrame>
    )
  }

  return <AdminFrame {...props} />
}

// --- the error surface --------------------------------------------------------

/**
 * `MISSING_ROLE_CLAIM_DETAIL` and the whole status → copy decision live in
 * `lib/adminApi.ts` as `adminErrorCopy`, re-exported here for the callers that
 * used to import it from this file.
 *
 * They moved out of this component on purpose: `bun test src` covers `lib/` and
 * the components are verified in a browser (DEVIATIONS 7.30, 9.39), so a security-
 * relevant sentence chosen inside a component is a sentence no test can see. The
 * 403 branch this restores was deleted in 7.37 precisely because nothing exercised
 * it, and shipping it back untested would repeat that.
 */
export { MISSING_ROLE_CLAIM_DETAIL } from '../lib/adminApi'

/**
 * The admin failure surface. §01 does not apply here and is inverted on purpose
 * (DEVIATIONS 9.36): a child never sees an error, a parent sees a plain sentence
 * with no jargon, and an admin debugging a pipeline sees the status, the method
 * and the path.
 *
 * Five cases, because five different things want five different actions:
 *
 *   403  the role claim — the 7.37 sentence, naming the dashboard action
 *   401  the API refused the token; Clerk may still think it is valid (7.32)
 *   404  the route is absent, which for `/api/admin/*` is a real possibility
 *   0    never reached the server — a different sentence to "the server said no"
 *   else the status, said plainly, with the body the server sent
 */
export function AdminError({
  status,
  detail,
  where,
  retry,
}: {
  status: number
  detail: string
  where: string
  retry?: () => void
}) {
  const copy = adminErrorCopy(status)

  return (
    <section className="max-w-[80ch] rounded-[4px] border border-paper-shade bg-paper-card p-6">
      <h2 className="font-display text-[24px] text-ink-900">{copy.headline}</h2>
      <p className="mt-3 text-[15px] leading-[1.6] text-ink-700">{copy.body}</p>

      {/* The machine facts, always, in mono. This is the difference between this
          surface and the parent one (DEVIATIONS 9.36): §01's "a child never sees
          an error" is about kid mode, a parent gets a plain sentence and no
          jargon, and an admin debugging a pipeline gets the status code. */}
      <dl className="mt-5 grid gap-x-6 gap-y-1 font-mono text-[12px] text-ink-500 sm:grid-cols-[auto_1fr]">
        <dt>request</dt>
        <dd className="text-ink-700">{where}</dd>
        <dt>status</dt>
        <dd className="text-ink-700">{status === 0 ? 'no response' : status}</dd>
        <dt>detail</dt>
        <dd className="break-words text-ink-700">{detail || '(empty body)'}</dd>
      </dl>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        {retry ? (
          <button
            type="button"
            onClick={retry}
            className="inline-flex min-h-tap min-w-tap items-center justify-center rounded-[4px]
                       border border-ink-900 bg-paper-card px-5 font-mono text-[12px] uppercase
                       tracking-[0.12em] text-ink-900"
          >
            try again
          </button>
        ) : null}
        {/* The remedy for a rejected session is to sign out on the way (7.32: while
            Clerk still holds a session, `<SignIn>` refuses to render and bounces
            back, so a plain link to /sign-in is a loop). For a MISSING ROLE CLAIM
            the remedy is a dashboard change by someone else, and the link is there
            only so the admin can re-sign-in after that has happened. */}
        {copy.kind === 'session' || copy.kind === 'role' ? (
          <Link
            to={SIGN_IN_PATH}
            className="inline-flex min-h-tap min-w-tap items-center justify-center rounded-[4px]
                       border border-paper-shade bg-paper-card px-5 font-mono text-[12px]
                       uppercase tracking-[0.12em] text-ink-500"
          >
            sign-in page
          </Link>
        ) : null}
      </div>
    </section>
  )
}

// --- small shared pieces ------------------------------------------------------

/** §09's mono section label: 11px, uppercase, 0.16em, ink-500. */
export function AdminLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-3 font-mono text-[11px] uppercase tracking-[0.16em] text-ink-500">
      {children}
    </div>
  )
}

/**
 * §09's metric card: a mono label over a 22px value, on a 1px-ruled panel.
 *
 * `tone` exists for one reason — §09 prints the playground's `decodability 0.0%`
 * in berry-700 — and it is never the only signal: the label says what the number
 * is and the reason lines below say why.
 */
export function AdminMetric({
  label,
  value,
  tone = 'default',
  children,
}: {
  label: string
  value: React.ReactNode
  tone?: 'default' | 'bad' | 'good'
  children?: React.ReactNode
}) {
  const VALUE: Record<'default' | 'bad' | 'good', string> = {
    default: 'text-ink-900',
    bad: 'text-berry-700',
    good: 'text-leaf-700',
  }
  return (
    <div className="rounded-[4px] border border-paper-shade bg-paper-card p-[14px]">
      <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-500">{label}</div>
      <div className={`mt-[6px] font-mono text-[22px] leading-none ${VALUE[tone]}`}>{value}</div>
      {children ? <div className="mt-[6px] font-mono text-[12px]">{children}</div> : null}
    </div>
  )
}

/**
 * The one panel shape §09 uses for everything: a 1px shade rule, 14px radius, and
 * a fill one step warmer than the cards inside it.
 *
 * §09 draws that fill as `#FBF7EC`, which is between `paper-page` and
 * `paper-card` and is not one of §12's six paper tokens. Rather than introduce a
 * seventh as a literal hex — the tokens are the source of truth, and a one-off hex
 * in a component is how a palette drifts — the two-level stack is built from real
 * tokens in the same direction: `paper-warm` outside, `paper-card` on the metric
 * cards within. Slightly warmer than the mock, identical in structure.
 */
export function AdminPanel({
  children,
  className = '',
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <section className={`rounded-[14px] border border-paper-shade bg-paper-warm p-6 ${className}`}>
      {children}
    </section>
  )
}
