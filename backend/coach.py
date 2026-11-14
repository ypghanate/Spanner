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
    out = {**view.model_dump(), "latency_ms": latency, "model": COACH_MODEL, "ts": time.time()}
    _latest.setdefault(session, {})["view"] = out
    return out


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


# ---------------------------------------------------------------- phone camera: pose + latest
_latest: dict[str, dict] = {}


class Intent(BaseModel):
    command: Literal["next", "back", "close", "take_apart", "reassemble", "xray", "reset", "tour", "stop", "rewind",
                     "show_mistake", "fix_it", "shop_parts", "shop_keyboards", "camera_on", "camera_off",
                     "calibrate", "show_part", "zoom_in", "zoom_out", "lights_on", "lights_off", "specs",
                     "sources", "ask", "none"]
    part: str = Field(default="", description="For show_part: socket, switch, keycap, pcb, plate, case, battery, usb, led, stabilizer, spring")
    shop_query: str = Field(default="", description="For shop_*: what to search for, in the user's words")


class IntentReq(BaseModel):
    text: str
    current_step: int = 0


@router.post("/intent")
async def intent(req: IntentReq):
    """What did the user mean? Spoken words -> one of Spanner's commands (handles synonyms, fillers, phrasing)."""
    sys_text = ("You map one spoken sentence from someone repairing a keyboard, hands-free, to exactly one Spanner "
                "command. Commands: next (continue, go on, done, finished), back (previous step), close (hide any panel, "
                "dismiss, that's enough, ok close), take_apart (explode the model), reassemble, xray, reset (start over), "
                "tour (walk me through), stop (pause), rewind, show_mistake (what can go wrong), fix_it, "
                "shop_parts (buy/replace/price of a part: keycaps, switches, stabilizers, cable), shop_keyboards "
                "(other/alternative/new keyboards to buy instead), camera_on (watch me), camera_off, calibrate (align the "
                "hologram with the real keyboard, set as zero), show_part (look at a part: fill part), zoom_in, zoom_out, "
                "lights_on, lights_off, specs, sources, ask (a real question about the repair that needs an answer), "
                "none (noise, not addressed to Spanner). Fillers like ok, please, hey Spanner are ignored.")
    def run() -> Intent:
        resp = _client().beta.messages.parse(
            model=COACH_MODEL, max_tokens=120, thinking={"type": "disabled"},
            system=[{"type": "text", "text": sys_text, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Current step on screen: {req.current_step}. Spoken: {req.text!r}"}],
            output_format=Intent)
        return resp.parsed_output or Intent(command="none")
    out = await asyncio.to_thread(run)
    return out.model_dump()


def _keyboard_pose(data: bytes, session: str) -> dict:
    """Fast, local: where is the real keyboard and how is it turned? YOLO box (team detector) + OpenCV min-area
    rectangle on the edges inside it -> centre, size, in-plane angle. ~100 ms, no API call."""
    import cv2
    import numpy as np

    from backend.vision.detect import detect

    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return {"found": False}
    h, w = img.shape[:2]
    boxes = [d for d in detect(data, f"pose-{session}") if d.label in ("keyboard", "laptop") and d.score >= 0.25]
    if boxes:
        b = max(boxes, key=lambda d: d.box.w * d.box.h).box
        x1, y1, x2, y2 = int(b.x * w), int(b.y * h), int((b.x + b.w) * w), int((b.y + b.h) * h)
    else:
        x1, y1, x2, y2 = 0, 0, w, h
    crop = img[max(0, y1):y2, max(0, x1):x2]
    if crop.size == 0:
        return {"found": False}
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    # keycaps are a dense field of edges; desk grain, grid lines and text are sparse: close the dense field into one
    # blob and fit the rectangle to the largest blob only
    k = max(9, int(min(crop.shape[:2]) * 0.06)) | 1
    blob = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    contours, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {"found": bool(boxes), "cx": (x1 + x2) / 2 / w, "cy": (y1 + y2) / 2 / h,
                "w": (x2 - x1) / w, "h": (y2 - y1) / h, "angle": 0.0, "detector": bool(boxes)}
    pts = max(contours, key=cv2.contourArea)
    (rcx, rcy), (rw, rh), ang = cv2.minAreaRect(pts)
    if rw < rh:                       # make the long side the keyboard's width: angle in (-90, 90]
        ang += 90.0
    ang = ((ang + 90.0) % 180.0) - 90.0
    return {"found": True, "cx": (x1 + rcx) / w, "cy": (y1 + rcy) / h, "w": max(rw, rh) / w, "h": min(rw, rh) / h,
            "angle": round(float(ang), 1), "detector": bool(boxes)}


@router.post("/pose")
async def pose(image: UploadFile = File(...), session: str = Form("laptop")):
    data = await image.read()
    if not data:
        raise HTTPException(400, "empty image")
    t0 = time.perf_counter()
    p = await asyncio.to_thread(_keyboard_pose, data, session)
    p["latency_ms"] = round((time.perf_counter() - t0) * 1000)
    p["ts"] = time.time()
    _latest.setdefault(session, {})["pose"] = p
    return p


class DeviceReq(BaseModel):
    text: str


class DevicePick(BaseModel):
    device: Literal["keyboard", "iphone15", "momentum4", "ipad11", "none"]
    say: str = Field(default="", description="If none: one short spoken sentence asking again, naming the four options.")


@router.post("/device")
async def device(req: DeviceReq):
    """Free speech -> which device Spanner should open (keyboard repair, or a hologram of the others)."""
    def run() -> DevicePick:
        resp = _client().beta.messages.parse(
            model=COACH_MODEL, max_tokens=120, thinking={"type": "disabled"},
            system="The user was asked 'What do you want to fix?'. Map their words to one device: keyboard (a mechanical "
                   "keyboard, RK61, a dead key, typing), iphone15 (phone, mobile, iPhone), momentum4 (headphones, "
                   "Sennheiser, earcups, headset), ipad11 (iPad, tablet). If it is none of these or unclear, 'none' with "
                   "a short friendly question naming the four options.",
            messages=[{"role": "user", "content": req.text}], output_format=DevicePick)
        return resp.parsed_output or DevicePick(device="none")
    return (await asyncio.to_thread(run)).model_dump()


@router.get("/qr")
async def qr(url: str):
    """QR code (PNG) for pairing the phone: the laptop shows it, the phone scans it."""
    import io

    import qrcode
    from fastapi.responses import Response

    q = qrcode.QRCode(box_size=8, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(url[:400]); q.make(fit=True)
    img = q.make_image(fill_color="#00E5FF", back_color="#05080F")
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/latest")
async def latest(session: str):
    """The phone's last results for this session: the coach's view (every ~2 s) and the keyboard pose (~4/s)."""
    return _latest.get(session, {})


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
