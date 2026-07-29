/**
 * Landing route (`/`) — style guide §10.
 *
 * Minimal by design: display headline, the one-sentence thesis, a single berry
 * CTA, and one honest artifact. No feature grid, no testimonials, no pricing.
 *
 * Unchanged from phase 0 apart from the CTA, which now goes somewhere: `/read/1`
 * is the kid session (§11 routes). The kid id is a placeholder until the `kids`
 * table lands with auth in phase 6 — see KidSession for how a stage is resolved
 * without one.
 */

import { Link } from 'react-router-dom'

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
 * PHASE 0 NOTE: these verdicts are hard-coded. They are the *expected* output of
 * the validator, used here to prove the design tokens render — they are NOT
 * validator output. The admin playground (§5.6, phase 8) is where the live
 * `POST /api/validate` result is shown; the phase 1 golden suite asserts the
 * real validator produces exactly this.
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

export default function Landing() {
  return (
    <main className="min-h-screen bg-paper-page px-6 py-16">
      <div className="mx-auto flex max-w-[720px] flex-col gap-10">
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
          <Link
            to="/read/1"
            className="sf-lift-kid inline-flex min-h-tap-kid items-center justify-center
                       rounded-btn border-[3px] border-ink-900 bg-berry-500 px-8
                       font-ui text-[17px] font-bold text-paper-page
                       transition-transform duration-[90ms] ease-out"
          >
            Read a story
          </Link>
        </div>
      </div>
    </main>
  )
}
