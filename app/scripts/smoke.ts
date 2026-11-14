/**
 * Headless check of the app's backend client (src/lib/live.ts) against a running backend.
 *   USE_MOCKS=1 backend up, then:  npm run smoke
 * Sends fixture frames and checks the mock repair walks identifying -> ... -> guiding steps -> done.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createSession, endSession, LiveSocket } from "../src/lib/live";
import type { Phase } from "../types/contracts";

const frame = readFileSync(resolve(__dirname, "../../fixtures/frames/placeholder.jpg")).toString("base64");
const seen: Phase[] = [];
let frames = 0, boxes = 0, says = 0, steps = new Set<number>();

async function main() {
  const id = await createSession();
  console.log("session", id);
  const done = new Promise<void>((ok) => {
    const sock = new LiveSocket(id, {
      onOpen: () => {
        sock.send({ type: "start", tts: "url" });
        const t = setInterval(() => {
          sock.send({ type: "frame", jpeg_b64: frame, ts: Date.now() / 1000 });
          if (seen.includes("done")) { clearInterval(t); sock.close(); ok(); }
        }, 400);
        setTimeout(() => { clearInterval(t); sock.close(); ok(); }, 60000);
      },
      onClose: () => {},
      onFrameResult: (r) => { frames++; boxes += r.detections.length; },
      onEvent: (r) => {
        if (seen[seen.length - 1] !== r.phase) { seen.push(r.phase); console.log("phase ->", r.phase); }
        if (r.say) says++;
        if (r.current_step != null) steps.add(r.current_step);
      },
      onResponse: () => {},
      onPartial: () => {},
      onFinal: () => {},
      onError: (c, m) => console.log("server error", c, m),
    });
    sock.connect();
  });
  await done;
  const sum = await endSession(id);
  console.log({ frames, boxes, says, steps: [...steps], phases: seen, summary: sum });
  const ok = frames > 0 && seen.includes("guiding") && steps.size > 0;
  console.log(ok ? "PASS" : "FAIL");
  process.exit(ok ? 0 : 1);
}
main().catch((e) => { console.error("FAIL", e); process.exit(1); });
