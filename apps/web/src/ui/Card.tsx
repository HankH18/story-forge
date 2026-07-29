/**
 * Card — style guide §12 contract: `surface: kid | parent | admin`.
 *
 * The surface is the whole "whimsy dial" (§01) expressed as three tokens:
 * border weight, shadow, and radius. Kid gets the 3px ink outline and the hard
 * 6px offset; parent gets 2px muted rule and a soft-ink 4px offset; admin gets
 * a 1px rule and no shadow at all. Nothing else about a card changes between
 * surfaces — same paper, same ink, same radius scale — which is what keeps the
 * three modes recognisably one product.
 *
 * Never white: every surface is `paper-card` (#FFFBF0) on `paper-page`
 * (#FDF6E7). §02 forbids pure #FFF anywhere.
 */

import type { HTMLAttributes, ReactNode } from 'react'

export type CardSurface = 'kid' | 'parent' | 'admin'

const SURFACE: Record<CardSurface, string> = {
  kid: 'sf-lift-kid rounded-card border-[3px] border-ink-900 bg-paper-card',
  parent: 'sf-lift-parent rounded-card border-2 border-paper-rule bg-paper-card',
  admin: 'rounded-[4px] border border-paper-shade bg-paper-card',
}

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  surface?: CardSurface
  children?: ReactNode
}

export default function Card({
  surface = 'kid',
  className = '',
  children,
  ...rest
}: CardProps) {
  return (
    <div className={[SURFACE[surface], className].join(' ')} {...rest}>
      {children}
    </div>
  )
}
