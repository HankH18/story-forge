/**
 * GpcTable — §11's per-GPC accuracy table. "Raw table is the v1; charts are
 * post-cut-line polish" (build doc §11, restated by style guide §11).
 *
 * Columns are §08's: Letters · Sound · Accuracy · Seen.
 *
 * Three things are load-bearing and none of them are decoration.
 *
 * **ARPABET never reaches the screen.** The API's `phonemes` field is "AE",
 * "IH Z", "CH ER". `glossSound` turns that into an example word and IPA — see
 * lib/phonics.ts for why that direction and not the other.
 *
 * **Rows with zero evidence are shown, not filtered.** The API returns every
 * correspondence in the child's cumulative inventory including the untouched
 * ones, and hiding them would turn "we have not asked her about `sh` yet" into
 * "she has no trouble with `sh`". A parent reading a table of six green rows
 * would conclude the second. The default view is therefore the sounds with
 * evidence, with an explicit, counted toggle for the rest — the information is
 * never removed, only folded.
 *
 * **It stacks rather than scrolls.** §10: "the table becomes stacked rows, never
 * a horizontal scroll." `.sf-gpc-row` is a grid that collapses to one column
 * under 768px; the explicit `role` attributes are what keep the thing a table
 * to a screen reader once `display: grid` has removed the native roles.
 */

import { useMemo, useState } from 'react'
import { BAND_LABEL, FEW_SAMPLES_BELOW, bandFor, glossSound } from '../lib/phonics'
import type { GpcAccuracy } from '../lib/api'
import GpcBar, { BandLegend } from './GpcBar'

/** Shown above every stacked cell below 768px, where the header row is gone. */
function CellLabel({ children }: { children: string }) {
  return (
    <span
      aria-hidden="true"
      className="mr-2 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-500 md:hidden"
    >
      {children}
    </span>
  )
}

export interface GpcTableProps {
  rows: GpcAccuracy[]
  /** The child's first name — this table is about a specific reader. */
  kidName: string
  currentStage: number
}

export default function GpcTable({ rows, kidName, currentStage }: GpcTableProps) {
  const [showAll, setShowAll] = useState(false)

  const { seen, unseen } = useMemo(() => {
    // Sort: current-stage correspondences first (they are the ones promotion is
    // measured over — §8), then most-practised, then alphabetical so the order
    // is stable between reloads rather than shuffling on every fetch.
    const sorted = [...rows].sort((a, b) => {
      if (a.current_stage !== b.current_stage) return a.current_stage ? -1 : 1
      if (a.n_seen !== b.n_seen) return b.n_seen - a.n_seen
      return a.gpc_id.localeCompare(b.gpc_id)
    })
    return {
      seen: sorted.filter((r) => r.n_seen > 0),
      unseen: sorted.filter((r) => r.n_seen === 0),
    }
  }, [rows])

  const visible = showAll ? [...seen, ...unseen] : seen

  return (
    <section
      aria-labelledby="gpc-heading"
      className="overflow-hidden rounded-card border-2 border-paper-rule bg-paper-card shadow-lift-parent"
    >
      <div className="px-[26px] pb-[18px] pt-[22px]">
        <h2 id="gpc-heading" className="font-display text-[22px] text-ink-900">
          Sounds {kidName} is practising
        </h2>
        {/* §08's exact caveat, and it is the most important sentence on the
            page: it is what stops the numbers being read as a reading score. */}
        <p className="mt-[4px] max-w-[68ch] text-[15px] leading-[1.5] text-ink-500">
          Counted only from words {kidName} answered a question about — not from words that merely
          appeared in a story. A letter-and-sound pair is one row: <i>th</i> in “thin” and <i>th</i>{' '}
          in “this” are two different sounds and are counted separately.
        </p>
      </div>

      <table role="table" className="w-full border-collapse text-left">
        <thead role="rowgroup" className="hidden md:table-header-group">
          <tr
            role="row"
            className="sf-gpc-row border-y-2 border-paper-rule bg-paper-warm px-[26px] py-[12px] font-mono text-[11px] uppercase tracking-[0.14em] text-ink-500"
          >
            <th role="columnheader" scope="col" className="font-normal">
              Letters
            </th>
            <th role="columnheader" scope="col" className="font-normal">
              Sound
            </th>
            <th role="columnheader" scope="col" className="font-normal">
              Accuracy
            </th>
            <th role="columnheader" scope="col" className="text-right font-normal">
              Seen
            </th>
          </tr>
        </thead>

        <tbody role="rowgroup">
          {visible.map((row) => {
            const gloss = glossSound(row.grapheme, row.phonemes)
            const band = bandFor(row.accuracy, row.n_seen)
            const pct = row.accuracy === null ? null : Math.round(row.accuracy * 100)

            return (
              <tr
                key={row.gpc_id}
                role="row"
                className="sf-gpc-row border-b border-paper-rule px-[26px] py-[14px] text-[16px]"
              >
                <td role="cell" className="flex items-baseline gap-2">
                  {/* Andika — the reading font. These are the letters as the
                      child meets them on the page, not a UI label. */}
                  <span className="font-read text-[22px] font-bold text-ink-900">
                    {row.grapheme}
                  </span>
                  {row.current_stage ? (
                    <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-ink-500">
                      stage {currentStage}
                    </span>
                  ) : null}
                </td>

                <td role="cell" className="text-[15px] leading-[1.4] text-ink-700">
                  <CellLabel>Sound</CellLabel>
                  {gloss.silent ? (
                    <span>
                      silent
                      {gloss.example ? (
                        <span className="text-ink-500"> — as in “{gloss.example}”</span>
                      ) : null}
                    </span>
                  ) : (
                    <span>
                      {gloss.example ? <>as in “{gloss.example}”</> : null}
                      {gloss.ipa ? (
                        <span className="ml-2 font-mono text-[13px] text-ink-500">
                          /{gloss.ipa}/
                        </span>
                      ) : null}
                    </span>
                  )}
                </td>

                <td role="cell" className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <CellLabel>Accuracy</CellLabel>
                  <GpcBar accuracy={row.accuracy} nSeen={row.n_seen} className="min-w-[110px]" />
                  <span className="font-mono text-[14px] text-ink-700">
                    {pct === null ? '—' : `${pct}%`}
                  </span>
                  {/* §11's non-colour signal, in words. */}
                  <span className="text-[13px] text-ink-500">{BAND_LABEL[band]}</span>
                </td>

                <td
                  role="cell"
                  className="font-mono text-[14px] text-ink-500 md:justify-self-end md:text-right"
                >
                  <CellLabel>Seen</CellLabel>
                  {row.n_seen}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {seen.length === 0 && !showAll ? (
        <p className="px-[26px] py-[20px] text-[15px] leading-[1.55] text-ink-700">
          No sounds have been counted yet. They start filling in once {kidName} answers the
          questions at the end of a story — reading a story on its own does not count towards
          these numbers.
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-4 px-[26px] py-[16px]">
        <BandLegend fewSamplesBelow={FEW_SAMPLES_BELOW} />

        {unseen.length > 0 ? (
          <button
            type="button"
            onClick={() => setShowAll((v) => !v)}
            aria-expanded={showAll}
            className="sf-lift-parent inline-flex min-h-tap min-w-tap items-center justify-center rounded-btn
                       border-2 border-paper-rule bg-paper-card px-5 font-ui text-[15px] font-bold
                       text-ink-900 transition-transform duration-[90ms] ease-out"
          >
            {showAll
              ? `Hide the ${unseen.length} not yet asked about`
              : `Show ${unseen.length} sounds not yet asked about`}
          </button>
        ) : null}
      </div>
    </section>
  )
}
