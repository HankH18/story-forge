/**
 * Browser `speechSynthesis` wrapper — style guide §11 "TTS is user-triggered".
 *
 * Three rules the rest of the app must never have to remember:
 *
 * 1. **Rate 0.85.** A decoding reader needs the word slower than conversational
 *    speech. Fixed here so no caller can drift.
 * 2. **One utterance at a time.** Every `speak` cancels whatever is talking
 *    first. A child who taps four words in a row hears the fourth, not a queue
 *    of four — a queue reads as a broken app and buries the word they asked for.
 * 3. **Speech is on-device.** No audio is recorded, uploaded, or synthesised
 *    server-side (§13: the architecture collects zero audio).
 *
 * The wrapper never throws and never leaves a caller hanging. `speechSynthesis`
 * is absent in some embedded webviews and present-but-voiceless in headless
 * Chromium, and in both cases the `end` event never arrives — so every call arms
 * a watchdog. The UI state machine (`rest → speaking → heard`) therefore always
 * completes, whether or not a sound was actually produced. The highlight is
 * feedback about the tap as much as about the audio.
 */

/** §11: a decoding reader's rate, not a conversational one. */
export const SPEECH_RATE = 0.85

/** §05: the gold highlight is held this long *after* speech starts. */
export const HOLD_AFTER_START_MS = 250

/** Rough speaking time, used only when the engine gives us no `end` event. */
function estimateMs(text: string): number {
  return Math.min(5000, Math.max(500, text.length * 90 + 300))
}

function synth(): SpeechSynthesis | null {
  if (typeof window === 'undefined') return null
  return 'speechSynthesis' in window ? window.speechSynthesis : null
}

export function speechAvailable(): boolean {
  return synth() !== null
}

let cachedVoice: SpeechSynthesisVoice | null = null

/**
 * Prefer a local (offline) English voice: it is lower latency, and a remote
 * voice would send the child's word list to a vendor, which §13 rules out.
 */
function pickVoice(s: SpeechSynthesis): SpeechSynthesisVoice | null {
  if (cachedVoice) return cachedVoice
  const voices = s.getVoices()
  if (!voices.length) return null
  const english = voices.filter((v) => v.lang?.toLowerCase().startsWith('en'))
  const pool = english.length ? english : voices
  cachedVoice =
    pool.find((v) => v.localService && v.lang?.toLowerCase().startsWith('en-us')) ??
    pool.find((v) => v.localService) ??
    pool[0]
  return cachedVoice
}

/** Rising token: any in-flight utterance whose token is stale is ignored. */
let token = 0
const timers: number[] = []

/**
 * The `onEnd` of whatever is currently speaking.
 *
 * Interrupting has to *settle* the interrupted caller, not orphan it. A word
 * left in `speaking` because a second tap cancelled the first would keep a gold
 * highlight on screen for the rest of the session, which reads as a stuck app.
 * So cancelling runs the previous end callback before invalidating the token.
 */
let finishActive: (() => void) | null = null

function clearTimers(): void {
  while (timers.length) window.clearTimeout(timers.pop())
}

/** Stop whatever is speaking. Safe to call when nothing is. */
export function cancelSpeech(): void {
  const finish = finishActive
  finishActive = null
  finish?.() // settle the interrupted caller while its token is still valid
  token += 1
  clearTimers()
  const s = synth()
  if (s) {
    try {
      s.cancel()
    } catch {
      /* some engines throw when cancelling an empty queue; nothing to do */
    }
  }
}

export interface SpeakOptions {
  /** Fired when audio begins — or immediately, when there is no engine. */
  onStart?: () => void
  /** Fired when audio ends, errors, or the watchdog fires. Exactly once. */
  onEnd?: () => void
}

/**
 * Speak `text` once, cancelling anything already speaking.
 *
 * Returns a cancel function; calling it suppresses the pending callbacks so a
 * component that unmounts mid-utterance cannot set state afterwards.
 */
export function speak(text: string, opts: SpeakOptions = {}): () => void {
  cancelSpeech()
  const mine = token
  const fresh = () => mine === token

  let started = false
  let ended = false
  const start = () => {
    if (!fresh() || started) return
    started = true
    opts.onStart?.()
  }
  const end = () => {
    if (!fresh() || ended) return
    ended = true
    if (finishActive === end) finishActive = null
    start() // an engine that errors before `start` still owes the caller both
    opts.onEnd?.()
  }
  finishActive = end

  const s = synth()
  const spoken = text.trim()
  if (!s || !spoken) {
    // No engine (or nothing to say): run the state machine on a timer so the
    // tap still gets its §05 highlight. Silence is degraded, not broken.
    start()
    timers.push(window.setTimeout(end, estimateMs(spoken) + HOLD_AFTER_START_MS))
    return () => {
      if (fresh()) cancelSpeech()
    }
  }

  const utterance = new SpeechSynthesisUtterance(spoken)
  utterance.rate = SPEECH_RATE
  utterance.pitch = 1
  utterance.lang = 'en-US'
  const voice = pickVoice(s)
  if (voice) utterance.voice = voice
  utterance.onstart = start
  utterance.onend = end
  utterance.onerror = end

  // Watchdog. Headless Chromium accepts `speak()` and then stays silent
  // forever, so without this a word would be stuck in `speaking` for the rest
  // of the session and the reading view would look frozen.
  const budget = estimateMs(spoken)
  timers.push(window.setTimeout(start, 400))
  timers.push(window.setTimeout(end, budget * 2 + 1200))

  try {
    s.speak(utterance)
  } catch {
    end()
  }

  return () => {
    if (fresh()) cancelSpeech()
  }
}

/**
 * Warm the voice list. Voices load asynchronously in Chrome, so the first
 * utterance of a session otherwise picks whatever default exists.
 */
export function primeVoices(): void {
  const s = synth()
  if (!s) return
  pickVoice(s)
  if ('onvoiceschanged' in s) {
    s.onvoiceschanged = () => {
      cachedVoice = null
      pickVoice(s)
    }
  }
}
