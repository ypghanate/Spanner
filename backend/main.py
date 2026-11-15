"""Spanner backend: one FastAPI service that wires every stage together.

Fast loop  (inline, every frame):   preprocess -> detect -> FrameResponse (+ queued brain events)
Slow loop  (background, keyframes): understand -> Pass 1 -> safety -> Pass 2 -> TTS -> queued event
Live socket (WS /session/{id}/live): frames in -> frame_result out; voice clips or streamed PCM in ->
           STT -> Pass 1 -> safety -> Pass 2 -> response (+ tts_url or streamed MP3); every brain
           result is pushed the moment it is ready
Fallbacks  (HTTP): POST /frame (events ride the reply), POST /audio (m4a clip -> TTS url)

Stages come from mocks.load_stages(): the real function when USE_MOCKS=0 and the module exists,
otherwise a mock, so every route works on fixtures with no API keys.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import inspect
import json
import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from . import mocks
from . import perception as perception_mod
from .brain import pass1 as pass1_mod
from .config import get_settings
from .contracts import (
    AudioResponse, BrainResponse, Detection, FrameQuality, FrameResponse, LiveAudio, LiveBargeIn,
    LiveError, LiveEvent, LiveFinal, LiveFrame, LiveFrameResult, LivePartial, LivePlayback,
    LiveResponse, LiveStart, LiveStop, LiveTtsEnd, LiveTtsStart, LiveUtterance, SessionCreate,
    SessionCreated, SessionSummary, Transcript,
)
from .session import FrameSnap, Session, SessionManager, UnknownSession
from .voice import stt as stt_mod
from .voice import tts as tts_mod

log = logging.getLogger("spanner")

MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
APOLOGY_NO_SPEECH = "Sorry, I didn't catch that."
APOLOGY_BRAIN = "Give me a second, say that again?"


class BrainError(RuntimeError):
    pass


@dataclass
class KeyframeJob:
    frame_id: str
    jpeg: bytes
    detections: list[Detection]


# --- helpers ----------------------------------------------------------------

async def call_stage(fn, *args, timeout: float, session: Session, name: str):
    """Run one pipeline stage (sync or async) with a timeout; record its timing on the session."""
    t0 = time.perf_counter()
    ok = False
    try:
        if asyncio.iscoroutinefunction(fn):
            result = await asyncio.wait_for(fn(*args), timeout)
        else:
            result = await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout)
            if inspect.isawaitable(result):
                result = await result
        ok = True
        return result
    finally:
        session.record_timing(name, (time.perf_counter() - t0) * 1000, ok)


def parse_meta(meta: str | None) -> dict:
    if not meta:
        return {}
    try:
        data = json.loads(meta)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def keyterms_for(session: Session) -> list[str]:
    """Part names the speech recogniser should expect."""
    terms: list[str] = []
    step = session.current_step()
    if step:
        terms += step.parts
    if session.observations:
        terms += session.observations[-1].visible_parts
    if session.plan:
        terms += session.plan.tools_needed
    seen: list[str] = []
    for t in terms:
        if t not in seen:
            seen.append(t)
    return seen


_WORD = re.compile(r"[a-z0-9']+")


def looks_like_echo(heard: str, spoken: str) -> bool:
    """True when `heard` is mostly words we are saying ourselves (the mic hearing the speaker)."""
    h = _WORD.findall(heard.lower())
    s = set(_WORD.findall(spoken.lower()))
    if not h or not s:
        return False
    return sum(w in s for w in h) / len(h) >= 0.7


async def _run_warm(name: str, warm) -> None:
    t0 = time.perf_counter()
    try:
        await asyncio.to_thread(warm)
        log.info("%s warmed up in %.1fs", name, time.perf_counter() - t0)
    except Exception:
        log.warning("%s warm-up failed; the first frame will be slow", name, exc_info=True)


def start_warmups(orch: "Orchestrator") -> list[asyncio.Task]:
    """A real vision stage may expose warm() (load weights, run a dummy frame). Run those in the
    background at startup so the first live frame doesn't blow its timeout on model loading."""
    tasks = []
    for name in ("preprocess", "detect", "understand"):
        if not orch.stages.real.get(name):
            continue
        module = sys.modules.get(getattr(getattr(orch.stages, name), "__module__", ""))
        warm = getattr(module, "warm", None)
        if callable(warm):
            tasks.append(asyncio.create_task(_run_warm(name, warm)))
    return tasks


def tts_url_for(text: str) -> str | None:
    name = tts_mod.start(text)
    return f"/tts/{name}" if name else None


# --- orchestrator -----------------------------------------------------------

class Orchestrator:
    def __init__(self) -> None:
        self.stages = mocks.get_stages()
        self.sessions = SessionManager(slow_handler=self.slow_loop)
        self.parts_poll_s = 2.0          # how often a Linkup parts job is polled
        self.parts_timeout_s = 180.0     # give up on a parts job after this long

    # store writes never break the request
    def write(self, table: str, row: dict) -> None:
        try:
            self.stages.store.write(table, row)
        except Exception:
            log.warning("store.write(%s) failed", table, exc_info=True)

    # -- sessions -------------------------------------------------------------

    def create_session(self, device: str) -> Session:
        s = self.sessions.create(device)
        self.write("sessions", {
            "session_id": s.id, "started_at": s.started_at, "ended_at": None, "device": device,
            "object": None, "fault": None, "status": "active", "steps_total": 0, "steps_done": 0})
        return s

    async def end_session(self, session_id: str) -> SessionSummary:
        s = await self.sessions.end(session_id)
        if s.live_conn is not None:
            await s.live_conn.close()
        plan = s.plan
        summary = SessionSummary(
            session_id=s.id, object=plan.object if plan else None, fault=plan.fault if plan else None,
            steps_total=len(plan.steps) if plan else 0, steps_done=s.steps_done,
            duration_s=round((s.ended_at or time.time()) - s.started_at, 1),
            sources=plan.sources if plan else [])
        self.write("sessions", {
            "session_id": s.id, "started_at": s.started_at, "ended_at": s.ended_at, "device": s.device,
            "object": summary.object, "fault": summary.fault,
            "status": "done" if s.phase == "done" else "ended",
            "steps_total": summary.steps_total, "steps_done": summary.steps_done})
        try:
            self.stages.store.flush()
        except Exception:
            log.warning("store.flush failed", exc_info=True)
        return summary

    # -- fast loop ------------------------------------------------------------

    async def handle_frame(self, session: Session, jpeg: bytes, meta: dict,
                           drain_events: bool = True) -> FrameResponse:
        st = self.stages
        session.frame_seq += 1
        frame_id = f"{session.id}-{session.frame_seq:05d}"
        ts = float(meta.get("ts") or time.time())

        try:
            jpeg, quality = await call_stage(st.preprocess, jpeg, session.id, timeout=3.0,
                                             session=session, name="preprocess")
        except Exception:
            log.warning("preprocess failed for %s", frame_id, exc_info=True)
            quality = FrameQuality(blurry=False, too_dark=False, is_keyframe=False,
                                   sharpness=0.0, brightness=0.0)

        usable = not (quality.blurry or quality.too_dark)
        detections: list[Detection] = []
        if usable:
            try:
                detections = await call_stage(st.detect, jpeg, session.id, timeout=2.0,
                                              session=session, name="detect")
            except Exception:
                log.warning("detect failed for %s", frame_id, exc_info=True)

        session.last_frame_jpeg, session.last_frame_id, session.last_detections = jpeg, frame_id, detections
        if usable:        # blurry or dark frames are useless to Claude: only sharp ones go in the ring
            session.recent_frames.append(FrameSnap(frame_id, time.time(), jpeg, detections))

        keyframe = quality.is_keyframe and usable
        image_path = ""
        if keyframe:
            kdir = get_settings().media_cache_dir / "keyframes"
            path = kdir / f"{frame_id}.jpg"
            try:
                await asyncio.to_thread(lambda: (kdir.mkdir(parents=True, exist_ok=True), path.write_bytes(jpeg)))
                image_path = str(path)
            except OSError:
                log.warning("could not save keyframe %s", frame_id, exc_info=True)
            session.offer_keyframe(KeyframeJob(frame_id, jpeg, detections))

        self.write("frames", {
            "session_id": session.id, "frame_id": frame_id, "ts": ts, "is_keyframe": keyframe,
            "sharpness": quality.sharpness, "brightness": quality.brightness, "image_path": image_path})
        for d in detections:
            self.write("detections", {"frame_id": frame_id, "label": d.label, "score": d.score,
                                      "x": d.box.x, "y": d.box.y, "w": d.box.w, "h": d.box.h})

        return FrameResponse(frame_id=frame_id, quality=quality, detections=detections,
                             events=session.drain_events() if drain_events else [])

    # -- the brain (shared by slow loop, /audio and live voice) ----------------

    async def run_brain(self, session: Session, transcript: Transcript | None,
                        speech_end_ts: float | None = None) -> tuple[Any, BrainResponse]:
        """Pass 1 -> safety -> Pass 2. Caller must hold session.brain_lock.

        `speech_end_ts` is when the spoken message arrived (server clock), or None when nobody spoke.
        On a spoken turn Claude is shown the recent camera frames, a close-up and the YOLO tracks along
        with the words (backend/perception.py); on a keyframe-only turn just the detector list."""
        st, s = self.stages, get_settings()
        session.last_step_check = None                          # never let Pass 2 read a stale check
        session.perception = session.last_perception_summary = None
        perception = None
        try:
            perception = await perception_mod.gather(
                session, speech_end_ts if transcript is not None else None,
                transcript.duration_s if transcript is not None else None)
        except Exception:
            log.warning("could not gather the camera view; Claude gets the text only this turn", exc_info=True)
        session.perception = perception                         # Pass 2 reads this to answer questions
        session.last_perception_summary = {**perception.summary(), "at": time.time()} if perception else None
        inp = pass1_mod.build_input(session, transcript, perception)
        check_task = self._start_step_check(session, transcript)  # runs alongside Pass 1
        try:
            report = await call_stage(st.pass1, inp, session.id, st.store,
                                      (perception.images or None) if perception else None,
                                      timeout=s.claude_timeout_s + 2, session=session, name="pass1")
        except Exception as e:
            if check_task is not None:
                check_task.cancel()
            raise BrainError(f"pass1 failed: {e}") from e
        if check_task is not None:
            try:
                session.last_step_check = await check_task
            except Exception:
                log.warning("step tracker failed; Pass 2 runs without a step check", exc_info=True)
        session.last_report = report
        self.write("situation_reports", {"session_id": session.id, "ts": time.time(),
                                         "json": report.model_dump_json()})

        response = None
        try:
            response = await call_stage(st.safety, report, session, timeout=2.0,
                                        session=session, name="safety")
        except Exception:
            log.warning("safety check failed; continuing to pass2", exc_info=True)
        if response is None:
            try:
                response = await call_stage(st.pass2, report, session, timeout=s.claude_timeout_s,
                                            session=session, name="pass2")
            except Exception as e:
                raise BrainError(f"pass2 failed: {e}") from e

        session.apply(response)
        self.write("brain_responses", {
            "session_id": session.id, "ts": time.time(), "phase": response.phase,
            "current_step": response.current_step, "step_status": response.step_status,
            "say": response.say, "alert_json": response.alert.model_dump_json() if response.alert else None})
        self._watch_parts(session)
        return report, response

    # -- knowledge-layer hooks --------------------------------------------------

    def _start_step_check(self, session: Session, transcript: Transcript | None) -> asyncio.Task | None:
        """Is the current step done, or is the user making a mistake? Only while guiding, with a step
        and something the camera has seen. 3 s budget; a failure never blocks the brain."""
        step = session.current_step()
        if session.phase not in ("guiding", "verifying") or step is None or not session.observations:
            return None
        return asyncio.create_task(call_stage(
            self.stages.track_step, step, session.observations[-1], transcript.text if transcript else "",
            session.id, session.brain_state.get("repair_id"),
            timeout=3.0, session=session, name="track_step"))

    def _watch_parts(self, session: Session) -> None:
        """Pass 2 starts a Linkup parts job and leaves its id in session.brain_state["parts_job"];
        we poll it and push the finished card to the phone."""
        job = session.brain_state.get("parts_job")
        if not job or job in session.parts_jobs:
            return
        session.parts_jobs.add(job)
        session.spawn(f"parts:{job}", self._poll_parts(session, job), timeout=self.parts_timeout_s + 10)

    async def _poll_parts(self, session: Session, job: str) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + self.parts_timeout_s
        while True:
            try:
                result = await self.stages.parts_card(job)
            except Exception:
                log.warning("parts job %s: polling failed", job, exc_info=True)
                return
            status = result.get("status")
            if status == "ready":
                session.queue_event(BrainResponse(phase=session.phase, parts_card=result["card"]))
                return
            if status != "researching":
                log.warning("parts job %s ended as %s: %s", job, status, result.get("error", ""))
                return
            if loop.time() >= end:
                log.warning("parts job %s still running after %.0fs; giving up", job, self.parts_timeout_s)
                return
            await asyncio.sleep(self.parts_poll_s)

    # -- slow loop ------------------------------------------------------------

    async def slow_loop(self, session: Session, job: KeyframeJob) -> None:
        st, s = self.stages, get_settings()
        step = session.current_step()
        expected = step.expected_visual_state if step else None
        try:
            obs = await call_stage(st.understand, job.jpeg, job.frame_id, job.detections, expected,
                                   timeout=s.vision_timeout_s, session=session, name="understand")
        except Exception:
            log.warning("understand failed for %s; skipping slow loop run", job.frame_id, exc_info=True)
            return
        session.observations.append(obs)
        session.last_observation_at = time.time()
        self.write("observations", {"frame_id": job.frame_id, "ts": time.time(), "json": obs.model_dump_json()})

        if session.voice_waiting:
            return          # the voice request is about to use this observation

        async with session.brain_lock:
            try:
                _, response = await self.run_brain(session, None)
            except BrainError:
                log.warning("slow loop brain failed for %s", job.frame_id, exc_info=True)
                return
        if response.say:
            response.tts_url = tts_url_for(response.say)
        session.queue_event(response)

    # -- /audio fallback ------------------------------------------------------

    async def handle_audio(self, session: Session, audio: bytes, filename: str, meta: dict) -> AudioResponse:
        s = get_settings()
        arrived = time.time()
        session.voice_waiting = True
        try:
            transcript: Transcript | None = None
            try:
                transcript = await call_stage(
                    self.stages.transcribe, audio, filename, keyterms_for(session), meta.get("duration_s"),
                    timeout=s.stt_timeout_s + 1, session=session, name="stt")
            except Exception:
                log.warning("speech-to-text failed", exc_info=True)

            if transcript is None or not transcript.text.strip():
                empty = Transcript(text="", language="en", duration_s=0.0)
                response = BrainResponse(phase=session.phase, say=APOLOGY_NO_SPEECH)
                return AudioResponse(transcript=empty, response=response, tts_url=tts_url_for(response.say))

            self.write("transcripts", {"session_id": session.id, "ts": time.time(),
                                       "text": transcript.text, "duration_s": transcript.duration_s})
            async with session.brain_lock:
                try:
                    _, response = await self.run_brain(session, transcript, speech_end_ts=arrived)
                except BrainError:
                    log.warning("brain failed on /audio", exc_info=True)
                    response = BrainResponse(phase=session.phase, say=APOLOGY_BRAIN)
            return AudioResponse(transcript=transcript, response=response, tts_url=tts_url_for(response.say))
        finally:
            session.voice_waiting = False

    # -- debug ----------------------------------------------------------------

    def debug_data(self, session: Session | None) -> dict:
        s = get_settings()
        base = {"stages": self.stages.describe(), "pass1_backend": s.pass1_backend,
                "use_mocks": s.use_mocks, "tts": tts_mod.available(), "stt": stt_mod.stt_available()}
        if session is None:
            return {**base, "session": None}
        st = session.state_dict()
        return {**base, "session": session.id, "phase": session.phase,
                "frame_id": session.last_frame_id,
                "detections": [d.model_dump() for d in session.last_detections],
                "observation": st["observations"][-1] if st["observations"] else None,
                "report": st["last_report"], "response": st["last_response"],
                "perception": st["last_perception"],
                "timings": st["timings"], "slow_busy": st["slow_busy"],
                "voice_waiting": st["voice_waiting"], "live": session.live_conn is not None}


# --- live session websocket -------------------------------------------------

class LiveConnection:
    """One phone <-> server socket for a session: camera frames in, voice in, and every brain
    result pushed back the moment it is ready (see CONTRACTS.md for the protocol)."""

    ECHO_TAIL_S = 2.0       # the text echo guard stays on this long after our own speech
    MUTE_TAIL_S = 0.7       # mic audio / clips are dropped this long after the phone says playback ended

    def __init__(self, orch: Orchestrator, ws: WebSocket, session: Session) -> None:
        self.orch, self.ws, self.session = orch, ws, session
        self.transcriber = stt_mod.make_live_transcriber()
        self.started = False                   # realtime STT stream opened (PCM path only)
        self.keyterms: list[str] = []
        self.tts_mode = "url"                  # "url": replies carry tts_url; "stream": MP3 on this socket
        self.queue: asyncio.Queue[tuple[str, float, int, float]] = asyncio.Queue()
        self.pending = 0                       # utterances being transcribed or answered
        self.seq = 0                           # bumps whenever the user starts or finishes an utterance
        self.speaking_task: asyncio.Task | None = None
        self.speaking = False                  # the server is streaming TTS (stream mode)
        self.playback = False                  # the phone says it is playing a reply
        self.playback_ended_at = float("-inf")
        self.spoken_text = ""
        self.spoken_at = float("-inf")
        self.closed = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self._out: asyncio.Queue[BrainResponse] = asyncio.Queue()    # pushed events, in order
        self._pending_frame: tuple[bytes, float | None] | None = None
        self._frame_task: asyncio.Task | None = None
        self._clip_tasks: set[asyncio.Task] = set()
        self._hanging_up = False
        self._hangup_task: asyncio.Task | None = None

    # -- outbound -------------------------------------------------------------

    async def send(self, msg) -> bool:
        if self.closed:
            return False
        try:
            await self.ws.send_text(msg.model_dump_json())
            return True
        except Exception:
            self.closed = True
            return False

    def push(self, response: BrainResponse) -> bool:
        """Called by Session.queue_event (from any thread). False = not accepted, so the session
        keeps the event queued for the next frame reply or connection."""
        if self.closed or self.loop is None:
            return False
        self.loop.call_soon_threadsafe(self._out.put_nowait, response)
        return True

    async def _pump(self) -> None:
        while True:
            r = await self._out.get()
            if not await self.send(LiveEvent(response=r)):
                self.session.events.append(r)           # socket died mid-send: don't lose it

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        with contextlib.suppress(Exception):
            await self.ws.close(code=code)

    # -- replies and speech ---------------------------------------------------

    @property
    def muted(self) -> bool:
        """The phone is playing audio (or just stopped): its mic is muted, so drop what arrives."""
        return self.playback or (time.monotonic() - self.playback_ended_at) < self.MUTE_TAIL_S

    def _echo(self, heard: str) -> bool:
        recent = (self.speaking or self.playback
                  or (time.monotonic() - self.spoken_at) < self.ECHO_TAIL_S)
        return recent and looks_like_echo(heard, self.spoken_text)

    async def _send_reply(self, response: BrainResponse, speak: bool) -> None:
        """Send the answer to something the user said, and speak it unless it is stale."""
        speak = speak and bool(response.say)
        # The step list stays on the server (session.plan tracks it); the phone never gets it.
        if response.plan is not None:
            response = response.model_copy(update={"plan": None})
        if speak and self.tts_mode == "url":
            response.tts_url = tts_url_for(response.say)        # None when TTS is off: the phone speaks `say`
            self.spoken_text, self.spoken_at = response.say, time.monotonic()
        await self.send(LiveResponse(response=response, speak=speak or not response.say))
        if speak and self.tts_mode == "stream":
            self._start_speaking(response.say)
        if self.session.brain_state.get("end_call") and not self._hanging_up:
            # every step is done: let the closing line finish playing, then end the call
            self._hanging_up = True
            self._hangup_task = asyncio.create_task(self._hang_up(spoke=speak))

    HANGUP_WAIT_S = 20.0    # longest we wait for the closing line to finish playing

    async def _hang_up(self, spoke: bool) -> None:
        deadline = time.monotonic() + self.HANGUP_WAIT_S
        try:
            if spoke and self.tts_mode == "stream":
                if self.speaking_task is not None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await asyncio.wait_for(asyncio.shield(self.speaking_task), self.HANGUP_WAIT_S)
            elif spoke:
                # url mode: the phone plays the reply and reports playback start / end
                sent_at = time.monotonic()
                while time.monotonic() < deadline and not self.closed:
                    if not self.playback and self.playback_ended_at > sent_at:
                        break
                    await asyncio.sleep(0.2)
            await asyncio.sleep(0.5)
            if not self.closed:
                await self.orch.end_session(self.session.id)
        except Exception:
            log.warning("could not end the call after the last step", exc_info=True)

    def _start_speaking(self, text: str) -> None:
        if not tts_mod.available():
            return                         # no tts_start follows: the app speaks `say` itself
        self.spoken_text = text
        self.speaking_task = asyncio.create_task(self._speak(text))

    async def _speak(self, text: str) -> None:
        self.speaking = True
        started = False
        try:
            async for chunk in tts_mod.stream(text):
                if self.closed:
                    return
                if not started:
                    await self.send(LiveTtsStart())
                    started = True
                await self.ws.send_bytes(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("live TTS failed: %s", e)
            await self.send(LiveError(code="tts_error", message="speech output failed"))
        finally:
            self.speaking = False
            self.spoken_at = time.monotonic()
            if started:
                await self.send(LiveTtsEnd())

    async def barge_in(self) -> None:
        self.seq += 1
        task = self.speaking_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # -- utterance bookkeeping ------------------------------------------------

    def _begin_pending(self) -> None:
        self.pending += 1
        self.session.voice_waiting = True

    def _end_pending(self) -> None:
        self.pending -= 1
        self.session.voice_waiting = self.pending > 0

    async def _accept_utterance(self, text: str, duration: float, already_pending: bool = False,
                                arrived: float | None = None) -> None:
        """`arrived` is when the spoken message reached the server (the camera view is matched to it)."""
        self.seq += 1
        await self.send(LiveFinal(text=text))
        if not already_pending:
            self._begin_pending()
        self.queue.put_nowait((text, duration, self.seq, time.time() if arrived is None else arrived))

    async def _worker(self) -> None:
        while True:
            text, duration, seq, arrived = await self.queue.get()
            try:
                await self._handle_utterance(text, duration, seq, arrived)
            except Exception:
                log.exception("live utterance failed")
            finally:
                self._end_pending()

    async def _handle_utterance(self, text: str, duration: float, seq: int, arrived: float) -> None:
        session = self.session
        transcript = Transcript(text=text, language="en", duration_s=duration)
        self.orch.write("transcripts", {"session_id": session.id, "ts": time.time(),
                                        "text": text, "duration_s": duration, "source": "live"})
        async with session.brain_lock:
            try:
                _, response = await self.orch.run_brain(session, transcript, speech_end_ts=arrived)
            except BrainError:
                log.warning("brain failed on live utterance", exc_info=True)
                response = BrainResponse(phase=session.phase, say=APOLOGY_BRAIN)
        # If the user began a new utterance while the brain ran, the reply is stale: show it, don't say it.
        await self._send_reply(response, speak=(seq == self.seq))

    # -- inbound: voice -------------------------------------------------------

    async def _ensure_started(self) -> None:
        if self.started:
            return
        self.started = True
        try:
            await self.transcriber.start([*self.keyterms, *keyterms_for(self.session)])
        except stt_mod.SttError as e:
            self.started = False
            await self.send(LiveError(code="stt_unavailable", message=str(e)))

    async def _on_pcm(self, pcm: bytes) -> None:
        if self.muted:
            return                          # the phone is playing a reply: this is its own speaker
        await self._ensure_started()
        await self.transcriber.send(pcm)

    async def _results_loop(self) -> None:
        async for r in self.transcriber.results():
            if r.kind == "partial":
                if self._echo(r.text):
                    continue
                if self.speaking:
                    await self.barge_in()
                else:
                    self.seq += 1
                await self.send(LivePartial(text=r.text))
            elif r.kind == "final":
                if self._echo(r.text):
                    continue
                if self.speaking:
                    await self.barge_in()
                await self._accept_utterance(r.text, r.duration_s)
            else:
                await self.send(LiveError(code="stt_error", message=r.message))

    async def _on_utterance(self, msg: LiveUtterance) -> None:
        if self.muted:
            return
        try:
            audio = base64.b64decode(msg.audio_b64, validate=True)
        except (ValueError, binascii.Error):
            await self.send(LiveError(code="bad_message", message="audio_b64 is not valid base64"))
            return
        task = asyncio.create_task(self._process_clip(audio, msg, time.time()))
        self._clip_tasks.add(task)
        task.add_done_callback(self._clip_tasks.discard)

    async def _process_clip(self, audio: bytes, msg: LiveUtterance, arrived: float) -> None:
        s = get_settings()
        self._begin_pending()                       # voice_waiting covers transcription too
        enqueued = False
        try:
            transcript: Transcript | None = None
            try:
                transcript = await call_stage(
                    self.orch.stages.transcribe, audio, f"utterance.{msg.format}",
                    [*self.keyterms, *keyterms_for(self.session)], msg.duration_s,
                    timeout=s.stt_timeout_s + 1, session=self.session, name="stt")
            except Exception:
                log.warning("speech-to-text failed on a clip", exc_info=True)
            text = transcript.text.strip() if transcript else ""
            if not text:
                await self._send_reply(BrainResponse(phase=self.session.phase, say=APOLOGY_NO_SPEECH), speak=True)
                return
            await self._accept_utterance(text, transcript.duration_s, already_pending=True, arrived=arrived)
            enqueued = True                         # the worker ends the pending count
        finally:
            if not enqueued:
                self._end_pending()

    # -- inbound: frames ------------------------------------------------------

    async def _on_frame(self, msg: LiveFrame) -> None:
        try:
            jpeg = base64.b64decode(msg.jpeg_b64, validate=True)
        except (ValueError, binascii.Error):
            await self.send(LiveError(code="bad_frame", message="jpeg_b64 is not valid base64"))
            return
        if not jpeg or len(jpeg) > MAX_FRAME_BYTES:
            await self.send(LiveError(code="bad_frame", message="empty or oversized frame"))
            return
        self._pending_frame = (jpeg, msg.ts)        # newest wins: an older waiting frame is replaced
        if self._frame_task is None or self._frame_task.done():
            self._frame_task = asyncio.create_task(self._frame_worker())

    async def _frame_worker(self) -> None:
        while self._pending_frame is not None and not self.closed:
            jpeg, ts = self._pending_frame
            self._pending_frame = None
            try:
                result = await self.orch.handle_frame(self.session, jpeg, {"ts": ts}, drain_events=False)
            except Exception:
                log.exception("frame failed")
                continue
            await self.send(LiveFrameResult(result=result))
            for ev in self.session.drain_events():  # anything queued while no socket was connected
                self._out.put_nowait(ev)

    # -- main loop ------------------------------------------------------------

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        for ev in self.session.drain_events():      # backlog from before this connection
            self._out.put_nowait(ev)
        results = asyncio.create_task(self._results_loop())
        worker = asyncio.create_task(self._worker())
        pump = asyncio.create_task(self._pump())
        try:
            while not self.closed:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await self._on_pcm(msg["bytes"])
                elif msg.get("text") is not None:
                    if not await self._on_message(msg["text"]):
                        break
        finally:
            self.closed = True
            tasks = [results, worker, pump, self.speaking_task, self._frame_task, *self._clip_tasks]
            tasks = [t for t in tasks if t is not None]
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            while not self._out.empty():            # unsent events go back to the session's queue
                self.session.events.append(self._out.get_nowait())
            await self.transcriber.close()
            self.session.voice_waiting = False
            if self.session.live_conn is self:
                self.session.live_conn = None

    async def _on_message(self, text: str) -> bool:
        """Handle one JSON message from the phone. False ends the connection."""
        try:
            data = json.loads(text)
            kind = data.get("type")
        except (ValueError, AttributeError):
            await self.send(LiveError(code="bad_message", message="expected a JSON message"))
            return True
        try:
            if kind == "frame":
                await self._on_frame(LiveFrame.model_validate(data))
            elif kind == "utterance":
                await self._on_utterance(LiveUtterance.model_validate(data))
            elif kind == "audio":
                try:
                    pcm = base64.b64decode(LiveAudio.model_validate(data).pcm_b64, validate=True)
                except (ValueError, binascii.Error):
                    await self.send(LiveError(code="bad_message", message="pcm_b64 is not valid base64"))
                    return True
                await self._on_pcm(pcm)
            elif kind == "playback":
                p = LivePlayback.model_validate(data)
                now = time.monotonic()
                if p.state == "start":
                    self.playback = True
                else:
                    self.playback, self.playback_ended_at, self.spoken_at = False, now, now
            elif kind == "start":
                s = LiveStart.model_validate(data)
                self.keyterms, self.tts_mode = s.keyterms, s.tts
            elif kind == "barge_in":
                LiveBargeIn.model_validate(data)
                await self.barge_in()
            elif kind == "stop":
                LiveStop.model_validate(data)
                return False
            else:
                await self.send(LiveError(code="bad_message", message=f"unknown message type {kind!r}"))
        except ValidationError as e:
            await self.send(LiveError(code="bad_message", message=f"bad {kind!r} message: {e.errors()[0]['msg']}"))
        return True


# --- debug page -------------------------------------------------------------

DEBUG_HTML = """<!doctype html><meta charset=utf-8><title>Spanner debug</title>
<style>
 body{background:#05080f;color:#cfe;font:13px/1.4 ui-monospace,Consolas,monospace;margin:0;padding:12px}
 h2{color:#00e5ff;margin:0 0 6px;font-size:14px} .row{display:flex;gap:12px;flex-wrap:wrap}
 .card{background:#0b1220;border:1px solid #16314a;border-radius:6px;padding:10px;min-width:300px;flex:1}
 canvas{max-width:100%;border:1px solid #16314a;background:#000} pre{white-space:pre-wrap;margin:0}
 .real{color:#3dffa8}.mock{color:#ffc83d} td{padding:1px 8px 1px 0} .bad{color:#ff3d5a}
 .pics{display:flex;gap:8px;flex-wrap:wrap} .pics figure{margin:0;max-width:230px} .pics img{max-width:100%;border:1px solid #16314a}
 figcaption{font-size:11px;color:#8ab}
</style>
<h2>Spanner pipeline <span id=sid></span> <span id=phase></span></h2>
<div class=row>
 <div class=card style="flex:2"><h2>Latest frame + boxes</h2><canvas id=cv width=768 height=576></canvas><div id=fid></div></div>
 <div class=card><h2>Stages</h2><div id=stages></div><h2 style="margin-top:10px">Timings (ms)</h2><table id=timings></table></div>
</div>
<div class=row>
 <div class=card><h2>Observation</h2><pre id=obs></pre></div>
 <div class=card><h2>Situation report</h2><pre id=rep></pre></div>
 <div class=card><h2>Brain response</h2><pre id=resp></pre></div>
</div>
<div class=row>
 <div class=card><h2>What Claude saw on the last spoken turn</h2><div id=percept class=pics>-</div><div id=perceptmeta></div></div>
</div>
<script>
const q=new URLSearchParams(location.search).get('session');
const cv=document.getElementById('cv'),ctx=cv.getContext('2d');let lastFrame=null,img=new Image();
function draw(dets){ctx.clearRect(0,0,cv.width,cv.height);if(img.complete&&img.naturalWidth){cv.width=img.naturalWidth;cv.height=img.naturalHeight;ctx.drawImage(img,0,0)}
 ctx.lineWidth=2;ctx.font='14px monospace';for(const d of dets||[]){const b=d.box,x=b.x*cv.width,y=b.y*cv.height,w=b.w*cv.width,h=b.h*cv.height;
 ctx.strokeStyle='#00e5ff';ctx.strokeRect(x,y,w,h);ctx.fillStyle='#00e5ff';ctx.fillText(d.label+' '+d.score.toFixed(2),x+2,y>14?y-4:y+14)}}
img.onload=()=>draw(window._dets);
async function tick(){try{const r=await fetch('/debug/data'+(q?'?session='+q:''));const d=await r.json();
 document.getElementById('sid').textContent=d.session||'(no session yet)';document.getElementById('phase').textContent=d.phase||'';
 document.getElementById('stages').innerHTML=Object.entries(d.stages).map(([k,v])=>'<div>'+k+': <span class='+v+'>'+v+'</span></div>').join('')+
  '<div>pass1 backend: '+d.pass1_backend+'</div><div>tts: '+d.tts+' / stt: '+d.stt+'</div>'+(d.live?'<div>live voice: connected</div>':'');
 window._dets=d.detections;if(d.frame_id&&d.frame_id!==lastFrame){lastFrame=d.frame_id;img.src='/debug/frame/'+d.frame_id+'.jpg?session='+d.session}else draw(d.detections);
 document.getElementById('fid').textContent=d.frame_id||'';
 const show=(id,o)=>document.getElementById(id).textContent=o?JSON.stringify(o,null,2):'-';show('obs',d.observation);show('rep',d.report);show('resp',d.response);
 const pc=d.perception;document.getElementById('percept').innerHTML=pc&&pc.image_notes.length?pc.image_notes.map((n,i)=>'<figure><img src="/debug/perception/'+i+'.jpg?session='+d.session+'&v='+pc.at+'"><figcaption>'+(i+1)+'. '+n+'</figcaption></figure>').join(''):'-';
 document.getElementById('perceptmeta').textContent=pc?('YOLO tracks: '+pc.detections+' | text observation age: '+(pc.observation_age_s==null?'fresh':pc.observation_age_s+' s')):'no camera view on the last turn';
 document.getElementById('timings').innerHTML=(d.timings||[]).slice(-12).reverse().map(t=>'<tr class='+(t.ok?'':'bad')+'><td>'+t.stage+'</td><td>'+t.ms+'</td></tr>').join('');
}catch(e){}}
tick();setInterval(tick,1000);
</script>"""


# --- app --------------------------------------------------------------------

def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        mocks.reset_stages()
        app.state.orch = Orchestrator()
        log.info("stages: %s", app.state.orch.stages.describe())
        app.state.warm_tasks = start_warmups(app.state.orch)      # keep references so they aren't collected
        yield
        orch: Orchestrator = app.state.orch
        for s in orch.sessions.all():
            if s.live_conn is not None:
                await s.live_conn.close()
            await s.cancel_tasks()
        with contextlib.suppress(Exception):
            orch.stages.store.flush()
        from .brain import pass1_adaption
        for closer in (pass1_mod.close_client, pass1_adaption.close_client,
                       stt_mod.close_client, tts_mod.close_client):
            with contextlib.suppress(Exception):
                await closer()

    app = FastAPI(title="Spanner", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    from .coach import mount as mount_coach   # laptop camera coach + 3D hologram at /training/viewer/
    mount_coach(app)

    def orch_of(request_or_ws) -> Orchestrator:
        return request_or_ws.app.state.orch

    @app.exception_handler(UnknownSession)
    async def _unknown(_: Request, exc: UnknownSession):
        return JSONResponse({"detail": f"unknown session {exc.args[0]}"}, status_code=404)

    @app.get("/health")
    async def health(request: Request):
        o, s = orch_of(request), get_settings()
        return {"status": "ok", "stages": o.stages.describe(), "pass1_backend": s.pass1_backend,
                "use_mocks": s.use_mocks, "tts": tts_mod.available(), "stt": stt_mod.stt_available(),
                "sessions": len(o.sessions.all())}

    @app.post("/session", response_model=SessionCreated)
    async def create_session(request: Request, body: SessionCreate | None = None):
        s = orch_of(request).create_session((body or SessionCreate()).device)
        return SessionCreated(session_id=s.id)

    @app.post("/session/{session_id}/frame", response_model=FrameResponse)
    async def post_frame(request: Request, session_id: str, frame: UploadFile = File(...),
                         meta: str | None = Form(None)):
        o = orch_of(request)
        session = o.sessions.get(session_id)
        data = await frame.read()
        if not data:
            return JSONResponse({"detail": "empty frame"}, status_code=400)
        if len(data) > MAX_FRAME_BYTES:
            return JSONResponse({"detail": "frame too large"}, status_code=413)
        return await o.handle_frame(session, data, parse_meta(meta))

    @app.post("/session/{session_id}/audio", response_model=AudioResponse)
    async def post_audio(request: Request, session_id: str, audio: UploadFile = File(...),
                         meta: str | None = Form(None)):
        o = orch_of(request)
        session = o.sessions.get(session_id)
        data = await audio.read()
        if len(data) > MAX_AUDIO_BYTES:
            return JSONResponse({"detail": "audio too large"}, status_code=413)
        return await o.handle_audio(session, data, audio.filename or "audio.m4a", parse_meta(meta))

    @app.websocket("/session/{session_id}/live")
    async def live(ws: WebSocket, session_id: str):
        o = orch_of(ws)
        try:
            session = o.sessions.get(session_id)
        except UnknownSession:
            await ws.close(code=4404)
            return
        await ws.accept()
        conn = LiveConnection(o, ws, session)
        old = session.live_conn
        session.live_conn = conn
        if old is not None:
            await old.close(code=4000)       # one live connection per session
        await conn.run()

    @app.get("/tts/{filename}")
    async def get_tts(filename: str):
        stream = await tts_mod.progressive(filename)          # still being generated: send as it arrives
        if stream is not None:
            return StreamingResponse(stream, media_type="audio/mpeg")
        path = await tts_mod.wait_for(filename, timeout=1.0)  # already finished (or failed)
        if path is None:
            return JSONResponse({"detail": "speech not available"}, status_code=404)
        return FileResponse(path, media_type="audio/mpeg")

    @app.get("/media/{file_path:path}")
    async def get_media(file_path: str):
        base = get_settings().media_cache_dir.resolve()
        target = (base / file_path).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            return JSONResponse({"detail": "not found"}, status_code=404)
        types = {".glb": "model/gltf-binary", ".png": "image/png", ".mp4": "video/mp4",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".mp3": "audio/mpeg"}
        return FileResponse(target, media_type=types.get(target.suffix.lower()))

    @app.post("/session/{session_id}/end", response_model=SessionSummary)
    async def end_session(request: Request, session_id: str):
        return await orch_of(request).end_session(session_id)

    @app.get("/session/{session_id}/state")
    async def session_state(request: Request, session_id: str):
        data = orch_of(request).sessions.get(session_id).state_dict()
        return JSONResponse(json.loads(json.dumps(data, default=str)))

    @app.get("/debug", response_class=HTMLResponse)
    async def debug_page():
        return DEBUG_HTML

    @app.get("/debug/data")
    async def debug_data(request: Request, session: str | None = None):
        o = orch_of(request)
        try:
            s = o.sessions.get(session) if session else o.sessions.latest()
        except UnknownSession:
            s = None
        return JSONResponse(json.loads(json.dumps(o.debug_data(s), default=str)))

    @app.get("/debug/perception/{index}.jpg")
    async def debug_perception(request: Request, index: int, session: str | None = None):
        """One of the pictures Claude was shown on the last turn (0 = first image, in the order listed)."""
        o = orch_of(request)
        try:
            s = o.sessions.get(session) if session else o.sessions.latest()
        except UnknownSession:
            s = None
        p = s.perception if s is not None else None
        if p is None or not (0 <= index < len(p.images)):
            return Response(status_code=404)
        return Response(p.images[index], media_type="image/jpeg")

    @app.get("/debug/frame/{frame_id}.jpg")
    async def debug_frame(request: Request, frame_id: str, session: str | None = None):
        o = orch_of(request)
        try:
            s = o.sessions.get(session) if session else o.sessions.latest()
        except UnknownSession:
            s = None
        if s is None or s.last_frame_jpeg is None:
            return Response(status_code=404)
        return Response(s.last_frame_jpeg, media_type="image/jpeg")

    return app


app = create_app()