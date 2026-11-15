"""Mocks for every pipeline stage, plus the resolver that picks real vs mock.

The orchestrator (main.py) never imports P2's or P4's modules directly. It asks
load_stages() for each stage: the real function when USE_MOCKS=0 and the module exists,
otherwise the mock here. Every stage runs on fixtures with no API keys.

Stage signatures P2/P4 must expose (also in CONTRACTS.md). Sync or async both work:

    backend.vision.preprocess.preprocess(jpeg, session_id) -> (jpeg, FrameQuality)
    backend.vision.detect.detect(jpeg, session_id) -> list[Detection]
    backend.vision.understand.understand(jpeg, frame_id, detections, expected_state) -> SceneObservation
    backend.brain.safety.check(report, session) -> BrainResponse | None
    backend.brain.pass2.respond(report, session) -> BrainResponse
    backend.store.get_store() -> Store   # .write(table, row) non-blocking, .read(table, limit), .flush()

Knowledge-layer stages (Aura Maker's backend/knowledge.py; mocked the same way):

    backend.knowledge.track_step_async(step, observation, said, session_id, repair_id) -> dict
        # {"step_status": "in_progress" | "done" | "mistake" | None, "mistake": str, ...}
    backend.knowledge.get_parts_card_async(job) -> dict
        # {"status": "researching" | "ready" | "failed" | "unknown_job", "card": {...}}
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import get_settings
from .contracts import (
    BrainResponse, Detection, FrameQuality, RepairPlan, SceneObservation, SituationReport,
    Transcript,
)

log = logging.getLogger(__name__)


# --- fixtures ---------------------------------------------------------------

def fixture_path(name: str) -> Path:
    return get_settings().fixtures_dir / "outputs" / f"{name}.json"


def fixture_json(name: str) -> dict:
    return json.loads(fixture_path(name).read_text(encoding="utf-8"))


async def _latency() -> None:
    ms = get_settings().mock_latency_ms
    if ms > 0:
        await asyncio.sleep(ms / 1000)


# --- P2 mocks (vision) ------------------------------------------------------

_frame_counts: dict[str, int] = {}
_obs_counts: dict[str, int] = {}


async def mock_preprocess(jpeg: bytes, session_id: str) -> tuple[bytes, FrameQuality]:
    await _latency()
    n = _frame_counts.get(session_id, 0)
    _frame_counts[session_id] = n + 1
    return jpeg, FrameQuality(
        blurry=False, too_dark=False, is_keyframe=(n % 3 == 0),
        sharpness=140.0, brightness=115.0)


async def mock_detect(jpeg: bytes, session_id: str) -> list[Detection]:
    await _latency()
    return [Detection.model_validate(d) for d in fixture_json("frame_response")["detections"]]


async def mock_understand(jpeg: bytes, frame_id: str, detections: list[Detection],
                          expected_state: str | None) -> SceneObservation:
    await _latency()
    sid = frame_id.rsplit("-", 1)[0]
    n = _obs_counts.get(sid, 0)
    _obs_counts[sid] = n + 1
    obs = fixture_json(f"scene_observation_{(n % 3) + 1}")
    obs["frame_id"] = frame_id
    return SceneObservation.model_validate(obs)


# --- P4 mocks (brain, store) ------------------------------------------------

from backend.brain.safety import _STOP as _SAFETY_RX   # same rule as the real stage: mains only matters while connected


async def mock_safety_check(report: SituationReport, session: Any) -> BrainResponse | None:
    text = f"{report.user_utterance or ''} {report.summary} {report.camera_sees}"
    if _SAFETY_RX.search(text):
        return BrainResponse(
            phase="safety_stop",
            say="Sorry, I can't help with mains or gas repairs. Please call a qualified professional.",
            alert={"severity": "danger", "message": "Mains or gas repair: not supported."})
    return None


def _brain(name: str) -> BrainResponse:
    return BrainResponse.model_validate(fixture_json(f"brain_response_{name}"))


def _guiding(plan: RepairPlan, position: int, say: str | None = None,
             status: str = "in_progress", attach_plan: bool = False) -> BrainResponse:
    """A guiding response for the step at 0-based `position`."""
    step = plan.steps[position]
    r = _brain("guiding")
    r.current_step = step.index
    r.step_status = status  # type: ignore[assignment]
    r.say = say if say is not None else f"Step {step.index}: {step.instruction}"
    r.scene3d = list(step.scene3d)
    r.plan = plan if attach_plan else None
    return r


async def mock_pass2_respond(report: SituationReport, session: Any) -> BrainResponse:
    """Scripted repair: identifying -> diagnosing -> researching -> guiding 1..N -> done."""
    await _latency()
    state = session.brain_state
    calls = state.get("mock_calls", 0)
    state["mock_calls"] = calls + 1
    plan = RepairPlan.model_validate(fixture_json("repair_plan"))
    intent = report.user_intent
    phase = session.phase

    if intent == "stop":
        state["mock_calls"] = 0
        return _brain("identifying")
    if phase == "safety_stop":
        return BrainResponse(phase="safety_stop", say="Say start over when you're ready.")

    if phase == "identifying":
        return _brain("identifying") if calls == 0 else _brain("diagnosing")
    if phase == "diagnosing":
        return _brain("researching")
    if phase == "researching" or session.plan is None:
        state.setdefault("parts_job", f"mock-parts-{session.id}")      # P4's convention: the job id lives here
        return _guiding(plan, 0, attach_plan=True)

    if phase == "done":
        return _brain("done")

    # guiding / verifying
    plan = session.plan
    pos = session.current_step_index or 0
    text = f"{report.camera_sees} {report.summary}".lower()
    check = session.last_step_check or {}
    if check.get("step_status") == "mistake" or "touching the tyre" in text or "touching the tire" in text:
        r = _brain("alert")
        r.current_step = plan.steps[pos].index
        if check.get("mistake"):
            r.alert.message = check["mistake"][:1].upper() + check["mistake"][1:] + "."
        return r
    if check.get("step_status") == "done" and intent == "none":
        # the camera clearly sees the step is complete: move on without waiting for the user to say so
        if pos + 1 >= len(plan.steps):
            return _brain("done")
        return _guiding(plan, pos + 1)
    if intent == "step_done":
        if pos + 1 >= len(plan.steps):
            return _brain("done")
        return _guiding(plan, pos + 1)
    if intent in ("question", "need_help"):
        step = plan.steps[pos]
        return _guiding(plan, pos, say=f"For step {step.index}: {step.instruction}")
    return _guiding(plan, pos, say="")


async def mock_track_step(step: Any, observation: Any, said: str = "", session_id: str | None = None,
                          repair_id: str | None = None) -> dict:
    """Step tracker stand-in: a pad touching the tyre is a mistake, a centred pad means done."""
    await _latency()
    text = observation if isinstance(observation, str) else f"{observation.state} {' '.join(observation.anomalies)}"
    text = text.lower()
    if "touching the tyre" in text or "touching the tire" in text:
        status, mistake = "error", "pad is touching the tyre"
    elif "centred" in text or "centered" in text:
        status, mistake = "done", ""
    else:
        status, mistake = "in_progress", ""
    contract = {"not_started": None, "in_progress": "in_progress", "done": "done", "error": "mistake"}[status]
    return {"status": status, "mistake": mistake, "backend": "mock", "latency_ms": 0, "step_status": contract}


_mock_jobs: dict[str, int] = {}


async def mock_get_parts_card(job: str) -> dict:
    """A Linkup job that is 'researching' on the first poll and 'ready' on the second."""
    await _latency()
    if not job.startswith("mock-parts-"):
        return {"status": "unknown_job"}
    polls = _mock_jobs.get(job, 0)
    _mock_jobs[job] = polls + 1
    if polls < 1:
        return {"status": "researching"}
    return {"status": "ready", "card": fixture_json("parts_card")}


class MockStore:
    """In-memory store with the same surface as P4's store."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}

    def write(self, table: str, row: dict) -> None:
        self.tables.setdefault(table, []).append(row)

    def read(self, table: str, limit: int = 100) -> list[dict]:
        return list(self.tables.get(table, []))[-limit:]

    def flush(self) -> None:
        return None


_mock_store = MockStore()


def mock_get_store() -> MockStore:
    return _mock_store


# --- P3 mocks (voice, pass 1) -----------------------------------------------

async def mock_transcribe(audio: bytes, filename: str, keyterms: list[str] | None = None,
                          duration_hint_s: float | None = None) -> Transcript:
    """Picks a fixture transcript from the file name: *done* / *start* / otherwise question."""
    await _latency()
    low = filename.lower()
    name = "done" if "done" in low else "start" if "start" in low else "question"
    return Transcript.model_validate(fixture_json(f"transcript_{name}"))


async def mock_pass1_run(inp: Any, session_id: str, store: Any = None,
                         images: list[bytes] | None = None) -> SituationReport:
    """Fixture report chosen by the heuristic's intent, with the user's words filled in."""
    await _latency()
    from .brain.pass1 import fallback_report

    intent = fallback_report(inp).user_intent
    report = SituationReport.model_validate(fixture_json(f"situation_report_{intent}"))
    if inp.transcript and inp.transcript.text.strip():
        report.user_utterance = inp.transcript.text.strip()
    else:
        report.user_utterance = None
    if inp.observations:
        newest = inp.observations[-1]
        report.camera_sees = f"{newest.object}: {newest.state}."
    return report


# --- resolver ---------------------------------------------------------------

_announced: set[str] = set()


def resolve(dotted_path: str, mock: Callable) -> tuple[Callable, bool]:
    """Return (function, is_real). Mock when USE_MOCKS=1 or the module/attribute is missing."""
    if get_settings().use_mocks:
        return mock, False
    module_name, _, attr = dotted_path.rpartition(".")
    try:
        fn = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as e:
        if dotted_path not in _announced:
            _announced.add(dotted_path)
            log.warning("stage %s not available (%s); using mock", dotted_path, e)
        return mock, False
    return fn, True


@dataclass
class Stages:
    preprocess: Callable
    detect: Callable
    understand: Callable
    safety: Callable
    pass2: Callable
    pass1: Callable
    transcribe: Callable
    track_step: Callable
    parts_card: Callable
    store: Any
    real: dict[str, bool] = field(default_factory=dict)

    def describe(self) -> dict[str, str]:
        return {k: ("real" if v else "mock") for k, v in self.real.items()}


def load_stages() -> Stages:
    entries = {
        "preprocess": ("backend.vision.preprocess.preprocess", mock_preprocess),
        "detect": ("backend.vision.detect.detect", mock_detect),
        "understand": ("backend.vision.understand.understand", mock_understand),
        "safety": ("backend.brain.safety.check", mock_safety_check),
        "pass2": ("backend.brain.pass2.respond", mock_pass2_respond),
        "pass1": ("backend.brain.pass1.run", mock_pass1_run),
        "transcribe": ("backend.voice.stt.transcribe", mock_transcribe),
        "track_step": ("backend.knowledge.track_step_async", mock_track_step),
        "parts_card": ("backend.knowledge.get_parts_card_async", mock_get_parts_card),
    }
    fns: dict[str, Callable] = {}
    real: dict[str, bool] = {}
    for name, (path, mock) in entries.items():
        fns[name], real[name] = resolve(path, mock)
    get_store, real["store"] = resolve("backend.store.get_store", mock_get_store)
    stages = Stages(store=get_store(), real=real, **fns)
    log.info("stages: %s", stages.describe())
    return stages


_stages: Stages | None = None


def get_stages() -> Stages:
    global _stages
    if _stages is None:
        _stages = load_stages()
    return _stages


def reset_stages() -> None:
    """Forget resolved stages and per-session mock counters (tests, settings reload)."""
    global _stages
    _stages = None
    _frame_counts.clear()
    _obs_counts.clear()
    _mock_jobs.clear()
    _mock_store.tables.clear()
