// ✋ Stop sign: a raised open palm in the camera means "stop talking". MediaPipe hand landmarks, in the browser.
//   import { watchPalm } from "./gesture.js";  watchPalm(videoElement, () => stopTalking());
// Fires once per raised palm (hold ~0.4 s), then waits 3 s before it can fire again.
const CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.18";
const MODEL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";

function openPalm(lm) {
  // landmarks: 0 wrist, 4 thumb tip, 8/12/16/20 finger tips, 6/10/14/18 finger PIP joints (image coords, y down)
  const wrist = lm[0];
  const extended = [[8, 6], [12, 10], [16, 14], [20, 18]].filter(([tip, pip]) => lm[tip].y < lm[pip].y && Math.hypot(lm[tip].x - wrist.x, lm[tip].y - wrist.y) > Math.hypot(lm[pip].x - wrist.x, lm[pip].y - wrist.y) * 1.15);
  const thumbOut = Math.hypot(lm[4].x - lm[17].x, lm[4].y - lm[17].y) > Math.hypot(lm[2].x - lm[17].x, lm[2].y - lm[17].y) * 1.1;
  const upright = lm[9].y < wrist.y;                       // fingers above the wrist: a raised hand, not a hand on the desk
  return extended.length === 4 && thumbOut && upright;
}

export async function watchPalm(video, onStop, { fps = 8, hold = 400, cooldown = 3000, onState } = {}) {
  let vision, landmarker;
  try {
    const mp = await import(`${CDN}/vision_bundle.mjs`);
    vision = await mp.FilesetResolver.forVisionTasks(`${CDN}/wasm`);
    landmarker = await mp.HandLandmarker.createFromOptions(vision, { baseOptions: { modelAssetPath: MODEL, delegate: "GPU" }, runningMode: "VIDEO", numHands: 1 });
  } catch (e) {
    console.warn("palm watch unavailable:", e.message); onState?.("unavailable"); return () => {};
  }
  let since = 0, lastFire = 0, stopped = false;
  const tick = () => {
    if (stopped) return;
    if (video.readyState >= 2 && video.videoWidth) {
      try {
        const res = landmarker.detectForVideo(video, performance.now());
        const lm = res.landmarks && res.landmarks[0];
        const now = performance.now();
        if (lm && openPalm(lm)) {
          if (!since) since = now;
          onState?.("palm");
          if (now - since >= hold && now - lastFire > cooldown) { lastFire = now; since = 0; onStop(); }
        } else { since = 0; onState?.(lm ? "hand" : "none"); }
      } catch { /* a dropped frame */ }
    }
    setTimeout(tick, 1000 / fps);
  };
  tick();
  return () => { stopped = true; };
}
