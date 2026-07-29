/**
 * The auth route constants and the two pure functions around them.
 *
 * Split out of `lib/auth.tsx` for one reason: `ui/ParentShell` needs the
 * sign-in path, and `lib/auth.tsx` needs ParentShell for its loading chrome.
 * Importing both ways is a module cycle, and a cycle whose bindings are read
 * during evaluation fails as a temporal-dead-zone error at runtime, in
 * production, on whichever module the bundler happened to order second. This
 * file has no React in it and imports nothing, so it can be the one both sides
 * depend on.
 *
 * That "imports nothing" is also why it is the first file in `apps/web` with a
 * real test suite behind it (`authPaths.test.ts`, DEVIATIONS 7.30): a pure
 * function carrying a security property is exactly what a test can settle.
 */

/** Where a signed-out parent is sent, and where `<SignIn>` is mounted. */
export const SIGN_IN_PATH = '/sign-in'
export const SIGN_UP_PATH = '/sign-up'
/** Where a parent lands after signing in when there is nothing to go back to. */
export const AFTER_SIGN_IN_PATH = '/app'
/**
 * The query parameter carrying the deep link a guard interrupted.
 *
 * **It is `next`, not `redirect_url`, and that is a security decision.** Measured
 * in a real browser: rendering `<SignIn forceRedirectUrl="/app">` on
 * `/sign-in?redirect_url=%2F%5Cevil.example%2Fx` — with no sign-in attempted and
 * nothing hostile passed to Clerk by this app — still logs
 *
 *   "Clerk: Redirect URL http://evil.example/x is not on one of the
 *    allowedRedirectOrigins, falling back to the default redirect URL."
 *
 * So Clerk reads `redirect_url` from the query string **by name, itself**,
 * whatever this app decides. While that was the parameter name, `safeRedirectPath`
 * was not the control on it at any value: Clerk's `allowedRedirectOrigins` was the
 * only thing standing between an attacker-chosen origin and a freshly minted
 * session, and DEVIATIONS 6.23 credited this file for work it was not doing
 * (DEVIATIONS 7.31).
 *
 * Under a name Clerk does not consume, the validated value is the only one in
 * play and this app's own check is genuinely the control. Clerk still gets its
 * allowlist as defence in depth — the point is that it is no longer the only
 * layer.
 */
export const REDIRECT_PARAM = 'next'

/**
 * The origin every candidate redirect is resolved against.
 *
 * A constant rather than `location.origin`, for two reasons. It keeps this file
 * free of the DOM, which is the property the module-cycle note above depends on
 * and the reason the function is testable at all. And the actual origin is
 * irrelevant: the question is "does this value stay on whatever origin it is
 * resolved against", and any origin answers it. `.invalid` is reserved by
 * RFC 2606 and can never resolve to a real host.
 */
const RESOLUTION_BASE = 'https://storyforge.invalid'

/**
 * Characters that make a string mean something other than it appears to.
 *
 * The WHATWG URL parser strips leading and trailing C0-control-or-space and
 * removes TAB, LF and CR from *anywhere* in a URL before it parses anything, and
 * it treats `\` as `/` in a special-scheme path. So `/\evil.example/x`,
 * `/<TAB>/evil.example` and `/<LF>//evil.example` are all `//evil.example` by the
 * time a browser has an opinion, and that is another origin.
 *
 * The origin comparison below already catches every one of those. This class
 * sits in front of it because a *same-origin* path carrying a NUL, a BOM or a
 * non-breaking space is not a path anything in this app produced either, and
 * because the line between "the parser strips it" and "the parser encodes it" is
 * a spec detail a security check should not be betting on. Both groups are
 * refused; only the first was ever a bypass, and `authPaths.test.ts` labels
 * which is which so the suite is not read as proving more than it does.
 *
 * Built with `new RegExp` from escapes rather than written as a literal: a
 * regex literal holding raw control characters is a source file whose meaning
 * depends on bytes nobody can see in a diff.
 */
const URL_HOSTILE_CHARS = new RegExp(
  '[' +
    '\\\\' + // backslash — `/` to the parser, an ordinary character to a prefix test
    '\\u0000-\\u0020' + // the C0 controls and space; TAB, LF and CR live here
    '\\u007f-\\u00a0' + // DEL, the C1 controls, NEL, and NBSP
    '\\u1680' + // Ogham space mark
    '\\u2000-\\u200f' + // the en/em space family and the bidi format characters
    '\\u2028\\u2029' + // line separator, paragraph separator
    '\\u202f\\u205f\\u3000' + // narrow NBSP, medium mathematical space, ideographic space
    '\\ufeff' + // byte order mark
    ']',
)

/**
 * A same-origin path from an untrusted query parameter, or the default.
 *
 * The check is an **origin comparison**, not a prefix test. It used to be the
 * latter — "starts with `/`, does not start with `//`" — and the Phase 6 audit
 * proved that let eight shapes through, among them `/\evil.example/x`, which a
 * browser resolves to `http://evil.example/x`. A prefix test on the raw string
 * asks a question about a string the URL parser never sees (DEVIATIONS 7.31).
 *
 * What comes back is the **resolved** `pathname + search + hash`, not the input.
 * A caller may hand this to `navigate()` or to `window.location`, so returning a
 * value that could still be re-parsed into something else would mean the
 * function had not finished its job. Dot segments are collapsed here rather than
 * downstream — which is also what makes the auth-loop check below hold for
 * `/app/../sign-in`.
 *
 * A value pointing back at the auth routes is rejected as well, because that is
 * a loop rather than a destination.
 */
export function safeRedirectPath(raw: string | null | undefined): string {
  if (!raw) return AFTER_SIGN_IN_PATH
  if (URL_HOSTILE_CHARS.test(raw)) return AFTER_SIGN_IN_PATH
  if (!raw.startsWith('/') || raw.startsWith('//')) return AFTER_SIGN_IN_PATH

  let url: URL
  try {
    url = new URL(raw, RESOLUTION_BASE)
  } catch {
    // An input the parser refuses outright is not a destination either.
    return AFTER_SIGN_IN_PATH
  }
  if (url.origin !== RESOLUTION_BASE) return AFTER_SIGN_IN_PATH

  const path = `${url.pathname}${url.search}${url.hash}`
  if (path.startsWith(SIGN_IN_PATH) || path.startsWith(SIGN_UP_PATH)) return AFTER_SIGN_IN_PATH
  return path
}

/** `/sign-in?next=/dashboard/3` — the deep link a guard interrupted. */
export function signInPathFor(pathname: string, search: string): string {
  const target = `${pathname}${search}`
  if (target === '/' || target === AFTER_SIGN_IN_PATH) return SIGN_IN_PATH
  return `${SIGN_IN_PATH}?${REDIRECT_PARAM}=${encodeURIComponent(target)}`
}
