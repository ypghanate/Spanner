// Run with: node tests/torch.test.mjs   (also run by tests/test_torch.py when node is installed)
import assert from "node:assert/strict";
import { watchLight } from "../training/viewer/torch.js";

const TORCH = 0.30;   // light the torch adds to the scene

/** A phone camera with AUTO EXPOSURE: it raises its gain (up to 6x) to bring the picture to ~0.42, and never darkens below
 *  gain 1. `refl` is how bright the scene is (a black keyboard is dark, a white desk is bright). exposure = the gain it chose. */
function autoCamera(room, refl, torchOn, t) {
  const raw = refl * (room(t) + (torchOn ? TORCH : 0));
  const gain = Math.min(6, Math.max(1, 0.42 / Math.max(raw, 1e-4)));
  return { luma: Math.min(1, raw * gain), exposure: gain };
}

async function simulate(room, seconds, { refl = 0.5, cover = () => false, opts = {} } = {}) {
  let t = 0, torch = false;
  const track = { readyState: "live", calls: [], getCapabilities: () => ({ torch: true }),
    async applyConstraints(c) { torch = c.advanced[0].torch; this.calls.push([t, torch]); } };
  const cam = () => (cover(t) ? { luma: 0.02, exposure: 6 } : autoCamera(room, refl, torch, t));
  const w = watchLight({ getTrack: () => track, sampleLuma: () => cam().luma, getExposure: () => cam().exposure, opts, now: () => t, timer: false });
  const log = [];
  for (t = 0; t <= seconds * 1000; t += 250) { await w.step(t); log.push([t, torch]); }
  const torchAt = (s) => log.find(([x]) => x >= s * 1000)[1];
  const offAfter = (s0) => { const c = track.calls.find(([x, on]) => x >= s0 * 1000 && !on); return c ? (c[0] / 1000) : null; };
  return { log, track, torchAt, offAfter, w };
}

// 1. a dark room: the torch comes on after 1.5 s of darkness and is still on after many looks at the room
{
  const r = await simulate(() => 0.03, 100);
  assert.equal(r.torchAt(1), false, "not on before 1.5 s of darkness");
  assert.equal(r.torchAt(4), true, "on in a dark room");
  assert.equal(r.torchAt(99), true, "still on after many looks");
  const looks = r.track.calls.filter(([, on]) => !on).length;
  assert.ok(looks >= 6 && looks <= 11, `a look about every 10 s (got ${looks})`);
  const longest = Math.max(...r.track.calls.map(([t, on], i, a) => (!on && a[i + 1] ? a[i + 1][0] - t : 0)));
  assert.ok(longest <= 2500, `a look is short (${longest} ms)`);
}

// 2. THE REPORTED BUG: the light comes back at 40 s while the torch is on: the torch goes off soon and stays off
for (const refl of [0.5, 0.12]) {                    // a bright desk, and a black keyboard (dark even in a bright room)
  const dimRoom = refl === 0.5 ? 0.03 : 0.08;
  const r = await simulate((t) => (t < 40000 ? dimRoom : 0.8), 100, { refl });
  assert.equal(r.torchAt(38), true, `refl ${refl}: on while dark`);
  const off = r.offAfter(40);
  assert.ok(off !== null && off <= 54, `refl ${refl}: off within 14 s of the light coming back (was ${off})`);
  assert.equal(r.torchAt(99), false, `refl ${refl}: stays off in the lit room`);
  assert.equal(r.track.calls.filter(([x, on]) => on && x > (off + 2) * 1000).length, 0, `refl ${refl}: never flickers back on`);
}

// 3. a black keyboard in a bright room never gets the torch at all (auto exposure shows it at a normal brightness)
{
  const r = await simulate(() => 0.8, 60, { refl: 0.12 });
  assert.equal(r.track.calls.length, 0, "no torch for a dark-coloured thing in a lit room");
}

// 4. dark-coloured scene that still reads as dark in a lit room: the torch is tried, changes almost nothing, goes off and
//    is not tried again for a minute
{
  const r = await simulate(() => 1.0, 70, { refl: 0.02 });
  const on = r.track.calls.filter(([, o]) => o).map(([x]) => x / 1000), off = r.track.calls.filter(([, o]) => !o).map(([x]) => x / 1000);
  assert.ok(on.length >= 1 && off.length >= 1 && off[0] - on[0] < 4, `tried and dropped quickly (on ${on}, off ${off})`);
  assert.equal(on.filter((x) => x > off[0] && x < off[0] + 58).length, 0, "not tried again within the cool-down");
}

// 5. the room goes dark again after being bright: the torch comes back
{
  const r = await simulate((t) => (t < 30000 ? 0.8 : 0.03), 60);
  assert.equal(r.torchAt(29), false);
  assert.equal(r.torchAt(40), true, "dark again: on again");
}

// 6. a hand over the lens for 0.75 s in a bright room is not "very dark"
{
  const r = await simulate(() => 0.8, 30, { cover: (t) => t >= 10000 && t < 10750 });
  assert.equal(r.track.calls.length, 0, "a passing hand does not trigger the torch");
}

// 7. a covered lens in the dark room, then uncovered into the light: off soon after
{
  const r = await simulate((t) => (t < 20000 ? 0.03 : 0.8), 60, { cover: () => false });
  assert.ok(r.offAfter(20) !== null && r.offAfter(20) <= 34, "off within 14 s");
}

// 8. a dim room that is not very dark (picture ~0.25) never gets the torch
{
  const r = await simulate(() => 0.25, 60, { refl: 0.5, opts: { dark: 0.17 } });
  assert.equal(r.track.calls.length, 0, "no torch in a dim, not dark, room");
}

// 9. the camera's exposure time dropping (when it reports one) starts the look at once, not at the next scheduled one
{
  const r = await simulate((t) => (t < 12500 ? 0.03 : 0.8), 40, { opts: { probeEveryMs: 1e9 } });
  const off = r.offAfter(12.5);
  assert.ok(off !== null && off <= 16, `exposure drop triggers a look within ~3 s (was ${off})`);
}

// 10. a phone without a torch (iPhone Safari): nothing is attempted and the UI is told it is unsupported
{
  let info = null;
  const track = { readyState: "live", getCapabilities: () => ({}), applyConstraints: () => { throw new Error("must not be called"); } };
  const w = watchLight({ getTrack: () => track, sampleLuma: () => 0.01, onChange: (i) => (info = i), now: () => 0, timer: false });
  for (let t = 0; t < 10000; t += 250) await w.step(t);
  assert.equal(info.supported, false);
  assert.equal(info.label, "");
}

// 11. the camera is reopened (a new track): the governor starts over and drives the new track
{
  let t = 0, cur;
  const make = () => ({ readyState: "live", calls: [], getCapabilities: () => ({ torch: true }), async applyConstraints(c) { this.calls.push(c.advanced[0].torch); } });
  const a = make(), b = make(); cur = a;
  let torchOn = false; const sample = () => (torchOn ? 0.4 : 0.04);
  a.applyConstraints = async function (c) { this.calls.push(c.advanced[0].torch); torchOn = c.advanced[0].torch; };
  b.applyConstraints = async function (c) { this.calls.push(c.advanced[0].torch); torchOn = c.advanced[0].torch; };
  const w = watchLight({ getTrack: () => cur, sampleLuma: sample, now: () => t, timer: false });
  for (t = 0; t < 4000; t += 250) await w.step(t);
  assert.deepEqual(a.calls, [true]);
  cur = b; torchOn = false;
  for (; t < 9000; t += 250) await w.step(t);
  assert.deepEqual(b.calls, [true], "the new track gets its own torch command");
}

// 12. applyConstraints keeps failing: it gives up after three tries instead of hammering the camera
{
  let n = 0, info = null;
  const track = { readyState: "live", getCapabilities: () => ({ torch: true }), applyConstraints: async () => { n++; throw new Error("OverconstrainedError"); } };
  let t = 0;
  const w = watchLight({ getTrack: () => track, sampleLuma: () => 0.02, onChange: (i) => (info = i), opts: { darkMs: 250, settleMs: 0, probeEveryMs: 1e9 }, now: () => t, timer: false });
  for (; t < 60000; t += 250) await w.step(t);
  assert.ok(n >= 3 && n <= 12, `gave up after a few failures (${n} calls)`);
  assert.equal(info.supported, false);
}

// 13. no picture yet (null): nothing happens
{
  const track = { readyState: "live", getCapabilities: () => ({ torch: true }), applyConstraints: () => { throw new Error("no"); } };
  const w = watchLight({ getTrack: () => track, sampleLuma: () => null, now: () => 0, timer: false });
  for (let t = 0; t < 5000; t += 250) await w.step(t);
}
console.log("torch: all 13 scenarios pass");
