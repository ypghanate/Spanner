"""Live repair coach for the laptop: webcam frame in -> "you're on step N" (+ mistake) out. No mocks.

The laptop viewer (training/viewer/, served by this app at /training/viewer/) grabs a webcam frame
every ~2 s and POSTs it to /coach/frame. Claude (vision) looks at it with the full knowledge-base
procedure for the repair in its (cached) system prompt and answers which step the user is doing,
whether it's done, and whether they're making one of that step's known mistakes. The viewer then
jumps the 3D hologram to that exact step and plays it; mistakes go to the Databricks learning loop.

    POST /coach/frame   multipart: image (JPEG), current_step (int, 1-based, 0 = none), session (str)
    GET  /coach/plan    the procedure + 3D commands + Linkup parts card the viewer plays
    GET  /coach/health  model + whether the procedure is loaded
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "data" / "models" / "rk61"
COACH_MODEL = os.getenv("COACH_MODEL", "claude-haiku-5-5")  # measured: same step accuracy as Sonnet, steadier confidence
FALLBACK_BETA = "server-side-fallback-2026-07-01"

router = APIRouter(prefix="/coach", tags=["coach"])


class CoachView(BaseModel):
    object_visible: bool = Field(description="True if the keyboard (or the part being repaired) is visible in the frame.")
    what_i_see: str = Field(description="One short sentence: what the hands and the keyboard are doing right now.")
    step_index: int = Field(description="1-based index of the procedure step the user is doing now (or about to do). "
                                        "0 if the repair has not started or nothing relevant is visible.")
    step_status: Literal["not_started", "in_progress", "done"]
    mistake: str = Field(description="If the user is making a mistake (especially one of that step's known mistakes), "
                                     "say it in a few words; else empty.")
    confidence: float = Field(description="0 to 1: how sure you are about step_index.")
    say: str = Field(description="At most one short sentence of spoken guidance for the user, or empty.")


@lru_cache(maxsize=1)
def _demo() -> dict:
    path = MODEL_DIR / "demo_plan.json"
    if not path.exists():
        raise HTTPException(503, "No demo plan yet: run `python -m training.scene3d` after building the knowledge base.")
    return json.loads(path.read_text(encoding="utf-8"))


def _system(demo: dict) -> str:
    plan = demo["plan"]
    key = demo.get("broken_key", "F")
    lines = [f"You are Spanner, a repair coach watching a live webcam. The repair: {plan['object']}; "
             f"fault: {plan['fault']}. The broken key is '{key}'.",
             "The procedure (use these step numbers):"]
    for s in plan["steps"]:
        mistakes = "; ".join(s.get("common_mistakes", [])) or "none listed"
        lines.append(f"{s['index']}. {s['instruction']}\n   DONE WHEN: {s['expected_visual_state']}\n   KNOWN MISTAKES: {mistakes}")
    lines += [
        "From one webcam frame, decide which step the user is on. Use the state of the keyboard as evidence: "
        "cable plugged or not, keycap on or off, switch in the plate or out, a switch in their hand, tools held "
        "(keycap puller = wire loops, switch puller = two prongs, tweezers). The current step shown on screen "
        "is a hint, not the answer: move forward or back only when the frame shows it.",
        "Report a mistake only if you can actually see it. Keep what_i_see and say short and concrete.",
    ]
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _client():
    import anthropic

    return anthropic.Anthropic()


def _ask(image_b64: str, current_step: int) -> CoachView:
    demo = _demo()
    hint = (f"The screen currently shows step {current_step}." if current_step > 0
            else "The repair has not started on screen yet.")
    resp = _client().beta.messages.parse(
        model=COACH_MODEL, max_tokens=2048,
        system=[{"type": "text", "text": _system(demo), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": hint + " Which step is the user on?"}]}],
        output_format=CoachView, output_config={"effort": "low"},
        thinking={"type": "disabled"},   # one frame -> one label: thinking only adds latency
        **({"betas": [FALLBACK_BETA], "fallbacks": "default"} if "haiku" not in COACH_MODEL else {}))
    if resp.parsed_output is None:
        raise HTTPException(502, f"vision model gave no answer (stop_reason={resp.stop_reason})")
    out = resp.parsed_output
    out.step_index = max(0, min(out.step_index, len(demo["plan"]["steps"])))
    out.confidence = max(0.0, min(out.confidence, 1.0))
    return out


@router.post("/frame")
async def frame(image: UploadFile = File(...), current_step: int = Form(0), session: str = Form("laptop")):
    data = await image.read()
    if not data:
        raise HTTPException(400, "empty image")
    t0 = time.perf_counter()
    view = await asyncio.to_thread(_ask, base64.standard_b64encode(data).decode(), current_step)
    latency = round((time.perf_counter() - t0) * 1000)
    from training.feedback import record_live_mistake, record_stage_timing
    from training.kb import current_repair_id

    record_stage_timing(session, "coach", latency, True)          # measured latency -> Databricks dashboard
    if view.mistake and view.step_index > 0:   # the learning loop: real mistakes go to Databricks
        record_live_mistake(session, current_repair_id(), view.step_index, view.mistake, view.what_i_see)
    return {**view.model_dump(), "latency_ms": latency, "model": COACH_MODEL}


class Ask(BaseModel):
    question: str
    current_step: int = 0


@router.post("/ask")
async def ask(q: Ask):
    """A free spoken question ("which way does the switch go in?") answered with the current step as context."""
    demo = _demo()
    steps = demo["plan"]["steps"]
    cur = steps[q.current_step - 1] if 0 < q.current_step <= len(steps) else None
    ctx = _system(demo) + (f"\n\nThe user is on step {cur['index']}: {cur['instruction']}" if cur else "")

    def run() -> str:
        resp = _client().messages.create(
            model=COACH_MODEL, max_tokens=250, thinking={"type": "disabled"},
            system=[{"type": "text", "text": ctx + "\n\nNow answer the user's spoken question in at most 2 short, "
                     "concrete spoken sentences. No lists, no markdown.", "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": q.question}])
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    return {"answer": await asyncio.to_thread(run)}


@router.get("/plan")
async def plan():
    return _demo()


@router.get("/learned")
async def learned():
    """{step_index: mistake} learned from earlier sessions (2+ sessions made it), straight from Databricks."""
    from training.feedback import learned_mistakes
    from training.kb import current_repair_id

    return {"repair_id": current_repair_id(), "learned": await asyncio.to_thread(learned_mistakes, current_repair_id())}


@router.get("/health")
async def health():
    try:
        steps = len(_demo()["plan"]["steps"])
    except HTTPException:
        steps = 0
    from .voice import tts

    return {"model": COACH_MODEL, "procedure_steps": steps, "anthropic_key": bool(os.getenv("ANTHROPIC_API_KEY")),
            "tts": tts.available()}


@router.get("/say")
async def say(text: str):
    """Spanner's voice: ElevenLabs speech for `text`, streamed as MP3 and cached on disk by text."""
    from fastapi.responses import StreamingResponse

    from .voice import tts

    if not tts.available():
        raise HTTPException(503, "ElevenLabs is not configured")
    text = text.strip()[:600]
    if not text:
        raise HTTPException(400, "empty text")
    return StreamingResponse(tts.stream(text), media_type="audio/mpeg",
                             headers={"Cache-Control": "public, max-age=86400"})


def mount(app) -> None:
    """Serve the coach API, the hologram viewer and its 3D assets from the backend (one URL, one server)."""
    from .shop import router as shop_router

    app.include_router(router)
    app.include_router(shop_router)

    @app.on_event("startup")
    async def _warm_shop() -> None:   # (main.py already warms the YOLO detector)
        if os.getenv("SHOP_WARMUP", "1") == "1" and os.getenv("LINKUP_API_KEY"):   # live Linkup + Tavily, cached for the demo
            from .shop import warm as warm_shop

            asyncio.get_running_loop().run_in_executor(None, warm_shop)
    app.mount("/data/models", StaticFiles(directory=ROOT / "data" / "models"), name="models")
    app.mount("/training/viewer", StaticFiles(directory=ROOT / "training" / "viewer", html=True), name="viewer")
