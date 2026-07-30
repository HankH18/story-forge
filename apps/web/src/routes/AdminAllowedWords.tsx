/**
 * `/admin/words` — the allowed-vocabulary browser (build doc §5.4, §11's "Admin
 * portal", §17 step 2; style guide §09).
 *
 * §5.4 says this table is three things: LLM prompt input, admin-portal browsable,
 * and "itself a demo artifact — here is every word a stage-3 reader can legally
 * see". This screen is the second and third of those.
 *
 * **The counts on screen are the server's, and §17's script is out of date.**
 * Measured against the live table: stage 1 holds 157 rows, stage 3 holds 2,593 and
 * stage 12 holds 9,360 — 64,296 in total. §17 step 2's line says "611 words" and
 * the phase brief says 9,359 at stage 12; neither is what this database contains.
 * Nothing on this screen prints a number from a document (DEVIATIONS 9.30).
 *
 * **`in_prompt` is why this is a browser and not a list.** §6 hands the generator
 * the top 400 words by frequency, not the whole stage, so "every word a stage-3
 * reader may see" and "the 400 words the model was actually shown" are two
 * different claims about the same table. The column marks which is which
 * (DEVIATIONS 9.12), and it is the honest answer to "so does the model just get a
 * dictionary?"
 */

import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { SEEDED_STAGES } from '../lib/api'
import {
  WORDS_PAGE_SIZE,
  fetchAllowedWords,
  type AdminResult,
  type AllowedWordsPage,
} from '../lib/adminApi'
import { count } from '../lib/validatorView'
import AdminShell, { AdminError, AdminPanel } from '../ui/AdminShell'

/** §5.6's debounce is about validation; this one is about not hammering a LIKE. */
const SEARCH_DEBOUNCE_MS = 250

export default function AdminAllowedWords() {
  // The stage, the page and the query live in the URL: this screen is a demo
  // artifact, and a demo artifact has to be linkable ("here is stage 3").
  const [params, setParams] = useSearchParams()
  const stage = clampStage(Number(params.get('stage') ?? 3))
  const offset = Math.max(0, Number(params.get('offset') ?? 0) || 0)
  const q = params.get('q') ?? ''

  const [search, setSearch] = useState(q)
  const [page, setPage] = useState<AdminResult<AllowedWordsPage> | null>(null)

  const setParam = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params)
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === '') next.delete(k)
        else next.set(k, v)
      }
      setParams(next, { replace: true })
    },
    [params, setParams],
  )

  // The text box writes to the URL on a short delay; the URL is what fetches.
  // One source of truth, so a back button and a typed query cannot disagree.
  useEffect(() => {
    if (search === q) return
    const timer = window.setTimeout(() => setParam({ q: search, offset: null }), SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [search, q, setParam])

  const load = useCallback(() => {
    let live = true
    setPage(null)
    void fetchAllowedWords({ stage, offset, limit: WORDS_PAGE_SIZE, q }).then((r) => {
      if (live) setPage(r)
    })
    return () => {
      live = false
    }
  }, [stage, offset, q])

  useEffect(load, [load])

  const data = page?.ok ? page.data : null
  // The stage row comes from the RESPONSE when there is one — `SEEDED_STAGES` is
  // only the pre-answer placeholder, and printing the seed's description beside
  // the server's row counts would be two sources for one stage.
  const stageRow = data?.stage.name ? data.stage : SEEDED_STAGES.find((s) => s.id === stage)
  const lastOffset = data
    ? Math.max(0, Math.floor((data.total - 1) / WORDS_PAGE_SIZE) * WORDS_PAGE_SIZE)
    : 0

  return (
    <AdminShell
      title={`Allowed words · stage ${stage}`}
      subtitle={
        <>
          GET /api/admin/allowed-words · §5.4 precompute output · machine-derived, never authored
        </>
      }
      meta={
        data ? (
          <>
            {/* §17 step 2's actual line: "every word a stage-3 reader is
                permitted to see". That is `stage_total`, which is unaffected by
                the filter — so a search narrows `total` and leaves the claim
                intact rather than appearing to shrink the vocabulary. */}
            {count(data.stage_total)} {data.stage_total === 1 ? 'word' : 'words'} at this stage
            {q ? ` · ${count(data.total)} matching “${q}”` : ''}
            {data.sight_word_count !== undefined
              ? ` · ${count(data.sight_word_count)} sight words on top (§4)`
              : ''}
          </>
        ) : null
      }
    >
      <AdminPanel>
        <div className="mb-5 flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1">
            <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-500">
              stage
            </span>
            <select
              value={stage}
              onChange={(e) => setParam({ stage: e.target.value, offset: null })}
              className="min-h-tap min-w-tap rounded-[4px] border border-ink-300 bg-paper-card px-3
                         font-mono text-[13px] text-ink-900"
            >
              {SEEDED_STAGES.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.id} · {s.name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-500">
              contains
            </span>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="sh"
              className="min-h-tap w-[220px] rounded-[4px] border border-ink-300 bg-paper-card px-3
                         font-mono text-[13px] text-ink-900"
            />
          </label>

          {/* §9.12: `%` is escaped server-side, so this really is a substring
              search and a typed wildcard is a literal. Worth saying on screen —
              someone will type one. */}
          <p className="max-w-[42ch] font-mono text-[11px] leading-[1.5] text-ink-500">
            substring match, case-insensitive · `%` and `_` are literal characters here, not
            wildcards
          </p>
        </div>

        {stageRow ? (
          <p className="mb-5 max-w-[90ch] font-mono text-[12px] leading-[1.6] text-ink-500">
            {stageRow.name} — {stageRow.description}
          </p>
        ) : null}

        {page === null ? (
          <p role="status" className="font-mono text-[12px] uppercase tracking-[0.14em] text-ink-500">
            loading…
          </p>
        ) : !page.ok ? (
          <AdminError
            status={page.status}
            detail={page.detail}
            where={page.where}
            retry={load}
          />
        ) : (
          <>
            <WordTable page={page.data} />

            <nav
              aria-label="Pages"
              className="mt-4 flex flex-wrap items-center justify-between gap-3"
            >
              <span className="font-mono text-[12px] text-ink-500">
                rows {count(page.data.offset + 1)}–
                {count(page.data.offset + page.data.words.length)} of {count(page.data.total)}
                {page.data.prompt_word_limit
                  ? ` · the generator sees the top ${count(page.data.prompt_word_limit)} by frequency (§6)`
                  : ''}
                {page.data.prompt_freq_rank_cutoff
                  ? ` — freq_rank ≤ ${count(page.data.prompt_freq_rank_cutoff)}`
                  : ''}
              </span>
              <span className="flex gap-2">
                <PageButton
                  disabled={page.data.offset <= 0}
                  onClick={() =>
                    setParam({ offset: String(Math.max(0, page.data.offset - WORDS_PAGE_SIZE)) })
                  }
                >
                  ← previous
                </PageButton>
                {/* `has_more` is the server's own cursor. Preferred over
                    arithmetic on `total`, which is what a filtered count makes
                    subtly wrong. */}
                <PageButton
                  disabled={!page.data.has_more}
                  onClick={() =>
                    setParam({
                      offset: String(
                        page.data.next_offset ?? page.data.offset + WORDS_PAGE_SIZE,
                      ),
                    })
                  }
                >
                  next →
                </PageButton>
                <PageButton
                  disabled={page.data.offset >= lastOffset}
                  onClick={() => setParam({ offset: String(lastOffset) })}
                >
                  last
                </PageButton>
              </span>
            </nav>
          </>
        )}
      </AdminPanel>
    </AdminShell>
  )
}

function clampStage(n: number): number {
  if (!Number.isFinite(n)) return 3
  return Math.min(12, Math.max(1, Math.trunc(n)))
}

function PageButton({
  children,
  disabled,
  onClick,
}: {
  children: React.ReactNode
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="min-h-tap min-w-tap rounded-[4px] border border-paper-shade bg-paper-card px-4
                 font-mono text-[12px] uppercase tracking-[0.12em] text-ink-900
                 disabled:border-paper-rule disabled:text-ink-300"
    >
      {children}
    </button>
  )
}

/**
 * §09's three-column table: word, syl, freq — right-aligned frequency, alternating
 * row fills, 1px rules.
 *
 * A real `<table>` rather than §09's CSS grid: this is a data table with a header
 * row and it should be one to a screen reader. §09's grid was drawn to make the
 * parent GPC table stack on a phone (styles/index.css `.sf-gpc-row`); the admin
 * grid is 1440px wide and nobody browses 9,360 words on a phone.
 */
function WordTable({ page }: { page: AllowedWordsPage }) {
  const hasPromptFlag = page.words.some((w) => w.in_prompt !== undefined)

  if (!page.words.length) {
    return (
      <p className="font-mono text-[13px] text-ink-500">
        no rows. This is the server's answer, not an empty screen — either the filter matches
        nothing at this stage, or `make precompute` has not been run against this database.
      </p>
    )
  }

  return (
    <div className="overflow-hidden rounded-[4px] border border-paper-shade">
      <table className="w-full border-collapse font-mono text-[13px]">
        <caption className="sr-only">
          Allowed words at stage {page.stage.id}, rows {page.offset + 1} onward
        </caption>
        <thead>
          <tr className="bg-paper-warm">
            <Th>word</Th>
            <Th>syl</Th>
            <Th align="right">freq</Th>
            {hasPromptFlag ? <Th>in prompt</Th> : null}
          </tr>
        </thead>
        <tbody>
          {page.words.map((w, i) => (
            <tr
              key={`${w.word}-${i}`}
              className={i % 2 ? 'bg-paper-card' : 'bg-transparent'}
            >
              <td className="border-t border-paper-rule px-[22px] py-[9px] text-ink-900">
                {w.word}
              </td>
              <td className="border-t border-paper-rule px-[22px] py-[9px] text-ink-700">
                {w.syllables}
              </td>
              <td className="border-t border-paper-rule px-[22px] py-[9px] text-right text-ink-700">
                {count(w.freq_rank)}
              </td>
              {hasPromptFlag ? (
                <td className="border-t border-paper-rule px-[22px] py-[9px]">
                  {/* §11: never colour alone — the word is the signal, the tint
                      is the reinforcement. */}
                  {w.in_prompt ? (
                    <span className="text-leaf-700">yes</span>
                  ) : (
                    <span className="text-ink-300">no</span>
                  )}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Th({ children, align = 'left' }: { children: React.ReactNode; align?: 'left' | 'right' }) {
  return (
    <th
      scope="col"
      className={`px-[22px] py-[10px] font-mono text-[11px] font-normal uppercase
                  tracking-[0.12em] text-ink-500 ${align === 'right' ? 'text-right' : 'text-left'}`}
    >
      {children}
    </th>
  )
}
