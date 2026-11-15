// SPANNER // RK61 repair hologram
// A true-to-spec Royal Kludge RK61 built procedurally (every keycap, switch internals, sockets, LEDs,
// plate with cut-outs, PCB, battery, case), playing the knowledge-base repair step by step with animated
// tools, "what can go wrong" demos, voice narration and voice commands, in a Jarvis-style HUD.
// Serve the repo root (python -m http.server 8765) and open http://127.0.0.1:8765/training/viewer/
// Deep links: ?step=3  ?explode=1  ?xray=1  ?select=switch_F  ?pull=1  ?open=1  ?mistake=1  ?panel=parts
//             ?instant=1 (no animation, for screenshots)  ?fx=0 (no post-processing)  ?key=SPACE
// For P1: applyCommand() plays contracts.SceneCommand; applyState() is the world-state model the app's
// 3D panel needs; both port directly into a react-three-fiber scene.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutlinePass } from "three/addons/postprocessing/OutlinePass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import * as BGU from "three/addons/utils/BufferGeometryUtils.js";
import { watchPalm } from "./gesture.js";

const BASE = "../../data/models/rk61/";
const QP = new URLSearchParams(location.search);
const FX = QP.get("fx") !== "0";
const INSTANT = QP.get("instant") === "1";
const CYAN = 0xcaf879 /* the accent: highlights, arrows, outlines */, RED = 0xe0554a;
const $ = (id) => document.getElementById(id);
const API = QP.get("api") || "";            // same origin when served by the backend (uvicorn backend.main:app)

// Never leave the page sitting on the boot screen without saying why.
function bootFail(msg) {
  const m = $("bootmsg"), b = $("boot");
  if (!m || !b || !b.isConnected || m.classList.contains("bad")) return;   // keep the first, most specific message
  m.textContent = msg; m.classList.add("bad");
  const r = $("bootretry"); if (r) { r.style.display = "inline-block"; r.onclick = () => location.reload(); }
}
addEventListener("error", (e) => bootFail("Something went wrong while loading: " + (e.message || "unknown error")));
addEventListener("unhandledrejection", (e) => bootFail("Something went wrong while loading: " + (e.reason?.message || e.reason)));
/** fetch that gives up after `ms`, so one stuck request can never freeze a loop or use up the browser's connections. */
const fetchT = (url, init = {}, ms = 8000) => {
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), ms);
  return fetch(url, { ...init, signal: ctl.signal }).finally(() => clearTimeout(t));
};
const fetchJson = async (url, ms = 8000) => {
  const r = await fetchT(url, {}, ms);
  if (!r.ok) throw new Error(`${url.split("?")[0].split("/").pop()} answered ${r.status}`);
  return r.json();
};
const sleep = (ms) => new Promise((r) => setTimeout(r, INSTANT ? 0 : ms));
const V3 = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);

// ------------------------------------------------------------------ renderer, scene, camera
let renderer, contextLost = false;
try {
  renderer = new THREE.WebGLRenderer({ canvas: $("c"), antialias: true, powerPreference: "high-performance" });
} catch (e) {
  bootFail("This browser couldn’t start 3D graphics (WebGL). Try Chrome or Edge and check hardware acceleration is on.");
  throw e;
}
// A GPU reset or sleep/wake loses the context: pause drawing, let three.js rebuild it, then carry on.
renderer.domElement.addEventListener("webglcontextlost", (e) => { e.preventDefault(); contextLost = true; });
renderer.domElement.addEventListener("webglcontextrestored", () => { contextLost = false; });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
scene.background = new THREE.Color(cssVar("--scene") || "#131a15");
scene.fog = new THREE.FogExp2(scene.background.getHex(), 0.006);
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;

const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 400);
camera.position.set(0, 60, 90);
const controls = new OrbitControls(camera, renderer.domElement);
Object.assign(controls, { enableDamping: true, dampingFactor: 0.08, minDistance: 4, maxDistance: 90,
  maxPolarAngle: Math.PI * 0.48, screenSpacePanning: true, rotateSpeed: 0.7, autoRotateSpeed: 0.5 });
controls.target.set(0, 2, 0);

const key = new THREE.DirectionalLight(0xffffff, 2.3);
key.position.set(12, 30, 18); key.castShadow = true;
key.shadow.mapSize.set(2048, 2048); Object.assign(key.shadow.camera, { left: -25, right: 25, top: 25, bottom: -25 });
key.shadow.bias = -0.0004; key.shadow.radius = 4;
const fill = new THREE.DirectionalLight(0xdff6ff, 0.9); fill.position.set(-6, 10, 30);
scene.add(key, fill, new THREE.HemisphereLight(0xcff6ff, 0x0a0d16, 0.6));
const rim = new THREE.PointLight(CYAN, 14, 60); rim.position.set(-18, 10, -14); scene.add(rim);

const floor = new THREE.Mesh(new THREE.CircleGeometry(60, 64), new THREE.ShadowMaterial({ opacity: 0.45 }));
floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
const grid = new THREE.PolarGridHelper(34, 24, 10, 96, CYAN, CYAN);
grid.material.transparent = true; grid.material.opacity = 0.09; grid.position.y = 0.01; scene.add(grid);
const rings = new THREE.Group(); scene.add(rings);
for (const [r, w, o] of [[19, 0.08, 0.6], [20.4, 0.03, 0.35], [22.5, 0.05, 0.25]]) {
  const m = new THREE.Mesh(new THREE.RingGeometry(r, r + w, 160, 1, 0, Math.PI * (1.2 + Math.random() * 0.7)),
    new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, opacity: o, side: THREE.DoubleSide, blending: THREE.AdditiveBlending }));
  m.rotation.x = -Math.PI / 2; m.position.y = 0.02; m.userData.spin = (Math.random() - 0.5) * 0.25; rings.add(m);
}

grid.visible = false; rings.visible = false;   // the calm Figma look: just the keyboard and its shadow

// post: outline (hover/selection) -> sanitize (no NaN/inf pixels into bloom) -> bloom (HDR glow only) -> output
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const outline = new OutlinePass(new THREE.Vector2(1, 1), scene, camera);
Object.assign(outline, { edgeStrength: 4.0, edgeGlow: 0.5, edgeThickness: 1.3, pulsePeriod: 0 });
outline.visibleEdgeColor.set(CYAN); outline.hiddenEdgeColor.set(0x6f8a45);
composer.addPass(outline);
composer.addPass(new ShaderPass({ uniforms: { tDiffuse: { value: null } },
  vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
  fragmentShader: "uniform sampler2D tDiffuse; varying vec2 vUv; void main(){ vec4 c = texture2D(tDiffuse, vUv);"
    + " if (c.r != c.r || c.g != c.g || c.b != c.b || c.a != c.a) c = vec4(0.0, 0.0, 0.0, 1.0);"
    + " gl_FragColor = vec4(min(c.rgb, vec3(16.0)), c.a); }" }));
const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.5, 0.5, 1.0);
composer.addPass(bloom);
composer.addPass(new OutputPass());
const labelRenderer = new CSS2DRenderer({ element: $("labels") });

// The 3D scene lives in its own card (the Figma "3D repair scene"), so it is sized to that card, not the window.
function resize() {
  const vp = $("viewport"), w = Math.max(1, vp.clientWidth), h = Math.max(1, vp.clientHeight);
  renderer.setSize(w, h, false); composer.setSize(w, h);
  labelRenderer.setSize(w, h); outline.resolution.set(w, h);
  camera.aspect = w / h; camera.updateProjectionMatrix();
}
function setStage() { resize(); }   // nothing overlaps the scene any more; kept for the callers that still ask
addEventListener("resize", resize); new ResizeObserver(resize).observe($("viewport")); resize();

// Light / dark: the page follows the Figma tokens, the 3D backdrop follows the page.
function applyTheme(t, persist = true) {
  document.documentElement.dataset.theme = t;
  if (persist) try { localStorage.setItem("spanner-theme", t); } catch { /* storage blocked */ }
  $("btnTheme").textContent = t === "light" ? "Dark" : "Light";
  scene.background = new THREE.Color(cssVar("--scene") || "#131a15"); scene.fog.color.copy(scene.background);
  renderer.toneMappingExposure = t === "light" ? 1.05 : 1.0;
}
$("btnTheme").onclick = () => applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
applyTheme(document.documentElement.dataset.theme, false);

// ------------------------------------------------------------------ materials
const M = {
  keycap: new THREE.MeshPhysicalMaterial({ color: 0x24262d, roughness: 0.62, clearcoat: 0.25, clearcoatRoughness: 0.6 }),
  housing: new THREE.MeshStandardMaterial({ color: 0x111215, roughness: 0.55 }),
  smoke: new THREE.MeshPhysicalMaterial({ color: 0x3a4048, roughness: 0.18, metalness: 0, transparent: true, opacity: 0.5, clearcoat: 1 }),
  stem: new THREE.MeshStandardMaterial({ color: 0xd8282f, roughness: 0.4 }),
  spring: new THREE.MeshStandardMaterial({ color: 0xd9dde3, metalness: 1, roughness: 0.3 }),
  copper: new THREE.MeshStandardMaterial({ color: 0xc98a4b, metalness: 1, roughness: 0.35 }),
  gold: new THREE.MeshStandardMaterial({ color: 0xd4a548, metalness: 1, roughness: 0.28 }),
  plate: new THREE.MeshPhysicalMaterial({ color: 0x5a5f68, metalness: 0.85, roughness: 0.38, clearcoat: 0.2 }),
  socket: new THREE.MeshStandardMaterial({ color: 0x1c1c20, roughness: 0.55 }),
  stab: new THREE.MeshStandardMaterial({ color: 0xd9dde3, metalness: 0.6, roughness: 0.35 }),
  case: new THREE.MeshPhysicalMaterial({ color: 0x2c2e35, roughness: 0.5, clearcoat: 0.45, clearcoatRoughness: 0.35 }),
  rubber: new THREE.MeshStandardMaterial({ color: 0x0c0c0e, roughness: 0.95 }),
  metal: new THREE.MeshStandardMaterial({ color: 0xc9ccd2, metalness: 1, roughness: 0.25 }),
  tool: new THREE.MeshStandardMaterial({ color: 0x1a1b1f, roughness: 0.5 }),
  steel: new THREE.MeshStandardMaterial({ color: 0xb8bcc4, metalness: 1, roughness: 0.3 }),
  battery: new THREE.MeshStandardMaterial({ color: 0x8e949b, metalness: 0.6, roughness: 0.45 }),
  tape: new THREE.MeshStandardMaterial({ color: 0xe8c34a, roughness: 0.7 }),
  can: new THREE.MeshStandardMaterial({ color: 0x2b6fd6, metalness: 0.4, roughness: 0.35 }),
  holo: new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.07, depthWrite: false, blending: THREE.AdditiveBlending }),
  edge: new THREE.LineBasicMaterial({ color: CYAN, transparent: true, opacity: 0.06 }),
  guide: new THREE.LineBasicMaterial({ color: CYAN, transparent: true, opacity: 0.8 }),
};
const hiCache = new Map();
function hiMat(mat, color = CYAN, k = 1.4) {
  if (Array.isArray(mat)) return mat.map((m) => hiMat(m, color, k));
  const id = mat.uuid + color + k;
  if (!hiCache.has(id)) { const m = mat.clone(); m.emissive = new THREE.Color(color); m.emissiveIntensity = k; hiCache.set(id, m); }
  return hiCache.get(id);
}

// ------------------------------------------------------------------ textures
function legendTextures(label, wu) {
  const W = Math.round(192 * Math.min(wu, 2.5)), H = 192, big = label.length <= 2;
  const mk = (bg, fg) => {
    const c = Object.assign(document.createElement("canvas"), { width: W, height: H }), g = c.getContext("2d");
    g.fillStyle = bg; g.fillRect(0, 0, W, H);
    g.fillStyle = fg; g.textBaseline = "top"; g.textAlign = "left";
    g.font = `600 ${big ? 66 : 34}px Inter, "Segoe UI", sans-serif`;
    g.fillText(label, 26, big ? 30 : 42);
    const t = new THREE.CanvasTexture(c); t.anisotropy = 8; t.colorSpace = THREE.SRGBColorSpace; return t;
  };
  return { map: mk("#24262d", "#e6e9ef"), emissive: mk("#000000", "#ffffff") };
}

function pcbTexture(pack, width, depth) {
  const W = 2048, H = Math.round(W * depth / width), c = Object.assign(document.createElement("canvas"), { width: W, height: H });
  const g = c.getContext("2d"), px = (x) => (x / width + 0.5) * W, pz = (z) => (z / depth + 0.5) * H;
  g.fillStyle = "#0f3a24"; g.fillRect(0, 0, W, H);
  g.strokeStyle = "#1f6b40"; g.lineWidth = 3;
  for (const k of Object.values(pack.keys)) {
    g.beginPath(); g.moveTo(px(k.x), pz(k.z) + 22); g.lineTo(px(k.x) + 34, pz(k.z) + 56); g.lineTo(px(-3.5), pz(k.z) + 56); g.stroke();
  }
  g.fillStyle = "#d4a548";
  for (const k of Object.values(pack.keys)) {
    for (const [dx, dz] of [[-0.38, 0.2], [0.26, 0.38]]) { g.beginPath(); g.arc(px(k.x + dx), pz(k.z + dz), 9, 0, 7); g.fill(); }
  }
  g.fillStyle = "#111"; g.fillRect(px(-4.6), pz(-0.9), 150, 100);
  g.fillStyle = "#e9efe9"; g.font = "bold 30px monospace"; g.fillText("RK61  REV 2.1  HOT-SWAP  3/5-PIN", 70, H - 34);
  g.fillText("BAT 1450mAh", px(-8.2), pz(0.2));
  const t = new THREE.CanvasTexture(c); t.anisotropy = 8; t.colorSpace = THREE.SRGBColorSpace; return t;
}

// ------------------------------------------------------------------ part metadata
const ROLE = {
  keycaps: ["KEYCAP", "ABS shine-through cap in Cherry/OEM profile. Pulls straight up with a wire keycap puller."],
  switches: ["MECHANICAL SWITCH", "MX-style linear switch (RK Red: 40 g, 2.0 mm actuation, 4.0 mm travel). Hot-swap: pulls out with a switch puller, no soldering."],
  spare: ["SPARE SWITCH", "The replacement: an MX-compatible 3-pin or 5-pin switch. Pins must be straight before it goes in."],
  stabilizers: ["STABILIZER", "Keeps long keys (space, shifts, enter, backspace) level when pressed off-centre."],
  plate: ["PLATE", "Steel plate with a 14 mm square cut-out per switch. The switch clips into it."],
  hotswap_sockets: ["HOT-SWAP SOCKET", "Kailh-type socket soldered to the PCB. Two spring contacts grip the switch pins: a bent pin is the #1 cause of a dead key after a swap."],
  leds: ["BACKLIGHT LED", "Per-key SMD LED north of the switch; shines through the legend."],
  pcb: ["PCB", "The circuit board: reads every switch, drives the LEDs, talks over USB-C, Bluetooth 5.1 or 2.4 GHz."],
  battery: ["BATTERY", "1450 mAh Li-Po pouch under the PCB (wireless models)."],
  case: ["CASE", "Plastic tray: the plate and PCB sit inside it. 289 x 103 mm."],
  usb_c_port: ["USB-C PORT", "Power, data and charging. Unplug it before touching switches."],
  mode_switch: ["MODE SWITCH", "Wired / Bluetooth / 2.4 GHz selector on the back edge."],
};
const SPEC_KEYS = {
  keycaps: ["keycap"], switches: ["switch type", "actuation", "travel"], spare: ["hot-swap", "switch type"], hotswap_sockets: ["hot-swap"],
  leds: ["backlight"], pcb: ["connectivity", "bluetooth", "polling", "rollover", "2.4"], battery: ["battery"],
  case: ["dimensions", "weight", "layout", "number of keys"], usb_c_port: ["usb"], plate: ["dimensions"], stabilizers: ["layout"], mode_switch: ["connectivity"],
};
const CALLOUT = { keycaps: "KEYCAPS", switches: "SWITCHES", stabilizers: "STABILIZERS", plate: "PLATE", hotswap_sockets: "HOT-SWAP SOCKETS",
                  leds: "RGB LEDS", pcb: "PCB", battery: "BATTERY", case: "CASE" };
const ROWP = [{ h: 1.08, tilt: 7 }, { h: 0.98, tilt: 3 }, { h: 0.92, tilt: 0 }, { h: 0.96, tilt: -5 }, { h: 0.98, tilt: -7 }];

// ------------------------------------------------------------------ state
let pack, plan, card, brokenKey = "F", K, stepIdx = -1, xray = false, touring = false, selected = null, hovered = null;
let rgbOn = true, voiceOn = false, micOn = false, mistakeOn = false, flying = false, lastInput = performance.now();
const parts = {};         // name -> { obj, group, base: Vector3 }
const amount = {};        // group -> explode amount 0..1
const lift = {};          // part -> extra y
const offset = {};        // part -> Vector3 extra offset (set-aside positions)
const highlighted = new Set();
const pickables = [];
const tweens = [];
const callouts = {};
const tags = [];
const capTop = {}, ledMat = {}, switchParts = {};
let pulseT0 = 0, arrow = null, openSwitch = null, dimLabel = null;
let Y, U, W, D, CW, CH, CD, WX, WZ;

const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
function tween(get, set, to, ms, tag = "") {
  const from = get();
  if (INSTANT) { set(to); return Promise.resolve(); }   // screenshots: jump to the end state, no frames needed
  return new Promise((done) => tweens.push({ t0: performance.now(), from, to, ms, set, done, tag, dead: false }));
}
function cancelTweens(tag) { for (const t of tweens) if (t.tag === tag) t.dead = true; }
const animAmount = (g, a, ms = 650) => tween(() => amount[g] ?? 0, (v) => (amount[g] = v), a, ms);
const animLift = (p, a, ms = 600) => tween(() => lift[p] ?? 0, (v) => (lift[p] = v), a, ms);
function animOffset(p, target, ms = 700) {
  const from = (offset[p] ?? V3()).clone(), to = target.clone();
  return tween(() => 0, (t) => { offset[p] = from.clone().lerp(to, t); }, 1, ms);
}
function tweenVec(v, to, ms, tag = "") { const from = v.clone(); return tween(() => 0, (t) => v.copy(from).lerp(to, t), 1, ms, tag); }
function tweenNum(obj, prop, to, ms) { return tween(() => obj[prop], (v) => (obj[prop] = v), to, ms); }

function updatePositions() {
  for (const [name, p] of Object.entries(parts)) {
    const off = (pack.explode_offsets_cm[p.group] ?? 0) * (amount[p.group] ?? 0) * (highlighted.has(name) ? 1.5 : 1);
    const o = offset[name];
    p.obj.position.set(p.base.x + (o?.x ?? 0), p.base.y + off + (lift[name] ?? 0) + (o?.y ?? 0), p.base.z + (o?.z ?? 0));
  }
  for (const [g, el] of Object.entries(callouts)) {
    const any = parts[pack.groups[g]?.[0]]; if (!any) continue;
    el.obj.position.set(W / 2 + 2.2, any.obj.position.y + 0.3, 0);
    el.div.style.opacity = (amount[g] ?? 0) > 0.3 ? 1 : 0;
  }
  if (dimLabel) dimLabel.div.style.opacity = (amount.keycaps ?? 0) > 0.3 ? 1 : 0;
}

// ------------------------------------------------------------------ geometry builders
function keycapGeometry(wu, row) {
  const w = wu * U - 0.14, d = U - 0.14, h = ROWP[row].h, tilt = THREE.MathUtils.degToRad(ROWP[row].tilt);
  const g = new THREE.BoxGeometry(w, h, d, 10, 1, 8), pos = g.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i), t = y / h + 0.5;
    const nx = x * (w - 2 * 0.17 * t) / w, nz = z * (d - 2 * 0.24 * t) / d;
    let ny = y;
    if (y > h / 2 - 1e-4) ny += -0.06 * (1 - Math.pow(2 * nx / w, 2)) - Math.tan(tilt) * nz;   // dish + row tilt
    pos.setXYZ(i, nx, ny, nz);
  }
  g.computeVertexNormals();
  return g;
}
function helix(r, y0, y1, turns) {
  const pts = [];
  for (let i = 0; i <= turns * 16; i++) { const a = (i / 16) * Math.PI * 2; pts.push(V3(Math.cos(a) * r, y0 + (y1 - y0) * i / (turns * 16), Math.sin(a) * r)); }
  return new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), turns * 16, 0.025, 6, false);
}
function translated(geo, x, y, z) { geo.translate(x, y, z); return geo; }
// RoundedBoxGeometry is non-indexed while Box/Cylinder are indexed; mergeGeometries needs them alike
const merge = (geos) => BGU.mergeGeometries(geos.map((g) => (g.index ? g.toNonIndexed() : g)));
function mesh(geo, mat, part) {
  const m = new THREE.Mesh(geo, mat); m.castShadow = m.receiveShadow = true;
  if (part) { m.userData.part = part; m.userData.mat = mat; pickables.push(m); }
  return m;
}
function edges(geo, obj, threshold = 30) {
  const e = new THREE.LineSegments(new THREE.EdgesGeometry(geo, threshold), M.edge); e.raycast = () => {}; obj.add(e); return e;
}

function buildSwitch(name) {
  const g = new THREE.Group(); g.name = name;
  const bottom = merge([
    new RoundedBoxGeometry(1.4, 0.56, 1.4, 2, 0.06).translate(0, -0.27, 0),
    translated(new THREE.BoxGeometry(1.58, 0.08, 1.58), 0, 0.01, 0),
    translated(new THREE.CylinderGeometry(0.1, 0.1, 0.32, 10), 0, -0.71, 0)]);
  const top = new RoundedBoxGeometry(1.36, 0.5, 1.36, 2, 0.08).translate(0, 0.27, 0);
  const stem = merge([
    translated(new THREE.BoxGeometry(0.42, 0.5, 0.12), 0, 0.68, 0), translated(new THREE.BoxGeometry(0.12, 0.5, 0.42), 0, 0.68, 0),
    translated(new THREE.BoxGeometry(0.56, 0.46, 0.56), 0, 0.28, 0)]);
  const sp = { bottom: mesh(bottom, M.housing, name), top: mesh(top, M.smoke, name), stem: mesh(stem, M.stem, name),
    spring: mesh(helix(0.2, -0.18, 0.3, 5.5), M.spring, name), leaf: mesh(translated(new THREE.BoxGeometry(0.06, 0.5, 0.3), -0.5, 0, 0.1), M.copper, name),
    pin1: mesh(translated(new THREE.CylinderGeometry(0.04, 0.04, 0.38, 8), 0, -0.19, 0), M.gold, name),
    pin2: mesh(translated(new THREE.CylinderGeometry(0.04, 0.04, 0.38, 8), 0, -0.19, 0), M.gold, name) };
  sp.pin1.position.set(-0.38, -0.55, 0.2); sp.pin2.position.set(0.26, -0.55, 0.38);   // pins pivot at the housing, so they can bend
  sp.top.castShadow = false;
  for (const m of Object.values(sp)) { m.userData.y0 = m.position.y; g.add(m); }
  edges(top, g, 40);
  switchParts[name] = sp;
  return g;
}
function buildPart(name, group, obj, base) { obj.name = name; obj.position.copy(base); scene.add(obj); parts[name] = { obj, group, base: base.clone() }; }

function buildKeyboard() {
  U = pack.key_unit_cm; [W, D] = pack.key_area_cm; [CW, CH, CD] = pack.case_cm; [WX, WZ] = pack.wall_cm; Y = pack.layers_y;
  for (const [kid, k] of Object.entries(pack.keys)) {
    const geo = keycapGeometry(k.width_u, k.row);
    const side = M.keycap, tex = kid === "SPACE" ? null : legendTextures(k.label, k.width_u);
    const topMat = tex ? new THREE.MeshPhysicalMaterial({ map: tex.map, emissiveMap: tex.emissive, emissive: 0x000000, roughness: 0.62, clearcoat: 0.25, clearcoatRoughness: 0.6 }) : side;
    const cap = new THREE.Group(); cap.add(mesh(geo, [side, side, topMat, side, side, side], `keycap_${kid}`));
    if (kid === "F" || kid === "J") cap.add(mesh(translated(new THREE.BoxGeometry(0.32, 0.03, 0.05), 0, ROWP[k.row].h / 2 - 0.03, 0.42), side, `keycap_${kid}`));
    edges(geo, cap, 45); capTop[`keycap_${kid}`] = topMat;
    buildPart(`keycap_${kid}`, "keycaps", cap, V3(k.x, Y.keycap, k.z));
    buildPart(`switch_${kid}`, "switches", buildSwitch(`switch_${kid}`), V3(k.x, Y.switch, k.z));
    const sock = new THREE.Group();
    sock.add(mesh(new RoundedBoxGeometry(1.0, 0.22, 0.55, 1, 0.04), M.socket, `socket_${kid}`));
    for (const [dx, dz] of [[-0.38, -0.1], [0.26, 0.08]]) sock.add(mesh(translated(new THREE.CylinderGeometry(0.08, 0.08, 0.06, 10), dx, 0.12, dz), M.gold, `socket_${kid}`));
    buildPart(`socket_${kid}`, "hotswap_sockets", sock, V3(k.x, Y.socket, k.z + 0.3));
    const lm = new THREE.MeshStandardMaterial({ color: 0x222228, emissive: 0x000000, emissiveIntensity: 2.2, roughness: 0.3 });
    const led = new THREE.Group(); led.add(mesh(new THREE.BoxGeometry(0.2, 0.06, 0.2), lm, `led_${kid}`)); ledMat[`led_${kid}`] = lm;
    buildPart(`led_${kid}`, "leds", led, V3(k.x, Y.led, k.z - 0.5));
    if (k.width_u >= 2) {
      const stab = new THREE.Group(), half = k.width_u * U / 2 - 0.9;
      stab.add(mesh(new THREE.BoxGeometry(k.width_u * U - 1.2 * U, 0.1, 0.22), M.stab, `stabilizer_${kid}`));
      for (const sx of [-half, half]) stab.add(mesh(translated(new THREE.BoxGeometry(0.6, 0.5, 0.75), sx, 0.1, 0), M.housing, `stabilizer_${kid}`));
      buildPart(`stabilizer_${kid}`, "stabilizers", stab, V3(k.x, Y.stab, k.z + 0.55));
    }
  }
  // plate with one 14 mm cut-out per switch
  const shape = new THREE.Shape([[-W / 2, -D / 2], [W / 2, -D / 2], [W / 2, D / 2], [-W / 2, D / 2]].map(([x, y]) => new THREE.Vector2(x, y)));
  for (const k of Object.values(pack.keys)) {
    const h = new THREE.Path(), x = k.x, y = -k.z;
    h.moveTo(x - 0.7, y - 0.7); h.lineTo(x + 0.7, y - 0.7); h.lineTo(x + 0.7, y + 0.7); h.lineTo(x - 0.7, y + 0.7); h.closePath(); shape.holes.push(h);
  }
  const plateGeo = new THREE.ExtrudeGeometry(shape, { depth: 0.15, bevelEnabled: false }); plateGeo.rotateX(-Math.PI / 2); plateGeo.translate(0, -0.075, 0);
  const plate = new THREE.Group(); plate.add(mesh(plateGeo, M.plate, "plate")); edges(plateGeo, plate, 60);
  buildPart("plate", "plate", plate, V3(0, Y.plate, 0));
  const pcbGeo = new THREE.BoxGeometry(W, 0.16, D), pcbSide = new THREE.MeshStandardMaterial({ color: 0x0c2d1c, roughness: 0.6 });
  const pcbTop = new THREE.MeshStandardMaterial({ map: pcbTexture(pack, W, D), roughness: 0.45, metalness: 0.15 });
  const pcb = new THREE.Group(); pcb.add(mesh(pcbGeo, [pcbSide, pcbSide, pcbTop, pcbSide, pcbSide, pcbSide], "pcb")); edges(pcbGeo, pcb);
  buildPart("pcb", "pcb", pcb, V3(0, Y.pcb, 0));
  const bat = new THREE.Group();
  bat.add(mesh(new RoundedBoxGeometry(6.5, 0.3, 2.8, 2, 0.08), M.battery, "battery"));
  bat.add(mesh(translated(new THREE.BoxGeometry(6.6, 0.31, 0.5), 0, 0, -0.6), M.tape, "battery"));
  buildPart("battery", "battery", bat, V3(-5.0, Y.battery, 0.3));
  const caseGeo = merge([
    new RoundedBoxGeometry(CW, 0.5, CD, 3, 0.22).translate(0, 0.25, 0),
    translated(new THREE.BoxGeometry(CW, CH, WZ), 0, CH / 2, -D / 2 - WZ / 2), translated(new THREE.BoxGeometry(CW, CH, WZ), 0, CH / 2, D / 2 + WZ / 2),
    translated(new THREE.BoxGeometry(WX, CH, D), -W / 2 - WX / 2, CH / 2, 0), translated(new THREE.BoxGeometry(WX, CH, D), W / 2 + WX / 2, CH / 2, 0)]);
  const cs = new THREE.Group(); cs.add(mesh(caseGeo, M.case, "case")); edges(caseGeo, cs, 50);
  for (const [sx, sz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) cs.add(mesh(translated(new THREE.CylinderGeometry(0.45, 0.45, 0.12, 16), sx * (CW / 2 - 2), -0.06, sz * (CD / 2 - 1.2)), M.rubber, "case"));
  buildPart("case", "case", cs, V3(0, 0, 0));
  const usb = new THREE.Group(); usb.add(mesh(new RoundedBoxGeometry(0.9, 0.35, 0.3, 2, 0.1), M.metal, "usb_c_port"));
  buildPart("usb_c_port", "usb_c_port", usb, V3(...pack.ports.usb_c_port));
  const mode = new THREE.Group(); mode.add(mesh(new THREE.BoxGeometry(0.8, 0.3, 0.3), M.socket, "mode_switch")); mode.add(mesh(translated(new THREE.BoxGeometry(0.22, 0.2, 0.2), 0.2, 0, -0.1), M.metal, "mode_switch"));
  buildPart("mode_switch", "mode_switch", mode, V3(...pack.ports.mode_switch));
  const spare = buildSwitch("switch_spare"); spare.visible = false;
  buildPart("switch_spare", "spare", spare, V3(K.x, Y.switch, K.z));
  pack.explode_offsets_cm.spare = 0;
}

// ------------------------------------------------------------------ tools
const tools = {};
function buildTools() {
  const kp = new THREE.Group(), loops = new THREE.Group();           // wire keycap puller
  for (const sx of [-1, 1]) {
    const pts = [V3(sx * 0.25, 2.0, 0), V3(sx * 0.88, 1.0, -0.5), V3(sx * 0.88, 0.0, -0.58), V3(sx * 0.88, 0.0, 0.58), V3(sx * 0.88, 1.0, 0.5), V3(sx * 0.25, 2.0, 0)];
    loops.add(mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts, false, "catmullrom", 0.2), 48, 0.035, 6), M.steel));
  }
  kp.add(loops); kp.add(mesh(translated(new THREE.CylinderGeometry(0.3, 0.3, 1.6, 16), 0, 2.7, 0), M.tool));
  kp.add(mesh(translated(new THREE.TorusGeometry(0.42, 0.06, 8, 24), 0, 3.5, 0), M.tool));
  kp.userData.loops = loops; tools.keycapPuller = kp;
  const sp = new THREE.Group(), prongs = [];                          // switch puller: prongs on the N/S clips
  for (const sz of [-1, 1]) { const p = mesh(translated(new THREE.BoxGeometry(0.55, 2.8, 0.14), 0, 1.4, 0), M.steel); p.userData.sz = sz; prongs.push(p); sp.add(p); }
  sp.add(mesh(translated(new THREE.BoxGeometry(0.7, 0.4, 1.3), 0, 3.0, 0), M.tool));
  sp.userData.prongs = prongs; sp.userData.gap = 1.75; tools.switchPuller = sp;
  const tw = new THREE.Group(), tips = [];                            // tweezers
  for (const sx of [-1, 1]) { const p = mesh(translated(new THREE.BoxGeometry(0.07, 2.4, 0.22), 0, 1.2, 0), M.steel); p.userData.sx = sx; tips.push(p); tw.add(p); }
  tw.add(mesh(translated(new THREE.BoxGeometry(0.7, 0.2, 0.22), 0, 2.4, 0), M.steel));
  tw.userData.tips = tips; tw.userData.gap = 0.5; tools.tweezers = tw;
  const plug = new THREE.Group();                                     // USB-C plug + cable, origin at the plug tip
  plug.add(mesh(new RoundedBoxGeometry(0.84, 0.26, 0.8, 2, 0.08).translate(0, 0, -0.4), M.metal));
  plug.add(mesh(new RoundedBoxGeometry(1.2, 0.6, 1.4, 2, 0.15).translate(0, 0, -1.5), M.tool));
  plug.add(mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([V3(0, 0, -2.1), V3(0, -0.2, -4), V3(0, -0.9, -6.5), V3(0, -1.0, -9)]), 24, 0.16, 8), M.tool));
  tools.plug = plug;
  const can = new THREE.Group();                                      // compressed-air can
  can.add(mesh(translated(new THREE.CylinderGeometry(0.8, 0.8, 3.2, 20), 0, 1.6, 0), M.can));
  can.add(mesh(translated(new THREE.CylinderGeometry(0.3, 0.45, 0.5, 14), 0, 3.45, 0), M.tool));
  const nozzle = mesh(new THREE.CylinderGeometry(0.06, 0.06, 2.2, 8), M.metal); nozzle.rotation.x = Math.PI / 2; nozzle.position.set(0, 3.5, -1.1); can.add(nozzle);
  tools.airCan = can;
  for (const t of Object.values(tools)) { t.visible = false; t.traverse((o) => { if (o.isMesh) o.castShadow = true; }); scene.add(t); }
}
function setGap(tool, gap) {
  tool.userData.gap = gap;
  if (tool.userData.prongs) for (const p of tool.userData.prongs) p.position.z = p.userData.sz * gap / 2;
  if (tool.userData.tips) for (const p of tool.userData.tips) p.position.x = p.userData.sx * gap / 2;
}
const animGap = (tool, gap, ms = 350) => tween(() => tool.userData.gap, (v) => setGap(tool, v), gap, ms);
function showTool(tool, pos, rot = V3()) { tool.visible = true; tool.position.copy(pos); tool.rotation.set(rot.x, rot.y, rot.z); }
function hideTools() { for (const t of Object.values(tools)) if (t !== tools.plug) t.visible = false; }

async function puff(origin) {
  const n = 90, g = new THREE.BufferGeometry(), p0 = new Float32Array(n * 3), dirs = [];
  for (let i = 0; i < n; i++) { p0.set([origin.x, origin.y, origin.z], i * 3); dirs.push(V3((Math.random() - 0.5) * 1.2, -1 - Math.random(), (Math.random() - 0.5) * 1.2).normalize()); }
  g.setAttribute("position", new THREE.BufferAttribute(p0, 3));
  const mat = new THREE.PointsMaterial({ color: 0xdffaff, size: 0.11, transparent: true, opacity: 0.9, depthWrite: false });
  const pts = new THREE.Points(g, mat); scene.add(pts);
  await tween(() => 0, (t) => { const a = g.attributes.position; for (let i = 0; i < n; i++) { const d = dirs[i]; a.setXYZ(i, origin.x + d.x * t * 2.6, origin.y + d.y * t * 2.6, origin.z + d.z * t * 2.6); } a.needsUpdate = true; mat.opacity = 0.9 * (1 - t); }, 1, 700);
  scene.remove(pts);
}

// ------------------------------------------------------------------ highlight / x-ray / RGB
function partNames(part) {
  if (!part) return [];
  if (parts[part]) return [part];
  const g = pack.parts?.[part] ?? part;
  return pack.groups[g] ? pack.groups[g] : parts[g] ? [g] : [];
}
function applyMaterials() {
  for (const [n, p] of Object.entries(parts)) p.obj.traverse((o) => {
    if (!o.isMesh || !o.userData.mat) return;
    o.material = xray ? M.holo : highlighted.has(n) ? hiMat(o.userData.mat) : o.userData.mat;
  });
}
function setHighlight(names) { highlighted.clear(); names.forEach((n) => highlighted.add(n)); applyMaterials(); refreshOutline(); }
function refreshOutline() {
  const objs = new Set([...highlighted].map((n) => parts[n]?.obj));
  if (selected) objs.add(parts[selected]?.obj); if (hovered) objs.add(parts[hovered]?.obj);
  outline.selectedObjects = [...objs].filter(Boolean);
}
function setXray(on) {
  xray = on; $("xray").classList.toggle("on", on); applyMaterials();
  tween(() => M.edge.opacity, (v) => (M.edge.opacity = v), on ? 0.75 : 0.06, 400);
  bloom.strength = on ? 0.9 : 0.5;
}
function setRGB(on) {
  rgbOn = on; $("rgb").classList.toggle("on", on);
  for (const [name, m] of Object.entries(ledMat)) {
    const kid = name.slice(4), k = pack.keys[kid], hue = (k.x + W / 2) / W;
    m.emissive.setHSL(hue, 1, 0.55); m.emissiveIntensity = on ? 2.2 : 0;
    const top = capTop[`keycap_${kid}`]; if (top && top.emissiveMap) { top.emissive.setHSL(hue, 1, 0.5); top.emissiveIntensity = on ? 0.9 : 0; }
  }
}

// ------------------------------------------------------------------ camera
let motionPaused = false;   // "Pause motion": the camera stops following the repair until resumed
function flyTo(pos, target, ms = 1100, force = false) {
  if (motionPaused && !force) return Promise.resolve();
  cancelTweens("cam"); flying = true; controls.enabled = false; controls.autoRotate = false;
  const p0 = camera.position.clone(), t0 = controls.target.clone(), mid = p0.clone().lerp(pos, 0.5).add(V3(0, 3, 0));
  return tween(() => 0, (t) => {
    const a = p0.clone().lerp(mid, t), b = mid.clone().lerp(pos, t); camera.position.copy(a.lerp(b, t));
    controls.target.copy(t0.clone().lerp(target, t));
  }, 1, ms, "cam").then(() => { flying = false; controls.enabled = true; });
}
function keyPoint() { return V3(K.x, Y.switch, K.z); }
function rotateTo(view) {
  const v = pack.views[view] || pack.views.iso;
  if (view === "detail" || view === "socket") { const t = keyPoint().add(V3(0, view === "socket" ? -0.6 : 0.6, 0)); return flyTo(t.clone().add(V3(...v)), t); }
  if (view === "back") { const t = V3(pack.ports.usb_c_port[0], 1.2, pack.ports.usb_c_port[2]); return flyTo(t.clone().add(V3(-4, 7, -16)), t); }
  return flyTo(V3(...v), V3(0, view === "top" ? 0 : 1.5, 0));
}
function partCenter(name) { updatePositions(); parts[name].obj.updateMatrixWorld(true); return new THREE.Box3().setFromObject(parts[name].obj).getCenter(V3()); }
function focusPart(name) {
  const c = partCenter(name), big = ["case", "pcb", "plate", "battery"].includes(parts[name].group), d = big ? 24 : 9;
  return flyTo(c.clone().add(V3(d * 0.3, d * 0.6, d * 0.75)), c);
}
function showArrow(part, direction = "down") {
  if (arrow) scene.remove(arrow);
  const n = partNames(part)[0]; if (!n) return;
  const c = partCenter(n);
  const dir = direction === "up" ? V3(0, 1, 0) : direction === "out" ? V3(0, 0, -1) : V3(0, -1, 0);
  const origin = direction === "up" ? c.clone().add(V3(0, 1.0, 0)) : direction === "out" ? c.clone().add(V3(0, 0.6, -0.8)) : c.clone().add(V3(0, 5.2, 0));
  arrow = new THREE.ArrowHelper(dir, origin, 3.0, CYAN, 1.0, 0.6); arrow.userData.o = origin.clone(); arrow.userData.dir = dir; scene.add(arrow);
}
function clearArrow() { if (arrow) { scene.remove(arrow); arrow = null; } }

// ------------------------------------------------------------------ tags (CSS2D)
function showTag(name, text, cls = "", dy = 1.3) {
  const div = Object.assign(document.createElement("div"), { className: "tag " + cls, textContent: text });
  const obj = new CSS2DObject(div); obj.position.copy(partCenter(name)).add(V3(0, dy, 0)); scene.add(obj); tags.push(obj); return obj;
}
function clearTags() { for (const t of tags) scene.remove(t); tags.length = 0; if (scene.userData.guide) { scene.remove(scene.userData.guide); scene.userData.guide = null; } }

// ------------------------------------------------------------------ contract commands
// The plan's 3D commands were generated for one key (demo_plan.broken_key); play them on whichever key is actually broken.
const DEMO_KEY = () => (plan?.generated ? plan.target_key || "F" : planJson?.broken_key || "F");
function retarget(name) { if (!name) return name; const d = DEMO_KEY(); return d !== brokenKey && name.endsWith("_" + d) ? name.slice(0, -d.length) + brokenKey : name; }
async function applyCommand(c) {
  c = { ...c, part: retarget(c.part) };
  switch (c.op) {
    case "highlight": setHighlight(partNames(c.part || c.group)); break;
    case "pulse": setHighlight(partNames(c.part || c.group)); pulseT0 = performance.now(); break;
    case "explode": await animAmount(c.group || parts[c.part]?.group, c.amount ?? 1); break;
    case "rotate_to": await rotateTo(c.view); break;
    case "arrow": showArrow(c.part, c.direction); break;
    case "reset": await explodeAll(0); break;
    default: break;
  }
}
async function explodeAll(a, ms = 650) { $("ex").value = a * 100; await Promise.all(Object.keys(pack.groups).map((g) => animAmount(g, a, ms))); }

// ------------------------------------------------------------------ world state of the repair (for the broken key K)
const INITIAL = { cable: "in", keycap: "on", old: "in", spare: "none", rgb: true, puller: "away" };
const TRANSITIONS = {
  "cable-out": (w) => { w.cable = "out"; w.rgb = false; }, "keycap-pull": (w) => { w.keycap = "off"; }, "puller-place": (w) => { w.puller = "on"; },
  "switch-pull": (w) => { w.old = "out"; w.puller = "away"; }, "new-switch-inspect": (w) => { w.spare = "shown"; }, "switch-align": (w) => { w.spare = "aligned"; },
  "switch-insert": (w) => { w.spare = "in"; }, "keycap-place": (w) => { w.keycap = "on"; }, "cable-in-test": (w) => { w.cable = "in"; w.rgb = true; },
};
function transition(kind, s) { const w = { ...s }; (TRANSITIONS[kind] || (() => {}))(w); return w; }
const stateBefore = (i) => plan.steps.slice(0, Math.max(0, i)).reduce((s, st) => transition(st.kind, s), { ...INITIAL });
const stateAfter = (i) => transition(plan.steps[i]?.kind, stateBefore(i));
let world = { ...INITIAL };
const DESK_CAP = () => V3(W / 2 + 2.8, 0.55, K.z - 1.0), DESK_SW = () => V3(W / 2 + 2.8, 0.75, K.z + 1.4);
const SPARE_POS = { none: () => V3(K.x, Y.switch, K.z), shown: () => V3(K.x + 2.4, Y.switch + 4.2, K.z + 0.6), aligned: () => V3(K.x, Y.switch + 1.7, K.z), in: () => V3(K.x, Y.switch, K.z) };
const plugPos = (cable) => V3(...pack.ports.usb_c_port).add(V3(0, 0, cable === "in" ? -0.05 : -4.4));

async function applyState(s, ms = 700) {
  world = { ...s };
  const cap = `keycap_${brokenKey}`, sw = `switch_${brokenKey}`;
  for (const n of [cap, sw, "switch_spare"]) parts[n].obj.rotation.set(0, 0, 0);
  for (const n of [sw, "switch_spare"]) { for (const pin of ["pin1", "pin2"]) { switchParts[n][pin].rotation.set(0, 0, 0); switchParts[n][pin].material = M.gold; } }
  hideTools(); tools.plug.visible = true;
  const jobs = [tweenVec(tools.plug.position, plugPos(s.cable), ms),
    animOffset(cap, s.keycap === "on" ? V3() : DESK_CAP().sub(parts[cap].base), ms),
    animOffset(sw, s.old === "in" ? V3() : DESK_SW().sub(parts[sw].base), ms),
    animLift(cap, 0, ms), animLift(sw, 0, ms)];
  parts.switch_spare.obj.visible = s.spare !== "none";
  jobs.push(animOffset("switch_spare", SPARE_POS[s.spare]().sub(parts.switch_spare.base), ms));
  if (s.puller === "on") { showTool(tools.switchPuller, V3(K.x, Y.plate + 0.08, K.z)); setGap(tools.switchPuller, 1.75); }
  setRGB(s.rgb);
  await Promise.all(jobs);
}

// ------------------------------------------------------------------ choreography per step kind
const capH = () => ROWP[K.row].h;
async function pressKey(name, times = 1, stemOnly = false) {
  const stem = (switchParts[name] || switchParts[`switch_${brokenKey}`]).stem;
  for (let i = 0; i < times; i++) {
    blip(640, 0.04);
    if (stemOnly) { await tweenNum(stem.position, "y", stem.userData.y0 - 0.3, 110); await tweenNum(stem.position, "y", stem.userData.y0, 140); }
    else { await animLift(name, -0.22, 110); await animLift(name, 0, 150); }
    await sleep(140);
  }
}
const ACT = {
  async "cable-out"() {
    setHighlight(["usb_c_port"]); await sleep(300); blip(500, 0.08);
    await tweenVec(tools.plug.position, plugPos("out"), 900);
    for (let i = 0; i < 3; i++) { setRGB(i % 2 === 1); await sleep(90); } setRGB(false);
    showTag("usb_c_port", "POWER OFF ✓", "ok", 1.2);
  },
  async "keycap-pull"() {
    const cap = `keycap_${brokenKey}`, base = parts[cap].base, t = tools.keycapPuller;
    showTool(t, base.clone().add(V3(0, 5.5 - capH() / 2, 0))); t.userData.loops.scale.x = 1.12;
    await tweenVec(t.position, base.clone().add(V3(0, -capH() / 2 + 0.05, 0)), 900);
    await tweenNum(t.userData.loops.scale, "x", 1.0, 300); blip(900, 0.05);
    await Promise.all([animLift(cap, 3.4, 800), tweenVec(t.position, t.position.clone().add(V3(0, 3.4, 0)), 800)]);
    await sleep(150);
    await Promise.all([animOffset(cap, DESK_CAP().sub(base).add(V3(0, -3.4, 0)), 900), tweenVec(t.position, t.position.clone().add(V3(3, 2.5, 0)), 700)]);
    offset[cap] = DESK_CAP().sub(base); lift[cap] = 0; t.visible = false;
    showTag(cap, "KEYCAP " + pack.keys[brokenKey].label + " · SET ASIDE", "ok", 1.0);
  },
  async "puller-place"() {
    const t = tools.switchPuller; setGap(t, 1.75);
    showTool(t, V3(K.x, Y.plate + 5.5, K.z));
    await tweenVec(t.position, V3(K.x, Y.plate + 0.08, K.z), 900); blip(760, 0.05);
    showTag(`switch_${brokenKey}`, "PRONGS ON THE N / S CLIPS", "", 3.6);
  },
  async "switch-pull"() {
    const sw = `switch_${brokenKey}`, t = tools.switchPuller;
    if (!t.visible) await ACT["puller-place"]();
    clearTags();
    await animGap(t, 1.42, 350); blip(520, 0.06);
    await Promise.all([animLift(sw, 3.2, 800), tweenVec(t.position, t.position.clone().add(V3(0, 3.2, 0)), 800)]);
    await sleep(150);
    await Promise.all([animOffset(sw, DESK_SW().sub(parts[sw].base).add(V3(0, -3.2, 0)), 900), tweenVec(t.position, t.position.clone().add(V3(3, 2.5, 0)), 700)]);
    offset[sw] = DESK_SW().sub(parts[sw].base); lift[sw] = 0; t.visible = false;
    showTag(sw, "DEAD SWITCH OUT", "ok", 1.6);
  },
  async "socket-inspect"() {
    const sock = `socket_${brokenKey}`, t = tools.airCan;
    showTool(t, V3(K.x + 2.6, Y.plate + 2.2, K.z + 1.8), V3(0, -0.6, 0));
    await sleep(400);
    for (let i = 0; i < 2; i++) { blip(300, 0.12); await puff(V3(K.x, Y.plate + 1.2, K.z + 0.2)); await sleep(200); }
    showTag(sock, "SOCKET OK · 2 CONTACTS CLEAR", "ok", 1.1);
  },
  async "new-switch-inspect"() {
    const sp = parts.switch_spare.obj; sp.visible = true; sp.scale.setScalar(0.01);
    await tween(() => 0.01, (v) => sp.scale.setScalar(v), 1, 500); blip(1000, 0.05);
    await tweenNum(sp.rotation, "x", Math.PI, 900);
    const tw = tools.tweezers; setGap(tw, 0.5);
    const pinWorld = V3(); switchParts.switch_spare.pin1.getWorldPosition(pinWorld);
    showTool(tw, pinWorld.clone().add(V3(0, 2.2, 0)));
    await tweenVec(tw.position, pinWorld.clone().add(V3(0, 0.25, 0)), 600);
    await animGap(tw, 0.12, 250); await sleep(300); await animGap(tw, 0.5, 250);
    await tweenVec(tw.position, tw.position.clone().add(V3(2, 2, 0)), 500); tw.visible = false;
    await tweenNum(sp.rotation, "x", 0, 900);
    await pressKey("switch_spare", 2, true);
    showTag("switch_spare", "SPARE SWITCH · PINS STRAIGHT ✓", "ok", 1.6);
  },
  async "switch-align"() {
    parts.switch_spare.obj.visible = true;
    await animOffset("switch_spare", SPARE_POS.aligned().sub(parts.switch_spare.base), 1000);
    const g = new THREE.BufferGeometry().setFromPoints([V3(K.x - 0.38, Y.switch + 1.2, K.z + 0.2), V3(K.x - 0.38, Y.socket + 0.2, K.z + 0.2),
                                                        V3(K.x + 0.26, Y.switch + 1.2, K.z + 0.38), V3(K.x + 0.26, Y.socket + 0.2, K.z + 0.38)]);
    scene.userData.guide = new THREE.LineSegments(g, M.guide); scene.add(scene.userData.guide);
    showTag("switch_spare", "PINS OVER THE SOCKET HOLES", "", 1.4);
  },
  async "switch-insert"() {
    parts.switch_spare.obj.visible = true;
    if (world.spare !== "aligned") await animOffset("switch_spare", SPARE_POS.aligned().sub(parts.switch_spare.base), 500);
    await sleep(200);
    await animOffset("switch_spare", V3(), 550); blip(420, 0.1);
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.7, 0.85, 40), new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.9, side: THREE.DoubleSide }));
    ring.rotation.x = -Math.PI / 2; ring.position.set(K.x, Y.plate + 0.1, K.z); scene.add(ring);
    await tween(() => 1, (v) => { ring.scale.setScalar(v); ring.material.opacity = 0.9 * (2.2 - v) / 1.2; }, 2.2, 600); scene.remove(ring);
    if (scene.userData.guide) { scene.remove(scene.userData.guide); scene.userData.guide = null; }
    showTag("switch_spare", "CLICK · FLUSH IN THE PLATE ✓", "ok", 1.4);
  },
  async "switch-test"() {
    parts.switch_spare.obj.visible = true;
    await pressKey("switch_spare", 3, true);
    showTag("switch_spare", "SPRINGS BACK ✓ · NO WOBBLE", "ok", 1.4);
  },
  async "keycap-place"() {
    const cap = `keycap_${brokenKey}`;
    await animOffset(cap, V3(0, 3.6, 0), 900);
    await sleep(150);
    await animOffset(cap, V3(), 500); blip(880, 0.06);
    showTag(cap, "KEYCAP SEATED ✓", "ok", 1.0);
  },
  async "cable-in-test"() {
    await tweenVec(tools.plug.position, plugPos("in"), 900); blip(600, 0.08);
    for (let i = 0; i < 2; i++) { setRGB(true); await sleep(120); setRGB(false); await sleep(80); } setRGB(true);
    await sleep(300);
    await pressKey(`keycap_${brokenKey}`, 3);
    showTag(`keycap_${brokenKey}`, `${pack.keys[brokenKey].label} ✓ x3 · REGISTERED ONCE PER PRESS`, "ok", 1.1);
  },
  async other(step) {
    const names = step.parts.flatMap((p) => partNames(p)).filter((n) => !n.includes("_") || n.endsWith(`_${brokenKey}`)).slice(0, 2);
    if (names.length) { setHighlight(names); pulseT0 = performance.now(); }
  },
};

// ------------------------------------------------------------------ mistakes ("what can go wrong")
function redden(name) { parts[name].obj.traverse((o) => { if (o.isMesh && o.userData.mat) o.material = hiMat(o.userData.mat, RED, 1.3); }); }
const MISTAKE = {
  async "cable-out"() {
    await applyState({ ...world, cable: "in", rgb: true }, 400);
    showTool(tools.keycapPuller, parts[`keycap_${brokenKey}`].base.clone().add(V3(0, 2.5, 0)));
    showTag("usb_c_port", "✗ STILL POWERED: UNPLUG FIRST", "red", 1.2); flash();
  },
  async "keycap-pull"(m) {
    const cap = `keycap_${brokenKey}`;
    if (/wrong/.test(m)) {
      const row = Object.entries(pack.keys).filter(([, k]) => k.row === K.row).sort((a, b) => a[1].x - b[1].x);
      const i = row.findIndex(([id]) => id === brokenKey), wrong = `keycap_${row[Math.min(i + 1, row.length - 1)][0]}`;
      setHighlight([]); redden(wrong);
      showTag(wrong, "✗ WRONG KEY", "red", 1.0); showTag(cap, `THE BROKEN KEY IS ${pack.keys[brokenKey].label}`, "ok", 1.0);
    } else {
      const t = tools.keycapPuller; showTool(t, parts[cap].base.clone().add(V3(0, -capH() / 2 + 0.05, 0)));
      await Promise.all([tweenNum(t.rotation, "z", 0.4, 500), tweenNum(parts[cap].obj.rotation, "z", 0.35, 500), animLift(cap, 0.8, 500)]);
      showTag(cap, "✗ PULLING AT AN ANGLE: STRESSES THE STEM", "red", 1.4);
    }
    flash();
  },
  async "puller-place"() {
    const t = tools.switchPuller; showTool(t, V3(K.x, Y.plate + 0.08, K.z), V3(0, Math.PI / 2, 0)); setGap(t, 1.75);
    showTag(`switch_${brokenKey}`, "✗ PRONGS ON THE SIDES: THE CLIPS ARE NORTH / SOUTH", "red", 3.6); flash();
  },
  async "switch-pull"() {
    const sw = `switch_${brokenKey}`; await applyState({ ...world, old: "in", puller: "on" }, 300);
    const sp = switchParts[sw], obj = parts[sw].obj;
    for (let i = 0; i < 4; i++) await tweenNum(obj.rotation, "z", i % 2 ? -0.2 : 0.2, 180);
    await tweenNum(obj.rotation, "z", 0, 150);
    sp.pin1.rotation.z = 0.6; sp.pin2.rotation.x = -0.5; sp.pin1.material = sp.pin2.material = hiMat(M.gold, RED, 1.5);
    showTag(sw, "✗ ROCKING SIDEWAYS: BENT PINS, DAMAGED SOCKET", "red", 1.6); flash();
  },
  async "socket-inspect"() { const s = `socket_${brokenKey}`; redden(s); showTag(s, "✗ LIFTED SOCKET / DEBRIS: A NEW SWITCH FAILS TOO", "red", 1.1); flash(); },
  async "new-switch-inspect"() {
    const sp = switchParts.switch_spare; parts.switch_spare.obj.visible = true;
    await tweenNum(parts.switch_spare.obj.rotation, "x", Math.PI, 600);
    sp.pin1.rotation.z = 0.7; sp.pin1.material = hiMat(M.gold, RED, 1.6);
    showTag("switch_spare", "✗ BENT PIN: STRAIGHTEN IT FIRST", "red", 1.6); flash();
  },
  async "switch-align"() {
    parts.switch_spare.obj.visible = true; await tweenNum(parts.switch_spare.obj.rotation, "y", Math.PI, 600);
    showTag("switch_spare", "✗ ROTATED 180°: PINS MISS THE SOCKET HOLES", "red", 1.4); flash();
  },
  async "switch-insert"() {
    const sp = switchParts.switch_spare; parts.switch_spare.obj.visible = true;
    await animOffset("switch_spare", V3(0.3, 0.5, 0), 400);
    sp.pin1.rotation.z = 0.9; sp.pin1.material = hiMat(M.gold, RED, 1.6);
    showTag("switch_spare", "✗ FORCED IN MISALIGNED: PIN CRUSHED", "red", 1.4); flash();
  },
  async "switch-test"() {
    parts.switch_spare.obj.visible = true; await tweenNum(parts.switch_spare.obj.rotation, "x", 0.18, 500);
    showTag("switch_spare", "✗ TILTED = A PIN IS NOT IN THE SOCKET", "red", 1.4); flash();
  },
  async "keycap-place"(m) {
    const cap = `keycap_${brokenKey}`;
    if (/rotat|wrong/.test(m)) { await tweenNum(parts[cap].obj.rotation, "y", Math.PI, 600); showTag(cap, "✗ ROTATED / WRONG POSITION", "red", 1.0); }
    else { await animLift(cap, 0.35, 400); showTag(cap, "✗ NOT PRESSED ALL THE WAY DOWN", "red", 1.0); }
    flash();
  },
  async other(m, step) {
    const n = step.parts.flatMap((p) => partNames(p))[0] || `keycap_${brokenKey}`;
    redden(n); showTag(n, "✗ " + m.slice(0, 48).toUpperCase(), "red", 1.2); flash();
  },
};
function flash() { $("flash").style.opacity = 0.18; blip(180, 0.2); setTimeout(() => ($("flash").style.opacity = 0), 260); }

async function showMistake() {
  if (stepIdx < 0 || mistakeOn) return;
  const step = plan.steps[stepIdx], m = (step.common_mistakes?.[0] || "a common mistake").toLowerCase();
  mistakeOn = true; $("mistake").classList.add("on"); $("fixit").style.display = "";
  clearTags(); clearArrow(); setMode("⚠ what can go wrong");
  narrate("Watch out. " + (step.common_mistakes?.[0] || ""));
  await (MISTAKE[step.kind] || MISTAKE.other)(m, step);
}
async function fixMistake() {
  if (!mistakeOn) return;
  mistakeOn = false; $("mistake").classList.remove("on"); $("fixit").style.display = "none";
  clearTags(); applyMaterials();
  await goStep(stepIdx, "", true);
}

// ------------------------------------------------------------------ HUD + steps
let typing = 0;
async function typeText(el, text) {
  const id = ++typing; el.textContent = "";
  if (INSTANT) { el.textContent = text; return; }
  for (let i = 0; i <= text.length; i += 2) { if (id !== typing) return; el.textContent = text.slice(0, i); await sleep(8); }
  el.textContent = text;
}
// ---- the five chapters of the Figma "Laptop companion": the real procedure's steps are grouped into them by kind
// A plan Claude wrote for a fault that is not about one key (power, backlight) has no "{K} key".
const KEYL = () => (plan?.generated && !plan.target_key ? "keyboard" : pack?.keys?.[brokenKey]?.label ?? brokenKey);
const fillK = (t) => String(t ?? "").replaceAll("{K}", KEYL());
const DEFAULT_STAGES = [
  { name: "Unplug", kinds: ["cable-out"], head: ["Start with", "the keyboard."], helper: "Your phone stays on camera and voice.\nYour laptop follows the same repair.",
    line: "Unplug your keyboard first.\nThen we’ll focus on the {K} key.", title: "Keyboard overview", mode: "keyboard overview", foot: "The 3D view follows at your pace." },
  { name: "Remove cap", kinds: ["keycap-pull", "puller-place", "switch-pull"], head: ["Take off", "the {K} cap."], helper: "The view moves in on the {K} key,\nthen holds steady while you work.",
    line: "Hook the puller under the {K} keycap.\nPull it straight up.", title: "{K} keycap · lifting off", mode: "{K} key", foot: "“Pause” · “repeat” · “zoom in”" },
  { name: "Align {K} key", kinds: ["socket-inspect", "new-switch-inspect", "switch-align"], head: ["Line up", "the {K} key."], helper: "The view eases into the {K}-key mechanism,\nthen holds steady while you work.",
    line: "Keep the {K} switch level.\nLine it up with the socket below.", title: "{K} keycap · switch stem", mode: "{K} key", foot: "“Pause” · “repeat” · “zoom in”" },
  { name: "Seat keycap", kinds: ["switch-insert", "switch-test", "keycap-place"], head: ["A gentle,", "straight press."], helper: "No rush. The model waits for your\nconfirmation before moving on.",
    line: "Press gently, straight down.\nDon’t force it sideways.", title: "{K} keycap · seated evenly", mode: "{K} key", foot: "“Pause” · “repeat” · “zoom out”" },
  { name: "Test {K}", kinds: ["cable-in-test"], head: ["Give {K}", "a quick test."], helper: "Test on the keyboard you repaired,\nthen confirm by voice when it works.",
    line: "Try the {K} key a few times.\nDoes it feel and type normally?", title: "{K} key · ready to test", mode: "{K} key", foot: "The session stays open until you confirm.", sub: "Confirming the repair" },
];
// what the person says (or clicks) to confirm each step and move on
const CONFIRM = { "cable-out": "unplugged", "keycap-pull": "cap off", "puller-place": "puller on", "switch-pull": "switch out", "socket-inspect": "socket clear",
  "new-switch-inspect": "switch ready", "switch-align": "aligned", "switch-insert": "inserted", "switch-test": "switch works", "keycap-place": "seated", "cable-in-test": "{K} works" };
let STAGES = DEFAULT_STAGES, stageOf = [], finished = false;
function buildStages() {
  if (plan.generated) {   // a plan Claude wrote: one chapter per step, worded from the step itself
    STAGES = plan.steps.map((s, i) => ({
      name: s.title || `Step ${i + 1}`, kinds: [s.kind], head: [`Step ${i + 1}`, s.title || ""],
      helper: "Your phone stays on camera and voice.\nThe 3D view follows at your pace.", line: s.instruction,
      title: `${plan.title || "Repair"} · ${s.title || i + 1}`, mode: plan.title || "repair", foot: "“Pause” · “repeat” · “zoom in”" }));
    stageOf = plan.steps.map((_, i) => i);
    return;
  }
  STAGES = DEFAULT_STAGES;
  const n = plan.steps.length, mapped = plan.steps.every((s) => STAGES.some((g) => g.kinds.includes(s.kind)));
  let last = 0;
  stageOf = plan.steps.map((s, i) => {
    if (!mapped) return Math.min(STAGES.length - 1, Math.floor((i * STAGES.length) / n));   // a different repair: spread evenly
    const j = STAGES.findIndex((g) => g.kinds.includes(s.kind)); if (j >= 0) last = j; return last;
  });
}
const stageNow = () => (stepIdx >= 0 ? STAGES[stageOf[stepIdx]] : null);
function setMode(t) {
  const g = stageNow(), el = $("mode");
  el.textContent = /^(assembled|scanning)$/.test(t) && stepIdx < 0 ? "Auto-follow · keyboard overview"
    : /^repair/.test(t) && g ? "Auto-follow · " + fillK(g.mode) : t.charAt(0).toUpperCase() + t.slice(1);
}
function telemetry() { $("telemetry").textContent = `${Object.keys(parts).length - 1} parts · ${selected ?? "none selected"}`; }
function dots() { renderStages(); }
function renderStages() {
  const cur = finished ? STAGES.length : stepIdx >= 0 ? stageOf[stepIdx] : -1;
  $("stages").innerHTML = STAGES.map((g, i) => {
    const st = i < cur ? "done" : i === cur ? "cur" : "";
    const sub = st === "done" ? "Complete" : st === "cur" ? g.sub || "Current step" : "Up next";
    return `<button class="stage ${st}" data-i="${i}" ${st === "cur" ? 'aria-current="step"' : ""}><span class="sl">${String(i + 1).padStart(2, "0")}  ${esc(fillK(g.name))}</span><span class="ss">${sub}</span></button>`;
  }).join("");
}
$("stages").addEventListener("click", (e) => {
  const b = e.target.closest(".stage"); if (!b) return;
  const first = stageOf.indexOf(Number(b.dataset.i));
  if (first >= 0) { stopTour(); goStep(first); }
});
const pad2 = (n) => String(n).padStart(2, "0");
function caption(text) { $("captText").textContent = text || ""; }
/** The right-hand card for the step being shown: chapter headline, the real instruction, how to confirm it. */
function setGuidance(i) {
  const s = plan.steps[i], si = stageOf[i], g = STAGES[si];
  $("sn").textContent = `STEP ${pad2(si + 1)} OF ${pad2(STAGES.length)}`;
  $("head").innerHTML = g.head.map((l) => `<span>${esc(fillK(l))}</span>`).join("");
  $("helper").textContent = fillK(g.helper);
  $("ctaLabel").textContent = `Say “${fillK(CONFIRM[s.kind] || "next")}”`; $("foot").textContent = fillK(g.foot);
  $("sceneTitle").textContent = fillK(g.title);
  $("sk").textContent = `KEY ${KEYL()} · ${s.kind.replace(/-/g, " ")}`;
  caption(fillK(g.line));
}
function setReady() {
  finished = false; stepIdx = -1;
  $("sn").textContent = "READY";
  $("head").innerHTML = chosen ? "<span>Ready when</span><span>you are.</span>" : "<span>What needs</span><span>fixing?</span>";
  $("si").textContent = chosen ? "Say “start” to begin." : "Just tell me what’s wrong. For example: “my space bar rattles”.";
  $("helper").textContent = "Your phone stays on camera and voice.\nYour laptop follows the same repair.";
  $("sd").textContent = ""; $("sm").textContent = ""; $("tools").innerHTML = "";
  $("ctaLabel").textContent = "Say “start”"; $("foot").textContent = "The 3D view follows at your pace.";
  $("sceneTitle").textContent = "Keyboard overview";
  caption(`Hi, I’m Spanner.\nShow me the keyboard and we’ll begin.`);
  renderStages();
}
function finishRepair() {
  finished = true; clearTags(); clearArrow(); setHighlight([]);
  $("sn").textContent = "ALL DONE";
  $("head").innerHTML = plan.generated ? `<span>Repair</span><span>complete.</span>` : `<span>${esc(KEYL())} key</span><span>is fixed.</span>`;
  $("si").textContent = plan.generated ? "Test it before you rely on it. Nice work." : `The ${KEYL()} key types normally. Nice work.`;
  $("helper").textContent = "Replug and use it as normal.\nSay “start over” to run the repair again.";
  $("sd").textContent = ""; $("sm").textContent = ""; $("tools").innerHTML = "";
  $("ctaLabel").textContent = "Say “start over”"; $("foot").textContent = "The session stays open until you close it.";
  $("sceneTitle").textContent = `${KEYL()} key · repaired`;
  setMode("repair complete"); renderStages();
  const done = `All done. Test the ${KEYL()} key before you rely on it.`; caption(done); narrate(done, false);
}
/** One step on: the same action for the button, "next", and the step's own confirm word. */
function advance() {
  if (finished) return $("reset").onclick();
  if (stepIdx < 0) return goStep(0);
  if (stepIdx >= plan.steps.length - 1) return finishRepair();
  return goStep(stepIdx + 1);
}
$("cta").onclick = () => { stopTour(); advance(); };
$("btnPause").onclick = () => {
  motionPaused = !motionPaused; $("btnPause").classList.toggle("on", motionPaused); $("btnPause").setAttribute("aria-pressed", motionPaused);
  $("btnPause").textContent = motionPaused ? "Resume motion" : "Pause motion"; if (motionPaused) { controls.autoRotate = false; cancelTweens("cam"); flying = false; controls.enabled = true; }
};
const TOOL_OF = { "keycap-pull": "KEYCAP PULLER", "puller-place": "SWITCH PULLER", "switch-pull": "SWITCH PULLER", "socket-inspect": "COMPRESSED AIR",
  "new-switch-inspect": "TWEEZERS · SPARE SWITCH", "switch-align": "SPARE SWITCH", "switch-insert": "SPARE SWITCH", "switch-test": "FINGER",
  "cable-out": "USB-C CABLE", "cable-in-test": "USB-C CABLE · KEY TESTER", "keycap-place": "HANDS" };

let ac = null;
function blip(f = 880, d = 0.05) {
  if (INSTANT) return;
  try { ac = ac || new AudioContext(); const o = ac.createOscillator(), g = ac.createGain();
    o.frequency.value = f; o.type = "sine"; g.gain.setValueAtTime(0.035, ac.currentTime); g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + d);
    o.connect(g).connect(ac.destination); o.start(); o.stop(ac.currentTime + d); } catch { /* no audio */ }
}

let runId = 0;
// the learning loop: mistakes other people made at this step (2+ sessions, from Databricks) become a warning
let learned = {}, warnedSteps = new Set();
let learnedBusy = false;
function refreshLearned() {
  if (learnedBusy) return;   // the server can be slow (Databricks): never stack requests behind a stuck one
  learnedBusy = true;
  fetchJson(API + "/coach/learned", 5000).then((d) => { learned = d.learned || {}; }).catch(() => {}).finally(() => { learnedBusy = false; });
}
function warnLearned(s) {
  if (plan.generated) return;   // learned mistakes are keyed by the canned plan's step numbers
  const m = learned[s.index]; if (!m || warnedSteps.has(s.index) || INSTANT) return;
  warnedSteps.add(s.index);
  $("sm").textContent = `⚠ Learned from earlier sessions: people often ${m} here`;
  setTimeout(() => { showTag(`switch_${brokenKey}`, "LEARNED · " + m.toUpperCase().slice(0, 48), "red", 3.2); narrate(`Heads up. People often ${m} at this step. That came from earlier repair sessions.`); }, 2200);
}
refreshLearned(); setInterval(refreshLearned, 30000);
let chosen = false;   // true once a plan exists for what the person said is wrong (loadPlan), never before
async function goStep(i, note = "", silent = false) {
  if (!chosen) { narrate("Tell me what’s wrong first, and I’ll plan the repair."); return; }
  if (!plan.steps.length) return;
  if (mistakeOn) { mistakeOn = false; $("mistake").classList.remove("on"); $("fixit").style.display = "none"; }
  const id = ++runId;
  stepIdx = Math.max(0, Math.min(i, plan.steps.length - 1));
  const s = plan.steps[stepIdx];
  finished = false;
  clearTags(); clearArrow(); pulseT0 = 0;
  setMode(`repair · step ${s.index}/${plan.steps.length}${note}`); blip(990);
  setGuidance(stepIdx);
  typeText($("si"), s.instruction);
  $("sd").textContent = `Step ${s.index} of ${plan.steps.length}` + (s.expected_visual_state ? ` · ✓ Done when: ${s.expected_visual_state}` : "");
  $("sm").textContent = s.common_mistakes?.length ? `⚠ ${s.common_mistakes[0]}` : "";
  warnLearned(s);
  $("tools").innerHTML = (TOOL_OF[s.kind] || "HANDS").split(" · ").map((t) => `<span class="chip">${t}</span>`).join("");
  dots();
  if (!silent) narrate(`Step ${s.index}. ${s.instruction}`, false);   // the card already shows the short coach line
  await applyState(stateBefore(stepIdx), 450);
  if (id !== runId) return;
  for (const c of s.scene3d || []) { await applyCommand(c); if (id !== runId) return; }
  await (ACT[s.kind] || ACT.other)(s);
  if (id === runId) world = stateAfter(stepIdx);
}

async function takeApart() {
  setMode("exploded view"); blip(660); setHighlight([]); clearTags(); clearArrow();
  rotateTo("iso");
  for (const g of ["keycaps", "stabilizers", "switches", "plate", "hotswap_sockets", "leds", "pcb", "battery"]) { animAmount(g, 1, 700); await sleep(140); }
  $("ex").value = 100;
}
async function reassemble() {
  setMode("reassembling"); blip(520); setHighlight([]);
  for (const g of ["battery", "pcb", "leds", "hotswap_sockets", "plate", "switches", "stabilizers", "keycaps"]) { animAmount(g, 0, 650); await sleep(140); }
  $("ex").value = 0; await sleep(700); setMode("assembled");
}
async function rewind() {   // undo the repair step by step: the world goes back to the state before each step
  stopTour();
  for (let i = stepIdx; i >= 0; i--) {
    const s = plan.steps[i]; stepIdx = i; finished = false; dots(); setMode(`rewind · step ${s.index}`); $("sn").textContent = `REWIND ${s.index}`;
    typeText($("si"), "Undoing: " + s.instruction); clearTags(); clearArrow();
    for (const c of s.scene3d || []) if (c.op === "rotate_to") await applyCommand(c);
    await applyState(stateBefore(i), 900); await sleep(500);
  }
  setReady(); setMode("assembled");
}
async function tour() {
  touring = !touring; $("tour").classList.toggle("on", touring); $("tour").textContent = touring ? "■ STOP" : "▶ TOUR";
  for (let i = Math.max(0, stepIdx + 1); touring && i < plan.steps.length; i++) { await goStep(i); if (!touring) break; await sleep(2800); }
  if (touring) tour();
}
const stopTour = () => { if (touring) tour(); };

// ------------------------------------------------------------------ inspector, picking, panels
function keyOf(name) { const m = /^(keycap|switch|socket|led|stabilizer)_(.+)$/.exec(name); return m && m[2] !== "spare" ? m[2] : null; }
function specRows(group) {
  const want = SPEC_KEYS[group] || [], rows = (pack.specs?.specs || []).filter((s) => want.some((w) => s.name.toLowerCase().includes(w)));
  return rows.slice(0, 4).map((s) => `<tr><td>${s.name}</td><td>${s.value} ${s.unit || ""} <a class="src" href="${s.source_url}" target="_blank" title="source">↗</a></td></tr>`).join("");
}
function select(name) {
  selected = name; telemetry(); refreshOutline();
  if (!name) { $("insp").style.display = "none"; return; }
  const g = parts[name].group, [title, text] = ROLE[g] || [name, ""], k = keyOf(name);
  $("ih").textContent = k ? `${title} · ${pack.keys[k]?.label ?? k}` : title;
  $("ip").textContent = text + (k === brokenKey ? "  ← this is the broken key." : "");
  $("it").innerHTML = specRows(g);
  $("pull").style.display = $("push").style.display = k ? "" : "none";
  $("open").style.display = g === "switches" || g === "spare" ? "" : "none";
  $("open").textContent = openSwitch === name ? "CLOSE SWITCH" : "OPEN SWITCH";
  $("insp").style.display = "block"; $("side").style.display = "none"; blip(1200, 0.03);
}
const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
function pick(ev) {
  const r = renderer.domElement.getBoundingClientRect();
  mouse.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(mouse, camera);
  const hit = ray.intersectObjects(pickables, false).find((h) => h.object.parent?.visible !== false);
  return hit?.object.userData.part ?? null;
}
renderer.domElement.addEventListener("pointermove", (ev) => {
  lastInput = performance.now(); controls.autoRotate = false;
  const n = flying ? null : pick(ev);
  if (n !== hovered) { hovered = n; refreshOutline(); renderer.domElement.style.cursor = n ? "pointer" : "default"; }
  const tip = $("tip");
  if (n) { const [t] = ROLE[parts[n].group] || [n]; const k = keyOf(n);
    tip.innerHTML = `<b>${t}</b>${k ? " · " + (pack.keys[k]?.label ?? k) : ""}`;
    tip.style.display = "block"; tip.style.left = ev.clientX + 16 + "px"; tip.style.top = ev.clientY + 14 + "px";
  } else tip.style.display = "none";
});
let down = null;
renderer.domElement.addEventListener("pointerdown", (ev) => { down = [ev.clientX, ev.clientY]; lastInput = performance.now(); controls.autoRotate = false; });
renderer.domElement.addEventListener("pointerup", (ev) => {
  if (!down || Math.hypot(ev.clientX - down[0], ev.clientY - down[1]) > 5) return;
  stopTour(); select(pick(ev));
});
renderer.domElement.addEventListener("dblclick", (ev) => { const n = pick(ev); if (n) { select(n); focusPart(n); } });

async function openSwitchAnim(name, open) {
  const sp = switchParts[name], k = open ? 1 : 0;
  await Promise.all([tweenNum(sp.top.position, "y", sp.top.userData.y0 + 1.1 * k, 600), tweenNum(sp.stem.position, "y", sp.stem.userData.y0 + 0.75 * k, 600),
    tweenNum(sp.spring.position, "y", sp.spring.userData.y0 + 0.42 * k, 600), tweenNum(sp.leaf.position, "y", sp.leaf.userData.y0 + 0.25 * k, 600),
    animLift(name, open ? 1.6 : 0, 600)]);
}
$("open").onclick = async () => {
  if (!selected) return; const was = openSwitch;
  if (was && was !== selected) await openSwitchAnim(was, false);
  openSwitch = was === selected ? null : selected; $("open").textContent = openSwitch ? "CLOSE SWITCH" : "OPEN SWITCH";
  blip(760); if (openSwitch) focusPart(selected); await openSwitchAnim(selected, !!openSwitch);
};
$("pull").onclick = async () => { const k = keyOf(selected); if (!k) return; blip(760); setHighlight([`keycap_${k}`, `switch_${k}`]); focusPart(`switch_${k}`);
  await animLift(`keycap_${k}`, 4.5); await animLift(`switch_${k}`, 2.4); };
$("push").onclick = async () => { const k = keyOf(selected); if (!k) return; blip(560); await animLift(`switch_${k}`, 0); await animLift(`keycap_${k}`, 0); setHighlight([]); };
$("focus").onclick = () => selected && focusPart(selected);

const host = (u) => { try { return new URL(u).hostname; } catch { return "source"; } };
function showSide(tab) {
  const open = $("side").style.display === "block" && $("side").dataset.tab === tab;
  for (const t of ["specs", "sources", "parts"]) $("tab-" + t).classList.toggle("on", !open && t === tab);
  if (open) { $("side").style.display = "none"; return; }
  $("side").dataset.tab = tab; $("side").style.display = "block"; $("insp").style.display = "none";
  const S = pack.specs || {};
  if (tab === "specs") {
    $("sideh").textContent = "SPECIFICATIONS · " + (S.product_name || "RK61");
    $("sidebody").innerHTML = `<table>${(S.specs || []).map((s) => `<tr><td>${s.name}</td><td>${s.value} ${s.unit || ""}<br><a class="src" href="${s.source_url}" target="_blank">${host(s.source_url)} ↗</a></td></tr>`).join("")}</table>
      <p style="margin-top:10px">${S.official_product_url ? `<a href="${S.official_product_url}" target="_blank">Official product page ↗</a> · ` : ""}${S.manual_url ? `<a href="${S.manual_url}" target="_blank">User manual (PDF) ↗</a>` : ""}</p>
      <p class="src">Specs researched live with Linkup; every value links to where it was read.</p>`;
  } else if (tab === "sources") {
    const srcs = plan.sources || [], tuts = card?.tutorial ? [card.tutorial] : [];
    $("sideh").textContent = "WHERE THIS REPAIR COMES FROM";
    $("sidebody").innerHTML = `<p>The ${plan.steps.length}-step procedure was built by Claude from repair videos (watched via Gemini, never downloaded) and web guides found with Tavily, then quality-checked and served from Databricks.</p>
      ${srcs.map((s) => `<div class="offer"><b>${s.kind.toUpperCase()}</b> · <a href="${s.url}" target="_blank">${s.title}</a></div>`).join("") || "<p class='src'>no sources recorded</p>"}
      ${tuts.map((t) => `<div class="offer"><b>TUTORIAL</b> · <a href="${t.url}" target="_blank">${t.title}</a>${t.chapter ? ` <span class="src">→ ${t.chapter}</span>` : ""}</div>`).join("")}
      ${card?.manual ? `<div class="offer"><b>MANUAL</b> · <a href="${card.manual.url}" target="_blank">${card.manual.title}</a></div>` : ""}`;
  } else {
    $("sideh").textContent = "REPLACEMENT PART · LINKUP";
    if (!card) { $("sidebody").innerHTML = "<p>No live replacement card yet: run <code>python -m backend.parts_agent --model \"Royal Kludge RK61\" --part \"...\"</code>.</p>"; return; }
    $("sidebody").innerHTML = `<div class="head">${card.title} · ${card.model}</div>
      ${card.decision ? `<div class="offer"><b>${card.decision.verdict.toUpperCase()}</b> · ${card.decision.headline}<br><span class="src">${card.decision.why}</span></div>` : ""}
      ${card.safety ? `<div class="offer" style="border-color:#ff3d5a88"><b>SAFETY</b> · ${card.safety.text}</div>` : `<p class="src">Recall check (last 2 years): ${card.recall_checked ? "none found" : "not run"}</p>`}
      ${card.options.map((o) => `<div class="offer"><b>${o.retailer}</b> · ${o.price}${o.in_stock === "yes" ? " · in stock" : ""}<br>${o.part}${o.part_number ? ` <span class="src">${o.part_number}</span>` : ""}<br>
        <span class="badge ${o.verified ? "ok" : "warn"}">${o.badge}</span><br><span class="src">${o.why}</span><br><a href="${o.url}" target="_blank">open offer ↗</a>${o.evidence.map((u, i) => ` <a class="src" href="${u}" target="_blank">[${i + 1}]</a>`).join("")}</div>`).join("")}
      ${card.manual ? `<div class="offer"><b>MANUAL</b> · <a href="${card.manual.url}" target="_blank">${card.manual.title}</a>${card.manual.key_fact ? `<br>${card.manual.key_fact}` : ""}</div>` : ""}
      <p class="src">${card.as_of} · ${card.sources_count} sources · verified = fit confirmed by 2+ independent sites</p>`;
  }
}
$("tab-specs").onclick = () => showSide("specs"); $("tab-sources").onclick = () => showSide("sources"); $("tab-parts").onclick = () => showSide("parts");

// ------------------------------------------------------------------ voice: narration + commands
let voice = null;
function pickVoice() {
  const all = speechSynthesis.getVoices(); if (!all.length) return;
  const vs = all.filter((v) => v.localService).length ? all.filter((v) => v.localService) : all;   // network voices can cut off without ever saying they ended
  voice = vs.find((v) => /Ryan|George|Daniel/.test(v.name)) || vs.find((v) => v.lang.startsWith("en-GB")) || vs.find((v) => v.lang.startsWith("en")) || vs[0];
}
if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }
// Spanner's voice: ElevenLabs through the backend (/coach/say), the browser's voice as fallback.
let elevenOK = false, speakingUntil = 0, sayAudio = null, currentSpeech = "";   // currentSpeech: for the echo filter
fetchJson(API + "/coach/health", 4000).then((d) => { elevenOK = !!d.tts; }).catch(() => {});
// While Spanner talks, the mic ignores what it hears (it would hear Spanner). That window must always end: the
// estimate below is based on the text length, and every way a clip can stop (end, error, stall, abort) closes it.
const speechMs = (text) => Math.min(45000, 2500 + text.split(/\s+/).length * 450);
// tell the phone how long we'll be talking, so it pauses its microphone (it would only record our own voice)
let lastSpeakReport = 0;
function reportSpeaking(ms) {
  if (!remoteOn) return;
  const now = performance.now(); if (ms > 0 && now - lastSpeakReport < 300) return; lastSpeakReport = now;
  fetch(API + "/coach/speaking", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ session: SESSION, seconds: ms / 1000 }) }).catch(() => {});
}
function stopSpeech() {
  reportSpeaking(0);
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  if (sayAudio) { try { sayAudio.pause(); } catch { /* already gone */ } sayAudio = null; }
  speakingUntil = 0;
}
function browserSay(text) {
  if (!("speechSynthesis" in window)) { speakingUntil = 0; return; }
  const u = new SpeechSynthesisUtterance(text); if (voice) u.voice = voice; u.rate = 1.03; u.pitch = 0.92;
  const done = () => { speakingUntil = performance.now() + 400; reportSpeaking(0); };
  u.onend = done; u.onerror = done;
  speakingUntil = performance.now() + speechMs(text); currentSpeech = text; reportSpeaking(speechMs(text)); speechSynthesis.speak(u);
}
function narrate(text, show = true) {
  if (!text) return;
  if (show) caption(text);
  if (!voiceOn || INSTANT) return;
  stopSpeech();
  if (!elevenOK) return browserSay(text);
  const a = new Audio(API + "/coach/say?text=" + encodeURIComponent(text));
  sayAudio = a; speakingUntil = performance.now() + speechMs(text) + 4000; currentSpeech = text; reportSpeaking(speechMs(text) + 1000);
  const fallback = () => { if (sayAudio === a) { sayAudio = null; browserSay(text); } };
  a.onended = () => { if (sayAudio === a) { sayAudio = null; speakingUntil = performance.now() + 400; reportSpeaking(0); } };
  a.onerror = a.onstalled = a.onabort = fallback;
  setTimeout(() => { if (sayAudio === a && a.paused && a.currentTime === 0) fallback(); }, 5000);   // never started playing
  a.play().catch(fallback);
}
$("voice").onclick = () => { voiceOn = !voiceOn; $("voice").classList.toggle("on", voiceOn); if (voiceOn) narrate("Voice on. I'm Spanner. Say next, take it apart, or show me the socket."); else stopSpeech(); };
// the voice pill in the guidance card: who is talking right now
setInterval(() => {
  const sp = performance.now() < speakingUntil, st = sp ? "speaking" : micOn ? "listening" : "idle", pill = $("captPill");
  if (pill.dataset.s === st + voiceOn) return; pill.dataset.s = st + voiceOn;
  pill.className = st; $("captState").textContent = sp ? "Spanner is speaking" : micOn ? "Listening to you" : voiceOn ? "Voice on" : "Voice off · tap to wake";
}, 250);
$("captPill").onclick = () => { if (performance.now() < speakingUntil) stopSpeech(); else if (!micOn) $("mic").onclick(); };
$("say").onclick = () => { const s = plan.steps[stepIdx]; if (!s) return; if (!voiceOn) { voiceOn = true; $("voice").classList.add("on"); } narrate(`Step ${s.index}. ${s.instruction}`); };

const PART_WORDS = [[/socket/, () => `socket_${brokenKey}`], [/spring|inside the switch|open the switch/, () => `switch_${brokenKey}`], [/spare|new switch|replacement/, () => "switch_spare"],
  [/switch/, () => `switch_${brokenKey}`], [/keycap|key cap|\bcap\b/, () => `keycap_${brokenKey}`], [/pcb|board|circuit/, () => "pcb"], [/plate/, () => "plate"],
  [/case|shell|tray/, () => "case"], [/battery/, () => "battery"], [/usb|port|cable|plug/, () => "usb_c_port"], [/led|light|backlight/, () => `led_${brokenKey}`],
  [/stabili|space ?bar/, () => "stabilizer_SPACE"], [/mode|bluetooth switch/, () => "mode_switch"]];
function heard(text, action) {
  const h = $("heard"); h.innerHTML = `🎤 <b>“${text}”</b> → ${action}`; h.style.display = "block"; clearTimeout(h._t); h._t = setTimeout(() => (h.style.display = "none"), 3500);
}
const CONFIRM_RE = () => new RegExp("\\b(" + [...new Set(Object.values(CONFIRM).map(fillK))].map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")\\b", "i");
// "my space bar rattles", "the E key is sticky", "I want to fix my keyboard": the person says what is wrong.
const FIX_EXPLICIT = /\b(i (want|need|would like) to (fix|repair)|help me (fix|repair)|can you (fix|repair)|(fix|repair) (my|the|this)|new repair|different problem|something else)\b/i;
const FIX_SYMPTOM = /\b(my|the) [\w\s-]{0,24}\b(is|are|isn'?t|aren'?t|keeps?|won'?t|doesn'?t|don'?t|has|have|got)\b[^.?]*\b(dead|broken|stuck|sticky|jam\w*|rattl\w*|wobbl\w*|loose|missing|cracked|flicker\w*|not (working|registering|typing|charging|turning on|powering)|double[- ]typ\w*|chatter\w*|work|register|type|charge|turn on|power|light)\b/i;
let clarifying = null;   // the complaint Spanner asked a follow-up question about
const midRepair = () => stepIdx >= 0 && !finished;
async function command(raw) {
  const t = raw.toLowerCase().trim(); let said = "";
  if (pickOpen) return pickByVoice(raw);
  if (/\b(start over|choose another|another device|pick a device|main menu|go back to the (menu|start))\b/.test(t)) { heard(raw, "back to the start"); narrate("Starting over."); setTimeout(() => { location.href = "./"; }, 700); return; }
  const say = (s) => { said = s; narrate(s); };
  if (clarifying) { const c = clarifying + ". " + raw; clarifying = null; return requestRepair(c); }
  if (!/^(which|what|where|how|why|is|are)\b/.test(t) && (FIX_EXPLICIT.test(t) || (!midRepair() && FIX_SYMPTOM.test(t)))) { return requestRepair(raw); }
  if (/\b(close|hide|dismiss|go away|that's enough|never ?mind)\b/.test(t)) { closePanels(); said = "closed"; }
  else if (/calibrat|align the hologram|set (this|that) as zero|zero it/.test(t)) { calibratePose(); said = "calibrated"; }
  else if (/use (my|the) phone|phone camera|phone as (the )?camera/.test(t)) { startRemote(); said = "phone camera"; }
  else if (/(other|another|alternative|different|new|better) keyboards?|keyboards? (instead|to buy)|instead of (this|the rk|it)|upgrade|should i buy|what else could i buy/.test(t)) { openShop(raw); said = "searching other keyboards"; }
  else if (/replace(ment)?s?|\bbuy\b|order|price|how much|where (can|do) i get|spare|new (key ?caps?|switch(es)?|stabili[sz]ers?|cable)/.test(t)) { openShop(raw); said = "searching replacements"; }
  else if (/turn (the )?camera off|stop watching|camera off/.test(t)) { if (camOn) toggleCamera(); if (remoteOn) { stopRemote(); setMode("assembled"); } said = "camera off"; }
  else if (/camera|watch me|look at me/.test(t)) { if (!camOn) toggleCamera(); said = "camera on"; }
  else if (/\b(light|dark) (theme|mode)\b/.test(t)) { applyTheme(/light/.test(t) ? "light" : "dark"); said = "theme"; }
  else if (/\b(resume|unpause)\b/.test(t)) { if (motionPaused) $("btnPause").onclick(); said = "motion resumed"; }
  else if (/\b(pause motion|freeze (the )?(view|camera))\b/.test(t)) { if (!motionPaused) $("btnPause").onclick(); said = "motion paused"; }
  else if (/start over|from the beginning/.test(t)) { $("reset").onclick(); say("Starting over."); }
  else if (CONFIRM_RE().test(t)) { stopTour(); advance(); said = "confirmed → next"; }
  else if (stepIdx < 0 && /^(start|begin|let'?s (go|start|begin))\b/.test(t)) { stopTour(); goStep(0); said = "starting"; }
  else if (/\b(repeat|say that again|say it again)\b/.test(t)) { $("say").onclick(); said = "repeating"; }
  else if (/\b(next|continue|go on|proceed|forward)\b/.test(t)) { stopTour(); advance(); said = "next step"; }
  else if (/\b(back|previous)\b/.test(t) && !/back together|put .*back/.test(t)) { stopTour(); goStep(stepIdx - 1); said = "previous step"; }
  else if (/take .*apart|explode|open it up|blow it up|exploded/.test(t)) { stopTour(); takeApart(); say("Taking it apart."); }
  else if (/put .*(together|back)|assemble|close it/.test(t)) { stopTour(); reassemble(); say("Putting it back together."); }
  else if (/x.?ray|see through|hologram/.test(t)) { setXray(!xray); say(xray ? "X-ray on." : "X-ray off."); }
  else if (/\b(reset|from the top)\b/.test(t)) { $("reset").onclick(); say("Reset."); }
  else if (/\b(tour|play|walk me through|show me the repair|start the repair)\b/.test(t)) { if (!touring) tour(); say("Walking you through the repair."); }
  else if (/(stop|pause|hold on|quiet|shut up|stop talking|be quiet|silence|enough)/.test(t)) { if (/pause/.test(t) && !motionPaused) $("btnPause").onclick(); quiet(raw); return; }
  else if (/rewind|reverse|undo/.test(t)) { rewind(); say("Rewinding."); }
  else if (/mistake|go wrong|wrong way|what not to do/.test(t)) { showMistake(); said = "showing the mistake"; }
  else if (/fix it|that's wrong|correct it/.test(t)) { fixMistake(); say("Fixed."); }
  else if (/what('s| is) (this|that|it)|what am i looking at/.test(t)) { const g = selected ? parts[selected].group : null; say(g ? ROLE[g][1] : "Nothing is selected. Click a part or say show me the switch."); }
  else if (/which key|broken key|what's broken|what is broken/.test(t)) { setHighlight([`keycap_${brokenKey}`]); pulseT0 = performance.now(); focusPart(`keycap_${brokenKey}`); say(`The broken key is ${pack.keys[brokenKey].label}.`); }
  else if (/zoom in|closer/.test(t)) { flyTo(camera.position.clone().lerp(controls.target, 0.35), controls.target.clone(), 600); said = "zoom in"; }
  else if (/zoom out|further|step back/.test(t)) { flyTo(camera.position.clone().sub(controls.target).multiplyScalar(1.5).add(controls.target), controls.target.clone(), 600); said = "zoom out"; }
  else if (/spec/.test(t)) { showSide("specs"); say("Specifications."); }
  else if (/source|where did|evidence|cite/.test(t)) { showSide("sources"); say("Sources."); }
  else if (/\b(parts? card|repair or replace|worth (it|repairing))\b/.test(t)) { showSide("parts"); say(card?.decision ? card.decision.headline : "Here is the parts card."); }
  else if (/lights? on|rgb on|backlight on/.test(t)) { setRGB(true); said = "lights on"; }
  else if (/lights? off|rgb off|backlight off/.test(t)) { setRGB(false); said = "lights off"; }
  else if (/show me|look at|focus on|go to|select/.test(t)) {
    const hit = PART_WORDS.find(([re]) => re.test(t));
    if (hit) { const n = hit[1](); stopTour(); select(n); if (/spring|inside|open/.test(t)) { openSwitch = null; await $("open").onclick(); } else focusPart(n); say(`Showing the ${(ROLE[parts[n].group]?.[0] || n).toLowerCase()}.`); }
    else said = "no such part";
  } else if (/top view|from above/.test(t)) { rotateTo("top"); said = "top view"; }
  else if (/side view/.test(t)) { rotateTo("side"); said = "side view"; }
  else if (/behind|rear|back view/.test(t)) { rotateTo("back"); said = "back view"; }
  else if (/step (\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven)/.test(t)) {
    const words = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven"], m = /step (\d+|\w+)/.exec(t);
    const n = parseInt(m[1]) || words.indexOf(m[1]) + 1; if (n > 0) { stopTour(); goStep(n - 1); said = `step ${n}`; }
  } else { said = "…"; heard(raw, "thinking"); return intentFallback(raw); }
  heard(raw, said);
}
// ---- every spoken sentence, from the laptop mic or the phone mic, comes in here
const QUICK = /^(?:s*(?:ok(?:ay)?|hey|spanner|please|and|now|just)[,s]*)*(close|stop|quiet|shut up|stop talking|be quiet|enough|pause|next|back|calibrate|reset|rewind|x-?ray|lights (?:on|off)|take it apart|put it back|zoom (?:in|out))/i;
const echoWords = (s) => new Set(s.toLowerCase().replace(/[^a-z0-9' ]/g, " ").split(/s+/).filter((w) => w.length > 2));
const STOCK = /what do you want to fix|i'm spanner|i am spanner|show it to me|step by step|replacement parts or other keyboards|heads up|people often|searching live|taking it apart|putting it back|starting over|let me work out the fix|scan the code/i;
/** Spanner hearing its own voice: its stock phrases, or most of the words are in what it is saying (or just said). */
function isEcho(text, graceMs = 1500) {
  if (STOCK.test(text)) return true;
  if (performance.now() > speakingUntil + graceMs || !currentSpeech) return false;
  const a = echoWords(text), b = echoWords(currentSpeech); if (!a.size) return true;
  let n = 0; for (const w of a) if (b.has(w)) n++; return n / a.size >= 0.6;
}
let lastActed = { text: "", t: 0 };
/** While Spanner talks only a clean short command gets through (barge-in); its own voice is dropped.
 *  graceMs: how long after it stops talking an echo can still arrive (the phone's clips are transcribed a few s later). */
function hearSpeech(text, graceMs = 1500) {
  const now = performance.now();
  if (!text || !text.trim()) return;
  if (text.trim().toLowerCase() === lastActed.text && now - lastActed.t < 4000) return;   // the final of a command already run
  if (now < speakingUntil + graceMs) {
    const q = text.match(QUICK);
    if (!q || isEcho(text, graceMs) || echoWords(currentSpeech).has(q[1].toLowerCase().split(" ")[0])) return;   // ignore silently
    text = q[0];                                                     // a clean short command from you: barge in
  } else if (isEcho(text, graceMs)) { heard(text, "ignored: that was me talking"); return; }
  lastActed = { text: text.trim().toLowerCase(), t: now };
  if (now < speakingUntil) stopSpeech();
  setListen(true, "Listening · " + text.trim().slice(0, 40)); command(text);
}
const stopSpeaking = () => stopSpeech();   // main's name for it (quiet(), ✋)
let rec = null;
function setListen(on, txt) { const l = $("listen"); l.classList.toggle("on", on); l.textContent = txt || (on ? "Listening" : "Mic off"); }
$("mic").onclick = () => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { heard("", "speech recognition needs Chrome or Edge"); setListen(false, "Needs Chrome or Edge"); return; }
  micOn = !micOn; $("mic").classList.toggle("on", micOn); setListen(micOn);
  if (!micOn) { const r = rec; rec = null; try { r?.stop(); } catch { /* already stopped */ } return; }
  // Chrome's speech recogniser stops by itself (silence, network blips). Restart it, but back off and give up visibly
  // after repeated real failures instead of spinning in a tight loop with the mic icon still saying "listening".
  let fails = 0, lastErr = "", recognizing = false, lastEvent = performance.now();
  const r = (rec = new SR()); r.continuous = true; r.interimResults = true; r.lang = "en-GB"; r.maxAlternatives = 1;
  r.onstart = () => { recognizing = true; lastEvent = performance.now(); };
  r.onresult = (e) => {
    fails = 0; lastEvent = performance.now();
    const res = e.results[e.results.length - 1], text = res[0].transcript;
    if (res.isFinal) hearSpeech(text);
    else if (QUICK.test(text)) hearSpeech(text.match(QUICK)[0]);      // "close" acts the moment it is heard
  };
  r.onspeechend = () => { lastEvent = performance.now(); };
  r.onend = () => {
    recognizing = false;
    if (!micOn || rec !== r) return;
    fails = /^(|no-speech|aborted)$/.test(lastErr) ? 0 : fails + 1; lastErr = "";
    if (fails > 6) { micOn = false; $("mic").classList.remove("on"); setListen(false, "Mic stopped · click to retry"); return; }
    setTimeout(() => { if (micOn && rec === r) try { r.start(); } catch { /* already running */ } }, Math.min(4000, 100 * fails * fails));
  };
  r.onerror = (e) => {
    lastErr = e.error;
    if (e.error === "not-allowed" || e.error === "service-not-allowed") { micOn = false; $("mic").classList.remove("on"); setListen(false, "Mic blocked · allow it in the address bar"); return; }
    if (e.error === "network") setListen(true, "Listening · reconnecting");
    else if (e.error !== "no-speech" && e.error !== "aborted") heard("", "mic error: " + e.error);
  };
  clearInterval(window._micDog); window._micDog = setInterval(() => {   // watchdog: Chrome sometimes stops listening silently
    if (!micOn || rec !== r) return;
    if (!recognizing || performance.now() - lastEvent > 20000) { try { r.abort(); } catch {} try { r.start(); } catch {} lastEvent = performance.now(); }
  }, 5000);
  try { r.start(); } catch { /* the end handler retries */ }
  if (!voiceOn) { voiceOn = true; $("voice").classList.add("on"); }
};

// ------------------------------------------------------------------ live shopping on request (Linkup + Tavily via /coach/shop)
const esc = (x) => String(x ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const money = (p, c) => (p > 0 ? `${{ GBP: "£", USD: "$", EUR: "€" }[c] || (c ? c + " " : "")}${Number(p).toFixed(2)}` : "see price ↗");
const moneyGbp = (i) => money(i.price, i.currency) + (i.price_gbp && i.currency !== "GBP" ? ` <small style="opacity:.6">≈ £${i.price_gbp.toFixed(2)}</small>` : "");
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
function closePanels() { clearTimeout(verifyTimer); shopReq++; for (const id of ["shop", "side", "insp"]) $(id).style.display = "none"; select(null); }
let shopReq = 0, verifyTimer = null;
const cites = (d) => (d.sources?.length ? `<div class="cites">LINKUP READ ${d.sources_count} PAGES · ${d.sources.slice(0, 8).map((x) => `<a href="${esc(x.url)}" target="_blank">${esc(hostOf(x.url))} ↗</a>`).join("")}</div>` : "");
function verifyBox(v) {
  if (!v || v.status === "researching") return `<div class="verify pending"><div class="vh">LINKUP AGENT · VERIFYING<span class="dots"></span></div><div class="meta">checking fit on 2+ independent sites · safety recalls (last 2 years) · price of a new RK61 · repair or replace</div></div>`;
  if (v.status !== "ready" || !v.card) return "";
  const c = v.card, dec = c.decision;
  return `<div class="verify"><div class="vh">✓ VERIFIED BY THE LINKUP AGENT${v.fresh ? "" : " · " + esc(c.as_of).toUpperCase()}${v.refreshing ? " · REFRESHING" : ""}</div>
    ${dec ? `<div class="dec">${esc(dec.headline).replace(/^(Repair it:)/, "<b>$1</b>")}</div>` : ""}
    <div class="meta">${c.recall_checked ? (c.safety ? "⚠ " + esc(c.safety.text) : "No safety recalls found in the last 2 years") : ""} · ${c.sources_count} sources read · ${esc(c.as_of)}</div>
    <div class="vrow">${c.options.map((o) => `<div class="vo"><div class="pr">${esc(o.price)}</div><div>${esc(o.part)}</div><div class="rt">${esc(o.retailer)}</div>
      <span class="badge ${o.verified ? "ok" : "no"}">${esc(o.badge).toUpperCase()}</span>
      <div class="ev"><a href="${esc(o.url)}" target="_blank">buy ↗</a>${(o.evidence || []).slice(0, 4).map((u, i) => `<a href="${esc(u)}" target="_blank">[${i + 1}]</a>`).join("")}</div></div>`).join("")}</div></div>`;
}
async function openShop(raw, auto = false) {
  const id = ++shopReq, el = $("shop"); clearTimeout(verifyTimer);
  $("side").style.display = "none"; $("insp").style.display = "none";
  el.style.display = "block";
  const autoTag = auto ? `<span class="auto">● SPANNER STARTED THIS ON ITS OWN</span>` : "";
  el.innerHTML = `<h3>SEARCHING THE WEB LIVE${autoTag}</h3><div class="searching">LINKUP · TAVILY · “${esc(raw)}”</div>`;
  if (!auto) narrate("Searching live.");
  // replacement parts: the deep verification agent runs alongside the quick search
  let verified = null, spoken = false, verifyPolls = 0;
  const isPart = !/(other|another|alternative|different|new|better) keyboards?|keyboards? (instead|to buy)|instead of|upgrade|should i buy|what else could i buy/i.test(raw);
  const pollVerify = async () => {
    if (id !== shopReq || !isPart) return;
    try {
      const r = await fetchT(API + "/coach/shop/verified?query=" + encodeURIComponent(raw), {}, 8000);
      if (r.ok) verified = await r.json();
    } catch { /* keep polling */ }
    if (++verifyPolls > 45) { if (verified?.status === "researching") verified = { status: "failed" }; return; }   // about 3 minutes, then stop
    if (id !== shopReq) return;
    const box = el.querySelector("#vbox"); if (box) box.innerHTML = verifyBox(verified);
    if (verified?.status === "ready" && verified.card?.decision && !spoken && el.querySelector("#vbox")) {
      spoken = true; narrate(verified.card.decision.headline);
    }
    if (!verified || verified.status === "researching" || verified.refreshing) verifyTimer = setTimeout(pollVerify, 4000);
  };
  if (isPart) pollVerify();
  try {
    const r = await fetchT(API + "/coach/shop", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ query: raw }) }, 75000);
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 120)}`);
    const d = await r.json(); if (id !== shopReq) return;
    const src = `LIVE · LINKUP + TAVILY · ${(d.latency_ms / 1000).toFixed(1)} s${d.cached ? " · CACHED" : ""}`;
    const autoTag = auto ? `<span class="auto">● SPANNER STARTED THIS ON ITS OWN</span>` : "";
    const img = (u) => (u ? `<img src="${esc(u)}" referrerpolicy="no-referrer" onerror="this.remove()">` : "");
    if (d.kind === "keyboard") {
      const ri = d.repair_instead ? `<div class="verify"><div class="vh">BEFORE YOU BUY · FIXING YOUR RK61 (LINKUP AGENT, ${esc(d.repair_instead.as_of).toUpperCase()})</div><div class="dec">${esc(d.repair_instead.headline).replace(/^(Repair it:)/, "<b>$1</b>")}</div></div>` : "";
      el.innerHTML = `<h3>OTHER KEYBOARDS · VS YOUR RK61${autoTag}</h3><div class="sub">${src}</div>${ri}<div class="grid">${d.items.map((k, i) => `
        <div class="it" style="animation-delay:${i * 70}ms">${img(k.image_url)}
          <div class="nm">${esc(k.name)}</div><div class="pr">${moneyGbp(k)}</div>
          <ul>${(k.pros || []).map((x) => `<li class="p">${esc(x)}</li>`).join("")}${(k.cons || []).map((x) => `<li class="c">${esc(x)}</li>`).join("")}</ul>
          <a href="${esc(k.url)}" target="_blank">${esc(hostOf(k.url))} ↗</a></div>`).join("")}</div>${cites(d)}`;
      const c = d.items.filter((k) => k.price_gbp > 0).sort((a, b) => a.price_gbp - b.price_gbp)[0];
      narrate(`I found ${d.items.length} alternatives.${c ? ` The cheapest is the ${c.name} at about £${Math.round(c.price_gbp)}.` : ""}${d.repair_instead ? " But fixing yours is far cheaper." : ""}`);
    } else {
      const fit = d.items.filter((o) => o.compatible);
      el.innerHTML = `<h3>REPLACEMENT ${esc(d.item.split(" (")[0]).toUpperCase()} · FOR YOUR RK61${autoTag}</h3><div class="sub">${src}</div><div id="vbox">${verifyBox(verified)}</div><div class="sub">MORE OPTIONS · QUICK SEARCH</div><div class="grid">${d.items.map((o, i) => `
        <div class="it" style="animation-delay:${i * 70}ms">${img(o.image_url)}
          <div class="nm">${esc(o.name)}</div><div class="pr">${moneyGbp(o)}</div><div class="rt">${esc(o.retailer)}</div>
          <span class="badge ${o.compatible ? "ok" : "no"}">${o.compatible ? "✓ FITS YOUR RK61" : "✗ WON'T FIT"}</span>
          <div class="why">${esc(o.compatibility)}</div>
          <a href="${esc(o.url)}" target="_blank">${esc(hostOf(o.url))} ↗</a></div>`).join("")}</div>${cites(d)}`;
      if (!spoken) {
        const c = fit.filter((o) => o.price > 0).sort((a, b) => a.price - b.price)[0];
        narrate(`${auto ? "Your old switch is out, so you'll need a new one. " : ""}${fit.length} of ${d.items.length} fit your RK61.${c ? ` Cheapest that fits: ${money(c.price, c.currency)}, from ${c.retailer}.` : ""} I'm verifying them now.`);
      }
    }
  } catch (e) {
    if (id !== shopReq) return;
    el.innerHTML = `<h3>SEARCH FAILED</h3><div class="searching bad">${esc(e.message)}</div><div id="vbox">${verifyBox(verified)}</div>`; narrate("The live search failed. Ask me again.");
  }
}
// Nothing matched the fast word rules: ask Claude what was meant, then run that command.
const INTENT_TEXT = { next: "next", back: "back", close: "close", take_apart: "take it apart", reassemble: "put it back together",
  xray: "x-ray", reset: "reset", tour: "tour", stop: "stop", rewind: "rewind", show_mistake: "show me the mistake", fix_it: "fix it",
  camera_on: "camera on", camera_off: "camera off", zoom_in: "zoom in", zoom_out: "zoom out", lights_on: "lights on", lights_off: "lights off",
  specs: "specs", sources: "sources" };
async function intentFallback(raw) {
  try {
    const r = await fetchT(API + "/coach/intent", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: raw, current_step: stepIdx + 1 }) }, 10000);
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    if (d.command === "none") { heard(raw, "not for me"); return; }
    if (d.command === "ask") { heard(raw, "asking Spanner"); return askCoach(raw); }
    if (d.command === "new_repair") { heard(raw, "planning a repair"); return requestRepair(raw); }
    if (d.command === "calibrate") { calibratePose(); heard(raw, "calibrated"); return; }
    if (d.command === "shop_parts") { heard(raw, "searching replacements"); return openShop(d.shop_query || raw); }
    if (d.command === "shop_keyboards") { heard(raw, "searching other keyboards"); return openShop("show me other keyboards " + (d.shop_query || "")); }
    if (d.command === "show_part") { heard(raw, "showing " + d.part); return command("show me the " + (d.part || "switch")); }
    const phrase = INTENT_TEXT[d.command]; if (phrase) { heard(raw, "→ " + phrase); return command(phrase); }
    heard(raw, "not understood");
  } catch (e) { heard(raw, "not understood"); }
}
/** Swap in a plan: put the old key back first, then re-aim everything at the new key and chapters. */
async function loadPlan(d) {
  stopTour(); runId++; clearTags(); clearArrow(); setHighlight([]); hideTools(); pulseT0 = 0;
  if (mistakeOn) { mistakeOn = false; $("mistake").classList.remove("on"); $("fixit").style.display = "none"; }
  await applyState({ ...INITIAL }, 0);          // restores the old key's cap and switch while brokenKey still points at it
  plan = d.plan; card = d.parts_card || null; chosen = true;
  brokenKey = pack.keys[d.broken_key] ? d.broken_key : "F"; K = pack.keys[brokenKey];
  for (const s of plan.steps) if (!s.kind) s.kind = "other";
  warnedSteps.clear(); world = { ...INITIAL }; agree = 0; lastDoneStep = -1; stepIdx = -1; finished = false;
  $("sessTitle").textContent = plan.generated ? plan.title || "Repair" : KEYL() + " key replacement";
  buildStages(); setReady(); setMode("assembled");
  await rotateTo("iso");
}
let planning = false;
/** What the person said they want fixed -> Claude writes the plan for it -> the hologram plays that plan. */
async function requestRepair(raw) {
  if (planning) return;
  planning = true; stopTour(); clarifying = null;
  heard(raw, "planning the repair");
  setMode("Planning the repair"); $("sn").textContent = "PLANNING"; caption("Working out the fix…\n“" + raw + "”");
  narrate("Okay. Let me work out the fix.", false);
  try {
    const r = await fetchT(API + "/coach/plan/generate", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ session: SESSION, complaint: raw }) }, 90000);
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    if (d.status === "ready") {
      await loadPlan(d);
      heard(raw, `${plan.steps.length}-step plan`); caption(d.say); narrate(d.say + " Say “start” when you’re ready.", false);
    } else {
      if (d.status === "clarify") clarifying = raw;
      setReady(); heard(raw, d.status); caption(d.say); narrate(d.say, false);
    }
  } catch (e) { setReady(); heard(raw, "couldn’t plan: " + e.message); narrate("Sorry, I couldn’t plan that. Try again."); }
  finally { planning = false; }
}
async function askCoach(q) {
  try {
    const r = await fetchT(API + "/coach/ask", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ question: q, current_step: stepIdx + 1, session: SESSION }) }, 20000);
    if (!r.ok) throw new Error(r.status);
    const d = await r.json(); heard(q, d.answer); narrate(d.answer);
  } catch (e) { heard(q, "couldn't reach Spanner: " + e.message); }
}

// ------------------------------------------------------------------ live camera coach (laptop webcam -> /coach/frame)
// One session id per tab (kept across reloads, or taken from ?s=), so a reload doesn't orphan a paired phone.
const SESSION = QP.get("s") || (() => {
  const fresh = "laptop-" + Math.random().toString(36).slice(2, 8);
  try { let s = sessionStorage.getItem("spanner-session"); if (!s) { s = fresh; sessionStorage.setItem("spanner-session", s); } return s; } catch { return fresh; }
})();
let camOn = false, camBusy = false, camStream = null, lastDetect = 0, agree = 0, lastMistakeAt = 0, lastDoneStep = -1;
const grabCanvas = document.createElement("canvas");

async function grabFrame() {
  const v = $("video"), w = 768, h = Math.round(w * v.videoHeight / v.videoWidth);
  grabCanvas.width = w; grabCanvas.height = h;
  grabCanvas.getContext("2d").drawImage(v, 0, 0, w, h);
  return new Promise((ok) => grabCanvas.toBlob(ok, "image/jpeg", 0.82));
}
function seen(html) { $("seen").innerHTML = html; }

async function coachTick() {
  if (!camOn || remoteOn) return;
  const v = $("video");
  if (!camBusy && v.readyState >= 2 && v.videoWidth) {
    camBusy = true;
    try {
      const fd = new FormData();
      fd.append("image", await grabFrame(), "frame.jpg");
      fd.append("current_step", String(stepIdx + 1)); fd.append("session", SESSION);
      const r = await fetchT(API + "/coach/frame", { method: "POST", body: fd }, 25000);
      if (r.ok) onCoach(await r.json());
      else seen(`<span class="bad">Coach error ${r.status}: ${(await r.text()).slice(0, 120)}</span>`);
    } catch (e) { seen(`<span class="bad">Can't reach the coach (${e.message}). Run: uvicorn backend.main:app</span>`); }
    camBusy = false;
  }
  setTimeout(coachTick, 1500);
}

let autoShopped = false;
function maybeAutoShop(c) {
  const kind = plan.steps[c.step_index - 1]?.kind;
  if (autoShopped || !kind) return;
  if ((kind === "switch-pull" && c.step_status === "done") || ["socket-inspect", "new-switch-inspect"].includes(kind)) {
    autoShopped = true; openShop("replacement switch for my RK61", true);
  }
}
// the key the camera sees missing becomes the key we repair (two frames must agree; exact legend match only)
let missingSeen = { key: null, n: 0 };
function keyIdForLegend(legend) {
  const L = String(legend || "").trim().toLowerCase(); if (!L) return null;
  for (const [id, k] of Object.entries(pack.keys)) if (id.toLowerCase() === L || String(k.label || "").toLowerCase() === L) return id;
  const alias = { return: "ENTER", esc: "ESC", escape: "ESC", space: "SPACE", spacebar: "SPACE", backspace: "BACKSPACE", tab: "TAB", caps: "CAPS", "caps lock": "CAPS", shift: "LSHIFT", ctrl: "LCTRL", control: "LCTRL", alt: "LALT", win: "WIN", fn: "FN", menu: "MENU" };
  return pack.keys[alias[L]] ? alias[L] : null;
}
function setBrokenKey(id, why) {
  if (!pack.keys[id] || id === brokenKey) return;
  brokenKey = id; applyState(INITIAL, 0); clearTags(); clearArrow();
  select(null); setHighlight([`keycap_${id}`]); pulseT0 = performance.now(); focusPart(`keycap_${id}`);
  $("sk").textContent = `KEY ${pack.keys[id].label}`; showTag(`keycap_${id}`, `${pack.keys[id].label} · THIS IS THE KEY WE FIX`, "ok", 1.2);
  narrate(`I can see the ${pack.keys[id].label} keycap is off. That's the key we're fixing.`); heard(why || "camera", "broken key → " + pack.keys[id].label);
  if (stepIdx >= 0) goStep(stepIdx, " · key " + pack.keys[id].label, true);
}
function onMissingKeys(c) {
  if (plan?.generated && plan.target_key) return;   // the user said which key: speech beats the camera
  const ids = (c.missing_keys || []).map(keyIdForLegend).filter(Boolean);
  if (ids.length !== 1) { missingSeen = { key: null, n: 0 }; return; }
  missingSeen = ids[0] === missingSeen.key ? { key: ids[0], n: missingSeen.n + 1 } : { key: ids[0], n: 1 };
  if (missingSeen.n >= 2 && c.confidence >= 0.5) setBrokenKey(ids[0], "camera: " + c.missing_keys.join(", ") + " missing");
}
function onCoach(c) {
  $("camlat").textContent = `${(c.latency_ms / 1000).toFixed(1)} s${c.crop?.cropped ? " · zoomed" : ""}`;
  onMissingKeys(c);
  $("camconfbar").style.width = Math.round(c.confidence * 100) + "%";
  const stepTxt = c.step_index > 0 ? ` · <b>STEP ${c.step_index}</b> ${c.step_status.replace("_", " ")}` : "";
  seen(`<b>I SEE</b> ${c.what_i_see}${stepTxt}${c.mistake ? `<br><span class="bad">⚠ ${c.mistake}</span>` : ""}`);
  if (!c.object_visible || c.step_index < 1) { agree = 0; return; }
  // follow the user: jump to the step they're doing (two frames agree, or one very confident frame)
  agree = c.step_index === lastDetect ? agree + 1 : 1; lastDetect = c.step_index;
  if (c.confidence >= 0.6) maybeAutoShop(c);
  const target = c.step_index - 1, sure = agree >= 2 || c.confidence >= 0.85;
  if (sure && target !== stepIdx && c.confidence >= 0.5) {
    stopTour(); goStep(target, " · detected");
  } else if (sure && target === stepIdx && c.step_status === "done" && lastDoneStep !== target) {
    lastDoneStep = target;                    // step finished: show the next one
    showTag(`keycap_${brokenKey}`, `STEP ${c.step_index} DONE ✓`, "ok", 2.2);
    narrate(c.say || "Done. Next step.");
    if (stepIdx < plan.steps.length - 1) setTimeout(() => goStep(stepIdx + 1, " · auto"), 1400);
    else setTimeout(() => { if (!finished) finishRepair(); }, 1400);
  }
  if (c.mistake && performance.now() - lastMistakeAt > 8000) {
    lastMistakeAt = performance.now(); flash();
    showTag(`keycap_${brokenKey}`, "⚠ " + c.mistake.toUpperCase().slice(0, 60), "red", 2.6);
    narrate("Careful. " + c.mistake);
  } else if (c.say && !c.mistake && sure && target === stepIdx && c.step_status !== "done") {
    // light guidance only when nothing else is being said
  }
}

// ---------------------------------------------------------------- phone camera: the laptop follows the phone's frames
let remoteOn = false, lastViewTs = 0, lastPoseTs = 0, lastSignalTs = 0, poseTrack = { on: QP.get("track") === "1", zero: 0, yaw: 0, baseAz: null, flip: QP.get("flip") === "1" ? 1 : -1 };   // say "calibrate"; if the hologram turns the wrong way, add ?flip=1
// ---- connection state, shown top right ("Connected to your phone")
const LINK_TEXT = { idle: "Phone not connected", waiting: "Waiting for your phone…", live: "Connected to your phone", lost: "Phone connection lost · waiting for it to return",
  webcam: "Using this laptop’s camera", offline: "Can’t reach the Spanner server" };
let linkState = "";
function setLink(state) {
  if (state === linkState) return; linkState = state;
  $("link").dataset.state = state; $("linktext").textContent = LINK_TEXT[state];
  $("btnPair").style.display = state === "live" || state === "webcam" ? "none" : "";
}
setLink("idle");

// The QR must carry an address the phone can reach: opened as localhost, the page asks the server for its network address.
async function phoneOrigin() {
  if (!/^(localhost|127\.|\[?::1\]?$)/.test(location.hostname)) return { origin: location.origin, local: false };
  try {
    const n = await fetchJson(API + "/coach/net", 3000);
    if (n.lan_ip) return { origin: `${location.protocol}//${n.lan_ip}${location.port ? ":" + location.port : ""}`, local: false };
  } catch { /* older server without /coach/net */ }
  return { origin: location.origin, local: true };
}
let pairRun = 0;
async function showPair() {
  const run = ++pairRun, el = $("pair"); el.style.display = "block";
  el.innerHTML = `<div class="t">Pair your phone</div><div class="tr">Preparing the code…</div>`;
  const { origin, local } = await phoneOrigin();
  if (run !== pairRun || linkState === "live") return;
  const url = `${origin}/training/viewer/phone.html?s=${SESSION}`;
  el.innerHTML = `<div class="t">Scan with the Spanner app</div>
    <img src="${API}/coach/qr?url=${encodeURIComponent(url)}" alt="QR code to pair your phone" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'warn',textContent:'The QR code could not be drawn. Type the address below on your phone instead.'}))">
    <div class="muted">or open <b>${esc(url)}</b></div>
    <div class="tr">${linkState === "lost" ? "Phone connection lost. Scan again, or wake the phone." : "Waiting for the first photo from your phone"}</div>
    ${local ? `<div class="warn">This page is open as “${esc(location.hostname)}”, which your phone can’t reach. Open it at your laptop’s network address instead (for example http://192.168.x.x:${esc(location.port || "8000")}/training/viewer/).</div>` : ""}`;
}
function hidePair() { pairRun++; $("pair").style.display = "none"; }
function startRemote() {
  if (camOn) toggleCamera();
  if (micOn) $("mic").onclick();   // the phone listens; this laptop's mic would only hear its own speaker
  setMode("live · phone camera");
  $("cam").style.display = "block"; $("video").style.display = "none";
  if (!voiceOn) { voiceOn = true; $("voice").classList.add("on"); }
  if (!remoteOn) { remoteOn = true; lastDataAt = 0; pollFails = 0; seen("Phone camera: waiting for frames."); setLink("waiting"); }
  if (linkState !== "live") showPair();
  remoteTick();   // does nothing if the loop is already running (saying "use my phone" twice must not start two)
}
/** The phone hung up: end the guide here too and go back to the start (a fresh session, ready to pair again). */
function endFromPhone() {
  remoteOn = false; stopSpeech(); stopTour();
  heard("phone", "call ended");
  try { sessionStorage.removeItem("spanner-session"); } catch { /* private window */ }
  setTimeout(() => { location.href = location.pathname + location.search.replace(/([?&])s=[^&]*&?/, "$1"); }, 300);
}
function stopRemote() { remoteOn = false; hidePair(); $("cam").style.display = "none"; setLink(camOn ? "webcam" : "idle"); }
let remoteLoop = false, lastDataAt = 0, pollFails = 0, lastUtterId = "", lastReplyId = "", lastStateSent = "";
// ---- the phone is the microphone: the server transcribes, decides what to do and what to say (one Claude turn with
// the conversation so far and what is on screen); this laptop just does it and speaks.
function screenState() {
  return { session: SESSION, screen: linkState !== "live" ? "pairing" : pickOpen ? "picking" : finished ? "done" : stepIdx >= 0 ? "step" : "ready",
    step: stepIdx + 1, total: plan?.steps?.length || 0, title: plan?.title || "" };
}
function reportState() {
  const st = screenState(), key = JSON.stringify(st); if (key === lastStateSent) return; lastStateSent = key;
  fetch(API + "/coach/state", { method: "POST", headers: { "content-type": "application/json" }, body: key }).catch(() => { lastStateSent = ""; });
}
const ACKS = ["Mm-hm.", "Okay.", "Let me see.", "Right."];
function closePicker() { if (!pickOpen) return; pickOpen = false; $("pick").style.opacity = 0; setTimeout(() => { $("pick").style.display = "none"; }, 600); }
async function onReply(r) {
  if (!r.addressed || isEcho(r.text, 6000)) { heard(r.text, "not for me"); return; }
  heard(r.text, r.action === "none" ? "talking" : r.action.replace(/_/g, " "));
  if (r.action !== "stop" && performance.now() < speakingUntil) stopSpeech();
  const viaCommand = { take_apart: "take it apart", reassemble: "put it back together", xray: "x-ray", zoom_in: "zoom in",
    zoom_out: "zoom out", show_mistake: "show me the mistake", specs: "specs", sources: "sources" };
  switch (r.action) {
    case "pick_device": if (pickOpen && r.device) return chooseDevice(r.device, ""); break;
    case "plan_repair": closePicker(); return requestRepair(r.complaint || r.text);
    case "start": stopTour(); return goStep(0);
    case "next": stopTour(); return advance();
    case "back": stopTour(); return goStep(stepIdx - 1);
    case "repeat": return $("say").onclick();
    case "stop": return quiet("voice");
    case "show_part": return command("show me the " + (r.part || "switch"));
    case "shop_parts": return openShop(r.text);
    case "shop_keyboards": return openShop("show me other keyboards " + r.text);
    default: if (viaCommand[r.action]) return command(viaCommand[r.action]);
  }
  if (r.say) narrate(r.say);   // a conversational answer (or the picker asking again)
}
async function remoteTick() {
  if (remoteLoop) return; remoteLoop = true;
  while (remoteOn) {
    try {
      const d = await fetchJson(API + "/coach/latest?session=" + SESSION, 2500);
      pollFails = 0; let fresh = false;
      if (d.view && d.view.ts !== lastViewTs) {
        lastViewTs = d.view.ts; fresh = true; onCoach(d.view);
      }
      // speech from the phone's microphone (transcribed on the laptop): the phone listens, this laptop speaks back
      if (d.utterance && d.utterance.id !== lastUtterId) {
        lastUtterId = d.utterance.id; heard(d.utterance.text, "…");
        // a real reply usually lands within a second or two: if it hasn't after 0.7 s, acknowledge right away
        const id = d.utterance.id, words = d.utterance.text.trim().split(/\s+/).length;
        if (words >= 3 && !pickOpen && linkState === "live" && !isEcho(d.utterance.text, 6000))
          setTimeout(() => { if (lastReplyId !== id && performance.now() > speakingUntil) narrate(ACKS[Math.floor(Math.random() * ACKS.length)], false); }, 700);
      }
      if (d.reply && d.reply.id !== lastReplyId) { lastReplyId = d.reply.id; onReply(d.reply); }
      reportState();
      if (d.signal && d.signal.ts !== lastSignalTs) { lastSignalTs = d.signal.ts; if (d.signal.signal === "stop") quiet("✋"); else if (d.signal.signal === "end") endFromPhone(); }
      if (poseTrack.on && d.pose && d.pose.ts !== lastPoseTs) { lastPoseTs = d.pose.ts; applyPose(d.pose); }
      if (fresh) { lastDataAt = performance.now(); if (linkState !== "live") hidePair(); }
      // staleness is judged on this laptop's own clock: no new photo for a few seconds means the phone stopped
      if (!lastDataAt) setLink("waiting");
      else if (performance.now() - lastDataAt > 6000) { if (linkState !== "lost") { setLink("lost"); showPair(); } }
      else setLink("live");
    } catch { if (++pollFails >= 3) setLink("offline"); }
    await new Promise((r) => setTimeout(r, 300));
  }
  remoteLoop = false;
}
// Rotation of the real keyboard on the desk -> same rotation of the hologram (orbit the view around it).
function calibratePose() { poseTrack.zero = poseTrack.lastAngle ?? 0; poseTrack.baseAz = controls.getAzimuthalAngle(); narrate("Aligned."); }
function applyPose(p) {
  if (!p.found || !poseTrack.on) return;
  poseTrack.lastAngle = p.angle;
  if (!poseTrack.on || flying || touring || motionPaused) return;
  if (poseTrack.baseAz === null) { poseTrack.baseAz = controls.getAzimuthalAngle(); poseTrack.zero = p.angle; }
  const target = poseTrack.baseAz + THREE.MathUtils.degToRad((p.angle - poseTrack.zero) * poseTrack.flip);
  const cur = controls.getAzimuthalAngle(); let d = target - cur; d = Math.atan2(Math.sin(d), Math.cos(d));
  const step = d * 0.25;                                    // smooth follow, no jitter
  if (Math.abs(d) < 0.004) return;
  const off = camera.position.clone().sub(controls.target); off.applyAxisAngle(V3(0, 1, 0), step);
  camera.position.copy(controls.target.clone().add(off)); controls.update();
  $("camlat").textContent = `tracking ${Math.round(p.angle - poseTrack.zero)}° · ${p.latency_ms} ms`;
}
// ✋ or "stop": Spanner stops talking (and stops the tour), right now
function quiet(how = "stop") { stopSpeaking(); stopTour(); heard(how, "ok, quiet"); }
async function toggleCamera() {
  if (camOn) {
    camOn = false; camStream?.getTracks().forEach((t) => t.stop()); camStream = null;
    $("cam").style.display = "none"; $("camera").classList.remove("on"); setMode("assembled"); setLink(remoteOn ? linkState : "idle"); return;
  }
  if (!navigator.mediaDevices?.getUserMedia) { heard("", "this page can’t use the camera over plain http: use the phone, or open it at localhost"); return; }
  try {
    // a permission prompt nobody answers must not freeze the app: give up after 20 s
    camStream = await Promise.race([navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false }),
      new Promise((_, no) => setTimeout(() => no(new Error("no answer to the camera prompt")), 20000))]);
  } catch (e) { heard("", "camera unavailable: " + (e.message || e)); return; }
  remoteOn = false; hidePair(); $("video").style.display = "";
  $("video").srcObject = camStream; $("cam").style.display = "block"; setLink("webcam");
  camStream.getVideoTracks()[0]?.addEventListener("ended", () => { if (camOn) { heard("", "camera stopped"); toggleCamera(); } });
  $("camera").classList.add("on"); camOn = true; agree = 0; lastDetect = 0;
  if (!voiceOn) { voiceOn = true; $("voice").classList.add("on"); }
  setMode("live · watching"); narrate("I'm watching. Show me the keyboard.");
  coachTick(); watchPalm($("video"), () => quiet("✋"));
}
$("camera").onclick = toggleCamera;

// ------------------------------------------------------------------ controls
$("apart").onclick = () => { stopTour(); takeApart(); };
$("together").onclick = () => { stopTour(); reassemble(); };
$("next").onclick = () => { stopTour(); advance(); };
$("btnPair").onclick = () => startRemote();
$("prev").onclick = () => { stopTour(); goStep(stepIdx - 1); };
$("tour").onclick = tour;
$("rewind").onclick = rewind;
$("xray").onclick = () => setXray(!xray);
$("rgb").onclick = () => setRGB(!rgbOn);
$("fx").onclick = () => $("fx").classList.toggle("on");
$("mistake").onclick = showMistake; $("fixit").onclick = fixMistake;
$("reset").onclick = async () => { stopTour(); stepIdx = -1; select(null); clearTags(); clearArrow(); setHighlight([]); mistakeOn = false; $("mistake").classList.remove("on"); $("fixit").style.display = "none";
  if (openSwitch) { await openSwitchAnim(openSwitch, false); openSwitch = null; }
  applyState({ ...INITIAL }, 600); explodeAll(0); rotateTo("iso"); setReady(); setMode("assembled"); };
$("ex").oninput = (e) => { stopTour(); const a = e.target.value / 100;
  for (const g of Object.keys(pack.groups)) if (!["case", "usb_c_port", "mode_switch"].includes(g)) amount[g] = a;
  setMode(a > 0 ? "exploded view" : "assembled"); };
addEventListener("keydown", (e) => {
  lastInput = performance.now(); controls.autoRotate = false;
  if (e.target.tagName === "INPUT") return;
  if (e.key === "ArrowRight") $("next").onclick(); else if (e.key === "ArrowLeft") $("prev").onclick();
  else if (e.key === " ") { e.preventDefault(); tour(); } else if (e.key.toLowerCase() === "e") ($("ex").value > 50 ? $("together") : $("apart")).onclick();
  else if (e.key.toLowerCase() === "x") setXray(!xray); else if (e.key.toLowerCase() === "r") $("reset").onclick();
  else if (e.key.toLowerCase() === "f" && selected) focusPart(selected); else if (e.key.toLowerCase() === "m") showMistake();
  else if (e.key.toLowerCase() === "c") toggleCamera();
  else if (e.key === "Escape") closePanels();
});

// ------------------------------------------------------------------ load + boot
let packJson, planJson;
try {
  $("bootmsg").textContent = "Loading the repair";
  [packJson, planJson] = await Promise.all([fetchJson(BASE + "pack.json", 12000), fetchJson(BASE + "demo_plan.json", 12000)]);
  if (!planJson?.plan?.steps?.length) throw new Error("the repair procedure is empty");
} catch (e) {
  bootFail("Couldn’t load the repair (" + (e.message || e) + "). Check the Spanner server is running, then try again. If the repair itself is missing, run: python -m training.scene3d");
  throw e;
}
pack = packJson; plan = planJson.plan; card = planJson.parts_card || null;
brokenKey = QP.get("key") || planJson?.broken_key || "F"; K = pack.keys[brokenKey]; if (!K) { brokenKey = "F"; K = pack.keys.F; }
for (const s of plan.steps) if (!s.kind) s.kind = "other";
$("sessTitle").textContent = KEYL() + " key replacement";
$("bootmsg").textContent = "Building " + (pack.object || "the model"); $("bootbar").style.width = "40%";
buildKeyboard(); buildTools();
tools.plug.visible = true; tools.plug.position.copy(plugPos("in"));
for (const g of Object.keys(CALLOUT)) {
  const div = Object.assign(document.createElement("div"), { className: "callout", textContent: CALLOUT[g] }); div.style.opacity = 0;
  const obj = new CSS2DObject(div); scene.add(obj); callouts[g] = { obj, div };
}
{ const [dw, dh, dd] = pack.dimensions_cm; const div = Object.assign(document.createElement("div"), { className: "dim", textContent: `${Math.round(dw * 10)} × ${Math.round(dd * 10)} × ${Math.round(dh * 10)} mm · 61 KEYS · ${Object.keys(parts).length - 1} PARTS` });
  dimLabel = { obj: new CSS2DObject(div), div }; dimLabel.obj.position.set(0, -0.4, CD / 2 + 2.2); scene.add(dimLabel.obj); }
setRGB(true); telemetry(); buildStages(); setReady();
$("bootbar").style.width = "100%"; await sleep(250);
$("boot").style.opacity = 0; setTimeout(() => $("boot")?.remove(), 700);

// loop (starts before the boot flight, which runs on it; performance.now so headless/virtual clocks agree).
// One bad frame must not stop all future frames, so the body is guarded and a tween that throws is dropped.
let loopErrors = 0;
renderer.setAnimationLoop(() => {
  if (contextLost) return;
  try {
    const now = performance.now();
    for (let i = tweens.length - 1; i >= 0; i--) {
      const tw = tweens[i];
      if (tw.dead) { tweens.splice(i, 1); tw.done(); continue; }
      const t = tw.ms <= 0 ? 1 : Math.min(1, (now - tw.t0) / tw.ms);
      tw.set(tw.from + (tw.to - tw.from) * ease(t));
      if (t >= 1) { tweens.splice(i, 1); tw.done(); }
    }
    updatePositions();
    if (pulseT0) { const k = 0.5 + 0.5 * Math.sin((now - pulseT0) / 170); for (const m of hiCache.values()) m.emissiveIntensity = 0.5 + 1.3 * k; }
    if (arrow) arrow.position.copy(arrow.userData.o).addScaledVector(arrow.userData.dir, Math.sin(now / 260) * 0.3);
    for (const r of rings.children) r.rotation.z += r.userData.spin * 0.01;
    if (motionPaused) controls.autoRotate = false;
    else if (!flying && !touring && now - lastInput > 12000 && !INSTANT) controls.autoRotate = true;
    if (!flying) controls.update();
    if (FX && $("fx").classList.contains("on")) composer.render(); else renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
  } catch (e) {
    if (++loopErrors <= 3) console.error("render loop:", e);
    for (const tw of tweens.splice(0)) { try { tw.done(); } catch { /* nothing waits on it any more */ } }   // release whoever awaits them
    flying = false; controls.enabled = true;
  }
});

// The intro flight and deep links run on the side: the wake gate must not wait for them (or break if one fails).
setXray(true); setMode("scanning");
(async () => {
  try {
    await flyTo(V3(...pack.views.iso), V3(0, 1.5, 0), 2200, true);
    setXray(false); setMode("assembled"); blip(880, 0.08);
    // deep links (pitch + screenshot tests)
    if (QP.get("xray") === "1") setXray(true);
    if (QP.get("explode") === "1") await takeApart();
    if (QP.get("fix")) await requestRepair(QP.get("fix"));   // e.g. ?fix=my%20space%20bar%20rattles
    if (QP.get("step")) chosen = true;   // screenshot deep link: play the canned plan without asking
    if (QP.get("step")) await goStep(Number(QP.get("step")) - 1, "", true);
    if (QP.get("mistake") === "1") await showMistake();
    if (QP.get("select") && parts[QP.get("select")]) { select(QP.get("select")); if (QP.get("pull") === "1") await $("pull").onclick(); if (QP.get("open") === "1") await $("open").onclick(); }
    if (QP.get("panel")) showSide(QP.get("panel"));
    if (QP.get("camera") === "1") toggleCamera();
    if (QP.get("phone") === "1" && INSTANT) startRemote();
    if (QP.get("shop")) openShop(QP.get("shop"), QP.get("auto") === "1");
    window.__spannerReady = true;
  } catch (e) { console.error("intro / deep link:", e); setXray(false); window.__spannerReady = true; }
})();

// what do you want to fix? keyboard = the guided repair; the others open their hologram (device.html)
let pickOpen = false;
/** Pairing on the question page (nothing behind it is shown yet): the QR for the Spanner app, then the question. */
async function pickPairCode() {
  const { origin } = await phoneOrigin();
  const url = `${origin}/training/viewer/phone.html?s=${SESSION}`;
  $("pickq").textContent = "First, scan this with the Spanner app";
  $("pickhint").textContent = "waiting for your phone";
  $("pickecho").innerHTML = `<img src="${API}/coach/qr?url=${encodeURIComponent(url)}" alt="Pairing code" style="width:220px;height:220px;border-radius:8px;margin-top:6px;background:#f2f2e9;padding:8px">`;
}
function showPicker() {
  pickOpen = true; $("pick").style.display = "flex"; $("pick").style.opacity = 1; $("pickecho").textContent = ""; $("pickq").textContent = "What do you want to fix?"; $("pickhint").textContent = "listening · just say it";
  narrate("What do you want to fix?");
}
const FAULT_WORDS = /(keys?|keycaps?|space ?bar|enter|shift|backspace|switch(es)?|cable|chargw*|power|lights?|backlight|led|dead|broken|stuck|sticky|rattlw*|wobblw*|loose|missing|cracked|flickerw*|double|chatterw*|not (working|registering|typing)|doesn'?t|won'?t)/i;
async function chooseDevice(dev, raw = "") {
  if (!pickOpen) return;
  pickOpen = false; $("pick").style.opacity = 0; setTimeout(() => { $("pick").style.display = "none"; }, 600);
  if (dev !== "keyboard") { narrate("Opening the " + { iphone15: "iPhone 15", momentum4: "Momentum 4", ipad11: "iPad" }[dev] + "."); setTimeout(() => { location.href = `./device.html?d=${dev}`; }, 900); return; }
  if (!remoteOn && !camOn) { if (QP.get("camera") === "1") await toggleCamera(); else startRemote(); }
  if (FAULT_WORDS.test(raw)) return requestRepair(raw);   // they already said what's wrong: plan it now
  narrate("The keyboard. What's wrong with it?");
}
async function pickByVoice(raw) {   // free speech -> which device; Claude decides when the words aren't obvious
  const t = raw.toLowerCase();
  let dev = /key ?board|rk ?61|keys?|keycap|space ?bar/.test(t) ? "keyboard" : /i ?phone/.test(t) ? "iphone15" : /head ?phone|momentum|sennheiser|ear ?phone/.test(t) ? "momentum4" : /i ?pad|tablet/.test(t) ? "ipad11" : null;
  $("pickecho").textContent = "“" + raw.trim() + "”";
  if (!dev) {
    try {
      const r = await fetch(API + "/coach/device", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: raw }) });
      const j = r.ok ? await r.json() : {}; dev = j.device && j.device !== "none" ? j.device : null;
      if (!dev) { $("pickq").textContent = j.say || "I can fix a keyboard, an iPhone 15, Momentum 4 headphones or an iPad. Which one?"; narrate($("pickq").textContent); return; }
    } catch { $("pickq").textContent = "Say: keyboard, iPhone, headphones, or iPad."; return; }
  }
  chooseDevice(dev, raw);
}
if (QP.get("pick") === "1" && INSTANT) { pickOpen = true; $("pick").style.display = "flex"; }
// wake: browsers need one gesture before mic + speech; after that it's voice only
// The question page is the first thing on screen (it covers the boot and the keyboard). One tap wakes the voice
// (browsers need a gesture). With the phone as camera + microphone, pair it first, then ask: the question needs ears.
if (INSTANT || QP.get("wake") === "0" || QP.get("pick") === "0") {
  if (QP.get("pick") !== "1") { $("pick").classList.remove("first"); $("pick").style.display = "none"; pickOpen = false; }
  else { $("pick").classList.remove("first"); $("pick").style.display = "flex"; $("pickhint").textContent = "listening"; pickOpen = true; }
} else {
  pickOpen = true;
  const laptopOnly = QP.get("camera") === "1";
  const wake = () => {
    removeEventListener("keydown", wake, true); $("pick").classList.remove("first"); $("pick").style.cursor = "default";
    voiceOn = true; $("voice").classList.add("on");
    if (laptopOnly) {                     // this laptop is the camera and the microphone: ask straight away
      if (!micOn) $("mic").onclick();
      $("pick").style.display = "flex"; $("pickhint").textContent = "listening"; narrate("What do you want to fix?");
      return;
    }
    // The keyboard stays hidden until the person says it is a keyboard: the pairing QR goes on the question page itself.
    $("pick").style.display = "flex"; pickPairCode();
    try { startRemote(); } catch (e) { console.error("wake:", e); }
    narrate("Hi, I'm Spanner. Scan the code with the Spanner app on your phone.");
    const waitPhone = setInterval(() => { if (linkState === "live") { clearInterval(waitPhone); if (pickOpen) showPicker(); } }, 500);
  };
  $("pick").addEventListener("pointerdown", wake, { once: true }); addEventListener("keydown", wake, { capture: true, once: true });
}
