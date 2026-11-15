// Run with: node tests/torch.test.mjs   (also run by tests/test_torch.py when node is installed)
import assert from "node:assert/strict";
import { LightGovernor, watchLight } from "../training/viewer/torch.js";

/** A room whose brightness is room(t); the torch adds 0.35 to what the camera sees (clamped to 1). Returns the torch history. */
async function simulate(room, seconds, { startTorch = false } = {}) {
  let t = 0, torch = startTorch;
  const track = { readyState: "live", calls: [], getCapabilities: () => ({ torch: true }),
    async applyConstraints(c) { torch = c.advanced[0].torch; this.calls.push([t, torch]); } };
  const w = watchLight({ getTrack: () => track, sampleLuma: () => Math.min(1, room(t) + (torch ? 0.35 : 0)), now: () => t, timer: false });
  const log = [];
  for (t = 0; t <= seconds * 1000; t += 250) { await w.step(t); log.push([t, torch]); }
  return { log, track, torchAt: (s) => log.find(([t]) => t >= s * 1000)[1], w };
}

// 1. a dark room: the torch comes on after the 1.5 s of darkness, and is still on after several probes
{
  const r = await simulate(() => 0.05, 100);
  assert.equal(r.torchAt(1), false, "not on before 1.5 s of darkness");
  assert.equal(r.torchAt(4), true, "on in a dark room");
  assert.equal(r.torchAt(99), true, "still on after many probes");
  const offs = r.track.calls.filter(([, on]) => !on).length;
  assert.ok(offs >= 2 && offs <= 4, `a probe every 30 s, not more (got ${offs})`);
  const longestOff = Math.max(...r.track.calls.map(([t, on], i, a) => (!on && a[i + 1] ? a[i + 1][0] - t : 0)));
  assert.ok(longestOff <= 2500, `a probe is short (${longestOff} ms)`);
}

// 2. the lights come on at 40 s: the torch goes off within a few seconds and stays off
{
  const r = await simulate((t) => (t < 40000 ? 0.05 : 0.6), 90);
  assert.equal(r.torchAt(38), true);
  assert.equal(r.torchAt(46), false, "off within ~6 s of the lights coming on");
  assert.equal(r.torchAt(89), false, "stays off in a bright room");
  assert.equal(r.track.calls.filter(([t, on]) => on && t > 40000).length, 0, "no flicker back on after the lights came on");
}

// 3. a room at 0.25 (dim but not very dark) never gets the torch; neither does a normal bright room
for (const level of [0.25, 0.5]) {
  const r = await simulate(() => level, 60);
  assert.equal(r.track.calls.length, 0, `room ${level}: no torch`);
}

// 4. a hand over the lens for 0.75 s in a bright room is not "very dark"
{
  const r = await simulate((t) => (t >= 10000 && t < 10750 ? 0.02 : 0.5), 30);
  assert.equal(r.track.calls.length, 0, "a passing hand does not trigger the torch");
}

// 5. hysteresis: lit room at 0.05 that becomes 0.22 (above "dark", below "light"): the torch stays on after the probe
{
  const r = await simulate((t) => (t < 20000 ? 0.05 : 0.22), 100);
  assert.equal(r.torchAt(99), true, "dim room (0.22): torch stays on, no flicker at the edge");
  const flips = r.track.calls.length;
  assert.ok(flips <= 8, `no rapid flipping (${flips} commands)`);
}

// 6. the room gets dark again after being bright: back on
{
  const r = await simulate((t) => (t < 30000 ? 0.6 : 0.04), 60);
  assert.equal(r.torchAt(29), false);
  assert.equal(r.torchAt(40), true, "dark again: on again");
}

// 7. a phone without a torch (iPhone Safari): nothing is attempted and the UI is told it is unsupported
{
  let info = null;
  const track = { readyState: "live", getCapabilities: () => ({}), applyConstraints: () => { throw new Error("must not be called"); } };
  const w = watchLight({ getTrack: () => track, sampleLuma: () => 0.01, onChange: (i) => (info = i), now: () => 0, timer: false });
  for (let t = 0; t < 10000; t += 250) await w.step(t);
  assert.equal(info.supported, false);
  assert.equal(info.label, "");
}

// 8. the camera is reopened (a new track): the governor starts over and drives the new track
{
  let t = 0, cur;
  const make = () => ({ readyState: "live", calls: [], getCapabilities: () => ({ torch: true }), async applyConstraints(c) { this.calls.push(c.advanced[0].torch); } });
  const a = make(), b = make(); cur = a;
  const w = watchLight({ getTrack: () => cur, sampleLuma: () => 0.04, now: () => t, timer: false });
  for (t = 0; t < 4000; t += 250) await w.step(t);
  assert.deepEqual(a.calls, [true]);
  cur = b;
  for (; t < 9000; t += 250) await w.step(t);
  assert.deepEqual(b.calls, [true], "the new track gets its own torch command");
}

// 9. applyConstraints keeps failing: it gives up after three tries instead of hammering the camera
{
  let n = 0, info = null;
  const track = { readyState: "live", getCapabilities: () => ({ torch: true }), applyConstraints: async () => { n++; throw new Error("OverconstrainedError"); } };
  let t = 0;
  const w = watchLight({ getTrack: () => track, sampleLuma: () => 0.02, onChange: (i) => (info = i), opts: { darkMs: 250, settleMs: 0, probeEveryMs: 1e9 }, now: () => t, timer: false });
  for (; t < 60000; t += 250) await w.step(t);
  assert.ok(n >= 3 && n <= 12, `gave up after a few failures (${n} calls)`);
  assert.equal(info.supported, false);
}

// 10. no picture yet (null): nothing happens
{
  const track = { readyState: "live", getCapabilities: () => ({ torch: true }), applyConstraints: () => { throw new Error("no"); } };
  const w = watchLight({ getTrack: () => track, sampleLuma: () => null, now: () => 0, timer: false });
  for (let t = 0; t < 5000; t += 250) await w.step(t);
}
console.log("torch: all 10 scenarios pass");
