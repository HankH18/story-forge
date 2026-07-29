/**
 * Parent home — `/app` (build doc §11 routes; style guide §08, §10).
 *
 * "Kid picker row of avatars" plus an add-kid affordance, and nothing else. The
 * temptation on a screen like this is a summary strip per child — last read,
 * streak, a little sparkline — and §08 rules all three out: "no invented scores,
 * no gamified streaks". Stage is the only number here because stage is the only
 * number that is a fact rather than a summary, and each child's real numbers are
 * one tap away on their own dashboard.
 *
 * Two actions per reader, because a parent standing here is about to do one of
 * exactly two things: look at how it is going, or hand over the tablet.
 */

import { useUser } from '@clerk/clerk-react'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchKids, type Kid, type Result } from '../lib/api'
import KidAvatar from '../ui/KidAvatar'
import ParentShell, { ParentError } from '../ui/ParentShell'
import { stageName } from '../lib/api'

export default function ParentHome() {
  const [state, setState] = useState<Result<Kid[]> | null>(null)
  const { user } = useUser()
  // The account control (and the sign-out) is in the shell header; this is the
  // one line of copy that makes the session legible on the screen a parent
  // actually lands on. It exists because the failure this phase fixed was
  // invisible: with the API's demo bypass on, an app with no session at all
  // rendered exactly like an app with one.
  const signedInAs = user?.primaryEmailAddress?.emailAddress ?? user?.username ?? null

  const load = useCallback(() => {
    let live = true
    setState(null)
    void fetchKids().then((r) => {
      if (live) setState(r)
    })
    return () => {
      live = false
    }
  }, [])

  useEffect(load, [load])

  return (
    <ParentShell>
      <header className="mb-8">
        {signedInAs ? (
          <p className="font-mono text-[12px] uppercase tracking-[0.14em] text-ink-500">
            Signed in as {signedInAs}
          </p>
        ) : null}
        <h1 className="mt-2 font-display text-[40px] leading-[1.1] text-ink-900">Your readers</h1>
        <p className="mt-3 max-w-[68ch] text-[17px] leading-[1.6] text-ink-700">
          Every story is generated fresh and checked, word by word, against the letter-sounds your
          reader has been taught. Open a reader to see which sounds are sticking.
        </p>
      </header>

      {state === null ? (
        <p className="font-mono text-[13px] uppercase tracking-[0.14em] text-ink-500">Loading…</p>
      ) : !state.ok ? (
        <ParentError status={state.status} message={state.message} retry={load} />
      ) : state.data.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {state.data.map((kid) => (
            <li key={kid.id}>
              <KidCard kid={kid} />
            </li>
          ))}
          <li>
            <AddKidCard />
          </li>
        </ul>
      )}

      {/* §08's own note, kept as visible copy rather than a code comment: it is
          the answer to "why can't I add a photo?", and a parent who wonders that
          deserves the reason on the screen. */}
      <p className="mt-10 max-w-[68ch] text-[14px] leading-[1.6] text-ink-500">
        Story Forge stores a first name and a stage for each reader — no birthday, no email, no
        photo, and no microphone anywhere in the app. Avatars are the first initial on a colour.
      </p>
    </ParentShell>
  )
}

function KidCard({ kid }: { kid: Kid }) {
  return (
    <div className="flex h-full flex-col gap-4 rounded-card border-2 border-paper-rule bg-paper-card p-6 shadow-lift-parent">
      <div className="flex items-center gap-4">
        <KidAvatar kidId={kid.id} name={kid.display_name} />
        <div>
          <div className="font-display text-[22px] leading-tight text-ink-900">
            {kid.display_name}
          </div>
          <div className="mt-1 text-[14px] text-ink-500">
            Stage {kid.stage_id} · {stageName(kid.stage_id)}
          </div>
        </div>
      </div>

      <div className="mt-auto flex flex-wrap gap-3">
        <Link
          to={`/dashboard/${kid.id}`}
          className="sf-lift-parent inline-flex min-h-tap min-w-tap items-center justify-center
                     rounded-btn border-2 border-paper-rule bg-paper-card px-5 font-ui text-[16px]
                     font-bold text-ink-900 transition-transform duration-[90ms] ease-out"
        >
          Progress
        </Link>
        <Link
          to={`/read/${kid.id}`}
          className="sf-lift-parent inline-flex min-h-tap min-w-tap items-center justify-center
                     rounded-btn border-2 border-berry-700 bg-berry-500 px-5 font-ui text-[16px]
                     font-bold text-paper-page transition-transform duration-[90ms] ease-out"
        >
          Read a story
        </Link>
      </div>
    </div>
  )
}

function AddKidCard() {
  return (
    <Link
      to="/app/add-kid"
      className="flex h-full min-h-[160px] flex-col items-center justify-center gap-3 rounded-card
                 border-2 border-dashed border-ink-300 bg-paper-page p-6 text-center"
    >
      <KidAvatar kidId={0} name="" placeholder="+" />
      <span className="font-ui text-[16px] font-bold text-ink-700">Add a reader</span>
      <span className="text-[14px] text-ink-500">A first name and a starting stage</span>
    </Link>
  )
}

function EmptyState() {
  return (
    <div className="rounded-card border-2 border-paper-rule bg-paper-card p-8 shadow-lift-parent">
      <h2 className="font-display text-[26px] text-ink-900">No readers yet</h2>
      <p className="mt-3 max-w-[60ch] text-[16px] leading-[1.6] text-ink-700">
        Add your first reader to get started. You will be asked for a first name and roughly where
        they are in phonics — you can change the stage at any time, and Story Forge moves it on its
        own once the reading shows they are ready.
      </p>
      <Link
        to="/app/add-kid"
        className="sf-lift-parent mt-6 inline-flex min-h-tap min-w-tap items-center justify-center
                   rounded-btn border-2 border-berry-700 bg-berry-500 px-6 font-ui text-[17px]
                   font-bold text-paper-page transition-transform duration-[90ms] ease-out"
      >
        Add a reader
      </Link>
    </div>
  )
}
