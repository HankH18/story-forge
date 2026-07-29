/**
 * Branch choice — §10 ("2 choices, each choice is one segment, branches remerge
 * to the spine") rendered per style guide §07: "Exactly two, stacked on tablet
 * portrait, side-by-side landscape."
 *
 * Choice labels are validated story text — they went through the same decoder
 * gate as the story (§10), so they are set in Andika at the reading size and
 * the child can hear any of them read aloud.
 *
 * Both arms are equally weighted. There is no "recommended" path, no preview of
 * what is behind each door, and no undo — a branch that can be taken back is a
 * menu, not a decision, and §01 wants one idea and at most two choices per kid
 * screen.
 */

import type { Segment } from '../lib/api'
import ChoiceCard from '../ui/ChoiceCard'
import type { ReadingSize } from '../ui/StoryText'
import { speak } from '../lib/speech'

export interface BranchChoiceProps {
  prompt?: string
  options: Segment[]
  size: ReadingSize
  onChoose: (segment: Segment) => void
}

export default function BranchChoice({
  prompt = 'What will they do?',
  options,
  size,
  onChoose,
}: BranchChoiceProps) {
  return (
    <main className="sf-page-in mx-auto flex min-h-screen w-full max-w-[900px] flex-col
                    justify-center gap-10 px-6 py-10">
      <h1 className="font-display text-[40px] leading-[1.15] text-ink-900">{prompt}</h1>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {options.map((option) => {
          const label = option.choice_label ?? ''
          return (
            <ChoiceCard
              key={option.id}
              label={label}
              size={size}
              onChoose={() => onChoose(option)}
              onSpeak={() => speak(label)}
            />
          )
        })}
      </div>
    </main>
  )
}
