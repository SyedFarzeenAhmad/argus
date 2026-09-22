import { WORLD } from '../core/theme.js';

/**
 * mulberry32 - a tiny, fast, seeded PRNG.
 *
 * Using this instead of Math.random() is what makes the whole demo
 * reproducible: same seed, same streets, same building heights, same defect
 * locations, every single launch. That is what makes the pitch rehearsable.
 */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Uniform float in [min, max). */
const range = (rng, min, max) => min + rng() * (max - min);
/** Integer in [min, max] inclusive. */
const rangeInt = (rng, min, max) => Math.floor(range(rng, min, max + 1));

const ARTERIAL_WIDTH = 15;
const STREET_WIDTH = 9;

/**
 * Lay out one axis of the street network.
 *
 * Gaps are deliberately irregular - a uniform grid reads as generated, whereas
 * varied block depths with the occasional wide arterial reads as surveyed.
 */
function layAxis(rng, extent) {
  const half = extent / 2;
  const lines = [];
  let pos = -half + range(rng, 6, 16);
  let sinceArterial = 0;

  while (pos < half - 14) {
    // Every third-to-fifth road is promoted to an arterial: wider surface,
    // centre-line markings, and the roads the bus routes prefer to follow.
    const arterial = sinceArterial >= rangeInt(rng, 2, 4);
    sinceArterial = arterial ? 0 : sinceArterial + 1;

    lines.push({
      pos,
      width: arterial ? ARTERIAL_WIDTH : STREET_WIDTH,
      arterial,
    });

    // Arterials get more breathing room on the far side, which naturally
    // produces larger downtown superblocks.
    pos += arterial ? range(rng, 40, 58) : range(rng, 24, 40);
  }
  return lines;
}

/**
 * River centreline as a function of x. A gentle double sine reads as a natural
 * watercourse rather than a canal.
 */
function riverZ(x) {
  return 74 + Math.sin(x * 0.0135) * 46 + Math.sin(x * 0.031 + 1.2) * 15;
}

const RIVER_HALF_WIDTH = 15;

/**
 * Build the full city description.
 *
 * Pure data in, pure data out - no three.js objects. Geometry builders consume
 * this, which keeps layout logic testable and cheap to re-tune.
 */
export function buildLayout(seed = 20261124) {
  const rng = mulberry32(seed);
  const extent = WORLD.extent;
  const half = extent / 2;

  const xRoads = layAxis(rng, extent);
  const zRoads = layAxis(rng, extent);

  // Blocks are the gaps between consecutive roads, inset by each neighbour's
  // half-width so built form never overlaps the carriageway.
  const blocks = [];
  for (let i = 0; i < xRoads.length - 1; i++) {
    for (let j = 0; j < zRoads.length - 1; j++) {
      const a = xRoads[i];
      const b = xRoads[i + 1];
      const c = zRoads[j];
      const d = zRoads[j + 1];

      const x0 = a.pos + a.width / 2 + 1.5;
      const x1 = b.pos - b.width / 2 - 1.5;
      const z0 = c.pos + c.width / 2 + 1.5;
      const z1 = d.pos - d.width / 2 - 1.5;
      if (x1 - x0 < 8 || z1 - z0 < 8) continue;

      const cx = (x0 + x1) / 2;
      const cz = (z0 + z1) / 2;

      // Distance from centre drives both height and land use, giving a
      // recognisable dense core fading to low-rise edges.
      const centrality = 1 - Math.min(1, Math.hypot(cx, cz) / (half * 0.92));

      let kind = 'built';
      if (Math.abs(cz - riverZ(cx)) < RIVER_HALF_WIDTH + 6) kind = 'water';
      else if (rng() < 0.055 + (1 - centrality) * 0.06) kind = 'park';

      blocks.push({ x0, z0, x1, z1, cx, cz, w: x1 - x0, d: z1 - z0, centrality, kind });
    }
  }

  // River polyline, sampled across the full extent for the water ribbon mesh.
  const riverPoints = [];
  for (let x = -half - 20; x <= half + 20; x += 12) {
    riverPoints.push({ x, z: riverZ(x) });
  }

  return {
    seed,
    extent,
    half,
    xRoads,
    zRoads,
    blocks,
    river: { points: riverPoints, halfWidth: RIVER_HALF_WIDTH },
    rng, // shared stream, so downstream consumers stay deterministic too
  };
}

export { range, rangeInt, riverZ };
