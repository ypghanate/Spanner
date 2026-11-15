"""Pass 2 (real): SituationReport + session -> BrainResponse.

Imports RAG knowledge retrieval and integrates Claude Vision analysis.

Integration:
  Set session.brain_state["repair_demo"] before starting:
    "keyboard_character", "keyboard_chassis", or "mouse_chassis".
  Load the matching procedure into session.plan and set phase="guiding".
  The original _plan() still reads training/repair.json; this file does not
  create or select the three demo procedures automatically.
  Apply returned BrainResponse.phase/current_step as your session runner does.
  safety_stop returns the warning in both say and alert.message; do not advance.
  Keep calling respond while paused so new frames can confirm resolution.

Limits:
  Camera analysis cannot measure applied force or see hidden screws.
  These are visual warnings, not a guaranteed physical safety interlock.
  Live Claude/camera integration requires your existing backend and credentials.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import anthropic
import cv2

from backend.contracts import Alert, BrainResponse, RepairPlan, SituationReport
from backend.brain.rag import retrieve_repair_knowledge

ROOT = Path(__file__).resolve().parents[2]
MODEL = os.getenv("PASS2_MODEL", "claude-sonnet-5-5")

# Set session.brain_state["repair_demo"] to one of these before starting.
# Unknown demos are not silently treated as keyboard repairs.
DEMO_WARNINGS = {
    "keyboard_character": {
        "wrong_key_position": "Stop. That key is in the wrong position. Check the keyboard layout before fitting it.",
        "too_much_force": "Stop pressing. Too much force can damage the key or stabilizer. Lift it and check the fit.",
    },
    "keyboard_chassis": {
        "screws_left": "Stop. Screws are still securing the chassis. Remove the remaining screws before lifting it.",
        "wrong_alignment": "Stop. The keyboard chassis is misaligned. Reposition it before fastening or pressing it into place.",
        "wrong_tool": "Stop. That tool is unsuitable for this step. Use the tool specified in the repair instructions.",
    },
    "mouse_chassis": {
        "wrong_alignment": "Stop. The mouse chassis is misaligned. Reposition it before fastening or pressing it into place.",
        "wrong_tool": "Stop. That tool is unsuitable for this step. Use the tool specified in the repair instructions.",
        "too_much_force": "Stop pressing. Too much force can break the mouse clips or shell. Release pressure and check the alignment.",
    },
}


def _repair_demo(session: Any) -> str | None:
  value = session.brain_state.get("repair_demo")
  return value if value in DEMO_WARNINGS else None


def _warning_for(demo: str, analysis: dict) -> tuple[str, str] | None:
  """Only applicable, explicitly observed issues interrupt; unknown is not safe."""
  checks = analysis.get("safety_checks")
  if not isinstance(checks, dict):
    return None
  for code, message in DEMO_WARNINGS[demo].items():
    check = checks.get(code)
    if (isinstance(check, dict) and check.get("status") == "unsafe"
        and isinstance(check.get("evidence"), str) and check["evidence"].strip()):
      return code, message
  return None


def _checks_clear(demo: str, analysis: dict) -> bool:
  checks = analysis.get("safety_checks")
  return isinstance(checks, dict) and all(
      isinstance(checks.get(code), dict) and checks[code].get("status") == "safe"
      for code in DEMO_WARNINGS[demo]
  )


SYSTEM = """You are an expert hardware repair technician and assistant watching a live camera feed of a keyboard or mouse repair.
You guide users safely and precisely. You incorporate retrieved technical documentation context to provide exact hardware instructions.

You receive:
- 1-2 video frames (older first)
- YOLO object detection summary
- Hand movement tracking
- Current repair step the user is performing
- Target issue/goal
- RAG Retrieved Repair Knowledge context

YOLO labels can be noisy; rely primarily on visual evidence in the frames.

CRITICAL SAFETY RULES:
1. Always advise unplugging cables or removing batteries before opening electronics.
2. Warn against forcing plastic clips or pulling ribbon cables abruptly.

Respond with ONLY a valid JSON object matching this exact schema:
{
 "hand_on_keyboard": "touching" | "hovering" | "away" | "unclear",
 "fault": {"found": bool, "description": str, "confidence": 0-1},
 "repair_plan": [ {"step": int, "action": str, "tool": str} ],
 "current_step_check": {
    "doing_correct_step": "yes" | "no" | "unclear",
    "movement_ok": "yes" | "no" | "unclear",
    "correction": str
 },
 "say_to_user": str,
 "safety_checks": {
   "issue_code": {"status": "safe" | "unsafe" | "unclear", "evidence": str}
 }
}

Repair interruption rules:
- Evaluate every issue code supplied for the active repair demo, and no other codes.
- Key replacement: wrong_key_position means a character key or stabilizer bar is being fitted in the wrong position.
- Chassis: screws_left means screws still secure the shell during an attempted lift, not spare screws sitting nearby.
- wrong_alignment means mating edges, clips, or mounting points do not line up during fitting.
- wrong_tool means the visible tool is unsuitable for the current step according to the retrieved instructions.
- too_much_force needs observable evidence such as bending, deformation, or forced movement. Do not claim to measure force from images.
- "safe" means the relevant condition is visibly resolved or demonstrably not applicable to this step.
- Use "unclear" for occluded parts, uncertain layouts/tools, or inadequate frames. Never invent visual evidence from YOLO labels.
- step_complete must be true only when this entire step is visibly finished, not merely being performed correctly.

Also include "step_complete": bool in the response.

Guidelines for `say_to_user`:
- Must be under 25 words.
- Optimized for Text-to-Speech (TTS): Conversational, warm, direct, and completely free of markdown, bullet symbols, or special characters.
- Use "unclear" when frames do not show enough detail."""


@lru_cache(maxsize=1)
def _cfg() -> dict:
  return json.loads((ROOT / "training" / "repair.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _plan() -> RepairPlan:
  from backend.knowledge import plan_from_knowledge
  from backend.contracts import SceneCommand
  from training.scene3d import scene3d_for_step

  plan = plan_from_knowledge()
  if plan is None:
    raise RuntimeError("No procedure in the knowledge base yet: run `python -m training.pipeline` first.")
  
  for s in plan.steps:
    if not s.scene3d:
      s.scene3d = [SceneCommand.model_validate(c) for c in scene3d_for_step(s.instruction, s.parts)]
  return plan


def _learned_warning() -> str | None:
  from training.feedback import learned_mistake_for
  return learned_mistake_for(_cfg()["repair_id"])


def _start_parts_job(session: Any) -> None:
  if session.brain_state.get("parts_job"):
    return
  from backend.knowledge import handle_parts_tool
  cfg = _cfg()
  job = handle_parts_tool({
      "part": "mechanical switch (MX-compatible, 3-pin or 5-pin)",
      "fault": cfg["fault"],
      "model": "Royal Kludge RK68"
  })
  session.brain_state["parts_job"] = job["job"]


def _object_in_view(report: SituationReport, session: Any) -> bool:
  obs = getattr(session, "observations", None) or []
  last = obs[-1] if obs else None
  seen = " ".join([
      report.camera_sees,
      report.suspected_fault or "",
      *([last.object, *last.visible_parts, *last.tools_visible] if last else [])
  ]).lower()
  vocab = {"mouse", "keyboard", "keycap", "switch", *(w.lower() for w in _cfg()["parts"] + _cfg()["tools"])}
  return any(w in seen for w in vocab)


@lru_cache(maxsize=1)
def _client():
  return anthropic.Anthropic()


def _img(frame):
  _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
  return {
      "type": "image",
      "source": {
          "type": "base64",
          "media_type": "image/jpeg",
          "data": base64.b64encode(buf).decode(),
      },
  }


def analyze(frames, scene_text, hand_motion, current_step, issue, repair_demo=None) -> dict:
  """Queries the external RAG module and passes retrieved context + frames to Claude Vision."""
  rag_query_text = f"{issue or ''} {current_step or ''}".strip()
  rag_context = retrieve_repair_knowledge(rag_query_text, top_k=2)

  content = [_img(f) for f in frames] if frames else []
  content.append({
      "type": "text",
      "text": (
          f"--- RETRIEVED KNOWLEDGE BASE GUIDES ---\n{rag_context}\n\n"
          f"--- LIVE SCENE TELEMETRY ---\n{scene_text}\n"
          f"Hand movement: {hand_motion or 'none'}\n"
          f"Current repair step: {current_step or 'none started - diagnose first'}\n"
          f"Target Issue / Goal: {issue or 'none specified'}\n"
          f"Active repair demo: {repair_demo or 'unknown'}\n"
          f"Required safety issue codes: {list(DEMO_WARNINGS.get(repair_demo, {}))}"
      ),
  })

  try:
    resp = _client().messages.create(
        model=MODEL,
        max_tokens=600,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
    )

    raw_text = resp.content[0].text.strip()
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    json_str = match.group(0) if match else raw_text
    result = json.loads(json_str)
    if not isinstance(result, dict):
      raise ValueError("Vision response must be a JSON object")
    return result

  except Exception as e:
    print(f"[Repair Assistant Error]: {e}")
    return {
        "hand_on_keyboard": "unclear",
        "fault": {"found": False, "description": "Error processing frame", "confidence": 0.0},
        "repair_plan": [],
        "current_step_check": {
            "doing_correct_step": "unclear",
            "movement_ok": "unclear",
            "correction": "Hold on, let me re-examine the feed.",
        },
        "say_to_user": "Hold on a second, let me check that step again.",
    }


async def respond(report: SituationReport, session: Any) -> BrainResponse:
  intent = report.user_intent
  if intent == "stop":
    return BrainResponse(phase="identifying", say="Okay, stopped. Say start when you want to go again.")
  demo = _repair_demo(session)
  if demo is None:
    return BrainResponse(phase="identifying", say="Choose mouse chassis, keyboard chassis, or keyboard key and bar replacement before starting.")
  pending = session.brain_state.get("repair_warning")
  if session.phase == "safety_stop" and not pending and intent != "start":
    return BrainResponse(phase="safety_stop", say="")

  plan = await asyncio.to_thread(_plan)
  
  if session.plan is None or session.phase in ("identifying", "diagnosing", "researching"):
    if intent == "start" or _object_in_view(report, session):
      # Do not launch the original hardcoded RK61 switch parts order for other demos.
      if not plan.steps:
        return BrainResponse(phase="identifying", say="No repair steps are available for this demo.")
      learned = _learned_warning()
      warning = f"Users often {learned}. " if learned else ""
      return BrainResponse(
          phase="guiding",
          current_step=plan.steps[0].index,
          step_status="in_progress",
          say=f"{warning}I see the repair item. {len(plan.steps)} steps, about {plan.time_min} minutes. Step 1: {plan.steps[0].instruction}",
          scene3d=list(plan.steps[0].scene3d),
          plan=plan
      )
    return BrainResponse(phase="identifying", say="" if intent == "none" else "Show me the keyboard or mouse.")

  if session.phase == "done":
    return BrainResponse(phase="done", step_status="done", say="The repair is complete. Reconnect power only after reassembly, then test the device.")

  plan = session.plan
  pos = session.current_step_index or 0
  if not plan.steps or not 0 <= pos < len(plan.steps):
    return BrainResponse(phase="identifying", say="The repair step is unavailable. Please reload the selected repair plan.")
  step = plan.steps[pos]

  perception = getattr(session, "perception", None)
  frames = perception.images if perception and hasattr(perception, "images") else []
  scene_text = report.camera_sees or "No camera description available."
  hand_motion = getattr(report, "hand_motion", "none")
  issue = getattr(plan, "fault", None) or report.suspected_fault or demo.replace("_", " ")

  analysis = await asyncio.to_thread(
      analyze,
      frames=frames,
      scene_text=scene_text,
      hand_motion=hand_motion,
      current_step=step.instruction,
      issue=issue,
      repair_demo=demo
  )

  warning = _warning_for(demo, analysis)
  if warning:
    code, message = warning
    session.brain_state["repair_warning"] = {"demo": demo, "code": code, "message": message}
    return BrainResponse(
        phase="safety_stop", current_step=step.index, step_status="mistake",
        say=message, alert=Alert(severity="warn", message=message)
    )

  # A start/step_done command must never bypass an unresolved warning.
  if pending:
    if pending.get("demo") != demo or not _checks_clear(demo, analysis):
      message = pending["message"]
      return BrainResponse(
          phase="safety_stop", current_step=step.index, step_status="mistake",
          say=message, alert=Alert(severity="warn", message=message)
      )
    session.brain_state.pop("repair_warning", None)
    return BrainResponse(
        phase="guiding", current_step=step.index, step_status="in_progress",
        say="The issue is resolved. Continue the current step.", scene3d=list(step.scene3d)
    )

  session.last_step_check = analysis.get("current_step_check", {})
  if not isinstance(session.last_step_check, dict):
    session.last_step_check = {}
  session.brain_state["hand_on_keyboard"] = analysis.get("hand_on_keyboard")
  session.brain_state["fault_detected"] = analysis.get("fault")

  check = session.last_step_check
  spoken_response = analysis.get("say_to_user") or f"Step {step.index}: {step.instruction}"

  if check.get("doing_correct_step") == "no" or check.get("movement_ok") == "no":
    correction_msg = check.get("correction") or "that's not quite right"
    return BrainResponse(
        phase="guiding",
        current_step=step.index,
        step_status="mistake",
        say=spoken_response,
        alert=Alert(severity="warn", message=correction_msg)
    )

  if not _checks_clear(demo, analysis):
    message = "Pause. I cannot confirm this step is safe. Show the key position, shell alignment, and tool clearly."
    return BrainResponse(
        phase="guiding", current_step=step.index, step_status="in_progress",
        say=message, alert=Alert(severity="warn", message=message)
    )

  if intent == "step_done" or analysis.get("step_complete") is True:
    next_pos = pos + 1
    if next_pos >= len(plan.steps):
      return BrainResponse(phase="done", step_status="done", say="The repair is complete. Reconnect power only after reassembly, then test the device.")
    next_step = plan.steps[next_pos]
    return BrainResponse(
        phase="guiding",
        current_step=next_step.index,
        step_status="in_progress",
        say=spoken_response,
        scene3d=list(next_step.scene3d)
    )

  return BrainResponse(
      phase="guiding",
      current_step=step.index,
      step_status="in_progress",
      say=spoken_response,
      scene3d=list(step.scene3d)
  )