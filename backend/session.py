"""In-memory session state shared by the fast loop, slow loop and voice paths."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine

from .contracts import (
    BrainResponse, Detection, MediaUpdate, Phase, RepairPlan, RepairStep,
    SceneObservation, SituationReport,
)

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "live"

SlowHandler = Callable[["Session", Any], Awaitable[None]]


class UnknownSession(KeyError):
    pass


@dataclass
class Session:
    id: str
    device: str = "unknown"
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None

    phase: Phase = "identifying"
    plan: RepairPlan | None = None
    current_step_index: int | None = None          # 0-based index into plan.steps

    observations: deque[SceneObservation] = field(default_factory=lambda: deque(maxlen=3))
    last_report: SituationReport | None = None
    last_response: BrainResponse | None = None
    brain_state: dict = field(default_factory=dict)  # P4's private scratch space

    events: deque[BrainResponse] = field(default_factory=deque)
    pending_media: list[MediaUpdate] = field(default_factory=list)
    timings: deque[dict] = field(default_factory=lambda: deque(maxlen=50))

    last_frame_jpeg: bytes | None = None
    last_frame_id: str | None = None
    last_detections: list[Detection] = field(default_factory=list)
    frame_seq: int = 0
    steps_done: int = 0

    brain_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    voice_waiting: bool = False
    live_conn: Any = None            # the one live socket for this session, if any (has .push(response) -> bool)
    # Latest answer from the step tracker (backend.knowledge.track_step_async): {"step_status":
    # "in_progress" | "done" | "mistake" | None, "mistake": str, "status": ..., "backend": ...}.
    # Set by the orchestrator before Pass 2 runs, so Pass 2 can read it. None until first check.
    last_step_check: dict | None = None
    parts_jobs: set[str] = field(default_factory=set)   # Linkup job ids the orchestrator is already watching

    # slow-loop scheduling: one running, newest pending wins
    slow_handler: SlowHandler | None = None
    _slow_running: bool = False
    _pending_job: Any = None
    _tasks: set[asyncio.Task] = field(default_factory=set)

    # -- plan / step ----------------------------------------------------------

    def current_step(self) -> RepairStep | None:
        if self.plan is None or self.current_step_index is None:
            return None
        if 0 <= self.current_step_index < len(self.plan.steps):
            return self.plan.steps[self.current_step_index]
        return None

    def apply(self, r: BrainResponse) -> None:
        """Fold a BrainResponse into session state (phase, plan, current step)."""
        self.phase = r.phase
        if r.plan is not None:
            self.plan = r.plan
        if r.current_step is not None:
            # The contract's RepairStep.index is the number the user sees; find it in the plan,
            # falling back to treating current_step as a 0-based position.
            idx = None
            if self.plan is not None:
                for i, s in enumerate(self.plan.steps):
                    if s.index == r.current_step:
                        idx = i
                        break
            if idx is None:
                idx = r.current_step
            if idx != self.current_step_index and r.step_status == "done":
                self.steps_done = max(self.steps_done, idx + 1)
            self.current_step_index = idx
        if r.step_status == "done" and self.current_step_index is not None:
            self.steps_done = max(self.steps_done, self.current_step_index + 1)
        if r.phase == "done" and self.plan is not None:
            self.steps_done = len(self.plan.steps)
        self.last_response = r

    # -- events ---------------------------------------------------------------

    def queue_event(self, r: BrainResponse) -> None:
        """Deliver a brain result. With a live socket connected it is pushed at once; otherwise it
        waits in the queue and rides the next HTTP frame reply (or the next socket connection)."""
        if self.pending_media:
            r.media = [*r.media, *self.pending_media]
            self.pending_media = []
        conn = self.live_conn
        if conn is not None and conn.push(r):
            return
        self.events.append(r)

    def queue_media(self, m: MediaUpdate) -> None:
        """Merged into the next event. With a live socket it is pushed straight away as a media-only
        event (fal images and scans arrive on their own schedule); without one, drain_events emits
        a media-only event on the next frame reply."""
        if self.live_conn is not None:
            self.queue_event(BrainResponse(phase=self.phase, media=[m]))
            return
        self.pending_media.append(m)

    def drain_events(self) -> list[BrainResponse]:
        out = list(self.events)
        self.events.clear()
        if self.pending_media:
            out.append(BrainResponse(phase=self.phase, media=self.pending_media))
            self.pending_media = []
        return out

    # -- timings --------------------------------------------------------------

    def record_timing(self, stage: str, ms: float, ok: bool) -> None:
        ts = time.time()
        timing = {"ts": ts, "stage": stage, "ms": round(ms, 1), "ok": ok}
        self.timings.append(timing)
        LIVE.mkdir(parents=True, exist_ok=True)
        with (LIVE / "stage_timings.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timing_id": f"{self.id}:{time.time_ns()}",
                                "session_id": self.id, **timing}) + "\n")

    # -- background tasks -----------------------------------------------------

    def spawn(self, name: str, coro: Coroutine, timeout: float | None = None) -> asyncio.Task:
        """Tracked background task (P4 research, P2 fal). Errors are logged, never raised."""

        async def runner() -> None:
            try:
                await asyncio.wait_for(coro, timeout) if timeout else await coro
            except asyncio.TimeoutError:
                log.warning("task %s timed out after %ss", name, timeout)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("task %s failed", name)

        task = asyncio.create_task(runner(), name=f"{self.id}:{name}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def cancel_tasks(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- slow loop: one per session, newest wins ------------------------------

    def offer_keyframe(self, job: Any) -> None:
        """Run the slow loop for `job` now, or park it as the single pending job.

        A running slow loop is never cancelled. If another keyframe arrives while it
        runs, only the newest is kept and started when the current run finishes.
        """
        if self.slow_handler is None:
            raise RuntimeError("session has no slow_handler")
        if self._slow_running:
            self._pending_job = job
            return
        self._slow_running = True
        self.spawn("slow-loop", self._slow_loop(job))

    async def _slow_loop(self, job: Any) -> None:
        try:
            while job is not None:
                try:
                    await self.slow_handler(self, job)  # type: ignore[misc]
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("slow loop failed for session %s", self.id)
                job, self._pending_job = self._pending_job, None
        finally:
            self._slow_running = False

    @property
    def slow_busy(self) -> bool:
        return self._slow_running

    # -- views ----------------------------------------------------------------

    def state_dict(self) -> dict:
        """JSON-safe dump (no image bytes) for GET /session/{id}/state."""
        return {
            "id": self.id,
            "device": self.device,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "phase": self.phase,
            "current_step_index": self.current_step_index,
            "steps_done": self.steps_done,
            "plan": self.plan.model_dump() if self.plan else None,
            "observations": [o.model_dump() for o in self.observations],
            "last_report": self.last_report.model_dump() if self.last_report else None,
            "last_response": self.last_response.model_dump() if self.last_response else None,
            "last_step_check": self.last_step_check,
            "queued_events": len(self.events),
            "slow_busy": self._slow_running,
            "voice_waiting": self.voice_waiting,
            "timings": list(self.timings),
            "brain_state": self.brain_state,
        }


class SessionManager:
    def __init__(self, slow_handler: SlowHandler | None = None):
        self._sessions: dict[str, Session] = {}
        self._last_id: str | None = None
        self.slow_handler = slow_handler

    def create(self, device: str = "unknown") -> Session:
        sid = uuid.uuid4().hex[:12]
        s = Session(id=sid, device=device, slow_handler=self.slow_handler)
        self._sessions[sid] = s
        self._last_id = sid
        return s

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise UnknownSession(session_id) from None

    async def end(self, session_id: str) -> Session:
        s = self.get(session_id)
        s.ended_at = time.time()
        await s.cancel_tasks()
        return s

    def latest(self) -> Session | None:
        return self._sessions.get(self._last_id) if self._last_id else None

    def all(self) -> list[Session]:
        return list(self._sessions.values())
