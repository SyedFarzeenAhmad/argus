/**
 * Single source of truth for colour + spatial constants.
 *
 * Values are declared once as hex integers (what three.js wants) and exposed
 * alongside CSS-string equivalents (what the HUD wants), so the 3D scene and
 * the DOM chrome can never drift apart.
 */

const hex = (n) => '#' + n.toString(16).padStart(6, '0');

export const COLOR = {
  // --- environment -------------------------------------------------------
  void: 0x05070b, // clear colour + fog, near-black with a blue cast
  ground: 0x0b1017,
  gridMinor: 0x121a26,
  gridMajor: 0x1b2634,

  // --- built form -------------------------------------------------------
  buildingBase: 0x1b2532, // colour at street level
  buildingTop: 0x3e4e61, // colour at roof level (vertical gradient)
  roadSurface: 0x27313f,
  roadArterial: 0x33404f,
  roadMarking: 0x6d87a5,
  water: 0x081420,

  // --- fleet ------------------------------------------------------------
  busBody: 0x1d3a4f,
  busTrim: 0x7dd3fc,
  busSensor: 0x38bdf8, // camera FOV cone
  busAlert: 0xef4444, // bus highlighted during an incident

  // --- interface --------------------------------------------------------
  accent: 0x38bdf8,
  text: 0xe2e8f0,
  textDim: 0x8b9bb0,
};

export const CSS = Object.fromEntries(
  Object.entries(COLOR).map(([k, v]) => [k, hex(v)]),
);

/** World-space dimensions. One unit is loosely one metre. */
export const WORLD = {
  extent: 460, // city spans -extent/2 .. +extent/2 on both axes
  groundY: 0,
  roadY: 0.06, // road surfaces float just above ground to avoid z-fighting
  markingY: 0.30, // above every road layer, see roads.js layering note
  // Reaches past the far edge of the city from the home camera, so distance
  // reads as haze rather than as the map simply ending.
  fogNear: 300,
  fogFar: 1250,
};

/** Simulation + presentation timing, in seconds. */
export const TIMING = {
  loop: 90, // full demo cycle before reset
  incidentAt: 62, // scripted incident beat
  step: 1 / 60, // fixed simulation timestep
  introDuration: 5, // establishing camera move
};

export { hex };
