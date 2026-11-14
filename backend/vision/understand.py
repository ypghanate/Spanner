"""Understand stage (real): Claude vision turns a keyframe into a SceneObservation."""
from __future__ import annotations

import asyncio
import base64
import os
from functools import lru_cache

from backend.contracts import Detection, SceneObservation

MODEL = os.getenv("UNDERSTAND_MODEL", "claude-haiku-5-5")
SYSTEM = ("You are the eyes of Spanner, a repair assistant. Describe one camera frame of a repair as a "
          "SceneObservation: the object, the parts you can see, their state in concrete words (cable plugged "
          "or not, keycap on or off, switch in the plate or out...), anomalies or mistakes, whether hands are "
          "visible and which tools are held. Only report what is actually visible.")


@lru_cache(maxsize=1)
def _client():
    import anthropic

    return anthropic.Anthropic()


def _observe(jpeg: bytes, frame_id: str, detections: list[Detection], expected_state: str | None) -> SceneObservation:
    hints = ", ".join(f"{d.label} ({d.score:.2f})" for d in detections[:12]) or "none"
    text = f"frame_id: {frame_id}\nDetector boxes: {hints}\n"
    if expected_state:
        text += f"The current step is done when: {expected_state}\n"
    resp = _client().beta.messages.parse(
        model=MODEL, max_tokens=1024, thinking={"type": "disabled"}, output_config={"effort": "low"},
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(jpeg).decode()}},
            {"type": "text", "text": text}]}],
        output_format=SceneObservation)
    obs = resp.parsed_output
    if obs is None:
        raise RuntimeError(f"vision model gave no observation (stop_reason={resp.stop_reason})")
    obs.frame_id = frame_id
    obs.confidence = max(0.0, min(obs.confidence, 1.0))
    return obs


async def understand(jpeg: bytes, frame_id: str, detections: list[Detection],
                     expected_state: str | None) -> SceneObservation:
    return await asyncio.to_thread(_observe, jpeg, frame_id, detections, expected_state)
