import * as THREE from 'three';
import { WORLD } from '../core/theme.js';

/** Muted, distinguishable route liveries. Route traces are wayfinding, not alerts. */
const LIVERIES = [
  { name: '12A Ring North', color: 0x4fa8d8 },
  { name: '27 City Central', color: 0x64c8a8 },
  { name: '5B Riverside', color: 0xc9a15e },
  { name: '41 East Corridor', color: 0x9d8ad8 },
  { name: '33 Market Loop', color: 0xd88a8a },
  { name: '8 Outer Belt', color: 0x7fb0c8 },
];

const LANE_OFFSET = 2.6; // metres left of centreline - buses keep left
const SAMPLE_STEP = 7; // waypoint spacing along each leg

/**
 * Score and select rectangular loops from the street grid.
 *
 * Arterials are strongly preferred (real bus routes follow major roads), but
 * ordinary streets stay eligible so the routes do not all stack onto the same
 * six carriageways.
 */
function pickRectangles(layout, count) {
  const { xRoads, zRoads, rng } = layout;
  const candidates = [];

  for (let i = 0; i < xRoads.length; i++) {
    for (let k = i + 1; k < xRoads.length; k++) {
      for (let j = 0; j < zRoads.length; j++) {
        for (let l = j + 1; l < zRoads.length; l++) {
          const w = xRoads[k].pos - xRoads[i].pos;
          const d = zRoads[l].pos - zRoads[j].pos;
          if (w < 95 || d < 95 || w > 330 || d > 330) continue;

          const arterials =
            (xRoads[i].arterial ? 1 : 0) +
            (xRoads[k].arterial ? 1 : 0) +
            (zRoads[j].arterial ? 1 : 0) +
            (zRoads[l].arterial ? 1 : 0);

          candidates.push({
            x0: xRoads[i].pos,
            x1: xRoads[k].pos,
            z0: zRoads[j].pos,
            z1: zRoads[l].pos,
            score: arterials * 2.4 + rng() * 3,
          });
        }
      }
    }
  }

  candidates.sort((a, b) => b.score - a.score);

  // Greedy spread: reject a high-scoring loop whose centre sits almost on top
  // of one already chosen, so the fleet covers the map instead of one corner.
  const chosen = [];
  for (const c of candidates) {
    if (chosen.length >= count) break;
    const cx = (c.x0 + c.x1) / 2;
    const cz = (c.z0 + c.z1) / 2;
    const tooClose = chosen.some(
      (o) => Math.hypot(cx - (o.x0 + o.x1) / 2, cz - (o.z0 + o.z1) / 2) < 46,
    );
    if (!tooClose) chosen.push(c);
  }
  return chosen;
}

/**
 * Convert a rectangle into a dense, lane-offset waypoint ring.
 *
 * Dense sampling matters: a Catmull-Rom through only four corners bulges into a
 * blob that leaves the road entirely. Sampling every few metres keeps the curve
 * pinned to the carriageway and rounds only the corners themselves - which is
 * exactly how a bus takes a junction.
 */
function waypointRing(rect) {
  const { x0, x1, z0, z1 } = rect;
  const o = LANE_OFFSET;
  const pts = [];
  const push = (x, z) => pts.push(new THREE.Vector3(x, 0, z));

  // Counter-clockwise, offset outward throughout, which puts the vehicle on
  // the left of its direction of travel on every leg.
  for (let x = x0; x < x1; x += SAMPLE_STEP) push(x, z0 - o); // +X
  for (let z = z0; z < z1; z += SAMPLE_STEP) push(x1 + o, z); // +Z
  for (let x = x1; x > x0; x -= SAMPLE_STEP) push(x, z1 + o); // -X
  for (let z = z1; z > z0; z -= SAMPLE_STEP) push(x0 - o, z); // -Z

  return pts;
}

export function buildRoutes(layout, count = 5) {
  const rects = pickRectangles(layout, count);

  return rects.map((rect, i) => {
    const curve = new THREE.CatmullRomCurve3(
      waypointRing(rect),
      true, // closed loop
      'catmullrom',
      0.35, // low tension: smooth corners without wandering off the road
    );
    const livery = LIVERIES[i % LIVERIES.length];
    return {
      id: `R${String(i + 1).padStart(2, '0')}`,
      name: livery.name,
      color: livery.color,
      curve,
      length: curve.getLength(),
      rect,
    };
  });
}

/**
 * Faint glowing ribbons tracing each route on the ground.
 *
 * Additive blending at low opacity means overlapping routes brighten where they
 * share a corridor - a free, legible density cue.
 */
export function buildRouteRibbons(routes, width = 1.7) {
  const group = new THREE.Group();
  group.name = 'routeRibbons';
  const y = WORLD.markingY + 0.06;

  for (const route of routes) {
    const samples = Math.max(120, Math.round(route.length / 4));
    const verts = [];
    const indices = [];

    for (let i = 0; i <= samples; i++) {
      const t = i / samples;
      const p = route.curve.getPointAt(t);
      const tan = route.curve.getTangentAt(t);
      // Perpendicular in the ground plane.
      const nx = -tan.z;
      const nz = tan.x;
      const len = Math.hypot(nx, nz) || 1;
      verts.push(p.x + (nx / len) * width, y, p.z + (nz / len) * width);
      verts.push(p.x - (nx / len) * width, y, p.z - (nz / len) * width);
    }
    for (let i = 0; i < samples; i++) {
      const a = i * 2;
      indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
    geo.setIndex(indices);

    group.add(
      new THREE.Mesh(
        geo,
        new THREE.MeshBasicMaterial({
          color: route.color,
          transparent: true,
          opacity: 0.2,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
          side: THREE.DoubleSide,
          toneMapped: false,
        }),
      ),
    );
  }
  return group;
}
