// SPANNER // device holograms: iPhone 15, iPad (11th gen), Sennheiser Momentum 4.
// Procedural, decomposable models in the same look as the RK61 hologram: take apart, inspect every part,
// x-ray, voice. No repair procedure for these (the keyboard is the repair demo).
//   device.html?d=iphone15 | ipad11 | momentum4   (&instant=1 for screenshots, &explode=1, &xray=1, &select=battery)
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
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x05080f); scene.fog = new THREE.FogExp2(0x05080f, 0.011);
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
const outline = new OutlinePass(new THREE.Vector2(innerWidth, innerHeight), scene, camera); Object.assign(outline, { edgeStrength: 4, edgeGlow: 0.5, edgeThickness: 1.3, pulsePeriod: 0 });
outline.visibleEdgeColor.set(CYAN); outline.hiddenEdgeColor.set(0x0a5566); composer.addPass(outline);
composer.addPass(new ShaderPass({ uniforms: { tDiffuse: { value: null } }, vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
  fragmentShader: "uniform sampler2D tDiffuse; varying vec2 vUv; void main(){ vec4 c = texture2D(tDiffuse, vUv); if (c.r != c.r || c.g != c.g || c.b != c.b || c.a != c.a) c = vec4(0.0,0.0,0.0,1.0); gl_FragColor = vec4(min(c.rgb, vec3(16.0)), c.a); }" }));
composer.addPass(new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.35, 0.4, 1.6)); composer.addPass(new OutputPass());
const labelRenderer = new CSS2DRenderer({ element: $("labels") });
function resize() { renderer.setSize(innerWidth, innerHeight); composer.setSize(innerWidth, innerHeight); labelRenderer.setSize(innerWidth, innerHeight); outline.resolution.set(innerWidth, innerHeight); camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); }
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
function ipad11() {
  const W = 17.95, H = 24.86, T = 0.7, parts = [];
  const add = (name, label, desc, obj, pos, dir, group = "body") => parts.push({ name, label, desc, obj, pos, dir, group });
  add("shell", "Aluminium back shell", "One-piece aluminium housing; every component screws or glues into it.", mesh(box(W, T, H, 0.8, 4), M.alu), [0, 0, 0], [0, 0, 0]);
  add("display", "Display assembly", "11-inch Liquid Retina LCD laminated to the cover glass; held by adhesive all around.", (() => { const g = new THREE.Group(); g.add(mesh(box(W - 0.4, 0.15, H - 0.4, 0.6, 4), M.oled)); const gl = mesh(box(W - 0.25, 0.08, H - 0.25, 0.7, 4), M.screen); gl.position.y = 0.12; g.add(gl); return g; })(), [0, T / 2 + 0.07, 0], [0, 1, 0], "front");
  add("battery_left", "Battery cell (left)", "One of two lithium-polymer cells, about 28.9 Wh together; glued with stretch-release strips.", mesh(box(6.4, 0.35, 15.5, 0.3), M.battery), [-4.6, 0, 1.2], [-0.5, 1, 0], "inside");
  add("battery_right", "Battery cell (right)", "The second cell, mirror of the left, joined by a flex to the board.", mesh(box(6.4, 0.35, 15.5, 0.3), M.battery), [4.6, 0, 1.2], [0.5, 1, 0], "inside");
  add("logic_board", "Logic board", "A16 chip, memory and storage on a narrow board between the cells, shielded.", (() => { const g = new THREE.Group(); g.add(mesh(box(3.2, 0.2, 14, 0.08), M.pcb)); for (const z of [-4.5, -1, 2.5]) { const s = mesh(box(2.8, 0.22, 2.6, 0.06), M.shield); s.position.set(0, 0.2, z); g.add(s); } return g; })(), [0, 0, 0.8], [0, 1, 0], "inside");
  add("speakers", "Stereo speakers", "Four speaker boxes, two per short edge, for landscape stereo.", (() => { const g = new THREE.Group(); for (const [x, z] of [[-6.5, -11], [6.5, -11], [-6.5, 11], [6.5, 11]]) { const s = mesh(box(3.0, 0.4, 1.2, 0.2), M.rubber); s.position.set(x, 0, z); g.add(s); } return g; })(), [0, 0, 0], [0, 1, 0], "inside");
  add("camera", "Rear camera", "12 MP wide camera in the corner.", (() => { const g = new THREE.Group(); const l = mesh(cyl(0.7, 0.4), M.lens); g.add(l); const r = mesh(cyl(0.9, 0.3), M.alu); r.position.y = -0.1; g.add(r); return g; })(), [-7.2, -T / 2 - 0.1, -10.6], [-0.3, -1, -0.3], "back");
  add("front_camera", "Front camera", "12 MP landscape front camera, centred on the long edge for video calls.", mesh(box(1.2, 0.2, 0.6, 0.1), M.black), [W / 2 - 1.2, T / 2 - 0.1, 0], [0.8, 1, 0], "front");
  add("usb_c", "USB-C port", "Charging and data port on the bottom edge.", mesh(box(0.9, 0.32, 0.8, 0.12), M.silver), [0, 0, H / 2 - 0.3], [0, 1, 1], "inside");
  add("smart_connector", "Smart Connector", "Three pogo pins on the edge for the keyboard case.", mesh(box(2.4, 0.1, 0.3, 0.05), M.gold), [W / 2 - 0.1, 0, 0], [1, 0, 0], "body");
  add("buttons", "Top button + volume", "Power button with Touch ID, and the volume buttons.", (() => { const g = new THREE.Group(); const p = mesh(box(1.4, 0.25, 0.3, 0.08), M.alu); p.position.set(-6.5, 0, -H / 2 - 0.12); g.add(p); for (const z of [-10.0, -8.6]) { const b = mesh(box(0.25, 0.25, 1.0, 0.08), M.alu); b.position.set(-W / 2 - 0.12, 0, z); g.add(b); } return g; })(), [0, 0, 0], [-1, 0, -1], "body");
  return { name: "iPad (11th gen)", parts, spread: 7, upright: true, views: { iso: [18, 24, 30] }, fact: "A16 chip, 11-inch Liquid Retina, landscape front camera, USB-C. Batteries and display are glued: a heat mat and patience." };
}
function momentum4() {
  const parts = [];
  const add = (name, label, desc, obj, pos, dir, group = "body") => parts.push({ name, label, desc, obj, pos, dir, group });
  const band = new THREE.Group();
  const curve = new THREE.QuadraticBezierCurve3(V3(-9, 4, 0), V3(0, 12.5, 0), V3(9, 4, 0));
  band.add(mesh(new THREE.TubeGeometry(curve, 48, 0.75, 16, false), M.fabric));
  const pad = mesh(new THREE.TubeGeometry(new THREE.QuadraticBezierCurve3(V3(-4, 8.6, 0), V3(0, 10.9, 0), V3(4, 8.6, 0)), 24, 0.95, 16, false), M.leather); band.add(pad);
  add("headband", "Headband", "Fabric-wrapped sprung steel band with the padded crown; adjusts by sliding the yokes.", band, [0, 0, 0], [0, 1, 0]);
  for (const side of [-1, 1]) {
    const s = side < 0 ? "left" : "right", L = side < 0 ? "Left" : "Right";
    add(`yoke_${s}`, `${L} yoke`, "Slider and hinge that holds the ear cup and lets it fold flat.", (() => { const g = new THREE.Group(); const y = mesh(box(0.7, 4.2, 0.7, 0.3), M.alu); y.position.y = 2.2; g.add(y); const h = mesh(cyl(0.6, 1.2), M.black); h.rotation.z = Math.PI / 2; h.position.y = 0.2; g.add(h); return g; })(), [side * 9.2, 0.6, 0], [side * 1, 0.6, 0]);
    add(`cup_${s}`, `${L} ear cup`, "Plastic shell with the touch surface on the right cup; the electronics live inside.", mesh(box(3.0, 8.4, 7.2, 1.2, 6), M.black), [side * 9.4, -3.2, 0], [side * 1, 0, 0], "cups");
    add(`cushion_${s}`, `${L} ear cushion`, "Memory-foam pad with a protein-leather cover; clips on and off, the most replaced part.", (() => { const g = new THREE.Group(); const t = new THREE.Mesh(new THREE.TorusGeometry(3.2, 1.0, 20, 48), M.leather); t.rotation.y = Math.PI / 2; g.add(t); return g; })(), [side * 7.4, -3.2, 0], [side * -1, 0, 0], "cups");
    add(`driver_${s}`, `${L} driver`, "42 mm dynamic driver, angled toward the ear.", (() => { const g = new THREE.Group(); const d = mesh(cyl(2.1, 0.5), M.silver); d.rotation.z = Math.PI / 2; g.add(d); const c = mesh(cyl(1.0, 0.6), M.copper); c.rotation.z = Math.PI / 2; c.position.x = side * -0.3; g.add(c); return g; })(), [side * 8.2, -3.2, 0], [side * -1, 0.3, 0], "inside");
  }
  add("battery", "Battery", "700 mAh pack in the right cup: about 60 hours with noise cancelling on.", mesh(box(1.2, 4.2, 2.6, 0.3), M.battery), [10.2, -2.6, 1.6], [1, 0, 0.4], "inside");
  add("pcb", "Main board", "Bluetooth 5.2, DSP and the touch controller, under the right cup's shell.", mesh(box(0.5, 5.0, 4.4, 0.1), M.pcb), [10.5, -3.6, -1.0], [1, 0, -0.5], "inside");
  add("anc_mics", "Noise-cancelling mics", "Four microphones per cup for adaptive noise cancelling and calls.", (() => { const g = new THREE.Group(); for (const [y, z] of [[-1, 2.6], [-5.4, -2.4]]) { const m = mesh(cyl(0.28, 0.2), M.silver); m.rotation.z = Math.PI / 2; m.position.set(0, y, z); g.add(m); } return g; })(), [-11.0, 0, 0], [-1, 0, 0], "cups");
  add("usb_c", "USB-C + button", "Charging port and the power/pairing button on the right cup's bottom edge.", mesh(box(0.8, 0.3, 0.9, 0.1), M.silver), [9.4, -7.5, 1.6], [0, -1, 0.3], "body");
  return { name: "Sennheiser Momentum 4", parts, spread: 5, views: { iso: [18, 14, 26] }, fact: "60-hour battery, adaptive noise cancelling, fabric headband. The ear cushions clip off and are the usual wear part." };
}
const DEVICES = { iphone15: iphone15, ipad11: ipad11, momentum4: momentum4 };
const d = (QP.get("d") || "iphone15").toLowerCase();
const device = (DEVICES[d] || iphone15)();
$("dev").textContent = device.name.toUpperCase(); document.title = `Spanner · ${device.name}`;

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
  ptr.set((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1); ray.setFromCamera(ptr, camera);
  const hit = ray.intersectObjects(group.children, true).find((h) => h.object.userData.part); select(hit ? hit.object.userData.part : null);
});

// ------------------------------------------------------------------ voice (ElevenLabs through the backend, browser as fallback) + commands
let elevenOK = false, sayAudio = null, speakingUntil = 0, currentSpeech = "";
fetch(API + "/coach/health").then((r) => r.json()).then((j) => { elevenOK = !!j.tts; }).catch(() => {});
function stopSpeaking() { if ("speechSynthesis" in window) speechSynthesis.cancel(); if (sayAudio) { sayAudio.pause(); sayAudio = null; } speakingUntil = 0; }
function narrate(text) {
  if (!voiceOn || INSTANT || !text) return; stopSpeaking(); currentSpeech = text; speakingUntil = performance.now() + 60000;
  if (elevenOK) { const a = new Audio(API + "/coach/say?text=" + encodeURIComponent(text)); sayAudio = a; a.onended = () => { speakingUntil = performance.now() + 300; }; a.play().catch(() => browserSay(text)); return; }
  browserSay(text);
}
function browserSay(text) { if (!("speechSynthesis" in window)) return; const u = new SpeechSynthesisUtterance(text); u.rate = 1.03; u.pitch = 0.92; u.onend = () => { speakingUntil = performance.now() + 300; }; speechSynthesis.speak(u); }
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
  if (/\b(close|hide|dismiss|that's enough|never ?mind)\b/.test(t)) { select(null); said = "closed"; }
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
  const QUICK = /^(?:\s*(?:ok(?:ay)?|hey|spanner|please|and|now|just)[,\s]*)*(close|stop|reset|x-?ray|take it apart|put it back|zoom (?:in|out)|keyboard|go back)\b/i;
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

// ------------------------------------------------------------------ loop + boot
renderer.setAnimationLoop(() => {
  const now = performance.now();
  for (let i = tweens.length - 1; i >= 0; i--) { const tw = tweens[i]; const t = Math.min(1, (now - tw.t0) / tw.ms); tw.set(tw.from + (tw.to - tw.from) * ease(t)); if (t >= 1) { tweens.splice(i, 1); tw.done(); } }
  for (const r of rings.children) r.rotation.z += r.userData.spin * 0.01;
  controls.update(); if (FX) composer.render(); else renderer.render(scene, camera); labelRenderer.render(scene, camera);
});
$("nb").textContent = device.name.toUpperCase(); $("nt").textContent = device.fact + " Say “take it apart”, “show me the battery”, “x-ray”, or “keyboard” to go back to the repair.";
$("tele").textContent = `${Object.keys(parts).length} PARTS · ASSEMBLED`; $("mode").textContent = "ASSEMBLED";
setXray(true); await frameAll(); setXray(false);
if (QP.get("explode") === "1") { await setExplode(1); await frameAll(); }
if (QP.get("xray") === "1") setXray(true);
if (QP.get("select") && parts[QP.get("select")]) { select(QP.get("select")); await focusPart(QP.get("select")); }
if (INSTANT || QP.get("wake") === "0") $("wake").remove();
else { const w = $("wake"); const wake = () => { w.remove(); voiceOn = true; startMic(); narrate(`${device.name}. ${device.fact} Say take it apart.`); }; w.addEventListener("pointerdown", wake, { once: true }); }
