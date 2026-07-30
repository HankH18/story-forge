/**
 * Comprehension — §11.2 step 3, style guide §07 "Questions — two types only".
 *
 *   tap_word  "Tap the word that starts like for." — three options, one of
 *             which provably starts with the target grapheme.
 *   mcq2      a two-choice story question; both options are validated
 *             decodable text.
 *
 * The grading rule is the point of this screen, and it is §07's: **wrong
 * answers dim and go dashed — no red, no buzzer, no score. The child retries
 * until correct; the attempt is what's logged.** There is no "next" past a
 * question and no way to fail one. A child who taps the wrong word twice ends
 * on the right word and leaves having read it.
 *
 * **The two question types are drawn as two different KINDS of thing** (see
 * DEVIATIONS 11.31–11.33). They used to differ only in how many columns the
 * options grid had, which hid two facts at once: that a listening game and a
 * question about a story are not the same task, and that
 * `mastery.MASTERY_QTYPES == ("tap_word",)` — only the sound question produces
 * phonics evidence toward promotion. The child is told the first fact, with an
 * icon, a tint and a layout; the second is a parent's business and is stated in
 * words on the dashboard, never here. §01 and §07 keep kid mode free of anything
 * that reads like grading, and "this one counts" is grading.
 *
 * **`tap_word` leads with the CUE, not with the sentence.** Below stage 4 the
 * prompt provably degrades to `"Tap it. Hint: for."` — every similarity word is
 * stage 4 or later, so the generator cannot write the instruction and the words
 * are not negotiable (llm/questions.py, DEVIATIONS 6.27). Speech, icons and
 * layout are not bound by the decoder, so the screen leads with the cue word on
 * a gold card, fans three chevrons down to the three options, and speaks a full
 * adult sentence. The generated prompt is still rendered — it is validated,
 * decodable, tappable text and at stage 6+ it is a real instruction — but as a
 * caption under the pattern rather than as the thing the screen is about.
 *
 * Where the verdict comes from, in order:
 *   1. `POST /api/readings/{id}/answers` — authoritative, and the same call
 *      that writes `answers` + `gpc_stats` (§9).
 *   2. `lib/grade.ts` — a restatement of the properties the question generator
 *      already proved, used only while that endpoint does not exist.
 *   3. If neither can decide, the answer is ACCEPTED. Telling a child they are
 *      wrong on no evidence is the one outcome worth avoiding at any cost.
 */

import { useEffect, useState } from 'react'
import { postAnswer, type Question } from '../lib/api'
import { localGrade } from '../lib/grade'
import { cueWord, spokenInstruction, taskKind } from '../lib/questions'
import { speak } from '../lib/speech'
import Button from '../ui/Button'
import ChoiceCard, { type ChoiceState } from '../ui/ChoiceCard'
import ProgressDots from '../ui/ProgressDots'
import SpeakerIcon from '../ui/SpeakerIcon'
import StoryText, { type ReadingSize } from '../ui/StoryText'
import { FanDownIcon, SoundTaskIcon, StoryTaskIcon } from '../ui/TaskIcon'

/**
 * The §11.3 task identities. Literal class strings in a `Record`, per STATE
 * invariant 7: Tailwind only emits classes it finds as literals while scanning
 * `content`, and `bg-${kind}-300` would be purged from the production bundle —
 * leaving both tasks on the default paper and silently undoing the whole
 * distinction this screen now depends on.
 */
const BAND: Record<'sound' | 'story', string> = {
  sound: 'border-gold-700 bg-gold-300',
  story: 'border-sky-700 bg-sky-100',
}

/** §05-ish: long enough to see the check land, short enough not to feel stuck. */
const CORRECT_PAUSE_MS = 900

export interface ComprehensionProps {
  questions: Question[]
  /** Everything the child actually read, for the local mcq2 check. */
  storyText: string
  readingId: number | null
  size: ReadingSize
  onToggleSize: () => void
  /** Fires once, when every question has been answered correctly. */
  onDone: () => void
}

export default function Comprehension({
  questions,
  storyText,
  readingId,
  size,
  onToggleSize,
  onDone,
}: ComprehensionProps) {
  const [index, setIndex] = useState(0)
  const [wrong, setWrong] = useState<Set<string>>(() => new Set())
  const [right, setRight] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const question = questions[index]

  /**
   * §07: the prompt is read aloud when the question appears.
   *
   * Every choice card has carried a speak button since this screen existed; the
   * prompt — the one string that says what to DO — had none, so it was the only
   * text on the screen a child could not hear. That gap is widest exactly where
   * the words are weakest: below stage 4 the prompt is "Tap it. Hint: win.",
   * because every similarity word a child could decode (`as`, `like`, `same`,
   * `alike`) is stage 4 or later, so the written sentence provably cannot say
   * more (llm/questions.py). Speech is not bound by the decoder. Logged in
   * DEVIATIONS 6.27, including what this does NOT fix.
   *
   * **One live utterance per question, and never zero.** The cleanup is the
   * whole of the StrictMode handling, and it must CANCEL rather than suppress.
   * React 18 double-invokes effects in dev (mount → cleanup → mount), so the
   * engine sees speak → cancel → speak and exactly one utterance survives.
   * `StoryWordChip` takes the other approach — a ref guard that is set and
   * never cleared — and the documented cost there is that dev teardown cancels
   * the first utterance while the guard refuses the second, so the chip is
   * silent in `bun run dev`. That is tolerable for a word the child can also
   * tap; it is not tolerable for the one string that explains the screen.
   *
   * The cleanup stops OUR utterance specifically — `speak()` returns a canceller
   * that no-ops once anything else has taken the microphone — so advancing to
   * the next question cannot cut off an option the child just asked to hear.
   * Nothing is awaited: a child can tap the instant the screen paints, and if
   * `speechSynthesis` is missing `speak()` degrades to silence rather than an
   * error (§01: kid mode has no error surface).
   *
   * Measured in a headed browser against a live story, not assumed: one
   * `speak()` without StrictMode, zero on re-render, one surviving utterance
   * with it, a real `start` event 2ms in and ~1.8s of `speechSynthesis.speaking`.
   */
  const questionId = question?.id ?? null
  // The SPOKEN instruction, not the written one: at stage 2 the written prompt
  // is the decoder's floor read aloud ("Tap it. Hint: for."), and speech has no
  // allowlist. lib/questions.ts owns the sentence so it is unit-tested.
  const said = question ? spokenInstruction(question) : ''

  useEffect(() => {
    if (questionId === null || !said) return
    return speak(said)
  }, [questionId, said])

  if (!question) return null

  /**
   * §07 draws a sound chip beside a tap_word prompt ("…that starts with /sh/").
   * `QuestionDTO` redacts `target_grapheme` — the correct option is the one
   * that starts with it, so shipping it would be shipping the answer key the
   * DTO exists to withhold. The chip therefore renders only if the API ever
   * offers the grapheme.
   *
   * `cue_word` DOES survive the denylist and is what carries the sound now: it
   * is a word from the story the child has already read, it gives no answer away
   * on its own, and "find the one that starts like this" is a task a child can
   * be shown rather than told.
   */
  const grapheme = String(question.payload?.target_grapheme ?? '')
  const kind = taskKind(question)
  const isTapWord = kind === 'sound'
  const cue = isTapWord ? cueWord(question) : null

  async function answer(option: string) {
    if (busy || right) return
    setBusy(true)
    let verdict: boolean | null = null
    if (readingId !== null) verdict = await postAnswer(readingId, question.id, option)
    if (verdict === null) verdict = localGrade(question, option, storyText)
    // Undecidable: accept. Never assert "wrong" without evidence.
    if (verdict === null) verdict = true

    if (!verdict) {
      setWrong((prev) => new Set(prev).add(option))
      setBusy(false)
      return
    }

    setRight(option)
    window.setTimeout(() => {
      setBusy(false)
      setRight(null)
      setWrong(new Set())
      if (index + 1 < questions.length) setIndex(index + 1)
      else onDone()
    }, CORRECT_PAUSE_MS)
  }

  const stateOf = (option: string): ChoiceState =>
    right === option ? 'correct' : wrong.has(option) ? 'wrong' : 'idle'

  return (
    <div className="flex min-h-screen flex-col px-6 py-5">
      {/* The task identity band. It answers "what is happening now", which is
          the question a child arriving from the story actually has, and it is
          the same tint and glyph the StoryEnd preview used for this task. */}
      <header className="flex items-center justify-between gap-4">
        <span
          className={[
            'inline-flex min-h-tap items-center gap-3 rounded-chip border-2 px-4 text-ink-900',
            BAND[kind],
          ].join(' ')}
        >
          <span aria-hidden="true">
            {isTapWord ? <SoundTaskIcon /> : <StoryTaskIcon />}
          </span>
          {/* Chrome, in Nunito, like every other label in kid mode. The task is
              carried by the glyph and by speech; this names it for an adult
              looking over a shoulder and for a screen reader. */}
          <span className="font-ui text-[20px] font-bold">
            {isTapWord ? 'sounds' : 'the story'}
          </span>
        </span>

        {questions.length > 1 ? (
          <ProgressDots total={questions.length} current={index} />
        ) : null}

        <Button
          variant="kid-quiet"
          size="md"
          onClick={onToggleSize}
          aria-pressed={size === 'large'}
          aria-label={size === 'large' ? 'smaller words' : 'bigger words'}
          className="px-4"
        >
          <span aria-hidden="true" className="font-read text-[22px]">
            Aa
          </span>
        </Button>
      </header>

      <main
        key={question.id}
        className="sf-page-in mx-auto flex w-full max-w-[720px] flex-1 flex-col
                   justify-center gap-8 py-6"
      >
        {isTapWord ? (
          <div className="flex flex-col items-center gap-3">
            {/* THE CUE, and the whole point of the redesign. A word on a gold
                card with a speaker on it, then three chevrons fanning down to
                three options, is a task a child can read off the layout with no
                sentence at all — which matters because at stages 2–3 the
                sentence provably cannot be written (DEVIATIONS 11.33). */}
            {cue ? (
              <button
                type="button"
                onClick={() => speak(cue)}
                aria-label={`hear the word ${cue}`}
                className="sf-lift-kid inline-flex min-h-tap-kid items-center gap-4 rounded-card
                           border-[3px] border-ink-900 bg-gold-300 px-6 py-3
                           font-read text-read text-ink-900"
              >
                <SpeakerIcon className="h-8 w-8 shrink-0" />
                <span>{cue}</span>
              </button>
            ) : null}

            {/* §07's sound chip, when the API offers a grapheme. It never does
                today (the DTO redacts it) — kept because the day it does, the
                sound belongs beside the cue and not in a sentence. */}
            {grapheme ? (
              <span
                aria-label={`the sound ${grapheme}`}
                className="inline-flex min-h-tap min-w-tap items-center justify-center
                           rounded-chip border-2 border-ink-900 bg-gold-300 px-4
                           font-read text-[28px] text-ink-900"
              >
                {grapheme}
              </span>
            ) : null}

            {/* ink-500, not ink-300: §11 allows ink-300 for decoration, and this
                IS decoration, but decoration too faint to point anywhere is not
                doing the job it was added for. */}
            <span className="text-ink-500" aria-hidden="true">
              <FanDownIcon className="h-7 w-28" />
            </span>
          </div>
        ) : (
          // mcq2: the prompt IS the question and it is a real sentence, so it
          // leads.
          //
          // NO CHIPS, and the comment that used to sit here said the opposite
          // while passing two props that could not render one (DEVIATIONS 13.6).
          // §07's chip sentence ends "...then behaves like any tappable word",
          // and by the question screen the introduction has happened: 11.26
          // decided that comprehension prompts render no chips, and every word
          // here is still tappable and still speaks (§11), which is the safety
          // net for a reader who has forgotten it. The second reason is louder —
          // a chip auto-speaks on first appearance, this screen already speaks
          // the instruction, and §11 allows exactly one utterance at a time. The
          // one that would lose is the sentence saying what to do.
          <StoryText text={question.prompt} size={size} />
        )}

        <ul
          className={
            isTapWord ? 'grid grid-cols-1 gap-4 sm:grid-cols-3' : 'grid grid-cols-1 gap-4'
          }
        >
          {question.options.map((option) => (
            <li key={option}>
              <ChoiceCard
                label={option}
                size={size}
                state={stateOf(option)}
                textClassName={isTapWord ? 'text-read' : undefined}
                onChoose={() => void answer(option)}
                onSpeak={() => speak(option)}
              />
            </li>
          ))}
        </ul>

        {/* The generated prompt for `tap_word`, moved BELOW the options. It is
            validated decodable text and every word of it is tappable, so it is
            not thrown away — but at stage 2 it reads "Tap it. Hint: for." and
            leading with that is what made the questions look badly written.
            
            Demoted by POSITION, not by type. Shrinking it was the obvious move
            and it is not available: §03 puts an absolute 28px floor under story
            text and nothing in `StoryText` may go below it, and tinting it to
            ink-500 would land at ~6.5:1 against paper where §11 requires ≥7:1
            for story text. Reading order is the one axis with no rule on it.

            No chips here either, for the reasons above the mcq2 prompt. */}
        {isTapWord ? <StoryText text={question.prompt} size={size} /> : null}
      </main>
    </div>
  )
}
