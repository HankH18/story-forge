/**
 * The shared drawing kit for Story Forge's illustration pool (§12, style guide §06).
 *
 * §06's consistency gates are the reason this file exists rather than 33
 * hand-written SVGs:
 *
 *   "Same palette across every theme · same texture · same light direction
 *    (upper left) · no photoreal, no 3D render, no anime, no thick vector-clipart
 *    outlines."
 *
 * Thirty-three independently drawn files drift on exactly those axes. Here there
 * is one `PALETTE`, one `grain()` texture, and one `LIGHT` offset that every
 * rounded form takes its highlight from, so drift is not expressible.
 *
 * Every part is drawn in a local 100x100 box with the origin at the subject's
 * FEET, centred horizontally, and placed with `at(x, y, scale)`. That is what
 * makes a scene a list of placements rather than a wall of coordinates.
 *
 * Hard rules from §06 and §02, enforced by construction rather than by review:
 *   - no gradients, no filters on artwork (the grain is a pattern, see `grain`)
 *   - never pure #000 or #FFF (§02) — the darkest ink here is #2B2118
 *   - nothing frightening: no fangs, no claws, no weapons. The knight carries a
 *     lantern.
 */

/** Style guide §02 / §06. The only colours any drawing may use. */
export const P = {
  page: '#FDF6E7',
  card: '#FFFBF0',
  warm: '#F6EAD2',
  shell: '#F1E4C8',
  rule: '#EADCBE',
  shade: '#E3D3B2',
  ink: '#2B2118',
  ink700: '#4A3A2A',
  ink500: '#6F5B45',
  ink300: '#A08A6E',
  gold300: '#F2D48A',
  gold: '#D8A93C',
  gold700: '#A87A22',
  berry100: '#F8E3DF',
  berry: '#C0453B',
  berry700: '#92302A',
  leaf100: '#E7F0E2',
  leaf: '#4C7C51',
  leaf700: '#35603A',
  sky100: '#E2EDF2',
  sky: '#3D7891',
  sky700: '#2A5A70',
  amber100: '#FBEFD4',
  amber: '#C98A2E',
  amber700: '#96631A',
}

/** §06: "same light direction (upper left)". Highlights are offset by this. */
export const LIGHT = { dx: -3.5, dy: -3.5 }

const n = (v) => Math.round(v * 100) / 100

/* -------------------------------------------------------------------------- */
/* primitives                                                                 */
/* -------------------------------------------------------------------------- */

export const g = (attrs, ...kids) => `<g ${attrs}>${kids.join('')}</g>`

/** Place a part drawn in local 100x100 (origin at its feet, x centred). */
export const at = (x, y, s, body) =>
  `<g transform="translate(${n(x)} ${n(y)}) scale(${n(s)})">${body}</g>`

export const circle = (cx, cy, r, fill, extra = '') =>
  `<circle cx="${n(cx)}" cy="${n(cy)}" r="${n(r)}" fill="${fill}"${extra ? ' ' + extra : ''}/>`

export const ellipse = (cx, cy, rx, ry, fill, extra = '') =>
  `<ellipse cx="${n(cx)}" cy="${n(cy)}" rx="${n(rx)}" ry="${n(ry)}" fill="${fill}"${
    extra ? ' ' + extra : ''
  }/>`

export const rect = (x, y, w, h, fill, r = 0, extra = '') =>
  `<rect x="${n(x)}" y="${n(y)}" width="${n(w)}" height="${n(h)}"${
    r ? ` rx="${n(r)}"` : ''
  } fill="${fill}"${extra ? ' ' + extra : ''}/>`

export const path = (d, fill, extra = '') =>
  `<path d="${d}" fill="${fill}"${extra ? ' ' + extra : ''}/>`

export const stroke = (d, color, w, extra = '') =>
  `<path d="${d}" fill="none" stroke="${color}" stroke-width="${n(
    w,
  )}" stroke-linecap="round" stroke-linejoin="round"${extra ? ' ' + extra : ''}/>`

/**
 * A rounded body with its §06 upper-left highlight.
 *
 * The highlight is a second FLAT shape, not a gradient — §06 forbids gradients
 * and the flat-gouache look comes from exactly this: two tones, hard edge.
 */
export const lit = (shape, base, light) =>
  shape(base, 0, 0) + shape(light, LIGHT.dx, LIGHT.dy)

/**
 * `o` defaults to 0.45 and is the difference between "flat gouache" and "white
 * belly". At full opacity every `leaf`/`amber` body took the `*-100` tone over
 * most of its area and the dragon read as a white seal — measured on a contact
 * sheet of all 33, which is the only way that kind of error is visible.
 */
export const litEllipse = (cx, cy, rx, ry, base, light, o = 0.45) =>
  ellipse(cx, cy, rx, ry, base) +
  ellipse(cx + LIGHT.dx, cy + LIGHT.dy, rx * 0.78, ry * 0.72, light, `opacity="${o}"`)

/** A gentle closed smile. Never a mouth full of teeth (§06). */
export const smile = (cx, cy, w, color = P.ink700, sw = 2.2) =>
  stroke(`M${n(cx - w / 2)} ${n(cy)} q ${n(w / 2)} ${n(w * 0.42)} ${n(w)} 0`, color, sw)

/** Eyes are dots. Two dots and a smile is the whole face vocabulary. */
export const eyes = (cx, cy, gap, r = 2.6, color = P.ink) =>
  circle(cx - gap / 2, cy, r, color) +
  circle(cx + gap / 2, cy, r, color) +
  // one cream catchlight each, upper-left, so the face reads as alive
  circle(cx - gap / 2 - r * 0.32, cy - r * 0.34, r * 0.3, P.card) +
  circle(cx + gap / 2 - r * 0.32, cy - r * 0.34, r * 0.3, P.card)

export const closedEyes = (cx, cy, gap, w = 5, color = P.ink700) =>
  stroke(`M${n(cx - gap / 2 - w / 2)} ${n(cy)} q ${n(w / 2)} ${n(w * 0.5)} ${n(w)} 0`, color, 2) +
  stroke(`M${n(cx + gap / 2 - w / 2)} ${n(cy)} q ${n(w / 2)} ${n(w * 0.5)} ${n(w)} 0`, color, 2)

/* -------------------------------------------------------------------------- */
/* canvas                                                                     */
/* -------------------------------------------------------------------------- */

/**
 * §06 wants "visible paper grain". This is a tiled PATTERN, not an SVG filter.
 *
 * `feTurbulence` is the obvious way to make grain and it is the wrong one here:
 * it is a per-pixel convolution the browser runs on every paint, on a 1200x900
 * surface, on the tablet a five-year-old is holding. A 16px pattern of four
 * short ink ticks costs one tile rasterisation and tiles for free.
 */
export const GRAIN_DEFS = `<pattern id="grain" width="17" height="17" patternUnits="userSpaceOnUse">
  <circle cx="3" cy="4" r="0.9" fill="${P.ink}" opacity="0.055"/>
  <circle cx="11.5" cy="2.5" r="0.7" fill="${P.ink}" opacity="0.04"/>
  <circle cx="7" cy="11" r="1" fill="${P.ink}" opacity="0.05"/>
  <circle cx="14" cy="13.5" r="0.65" fill="${P.ink}" opacity="0.035"/>
</pattern>`

/** The grain layer. Painted last so it sits over every shape, like real tooth. */
export const grain = (w, h) => rect(0, 0, w, h, 'url(#grain)')

/**
 * One SVG document.
 *
 * `preserveAspectRatio` is left at the default and the frame in the UI is fixed
 * at the same ratio, so `object-cover` never crops anything that matters.
 * `role="img"` is deliberately absent: these are referenced from `<img>` tags
 * that carry the asset row's mandatory `alt_text` (§06), and a nested role would
 * fight it.
 */
export const doc = (w, h, body) =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">` +
  `<defs>${GRAIN_DEFS}</defs>` +
  body +
  grain(w, h) +
  '</svg>'

/** A cutout: transparent, one object, no scene, no shadow (§06). */
export const cutout = (body) =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" width="96" height="96">` +
  body +
  '</svg>'

/* -------------------------------------------------------------------------- */
/* backdrops                                                                  */
/* -------------------------------------------------------------------------- */

export const cloud = (x, y, s, fill = P.card) =>
  at(x, y, s, ellipse(0, -9, 26, 11, fill) + ellipse(-14, -6, 14, 8, fill) + ellipse(15, -5, 12, 7, fill))

export const skyBand = (w, h, fill) => rect(0, 0, w, h, fill)

/**
 * Rolling hills: two flat bands, darker behind, both monotone arcs.
 *
 * The first version chained two quadratics per band and the join produced a
 * visible notch — a dark green wedge in the middle of the frame that reads as a
 * drawing mistake rather than a hill. One arc per band cannot notch.
 */
export const hills = (w, y, near, far) =>
  path(`M0 ${n(y + 34)} Q ${n(w * 0.5)} ${n(y - 62)} ${w} ${n(y + 22)} L${w} ${n(y + 400)} L0 ${n(y + 400)} Z`, far) +
  path(`M0 ${n(y + 76)} Q ${n(w * 0.62)} ${n(y - 6)} ${w} ${n(y + 66)} L${w} ${n(y + 400)} L0 ${n(y + 400)} Z`, near)

export const ground = (w, h, y, fill, top = null) =>
  rect(0, y, w, h - y, fill) + (top ? rect(0, y, w, 10, top) : '')

/** Grass tufts along a ground line. */
export const tufts = (w, y, step, color, s = 1) => {
  let out = ''
  for (let x = step * 0.4; x < w; x += step) {
    const j = ((x * 7919) % 13) - 6
    out += stroke(
      `M${n(x)} ${n(y + 4)} q ${n(3 * s)} ${n(-13 * s)} ${n(6 * s + j * 0.2)} ${n(-2 * s)}`,
      color,
      3 * s,
    )
  }
  return out
}

/** Deterministic pseudo-random, so the same scene always draws the same stars. */
export const rng = (seed) => {
  let t = seed >>> 0
  return () => {
    t = (t * 1664525 + 1013904223) >>> 0
    return t / 4294967296
  }
}

export const star4 = (cx, cy, r, fill) =>
  path(
    `M${n(cx)} ${n(cy - r)} Q ${n(cx + r * 0.18)} ${n(cy - r * 0.18)} ${n(cx + r)} ${n(cy)} ` +
      `Q ${n(cx + r * 0.18)} ${n(cy + r * 0.18)} ${n(cx)} ${n(cy + r)} ` +
      `Q ${n(cx - r * 0.18)} ${n(cy + r * 0.18)} ${n(cx - r)} ${n(cy)} ` +
      `Q ${n(cx - r * 0.18)} ${n(cy - r * 0.18)} ${n(cx)} ${n(cy - r)} Z`,
    fill,
  )

export const starfield = (w, h, count, seed, { fill = P.card, big = 4, maxY = null } = {}) => {
  const r = rng(seed)
  let out = ''
  for (let i = 0; i < count; i++) {
    const x = r() * w
    const y = r() * (maxY ?? h)
    const k = r()
    if (k > 0.82) out += star4(x, y, big + r() * 2, P.gold300)
    else out += circle(x, y, 1.4 + r() * 1.8, fill, `opacity="${n(0.55 + r() * 0.45)}"`)
  }
  return out
}

/* -------------------------------------------------------------------------- */
/* props — all drawn in local 100x100, origin at the base                     */
/* -------------------------------------------------------------------------- */

export const tree = (leafBase = P.leaf, leafLight = P.leaf100) =>
  rect(-6, -46, 12, 48, P.ink700, 4) +
  litEllipse(0, -62, 30, 26, leafBase, leafLight) +
  ellipse(-19, -46, 17, 13, leafBase) +
  ellipse(20, -44, 15, 12, leafBase)

export const pine = (base = P.leaf, dark = P.leaf700, light = P.leaf100) =>
  rect(-5, -34, 10, 36, P.ink700, 3) +
  path('M0 -96 L26 -44 L-26 -44 Z', dark) +
  path('M0 -78 L31 -22 L-31 -22 Z', base) +
  path('M-8 -80 L4 -50 L-22 -50 Z', light, 'opacity="0.35"')

export const fern = (color = P.leaf, light = P.leaf100) => {
  let out = ''
  for (const [a, len] of [
    [-72, 52],
    [-40, 64],
    [-8, 70],
    [24, 62],
    [56, 50],
  ]) {
    const rad = (a * Math.PI) / 180
    const ex = Math.sin(rad) * len
    const ey = -Math.cos(rad) * len
    out += stroke(`M0 0 Q ${n(ex * 0.35)} ${n(ey * 0.8)} ${n(ex)} ${n(ey)}`, color, 9)
    out += stroke(`M0 0 Q ${n(ex * 0.35)} ${n(ey * 0.8)} ${n(ex)} ${n(ey)}`, light, 3, 'opacity="0.4"')
  }
  return out
}

export const mountain = (fill = P.ink500, light = P.shade) =>
  path('M0 -100 L58 0 L-58 0 Z', fill) +
  path('M0 -100 L14 -76 L-24 0 L-58 0 Z', light, 'opacity="0.55"') +
  path('M0 -100 L18 -66 L-16 -66 Z', P.card, 'opacity="0.75"')

export const caveMouth = (fill = P.ink) =>
  path('M-52 0 Q -56 -66 0 -70 Q 56 -66 52 0 Z', fill)

export const rocks = (color = P.ink500, light = P.shade) =>
  litEllipse(-22, -8, 20, 12, color, light) +
  litEllipse(16, -6, 15, 9, color, light) +
  litEllipse(0, -3, 12, 7, color, light)

export const castle = () =>
  // keep
  rect(-40, -84, 80, 84, P.shade, 3) +
  rect(-40, -84, 42, 84, P.rule, 3) +
  // towers
  rect(-62, -104, 26, 104, P.shade, 3) +
  rect(-62, -104, 13, 104, P.rule, 3) +
  rect(36, -104, 26, 104, P.shade, 3) +
  rect(36, -104, 13, 104, P.rule, 3) +
  // crenellations
  [-62, -49, -40, -18, 4, 26, 36, 49].map((x) => rect(x, -114, 13, 12, P.shade, 2)).join('') +
  // gate
  path('M-15 0 Q -15 -34 0 -34 Q 15 -34 15 0 Z', P.ink700) +
  rect(-13, -30, 26, 30, P.gold700, 3) +
  stroke('M0 -30 L0 0', P.ink700, 2) +
  // banner
  rect(48, -142, 3, 40, P.ink700) +
  path('M51 -140 L78 -132 L51 -124 Z', P.berry)

export const house = (roof = P.berry, wall = P.card) =>
  rect(-26, -40, 52, 40, wall, 3) +
  path('M-32 -38 L0 -66 L32 -38 Z', roof) +
  rect(-9, -26, 18, 26, P.ink700, 2) +
  rect(11, -32, 11, 11, P.gold300, 2)

export const village = () =>
  at(-52, 0, 0.72, house(P.berry700)) +
  at(0, 0, 0.92, house(P.berry)) +
  at(54, 0, 0.66, house(P.amber700)) +
  at(24, 0, 0.5, house(P.sky700))

export const moonBall = (r = 40) =>
  circle(0, -r, r, P.gold300) +
  circle(-r * 0.22, -r * 1.2, r * 0.72, P.card, 'opacity="0.55"') +
  circle(r * 0.3, -r * 0.7, r * 0.16, P.shade, 'opacity="0.8"') +
  circle(-r * 0.35, -r * 1.35, r * 0.11, P.shade, 'opacity="0.7"') +
  circle(r * 0.1, -r * 1.5, r * 0.09, P.shade, 'opacity="0.6"')

export const sun = (r = 34) =>
  circle(0, -r, r, P.gold) + circle(-r * 0.2, -r * 1.2, r * 0.7, P.gold300)

export const planetBall = (r = 60, body = P.amber, band = P.amber700) =>
  circle(0, -r, r, body) +
  circle(-r * 0.24, -r * 1.24, r * 0.66, P.gold300, 'opacity="0.45"') +
  path(
    `M${n(-r)} ${n(-r + r * 0.28)} a ${n(r)} ${n(r)} 0 0 0 ${n(2 * r)} 0 Z`,
    band,
    'opacity="0.35"',
  ) +
  ellipse(0, -r, r * 1.62, r * 0.3, 'none', `stroke="${P.gold300}" stroke-width="${n(r * 0.16)}"`) +
  ellipse(0, -r, r * 1.62, r * 0.3, 'none', `stroke="${P.gold}" stroke-width="${n(r * 0.06)}"`)

export const rocketShip = ({ flame = true } = {}) =>
  // fins first so the body overlaps them
  path('M-16 -22 Q -40 -14 -34 6 L-14 -2 Z', P.berry700) +
  path('M16 -22 Q 40 -14 34 6 L14 -2 Z', P.berry700) +
  path('M0 -108 Q 26 -74 26 -22 L-26 -22 Q -26 -74 0 -108 Z', P.card) +
  path('M0 -108 Q -12 -80 -14 -22 L-26 -22 Q -26 -74 0 -108 Z', P.shell) +
  path('M0 -108 Q 15 -92 19 -74 L-19 -74 Q -15 -92 0 -108 Z', P.berry) +
  circle(0, -56, 13, P.sky700) +
  circle(0, -56, 10, P.sky100) +
  circle(-3.5, -59.5, 5, P.card) +
  rect(-26, -26, 52, 8, P.gold, 3) +
  (flame
    ? path('M-13 -18 Q 0 26 13 -18 Z', P.gold) + path('M-7 -18 Q 0 12 7 -18 Z', P.berry)
    : '')

/**
 * Fire, as three pointed tongues rather than one blob.
 *
 * The first version was a rounded amoeba in gold with a berry amoeba inside it
 * and read as a red splat, not as flame. Points are what say "fire" at thumbnail
 * size, and the direction of the points is what says which way it is blowing.
 */
export const flameBurst = (s = 1) =>
  at(
    0,
    0,
    s,
    path('M-6 -2 Q -30 -10 -18 -26 Q -34 -30 -22 -44 Q -8 -40 -2 -52 Q 6 -34 22 -44 Q 22 -26 40 -30 Q 30 -14 48 -12 Q 26 4 -6 -2 Z', P.gold) +
      path('M-2 -6 Q -18 -12 -10 -24 Q -20 -28 -12 -38 Q -2 -34 2 -44 Q 8 -30 20 -38 Q 20 -24 32 -26 Q 24 -14 36 -12 Q 18 -2 -2 -6 Z', P.berry) +
      path('M2 -8 Q -8 -12 -2 -22 Q 2 -30 6 -36 Q 10 -26 18 -32 Q 17 -20 24 -20 Q 16 -10 2 -8 Z', P.gold300),
  )

export const treasurePile = () =>
  path('M-56 0 Q -34 -30 0 -30 Q 34 -30 56 0 Z', P.gold) +
  path('M-56 0 Q -34 -30 0 -30 Q 8 -30 10 0 Z', P.gold300) +
  circle(-30, -12, 8, P.gold300) +
  circle(-30, -12, 4.5, P.gold700, 'opacity="0.5"') +
  circle(28, -10, 7, P.gold300) +
  circle(28, -10, 4, P.gold700, 'opacity="0.5"') +
  // a cup
  path('M-6 -30 L-6 -48 L14 -48 L14 -30 Z', P.gold300) +
  path('M14 -44 q 12 4 0 12', 'none', `stroke="${P.gold700}" stroke-width="3"`) +
  rect(-11, -30, 30, 5, P.gold700, 2)

export const nest = () =>
  ellipse(0, -12, 46, 18, P.ink500) +
  ellipse(0, -16, 40, 13, P.ink300) +
  ellipse(0, -14, 30, 8, P.ink700, 'opacity="0.35"') +
  stroke('M-44 -14 q 20 -10 44 -6', P.shade, 3) +
  stroke('M-30 -22 q 24 -8 50 0', P.shade, 3)

export const flag = () =>
  rect(-2, -96, 4, 96, P.card) +
  path('M2 -94 L46 -80 L2 -66 Z', P.berry) +
  ellipse(0, 0, 16, 5, P.shade)

export const lantern = () =>
  stroke('M0 0 L0 -8', P.ink700, 2) +
  path('M-9 -8 L9 -8 L7 -26 L-7 -26 Z', P.gold300) +
  rect(-9, -30, 18, 5, P.ink700, 2) +
  stroke('M0 -30 q 0 -10 8 -10', P.ink700, 2)

/* -------------------------------------------------------------------------- */
/* characters — local 100x100, origin at the feet, x centred                   */
/* -------------------------------------------------------------------------- */

const dragonWing = (flip = false) =>
  g(
    `transform="${flip ? 'scale(-1 1)' : ''}"`,
    path('M6 -52 Q 44 -92 62 -66 Q 54 -60 58 -50 Q 44 -46 40 -34 Q 24 -34 6 -52 Z', P.leaf700) +
      path('M8 -52 Q 40 -84 56 -66 Q 40 -60 34 -40 Q 20 -40 8 -52 Z', P.leaf100, 'opacity="0.42"'),
  )

const dragonHead = (x, y, s, { asleep = false, flip = false } = {}) =>
  at(
    x,
    y,
    s,
    g(
      `transform="${flip ? 'scale(-1 1)' : ''}"`,
      // horns, rounded — §06: nothing frightening
      path('M-10 -30 Q -16 -46 -2 -42 Z', P.gold300) +
        path('M8 -32 Q 6 -48 18 -40 Z', P.gold300) +
        litEllipse(0, -16, 24, 20, P.leaf, P.leaf100) +
        // snout
        litEllipse(19, -8, 14, 10, P.leaf, P.leaf100) +
        ellipse(25, -9, 2, 1.6, P.ink700) +
        (asleep ? closedEyes(2, -20, 15, 6) : eyes(2, -21, 15, 3)) +
        smile(14, -3, 12, P.ink700, 2) +
        // cheek
        circle(-9, -10, 5, P.berry100, 'opacity="0.75"'),
    ),
  )

export const dragon = ({ pose = 'standing', asleep = false, flip = false } = {}) => {
  if (pose === 'curled') {
    return (
      // the tail comes round the front of the body and ends in front of the nose,
      // which is what makes the pose read as "curled up" rather than "lying down"
      stroke('M-42 -22 Q -74 -14 -60 6 Q -34 20 24 8', P.leaf, 15) +
      path('M22 8 Q 34 0 46 8 Q 34 16 22 8 Z', P.gold300) +
      litEllipse(0, -30, 52, 29, P.leaf, P.leaf100) +
      // spine ridge along the curve of the back
      path('M-30 -56 q 9 -9 16 0 q 9 -10 17 0 q 9 -9 16 1 Z', P.gold300) +
      at(-14, -38, 0.7, dragonWing(false)) +
      dragonHead(34, -26, 1.05, { asleep: true, flip: false })
    )
  }
  if (pose === 'flying') {
    return (
      at(-4, -34, 1.05, dragonWing(false)) +
      at(4, -34, 1.05, dragonWing(true)) +
      stroke('M-36 -30 Q -66 -26 -74 -44', P.leaf, 11) +
      path('M-76 -46 Q -68 -52 -60 -44 Q -68 -38 -76 -46 Z', P.gold300) +
      litEllipse(0, -32, 42, 20, P.leaf, P.leaf100) +
      // little legs tucked
      stroke('M-8 -14 q 2 8 10 8', P.leaf700, 8) +
      stroke('M12 -14 q 2 8 10 8', P.leaf700, 8) +
      dragonHead(34, -34, 0.9, { asleep, flip: false })
    )
  }
  if (pose === 'peeking') {
    return dragonHead(0, -6, 1.25, { asleep: false }) + at(-30, -30, 0.55, dragonWing(false))
  }
  // standing
  return (
    stroke('M-30 -34 Q -60 -30 -58 -6', P.leaf, 12) +
    path('M-60 -8 Q -52 -14 -44 -6 Q -52 0 -60 -8 Z', P.gold300) +
    at(-6, -40, 0.8, dragonWing(false)) +
    litEllipse(0, -38, 38, 24, P.leaf, P.leaf100) +
    // spine ridge
    path('M-16 -60 q 8 -8 14 0 q 8 -8 14 0 Z', P.gold300) +
    rect(-22, -16, 12, 16, P.leaf700, 5) +
    rect(8, -16, 12, 16, P.leaf700, 5) +
    ellipse(-16, -1, 9, 4, P.gold700) +
    ellipse(14, -1, 9, 4, P.gold700) +
    dragonHead(28, -50, 1, { asleep, flip })
  )
}

export const rabbit = ({ pose = 'sitting', hiding = false } = {}) => {
  const ears =
    path('M-13 -56 Q -22 -96 -8 -94 Q -2 -78 -6 -56 Z', P.ink300) +
    path('M-12 -58 Q -18 -88 -9 -88 Q -5 -76 -7 -58 Z', P.berry100) +
    path('M9 -56 Q 4 -96 18 -92 Q 20 -76 15 -56 Z', P.ink300) +
    path('M9 -58 Q 6 -86 15 -86 Q 16 -74 13 -58 Z', P.berry100)
  const head =
    litEllipse(2, -46, 21, 19, P.ink300, P.shade) +
    eyes(2, -49, 15, 2.8) +
    circle(2, -40, 2.6, P.berry700) +
    smile(2, -36, 9, P.ink700, 1.8) +
    stroke('M-6 -38 L-20 -42', P.ink500, 1.4) +
    stroke('M10 -38 L24 -42', P.ink500, 1.4)
  if (pose === 'digging') {
    // Leaning forward with both forepaws in a mound of earth. The head-down
    // version read as a brown lump with two ears floating above it, which is
    // what happens when the only cue for a pose is a rotation.
    return (
      // the earth it has thrown up
      ellipse(30, -8, 30, 12, P.ink500) +
      ellipse(24, -16, 18, 8, P.ink700) +
      litEllipse(-8, -26, 27, 26, P.ink300, P.shade) +
      circle(-30, -24, 9, P.card) +
      // forepaws reaching into the mound
      stroke('M8 -30 Q 22 -22 26 -12', P.ink300, 9) +
      stroke('M14 -34 Q 30 -26 34 -14', P.shade, 9) +
      ears +
      head
    )
  }
  return (
    litEllipse(0, -24, 26, 24, P.ink300, P.shade) +
    ellipse(-22, -6, 12, 7, P.ink300) +
    ellipse(20, -6, 12, 7, P.ink300) +
    circle(-24, -22, 8, P.card) +
    ears +
    head +
    (hiding ? '' : '')
  )
}

export const fox = ({ pose = 'walking' } = {}) => {
  const tail =
    path('M-24 -30 Q -62 -34 -60 -6 Q -44 -6 -34 -18 Z', P.amber) +
    path('M-60 -6 Q -68 -16 -58 -22 Q -52 -14 -60 -6 Z', P.card)
  const head =
    path('M14 -62 L8 -82 L26 -71 Z', P.amber700) +
    path('M40 -62 L46 -82 L28 -71 Z', P.amber700) +
    path('M16 -64 L12 -76 L23 -70 Z', P.berry100) +
    path('M38 -64 L42 -76 L31 -70 Z', P.berry100) +
    litEllipse(27, -54, 20, 17, P.amber, P.gold300) +
    // muzzle: short and blunt, so it does not read as a white sliver
    path('M28 -48 Q 42 -46 43 -37 Q 32 -33 28 -40 Z', P.card) +
    ellipse(42, -38, 3.4, 2.6, P.ink) +
    eyes(25, -57, 15, 2.8) +
    smile(35, -38, 7, P.ink700, 1.6)
  if (pose === 'sitting') {
    // The tail wraps round the FRONT of the paws — the shape everyone draws a
    // sitting fox with, and the thing that stops the body reading as an egg.
    return (
      litEllipse(0, -30, 27, 30, P.amber, P.gold300) +
      // pale chest, drawn as a proper bib rather than a sliver
      path('M-2 -40 Q 14 -30 16 -8 Q 0 -4 -10 -12 Q -8 -30 -2 -40 Z', P.card, 'opacity="0.85"') +
      stroke('M-20 -32 Q -54 -30 -50 -6 Q -30 6 8 -3', P.amber, 15) +
      path('M6 -4 Q 18 -11 28 -3 Q 18 5 6 -4 Z', P.card) +
      at(-6, 0, 1, head)
    )
  }
  // WALKING. The head used to sit up and to the right of the body with nothing
  // between them, so at 5x the fox read as two separate animals. A neck wedge
  // under the head, drawn before it, closes the silhouette.
  return (
    tail +
    // legs are strokes with a bend, not posts — a post reads as furniture
    stroke('M-16 -30 Q -20 -16 -14 -2', P.amber700, 8) +
    stroke('M-2 -28 Q 4 -14 -2 -2', P.amber700, 8) +
    stroke('M16 -30 Q 12 -16 18 -2', P.amber700, 8) +
    litEllipse(0, -34, 33, 16, P.amber, P.gold300) +
    // pale underside
    path('M-24 -26 Q 0 -16 26 -26 Q 0 -20 -24 -26 Z', P.card, 'opacity="0.8"') +
    // neck wedge, drawn before the head so the silhouette closes
    path('M6 -44 Q 26 -52 32 -32 Q 18 -26 4 -32 Z', P.amber) +
    at(-2, -8, 0.94, head)
  )
}

export const cat = ({ pose = 'curled' } = {}) => {
  const head =
    path('M-16 -22 L-20 -40 L-4 -30 Z', P.ink500) +
    path('M16 -22 L20 -40 L4 -30 Z', P.ink500) +
    litEllipse(0, -16, 19, 16, P.ink500, P.ink300) +
    eyes(0, -19, 14, 2.8) +
    circle(0, -11, 2.2, P.berry700) +
    smile(0, -8, 8, P.ink700, 1.6) +
    stroke('M-8 -10 L-24 -14', P.ink700, 1.3) +
    stroke('M8 -10 L24 -14', P.ink700, 1.3)
  if (pose === 'curled') {
    return (
      litEllipse(0, -20, 40, 20, P.ink500, P.ink300) +
      stroke('M32 -16 Q 52 -20 44 -34', P.ink500, 9) +
      // tabby stripes
      stroke('M-14 -34 q 6 6 0 12', P.ink700, 3, 'opacity="0.5"') +
      stroke('M0 -37 q 6 7 0 14', P.ink700, 3, 'opacity="0.5"') +
      stroke('M14 -35 q 6 6 0 12', P.ink700, 3, 'opacity="0.5"') +
      at(-20, -18, 0.9, path('M-16 -22 L-20 -40 L-4 -30 Z', P.ink500) +
        path('M16 -22 L20 -40 L4 -30 Z', P.ink500) +
        litEllipse(0, -16, 19, 16, P.ink500, P.ink300) +
        closedEyes(0, -19, 14, 6) +
        circle(0, -11, 2.2, P.berry700) +
        smile(0, -8, 8, P.ink700, 1.6))
    )
  }
  return (
    litEllipse(0, -26, 24, 24, P.ink500, P.ink300) +
    stroke('M20 -18 Q 42 -26 34 -46', P.ink500, 9) +
    ellipse(-12, -4, 10, 5, P.ink300) +
    ellipse(12, -4, 10, 5, P.ink300) +
    at(0, -44, 1, head)
  )
}

export const dog = ({ pose = 'running' } = {}) => {
  const head =
    path('M-18 -20 Q -30 -6 -18 -2 Q -10 -10 -10 -18 Z', P.ink700) +
    litEllipse(2, -18, 20, 17, P.gold700, P.gold) +
    path('M20 -20 Q 34 -18 34 -8 Q 22 -6 18 -12 Z', P.card) +
    ellipse(33, -10, 3, 2.4, P.ink) +
    eyes(2, -22, 14, 2.8) +
    smile(24, -8, 8, P.ink700, 1.6) +
    path('M20 -22 Q 30 -22 30 -14 Z', P.berry100)
  if (pose === 'sitting') {
    return (
      litEllipse(0, -26, 24, 25, P.gold700, P.gold) +
      stroke('M-20 -20 Q -40 -26 -36 -44', P.gold700, 9) +
      ellipse(12, -5, 12, 6, P.card) +
      at(4, -44, 1, head)
    )
  }
  return (
    stroke('M-28 -34 Q -50 -44 -46 -60', P.gold700, 10) +
    litEllipse(0, -34, 34, 19, P.gold700, P.gold) +
    stroke('M-18 -20 Q -30 -8 -20 0', P.gold700, 9) +
    stroke('M-6 -20 Q -2 -6 -14 -2', P.gold700, 9) +
    stroke('M14 -20 Q 26 -10 18 0', P.gold700, 9) +
    stroke('M20 -20 Q 30 -8 42 -6', P.gold700, 9) +
    at(24, -34, 1, head)
  )
}

export const bird = ({ pose = 'flying', body = P.sky, light = P.sky100 } = {}) => {
  if (pose === 'sleeping') {
    return (
      // tail
      path('M-18 -26 Q -44 -30 -46 -14 Q -28 -12 -16 -18 Z', body) +
      litEllipse(0, -22, 26, 19, body, light) +
      // folded wing, drawn as one flat shape over the body
      path('M-6 -30 Q -26 -28 -24 -12 Q -8 -12 -2 -20 Z', P.sky700) +
      litEllipse(20, -38, 15, 14, body, light) +
      closedEyes(20, -40, 11, 5) +
      path('M33 -38 L47 -34 L33 -30 Z', P.gold) +
      circle(13, -31, 4, P.berry100, 'opacity="0.7"')
    )
  }
  // FLYING. The first version put two dark wing shapes BEHIND a pale body and
  // read as a torpedo. One wing up, one wing down, both in front of the body,
  // with the body's silhouette closed underneath — that is a bird at thumbnail
  // size (§06).
  return (
    // tail
    path('M-24 -34 Q -54 -40 -56 -22 Q -36 -20 -20 -26 Z', P.sky700) +
    litEllipse(0, -32, 30, 18, body, light) +
    // far wing, down and behind
    path('M-2 -34 Q 18 -6 44 -14 Q 26 -34 4 -40 Z', P.sky700) +
    // near wing, up
    path('M-2 -36 Q -6 -78 26 -80 Q 22 -54 8 -40 Z', body) +
    path('M0 -40 Q -2 -70 20 -74 Q 16 -54 6 -44 Z', light, 'opacity="0.45"') +
    litEllipse(24, -42, 15, 13, body, light) +
    eyes(26, -44, 10, 2.6) +
    path('M37 -42 L52 -38 L37 -34 Z', P.gold)
  )
}

export const fish = ({ body = P.amber, light = P.gold300 } = {}) =>
  path('M-26 -14 Q -44 -30 -46 -14 Q -44 2 -26 -14 Z', body) +
  litEllipse(0, -14, 28, 16, body, light) +
  path('M-6 -30 Q 4 -40 12 -28 Z', body) +
  circle(16, -18, 3.2, P.ink) +
  circle(15, -19, 1.1, P.card) +
  smile(20, -11, 6, P.ink700, 1.4)

export const knight = () =>
  // cloak
  path('M-20 0 Q -26 -46 0 -50 Q 26 -46 20 0 Z', P.sky700) +
  path('M-20 0 Q -26 -46 -2 -50 Q -4 -30 -8 0 Z', P.sky) +
  // arms
  stroke('M-16 -40 Q -30 -30 -28 -16', P.shade, 9) +
  stroke('M16 -40 Q 30 -32 30 -20', P.shade, 9) +
  // legs + boots
  rect(-13, -14, 10, 14, P.ink700, 4) +
  rect(3, -14, 10, 14, P.ink700, 4) +
  ellipse(-8, -1, 9, 4, P.ink) +
  ellipse(8, -1, 9, 4, P.ink) +
  // tabard
  rect(-11, -46, 22, 22, P.card, 2) +
  path('M0 -44 L5 -34 L0 -26 L-5 -34 Z', P.berry) +
  // helm — open visor, friendly face
  litEllipse(0, -60, 17, 16, P.shade, P.rule) +
  path('M-13 -62 Q 0 -50 13 -62 L13 -56 Q 0 -46 -13 -56 Z', P.warm) +
  eyes(0, -62, 13, 2.6) +
  smile(0, -55, 8, P.ink700, 1.6) +
  path('M-4 -76 Q 0 -88 6 -76 Z', P.berry) +
  at(30, -18, 0.9, lantern())

export const astronaut = ({ pose = 'standing', flip = false } = {}) => {
  const suit = P.card
  const trim = P.sky
  const helmet =
    circle(0, -66, 21, P.shell) +
    circle(0, -66, 17, P.sky700) +
    path('M-15 -70 Q -6 -82 8 -78 Q -4 -74 -8 -62 Z', P.sky100, 'opacity="0.7"') +
    eyes(0, -68, 13, 2.6, P.card) +
    smile(0, -60, 9, P.card, 1.8)
  const body =
    rect(-19, -50, 38, 34, suit, 10) +
    rect(-19, -50, 14, 34, P.shell, 10) +
    rect(-9, -44, 18, 11, trim, 3) +
    circle(-12, -24, 3, P.berry) +
    circle(-4, -24, 3, P.gold)
  if (pose === 'floating') {
    return g(
      `transform="${flip ? 'scale(-1 1)' : ''}"`,
      // tether
      stroke('M-19 -34 Q -60 -22 -96 -34', P.shade, 3.5) +
        rect(14, -52, 12, 26, P.shade, 4) +
        body +
        stroke('M-19 -44 Q -38 -50 -42 -66', suit, 11) +
        stroke('M19 -44 Q 40 -46 46 -60', suit, 11) +
        stroke('M-12 -16 Q -22 0 -34 2', suit, 12) +
        stroke('M12 -16 Q 22 -2 36 -4', suit, 12) +
        helmet,
    )
  }
  if (pose === 'cheering') {
    return (
      rect(-13, -18, 11, 18, suit, 4) +
      rect(2, -18, 11, 18, suit, 4) +
      ellipse(-8, -1, 10, 4, trim) +
      ellipse(8, -1, 10, 4, trim) +
      body +
      stroke('M-17 -44 Q -34 -56 -30 -78', suit, 11) +
      stroke('M17 -44 Q 34 -56 30 -78', suit, 11) +
      helmet
    )
  }
  if (pose === 'sleeping') {
    // The sleeper only: a pillow, a head on it, and a mounded blanket with one
    // arm out. The BUNK belongs to the scene — drawing it here too produced two
    // overlapping slabs and a head that looked detached from everything.
    return (
      // pillow
      ellipse(-42, -13, 26, 13, P.card) +
      // body under the blanket
      path('M-30 0 Q -26 -30 4 -30 Q 44 -30 52 0 Z', P.sky) +
      path('M-30 0 Q -26 -30 4 -30 Q 12 -22 14 0 Z', P.sky100, 'opacity="0.5"') +
      // turned-down edge
      rect(-32, -32, 30, 9, P.sky100, 4) +
      // one arm resting outside it
      stroke('M0 -26 Q 22 -34 34 -26', suit, 9) +
      // head, asleep, facing up-left towards the light
      circle(-44, -32, 20, P.shell) +
      circle(-44, -32, 16, P.sky700) +
      closedEyes(-42, -33, 13, 6, P.card) +
      smile(-42, -26, 9, P.card, 1.8) +
      circle(-52, -38, 5, P.sky100, 'opacity="0.65"')
    )
  }
  return (
    rect(-13, -18, 11, 18, suit, 4) +
    rect(2, -18, 11, 18, suit, 4) +
    ellipse(-8, -1, 10, 4, trim) +
    ellipse(8, -1, 10, 4, trim) +
    rect(14, -52, 12, 26, P.shade, 4) +
    body +
    stroke('M-19 -44 Q -32 -34 -30 -22', suit, 11) +
    stroke('M19 -44 Q 32 -36 32 -24', suit, 11) +
    helmet
  )
}

export const creature = ({ body = P.berry, light = P.berry100 } = {}) =>
  stroke('M0 -54 L-8 -70', P.ink700, 3) +
  circle(-9, -73, 5, P.gold300) +
  litEllipse(0, -28, 27, 27, body, light) +
  ellipse(-16, -3, 11, 5, body) +
  ellipse(16, -3, 11, 5, body) +
  eyes(0, -32, 20, 4.5) +
  smile(0, -22, 14, P.ink700, 2.2)

export const wingsPair = () => at(-4, 0, 1.15, dragonWing(false)) + at(4, 0, 1.15, dragonWing(true))

export const dragonWingPart = dragonWing
