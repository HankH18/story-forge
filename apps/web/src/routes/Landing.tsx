/**
 * Landing route (`/`) — style guide §10.
 *
 * Minimal by design: display headline, the one-sentence thesis, a single berry
 * CTA, and one honest artifact. No feature grid, no testimonials, no pricing.
 *
 * The CTA is the only berry control §10 allows on this page, so it is the *same*
 * control in both session states and only its destination changes: signed in it
 * opens the reader list, signed out it opens sign-in — which is the same
 * journey, one step longer. Everything else about the session lives in the top
 * rail, where "Sign in" and "Create an account" are quiet links rather than a
 * second competing CTA.
 *
 * This is also where a signed-out child lands: `/read/:kidId` redirects here
 * rather than to a login form (§01, DEVIATIONS 6.18). Nothing on this page is an
 * error, which is what makes it a safe place to send them — the grown-up who
 * reads it is the one who can act.
 */

import { SignedIn, SignedOut, UserButton } from '@clerk/clerk-react'
import { Link } from 'react-router-dom'
import { AFTER_SIGN_IN_PATH, SIGN_IN_PATH, SIGN_UP_PATH } from '../lib/authPaths'

type Verdict = 'decodable' | 'sight' | 'story' | 'violation'

/**
 * Style guide §02: the locked verdict → presentation mapping, written as literal
 * class names rather than an interpolated `verdict-${v}`. Interpolated names are
 * invisible to a CSS scanner, which is how all four verdicts once silently
 * collapsed to plain ink. The rules themselves live outside `@layer utilities`
 * in styles/index.css for the same reason.
 */
const VERDICT_CLASS: Record<Verdict, string> = {
  decodable: 'verdict-decodable',
  sight: 'verdict-sight',
  story: 'verdict-story',
  violation: 'verdict-violation',
}

/**
 * The §17 demo sentence at stage 3.
 *
 * **These verdicts are hard-coded, and they stay hard-coded.** They are the
 * *expected* output of the validator, used here to prove the design tokens render
 * — they are NOT validator output. The live `POST /api/validate` result is now
 * shown by the admin playground at `/admin` (§5.6, §17 step 1), which exists as of
 * the Phase 8 portal.
 *
 * The obvious tidy-up — point this page at the live endpoint and delete the
 * constant — is refused on purpose: `/` is public and `/api/validate` is
 * admin-only (§9), so a signed-out visitor cannot be shown a live validation, and
 * reaching for one here would mean opening the one endpoint that accepts arbitrary
 * text to anybody (DEVIATIONS 9.38). What keeps this honest instead is the phase 1
 * golden suite, which asserts the real validator produces exactly these six
 * verdicts (`tests/decoder/golden.yaml::demo-sentence-stage-3`), and the fact that
 * the same sentence is on screen at `/admin` with the API's own answer beside it.
 */
const DEMO_SENTENCE: { word: string; verdict: Verdict }[] = [
  { word: 'The', verdict: 'sight' },
  { word: 'knight', verdict: 'violation' },
  { word: 'sees', verdict: 'violation' },
  { word: 'a', verdict: 'sight' },
  { word: 'beautiful', verdict: 'violation' },
  { word: 'dragon', verdict: 'story' },
]

const LEGEND: { verdict: Verdict; label: string }[] = [
  { verdict: 'decodable', label: 'decodable' },
  { verdict: 'sight', label: 'sight word' },
  { verdict: 'story', label: 'story word' },
  { verdict: 'violation', label: 'violation' },
]

/**
 * §10's single berry CTA, written once so the two session states cannot drift
 * into two different-looking buttons.
 */
function CtaLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className="sf-lift-kid inline-flex min-h-tap-kid items-center justify-center
                 rounded-btn border-[3px] border-ink-900 bg-berry-500 px-8
                 font-ui text-[17px] font-bold text-paper-page
                 transition-transform duration-[90ms] ease-out"
    >
      {children}
    </Link>
  )
}

export default function Landing() {
  return (
    <main className="min-h-screen bg-paper-page px-6 py-16">
      <div className="mx-auto flex max-w-[720px] flex-col gap-10">
        {/* The session rail. Quiet by construction — §10 gives this page one
            berry CTA and it is the one at the bottom. */}
        <div className="flex min-h-tap items-center justify-end gap-3">
          <SignedOut>
            <Link
              to={SIGN_UP_PATH}
              className="inline-flex min-h-tap items-center justify-center rounded-btn px-3
                         font-ui text-[16px] text-ink-700 underline underline-offset-4"
            >
              Create an account
            </Link>
            <Link
              to={SIGN_IN_PATH}
              className="sf-lift-parent inline-flex min-h-tap min-w-tap items-center justify-center
                         rounded-btn border-2 border-paper-rule bg-paper-card px-5 font-ui
                         text-[16px] font-bold text-ink-900 transition-transform duration-[90ms]
                         ease-out"
            >
              Sign in
            </Link>
          </SignedOut>
          <SignedIn>
            <UserButton />
          </SignedIn>
        </div>

        <header className="flex flex-col gap-5">
          <p className="font-mono text-[13px] uppercase tracking-[0.16em] text-ink-500">
            Story Forge · stage 3 · digraphs
          </p>
          <h1 className="font-display text-[56px] leading-[1.02] text-ink-900">
            Once upon a stage
          </h1>
          <p className="font-ui text-[17px] leading-[1.6] text-ink-700">
            Stories are generated fresh and checked against the sounds your reader
            knows, word by word, before they ever reach the page.
          </p>
        </header>

        {/* The one honest artifact (§10). */}
        <section
          aria-labelledby="artifact-heading"
          className="rounded-card border-2 border-paper-rule bg-paper-card p-8 shadow-lift-parent"
        >
          <h2
            id="artifact-heading"
            className="font-mono text-[13px] uppercase tracking-[0.16em] text-ink-500"
          >
            The same sentence, checked at stage 3
          </h2>

          <p className="mt-6 font-read text-read text-ink-900">
            {DEMO_SENTENCE.map(({ word, verdict }, i) => (
              <span key={`${word}-${i}`}>
                <span className={VERDICT_CLASS[verdict]}>{word}</span>
                {i < DEMO_SENTENCE.length - 1 ? ' ' : ''}
              </span>
            ))}
          </p>

          {/* §11: never colour alone — the legend names every verdict. */}
          <ul className="mt-8 flex flex-wrap gap-x-6 gap-y-2">
            {LEGEND.map(({ verdict, label }) => (
              <li key={verdict} className="flex items-center gap-2">
                <span className={`font-read text-[15px] ${VERDICT_CLASS[verdict]}`}>Aa</span>
                <span className="font-mono text-[12px] text-ink-500">{label}</span>
              </li>
            ))}
          </ul>
        </section>

        <div>
          {/* §10 allows exactly one berry CTA here, so it points at the app's
              real entrance rather than at a hard-coded kid id. A landing page is
              read by a parent — the child never navigates to one — and the
              parent's first screen is their list of readers.

              Signed out it goes to `/sign-in` instead of to `/app`. Both work —
              the guard on `/app` would bounce them — but a CTA that visibly
              lands somewhere else is a CTA that looks broken, and the redirect
              would drop the deep link for nothing. */}
          <SignedIn>
            <CtaLink to={AFTER_SIGN_IN_PATH}>Open Story Forge</CtaLink>
          </SignedIn>
          <SignedOut>
            <CtaLink to={SIGN_IN_PATH}>Open Story Forge</CtaLink>
          </SignedOut>
        </div>
      </div>
    </main>
  )
}
