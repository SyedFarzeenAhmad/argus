import { DETECTION_TYPES } from '../events/catalog.js';
import { TIMING } from '../core/theme.js';

/**
 * KPI definitions.
 *
 * `format` receives the live stats object. Values are eased toward their target
 * rather than snapped, which is what produces the count-up on load and stops
 * the numbers from looking like they are jittering.
 */
const KPIS = [
  { key: 'busesOnline', label: 'Buses online', decimals: 0 },
  { key: 'kmCovered', label: 'Fleet km today', decimals: 1 },
  { key: 'defectsLogged', label: 'Defects logged', decimals: 0 },
  { key: 'alertsSent', label: 'Alerts escalated', decimals: 0 },
  { key: 'queueDepth', label: 'Uplink queue', decimals: 0, accent: true },
  { key: 'avgConfidence', label: 'Mean confidence', decimals: 1, scale: 100, unit: '%' },
];

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function formatClock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

/**
 * Builds the top bar, the KPI block and the detection legend, and returns an
 * updater to be called once per frame.
 */
export function createHud({ topbar, left }) {
  // ── top bar ────────────────────────────────────────────────────────────
  const brand = el('div', 'brand');
  brand.append(el('span', 'brand-mark', 'ARGUS'));
  const sub = el('span', 'brand-sub');
  sub.innerHTML =
    'Mobile Urban Intelligence &middot; <b>Fleet Sensing Network</b>';
  brand.append(sub);

  const live = el('div', 'live');
  live.append(el('span', 'live-dot'));
  const liveText = el('span', undefined, 'Live');
  live.append(liveText);

  const loop = el('div', 'loop');
  loop.append(el('span', 'eyebrow', 'Cycle'));
  const track = el('div', 'loop-track');
  const fill = el('div', 'loop-fill');
  track.append(fill);
  loop.append(track);
  const loopTime = el('span', 'loop-time', '00:00');
  loop.append(loopTime);

  topbar.append(brand, el('div', 'topbar-spacer'), loop, live);

  // ── KPI panel ──────────────────────────────────────────────────────────
  const kpiPanel = el('section', 'panel');
  kpiPanel.id = 'kpis';
  kpiPanel.append(el('div', 'eyebrow', 'Network status'));

  const grid = el('div', 'kpi-grid');
  const kpiNodes = KPIS.map((def) => {
    const cell = el('div', 'kpi' + (def.accent ? ' is-queue' : ''));
    const value = el('div', 'kpi-value', '0');
    if (def.unit) {
      value.textContent = '0';
      const unit = el('span', 'kpi-unit', def.unit);
      value.append(unit);
    }
    cell.append(value, el('div', 'kpi-label', def.label));
    grid.append(cell);
    return { def, value, shown: 0 };
  });
  kpiPanel.append(grid);

  // ── legend ─────────────────────────────────────────────────────────────
  const legendPanel = el('section', 'panel');
  legendPanel.id = 'legend';
  legendPanel.append(el('div', 'eyebrow', 'Detection classes'));

  const list = el('div', 'legend-list');
  const legendNodes = new Map();
  for (const type of DETECTION_TYPES) {
    const row = el('div', 'legend-row');
    const swatch = el('span', 'legend-swatch');
    swatch.style.background = type.css;
    const count = el('span', 'legend-count', '0');
    row.append(swatch, el('span', undefined, type.label), count);
    list.append(row);
    legendNodes.set(type.id, count);
  }
  legendPanel.append(list);

  left.append(kpiPanel, legendPanel);

  // ── per-frame update ───────────────────────────────────────────────────
  const mix = new Map();

  function countEvent(event) {
    mix.set(event.typeId, (mix.get(event.typeId) ?? 0) + 1);
    legendNodes.get(event.typeId).textContent = String(mix.get(event.typeId));
  }

  function resetCounts() {
    mix.clear();
    for (const node of legendNodes.values()) node.textContent = '0';
  }

  function update(dt, stats) {
    for (const node of kpiNodes) {
      const target = (stats[node.def.key] ?? 0) * (node.def.scale ?? 1);
      // Exponential ease, frame-rate corrected. Snapping the last fraction
      // avoids a value creeping forever toward an integer.
      const k = 1 - Math.pow(0.0025, dt);
      node.shown += (target - node.shown) * k;
      if (Math.abs(target - node.shown) < 0.02) node.shown = target;

      const text = node.shown.toFixed(node.def.decimals);
      if (node.def.unit) {
        node.value.firstChild.nodeValue = text;
      } else {
        node.value.textContent = text;
      }
    }

    fill.style.width = `${Math.min(100, stats.loopProgress * 100).toFixed(2)}%`;
    loopTime.textContent = `${formatClock(stats.simTime)} / ${formatClock(TIMING.loop)}`;

    live.classList.toggle('is-paused', stats.paused);
    liveText.textContent = stats.paused ? 'Paused' : 'Live';
  }

  return { update, countEvent, resetCounts };
}
