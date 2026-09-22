import * as THREE from 'three';
import { COLOR } from '../core/theme.js';
import { range } from './layout.js';

/**
 * A unit cube, re-based so its footprint sits on y=0 and it grows upward, with
 * a vertical colour gradient baked into its vertex colours.
 *
 * Baking the gradient into the geometry (rather than into a shader or into one
 * material per building) is what lets several hundred towers share a single
 * InstancedMesh and still read as atmospheric: dark at street level, lighter at
 * roof level, which is the cue the eye uses to judge height on a dark map.
 */
function createGradientBox() {
  const geo = new THREE.BoxGeometry(1, 1, 1);
  geo.translate(0, 0.5, 0); // base at origin so instance scale.y == height

  const base = new THREE.Color(COLOR.buildingBase);
  const top = new THREE.Color(COLOR.buildingTop);
  const pos = geo.attributes.position;
  const colors = new Float32Array(pos.count * 3);
  const c = new THREE.Color();

  for (let i = 0; i < pos.count; i++) {
    // Bias the ramp so the brightening happens in the upper half - a linear
    // ramp washes out the ground floors and flattens the skyline.
    const t = Math.pow(THREE.MathUtils.clamp(pos.getY(i), 0, 1), 0.72);
    c.copy(base).lerp(top, t);
    colors[i * 3] = c.r;
    colors[i * 3 + 1] = c.g;
    colors[i * 3 + 2] = c.b;
  }
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  return geo;
}

// Blocks wider or deeper than this are split in half on that axis. Tuned
// empirically: at 32 the city yielded only ~124 buildings and read as sparse,
// at 18 it yields ~287 plots with no plot falling below the minimum size.
const SUBDIVIDE_ABOVE = 18;

/** Split a block into 1-4 plots, then apply a random setback to each. */
function plotsFor(block, rng) {
  let cells = [{ x0: block.x0, z0: block.z0, x1: block.x1, z1: block.z1 }];

  if (block.w > SUBDIVIDE_ABOVE) {
    cells = cells.flatMap((c) => {
      const m = c.x0 + (c.x1 - c.x0) * range(rng, 0.38, 0.62);
      return [
        { ...c, x1: m - 1.6 },
        { ...c, x0: m + 1.6 },
      ];
    });
  }
  if (block.d > SUBDIVIDE_ABOVE) {
    cells = cells.flatMap((c) => {
      const m = c.z0 + (c.z1 - c.z0) * range(rng, 0.38, 0.62);
      return [
        { ...c, z1: m - 1.6 },
        { ...c, z0: m + 1.6 },
      ];
    });
  }

  const plots = [];
  for (const c of cells) {
    // Occasional vacant plot: breaks up the rhythm and lets the ground grid
    // show through, which stops the core looking like extruded graph paper.
    if (rng() < 0.08) continue;
    const inset = range(rng, 0.9, 2.6);
    const w = c.x1 - c.x0 - inset * 2;
    const d = c.z1 - c.z0 - inset * 2;
    if (w < 5 || d < 5) continue;
    plots.push({ cx: (c.x0 + c.x1) / 2, cz: (c.z0 + c.z1) / 2, w, d });
  }
  return plots;
}

/**
 * Build all built form as a single instanced draw call, plus a small second
 * call for rooftop aviation lights on the taller towers.
 */
export function buildBuildings(layout) {
  const { blocks, rng } = layout;
  const group = new THREE.Group();
  group.name = 'buildings';

  const instances = [];
  const roofs = [];
  const windows = [];

  for (const block of blocks) {
    if (block.kind !== 'built') continue;

    for (const plot of plotsFor(block, rng)) {
      // Height is driven by centrality so the city has a legible dense core
      // that fades to low-rise at the edges.
      //
      // These constants are tuned against the 460-unit city extent: they give a
      // median height around 22 and a 90th percentile around 66. An earlier
      // exponent of 1.85 crushed everything outside the very centre to a median
      // of 8, and the city read as a flat plane from any survey angle.
      const core = block.centrality;
      let h = (11 + core * 72) * range(rng, 0.62, 1.5);
      if (rng() < 0.035) h *= range(rng, 1.5, 2.3); // occasional landmark tower
      h = THREE.MathUtils.clamp(h, 6, 125);

      instances.push({ ...plot, h });
      if (h > 46) roofs.push({ cx: plot.cx, cz: plot.cz, h });

      // Lit windows. A handful per building, scattered over its four faces.
      // This is the single biggest contributor to the city reading as inhabited
      // rather than as extruded blocks, and it costs one extra draw call.
      const windowCount = Math.round(
        THREE.MathUtils.clamp(h / 9, 1, 7) * range(rng, 0.5, 1.4),
      );
      for (let w = 0; w < windowCount; w++) {
        const face = Math.floor(rng() * 4); // 0:+X 1:-X 2:+Z 3:-Z
        const alongX = face >= 2;
        const span = (alongX ? plot.w : plot.d) * 0.72;
        const off = range(rng, -span / 2, span / 2);
        const depth = (alongX ? plot.d : plot.w) / 2 + 0.06;
        windows.push({
          x: plot.cx + (alongX ? off : (face === 0 ? depth : -depth)),
          z: plot.cz + (alongX ? (face === 2 ? depth : -depth) : off),
          y: range(rng, h * 0.16, h * 0.92),
          rotY: alongX ? 0 : Math.PI / 2,
          warm: rng() < 0.62,
          bright: range(rng, 0.35, 1),
        });
      }
    }
  }

  // --- built form ---------------------------------------------------------
  const mesh = new THREE.InstancedMesh(
    createGradientBox(),
    new THREE.MeshStandardMaterial({
      vertexColors: true,
      roughness: 0.84,
      metalness: 0.06,
    }),
    instances.length,
  );

  const m = new THREE.Matrix4();
  const tint = new THREE.Color();
  instances.forEach((b, i) => {
    m.makeScale(b.w, b.h, b.d);
    m.setPosition(b.cx, 0, b.cz);
    mesh.setMatrixAt(i, m);

    // Per-instance tint multiplies the baked gradient. Small variance only -
    // enough to separate adjacent blocks, not enough to look patchwork.
    const v = range(rng, 0.82, 1.22);
    tint.setRGB(v * range(rng, 0.94, 1.02), v, v * range(rng, 0.98, 1.08));
    mesh.setColorAt(i, tint);
  });
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  mesh.castShadow = false;
  group.add(mesh);

  // --- rooftop lights -----------------------------------------------------
  // MeshBasicMaterial is deliberate: unlit geometry above the bloom threshold
  // glows, which is what makes them read as lights rather than beads.
  const roofMesh = new THREE.InstancedMesh(
    new THREE.SphereGeometry(0.62, 6, 5),
    new THREE.MeshBasicMaterial({ toneMapped: false }),
    Math.max(1, roofs.length),
  );
  const roofColor = new THREE.Color();
  roofs.forEach((r, i) => {
    m.makeTranslation(r.cx, r.h + 1.1, r.cz);
    roofMesh.setMatrixAt(i, m);
    const warm = rng() < 0.4;
    roofColor.setHex(warm ? 0xff7b6b : 0xbfe4ff).multiplyScalar(range(rng, 0.5, 0.95));
    roofMesh.setColorAt(i, roofColor);
  });
  roofMesh.count = roofs.length;
  roofMesh.instanceMatrix.needsUpdate = true;
  if (roofMesh.instanceColor) roofMesh.instanceColor.needsUpdate = true;
  group.add(roofMesh);

  // --- lit windows --------------------------------------------------------
  // A single instanced plane per window, unlit and just under the bloom
  // threshold at low brightness so only the brightest windows glow.
  const winMesh = new THREE.InstancedMesh(
    new THREE.PlaneGeometry(0.85, 0.62),
    new THREE.MeshBasicMaterial({
      vertexColors: false,
      toneMapped: false,
      side: THREE.DoubleSide,
    }),
    Math.max(1, windows.length),
  );
  const winColor = new THREE.Color();
  const q = new THREE.Quaternion();
  const up = new THREE.Vector3(0, 1, 0);
  const one = new THREE.Vector3(1, 1, 1);
  const at = new THREE.Vector3();

  windows.forEach((w, i) => {
    q.setFromAxisAngle(up, w.rotY);
    at.set(w.x, w.y, w.z);
    m.compose(at, q, one);
    winMesh.setMatrixAt(i, m);
    winColor
      .setHex(w.warm ? 0xffd9a0 : 0xbfe0ff)
      .multiplyScalar(w.bright * 0.85);
    winMesh.setColorAt(i, winColor);
  });
  winMesh.count = windows.length;
  winMesh.instanceMatrix.needsUpdate = true;
  if (winMesh.instanceColor) winMesh.instanceColor.needsUpdate = true;
  group.add(winMesh);

  group.userData.stats = {
    buildings: instances.length,
    roofLights: roofs.length,
    windows: windows.length,
  };
  return group;
}
