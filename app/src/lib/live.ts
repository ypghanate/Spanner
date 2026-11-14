import type {
  BrainResponse,
  FrameResponse,
  LiveClientMessage,
  LiveServerMessage,
  SessionSummary,
} from "../../types/contracts";
import { BACKEND_URL, WS_URL } from "./config";

export interface LiveHandlers {
  onOpen(): void;
  onClose(code: number): void;
  onFrameResult(r: FrameResponse): void;
  onEvent(r: BrainResponse): void;
  onResponse(r: BrainResponse, speak: boolean): void;
  onPartial(text: string): void;
  onFinal(text: string): void;
  onError(code: string, message: string): void;
}

export async function createSession(): Promise<string> {
  const res = await fetch(`${BACKEND_URL}/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device: "iphone" }),
  });
  if (!res.ok) throw new Error(`POST /session ${res.status}`);
  return (await res.json()).session_id as string;
}

export async function endSession(id: string): Promise<SessionSummary> {
  const res = await fetch(`${BACKEND_URL}/session/${id}/end`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /end ${res.status}`);
  return (await res.json()) as SessionSummary;
}

/** One WebSocket per session; reconnects to the same URL (queued results are replayed by the server). */
export class LiveSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private retry = 0;

  constructor(private sessionId: string, private h: LiveHandlers) {}

  connect() {
    this.closed = false;
    const ws = new WebSocket(`${WS_URL}/session/${this.sessionId}/live`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.h.onOpen();
    };
    ws.onmessage = (e) => {
      if (typeof e.data !== "string") return; // binary = streamed mp3, unused in url mode
      let m: LiveServerMessage;
      try {
        m = JSON.parse(e.data);
      } catch {
        return;
      }
      switch (m.type) {
        case "frame_result": this.h.onFrameResult(m.result); break;
        case "event": this.h.onEvent(m.response); break;
        case "response": this.h.onResponse(m.response, m.speak); break;
        case "partial": this.h.onPartial(m.text); break;
        case "final": this.h.onFinal(m.text); break;
        case "error": this.h.onError(m.code, m.message); break;
      }
    };
    ws.onclose = (e) => {
      this.h.onClose(e.code);
      // 4404 unknown session, 4000 replaced by a newer socket: do not retry.
      if (this.closed || e.code === 4404 || e.code === 4000) return;
      const wait = Math.min(1000 * 2 ** this.retry++, 8000);
      setTimeout(() => !this.closed && this.connect(), wait);
    };
    ws.onerror = () => {};
  }

  get open() {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  send(m: LiveClientMessage) {
    if (this.open) this.ws!.send(JSON.stringify(m));
  }

  close() {
    this.closed = true;
    this.send({ type: "stop" });
    this.ws?.close();
  }
}
