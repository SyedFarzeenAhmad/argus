import * as THREE from 'three';
import { COLOR } from '../core/theme.js';
import { range } from '../city/layout.js';

// Buses are drawn ~40% oversized. At the distance a viewer actually orbits
// from, a to-scale 11 m bus is a few pixels wide. Exaggerating the subject
// while keeping its *position* exact is standard map-visualisation practice.
const SCALE = 1.4;
const LEN = 11 * SCALE;
const WID = 2.55 * SCALE;
const HGT = 3.05 * SCALE;
const WHEEL_Y = 0.42 * SCALE;

const FOV_RANGE = 30;
const FOV_HALF_ANGLE = THREE.MathUtils.degToRad(29);

/**
 * Ground-projected camera footprint.
 *
 * Vertex colours ramp from the sensor colour at the apex to pure black at the
 * far arc. Under additive blending black adds nothing, so this fades out
 * perfectly without needing per-vertex alpha.
 */
function createFovSector(color) {
  const segments = 18;
  const verts = [0, 0, 0];
  const colors = [];
  const c = new THREE.Color(color);
  colors.push(c.r, c.g, c.b);

  for (let i = 0; i <= segments; i++) {
    const a = -FOV_HALF_ANGLE + (i / segments) * FOV_HALF_ANGLE * 2;
    // Bus forward is +Z (see orientation note in updateBus).
    verts.push(Math.sin(a) * FOV_RANGE, 0, Math.cos(a) * FOV_RANGE);
    colors.push(0, 0, 0);
  }

  const indices = [];
  for (let i = 1; i <= segments; i++) indices.push(0, i, i + 1);

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.setIndex(indices);

  return new THREE.Mesh(
    geo,
    new THREE.MeshBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.3,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      side: THREE.DoubleSide,
      toneMapped: false,
    }),
  );
}

function createBusMesh(routeColor) {
  const g = new THREE.Group();

  const body = new THREE.Mesh(
    new THREE.BoxGeometry(WID, HGT, LEN),
    new THREE.MeshStandardMaterial({
      color: COLOR.busBody,
      roughness: 0.45,
      metalness: 0.3,
    }),
  );
  body.position.y = WHEEL_Y + HGT / 2;
  g.add(body);

  // Glazing band: a slightly oversized dark box intersecting the body reads as
  // a continuous window strip for free, no UV work required.
  const glass = new THREE.Mesh(
    new THREE.BoxGeometry(WID + 0.06, HGT * 0.34, LEN * 0.9),
    new THREE.MeshStandardMaterial({ color: 0x08131c, roughness: 0.18, metalness: 0.6 }),
  );
  glass.position.y = WHEEL_Y + HGT * 0.66;
  g.add(glass);

  // Route livery stripe - colour-codes each vehicle to its line at a glance.
  const stripe = new THREE.Mesh(
    new THREE.BoxGeometry(WID + 0.1, 0.34 * SCALE, LEN * 0.94),
    new THREE.MeshBasicMaterial({ color: routeColor, toneMapped: false }),
  );
  stripe.position.y = WHEEL_Y + HGT * 0.3;
  g.add(stripe);

  // Headlights (+Z, forward) and tail lights (-Z).
  const head = new THREE.Mesh(
    new THREE.BoxGeometry(WID * 0.82, 0.3 * SCALE, 0.18),
    new THREE.MeshBasicMaterial({ color: 0xdff2ff, toneMapped: false }),
  );
  head.position.set(0, WHEEL_Y + HGT * 0.28, LEN / 2 + 0.05);
  g.add(head);

  const tail = new THREE.Mesh(
    new THREE.BoxGeometry(WID * 0.82, 0.26 * SCALE, 0.18),
    new THREE.MeshBasicMaterial({ color: 0xff4444, toneMapped: false }),
  );
  tail.position.set(0, WHEEL_Y + HGT * 0.28, -LEN / 2 - 0.05);
  g.add(tail);

  // Roof sensor pod - the physical justification for the whole platform, so
  // it is worth the two extra meshes to make it visible.
  const pod = new THREE.Mesh(
    new THREE.BoxGeometry(WID * 0.5, 0.42 * SCALE, 1.5 * SCALE),
    new THREE.MeshStandardMaterial({ color: 0x0e1620, roughness: 0.5 }),
  );
  pod.position.set(0, WHEEL_Y + HGT + 0.2 * SCALE, LEN * 0.24);
  g.add(pod);

  const podLight = new THREE.Mesh(
    new THREE.SphereGeometry(0.24 * SCALE, 8, 6),
    new THREE.MeshBasicMaterial({ color: COLOR.busSensor, toneMapped: false }),
  );
  podLight.position.set(0, WHEEL_Y + HGT + 0.52 * SCALE, LEN * 0.24);
  g.add(podLight);

  const fov = createFovSector(COLOR.busSensor);
  fov.position.set(0, 0.36, LEN / 2);
  g.add(fov);

  return { group: g, body, stripe, podLight, fov };
}

const FLEET_PREFIX = 'KA-01-F';

/**
 * Instantiate the fleet and return an updater.
 *
 * Buses advance by arc length along their route curve, so a bus travels at a
 * constant ground speed regardless of how the control points are spaced.
 */
export function buildFleet(layout, routes, perRoute = 3) {
  const { rng } = layout;
  const group = new THREE.Group();
  group.name = 'fleet';
  const buses = [];

  let serial = 1012;

  routes.forEach((route, ri) => {
    for (let i = 0; i < perRoute; i++) {
      const parts = createBusMesh(route.color);
      group.add(parts.group);

      const bus = {
        id: `${FLEET_PREFIX}${serial++}`,
        route,
        routeId: route.id,
        routeName: route.name,
        // Evenly spaced along the loop, with a seeded nudge so the fleet does
        // not look mechanically phased.
        t: (i / perRoute + rng() * 0.04) % 1,
        speed: range(rng, 11.5, 16.5), // metres per second
        odometer: 0,
        parts,
        position: new THREE.Vector3(),
        heading: new THREE.Vector3(0, 0, 1),
        alert: 0,
      };
      buses.push(bus);
    }
  });

  const scratchTarget = new THREE.Vector3();
  const alertColor = new THREE.Color(COLOR.busAlert);
  const normalColor = new THREE.Color(COLOR.busBody);

  function update(dt) {
    for (const bus of buses) {
      const travelled = bus.speed * dt;
      bus.t = (bus.t + travelled / bus.route.length) % 1;
      bus.odometer += travelled;

      bus.route.curve.getPointAt(bus.t, bus.position);
      bus.route.curve.getTangentAt(bus.t, bus.heading);

      bus.parts.group.position.copy(bus.position);
      // For a non-camera Object3D, lookAt aims local +Z at the target - which
      // is why the body, headlights and FOV cone are all built facing +Z.
      scratchTarget.copy(bus.position).add(bus.heading);
      bus.parts.group.lookAt(scratchTarget);

      // Alert decay: the incident beat flashes the offending vehicle, then it
      // settles back to normal livery on its own.
      if (bus.alert > 0) {
        bus.alert = Math.max(0, bus.alert - dt / 6);
        const k = Math.sin(bus.alert * 26) * 0.5 + 0.5;
        bus.parts.body.material.color
          .copy(normalColor)
          .lerp(alertColor, bus.alert * k);
      }
    }
  }

  function flagBus(bus) {
    bus.alert = 1;
  }

  return { group, buses, update, flagBus };
}
