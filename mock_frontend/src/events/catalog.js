import { hex } from '../core/theme.js';

/**
 * Detection taxonomy, drawn directly from the problem statement's list of
 * targets: road defects, missing infrastructure, hazards and driving incidents.
 *
 * `weight` is relative frequency in the simulation - potholes are common,
 * hit-and-run incidents are not, and the feed should reflect that.
 */
export const DETECTION_TYPES = [
  {
    id: 'pothole',
    label: 'Pothole',
    color: 0xf59e0b,
    severity: 'medium',
    weight: 30,
    category: 'Road defect',
    glyph: '\u25c9',
    action: 'Raise patching work order to ward engineer',
    confidence: [0.86, 0.98],
  },
  {
    id: 'damaged_road',
    label: 'Damaged road surface',
    color: 0xfb923c,
    severity: 'medium',
    weight: 18,
    category: 'Road defect',
    glyph: '\u2593',
    action: 'Schedule resurfacing survey for this segment',
    confidence: [0.78, 0.94],
  },
  {
    id: 'waterlogging',
    label: 'Waterlogging',
    color: 0x38bdf8,
    severity: 'high',
    weight: 12,
    category: 'Hazard',
    glyph: '\u2248',
    action: 'Dispatch de-silting crew; flag drain blockage',
    confidence: [0.88, 0.99],
  },
  {
    id: 'missing_divider',
    label: 'Missing road divider',
    color: 0xfacc15,
    severity: 'medium',
    weight: 10,
    category: 'Missing infrastructure',
    glyph: '\u2016',
    action: 'Reinstate median segment; interim cones',
    confidence: [0.8, 0.95],
  },
  {
    id: 'missing_zebra',
    label: 'Missing zebra crossing',
    color: 0xf472b6,
    severity: 'high',
    weight: 9,
    category: 'Missing infrastructure',
    glyph: '\u2261',
    action: 'Repaint crossing - school route priority',
    confidence: [0.82, 0.96],
  },
  {
    id: 'damaged_sign',
    label: 'Damaged traffic signboard',
    color: 0xa78bfa,
    severity: 'low',
    weight: 12,
    category: 'Missing infrastructure',
    glyph: '\u2691',
    action: 'Replace signboard; log asset ID',
    confidence: [0.75, 0.93],
  },
  {
    id: 'pedestrian_risk',
    label: 'Vulnerable pedestrian crossing',
    color: 0x34d399,
    severity: 'high',
    weight: 7,
    category: 'Safety',
    glyph: '\u26a0',
    action: 'Assess for warden posting during school hours',
    confidence: [0.79, 0.92],
  },
  {
    id: 'incident',
    label: 'Rash driving / hit-and-run',
    color: 0xef4444,
    severity: 'critical',
    weight: 0, // never random - fired only by the scripted incident beat
    category: 'Incident',
    glyph: '\u25b2',
    action: 'Escalate to control room; preserve 30 s evidence clip',
    confidence: [0.91, 0.97],
  },
];

for (const t of DETECTION_TYPES) t.css = hex(t.color);

export const BY_ID = Object.fromEntries(DETECTION_TYPES.map((t) => [t.id, t]));

export const SEVERITY_RANK = { low: 0, medium: 1, high: 2, critical: 3 };

export const SEVERITY_CSS = {
  low: '#7f96ad',
  medium: '#f59e0b',
  high: '#fb7185',
  critical: '#ef4444',
};

/** Weighted pick across every type with a non-zero weight. */
export function pickType(rng) {
  const pool = DETECTION_TYPES.filter((t) => t.weight > 0);
  const total = pool.reduce((s, t) => s + t.weight, 0);
  let r = rng() * total;
  for (const t of pool) {
    r -= t.weight;
    if (r <= 0) return t;
  }
  return pool[pool.length - 1];
}

const PLATE_STATES = ['KA', 'MH', 'TN', 'TS', 'DL', 'KL', 'AP'];
const PLATE_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ';

/** A plausible Indian registration mark, for the incident evidence card. */
export function mockPlate(rng) {
  const state = PLATE_STATES[Math.floor(rng() * PLATE_STATES.length)];
  const rto = String(Math.floor(rng() * 40) + 1).padStart(2, '0');
  const series =
    PLATE_LETTERS[Math.floor(rng() * PLATE_LETTERS.length)] +
    PLATE_LETTERS[Math.floor(rng() * PLATE_LETTERS.length)];
  const number = String(Math.floor(rng() * 9000) + 1000);
  return `${state} ${rto} ${series} ${number}`;
}

/**
 * Map world coordinates onto plausible lat/lon.
 *
 * Anchored loosely on Bengaluru so the coordinates shown on evidence cards look
 * like somewhere real. The scale factor is honest for the city's latitude:
 * roughly 111 km per degree, so one world unit reads as one metre.
 */
const ORIGIN = { lat: 12.9716, lon: 77.5946 };
const M_PER_DEG_LAT = 110574;
const M_PER_DEG_LON = 111320 * Math.cos((ORIGIN.lat * Math.PI) / 180);

export function toLatLon(x, z) {
  return {
    lat: ORIGIN.lat - z / M_PER_DEG_LAT,
    lon: ORIGIN.lon + x / M_PER_DEG_LON,
  };
}

export function formatLatLon(x, z) {
  const { lat, lon } = toLatLon(x, z);
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

/**
 * Stratified type allocation.
 *
 * Independent weighted draws (see pickType) are correct in expectation but
 * lumpy at small N: a 45-site draw can easily land 15 signboards and 1 divider,
 * which makes the event feed look monotonous and under-sells how many distinct
 * classes the platform detects. Apportioning exact quotas by largest remainder
 * and then shuffling keeps placement random while guaranteeing the mix matches
 * the intended distribution on every run.
 */
export function buildTypeQuota(rng, count) {
  const pool = DETECTION_TYPES.filter((t) => t.weight > 0);
  const total = pool.reduce((s, t) => s + t.weight, 0);

  const alloc = pool.map((t) => {
    const exact = (count * t.weight) / total;
    return { type: t, n: Math.floor(exact), remainder: exact - Math.floor(exact) };
  });

  // Largest-remainder method: hand out the leftover seats to whichever types
  // were rounded down hardest.
  let leftover = count - alloc.reduce((s, a) => s + a.n, 0);
  alloc.sort((a, b) => b.remainder - a.remainder);
  for (let i = 0; i < leftover; i++) alloc[i % alloc.length].n++;

  const bag = [];
  for (const a of alloc) for (let i = 0; i < a.n; i++) bag.push(a.type);

  // Seeded Fisher-Yates, so the ordering is random but reproducible.
  for (let i = bag.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [bag[i], bag[j]] = [bag[j], bag[i]];
  }
  return bag;
}
