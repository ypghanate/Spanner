// Phone flashlight that follows the light: on when the room is very dark, off when it is bright enough again.
//
// The hard part is that the torch lights the very scene we measure: once it is on, the picture is bright whatever the room
// is like, so "it is bright now" cannot mean "turn it off". The governor therefore measures the room itself: while the torch
// is on it switches it off for a moment now and then (and at once when the picture suddenly gets much brighter than it was
// with the torch alone), reads the brightness without it, and only keeps it off if the room is bright enough by itself.
//
// LightGovernor is pure logic (brightness 0..1 and a clock in, a torch command out), so it is tested without a phone.
// watchLight wires it to a camera track: torch support check, the brightness sampling and the constraint call.

export const DEFAULTS = {
  dark: 0.17,          // room darker than this (mean brightness 0..1 of the camera picture) ...
  darkMs: 1500,        // ... for this long: torch on (a hand passing over the lens must not trigger it)
  light: 0.30,         // room brighter than this with the torch off: it stays off (higher than `dark`: no flicker at the edge)
  settleMs: 1200,      // after a switch the camera's auto exposure needs this long before the picture means anything
  probeEveryMs: 30000, // while on: how often to look at the room without the torch
  probeMs: 1500,       // how long the torch stays off for a probe (the last part of it is what is measured)
  jump: 1.6,           // lit picture this many times brighter than right after switching on: a room light came on, probe now
  jumpMs: 1000,
};

export class LightGovernor {
  constructor(opts = {}) {
    this.o = { ...DEFAULTS, ...opts };
    this.torch = false;          // what the governor last commanded
    this.state = "off";          // off | lit | probing
    this.since = 0;              // when the current state began
    this.darkSince = null;       // when the picture first became dark (off state)
    this.baseline = null;        // brightness a little after the torch came on
    this.jumpSince = null;
    this.lastProbe = 0;
    this.samples = [];
  }

  /** One brightness reading (0..1) at time t in ms. Returns "on", "off" or null (nothing to change). */
  tick(luma, t) {
    const o = this.o;
    if (this.state === "off") {
      if (t - this.since < o.settleMs) return null;
      if (luma < o.dark) {
        this.darkSince ??= t;
        if (t - this.darkSince >= o.darkMs) return this._on(t);
      } else this.darkSince = null;
      return null;
    }
    if (this.state === "lit") {
      const age = t - this.since;
      if (age < o.settleMs) return null;
      if (this.baseline === null) { this.baseline = Math.max(luma, 0.02); return null; }
      if (t - this.lastProbe >= o.probeEveryMs) return this._probe(t);
      if (luma > Math.max(0.5, this.baseline * o.jump)) {
        this.jumpSince ??= t;
        if (t - this.jumpSince >= o.jumpMs) return this._probe(t);
      } else this.jumpSince = null;
      return null;
    }
    // probing: the torch is off; read the room once the exposure has settled
    const age = t - this.since;
    if (age >= o.probeMs - 500) this.samples.push(luma);
    if (age < o.probeMs) return null;
    const room = this.samples.length ? this.samples.reduce((a, b) => a + b, 0) / this.samples.length : 0;
    this.room = room;
    if (room >= o.light) { this.state = "off"; this.since = t; this.darkSince = null; return null; }   // already off
    return this._on(t);                                                                                // still dark: back on
  }

  _on(t) {
    this.torch = true; this.state = "lit"; this.since = t; this.baseline = null; this.jumpSince = null; this.darkSince = null;
    this.lastProbe = t; return "on";
  }

  _probe(t) {
    this.torch = false; this.state = "probing"; this.since = t; this.samples = []; this.lastProbe = t; return "off";
  }

  /** The camera refused a command: go back to what is really true (torch off after a failed "on"; still on after a failed "off"). */
  failed(cmd, t) {
    if (cmd === "on") this.reset(t);
    else { this.torch = true; this.state = "lit"; this.since = t; this.baseline = null; this.jumpSince = null; this.lastProbe = t; }
  }

  /** The camera was reopened (torch is off again on a new track): start from the beginning. */
  reset(t = 0) {
    this.torch = false; this.state = "off"; this.since = t; this.darkSince = null; this.baseline = null; this.jumpSince = null; this.samples = [];
  }

  /** What to show the person. */
  label() {
    if (this.state === "probing") return "checking the light";
    return this.torch ? "flashlight on · it is dark" : "";
  }
}

/** Mean brightness 0..1 of the video, from a tiny copy of the picture. */
export function videoLuma(video, canvas) {
  const w = 32, h = 18;
  canvas.width = w; canvas.height = h;
  const g = canvas.getContext("2d", { willReadFrequently: true });
  g.drawImage(video, 0, 0, w, h);
  const d = g.getImageData(0, 0, w, h).data;
  let s = 0;
  for (let i = 0; i < d.length; i += 4) s += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
  return s / (w * h) / 255;
}

/**
 * Keep the torch of the video's current camera track in step with the room's light.
 *   getTrack():  the live video track now (it changes when the camera is reopened)
 *   sampleLuma(): brightness 0..1 of the picture now, or null when there is no picture yet
 *   onChange(info): { supported, torch, label }, for the on-screen line
 * Returns { step(t), stop(), governor }; step is what the timer calls (and what tests call).
 */
export function watchLight({ getTrack, sampleLuma, onChange = () => {}, opts = {}, tickMs = 250, now = () => performance.now(), timer = true }) {
  const gov = new LightGovernor(opts);
  let track = null, supported = false, failures = 0, last = "", busy = false;

  function report(extra = {}) {
    const info = { supported, torch: gov.torch, label: supported ? gov.label() : "", ...extra };
    const key = JSON.stringify(info);
    if (key !== last) { last = key; onChange(info); }
  }

  async function setTorch(on) {
    try {
      await track.applyConstraints({ advanced: [{ torch: on }] });
      failures = 0;
    } catch (e) {
      gov.failed(on ? "on" : "off", now());               // what is true now: it will try again later
      if (++failures >= 3) { supported = false; gov.reset(now()); report({ error: String(e && e.message || e) }); }
    }
  }

  async function step(t = now()) {
    const cur = getTrack();
    if (cur !== track) {                                  // first track, or the camera was reopened
      track = cur; failures = 0;
      const caps = track && track.getCapabilities ? track.getCapabilities() : {};
      supported = !!(caps && caps.torch);
      gov.reset(t); report();
    }
    if (!supported || !track || track.readyState === "ended" || busy) return;
    const luma = sampleLuma();
    if (luma === null || luma === undefined || Number.isNaN(luma)) return;
    const cmd = gov.tick(luma, t);
    if (cmd) { busy = true; try { await setTorch(cmd === "on"); } finally { busy = false; } }
    report();
  }

  const id = timer ? setInterval(step, tickMs) : null;
  return { step, governor: gov, stop() { if (id) clearInterval(id); if (track && supported && gov.torch) track.applyConstraints({ advanced: [{ torch: false }] }).catch(() => {}); } };
}
