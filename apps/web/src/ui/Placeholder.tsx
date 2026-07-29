/**
 * Placeholder — style guide §06 / §12 contract: `ratio, label`.
 *
 * "If tag matching finds nothing, render the striped placeholder — never an
 * empty box, never a broken-image icon." This is the app-wide answer to a
 * missing asset, and it is also what a *failed* asset resolves to: `<img>` gets
 * an `onError` that swaps to this, so a URL the server has but the CDN has not
 * degrades the same way as a URL that was never there. The alternative — a
 * torn-page icon and alt text — reads to a five-year-old as "broken", and §01
 * says nothing can fail visibly.
 *
 * It keeps the frame's aspect ratio so the layout does not reflow when real art
 * arrives, and it carries an accessible name, because a decorative-only
 * placeholder would silently drop the information that a picture belongs here.
 */

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
  className?: string
}

export default function Placeholder({
  ratio = '4:3',
  label = 'a picture is on its way',
  showLabel = true,
  className = '',
}: PlaceholderProps) {
  return (
    <div
      role="img"
      aria-label={label}
      style={{ aspectRatio: RATIO[ratio] }}
      className={`sf-stripes flex items-center justify-center overflow-hidden ${className}`}
    >
      {showLabel ? (
        <span className="rounded-chip bg-paper-card/90 px-3 py-1 font-ui text-[15px] text-ink-500">
          {label}
        </span>
      ) : null}
    </div>
  )
}
