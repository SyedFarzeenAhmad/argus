import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { COLOR, WORLD } from './theme.js';

// A higher, further vantage than a natural "hero shot": the point of this view
// is reading a road network and where the markers cluster on it, which needs a
// steeper look-down than a cinematic three-quarter angle gives.
// Framed so the whole 460-unit city fits with margin, at a steep enough
// look-down (~42 degrees) that the road network and marker clusters read as a
// map. A shallower, more cinematic angle looks better in a still and is worse
// for the job this view actually does.
const HOME = {
  position: new THREE.Vector3(140, 470, 400),
  target: new THREE.Vector3(0, 0, -6),
};

const easeInOutCubic = (t) =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

/**
 * Owns the render stack: scene, camera, controls, post-processing and the RAF
 * loop. Everything else in the app is a consumer that registers a per-frame
 * callback and never touches the renderer directly.
 */
export function createViewer(container) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(COLOR.void);
  scene.fog = new THREE.Fog(COLOR.void, WORLD.fogNear, WORLD.fogFar);

  const camera = new THREE.PerspectiveCamera(
    46,
    container.clientWidth / container.clientHeight,
    0.5,
    2400,
  );
  camera.position.copy(HOME.position);

  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    powerPreference: 'high-performance',
  });
  renderer.setSize(container.clientWidth, container.clientHeight);
  // Capping DPR at 1.75 is the single cheapest perf lever on a 4K laptop
  // panel: visually near-identical, roughly half the fragment work.
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  container.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(HOME.target);
  controls.enableDamping = true;
  controls.dampingFactor = 0.055;
  controls.minDistance = 45;
  controls.maxDistance = 950;
  controls.maxPolarAngle = 1.32; // keep some look-down; the map stays readable
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.34;
  controls.update();

  // Kiosk behaviour: the camera drifts on its own, yields the moment someone
  // grabs it, and resumes after a few seconds of stillness. This is what makes
  // the demo look alive while unattended without fighting the presenter.
  let idleTimer = null;
  const resumeDrift = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      controls.autoRotate = true;
    }, 6000);
  };
  controls.addEventListener('start', () => {
    controls.autoRotate = false;
    clearTimeout(idleTimer);
  });
  controls.addEventListener('end', resumeDrift);

  const renderPass = new RenderPass(scene, camera);
  const bloomPass = new UnrealBloomPass(
    new THREE.Vector2(container.clientWidth, container.clientHeight),
    0.62, // strength - restrained, so only genuinely emissive things bloom
    0.55, // radius
    0.62, // threshold
  );
  const composer = new EffectComposer(renderer);
  composer.addPass(renderPass);
  composer.addPass(bloomPass);
  composer.addPass(new OutputPass());
  composer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
  composer.setSize(container.clientWidth, container.clientHeight);

  let bloomEnabled = true;

  function resize() {
    const w = container.clientWidth;
    const h = container.clientHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    composer.setSize(w, h);
    bloomPass.resolution.set(w, h);
  }
  window.addEventListener('resize', resize);

  // --- camera tweening ----------------------------------------------------
  let tween = null;

  function flyTo(position, target, duration = 1.25) {
    tween = {
      t: 0,
      duration,
      fromPos: camera.position.clone(),
      toPos: position.clone(),
      fromTarget: controls.target.clone(),
      toTarget: target.clone(),
    };
    controls.autoRotate = false;
    clearTimeout(idleTimer);
  }

  /** Frame a point of interest from a pleasant standing distance. */
  function focusOn(point, distance = 88) {
    const offset = new THREE.Vector3(0.62, 0.66, 0.72)
      .normalize()
      .multiplyScalar(distance);
    flyTo(point.clone().add(offset), point.clone());
    resumeDrift();
  }

  function resetCamera() {
    flyTo(HOME.position, HOME.target);
    resumeDrift();
  }

  function stepTween(dt) {
    if (!tween) return;
    tween.t = Math.min(1, tween.t + dt / tween.duration);
    const k = easeInOutCubic(tween.t);
    camera.position.lerpVectors(tween.fromPos, tween.toPos, k);
    controls.target.lerpVectors(tween.fromTarget, tween.toTarget, k);
    if (tween.t >= 1) tween = null;
  }

  // --- frame loop ---------------------------------------------------------
  const callbacks = [];
  // THREE.Clock is deprecated as of r185; Timer is the supported replacement.
  const timer = new THREE.Timer();

  function loop() {
    requestAnimationFrame(loop);
    timer.update();
    // Timer does not clamp, and an unclamped delta after a tab-switch would
    // teleport every bus across the city on the first frame back.
    const dt = Math.min(timer.getDelta(), 0.05);
    const elapsed = timer.getElapsed();

    stepTween(dt);
    for (const cb of callbacks) cb(dt, elapsed);
    controls.update();

    if (bloomEnabled) composer.render();
    else renderer.render(scene, camera);
  }
  requestAnimationFrame(loop);

  return {
    scene,
    camera,
    renderer,
    controls,
    onFrame: (cb) => callbacks.push(cb),
    focusOn,
    resetCamera,
    isTweening: () => tween !== null,
    toggleBloom() {
      bloomEnabled = !bloomEnabled;
      return bloomEnabled;
    },
  };
}
