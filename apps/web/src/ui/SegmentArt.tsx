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
 */

import { useState } from 'react'
import type { Asset } from '../lib/api'
import Placeholder from './Placeholder'

export interface SegmentArtProps {
  asset: Asset | null
  className?: string
}

export default function SegmentArt({ asset, className = '' }: SegmentArtProps) {
  const [failed, setFailed] = useState(false)
  const frame = `overflow-hidden rounded-card border-[3px] border-ink-900 bg-paper-warm ${className}`

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
