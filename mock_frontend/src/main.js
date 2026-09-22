import * as THREE from 'three';
import { CSS, TIMING } from './core/theme.js';
import { createViewer } from './core/viewer.js';
import { buildLayout } from './city/layout.js';
import { buildRoads } from './city/roads.js';
import { buildBuildings } from './city/buildings.js';
import { buildEnvironment } from './city/environment.js';
import { buildRoutes, buildRouteRibbons } from './fleet/routes.js';
import { buildFleet } from './fleet/buses.js';
import { createSimulation } from './events/generator.js';
import { createMarkerLayer } from './events/markers.js';
import { createHud } from './ui/hud.js';
import { createFeed } from './ui/feed.js';
import { createDetailCard } from './ui/detail.js';

/* ── theme handoff ───────────────────────────────────────────────────────────
 * Push the 3D palette into CSS custom properties so the chrome is coloured from
 * the same source as the scene. Re-grading the visualiser means editing one
 * file, not two.
 * ─────────────────────────────────────────────────────────────────────────── */
const rootStyle = document.documentElement.style;
rootStyle.setProperty('--void', CSS.void);
rootStyle.setProperty('--accent', CSS.accent);
rootStyle.setProperty('--text', CSS.text);
rootStyle.setProperty('--text-dim', CSS.textDim);

/* ── scene assembly ─────────────────────────────────────────────────────── */

const container = document.getElementById('scene');
const viewer = createViewer(container);

const layout = buildLayout();
viewer.scene.add(buildEnvironment(layout));
viewer.scene.add(buildRoads(layout));

const buildings = buildBuildings(layout);
viewer.scene.add(buildings);

const routes = buildRoutes(layout, 5);
viewer.scene.add(buildRouteRibbons(routes));

const fleet = buildFleet(layout, routes, 3);
viewer.scene.add(fleet.group);

const sim = createSimulation({ layout, routes, fleet });
const markers = createMarkerLayer(viewer);

/* ── interface ──────────────────────────────────────────────────────────── */

const hud = createHud({
  topbar: document.getElementById('topbar'),
  left: document.getElementById('left'),
});

const detail = createDetailCard(document.body, {
  onClose: () => selectEvent(null),
});

const feed = createFeed(
  { right: document.getElementById('right') },
  {
    // Clicking a feed row flies the camera to the marker and opens its card,
    // which is what turns the ticker into a navigation aid rather than decor.
    onSelect: (event) => {
      selectEvent(event);
      viewer.focusOn(event.position);
    },
  },
);

/** Single place that keeps the 3D selection, the feed highlight and the card in agreement. */
function selectEvent(event) {
  markers.select(event);
  feed.select(event);
  if (event) detail.show(event);
  else detail.hide();
}

/* ── simulation wiring ──────────────────────────────────────────────────────
 * Everything below consumes DetectionEvents and is entirely unaware they are
 * synthetic. Replacing createSimulation with a WebSocket client is the whole of
 * the work needed to make this live.
 * ─────────────────────────────────────────────────────────────────────────── */

sim.subscribe((event) => {
  markers.add(event);
  feed.add(event);
  hud.countEvent(event);

  // Critical events pull the camera - but only while nobody is driving it.
  // Yanking the view away from a presenter mid-gesture would be hostile.
  if (event.severity === 'critical' && viewer.controls.autoRotate) {
    viewer.focusOn(event.position, 104);
    selectEvent(event);
  }
});

sim.onReset(() => {
  markers.clearAll();
  feed.clear();
  hud.resetCounts();
  detail.hide();
});

/* ── pointer interaction ────────────────────────────────────────────────── */

const tooltip = document.getElementById('tooltip');
const ndc = new THREE.Vector2();

function toNdc(clientX, clientY) {
  const rect = container.getBoundingClientRect();
  ndc.set(
    ((clientX - rect.left) / rect.width) * 2 - 1,
    -((clientY - rect.top) / rect.height) * 2 + 1,
  );
  return ndc;
}

container.addEventListener('pointermove', (e) => {
  const event = markers.pick(toNdc(e.clientX, e.clientY));
  container.style.cursor = event ? 'pointer' : 'grab';

  if (event) {
    tooltip.innerHTML = `${event.label}<span class="tt-conf">${(event.confidence * 100).toFixed(0)}%</span>`;
    // Offset from the cursor, and flip left near the right edge so the tooltip
    // never runs off screen.
    const flip = e.clientX > window.innerWidth - 190;
    tooltip.style.left = `${e.clientX + (flip ? -14 : 14)}px`;
    tooltip.style.top = `${e.clientY + 12}px`;
    tooltip.style.transform = flip ? 'translateX(-100%)' : 'none';
    tooltip.classList.add('is-visible');
  } else {
    tooltip.classList.remove('is-visible');
  }
});

container.addEventListener('pointerleave', () => {
  tooltip.classList.remove('is-visible');
});

// Distinguishing a click from an orbit drag: OrbitControls swallows the drag,
// but a plain click listener would still fire at the end of one. Requiring a
// short, near-stationary press is the simplest reliable discriminator.
const CLICK_SLOP = 5; // px
const CLICK_TIME = 400; // ms
let press = null;

container.addEventListener('pointerdown', (e) => {
  press = { x: e.clientX, y: e.clientY, t: performance.now() };
});

container.addEventListener('pointerup', (e) => {
  if (!press) return;
  const moved = Math.hypot(e.clientX - press.x, e.clientY - press.y);
  const held = performance.now() - press.t;
  press = null;
  if (moved > CLICK_SLOP || held > CLICK_TIME) return;

  const event = markers.pick(toNdc(e.clientX, e.clientY));
  selectEvent(event); // null clears the selection and closes the card
});

/* ── keyboard ───────────────────────────────────────────────────────────── */

window.addEventListener('keydown', (e) => {
  if (e.target instanceof HTMLInputElement) return;

  switch (e.code) {
    case 'Space':
      e.preventDefault(); // stop the page scrolling under the canvas
      sim.togglePause();
      break;
    case 'KeyR':
      viewer.resetCamera();
      break;
    case 'KeyB':
      viewer.toggleBloom();
      break;
    case 'Escape':
      selectEvent(null);
      break;
    default:
      break;
  }
});

/* ── frame loop ─────────────────────────────────────────────────────────── */

viewer.onFrame((dt, elapsed) => {
  sim.update(dt);
  markers.update(dt, elapsed);
  hud.update(dt, sim.stats());
});

/* ── presenter handle ───────────────────────────────────────────────────────
 * Exposed deliberately. During a pitch you sometimes need to jump straight to
 * a beat rather than wait for it, and it makes the visualiser inspectable from
 * the console when checking behaviour on an unfamiliar machine.
 *
 *   ARGUS.skipTo(60)   - advance the simulation to the incident beat
 *   ARGUS.stats()      - current network figures
 *   ARGUS.select(n)    - open the evidence card for event number n
 * ─────────────────────────────────────────────────────────────────────────── */
window.ARGUS = {
  viewer,
  sim,
  markers,
  fleet,
  routes,
  layout,
  stats: () => sim.stats(),
  /**
   * Run the simulation forward to a given cycle time, in seconds.
   *
   * Stops at the loop boundary rather than wrapping through it. Without this
   * guard, asking for a time at or beyond the cycle length would silently
   * trigger a reset and then keep stepping into the *next* cycle, leaving the
   * clock somewhere unrelated to what was requested.
   */
  skipTo(seconds) {
    const target = Math.min(seconds, TIMING.loop - 0.05);
    let previous = sim.state.simTime;
    let guard = 0;
    while (sim.state.simTime < target && guard++ < 8000) {
      sim.update(1 / 60);
      if (sim.state.simTime < previous) break; // cycle wrapped; stop here
      previous = sim.state.simTime;
    }
    return sim.stats();
  },
  select(no) {
    const event = sim.state.emitted.find((e) => e.no === no);
    if (event) selectEvent(event);
    return event ?? null;
  },
};

// One-line build report, handy when checking performance on a demo machine.
console.info(
  `[ARGUS] city: ${buildings.userData.stats.buildings} buildings, ` +
    `${buildings.userData.stats.windows} lit windows, ${layout.blocks.length} blocks · ` +
    `fleet: ${fleet.buses.length} buses on ${routes.length} routes · ` +
    `${sim.sites.length} defect sites seeded`,
);
