/**
 * Placeholder — style guide §06 / §12 contract: `ratio, label`.
 *
 * §06 in full, and the second clause was missing for five phases: "If tag
 * matching finds nothing, render the striped placeholder **with the theme cover
 * tinted at 40%** — never an empty box, never a broken-image icon." This
 * rendered `.sf-stripes` and nothing else, so a segment with no asset was a
 * blank striped half-screen — one story in five had at least one, and five of
 * them were on page 1 (DEVIATIONS 13.7).
 *
 * This is the app-wide answer to a missing asset, and it is also what a *failed*
 * asset resolves to: `<img>` gets an `onError` that swaps to this, so a URL the
 * server has but the CDN has not degrades the same way as a URL that was never
 * there. The alternative — a torn-page icon and alt text — reads to a
 * five-year-old as "broken", and §01 says nothing can fail visibly. The tint
 * obeys the same rule: it drops itself on `onError` rather than becoming the
 * broken-image icon §06 names.
 *
 * It keeps the frame's aspect ratio so the layout does not reflow when real art
 * arrives, and it carries an accessible name, because a decorative-only
 * placeholder would silently drop the information that a picture belongs here.
 * The tint is `aria-hidden` and adds nothing to that name: "the theme cover, at
 * 40%" is a fact about the fallback, not about this page's story.
 */

import { useState } from 'react'

export type Ratio = '4:3' | '1:1' | '16:9'

const RATIO: Record<Ratio, string> = {
  '4:3': '4 / 3',
  '1:1': '1 / 1',
  '16:9': '16 / 9',
}

export interface PlaceholderProps {
  ratio?: Ratio
  /** Accessible name *and*, when `showLabel`, the visible caption. */
  label?: string
  showLabel?: boolean
  /** §06's required tint: the theme cover, at 40%, over the stripes. */
  tintUrl?: string | null
  className?: string
}

export default function Placeholder({
  ratio = '4:3',
  label = 'a picture is on its way',
  showLabel = true,
  tintUrl = null,
  className = '',
}: PlaceholderProps) {
  const [tintFailed, setTintFailed] = useState(false)
  const showTint = Boolean(tintUrl) && !tintFailed

  return (
    <div
      role="img"
      aria-label={label}
      style={{ aspectRatio: RATIO[ratio] }}
      className={`sf-stripes relative flex items-center justify-center overflow-hidden ${className}`}
    >
      {showTint ? (
        <img
          src={tintUrl ?? undefined}
          alt=""
          aria-hidden="true"
          onError={() => setTintFailed(true)}
          className="pointer-events-none absolute inset-0 h-full w-full object-cover opacity-40"
        />
      ) : null}
      {showLabel ? (
        <span className="relative rounded-chip bg-paper-card/90 px-3 py-1 font-ui text-[15px] text-ink-500">
          {label}
        </span>
      ) : null}
    </div>
  )
}
