/**
 * The two question types, as pictures.
 *
 * §11.3 has exactly two question types and the live screen drew them almost
 * identically — same header, same prompt line, same cards, differing only in
 * how many columns the options grid had. So a child got no signal that the
 * second question was a different *kind* of thing from the first, and neither
 * did anyone else: `mastery.MASTERY_QTYPES` is `("tap_word",)`, so only the
 * sound question produces phonics evidence, and nothing on screen distinguished
 * them.
 *
 * An icon, not an emoji (§07: "the audio glyph is an icon, not emoji, in the
 * build") — an emoji renders as a different picture on every platform and, on
 * some, in colour that fights the palette. Flat ink on the task's own tint, no
 * glow (§01, §04).
 *
 * `currentColor` throughout so the caller sets the tint once, on the band, and
 * the glyph follows. Colour is never the only difference: the two glyphs have
 * different silhouettes, which is the §11 "never colour alone" rule applied to
 * the one place on this screen where a difference is being asserted.
 */

export interface TaskIconProps {
  className?: string
}

/** An ear — the listening game (`tap_word`). */
export function SoundTaskIcon({ className = 'h-8 w-8' }: TaskIconProps) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className={className}>
      <path
        d="M8 9.2a4 4 0 0 1 8 0c0 2.2-1.6 3-2.6 4.1-.8.9-.9 1.7-.9 2.6 0 1.5-1 2.6-2.4 2.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M11 10.4a1.4 1.4 0 0 1 2.4 1"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.1"
        strokeLinecap="round"
      />
      {/* two sound arcs, so the glyph reads as "listen" and not just "ear" */}
      <path
        d="M18.6 6.6a7.6 7.6 0 0 1 0 10.8M21 4.2a11 11 0 0 1 0 15.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        opacity="0.7"
      />
    </svg>
  )
}

/** An open book — the question about the story (`mcq2`). */
export function StoryTaskIcon({ className = 'h-8 w-8' }: TaskIconProps) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className={className}>
      <path
        d="M12 6.4C10.2 5 7.6 4.6 4 5.1v13c3.6-.5 6.2-.1 8 1.3 1.8-1.4 4.4-1.8 8-1.3v-13c-3.6-.5-6.2-.1-8 1.3z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M12 6.4v13.3"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.1"
        strokeLinecap="round"
      />
    </svg>
  )
}

/**
 * Three chevrons pointing DOWN, from the cue card to the three options.
 *
 * The first version was `M12 4l-6 8 6 8 …` — chevrons pointing left and right,
 * which rendered as `‹ | ›` and read as "previous / next", i.e. as navigation.
 * Verified in a browser at a tablet viewport, which is the only place the
 * difference between "look down here" and "go back" is visible.
 */
export function FanDownIcon({ className = 'h-6 w-24' }: TaskIconProps) {
  return (
    <svg viewBox="0 0 96 26" aria-hidden="true" focusable="false" className={className}>
      <path
        d="M8 6l8 10 8-10M40 6l8 10 8-10M72 6l8 10 8-10"
        fill="none"
        stroke="currentColor"
        strokeWidth="3.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
