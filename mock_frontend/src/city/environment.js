import * as THREE from 'three';
import { COLOR, WORLD } from '../core/theme.js';
import { range } from './layout.js';

const GROUND_SIZE = 2600; // far larger than the city, so the map has no visible edge

/**
 * Build the river as a ribbon that follows the layout's centreline.
 *
 * Sits *below* the road layer on purpose: the arterials then read as bridges
 * crossing it, which costs nothing and adds a surprising amount of legibility
 * to the aerial view.
 */
function buildRiver(layout) {
  const { points, halfWidth } = layout.river;
  const verts = [];
  const indices = [];
  const y = WORLD.roadY - 0.03;

  for (let i = 0; i < points.length; i++) {
    const prev = points[Math.max(0, i - 1)];
    const next = points[Math.min(points.length - 1, i + 1)];
    // Perpendicular to the local tangent, in the ground plane.
    const tx = next.x - prev.x;
    const tz = next.z - prev.z;
    const len = Math.hypot(tx, tz) || 1;
    const nx = -tz / len;
    const nz = tx / len;

    const p = points[i];
    verts.push(p.x + nx * halfWidth, y, p.z + nz * halfWidth);
    verts.push(p.x - nx * halfWidth, y, p.z - nz * halfWidth);
  }
  for (let i = 0; i < points.length - 1; i++) {
    const a = i * 2;
    indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();

  return new THREE.Mesh(
    geo,
    new THREE.MeshStandardMaterial({
      color: COLOR.water,
      roughness: 0.14, // low roughness + some metalness reads as wet
      metalness: 0.45,
      side: THREE.DoubleSide,
    }),
  );
}

/** Flat green plots plus a scatter of low-poly trees, in two draw calls. */
function buildParks(layout) {
  const { blocks, rng } = layout;
  const parks = blocks.filter((b) => b.kind === 'park');
  const group = new THREE.Group();
  group.name = 'parks';
  if (!parks.length) return group;

  const plotGeo = new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2);
  const plots = new THREE.InstancedMesh(
    plotGeo,
    new THREE.MeshStandardMaterial({ color: 0x0c1a15, roughness: 0.95 }),
    parks.length,
  );

  const trees = [];
  const m = new THREE.Matrix4();

  parks.forEach((p, i) => {
    m.makeScale(p.w, 1, p.d);
    m.setPosition(p.cx, WORLD.roadY - 0.01, p.cz);
    plots.setMatrixAt(i, m);

    const count = Math.round(THREE.MathUtils.clamp((p.w * p.d) / 90, 3, 14));
    for (let t = 0; t < count; t++) {
      trees.push({
        x: p.cx + range(rng, -p.w / 2 + 2, p.w / 2 - 2),
        z: p.cz + range(rng, -p.d / 2 + 2, p.d / 2 - 2),
        s: range(rng, 1.4, 3.1),
      });
    }
  });
  plots.instanceMatrix.needsUpdate = true;
  group.add(plots);

  const treeMesh = new THREE.InstancedMesh(
    new THREE.IcosahedronGeometry(1, 0),
    new THREE.MeshStandardMaterial({ color: 0x152a1f, roughness: 0.9, flatShading: true }),
    trees.length,
  );
  trees.forEach((t, i) => {
    m.makeScale(t.s, t.s * 1.35, t.s);
    m.setPosition(t.x, t.s * 1.1, t.z);
    treeMesh.setMatrixAt(i, m);
  });
  treeMesh.instanceMatrix.needsUpdate = true;
  group.add(treeMesh);

  return group;
}

export function buildEnvironment(layout) {
  const group = new THREE.Group();
  group.name = 'environment';

  // --- ground -------------------------------------------------------------
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(GROUND_SIZE, GROUND_SIZE).rotateX(-Math.PI / 2),
    new THREE.MeshStandardMaterial({ color: COLOR.ground, roughness: 1 }),
  );
  ground.position.y = WORLD.groundY;
  group.add(ground);

  // --- reference grid -----------------------------------------------------
  // Two grids at different scales. Extending them well past the built area is
  // what makes the scene read as a GIS canvas rather than a floating diorama.
  const minor = new THREE.GridHelper(GROUND_SIZE, GROUND_SIZE / 20, COLOR.gridMinor, COLOR.gridMinor);
  minor.material.transparent = true;
  minor.material.opacity = 0.32;
  minor.position.y = WORLD.groundY + 0.015;
  group.add(minor);

  const major = new THREE.GridHelper(GROUND_SIZE, GROUND_SIZE / 100, COLOR.gridMajor, COLOR.gridMajor);
  major.material.transparent = true;
  major.material.opacity = 0.5;
  major.position.y = WORLD.groundY + 0.025;
  group.add(major);

  group.add(buildRiver(layout));
  group.add(buildParks(layout));

  // --- lighting -----------------------------------------------------------
  // Deliberately simple: a cool hemisphere for ambient fill plus one
  // directional for form. On a near-black scene the emissive markers and
  // rooftop lights do the dramatic work, so heavy lighting only muddies it.
  group.add(new THREE.HemisphereLight(0x39506e, 0x080d14, 0.85));

  const sun = new THREE.DirectionalLight(0xb8d4ef, 1.05);
  sun.position.set(220, 300, 140);
  group.add(sun);

  const rim = new THREE.DirectionalLight(0x3d78a8, 0.45);
  rim.position.set(-180, 120, -220);
  group.add(rim);

  return group;
}
