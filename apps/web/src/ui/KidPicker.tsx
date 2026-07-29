/**
 * KidPicker — §08's "Kid picker & add", and §10's "kid picker row of avatars".
 *
 * Two densities, one component, because they are the same row doing the same
 * job at two sizes: the parent home shows it large as the page's main content,
 * and the dashboard shows it small as a switcher. Duplicating it would be how
 * the two eventually disagree about what a kid looks like.
 *
 * Every entry is a link, sized past §11's 48px floor with 8px of clear space —
 * an avatar is 52 or 76px and the label sits inside the same target, so the name
 * is tappable too rather than being a 13px decoration next to the real control.
 */

import { Link } from 'react-router-dom'
import type { Kid } from '../lib/api'
import { stageName } from '../lib/api'
import KidAvatar from './KidAvatar'

export interface KidPickerProps {
  kids: Kid[]
  /** Highlighted as the reader currently being viewed. */
  currentKidId?: number
  size?: 'sm' | 'md'
  /** Where a kid's tile goes. */
  hrefFor?: (kid: Kid) => string
  /** Hidden on the dashboard switcher, where adding a kid is not the job. */
  showAdd?: boolean
}

export default function KidPicker({
  kids,
  currentKidId,
  size = 'md',
  hrefFor = (kid) => `/dashboard/${kid.id}`,
  showAdd = true,
}: KidPickerProps) {
  return (
    <ul className="flex flex-wrap items-start gap-4">
      {kids.map((kid) => {
        const current = kid.id === currentKidId
        return (
          <li key={kid.id}>
            <Link
              to={hrefFor(kid)}
              aria-current={current ? 'page' : undefined}
              className={[
                'flex min-h-tap min-w-tap flex-col items-center gap-2 rounded-card px-3 py-2',
                'text-center transition-colors duration-[90ms]',
                // §11: never colour alone. "Currently viewing" is a fill AND a
                // 2px rule AND the word itself under the name — the fill alone
                // would be the only difference on a monochrome screen.
                current ? 'border-2 border-paper-shade bg-paper-warm' : 'border-2 border-transparent',
              ].join(' ')}
            >
              <KidAvatar kidId={kid.id} name={kid.display_name} size={size} />
              <span className="font-ui text-[15px] font-bold text-ink-900">{kid.display_name}</span>
              <span className="text-[13px] text-ink-500">
                Stage {kid.stage_id} · {stageName(kid.stage_id)}
              </span>
              {current ? (
                <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-500">
                  viewing
                </span>
              ) : null}
            </Link>
          </li>
        )
      })}

      {showAdd ? (
        <li>
          <Link
            to="/app/add-kid"
            className="flex min-h-tap min-w-tap flex-col items-center gap-2 rounded-card border-2
                       border-transparent px-3 py-2 text-center"
          >
            <KidAvatar kidId={0} name="" size={size} placeholder="+" />
            <span className="font-ui text-[15px] text-ink-500">Add a reader</span>
          </Link>
        </li>
      ) : null}
    </ul>
  )
}
