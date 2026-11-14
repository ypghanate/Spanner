import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Alert as RNAlert } from "react-native";
import type {
  Alert,
  BrainResponse,
  Detection,
  HudCommand,
  MediaUpdate,
  Phase,
  RepairPlan,
  SceneCommand,
  SessionSummary,
} from "../../types/contracts";
import { createSession, endSession, LiveSocket } from "../lib/live";
import { SpeechPlayer, VoiceListener } from "../lib/voice";

export interface HistoryItem {
  id: string;
  title: string;
  detail: string;
}

export interface SpannerState {
  status: "idle" | "connecting" | "live" | "error";
  error: string | null;
  connected: boolean;
  sessionId: string | null;
  startedAt: number | null;
  phase: Phase;
  plan: RepairPlan | null;
  currentStep: number | null;
  stepStatus: BrainResponse["step_status"];
  caption: string; // what Spanner last said
  userText: string; // live caption of what the user is saying / just said
  speaking: boolean;
  level: number; // mic level 0-1
  hud: HudCommand[];
  detections: Detection[];
  alert: Alert | null;
  partsCard: Record<string, unknown> | null;
  scene3d: SceneCommand[];
  media: MediaUpdate[];
  summary: SessionSummary | null;
  history: HistoryItem[];
  explainer: boolean; // user (or a 3D command) switched to the 3D / demonstration view
}

const initial: SpannerState = {
  status: "idle",
  error: null,
  connected: false,
  sessionId: null,
  startedAt: null,
  phase: "identifying",
  plan: null,
  currentStep: null,
  stepStatus: null,
  caption: "",
  userText: "",
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
  explainer: false,
};

interface Actions {
  startCall(): Promise<void>;
  endCall(): Promise<void>;
  sendFrame(jpegB64: string): void;
  /** Tap on the voice action: send what has been said so far. */
  sendNow(): void;
  /** Tap on "Spanner is speaking": stop the reply (barge-in). */
  interrupt(): void;
  setExplainer(on: boolean): void;
  dismissSummary(): void;
}

const Ctx = createContext<(SpannerState & Actions) | null>(null);

/** HUD commands: empty = no change, {"op":"clear"} = clear. */
function applyHud(current: HudCommand[], incoming: HudCommand[]): HudCommand[] {
  let out = current;
  for (const c of incoming) {
    if (c.op === "clear") out = [];
    else out = [...out.filter((o) => !(o.target_label && o.target_label === c.target_label)), c].slice(-6);
  }
  return out;
}

export function SpannerProvider({ children }: { children: React.ReactNode }) {
  const [s, setS] = useState<SpannerState>(initial);
  const patch = useCallback((p: Partial<SpannerState> | ((s: SpannerState) => Partial<SpannerState>)) => {
    setS((prev) => ({ ...prev, ...(typeof p === "function" ? p(prev) : p) }));
  }, []);

  const sock = useRef<LiveSocket | null>(null);
  const voice = useRef<VoiceListener | null>(null);
  const speech = useRef<SpeechPlayer | null>(null);
  const alertTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stateRef = useRef(s);
  stateRef.current = s;

  const apply = useCallback(
    (r: BrainResponse, speak: boolean) => {
      patch((prev) => {
        const next: Partial<SpannerState> = {
          phase: r.phase,
          hud: applyHud(prev.hud, r.hud),
          scene3d: r.scene3d.length ? r.scene3d : prev.scene3d,
          media: r.media.length ? [...prev.media, ...r.media] : prev.media,
        };
        if (r.say) next.caption = r.say;
        if (r.plan) next.plan = r.plan;
        if (r.current_step != null) next.currentStep = r.current_step;
        if (r.step_status) next.stepStatus = r.step_status;
        if (r.parts_card) next.partsCard = r.parts_card;
        if (r.alert) next.alert = r.alert;
        else if (r.step_status === "in_progress" || r.step_status === "done") next.alert = null;
        if (r.scene3d.some((c) => c.op !== "reset")) next.explainer = true;
        if (r.scene3d.some((c) => c.op === "reset")) next.explainer = false;
        return next;
      });
      if (r.alert?.severity === "warn") {
        if (alertTimer.current) clearTimeout(alertTimer.current);
        alertTimer.current = setTimeout(() => patch({ alert: null }), 10000);
      }
      if (speak && r.say) speech.current?.enqueue(r.say, r.tts_url);
    },
    [patch],
  );

  const teardown = useCallback(async () => {
    await voice.current?.stop();
    speech.current?.stop();
    sock.current?.close();
    voice.current = null;
    sock.current = null;
    if (alertTimer.current) clearTimeout(alertTimer.current);
  }, []);

  const startCall = useCallback(async () => {
    if (stateRef.current.status === "connecting" || stateRef.current.status === "live") return;
    patch({ ...initial, history: stateRef.current.history, status: "connecting" });
    try {
      const id = await createSession();

      speech.current = new SpeechPlayer((playing) => {
        patch({ speaking: playing });
        sock.current?.send({ type: "playback", state: playing ? "start" : "end" });
        return voice.current?.setMuted(playing);
      });

      const socket = new LiveSocket(id, {
        onOpen: () => {
          patch({ connected: true });
          socket.send({ type: "start", tts: "url" });
        },
        onClose: () => patch({ connected: false }),
        onFrameResult: (r) => patch({ detections: r.detections }),
        onEvent: (r) => apply(r, true),
        onResponse: (r, speak) => apply(r, speak),
        onPartial: (text) => patch({ userText: text }),
        onFinal: (text) => patch({ userText: text }),
        onError: (code, message) => {
          if (code === "tts_error" || code === "bad_frame") return;
          patch({ error: `${code}: ${message}` });
        },
      });
      sock.current = socket;
      socket.connect();

      const listener = new VoiceListener(
        (u) => socket.send({ type: "utterance", audio_b64: u.audioB64, format: "m4a", duration_s: u.durationS }),
        (level) => patch({ level }),
        () => {},
      );
      voice.current = listener;
      const ok = await listener.start();
      if (!ok) RNAlert.alert("Microphone off", "Spanner can still see, but it can't hear you. Enable the microphone in Settings.");

      patch({ status: "live", sessionId: id, startedAt: Date.now() });
    } catch (e) {
      await teardown();
      patch({ status: "error", error: e instanceof Error ? e.message : String(e) });
    }
  }, [apply, patch, teardown]);

  const endCall = useCallback(async () => {
    const { sessionId, plan } = stateRef.current;
    await teardown();
    let summary: SessionSummary | null = null;
    if (sessionId) {
      try {
        summary = await endSession(sessionId);
      } catch {}
    }
    patch((prev) => ({
      status: "idle",
      connected: false,
      speaking: false,
      summary: summary && (summary.steps_done > 0 || prev.phase === "done") ? summary : null,
      history: summary && summary.steps_done > 0
        ? [{ id: summary.session_id, title: summary.object ?? plan?.object ?? "Repair", detail: summary.fault ?? "Repaired" }, ...prev.history]
        : prev.history,
    }));
  }, [patch, teardown]);

  // The repair is over: close the call and show the summary.
  useEffect(() => {
    if (s.status === "live" && s.phase === "done") {
      const t = setTimeout(() => void endCall(), 6000); // let the closing line be spoken
      return () => clearTimeout(t);
    }
  }, [s.status, s.phase, endCall]);

  useEffect(() => () => void teardown(), [teardown]);

  const actions = useMemo<Actions>(
    () => ({
      startCall,
      endCall,
      sendFrame: (jpegB64) => sock.current?.send({ type: "frame", jpeg_b64: jpegB64, ts: Date.now() / 1000 }),
      sendNow: () => voice.current?.flush(),
      interrupt: () => {
        speech.current?.stop();
        sock.current?.send({ type: "barge_in" });
      },
      setExplainer: (on) => patch({ explainer: on }),
      dismissSummary: () => patch({ summary: null, plan: null, phase: "identifying", caption: "", userText: "" }),
    }),
    [startCall, endCall, patch],
  );

  return <Ctx.Provider value={{ ...s, ...actions }}>{children}</Ctx.Provider>;
}

export function useSpanner() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSpanner outside SpannerProvider");
  return v;
}
