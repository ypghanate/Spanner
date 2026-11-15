"""Pass 2 (real): SituationReport + session -> BrainResponse.

Imports RAG knowledge retrieval and integrates Claude Vision analysis.

Integration:
  Set session.brain_state["repair_demo"] before starting:
    "keyboard_character", "keyboard_chassis", "mouse_chassis", or "fuse_replacement".
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
from backend.brain.rag import ALWAYS_INCLUDE_BY_DEMO, retrieve_repair_knowledge

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
    "fuse_replacement": {
        "power_connected": "Stop. The device still looks connected to power. Unplug it and remove batteries before touching the fuse.",
        "wrong_fuse_rating": "Stop. That fuse does not match the required rating or type. Check the amps, voltage, and fast or slow type first.",
        "fuse_bridged": "Stop. Never bridge a fuse with wire or foil. Remove it and fit a correct rated fuse.",
        "wrong_tool": "Stop. Do not use a metal tool near the fuse holder. Use plastic tweezers or a fuse puller.",
        "too_much_force": "Stop pushing. Forcing a fuse can bend the clips or crack the holder. Remove it and check the fit.",
    },
}

# Retrieval hint per demo so the query matches the right guides even when the step text is short
# ("Remove the old one"), and an optional metadata category to search first.
RAG_HINTS = {
    "keyboard_character": "keyboard keycap switch",
    "keyboard_chassis": "keyboard case screws opening",
    "mouse_chassis": "mouse case skates screws opening",
    "fuse_replacement": "fuse replacement blown fuse rating amps holder multimeter continuity",
}
RAG_CATEGORY = {"fuse_replacement": "fuses"}


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


def _status(analysis: dict, code: str) -> str:
  checks = analysis.get("safety_checks")
  check = checks.get(code) if isinstance(checks, dict) else None
  return check.get("status", "unclear") if isinstance(check, dict) else "unclear"


def _checks_clear(demo: str, analysis: dict) -> bool:
  """No check is unsafe and the model actually returned the checks.

  "unclear" no longer blocks: many codes do not apply to a given step (no fuse in hand yet ->
  wrong_fuse_rating is unclear; markings too small to read on camera), and requiring every code to be
  "safe" left the fuse demo stuck on "I cannot confirm this step is safe" forever."""
  checks = analysis.get("safety_checks")
  if not isinstance(checks, dict) or not checks:
    return False
  return all(_status(analysis, code) != "unsafe" for code in DEMO_WARNINGS[demo])


def _warning_resolved(demo: str, code: str, analysis: dict) -> bool:
  """A paused warning clears when that specific issue is now visibly safe and nothing else is unsafe."""
  return _status(analysis, code) == "safe" and _checks_clear(demo, analysis)


SYSTEM = """You are an expert hardware repair technician and assistant watching a live camera feed of a keyboard, mouse, or small-electronics repair (including fuse replacement).
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
3. Never suggest bridging a fuse holder, or fitting a fuse with a higher rating than the one specified.
4. Never guide mains-voltage internals, boilers or gas appliances, microwaves, or swollen or punctured lithium batteries. Tell the user to stop and call a qualified professional.

FUSE KNOWLEDGE (use together with the retrieved guides; the retrieved guides win if they differ):
- A blown fuse is a symptom. Look for the cause (burnt smell, scorching, bulging capacitors, damaged cable, liquid) before and after replacing it. If the new fuse blows again at once, stop: the device has a fault a fuse cannot fix.
- A valid replacement matches physical size, current (amps), voltage (equal or higher), speed (F fast, T slow-blow), and breaking capacity where marked. Matching size alone is not enough.
- Test with the device unplugged, using a multimeter in continuity mode: a good fuse beeps or reads near 0 ohms; a blown one reads open. Ceramic and SMD fuses look fine when blown, so they must be tested.
- Remove and fit fuses with plastic tweezers or a fuse puller, never a metal screwdriver. Seat the fuse gently and centered. Do not force it.
- Car blade fuses: ignition off first; use the same amp rating and same blade size. Colour codes: 5 A tan, 7.5 A brown, 10 A red, 15 A blue, 20 A yellow, 25 A clear, 30 A green.
- UK plug fuses (BS 1362): unplug the plug from the wall first; 3 A for appliances up to about 700 W, 13 A above that, or the value in the appliance instructions.
- A tripped resettable polyfuse on a USB keyboard or mouse recovers after unplugging and cooling for a few minutes; repeated tripping points to a cable, connector, or board short.

GENERAL REPAIR KNOWLEDGE:
- Disconnect power first, wait for capacitors to discharge, keep screws sorted by location, and photograph the layout before disassembly.
- Use plastic spudgers on shells and clips, and hold boards by the edges to avoid static damage.
- Close the case fully and refit every screw before reconnecting power. After repair, watch for heat, smell, or smoke and disconnect at once if any appear.
- If a step cannot be done safely with what the user has, say so and recommend stopping.

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
- Fuse replacement: power_connected means the device or its cable is visibly plugged into power, or a battery is installed, while the fuse area is open or being touched.
- wrong_fuse_rating means the fuse in hand or being fitted visibly has a rating, size, or type that differs from the required one. If the markings are not legible, use "unclear", never guess.
- fuse_bridged means foil, wire, a paperclip, a coin, or similar is visibly placed across the fuse holder or in place of a fuse.
- For the fuse demo, wrong_tool also means a metal screwdriver or metal pliers used to pry a fuse out of a holder; plastic tweezers or a fuse puller are correct.
- too_much_force needs observable evidence such as bending, deformation, or forced movement. Do not claim to measure force from images.
- "safe" means the relevant condition is visibly resolved or demonstrably not applicable to this step.
- Use "unclear" for occluded parts, uncertain layouts/tools, or inadequate frames. Never invent visual evidence from YOLO labels.
- step_complete must be true only when this entire step is visibly finished, not merely being performed correctly.

Also include "step_complete": bool in the response.

Guidelines for `say_to_user`:
- Must be under 25 words.
- Never read out step numbers, step counts, or the list of steps. Talk only about what the user is doing now.
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
      "model": "Royal Kludge RK61"
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
  vocab = {"mouse", "keyboard", "keycap", "switch", "fuse", "multimeter", *(w.lower() for w in _cfg()["parts"] + _cfg()["tools"])}
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
  hint = RAG_HINTS.get(repair_demo, "")
  rag_query_text = f"{hint} {issue or ''} {current_step or ''}".strip()
  rag_context = retrieve_repair_knowledge(
      rag_query_text,
      top_k=3,
      always_include=ALWAYS_INCLUDE_BY_DEMO.get(repair_demo),
      category=RAG_CATEGORY.get(repair_demo),
  )

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
        max_tokens=800,
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


DONE_SAY = "That's the last step, the repair is done. Close everything up before reconnecting power, then test it. Ending the call now."


def _finish(session: Any) -> BrainResponse:
  """All steps finished: say it once, flag the call to end, and stay quiet afterwards."""
  session.brain_state["steps_done"] = True
  session.brain_state["end_call"] = True   # the session runner hangs up after speaking `say`
  return BrainResponse(phase="done", step_status="done", say=DONE_SAY)


def _new_repair(session: Any) -> None:
  """A fresh repair: clear the finished / hang-up flags from any earlier one.
  The plan itself lives in session.plan (main.py strips it before replies reach the phone)."""
  session.brain_state.pop("steps_done", None)
  session.brain_state.pop("end_call", None)


async def respond(report: SituationReport, session: Any) -> BrainResponse:
  resp = await _respond(report, session)
  # remember what the assistant is about to say, so Pass 1 can drop it if the mic hears it back
  from backend.brain.pass1 import remember_spoken
  remember_spoken(session, resp.say)
  if resp.alert is not None:
    remember_spoken(session, resp.alert.message)
  return resp


async def _respond(report: SituationReport, session: Any) -> BrainResponse:
  intent = report.user_intent
  if intent == "stop":
    return BrainResponse(phase="identifying", say="Okay, stopped. Say start when you want to go again.")
  demo = _repair_demo(session)
  if demo is None:
    return BrainResponse(phase="identifying", say="Choose mouse chassis, keyboard chassis, keyboard key and bar replacement, or fuse replacement before starting.")
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
      _new_repair(session)
      return BrainResponse(
          phase="guiding",
          current_step=plan.steps[0].index,
          step_status="in_progress",
          say=f"{warning}I see it. First, {plan.steps[0].instruction}",
          scene3d=list(plan.steps[0].scene3d),
          plan=plan,   # kept by the session for tracking; never sent to the phone
      )
    return BrainResponse(phase="identifying", say="" if intent == "none" else "Show me the device or fuse you are repairing.")

  if session.phase == "done" or session.brain_state.get("steps_done"):
    return BrainResponse(phase="done", step_status="done", say="")

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
    if pending.get("demo") != demo or not _warning_resolved(demo, pending.get("code", ""), analysis):
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
  spoken_response = analysis.get("say_to_user") or step.instruction

  if check.get("doing_correct_step") == "no" or check.get("movement_ok") == "no":
    correction_msg = check.get("correction") or "that's not quite right"
    return BrainResponse(
        phase="guiding",
        current_step=step.index,
        step_status="mistake",
        say=spoken_response,
        alert=Alert(severity="warn", message=correction_msg)
    )

  if not _checks_clear(demo, analysis) and (intent == "step_done" or analysis.get("step_complete") is True):
    message = "Pause. I cannot confirm this step is safe. Show the device, fuse markings, and tool clearly."
    return BrainResponse(
        phase="guiding", current_step=step.index, step_status="in_progress",
        say=message, alert=Alert(severity="warn", message=message)
    )

  if intent == "step_done" or analysis.get("step_complete") is True:
    next_pos = pos + 1
    if next_pos >= len(plan.steps):
      return _finish(session)
    next_step = plan.steps[next_pos]
    return BrainResponse(
        phase="guiding",
        current_step=next_step.index,
        step_status="in_progress",
        say=f"Good. Next, {next_step.instruction}",
        scene3d=list(next_step.scene3d)
    )

  return BrainResponse(
      phase="guiding",
      current_step=step.index,
      step_status="in_progress",
      say=spoken_response,
      scene3d=list(step.scene3d)
  )