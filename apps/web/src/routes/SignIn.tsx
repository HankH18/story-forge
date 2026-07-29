/**
 * `/sign-in/*` and `/sign-up/*` — Clerk's own components on this app's routes
 * (build doc §9, §13; style guide §10 parent surface).
 *
 * `routing="path"` with a splat route, not the modal or the hosted portal.
 * Clerk walks a multi-step flow through child paths (`/sign-in/factor-one`,
 * `/sign-in/sso-callback`), so the route has to match `/sign-in/*` or the second
 * step of every sign-in renders the catch-all redirect instead. The hosted
 * portal would work too and would take the parent off this origin mid-flow,
 * which is a worse thing to ask of someone typing a password.
 *
 * The `next` parameter is the deep link a guard interrupted, validated
 * to a same-origin path first (DEVIATIONS 6.23) — an unvalidated post-login
 * redirect is an open redirect with a session attached to it.
 *
 * The chrome is deliberately the parent shell: this screen exists only for a
 * parent. A child never reaches it — `/read/:kidId` bounces to `/` instead
 * (§01, DEVIATIONS 6.18).
 */

import { SignIn, SignUp } from '@clerk/clerk-react'
import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  AFTER_SIGN_IN_PATH,
  REDIRECT_PARAM,
  SIGN_IN_PATH,
  SIGN_UP_PATH,
  safeRedirectPath,
} from '../lib/authPaths'

function AuthFrame({
  title,
  blurb,
  children,
}: {
  title: string
  blurb: string
  children: ReactNode
}) {
  return (
    <div className="min-h-screen bg-paper-page">
      <header className="border-b-2 border-paper-rule bg-paper-card">
        <div className="mx-auto flex max-w-[1120px] items-center justify-between gap-4 px-6 py-4">
          <Link
            to="/"
            className="inline-flex min-h-tap items-center rounded-btn px-2 font-display text-[24px] text-ink-900"
          >
            Story Forge
          </Link>
        </div>
      </header>

      <main className="mx-auto flex max-w-[720px] flex-col items-center gap-8 px-6 py-12">
        <div className="w-full">
          <h1 className="font-display text-[40px] leading-[1.1] text-ink-900">{title}</h1>
          <p className="mt-3 max-w-[60ch] font-ui text-[17px] leading-[1.6] text-ink-700">{blurb}</p>
        </div>
        {children}
      </main>
    </div>
  )
}

export function SignInRoute() {
  const [params] = useSearchParams()
  const redirect = safeRedirectPath(params.get(REDIRECT_PARAM))

  return (
    <AuthFrame
      title="Sign in"
      blurb="Story Forge signs in the grown-up, never the child. A reader’s screen has no
             password, no email address and no account of its own."
    >
      <SignIn
        routing="path"
        path={SIGN_IN_PATH}
        signUpUrl={SIGN_UP_PATH}
        forceRedirectUrl={redirect}
        fallbackRedirectUrl={AFTER_SIGN_IN_PATH}
      />
    </AuthFrame>
  )
}

export function SignUpRoute() {
  const [params] = useSearchParams()
  const redirect = safeRedirectPath(params.get(REDIRECT_PARAM))

  return (
    <AuthFrame
      title="Create an account"
      blurb="One account, for the grown-up. Readers are added afterwards with a first name and a
             starting stage — a child never gets an email address, a birthday or a photo here."
    >
      <SignUp
        routing="path"
        path={SIGN_UP_PATH}
        signInUrl={SIGN_IN_PATH}
        forceRedirectUrl={redirect}
        fallbackRedirectUrl={AFTER_SIGN_IN_PATH}
      />
    </AuthFrame>
  )
}
