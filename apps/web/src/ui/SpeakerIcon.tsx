/**
 * The audio glyph. §07: "the audio glyph is an icon, not emoji, in the build."
 *
 * An emoji would render as a different picture on every platform and, on some,
 * in colour that fights the palette. Flat ink, no glow (§01).
 */
export default function SpeakerIcon({ className = 'h-6 w-6' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className={className}>
      <path
        d="M4 9.5h3.2L12 5.4v13.2L7.2 14.5H4z"
        fill="currentColor"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M15.4 9.2a4 4 0 0 1 0 5.6M17.9 6.7a7.5 7.5 0 0 1 0 10.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  )
}
