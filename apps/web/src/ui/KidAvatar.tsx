/**
 * KidAvatar — style guide §08: "Avatar is the initial on a rotating palette
 * tint, never a photo upload."
 *
 * The "never" is the component. There is no `src` prop, no `image`, no `upload`
 * — §13 forbids collecting a child's photo, and the cheapest way for one to
 * arrive anyway is a component that *can* take one. This one cannot, so no
 * future screen can accidentally ask for it.
 *
 * The tint rotates on the kid's **id**, not on their position in the list.
 * Position changes the moment a sibling is added, and an avatar that changes
 * colour when someone else joins is a small lie about identity to a family that
 * has just started calling it "Maya's gold one".
 */

import type { ReactNode } from 'react'

/**
 * §02's accent tints, at the 100/300 end where ink-900 sits at high contrast.
 * §08 shows gold-300 and sky-100 explicitly; the other three are the remaining
 * accent families at the same lightness, so the set stays inside the one palette
 * the whole app shares rather than introducing an "avatar colour" system.
 */
const TINTS = ['#F2D48A', '#E2EDF2', '#E7F0E2', '#F8E3DF', '#FBEFD4'] as const

export function tintFor(kidId: number): string {
  // Non-negative modulo: an id is always ≥ 1 in practice, but a negative index
  // into this array is a blank avatar and that is not worth risking.
  const i = ((kidId % TINTS.length) + TINTS.length) % TINTS.length
  return TINTS[i]
}

/**
 * The initial, not the name.
 *
 * `codePointAt`-aware slicing rather than `name[0]`: a name starting outside the
 * BMP would otherwise render half a surrogate pair. First names are the only
 * data this app holds about a child and mangling one is a bad first impression.
 */
export function initialFor(name: string): string {
  const trimmed = name.trim()
  if (!trimmed) return '?'
  return Array.from(trimmed)[0].toUpperCase()
}

export type AvatarSize = 'sm' | 'md'

const SIZE: Record<AvatarSize, string> = {
  // 48px is §11's floor for anything tappable, and the switcher row on the
  // dashboard uses it. 76px is §08's size for the picker itself.
  sm: 'h-[52px] w-[52px] text-[22px]',
  md: 'h-[76px] w-[76px] text-[30px]',
}

export interface KidAvatarProps {
  kidId: number
  name: string
  size?: AvatarSize
  /** Drawn as the "add" affordance instead: dashed rule, no tint, no initial. */
  placeholder?: ReactNode
  className?: string
}

export default function KidAvatar({
  kidId,
  name,
  size = 'md',
  placeholder,
  className = '',
}: KidAvatarProps) {
  if (placeholder !== undefined) {
    return (
      <span
        aria-hidden="true"
        className={[
          'flex items-center justify-center rounded-pill border-[3px] border-dashed',
          'border-ink-300 bg-paper-page font-display text-ink-300',
          SIZE[size],
          className,
        ].join(' ')}
      >
        {placeholder}
      </span>
    )
  }

  return (
    <span
      aria-hidden="true"
      /* §08 gives the picker avatars the KID treatment — 3px ink border, hard
         ink-offset shadow — on an otherwise 40%-whimsy surface. Kept exactly:
         they are the one element on the parent screens that stands for a child
         rather than for data, and the style guide is explicit about it. */
      className={[
        'sf-lift-kid flex shrink-0 items-center justify-center rounded-pill',
        'border-[3px] border-ink-900 font-display leading-none text-ink-900',
        SIZE[size],
        className,
      ].join(' ')}
      style={{ backgroundColor: tintFor(kidId) }}
    >
      {initialFor(name)}
    </span>
  )
}
