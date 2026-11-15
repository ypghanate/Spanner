// SPANNER // device holograms: iPhone 15, iPad (11th gen), Sennheiser Momentum 4.
// Procedural, decomposable models in the same look as the RK61 hologram: take apart, inspect every part,
// x-ray, voice. No repair procedure for these (the keyboard is the repair demo).
//   device.html?d=iphone15 | ipad11 | momentum4 | plug13a   (&instant=1 for screenshots, &explode=1, &xray=1, &select=battery)
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

const QP = new URLSearchParams(location.search), INSTANT = QP.get("instant") === "1", FX = QP.get("fx") !== "0";
const CYAN = 0x00e5ff, $ = (id) => document.getElementById(id), V3 = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const API = QP.get("api") || "";

// ------------------------------------------------------------------ scene (same look as the keyboard hologram)
const renderer = new THREE.WebGLRenderer({ canvas: $("c"), antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75)); renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const scene = new THREE.Scene(); scene.background = new THREE.Color(cssVar("--scene") || "#131a15"); scene.fog = new THREE.FogExp2(scene.background.getHex(), 0.006);
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 400); camera.position.set(0, 30, 40);
const controls = new OrbitControls(camera, renderer.domElement);
Object.assign(controls, { enableDamping: true, dampingFactor: 0.08, minDistance: 3, maxDistance: 90, maxPolarAngle: Math.PI * 0.49, autoRotateSpeed: 0.5 });
controls.target.set(0, 2, 0); controls.minPolarAngle = 0.2; controls.maxPolarAngle = Math.PI * 0.6;
const key = new THREE.DirectionalLight(0xffffff, 2.3); key.position.set(12, 30, 18); key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, { left: -25, right: 25, top: 25, bottom: -25 }); key.shadow.bias = -0.0004;
const fill = new THREE.DirectionalLight(0xdff6ff, 0.9); fill.position.set(-6, 10, 30);
scene.add(key, fill, new THREE.HemisphereLight(0xcff6ff, 0x0a0d16, 0.6));
const rim = new THREE.PointLight(CYAN, 40, 60); rim.position.set(-18, 10, -14); scene.add(rim);
const floor = new THREE.Mesh(new THREE.CircleGeometry(60, 64), new THREE.ShadowMaterial({ opacity: 0.45 })); floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
const grid = new THREE.PolarGridHelper(34, 24, 10, 96, CYAN, CYAN); grid.material.transparent = true; grid.material.opacity = 0.09; grid.position.y = 0.01; scene.add(grid);
const rings = new THREE.Group(); scene.add(rings);
for (const [r, w, o] of [[19, 0.08, 0.6], [20.4, 0.03, 0.35], [22.5, 0.05, 0.25]]) {
  const m = new THREE.Mesh(new THREE.RingGeometry(r, r + w, 160, 1, 0, Math.PI * (1.2 + Math.random() * 0.7)), new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, opacity: o, side: THREE.DoubleSide, blending: THREE.AdditiveBlending }));
  m.rotation.x = -Math.PI / 2; m.position.y = 0.02; m.userData.spin = (Math.random() - 0.5) * 0.25; rings.add(m);
}
const composer = new EffectComposer(renderer); composer.addPass(new RenderPass(scene, camera));
grid.visible = false; rings.visible = false;   // the calm look: just the device and its shadow
const outline = new OutlinePass(new THREE.Vector2(1, 1), scene, camera); Object.assign(outline, { edgeStrength: 4, edgeGlow: 0.5, edgeThickness: 1.3, pulsePeriod: 0 });
outline.visibleEdgeColor.set(CYAN); outline.hiddenEdgeColor.set(0x0a5566); composer.addPass(outline);
composer.addPass(new ShaderPass({ uniforms: { tDiffuse: { value: null } }, vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
  fragmentShader: "uniform sampler2D tDiffuse; varying vec2 vUv; void main(){ vec4 c = texture2D(tDiffuse, vUv); if (c.r != c.r || c.g != c.g || c.b != c.b || c.a != c.a) c = vec4(0.0,0.0,0.0,1.0); gl_FragColor = vec4(min(c.rgb, vec3(16.0)), c.a); }" }));
composer.addPass(new UnrealBloomPass(new THREE.Vector2(1, 1), 0.35, 0.4, 1.6)); composer.addPass(new OutputPass());
const labelRenderer = new CSS2DRenderer({ element: $("labels") });
function resize() {
  const vp = $("viewport"), w = Math.max(1, vp.clientWidth), h = Math.max(1, vp.clientHeight);
  renderer.setSize(w, h, false); composer.setSize(w, h); labelRenderer.setSize(w, h); outline.resolution.set(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
}
function applyTheme(t, persist = true) {
  document.documentElement.dataset.theme = t;
  if (persist) try { localStorage.setItem("spanner-theme", t); } catch { /* storage blocked */ }
  $("btnTheme").textContent = t === "light" ? "Dark" : "Light";
  scene.background = new THREE.Color(cssVar("--scene") || "#131a15"); scene.fog.color.copy(scene.background);
  renderer.toneMappingExposure = t === "light" ? 1.05 : 1.0;
}
$("btnTheme").onclick = () => applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
applyTheme(document.documentElement.dataset.theme, false);
new ResizeObserver(() => resize()).observe($("viewport"));
addEventListener("resize", resize); resize();

// ------------------------------------------------------------------ materials
const P = (o) => new THREE.MeshPhysicalMaterial(o), S = (o) => new THREE.MeshStandardMaterial(o);
const M = {
  alu: P({ color: 0xc9d3dc, metalness: 0.9, roughness: 0.42, clearcoat: 0.1 }), titanium: P({ color: 0x7d7f85, metalness: 1, roughness: 0.4 }),
  glassBack: P({ color: 0xb9ccdd, roughness: 0.55, clearcoat: 0.6, clearcoatRoughness: 0.5, metalness: 0.0 }),
  plateau: P({ color: 0xc3d1de, roughness: 0.35, clearcoat: 0.9, clearcoatRoughness: 0.2 }),
  bezel: P({ color: 0x050607, roughness: 0.15, clearcoat: 1, clearcoatRoughness: 0.05 }),
  screen: P({ color: 0x05070c, roughness: 0.08, clearcoat: 1, clearcoatRoughness: 0.05, emissive: 0x0a2a3a, emissiveIntensity: 0.35 }),
  oled: S({ color: 0x1a1d24, roughness: 0.5, metalness: 0.3 }), glass: P({ color: 0x8fb7c9, roughness: 0.05, transmission: 0.6, transparent: true, opacity: 0.55, clearcoat: 1, ior: 1.5 }),
  battery: S({ color: 0x2b2f36, roughness: 0.7, metalness: 0.15 }), batteryLabel: S({ color: 0x9aa0a8, roughness: 0.9, metalness: 0 }),
  pcb: S({ color: 0x0f5f3a, roughness: 0.55, metalness: 0.2 }), shield: S({ color: 0xc9ccd2, metalness: 1, roughness: 0.3 }),
  black: S({ color: 0x121317, roughness: 0.6 }), rubber: S({ color: 0x0c0c0e, roughness: 0.95 }), fabric: S({ color: 0x2a2b2f, roughness: 1 }),
  leather: P({ color: 0x1b1a1c, roughness: 0.75, clearcoat: 0.15 }), foam: S({ color: 0x26272b, roughness: 1 }),
  copper: S({ color: 0xc98a4b, metalness: 1, roughness: 0.35 }), lens: P({ color: 0x0b1a2e, roughness: 0.05, clearcoat: 1, metalness: 0.3 }),
  gold: S({ color: 0xd4a548, metalness: 1, roughness: 0.28 }), silver: S({ color: 0xd9dde3, metalness: 1, roughness: 0.25 }),
  flex: S({ color: 0xc8742a, roughness: 0.6, metalness: 0.3 }),
  holo: new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.07, depthWrite: false, blending: THREE.AdditiveBlending }),
  edge: new THREE.LineBasicMaterial({ color: CYAN, transparent: true, opacity: 0.08 }),
};
const box = (w, h, d, r = 0.15, seg = 3) => new RoundedBoxGeometry(w, h, d, seg, Math.min(r, Math.min(w, h, d) / 2.01));
const cyl = (r, h, s = 48) => new THREE.CylinderGeometry(r, r, h, s);
function mesh(geo, mat) { const m = new THREE.Mesh(geo, mat); m.castShadow = m.receiveShadow = true; return m; }

// ------------------------------------------------------------------ the devices (cm; y up; parts: name, label, what it is, position, explode direction)
// ---- canvas textures (same technique as the keyboard's key legends)
function canvasTex(w, h, draw) { const c = document.createElement("canvas"); c.width = w; c.height = h; draw(c.getContext("2d"), w, h); const t = new THREE.CanvasTexture(c); t.anisotropy = 8; t.colorSpace = THREE.SRGBColorSpace; return t; }
const TEX = {
  screen: () => canvasTex(512, 1024, (g, w, h) => {
    const bg = g.createLinearGradient(0, 0, w, h); bg.addColorStop(0, "#0b2a4a"); bg.addColorStop(0.55, "#071424"); bg.addColorStop(1, "#1a0b2e"); g.fillStyle = bg; g.fillRect(0, 0, w, h);
    const glow = g.createRadialGradient(w * 0.7, h * 0.3, 10, w * 0.7, h * 0.3, 420); glow.addColorStop(0, "#00e5ff44"); glow.addColorStop(1, "#00000000"); g.fillStyle = glow; g.fillRect(0, 0, w, h);
    g.lineWidth = 22; g.strokeStyle = "#050607"; g.beginPath(); g.roundRect(11, 11, w - 22, h - 22, 70); g.stroke();   // bezel
    g.fillStyle = "#000"; g.beginPath(); g.roundRect(w / 2 - 70, 44, 140, 38, 19); g.fill();                     // Dynamic Island
    g.fillStyle = "#e6fbff"; g.font = "600 92px system-ui,Segoe UI,Inter,sans-serif"; g.textAlign = "center"; g.fillText("9:41", w / 2, 230);
    g.font = "500 26px system-ui,Segoe UI,Inter,sans-serif"; g.fillStyle = "#bfeef7"; g.fillText("Saturday 15 November", w / 2, 270);
    g.fillStyle = "#ffffff22"; g.beginPath(); g.roundRect(w / 2 - 60, h - 24, 120, 8, 4); g.fill();               // home indicator
  }),
  battery: () => canvasTex(512, 256, (g, w, h) => {
    g.fillStyle = "#8d9299"; g.fillRect(0, 0, w, h); g.fillStyle = "#1a1c20"; g.font = "600 30px system-ui,sans-serif"; g.textAlign = "left";
    g.fillText("Li-ion Polymer Battery", 22, 60); g.font = "400 22px system-ui,sans-serif"; g.fillText("3.87 V  3349 mAh  12.98 Wh", 22, 100);
    g.fillText("Model A2862  ·  Do not puncture or disassemble", 22, 134); g.strokeStyle = "#1a1c20"; g.lineWidth = 3; g.strokeRect(22, 160, 120, 70);
    for (let i = 0; i < 14; i++) { g.fillRect(170 + i * 22, 165 + (i % 2) * 30, 8, 30); }
  }),
  pcb: () => canvasTex(256, 1024, (g, w, h) => {
    g.fillStyle = "#0f5f3a"; g.fillRect(0, 0, w, h); g.strokeStyle = "#1a9a5e"; g.lineWidth = 2;
    for (let i = 0; i < 60; i++) { g.beginPath(); const x = Math.random() * w, y = Math.random() * h; g.moveTo(x, y); g.lineTo(x + (Math.random() - 0.5) * 120, y); g.lineTo(x + (Math.random() - 0.5) * 120, y + (Math.random() - 0.5) * 160); g.stroke(); }
    g.fillStyle = "#d4a548"; for (let i = 0; i < 90; i++) g.fillRect(Math.random() * w, Math.random() * h, 5, 5);
  }),
};
function roundedRectShape(w, h, r) { const s = new THREE.Shape(); s.moveTo(-w / 2 + r, -h / 2); s.lineTo(w / 2 - r, -h / 2); s.quadraticCurveTo(w / 2, -h / 2, w / 2, -h / 2 + r); s.lineTo(w / 2, h / 2 - r); s.quadraticCurveTo(w / 2, h / 2, w / 2 - r, h / 2); s.lineTo(-w / 2 + r, h / 2); s.quadraticCurveTo(-w / 2, h / 2, -w / 2, h / 2 - r); s.lineTo(-w / 2, -h / 2 + r); s.quadraticCurveTo(-w / 2, -h / 2, -w / 2 + r, -h / 2); return s; }
function flatRing(w, h, r, thick, depth, mat) {  // a rounded-rectangle band (frame), lying flat
  const s = roundedRectShape(w, h, r); const hole = roundedRectShape(w - 2 * thick, h - 2 * thick, Math.max(0.05, r - thick)); s.holes.push(new THREE.Path(hole.getPoints(24)));
  const geo = new THREE.ExtrudeGeometry(s, { depth, bevelEnabled: false, curveSegments: 24 }); geo.rotateX(-Math.PI / 2); geo.translate(0, -depth / 2, 0);
  const m = mesh(geo, mat); return m;
}
function edgesOn(obj, threshold = 40) { obj.traverse((o) => { if (o.isMesh && o.geometry) { const e = new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry, threshold), M.edge); e.raycast = () => {}; o.add(e); } }); return obj; }
function flex(len, wid = 0.5, mat) { return mesh(box(wid, 0.03, len, 0.01, 1), mat || M.flex); }
function screw(r = 0.07) { const g = new THREE.Group(); const h = mesh(cyl(r, 0.08, 16), M.silver); g.add(h); const c = mesh(cyl(r * 0.35, 0.05, 8), M.black); c.position.y = 0.05; g.add(c); return g; }

function iphone15() {
  const W = 7.16, H = 14.76, T = 0.78, parts = [];
  const add = (name, label, desc, obj, pos, dir, group = "body", callout = false) => parts.push({ name, label, desc, obj: edgesOn(obj), pos, dir, group, callout });
  // body
  const frame = new THREE.Group(); frame.add(flatRing(W, H, 1.12, 0.16, T, M.alu));
  for (const [x, z] of [[-W / 2 + 0.08, -5.2], [W / 2 - 0.08, -5.2], [-W / 2 + 0.08, 5.6], [W / 2 - 0.08, 5.6]]) { const a = mesh(box(0.17, T - 0.04, 0.12, 0.02, 1), M.black); a.position.set(x, 0, z); frame.add(a); }   // antenna lines
  for (const z of [-1.6, 1.6]) { const a = mesh(box(0.12, T - 0.04, 0.17, 0.02, 1), M.black); a.position.set(0, 0, z > 0 ? H / 2 - 0.08 : -H / 2 + 0.08); a.position.x = z; frame.add(a); }
  const grille = new THREE.Group(); for (let i = 0; i < 6; i++) { const h = mesh(cyl(0.06, 0.2, 10), M.black); h.rotation.x = Math.PI / 2; h.position.set(1.0 + i * 0.22, 0, H / 2); grille.add(h); } frame.add(grille);
  const grille2 = grille.clone(); grille2.position.x = -2.5; frame.add(grille2);
  add("frame", "Aluminium frame", "Aerospace-grade aluminium band with the antenna lines, speaker grilles and the port cutout; everything mounts to it.", frame, [0, 0, 0], [0, 0, 0]);
  // display assembly
  const disp = new THREE.Group();
  const st = TEX.screen();
  const oled = mesh(box(W - 0.24, 0.1, H - 0.24, 1.0, 5), new THREE.MeshPhysicalMaterial({ map: st, roughness: 0.06, clearcoat: 1, clearcoatRoughness: 0.04, emissiveMap: st, emissive: 0xffffff, emissiveIntensity: 1.0 })); oled.position.y = 0.08; disp.add(oled);
  const shield = mesh(box(W - 0.16, 0.03, H - 0.16, 1.05, 6), M.bezel); shield.position.y = 0.0; disp.add(shield);   // black glass under the panel
  const ear = mesh(box(1.0, 0.04, 0.08, 0.02, 1), M.black); ear.position.set(0, 0.19, -H / 2 + 0.5); disp.add(ear);
  const dflex = flex(1.6, 1.2); dflex.rotation.x = Math.PI / 2; dflex.position.set(1.4, -0.6, -3.2); disp.add(dflex);
  add("display", "Display assembly", "6.1-inch Super Retina XDR OLED bonded to the Ceramic Shield front glass, with the earpiece mesh and the display flex. It lifts off the frame first, hinged from the left.", disp, [0, T / 2 + 0.05, 0], [0, 7.5, 0], "front", true);
  add("display_bracket", "Display connector bracket", "Steel bracket over the display and Face ID connectors; four tri-point screws.", (() => { const g = new THREE.Group(); g.add(mesh(box(1.4, 0.08, 0.9, 0.03, 1), M.shield)); for (const [x, z] of [[-0.55, -0.3], [0.55, -0.3], [-0.55, 0.3], [0.55, 0.3]]) { const s = screw(0.06); s.position.set(x, 0.05, z); g.add(s); } return g; })(), [1.4, T / 2 - 0.2, -3.2], [0.3, 5.2, 0], "inside");
  // battery
  const bat = new THREE.Group();
  bat.add(mesh(box(3.7, 0.44, 8.8, 0.22), M.battery));
  const lab = mesh(box(3.4, 0.01, 7.9, 0.02, 1), new THREE.MeshStandardMaterial({ map: TEX.battery(), roughness: 0.9 })); lab.position.y = 0.23; bat.add(lab);
  const bflex = flex(1.1, 0.7); bflex.position.set(2.1, 0.1, -3.6); bat.add(bflex);
  const bconn = mesh(box(0.7, 0.12, 0.5, 0.03, 1), M.black); bconn.position.set(2.6, 0.12, -3.6); bat.add(bconn);
  for (const x of [-1.2, 1.2]) { const tab = mesh(box(0.5, 0.02, 1.6, 0.01, 1), M.black); tab.position.set(x, -0.2, 5.0); bat.add(tab); }   // pull tabs
  add("battery", "Battery", "3,349 mAh single-cell lithium-ion pack with four stretch-release pull tabs and its connector; the most common replacement on this phone.", bat, [-1.25, 0, 0.9], [-1.0, 4.2, 0.3], "inside", true);
  add("battery_bracket", "Battery connector bracket", "Small plate over the battery connector; remove it to disconnect power before any other step.", (() => { const g = new THREE.Group(); g.add(mesh(box(0.9, 0.08, 0.7, 0.03, 1), M.shield)); const s = screw(0.06); s.position.set(0.3, 0.05, 0.2); g.add(s); return g; })(), [1.4, 0.3, -2.7], [0.3, 4.6, 0], "inside");
  // logic board
  const lb = new THREE.Group();
  lb.add(mesh(box(2.5, 0.16, 9.2, 0.06, 1), new THREE.MeshStandardMaterial({ map: TEX.pcb(), roughness: 0.6, metalness: 0.2 })));
  for (const [z, h, lbl] of [[-3.3, 2.3], [-0.6, 2.8], [2.4, 2.0]]) { const s = mesh(box(2.1, 0.22, h, 0.05, 1), M.shield); s.position.set(0, 0.19, z); lb.add(s); }
  const die = mesh(box(1.3, 0.1, 1.3, 0.02, 1), M.black); die.position.set(0, 0.33, -0.6); lb.add(die);                        // A16 under the shield line
  for (const [x, z] of [[-1.0, 4.2], [1.0, 4.2], [-1.0, -4.3]]) { const c = mesh(box(0.45, 0.1, 0.3, 0.02, 1), M.gold); c.position.set(x, 0.14, z); lb.add(c); }  // board-to-board connectors
  for (const [x, z] of [[-1.1, -4.4], [1.1, -4.4], [-1.1, 4.4], [1.1, 4.4], [1.1, 0]]) { const s = screw(); s.position.set(x, 0.1, z); lb.add(s); }
  add("logic_board", "Logic board", "A16 Bionic, memory, storage and the radios on a stacked board under EMI shield cans, running up the right side. Five screws, three connectors.", lb, [2.05, 0, -0.7], [1.4, 5.0, -0.2], "inside", true);
  // cameras
  const cam = new THREE.Group(); cam.add(mesh(box(3.5, 0.55, 3.5, 0.5), M.black));
  for (const [x, z] of [[-0.75, -0.75], [0.75, 0.75]]) { const ring = mesh(cyl(0.95, 0.32), M.alu); ring.position.set(x, -0.35, z); cam.add(ring); const l = mesh(cyl(0.78, 0.34), M.lens); l.position.set(x, -0.4, z); cam.add(l); const g = mesh(cyl(0.45, 0.36), new THREE.MeshPhysicalMaterial({ color: 0x3b2a6b, roughness: 0.02, clearcoat: 1, metalness: 0.6 })); g.position.set(x, -0.42, z); cam.add(g); }
  const fl = mesh(cyl(0.3, 0.2), new THREE.MeshStandardMaterial({ color: 0xfff1b8, emissive: 0xffe08a, emissiveIntensity: 0.4 })); fl.position.set(0.9, -0.3, -0.95); cam.add(fl);
  const mic = mesh(cyl(0.08, 0.2, 8), M.black); mic.position.set(-0.9, -0.3, 0.95); cam.add(mic);
  const cflex = flex(2.0, 0.9); cflex.rotation.y = Math.PI / 2; cflex.position.set(2.3, 0.1, 0.8); cam.add(cflex);
  add("camera_module", "Rear camera module", "48 MP main and 12 MP ultra-wide in one housing, with the flash and the rear mic; connects to the board with one flex.", cam, [-1.7, -0.1, -4.9], [-0.9, -4.2, -0.8], "back", true);
  const bump = new THREE.Group(); const bp = mesh(box(3.9, 0.16, 3.9, 0.9, 5), M.plateau); bump.add(bp);
  for (const [x, z] of [[-0.75, -0.75], [0.75, 0.75]]) { const r = mesh(cyl(1.02, 0.18), M.alu); r.position.set(x, -0.02, z); bump.add(r); }
  add("camera_bump", "Camera bump", "Raised glass plateau on the back with the two lens rings.", bump, [-1.7, -T / 2 - 0.14, -4.9], [-0.9, -6.4, -0.8], "back");
  add("back_glass", "Back glass", "Colour-infused glass with the matte finish, glued to the frame around the edge; the camera bump sits on it.", mesh(box(W - 0.14, 0.12, H - 0.14, 1.05, 6), M.glassBack), [0, -T / 2 - 0.05, 0], [0, -5.4, 0], "back", true);
  const coil = new THREE.Group(); const ring = mesh(new THREE.TorusGeometry(1.9, 0.09, 8, 64), M.copper); ring.rotation.x = Math.PI / 2; coil.add(ring);
  const ring2 = mesh(new THREE.TorusGeometry(1.55, 0.09, 8, 64), M.copper); ring2.rotation.x = Math.PI / 2; coil.add(ring2); const ring3 = mesh(new THREE.TorusGeometry(1.2, 0.09, 8, 64), M.copper); ring3.rotation.x = Math.PI / 2; coil.add(ring3);
  for (let i = 0; i < 16; i++) { const m = mesh(box(0.5, 0.06, 0.22, 0.02, 1), M.black); const a = (i / 16) * Math.PI * 2; m.position.set(Math.cos(a) * 2.35, 0, Math.sin(a) * 2.35); m.rotation.y = -a; coil.add(m); }   // MagSafe magnets
  add("charging_coil", "Wireless charging coil + MagSafe ring", "Copper coil for Qi and MagSafe charging, with the ring of magnets around it, on a thin graphite sheet.", coil, [0, -T / 2 + 0.12, 1.2], [0, -3.0, 0], "back", true);
  // small parts
  add("taptic", "Taptic Engine", "Linear actuator that makes every click and buzz; beside the battery at the bottom, two screws.", (() => { const g = new THREE.Group(); g.add(mesh(box(1.9, 0.4, 1.6, 0.12), M.black)); const s = screw(); s.position.set(-0.7, 0.22, 0.6); g.add(s); return g; })(), [-1.95, 0, 5.7], [-1.4, 3.6, 1.2], "inside");
  add("speaker", "Loudspeaker", "Bottom-firing speaker box next to the USB-C port, with its rubber gasket.", (() => { const g = new THREE.Group(); g.add(mesh(box(2.3, 0.45, 1.4, 0.18), M.rubber)); const p = mesh(box(2.0, 0.05, 1.1, 0.05, 1), M.black); p.position.y = 0.24; g.add(p); return g; })(), [1.65, 0, 6.1], [1.3, 3.6, 1.4], "inside");
  add("usb_c", "USB-C port + flex", "Charge port, bottom microphones and the Taptic connector on one flex along the bottom edge.", (() => { const g = new THREE.Group(); const p = mesh(box(0.92, 0.34, 0.75, 0.1, 2), M.silver); g.add(p); const hole = mesh(box(0.72, 0.22, 0.4, 0.08, 1), M.black); hole.position.z = 0.2; g.add(hole); const f = flex(3.6, 0.6); f.rotation.y = Math.PI / 2; f.position.set(-1.4, 0, -0.1); g.add(f); const s1 = screw(); s1.position.set(-0.9, -0.17, 0.3); g.add(s1); const s2 = screw(); s2.position.set(0.9, -0.17, 0.3); g.add(s2); return g; })(), [0, 0, H / 2 - 0.45], [0, 2.4, 3.4], "inside");
  add("front_camera", "Front camera + Face ID", "TrueDepth module: 12 MP camera, dot projector and flood illuminator behind the Dynamic Island.", (() => { const g = new THREE.Group(); g.add(mesh(box(2.2, 0.28, 0.8, 0.3, 3), M.black)); for (const x of [-0.6, 0.1, 0.7]) { const l = mesh(cyl(0.16, 0.1, 16), M.lens); l.position.set(x, 0.16, 0); g.add(l); } return g; })(), [0, T / 2 - 0.16, -6.3], [0, 4.0, -1.4], "front");
  add("earpiece", "Earpiece speaker", "Top speaker behind the slot above the Dynamic Island; also a mic.", mesh(box(1.4, 0.2, 0.5, 0.08, 1), M.rubber), [0, T / 2 - 0.2, -7.0], [0, 3.2, -1.8], "front");
  add("sim_tray", "SIM tray", "Nano-SIM tray with its rubber seal; ejects with a pin from the left edge.", (() => { const g = new THREE.Group(); g.add(mesh(box(0.2, 0.32, 1.5, 0.05, 1), M.alu)); const t = mesh(box(0.9, 0.08, 1.3, 0.03, 1), M.black); t.position.x = 0.55; g.add(t); return g; })(), [-W / 2 - 0.05, 0, -2.2], [-2.6, 0, 0], "body");
  add("buttons", "Volume + Action buttons", "Two volume buttons and the Action button, each on a flex with a bracket.", (() => { const g = new THREE.Group(); for (const [z, len] of [[-4.7, 0.6], [-3.2, 1.0], [-1.9, 1.0]]) { const b = mesh(box(0.25, 0.32, len, 0.09, 2), M.alu); b.position.z = z; g.add(b); } return g; })(), [-W / 2 - 0.1, 0, 0], [-2.2, 0, 0], "body");
  add("power_button", "Side button", "Power and Siri button on the right edge, on the same flex as the mmWave window on US models.", mesh(box(0.25, 0.32, 1.5, 0.09, 2), M.alu), [W / 2 + 0.1, 0, -2.6], [2.2, 0, 0], "body");
  add("screws", "Pentalobe + tri-point screws", "Two pentalobe screws by the port hold the display; the rest are tri-point and Phillips inside.", (() => { const g = new THREE.Group(); for (const [x, z] of [[-0.8, 7.0], [0.8, 7.0], [-3.0, -6.2], [3.0, -6.2], [-3.0, 6.0], [3.0, 6.0], [-3.0, 0.4], [3.0, 1.2]]) { const s = screw(0.07); s.position.set(x, 0.28, z); g.add(s); } return g; })(), [0, 0, 0], [0, 6.2, 0], "inside");
  const hotspots = [
    ["Action button", [-W / 2 - 0.3, 0.2, -4.7]], ["Volume up / down", [-W / 2 - 0.3, 0.2, -2.5]], ["Side button", [W / 2 + 0.3, 0.2, -2.6]],
    ["Dynamic Island", [0, T / 2 + 0.3, -6.4]], ["USB-C", [0, 0.1, H / 2 + 0.3]], ["SIM tray", [-W / 2 - 0.3, 0.1, -1.0]],
    ["Cameras", [-1.7, -T / 2 - 0.4, -4.9]], ["Speaker + mic", [1.8, 0.1, H / 2 + 0.3]]];
  return { name: "iPhone 15", parts, spread: 1, hotspots, upright: true, views: { iso: [12, 16, 20] }, fact: "128 to 512 GB, Dynamic Island, USB-C. Battery and display are the two repairs people actually do; disconnect the battery first, always." };
}
TEX.ipad = () => canvasTex(1024, 768, (g, w, h) => {
  const bg = g.createLinearGradient(0, 0, w, h); bg.addColorStop(0, "#102a4a"); bg.addColorStop(0.5, "#0a1a2e"); bg.addColorStop(1, "#2a0f3a"); g.fillStyle = bg; g.fillRect(0, 0, w, h);
  const glow = g.createRadialGradient(w * 0.3, h * 0.7, 10, w * 0.3, h * 0.7, 520); glow.addColorStop(0, "#00e5ff33"); glow.addColorStop(1, "#00000000"); g.fillStyle = glow; g.fillRect(0, 0, w, h);
  g.lineWidth = 46; g.strokeStyle = "#050607"; g.beginPath(); g.roundRect(23, 23, w - 46, h - 46, 40); g.stroke();   // bezel
  g.fillStyle = "#e6fbff"; g.font = "600 110px system-ui,Segoe UI,Inter,sans-serif"; g.textAlign = "center"; g.fillText("9:41", w / 2, 300);
  g.font = "500 30px system-ui,Segoe UI,Inter,sans-serif"; g.fillStyle = "#bfeef7"; g.fillText("Saturday 15 November", w / 2, 345);
  g.fillStyle = "#ffffff22"; g.beginPath(); g.roundRect(w / 2 - 70, h - 42, 140, 8, 4); g.fill();
});
TEX.fabric = () => canvasTex(256, 256, (g, w, h) => { g.fillStyle = "#2b2c31"; g.fillRect(0, 0, w, h); for (let y = 0; y < h; y += 4) for (let x = 0; x < w; x += 4) { g.fillStyle = ((x + y) / 4) % 2 ? "#34363c" : "#232428"; g.fillRect(x, y, 3, 3); } });
TEX.ipadBattery = () => canvasTex(512, 1024, (g, w, h) => { g.fillStyle = "#8d9299"; g.fillRect(0, 0, w, h); g.fillStyle = "#1a1c20"; g.font = "600 44px system-ui,sans-serif"; g.textAlign = "center"; g.fillText("Li-ion Polymer", w / 2, 120); g.font = "400 34px system-ui,sans-serif"; g.fillText("3.82 V · 7606 mAh · 28.93 Wh", w / 2, 180); g.fillText("Do not puncture · Recycle", w / 2, 230); g.strokeStyle = "#1a1c20"; g.lineWidth = 4; g.strokeRect(80, 300, w - 160, 500); });

function ipad11() {
  const W = 17.95, H = 24.86, T = 0.70, parts = [];
  const add = (name, label, desc, obj, pos, dir, group = "body", callout = false) => parts.push({ name, label, desc, obj: edgesOn(obj), pos, dir, group, callout });
  // shell: one-piece aluminium with the camera opening, antenna window, speaker grilles and the port
  const shell = new THREE.Group(); shell.add(flatRing(W, H, 0.9, 0.14, T, M.alu));
  const back = mesh(box(W - 0.2, 0.1, H - 0.2, 0.8, 4), M.alu); back.position.y = -T / 2 + 0.06; shell.add(back);
  const ant = mesh(box(W - 0.6, 0.11, 0.9, 0.05, 1), M.black); ant.position.set(0, -T / 2 + 0.06, -H / 2 + 0.7); shell.add(ant);   // antenna window
  for (const zEdge of [-H / 2, H / 2]) for (const x0 of [-7.5, 5.3]) for (let i = 0; i < 8; i++) { const hole = mesh(cyl(0.06, 0.2, 10), M.black); hole.rotation.x = Math.PI / 2; hole.position.set(x0 + i * 0.3, 0, zEdge); shell.add(hole); }
  add("shell", "Aluminium back shell", "One-piece recycled aluminium housing with the antenna window, four speaker grilles and the port cutout; everything screws or glues into it.", shell, [0, 0, 0], [0, 0, 0]);
  // display assembly: LCD + digitizer + cover glass, black bezel, with its two flexes
  const disp = new THREE.Group(); const st = TEX.ipad();
  const panel = mesh(box(W - 0.26, 0.14, H - 0.26, 0.85, 5), new THREE.MeshPhysicalMaterial({ map: st, roughness: 0.06, clearcoat: 1, clearcoatRoughness: 0.04, emissiveMap: st, emissive: 0xffffff, emissiveIntensity: 0.9 })); panel.position.y = 0.09; disp.add(panel);
  const under = mesh(box(W - 0.2, 0.04, H - 0.2, 0.9, 5), M.bezel); disp.add(under);
  for (const z of [-3.5, 3.5]) { const f = flex(2.2, 1.6); f.rotation.x = Math.PI / 2; f.position.set(W / 2 - 3.2, -0.9, z); disp.add(f); }
  add("display", "Display assembly", "11-inch Liquid Retina LCD laminated to the cover glass and digitizer, with the two display flexes; held by adhesive all around, so it comes off first with heat.", disp, [0, T / 2 + 0.07, 0], [0, 7.5, 0], "front", true);
  add("display_bracket", "Display cable bracket", "Steel bracket over the display and digitizer connectors; four screws.", (() => { const g = new THREE.Group(); g.add(mesh(box(2.4, 0.08, 7.6, 0.03, 1), M.shield)); for (const z of [-3.2, -1.1, 1.1, 3.2]) { const s = screw(0.07); s.position.set(0.9, 0.05, z); g.add(s); } return g; })(), [W / 2 - 3.2, T / 2 - 0.25, 0], [0.6, 5.0, 0], "inside");
  // batteries: two cells, labelled, with the battery flex between them
  const cell = (side) => { const g = new THREE.Group(); g.add(mesh(box(6.6, 0.36, 16.0, 0.3), M.battery)); const l = mesh(box(6.2, 0.01, 15.2, 0.02, 1), new THREE.MeshStandardMaterial({ map: TEX.ipadBattery(), roughness: 0.9 })); l.position.y = 0.19; g.add(l); for (const z of [-5, 0, 5]) { const tab = mesh(box(0.5, 0.02, 2.4, 0.01, 1), M.black); tab.position.set(side * 3.0, -0.17, z); g.add(tab); } return g; };
  add("battery_left", "Battery cell (left)", "One of two lithium-polymer cells, 28.9 Wh together, glued with six stretch-release strips.", cell(-1), [-4.7, 0, 1.0], [-3.0, 4.2, 0], "inside", true);
  add("battery_right", "Battery cell (right)", "The second cell, mirror of the left, joined to the board by the battery flex.", cell(1), [4.7, 0, 1.0], [3.0, 4.2, 0], "inside", true);
  add("battery_flex", "Battery flex + connector", "Joins both cells to the logic board; disconnect this before anything else.", (() => { const g = new THREE.Group(); const f = flex(3.4, 1.0); f.rotation.y = Math.PI / 2; g.add(f); const c = mesh(box(0.9, 0.14, 0.6, 0.03, 1), M.black); c.position.set(0, 0.08, -0.6); g.add(c); return g; })(), [0, 0.2, 7.6], [0, 5.6, 0], "inside");
  // logic board between the cells
  const lb = new THREE.Group(); lb.add(mesh(box(3.6, 0.16, 15.0, 0.06, 1), new THREE.MeshStandardMaterial({ map: TEX.pcb(), roughness: 0.6, metalness: 0.2 })));
  for (const [z, h] of [[-5.5, 3.2], [-1.5, 3.8], [2.6, 3.0], [6.0, 2.2]]) { const s = mesh(box(3.1, 0.24, h, 0.05, 1), M.shield); s.position.set(0, 0.2, z); lb.add(s); }
  const die = mesh(box(1.6, 0.1, 1.6, 0.02, 1), M.black); die.position.set(0, 0.36, -1.5); lb.add(die);
  for (const [x, z] of [[-1.3, -7.1], [1.3, -7.1], [-1.3, 7.1], [1.3, 7.1], [1.4, 0.4]]) { const c = mesh(box(0.5, 0.1, 0.32, 0.02, 1), M.gold); c.position.set(x, 0.14, z); lb.add(c); }
  for (const [x, z] of [[-1.6, -7.3], [1.6, -7.3], [-1.6, 7.3], [1.6, 7.3], [-1.6, 0], [1.6, 0]]) { const s = screw(); s.position.set(x, 0.1, z); lb.add(s); }
  add("logic_board", "Logic board", "A16 chip, memory and storage on the long narrow board between the cells, under four EMI shields; six screws and five connectors.", lb, [0, 0, 0.4], [0, 5.0, 0], "inside", true);
  // speakers, cameras, ports, buttons
  const spk = (x, z) => { const g = new THREE.Group(); const b = mesh(box(3.2, 0.42, 1.4, 0.2), M.rubber); b.position.set(x, 0, z); g.add(b); const p = mesh(box(2.9, 0.04, 1.1, 0.04, 1), M.black); p.position.set(x, 0.23, z); g.add(p); return g; };
  add("speakers_top", "Speakers (top edge)", "Two of the four speaker boxes, behind the top grilles, for landscape stereo.", (() => { const g = new THREE.Group(); g.add(spk(-6.3, -11.2)); g.add(spk(6.3, -11.2)); return g; })(), [0, 0, 0], [0, 3.6, -2.6], "inside");
  add("speakers_bottom", "Speakers (bottom edge)", "The other two speaker boxes, beside the USB-C port.", (() => { const g = new THREE.Group(); g.add(spk(-6.3, 11.2)); g.add(spk(6.3, 11.2)); return g; })(), [0, 0, 0], [0, 3.6, 2.6], "inside");
  const cam = new THREE.Group(); cam.add(mesh(box(1.9, 0.5, 1.9, 0.3), M.black)); const ring = mesh(cyl(0.82, 0.22), M.alu); ring.position.y = -0.3; cam.add(ring); const l = mesh(cyl(0.6, 0.26), M.lens); l.position.y = -0.34; cam.add(l); const gl = mesh(cyl(0.34, 0.28), new THREE.MeshPhysicalMaterial({ color: 0x3b2a6b, roughness: 0.02, clearcoat: 1, metalness: 0.6 })); gl.position.y = -0.36; cam.add(gl);
  const cf = flex(1.6, 0.7); cf.position.set(1.2, 0.1, 0); cam.add(cf);
  add("rear_camera", "Rear camera", "12 MP wide camera in the top-left corner, with its flex.", cam, [-7.3, -0.05, -10.8], [-1.4, -3.2, -1.6], "back", true);
  add("front_camera", "Front camera (landscape)", "12 MP ultra-wide front camera on the long edge, so you are centred in video calls; one flex to the board.", (() => { const g = new THREE.Group(); g.add(mesh(box(1.6, 0.26, 0.9, 0.2, 2), M.black)); const l = mesh(cyl(0.22, 0.1, 16), M.lens); l.position.y = 0.16; g.add(l); const f = flex(1.6, 0.5); f.rotation.y = Math.PI / 2; f.position.set(-1.2, 0, 0); g.add(f); return g; })(), [W / 2 - 1.1, T / 2 - 0.2, 0], [2.0, 4.0, 0], "front");
  add("usb_c", "USB-C port + flex", "Charging and data, with the bottom microphone, on a flex along the bottom edge.", (() => { const g = new THREE.Group(); const p = mesh(box(0.92, 0.34, 0.75, 0.1, 2), M.silver); g.add(p); const hole = mesh(box(0.72, 0.22, 0.4, 0.08, 1), M.black); hole.position.z = 0.2; g.add(hole); const f = flex(4.0, 0.7); f.rotation.y = Math.PI / 2; f.position.set(-2.4, 0, -0.1); g.add(f); for (const x of [-0.9, 0.9]) { const s = screw(); s.position.set(x, -0.17, 0.3); g.add(s); } return g; })(), [0, 0, H / 2 - 0.45], [0, 2.4, 3.4], "inside");
  add("smart_connector", "Smart Connector", "Three pogo pins on the side edge for the keyboard case, on their own flex.", (() => { const g = new THREE.Group(); const p = mesh(box(0.12, 0.08, 2.6, 0.03, 1), M.gold); g.add(p); for (const z of [-0.8, 0, 0.8]) { const pin = mesh(cyl(0.12, 0.1, 10), M.gold); pin.rotation.z = Math.PI / 2; pin.position.set(0.06, 0, z); g.add(pin); } const f = flex(2.0, 0.5); f.position.set(-1.1, 0, 0); f.rotation.y = Math.PI / 2; g.add(f); return g; })(), [W / 2 - 0.06, -0.1, 2.0], [2.6, 0, 0], "body");
  add("top_button", "Top button (Touch ID)", "Power button with the Touch ID sensor built in, on the top edge; its flex runs down the inside of the shell.", (() => { const g = new THREE.Group(); g.add(mesh(box(1.5, 0.26, 0.26, 0.1, 2), M.alu)); const f = flex(2.2, 0.5); f.position.set(0, -0.3, 1.3); g.add(f); return g; })(), [-6.6, 0, -H / 2 - 0.1], [-1.5, 0, -2.6], "body");
  add("volume", "Volume buttons", "Volume up and down on the left edge, near the top.", (() => { const g = new THREE.Group(); for (const z of [-10.0, -8.6]) { const b = mesh(box(0.26, 0.26, 1.1, 0.1, 2), M.alu); b.position.z = z; g.add(b); } return g; })(), [-W / 2 - 0.1, 0, 0], [-2.6, 0, 0], "body");
  add("screws", "Shell screws", "Phillips screws holding the board, brackets and speakers to the shell.", (() => { const g = new THREE.Group(); for (const [x, z] of [[-8.2, -11.6], [8.2, -11.6], [-8.2, 11.6], [8.2, 11.6], [-2.4, -9.5], [2.4, -9.5], [-2.4, 9.5], [2.4, 9.5]]) { const s = screw(0.08); s.position.set(x, 0.26, z); g.add(s); } return g; })(), [0, 0, 0], [0, 6.4, 0], "inside");
  const hotspots = [["Top button · Touch ID", [-6.6, 0.3, -H / 2 - 0.4]], ["Volume", [-W / 2 - 0.4, 0.2, -9.3]], ["USB-C", [0, 0.2, H / 2 + 0.4]], ["Smart Connector", [W / 2 + 0.4, 0, 2.0]], ["Front camera", [W / 2 - 1.1, T / 2 + 0.4, 0]], ["Rear camera", [-7.3, -T / 2 - 0.5, -10.8]], ["Speakers", [6.3, 0.2, -H / 2 - 0.4]]];
  return { name: "iPad (11th gen)", parts, spread: 1, hotspots, upright: true, views: { iso: [18, 24, 30] }, fact: "A16 chip, 11-inch Liquid Retina, landscape front camera, USB-C. The batteries and display are glued: heat, patience, and disconnect the battery flex first." };
}

function momentum4() {
  const parts = [];
  const add = (name, label, desc, obj, pos, dir, group = "body", callout = false) => parts.push({ name, label, desc, obj: edgesOn(obj, 50), pos, dir, group, callout });
  const fabricMat = new THREE.MeshStandardMaterial({ map: TEX.fabric(), roughness: 1, metalness: 0 });
  // headband: fabric-wrapped steel band with the padded crown and the two metal sliders
  const band = new THREE.Group();
  const arc = new THREE.QuadraticBezierCurve3(V3(-8.6, 3.6, 0), V3(0, 13.4, 0), V3(8.6, 3.6, 0));
  const tube = mesh(new THREE.TubeGeometry(arc, 64, 0.0, 4, false), M.black); band.add(tube);
  // along an in-plane path, the shape's x is radial (thickness) and y is sideways (width)
  const bandShape = new THREE.Shape(); bandShape.moveTo(-0.45, -1.5); bandShape.lineTo(0.45, -1.5); bandShape.quadraticCurveTo(0.5, 0, 0.45, 1.5); bandShape.lineTo(-0.45, 1.5); bandShape.quadraticCurveTo(-0.5, 0, -0.45, -1.5);
  const bandGeo = new THREE.ExtrudeGeometry(bandShape, { extrudePath: arc, steps: 64, bevelEnabled: false }); band.add(mesh(bandGeo, fabricMat));
  const padArc = new THREE.QuadraticBezierCurve3(V3(-4.2, 5.6, 0), V3(0, 9.4, 0), V3(4.2, 5.6, 0));
  const padShape = new THREE.Shape(); padShape.absellipse(0, 0, 0.75, 1.7, 0, Math.PI * 2);
  band.add(mesh(new THREE.ExtrudeGeometry(padShape, { extrudePath: padArc, steps: 32, bevelEnabled: false }), M.leather));
  add("headband", "Headband", "Fabric-wrapped sprung steel band with the padded crown; the sliders ride inside it to adjust the size.", band, [0, 0, 0], [0, 4.5, 0], "body", true);
  for (const side of [-1, 1]) {
    const s = side < 0 ? "left" : "right", L = side < 0 ? "Left" : "Right";
    const slider = new THREE.Group(); const rail = mesh(box(0.9, 4.6, 0.5, 0.2, 2), M.alu); rail.position.y = 1.4; slider.add(rail);
    for (const y of [0.4, 1.2, 2.0, 2.8]) { const notch = mesh(box(0.95, 0.06, 0.52, 0.01, 1), M.black); notch.position.y = y; slider.add(notch); }
    add(`slider_${s}`, `${L} slider`, "Metal slider that adjusts the headband length, with click stops.", slider, [side * 8.8, 1.6, 0], [side * 0.6, 3.2, 0], "body");
    const yoke = new THREE.Group(); const hinge = mesh(cyl(0.55, 1.1, 24), M.black); hinge.rotation.z = Math.PI / 2; yoke.add(hinge);
    const arm = mesh(new THREE.TorusGeometry(2.2, 0.28, 10, 32, Math.PI), M.black); arm.rotation.z = -Math.PI / 2; arm.rotation.y = side < 0 ? Math.PI / 2 : -Math.PI / 2; arm.position.set(side * 0.2, -2.2, 0); yoke.add(arm);
    add(`yoke_${s}`, `${L} yoke`, "Hinge and half-ring that holds the ear cup, lets it swivel to your head and fold flat.", yoke, [side * 9.0, -0.6, 0], [side * 1.6, 1.4, 0], "body");
    const cup = new THREE.Group(); const shellOuter = mesh(box(2.4, 8.6, 7.4, 1.3, 7), M.black); cup.add(shellOuter);
    const plate = mesh(box(0.3, 7.4, 6.2, 0.9, 5), fabricMat); plate.position.x = side * 1.25; cup.add(plate);          // fabric outer plate (touch surface on the right)
    for (const [y, z] of [[2.8, 2.4], [-3.0, -2.4], [3.2, -2.8]]) { const mic = mesh(cyl(0.12, 0.3, 10), M.silver); mic.rotation.z = Math.PI / 2; mic.position.set(side * 1.2, y, z); cup.add(mic); }   // ANC mic ports
    if (side > 0) { const usb = mesh(box(0.5, 0.3, 0.9, 0.1, 2), M.silver); usb.position.set(0.3, -4.3, 1.4); cup.add(usb); const jack = mesh(cyl(0.22, 0.3, 16), M.silver); jack.position.set(0.3, -4.3, -0.6); cup.add(jack); const btn = mesh(box(0.35, 0.22, 1.1, 0.08, 2), M.silver); btn.position.set(0.3, -4.3, 0.3); cup.add(btn); const led = mesh(cyl(0.08, 0.1, 8), new THREE.MeshStandardMaterial({ color: 0x3dffa8, emissive: 0x3dffa8, emissiveIntensity: 1.2 })); led.position.set(0.3, -4.3, 2.2); cup.add(led); }
    add(`cup_${s}`, `${L} ear cup`, side > 0 ? "Right cup: fabric touch surface on the outside; power/Bluetooth button, USB-C, 3.5 mm jack and the status LED on the bottom edge." : "Left cup: plastic shell with the fabric outer plate and three noise-cancelling mic ports.", cup, [side * 9.4, -3.2, 0], [side * 3.2, 0, 0], "cups", true);
    const ring = new THREE.Group(); const r = mesh(new THREE.TorusGeometry(3.3, 0.18, 10, 48), M.black); r.rotation.y = Math.PI / 2; ring.add(r); for (let i = 0; i < 4; i++) { const a = i * Math.PI / 2; const clip = mesh(box(0.3, 0.5, 0.5, 0.05, 1), M.black); clip.position.set(0, Math.cos(a) * 3.3, Math.sin(a) * 3.3); ring.add(clip); }
    add(`cushion_ring_${s}`, `${L} cushion clip ring`, "The plate the cushion clips onto; the cushion twists off it for replacement.", ring, [side * 8.1, -3.2, 0], [side * -1.2, 0, 0], "cups");
    const cushion = new THREE.Group(); const t = mesh(new THREE.TorusGeometry(3.2, 1.05, 22, 56), M.leather); t.rotation.y = Math.PI / 2; cushion.add(t); const inner = mesh(cyl(2.3, 0.2, 48), M.foam); inner.rotation.z = Math.PI / 2; inner.position.x = side * 0.4; cushion.add(inner);
    add(`cushion_${s}`, `${L} ear cushion`, "Memory-foam pad with a protein-leather cover; it twists off the clip ring and is the part that wears out first.", cushion, [side * 7.3, -3.2, 0], [side * -3.6, 0, 0], "cups", true);
    const drv = new THREE.Group(); const magnet = mesh(cyl(2.1, 0.5, 48), M.silver); magnet.rotation.z = Math.PI / 2; drv.add(magnet); const cone = mesh(new THREE.ConeGeometry(1.9, 0.5, 48, 1, true), M.black); cone.rotation.z = side * Math.PI / 2; cone.position.x = side * -0.3; drv.add(cone); const mesh2 = mesh(cyl(2.2, 0.06, 48), new THREE.MeshStandardMaterial({ color: 0x1a1b1f, roughness: 0.9, wireframe: true })); mesh2.rotation.z = Math.PI / 2; mesh2.position.x = side * -0.7; drv.add(mesh2);
    add(`driver_${s}`, `${L} driver`, "42 mm dynamic driver, angled toward the ear, behind a protective mesh.", drv, [side * 8.6, -3.2, 0], [side * -2.2, 0.6, 0], "inside", true);
  }
  const bat = new THREE.Group(); bat.add(mesh(box(1.0, 4.4, 2.8, 0.3), M.battery)); const bl = mesh(box(0.02, 3.8, 2.3, 0.02, 1), new THREE.MeshStandardMaterial({ map: TEX.battery(), roughness: 0.9 })); bl.position.x = 0.51; bat.add(bl); const bf = flex(1.4, 0.5); bf.rotation.z = Math.PI / 2; bf.position.set(0, 2.6, -0.8); bat.add(bf);
  add("battery", "Battery", "700 mAh pack in the right cup: about 60 hours with noise cancelling on. One flex to the board.", bat, [10.0, -2.4, 1.4], [5.2, 0.4, 1.2], "inside", true);
  const pcb = new THREE.Group(); pcb.add(mesh(box(0.4, 5.4, 4.6, 0.1, 1), new THREE.MeshStandardMaterial({ map: TEX.pcb(), roughness: 0.6, metalness: 0.2 }))); for (const [y, z, h] of [[1.4, -1.2, 1.6], [-1.2, 0.8, 1.9]]) { const s = mesh(box(0.26, h, 1.8, 0.04, 1), M.shield); s.position.set(0.3, y, z); pcb.add(s); } for (const [y, z] of [[2.3, 1.8], [-2.3, -1.8], [2.3, -1.8]]) { const s = screw(0.07); s.rotation.z = -Math.PI / 2; s.position.set(0.25, y, z); pcb.add(s); }
  add("pcb", "Main board", "Bluetooth 5.2 SoC, the ANC DSP and the touch controller, under the right cup's outer plate.", pcb, [10.3, -3.6, -1.0], [5.6, 0, -1.4], "inside", true);
  add("anc_mics", "Noise-cancelling mic array", "Four microphones per cup: two outside for feed-forward, two inside for feedback cancelling.", (() => { const g = new THREE.Group(); for (const [y, z] of [[-1, 2.6], [-5.4, -2.4], [2.0, -2.0]]) { const m = mesh(cyl(0.3, 0.3, 12), M.silver); m.rotation.z = Math.PI / 2; m.position.set(0, y, z); g.add(m); } return g; })(), [-10.9, 0, 0], [-3.4, 0, 0], "cups");
  const hotspots = [["Touch control (right cup)", [11.2, -1.0, 0]], ["Power · Bluetooth", [9.9, -8.0, 0.3]], ["USB-C", [9.9, -8.0, 1.6]], ["3.5 mm jack", [9.9, -8.0, -1.0]], ["Noise-cancelling mics", [-11.0, -0.4, 2.6]], ["Cushion: twists off", [-7.3, -7.2, 0]], ["Size slider", [-8.8, 4.6, 0]]];
  return { name: "Sennheiser Momentum 4", parts, spread: 1, hotspots, views: { iso: [18, 14, 26] }, fact: "60-hour battery, adaptive noise cancelling, fabric headband and touch control on the right cup. The ear cushions twist off and are the usual wear part." };
}
// UK 13 A moulded mains plug (BS 1363): the modern, sealed type with no screws. The demo repair is swapping its fuse:
// the fuse sits behind a small snap-shut door on the back, prised open with a flat screwdriver at the notch.
// Cm, y up; the plug stands on its pins, back up. Real size: ~5 x 6.2 cm footprint, ~3.2 cm deep, pins 1.75 cm (earth 2.3 cm).
function plug13a() {
  const parts = [];
  const add = (name, label, desc, obj, pos, dir, group = "plug", callout = false) => parts.push({ name, label, desc, obj: edgesOn(obj, 40), pos, dir, group, callout });
  const W = 5.0, D = 6.2, T = 3.2, cavDepth = 1.2, floorY = T - cavDepth;
  const cx0 = -1.4, cx1 = 1.4, cz0 = 0.85, cz1 = 2.35, cz = (cz0 + cz1) / 2;   // the fuse cavity under the door
  const shell = new THREE.MeshPhysicalMaterial({ color: 0x4b4f58, roughness: 0.5, clearcoat: 0.3, clearcoatRoughness: 0.5 });   // moulded plastic: mid grey so it reads on both themes
  const doorMat = new THREE.MeshPhysicalMaterial({ color: 0x5d626d, roughness: 0.45, clearcoat: 0.3 });
  const steel = M.silver, brass = M.gold;
  const ceramic = new THREE.MeshStandardMaterial({ color: 0xe6d9ba, roughness: 0.7 }), rubber = new THREE.MeshStandardMaterial({ color: 0x3a3d44, roughness: 0.8 });
  const flexMat = new THREE.MeshStandardMaterial({ color: 0xc9ccd1, roughness: 0.6 }), dark = new THREE.MeshStandardMaterial({ color: 0x15171b, roughness: 0.6 });

  // moulded body: a solid block with a rectangular cavity in the back (a slab plus a frame of blocks around the cavity)
  const body = new THREE.Group();
  const slab = mesh(box(W, floorY, D, 0.55, 4), shell); slab.position.y = floorY / 2; body.add(slab);
  const frame = (w, d, x, z) => { const b = mesh(box(w, cavDepth, d, 0.18, 2), shell); b.position.set(x, floorY + cavDepth / 2, z); body.add(b); };
  frame(W - 0.06, D / 2 + cz0 - 0.03, 0, (-D / 2 + cz0) / 2);              // the back, beyond the cavity
  frame(W - 0.06, D / 2 - cz1 - 0.03, 0, (cz1 + D / 2) / 2);               // the cable end
  for (const sx of [-1, 1]) frame(W / 2 + cx0 - 0.03, cz1 - cz0, sx * (cx1 + (W / 2 - cx1) / 2), cz);   // the two sides of the cavity
  add("body", "Moulded plug body", "The sealed, moulded plastic body of the plug. It has no screws and is not meant to be opened: the only way in is the small fuse door on the back.", body, [0, 0, 0], [0, 0, 0], "plug");

  // fuse door: the snap-fit cover over the fuse, with a notch to prise it open
  const door = new THREE.Group(); const dp = mesh(box(cx1 - cx0 + 0.1, 0.25, cz1 - cz0 + 0.1, 0.08, 2), doorMat); door.add(dp);
  const doorTex = canvasTex(256, 128, (g, w, h) => { g.fillStyle = "#5d626d"; g.fillRect(0, 0, w, h); g.fillStyle = "#e7e9ee"; g.textAlign = "center"; g.font = "700 46px system-ui,sans-serif"; g.fillText("13A", w / 2, 62); g.font = "600 22px system-ui,sans-serif"; g.fillText("FUSE", w / 2, 98); });
  const mark = new THREE.Mesh(new THREE.PlaneGeometry(1.9, 0.95), new THREE.MeshStandardMaterial({ map: doorTex, roughness: 0.5 })); mark.rotation.x = -Math.PI / 2; mark.position.set(0, 0.128, -0.12); door.add(mark);
  const notch = mesh(box(0.9, 0.12, 0.16, 0.02, 1), dark); notch.position.set(0, 0.1, (cz1 - cz0) / 2 + 0.02); door.add(notch);          // the slot to prise it open
  for (const x of [-0.45, 0, 0.45]) { const rib = mesh(box(0.1, 0.06, 0.22, 0.01, 1), doorMat); rib.position.set(x, 0.15, (cz1 - cz0) / 2 - 0.2); door.add(rib); }
  add("fuse_door", "Fuse door", "The small snap-fit door on the back that covers the fuse. There is no screw: put a small flat screwdriver in the notch at the cable end and prise it up. It clicks shut again.", door, [0, T - 0.125, cz], [0, 4.6, 1.6], "plug", true);

  // fuse holder and fuse
  const holder = new THREE.Group(); const cradle = mesh(box(3.0, 0.16, 1.1, 0.05, 1), rubber); cradle.position.y = -0.08; holder.add(cradle);
  for (const sx of [-1, 1]) { const clip = mesh(box(0.26, 0.55, 0.72, 0.05, 1), brass); clip.position.set(sx * 1.35, 0.2, 0); holder.add(clip); }
  add("fuse_holder", "Fuse holder (clips)", "The two spring brass clips the cartridge fuse is held in. The current passes from the live pin through the fuse into the cable.", holder, [0, floorY + 0.16, cz], [0, 2.4, 0], "fuse");
  const fuse = new THREE.Group(); const fb = mesh(cyl(0.32, 2.54, 28), ceramic); fb.rotation.z = Math.PI / 2; fuse.add(fb);
  for (const sx of [-1, 1]) { const cap = mesh(cyl(0.34, 0.42, 28), steel); cap.rotation.z = Math.PI / 2; cap.position.x = sx * 1.06; fuse.add(cap); }
  const band = mesh(cyl(0.325, 0.5, 28), new THREE.MeshStandardMaterial({ color: 0x7a431c, roughness: 0.6 })); band.rotation.z = Math.PI / 2; fuse.add(band);   // a 13 A fuse's colour band is brown (3 A red, 5 A black)
  add("fuse", "Cartridge fuse (13 A)", "The 13 A (BS 1362) ceramic cartridge fuse, 25.4 x 6.3 mm. When it blows the plug stops working. Replace it with the same rating: 3 A for lamps and small devices, 13 A for heaters and kettles.", fuse, [0, floorY + 0.7, cz], [0, 6.4, 0.6], "fuse", true);

  // pins (front face): earth on top and longest, live and neutral below, each with an insulating sleeve at the base
  const earth = new THREE.Group(); const ep = mesh(box(0.42, 2.3, 0.17, 0.03, 1), steel); ep.position.y = -1.15; earth.add(ep);
  const ehole = mesh(box(0.1, 0.5, 0.2, 0.01, 1), dark); ehole.position.y = -1.9; earth.add(ehole);
  add("earth_pin", "Earth pin", "The longest, top pin. It is what opens the shutters in a socket so the live and neutral pins can go in.", earth, [0, 0, -1.55], [0, -3.4, -0.6], "contacts");
  for (const [nm, lb, sx, ds] of [["live_pin", "Live pin", 1, "The lower right pin (looking at the front). It carries the current in through the fuse."], ["neutral_pin", "Neutral pin", -1, "The lower left pin. It is the return path for the current."]]) {
    const pin = new THREE.Group(); const pp = mesh(box(0.17, 1.75, 0.42, 0.03, 1), steel); pp.position.y = -0.875; pin.add(pp);
    const sleeve = mesh(box(0.32, 0.9, 0.62, 0.05, 1), shell); sleeve.position.y = -0.45; pin.add(sleeve);
    add(nm, lb, ds, pin, [sx * 1.1, 0, 0.65], [sx * 1.4, -3.0, 0.5], "contacts");
  }

  // cable boot (the moulded strain relief) and the flex, which is part of the plug and cannot be taken out
  const boot = new THREE.Group(); const cone = mesh(new THREE.CylinderGeometry(0.42, 0.78, 1.7, 28), rubber); cone.rotation.x = Math.PI / 2; boot.add(cone);
  for (const z of [-0.45, -0.1, 0.25, 0.6]) { const ring = mesh(new THREE.TorusGeometry(0.62 - (0.6 - z) * 0.12, 0.05, 8, 24), rubber); ring.position.z = z; boot.add(ring); }
  add("cable_boot", "Cable boot", "The moulded rubber sleeve where the cable leaves the plug. It stops the cable bending sharply and being pulled off the wires inside.", boot, [0, T / 2 - 0.2, D / 2 + 0.75], [0, 0, 2.0], "cable");
  const cable = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([V3(0, T / 2 - 0.2, D / 2 + 1.5), V3(0, T / 2 - 0.25, D / 2 + 2.8), V3(0.2, T / 2 - 0.4, D / 2 + 5.2), V3(0.6, T / 2 - 0.6, D / 2 + 7.2)]), 40, 0.36, 12, false), flexMat); cable.castShadow = cable.receiveShadow = true;
  add("cable", "Cable (flex)", "The mains flex, moulded permanently into the plug. If it is damaged, the whole plug or cable has to be replaced; it cannot be repaired from outside.", cable, [0, 0, 0], [0, 0, 4.2], "cable");

  const hotspots = [["Earth pin (longest)", [0, -2.5, -1.55]], ["Live pin", [1.1, -2.0, 0.65]], ["Neutral pin", [-1.1, -2.0, 0.65]], ["Fuse door", [0, T + 0.5, cz]], ["Cable boot", [0, 1.7, D / 2 + 1.6]]];
  const K = 2.6;   // real size is 6 cm; shown at the scale of the phone and headphones so it fills the view
  for (const p of parts) { p.obj.scale.setScalar(K); p.pos = p.pos.map((v) => v * K); p.dir = p.dir.map((v) => v * K); }
  for (const h of hotspots) h[1] = h[1].map((v) => v * K);
  return { name: "UK 3-pin plug", parts, spread: 1, hotspots, views: { iso: [10, 9, 14] }, fact: "A modern UK 13-amp moulded plug (BS 1363). It has no screws: the fuse is behind a small snap-shut door on the back that you prise open with a flat screwdriver. The fuse is the safety link: 13 A for most appliances, 3 A for lamps and small devices. Always unplug it from the wall first." };
}

const DEVICES = { iphone15: iphone15, ipad11: ipad11, momentum4: momentum4, plug13a: plug13a };
const d = (QP.get("d") || "iphone15").toLowerCase();
// the example under the "say what is wrong" prompt is a fault this device can really have (a plug has no screen)
const EXAMPLE_FAULT = { iphone15: "the screen is cracked", ipad11: "the battery drains too fast", momentum4: "the ear cushion is torn", plug13a: "the fuse in my plug has blown" };
const device = (DEVICES[d] || iphone15)();
document.title = `Spanner · ${device.name}`; $("sessTitle").textContent = device.name + " repair"; $("sessSub").textContent = device.name + " · guided repair"; $("sceneTitle").textContent = device.name;

// ------------------------------------------------------------------ build + explode
const parts = {}, group = new THREE.Group(); scene.add(group);
let explode = 0, xray = false, selected = null, voiceOn = false, micOn = false;
for (const p of device.parts) {
  p.obj.traverse((o) => { if (o.isMesh) { o.castShadow = o.receiveShadow = true; o.userData.part = p.name; } });
  p.obj.position.set(...p.pos); group.add(p.obj); parts[p.name] = p;
}
function updatePositions() {
  for (const p of Object.values(parts)) {
    const dir = V3(...p.dir); p.obj.position.set(p.pos[0] + dir.x * explode * device.spread, p.pos[1] + dir.y * explode * device.spread, p.pos[2] + dir.z * explode * device.spread);
    updateHotspots();
    if (p.callout) {
      if (!p.calloutObj) { const div = document.createElement("div"); div.className = "callout"; div.textContent = p.label.toUpperCase(); p.calloutObj = new CSS2DObject(div); p.calloutObj.position.set(0, 0.9, 0); p.obj.add(p.calloutObj); }
      p.calloutObj.element.style.opacity = explode > 0.6 && !selected ? String(Math.min(1, (explode - 0.6) * 2.5)) : "0";
    }
  }
}
if (device.upright) { group.rotation.x = Math.PI / 2; group.rotation.y = 0.0; }
{ const b0 = new THREE.Box3().setFromObject(group); group.position.y += 1.2 - b0.min.y; }
// exterior hotspots (shown when the model is assembled and nothing is selected)
const hotspotObjs = (device.hotspots || []).map(([text, pos]) => { const div = document.createElement("div"); div.className = "callout"; div.textContent = text.toUpperCase(); const o = new CSS2DObject(div); o.position.set(...pos); group.add(o); return o; });
function updateHotspots() { for (const o of hotspotObjs) o.element.style.opacity = explode < 0.15 && !selected && !xray ? "0.9" : "0"; }
updatePositions();
const bb = new THREE.Box3().setFromObject(group); const size = bb.getSize(V3()); const c0 = bb.getCenter(V3()); controls.target.copy(c0);
const tweens = []; const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
function tween(from, to, ms, set) { return new Promise((done) => { if (INSTANT) { set(to); done(); return; } tweens.push({ from, to, ms, set, done, t0: performance.now() }); }); }
function setExplode(v) { return tween(explode, v, 1400, (x) => { explode = x; updatePositions(); $("tele").textContent = `${Object.keys(parts).length} PARTS · ${Math.round(explode * 100)}% APART`; }); }
function setXray(on) { xray = on; for (const p of Object.values(parts)) p.obj.traverse((o) => { if (o.isMesh) { if (!o.userData.mat) o.userData.mat = o.material; o.material = on ? M.holo : o.userData.mat; } }); $("mode").textContent = on ? "X-RAY" : explode > 0.5 ? "EXPLODED" : "ASSEMBLED"; }
function flyTo(pos, target, ms = 1100) { const p0 = camera.position.clone(), t0 = controls.target.clone(); return tween(0, 1, ms, (t) => { camera.position.lerpVectors(p0, pos, ease(t)); controls.target.lerpVectors(t0, target, ease(t)); }); }
function frameAll() { const b = new THREE.Box3().setFromObject(group), s = b.getSize(V3()), c = b.getCenter(V3()); const dist = Math.max(s.x, s.y * 1.1, s.z) * 1.25 + 6; return flyTo(c.clone().add(device.upright ? V3(dist * 0.45, dist * 0.22, dist * 0.9) : V3(dist * 0.55, dist * 0.55, dist * 0.8)), c, 1600); }

// labels + inspect
const tags = [];
function clearTags() { for (const t of tags) scene.remove(t); tags.length = 0; }
function tag(p, text, cls = "") { const div = document.createElement("div"); div.className = "tag " + cls; div.textContent = text; const o = new CSS2DObject(div); o.position.copy(p.obj.getWorldPosition(V3())).add(V3(0, 1.4, 0)); scene.add(o); tags.push(o); }
function select(name) {
  selected = name && parts[name] ? name : null; clearTags();
  outline.selectedObjects = selected ? [parts[selected].obj] : [];
  updatePositions();
  if (!selected) { $("insp").style.display = "none"; return; }
  const p = parts[selected]; $("ih").textContent = p.label.toUpperCase(); $("ip").textContent = p.desc; $("ik").textContent = `${device.name} · ${p.group.toUpperCase()}`; $("insp").style.display = "block"; tag(p, p.label.toUpperCase());
}
function focusPart(name) { const p = parts[name]; if (!p) return; const b = new THREE.Box3().setFromObject(p.obj), s = b.getSize(V3()), w = b.getCenter(V3()); const dist = Math.max(s.x, s.y, s.z) * 2.2 + 5; return flyTo(w.clone().add(V3(dist * 0.5, dist * 0.55, dist * 0.7)), w, 900); }
const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
renderer.domElement.addEventListener("click", (e) => {
  const rc = renderer.domElement.getBoundingClientRect(); ptr.set(((e.clientX - rc.left) / rc.width) * 2 - 1, -((e.clientY - rc.top) / rc.height) * 2 + 1); ray.setFromCamera(ptr, camera);
  const hit = ray.intersectObjects(group.children, true).find((h) => h.object.userData.part); select(hit ? hit.object.userData.part : null);
});

// ------------------------------------------------------------------ voice (ElevenLabs through the backend, browser as fallback) + commands
let elevenOK = false, sayAudio = null, speakingUntil = 0, currentSpeech = "";
// Spanner's voice is ElevenLabs (through the server). Wait for the server to say so before the first line is spoken,
// or that line comes out in the browser's own voice.
// A page opened by navigating from another page has had no click yet, so the browser will not play audio: ask for one tap
// (and keep the pending line) rather than speaking in the browser's own voice.
let pendingSay = "";
function needTap(text) {
  pendingSay = text; const w = $("wake"); if (!w) return;
  w.querySelector(".big").textContent = "Tap to turn on Spanner’s voice"; w.querySelector("small").textContent = "Your browser needs one tap before it will play sound.";
  w.style.display = "flex";
  const go = () => { removeEventListener("keydown", go, true); w.style.display = "none"; const t = pendingSay; pendingSay = ""; if (t) narrate(t); };
  w.addEventListener("pointerdown", go, { once: true }); addEventListener("keydown", go, { capture: true, once: true });
}
let healthKnown = false;
const healthP = fetch(API + "/coach/health", { signal: AbortSignal.timeout(4000) }).then((r) => r.json()).then((j) => { elevenOK = !!j.tts; }).catch(() => {}).finally(() => { healthKnown = true; });
function stopSpeaking() { queuedSay = null; reportSpeaking(0); if ("speechSynthesis" in window) speechSynthesis.cancel(); if (sayAudio) { sayAudio.pause(); sayAudio = null; } speakingUntil = 0; }
// ---- smooth feedback: say a line without cutting off the one being spoken; react only to what two frames agree on
let queuedSay = null, queueTimer = 0;
/** Say a line when the current one has finished (only the newest waiting line is kept; too late to be useful after 12 s). */
function narrateQueued(text) {
  if (!text) return;
  if (INSTANT || !voiceOn || performance.now() >= speakingUntil) { narrate(text); return; }
  queuedSay = { text, show, t: performance.now() };
  if (!queueTimer) queueTimer = setInterval(() => {
    if (!queuedSay) { clearInterval(queueTimer); queueTimer = 0; return; }
    if (performance.now() - queuedSay.t > 12000) { queuedSay = null; return; }
    if (performance.now() >= speakingUntil) { const q = queuedSay; queuedSay = null; narrate(q.text); }
  }, 120);
}
/** Run fn once nothing is being said or waiting to be said, after a short natural pause. */
function afterSpeech(fn, pauseMs = 700) {
  const t0 = performance.now();
  const tick = () => {
    if ((performance.now() >= speakingUntil && !queuedSay) || performance.now() - t0 > 9000) setTimeout(fn, pauseMs); else setTimeout(tick, 120);
  };
  tick();
}
let mistakeStreak = 0, doneStreak = 0, mistakeShown = false, advancing = false, cleanFrames = 2;
function narrate(text, tries = 0) {
  if (!voiceOn || INSTANT || !text) return;
  queuedSay = null;
  if (!healthKnown) { healthP.then(() => narrate(text, tries)); return; }
  stopSpeaking(); currentSpeech = text; speakingUntil = performance.now() + 60000;
  caption(text); reportSpeaking(Math.min(45000, 2500 + text.split(/\s+/).length * 450) + 1000);
  if (elevenOK) { const a = new Audio(API + "/coach/say?text=" + encodeURIComponent(text)); sayAudio = a; a.onended = () => { speakingUntil = performance.now() + 300; reportSpeaking(0); };
    // always Spanner's own (ElevenLabs) voice: one retry on a real failure, then silent (the caption still shows the line)
    const retry = () => { if (sayAudio !== a) return; sayAudio = null; if (tries < 1) setTimeout(() => { if (!sayAudio) narrate(text, tries + 1); }, 400); else { speakingUntil = performance.now() + 300; reportSpeaking(0); } };
    a.onerror = retry; setTimeout(() => { if (sayAudio === a && a.paused && a.currentTime === 0) retry(); }, 12000);
    a.play().catch((e) => { if (e && e.name === "NotAllowedError") needTap(text); else retry(); }); return; }
  browserSay(text);
}
function browserSay(text) { if (!("speechSynthesis" in window)) return; const u = new SpeechSynthesisUtterance(text); u.rate = 1.03; u.pitch = 0.92; u.onend = () => { speakingUntil = performance.now() + 300; reportSpeaking(0); }; speechSynthesis.speak(u); }
function heard(text, action) { const h = $("heard"); h.innerHTML = `🎤 <b>“${text}”</b> → ${action}`; h.style.display = "block"; clearTimeout(h._t); h._t = setTimeout(() => (h.style.display = "none"), 3500); }
const PART_WORDS = Object.values(parts).map((p) => [new RegExp(p.label.toLowerCase().replace(/[()+]/g, "").split(/\s+/).filter((w) => w.length > 3).join("|")), p.name]);
async function explodeAndFrame(v) { await setExplode(v); await frameAll(); }
async function command(raw) {
  const t = raw.toLowerCase().trim(); let said = "";
  const say = (s) => { said = s; narrate(s); };
  if (/\b(keyboard|rk ?61|go back|start over|choose another|another device|pick a device|main menu)\b/.test(t)) { heard(raw, "back to the device choice"); location.href = "./"; return; }
  if (/iphone/.test(t) && d !== "iphone15") { location.href = "./device.html?d=iphone15"; return; }
  if (/ipad|tablet/.test(t) && d !== "ipad11") { location.href = "./device.html?d=ipad11"; return; }
  if (/headphone|momentum|sennheiser/.test(t) && d !== "momentum4") { location.href = "./device.html?d=momentum4"; return; }
  if (/\b(plug|fuse|three[- ]?pin|3[- ]?pin)\b/.test(t) && d !== "plug13a") { location.href = "./device.html?d=plug13a"; return; }
  if (/\b(stop|quiet|shut up|stop talking|be quiet|silence|enough)\b/.test(t)) { stopSpeaking(); heard(raw, "ok, quiet"); return; }
  if (/\b(close|hide|dismiss|never ?mind)\b/.test(t)) { select(null); said = "closed"; }
  else if (/take .*apart|explode|open it up|blow it up|exploded|cut it|disassemble/.test(t)) { explodeAndFrame(1); setXray(false); say("Taking it apart."); }
  else if (/put .*(together|back)|assemble|close it up/.test(t)) { explodeAndFrame(0); say("Putting it back together."); }
  else if (/x.?ray|see through|hologram/.test(t)) { setXray(!xray); say(xray ? "X-ray on." : "X-ray off."); }
  else if (/\b(reset|start over)\b/.test(t)) { select(null); setExplode(0); setXray(false); frameAll(); say("Reset."); }
  else if (/zoom in|closer/.test(t)) { flyTo(camera.position.clone().lerp(controls.target, 0.35), controls.target.clone(), 600); said = "zoom in"; }
  else if (/zoom out|further|step back/.test(t)) { flyTo(camera.position.clone().sub(controls.target).multiplyScalar(1.5).add(controls.target), controls.target.clone(), 600); said = "zoom out"; }
  else if (/what('s| is) (this|that|it)/.test(t)) { say(selected ? parts[selected].desc : "Nothing is selected. Say show me the battery."); }
  else if (/show me|look at|focus on|go to|select|where('s| is)/.test(t)) {
    const hit = PART_WORDS.find(([re]) => re.test(t));
    if (hit) { if (explode < 0.5) await setExplode(1); select(hit[1]); await focusPart(hit[1]); say(parts[hit[1]].desc); } else said = "no such part";
  } else if (/tell me about|what is a|what does/.test(t)) { say(device.fact); }
  else { said = "…"; heard(raw, "thinking"); return intentFallback(raw); }
  heard(raw, said);
}
async function intentFallback(raw) {
  try {
    const r = await fetch(API + "/coach/intent", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: raw, current_step: 0 }) });
    const j = r.ok ? await r.json() : { command: "none" };
    const map = { take_apart: "take it apart", reassemble: "put it back", xray: "x-ray", reset: "reset", close: "close", zoom_in: "zoom in", zoom_out: "zoom out", show_part: "show me the " + (j.part || "battery") };
    if (map[j.command]) return command(map[j.command]);
    if (j.command === "ask") { say_(device.fact); return; }
    heard(raw, "not understood");
  } catch { heard(raw, "not understood"); }
}
function say_(s) { narrate(s); }
let rec = null;
function startMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition; if (!SR) { $("listen").textContent = "VOICE NEEDS CHROME / EDGE"; return; }
  micOn = true; $("listen").classList.add("on"); $("listen").textContent = "LISTENING";
  rec = new SR(); rec.continuous = true; rec.interimResults = true; rec.lang = "en-GB";
  let lastActed = { text: "", t: 0 }, recognizing = false, lastEvent = performance.now();
  const QUICK = /^(?:\s*(?:ok(?:ay)?|hey|spanner|please|and|now|just)[,\s]*)*(close|stop|quiet|shut up|stop talking|be quiet|enough|reset|x-?ray|take it apart|put it back|zoom (?:in|out)|keyboard|go back)\b/i;
  const words = (s) => new Set(s.toLowerCase().replace(/[^a-z0-9' ]/g, " ").split(/\s+/).filter((w) => w.length > 2));
  const isEcho = (text) => { if (performance.now() > speakingUntil || !currentSpeech) return false; const a = words(text), b = words(currentSpeech); if (!a.size) return true; let n = 0; for (const w of a) if (b.has(w)) n++; return n / a.size >= 0.6; };
  const STOCK = /what do you want to fix|say take it apart|taking it apart|putting it back|x-ray on|x-ray off|starting over|opening the/i;
  const act = (text) => {
    const now = performance.now(); if (text.trim().toLowerCase() === lastActed.text && now - lastActed.t < 4000) return;
    if (now < speakingUntil + 1500) { const q = text.match(QUICK); if (!q || STOCK.test(text) || isEcho(text) || words(currentSpeech).has(q[1].toLowerCase().split(" ")[0])) return; text = q[0]; }
    else if (STOCK.test(text) || isEcho(text)) { heard(text, "ignored: that was me talking"); return; }
    lastActed = { text: text.trim().toLowerCase(), t: now }; if (now < speakingUntil) stopSpeaking(); $("listen").textContent = "LISTENING · " + text.trim().slice(0, 40); command(text); };
  rec.onstart = () => { recognizing = true; lastEvent = performance.now(); };
  rec.onresult = (e) => { lastEvent = performance.now(); const r = e.results[e.results.length - 1], text = r[0].transcript; if (r.isFinal) act(text); else if (QUICK.test(text)) act(text.match(QUICK)[0]); };
  rec.onend = () => { recognizing = false; if (micOn) setTimeout(() => { try { rec.start(); } catch {} }, 250); };
  rec.onerror = (e) => { if (e.error === "not-allowed") { micOn = false; $("listen").classList.remove("on"); $("listen").textContent = "MIC BLOCKED"; } };
  setInterval(() => { if (micOn && (!recognizing || performance.now() - lastEvent > 20000)) { try { rec.abort(); } catch {} try { rec.start(); } catch {} lastEvent = performance.now(); } }, 5000);
  rec.start();
}


// ------------------------------------------------------------------ the shared session: phone camera + microphone, Claude plans the repair
// Same session as the keyboard page (it is passed on when a device is picked): the phone keeps listening and watching,
// the server transcribes and answers (one Claude turn), this page plays the 3D, shows the step and speaks.
const SESSION = QP.get("s") || (() => { try { return sessionStorage.getItem("spanner-session") || ""; } catch { return ""; } })();
const REMOTE = !!SESSION && !INSTANT;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const jpost = (path, body, ms = 8000) => fetch(API + path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal: AbortSignal.timeout(ms) });
let plan = null, stepIdx = -1, finishedDev = false, linkLive = false, lastDataAt = 0, lastViewTs = 0, lastUtterId = "", lastReplyId = "", lastSignalTs = 0;
let lastStateSent = "", planning = false, clarifying = null, agree = 0, lastDetect = 0, lastDoneStep = -1, lastMistakeAt = 0, stepHoldUntil = 0, runId = 0, lastSpeakReport = 0;

if (REMOTE) jpost("/coach/device/parts", { session: SESSION, device: d, parts: device.parts.map((p) => ({ name: p.name, label: p.label, desc: p.desc, group: p.group })) }).catch(() => {});
function reportSpeaking(ms) {
  if (!REMOTE) return;
  const now = performance.now(); if (ms > 0 && now - lastSpeakReport < 300) return; lastSpeakReport = now;
  jpost("/coach/speaking", { session: SESSION, seconds: ms / 1000 }, 3000).catch(() => {});
}

// ---- Spanner hearing its own voice (the phone's microphone is next to the laptop's speaker)
const tokensDev = (s) => s.toLowerCase().replace(/[^a-z0-9' ]/g, " ").split(/\s+/).filter(Boolean);
/** The longest run of words that appears, in the same order and back to back, in both lines. */
function longestRunDev(a, b) {
  let best = 0; const row = new Array(b.length + 1).fill(0);
  for (let i = 1; i <= a.length; i++) {
    let diag = 0;
    for (let j = 1; j <= b.length; j++) { const up = row[j]; row[j] = a[i - 1] === b[j - 1] ? diag + 1 : 0; if (row[j] > best) best = row[j]; diag = up; }
  }
  return best;
}
// Lines only Spanner says. Not "what's wrong with it", "all done" or "step 2": a person says those too.
const STOCK_DEV = /i'm spanner|scan the code|let me work out|heads up/i;
/** Only counts while Spanner is talking or just after. Then it is an echo if it is one of its stock lines, or (5+ words)
 *  most of the sentence is Spanner's own words back to back. You repeating a step in your own words is not an echo, and a
 *  tap to talk (`r.forced`) skips this check altogether. */
function isEchoDev(text, graceMs = 3500) {
  if (performance.now() > speakingUntil + graceMs || !currentSpeech) return false;
  if (STOCK_DEV.test(text)) return true;
  const a = tokensDev(text); if (!a.length) return true;
  if (a.length < 5) return false;        // "start", "next", "the door popped off": a short line is you
  return longestRunDev(a, tokensDev(currentSpeech)) / a.length >= 0.8;
}

// ---- pairing code (only while no phone is sending pictures)
async function phoneOrigin() {
  if (!/^(localhost|127\.|\[?::1\]?$)/.test(location.hostname)) return location.origin;
  try { const n = await (await fetch(API + "/coach/net", { signal: AbortSignal.timeout(3000) })).json(); if (n.public_url) return n.public_url; if (n.lan_ip) return `${location.protocol}//${n.lan_ip}${location.port ? ":" + location.port : ""}`; } catch { /* older server */ }
  return location.origin;
}
let pairRun = 0;
async function showPair() {
  const run = ++pairRun, el = $("pair"); el.style.display = "block";
  el.innerHTML = `<div class="t">Pair your phone</div><div class="tr">Preparing the code…</div>`;
  const url = `${await phoneOrigin()}/training/viewer/phone.html?s=${SESSION}`;
  if (run !== pairRun || linkLive) return;
  el.innerHTML = `<div class="t">Scan with the Spanner app</div><img src="${API}/coach/qr?url=${encodeURIComponent(url)}" alt="QR code to pair your phone"><div class="tr">Waiting for the first photo from your phone</div>`;
}
function hidePair() { pairRun++; $("pair").style.display = "none"; }

// ---- the repair guide
const esc = (x) => String(x ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pad2 = (n) => String(n).padStart(2, "0");
const two = (t) => { const w = String(t || "").split(/\s+/); const k = Math.ceil(w.length / 2); return w.length < 3 ? [t || ""] : [w.slice(0, k).join(" "), w.slice(k).join(" ")]; };
function caption(text) { $("captText").textContent = text || ""; }
function renderCard() {
  { const m = $("mid"); m.classList.remove("rise-in"); void m.offsetWidth; m.classList.add("rise-in"); }   // the new text fades in
  const n = plan?.steps?.length || 0, s = plan?.steps?.[stepIdx];
  $("tools").innerHTML = ""; $("sd").textContent = ""; $("sm").textContent = "";
  if (!plan) {
    $("sn").textContent = "READY"; $("head").innerHTML = "<span>What needs</span><span>fixing?</span>";
    $("si").textContent = `Just tell me what’s wrong with your ${device.name}.`; $("helper").textContent = device.fact;
    $("ctaLabel").textContent = "Say what’s wrong"; $("foot").textContent = `For example: “${EXAMPLE_FAULT[d] || "the screen is cracked"}”.`; $("sceneTitle").textContent = device.name;
  } else if (finishedDev) {
    $("sn").textContent = "ALL DONE"; $("head").innerHTML = "<span>Repair</span><span>complete.</span>"; $("si").textContent = "Test it before you rely on it.";
    $("helper").textContent = "Say “start over” to run the repair again."; $("ctaLabel").textContent = "Say “start over”"; $("foot").textContent = "The session stays open until you close it."; $("sceneTitle").textContent = `${plan.title || device.name} · done`;
  } else if (!s) {
    $("sn").textContent = `${n} STEPS · ${String(plan.difficulty || "").toUpperCase()}`; $("head").innerHTML = two(plan.title || "Your repair").map((l) => `<span>${esc(l)}</span>`).join("");
    $("si").textContent = plan.diagnosis || plan.fault; $("helper").textContent = `About ${plan.time_min} min.${(plan.safety_notes || [])[0] ? "  ⚠ " + plan.safety_notes[0] : ""}`;
    $("ctaLabel").textContent = "Say “start”"; $("foot").textContent = "The 3D view follows at your pace."; $("sceneTitle").textContent = plan.title || device.name;
  } else {
    $("sn").textContent = `STEP ${pad2(s.index)} OF ${pad2(n)}`; $("head").innerHTML = two(s.title || "").map((l) => `<span>${esc(l)}</span>`).join("");
    $("si").textContent = s.instruction; $("helper").textContent = "";
    $("sd").textContent = s.expected_visual_state ? "✓ Done when: " + s.expected_visual_state : ""; $("sm").textContent = s.common_mistakes?.[0] ? "⚠ " + s.common_mistakes[0] : "";
    $("tools").innerHTML = (plan.tools_needed || []).slice(0, 4).map((t) => `<span class="chip">${esc(t)}</span>`).join("");
    $("ctaLabel").textContent = stepIdx >= n - 1 ? "Say “done”" : "Say “next”"; $("foot").textContent = "“Repeat” · “what could go wrong” · “zoom in”"; $("sceneTitle").textContent = `${plan.title || device.name} · ${s.title || ""}`;
  }
  $("stages").style.gridTemplateColumns = `repeat(${Math.max(1, n)}, minmax(0, 1fr))`;
  $("stages").innerHTML = (plan?.steps || []).map((st, i) => {
    const cls = finishedDev || i < stepIdx ? "done" : i === stepIdx ? "cur" : "";
    return `<button class="stage ${cls}" data-i="${i}" ${cls === "cur" ? 'aria-current="step"' : ""}><span class="sl">${pad2(i + 1)}  ${esc(st.title || "Step " + (i + 1))}</span><span class="ss">${cls === "done" ? "Complete" : cls === "cur" ? "Current step" : "Up next"}</span></button>`;
  }).join("");
}
$("stages").addEventListener("click", (e) => { const b = e.target.closest(".stage"); if (b && plan) goStep(Number(b.dataset.i)); });
$("cta").onclick = () => { if (plan) advance(); };
setInterval(() => {   // the voice pill: who is talking right now
  const sp = performance.now() < speakingUntil, st = sp ? "speaking" : (REMOTE ? linkLive : micOn) ? "listening" : "idle", pill = $("captPill");
  if (pill.dataset.s === st + voiceOn) return; pill.dataset.s = st + voiceOn; pill.className = st;
  $("captState").textContent = sp ? "Spanner is speaking" : st === "listening" ? "Listening to you" : voiceOn ? "Voice on" : "Voice off · tap to wake";
}, 250);
const LINK_TEXT = { idle: "Phone not connected", waiting: "Waiting for your phone…", live: "Connected to your phone", lost: "Phone connection lost · waiting for it to return" };
function setLink(state) { $("link").dataset.state = state; $("linktext").textContent = LINK_TEXT[state]; $("btnPair").style.display = state === "live" ? "none" : ""; }
$("btnPair").onclick = () => { if (REMOTE) showPair(); };
function highlightParts(names) {
  const ok = names.filter((n) => parts[n]); select(ok[0] || null);
  if (ok.length > 1) outline.selectedObjects = ok.map((n) => parts[n].obj);   // a second and third part pulse along with the first
  return ok;
}
async function runStep(s, id) {
  for (const c of s.scene3d || []) {
    if (id !== runId) return;
    if (c.op === "explode") { setXray(false); select(null); await setExplode(c.amount ?? 1); await frameAll(); }
    else if (c.op === "reset") { select(null); setXray(false); await setExplode(0); await frameAll(); }
  }
  if (id !== runId) return;
  const names = (s.scene3d || []).filter((c) => (c.op === "highlight" || c.op === "pulse") && c.part).map((c) => c.part);
  if (!names.length) return;
  if (explode < 0.5 && !["frame", "display", "back_glass", "shell", "headband", "usb_c", "screws"].includes(names[0])) { setXray(false); await setExplode(1); }   // an inside part: open it up so it can be seen
  const ok = highlightParts(names); if (ok[0]) await focusPart(ok[0]);
}
// The repair only moves forward while the camera can actually see the {WHAT}: it is seen when a recent picture shows it.
let lastSeenAt = 0, pendingGo = null, lastSeenWarn = 0;
const seenNow = () => INSTANT || QP.get("nocam") === "1" || performance.now() - lastSeenAt < 8000;   // ?nocam=1: no camera (demos, tests)
function holdUntilSeen(i, note) {
  if (i <= stepIdx || seenNow()) { pendingGo = null; return false; }             // going back, repeating, or it is in view
  pendingGo = { i, note };                                                       // carry on by itself the moment it comes into view
  if (performance.now() - lastSeenWarn > 6000) { lastSeenWarn = performance.now(); narrate(`I can’t see your ${device.name} yet. Point the camera at it and I’ll carry on.`); heard("camera", `waiting to see the ${device.name}`); }
  return true;
}
function noteSeen(c) {
  if (c.object_visible) lastSeenAt = performance.now();
  if (pendingGo && seenNow()) { const g = pendingGo; pendingGo = null; goStep(g.i, g.note); }
}
async function goStep(i, note = "") {
  if (!plan) { narrate("Tell me what’s wrong first, and I’ll plan the repair."); return; }
  if (holdUntilSeen(i, note)) return;
  const id = ++runId; stepIdx = Math.max(0, Math.min(i, plan.steps.length - 1)); finishedDev = false;
  const s = plan.steps[stepIdx]; $("mode").textContent = `STEP ${s.index}/${plan.steps.length}${note}`;
  renderCard(); narrate(`Step ${s.index}. ${s.instruction}`);
  await runStep(s, id);
}
function finishRepair() { finishedDev = true; runId++; select(null); setXray(false); renderCard(); $("mode").textContent = "REPAIR COMPLETE"; narrate("All done. Test it before you rely on it."); }
function advance() { if (finishedDev) return restart(); if (stepIdx < 0) return goStep(0); if (stepIdx >= plan.steps.length - 1) return finishRepair(); return goStep(stepIdx + 1); }
function restart() { pendingGo = null; runId++; stepIdx = -1; finishedDev = false; lastDoneStep = -1; select(null); setXray(false); setExplode(0).then(frameAll); renderCard(); }
function showMistake() {
  const s = plan?.steps[stepIdx]; const m = s?.common_mistakes?.[0];
  if (!m) { narrate("Nothing in particular to watch for here. Take it slowly."); return; }
  narrate("Watch out. People often get this wrong: " + m);
}

// ---- Claude plans the repair from what was said
async function requestRepair(raw) {
  if (planning) return; planning = true; clarifying = null; runId++; closeShop();
  heard(raw, "planning the repair"); $("mode").textContent = "PLANNING THE REPAIR";
  narrate("Okay. Let me work out the fix.");
  try {
    const r = await jpost("/coach/plan/generate", { session: SESSION, complaint: raw, device: d }, 120000);
    if (!r.ok) throw new Error(r.status);
    const j = await r.json();
    if (j.status === "ready") {
      plan = j.plan; stepIdx = -1; finishedDev = false; lastDoneStep = -1; agree = 0; select(null); setXray(false); await setExplode(0); frameAll();
      $("mode").textContent = "PLAN READY"; renderCard(); heard(raw, `${plan.steps.length}-step plan`); narrate((j.say || plan.diagnosis) + " Say start when you’re ready.");
    } else {
      if (j.status === "clarify") clarifying = raw;
      $("mode").textContent = "ASSEMBLED"; heard(raw, j.status); narrate(j.say);
    }
  } catch (e) { $("mode").textContent = "ASSEMBLED"; heard(raw, "couldn’t plan: " + e.message); narrate("Sorry, I couldn’t plan that. Try again."); }
  finally { planning = false; }
}

// ---- what the server decided for something said into the phone: do it
async function onReply(r) {
  if (!r.addressed) { heard(r.text, "not for me"); return; }
  if (!r.forced && isEchoDev(r.text)) { heard(r.text, "ignored: sounded like me"); return; }   // r.forced = they tapped to talk
  if (clarifying && r.action !== "plan_repair" && r.action !== "stop") { const c = clarifying + ". " + r.text; clarifying = null; return requestRepair(c); }
  heard(r.text, r.action === "none" ? "talking" : r.action.replace(/_/g, " "));
  if (r.action !== "stop" && performance.now() < speakingUntil) stopSpeaking();
  switch (r.action) {
    case "pick_device": if (r.device && r.device !== d) { location.href = r.device === "keyboard" ? "./" : `./device.html?d=${r.device}&s=${encodeURIComponent(SESSION)}`; return; } break;
    case "plan_repair": return requestRepair(r.complaint || r.text);
    case "start": return goStep(0);
    case "next": return advance();
    case "back": return goStep(stepIdx - 1);
    case "repeat": return plan?.steps[stepIdx] ? narrate(`Step ${plan.steps[stepIdx].index}. ${plan.steps[stepIdx].instruction}`) : undefined;
    case "stop": stopSpeaking(); heard(r.text, "ok, quiet"); return;
    case "show_part": { const n = Object.keys(parts).find((k) => k === r.part) || (PART_WORDS.find(([re]) => re.test(r.part || ""))?.[1]); if (n) { if (explode < 0.5) await setExplode(1); select(n); await focusPart(n); narrate(parts[n].desc); return; } break; }
    case "take_apart": setXray(false); return explodeAndFrame(1);
    case "reassemble": return explodeAndFrame(0);
    case "xray": return setXray(!xray);
    case "zoom_in": return flyTo(camera.position.clone().lerp(controls.target, 0.35), controls.target.clone(), 600);
    case "zoom_out": return flyTo(camera.position.clone().sub(controls.target).multiplyScalar(1.5).add(controls.target), controls.target.clone(), 600);
    case "show_mistake": return showMistake();
    case "shop_parts": case "shop_keyboards": return openShop(r.text);
    case "specs": case "sources": break;
  }
  if (r.say) narrate(r.say);
}

// ---- what the phone's camera shows (the server checks each picture against the step on screen)
function onCoach(c) {
  noteSeen(c);
  $("tele").textContent = `${Object.keys(parts).length} PARTS · CAMERA ${(c.latency_ms / 1000).toFixed(1)} s`;
  if (!plan || !c.object_visible) { agree = 0; return; }
  if (performance.now() < stepHoldUntil || advancing) return;          // we just moved on (or are about to): this picture is stale
  const cur = stepIdx + 1;
  if (c.mistake && c.confidence >= 0.4) mistakeStreak++; else mistakeStreak = 0;
  if (!c.mistake && mistakeShown && !--cleanFrames) { mistakeShown = false; select(null); }
  if (cur >= 1 && c.mistake && (mistakeStreak >= 2 || c.confidence >= 0.8) && performance.now() - lastMistakeAt > 6000) {
    lastMistakeAt = performance.now(); mistakeShown = true; cleanFrames = 2; narrateQueued("Careful. " + (c.say || c.mistake)); heard("camera", "⚠ " + c.mistake); return;
  }
  if (cur >= 1 && c.step_status === "done" && c.step_index >= cur && c.confidence >= 0.55) doneStreak++; else doneStreak = 0;
  if (cur >= 1 && lastDoneStep !== stepIdx && (doneStreak >= 2 || (doneStreak >= 1 && c.confidence >= 0.85))) {
    lastDoneStep = stepIdx; agree = 0; doneStreak = 0; advancing = true; narrateQueued(c.say || "Nice. On to the next step.");
    afterSpeech(() => {
      advancing = false; stepHoldUntil = performance.now() + 2500;
      if (stepIdx !== cur - 1) return;
      if (stepIdx < plan.steps.length - 1) goStep(stepIdx + 1, " · auto"); else if (!finishedDev) finishRepair();
    });
    return;
  }
  agree = c.step_index === lastDetect ? agree + 1 : 1; lastDetect = c.step_index;
  if (cur >= 1 && c.step_index > cur && agree >= 2 && c.confidence >= 0.6) { stepHoldUntil = performance.now() + 2500; goStep(c.step_index - 1, " · detected"); }
}

// ---- the loop: pictures, speech, signals from the phone; what is on screen back to the server
function reportState() {
  const st = { session: SESSION, screen: !linkLive ? "pairing" : finishedDev ? "done" : stepIdx >= 0 ? "step" : "ready", step: stepIdx + 1, total: plan?.steps?.length || 0, title: plan?.title || "" };
  const key = JSON.stringify(st); if (key === lastStateSent) return; lastStateSent = key;
  jpost("/coach/state", st, 4000).catch(() => { lastStateSent = ""; });
}
const ACKS_DEV = ["Mm-hm.", "Okay.", "Let me see.", "Right."];
async function remoteLoop() {
  showPair();
  for (;;) {
    try {
      const d2 = await (await fetch(API + "/coach/latest?session=" + SESSION, { signal: AbortSignal.timeout(2500) })).json();
      let fresh = false;
      if (d2.view && d2.view.ts !== lastViewTs) { lastViewTs = d2.view.ts; fresh = true; onCoach(d2.view); }
      if (d2.utterance && d2.utterance.id !== lastUtterId) {
        lastUtterId = d2.utterance.id; heard(d2.utterance.text, "…");
        const id = d2.utterance.id;   // a reply usually lands within a second or two: if it hasn't after 0.7 s, say a short "mm-hm"
        if (d2.utterance.text.trim().split(/\s+/).length >= 3 && linkLive && !isEchoDev(d2.utterance.text))
          setTimeout(() => { if (lastReplyId !== id && performance.now() > speakingUntil) narrate(ACKS_DEV[Math.floor(Math.random() * ACKS_DEV.length)]); }, 700);
      }
      if (d2.reply && d2.reply.id !== lastReplyId) { lastReplyId = d2.reply.id; onReply(d2.reply); }
      if (d2.signal && d2.signal.ts !== lastSignalTs) {
        lastSignalTs = d2.signal.ts;
        if (d2.signal.signal === "stop") { stopSpeaking(); heard("✋", "ok, quiet"); }
        else if (d2.signal.signal === "end") { try { sessionStorage.removeItem("spanner-session"); } catch { /* private window */ } location.href = "./"; return; }
      }
      if (fresh) lastDataAt = performance.now();
      const live = !!lastDataAt && performance.now() - lastDataAt < 6000;
      if (live !== linkLive) { linkLive = live; if (live) hidePair(); else if (lastDataAt) showPair(); }
      setLink(live ? "live" : lastDataAt ? "lost" : "waiting");
      reportState();
    } catch { /* keep polling */ }
    await sleep(300);
  }
}

if (QP.get("debug") === "1") window.__spanner = { onCoach, state: () => ({ stepIdx, advancing, speaking: performance.now() < speakingUntil, finishedDev, queued: !!queuedSay, mistakeStreak, lastMistakeAgo: Math.round(performance.now() - lastMistakeAt), hold: Math.round(stepHoldUntil - performance.now()), mistakeShown }), until: () => Math.round(speakingUntil - performance.now()) };   // for tests

// ------------------------------------------------------------------ shopping on request ("order a new battery", "how much is the screen")
// The server (backend/shop.py) works out what is meant from the words and what is on screen, searches only that, and
// answers {kind: "part" | "alternatives" | "clarify"}. Nothing is searched unless it is asked for.
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
const money = (p, c) => (p > 0 ? `${{ GBP: "£", USD: "$", EUR: "€" }[c] || (c ? c + " " : "")}${Number(p).toFixed(2)}` : "see price ↗");
const moneyGbp = (i) => money(i.price, i.currency) + (i.price_gbp && i.currency !== "GBP" ? ` <small style="opacity:.6">≈ £${i.price_gbp.toFixed(2)}</small>` : "");
const shopImg = (u) => (u ? `<img src="${esc(u)}" referrerpolicy="no-referrer" onerror="this.remove()">` : "");
const shopCites = (d) => (d.sources?.length ? `<div class="cites">LINKUP READ ${d.sources_count} PAGES · ${d.sources.slice(0, 8).map((x) => `<a href="${esc(x.url)}" target="_blank">${esc(hostOf(x.url))} ↗</a>`).join("")}</div>` : "");
function verifyBox(v) {
  if (!v || v.status === "researching") return `<div class="verify pending"><div class="vh">LINKUP AGENT · VERIFYING<span class="dots"></span></div><div class="meta">checking fit on 2+ independent sites · safety recalls · price of a new one · repair or replace</div></div>`;
  if (v.status !== "ready" || !v.card) return "";
  const c = v.card, dec = c.decision;
  return `<div class="verify"><div class="vh">✓ VERIFIED BY THE LINKUP AGENT${v.fresh ? "" : " · " + esc(c.as_of).toUpperCase()}</div>
    ${dec ? `<div class="dec">${esc(dec.headline)}</div>` : ""}
    <div class="meta">${c.recall_checked ? (c.safety ? "⚠ " + esc(c.safety.text) : "No safety recalls found in the last 2 years") : ""} · ${c.sources_count} sources read · ${esc(c.as_of)}</div>
    <div class="vrow">${c.options.map((o) => `<div class="vo"><div class="pr">${esc(o.price)}</div><div>${esc(o.part)}</div><div class="rt">${esc(o.retailer)}</div>
      <span class="badge ${o.verified ? "ok" : "no"}">${esc(o.badge).toUpperCase()}</span>
      <div class="ev"><a href="${esc(o.url)}" target="_blank">buy ↗</a></div></div>`).join("")}</div></div>`;
}
let shopReq = 0, verifyTimer = null;
function closeShop() { shopReq++; clearTimeout(verifyTimer); $("shop").style.display = "none"; }
async function openShop(raw, refresh = false) {
  const id = ++shopReq, el = $("shop"); clearTimeout(verifyTimer);
  const head = (t) => `<h3>${t}<button class="small" id="shopx" style="float:right" aria-label="Close">✕</button></h3>`;
  el.style.display = "block"; select(null);
  el.innerHTML = head("SEARCHING THE WEB LIVE") + `<div class="searching">LINKUP · TAVILY · “${esc(raw)}”</div>`;
  $("shopx").onclick = closeShop;
  if (!refresh) narrate("Searching live.");
  let verified = null, spoken = false, polls = 0;
  const pollVerify = async (product, part) => {
    if (id !== shopReq || !part) return;
    try { const r = await fetch(API + "/coach/shop/verified?product=" + encodeURIComponent(product) + "&part=" + encodeURIComponent(part), { signal: AbortSignal.timeout(8000) }); if (r.ok) verified = await r.json(); } catch { /* keep polling */ }
    if (++polls > 45 || id !== shopReq) return;
    const box = el.querySelector("#vbox"); if (box) box.innerHTML = verifyBox(verified);
    if (verified?.status === "ready" && verified.card?.decision && !spoken && box) { spoken = true; narrate(verified.card.decision.headline); }
    if (!verified || verified.status === "researching") verifyTimer = setTimeout(() => pollVerify(product, part), 4000);
  };
  try {
    const r = await jpost("/coach/shop", { query: raw, session: SESSION }, 75000);
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 120)}`);
    const d = await r.json(); if (id !== shopReq) return;
    if (d.kind === "clarify") { closeShop(); narrate(d.say); return; }              // not sure which part: ask, do not guess
    if (d.partial) setTimeout(() => { if (id === shopReq) openShop(raw, true); }, 5000);   // photos / prices still arriving
    const src = `LIVE · LINKUP + TAVILY · ${(d.latency_ms / 1000).toFixed(1)} s${d.cached ? " · CACHED" : ""}`;
    if (d.kind === "alternatives") {
      el.innerHTML = head(`ALTERNATIVES TO YOUR ${esc((d.product || "device").toUpperCase())}`) + `<div class="sub">${src}</div><div class="grid">${d.items.map((k, i) => `
        <div class="it" style="animation-delay:${i * 70}ms">${shopImg(k.image_url)}<div class="nm">${esc(k.name)}</div><div class="pr">${moneyGbp(k)}</div>
          <ul>${(k.pros || []).map((x) => `<li class="p">${esc(x)}</li>`).join("")}${(k.cons || []).map((x) => `<li class="c">${esc(x)}</li>`).join("")}</ul>
          <a href="${esc(k.url)}" target="_blank">${esc(hostOf(k.url))} ↗</a></div>`).join("")}</div>${shopCites(d)}`;
      const c = d.items.filter((k) => k.price_gbp > 0).sort((a, b) => a.price_gbp - b.price_gbp)[0];
      narrate(`I found ${d.items.length} alternatives.${c ? ` The cheapest is the ${c.name} at about £${Math.round(c.price_gbp)}.` : ""}`);
    } else {
      pollVerify(d.product, d.part);
      const fit = d.items.filter((o) => o.compatible);
      el.innerHTML = head(esc(d.part.toUpperCase())) + `<div class="sub">${src}</div><div id="vbox">${verifyBox(verified)}</div><div class="sub">MORE OPTIONS · QUICK SEARCH</div><div class="grid">${d.items.map((o, i) => `
        <div class="it" style="animation-delay:${i * 70}ms">${shopImg(o.image_url)}<div class="nm">${esc(o.name)}</div><div class="pr">${moneyGbp(o)}</div><div class="rt">${esc(o.retailer)}</div>
          <span class="badge ${o.compatible ? "ok" : "no"}">${o.compatible ? "✓ FITS YOUR DEVICE" : "✗ WON'T FIT"}</span><div class="why">${esc(o.compatibility)}</div>
          <a href="${esc(o.url)}" target="_blank">${esc(hostOf(o.url))} ↗</a></div>`).join("")}</div>${shopCites(d)}`;
      if (!refresh) {
        const c = fit.filter((o) => o.price > 0).sort((a, b) => a.price - b.price)[0];
        narrate(`${fit.length} of ${d.items.length} fit.${c ? ` Cheapest that fits: ${money(c.price, c.currency)}, from ${c.retailer}.` : ""} I'm verifying the fit.`);
      }
    }
    $("shopx").onclick = closeShop;
  } catch (e) {
    if (id !== shopReq) return;
    el.innerHTML = head("SEARCH FAILED") + `<div class="searching bad">${esc(e.message)}</div>`; $("shopx").onclick = closeShop; narrate("The live search failed. Ask me again.");
  }
}

// ------------------------------------------------------------------ loop + boot
renderer.setAnimationLoop(() => {
  const now = performance.now();
  for (let i = tweens.length - 1; i >= 0; i--) { const tw = tweens[i]; const t = Math.min(1, (now - tw.t0) / tw.ms); tw.set(tw.from + (tw.to - tw.from) * ease(t)); if (t >= 1) { tweens.splice(i, 1); tw.done(); } }
  for (const r of rings.children) r.rotation.z += r.userData.spin * 0.01;
  controls.update(); if (FX) composer.render(); else renderer.render(scene, camera); labelRenderer.render(scene, camera);
});
renderCard(); setLink(REMOTE ? "waiting" : "idle");
$("tele").textContent = `${Object.keys(parts).length} PARTS · ASSEMBLED`; $("mode").textContent = "ASSEMBLED";
setXray(true); await frameAll(); setXray(false);
if (QP.get("explode") === "1") { await setExplode(1); await frameAll(); }
if (QP.get("xray") === "1") setXray(true);
if (QP.get("select") && parts[QP.get("select")]) { select(QP.get("select")); await focusPart(QP.get("select")); }
if (INSTANT || QP.get("wake") === "0" || REMOTE) {
  if (!REMOTE) $("wake").remove();
  if (REMOTE) { voiceOn = true; $("listen").textContent = "PHONE MIC"; narrate(`${device.name}. What's wrong with it?`); remoteLoop(); }
} else { const w = $("wake"); const wake = () => { w.remove(); voiceOn = true; startMic(); narrate(`${device.name}. ${device.fact} Say take it apart.`); }; w.addEventListener("pointerdown", wake, { once: true }); }
