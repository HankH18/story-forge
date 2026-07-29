/**
 * Kid mode — `/read/:kidId` (§11 routes), the §11.2 session end to end:
 * theme picker → reading view → branch → comprehension → celebration.
 *
 * One component owns the phase, because the phases share three things a child
 * would notice if they reset: the reading size (the Aa toggle must survive a
 * page turn and the question screen), which story words have already announced
 * themselves, and the reading id that every telemetry call hangs off.
 *
 * **There is no error state.** §01: "Kid mode has no error state — the warm
 * pool guarantees a story." The server's fallback ladder (stories.py) already
 * turns an empty (stage, theme) pool into a different story rather than a 404,
 * so the only thing left that can fail is the network. That retries three times
 * behind the "the printer is working" screen and then offers a big Try again —
 * still a friendly sentence and a button, never a stack trace, never a red box.
 *
 * **The stage.** §9's request body is `{kid_id, theme_id}` and the server maps
 * kid → stage. `GET /api/kids` is live but unauthenticated until Clerk lands
 * (§16 phase 6), so the reader is resolved from that list; with no kids at all
 * the session runs anonymously at `?stage=` or DEFAULT_STAGE and simply records
 * nothing. Nothing here invents a reader: §13 means kid mode holds no name, no
 * birthday, no identity of any kind that a parent did not already enter.
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

/**
 * The stage a session runs at when nothing else says otherwise.
 *
 * Stage 4 rather than 1 for one honest reason: it is the lowest stage in the
 * seeded warm pool whose stories carry comprehension questions and tagged
 * artwork, so the default session exercises the whole §11.2 flow instead of
 * ending at the celebration with nothing to show. It only applies when the
 * parent has no kids at all — a real kid row's `stage_id` always wins.
 */
const DEFAULT_STAGE = 4

const REQUEST_ATTEMPTS = 3
const SIZE_KEY = 'sf.readingSize'

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
  const kidId = Number(params.kidId ?? '1') || 1

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

  /** Story words that have already auto-announced (§07: once per story). */
  const announced = useRef<Set<string>>(new Set())

  const stageFromQuery = Number(new URLSearchParams(window.location.search).get('stage'))
  const stage = kid?.stage_id ?? (stageFromQuery > 0 ? stageFromQuery : DEFAULT_STAGE)

  useEffect(() => {
    primeVoices()
    void getThemes().then(setThemes)
    void resolveKid(kidId).then(setKid)
    return () => cancelSpeech()
  }, [kidId])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIZE_KEY, size)
    } catch {
      /* private mode: the toggle still works for this session */
    }
  }, [size])

  const beginStory = useCallback(
    (fresh: Story) => {
      announced.current = new Set()
      setStory(fresh)
      setPlan(buildPlan(fresh.segments))
      setStep(0)
      setCompleted(null)
      setProgress(null)
      setReadingId(null)
      setPhase('reading')
      // No kid row → no telemetry. The session still reads; §13 would rather
      // lose a stats row than attach a reading to a reader nobody created.
      if (fresh.id > 0 && kid) void startReading(kid.id, fresh.id).then(setReadingId)
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
      cancelSpeech()
      setStalled(false)
      setPhase('loading')

      if (isFixtureRequested(window.location.search)) {
        beginStory(branchFixture())
        return
      }

      for (let attempt = 0; attempt < REQUEST_ATTEMPTS; attempt += 1) {
        try {
          beginStory(await requestStory(stage, theme.slug))
          return
        } catch (err) {
          if (import.meta.env.DEV) console.warn('[kid] story request failed:', err)
          if (attempt < REQUEST_ATTEMPTS - 1) await delay(600 * 2 ** attempt)
        }
      }
      // Still the printer screen — plus a way to ask again. Not an error state.
      setStalled(true)
    },
    [beginStory, stage],
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
