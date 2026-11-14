"""The live backend's single entry point to the knowledge / training / sponsor features.

Everything here speaks backend/contracts.py, and every slow call has an async version for the
asyncio server (they run in a worker thread so the event loop never blocks).

    from backend.knowledge import (plan_from_knowledge, search_steps, track_step_async,
                                   PARTS_TOOL, handle_parts_tool, get_parts_card)

| Function                                   | Where (owner)            | What it does                                              |
|--------------------------------------------|--------------------------|-----------------------------------------------------------|
| plan_from_knowledge(repair_id=None)        | brain, researching phase | Canonical procedure from Databricks-curated videos + web  |
|                                            | (P4)                     | guides + manuals, as a contracts.RepairPlan. Use it before |
|                                            |                          | (or instead of) live research                             |
| search_steps(text, k=3)                    | brain (P4)               | Matching steps from the Vector Search knowledge base      |
| track_step / track_step_async(step, obs,   | slow loop, every         | Adaption step tracker -> {"step_status": in_progress |     |
|        said, session_id, repair_id)        | keyframe (P3/P4)         | done | mistake | None, "mistake", ...}. Mistakes are       |
|                                            |                          | logged to Databricks automatically (learning loop)        |
| PARTS_TOOL / handle_parts_tool(input)      | brain's Claude tools (P4)| Linkup research starts; returns {"status","job"} at once  |
| get_parts_card(job)                        | orchestrator (P3): poll, | {"status":"ready","card":{...}} -> send to the app         |
|                                            | then push to the app     | (see PARTS_CARD_CONTRACT below)                           |
"""
from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from urllib.parse import urlparse

from backend.contracts import RepairPlan, RepairStep, SceneObservation, SourceRef
from backend.parts_agent import TOOL as PARTS_TOOL
from backend.parts_agent import card_view, get_card as get_parts_card
from backend.parts_agent import handle_tool as handle_parts_tool
from training.feedback import record_live_mistake
from training.kb import search_steps
from training.step_tracker import describe_observation, step_check

ROOT = Path(__file__).resolve().parents[1]

# The app needs one new field to show the Linkup card. Proposed addition to contracts.py (P3):
#   class BrainResponse(BaseModel): ...
#       parts_card: dict | None = None   # backend.knowledge.get_parts_card(job)["card"]
PARTS_CARD_CONTRACT = "BrainResponse.parts_card: dict | None (shape: backend.parts_agent.card_view)"


# ---------------------------------------------------------------- knowledge -> contracts
def to_source_ref(src: dict) -> SourceRef:
    url = src.get("url", "")
    host = urlparse(url).netloc.lower()
    kind = "youtube" if "youtube." in host or "youtu.be" in host else "ifixit" if "ifixit." in host else "web"
    return SourceRef(title=src.get("title") or host or "source", url=url, kind=kind)


def plan_from_knowledge(repair_id: str | None = None) -> RepairPlan | None:
    """The canonical procedure for this repair as the contract's RepairPlan (None if not built yet)."""
    if repair_id is None:
        cfg = json.loads((ROOT / "training" / "repair.json").read_text(encoding="utf-8"))
        repair_id = cfg["repair_id"]
    path = ROOT / "data" / "procedures" / f"{repair_id}.json"
    if not path.exists():
        return None
    rec = json.loads(path.read_text(encoding="utf-8"))
    proc = rec["procedure"]
    sources = [to_source_ref(s) for s in rec.get("sources", []) if s.get("url")]
    steps = [RepairStep(index=s["index"], instruction=s["instruction"], parts=s["parts"],
                        expected_visual_state=s["expected_visual_state"], common_mistakes=s["common_mistakes"],
                        sources=sources[:3])
             for s in proc["steps"]]
    minutes = sum(max(0.0, s["end_s"] - s["start_s"]) for s in proc["steps"]) / 60
    safety = sorted({n for s in proc["steps"] for n in s["safety_notes"]})
    return RepairPlan(object=proc["object"], fault=proc["fault"], difficulty="easy",
                      time_min=max(5, math.ceil(minutes)) if minutes else 10,
                      tools_needed=proc["tools_needed"], safety_notes=safety, steps=steps, sources=sources)


def observation_text(obs: SceneObservation) -> str:
    """A live SceneObservation in the same wording the step tracker was trained on."""
    action = ", ".join(obs.anomalies) or ("hands working on it" if obs.hands_visible else "no hands visible")
    return describe_observation(obs.state, action, obs.visible_parts)


# ---------------------------------------------------------------- step tracking + learning loop
def track_step(step: RepairStep, observation: SceneObservation | str, said: str = "",
               session_id: str | None = None, repair_id: str | None = None) -> dict:
    """Is the current step done? Logs any mistake to the learning loop when session_id is given."""
    obs = observation if isinstance(observation, str) else observation_text(observation)
    r = step_check(step.instruction, step.expected_visual_state, step.common_mistakes, obs, said)
    if r["step_status"] == "mistake" and session_id:
        record_live_mistake(session_id, repair_id or "", step.index, r["mistake"], obs)
    return r


async def track_step_async(*args, **kwargs) -> dict:
    return await asyncio.to_thread(track_step, *args, **kwargs)


async def search_steps_async(query: str, k: int = 3, canonical_only: bool = False) -> list[dict]:
    return await asyncio.to_thread(search_steps, query, k, canonical_only)  # current repair only


async def get_parts_card_async(job: str) -> dict:
    return await asyncio.to_thread(get_parts_card, job)


__all__ = ["plan_from_knowledge", "search_steps", "search_steps_async", "track_step", "track_step_async",
           "observation_text", "record_live_mistake", "PARTS_TOOL", "handle_parts_tool", "get_parts_card",
           "get_parts_card_async", "card_view", "to_source_ref", "PARTS_CARD_CONTRACT"]
