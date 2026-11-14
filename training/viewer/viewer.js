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

const BASE = "../../data/models/rk61/";
const QP = new URLSearchParams(location.search);
const FX = QP.get("fx") !== "0";
const INSTANT = QP.get("instant") === "1";
const CYAN = 0x00e5ff, RED = 0xff3d5a;
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, INSTANT ? 0 : ms));
const V3 = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);

// ------------------------------------------------------------------ renderer, scene, camera
const renderer = new THREE.WebGLRenderer({ canvas: $("c"), antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x05080f);
scene.fog = new THREE.FogExp2(0x05080f, 0.011);
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
const rim = new THREE.PointLight(CYAN, 40, 60); rim.position.set(-18, 10, -14); scene.add(rim);

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

// post: outline (hover/selection) -> sanitize (no NaN/inf pixels into bloom) -> bloom (HDR glow only) -> output
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const outline = new OutlinePass(new THREE.Vector2(innerWidth, innerHeight), scene, camera);
Object.assign(outline, { edgeStrength: 4.0, edgeGlow: 0.5, edgeThickness: 1.3, pulsePeriod: 0 });
outline.visibleEdgeColor.set(CYAN); outline.hiddenEdgeColor.set(0x0a5566);
composer.addPass(outline);
composer.addPass(new ShaderPass({ uniforms: { tDiffuse: { value: null } },
  vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
  fragmentShader: "uniform sampler2D tDiffuse; varying vec2 vUv; void main(){ vec4 c = texture2D(tDiffuse, vUv);"
    + " if (c.r != c.r || c.g != c.g || c.b != c.b || c.a != c.a) c = vec4(0.0, 0.0, 0.0, 1.0);"
    + " gl_FragColor = vec4(min(c.rgb, vec3(16.0)), c.a); }" }));
const bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.5, 0.5, 1.0);
composer.addPass(bloom);
composer.addPass(new OutputPass());
const labelRenderer = new CSS2DRenderer({ element: $("labels") });

function resize() {
  renderer.setSize(innerWidth, innerHeight); composer.setSize(innerWidth, innerHeight);
  labelRenderer.setSize(innerWidth, innerHeight); outline.resolution.set(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight;
  // stage mode: render as if the viewport were taller by the card strip, so the keyboard centres in the free area
  const strip = document.body.classList.contains("stage") ? 110 : 0;
  if (strip) camera.setViewOffset(innerWidth, innerHeight + strip, 0, 0, innerWidth, innerHeight); else camera.clearViewOffset();
  camera.updateProjectionMatrix();
}
function setStage(on) { document.body.classList.toggle("stage", on); resize(); }
addEventListener("resize", resize); resize();

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
function flyTo(pos, target, ms = 1100) {
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
async function applyCommand(c) {
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
function setMode(t) { $("mode").textContent = t.toUpperCase(); }
function telemetry() { $("telemetry").textContent = `${Object.keys(parts).length - 1} PARTS · ${selected ? selected.replace("_", " ").toUpperCase() : "— SELECTED"}`; }
function dots() { $("dots").innerHTML = plan.steps.map((_, i) => `<span class="${i < stepIdx ? "done" : i === stepIdx ? "on" : ""}"></span>`).join(""); }
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
function refreshLearned() {
  fetch((QP.get("api") || "") + "/coach/learned").then((r) => r.json()).then((d) => { learned = d.learned || {}; }).catch(() => {});
}
function warnLearned(s) {
  const m = learned[s.index]; if (!m || warnedSteps.has(s.index) || INSTANT) return;
  warnedSteps.add(s.index);
  $("sm").textContent = `⚠ Learned from earlier sessions: people often ${m} here`;
  setTimeout(() => { showTag(`switch_${brokenKey}`, "LEARNED · " + m.toUpperCase().slice(0, 48), "red", 3.2); narrate(`Heads up. People often ${m} at this step. That came from earlier repair sessions.`); }, 2200);
}
refreshLearned(); setInterval(refreshLearned, 30000);
async function goStep(i, note = "", silent = false) {
  if (!plan.steps.length) return;
  if (mistakeOn) { mistakeOn = false; $("mistake").classList.remove("on"); $("fixit").style.display = "none"; }
  const id = ++runId;
  stepIdx = Math.max(0, Math.min(i, plan.steps.length - 1));
  const s = plan.steps[stepIdx];
  $("card").style.display = "block"; $("card").classList.add("on");
  clearTags(); clearArrow(); pulseT0 = 0;
  setMode(`repair · step ${s.index}/${plan.steps.length}${note}`); blip(990);
  $("sn").textContent = `STEP ${s.index} / ${plan.steps.length}`; $("sk").textContent = `KEY ${pack.keys[brokenKey].label} · ${s.kind.toUpperCase().replace(/-/g, " ")}`;
  typeText($("si"), s.instruction);
  $("sd").textContent = s.expected_visual_state ? `✓ Done when: ${s.expected_visual_state}` : "";
  $("sm").textContent = s.common_mistakes?.length ? `⚠ ${s.common_mistakes[0]}` : "";
  warnLearned(s);
  $("tools").innerHTML = (TOOL_OF[s.kind] || "HANDS").split(" · ").map((t) => `<span class="chip">${t}</span>`).join("");
  dots();
  if (!silent) narrate(`Step ${s.index}. ${s.instruction}`);
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
    const s = plan.steps[i]; stepIdx = i; dots(); setMode(`rewind · step ${s.index}`); $("sn").textContent = `REWIND ${s.index}`;
    typeText($("si"), "Undoing: " + s.instruction); clearTags(); clearArrow();
    for (const c of s.scene3d || []) if (c.op === "rotate_to") await applyCommand(c);
    await applyState(stateBefore(i), 900); await sleep(500);
  }
  stepIdx = -1; dots(); setMode("assembled"); $("card").style.display = "none"; $("card").classList.remove("on");
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
  mouse.set((ev.clientX / innerWidth) * 2 - 1, -(ev.clientY / innerHeight) * 2 + 1);
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
  const vs = speechSynthesis.getVoices(); if (!vs.length) return;
  voice = vs.find((v) => /Google UK English Male|Ryan|George|Daniel/.test(v.name)) || vs.find((v) => v.lang.startsWith("en-GB")) || vs.find((v) => v.lang.startsWith("en")) || vs[0];
}
if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }
// Spanner's voice: ElevenLabs through the backend (/coach/say), the browser's voice as fallback.
let elevenOK = false, speakingUntil = 0, sayAudio = null, currentSpeech = "";
function stopSpeaking() { if ("speechSynthesis" in window) speechSynthesis.cancel(); if (sayAudio) { sayAudio.pause(); sayAudio = null; } speakingUntil = 0; }
fetch((QP.get("api") || "") + "/coach/health").then((r) => r.json()).then((d) => { elevenOK = !!d.tts; }).catch(() => {});
function browserSay(text) {
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text); if (voice) u.voice = voice; u.rate = 1.03; u.pitch = 0.92;
  u.onend = () => { speakingUntil = performance.now() + 400; };
  speakingUntil = performance.now() + 60000; currentSpeech = text; speechSynthesis.speak(u);
}
function narrate(text) {
  if (!voiceOn || INSTANT || !text) return;
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  if (sayAudio) { sayAudio.pause(); sayAudio = null; }
  if (!elevenOK) return browserSay(text);
  const a = new Audio((QP.get("api") || "") + "/coach/say?text=" + encodeURIComponent(text));
  sayAudio = a; speakingUntil = performance.now() + 60000; currentSpeech = text;
  a.onended = () => { if (sayAudio === a) { sayAudio = null; speakingUntil = performance.now() + 400; } };
  a.onerror = () => { if (sayAudio === a) { sayAudio = null; browserSay(text); } };
  a.play().catch(() => { if (sayAudio === a) { sayAudio = null; browserSay(text); } });
}
$("voice").onclick = () => { voiceOn = !voiceOn; $("voice").classList.toggle("on", voiceOn); if (voiceOn) narrate("Voice on. I'm Spanner. Say next, take it apart, or show me the socket."); else { speechSynthesis.cancel(); if (sayAudio) { sayAudio.pause(); sayAudio = null; } } };
$("say").onclick = () => { const s = plan.steps[stepIdx]; if (!s) return; if (!voiceOn) { voiceOn = true; $("voice").classList.add("on"); } narrate(`Step ${s.index}. ${s.instruction}`); };

const PART_WORDS = [[/socket/, () => `socket_${brokenKey}`], [/spring|inside the switch|open the switch/, () => `switch_${brokenKey}`], [/spare|new switch|replacement/, () => "switch_spare"],
  [/switch/, () => `switch_${brokenKey}`], [/keycap|key cap|\bcap\b/, () => `keycap_${brokenKey}`], [/pcb|board|circuit/, () => "pcb"], [/plate/, () => "plate"],
  [/case|shell|tray/, () => "case"], [/battery/, () => "battery"], [/usb|port|cable|plug/, () => "usb_c_port"], [/led|light|backlight/, () => `led_${brokenKey}`],
  [/stabili|space ?bar/, () => "stabilizer_SPACE"], [/mode|bluetooth switch/, () => "mode_switch"]];
function heard(text, action) {
  const h = $("heard"); h.innerHTML = `🎤 <b>“${text}”</b> → ${action}`; h.style.display = "block"; clearTimeout(h._t); h._t = setTimeout(() => (h.style.display = "none"), 3500);
}
async function command(raw) {
  const t = raw.toLowerCase().trim(); let said = "";
  if (pickOpen) {
    const dev = /key ?board|rk ?61/.test(t) ? "keyboard" : /i ?phone/.test(t) ? "iphone15" : /head ?phone|momentum|sennheiser/.test(t) ? "momentum4" : /i ?pad|tablet/.test(t) ? "ipad11" : null;
    heard(raw, dev ? "→ " + dev : "say keyboard, iphone, headphones or ipad"); if (dev) chooseDevice(dev); return;
  }
  if (/(choose another|another device|pick a device|main menu|go back to the (menu|start))/.test(t)) { closePanels(); showPicker(); heard(raw, "device choice"); return; }
  const say = (s) => { said = s; narrate(s); };
  if (/\b(close|hide|dismiss|go away|that's enough|never ?mind)\b/.test(t)) { closePanels(); said = "closed"; }
  else if (/calibrat|align the hologram|set (this|that) as zero|zero it/.test(t)) { calibratePose(); said = "calibrated"; }
  else if (/use (my|the) phone|phone camera|phone as (the )?camera/.test(t)) { startRemote(); said = "phone camera"; }
  else if (/(other|another|alternative|different|new|better) keyboards?|keyboards? (instead|to buy)|instead of (this|the rk|it)|upgrade|should i buy|what else could i buy/.test(t)) { openShop(raw); said = "searching other keyboards"; }
  else if (/replace(ment)?s?|\bbuy\b|order|price|how much|where (can|do) i get|spare|new (key ?caps?|switch(es)?|stabili[sz]ers?|cable)/.test(t)) { openShop(raw); said = "searching replacements"; }
  else if (/turn (the )?camera off|stop watching|camera off/.test(t)) { if (camOn) toggleCamera(); if (remoteOn) { remoteOn = false; setStage(false); $("cam").style.display = "none"; setMode("assembled"); } said = "camera off"; }
  else if (/camera|watch me|look at me/.test(t)) { if (!camOn) toggleCamera(); said = "camera on"; }
  else if (/\b(next|continue|go on|proceed|forward)\b/.test(t)) { stopTour(); goStep(stepIdx + 1); said = "next step"; }
  else if (/\b(back|previous)\b/.test(t) && !/back together|put .*back/.test(t)) { stopTour(); goStep(stepIdx - 1); said = "previous step"; }
  else if (/take .*apart|explode|open it up|blow it up|exploded/.test(t)) { stopTour(); takeApart(); say("Taking it apart."); }
  else if (/put .*(together|back)|assemble|close it/.test(t)) { stopTour(); reassemble(); say("Putting it back together."); }
  else if (/x.?ray|see through|hologram/.test(t)) { setXray(!xray); say(xray ? "X-ray on." : "X-ray off."); }
  else if (/\b(reset|start over|from the top)\b/.test(t)) { $("reset").onclick(); say("Reset."); }
  else if (/\b(tour|play|walk me through|show me the repair|start the repair)\b/.test(t)) { if (!touring) tour(); say("Walking you through the repair."); }
  else if (/\b(stop|pause|hold on)\b/.test(t)) { stopTour(); said = "stopped"; }
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
let rec = null;
function setListen(on, txt) { const l = $("listen"); l.classList.toggle("on", on); l.textContent = txt || (on ? "LISTENING" : "MIC OFF"); }
$("mic").onclick = () => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { heard("", "speech recognition needs Chrome or Edge"); setListen(false, "VOICE NEEDS CHROME / EDGE"); return; }
  micOn = !micOn; $("mic").classList.toggle("on", micOn); setListen(micOn);
  if (!micOn) { rec?.stop(); rec = null; return; }
  rec = new SR(); rec.continuous = true; rec.interimResults = true; rec.lang = "en-GB"; rec.maxAlternatives = 1;
  let recognizing = false, lastEvent = performance.now(), lastActed = { text: "", t: 0 };
  const QUICK = /^(?:\s*(?:ok(?:ay)?|hey|spanner|please|and|now|just)[,\s]*)*(close|stop|pause|next|back|calibrate|reset|rewind|x-?ray|lights (?:on|off)|take it apart|put it back|zoom (?:in|out))\b/i;
  const words = (s) => new Set(s.toLowerCase().replace(/[^a-z0-9' ]/g, " ").split(/\s+/).filter((w) => w.length > 2));
  const isEcho = (text) => {        // Spanner hearing its own voice: most of the words are in what it is saying
    if (performance.now() > speakingUntil || !currentSpeech) return false;
    const a = words(text), b = words(currentSpeech); if (!a.size) return true;
    let n = 0; for (const w of a) if (b.has(w)) n++; return n / a.size >= 0.6;
  };
  const act = (text, how) => {
    const now = performance.now();
    if (text.trim().toLowerCase() === lastActed.text && now - lastActed.t < 4000) return;   // the final of a command already run
    lastActed = { text: text.trim().toLowerCase(), t: now };
    if (isEcho(text)) { heard(text, "ignored: that was me talking"); return; }
    if (now < speakingUntil) stopSpeaking();                      // barge-in: you talk, Spanner stops
    setListen(true, "LISTENING · " + text.trim().slice(0, 40)); command(text);
  };
  rec.onstart = () => { recognizing = true; lastEvent = performance.now(); };
  rec.onresult = (e) => {
    lastEvent = performance.now();
    const r = e.results[e.results.length - 1], text = r[0].transcript;
    if (r.isFinal) act(text, "final");
    else if (QUICK.test(text)) act(text.match(QUICK)[0], "quick");      // "close" acts the moment it is heard
  };
  rec.onspeechend = () => { lastEvent = performance.now(); };
  rec.onend = () => { recognizing = false; if (micOn) setTimeout(() => { try { rec.start(); } catch { /* already running */ } }, 250); };
  rec.onerror = (e) => { if (e.error !== "no-speech" && e.error !== "aborted") heard("", "mic error: " + e.error); if (e.error === "not-allowed") { micOn = false; setListen(false, "MIC BLOCKED"); } };
  clearInterval(window._micDog); window._micDog = setInterval(() => {   // watchdog: Chrome sometimes stops listening silently
    if (!micOn) return; if (!recognizing || performance.now() - lastEvent > 20000) { try { rec.abort(); } catch {} try { rec.start(); } catch {} lastEvent = performance.now(); }
  }, 5000);
  rec.start(); if (!voiceOn) { voiceOn = true; $("voice").classList.add("on"); }
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
  let verified = null, spoken = false;
  const isPart = !/(other|another|alternative|different|new|better) keyboards?|keyboards? (instead|to buy)|instead of|upgrade|should i buy|what else could i buy/i.test(raw);
  const pollVerify = async () => {
    if (id !== shopReq || !isPart) return;
    try {
      const r = await fetch(API + "/coach/shop/verified?query=" + encodeURIComponent(raw));
      if (r.ok) verified = await r.json();
    } catch { /* keep polling */ }
    if (id !== shopReq) return;
    const box = el.querySelector("#vbox"); if (box) box.innerHTML = verifyBox(verified);
    if (verified?.status === "ready" && verified.card?.decision && !spoken && el.querySelector("#vbox")) {
      spoken = true; narrate(verified.card.decision.headline);
    }
    if (!verified || verified.status === "researching" || verified.refreshing) verifyTimer = setTimeout(pollVerify, 4000);
  };
  if (isPart) pollVerify();
  try {
    const r = await fetch(API + "/coach/shop", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ query: raw }) });
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
    const r = await fetch(API + "/coach/intent", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: raw, current_step: stepIdx + 1 }) });
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    if (d.command === "none") { heard(raw, "not for me"); return; }
    if (d.command === "ask") { heard(raw, "asking Spanner"); return askCoach(raw); }
    if (d.command === "calibrate") { calibratePose(); heard(raw, "calibrated"); return; }
    if (d.command === "shop_parts") { heard(raw, "searching replacements"); return openShop(d.shop_query || raw); }
    if (d.command === "shop_keyboards") { heard(raw, "searching other keyboards"); return openShop("show me other keyboards " + (d.shop_query || "")); }
    if (d.command === "show_part") { heard(raw, "showing " + d.part); return command("show me the " + (d.part || "switch")); }
    const phrase = INTENT_TEXT[d.command]; if (phrase) { heard(raw, "→ " + phrase); return command(phrase); }
    heard(raw, "not understood");
  } catch (e) { heard(raw, "not understood"); }
}
async function askCoach(q) {
  try {
    const r = await fetch(API + "/coach/ask", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ question: q, current_step: stepIdx + 1 }) });
    if (!r.ok) throw new Error(r.status);
    const d = await r.json(); heard(q, d.answer); narrate(d.answer);
  } catch (e) { heard(q, "couldn't reach Spanner: " + e.message); }
}

// ------------------------------------------------------------------ live camera coach (laptop webcam -> /coach/frame)
const API = QP.get("api") || "";            // same origin when served by the backend (uvicorn backend.main:app)
const SESSION = "laptop-" + Math.random().toString(36).slice(2, 8);
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
      const r = await fetch(API + "/coach/frame", { method: "POST", body: fd });
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
function onCoach(c) {
  $("camlat").textContent = `${(c.latency_ms / 1000).toFixed(1)} s`;
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
let remoteOn = false, lastViewTs = 0, lastPoseTs = 0, poseTrack = { on: true, zero: 0, yaw: 0, baseAz: null, flip: QP.get("flip") === "1" ? 1 : -1 };   // say "calibrate"; if the hologram turns the wrong way, add ?flip=1
const PHONE_URL = () => `${location.origin}/training/viewer/phone.html?s=${SESSION}`;
function startRemote() {
  if (camOn) toggleCamera();
  remoteOn = true; setStage(true); setMode("live · phone camera");
  const el = $("pair"); el.style.display = "block";
  el.innerHTML = `<img src="${API}/coach/qr?url=${encodeURIComponent(PHONE_URL())}" alt="QR" style="width:260px;height:260px;display:block;margin:0 auto 10px;border-radius:4px"><div style="font:600 12px var(--mono);letter-spacing:.2em;color:var(--cyan)">SCAN WITH THE PHONE</div><div style="margin-top:6px;opacity:.75;font-size:11.5px">or type: <b style="font-size:12px">${PHONE_URL()}</b></div><div class="tr">WAITING FOR THE FIRST FRAME</div>`;
  $("cam").style.display = "block"; $("video").style.display = "none"; seen("Phone camera: waiting for frames.");
  if (!voiceOn) { voiceOn = true; $("voice").classList.add("on"); }
  remoteTick();
}
async function remoteTick() {
  if (!remoteOn) return;
  try {
    const r = await fetch(API + "/coach/latest?session=" + SESSION); const d = r.ok ? await r.json() : {};
    if (d.view && d.view.ts !== lastViewTs) { lastViewTs = d.view.ts; $("pair").style.display = "none"; onCoach(d.view); }
    if (d.pose && d.pose.ts !== lastPoseTs) { lastPoseTs = d.pose.ts; applyPose(d.pose); $("pair").style.display = "none"; }
  } catch { /* keep polling */ }
  setTimeout(remoteTick, 300);
}
// Rotation of the real keyboard on the desk -> same rotation of the hologram (orbit the view around it).
function calibratePose() { poseTrack.zero = poseTrack.lastAngle ?? 0; poseTrack.baseAz = controls.getAzimuthalAngle(); narrate("Aligned."); }
function applyPose(p) {
  if (!p.found) return;
  poseTrack.lastAngle = p.angle;
  if (!poseTrack.on || flying || touring) return;
  if (poseTrack.baseAz === null) { poseTrack.baseAz = controls.getAzimuthalAngle(); poseTrack.zero = p.angle; }
  const target = poseTrack.baseAz + THREE.MathUtils.degToRad((p.angle - poseTrack.zero) * poseTrack.flip);
  const cur = controls.getAzimuthalAngle(); let d = target - cur; d = Math.atan2(Math.sin(d), Math.cos(d));
  const step = d * 0.25;                                    // smooth follow, no jitter
  if (Math.abs(d) < 0.004) return;
  const off = camera.position.clone().sub(controls.target); off.applyAxisAngle(V3(0, 1, 0), step);
  camera.position.copy(controls.target.clone().add(off)); controls.update();
  $("camlat").textContent = `tracking ${Math.round(p.angle - poseTrack.zero)}° · ${p.latency_ms} ms`;
}
async function toggleCamera() {
  if (camOn) {
    camOn = false; camStream?.getTracks().forEach((t) => t.stop()); camStream = null;
    $("cam").style.display = "none"; $("keys").style.display = ""; $("camera").classList.remove("on"); setMode("assembled"); setStage(false); return;
  }
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
  } catch (e) { heard("", "camera blocked: " + e.message); return; }
  remoteOn = false; $("pair").style.display = "none"; $("video").style.display = "";
  $("video").srcObject = camStream; $("cam").style.display = "block"; $("keys").style.display = "none"; setStage(true);
  $("camera").classList.add("on"); camOn = true; agree = 0; lastDetect = 0;
  if (!voiceOn) { voiceOn = true; $("voice").classList.add("on"); }
  setMode("live · watching"); narrate("I'm watching. Show me the keyboard.");
  coachTick();
}
$("camera").onclick = toggleCamera;

// ------------------------------------------------------------------ controls
$("apart").onclick = () => { stopTour(); takeApart(); };
$("together").onclick = () => { stopTour(); reassemble(); };
$("next").onclick = () => { stopTour(); goStep(stepIdx + 1); };
$("prev").onclick = () => { stopTour(); goStep(stepIdx - 1); };
$("tour").onclick = tour;
$("rewind").onclick = rewind;
$("xray").onclick = () => setXray(!xray);
$("rgb").onclick = () => setRGB(!rgbOn);
$("fx").onclick = () => $("fx").classList.toggle("on");
$("mistake").onclick = showMistake; $("fixit").onclick = fixMistake;
$("reset").onclick = async () => { stopTour(); stepIdx = -1; select(null); clearTags(); clearArrow(); setHighlight([]); mistakeOn = false; $("mistake").classList.remove("on"); $("fixit").style.display = "none";
  if (openSwitch) { await openSwitchAnim(openSwitch, false); openSwitch = null; }
  applyState({ ...INITIAL }, 600); explodeAll(0); rotateTo("iso"); setMode("assembled"); dots();
  $("card").style.display = "none"; $("card").classList.remove("on"); };
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


const [packJson, planJson] = await Promise.all([fetch(BASE + "pack.json").then((r) => r.json()),
  fetch(BASE + "demo_plan.json").then((r) => (r.ok ? r.json() : null)).catch(() => null)]);
if (!planJson?.plan) {   // no placeholder data: without the knowledge-base procedure there is nothing to show
  $("bootmsg").textContent = "no repair procedure found: run python -m training.scene3d";
  throw new Error("demo_plan.json missing");
}
pack = packJson; plan = planJson.plan; card = planJson.parts_card || null;
brokenKey = QP.get("key") || planJson?.broken_key || "F"; K = pack.keys[brokenKey]; if (!K) { brokenKey = "F"; K = pack.keys.F; }
for (const s of plan.steps) if (!s.kind) s.kind = "other";
$("bootmsg").textContent = "building " + (pack.object || "model"); $("bootbar").style.width = "40%";
buildKeyboard(); buildTools();
tools.plug.visible = true; tools.plug.position.copy(plugPos("in"));
for (const g of Object.keys(CALLOUT)) {
  const div = Object.assign(document.createElement("div"), { className: "callout", textContent: CALLOUT[g] }); div.style.opacity = 0;
  const obj = new CSS2DObject(div); scene.add(obj); callouts[g] = { obj, div };
}
{ const [dw, dh, dd] = pack.dimensions_cm; const div = Object.assign(document.createElement("div"), { className: "dim", textContent: `${Math.round(dw * 10)} × ${Math.round(dd * 10)} × ${Math.round(dh * 10)} mm · 61 KEYS · ${Object.keys(parts).length - 1} PARTS` });
  dimLabel = { obj: new CSS2DObject(div), div }; dimLabel.obj.position.set(0, -0.4, CD / 2 + 2.2); scene.add(dimLabel.obj); }
setRGB(true); telemetry(); dots();
$("bootbar").style.width = "100%"; await sleep(250);
$("boot").style.opacity = 0; setTimeout(() => $("boot").remove(), 900);

// loop (starts before the boot flight, which runs on it; performance.now so headless/virtual clocks agree)
renderer.setAnimationLoop(() => {
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
  if (!flying && !touring && now - lastInput > 12000 && !INSTANT) controls.autoRotate = true;
  if (!flying) controls.update();
  if (FX && $("fx").classList.contains("on")) composer.render(); else renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
});

setXray(true); setMode("scanning");
await flyTo(V3(...pack.views.iso), V3(0, 1.5, 0), 2200);
setXray(false); setMode("assembled"); blip(880, 0.08);

// deep links (pitch + screenshot tests)
if (QP.get("xray") === "1") setXray(true);
if (QP.get("explode") === "1") await takeApart();
if (QP.get("step")) await goStep(Number(QP.get("step")) - 1, "", true);
if (QP.get("mistake") === "1") await showMistake();
if (QP.get("select") && parts[QP.get("select")]) { select(QP.get("select")); if (QP.get("pull") === "1") await $("pull").onclick(); if (QP.get("open") === "1") await $("open").onclick(); }
if (QP.get("panel")) showSide(QP.get("panel"));
if (QP.get("camera") === "1") toggleCamera();
if (QP.get("phone") === "1" && INSTANT) startRemote();
if (QP.get("shop")) openShop(QP.get("shop"), QP.get("auto") === "1");

// what do you want to fix? keyboard = the guided repair; the others open their hologram (device.html)
let pickOpen = false;
function showPicker() {
  pickOpen = true; $("pick").style.display = "flex";
  narrate("What do you want to fix? The keyboard, an iPhone 15, Sennheiser Momentum 4 headphones, or an iPad?");
}
async function chooseDevice(dev) {
  if (!pickOpen) return;
  pickOpen = false; $("pick").style.display = "none";
  if (dev !== "keyboard") { narrate("Opening the " + { iphone15: "iPhone 15", momentum4: "Momentum 4", ipad11: "iPad" }[dev] + "."); setTimeout(() => { location.href = `./device.html?d=${dev}`; }, 900); return; }
  if (QP.get("phone") === "1") startRemote(); else if (!camOn) await toggleCamera();
  narrate("The keyboard. I'm watching. Show it to me and I'll guide you step by step. You can also ask me for replacement parts or other keyboards.");
}
for (const tile of document.querySelectorAll("#pick .tile")) tile.onclick = () => chooseDevice(tile.dataset.d);
if (QP.get("pick") === "1" && INSTANT) { pickOpen = true; $("pick").style.display = "flex"; }
// wake: browsers need one gesture before mic + speech; after that it's voice only
if (!INSTANT && QP.get("wake") !== "0") {
  const w = $("wake"); w.style.display = "flex";
  const wake = async () => {
    removeEventListener("keydown", wake, true); w.remove();
    voiceOn = true; $("voice").classList.add("on");
    if (!micOn) $("mic").onclick();
    if (QP.get("pick") === "0") { if (QP.get("phone") === "1") startRemote(); else if (!camOn) await toggleCamera(); narrate("Hi, I'm Spanner. I'm watching. Show me the keyboard."); }
    else showPicker();
  };
  w.addEventListener("pointerdown", wake, { once: true }); addEventListener("keydown", wake, { capture: true, once: true });
}
