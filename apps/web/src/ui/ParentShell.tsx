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

import { SignedIn, SignedOut, UserButton } from '@clerk/clerk-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { MISSING_ROLE_CLAIM_DETAIL, SIGN_IN_PATH } from '../lib/authPaths'

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
 * The one failure state the parent surface has to have.
 *
 * Kid mode may never show an error (§01). A parent must: they came for facts
 * about their child, and a screen that renders zeros because the request failed
 * has told them something false.
 *
 * 401 and 403 are separated because they are different problems with different
 * fixes, and collapsing them into "please sign in again" sends someone to do
 * the one thing that will not help. A 401 is an expired or absent session and
 * the button below signs in again. A 403 means the API *verified* the session
 * and still refused: `require_admin` only passes when the token itself carries
 * `public_metadata.role = "admin"`, and Clerk's default session token carries no
 * metadata at all — so every real session reads as a parent until a human adds
 * the claim in the Clerk dashboard. That sentence is in lib/auth.tsx, stated
 * once, and it names the dashboard action rather than saying "forbidden"
 * (DEVIATIONS 6.20).
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
  const signedOut = status === 401
  const forbidden = status === 403
  const offline = status === 0

  return (
    <div className="rounded-card border-2 border-paper-rule bg-paper-card p-8 shadow-lift-parent">
      <h1 className="font-display text-[28px] text-ink-900">
        {signedOut
          ? 'Please sign in again'
          : forbidden
            ? 'This account cannot open this screen'
            : offline
              ? 'Could not reach Story Forge'
              : 'Something went wrong'}
      </h1>
      <p className="mt-3 max-w-[60ch] text-[16px] leading-[1.6] text-ink-700">
        {signedOut
          ? 'Your session has expired, so this page is not showing your reader’s progress rather than showing it wrong.'
          : forbidden
            ? MISSING_ROLE_CLAIM_DETAIL
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
        {signedOut ? (
          <Link
            to={SIGN_IN_PATH}
            className="sf-lift-parent inline-flex min-h-tap min-w-tap items-center justify-center
                       rounded-btn border-2 border-berry-700 bg-berry-500 px-6 font-ui text-[17px]
                       font-bold text-paper-page transition-transform duration-[90ms] ease-out"
          >
            Sign in
          </Link>
        ) : null}
      </div>
    </div>
  )
}
