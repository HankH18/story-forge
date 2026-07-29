/**
 * The auth route constants and the two pure functions around them.
 *
 * Split out of `lib/auth.tsx` for one reason: `ui/ParentShell` needs the
 * sign-in path and the 403 sentence, and `lib/auth.tsx` needs ParentShell for
 * its loading chrome. Importing both ways is a module cycle, and a cycle whose
 * bindings are read during evaluation fails as a temporal-dead-zone error at
 * runtime, in production, on whichever module the bundler happened to order
 * second. This file has no React in it and imports nothing, so it can be the
 * one both sides depend on.
 */

/** Where a signed-out parent is sent, and where `<SignIn>` is mounted. */
export const SIGN_IN_PATH = '/sign-in'
export const SIGN_UP_PATH = '/sign-up'
/** Where a parent lands after signing in when there is nothing to go back to. */
export const AFTER_SIGN_IN_PATH = '/app'
/** The query parameter carrying the deep link a guard interrupted. */
export const REDIRECT_PARAM = 'redirect_url'

/**
 * The 403 sentence, in one place because it is the only thing the client can do
 * about a Clerk configuration only a human can change.
 *
 * Verified against the live instance rather than assumed: a REAL session token,
 * minted through Clerk's Backend API and decoded, carries exactly
 * `exp, fva, iat, iss, nbf, sid, sts, sub, v` — no `public_metadata`, no
 * `user_public_metadata`, no `metadata`, no `role` — even for a user whose
 * `public_metadata` IS `{"role":"admin"}` server-side, and the instance has no
 * JWT templates at all. So `auth.py::role_from_claims` resolves every real
 * session to `parent`, correctly, and every `require_admin` route answers 403
 * until someone customises the session token in the Clerk dashboard.
 *
 * Naming that is the entire available fix. Fetching the role from the API or
 * trusting a client-side value would defeat the property `auth.py`'s docstring
 * is built on — the role is re-read from the SIGNED token on every request, so
 * revoking admin takes effect on the next call rather than the next login.
 */
export const MISSING_ROLE_CLAIM_DETAIL =
  'The API accepted this session but not this screen. Clerk’s default session ' +
  'token carries no metadata, so the API reads every signed-in user as a ' +
  'parent — including admins. Someone with access to the Clerk dashboard has ' +
  'to add the claim (Sessions → customize session token → ' +
  '{"public_metadata": "{{user.public_metadata}}"}), and then sign in again.'

/**
 * A same-origin path from an untrusted query parameter, or the default.
 *
 * One leading slash and no second one. `//evil.example/x` is a
 * protocol-relative URL that a browser treats as another origin, so it is the
 * shape that turns a post-login redirect into an open redirect with a freshly
 * minted session attached. A `redirect_url` pointing back at the auth routes is
 * also rejected, because that is a loop rather than a destination.
 */
export function safeRedirectPath(raw: string | null | undefined): string {
  if (!raw) return AFTER_SIGN_IN_PATH
  if (!raw.startsWith('/') || raw.startsWith('//')) return AFTER_SIGN_IN_PATH
  if (raw.startsWith(SIGN_IN_PATH) || raw.startsWith(SIGN_UP_PATH)) return AFTER_SIGN_IN_PATH
  return raw
}

/** `/sign-in?redirect_url=/dashboard/3` — the deep link a guard interrupted. */
export function signInPathFor(pathname: string, search: string): string {
  const target = `${pathname}${search}`
  if (target === '/' || target === AFTER_SIGN_IN_PATH) return SIGN_IN_PATH
  return `${SIGN_IN_PATH}?${REDIRECT_PARAM}=${encodeURIComponent(target)}`
}
