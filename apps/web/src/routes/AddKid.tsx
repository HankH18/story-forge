/**
 * Add a reader — `/app/add-kid` (style guide §08; build doc §13).
 *
 * Two fields. A first name, and where to start.
 *
 * **There is no third field and there is not going to be one.** §13: no email,
 * no date of birth, no photo, no microphone. §08 says it in one line — "the
 * add-kid form asks for a name and a starting stage — never a birthday". The
 * reason a birthday is the named example is that it is the field everyone adds
 * without thinking, to "personalise" or to "check the age", and under COPPA it
 * is the one that changes what this app legally is. The server's `KidCreate`
 * has the same two fields, so the two layers agree.
 *
 * The form says all of that out loud, because a parent typing their child's name
 * into a website is entitled to know what happens to it, and the answer here is
 * unusually good.
 */

import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { createKid, getStages, type Stage } from '../lib/api'
import ParentShell from '../ui/ParentShell'
import { initialFor, tintFor } from '../ui/KidAvatar'

/** Mirrors `MAX_DISPLAY_NAME` in services/api/storyforge/api/readings.py. */
const MAX_NAME = 40

export default function AddKid() {
  const navigate = useNavigate()
  const [stages, setStages] = useState<Stage[]>([])
  const [name, setName] = useState('')
  const [stageId, setStageId] = useState(1)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    void getStages().then(setStages)
  }, [])

  const trimmed = name.trim()
  // Mirrors the server's `_no_contact_details`. Checked here as well so the
  // parent is told before the round trip, never *instead* of the server.
  const looksLikeEmail = trimmed.includes('@')
  const valid = trimmed.length > 0 && trimmed.length <= MAX_NAME && !looksLikeEmail

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!valid || submitting) return
    setSubmitting(true)
    setError(null)

    const result = await createKid(trimmed, stageId)
    if (result.ok) {
      navigate(`/dashboard/${result.data.id}`, { replace: true })
      return
    }

    setSubmitting(false)
    setError(
      result.status === 401 || result.status === 403
        ? 'Your session has expired. Sign in again and this reader can be added.'
        : result.status === 0
          ? 'Could not reach Story Forge. Nothing was saved — try again.'
          : `Could not add this reader (${result.message}).`,
    )
    nameRef.current?.focus()
  }

  const selected = stages.find((s) => s.id === stageId)

  return (
    <ParentShell>
      <div className="mx-auto max-w-[720px]">
        <Link
          to="/app"
          className="inline-flex min-h-tap items-center rounded-btn font-ui text-[15px] text-ink-500"
        >
          ← All readers
        </Link>

        <h1 className="mt-4 font-display text-[40px] leading-[1.1] text-ink-900">Add a reader</h1>
        <p className="mt-3 max-w-[68ch] text-[17px] leading-[1.6] text-ink-700">
          Story Forge needs two things: what to call your reader, and roughly where they are in
          phonics so the first story is decodable for them.
        </p>

        <form onSubmit={onSubmit} noValidate className="mt-8 flex flex-col gap-6">
          {/* --- first name ------------------------------------------------- */}
          <div className="rounded-card border-2 border-paper-rule bg-paper-card p-6 shadow-lift-parent">
            <label htmlFor="kid-name" className="font-display text-[22px] text-ink-900">
              First name
            </label>
            <p className="mt-1 text-[15px] leading-[1.5] text-ink-500">
              Just a first name, or whatever you call them. It appears on their own screen and
              nowhere else.
            </p>

            <div className="mt-4 flex items-center gap-4">
              {/* A live preview of the avatar, which is the whole of what this
                  name becomes visually — and a quiet demonstration that there is
                  no photo step coming. */}
              <span
                aria-hidden="true"
                className="sf-lift-kid flex h-[64px] w-[64px] shrink-0 items-center justify-center
                           rounded-pill border-[3px] border-ink-900 font-display text-[26px]
                           leading-none text-ink-900"
                style={{ backgroundColor: tintFor(stageId) }}
              >
                {trimmed ? initialFor(trimmed) : '?'}
              </span>

              <input
                id="kid-name"
                ref={nameRef}
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={MAX_NAME}
                autoComplete="off"
                spellCheck={false}
                aria-describedby={looksLikeEmail ? 'kid-name-error' : undefined}
                aria-invalid={looksLikeEmail || undefined}
                className="min-h-tap w-full rounded-btn border-2 border-paper-shade bg-paper-page
                           px-4 py-2 font-ui text-[19px] text-ink-900 placeholder:text-ink-300"
                placeholder="Maya"
              />
            </div>

            {looksLikeEmail ? (
              <p id="kid-name-error" role="alert" className="mt-3 text-[15px] text-berry-700">
                That looks like an email address. Story Forge does not store email addresses for
                children — a first name is all it needs.
              </p>
            ) : null}
          </div>

          {/* --- starting stage ---------------------------------------------- */}
          <fieldset className="rounded-card border-2 border-paper-rule bg-paper-card p-6 shadow-lift-parent">
            {/* `float-left w-full` is the standard cure for a `<legend>` cutting
                a notch in its fieldset's border: floated, it flows as an
                ordinary block inside the box instead of being painted over the
                top rule. The element stays a real `<legend>`, which is what
                gives the twelve radios their group name. */}
            <legend className="float-left w-full font-display text-[22px] text-ink-900">
              Starting stage
            </legend>
            <p className="mt-1 max-w-[68ch] text-[15px] leading-[1.5] text-ink-500">
              Pick the last row your reader is comfortable with. If you are not sure, start lower —
              an easy story is a good day and a hard one is not, and Story Forge moves the stage up
              on its own once the answers show they are ready.
            </p>

            <div className="mt-5 flex flex-col gap-2">
              {stages.map((stage) => {
                const checked = stage.id === stageId
                return (
                  <label
                    key={stage.id}
                    className={[
                      'flex min-h-tap cursor-pointer items-start gap-4 rounded-btn border-2 px-4 py-3',
                      // §11: never colour alone — the checked row has a fill, a
                      // rule, AND the radio dot itself.
                      checked
                        ? 'border-paper-shade bg-paper-warm'
                        : 'border-transparent bg-transparent',
                    ].join(' ')}
                  >
                    <input
                      type="radio"
                      name="stage"
                      value={stage.id}
                      checked={checked}
                      onChange={() => setStageId(stage.id)}
                      className="mt-[6px] h-[20px] w-[20px] shrink-0 accent-berry-500"
                    />
                    <span>
                      <span className="font-ui text-[17px] font-bold text-ink-900">
                        Stage {stage.id} · {stage.name}
                      </span>
                      <span className="mt-[2px] block text-[15px] leading-[1.45] text-ink-700">
                        {stage.description}
                      </span>
                    </span>
                  </label>
                )
              })}
            </div>
          </fieldset>

          {error ? (
            <p role="alert" className="text-[16px] leading-[1.5] text-berry-700">
              {error}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-4">
            <button
              type="submit"
              disabled={!valid || submitting}
              className="sf-lift-parent inline-flex min-h-tap min-w-tap items-center justify-center
                         rounded-btn border-2 border-berry-700 bg-berry-500 px-7 font-ui text-[17px]
                         font-bold text-paper-page transition-transform duration-[90ms] ease-out
                         disabled:cursor-default disabled:border-paper-shade disabled:bg-paper-warm
                         disabled:text-ink-300 disabled:shadow-none"
            >
              {submitting ? 'Adding…' : 'Add reader'}
            </button>
            <Link
              to="/app"
              className="inline-flex min-h-tap min-w-tap items-center justify-center rounded-btn
                         px-4 font-ui text-[16px] text-ink-700"
            >
              Cancel
            </Link>
          </div>

          {/* §13, stated where it applies rather than buried in a policy page. */}
          <p className="max-w-[68ch] text-[14px] leading-[1.6] text-ink-500">
            That is the whole form. Story Forge asks for no birthday, no email address, and no
            photo for a child, and there is no microphone or camera anywhere in the app — reading
            aloud is never recorded. {selected ? `${selected.name} it is.` : null}
          </p>
        </form>
      </div>
    </ParentShell>
  )
}
