// Phone flashlight that follows the light: on when the room is very dark, off when it is bright enough again.
//
// Two facts about a phone camera shape this:
//  1. The torch lights the very scene we measure, so once it is on, "the picture is bright" says nothing about the room.
//  2. Auto exposure hides the room: a dark scene (a black keyboard) reads dark even in a bright room, and a dark room is
//     brightened by the camera until it looks almost normal. The absolute brightness of the picture is not the room's light.
// So the governor never trusts a single brightness number. It compares the same scene WITH and WITHOUT the torch:
//   - turning on: the torch is only kept if it makes the picture clearly brighter (a dark room); if it changes almost nothing,
//     the scene is just dark-coloured in a lit room, the torch goes off again and stays off for a minute;
//   - turning off: the torch is switched off briefly to read the room. Bright enough = the room alone reaches most of the
//     brightness the torch gave (or is plainly bright). A cheap relative check starts that look within a second when the picture
//     gets brighter or the camera's exposure time drops, and a look is taken every few seconds otherwise.
//
// LightGovernor is pure logic (brightness 0..1 and a clock in, a torch command out), so it is tested without a phone.
// watchLight wires it to a camera track: torch support check, the brightness sampling and the constraint call.

export const DEFAULTS = {
  dark: 0.17,          // picture darker than this ...
  darkMs: 1500,        // ... for this long: try the torch (a hand passing over the lens must not trigger it)
  light: 0.30,         // the room alone brighter than this (torch off): plainly bright, the torch stays off
  keepRatio: 0.62,     // ... or the room alone reaches this share of the lit brightness: bright enough, stays off
  minGain: 1.35,       // the torch must make the picture at least this much brighter, or it is not a dark room
  coolMs: 60000,       // after a torch that changed nothing: do not try again for this long
  settleMs: 1100,      // after a switch the camera's auto exposure needs this long before the picture means anything
  probeEveryMs: 10000, // while on: how often to look at the room without the torch
  probeMs: 1400,       // how long the torch stays off for a look (the last 500 ms are what is measured)
  rise: 1.3,           // lit picture this many times brighter than right after switching on: the room got lighter, look now
  expDrop: 0.55,       // camera exposure time fell below this share of what it was with the torch: the room got lighter, look now
  jumpMs: 800,
};

export class LightGovernor {
  constructor(opts = {}) {
    this.o = { ...DEFAULTS, ...opts };
    this.reset(0);
    this.room = undefined;
  }

  /** The camera was reopened, or a command failed so far that we start over: torch off, nothing assumed. */
  reset(t = 0) {
    this.torch = false;          // what the governor last commanded
    this.state = "off";          // off | lit | probing
    this.since = t;              // when the current state began
    this.darkSince = null;       // when the picture first became dark (off state)
    this.darkSum = 0; this.darkN = 0;   // brightness during that dark spell: what "before the torch" looked like
    this.dark = null;            // ... its mean when the torch went on
    this.baseline = null;        // brightness a little after the torch came on
    this.baseExp = null;         // exposure time then, when the camera reports one
    this.jumpSince = null;
    this.lastProbe = t;
    this.coolUntil = this.coolUntil || 0;
    this.samples = [];
  }

  /** One brightness reading (0..1) at time t in ms, with the camera's exposure time when it reports one.
   *  Returns "on", "off" or null (nothing to change). */
  tick(luma, t, exposure = null) {
    const o = this.o;
    if (this.state === "off") {
      if (t - this.since < o.settleMs || t < this.coolUntil) return null;
      if (luma < o.dark) {
        if (this.darkSince === null) { this.darkSince = t; this.darkSum = 0; this.darkN = 0; }
        this.darkSum += luma; this.darkN++;
        if (t - this.darkSince >= o.darkMs) { this.dark = this.darkSum / Math.max(1, this.darkN); return this._on(t); }
      } else this.darkSince = null;
      return null;
    }
    if (this.state === "lit") {
      const age = t - this.since;
      if (age < o.settleMs) return null;
      if (this.baseline === null) {
        this.baseline = Math.max(luma, 0.02); this.baseExp = exposure;
        // the torch must clearly light the scene, or it was never a dark room (dark-coloured things in a lit room)
        if (this.dark !== null && this.baseline < Math.max(this.dark, 0.02) * o.minGain) return this._giveUp(t);
        return null;
      }
      if (t - this.lastProbe >= o.probeEveryMs) return this._probe(t);
      const brighter = luma > Math.max(0.3, this.baseline * o.rise);
      const faster = exposure !== null && this.baseExp !== null && exposure < this.baseExp * o.expDrop;
      if (brighter || faster) {
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
    const enough = room >= o.light || (this.baseline !== null && room >= this.baseline * o.keepRatio);
    if (enough) { this.state = "off"; this.since = t; this.darkSince = null; return null; }     // already off: stays off
    return this._on(t);                                                                         // still dark: back on
  }

  _on(t) {
    this.torch = true; this.state = "lit"; this.since = t; this.baseline = null; this.baseExp = null; this.jumpSince = null; this.darkSince = null;
    this.lastProbe = t; return "on";
  }

  _probe(t) {
    this.torch = false; this.state = "probing"; this.since = t; this.samples = []; this.lastProbe = t; this.jumpSince = null; return "off";
  }

  _giveUp(t) {
    this.torch = false; this.state = "off"; this.since = t; this.baseline = null; this.darkSince = null; this.coolUntil = t + this.o.coolMs; return "off";
  }

  /** The camera refused a command: go back to what is really true (torch off after a failed "on"; still on after a failed "off"). */
  failed(cmd, t) {
    if (cmd === "on") this.reset(t);
    else { this.torch = true; this.state = "lit"; this.since = t; this.baseline = null; this.baseExp = null; this.jumpSince = null; this.lastProbe = t; }
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
 *   getExposure(): the camera's exposure time now, when it reports one (optional; null otherwise)
 *   onChange(info): { supported, torch, label }, for the on-screen line
 * Returns { step(t), stop(), governor }; step is what the timer calls (and what tests call).
 */
export function watchLight({ getTrack, sampleLuma, getExposure = () => null, onChange = () => {}, opts = {}, tickMs = 250, now = () => performance.now(), timer = true }) {
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
    let exposure = null; try { const e = getExposure(); exposure = typeof e === "number" && e > 0 ? e : null; } catch { /* not reported */ }
    const cmd = gov.tick(luma, t, exposure);
    if (cmd) { busy = true; try { await setTorch(cmd === "on"); } finally { busy = false; } }
    report();
  }

  const id = timer ? setInterval(step, tickMs) : null;
  return { step, governor: gov, stop() { if (id) clearInterval(id); if (track && supported && gov.torch) track.applyConstraints({ advanced: [{ torch: false }] }).catch(() => {}); } };
}
