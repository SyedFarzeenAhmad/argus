import { SEVERITY_CSS } from '../events/catalog.js';

const FRAME_W = 342;
const FRAME_H = 190;

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/** Cheap string hash, used to seed per-event frame variation deterministically. */
function hashString(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967296;
}

/* ────────────────────────────────────────────────────────────────────────────
 * Mock camera still.
 *
 * Drawn procedurally rather than shipped as images: nothing to load, nothing to
 * licence, and every event gets a frame consistent with its own class and
 * position. The important detail is that the *defect itself* is drawn under the
 * bounding box - a box over noise reads as a placeholder, a box over a rendered
 * pothole reads as a detection.
 * ──────────────────────────────────────────────────────────────────────────── */

const HORIZON = FRAME_H * 0.42;
const ROAD_BOTTOM = [FRAME_W * 0.04, FRAME_W * 0.96];
const ROAD_TOP = [FRAME_W * 0.435, FRAME_W * 0.565];

/** Interpolate the road's left/right edge at a given depth (0 = horizon, 1 = camera). */
function roadEdges(depth) {
  return [
    ROAD_TOP[0] + (ROAD_BOTTOM[0] - ROAD_TOP[0]) * depth,
    ROAD_TOP[1] + (ROAD_BOTTOM[1] - ROAD_TOP[1]) * depth,
  ];
}

const yAtDepth = (depth) => HORIZON + (FRAME_H - HORIZON) * depth;

function drawBackdrop(ctx, seed) {
  // Sky
  const sky = ctx.createLinearGradient(0, 0, 0, HORIZON);
  sky.addColorStop(0, '#080e16');
  sky.addColorStop(1, '#16242f');
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, FRAME_W, HORIZON);

  // Skyline silhouette
  ctx.fillStyle = '#0b131c';
  let x = -8;
  let n = 0;
  while (x < FRAME_W) {
    const w = 14 + ((seed * 1000 + n * 137) % 26);
    const h = 12 + ((seed * 700 + n * 91) % 34);
    ctx.fillRect(x, HORIZON - h, w, h);
    x += w + 3;
    n++;
  }

  // Ground
  ctx.fillStyle = '#0a0f16';
  ctx.fillRect(0, HORIZON, FRAME_W, FRAME_H - HORIZON);

  // Carriageway
  ctx.beginPath();
  ctx.moveTo(ROAD_TOP[0], HORIZON);
  ctx.lineTo(ROAD_TOP[1], HORIZON);
  ctx.lineTo(ROAD_BOTTOM[1], FRAME_H);
  ctx.lineTo(ROAD_BOTTOM[0], FRAME_H);
  ctx.closePath();
  const road = ctx.createLinearGradient(0, HORIZON, 0, FRAME_H);
  road.addColorStop(0, '#161d26');
  road.addColorStop(1, '#0f151c');
  ctx.fillStyle = road;
  ctx.fill();

  // Edge lines
  ctx.strokeStyle = 'rgba(120, 145, 170, 0.22)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(ROAD_TOP[0], HORIZON);
  ctx.lineTo(ROAD_BOTTOM[0], FRAME_H);
  ctx.moveTo(ROAD_TOP[1], HORIZON);
  ctx.lineTo(ROAD_BOTTOM[1], FRAME_H);
  ctx.stroke();

  // Centre dashes, spaced by depth so they converge naturally.
  ctx.fillStyle = 'rgba(150, 172, 196, 0.3)';
  for (let i = 1; i < 9; i++) {
    const d0 = Math.pow(i / 9, 2.1);
    const d1 = Math.pow((i + 0.42) / 9, 2.1);
    const [l0, r0] = roadEdges(d0);
    const [l1, r1] = roadEdges(d1);
    const w0 = (r0 - l0) * 0.014;
    const w1 = (r1 - l1) * 0.014;
    ctx.beginPath();
    ctx.moveTo((l0 + r0) / 2 - w0, yAtDepth(d0));
    ctx.lineTo((l0 + r0) / 2 + w0, yAtDepth(d0));
    ctx.lineTo((l1 + r1) / 2 + w1, yAtDepth(d1));
    ctx.lineTo((l1 + r1) / 2 - w1, yAtDepth(d1));
    ctx.closePath();
    ctx.fill();
  }
}

/**
 * Draw the defect for a given class and return its bounding box.
 * Each branch is a handful of primitives - enough to be unmistakable at this
 * size without pretending to be photographic.
 */
function drawDefect(ctx, typeId, seed, color) {
  const depth = 0.52 + seed * 0.16;
  const [left, right] = roadEdges(depth);
  const y = yAtDepth(depth);
  const laneW = right - left;
  const cx = left + laneW * (0.3 + seed * 0.4);

  switch (typeId) {
    case 'pothole': {
      const rx = laneW * 0.11;
      const ry = rx * 0.42;
      ctx.fillStyle = '#05080b';
      ctx.beginPath();
      ctx.ellipse(cx, y, rx, ry, 0, 0, Math.PI * 2);
      ctx.fill();
      // Broken rim on the near side catches the light.
      ctx.strokeStyle = 'rgba(180, 170, 150, 0.4)';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.ellipse(cx, y, rx * 1.1, ry * 1.15, 0, 0.15, Math.PI - 0.15);
      ctx.stroke();
      return [cx - rx * 1.5, y - ry * 2.6, rx * 3, ry * 5.2];
    }

    case 'damaged_road': {
      const w = laneW * 0.3;
      const h = w * 0.3;
      ctx.strokeStyle = 'rgba(30, 24, 20, 0.95)';
      ctx.lineWidth = 1.4;
      for (let i = 0; i < 5; i++) {
        ctx.beginPath();
        let px = cx - w / 2;
        let py = y - h / 2 + (h / 4) * i;
        ctx.moveTo(px, py);
        for (let s = 0; s < 4; s++) {
          px += w / 4;
          py += (((seed * 900 + i * 31 + s * 17) % 10) - 5) * 0.5;
          ctx.lineTo(px, py);
        }
        ctx.stroke();
      }
      ctx.fillStyle = 'rgba(10, 8, 6, 0.4)';
      ctx.fillRect(cx - w / 2, y - h / 2, w, h);
      return [cx - w * 0.6, y - h * 0.85, w * 1.2, h * 1.7];
    }

    case 'waterlogging': {
      const rx = laneW * 0.34;
      const ry = rx * 0.26;
      const grad = ctx.createLinearGradient(0, y - ry, 0, y + ry);
      grad.addColorStop(0, 'rgba(56, 189, 248, 0.42)');
      grad.addColorStop(1, 'rgba(14, 60, 90, 0.62)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.ellipse(cx, y, rx, ry, 0, 0, Math.PI * 2);
      ctx.fill();
      // Specular streaks read as a wet surface.
      ctx.strokeStyle = 'rgba(190, 230, 255, 0.5)';
      ctx.lineWidth = 0.9;
      for (let i = 0; i < 3; i++) {
        const yy = y - ry * 0.4 + i * ry * 0.45;
        ctx.beginPath();
        ctx.moveTo(cx - rx * (0.6 - i * 0.15), yy);
        ctx.lineTo(cx + rx * (0.4 - i * 0.1), yy);
        ctx.stroke();
      }
      return [cx - rx * 1.12, y - ry * 2.4, rx * 2.24, ry * 4.8];
    }

    case 'missing_divider': {
      // Median segments present either side, conspicuously absent in the middle.
      ctx.fillStyle = 'rgba(190, 196, 205, 0.55)';
      const mid = (left + right) / 2;
      for (const side of [-1, 1]) {
        for (let i = 1; i <= 2; i++) {
          const w = laneW * 0.05;
          const h = laneW * 0.035;
          ctx.fillRect(mid + side * laneW * (0.1 + i * 0.1) - w / 2, y - h, w, h * 2);
        }
      }
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(mid - laneW * 0.08, y);
      ctx.lineTo(mid + laneW * 0.08, y);
      ctx.stroke();
      ctx.setLineDash([]);
      return [mid - laneW * 0.13, y - laneW * 0.07, laneW * 0.26, laneW * 0.14];
    }

    case 'missing_zebra': {
      // Ghost stripes: worn to near-invisible, which is the actual failure mode.
      const d = 0.58;
      const [l, r] = roadEdges(d);
      const yy = yAtDepth(d);
      const barH = (r - l) * 0.05;
      ctx.fillStyle = 'rgba(200, 210, 225, 0.12)';
      for (let i = 0; i < 7; i++) {
        const w = (r - l) * 0.07;
        ctx.fillRect(l + (r - l) * (0.08 + i * 0.13), yy - barH / 2, w, barH);
      }
      return [l + (r - l) * 0.04, yy - barH * 1.9, (r - l) * 0.92, barH * 3.8];
    }

    case 'damaged_sign': {
      // Roadside post with the plate hanging off true.
      const px = right + laneW * 0.06;
      const topY = y - laneW * 0.42;
      ctx.strokeStyle = 'rgba(150, 160, 175, 0.7)';
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(px, y);
      ctx.lineTo(px, topY);
      ctx.stroke();

      ctx.save();
      ctx.translate(px, topY);
      ctx.rotate(0.42);
      const s = laneW * 0.14;
      ctx.fillStyle = 'rgba(96, 104, 118, 0.85)';
      ctx.fillRect(-s / 2, -s, s, s);
      ctx.strokeStyle = 'rgba(200, 208, 220, 0.5)';
      ctx.lineWidth = 1;
      ctx.strokeRect(-s / 2, -s, s, s);
      ctx.restore();

      return [px - laneW * 0.14, topY - laneW * 0.2, laneW * 0.3, laneW * 0.34];
    }

    case 'pedestrian_risk': {
      // Two small figures stepping off the kerb.
      const px = left - laneW * 0.02;
      const h = laneW * 0.17;
      ctx.fillStyle = 'rgba(52, 211, 153, 0.85)';
      for (let i = 0; i < 2; i++) {
        const fx = px + i * h * 0.62;
        const fy = y - i * 2;
        ctx.beginPath();
        ctx.arc(fx, fy - h, h * 0.2, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillRect(fx - h * 0.11, fy - h * 0.78, h * 0.22, h * 0.78);
      }
      return [px - h * 0.4, y - h * 1.5, h * 1.8, h * 1.75];
    }

    case 'incident': {
      // Offending vehicle, with motion streaks behind it.
      const w = laneW * 0.3;
      const h = w * 0.62;
      const vx = cx;
      const vy = y - h * 0.35;
      ctx.fillStyle = '#1a222c';
      ctx.fillRect(vx - w / 2, vy - h / 2, w, h);
      ctx.fillStyle = '#0a1017';
      ctx.fillRect(vx - w * 0.36, vy - h * 0.3, w * 0.72, h * 0.34);
      ctx.fillStyle = 'rgba(255, 90, 70, 0.9)';
      ctx.fillRect(vx - w / 2, vy + h * 0.22, w * 0.16, h * 0.14);
      ctx.fillRect(vx + w * 0.34, vy + h * 0.22, w * 0.16, h * 0.14);

      ctx.strokeStyle = 'rgba(239, 68, 68, 0.45)';
      ctx.lineWidth = 1;
      for (let i = 1; i <= 3; i++) {
        ctx.beginPath();
        ctx.moveTo(vx - w * 0.6 - i * 7, vy - h * 0.2 + i * 4);
        ctx.lineTo(vx - w * 0.5 - i * 3, vy - h * 0.2 + i * 4);
        ctx.stroke();
      }
      return [vx - w * 0.62, vy - h * 0.78, w * 1.24, h * 1.6];
    }

    default:
      return [cx - 24, y - 14, 48, 28];
  }
}

/** Detection box with corner ticks and a class label chip. */
function drawBoundingBox(ctx, box, color, label) {
  const [x, y, w, h] = box;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  ctx.strokeRect(x, y, w, h);

  // Corner ticks: the visual convention that says "detector output".
  const t = Math.min(7, w * 0.28, h * 0.28);
  ctx.lineWidth = 2.2;
  ctx.beginPath();
  for (const [cx, cy, sx, sy] of [
    [x, y, 1, 1],
    [x + w, y, -1, 1],
    [x, y + h, 1, -1],
    [x + w, y + h, -1, -1],
  ]) {
    ctx.moveTo(cx + sx * t, cy);
    ctx.lineTo(cx, cy);
    ctx.lineTo(cx, cy + sy * t);
  }
  ctx.stroke();

  // Label chip. Flipped below the box if it would clip the top of the frame,
  // and slid left if it would run off the right edge - long class names like
  // "RASH DRIVING / HIT-AND-RUN" are wider than the box they annotate.
  ctx.font = '600 8px ui-monospace, monospace';
  const tw = ctx.measureText(label).width + 8;
  const above = y - 12 >= 2;
  const ly = above ? y - 11 : y + h + 1;
  const lx = Math.max(2, Math.min(x, FRAME_W - tw - 2));
  ctx.fillStyle = color;
  ctx.fillRect(lx, ly, tw, 10);
  ctx.fillStyle = '#05070b';
  ctx.fillText(label, lx + 4, ly + 7.5);
}

/** Camera-system overlay: identity, timecode, crosshair, scanlines, vignette. */
function drawOverlay(ctx, event) {
  ctx.font = '600 8px ui-monospace, monospace';
  ctx.fillStyle = 'rgba(226, 232, 240, 0.72)';
  ctx.fillText(`CAM 02 · FRONT-LEFT · 1080p`, 7, 12);

  const stamp = `${event.time}`;
  ctx.textAlign = 'right';
  ctx.fillText(stamp, FRAME_W - 7, 12);
  ctx.textAlign = 'left';

  // Recording indicator
  ctx.fillStyle = 'rgba(239, 68, 68, 0.92)';
  ctx.beginPath();
  ctx.arc(FRAME_W - 30, FRAME_H - 9, 2.6, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = 'rgba(226, 232, 240, 0.72)';
  ctx.fillText('REC', FRAME_W - 24, FRAME_H - 6);

  ctx.fillStyle = 'rgba(226, 232, 240, 0.5)';
  ctx.fillText(event.busId, 7, FRAME_H - 6);

  // Centre crosshair
  ctx.strokeStyle = 'rgba(226, 232, 240, 0.2)';
  ctx.lineWidth = 1;
  const mx = FRAME_W / 2;
  const my = FRAME_H / 2;
  ctx.beginPath();
  ctx.moveTo(mx - 5, my);
  ctx.lineTo(mx + 5, my);
  ctx.moveTo(mx, my - 5);
  ctx.lineTo(mx, my + 5);
  ctx.stroke();

  // Scanlines - subtle, every other line, or it turns into moire on a projector.
  ctx.fillStyle = 'rgba(0, 0, 0, 0.14)';
  for (let y = 0; y < FRAME_H; y += 2) ctx.fillRect(0, y, FRAME_W, 1);

  // Vignette
  const vig = ctx.createRadialGradient(
    mx,
    my,
    FRAME_H * 0.28,
    mx,
    my,
    FRAME_W * 0.62,
  );
  vig.addColorStop(0, 'rgba(0,0,0,0)');
  vig.addColorStop(1, 'rgba(0,0,0,0.42)');
  ctx.fillStyle = vig;
  ctx.fillRect(0, 0, FRAME_W, FRAME_H);
}

function renderFrame(canvas, event) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = FRAME_W * dpr;
  canvas.height = FRAME_H * dpr;
  canvas.style.aspectRatio = `${FRAME_W} / ${FRAME_H}`;

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, FRAME_W, FRAME_H);

  const seed = hashString(event.id);
  drawBackdrop(ctx, seed);
  const box = drawDefect(ctx, event.typeId, seed, event.type.css);
  drawBoundingBox(
    ctx,
    box,
    event.type.css,
    `${event.type.label.toUpperCase()} ${event.confidence.toFixed(2)}`,
  );
  drawOverlay(ctx, event);
}

/* ────────────────────────────────────────────────────────────────────────────
 * The card itself
 * ──────────────────────────────────────────────────────────────────────────── */

function field(label, value, wide = false) {
  const node = el('div', 'field' + (wide ? ' is-wide' : ''));
  node.append(el('div', 'eyebrow', label), el('div', 'field-value', value));
  return node;
}

export function createDetailCard(root, { onClose }) {
  const card = el('aside');
  card.id = 'detail';
  card.classList.add('is-closed');

  const head = el('div', 'detail-head');
  const accent = el('div', 'detail-accent');
  const title = el('div', 'detail-title');
  const heading = el('h2', undefined, '');
  const subtitle = el('p', undefined, '');
  title.append(heading, subtitle);
  const chip = el('span', 'chip', '');
  const close = el('button', 'detail-close', '×');
  close.setAttribute('aria-label', 'Close evidence card');
  head.append(accent, title, chip, close);

  const canvas = el('canvas', 'detail-frame');

  const body = el('div', 'detail-body');
  const confRow = el('div', 'conf-row');
  confRow.append(el('span', 'eyebrow', 'Confidence'));
  const bar = el('div', 'conf-bar');
  const barFill = el('div', 'conf-fill');
  bar.append(barFill);
  const confValue = el('span', 'conf-value', '');
  confRow.append(bar, confValue);

  const grid = el('div', 'field-grid');
  body.append(confRow, grid);

  card.append(head, canvas, body);
  root.append(card);

  close.addEventListener('click', () => {
    hide();
    onClose?.();
  });

  let open = false;

  function show(event) {
    const sevColor = SEVERITY_CSS[event.severity];

    accent.style.background = event.type.css;
    heading.textContent = event.label;
    subtitle.textContent = `${event.type.category} · Event #${String(event.no).padStart(4, '0')}`;
    chip.textContent = event.severity;
    chip.style.color = sevColor;
    chip.style.background = `${sevColor}1f`;

    renderFrame(canvas, event);

    confValue.textContent = `${(event.confidence * 100).toFixed(1)}%`;
    confValue.style.color = sevColor;
    barFill.style.background = event.type.css;
    // Set from zero so the bar animates on every open.
    barFill.style.width = '0%';
    requestAnimationFrame(() => {
      barFill.style.width = `${(event.confidence * 100).toFixed(1)}%`;
    });

    grid.replaceChildren(
      field('Detected at', event.time),
      field('GPS', event.coords),
      field('Source bus', event.busId),
      field('Route', `${event.routeId} · ${event.routeName}`),
    );

    if (event.plate) {
      const plate = el('div', 'plate');
      plate.append(el('div', 'eyebrow', 'Registration mark extracted'));
      plate.append(el('div', 'plate-value', event.plate));
      plate.append(
        el(
          'div',
          'plate-meta',
          `OCR confidence ${(event.plateConfidence * 100).toFixed(1)}% · est. speed ${event.speedKph} km/h · evidence clip retained`,
        ),
      );
      grid.append(plate);
      plate.style.gridColumn = '1 / -1';
    }

    grid.append(field('Recommended action', event.type.action, true));

    card.classList.add('is-open');
    card.classList.remove('is-closed');
    open = true;
  }

  function hide() {
    card.classList.remove('is-open');
    card.classList.add('is-closed');
    open = false;
  }

  return { show, hide, isOpen: () => open };
}
