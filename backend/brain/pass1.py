"""Pass 1: camera observations + speech -> one SituationReport.

render_user() and load_system_prompt() are the ONLY way anything builds the Pass 1 prompt:
the Claude backend, the Adaption backend, make_dataset.py and eval.py all call them, so
training data matches serving byte for byte.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anthropic
from pydantic import BaseModel, Field

from ..config import get_settings
from ..contracts import Detection, Phase, SceneObservation, SituationReport, Transcript
from ..perception import MAX_DETECTIONS_SHOWN, detector_line, image_blocks  # noqa: F401  (re-exported)

if TYPE_CHECKING:
    from ..perception import Perception
    from ..session import Session

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "pass1.md"


class Pass1Error(RuntimeError):
    """The Claude call failed in a way that has no usable report."""


# --- input ------------------------------------------------------------------

class StepBrief(BaseModel):
    index: int
    instruction: str
    expected_visual_state: str
    common_mistakes: list[str] = Field(default_factory=list)


class Pass1Input(BaseModel):
    phase: Phase
    observations: list[SceneObservation] = Field(default_factory=list)  # up to 3, oldest first
    transcript: Transcript | None = None                                 # None on keyframe-only calls
    current_step: StepBrief | None = None
    previous_summary: str | None = None
    # What the camera and detector show right now (see backend/perception.py). All optional: an input
    # without them renders exactly as it always did. The pictures themselves are not stored here (bytes
    # don't belong in the logged JSON); they are passed to call_claude() separately, in the order
    # image_notes describes.
    detections: list[Detection] = Field(default_factory=list)     # YOLO tracks on the latest frame
    observation_age_s: float | None = None                        # how old the newest observation is
    image_notes: list[str] = Field(default_factory=list)          # one line per attached image, in order


def build_input(session: "Session", transcript: Transcript | None,
                perception: "Perception | None" = None) -> Pass1Input:
    step = session.current_step()
    return Pass1Input(
        phase=session.phase,
        observations=list(session.observations)[-3:],
        transcript=transcript,
        current_step=None if step is None else StepBrief(
            index=step.index, instruction=step.instruction,
            expected_visual_state=step.expected_visual_state,
            common_mistakes=list(step.common_mistakes)),
        previous_summary=session.last_report.summary if session.last_report else None,
        detections=list(perception.detections) if perception else [],
        observation_age_s=perception.observation_age_s if perception else None,
        image_notes=list(perception.image_notes) if perception else [],
    )


# --- prompt -----------------------------------------------------------------

@lru_cache
def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def _list(items: list[str]) -> str:
    return ", ".join(items) if items else "none"


def render_user(inp: Pass1Input) -> str:
    """Deterministic text rendering of the input. Never reorder or reword without retraining.

    The IMAGES / DETECTOR lines and the "seen N s ago" tag appear only when the new optional fields are
    set, so inputs built without a perception render byte for byte as before."""
    lines = [f"PHASE: {inp.phase}"]
    if inp.current_step is not None:
        s = inp.current_step
        lines.append(f"CURRENT STEP: {s.index}. {s.instruction}")
        lines.append(f"  Done when: {s.expected_visual_state}")
        lines.append(f"  Common mistakes: {'; '.join(s.common_mistakes) if s.common_mistakes else 'none'}")
    else:
        lines.append("CURRENT STEP: none")
    lines.append(f"PREVIOUS SUMMARY: {inp.previous_summary or 'none'}")
    lines.append("OBSERVATIONS (oldest first):")
    if not inp.observations:
        lines.append("  none")
    for i, o in enumerate(inp.observations, start=1):
        lines.append(
            f"  [{i}] frame {o.frame_id} | object: {o.object} | state: {o.state} | "
            f"parts: {_list(o.visible_parts)} | anomalies: {_list(o.anomalies)} | "
            f"hands visible: {'yes' if o.hands_visible else 'no'} | tools: {_list(o.tools_visible)} | "
            f"confidence: {o.confidence:.2f}"
            + (f" | seen {inp.observation_age_s:.1f} s ago"
               if inp.observation_age_s is not None and i == len(inp.observations) else ""))
    if inp.image_notes:
        lines.append("IMAGES ATTACHED (in order): "
                     + "; ".join(f"{n}. {note}" for n, note in enumerate(inp.image_notes, start=1)))
    if inp.image_notes or inp.detections:
        lines.append(f"DETECTOR (YOLO, latest frame): {detector_line(inp.detections)}")
    if inp.transcript is not None and inp.transcript.text.strip():
        t = inp.transcript
        lines.append(f'USER SAID: "{t.text.strip()}" (language {t.language}, {t.duration_s:.1f}s)')
    else:
        lines.append("USER SAID: (nothing)")
    return "\n".join(lines)


# --- heuristic fallback (no model call) -------------------------------------

_RULES: list[tuple[str, re.Pattern]] = [
    ("stop", re.compile(r"\b(stop|cancel|start over|forget it|abort)\b", re.I)),
    ("need_help", re.compile(r"\b(help|stuck|confused|doesn'?t work|won'?t|can'?t|broke|broken)\b", re.I)),
    ("step_done", re.compile(r"\b(done|finished|finish|next|complete[d]?|all set|ready)\b", re.I)),
]
_START = re.compile(r"\b(start|begin|fix|repair|rubbing|broken|problem)\b", re.I)


def fallback_report(inp: Pass1Input) -> SituationReport:
    """Model-free report. Low confidence; keeps the loop alive when Claude is down."""
    newest = inp.observations[-1] if inp.observations else None
    camera = (f"{newest.object}: {newest.state}." if newest else "No camera observation yet.")
    text = inp.transcript.text.strip() if inp.transcript else ""

    if not text:
        intent = "none"
    else:
        intent = "question"
        for name, rx in _RULES:
            if rx.search(text):
                intent = name
                break
        else:
            if inp.phase in ("identifying", "diagnosing") and _START.search(text):
                intent = "start"

    fault = None
    if newest and newest.anomalies:
        fault = newest.anomalies[0]
    return SituationReport(
        summary=(f'The user said "{text}". ' if text else "") + camera,
        user_intent=intent,
        user_utterance=text or None,
        camera_sees=camera,
        change_since_last="Unknown.",
        suspected_fault=fault,
        confidence=0.3,
    )


# --- Claude backend ---------------------------------------------------------

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(
            api_key=get_settings().anthropic_api_key or None, max_retries=0)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def _user_content(inp: Pass1Input, images: list[bytes] | None) -> str | list[dict]:
    """The user turn: plain text, or (images first, then the text) when pictures are attached."""
    text = render_user(inp)
    if not images:
        return text
    return [*image_blocks(images), {"type": "text", "text": text}]


async def call_claude(inp: Pass1Input, images: list[bytes] | None = None) -> SituationReport:
    """One Haiku call. Raises Pass1Error on any failure (the dataset teacher uses this).
    `images` are JPEGs shown before the text, in the order inp.image_notes lists them."""
    s = get_settings()
    try:
        msg = await _get_client().messages.parse(
            model=s.pass1_model,
            max_tokens=1024,
            system=load_system_prompt(),
            messages=[{"role": "user", "content": _user_content(inp, images)}],
            output_format=SituationReport,
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            timeout=s.claude_timeout_s,
        )
    except anthropic.APITimeoutError as e:
        raise Pass1Error("claude timed out") from e
    except anthropic.RateLimitError as e:
        raise Pass1Error("claude rate limited") from e
    except anthropic.APIStatusError as e:
        raise Pass1Error(f"claude returned HTTP {e.status_code}") from e
    except anthropic.APIConnectionError as e:
        raise Pass1Error("could not reach claude") from e
    except Exception as e:  # e.g. the response failed schema validation
        raise Pass1Error(f"claude output unusable: {e}") from e

    if msg.stop_reason == "refusal":
        raise Pass1Error("claude refused")
    if msg.stop_reason == "max_tokens":
        raise Pass1Error("claude hit max_tokens")
    if msg.parsed_output is None:
        raise Pass1Error("claude returned no parsed output")
    return msg.parsed_output


async def run_claude(inp: Pass1Input, images: list[bytes] | None = None) -> SituationReport:
    """Haiku, or the heuristic fallback if the call fails."""
    try:
        return await (call_claude(inp, images) if images else call_claude(inp))
    except Pass1Error as e:
        log.warning("Pass 1 Claude failed (%s); using heuristic report", e)
        return fallback_report(inp)


# --- dispatcher -------------------------------------------------------------

def _write_local(row: dict) -> None:
    path = get_settings().training_data_dir / "raw" / "pass1_calls.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        log.warning("could not append to %s", path, exc_info=True)


def _log_call(store: Any, session_id: str, backend: str, inp: Pass1Input,
              out: SituationReport | None, latency_ms: float, valid: bool) -> None:
    row = {
        "session_id": session_id,
        "ts": time.time(),
        "backend": backend,
        "input_json": inp.model_dump_json(),
        "output_json": out.model_dump_json() if out else "",
        "latency_ms": round(latency_ms, 1),
        "valid_json": valid,
    }
    try:
        if store is not None:
            store.write("pass1_calls", row)
    except Exception:
        log.warning("store.write(pass1_calls) failed", exc_info=True)
    _write_local(row)


async def run(inp: Pass1Input, session_id: str, store: Any = None,
              images: list[bytes] | None = None) -> SituationReport:
    """Pick the backend, time it, log the call as a pass1_calls row, never raise.

    `images` (the camera frames and close-up for this turn) go to Claude only. The fine-tuned model is
    text-only: it still gets the detection list through the rendered input."""
    s = get_settings()
    if store is None:
        from .. import mocks  # lazy: mocks imports this module

        store = mocks.get_stages().store

    t0 = time.perf_counter()
    report: SituationReport | None = None
    backend = "claude"
    valid = True

    if s.pass1_backend == "adaption":
        from .pass1_adaption import AdaptionError, run_adaption

        try:
            report = await asyncio.wait_for(run_adaption(inp), s.adaption_timeout_s)
            backend = "adaption"
        except (AdaptionError, asyncio.TimeoutError) as e:
            log.warning("Adaption failed (%r); falling back to Claude", e)
            backend = "adaption_fallback"

    if report is None:
        try:
            report = await (call_claude(inp, images) if images else call_claude(inp))
        except Pass1Error as e:
            log.warning("Pass 1 Claude failed (%s); using heuristic report", e)
            report = fallback_report(inp)
            backend, valid = "heuristic", False

    _log_call(store, session_id, backend, inp, report, (time.perf_counter() - t0) * 1000, valid)
    return report
