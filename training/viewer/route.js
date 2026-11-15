// Where a spoken request goes. Three separate worlds, never mixed:
//   keyboard      -> the keyboard pipeline (hand-built hologram, camera reads the keycaps). Untouched: no object questions.
//   iphone15 / ipad11 / momentum4 / plug13a -> their own device pages (hologram teardown).
//   anything else -> object mode: live photo search, fal 3D model, diagnostic questions, repair learned from the web.
// Pure functions (no DOM), so every decision is tested in tests/route.test.mjs.

// "keyboard", "RK68", "my J key", "keycap", "space bar": one whole word, never a piece of another one ("monkey", "turkey").
export const KEYBOARD_WORDS = /\bkey ?boards?\b|\brk ?\d+\b|\bkey ?caps?\b|\bspace ?bar\b|\bkeys?\b/i;

/** The built-in device a request names, or null when it is something else (which Spanner then learns live).
 *  Headphones in general are NOT the Momentum 4: only the word "Momentum" is. A Sony or Bose headset gets its own 3D model. */
export function deviceFor(text) {
  const t = String(text || "").toLowerCase();
  if (KEYBOARD_WORDS.test(t)) return "keyboard";
  // the three-pin plug and its fuse (a built-in device). "plug" alone counts only when no model number is named, so
  // "Dyson V8, the plug is loose" stays a Dyson.
  if (/\b(?:three[- ]?pin|3[- ]?pin|13 ?a(?:mp)?|mains|fused?)\b/.test(t) || (/\bplug\b/.test(t) && !/\d/.test(t))) return "plug13a";
  if (/\bi ?phones?\b/.test(t)) return "iphone15";
  if (/\bmomentum\b/.test(t)) return "momentum4";
  if (/\bi ?pads?\b/.test(t)) return "ipad11";
  return null;
}

/** What to do with a "plan a repair" request.
 *   "answer"   an answer to Spanner's own question about an object it is learning
 *   "keyboard" the keyboard pipeline
 *   "object"   start (or restart) live object mode
 *   device id  open that built-in device
 *  On the first screen the words decide. Once the keyboard is chosen, every fault stays in the keyboard pipeline, whatever
 *  words it uses ("the switch is sticky", "the backlight is dead"): it never turns into an object interview. */
export function routePlanRepair({ text, pickOpen, objectOn, asking }) {
  if (asking) return "answer";
  if (pickOpen) return deviceFor(text) || "object";
  if (objectOn) return KEYBOARD_WORDS.test(String(text || "")) ? "keyboard" : "object";
  return "keyboard";
}
