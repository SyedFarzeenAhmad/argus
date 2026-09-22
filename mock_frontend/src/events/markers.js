import * as THREE from 'three';

// Comfortably above the median building height (~22) so beacons read over
// ordinary streetscape, but well short of the towers - matching the tallest
// buildings would turn the city into a forest of pillars and the markers would
// stop reading as annotations on a map.
const SHAFT_HEIGHT = 34;
const DROP_DURATION = 0.42;
const PING_DURATION = 1.3;
// Roughly one city block across at full expansion. At 24 the shockwave grew
// to ~67 units diameter - a seventh of the whole city - and read as a bug.
const PING_MAX_RADIUS = 11;
const PICK_RADIUS = 6.5; // generous hit target - these are small on screen

/**
 * Geometry and material cache, keyed by detection type.
 *
 * Every marker of a given class shares its meshes' materials. Without this,
 * forty-odd markers would mean well over a hundred distinct materials, each of
 * which is a separate shader binding at draw time.
 */
function createCache() {
  const ringGeo = new THREE.RingGeometry(3.6, 4.7, 36).rotateX(-Math.PI / 2);
  const dotGeo = new THREE.SphereGeometry(0.85, 12, 10);
  const pingGeo = new THREE.RingGeometry(0.86, 1.3, 44).rotateX(-Math.PI / 2);
  const proxyGeo = new THREE.SphereGeometry(PICK_RADIUS, 8, 6);
  const byType = new Map();

  /**
   * A light beam: an open cylinder whose vertex colours ramp from the type
   * colour at the base to black at the tip. Under additive blending black
   * contributes nothing, so the beam fades upward with no alpha handling, and
   * the two visible walls sum through the centre to give a bright core.
   */
  function shaftGeometry() {
    const geo = new THREE.CylinderGeometry(0.34, 0.34, 1, 7, 1, true);
    geo.translate(0, 0.5, 0); // base at origin, so scale.y is the height
    return geo;
  }

  function forType(type) {
    if (byType.has(type.id)) return byType.get(type.id);

    const color = new THREE.Color(type.color);

    const shaft = shaftGeometry();
    const pos = shaft.attributes.position;
    const colors = new Float32Array(pos.count * 3);
    for (let i = 0; i < pos.count; i++) {
      const fade = 1 - Math.pow(THREE.MathUtils.clamp(pos.getY(i), 0, 1), 0.55);
      colors[i * 3] = color.r * fade;
      colors[i * 3 + 1] = color.g * fade;
      colors[i * 3 + 2] = color.b * fade;
    }
    shaft.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const additive = (opacity) =>
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        side: THREE.DoubleSide,
        toneMapped: false,
      });

    const entry = {
      shaftGeo: shaft,
      shaftMat: new THREE.MeshBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.72,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        side: THREE.DoubleSide,
        toneMapped: false,
      }),
      ringMat: additive(0.62),
      dotMat: new THREE.MeshBasicMaterial({ color, toneMapped: false }),
      color,
    };
    byType.set(type.id, entry);
    return entry;
  }

  return { ringGeo, dotGeo, pingGeo, proxyGeo, forType };
}

/**
 * The 3D marker layer.
 *
 * Subscribes to detection events and owns everything visual about them: the
 * drop animation, the idle pulse, selection highlight, hit testing, and the
 * fade-out when the demo loop restarts.
 */
export function createMarkerLayer(viewer) {
  const cache = createCache();
  const group = new THREE.Group();
  group.name = 'markers';
  viewer.scene.add(group);

  const markers = [];
  const pings = [];
  const proxies = []; // raycast targets only, never rendered
  const raycaster = new THREE.Raycaster();
  let selected = null;

  function add(event) {
    const type = cache.forType(event.type);
    const holder = new THREE.Group();
    holder.position.copy(event.position);

    const ring = new THREE.Mesh(cache.ringGeo, type.ringMat);
    ring.position.y = 0.34;
    holder.add(ring);

    const shaft = new THREE.Mesh(type.shaftGeo, type.shaftMat);
    shaft.scale.set(1, SHAFT_HEIGHT, 1);
    holder.add(shaft);

    const dot = new THREE.Mesh(cache.dotGeo, type.dotMat);
    dot.position.y = SHAFT_HEIGHT + 0.6;
    holder.add(dot);

    // Invisible pick proxy. Raycasting one sphere per marker is far cheaper and
    // far more forgiving than testing the beacon geometry itself.
    const proxy = new THREE.Mesh(
      cache.proxyGeo,
      new THREE.MeshBasicMaterial({ visible: false }),
    );
    proxy.position.copy(event.position);
    proxy.position.y = SHAFT_HEIGHT * 0.55;
    proxy.userData.event = event;
    group.add(proxy);
    proxies.push(proxy);

    group.add(holder);

    const marker = {
      event,
      holder,
      ring,
      shaft,
      dot,
      proxy,
      age: 0,
      drop: 0, // 0..1 drop-in progress
      dying: 0, // 0..1 fade-out progress
      phase: Math.random() * Math.PI * 2, // desynchronise the idle pulse
      selected: false,
    };
    markers.push(marker);
    event.marker = marker;

    spawnPing(event.position, type.color);
    return marker;
  }

  /** Expanding shockwave on the ground, marking the moment of detection. */
  function spawnPing(position, color) {
    const mesh = new THREE.Mesh(
      cache.pingGeo,
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0.9,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        side: THREE.DoubleSide,
        toneMapped: false,
      }),
    );
    mesh.position.copy(position);
    mesh.position.y = 0.3;
    group.add(mesh);
    pings.push({ mesh, t: 0 });
  }

  function select(event) {
    if (selected) selected.selected = false;
    selected = event?.marker ?? null;
    if (selected) selected.selected = true;
    return selected;
  }

  /** Begin fading everything out; markers are disposed once fully faded. */
  function clearAll() {
    for (const m of markers) if (!m.dying) m.dying = 0.0001;
    select(null);
  }

  function disposeMarker(m) {
    group.remove(m.holder);
    group.remove(m.proxy);
    m.proxy.material.dispose();
    const pi = proxies.indexOf(m.proxy);
    if (pi >= 0) proxies.splice(pi, 1);
    delete m.event.marker;
  }

  function update(dt, elapsed) {
    // --- markers ----------------------------------------------------------
    for (let i = markers.length - 1; i >= 0; i--) {
      const m = markers[i];
      m.age += dt;

      if (m.dying) {
        m.dying = Math.min(1, m.dying + dt / 0.9);
        const k = 1 - m.dying;
        m.holder.scale.setScalar(k);
        m.holder.visible = k > 0.01;
        if (m.dying >= 1) {
          disposeMarker(m);
          markers.splice(i, 1);
        }
        continue;
      }

      // Drop-in: the beam grows from the ground and the ring snaps outward.
      if (m.drop < 1) {
        m.drop = Math.min(1, m.drop + dt / DROP_DURATION);
        // easeOutBack gives the ring a slight overshoot, which reads as a
        // physical "snap" rather than a fade.
        const t = m.drop;
        const c = 1.9;
        const over = 1 + c * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2);
        m.shaft.scale.set(1, SHAFT_HEIGHT * t, 1);
        m.dot.position.y = SHAFT_HEIGHT * t + 0.6;
        m.ring.scale.setScalar(over);
        m.dot.scale.setScalar(over);
      }

      const sel = m.selected ? 1 : 0;
      // Idle breathing, plus a stronger, faster pulse when selected.
      const pulse = 1 + Math.sin(elapsed * (sel ? 5.2 : 1.9) + m.phase) * (sel ? 0.16 : 0.07);
      const target = (m.selected ? 1.45 : 1) * pulse;
      if (m.drop >= 1) {
        m.ring.scale.setScalar(THREE.MathUtils.lerp(m.ring.scale.x, target, 0.14));
        m.dot.scale.setScalar(THREE.MathUtils.lerp(m.dot.scale.x, target, 0.14));
      }
      m.ring.rotation.y += dt * (m.selected ? 0.9 : 0.18);
    }

    // --- pings ------------------------------------------------------------
    for (let i = pings.length - 1; i >= 0; i--) {
      const p = pings[i];
      p.t += dt / PING_DURATION;
      if (p.t >= 1) {
        group.remove(p.mesh);
        p.mesh.material.dispose();
        pings.splice(i, 1);
        continue;
      }
      // Radius eases out while opacity falls away faster, so the wave thins as
      // it travels instead of vanishing at full brightness.
      const e = 1 - Math.pow(1 - p.t, 2.2);
      p.mesh.scale.setScalar(1 + e * PING_MAX_RADIUS);
      p.mesh.material.opacity = 0.9 * Math.pow(1 - p.t, 1.6);
    }
  }

  /**
   * Hit test in normalised device coordinates.
   * Returns the detection event under the pointer, or null.
   */
  function pick(ndc) {
    if (!proxies.length) return null;
    raycaster.setFromCamera(ndc, viewer.camera);
    const hits = raycaster.intersectObjects(proxies, false);
    return hits.length ? hits[0].object.userData.event : null;
  }

  return { group, markers, add, update, pick, select, clearAll, get selected() { return selected; } };
}
