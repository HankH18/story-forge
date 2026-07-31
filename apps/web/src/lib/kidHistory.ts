/**
 * Kid mode as a MODE rather than a route — what the platform back gesture means
 * while a child is holding the tablet (build doc §13, DEVIATIONS 27.1-27.3).
 *
 * §13: *"Kids: rows under parent, display-name only. No email, DOB, mic, camera,
 * free-text input, or third-party trackers in kid mode."* That sentence is a
 * promise about a surface, and until this module existed the app kept it for
 * exactly one route: `/read/:kidId` is a single React component with the phase in
 * state, so a session pushed NO history of its own and the platform back gesture
 * — an edge swipe on the tablet §11 designs for — left kid mode entirely. Driven
 * live at 1024x768 against an enforced API: page 3 of 7 of a dragons story, one
 * `back`, and the child is on `/app` reading `SIGNED IN AS
 * HOLCOMBHANK@GMAIL.COM`, with every other reader's name and stage, and `Add a
 * reader` one tap from a free-text `<input id="kid-name">`.
 *
 * The containment is the ← button, handed to the platform gesture:
 *
 *   - inside a story, back means what `←` means — return to the theme picker;
 *   - at the theme picker, back means nothing at all.
 *
 * **The trap that answer creates is real and is why this file names its escape
 * routes.** A back button that never leaves is a parent stuck in their child's
 * app. Four escapes are kept and each is proven in `scripts/kid-check.mjs`:
 * `ui/GrownUpExit` on the picker (a press-and-hold, which is a gesture a child
 * does not stumble into and an adult performs on purpose), the address bar (a
 * fresh navigation is not a `popstate` and this guard cannot see it), a new tab,
 * and the session's own ends — `Celebration`'s "All done", and the two places
 * `KidSession` already hands the tablet back to `/app` (no reader, and a stalled
 * API). An adult always leaves; a child never stumbles out. The two are not in
 * conflict because they are not the same person.
 *
 * Everything here is a pure function so the DECISION is testable in
 * `bun test src`; what a browser actually does with it is `make kid-check`,
 * because a pure-helper suite structurally cannot see a history stack.
 */

/**
 * The `KidSession` phases, restated as a type this module can decide over.
 * Kept structurally identical to `routes/KidSession.tsx`'s `Phase` — a phase
 * added there and not here would be a back gesture with no defined meaning, and
 * `backOutcome`'s switch is exhaustive so `tsc` says so.
 */
export type KidPhase = 'picker' | 'loading' | 'reading' | 'story-end' | 'questions' | 'celebration'

/** What the platform back gesture does. There is no third answer: leaving is not one. */
export type BackOutcome = 'to-picker' | 'stay'

/**
 * Back, decided by phase.
 *
 * `loading` STAYS, and that is the one entry worth arguing about. The printer
 * screen is a story request in flight, and `pickTheme` calls `beginStory` when it
 * lands — so sending a back gesture to the picker mid-request would show the
 * picker for a moment and then yank the child into a story they had just left.
 * The wait is already bounded (three attempts, then the last story, then one
 * `Try again`, then `/app`), so the honest answer is that there is nothing to go
 * back to yet.
 *
 * `celebration` goes to the picker because that screen's own button already does
 * ("read another?", §11.4), and a child who backs out of the stars has not
 * un-read the story: the reading was completed before the celebration painted.
 */
export function backOutcome(phase: KidPhase): BackOutcome {
  switch (phase) {
    case 'picker':
    case 'loading':
      return 'stay'
    case 'reading':
    case 'story-end':
    case 'questions':
    case 'celebration':
      return 'to-picker'
  }
}

/**
 * The marker on kid mode's own history entry.
 *
 * A string constant rather than a symbol because it round-trips through
 * `history.state`, which is structured-cloned by the browser and restored across
 * a reload.
 */
export const GUARD_MARK = 'sfKidGuard'

/**
 * Does this history entry still need kid mode's guard entry pushed on top of it?
 *
 * **This is the reload half, and it is why the check exists at all.** The guard
 * is one extra entry for the same URL; the session consumes it on every back
 * gesture and pushes it again. A reload restores `history.state` for the CURRENT
 * entry, so a mount that pushed unconditionally would add one entry per reload
 * and a child who reloads eleven times would need eleven back presses to reach a
 * parent who is trying to leave. Asking the entry whether it is already the
 * guard makes mount and `popstate` the same line of code and makes the stack
 * depth constant.
 */
export function needsGuardEntry(state: unknown): boolean {
  if (typeof state !== 'object' || state === null) return true
  return (state as Record<string, unknown>)[GUARD_MARK] !== true
}

/**
 * The state to push for the guard entry — the current entry's state, marked.
 *
 * **The spread is load-bearing.** React Router keeps its own bookkeeping in
 * `history.state` (`idx`, `key`, `usr`); pushing a bare `{ [GUARD_MARK]: true }`
 * would delete it under the router's feet and leave its index counter
 * describing an entry that no longer exists. Copying the entry we are shadowing
 * keeps the router's view of this location exactly as it was — which is correct,
 * because the guard entry IS this location.
 */
export function guardState(current: unknown): Record<string, unknown> {
  const base =
    typeof current === 'object' && current !== null ? { ...(current as Record<string, unknown>) } : {}
  return { ...base, [GUARD_MARK]: true }
}
