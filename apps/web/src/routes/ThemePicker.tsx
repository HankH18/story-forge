/**
 * Theme picker — §11.2 step 1, style guide §07 "Theme cards — picker, 3–6
 * across".
 *
 * The first screen of a kid session and the only place a child expresses a
 * preference. One job, at most six choices, every one of them a 1:1 card with a
 * picture and a name — no search, no free text, no "other" (§13: kid mode
 * renders no text input, ever).
 *
 * The card renders the theme's §06 COVER — 1:1, one bold subject centred,
 * legible at 180px, drawn to survive a circular crop. This comment used to say
 * "cover art is not shipped yet, so the card falls back to `themes.emoji`. §07
 * carves out exactly one exception to 'no emoji as artwork' and this is it ...
 * If a cover ever arrives on the theme row it takes over and the emoji retires."
 * It has arrived and the emoji has retired (DEVIATIONS 11.29).
 *
 * The emoji is still the fallback, and still the documented one: a theme slug
 * with no cover in `lib/art.ts` renders it rather than a blank card, and a cover
 * whose file fails to load demotes to it too. §01 — nothing here can look broken.
 *
 * The theme name is set in Andika: it is a word a child reads, and §03 makes
 * Andika non-negotiable for choice labels.
 *
 * **This is also the one screen in kid mode with a way OUT of it** (`onExit`,
 * DEVIATIONS 27.2). Kid mode refuses the platform back gesture now, so the adult
 * needs a door that a child does not stumble through, and it belongs here rather
 * than in the reading view: §10 fixes the reading chrome at three controls, and
 * the picker is where a parent already is when they hand the tablet over and
 * when they take it back. `onExit` is optional so the component stays renderable
 * without one — the harness and the "no story" fallback both do that.
 */

import { useState } from 'react'
import type { Theme } from '../lib/api'
import { coverFor } from '../lib/art'
import GrownUpExit from '../ui/GrownUpExit'

export interface ThemePickerProps {
  themes: Theme[]
  readerName?: string
  onPick: (theme: Theme) => void
  /** The adult's way out of kid mode. Omitted, the screen simply has none. */
  onExit?: () => void
  /** Harness override for the hold duration; the product never passes it. */
  exitHoldMs?: number
}

export default function ThemePicker({
  themes,
  readerName,
  onPick,
  onExit,
  exitHoldMs,
}: ThemePickerProps) {
  // §07: 3–6 cards. More than six is a wall, fewer than three is not a choice.
  const shown = themes.slice(0, 6)
  /** Slugs whose cover file did not load. Falls back to the emoji, never a gap. */
  const [failed, setFailed] = useState<Set<string>>(() => new Set())

  return (
    <main
      className="sf-page-in mx-auto flex min-h-screen w-full max-w-[720px] flex-col
                 justify-center gap-10 px-6 py-10"
    >
      <header className="flex flex-col gap-3">
        {readerName ? (
          <p className="font-hand text-[24px] text-ink-500">hello {readerName}</p>
        ) : null}
        <h1 className="font-display text-[40px] leading-[1.15] text-ink-900">
          What is your story about?
        </h1>
      </header>

      <ul className="grid grid-cols-1 gap-6 sm:grid-cols-3">
        {shown.map((theme) => {
          const cover = coverFor(theme.slug)
          return (
          <li key={theme.id}>
            {/* The whole card is the button: one card, one job, one target —
                and the focus ring then lands on the thing a finger hits. */}
            <button
              type="button"
              onClick={() => onPick(theme)}
              aria-label={`read a story about ${theme.display_name}`}
              className="sf-lift-kid flex min-h-tap-kid w-full flex-col items-center gap-4
                         rounded-card border-[3px] border-ink-900 bg-paper-card p-5
                         transition-transform duration-[90ms] ease-out"
            >
              <span
                aria-hidden="true"
                className="flex aspect-square w-full items-center justify-center overflow-hidden
                           rounded-chip border-2 border-paper-rule bg-paper-warm text-[72px]
                           leading-none"
              >
                {cover && !failed.has(theme.slug) ? (
                  <img
                    src={cover}
                    alt=""
                    onError={() =>
                      setFailed((prev) => {
                        const next = new Set(prev)
                        next.add(theme.slug)
                        return next
                      })
                    }
                    className="h-full w-full object-contain"
                  />
                ) : (
                  (theme.emoji || '📖')
                )}
              </span>
              <span className="font-read text-[28px] text-ink-900">{theme.display_name}</span>
            </button>
          </li>
          )
        })}
      </ul>

      {onExit ? (
        <footer className="flex justify-center">
          <GrownUpExit onExit={onExit} {...(exitHoldMs ? { holdMs: exitHoldMs } : {})} />
        </footer>
      ) : null}
    </main>
  )
}
