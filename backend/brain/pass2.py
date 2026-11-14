"""Pass 2 (real): SituationReport + session -> BrainResponse.

The procedure comes from the knowledge base (plan_from_knowledge: YouTube + web guides curated in
Databricks), never from fixtures. Step progress comes from the real step tracker (session.last_step_check),
the user's intent from Pass 1, and questions are answered by Claude with the current step as context.
Once the repair starts, the Linkup replacement-part research is kicked off in the background.

    identifying -> guiding 1..N -> done        (stop -> identifying; safety_stop until "start")
"""
from __future__ import annotations

import asyncio
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.contracts import Alert, BrainResponse, RepairPlan, SituationReport

ROOT = Path(__file__).resolve().parents[2]
ANSWER_MODEL = os.getenv("PASS2_MODEL", "claude-haiku-5-5")


@lru_cache(maxsize=1)
def _cfg() -> dict:
    return json.loads((ROOT / "training" / "repair.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _plan() -> RepairPlan:
    from backend.knowledge import plan_from_knowledge

    plan = plan_from_knowledge()
    if plan is None:
        raise RuntimeError("No procedure in the knowledge base yet: run `python -m training.pipeline` first.")
    # attach the 3D hologram commands for every step
    from backend.contracts import SceneCommand
    from training.scene3d import scene3d_for_step

    for s in plan.steps:
        if not s.scene3d:
            s.scene3d = [SceneCommand.model_validate(c) for c in scene3d_for_step(s.instruction, s.parts)]
    return plan


def _start_parts_job(session: Any) -> None:
    if session.brain_state.get("parts_job"):
        return
    from backend.knowledge import handle_parts_tool

    cfg = _cfg()
    job = handle_parts_tool({"part": "mechanical switch (MX-compatible, 3-pin or 5-pin)", "fault": cfg["fault"],
                             "model": "Royal Kludge RK61"})
    session.brain_state["parts_job"] = job["job"]   # the orchestrator polls it and pushes the card


def _object_in_view(report: SituationReport, session: Any) -> bool:
    """The repair object (or any of its parts/tools) is in the latest camera report or observation."""
    obs = getattr(session, "observations", None) or []
    last = obs[-1] if obs else None
    seen = " ".join([report.camera_sees, report.suspected_fault or "",
                     *( [last.object, *last.visible_parts, *last.tools_visible] if last else [])]).lower()
    vocab = {"keyboard", "keycap", "switch", *(w.lower() for w in _cfg()["parts"] + _cfg()["tools"])}
    return any(w in seen for w in vocab)


def _guide(plan: RepairPlan, pos: int, say: str | None = None, status: str = "in_progress",
           attach_plan: bool = False, alert: Alert | None = None) -> BrainResponse:
    step = plan.steps[pos]
    return BrainResponse(phase="guiding", current_step=step.index, step_status=status,  # type: ignore[arg-type]
                         say=f"Step {step.index}: {step.instruction}" if say is None else say,
                         scene3d=list(step.scene3d), plan=plan if attach_plan else None, alert=alert)


def _done() -> BrainResponse:
    return BrainResponse(phase="done", step_status="done",
                         say="That's it, the key is fixed. Plug the cable back in and type on it to check.")


@lru_cache(maxsize=1)
def _client():
    import anthropic

    return anthropic.Anthropic()


def _answer(plan: RepairPlan, pos: int, report: SituationReport) -> str:
    step = plan.steps[pos]
    ctx = (f"Repair: {plan.object}. Fault: {plan.fault}.\nCurrent step {step.index}/{len(plan.steps)}: "
           f"{step.instruction}\nDone when: {step.expected_visual_state}\n"
           f"Known mistakes: {'; '.join(step.common_mistakes) or 'none'}\nCamera sees: {report.camera_sees}")
    resp = _client().messages.create(
        model=ANSWER_MODEL, max_tokens=200, thinking={"type": "disabled"},
        system="You are Spanner, a calm repair coach. Answer in at most 2 short spoken sentences, concrete, no lists.",
        messages=[{"role": "user", "content": f"{ctx}\n\nThe user says: {report.user_utterance or report.summary}"}])
    return "".join(b.text for b in resp.content if b.type == "text").strip()


async def respond(report: SituationReport, session: Any) -> BrainResponse:
    intent = report.user_intent
    if intent == "stop":
        return BrainResponse(phase="identifying", say="Okay, stopped. Say start when you want to go again.")
    if session.phase == "safety_stop" and intent != "start":
        return BrainResponse(phase="safety_stop", say="")

    plan = await asyncio.to_thread(_plan)
    if session.plan is None or session.phase in ("identifying", "diagnosing", "researching", "safety_stop"):
        if intent == "start" or _object_in_view(report, session):
            _start_parts_job(session)
            return _guide(plan, 0, attach_plan=True,
                          say=f"I see the keyboard. {len(plan.steps)} steps, about {plan.time_min} minutes. "
                              f"Step 1: {plan.steps[0].instruction}")
        return BrainResponse(phase="identifying", say="" if intent == "none" else "Show me the keyboard.")

    if session.phase == "done":
        return BrainResponse(phase="done")

    plan = session.plan
    pos = session.current_step_index or 0
    check = session.last_step_check or {}
    if check.get("step_status") == "mistake":
        msg = (check.get("mistake") or "that's not quite right").strip()
        return _guide(plan, pos, status="mistake", say=f"Careful: {msg}.",
                      alert=Alert(severity="warn", message=msg[:1].upper() + msg[1:] + "."))
    if intent == "step_done" or (check.get("step_status") == "done" and intent == "none"):
        return _done() if pos + 1 >= len(plan.steps) else _guide(plan, pos + 1)
    if intent in ("question", "need_help"):
        return _guide(plan, pos, say=await asyncio.to_thread(_answer, plan, pos, report))
    return _guide(plan, pos, say="")
