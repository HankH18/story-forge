/**
 * Clerk on the web side — the provider, the token bridge, and the two route
 * guards (build doc §9, §13, §16 phase 6; DEVIATIONS 6.16–6.23).
 *
 * `services/api/storyforge/auth.py` has verified Clerk RS256 sessions since
 * phase 6. This file is the other half: until it existed, `.env.local` carried
 * `VITE_CLERK_PUBLISHABLE_KEY` and nothing read it, no request carried an
 * `Authorization` header, and the whole app worked only because the API was
 * running §18's `DEMO_BYPASS_AUTH`. Every screen looked signed in and no layer
 * said otherwise.
 *
 * Three decisions are load-bearing here.
 *
 * **The provider lives under the router.** `<ClerkProvider>` gets `routerPush`
 * and `routerReplace` from `useNavigate`, so when Clerk finishes a sign-in and
 * navigates to `/app` it goes through react-router. Without them Clerk pushes
 * history itself, the URL changes and the rendered route does not — which looks
 * exactly like a hung sign-in button.
 *
 * **The parent routes and the kid route fail in opposite directions.** A signed
 * out parent is sent to `/sign-in`, because they can act on it. A signed out
 * child is sent to `/` — §01 says kid mode has no error state, and a login form
 * is an error state a five-year-old cannot clear (DEVIATIONS 6.18).
 *
 * **Roles still come from the signed token, never from here.** `auth.py` is
 * explicit that `public_metadata.role` is re-read from the JWT on every request
 * so the dashboard can revoke admin immediately. Nothing in this file reads,
 * caches or asserts a role; the only role-shaped thing in the web app is the
 * sentence shown when the API answers 403, which explains the claim that is
 * missing rather than inventing one (lib/authPaths.ts, DEVIATIONS 6.20).
 */

import { ClerkProvider, useAuth } from '@clerk/clerk-react'
import type { ReactNode } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { setAuthTokenGetter } from './api'
import { AFTER_SIGN_IN_PATH, SIGN_IN_PATH, SIGN_UP_PATH, signInPathFor } from './authPaths'
import ParentShell from '../ui/ParentShell'
import PrinterWorking from '../ui/PrinterWorking'

/**
 * `<ClerkProvider>`, wired to react-router. Must be rendered inside the router.
 *
 * `signInUrl`/`signUpUrl` point at this app's own routes rather than Clerk's
 * hosted account portal, so `<RedirectToSignIn/>` and every "sign up instead"
 * link inside Clerk's own UI stay in the SPA.
 */
export function StoryForgeClerkProvider({
  publishableKey,
  children,
}: {
  publishableKey: string
  children: ReactNode
}) {
  const navigate = useNavigate()

  return (
    <ClerkProvider
      publishableKey={publishableKey}
      routerPush={(to) => navigate(to)}
      routerReplace={(to) => navigate(to, { replace: true })}
      signInUrl={SIGN_IN_PATH}
      signUpUrl={SIGN_UP_PATH}
      signInFallbackRedirectUrl={AFTER_SIGN_IN_PATH}
      signUpFallbackRedirectUrl={AFTER_SIGN_IN_PATH}
      afterSignOutUrl="/"
      appearance={CLERK_APPEARANCE}
    >
      <ClerkTokenBridge />
      {children}
    </ClerkProvider>
  )
}

/**
 * Hands Clerk's `getToken` to `lib/api.ts`, which is plain async functions and
 * cannot call a hook.
 *
 * **Registered during render, not in an effect.** React runs a child's effects
 * before its ancestors', so a route component's first `fetch` — fired from its
 * own mount effect — would beat an ancestor's `useEffect` here and go out with
 * no `Authorization` header. Every page load would 401 once, and the parent
 * screens would show "please sign in again" to someone who just signed in.
 * Assignment during render is idempotent, so StrictMode's double render costs
 * nothing, and the getter is never cleared: `getToken` on a signed-out Clerk
 * resolves to `null`, which `authHeader` already treats as "send no header".
 */
function ClerkTokenBridge() {
  const { getToken } = useAuth()
  setAuthTokenGetter(() => getToken())
  return null
}

/**
 * §9's `(parent)` routes: `/app`, `/app/add-kid`, `/dashboard/:kidId`.
 *
 * Signed out is a redirect, not a 401 toast and not a blank screen — those are
 * the two shapes this guard exists to prevent. The deep link rides along in
 * `?next=` so the parent lands where they were going (`next`, not `redirect_url`:
 * Clerk consumes that name itself — lib/authPaths.ts, DEVIATIONS 7.31).
 */
export function RequireParent({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn } = useAuth()
  const location = useLocation()

  if (!isLoaded) {
    return (
      <ParentShell>
        <p
          role="status"
          className="font-mono text-[13px] uppercase tracking-[0.14em] text-ink-500"
        >
          Checking your session…
        </p>
      </ParentShell>
    )
  }

  if (!isSignedIn) {
    return <Navigate to={signInPathFor(location.pathname, location.search)} replace />
  }

  return <>{children}</>
}

/**
 * §9's `(admin)` routes: the `/admin` tree (DEVIATIONS 9.20-9.23).
 *
 * **This guard checks a SESSION, not a role, and that is deliberate.** The file
 * docstring above is explicit that roles come from the signed token and never from
 * here — `auth.py` re-reads `public_metadata.role` from the JWT on every request
 * so the Clerk dashboard can revoke admin immediately. A client-side role test
 * would be a second opinion able to disagree with the only one that matters, and
 * it would disagree in the dangerous direction the first time Clerk's client cache
 * is ahead of a revocation.
 *
 * It would also have to read a claim that is frequently not there. Clerk's DEFAULT
 * session token carries NO metadata (DEVIATIONS 6.13, measured by minting a real
 * token and decoding it), so a guard reading `user.publicMetadata.role` would
 * either lock every admin out of a portal the API is willing to serve them, or —
 * falling back to "allow" — let every parent in. Neither is a gate.
 *
 * So the role gate is the API's 403, rendered by `ui/AdminShell`: it asks the API
 * once and renders nothing else until the answer arrives. This function's only job
 * is the signed-out case, and its answer is the parent routes' answer — a redirect
 * carrying the deep link, because an admin who followed a link to
 * `/admin/evals/114` should land there after signing in.
 */
export function RequireAdmin({ children }: { children: ReactNode }) {
  return <RequireParent>{children}</RequireParent>
}

/**
 * `/read/:kidId` — the same check, the opposite failure.
 *
 * §01: kid mode has no error state. A child handed a tablet cannot type an
 * email address, so a sign-in wall here is a dead end wearing a form. Signed
 * out, the session bounces to `/`, which is the parent's entrance. While Clerk
 * is still loading the child sees §07's printer — the one wait screen kid mode
 * is allowed to show — rather than a blank div or a flash of a story that is
 * about to be replaced by a redirect.
 */
export function RequireKidSession({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn } = useAuth()

  if (!isLoaded) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-paper-page px-6">
        <PrinterWorking message="Getting your stories…" />
      </main>
    )
  }

  if (!isSignedIn) return <Navigate to="/" replace />

  return <>{children}</>
}

/**
 * Clerk's widgets, themed rather than restyled (DEVIATIONS 6.22).
 *
 * `variables` only: the palette, the type and the 16px `btn` radius from style
 * guide §12, so an SDK update can change a widget's internals without
 * repainting this app. The two `elements` overrides are §11 floors, not taste —
 * Clerk's default `<UserButton>` trigger is a 28px avatar, under the 48px
 * minimum every control in this app has to meet. Both class names are literals
 * in this file, so Tailwind's scanner finds them and the production bundle
 * keeps them (STATE invariant 7).
 */
const CLERK_APPEARANCE = {
  variables: {
    colorPrimary: '#C0453B', // berry-500
    colorText: '#2B2118', // ink-900
    colorTextSecondary: '#6F5B45', // ink-500
    colorBackground: '#FFFBF0', // paper-card
    colorInputBackground: '#FDF6E7', // paper-page
    colorInputText: '#2B2118',
    colorDanger: '#92302A', // berry-700
    colorSuccess: '#4C7C51', // leaf-500
    colorWarning: '#D8A93C', // gold-500
    fontFamily: 'Nunito, system-ui, sans-serif',
    borderRadius: '16px', // §12 `btn`
  },
  elements: {
    userButtonTrigger: 'min-h-tap min-w-tap',
    userButtonAvatarBox: 'h-[40px] w-[40px]',
  },
} as const
