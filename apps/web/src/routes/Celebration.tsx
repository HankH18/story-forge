/**
 * Celebration — §11.2 step 4, style guide §07 "Celebration".
 *
 * "Foil seals — gold-500 disc, ink border, gold-300 inner ring · unearned =
 * dashed ink-300 outline." Max 3 stars, no confetti, no score, no streak. The
 * reward is a printed thing: a foil seal on the page for each sound the child
 * proved they could hear.
 *
 * Everything on this screen is earned or absent. There is no invented number:
 * the word count is the distinct words on the pages the child actually turned,
 * and the seals and deltas are whatever `POST /api/readings/{id}/complete` plus
 * `GET /api/kids/{id}/progress` say (§9) — nothing else. If those calls do not
 * answer, the section is simply not there; a fabricated "+2 · 8 of 10" would be
 * exactly the invented score §08 forbids for parents and §07 forbids for kids.
 *
 * Note that a seal can legitimately come back unearned after the child answered
 * correctly: the server logs the FIRST attempt (§07 — "the child retries until
 * correct; the attempt is what's logged"), so a right answer on the second try
 * scores a dashed seal. That is the honest reading of what happened.
 *
 * Two ways out, both explicit: read another, or stop. No auto-advance into
 * another story — deciding to keep reading is the child's, and an autoplaying
 * next story is how a reading app becomes a slot machine.
 */

import type { CompleteResult, Progress, Story } from '../lib/api'
import { speak } from '../lib/speech'
import Button from '../ui/Button'
import Card from '../ui/Card'
import StarBurst from '../ui/StarBurst'
import StoryWordChip from '../ui/StoryWordChip'

export interface CelebrationProps {
  story: Story
  /** Distinct words on the pages actually read. */
  wordCount: number
  /** What `POST /api/readings/{id}/complete` changed, if it answered. */
  completed: CompleteResult | null
  progress: Progress | null
  onReadAnother: () => void
  onAllDone: () => void
}

/**
 * §07: "gold-500 disc, ink border, gold-300 inner ring · unearned = dashed
 * ink-300 outline". Earned and unearned differ by fill, border style AND the
 * accessible name — never by colour alone (§11).
 */
function Seal({ grapheme, earned }: { grapheme: string; earned: boolean }) {
  return (
    <span
      aria-label={
        earned ? `you got the ${grapheme} sound` : `keep practising the ${grapheme} sound`
      }
      className={[
        'inline-flex h-[72px] w-[72px] items-center justify-center rounded-full',
        earned
          ? 'border-[3px] border-ink-900 bg-gold-500 ring-4 ring-inset ring-gold-300'
          : 'border-[3px] border-dashed border-ink-300 bg-paper-warm',
      ].join(' ')}
    >
      <span className={`font-read text-[28px] ${earned ? 'text-ink-900' : 'text-ink-500'}`}>
        {grapheme}
      </span>
    </span>
  )
}

export default function Celebration({
  story,
  wordCount,
  completed,
  progress,
  onReadAnother,
  onAllDone,
}: CelebrationProps) {
  /**
   * Only the sounds THIS reading moved. `gpc_updates` names them and
   * `gpc_accuracy` carries the running totals, so a row can say both "+1 just
   * now" and "3 of 4 so far" without either number being invented here.
   *
   * The seals come from the same place rather than from the question payloads:
   * the server is what decided the answer was right, and it is what will still
   * be right after `answers` is re-read. Nothing on this screen is the client's
   * opinion of how the child did.
   */
  const touched = new Map((completed?.gpc_updates ?? []).map((d) => [d.gpc_id, d]))
  const rows = (progress?.gpc_accuracy ?? []).filter((row) => touched.has(row.gpc_id)).slice(0, 6)
  const promoted = completed?.promoted === true

  return (
    // Two columns at the §10 landscape reference so the whole celebration fits
    // 1024×768 without scrolling, the same rule the reading view lives under.
    <main
      className="sf-page-in mx-auto grid min-h-screen w-full max-w-[1024px] grid-cols-1
                 items-center gap-8 px-6 py-8 lg:grid-cols-2"
    >
      <section className="flex flex-col items-center gap-6 text-center">
        <StarBurst count={3} />
        <h1 className="font-display text-[40px] leading-[1.15] text-ink-900">
          You read the whole story!
        </h1>
        {/* §12 "Do": one Kalam phrase per screen, max. This is it. */}
        <p className="font-hand text-[24px] text-ink-500">
          that&rsquo;s {wordCount} words today
        </p>
        <div className="flex flex-wrap items-center justify-center gap-4">
          <Button variant="kid-primary" size="kid" onClick={onReadAnother}>
            Read another
          </Button>
          <Button variant="kid-quiet" size="kid" onClick={onAllDone}>
            All done
          </Button>
        </div>
      </section>

      <section className="flex flex-col gap-6">
        {promoted ? (
          // §08 forbids invented scores and §07 forbids numeric progress for
          // kids, so promotion is announced as a fact about sounds, not a
          // level-up with a number on it.
          <Card surface="kid" className="w-full bg-gold-300 p-5 text-center">
            <p className="font-display text-[24px] text-ink-900">
              You are ready for new sounds!
            </p>
          </Card>
        ) : null}

        {rows.length ? (
          <Card surface="kid" className="w-full p-6">
            <h2 className="text-center font-display text-[24px] text-ink-900">
              Sounds you read
            </h2>
            <div className="mt-5 flex flex-wrap justify-center gap-4">
              {rows.map((row) => (
                <Seal
                  key={row.gpc_id}
                  grapheme={row.grapheme}
                  earned={(touched.get(row.gpc_id)?.n_correct ?? 0) > 0}
                />
              ))}
            </div>
            {/* §07's "+2 · 8 of 10" line, minus the phoneme: the API sends
                ARPABET ("AE"), which means nothing to a six-year-old. The
                parent dashboard (§08) is where the phoneme column belongs. */}
            <ul className="mt-5 flex flex-col gap-2">
              {rows.map((row) => {
                const delta = touched.get(row.gpc_id)
                return (
                  <li key={row.gpc_id} className="flex items-center gap-4">
                    <span className="font-read text-[22px] text-ink-900">{row.grapheme}</span>
                    <span className="ml-auto font-ui text-[17px] font-bold text-leaf-700">
                      {delta && delta.n_correct > 0 ? `+${delta.n_correct}` : null}
                    </span>
                    <span className="font-ui text-[15px] text-ink-500">
                      {row.n_correct} of {row.n_seen}
                    </span>
                  </li>
                )
              })}
            </ul>
          </Card>
        ) : null}

        {story.story_words.length ? (
          <Card surface="kid" className="w-full p-6">
            <h2 className="text-center font-display text-[24px] text-ink-900">New words</h2>
            <p className="mt-3 text-center font-read text-read text-ink-900">
              {story.story_words.map((word) => (
                <StoryWordChip key={word} word={word} onSpeak={(w) => speak(w)} />
              ))}
            </p>
          </Card>
        ) : null}
      </section>
    </main>
  )
}
