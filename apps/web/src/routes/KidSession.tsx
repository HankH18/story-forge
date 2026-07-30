/**
 * Kid mode — `/read/:kidId` (§11 routes), the §11.2 session end to end:
 * theme picker → reading view → branch → comprehension → celebration.
 *
 * One component owns the phase, because the phases share three things a child
 * would notice if they reset: the reading size (the Aa toggle must survive a
 * page turn and the question screen), which story words have already announced
 * themselves, and the reading id that every telemetry call hangs off.
 *
 * **There is no error state, and there is no dead end either.** §01: "Kid mode has
 * no error state — the warm pool guarantees a story." The server's fallback ladder
 * (stories.py) already turns an empty (stage, theme) pool into a different story
 * rather than a 404, so the only thing left that can fail is the network. That
 * retries three times behind the "the printer is working" screen — still a
 * friendly sentence, never a stack trace, never a red box.
 *
 * §01 has a second clause and this component used to fail it: a child must never
 * get **stuck**. "Try again" returned to the picker, which re-entered the same
 * loop, so against a failing API the printer screen was permanent — and it said
 * the printer was working while nothing was (audit C10). §9's limiter is 10 story
 * requests a minute, and an eleventh tap of "Read another" is ordinary behaviour
 * for a five-year-old who wants a *different* dragon, so this was reachable
 * without a single thing being broken.
 *
 * The path is now bounded, and every rung of it is a screen this app already has:
 *
 *   3 network attempts
 *     → re-serve the last story THIS reader was served, if there is one
 *       (`lib/lastStory.ts` — the server chose it, at this reader's stage, and
 *       §5.7c monotonicity means a lower stage stays decodable, DEVIATIONS 8.62)
 *     → otherwise the printer plus ONE "Try again"
 *     → and on a second stall, `/app`: the adult's own screen, which is where a
 *       problem a child cannot solve belongs (the same route a reader that does
 *       not resolve already takes, 7.24).
 *
 * Nothing on that path renders an error string to a child, and nothing on it
 * waits forever.
 *
 * **The stage is the reader's, and there is no session without one.** §9's
 * request body is `{kid_id, theme_id}` and the server maps kid → stage. This
 * component used to compute a stage locally — `kid?.stage_id ?? ?stage= ??
 * DEFAULT_STAGE`, with `DEFAULT_STAGE = 4` — and after the Phase 6 migration
 * orphaned every kid row, `kid` was null for every account in the database, so
 * every session in the product ran at stage 4. A stage-1 reader was served text
 * measuring 17.6% decodable at their own stage, silently: the only visible
 * difference was the missing "hello {name}" greeting.
 *
 * A constant that substitutes a stage is the anti-pattern. §01 forbids a
 * child-facing error screen, so "show an error" and "guess a stage" are both
 * wrong; the third option is the honest one. **If there is no reader, there is
 * no reading session** — the printer screen holds while the reader resolves,
 * and if none resolves we navigate to `/app`, the adult surface, which already
 * knows how to say "no readers yet" and how to add one. The child never sees a
 * failure and the adult sees the actual problem (DEVIATIONS 7.24).
 *
 * This also removes the render/effect race the audit suspected: `stage` was
 * computed during render while `resolveKid` was awaited in an effect, so a fast
 * tap beat the reader home. There is now no stage to compute and nothing to tap
 * until the reader exists.
 *
 * `?stage=` is gone. It was the address-bar half of the escalation defect and
 * it has no legitimate caller: the stage belongs to the child, and the child is
 * already in the URL.
 *
 * **And it is that child or no child.** `resolveKid` used to fall back to the
 * account's first reader for an id it did not recognise, so `/read/162` from an
 * account that does not own kid 162 rendered "hello Audit" — a different child —
 * and filed the reading, and its §8 mastery evidence, against Audit's row. The
 * URL said 162 and nothing on screen disagreed. That fallback is gone
 * (DEVIATIONS 7.33); an unrecognised id is an unresolvable reader and takes the
 * same route as no reader at all, which is the one below.
 *
 * Nothing here invents a reader: §13 means kid mode holds no name, no birthday,
 * no identity of any kind that a parent did not already enter.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  completeReading,
  getProgress,
  getThemes,
  postChoice,
  requestStory,
  resolveKid,
  startReading,
  type CompleteResult,
  type Kid,
  type Progress,
  type Segment,
  type Story,
  type Theme,
} from '../lib/api'
import { branchFixture, isFixtureRequested } from '../lib/fixtures'
import { loadLastStory, saveLastStory } from '../lib/lastStory'
import {
  buildPlan,
  expectedSegmentCount,
  readText,
  segmentOrdinal,
  withChoice,
  type PlanStep,
} from '../lib/plan'
import { cancelSpeech, primeVoices } from '../lib/speech'
import { distinctWords } from '../lib/text'
import Button from '../ui/Button'
import PrinterWorking from '../ui/PrinterWorking'
import type { ReadingSize } from '../ui/StoryText'
import BranchChoice from './BranchChoice'
import Celebration from './Celebration'
import Comprehension from './Comprehension'
import ReadingView from './ReadingView'
import ThemePicker from './ThemePicker'

const REQUEST_ATTEMPTS = 3
const SIZE_KEY = 'sf.readingSize'
/**
 * How many times a child may land on the printer screen before the tablet goes
 * back to the adult (§01: never stuck). One retry, then hand over — a second
 * failure means the API is not having a moment, it is unavailable, and no number
 * of taps by a five-year-old will change that.
 */
const MAX_STALLS = 2

type Phase = 'picker' | 'loading' | 'reading' | 'questions' | 'celebration'

function loadSize(): ReadingSize {
  try {
    return window.localStorage.getItem(SIZE_KEY) === 'large' ? 'large' : 'default'
  } catch {
    return 'default'
  }
}

const delay = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

export default function KidSession() {
  const params = useParams()
  const navigate = useNavigate()
  // `NaN` for `/read/nonsense`, and that is the honest answer: it used to fall
  // back to `1`, a guess at a kid id, which `resolveKid` then turned into a real
  // but WRONG reader. `resolveKid` rejects anything that is not a positive
  // integer, so this lands on the same route as an unowned id — the adult's
  // screen (DEVIATIONS 7.33).
  const kidId = Number(params.kidId)

  const [phase, setPhase] = useState<Phase>('picker')
  const [themes, setThemes] = useState<Theme[]>([])
  const [kid, setKid] = useState<Kid | null>(null)
  const [story, setStory] = useState<Story | null>(null)
  const [plan, setPlan] = useState<PlanStep[]>([])
  const [step, setStep] = useState(0)
  const [readingId, setReadingId] = useState<number | null>(null)
  const [size, setSize] = useState<ReadingSize>(loadSize)
  const [completed, setCompleted] = useState<CompleteResult | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [stalled, setStalled] = useState(false)
  /** `resolveKid` came back empty; we are on our way to `/app`. */
  const [kidMissing, setKidMissing] = useState(false)

  /** Story words that have already auto-announced (§07: once per story). */
  const announced = useRef<Set<string>>(new Set())
  /** Printer screens this session has parked on. See `MAX_STALLS`. */
  const stalls = useRef(0)

  useEffect(() => {
    primeVoices()
    let live = true
    void getThemes().then((t) => {
      if (live) setThemes(t)
    })
    void resolveKid(kidId).then((resolved) => {
      if (!live) return
      if (resolved) {
        setKid(resolved)
        return
      }
      // No reader — the account has none, or the call failed. There is nothing
      // honest to read at, so there is no session: hand the tablet back to the
      // adult rather than invent a stage (DEVIATIONS 7.24). `/app` is a real
      // screen with a real explanation, not an error page, and `RequireParent`
      // sends a signed-out visitor on to sign-in.
      setKidMissing(true)
      navigate('/app', { replace: true })
    })
    return () => {
      live = false
      cancelSpeech()
    }
  }, [kidId, navigate])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIZE_KEY, size)
    } catch {
      /* private mode: the toggle still works for this session */
    }
  }, [size])

  const beginStory = useCallback(
    (fresh: Story, remember = true) => {
      announced.current = new Set()
      stalls.current = 0
      setStalled(false)
      setStory(fresh)
      setPlan(buildPlan(fresh.segments))
      setStep(0)
      setCompleted(null)
      setProgress(null)
      setReadingId(null)
      setPhase('reading')
      // `kid` is non-null by construction here — nothing can reach this without
      // a resolved reader — but the guard stays, because the alternative is a
      // reading filed against `undefined`.
      if (fresh.id > 0 && kid) {
        void startReading(kid.id, fresh.id).then(setReadingId)
        // `remember` is false only when this story CAME from the cache: re-saving
        // it would roll its own seven-day expiry forward every time it is
        // re-served, and that expiry is what stops a retracted story being
        // re-served forever (DEVIATIONS 8.62).
        if (remember) saveLastStory(kid.id, fresh)
      }
      if (import.meta.env.DEV && fresh.fallback) {
        // Never shown to the child (§10 fixes the reading chrome at three
        // controls), but the operator should see which pool needs refilling.
        console.info('[kid] served a fallback story:', fresh.fallback.detail)
      }
    },
    [kid],
  )

  const pickTheme = useCallback(
    async (theme: Theme) => {
      // Unreachable while the reader is still resolving — the picker is not
      // rendered until then — and asserted rather than defaulted, because a
      // default is the whole defect this replaced.
      if (!kid) return
      cancelSpeech()
      setStalled(false)
      setPhase('loading')

      if (isFixtureRequested(window.location.search)) {
        beginStory(branchFixture())
        return
      }

      for (let attempt = 0; attempt < REQUEST_ATTEMPTS; attempt += 1) {
        try {
          beginStory(await requestStory(kid.id, theme.slug))
          return
        } catch (err) {
          if (import.meta.env.DEV) console.warn('[kid] story request failed:', err)
          if (attempt < REQUEST_ATTEMPTS - 1) await delay(600 * 2 ** attempt)
        }
      }

      // Every attempt failed. Before the printer screen: the last story the
      // SERVER served this same reader, if it is still fit to re-serve. The child
      // re-reads something decodable instead of watching a printer that is not
      // printing, and the theme may be the previous one — which is exactly what
      // §7's ladder does server-side when a pair is empty (DEVIATIONS 8.62).
      const previous = loadLastStory(kid.id, kid.stage_id)
      if (previous) {
        if (import.meta.env.DEV) {
          console.info('[kid] request failed; re-serving the last story', previous.id)
        }
        beginStory(previous, false)
        return
      }

      stalls.current += 1
      if (stalls.current >= MAX_STALLS) {
        // Nothing to read and the API is not answering. A child cannot fix this
        // and must not be left in front of it, so the tablet goes back to the
        // adult — `/app` is a real screen with a real explanation, not an error
        // page, and `RequireParent` sends a signed-out visitor to sign-in
        // (DEVIATIONS 7.24, 8.62).
        navigate('/app', { replace: true })
        return
      }
      // Still the printer screen — plus a way to ask again. Not an error state.
      setStalled(true)
    },
    [beginStory, kid, navigate],
  )

  const finish = useCallback(
    () => {
      cancelSpeech()
      setPhase('celebration')
      // Complete first, then read the totals back: the deltas this reading
      // produced are only in `gpc_stats` after the complete call lands, and the
      // celebration's "+1 · 3 of 4" is the two answers side by side.
      const settle = async () => {
        if (readingId !== null) setCompleted(await completeReading(readingId))
        if (kid) setProgress(await getProgress(kid.id))
      }
      void settle()
    },
    [kid, readingId],
  )

  const next = useCallback(() => {
    cancelSpeech()
    if (step + 1 < plan.length) {
      setStep(step + 1)
      return
    }
    if (story && story.questions.length > 0) {
      setPhase('questions')
      return
    }
    finish()
  }, [finish, plan.length, step, story])

  const choose = useCallback(
    (segment: Segment) => {
      cancelSpeech()
      setPlan((prev) => withChoice(prev, step, segment))
      setStep(step + 1)
      if (readingId !== null) {
        void postChoice(readingId, segment.id, segment.choice_label ?? '')
      }
    },
    [readingId, step],
  )

  const toPicker = useCallback(() => {
    cancelSpeech()
    setStory(null)
    setPlan([])
    setStep(0)
    setPhase('picker')
  }, [])

  const shouldAnnounce = useCallback((word: string) => !announced.current.has(word), [])
  const onAnnounced = useCallback((word: string) => {
    announced.current.add(word)
  }, [])

  const wordCount = useMemo(() => distinctWords(readText(plan)).length, [plan])

  // --- render ---------------------------------------------------------------

  // No reader yet. Two states, one screen, and it is the friendly one:
  //   - still resolving → the printer, for the few ms `GET /api/kids` takes;
  //   - resolved to nothing → the printer for the one frame before the effect's
  //     `navigate('/app')` commits.
  // Neither may render the picker. Tapping a theme without a reader is the
  // request that used to invent a stage (DEVIATIONS 7.24), so the tap must not
  // be offered rather than be guarded after the fact.
  if (!kid && (phase === 'picker' || kidMissing)) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-10 bg-paper-page px-6">
        <PrinterWorking />
      </div>
    )
  }

  if (phase === 'loading' || (phase === 'picker' && themes.length === 0)) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-10 bg-paper-page px-6">
        <PrinterWorking />
        {stalled ? (
          <Button variant="kid-primary" size="kid" onClick={() => setPhase('picker')}>
            Try again
          </Button>
        ) : null}
      </div>
    )
  }

  if (phase === 'picker') {
    return (
      <div className="min-h-screen bg-paper-page">
        <ThemePicker
          themes={themes}
          readerName={kid?.display_name}
          onPick={(theme) => void pickTheme(theme)}
        />
      </div>
    )
  }

  if (!story) {
    // Only reachable if a phase is entered without a story; go back to the one
    // screen that always works rather than render nothing.
    return (
      <div className="min-h-screen bg-paper-page">
        <ThemePicker themes={themes} onPick={(theme) => void pickTheme(theme)} />
      </div>
    )
  }

  if (phase === 'questions') {
    return (
      <div className="min-h-screen bg-paper-page">
        <Comprehension
          questions={story.questions}
          storyText={readText(plan)}
          readingId={readingId}
          size={size}
          onToggleSize={() => setSize(size === 'large' ? 'default' : 'large')}
          onDone={finish}
        />
      </div>
    )
  }

  if (phase === 'celebration') {
    return (
      <div className="min-h-screen bg-paper-page">
        <Celebration
          story={story}
          wordCount={wordCount}
          completed={completed}
          progress={progress}
          onReadAnother={toPicker}
          onAllDone={() => navigate('/')}
        />
      </div>
    )
  }

  const current = plan[step]
  if (!current) return null

  if (current.kind === 'choice') {
    return (
      <div className="min-h-screen bg-paper-page">
        <BranchChoice options={current.options} size={size} onChoose={choose} />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-paper-page">
      <ReadingView
        story={story}
        segment={current.segment}
        totalSegments={expectedSegmentCount(plan)}
        segmentOrdinal={segmentOrdinal(plan, step)}
        size={size}
        onToggleSize={() => setSize(size === 'large' ? 'default' : 'large')}
        onBack={toPicker}
        onNext={next}
        isLast={step === plan.length - 1}
        shouldAnnounce={shouldAnnounce}
        onAnnounced={onAnnounced}
      />
    </div>
  )
}
