/**
 * SegmentArt — the §06 frame every illustration renders inside.
 *
 * "4:3, sits above the story text, full card width, 3px ink border in kid mode.
 * Never behind text." The frame is fixed at 4:3 whether or not the art exists,
 * so the reading view's geometry is identical in both cases and a story that
 * gains art later does not reflow the text a child is mid-sentence in.
 *
 * `alt_text` is mandatory on every asset row (§06); if a row somehow arrives
 * without one, the image is marked decorative rather than announced with a
 * filename, which is worse than silence.
 *
 * **`themeSlug` is required, and that is the point.** §06's fallback is "the
 * striped placeholder with the theme cover tinted at 40%", so the frame cannot
 * draw its own fallback without knowing the theme. Optional, it would be omitted
 * — which is exactly how `assetUrl` came to be missing on the celebration screen
 * (DEVIATIONS 13.4, 13.7). The only way this regresses is a caller not passing
 * it, so a caller not passing it does not compile.
 */

import { useState } from 'react'
import type { Asset } from '../lib/api'
import { coverFor } from '../lib/art'
import Placeholder from './Placeholder'

/**
 * §06's frame, exported because `scripts/fit-check.mjs` measures the pool against
 * this exact box and a gate that re-spells the layout measures a different one
 * (DEVIATIONS 13.2).
 */
export const SEGMENT_ART_FRAME =
  'overflow-hidden rounded-card border-[3px] border-ink-900 bg-paper-warm'

export interface SegmentArtProps {
  asset: Asset | null
  /** `themes.slug`, for §06's tinted-cover fallback. Required — see above. */
  themeSlug: string
  className?: string
}

export default function SegmentArt({ asset, themeSlug, className = '' }: SegmentArtProps) {
  const [failed, setFailed] = useState(false)
  const frame = `${SEGMENT_ART_FRAME} ${className}`

  if (!asset || failed) {
    // The caption is the accessible name only, never visible text. Alt text is
    // written for a parent or a screen reader and has never been through the
    // decoder — printing it beside the story would put undecodable English in
    // front of a child, which is the one thing the whole pipeline exists to
    // prevent (§0, §13).
    return (
      <Placeholder
        ratio="4:3"
        label={asset?.alt_text ?? 'a picture is on its way'}
        showLabel={false}
        tintUrl={coverFor(themeSlug)}
        className={frame}
      />
    )
  }

  return (
    <div className={frame} style={{ aspectRatio: '4 / 3' }}>
      <img
        src={asset.url}
        alt={asset.alt_text ?? ''}
        onError={() => setFailed(true)}
        className="h-full w-full object-cover"
      />
    </div>
  )
}
