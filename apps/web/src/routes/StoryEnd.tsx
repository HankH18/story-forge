/**
 * StoryEnd — the beat between the last page and the first question.
 *
 * The user's report on the live app: the questions "begin out of nowhere". They
 * did. `next()` on the last segment set `phase = 'questions'` and the reading
 * view was replaced, in one frame, by a different screen asking a child to do
 * something. Nothing said the story was over, nothing said what was coming, and
 * for a five-year-old who cannot read the new screen's heading that is the
 * whole of the information available.
 *
 * So there is now a screen whose only job is the transition, and it does three
 * things a §07-shaped screen can do:
 *
 * 1. **Closes the story.** A display headline and a hand-lettered line, in the
 *    same voice as the celebration screen. The reading is finished and finishing
 *    it was the achievement — §08's "no derived reading level, no streak, no
 *    badge" applies here too, so this is praise for reading, not a score.
 * 2. **Shows what is coming, as pictures.** One card per question, with the
 *    §11.3 task's own icon. A child cannot read "two questions" but can see two
 *    cards, and can see that the two are not the same shape as each other.
 * 3. **Speaks it, in an adult voice.** The one channel with no decoder
 *    constraint (see lib/questions.ts). The written words on this screen are
 *    chrome, like "Read a story" and "try again" already are; the *instruction*
 *    is the utterance.
 *
 * The child advances it themselves. An auto-advance would put the beat back on a
 * timer the child does not control, which is the same problem in slower motion —
 * and §11 wants every step of kid mode to be a target you can hit.
 */

import { useEffect } from 'react'
import type { Question } from '../lib/api'
import { taskKind } from '../lib/questions'
import { speak } from '../lib/speech'
import Button from '../ui/Button'
import { SoundTaskIcon, StoryTaskIcon } from '../ui/TaskIcon'

export interface StoryEndProps {
  /** The questions about to be asked, for the preview cards. */
  questions: Question[]
  /** The child's name, when the session knows it — this is their moment. */
  readerName?: string
  onStart: () => void
}

/**
 * §07's kid-mode copy voice. Short, warm, second person, no grading words:
 * "nice reading" not "well done", never "correct", never "score".
 */
function spokenIntro(count: number, readerName?: string): string {
  const who = readerName ? `${readerName}, you` : 'You'
  if (count <= 1) return `${who} read the whole story. Now there is one thing to try.`
  return (
    `${who} read the whole story. Now there are two things to try: ` +
    'a listening game, and a question about the story.'
  )
}

const TASK_LABEL = { sound: 'sounds', story: 'the story' } as const

export default function StoryEnd({ questions, readerName, onStart }: StoryEndProps) {
  const intro = spokenIntro(questions.length, readerName)

  /**
   * Speaks once. `speak()` returns a canceller that no-ops once anything else
   * has taken the microphone, and returning it from the effect is what makes
   * React 18's dev double-invoke land on speak → cancel → speak, i.e. exactly
   * one surviving utterance — the same pattern `Comprehension` uses and for the
   * same reason (a suppressing ref guard leaves the dev build silent).
   */
  useEffect(() => speak(intro), [intro])

  return (
    <main
      className="sf-page-in mx-auto flex min-h-screen w-full max-w-[720px] flex-col
                 justify-center gap-10 px-6 py-10"
    >
      <header className="flex flex-col gap-3">
        <h1 className="font-display text-[44px] leading-[1.12] text-ink-900">
          You read the story!
        </h1>
        <p className="font-hand text-[26px] text-ink-500">
          {questions.length > 1 ? 'two things to try' : 'one thing to try'}
        </p>
      </header>

      {/* Not buttons. These are a map of what is coming, and making them tappable
          would offer a choice of question order that the session does not have. */}
      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {questions.map((question, i) => {
          const kind = taskKind(question)
          return (
            <li
              key={question.id}
              className={[
                'flex items-center gap-4 rounded-card border-[3px] border-ink-900 p-5',
                // The two tints are the §11.3 task identities, and they are the
                // same two the question screen uses — so the preview and the
                // thing it previews cannot drift apart.
                kind === 'sound' ? 'bg-gold-300' : 'bg-sky-100',
              ].join(' ')}
            >
              <span className="text-ink-900" aria-hidden="true">
                {kind === 'sound' ? (
                  <SoundTaskIcon className="h-10 w-10" />
                ) : (
                  <StoryTaskIcon className="h-10 w-10" />
                )}
              </span>
              <span className="font-ui text-[22px] font-bold text-ink-900">
                {TASK_LABEL[kind]}
              </span>
              {/* Ordinal for assistive tech only — never printed, because "1 of
                  2" is a countdown and §07 says kids read those as work
                  remaining. */}
              <span className="sr-only">{`${i + 1} of ${questions.length}`}</span>
            </li>
          )
        })}
      </ul>

      <div className="flex justify-end">
        <Button variant="kid-primary" size="kid" onClick={onStart}>
          Let&rsquo;s go <span aria-hidden="true">→</span>
        </Button>
      </div>
    </main>
  )
}
