/**
 * `safeRedirectPath` — the post-login redirect validator (DEVIATIONS 6.23, 7.31).
 *
 * This file exists because the function did not do what its own comment said.
 * The Phase 6 audit (C7) drove `/sign-in?redirect_url=%2F%5Cevil.example%2Fx`
 * through the real sign-in route: the validator returned the value unchanged,
 * the browser resolved it to `http://evil.example/x`, and the only thing that
 * stopped the redirect was Clerk's own `allowedRedirectOrigins`. Five shapes got
 * through while the three the comment named were correctly rejected.
 *
 * The central assertion is therefore not a list of cases — it is a **property**:
 *
 *   for every input, `new URL(safeRedirectPath(input), origin).origin === origin`
 *
 * and its anti-vacuity twin, which is what makes this file worth its bytes:
 *
 *   every member of OFF_ORIGIN is a value a browser really does resolve to
 *   another origin.
 *
 * Each group below says plainly what it proves. `OFF_ORIGIN` is the only group
 * that contains bypasses; `ENCODED_NOT_STRIPPED` is a strictness ratchet on
 * inputs that were never bypasses, and is labelled so that nobody reads it as
 * one.
 */

import { describe, expect, test } from 'bun:test'
import {
  AFTER_SIGN_IN_PATH,
  REDIRECT_PARAM,
  SIGN_IN_PATH,
  SIGN_UP_PATH,
  safeRedirectPath,
  signInPathFor,
} from './authPaths'

/** Whatever a browser would actually resolve against. Any origin will do. */
const ORIGIN = 'http://localhost:5173'

function resolvedOrigin(path: string): string {
  return new URL(path, ORIGIN).origin
}

/**
 * Values a browser resolves to ANOTHER ORIGIN. Measured, not reasoned.
 *
 * The WHATWG URL parser strips leading and trailing C0-control-or-space,
 * removes TAB/LF/CR from anywhere in the input, and treats `\` as `/` in a
 * special-scheme path — so a prefix test on the raw string is asking a question
 * about a string the parser never sees.
 *
 * The eight marked NEW are the ones the previous implementation let through;
 * they are red against it, each naming `http://evil.example`. The rest were
 * already refused and are here so a fix cannot trade one bypass for another.
 */
const OFF_ORIGIN: Array<[label: string, raw: string]> = [
  ['backslash — NEW', '/\\evil.example/x'],
  ['backslash then slash — NEW', '/\\/evil.example'],
  ['backslash doubled — NEW', '/\\\\evil.example'],
  ['backslash then userinfo — NEW', '/\\@evil.example'],
  ['tab — NEW', '/\t/evil.example'],
  ['line feed — NEW', '/\n//evil.example'],
  ['CRLF — NEW', '/\r\n//evil.example'],
  ['tab inside a backslash pair — NEW', '/\\\t\\evil.example'],
  ['protocol relative', '//evil.example/x'],
  ['absolute https', 'https://evil.example'],
  ['absolute http', 'http://evil.example/app'],
  ['leading space, then protocol relative', ' //evil.example'],
  ['leading tab, then protocol relative', '\t//evil.example'],
  ['javascript scheme', 'javascript:alert(1)'],
  ['data scheme', 'data:text/html,<script>alert(1)</script>'],
]

/**
 * Control, whitespace and format characters the parser PERCENT-ENCODES rather
 * than strips. **None of these is a bypass** — every one resolves on-origin as a
 * raw string, verified below. They are rejected anyway, because a path
 * containing a NUL or a BOM is not a path anything in this app generated, and
 * because the difference between "stripped" and "encoded" is a parser detail
 * this function should not be betting on.
 */
const ENCODED_NOT_STRIPPED: Array<[label: string, raw: string]> = [
  ['vertical tab', '/\v//evil.example'],
  ['form feed', '/\f//evil.example'],
  ['NUL', '/\0//evil.example'],
  ['U+2028 line separator', '/ //evil.example'],
  ['U+0085 next line', '///evil.example'],
  ['non-breaking space', '/ //evil.example'],
  ['ideographic space', '/　//evil.example'],
  ['byte order mark', '/﻿//evil.example'],
  ['plain space', '/ //evil.example'],
]

/** Not a path at all. On-origin once resolved, but never a destination. */
const NOT_A_PATH: Array<[label: string, raw: string]> = [
  ['relative, no leading slash', 'app'],
  ['bare host with a path', 'evil.example/x'],
  ['a fragment only', '#sounds'],
  ['a query only', '?tab=sounds'],
]

/** A redirect back to the auth routes is a loop, not a destination. */
const AUTH_LOOP: Array<[label: string, raw: string]> = [
  ['the sign-in route', SIGN_IN_PATH],
  ['the sign-in route with a parameter', `${SIGN_IN_PATH}?${REDIRECT_PARAM}=/app`],
  ['a Clerk sign-in sub-step', `${SIGN_IN_PATH}/factor-one`],
  ['the sign-up route', SIGN_UP_PATH],
  ['a loop spelled with dot segments', '/app/../sign-in'],
]

describe('safeRedirectPath', () => {
  test('an absent parameter is the default destination', () => {
    expect(safeRedirectPath(null)).toBe(AFTER_SIGN_IN_PATH)
    expect(safeRedirectPath(undefined)).toBe(AFTER_SIGN_IN_PATH)
    expect(safeRedirectPath('')).toBe(AFTER_SIGN_IN_PATH)
  })

  test.each(OFF_ORIGIN)('%s really does leave the origin as a raw value', (_label, raw) => {
    // Anti-vacuity. Without this, a corpus of harmless strings would pass every
    // other assertion in this file and prove nothing at all.
    let origin: string
    try {
      origin = new URL(raw, ORIGIN).origin
    } catch {
      origin = 'threw'
    }
    expect(origin).not.toBe(ORIGIN)
  })

  test.each(OFF_ORIGIN)('%s is neutralised', (_label, raw) => {
    // Two statements on purpose: what the function promises, and what a browser
    // does with the answer. The old implementation kept the first for three
    // shapes and broke the second for eight.
    expect(safeRedirectPath(raw)).toBe(AFTER_SIGN_IN_PATH)
    expect(resolvedOrigin(safeRedirectPath(raw))).toBe(ORIGIN)
  })

  test.each(ENCODED_NOT_STRIPPED)('%s is refused, though it was never a bypass', (_label, raw) => {
    // The second assertion records WHY this group is not the one above.
    expect(resolvedOrigin(raw)).toBe(ORIGIN)
    expect(safeRedirectPath(raw)).toBe(AFTER_SIGN_IN_PATH)
  })

  test.each(NOT_A_PATH)('%s is not a destination', (_label, raw) => {
    expect(safeRedirectPath(raw)).toBe(AFTER_SIGN_IN_PATH)
  })

  test.each(AUTH_LOOP)('%s is a loop, not a destination', (_label, raw) => {
    expect(safeRedirectPath(raw)).toBe(AFTER_SIGN_IN_PATH)
  })

  test('an ordinary deep link survives intact', () => {
    expect(safeRedirectPath('/app')).toBe('/app')
    expect(safeRedirectPath('/app/add-kid')).toBe('/app/add-kid')
    expect(safeRedirectPath('/dashboard/3')).toBe('/dashboard/3')
    expect(safeRedirectPath('/dashboard/3?tab=sounds')).toBe('/dashboard/3?tab=sounds')
    expect(safeRedirectPath('/dashboard/3#sounds')).toBe('/dashboard/3#sounds')
    expect(safeRedirectPath('/read/1648')).toBe('/read/1648')
  })

  test('percent-encoded separators stay encoded rather than becoming a host', () => {
    // `%2F` and `%5C` are legitimate path characters and the parser does not
    // decode them, so these are same-origin paths and passing them through is
    // correct. They are here because "reject anything containing a slash" would
    // have been the wrong fix.
    expect(safeRedirectPath('/%2F%2Fevil.example')).toBe('/%2F%2Fevil.example')
    expect(safeRedirectPath('/%5Cevil.example')).toBe('/%5Cevil.example')
    expect(resolvedOrigin('/%2F%2Fevil.example')).toBe(ORIGIN)
  })

  test('a Unicode slash lookalike is a path character, not a host separator', () => {
    // U+FF0F FULLWIDTH SOLIDUS. Asserted rather than assumed, because "it looks
    // like a slash" is the kind of reasoning that produces a false reject or a
    // bypass depending on which way the author guessed.
    expect(resolvedOrigin(safeRedirectPath('/／/evil.example'))).toBe(ORIGIN)
  })

  test('the value returned is normalised, not the raw input', () => {
    // The caller may hand the result to `navigate()`. If what comes back can be
    // re-parsed into something else, the function has not finished its job — so
    // dot segments are resolved here rather than downstream.
    expect(safeRedirectPath('/dashboard/../app')).toBe('/app')
    expect(safeRedirectPath('/dashboard/../../../etc/passwd')).toBe('/etc/passwd')
  })
})

describe('signInPathFor', () => {
  test('the deep link rides along encoded', () => {
    expect(signInPathFor('/dashboard/3', '')).toBe(
      `${SIGN_IN_PATH}?${REDIRECT_PARAM}=%2Fdashboard%2F3`,
    )
    expect(signInPathFor('/dashboard/3', '?tab=sounds')).toBe(
      `${SIGN_IN_PATH}?${REDIRECT_PARAM}=%2Fdashboard%2F3%3Ftab%3Dsounds`,
    )
  })

  test('the default destination needs no parameter', () => {
    expect(signInPathFor('/', '')).toBe(SIGN_IN_PATH)
    expect(signInPathFor(AFTER_SIGN_IN_PATH, '')).toBe(SIGN_IN_PATH)
  })

  test('every path it produces round-trips through safeRedirectPath', () => {
    // The two functions are a pair: one writes the parameter, the other reads
    // it. A validator that rejected its own partner's output would send every
    // interrupted deep link to `/app`, and the guards would look like they were
    // working.
    for (const [pathname, search] of [
      ['/dashboard/3', ''],
      ['/dashboard/3', '?tab=sounds'],
      ['/app/add-kid', ''],
      ['/read/1648', ''],
    ] as const) {
      const url = new URL(signInPathFor(pathname, search), ORIGIN)
      expect(safeRedirectPath(url.searchParams.get(REDIRECT_PARAM))).toBe(`${pathname}${search}`)
    }
  })
})
