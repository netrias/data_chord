/**
 * "Chord" loading animation for the Stage 3 harmonizing state.
 * Five out-of-tune strings settle into a five-line staff, and the data points
 * riding them land as note heads stacked into chords.
 *
 * Purely decorative. The page shows a fallback spinner until this module marks
 * the loading state as running, so a failure here never leaves a blank box.
 *
 * Two parts: drawChordFrame is pure drawing for one moment in time, and
 * startHarmonizeAnimation owns the canvas size, the frame loop, and the marker.
 */

/** Drawing units. The canvas is scaled so every frame is drawn in this box. */
const W = 680;
const H = 320;
const TAU = Math.PI * 2;

/** Time of the single frame shown when the user prefers reduced motion. */
const STILL_FRAME_SECONDS = 6.3;

/** Value of data-animation on #loadingState that the CSS keys on. */
const RUNNING = 'running';

/**
 * @typedef {[number, number, number]} Rgb
 * @typedef {{ green: Rgb, surface: Rgb }} ChordColors
 */

/** @returns {Rgb | null} null when the text is not a six-digit hex color */
const parseHex = (text) => {
  const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(text.trim());
  return match ? [parseInt(match[1], 16), parseInt(match[2], 16), parseInt(match[3], 16)] : null;
};

/* why: decorative colors for the unharmonized strings and points; they stand for disparate source data and are deliberately off-brand. */
const DISPARATE = ['#e74c3c', '#3498db', '#f39c12', '#9b59b6', '#1abc9c'].map(parseHex);

const css = (c, a = 1) => `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${a})`;
const mix = (a, b, k) => a.map((v, i) => v + (b[i] - v) * k);
const clamp = (x) => Math.max(0, Math.min(1, x));
const smooth = (a, b, x) => {
  const k = clamp((x - a) / (b - a));
  return k * k * (3 - 2 * k);
};
const lerp = (a, b, k) => a + (b - a) * k;
const frac = (x) => x - Math.floor(x);
/* why: seeded, so a point keeps its shape and size from frame to frame without stored state. */
const rnd = (n) => frac(Math.sin(n * 127.1 + 311.7) * 43758.5453);
const pick = (list, seed) => list[Math.floor(rnd(seed) * list.length)];

/* Shapes are radius-by-angle functions, so any shape can morph into a note head. */
const poly = (n, scale = 1) => (th) => {
  const s = TAU / n;
  const a = ((th % s) + s) % s;
  return scale * Math.cos(Math.PI / n) / Math.cos(a - s / 2);
};
const SHAPES = [poly(3, 1.4), poly(4), poly(6), () => 0.85, poly(5, 1.1)];
const NOTE_HEAD = (th) => 1.125 / Math.hypot(0.9 * Math.cos(th), 1.25 * Math.sin(th));

const morphPath = (ctx, x, y, size, rot, from, to, k) => {
  ctx.beginPath();
  for (let j = 0; j < 60; j++) {
    const th = j / 60 * TAU;
    const r = size * lerp(from(th), to(th), k);
    const a = th + rot;
    if (j) ctx.lineTo(x + r * Math.cos(a), y + r * Math.sin(a));
    else ctx.moveTo(x + r * Math.cos(a), y + r * Math.sin(a));
  }
  ctx.closePath();
};

/* Soft left and right edges, painted in the stage color. */
const fadeEdges = (ctx, width, surface) => {
  for (const [x0, x1] of [[0, width], [W, W - width]]) {
    const g = ctx.createLinearGradient(x0, 0, x1, 0);
    g.addColorStop(0, css(surface, 1));
    g.addColorStop(1, css(surface, 0));
    ctx.fillStyle = g;
    ctx.fillRect(Math.min(x0, x1), 0, width, H);
  }
};

/* Per-string wave frequency, amplitude, speed, stroke width, and dash length. */
const F = [0.031, 0.047, 0.022, 0.058, 0.038];
const A = [30, 18, 38, 14, 24];
const S = [1.7, 2.6, 1.1, 3.3, 2.0];
const WIDTH = [1.3, 3.6, 2, 4.6, 1.6];
const DASH = [0, 20, 0, 34, 11];
/* Staff steps of the three notes in each chord, and the strings those notes ride in on. */
const TRIADS = [[-3, -1, 1], [-2, 0, 2], [-4, -2, 0], [-1, 1, 3]];
const RIDES = [[4, 2, 0], [3, 2, 1], [4, 3, 1], [3, 1, 0]];
const GROUPS = 4;
const STEP = 8;
const MID = H / 2;
const LOCK_X = 0.62 * W;
const lock = (x) => smooth(0.3 * W, LOCK_X, x);
/** Largest stroke-width change, in drawing units, before a string starts a new stroke. */
const WIDTH_BAND = 0.3;

/* A string's color runs from its own color to brand green across the lock zone. */
const stringGradient = (ctx, from, to) => {
  const gradient = ctx.createLinearGradient(0, 0, W, 0);
  for (const k of [0, 0.25, 0.5, 0.75, 1]) {
    gradient.addColorStop(0.36 + 0.24 * k, css(mix(from, to, smooth(0, 1, k))));
  }
  return gradient;
};

/**
 * Draw the Chord frame for time t (seconds) in W x H drawing units.
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} t
 * @param {ChordColors} colors
 */
const drawChordFrame = (ctx, t, colors) => {
  const { green, surface } = colors;

  /* One chord of three notes per group. Each chord rings the staff as it locks. */
  const groups = [];
  for (let g = 0; g < GROUPS; g++) {
    const q = t * 0.1 + g / GROUPS;
    const cyc = Math.floor(q);
    const x = frac(q) * W;
    const order = (((cyc * GROUPS - g) % 4) + 4) % 4;
    const ring = smooth(0.55 * W, 0.64 * W, x) * (1 - smooth(0.64 * W, 0.98 * W, x));
    groups.push({ x, cyc, g, ring, triad: TRIADS[order], rides: RIDES[order], barX: frac(q + 0.5 / GROUPS) * W });
  }
  const yAt = (i, x) => {
    const h = lock(x);
    const messy = (i - 2) * 30
      + A[i] * Math.sin(F[i] * x - S[i] * t + i * 1.9)
      + 0.4 * A[i] * Math.sin(2.7 * F[i] * x - 1.6 * S[i] * t + i);
    let vib = 0;
    for (const grp of groups) {
      if (grp.ring > 0.01) {
        vib += 3 * grp.ring * Math.exp(-Math.pow((x - grp.x) / 46, 2)) * Math.sin(0.45 * x - 16 * t + i * 1.3);
      }
    }
    return MID + (1 - h) * messy + h * ((i - 2) * 2 * STEP + vib);
  };

  /* why: a GPU canvas pays per stroke call, so each string is a few long strokes (one per width band), not one per segment. */
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  for (let i = 0; i < 5; i++) {
    ctx.strokeStyle = stringGradient(ctx, DISPARATE[i], green);
    let bandWidth = WIDTH[i];
    let penDown = false;
    let py = yAt(i, 0);
    ctx.lineWidth = bandWidth;
    ctx.beginPath();
    for (let x = 4; x <= W; x += 4) {
      const y = yAt(i, x);
      const width = lerp(WIDTH[i], 1.5, lock(x));
      if (Math.abs(width - bandWidth) > WIDTH_BAND) {
        ctx.stroke();
        ctx.beginPath();
        bandWidth = width;
        ctx.lineWidth = width;
        penDown = false;
      }
      const solid = lerp(0.55, 1.01, smooth(0.16 * W, 0.5 * W, x));
      if (!DASH[i] || frac((x - 55 * t) / DASH[i]) < solid) {
        if (!penDown) ctx.moveTo(x - 4, py);
        ctx.lineTo(x, y);
        penDown = true;
      } else {
        penDown = false;
      }
      py = y;
    }
    ctx.stroke();
  }

  for (const grp of groups) {
    const bar = smooth(LOCK_X, LOCK_X + 60, grp.barX);
    if (bar > 0) {
      ctx.strokeStyle = css(green, bar);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(grp.barX, MID - 4 * STEP);
      ctx.lineTo(grp.barX, MID + 4 * STEP);
      ctx.stroke();
    }
    const { x } = grp;
    const h = lock(x);
    const e = smooth(0.42 * W, LOCK_X, x);
    const ys = grp.triad.map((step, k) => lerp(yAt(grp.rides[k], x), MID - step * STEP, h));
    const stem = smooth(LOCK_X - 10, LOCK_X + 50, x);
    if (stem > 0) {
      ctx.strokeStyle = css(green, stem);
      ctx.lineWidth = 1.8;
      ctx.beginPath();
      ctx.moveTo(x + 7, ys[0] - 1);
      ctx.lineTo(x + 7, ys[2] - 1 - 30 * stem);
      ctx.stroke();
    }
    ys.forEach((y, k) => {
      const seed = grp.g * 11 + k * 5 + grp.cyc * 37;
      ctx.fillStyle = css(mix(DISPARATE[grp.rides[k]], green, e));
      ctx.strokeStyle = css(surface);
      ctx.lineWidth = 2.5;
      morphPath(ctx, x, y, lerp(6 + rnd(seed) * 4, 6.2, e), lerp(seed + t * 1.5, -0.35, e), pick(SHAPES, seed + 1), NOTE_HEAD, e);
      ctx.stroke();
      ctx.fill();
    });
  }
  fadeEdges(ctx, 60, surface);
};

/** @returns {ChordColors} brand green and stage background from the design tokens, or literals if unreadable */
const readColors = (win) => {
  const style = win.getComputedStyle(win.document.documentElement);
  return {
    green: parseHex(style.getPropertyValue('--netrias-500')) ?? [0x73, 0xb3, 0x06],
    surface: parseHex(style.getPropertyValue('--white')) ?? [0xff, 0xff, 0xff],
  };
};

/**
 * Start the animation on the canvas inside #loadingState and mark the loading
 * state as running. Leaves the state unmarked (spinner visible) when there is
 * no 2D context, and unmarks it again if any frame cannot be drawn.
 * @param {Window & typeof globalThis} win
 */
export const startHarmonizeAnimation = (win) => {
  const loadingState = win.document.getElementById('loadingState');
  const canvas = /** @type {HTMLCanvasElement | null} */ (loadingState?.querySelector('.harmonize-animation'));
  const ctx = canvas?.getContext('2d');
  if (!ctx || !win.ResizeObserver) return;

  const colors = readColors(win);
  const reducedMotion = win.matchMedia('(prefers-reduced-motion: reduce)').matches;
  /** @type {number | null} */
  let frame = null;

  const stopFrame = () => {
    if (frame !== null) win.cancelAnimationFrame(frame);
    frame = null;
  };
  const showFallback = () => {
    stopFrame();
    observer.disconnect();
    delete loadingState.dataset.animation;
  };
  const paint = (seconds) => {
    try {
      const dpr = Math.min(win.devicePixelRatio || 1, 2);
      const width = Math.round(canvas.clientWidth * dpr);
      if (canvas.width !== width) {
        canvas.width = width;
        canvas.height = Math.round(width * H / W);
      }
      const scale = canvas.width / W;
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.clearRect(0, 0, W, H);
      drawChordFrame(ctx, seconds, colors);
    } catch (error) {
      showFallback();
      throw error;
    }
  };
  const loop = (ms) => {
    frame = null;
    if (canvas.clientWidth === 0) return;
    paint(ms / 1000);
    frame = win.requestAnimationFrame(loop);
  };
  const syncAnimation = () => {
    if (canvas.clientWidth === 0) {
      stopFrame();
    } else if (reducedMotion) {
      paint(STILL_FRAME_SECONDS);
    } else if (frame === null) {
      loop(win.performance.now());
    }
  };

  /* A canvas with no layout box stops drawing and resumes when a retry shows it again. */
  const observer = new win.ResizeObserver(syncAnimation);
  canvas.addEventListener('contextlost', showFallback, { once: true });
  loadingState.dataset.animation = RUNNING;
  observer.observe(canvas);
  syncAnimation();
};

if (typeof window !== 'undefined') {
  startHarmonizeAnimation(window);
}
