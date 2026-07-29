/**
 * StarBurst — style guide §07 / §12 contract: `count(1-3), staggered pop`.
 *
 * §05 fixes the whole animation: 420ms, staggered 90ms per star, overshoot to
 * 1.12 then settle. "Max 3 stars, no confetti" — the reward is a printed foil
 * star on a page, not a slot machine.
 *
 * Under `prefers-reduced-motion` the stars still appear; only the pop is
 * dropped (the global rule in styles/index.css collapses the duration, and
 * `animation-fill-mode: both` means the resting state is what remains). A
 * celebration that vanishes for a motion-sensitive child would be a worse
 * failure than one that does not bounce.
 *
 * Flat gold fill with an ink outline, per §01: printed, not glowing.
 */

const STAR_PATH =
  'M12 2.2l2.9 6.06 6.6.9-4.8 4.6 1.2 6.6L12 17.3 6.1 20.4l1.2-6.6-4.8-4.6 6.6-.9z'

export interface StarBurstProps {
  count?: number
  className?: string
}

export default function StarBurst({ count = 3, className = '' }: StarBurstProps) {
  const stars = Math.max(1, Math.min(3, count))
  return (
    <div
      role="img"
      aria-label={stars === 1 ? 'one star' : `${stars} stars`}
      className={`flex items-end justify-center gap-4 ${className}`}
    >
      {Array.from({ length: stars }, (_, i) => (
        <svg
          key={i}
          viewBox="0 0 24 24"
          aria-hidden="true"
          className="sf-star h-16 w-16"
          style={{ animationDelay: `${i * 90}ms` }}
        >
          <path
            d={STAR_PATH}
            fill="var(--sf-gold-500)"
            stroke="var(--sf-ink-900)"
            strokeWidth="1.4"
            strokeLinejoin="round"
          />
        </svg>
      ))}
    </div>
  )
}
