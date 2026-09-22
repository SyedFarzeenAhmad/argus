import { SEVERITY_CSS } from '../events/catalog.js';

const MAX_ROWS = 9;

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/** Translate a hex colour into a low-alpha rgba fill for glyph backgrounds. */
function tint(css, alpha) {
  const n = parseInt(css.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/**
 * The live event ticker.
 *
 * Newest at the top, capped at MAX_ROWS. Rows are removed from the bottom
 * rather than scrolled, so the panel never grows and never needs a scrollbar -
 * which matters on a projector where a scrollbar is invisible anyway.
 */
export function createFeed({ right }, { onSelect }) {
  const panel = el('section', 'panel');
  panel.id = 'feed';

  const head = el('div', 'feed-head');
  head.append(el('span', 'eyebrow', 'Live detection feed'));
  const count = el('span', 'feed-count', '0 events');
  head.append(count);

  const list = el('ul', 'feed-list');
  const empty = el('li', 'feed-empty', 'Awaiting first uplink from fleet…');
  list.append(empty);

  panel.append(head, list);
  right.append(panel);

  const rows = new Map(); // event.id -> <li>
  let total = 0;
  let selectedId = null;

  function add(event) {
    if (empty.parentNode) empty.remove();

    const row = el('li', 'feed-row');
    row.dataset.eventId = event.id;
    if (event.severity === 'critical') row.classList.add('is-critical');

    const glyph = el('span', 'feed-glyph', event.type.glyph);
    glyph.style.background = tint(event.type.css, 0.15);
    glyph.style.color = event.type.css;

    const label = el('span', 'feed-label', event.label);
    const conf = el('span', 'feed-conf', `${(event.confidence * 100).toFixed(0)}%`);
    conf.style.color = SEVERITY_CSS[event.severity];

    const meta = el(
      'span',
      'feed-meta',
      `${event.time} · ${event.busId} · ${event.routeName}`,
    );

    row.append(glyph, label, conf, meta);
    row.addEventListener('click', () => onSelect(event));

    list.prepend(row);
    rows.set(event.id, row);

    // Trim from the bottom once the panel is full.
    while (list.children.length > MAX_ROWS) {
      const last = list.lastElementChild;
      rows.delete(last.dataset.eventId);
      last.remove();
    }

    total++;
    count.textContent = `${total} event${total === 1 ? '' : 's'}`;
  }

  /** Mirror the 3D selection into the list, so the two stay in agreement. */
  function select(event) {
    if (selectedId && rows.has(selectedId)) {
      rows.get(selectedId).classList.remove('is-selected');
    }
    selectedId = event?.id ?? null;
    if (selectedId && rows.has(selectedId)) {
      rows.get(selectedId).classList.add('is-selected');
    }
  }

  function clear() {
    list.replaceChildren(empty);
    rows.clear();
    selectedId = null;
    total = 0;
    count.textContent = '0 events';
  }

  return { add, select, clear };
}
