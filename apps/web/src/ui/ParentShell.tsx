/**
 * ParentShell — §10's parent chrome: "Header with wordmark + account", on the
 * parent grid (max 1120px, 24px gutter).
 *
 * The account slot was an honest placeholder ("Demo session") for as long as
 * there was no session to render: auth landed on the API first, and a fake
 * avatar-and-chevron menu that does nothing is the kind of thing that survives
 * all the way to a demo. There is a real session now, so the slot holds a real
 * control — Clerk's `<UserButton/>`, which is also the sign-out (DEVIATIONS
 * 6.22). Signed out it is a link to `/sign-in`, which is a state the guards
 * normally prevent; the shell renders it anyway rather than assuming.
 */

import { SignedIn, SignedOut, SignOutButton, UserButton } from '@clerk/clerk-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { SIGN_IN_PATH } from '../lib/authPaths'

export interface ParentShellProps {
  children: ReactNode
  /** Rendered at the right of the header. Defaults to the account control. */
  account?: ReactNode
}

export default function ParentShell({ children, account }: ParentShellProps) {
  return (
    <div className="min-h-screen bg-paper-page">
      <header className="border-b-2 border-paper-rule bg-paper-card">
        <div className="mx-auto flex max-w-[1120px] flex-wrap items-center justify-between gap-4 px-6 py-4">
          <Link
            to="/app"
            className="inline-flex min-h-tap items-center rounded-btn px-2 font-display text-[24px] text-ink-900"
          >
            Story Forge
          </Link>

          <div className="flex items-center gap-3">{account ?? <AccountControl />}</div>
        </div>
      </header>

      <main className="mx-auto max-w-[1120px] px-6 py-10">{children}</main>
    </div>
  )
}

/**
 * The §10 account slot.
 *
 * `<UserButton/>` carries its own menu and its own sign-out, and
 * `afterSignOutUrl="/"` is set once on the provider so signing out lands on the
 * landing page rather than on a parent route that would immediately bounce to
 * `/sign-in`. Clerk's default trigger is a 28px avatar; the appearance override
 * in lib/auth.tsx raises it to §11's 48px floor.
 */
export function AccountControl() {
  return (
    <>
      <SignedIn>
        <UserButton />
      </SignedIn>
      <SignedOut>
        <Link
          to={SIGN_IN_PATH}
          className="sf-lift-parent inline-flex min-h-tap min-w-tap items-center justify-center
                     rounded-btn border-2 border-paper-rule bg-paper-card px-5 font-ui text-[16px]
                     font-bold text-ink-900 transition-transform duration-[90ms] ease-out"
        >
          Sign in
        </Link>
      </SignedOut>
    </>
  )
}

/**
 * The primary action on this screen. A literal in this file so Tailwind's
 * scanner finds every class and the production bundle keeps them (STATE
 * invariant 7 — dynamic class names are purged silently).
 */
const PRIMARY_ACTION =
  'sf-lift-parent inline-flex min-h-tap min-w-tap items-center justify-center ' +
  'rounded-btn border-2 border-berry-700 bg-berry-500 px-6 font-ui text-[17px] ' +
  'font-bold text-paper-page transition-transform duration-[90ms] ease-out'

/**
 * The one failure state the parent surface has to have.
 *
 * Kid mode may never show an error (§01). A parent must: they came for facts
 * about their child, and a screen that renders zeros because the request failed
 * has told them something false.
 *
 * **A 401 here is not "you are signed out".** That was the bug (audit C4,
 * DEVIATIONS 7.32): this screen said "Please sign in again" directly beneath
 * ParentHome's "SIGNED IN AS …" and a live `<UserButton/>`, because
 * `SignedIn`/`SignedOut` read Clerk's *client* state — which is still perfectly
 * valid — while the 401 comes from the *API* refusing the token. Two systems,
 * described as one, contradicting each other on the same screen. And its "Sign
 * in" button linked to `/sign-in`, where `<SignIn>` refuses to render for an
 * already-signed-in user and redirects straight back here: the remedy was a loop.
 *
 * So the copy names which side refused, and the remedy is `signOut()` first.
 * Signing out is not ceremony — it is the step that clears the stale client
 * session that makes the sign-in form bounce.
 *
 * There is no 403 branch. `require_admin` is the only thing in the API that
 * produces 403, it guards exactly one route (`POST /api/validate`), and no web
 * code calls it — so the branch could not render, and a case that cannot happen
 * looks like a case that has been handled. It returns with the Phase 8 admin
 * portal; the sentence it displayed is preserved verbatim in DEVIATIONS 7.37 so
 * it can be restored rather than re-derived. A 403 from somewhere unforeseen (an
 * edge proxy, a WAF) falls to the generic branch with its status in the message,
 * which is the honest rendering of a status this app cannot explain.
 */
export function ParentError({
  status,
  message,
  retry,
}: {
  status: number
  message: string
  retry?: () => void
}) {
  const rejected = status === 401
  const offline = status === 0

  return (
    <div className="rounded-card border-2 border-paper-rule bg-paper-card p-8 shadow-lift-parent">
      <h1 className="font-display text-[28px] text-ink-900">
        {rejected
          ? 'Your session was not accepted'
          : offline
            ? 'Could not reach Story Forge'
            : 'Something went wrong'}
      </h1>
      <p className="mt-3 max-w-[60ch] text-[16px] leading-[1.6] text-ink-700">
        {rejected
          ? 'Story Forge asked the server for your reader’s progress and the server did not accept this sign-in — most likely it has been open long enough to expire. Rather than show numbers that might not be yours, this page is showing none. Your browser still thinks you are signed in, so the way back is to sign out and in again.'
          : offline
            ? 'The server did not answer. Nothing has been lost — this page only reads.'
            : 'This page could not load your reader’s progress. Rather than show zeros that are not true, it is showing nothing.'}
      </p>
      <p className="mt-4 font-mono text-[12px] text-ink-500">{message}</p>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        {retry ? (
          <button
            type="button"
            onClick={retry}
            className="sf-lift-parent inline-flex min-h-tap min-w-tap items-center justify-center
                       rounded-btn border-2 border-paper-rule bg-paper-card px-6 font-ui text-[17px]
                       font-bold text-ink-900 transition-transform duration-[90ms] ease-out"
          >
            Try again
          </button>
        ) : null}
        {rejected ? <RejectedSessionRemedy /> : null}
      </div>
    </div>
  )
}

/**
 * The way out of a session the API will not accept.
 *
 * Both arms end at `/sign-in`; only one of them can get there. While Clerk still
 * holds a session, `<SignIn>` will not render and redirects back — so the button
 * has to sign out on the way. `<SignedOut>` is the case where Clerk agrees the
 * session is gone (a genuinely signed-out visitor who reached a parent route
 * before the guard moved), and then a plain link is the whole answer.
 */
function RejectedSessionRemedy() {
  return (
    <>
      <SignedIn>
        <SignOutButton redirectUrl={SIGN_IN_PATH}>
          <button type="button" className={PRIMARY_ACTION}>
            Sign out and sign in again
          </button>
        </SignOutButton>
      </SignedIn>
      <SignedOut>
        <Link to={SIGN_IN_PATH} className={PRIMARY_ACTION}>
          Sign in
        </Link>
      </SignedOut>
    </>
  )
}
