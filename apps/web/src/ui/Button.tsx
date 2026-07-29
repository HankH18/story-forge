/**
 * Button — style guide §07 / §12 contract.
 *
 *   variant: kid-primary | kid-secondary | kid-quiet | parent | ghost
 *   size:    kid (64px) | md (48px)   — never smaller
 *
 * The sizes are floors, not fixed heights: `min-h` plus real padding, so a long
 * label wraps into a taller button instead of overflowing a fixed box. §11's
 * 48px is an absolute minimum for *every* control in the app and 64px is the
 * minimum for a kid primary action, so there is deliberately no `sm`.
 *
 * The press behaviour (shadow 6px → 2px, translateY(4px), 90ms) and the focus
 * composition live in `.sf-lift-kid` / `.sf-lift-parent` in styles/index.css —
 * see the note there for why the ink-offset shadow cannot be a plain Tailwind
 * utility once a gold focus ring has to coexist with it.
 */

import type { ButtonHTMLAttributes, ReactNode } from 'react'

export type ButtonVariant = 'kid-primary' | 'kid-secondary' | 'kid-quiet' | 'parent' | 'ghost'
export type ButtonSize = 'kid' | 'md'

const VARIANT: Record<ButtonVariant, string> = {
  // Berry is the primary action colour (§02). It is also the violation colour,
  // which is only safe because violations never reach kid mode.
  'kid-primary': 'sf-lift-kid border-[3px] border-ink-900 bg-berry-500 text-paper-page',
  'kid-secondary': 'sf-lift-kid border-[3px] border-ink-900 bg-gold-500 text-ink-900',
  'kid-quiet': 'sf-lift-kid border-[3px] border-ink-900 bg-paper-card text-ink-900',
  parent: 'sf-lift-parent border-2 border-paper-rule bg-paper-card text-ink-900',
  ghost: 'border-2 border-transparent bg-transparent text-ink-700',
}

const SIZE: Record<ButtonSize, string> = {
  kid: 'min-h-tap-kid min-w-tap-kid px-8 py-3 text-[20px]',
  md: 'min-h-tap min-w-tap px-6 py-2 text-[17px]',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  children?: ReactNode
}

export default function Button({
  variant = 'kid-primary',
  size = 'kid',
  className = '',
  type = 'button',
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={[
        'inline-flex select-none items-center justify-center gap-3 rounded-btn',
        'font-ui font-bold leading-tight',
        'transition-transform duration-[90ms] ease-out',
        'disabled:cursor-default disabled:border-paper-shade disabled:bg-paper-warm',
        'disabled:text-ink-300 disabled:shadow-none',
        VARIANT[variant],
        SIZE[size],
        className,
      ].join(' ')}
      {...rest}
    >
      {children}
    </button>
  )
}
