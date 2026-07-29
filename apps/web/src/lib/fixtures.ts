/**
 * DEV-ONLY fixture. Never reachable in a production build.
 *
 * §10 gives stories 0–2 branch points, but the warm pool is currently all
 * linear — `choice_label` is null on every row in the database, so the branch
 * screen has no live path to be driven through. Rather than ship branching as
 * code nobody has ever seen run, this fixture makes it drivable at
 * `/read/<one of your own kid ids>?fixture=branch` while `bun run dev` is
 * running. It has to be a real, owned reader now: `/read/1` used to work because
 * `resolveKid` substituted the account's first kid for an id it did not
 * recognise, and that substitution is exactly what DEVIATIONS 7.33 removed. The
 * fixture replaces the STORY, never the reader.
 *
 * Two guards keep it out of the child's way: the query parameter, and
 * `import.meta.env.DEV`, which Vite replaces with `false` at build time so the
 * whole module is dropped by dead-code elimination. Nothing here is content —
 * it is the shape of content, written at roughly stage 3 so it renders at a
 * believable size. Delete it once the generator emits branches.
 */

import type { Story } from './api'

export const BRANCH_FIXTURE_KEY = 'branch'

export function isFixtureRequested(search: string): boolean {
  if (!import.meta.env.DEV) return false
  return new URLSearchParams(search).get('fixture') === BRANCH_FIXTURE_KEY
}

export function branchFixture(): Story {
  return {
    id: -1,
    stage_id: 3,
    theme_id: 1,
    theme_slug: 'dragons',
    theme_display_name: 'Dragons',
    status: 'warm',
    title: 'Sam and the Ship',
    decodability: 1,
    story_words: ['dragon'],
    segments: [
      {
        id: -10,
        idx: 0,
        body: 'Sam had a big red ship. The ship did not sink.',
        scene_tags: [],
        parent_segment_id: null,
        choice_label: null,
        asset: null,
      },
      {
        id: -11,
        idx: 1,
        body: 'Sam met a dragon in the fog.',
        scene_tags: [],
        parent_segment_id: null,
        choice_label: null,
        asset: null,
      },
      // The two arms of the branch off segment -11.
      {
        id: -12,
        idx: 0,
        body: 'Sam ran up the hill. The dragon sat and did not run.',
        scene_tags: [],
        parent_segment_id: -11,
        choice_label: 'Run up the hill.',
        asset: null,
      },
      {
        id: -13,
        idx: 1,
        body: 'Sam got in the ship. The dragon got in with him.',
        scene_tags: [],
        parent_segment_id: -11,
        choice_label: 'Get in the ship.',
        asset: null,
      },
      {
        id: -14,
        idx: 2,
        body: 'Sam and the dragon had a fun trip back.',
        scene_tags: [],
        parent_segment_id: null,
        choice_label: null,
        asset: null,
      },
    ],
    questions: [
      {
        id: -20,
        segment_id: null,
        idx: 0,
        qtype: 'tap_word',
        prompt: 'Tap: shed.',
        options: ['ship', 'ran', 'big'],
        payload: { options: ['ship', 'ran', 'big'], target_grapheme: 'sh', cue_word: 'shed' },
      },
      {
        id: -21,
        segment_id: null,
        idx: 1,
        qtype: 'mcq2',
        prompt: 'Did Sam have a red ship?',
        options: ['Sam had a big red ship.', 'Sam had a big red hat.'],
        payload: { options: ['Sam had a big red ship.', 'Sam had a big red hat.'] },
      },
    ],
    fallback: null,
  }
}
