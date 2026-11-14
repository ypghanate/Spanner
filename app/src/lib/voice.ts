import { AudioModule, RecordingPresets, setAudioModeAsync, createAudioPlayer } from "expo-audio";
import type { AudioPlayer, AudioRecorder } from "expo-audio";
import * as FileSystem from "expo-file-system/legacy";
import * as Speech from "expo-speech";
import { absUrl } from "./config";

const SPEECH_DB = -38; // louder than this counts as speech
const SILENCE_MS = 700; // end of utterance (contract: about 0.7 s)
const MIN_SPEECH_MS = 350;
const MAX_UTTERANCE_MS = 15000;

export interface Utterance {
  audioB64: string;
  durationS: number;
}

/** Always-on listener: records, detects the end of speech from metering, hands over one m4a clip at a time. */
export class VoiceListener {
  private rec: AudioRecorder | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private running = false;
  private muted = false;
  private busy = false;
  private startedAt = 0;
  private speechAt = 0;
  private lastLoud = 0;
  private forceFlush = false;

  constructor(
    private onUtterance: (u: Utterance) => void,
    private onLevel: (level: number) => void,
    private onSpeechStart: () => void,
  ) {}

  async start(): Promise<boolean> {
    const perm = await AudioModule.requestRecordingPermissionsAsync();
    if (!perm.granted) return false;
    await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
    this.running = true;
    await this.begin();
    return true;
  }

  /**
   * Mute while Spanner talks (contract: the phone mutes the mic during playback).
   * Fully stops the recorder and leaves record mode so iOS plays through the speaker, not the earpiece.
   */
  async setMuted(m: boolean) {
    if (this.muted === m) return;
    this.muted = m;
    if (!this.running) return;
    if (m) {
      await this.release();
      try { await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true }); } catch {}
    } else {
      try { await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true }); } catch {}
      await new Promise((r) => setTimeout(r, 500)); // let the room tail off before listening again
      await this.begin();
    }
  }

  private async release() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    const rec = this.rec;
    this.rec = null;
    if (!rec) return;
    try { await rec.stop(); } catch {}
    try { rec.release(); } catch {}
  }

  /** Tap on the voice action: send what has been said so far. */
  flush() {
    this.forceFlush = true;
  }

  async stop() {
    this.running = false;
    await this.release();
  }

  private async begin() {
    if (!this.running || this.muted || this.rec) return;
    try {
      const rec = new AudioModule.AudioRecorder({ ...RecordingPresets.HIGH_QUALITY, isMeteringEnabled: true });
      await rec.prepareToRecordAsync();
      rec.record();
      this.rec = rec;
      this.startedAt = Date.now();
      this.speechAt = 0;
      this.lastLoud = 0;
      this.forceFlush = false;
      this.timer = setInterval(() => void this.tick(), 100);
    } catch {
      setTimeout(() => void this.begin(), 1000);
    }
  }

  private async tick() {
    const rec = this.rec;
    if (!rec || this.busy) return;
    const now = Date.now();
    const db = rec.getStatus().metering ?? -160;
    this.onLevel(Math.max(0, Math.min(1, (db + 60) / 50)));

    if (this.muted) return;
    if (db > SPEECH_DB) {
      if (!this.speechAt) {
        this.speechAt = now;
        this.onSpeechStart();
      }
      this.lastLoud = now;
    }
    const spoke = this.speechAt > 0 && this.lastLoud - this.speechAt >= MIN_SPEECH_MS;
    const quiet = this.speechAt > 0 && now - this.lastLoud >= SILENCE_MS;
    const tooLong = this.speechAt > 0 && now - this.speechAt >= MAX_UTTERANCE_MS;
    if ((spoke && quiet) || tooLong || (this.forceFlush && spoke)) await this.cycle(true);
    else if (this.speechAt === 0 && now - this.startedAt > 20000) await this.cycle(false); // keep files short
    else if (quiet && !spoke) await this.cycle(false); // a click, not speech
  }

  private async cycle(send: boolean) {
    const rec = this.rec;
    if (!rec) return;
    this.busy = true;
    this.rec = null;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    const durationS = (Date.now() - this.startedAt) / 1000;
    let uri: string | null = null;
    try {
      await rec.stop();
      uri = rec.uri;
    } catch {}
    try { rec.release(); } catch {}
    try {
      if (send && uri) {
        const audioB64 = await FileSystem.readAsStringAsync(uri, { encoding: "base64" });
        this.onUtterance({ audioB64, durationS });
      }
    } catch {}
    this.busy = false;
    await this.begin();
  }
}

/** Plays replies one at a time: tts_url when present, otherwise expo-speech. */
export class SpeechPlayer {
  private queue: { say: string; url: string | null }[] = [];
  private busy = false;
  private player: AudioPlayer | null = null;
  private epoch = 0;

  constructor(private onState: (playing: boolean) => void | Promise<void>) {}

  /** Only the newest pending line is kept: the brain can produce lines faster than they can be spoken. */
  enqueue(say: string, url: string | null) {
    if (!say.trim()) return;
    this.queue = [{ say, url }];
    void this.pump();
  }

  /** Barge-in: stop now and drop everything queued. */
  stop() {
    this.epoch++;
    this.queue = [];
    const p = this.player;
    this.player = null;
    try { p?.pause(); } catch {}
    try { p?.remove(); } catch {}
    void Speech.stop();
    if (this.busy) {
      this.busy = false;
      this.onState(false);
    }
  }

  private async pump() {
    if (this.busy) return;
    const next = this.queue.shift();
    if (!next) return;
    this.busy = true;
    const epoch = this.epoch;
    try { await this.onState(true); } catch {}
    if (epoch !== this.epoch) return;
    try {
      await this.playOne(next.say, next.url);
    } catch {}
    if (epoch !== this.epoch) return; // stopped meanwhile
    this.busy = false;
    this.onState(false);
    void this.pump();
  }

  private playOne(say: string, url: string | null) {
    return new Promise<void>((resolve) => {
      const speak = () => Speech.speak(say, { onDone: () => resolve(), onStopped: () => resolve(), onError: () => resolve() });
      if (!url) return speak();
      let settled = false;
      const p = createAudioPlayer({ uri: absUrl(url) });
      this.player = p;
      const finish = (fallback: boolean) => {
        if (settled) return;
        settled = true;
        try { sub.remove(); } catch {}
        try { p.remove(); } catch {}
        if (this.player === p) this.player = null;
        if (fallback) speak();
        else resolve();
      };
      const sub = p.addListener("playbackStatusUpdate", (s) => {
        if (s.didJustFinish) finish(false);
      });
      // 404 or a stalled stream: speak the text locally instead.
      setTimeout(() => {
        if (settled) return;
        try {
          if (!p.playing && p.currentTime === 0) finish(true);
        } catch {
          finish(false); // player was released by stop()
        }
      }, 4000);
      try { p.play(); } catch { finish(true); }
    });
  }
}
