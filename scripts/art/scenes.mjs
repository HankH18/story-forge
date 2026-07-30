/**
 * The 33 curated illustrations (§12) and the 14 story-word cutouts (§06),
 * composed from `kit.mjs`.
 *
 * Each entry's key is the file it writes and its `tags` MUST equal the tag set
 * on the matching `data/seed/assets.csv` row — `generate-art.mjs` diffs the two
 * and fails if they drift, because a picture that no longer matches its tags is
 * a picture no segment can reach.
 *
 * Order in this file is the MEASURED priority from DEVIATIONS 11.21: the nine
 * assets that win 736 of the 831 art-bearing segments come first.
 */

import {
  P,
  at,
  bird,
  castle,
  cat,
  caveMouth,
  circle,
  cloud,
  creature,
  doc,
  dog,
  dragon,
  dragonWingPart,
  ellipse,
  fern,
  fish,
  flag,
  flameBurst,
  fox,
  ground,
  hills,
  knight,
  litEllipse,
  moonBall,
  mountain,
  nest,
  path,
  pine,
  planetBall,
  rabbit,
  rect,
  rocketShip,
  rocks,
  starfield,
  star4,
  stroke,
  sun,
  treasurePile,
  tree,
  tufts,
  village,
  cutout,
  astronaut,
} from './kit.mjs'

const W = 1200
const H = 900
const CW = 800

/* -------------------------------------------------------------------------- */
/* reusable backdrops at scene scale                                          */
/* -------------------------------------------------------------------------- */

const dayBackdrop = (horizon = 600) =>
  rect(0, 0, W, H, P.sky100) +
  cloud(210, 150, 2.1) +
  cloud(880, 110, 1.6) +
  cloud(620, 230, 1.2, P.page) +
  at(1010, 40, 1.5, sun(30)) +
  hills(W, horizon - 60, P.leaf, P.leaf700) +
  ground(W, H, horizon, P.leaf, P.leaf700)

/**
 * Night over land. ONE flat sky.
 *
 * A darker upper region plus a hill silhouette left wedges of mid-blue between
 * the two edges, and at a glance those wedges read as objects. A night sky that
 * is one colour with stars in it, and a dark land, is both simpler and more
 * legible.
 */
const nightBackdrop = (horizon = 640, seed = 11) =>
  rect(0, 0, W, H, P.sky700) +
  starfield(W, horizon - 30, 90, seed) +
  hills(W, horizon - 40, P.ink, P.ink700) +
  ground(W, H, horizon, P.ink700, P.ink500)

/**
 * Deep space. The darker region is a CURVE, not a band.
 *
 * It was `rect(0, 0, W, H/2)`, and a straight dark edge across the middle of a
 * starfield does not read as depth — it reads as a horizon, in a picture with no
 * ground, i.e. as a mistake. Measured on the contact sheet across five space
 * scenes at once, which is the only place it was obvious.
 */
const spaceBackdrop = (seed = 3) =>
  rect(0, 0, W, H, P.sky700) +
  path(`M0 0 L${W} 0 L${W} ${H * 0.34} Q ${W * 0.5} ${H * 0.62} 0 ${H * 0.3} Z`, P.ink700) +
  starfield(W, H, 130, seed)

const forestBackdrop = (horizon = 620, { pines = true } = {}) =>
  rect(0, 0, W, H, P.sky100) +
  rect(0, 0, W, horizon * 0.7, P.leaf100) +
  cloud(300, 130, 1.7, P.card) +
  cloud(940, 170, 1.3, P.card) +
  hills(W, horizon - 70, P.leaf, P.leaf700) +
  ground(W, H, horizon, P.leaf, P.leaf700) +
  (pines
    ? at(120, horizon + 34, 2.5, pine()) +
      at(1080, horizon + 46, 2.9, pine()) +
      at(430, horizon + 10, 1.9, pine()) +
      at(800, horizon + 4, 1.7, pine())
    : '')

/**
 * Cave interior: an ink field with a lit mouth at one EDGE, floor of rock.
 *
 * The mouth was a 5.2x arch near the middle of the frame and read as a pale
 * headstone standing behind the dragon. Pushed to the edge and cut down, it
 * reads as the way out — which is also what every one of these segments is about.
 */
const caveInterior = ({ mouthX = 1140, mouthScale = 3.2 } = {}) =>
  rect(0, 0, W, H, P.ink700) +
  // the daylight beyond the mouth
  at(mouthX, 706, mouthScale, caveMouth(P.sky) + at(0, -4, 0.88, caveMouth(P.sky100))) +
  // ragged ceiling
  path(
    `M0 0 L${W} 0 L${W} 130 Q ${W * 0.82} 235 ${W * 0.66} 150 Q ${W * 0.5} 245 ${W * 0.34} 155 Q ${
      W * 0.18
    } 240 0 150 Z`,
    P.ink,
  ) +
  ground(W, H, 700, P.ink500, P.ink300) +
  at(190, 708, 1.4, rocks(P.ink500, P.ink300))

/* -------------------------------------------------------------------------- */
/* the pool — 30 segment illustrations (4:3) + 3 covers (1:1)                  */
/* -------------------------------------------------------------------------- */

export const SEGMENTS = {
  /* ---- the nine that carry 88.6% of live segments (DEVIATIONS 11.21) ---- */

  'animals/forest-02': {
    tags: ['forest', 'rabbit', 'hiding'],
    draw: () =>
      forestBackdrop(600) +
      at(760, 640, 2.3, tree(P.leaf700, P.leaf)) +
      at(300, 660, 2.7, tree()) +
      // the fern it is hiding under
      at(430, 830, 3.4, fern(P.leaf700, P.leaf100)) +
      at(560, 830, 5.2, rabbit({ pose: 'sitting', hiding: true })) +
      // the fern the rabbit is hiding UNDER, so it overlaps the body
      at(430, 890, 3.8, fern(P.leaf, P.leaf100)) +
      at(880, 880, 3.2, fern(P.leaf700, P.leaf100)) +
      tufts(W, 800, 70, P.leaf700),
  },

  'dragons/cave-01': {
    tags: ['dragon', 'cave', 'sleeping'],
    draw: () =>
      caveInterior({ mouthX: 1150, mouthScale: 3.4 }) +
      at(520, 726, 3.0, treasurePile()) +
      at(500, 690, 5.0, dragon({ pose: 'curled' })) +
      // a few coins spilled in front
      circle(250, 726, 13, P.gold300) +
      circle(300, 736, 10, P.gold) +
      circle(760, 730, 11, P.gold300),
  },

  'space/rocket-01': {
    tags: ['spaceship', 'space', 'stars'],
    draw: () =>
      spaceBackdrop(21) +
      at(230, 250, 1.4, planetBall(46, P.sky, P.sky700)) +
      at(620, 830, 6.4, rocketShip()) +
      star4(950, 240, 26, P.gold300) +
      star4(180, 640, 18, P.gold300) +
      star4(1040, 620, 14, P.gold300),
  },

  'dragons/sky-01': {
    tags: ['dragon', 'flying', 'sky', 'day'],
    draw: () =>
      rect(0, 0, W, H, P.sky100) +
      at(1030, 60, 1.7, sun(30)) +
      cloud(200, 200, 2.6) +
      cloud(900, 300, 2.1) +
      cloud(520, 120, 1.6, P.page) +
      hills(W, 830, P.leaf, P.leaf700) +
      at(560, 660, 4.8, dragon({ pose: 'flying' })) +
      cloud(280, 700, 2.0, P.card),
  },

  'space/spacewalk-01': {
    tags: ['astronaut', 'space', 'flying', 'spaceship'],
    draw: () =>
      spaceBackdrop(41) +
      // the ship the tether runs back to
      at(1080, 300, 1.0, rect(-150, -60, 250, 92, P.shell, 30) + rect(-150, -60, 250, 30, P.card, 18) +
        circle(-70, -14, 22, P.sky700) + circle(-70, -14, 17, P.sky100) +
        circle(10, -14, 22, P.sky700) + circle(10, -14, 17, P.sky100) +
        rect(-30, 32, 60, 26, P.shade, 8)) +
      at(480, 660, 5.8, astronaut({ pose: 'floating' })) +
      at(180, 200, 0.9, planetBall(40, P.leaf, P.leaf700)) +
      star4(900, 700, 20, P.gold300),
  },

  'space/moon-01': {
    tags: ['moon', 'astronaut', 'space'],
    draw: () =>
      spaceBackdrop(7) +
      // moon surface
      path(`M0 660 Q ${W * 0.3} 560 ${W * 0.62} 640 Q ${W * 0.86} 700 ${W} 620 L${W} ${H} L0 ${H} Z`, P.shade) +
      path(`M0 700 Q ${W * 0.32} 610 ${W * 0.64} 686 Q ${W * 0.88} 742 ${W} 668 L${W} ${H} L0 ${H} Z`, P.rule) +
      ellipse(230, 800, 78, 22, P.shade) +
      ellipse(980, 780, 60, 17, P.shade) +
      ellipse(640, 862, 52, 15, P.shade) +
      at(540, 780, 6.0, astronaut({ pose: 'standing' })) +
      at(160, 250, 1.1, planetBall(48, P.sky, P.sky700)) +
      star4(1010, 190, 22, P.gold300),
  },

  'animals/forest-01': {
    tags: ['forest', 'fox', 'day'],
    draw: () =>
      forestBackdrop(590) +
      at(220, 630, 3.0, tree()) +
      at(980, 620, 2.7, tree(P.leaf700, P.leaf)) +
      at(640, 596, 2.0, tree()) +
      tufts(W, 700, 66, P.leaf700) +
      at(540, 840, 5.4, fox({ pose: 'walking' })) +
      at(1090, 880, 2.6, fern(P.leaf700, P.leaf100)),
  },

  'dragons/fire-01': {
    tags: ['dragon', 'fire', 'mountain'],
    draw: () =>
      rect(0, 0, W, H, P.amber100) +
      rect(0, 0, W, 420, P.sky100) +
      cloud(240, 130, 1.7, P.card) +
      at(300, 640, 5.4, mountain(P.ink500, P.shade)) +
      at(880, 660, 4.1, mountain(P.ink300, P.shade)) +
      ground(W, H, 640, P.shade, P.rule) +
      at(1060, 690, 1.4, rocks()) +
      at(420, 810, 5.4, dragon({ pose: 'standing' })) +
      // the flame starts at the mouth, not somewhere near it
      at(700, 560, 3.8, flameBurst()) +
      at(1080, 780, 1.6, rocks(P.ink500, P.ink300)),
  },

  'space/rocket-02': {
    tags: ['spaceship', 'flying', 'sky', 'day'],
    draw: () =>
      rect(0, 0, W, H, P.sky100) +
      at(120, 70, 1.4, sun(30)) +
      cloud(880, 190, 2.2) +
      cloud(320, 300, 1.7) +
      hills(W, 700, P.leaf, P.leaf700) +
      ground(W, H, 760, P.leaf, P.leaf700) +
      tufts(W, 790, 60, P.leaf700) +
      at(600, 660, 5.4, rocketShip()) +
      // exhaust cloud on the field
      cloud(600, 830, 2.6, P.warm) +
      cloud(430, 856, 1.9, P.shell) +
      cloud(790, 850, 1.7, P.shell),
  },

  /* ---- the rest of the pool ------------------------------------------- */

  'dragons/cave-02': {
    tags: ['dragon', 'cave', 'treasure'],
    draw: () =>
      caveInterior({ mouthX: 60, mouthScale: 3.4 }) +
      at(640, 740, 4.2, treasurePile()) +
      at(640, 660, 4.0, dragon({ pose: 'peeking' })) +
      circle(430, 736, 14, P.gold300) +
      circle(480, 748, 10, P.gold) +
      circle(1010, 742, 12, P.gold300),
  },

  'dragons/sky-02': {
    tags: ['dragon', 'flying', 'night', 'moon'],
    draw: () =>
      rect(0, 0, W, H, P.sky700) +
      rect(0, 0, W, 480, P.ink700) +
      starfield(W, H, 90, 5) +
      at(880, 440, 3.8, moonBall(60)) +
      at(520, 620, 4.4, dragon({ pose: 'flying' })) +
      hills(W, 860, P.ink, P.ink700),
  },

  'dragons/castle-01': {
    tags: ['castle', 'knight', 'day'],
    draw: () =>
      dayBackdrop(660) +
      at(700, 700, 4.2, castle()) +
      tufts(W, 700, 70, P.leaf700) +
      at(280, 850, 5.2, knight()) +
      at(1120, 880, 2.4, tree()),
  },

  'dragons/castle-02': {
    tags: ['castle', 'dragon', 'night', 'stars'],
    draw: () =>
      nightBackdrop(700, 29) +
      at(480, 740, 4.4, castle()) +
      at(770, 300, 3.0, dragon({ pose: 'standing' })) +
      at(1050, 240, 2.4, moonBall(34)) +
      starfield(W, 420, 34, 71, { big: 7 }),
  },

  'dragons/meadow-01': {
    tags: ['dragon', 'knight', 'helping', 'meadow'],
    draw: () =>
      dayBackdrop(600) +
      tufts(W, 690, 58, P.leaf700) +
      at(1060, 690, 1.9, tree()) +
      at(150, 700, 1.6, tree(P.leaf700, P.leaf)) +
      at(390, 840, 4.8, knight()) +
      at(800, 830, 3.6, dragon({ pose: 'standing', flip: true })) +
      // three little hearts of help, kept as flowers instead (§06: no text, no icons)
      circle(620, 790, 9, P.berry100) +
      circle(660, 810, 7, P.gold300) +
      tufts(W, 830, 96, P.leaf700, 1.2),
  },

  'dragons/forest-01': {
    tags: ['dragon', 'forest', 'hiding'],
    draw: () =>
      forestBackdrop(610) +
      at(250, 700, 3.4, pine()) +
      at(940, 720, 3.8, pine()) +
      at(620, 800, 4.6, dragon({ pose: 'peeking' })) +
      at(430, 800, 3.6, pine()) +
      at(800, 810, 3.2, pine()) +
      tufts(W, 860, 74, P.leaf700),
  },

  'dragons/path-01': {
    tags: ['knight', 'path', 'lost', 'forest'],
    draw: () => {
      // A fork has to be visibly TWO ways out of ONE. Wide stem at the bottom
      // edge, two arms tapering into the trees, and the knight standing BESIDE
      // the junction rather than on top of it — he was hiding the whole subject.
      const stem = 'M330 900 L700 900 L610 664 L470 664 Z'
      const forkLeft = 'M470 672 L556 672 Q 470 626 300 596 L286 638 Q 430 660 470 672 Z'
      const forkRight = 'M528 672 L612 672 Q 700 624 880 600 L892 642 Q 700 664 606 672 Z'
      return (
        forestBackdrop(560, { pines: false }) +
        at(120, 620, 2.6, pine()) +
        at(1090, 640, 3.0, pine()) +
        at(300, 590, 2.0, pine()) +
        at(890, 596, 2.2, pine()) +
        path(stem, P.shade) +
        path(forkLeft, P.shade) +
        path(forkRight, P.shade) +
        path('M360 900 L668 900 L594 672 L486 672 Z', P.shell) +
        at(880, 860, 4.6, knight()) +
        tufts(W, 700, 64, P.leaf700)
      )
    },
  },

  'space/moon-02': {
    tags: ['moon', 'night', 'sky', 'village'],
    draw: () =>
      rect(0, 0, W, H, P.sky700) +
      rect(0, 0, W, 460, P.ink700) +
      starfield(W, 620, 70, 13) +
      at(300, 400, 3.2, moonBall(56)) +
      hills(W, 700, P.ink, P.ink700) +
      ground(W, H, 720, P.ink700, P.ink500) +
      at(720, 830, 4.2, village()),
  },

  'space/planet-01': {
    tags: ['planet', 'space', 'stars'],
    draw: () =>
      spaceBackdrop(17) +
      at(600, 560, 3.0, planetBall(64)) +
      star4(220, 220, 26, P.gold300) +
      star4(980, 300, 20, P.gold300) +
      star4(300, 760, 16, P.gold300) +
      star4(920, 740, 22, P.gold300) +
      at(160, 560, 0.7, planetBall(40, P.leaf, P.leaf700)),
  },

  'space/planet-02': {
    tags: ['planet', 'astronaut', 'friendly_creature'],
    draw: () =>
      spaceBackdrop(23) +
      path(`M0 640 Q ${W * 0.5} 520 ${W} 640 L${W} ${H} L0 ${H} Z`, P.berry700) +
      path(`M0 690 Q ${W * 0.5} 578 ${W} 690 L${W} ${H} L0 ${H} Z`, P.berry) +
      ellipse(240, 790, 66, 18, P.berry700) +
      ellipse(960, 830, 54, 15, P.berry700) +
      at(410, 840, 5.0, astronaut({ pose: 'standing' })) +
      at(800, 830, 4.6, creature({ body: P.sky, light: P.sky100 })) +
      at(1080, 260, 1.0, planetBall(38, P.amber, P.amber700)),
  },

  'space/stars-01': {
    tags: ['stars', 'night', 'sky'],
    draw: () =>
      // "A wide night sky filled with soft white stars above a dark horizon."
      // The first version had a straight dark band, twelve visible stars and a
      // horizon so low the picture was three empty rectangles.
      rect(0, 0, W, H, P.sky700) +
      path(`M0 0 L${W} 0 L${W} 300 Q ${W * 0.5} 470 0 280 Z`, P.ink700) +
      starfield(W, 700, 220, 99, { big: 7 }) +
      star4(300, 200, 40, P.gold300) +
      star4(300, 200, 20, P.card) +
      star4(880, 310, 30, P.gold300) +
      star4(560, 120, 22, P.gold300) +
      star4(1050, 560, 18, P.gold300) +
      star4(150, 520, 16, P.gold300) +
      // a dark tree-line horizon, so the sky has something to be "above"
      hills(W, 700, P.ink, P.ink700) +
      at(180, 790, 3.2, pine(P.ink, P.ink, P.ink700)) +
      at(430, 830, 2.6, pine(P.ink, P.ink, P.ink700)) +
      at(760, 820, 3.0, pine(P.ink, P.ink, P.ink700)) +
      at(1030, 850, 3.6, pine(P.ink, P.ink, P.ink700)),
  },

  'space/sleep-01': {
    tags: ['astronaut', 'spaceship', 'sleeping'],
    draw: () =>
      // cabin interior — warm and small, the opposite of the space scenes
      rect(0, 0, W, H, P.shell) +
      rect(0, 0, W, 340, P.rule) +
      // ribs of the hull, so the room reads as inside a ship
      rect(120, 0, 24, 340, P.shade, 6) +
      rect(1060, 0, 24, 340, P.shade, 6) +
      // porthole onto space
      circle(300, 250, 150, P.shade) +
      circle(300, 250, 128, P.sky700) +
      at(196, 146, 0.7, starfield(300, 300, 30, 61)) +
      at(360, 210, 1.0, moonBall(30)) +
      circle(300, 250, 128, 'none', `stroke="${P.warm}" stroke-width="12"`) +
      // four bolts round the porthole
      circle(300, 108, 9, P.ink300) +
      circle(300, 392, 9, P.ink300) +
      circle(158, 250, 9, P.ink300) +
      circle(442, 250, 9, P.ink300) +
      // the bunk
      rect(0, 620, W, 280, P.warm) +
      rect(80, 566, 1040, 60, P.rule, 18) +
      rect(80, 566, 1040, 20, P.shade, 10) +
      at(700, 566, 6.4, astronaut({ pose: 'sleeping' })),
  },

  'space/celebrate-01': {
    tags: ['astronaut', 'celebrating', 'found', 'planet'],
    draw: () =>
      spaceBackdrop(37) +
      path(`M0 660 Q ${W * 0.5} 540 ${W} 660 L${W} ${H} L0 ${H} Z`, P.amber700) +
      path(`M0 710 Q ${W * 0.5} 596 ${W} 710 L${W} ${H} L0 ${H} Z`, P.amber) +
      at(620, 830, 4.2, flag()) +
      at(300, 860, 4.6, astronaut({ pose: 'cheering' })) +
      at(920, 860, 4.6, astronaut({ pose: 'cheering' })) +
      star4(200, 240, 24, P.gold300) +
      star4(1000, 200, 20, P.gold300) +
      at(1060, 440, 0.8, planetBall(36, P.sky, P.sky700)),
  },

  'animals/burrow-01': {
    tags: ['rabbit', 'digging', 'meadow'],
    draw: () =>
      dayBackdrop(600) +
      tufts(W, 690, 54, P.leaf700) +
      // the burrow mouth
      ellipse(880, 780, 130, 60, P.ink700) +
      ellipse(880, 762, 114, 48, P.ink) +
      path('M750 792 Q 880 690 1010 792 Q 880 744 750 792 Z', P.ink500) +
      at(470, 812, 5.2, rabbit({ pose: 'digging' })) +
      // thrown earth
      circle(650, 742, 15, P.ink500) +
      circle(690, 762, 11, P.ink300) +
      circle(614, 768, 9, P.ink300) +
      tufts(W, 830, 88, P.leaf700, 1.2),
  },

  'animals/pond-01': {
    tags: ['water', 'fish', 'swimming'],
    draw: () =>
      rect(0, 0, W, H, P.sky100) +
      // bank
      ground(W, H, 200, P.sky, P.sky100) +
      rect(0, 0, W, 200, P.leaf) +
      tufts(W, 196, 62, P.leaf700) +
      // surface line + ripples
      rect(0, 196, W, 12, P.sky100) +
      stroke('M120 270 q 60 -22 120 0 q 60 22 120 0', P.sky100, 7, 'opacity="0.6"') +
      stroke('M700 320 q 60 -22 120 0 q 60 22 120 0', P.sky100, 7, 'opacity="0.5"') +
      at(360, 480, 4.6, fish()) +
      at(830, 660, 3.6, fish({ body: P.gold, light: P.gold300 })) +
      at(540, 830, 3.0, fish({ body: P.amber700, light: P.gold })) +
      // weeds
      stroke('M140 900 q 30 -140 6 -230', P.leaf700, 14) +
      stroke('M1080 900 q -34 -160 -4 -250', P.leaf700, 14),
  },

  'animals/bird-01': {
    tags: ['bird', 'flying', 'sky', 'day'],
    draw: () =>
      rect(0, 0, W, H, P.sky100) +
      at(140, 80, 1.5, sun(30)) +
      cloud(320, 260, 2.4) +
      cloud(900, 180, 1.9) +
      cloud(700, 460, 1.5, P.page) +
      hills(W, 840, P.leaf, P.leaf700) +
      at(560, 600, 6.2, bird({ pose: 'flying' })) +
      at(1010, 700, 1.9, bird({ pose: 'flying' })) +
      at(230, 740, 1.4, bird({ pose: 'flying' })),
  },

  'animals/nest-01': {
    tags: ['bird', 'sleeping', 'forest'],
    draw: () =>
      forestBackdrop(700, { pines: false }) +
      at(1000, 760, 3.4, tree(P.leaf700, P.leaf)) +
      at(180, 780, 2.8, tree()) +
      // the branch
      stroke('M0 470 Q 300 430 640 500', P.ink700, 26) +
      stroke('M420 486 Q 470 420 560 400', P.ink700, 13) +
      at(560, 400, 1.0, litEllipse(0, -18, 40, 22, P.leaf, P.leaf100)) +
      at(330, 500, 3.2, nest()) +
      at(330, 462, 3.2, bird({ pose: 'sleeping' })),
  },

  'animals/cat-01': {
    tags: ['cat', 'sleeping', 'night'],
    draw: () =>
      // room wall
      rect(0, 0, W, H, P.ink700) +
      // window onto the night
      rect(220, 90, 760, 480, P.shade, 18) +
      rect(248, 118, 704, 424, P.sky700) +
      at(248, 118, 1, starfield(704, 424, 46, 43)) +
      at(800, 300, 1.7, moonBall(40)) +
      stroke('M600 118 L600 542', P.shade, 12) +
      stroke('M248 330 L952 330', P.shade, 12) +
      // sill
      rect(150, 566, 900, 54, P.rule, 12) +
      rect(150, 566, 900, 18, P.shade, 8) +
      at(600, 574, 5.0, cat({ pose: 'curled' })),
  },

  'animals/dog-01': {
    tags: ['dog', 'running', 'meadow'],
    draw: () =>
      dayBackdrop(560) +
      // long grass, dog running through it
      ground(W, H, 640, P.leaf, P.leaf700) +
      tufts(W, 660, 44, P.leaf700, 1.3) +
      at(560, 830, 6.0, dog({ pose: 'running' })) +
      tufts(W, 830, 40, P.leaf700, 1.7) +
      tufts(W, 890, 46, P.leaf, 2.0),
  },

  'animals/snow-01': {
    tags: ['fox', 'snow', 'path'],
    draw: () => {
      const flakes = (() => {
        let out = ''
        const pts = [
          [140, 120], [420, 80], [700, 150], [980, 100], [260, 300], [560, 260],
          [860, 320], [1120, 260], [200, 470], [640, 440], [1040, 480], [380, 600],
        ]
        for (const [x, y] of pts) out += circle(x, y, 7, P.card, 'opacity="0.85"')
        return out
      })()
      return (
        rect(0, 0, W, H, P.sky100) +
        rect(0, 0, W, 420, P.rule) +
        at(150, 640, 2.6, pine()) +
        at(1060, 660, 3.0, pine()) +
        // snow on the pines
        ellipse(150, 470, 62, 16, P.card) +
        ellipse(1060, 470, 70, 18, P.card) +
        ground(W, H, 620, P.card, P.page) +
        // the path
        path('M420 900 Q 520 740 660 660 L800 690 Q 640 770 580 900 Z', P.shell) +
        at(600, 840, 5.2, fox({ pose: 'walking' })) +
        // pawprints behind
        circle(360, 840, 9, P.shade) +
        circle(300, 872, 9, P.shade) +
        flakes
      )
    },
  },

  'animals/help-01': {
    tags: ['dog', 'cat', 'helping', 'found'],
    draw: () =>
      dayBackdrop(620) +
      tufts(W, 700, 60, P.leaf700) +
      at(1050, 700, 1.8, tree()) +
      at(360, 850, 5.0, dog({ pose: 'sitting' })) +
      at(790, 840, 4.6, cat({ pose: 'sitting' })) +
      // the moment they meet: two little sparkles between the noses
      star4(576, 600, 20, P.gold300) +
      star4(612, 660, 12, P.gold) +
      tufts(W, 850, 92, P.leaf700, 1.3),
  },
}

export const COVERS = {
  'dragons/cover': {
    tags: ['dragon'],
    draw: () =>
      rect(0, 0, CW, CW, P.page) +
      circle(CW / 2, CW / 2, 330, P.gold300) +
      circle(CW / 2, CW / 2, 300, P.gold) +
      circle(CW / 2, CW / 2, 282, P.leaf100) +
      at(CW / 2 + 26, 560, 4.0, dragon({ pose: 'standing' })) +
      circle(CW / 2, CW / 2, 300, 'none', `stroke="${P.gold700}" stroke-width="8"`),
  },
  'space/cover': {
    tags: ['spaceship'],
    draw: () =>
      rect(0, 0, CW, CW, P.page) +
      circle(CW / 2, CW / 2, 330, P.gold300) +
      circle(CW / 2, CW / 2, 300, P.gold) +
      circle(CW / 2, CW / 2, 282, P.sky700) +
      at(215, 215, 0.46, starfield(CW, CW, 40, 77)) +
      at(CW / 2, 630, 4.4, rocketShip()) +
      circle(CW / 2, CW / 2, 300, 'none', `stroke="${P.gold700}" stroke-width="8"`),
  },
  'animals/cover': {
    tags: ['fox'],
    draw: () =>
      rect(0, 0, CW, CW, P.page) +
      circle(CW / 2, CW / 2, 330, P.gold300) +
      circle(CW / 2, CW / 2, 300, P.gold) +
      circle(CW / 2, CW / 2, 282, P.leaf100) +
      at(CW / 2 + 30, 570, 4.4, fox({ pose: 'sitting' })) +
      circle(CW / 2, CW / 2, 300, 'none', `stroke="${P.gold700}" stroke-width="8"`),
  },
}

/* -------------------------------------------------------------------------- */
/* story-word cutouts (§06: one object, transparency, no scene, no shadow)     */
/* -------------------------------------------------------------------------- */

/**
 * The 14 words `SELECT unnest(story_words)` returns over servable stories.
 * Deliberately NOT every noun in English: a word with no cutout renders as a
 * word with no picture, which is a fine outcome (DEVIATIONS 11.25).
 */
export const CUTOUTS = {
  dragon: () => at(48, 88, 0.78, dragon({ pose: 'standing' })),
  cave: () =>
    at(48, 86, 0.82, caveMouth(P.ink700) + path('M-52 0 Q -56 -66 0 -70 Q 56 -66 52 0 Z', P.ink) +
      at(0, -6, 0.5, rocks(P.ink500, P.ink300))),
  rabbit: () => at(48, 90, 0.8, rabbit({ pose: 'sitting' })),
  fox: () => at(48, 90, 0.8, fox({ pose: 'sitting' })),
  rocket: () => at(48, 92, 0.82, rocketShip()),
  moon: () => at(48, 92, 1.0, moonBall(42)),
  stars: () =>
    star4(30, 32, 22, P.gold300) + star4(68, 58, 16, P.gold) + star4(44, 78, 12, P.gold300),
  forest: () => at(30, 92, 0.72, pine()) + at(66, 92, 0.88, pine()),
  burrow: () =>
    ellipse(48, 70, 42, 22, P.ink700) +
    ellipse(48, 64, 34, 16, P.ink) +
    path('M8 76 Q 48 30 88 76 Q 48 52 8 76 Z', P.leaf) +
    at(48, 78, 0.4, rabbit({ pose: 'sitting' })),
  wings: () => at(48, 62, 0.62, dragonWingPart(false)) + at(48, 62, 0.62, dragonWingPart(true)),
  fire: () => at(48, 88, 1.25, flameBurst()),
  planet: () => at(48, 62, 0.52, planetBall(56)),
  astronaut: () => at(48, 92, 0.82, astronaut({ pose: 'standing' })),
  treasure: () => at(48, 84, 0.72, treasurePile()),
  giraffe: () =>
    // Only story word in the pool with no animal part in the kit. Drawn here.
    stroke('M40 84 L40 58', P.gold, 8) +
    stroke('M58 84 L58 58', P.gold, 8) +
    litEllipse(48, 52, 22, 16, P.gold, P.gold300) +
    stroke('M60 44 L74 20', P.gold, 9) +
    litEllipse(76, 18, 11, 9, P.gold, P.gold300) +
    circle(84, 20, 2, P.ink) +
    stroke('M72 10 L70 4', P.ink700, 2.5) +
    stroke('M80 10 L82 4', P.ink700, 2.5) +
    circle(38, 50, 4, P.amber700) +
    circle(54, 58, 4, P.amber700) +
    circle(46, 46, 3.4, P.amber700),
}

export { doc, cutout, W, H, CW }
