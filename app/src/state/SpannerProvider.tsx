import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type {
  Alert,
  Detection,
  HudCommand,
  MediaUpdate,
  Phase,
  RepairPlan,
  SceneCommand,
  SessionSummary,
} from "../../types/contracts";
import { CoachClient, CoachPose, CoachView, connect, Pairing, parsePairing, StepTracker } from "../lib/coach";
import { SpeechPlayer, VoiceListener } from "../lib/voice";

export interface HistoryItem {
  id: string;
  title: string;
  detail: string;
}

/** What the call screen shows while guiding: the live camera, or one of the diagram views from the controls sheet. */
export type CallView = "camera" | "3d" | "2d" | "split" | "demo";

/** What Spanner found while diagnosing, for the "problem is understood" card. */
export interface Finding {
  title: string | null;
  detail: string;
}

/** "One key no longer registers because its switch has failed; replace it." -> "One key no longer registers" */
const faultHeadline = (fault: string) => fault.split(/\s+because\b|[;.]/)[0].trim();

export interface SpannerState {
  /** idle = home, scanning = QR scanner, connecting = pairing with the laptop, live = on a call with the laptop. */
  status: "idle" | "scanning" | "connecting" | "live" | "error";
  error: string | null;
  connected: boolean; // the laptop answered the last poll
  pairing: Pairing | null;
  sessionId: string | null;
  startedAt: number | null;
  phase: Phase;
  plan: RepairPlan | null;
  currentStep: number | null;
  stepStatus: "in_progress" | "done" | "mistake" | null;
  caption: string; // what Spanner last said (the laptop speaks it)
  userText: string;
  transcriptWho: "you" | "spanner"; // whose words the transcript card shows
  speaking: boolean;
  level: number;
  hud: HudCommand[];
  detections: Detection[];
  alert: Alert | null;
  partsCard: Record<string, unknown> | null;
  scene3d: SceneCommand[];
  media: MediaUpdate[];
  summary: SessionSummary | null;
  history: HistoryItem[];
  view: CallView; // diagram view picked from the controls sheet
  finding: Finding | null; // what the coach sees right now
  micMuted: boolean; // Mute switch in the controls sheet (the laptop owns the real microphone for now)
  botMuted: boolean; // speaker button: Spanner is silenced and the conversation is paused
  speakerOn: boolean; // also play Spanner's voice on this phone (the laptop already speaks)
  cameraOn: boolean; // false = stop sending frames
  lastFrameUri: string | null; // last photo sent to the laptop, shown on the summary
}

const initial: SpannerState = {
  status: "idle",
  error: null,
  connected: false,
  pairing: null,
  sessionId: null,
  startedAt: null,
  phase: "identifying",
  plan: null,
  currentStep: null,
  stepStatus: null,
  caption: "",
  userText: "",
  transcriptWho: "spanner",
  speaking: false,
  level: 0,
  hud: [],
  detections: [],
  alert: null,
  partsCard: null,
  scene3d: [],
  media: [],
  summary: null,
  history: [
    { id: "sample-1", title: "Desk fan", detail: "Loose grille · Yesterday" },
    { id: "sample-2", title: "Bicycle", detail: "Chain reseated · 12 Nov" },
  ],
  view: "camera",
  finding: null,
  micMuted: false,
  botMuted: false,
  speakerOn: false,
  cameraOn: true,
  lastFrameUri: null,
};

interface Actions {
  /** Open the QR scanner to pair with the laptop. */
  openScanner(): void;
  closeScanner(): void;
  /** A scanned code: connect to that laptop. Returns an error message, or null when connected. */
  pair(qrText: string): Promise<string | null>;
  endCall(): Promise<void>;
  /** A photo from the camera: goes to the laptop's /coach/pose (often) and /coach/frame (about every 1.5 s). */
  sendCapture(uri: string): boolean;
  /** Tap on the voice footer: send what has been said so far. */
  sendNow(): void;
  /** Tap anywhere on the call: cut Spanner off and listen at once; if Spanner is quiet, send what was said so far. */
  tapScreen(): void;
  setBotMuted(on: boolean): void;
  /** Tap on "Spanner is speaking": stop the phone's own playback. */
  interrupt(): void;
  setView(view: CallView): void;
  setMicMuted(on: boolean): void;
  setSpeakerOn(on: boolean): void;
  setCameraOn(on: boolean): void;
  dismissSummary(): void;
}

const Ctx = createContext<(SpannerState & Actions) | null>(null);

const POLL_MS = 300; // the laptop viewer polls /coach/latest at the same rate
const POSE_MS = 250;
const FRAME_MS = 0; // the next frame goes as soon as the last answer lands (frameBusy is the only gate): the camera is always being read

export function SpannerProvider({ children }: { children: React.ReactNode }) {
  const [s, setS] = useState<SpannerState>(initial);
  const patch = useCallback((p: Partial<SpannerState> | ((s: SpannerState) => Partial<SpannerState>)) => {
    setS((prev) => ({ ...prev, ...(typeof p === "function" ? p(prev) : p) }));
  }, []);

  const stateRef = useRef(s);
  stateRef.current = s;

  const client = useRef<CoachClient | null>(null);
  const speech = useRef<SpeechPlayer | null>(null);
  const tracker = useRef(new StepTracker());
  const listener = useRef<VoiceListener | null>(null);
  const laptopLeads = useRef(false); // true once the laptop reports its screen: it owns the current step
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);
  const polling = useRef(false);
  const last = useRef({ view: null as CoachView | null, viewTs: 0, poseTs: 0, planId: "", say: "", poseAt: 0, frameAt: 0, poseBusy: false, frameBusy: false, uri: null as string | null, replyId: "" });
  const laptopSpeaking = useRef(false); // the laptop's own voice, from /coach/latest
  const phonePlaying = useRef(false);
  const bargeHold = useRef(0); // after a tap: ignore the old "laptop is speaking" until it clears (or this time passes)
  const lastStop = useRef(0);
  const speakingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Turn the laptop's latest coach view + step tracker into the screen state the Figma UI reads. */
  const project = useCallback(
    (v: CoachView | null) => {
      const plan = stateRef.current.plan;
      if (!plan) return;
      const t = tracker.current;
      const phase: Phase = t.complete ? "done" : t.step >= 1 ? "guiding" : v?.object_visible ? "researching" : "identifying";
      const mistake = v?.mistake?.trim() ?? "";
      patch({
        phase,
        currentStep: t.step >= 1 ? t.step : null,
        stepStatus: mistake ? "mistake" : t.step >= 1 ? (v?.step_status === "done" ? "done" : "in_progress") : null,
        alert: mistake ? { severity: "warn", message: mistake } : null,
        // The laptop plan's own 3D commands for this step (e.g. highlight switch_F) label the 3D view.
        scene3d: t.step >= 1 ? (plan.steps.find((x) => x.index === t.step)?.scene3d ?? []) : [],
        finding: v?.object_visible ? { title: faultHeadline(plan.fault), detail: v.what_i_see } : null,
      });
    },
    [patch],
  );

  const onPose = useCallback(
    (p: CoachPose) => {
      const warn = !!stateRef.current.alert;
      if (!p.found || p.cx == null || p.cy == null || p.w == null || p.h == null) {
        patch({ hud: [] });
        return;
      }
      const clamp = (n: number) => Math.max(0, Math.min(1, n));
      const x = clamp(p.cx - p.w / 2);
      const y = clamp(p.cy - p.h / 2);
      patch({
        hud: [
          {
            op: "label",
            target_label: "keyboard",
            box: { x, y, w: clamp(p.w), h: clamp(p.h) },
            text: `Keyboard · ${Math.round(p.angle ?? 0)}°`,
            color: warn ? "warn" : "info",
          },
        ],
      });
    },
    [patch],
  );

  const onView = useCallback(
    (v: CoachView, c: CoachClient) => {
      const l = last.current;
      const plan = stateRef.current.plan;
      if (!plan) return;
      if (!laptopLeads.current) tracker.current.update(v, plan.steps.length, Date.now()); // else the laptop's step rules
      l.view = v;
      project(v);
      if (v.say && v.say !== l.say) {
        l.say = v.say;
        patch({ caption: v.say, transcriptWho: "spanner", speaking: true });
        if (speakingTimer.current) clearTimeout(speakingTimer.current);
        speakingTimer.current = setTimeout(() => patch({ speaking: false }), 4000);
        if (stateRef.current.speakerOn) speech.current?.enqueue(v.say, c.sayUrl(v.say));
      }
    },
    [patch, project],
  );

  const stopPolling = useCallback(() => {
    if (poll.current) clearInterval(poll.current);
    poll.current = null;
    if (speakingTimer.current) clearTimeout(speakingTimer.current);
  }, []);

  const startPolling = useCallback(
    (c: CoachClient) => {
      stopPolling();
      poll.current = setInterval(async () => {
        if (polling.current) return;
        polling.current = true;
        try {
          const d = await c.latest();
          const l = last.current;
          if (d.plan_id && d.plan_id !== l.planId) {
            // The laptop wrote a new plan for what the user said they want fixed: follow it.
            l.planId = d.plan_id;
            {
              const np = await c.plan();
              tracker.current = new StepTracker();
              patch({ plan: np.plan, partsCard: np.parts_card, currentStep: null, stepStatus: null, phase: "identifying" });
            }
          }
          // the laptop decides which step is current (one source of truth, no flicking between two trackers)
          if (d.screen) {
            laptopLeads.current = true;
            const tr = tracker.current, step = d.screen === "step" ? d.screen_step ?? 0 : tr.step, done = d.screen === "done";
            if (step !== tr.step || done !== tr.complete) { tr.step = step; tr.complete = done; project(l.view); }
          }
          if (d.pose && d.pose.ts !== l.poseTs) {
            l.poseTs = d.pose.ts;
            onPose(d.pose);
          }
          if (d.view && d.view.ts !== l.viewTs) {
            l.viewTs = d.view.ts;
            onView(d.view, c);
          } else {
            const before = tracker.current.step;
            tracker.current.tick(Date.now());
            if (tracker.current.step !== before) project(l.view);
          }
          // what Spanner answered to the last thing said (shown in the transcript; the laptop speaks it)
          if (d.reply && d.reply.id !== l.replyId) {
            l.replyId = d.reply.id;
            if (d.reply.say?.trim()) patch({ caption: d.reply.say.trim(), transcriptWho: "spanner" });
          }
          // the laptop is talking: pause the phone's mic (it would only hear Spanner), resume when it stops
          let talking = !!d.speaking;
          if (bargeHold.current) {   // the person tapped to cut Spanner off: the old speech is over even if the flag lingers
            if (!talking || Date.now() > bargeHold.current) bargeHold.current = 0;
            else talking = false;
          }
          if (talking !== laptopSpeaking.current) {
            laptopSpeaking.current = talking;
            if (!phonePlaying.current) patch({ speaking: talking });
          }
          const s0 = stateRef.current;
          if (s0.botMuted && d.speaking && Date.now() - lastStop.current > 1000) {   // paused: keep Spanner quiet
            lastStop.current = Date.now();
            void c.signal("stop").catch(() => {});
          }
          void listener.current?.setMuted(talking || s0.micMuted);
          if (!stateRef.current.connected) patch({ connected: true });
        } catch {
          if (stateRef.current.connected) patch({ connected: false });
        } finally {
          polling.current = false;
        }
      }, POLL_MS);
    },
    [onPose, onView, patch, project, stopPolling],
  );

  /** The phone is the microphone: it records each utterance and the laptop transcribes it, acts on it and speaks back. */
  const startListening = useCallback(
    (c: CoachClient) => {
      void listener.current?.stop();
      const l = new VoiceListener(
        (u) => {
          if (stateRef.current.botMuted) return;   // conversation paused: nothing is sent
          c.utterance(u.audioB64)
            .then((r) => { if (r?.text) patch({ userText: r.text, transcriptWho: "you" }); })
            .catch((e) => patch({ error: "Voice failed: " + (e instanceof Error ? e.message : String(e)) }));
        },
        (level) => patch({ level }),
        () => patch({ userText: "", transcriptWho: "you" }),
      );
      listener.current = l;
      l.start().then((ok) => {
        if (!ok) patch({ error: "Microphone permission is off: Spanner can't hear you. Enable it in Settings." });
        else void l.setMuted(stateRef.current.micMuted);
      });
    },
    [patch],
  );

  const teardown = useCallback(() => {
    stopPolling();
    speech.current?.stop();
    void listener.current?.stop();
    listener.current = null;
    client.current = null;
  }, [stopPolling]);

  const openScanner = useCallback(() => {
    if (stateRef.current.status === "idle" || stateRef.current.status === "error") patch({ status: "scanning", error: null });
  }, [patch]);

  const closeScanner = useCallback(() => {
    if (stateRef.current.status === "scanning" || stateRef.current.status === "connecting") patch({ status: "idle", error: null });
  }, [patch]);

  const pair = useCallback(
    async (text: string) => {
      if (stateRef.current.status === "connecting") return null;
      const p = parsePairing(text);
      if (!p) return "That isn’t a Spanner pairing code. Scan the QR shown on your laptop.";
      patch({ status: "connecting", error: null });
      try {
        const { client: c, plan } = await connect(p);
        teardown();
        client.current = c;
        tracker.current = new StepTracker();
        last.current = { view: null, viewTs: 0, poseTs: 0, planId: "", say: "", poseAt: 0, frameAt: 0, poseBusy: false, frameBusy: false, uri: null, replyId: "" };
        speech.current = new SpeechPlayer((playing) => {
          phonePlaying.current = playing;
          patch({ speaking: playing || laptopSpeaking.current });
        });
        patch({
          ...initial,
          history: stateRef.current.history,
          status: "live",
          connected: true,
          pairing: { origin: c.origin, session: c.session },
          sessionId: c.session,
          startedAt: Date.now(),
          // until the person says what is wrong there is no repair: don't show the laptop's canned fallback plan
          plan: plan.chosen === false ? null : plan.plan,
          partsCard: plan.chosen === false ? null : plan.parts_card,
        });
        startPolling(c);
        startListening(c);
        return null;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        patch({ status: "scanning", error: msg });
        return msg;
      }
    },
    [patch, startPolling, teardown],
  );

  const endCall = useCallback(async () => {
    const prev = stateRef.current;
    const t = tracker.current;
    void client.current?.signal("end").catch(() => {}); // hang up the laptop's guide too
    teardown();
    const total = prev.plan?.steps.length ?? 0;
    const done = t.complete ? total : Math.max(0, t.step - 1);
    const summary: SessionSummary | null =
      prev.plan && done > 0
        ? {
            session_id: prev.sessionId ?? "laptop",
            object: prev.plan.object,
            fault: faultHeadline(prev.plan.fault),
            steps_total: total,
            steps_done: done,
            duration_s: prev.startedAt ? Math.round((Date.now() - prev.startedAt) / 1000) : 0,
            sources: prev.plan.sources ?? [],
          }
        : null;
    patch((p) => ({
      status: "idle",
      connected: false,
      speaking: false,
      hud: [],
      pairing: null,
      lastFrameUri: last.current.uri,
      summary,
      history: summary
        ? [{ id: `${summary.session_id}-${Date.now()}`, title: shortObject(summary.object ?? "Repair"), detail: `${summary.fault ?? "Repaired"} · ${summary.steps_done}/${summary.steps_total} steps` }, ...p.history]
        : p.history,
    }));
  }, [patch, teardown]);

  // The repair is over: close the call and show the summary.
  useEffect(() => {
    if (s.status === "live" && s.phase === "done") {
      const t = setTimeout(() => void endCall(), 6000); // let the closing line be spoken
      return () => clearTimeout(t);
    }
  }, [s.status, s.phase, endCall]);

  useEffect(() => () => teardown(), [teardown]);

  const actions = useMemo<Actions>(
    () => ({
      openScanner,
      closeScanner,
      pair,
      endCall,
      sendCapture: (uri) => {
        const c = client.current;
        if (!c) return false;
        const l = last.current;
        const now = Date.now();
        let used = false;
        if (!l.frameBusy && now - l.frameAt >= FRAME_MS) {
          used = true;
          l.frameBusy = true;
          l.frameAt = now;
          l.uri = uri;
          c.frame(uri, tracker.current.step)
            .catch((e) => patch({ error: "Upload failed: " + (e instanceof Error ? e.message : String(e)) }))
            .finally(() => {
              l.frameBusy = false;
            });
        }
        return used;
      },
      sendNow: () => listener.current?.flush(),
      interrupt: () => speech.current?.stop(),
      tapScreen: () => {
        const s1 = stateRef.current;
        if (s1.speaking || laptopSpeaking.current) {
          // cut Spanner off (phone playback and the laptop's voice) and open the microphone right now
          speech.current?.stop();
          void client.current?.signal("stop").catch(() => {});
          bargeHold.current = Date.now() + 6000;
          laptopSpeaking.current = false;
          patch({ speaking: false, transcriptWho: "you", userText: "" });
          if (!s1.micMuted) void listener.current?.setMuted(false);
        } else {
          listener.current?.flush();   // "tap once you've explained it"
        }
      },
      setBotMuted: (on) => {
        patch({ botMuted: on });
        if (on) {
          speech.current?.stop();
          void client.current?.signal("stop").catch(() => {});
        }
      },
      setView: (view) => patch({ view }),
      setMicMuted: (on) => {
        patch({ micMuted: on });
        void listener.current?.setMuted(on);
      },
      setSpeakerOn: (on) => {
        patch({ speakerOn: on });
        if (!on) speech.current?.stop();
      },
      setCameraOn: (on) => patch({ cameraOn: on }),
      dismissSummary: () =>
        patch({ summary: null, plan: null, phase: "identifying", caption: "", userText: "", view: "camera", finding: null, lastFrameUri: null }),
    }),
    [openScanner, closeScanner, pair, endCall, patch],
  );

  return <Ctx.Provider value={{ ...s, ...actions }}>{children}</Ctx.Provider>;
}

/** "Royal Kludge RK61 60% mechanical keyboard (hot-swappable)" -> "Royal Kludge RK61 60% mechanical keyboard" */
export const shortObject = (o: string) => o.replace(/\s*\(.*\)\s*$/, "").trim();

export function useSpanner() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSpanner outside SpannerProvider");
  return v;
}
