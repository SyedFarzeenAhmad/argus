import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { COLOR, WORLD } from '../core/theme.js';

/**
 * Depth-layer plan.
 *
 * Every road surface is a coplanar quad, so anywhere two roads cross we would
 * get z-fighting. Rather than reach for polygonOffset (which is fiddly and
 * driver-dependent), each class of road gets its own hair-thin elevation. The
 * separation is invisible at any camera distance we allow but is far more than
 * the depth buffer needs to resolve cleanly.
 */
const LAYER = {
  streetX: WORLD.roadY,
  streetZ: WORLD.roadY + 0.04,
  arterialX: WORLD.roadY + 0.08,
  arterialZ: WORLD.roadY + 0.12,
  marking: WORLD.markingY,
};

/** A flat quad on the ground plane. */
function quad(w, d, x, y, z) {
  const g = new THREE.PlaneGeometry(w, d);
  g.rotateX(-Math.PI / 2);
  g.translate(x, y, z);
  return g;
}

function surfaceMesh(geometries, color) {
  const merged = mergeGeometries(geometries);
  return new THREE.Mesh(
    merged,
    new THREE.MeshStandardMaterial({ color, roughness: 0.95, metalness: 0.0 }),
  );
}

export function buildRoads(layout) {
  const { xRoads, zRoads, half } = layout;
  const span = half * 2 + 40; // overshoot the extent so roads run off-frame
  const group = new THREE.Group();
  group.name = 'roads';

  // --- carriageways -------------------------------------------------------
  const buckets = { streetX: [], streetZ: [], arterialX: [], arterialZ: [] };

  for (const r of xRoads) {
    const key = r.arterial ? 'arterialX' : 'streetX';
    buckets[key].push(quad(r.width, span, r.pos, LAYER[key], 0));
  }
  for (const r of zRoads) {
    const key = r.arterial ? 'arterialZ' : 'streetZ';
    buckets[key].push(quad(span, r.width, 0, LAYER[key], r.pos));
  }

  group.add(surfaceMesh(buckets.streetX, COLOR.roadSurface));
  group.add(surfaceMesh(buckets.streetZ, COLOR.roadSurface));
  group.add(surfaceMesh(buckets.arterialX, COLOR.roadArterial));
  group.add(surfaceMesh(buckets.arterialZ, COLOR.roadArterial));

  // --- centre-line dashes on arterials only -------------------------------
  const dashes = [];
  const DASH_LEN = 5;
  const DASH_GAP = 13;

  const nearPerpendicular = (value, perpRoads) =>
    perpRoads.some((p) => Math.abs(value - p.pos) < p.width / 2 + 3.5);

  for (const r of xRoads) {
    if (!r.arterial) continue;
    for (let z = -half; z <= half; z += DASH_GAP) {
      // Markings stop short of junctions. Running a dashed line straight
      // through an intersection is the sort of detail that quietly signals
      // nobody looked closely at the render.
      if (nearPerpendicular(z, zRoads)) continue;
      dashes.push({ x: r.pos, z, sx: 0.5, sz: DASH_LEN });
    }
  }
  for (const r of zRoads) {
    if (!r.arterial) continue;
    for (let x = -half; x <= half; x += DASH_GAP) {
      if (nearPerpendicular(x, xRoads)) continue;
      dashes.push({ x, z: r.pos, sx: DASH_LEN, sz: 0.5 });
    }
  }

  group.add(
    instancedFlats(dashes, COLOR.roadMarking, LAYER.marking, 'dashes'),
  );

  // --- zebra crossings ----------------------------------------------------
  // Real crossings matter here: the platform is meant to flag *missing* zebra
  // crossings, so the scene needs visible ones for that absence to read.
  const bars = [];
  const crossings = [];
  const arterialsX = xRoads.filter((r) => r.arterial);
  const arterialsZ = zRoads.filter((r) => r.arterial);

  for (const rx of arterialsX) {
    for (const rz of arterialsZ) {
      crossings.push({ x: rx.pos, z: rz.pos });

      // Two crossings per junction on the x-road approaches. Bars are
      // elongated along the direction of travel and stepped across the road
      // width, which is how a real zebra is painted.
      for (const side of [-1, 1]) {
        const z = rz.pos + side * (rz.width / 2 + 4.2);
        for (let x = rx.pos - rx.width / 2 + 1; x < rx.pos + rx.width / 2 - 0.5; x += 1.15) {
          bars.push({ x, z, sx: 0.55, sz: 3.4 });
        }
      }
      // ...and two on the z-road approaches, rotated ninety degrees.
      for (const side of [-1, 1]) {
        const x = rx.pos + side * (rx.width / 2 + 4.2);
        for (let z = rz.pos - rz.width / 2 + 1; z < rz.pos + rz.width / 2 - 0.5; z += 1.15) {
          bars.push({ x, z, sx: 3.4, sz: 0.55 });
        }
      }
    }
  }

  group.add(instancedFlats(bars, 0x6d8499, LAYER.marking, 'zebras'));

  group.userData = {
    crossings,
    stats: { dashes: dashes.length, zebraBars: bars.length },
  };
  return group;
}

/**
 * One instanced draw call for a set of flat painted rectangles.
 * Unlit (MeshBasic) but deliberately kept below the bloom threshold, so road
 * paint stays crisp while genuine light sources glow.
 */
function instancedFlats(items, color, y, name) {
  const mesh = new THREE.InstancedMesh(
    new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ color, toneMapped: false }),
    Math.max(1, items.length),
  );
  mesh.name = name;
  const m = new THREE.Matrix4();
  items.forEach((it, i) => {
    m.makeScale(it.sx, 1, it.sz);
    m.setPosition(it.x, y, it.z);
    mesh.setMatrixAt(i, m);
  });
  mesh.count = items.length;
  mesh.instanceMatrix.needsUpdate = true;
  return mesh;
}
