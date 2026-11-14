/**
 * Headless check of the app's laptop client (src/lib/coach.ts) against a running backend.
 *   backend up (uvicorn backend.main:app, or scripts/serve.py), then:  npm run coach-smoke -- http://127.0.0.1:8000
 * Checks pairing-code parsing, /coach/health, /coach/plan and /coach/latest. With a detector available it also posts
 * a fixture frame to /coach/pose. (/coach/frame needs ANTHROPIC_API_KEY on the laptop, so it is skipped here.)
 */
import { parsePairing, CoachClient, StepTracker } from "../src/lib/coach";

const origin = (process.argv[2] ?? "http://127.0.0.1:8000").replace(/\/$/, "");
let failed = 0;
const ok = (name: string, pass: boolean, extra = "") => {
  console.log(`${pass ? "ok  " : "FAIL"} ${name}${extra ? " · " + extra : ""}`);
  if (!pass) failed++;
};

async function main() {
  // The code the laptop viewer shows (viewer.js PHONE_URL) and a few things that are not pairing codes.
  const p = parsePairing(`${origin}/training/viewer/phone.html?s=laptop-ab12cd`);
  ok("parse QR url", p?.origin === origin && p?.session === "laptop-ab12cd", JSON.stringify(p));
  ok("parse extra params", parsePairing("https://x.trycloudflare.com/training/viewer/phone.html?a=1&s=laptop-zz9")?.session === "laptop-zz9");
  ok("reject other text", parsePairing("hello") === null && parsePairing("https://example.com/") === null);

  const c = new CoachClient(origin, "laptop-smoke");
  const h = await c.health();
  ok("/coach/health", h.procedure_steps > 0, `${h.procedure_steps} steps, model ${h.model}`);

  const plan = await c.plan();
  const steps = plan.plan.steps;
  ok("/coach/plan", steps.length > 0 && steps.every((s) => Array.isArray(s.parts) && Array.isArray(s.common_mistakes)), `${plan.plan.object} · ${steps.length} steps`);

  const latest = await c.latest();
  ok("/coach/latest", typeof latest === "object" && latest !== null);

  // The step tracker follows the viewer's rule: two agreeing frames (or one very confident frame) to change step.
  const t = new StepTracker();
  const v = (step: number, conf: number, status: "in_progress" | "done" = "in_progress") => ({
    object_visible: true, what_i_see: "", step_index: step, step_status: status, mistake: "", confidence: conf, say: "", ts: 1,
  });
  t.update(v(2, 0.6), 5, 0);
  ok("tracker waits for a second frame", t.step === 0);
  t.update(v(2, 0.6), 5, 100);
  ok("tracker follows two agreeing frames", t.step === 2);
  t.update(v(2, 0.9, "done"), 5, 200);
  t.tick(200);
  ok("tracker holds a finished step briefly", t.step === 2);
  t.tick(1700);
  ok("tracker then advances", t.step === 3);

  try {
    const { readFileSync, existsSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const file = resolve(__dirname, "../../fixtures/frames/placeholder.jpg");
    if (existsSync(file)) {
      // Node can't send a React Native file URI, so post the multipart body directly with the same field names.
      const fd = new FormData();
      fd.append("image", new Blob([readFileSync(file)], { type: "image/jpeg" }), "f.jpg");
      fd.append("session", "laptop-smoke");
      const r = await fetch(`${origin}/coach/pose`, { method: "POST", body: fd });
      const pose = (await r.json()) as { found?: boolean; ts?: number };
      ok("/coach/pose", r.ok && typeof pose.ts === "number", `found=${pose.found}`);
      const after = await c.latest();
      ok("/coach/latest returns the pose for the session", after.pose?.ts === pose.ts);
    }
  } catch (e) {
    ok("/coach/pose", false, String(e));
  }

  console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.error("FAIL", e instanceof Error ? e.message : e);
  process.exit(1);
});
