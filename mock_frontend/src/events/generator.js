import * as THREE from 'three';
import { TIMING } from '../core/theme.js';
import { range } from '../city/layout.js';
import {
  buildTypeQuota,
  BY_ID,
  SEVERITY_RANK,
  mockPlate,
  formatLatLon,
} from './catalog.js';

const SITES_PER_ROUTE = 9;
const DETECT_RANGE = 26; // matches the visual FOV cone in buses.js
const DETECT_DOT = 0.34; // roughly "inside the cone", not merely nearby

// Uplink pacing. Detections are found far faster than this; the queue is what
// turns a 20-second burst into a continuous 90-second feed.
const UPLINK_START = 5.5; // hold transmission until the intro camera settles
const UPLINK_INTERVAL = [1.5, 2.6];

/**
 * Scatter defect sites along each route, slightly off the bus's own path so
 * they read as roadside or carriageway features rather than points on a rail.
 */
function placeSites(layout, routes) {
  const { rng } = layout;
  const sites = [];
  const byRoute = new Map();
  let n = 1;

  // One stratified bag for the whole fleet, consumed as sites are placed. This
  // guarantees the detection mix is representative on every run rather than
  // merely representative in expectation.
  const bag = buildTypeQuota(rng, routes.length * SITES_PER_ROUTE);
  let bagIndex = 0;

  for (const route of routes) {
    const list = [];
    for (let i = 0; i < SITES_PER_ROUTE; i++) {
      // Even spacing with jitter: guarantees coverage of the whole loop while
      // avoiding a visibly regular necklace of pins.
      const t = (i / SITES_PER_ROUTE + range(rng, 0.01, 0.09)) % 1;
      const p = route.curve.getPointAt(t);
      const tan = route.curve.getTangentAt(t);
      const nx = -tan.z;
      const nz = tan.x;
      const len = Math.hypot(nx, nz) || 1;
      const lateral = range(rng, -5.5, 5.5);

      const type = bag[bagIndex++ % bag.length];
      const site = {
        id: `S${String(n++).padStart(3, '0')}`,
        type,
        position: new THREE.Vector3(
          p.x + (nx / len) * lateral,
          0,
          p.z + (nz / len) * lateral,
        ),
        routeId: route.id,
        confidence: range(rng, type.confidence[0], type.confidence[1]),
        detected: false,
      };
      sites.push(site);
      list.push(site);
    }
    byRoute.set(route.id, list);
  }
  return { sites, byRoute };
}

/**
 * The simulation.
 *
 * Everything downstream of `subscribe` receives DetectionEvents and has no idea
 * they are synthetic. Swapping this module for a WebSocket client is the whole
 * of the work required to make the visualiser live.
 */
export function createSimulation({ layout, routes, fleet }) {
  const { sites, byRoute } = placeSites(layout, routes);
  const { rng } = layout;

  const listeners = { event: [], reset: [] };
  const subscribe = (fn) => listeners.event.push(fn);
  const onReset = (fn) => listeners.reset.push(fn);

  const state = {
    simTime: 0,
    loopCount: 0,
    paused: false,
    queue: [],
    emitted: [],
    nextUplinkAt: UPLINK_START,
    eventNo: 1,
    incidentFired: false,
    confidenceSum: 0,
    alertsSent: 0,
  };

  const scratch = new THREE.Vector3();

  /** Proximity test: is this site inside the bus's forward sensing cone? */
  function senses(bus, site) {
    scratch.subVectors(site.position, bus.position);
    const dist = scratch.length();
    if (dist > DETECT_RANGE) return false;
    if (dist < 0.001) return true;
    return scratch.normalize().dot(bus.heading) > DETECT_DOT;
  }

  function makeEvent(site, bus, priority = false) {
    const now = new Date();
    const ev = {
      no: state.eventNo++,
      id: `${site.id}-${state.loopCount}`,
      typeId: site.type.id,
      type: site.type,
      label: site.type.label,
      severity: site.type.severity,
      position: site.position.clone(),
      coords: formatLatLon(site.position.x, site.position.z),
      busId: bus.id,
      routeId: bus.routeId,
      routeName: bus.routeName,
      confidence: site.confidence,
      detectedAt: state.simTime,
      timestamp: now,
      time: now.toLocaleTimeString('en-GB', { hour12: false }),
      priority,
    };

    // Incidents carry the registration read the brief specifically asks for,
    // with its own independent confidence score.
    if (site.type.id === 'incident') {
      ev.plate = mockPlate(rng);
      ev.plateConfidence = range(rng, 0.83, 0.96);
      ev.speedKph = Math.round(range(rng, 68, 94));
    }
    return ev;
  }

  function emit(ev) {
    ev.uplinkedAt = state.simTime;
    state.emitted.push(ev);
    state.confidenceSum += ev.confidence;
    if (ev.severity === 'high' || ev.severity === 'critical') state.alertsSent++;
    for (const fn of listeners.event) fn(ev);
  }

  function reset() {
    for (const s of sites) s.detected = false;
    state.queue.length = 0;
    state.emitted.length = 0;
    state.simTime = 0;
    state.nextUplinkAt = UPLINK_START;
    state.incidentFired = false;
    state.confidenceSum = 0;
    state.alertsSent = 0;
    state.loopCount++;
    for (const fn of listeners.reset) fn();
  }

  const AGING_RATE = 0.3; // severity ranks gained per second of waiting

  /**
   * Pull the next event off the uplink queue by aging priority.
   *
   * Severity leads, so the most serious finding available is transmitted first
   * - which is what a genuinely bandwidth-constrained edge device should do.
   * The age term prevents starvation: after roughly seven seconds of waiting a
   * low-severity signboard outranks a freshly-found high-severity defect, so
   * nothing sits in the queue forever and the feed stays varied.
   */
  function dequeue() {
    let bestIndex = 0;
    let bestScore = -Infinity;
    for (let i = 0; i < state.queue.length; i++) {
      const ev = state.queue[i];
      const age = state.simTime - ev.detectedAt;
      const score = SEVERITY_RANK[ev.severity] + age * AGING_RATE;
      if (score > bestScore) {
        bestScore = score;
        bestIndex = i;
      }
    }
    return state.queue.splice(bestIndex, 1)[0];
  }

  /** One fixed-size simulation tick. Never called with a variable dt. */
  function step(dt) {
    state.simTime += dt;
    fleet.update(dt);

    // --- edge detection: proximity, per bus, against its own route's sites --
    for (const bus of fleet.buses) {
      const list = byRoute.get(bus.routeId);
      if (!list) continue;
      for (const site of list) {
        if (site.detected) continue;
        if (!senses(bus, site)) continue;
        site.detected = true;
        state.queue.push(makeEvent(site, bus));
      }
    }

    // --- scripted incident beat -------------------------------------------
    if (!state.incidentFired && state.simTime >= TIMING.incidentAt) {
      state.incidentFired = true;
      const bus = fleet.buses[Math.min(3, fleet.buses.length - 1)];
      const site = {
        id: `INC${state.loopCount}`,
        type: BY_ID.incident,
        position: bus.position
          .clone()
          .add(bus.heading.clone().multiplyScalar(16)),
        routeId: bus.routeId,
        confidence: range(rng, 0.91, 0.97),
        detected: true,
      };
      fleet.flagBus(bus);
      // Critical alerts pre-empt the queue - exactly how a bandwidth-managed
      // edge uplink should behave.
      emit(makeEvent(site, bus, true));
    }

    // --- bandwidth-limited uplink -----------------------------------------
    if (state.simTime >= state.nextUplinkAt && state.queue.length) {
      emit(dequeue());
      state.nextUplinkAt =
        state.simTime + range(rng, UPLINK_INTERVAL[0], UPLINK_INTERVAL[1]);
    }

    if (state.simTime >= TIMING.loop) reset();
  }

  let accumulator = 0;

  /**
   * Advance the simulation by real elapsed time, consumed in fixed slices.
   *
   * This is the single most important decision for demo safety: event timing is
   * identical on a 144 Hz desktop and a struggling 30 fps laptop, so the beat
   * sheet can be rehearsed to the second.
   */
  function update(dt) {
    if (state.paused) return;
    accumulator += dt;
    let guard = 0;
    while (accumulator >= TIMING.step && guard++ < 240) {
      step(TIMING.step);
      accumulator -= TIMING.step;
    }
  }

  function stats() {
    const km = fleet.buses.reduce((s, b) => s + b.odometer, 0) / 1000;
    return {
      busesOnline: fleet.buses.length,
      kmCovered: km,
      defectsLogged: state.emitted.length,
      alertsSent: state.alertsSent,
      queueDepth: state.queue.length,
      avgConfidence: state.emitted.length
        ? state.confidenceSum / state.emitted.length
        : 0,
      simTime: state.simTime,
      loopProgress: state.simTime / TIMING.loop,
      paused: state.paused,
    };
  }

  return {
    update,
    subscribe,
    onReset,
    stats,
    sites,
    state,
    togglePause() {
      state.paused = !state.paused;
      return state.paused;
    },
  };
}
