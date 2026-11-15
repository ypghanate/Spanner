import type { RepairPlan, RepairStep } from "../../types/contracts";

/**
 * Client for the laptop webapp's coach API (backend/coach.py, the RK61 hologram viewer).
 * The phone is the viewer's camera: it uploads JPEGs under the laptop's session id and reads back
 * the coach's view (step, mistakes, spoken line) and the keyboard pose, exactly like training/viewer/phone.html.
 */

export interface Pairing {
  /** Laptop origin as encoded in the QR, e.g. https://abc.trycloudflare.com or https://192.168.1.5:8443 */
  origin: string;
  /** The laptop viewer's session id (the `s` query parameter, "laptop-xxxxxx"). */
  session: string;
}

export interface CoachView {
  object_visible: boolean;
  what_i_see: string;
  step_index: number; // 1-based, 0 = not started / nothing relevant visible
  step_status: "not_started" | "in_progress" | "done";
  mistake: string;
  confidence: number;
  say: string;
  latency_ms?: number;
  ts: number;
}

export interface CoachPose {
  found: boolean;
  cx?: number;
  cy?: number;
  w?: number;
  h?: number;
  angle?: number;
  ts: number;
}

export interface CoachPlan {
  broken_key: string;
  plan: RepairPlan;
  parts_card: Record<string, unknown> | null;
  /** False until the person has said what is wrong: `plan` is then only the canned fallback and nothing should start. */
  chosen?: boolean;
}

export interface Latest {
  view?: CoachView;
  pose?: CoachPose;
  /** Changes whenever the laptop generates a new plan for this session (the user said what they want fixed). */
  plan_id?: string;
}

/** The laptop's QR encodes `${origin}/training/viewer/phone.html?s=<session>`. Anything else is not a pairing code. */
export function parsePairing(text: string): Pairing | null {
  const m = text.trim().match(/^(https?:\/\/[^/?#\s]+)[^?#\s]*\?(?:[^#\s]*&)?s=([A-Za-z0-9_-]+)/);
  return m ? { origin: m[1], session: m[2] } : null;
}

const withTimeout = async (url: string, init: RequestInit | undefined, ms: number) => {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try {
    return await fetch(url, { ...init, signal: ctl.signal });
  } finally {
    clearTimeout(t);
  }
};

export class CoachClient {
  constructor(readonly origin: string, readonly session: string) {}

  private async json<T>(path: string, ms = 6000): Promise<T> {
    const res = await withTimeout(`${this.origin}${path}`, undefined, ms);
    if (!res.ok) throw new Error(`${path} ${res.status}`);
    return (await res.json()) as T;
  }

  health() {
    return this.json<{ model: string; procedure_steps: number; anthropic_key: boolean; tts: boolean }>("/coach/health", 4000);
  }

  /** The repair plan the laptop is showing. Steps are normalised so the UI can rely on every field. */
  async plan(): Promise<CoachPlan> {
    const raw = await this.json<CoachPlan>(`/coach/plan?session=${encodeURIComponent(this.session)}`);
    const steps: RepairStep[] = raw.plan.steps.map((s) => ({
      ...s,
      parts: s.parts ?? [],
      common_mistakes: s.common_mistakes ?? [],
      scene3d: s.scene3d ?? [],
      sources: s.sources ?? [],
      illustration_prompt: s.illustration_prompt ?? null,
      illustration_url: s.illustration_url ?? null,
      clip_url: s.clip_url ?? null,
    }));
    return { ...raw, plan: { ...raw.plan, steps, safety_notes: raw.plan.safety_notes ?? [], tools_needed: raw.plan.tools_needed ?? [], sources: raw.plan.sources ?? [] } };
  }

  latest() {
    return this.json<Latest>(`/coach/latest?session=${encodeURIComponent(this.session)}`, 3000);
  }

  private async upload<T>(path: string, uri: string, extra: Record<string, string> = {}, ms = 20000): Promise<T> {
    // The phone's fetch rejects a {uri, name, type} file object ("unsupported FormDataPart") and Expo's native
    // uploadAsync hung on some networks, so read the photo into a real Blob and send it with the same fetch the
    // polling already uses.
    const blob = await (await withTimeout(uri, undefined, 5000)).blob();
    const fd = new FormData();
    fd.append("image", blob, "f.jpg");
    fd.append("session", this.session);
    for (const [k, v] of Object.entries(extra)) fd.append(k, v);
    const res = await withTimeout(`${this.origin}${path}`, { method: "POST", body: fd }, ms);
    if (!res.ok) throw new Error(`${path} ${res.status}`);
    return (await res.json()) as T;
  }

  /** One spoken utterance from this phone's microphone (base64 m4a). The laptop transcribes it and acts on it. */
  async utterance(audioB64: string): Promise<{ text: string }> {
    const res = await withTimeout(
      `${this.origin}/coach/utterance`,
      { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ session: this.session, audio_b64: audioB64, filename: "clip.m4a" }) },
      20000,
    );
    if (!res.ok) throw new Error(`/coach/utterance ${res.status}`);
    return (await res.json()) as { text: string };
  }

  /** Where is the keyboard and how is it turned? Fast and local on the laptop, drives the hologram's rotation. */
  pose(uri: string) {
    return this.upload<CoachPose>("/coach/pose", uri, {}, 8000);
  }

  /** What step is the user on? One Claude call on the laptop; `currentStep` is the step the screen shows (1-based, 0 = none). */
  frame(uri: string, currentStep: number) {
    return this.upload<CoachView>("/coach/frame", uri, { current_step: String(currentStep) });
  }

  /** Spanner's voice for a line, streamed as MP3 (ElevenLabs on the laptop). */
  sayUrl(text: string) {
    return `${this.origin}/coach/say?text=${encodeURIComponent(text)}`;
  }
}

/**
 * Connect to the laptop named by a pairing code. The QR origin is tried first; if that fails we also try plain
 * http on :8000 of the same host (a self-signed https certificate is rejected by the phone's native fetch).
 */
export async function connect(p: Pairing): Promise<{ client: CoachClient; plan: CoachPlan }> {
  const host = p.origin.replace(/^https?:\/\//, "").replace(/:\d+$/, "");
  const origins = [...new Set([p.origin, `http://${host}:8000`])];
  let last: unknown = null;
  for (const origin of origins) {
    const client = new CoachClient(origin, p.session);
    try {
      await client.health();
      return { client, plan: await client.plan() };
    } catch (e) {
      last = e;
    }
  }
  const msg = last instanceof Error ? last.message : String(last);
  throw new Error(
    `Couldn’t reach the laptop (${msg}). Check you’re on the same network, or open the laptop page through the tunnel URL so the QR has a trusted https address.`,
  );
}

/**
 * Follows the laptop viewer's rule for "what step are we on" (viewer.js onCoach) so the phone and the laptop agree:
 * two frames in a row, or one very confident frame, and finished steps advance after a short beat.
 */
export class StepTracker {
  /** Step shown now, 1-based. 0 = repair not started. */
  step = 0;
  /** True once the last step has been seen done. */
  complete = false;
  private agree = 0;
  private last = 0;
  private lastDone = -1;
  private advanceAt: number | null = null;

  update(v: CoachView, total: number, now: number) {
    if (!v.object_visible || v.step_index < 1) {
      this.agree = 0;
      return;
    }
    this.agree = v.step_index === this.last ? this.agree + 1 : 1;
    this.last = v.step_index;
    const sure = this.agree >= 2 || v.confidence >= 0.85;
    if (sure && v.step_index !== this.step && v.confidence >= 0.5) {
      this.step = v.step_index;
      this.advanceAt = null;
    } else if (sure && v.step_index === this.step && v.step_status === "done" && this.lastDone !== this.step) {
      this.lastDone = this.step;
      if (this.step >= total) this.complete = true;
      else this.advanceAt = now + 1400;
    }
  }

  /** Call regularly: moves on to the next step once a finished step has had its beat. */
  tick(now: number) {
    if (this.advanceAt !== null && now >= this.advanceAt) {
      this.step += 1;
      this.advanceAt = null;
    }
  }
}
