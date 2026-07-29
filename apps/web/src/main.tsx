/**
 * Entry point — and the one place that refuses to start (DEVIATIONS 6.16).
 *
 * `VITE_CLERK_PUBLISHABLE_KEY` sat in `apps/web/.env.local` for a whole phase
 * with nothing reading it. The app worked anyway, because the API was running
 * §18's `DEMO_BYPASS_AUTH` and answered every request as a fixed local parent —
 * so an app with no authentication at all looked exactly like an app with
 * authentication. That is the failure this file now makes impossible: without a
 * usable key, `<App/>` is never mounted and the screen says which variable, in
 * which file, from which dashboard page. An unauthenticated app that renders is
 * worse than an app that will not start, because only the second is found
 * before a demo.
 *
 * Two failures, not one. A *missing* key is checked here. A *malformed* key
 * gets past that check and makes `<ClerkProvider>` throw during render, which
 * React answers with an empty page and a stack trace in the console — the same
 * blank div, arrived at differently. `ClerkBootBoundary` catches it and shows
 * the same actionable screen. Both were reproduced in a browser, not reasoned
 * about: `VITE_CLERK_PUBLISHABLE_KEY=` renders the first, `pk_test_BUILD_PROBE`
 * rendered the second (as a blank page, before the boundary existed).
 *
 * `<BrowserRouter>` moved here from App.tsx so `<ClerkProvider>` can sit inside
 * the router and be handed `useNavigate` — see lib/auth.tsx.
 */

import React from 'react'
import type { ReactNode } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { StoryForgeClerkProvider } from './lib/auth'
import './styles/index.css'

const MISSING_KEY_CONSOLE =
  'Story Forge cannot start: VITE_CLERK_PUBLISHABLE_KEY is missing or empty.\n' +
  'Set it in apps/web/.env.local (Clerk dashboard → API keys → Publishable key),\n' +
  'then restart the dev server — Vite reads env files only at startup.'

const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim()

const root = ReactDOM.createRoot(document.getElementById('root')!)

/** Called at the bottom of the file — a class declaration does not hoist. */
function boot() {
  if (!publishableKey) {
    console.error(MISSING_KEY_CONSOLE)
    root.render(
      <React.StrictMode>
        <StartupFailure headline="VITE_CLERK_PUBLISHABLE_KEY is not set" />
      </React.StrictMode>,
    )
    return
  }

  root.render(
    <React.StrictMode>
      <ClerkBootBoundary publishableKey={publishableKey}>
        <BrowserRouter>
          <StoryForgeClerkProvider publishableKey={publishableKey}>
            <App />
          </StoryForgeClerkProvider>
        </BrowserRouter>
      </ClerkBootBoundary>
    </React.StrictMode>,
  )
}

/**
 * Catches the throw `<ClerkProvider>` performs on a malformed publishable key.
 *
 * Scoped by re-throwing anything that does not name Clerk or the key: this
 * boundary sits above every route, and a boundary that swallows *all* render
 * errors would relabel an ordinary component bug as "your Clerk key is wrong"
 * and send whoever meets it to the wrong file. An unrelated error propagates as
 * it did before — no boundary, no new lie.
 *
 * The key is printed because a publishable key is public by construction (it
 * ships in the bundle and is in every network request), and the whole failure
 * is usually a typo or a placeholder that got committed.
 */
class ClerkBootBoundary extends React.Component<
  { publishableKey: string; children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.error(
      'Story Forge cannot start: Clerk refused the publishable key ' +
        `(${this.props.publishableKey}).\nCheck VITE_CLERK_PUBLISHABLE_KEY in ` +
        'apps/web/.env.local against the Clerk dashboard → API keys, then restart the ' +
        `dev server.\nClerk said: ${error.message}`,
    )
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    const clerkBoot = /publishablekey|clerk/i.test(error.message)
    if (!clerkBoot) throw error

    return (
      <StartupFailure
        headline="Clerk refused this publishable key"
        detail={error.message}
        keyValue={this.props.publishableKey}
      />
    )
  }
}

/**
 * The whole screen, on purpose.
 *
 * A console warning would scroll away and a banner would be ignored, and both
 * leave a signed-out app rendering parent screens as if they were signed in.
 * Every sentence here is something the reader can act on: the variable, the
 * file, where the value comes from, and the restart Vite requires.
 */
function StartupFailure({
  headline,
  detail,
  keyValue,
}: {
  headline: string
  detail?: string
  keyValue?: string
}) {
  return (
    <main className="min-h-screen bg-paper-page px-6 py-16">
      <div className="mx-auto flex max-w-[720px] flex-col gap-6">
        <p className="font-mono text-[13px] uppercase tracking-[0.16em] text-berry-700">
          Story Forge did not start
        </p>
        <h1 className="font-display text-[40px] leading-[1.1] text-ink-900">{headline}</h1>
        <p className="max-w-[68ch] font-ui text-[17px] leading-[1.6] text-ink-700">
          Story Forge will not run without Clerk. It could have rendered the parent screens
          anyway — the API can be started with <code className="font-mono">DEMO_BYPASS_AUTH</code>,
          and then an app with no sign-in looks exactly like an app with one. It refuses instead,
          because that is a difference nobody notices until the demo.
        </p>

        <ol className="flex flex-col gap-3 rounded-card border-2 border-paper-rule bg-paper-card p-6 font-ui text-[16px] leading-[1.6] text-ink-700 shadow-lift-parent">
          <li>
            <span className="font-bold text-ink-900">1.</span> Copy the publishable key from the
            Clerk dashboard → <span className="font-mono text-[15px]">API keys</span>. It starts
            with <span className="font-mono text-[15px]">pk_test_</span>.
          </li>
          <li>
            <span className="font-bold text-ink-900">2.</span> Put it in{' '}
            <span className="font-mono text-[15px]">apps/web/.env.local</span> as{' '}
            <span className="font-mono text-[15px]">VITE_CLERK_PUBLISHABLE_KEY=…</span>
          </li>
          <li>
            <span className="font-bold text-ink-900">3.</span> Restart the dev server. Vite reads
            env files only at startup, so an already-running{' '}
            <span className="font-mono text-[15px]">make dev</span> will not pick it up — and a
            production bundle bakes the value in at <span className="font-mono text-[15px]">bun
            run build</span> time.
          </li>
        </ol>

        {keyValue ? (
          <p className="font-mono text-[13px] leading-[1.6] text-berry-700">
            The key this build was given: {keyValue}
          </p>
        ) : null}
        {detail ? (
          <p className="font-mono text-[12px] leading-[1.6] text-ink-500">{detail}</p>
        ) : null}

        <p className="font-mono text-[12px] leading-[1.6] text-ink-500">
          The API side is separate: services/api/.env needs CLERK_JWKS_URL for the same instance.
        </p>
      </div>
    </main>
  )
}

boot()
