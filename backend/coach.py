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
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "data" / "models" / "rk61"
COACH_MODEL = os.getenv("COACH_MODEL", "claude-haiku-5-5")  # per-frame step read: ~1 s; the Sonnet second look (verify_gap) reads key legends
INTENT_MODEL = os.getenv("INTENT_MODEL", "claude-haiku-5-5")  # spoken words -> command / device: classification, keep it fast
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _thinking(model: str) -> dict:
    """No reasoning before answering: one frame -> one label. Sonnet/Opus 5.5 spell that 'between_tools'."""
    return {"type": "between_tools"} if ("sonnet-5-5" in model or "opus-5" in model) else {"type": "disabled"}

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
    keyboard_visible: bool = Field(default=False, description="True if a mechanical keyboard is in the frame.")
    missing_keys: list[str] = Field(default_factory=list, description=(
        "Printed legends of keycaps that are MISSING from the keyboard (a gap showing the switch top or stem), e.g. "
        "['F'] or ['Enter']. Identify a gap by its neighbours (the keys left and right of it, and the row). Empty if "
        "every keycap is in place or you cannot tell. Never guess: only list a key you can place exactly."))
    switch_removed: bool = Field(default=False, description="True if a switch has been pulled out and the empty socket is visible.")
    gap_left: str = Field(default="", description="If there is a gap: the printed legend of the key immediately LEFT of the gap in the same row (it is visible: read it exactly). Empty if none or unsure.")
    gap_right: str = Field(default="", description="If there is a gap: the printed legend of the key immediately RIGHT of the gap in the same row. Empty if none or unsure.")
    gap_row: Literal["number", "top", "home", "bottom", "space", ""] = Field(default="", description="Row of the gap: number (1-0), top (Q-P), home (A-L), bottom (Z-M), space (modifiers), or empty.")
    gap_x: float | None = Field(default=None, description="If there is a gap: its centre, as a fraction of the image width (0 = left, 1 = right). null if none.")
    gap_y: float | None = Field(default=None, description="If there is a gap: its centre, as a fraction of the image height (0 = top, 1 = bottom). null if none.")


@lru_cache(maxsize=1)
def _default_demo() -> dict:
    path = MODEL_DIR / "demo_plan.json"
    if not path.exists():
        raise HTTPException(503, "No demo plan yet: run `python -m training.scene3d` after building the knowledge base.")
    return json.loads(path.read_text(encoding="utf-8"))


# The plan Claude wrote for each session's complaint (backend/planner.py). Sessions without one get the canned demo plan.
_plans: dict[str, dict] = {}


def _demo(session: str = "") -> dict:
    return _plans.get(session) or _default_demo()


def _system_device(plan: dict) -> str:
    """The frame check for the non-keyboard devices: generic, driven by the plan's own steps."""
    lines = [f"You are Spanner, a repair coach watching a live phone camera. The repair: {plan['object']}; fault: {plan['fault']}.",
             "The procedure (use these step numbers):"]
    for s in plan["steps"]:
        mistakes = "; ".join(s.get("common_mistakes", [])) or "none listed"
        lines.append(f"{s['index']}. {s['instruction']}\n   DONE WHEN: {s['expected_visual_state']}\n   KNOWN MISTAKES: {mistakes}")
    lines += ["From one camera frame, decide which step the person is on, using the state of the device and their hands and tools "
              "as evidence (screen on or off, case open or closed, a part out or in, screws, a tool in use). The step shown on "
              "screen is a hint: move forward only when the frame shows it.",
              "Report a mistake only if you can actually see it. Keep what_i_see and say short and concrete.",
              f"object_visible is true ONLY when the {plan['object']} itself (or the part being repaired) is clearly in the frame. "
              "A hand alone, another object, a screen showing a picture of it, or an empty frame is false."]
    return "\n".join(lines)


def _system(demo: dict) -> str:
    plan = demo["plan"]
    if plan.get("device", "keyboard") != "keyboard":
        return _system_device(plan)
    key = demo.get("broken_key", "F")
    keyline = f" The broken key is '{key}'." if plan.get("target_key") or not plan.get("generated") else ""
    lines = [f"You are Spanner, a repair coach watching a live webcam. The repair: {plan['object']}; "
             f"fault: {plan['fault']}." + (f" The user said the broken key is '{key}'." if plan.get("target_key") else
             " WHICH key is broken is not known in advance: the user decides by pulling that keycap. Find the gap in "
             "the frame and report its printed legend in missing_keys; it can be any key."),
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
        "The keyboard is a 61-key ANSI 60% layout (rows: Esc 1-0 - = Backspace / Tab Q-P [ ] \\ / Caps A-L ; ' Enter / "
        "Shift Z-M , . / Shift / Ctrl Win Alt Space Alt Fn Menu Ctrl). If a keycap is missing you will see a gap with the "
        "switch top or its stem. Then READ the legends of the keys immediately left and right of the gap in the same "
        "row (they are visible) into gap_left / gap_right, and the row into gap_row; the missing key is the one between "
        "them. Be precise: a wrong key is worse than none.",
    ]
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _client():
    import anthropic

    # the SDK default is a 10 minute timeout with 2 retries: one stuck call would hold a worker thread for half an hour
    return anthropic.Anthropic(timeout=float(os.getenv("COACH_TIMEOUT_S", "20")), max_retries=1)


def _keyboard_crop(data: bytes) -> tuple[bytes, dict]:
    """The frame, cropped to the keyboard (YOLO box + padding) and resized to <= 1280 px, so key legends are readable.
    Falls back to the full frame when no keyboard is found."""
    import cv2
    import numpy as np

    from backend.vision.detect import detect

    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return data, {"cropped": False}
    h, w = img.shape[:2]
    boxes = [d for d in detect(data, "coach-crop") if d.label in ("keyboard", "laptop") and d.score >= 0.25]
    info = {"cropped": False, "frame": [w, h]}
    if boxes:
        b = max(boxes, key=lambda d: d.box.w * d.box.h).box
        pad = 0.08
        x1, y1 = int(max(0, (b.x - pad) * w)), int(max(0, (b.y - pad) * h))
        x2, y2 = int(min(w, (b.x + b.w + pad) * w)), int(min(h, (b.y + b.h + pad) * h))
        if x2 - x1 > 80 and y2 - y1 > 40:
            img = img[y1:y2, x1:x2]
            info = {"cropped": True, "box": [x1, y1, x2, y2], "frame": [w, h]}
    ch, cw = img.shape[:2]
    if cw > 1280:
        img = cv2.resize(img, (1280, int(ch * 1280 / cw)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return (buf.tobytes() if ok else data), info


# the RK61's 61-key ANSI layout, row by row, as printed legends (the ground truth for "which key is the gap")
LAYOUT = {
    "number": ["Esc", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=", "Backspace"],
    "top": ["Tab", "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "[", "]", "\\"],
    "home": ["Caps", "A", "S", "D", "F", "G", "H", "J", "K", "L", ";", "'", "Enter"],
    "bottom": ["Shift", "Z", "X", "C", "V", "B", "N", "M", ",", ".", "/", "Shift"],
    "space": ["Ctrl", "Win", "Alt", "Space", "Alt", "Fn", "Menu", "Ctrl"],
}
_ALIAS = {"escape": "Esc", "return": "Enter", "capslock": "Caps", "caps lock": "Caps", "backslash": "\\", "control": "Ctrl",
          "windows": "Win", "command": "Win", "spacebar": "Space", "minus": "-", "equals": "=", "semicolon": ";", "quote": "'",
          "comma": ",", "period": ".", "slash": "/", "lbracket": "[", "rbracket": "]"}


def _legend(x: str) -> str:
    x = (x or "").strip()
    return _ALIAS.get(x.lower(), x.upper() if len(x) == 1 else x.capitalize())


def resolve_gap(view: "CoachView") -> str | None:
    """The missing key from its visible neighbours: left and right legends two apart in one layout row."""
    left, right = _legend(view.gap_left), _legend(view.gap_right)
    rows = [view.gap_row] if view.gap_row in LAYOUT else list(LAYOUT)
    for row in rows:
        keys = LAYOUT[row]
        if left in keys and right in keys:
            i, j = keys.index(left), keys.index(right)
            if j - i == 2:
                return keys[i + 1]
    return None


class GapRead(BaseModel):
    gap_present: bool = Field(description="True only if a keycap is clearly missing in this close-up (switch top or stem visible in a gap).")
    left: str = Field(default="", description="Printed legend of the key immediately LEFT of the gap, same row. Empty if the gap is at the row's left end or unreadable.")
    right: str = Field(default="", description="Printed legend of the key immediately RIGHT of the gap, same row.")
    above: str = Field(default="", description="Legend of the key directly above the gap (next row up), if readable.")
    below: str = Field(default="", description="Legend of the key directly below the gap, if readable.")
    row: Literal["number", "top", "home", "bottom", "space", ""] = ""


VERIFY_MODEL = os.getenv("COACH_VERIFY_MODEL", "claude-sonnet-5-5")


def _zoom(data: bytes, gx: float, gy: float, frac: float = 0.34) -> bytes | None:
    """A tight crop around the suspected gap, upscaled so the surrounding legends are large and legible."""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    cw, ch = int(w * frac), int(w * frac * 0.6)
    x1, y1 = int(max(0, gx * w - cw / 2)), int(max(0, gy * h - ch / 2))
    crop = img[y1:y1 + ch, x1:x1 + cw]
    if crop.size == 0:
        return None
    crop = cv2.resize(crop, (1024, int(crop.shape[0] * 1024 / crop.shape[1])), interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return buf.tobytes() if ok else None


def verify_gap(data: bytes, view: "CoachView") -> str | None:
    """Second look with a stronger model on a zoomed crop: which key is missing, from the legends around the gap."""
    located = view.gap_x is not None and view.gap_y is not None and 0 <= view.gap_x <= 1 and 0 <= view.gap_y <= 1
    zoom = _zoom(data, view.gap_x, view.gap_y) if located else data      # no location: the stronger model reads the whole crop
    if not zoom:
        return None
    resp = _client().beta.messages.parse(
        model=VERIFY_MODEL, max_tokens=300, thinking=_thinking(VERIFY_MODEL),
        system=("Close-up of part of a 61-key ANSI 60% mechanical keyboard. Rows: number (Esc 1-0 - = Backspace), top (Tab Q-P [ ] \\), "
                "home (Caps A-L ; ' Enter), bottom (Shift Z-M , . / Shift), space (Ctrl Win Alt Space Alt Fn Menu Ctrl). "
                "If a keycap is missing you see a gap with the switch top or stem. Read the printed legends of the keys around "
                "the gap EXACTLY as printed. Do not infer the missing key; only report what you can read."),
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(zoom).decode()}},
            {"type": "text", "text": "Is a keycap missing here? Read the legends left, right, above and below the gap."}]}],
        output_format=GapRead)
    g = resp.parsed_output
    if not g or not g.gap_present:
        return None
    fake = CoachView(object_visible=True, what_i_see="", step_index=0, step_status="in_progress", mistake="", confidence=0,
                     say="", gap_left=g.left, gap_right=g.right, gap_row=g.row)
    key = resolve_gap(fake)
    if key:
        return key
    # one neighbour plus the key above or below pins the column too (ANSI stagger: above-row index == same index)
    for row_name, keys in LAYOUT.items():
        if g.row and row_name != g.row:
            continue
        left, right = _legend(g.left), _legend(g.right)
        cands = [keys[keys.index(left) + 1] for _ in [0] if left in keys and keys.index(left) + 1 < len(keys)] +                 [keys[keys.index(right) - 1] for _ in [0] if right in keys and keys.index(right) - 1 >= 0]
        if len(cands) == 1 and (g.above or g.below):
            return cands[0]
    return None


def _focus(demo: dict, current: int) -> str:
    """The question for one frame, focused on the step on screen: is it done, is it going wrong, has the person moved on.
    (Asked only "which step?", the model hopped between neighbouring steps and rarely flagged mistakes.)"""
    steps = demo["plan"]["steps"]
    if not 0 < current <= len(steps):
        return ("The repair has not started on screen yet. Which step's state does the frame show (step_index, 0 if "
                "unclear)? Report a mistake only if you can see one.")
    s = steps[current - 1]
    nxt = steps[current] if current < len(steps) else None
    mistakes = "; ".join(s.get("common_mistakes", [])) or "none listed"
    lines = [f"STEP {current} is on screen: {s['instruction']}",
             f"It is DONE when: {s['expected_visual_state']}",
             f"Known mistakes at this step: {mistakes}"]
    if nxt:
        lines.append(f"(Next, step {current + 1}: {nxt['instruction']} Done when: {nxt['expected_visual_state']})")
    lines += [
        f"1. step_status: 'done' as soon as the frame shows step {current}'s DONE state, even if the hands are still there; "
        "'in_progress' if they are working on it; 'not_started' if not yet.",
        "2. mistake: if the frame shows one of the known mistakes, or anything clearly wrong for this step (the wrong part, "
        "a tool on the wrong part, force at an angle, the keyboard still plugged in when it should not be), say it in a "
        "few words. Empty if nothing is wrong.",
        f"3. step_index: {current}, unless the frame clearly shows a LATER step's state (then that step). Never go back "
        "because of an unclear frame.",
        "4. say: if done, one short line of praise; if a mistake, what to do instead; otherwise empty.",
    ]
    return "\n".join(lines)


def _ask(image_b64: str, current_step: int, session: str = "") -> CoachView:
    demo = _demo(session)
    st = _view_state.get(session)
    if st and st.screen == "step":       # the laptop's step on screen, not the phone's own counter (they could differ)
        current_step = st.step
    hint = _focus(demo, current_step)
    resp = _client().beta.messages.parse(
        model=COACH_MODEL, max_tokens=2048,
        system=[{"type": "text", "text": _system(demo), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": hint}]}],
        output_format=CoachView, output_config={"effort": "low"},
        thinking=_thinking(COACH_MODEL),   # one frame -> one label: thinking only adds latency
        **({"betas": [FALLBACK_BETA], "fallbacks": "default"} if "haiku" not in COACH_MODEL else {}))
    if resp.parsed_output is None:
        raise HTTPException(502, f"vision model gave no answer (stop_reason={resp.stop_reason})")
    out = resp.parsed_output
    out.step_index = max(0, min(out.step_index, len(demo["plan"]["steps"])))
    resolved = resolve_gap(out) if demo["plan"].get("device", "keyboard") == "keyboard" else None
    if resolved:                                   # neighbours beat a read of the absent legend
        out.missing_keys = [resolved]
    elif out.missing_keys and (out.gap_left or out.gap_right):
        out.missing_keys = []                      # neighbours given but inconsistent with the layout: don't trust the guess
    out.confidence = max(0.0, min(out.confidence, 1.0))
    return out


MAX_IMAGE_BYTES = 8 * 1024 * 1024
_last_frame: dict[str, tuple[bytes, float]] = {}   # session -> (jpeg, when)
_frame_inflight: set[str] = set()        # sessions with a vision call running: a second frame is dropped, not queued


@router.post("/frame")
async def frame(image: UploadFile = File(...), current_step: int = Form(0), session: str = Form("laptop")):
    data = await image.read()
    if not data:
        raise HTTPException(400, "empty image")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image too large")
    _last_frame[session] = (data, time.time())      # the newest picture, for a spoken question about what the camera sees
    if session in _frame_inflight:       # the previous frame is still being read: never stack Claude calls behind it
        raise HTTPException(429, "still reading the previous frame")
    if session not in _plans:
        # The person hasn't said what is wrong yet, so there is no repair to check frames against (and no Claude call
        # to pay for). Answer with an idle view so the phone link still shows as live.
        out = {"object_visible": False, "what_i_see": "", "step_index": 0, "step_status": "not_started", "mistake": "",
               "confidence": 0.0, "say": "", "latency_ms": 0, "model": COACH_MODEL, "ts": time.time()}
        _latest.setdefault(session, {})["view"] = out
        return out
    _frame_inflight.add(session)
    t0 = time.perf_counter()
    try:
        on_keyboard = _demo(session)["plan"].get("device", "keyboard") == "keyboard"
        data, crop = await asyncio.to_thread(_keyboard_crop, data) if on_keyboard else (data, {"cropped": False})
        view = await asyncio.to_thread(_ask, base64.standard_b64encode(data).decode(), current_step, session)
        gap_suspected = on_keyboard and bool(view.missing_keys or view.gap_left or view.gap_right or view.gap_x is not None)
        verified = await asyncio.to_thread(verify_gap, data, view) if gap_suspected else None
    finally:
        _frame_inflight.discard(session)
    if verified:
        view.missing_keys = [verified]
        crop["verified"] = True
    elif gap_suspected and not view.missing_keys:
        crop["verified"] = False
    latency = round((time.perf_counter() - t0) * 1000)
    from training.feedback import record_live_mistake, record_stage_timing
    from training.kb import current_repair_id

    record_stage_timing(session, "coach", latency, True)          # measured latency -> Databricks dashboard
    if view.mistake and view.step_index > 0:   # the learning loop: real mistakes go to Databricks
        plan = _demo(session)["plan"]          # generated plans have their own step numbering: log them under their own id
        rid = f"{current_repair_id()}:{plan['fault_signature']}" if plan.get("generated") else current_repair_id()
        record_live_mistake(session, rid, view.step_index, view.mistake, view.what_i_see)
    out = {**view.model_dump(), "latency_ms": latency, "model": COACH_MODEL, "ts": time.time(), "crop": crop}
    _latest.setdefault(session, {})["view"] = out
    return out


class Ask(BaseModel):
    question: str
    current_step: int = 0
    session: str = ""


@router.post("/ask")
async def ask(q: Ask):
    """A free spoken question ("which way does the switch go in?") answered with the current step as context."""
    demo = _demo(q.session)
    steps = demo["plan"]["steps"]
    cur = steps[q.current_step - 1] if 0 < q.current_step <= len(steps) else None
    ctx = _system(demo) + (f"\n\nThe user is on step {cur['index']}: {cur['instruction']}" if cur else "")

    def run() -> str:
        resp = _client().messages.create(
            model=COACH_MODEL, max_tokens=250, thinking=_thinking(COACH_MODEL),
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
                     "sources", "ask", "new_repair", "none"]
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
                "shop_parts (order, buy, price or get a replacement for any part of the device), shop_keyboards "
                "(other/alternative/new keyboards to buy instead), camera_on (watch me), camera_off, calibrate (align the "
                "hologram with the real keyboard, set as zero), show_part (look at a part: fill part), zoom_in, zoom_out, "
                "lights_on, lights_off, specs, sources, ask (a real question about the repair that needs an answer), "
                "new_repair (the person says what is broken or what they want fixed, e.g. 'my space bar rattles', "
                "'the E key is sticky', 'I want to fix my keyboard', 'it won't turn on'), "
                "none (noise, not addressed to Spanner). Fillers like ok, please, hey Spanner are ignored.")
    def run() -> Intent:
        resp = _client().beta.messages.parse(
            model=INTENT_MODEL, max_tokens=120, thinking=_thinking(INTENT_MODEL),
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
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image too large")
    t0 = time.perf_counter()
    p = await asyncio.to_thread(_keyboard_pose, data, session)
    p["latency_ms"] = round((time.perf_counter() - t0) * 1000)
    p["ts"] = time.time()
    _latest.setdefault(session, {})["pose"] = p
    return p


class DeviceReq(BaseModel):
    text: str


class DevicePick(BaseModel):
    device: Literal["keyboard", "iphone15", "momentum4", "ipad11", "plug13a", "none"]
    say: str = Field(default="", description="If none: one short spoken sentence asking again, naming the four options.")


@router.post("/device")
async def device(req: DeviceReq):
    """Free speech -> which device Spanner should open (keyboard repair, or a hologram of the others)."""
    def run() -> DevicePick:
        resp = _client().beta.messages.parse(
            model=INTENT_MODEL, max_tokens=120, thinking=_thinking(INTENT_MODEL),
            system="The user was asked 'What do you want to fix?'. Map their words to one device: keyboard (a mechanical "
                   "keyboard, a dead key, typing), iphone15 (phone, mobile, iPhone), momentum4 (headphones, "
                   "Sennheiser, earcups, headset), ipad11 (iPad, tablet), plug13a (a three-pin mains plug, a plug fuse, "
                   "a blown fuse). If it is none of these or unclear, 'none' with a short friendly question naming the five options.",
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
    # dark modules on a light ground: the polarity every phone scanner reads reliably
    img = q.make_image(fill_color="#101512", back_color="#f2f2e9")
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/net")
async def net():
    """This laptop's address on the local network. A phone can't reach 'localhost', so a viewer opened as localhost asks
    for this to build the pairing QR."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))          # no packet is sent: this only picks the outgoing interface
        ip = s.getsockname()[0]
    except OSError:
        ip = ""
    finally:
        s.close()
    public = ""
    try:                                      # start.sh writes the tunnel address here once cloudflared has one
        public = Path(os.environ.get("PUBLIC_URL_FILE", "")).read_text().strip() if os.environ.get("PUBLIC_URL_FILE") else ""
    except OSError:
        pass
    return {"lan_ip": "" if not ip or ip.startswith("127.") else ip, "public_url": public}


class Signal(BaseModel):
    session: str
    signal: Literal["stop", "end"]     # stop = stop talking (✋); end = the phone hung up the call


@router.post("/signal")
async def signal(sig: Signal):
    """The phone saw a gesture (✋ = stop talking); the laptop picks it up from /coach/latest."""
    _latest.setdefault(sig.session, {})["signal"] = {"signal": sig.signal, "ts": time.time()}
    if sig.signal == "stop":
        _speaking[sig.session] = 0.0   # the laptop is being cut off: don't keep the phone's mic muted for it
    return {"ok": True}


@router.get("/signal/wait")
async def signal_wait(session: str, since: float = 0.0, timeout: float = 20.0):
    """Long poll for the phone's signals, so a tap that cuts Spanner off reaches the laptop in one round trip instead of
    waiting for the next /coach/latest poll. since < 0 only returns the server's clock (to start listening from now)."""
    if since < 0:
        return {"now": time.time(), "signal": None}
    deadline = time.time() + max(1.0, min(timeout, 25.0))
    while time.time() < deadline:
        sig = _latest.get(session, {}).get("signal")
        if sig and sig["ts"] > since:
            return {"now": time.time(), "signal": sig}
        await asyncio.sleep(0.04)
    return {"now": time.time(), "signal": None}


class Speaking(BaseModel):
    session: str
    seconds: float      # how much longer the laptop expects to be talking; 0 = it stopped


@router.post("/speaking")
async def speaking(sp: Speaking):
    """The laptop is talking: the phone pauses its microphone meanwhile, so it doesn't record Spanner's own voice."""
    _speaking[sp.session] = time.time() + max(0.0, min(sp.seconds, 60.0))
    return {"ok": True}


_speaking: dict[str, float] = {}


@router.get("/latest")
async def latest(session: str):
    """The phone's last results for this session: the coach's view, the newest utterance, signals, and whether the
    laptop is talking right now (judged on the server's clock, so phone and laptop clocks don't matter)."""
    st = _view_state.get(session)
    shown = {"screen": st.screen, "screen_step": st.step if st.screen == "step" else 0} if st else {}
    return {**_latest.get(session, {}), **shown, "speaking": _speaking.get(session, 0) > time.time()}


@router.get("/plan")
async def plan(session: str = ""):
    """The session's own plan once Claude has written one. `chosen` is false while the person hasn't said what is wrong:
    then this is only the canned demo plan, as a fallback for old clients, and nothing should start from it."""
    return {**_demo(session), "chosen": session in _plans}

_utt_lock: dict[str, asyncio.Lock] = {}
_utt_seq: dict[str, int] = {}
_utt_carry: dict[str, tuple[str, float]] = {}     # text of clips that were superseded (a sentence the phone split)
CARRY_S = 4.0


def _say_seconds(text: str) -> float:
    """Rough speaking time: about 145 words a minute plus a second. The laptop's /speaking call refines it."""
    return len(text.split()) / 2.4 + 1.0



class UtteranceReq(BaseModel):
    session: str
    audio_b64: str
    filename: str = "clip.m4a"
    forced: bool = False      # the person tapped the screen to talk to Spanner: this is for it, never an echo or room talk


async def _warm_voice(text: str) -> None:
    """Generate and cache the spoken line now, so /coach/say answers from disk when the laptop asks for it."""
    from .voice import tts

    try:
        if tts.available():
            async for _ in tts.stream(text):
                pass
    except Exception:
        pass


@router.post("/utterance")
async def utterance(req: UtteranceReq):
    """Speech from the phone's microphone (one m4a clip per utterance). It is transcribed here (ElevenLabs Scribe) and
    handed to the laptop viewer through /coach/latest, so the phone listens and the laptop speaks back."""
    from .voice import stt

    try:
        audio = base64.b64decode(req.audio_b64, validate=False)
    except Exception:
        raise HTTPException(400, "bad audio")
    if not audio or len(audio) > 8_000_000:
        raise HTTPException(400, "empty or too long")
    t_in = time.perf_counter()
    try:
        tr = await stt.transcribe(audio, req.filename)
    except Exception as e:
        if "speech_to_text" in str(e) and "permission" in str(e):
            raise HTTPException(502, "the ElevenLabs key needs the Speech to Text permission turned on")
        raise HTTPException(502, f"couldn't transcribe: {str(e)[:200]}")
    text = tr.text.strip()
    if not text:
        return {"text": ""}
    turn_id = f"{time.time():.3f}"
    _latest.setdefault(req.session, {})["utterance"] = {"text": text, "id": turn_id, "ts": time.time()}
    # Straight on to the reply (no waiting for the laptop to notice the words): what to do + what to say, in one go.
    t_stt = time.perf_counter()
    turn = _quick_turn(text, req.session) or await asyncio.to_thread(_converse, text, req.session, req.forced)
    if req.forced:
        turn.addressed = True
    print(f"utterance: stt {1000 * (t_stt - t_in):.0f} ms, reply {1000 * (time.perf_counter() - t_stt):.0f} ms", flush=True)
    if turn.say.strip():                      # have the voice ready by the time the laptop asks for it
        asyncio.create_task(_warm_voice(turn.say.strip()[:600]))
    if turn.addressed and turn.say.strip():   # Spanner is about to speak: the phone mutes its mic from now, not once the audio starts
        _speaking[req.session] = max(_speaking.get(req.session, 0.0), time.time() + _say_seconds(turn.say))
    _latest[req.session]["reply"] = {**turn.model_dump(), "text": text, "id": turn_id, "ts": time.time(), "forced": req.forced}
    return {"text": text, "reply": {**turn.model_dump(), "forced": req.forced}}


# ---------------------------------------------------------------- conversation (phone mic -> one reply + action)
class Turn(BaseModel):
    addressed: bool = Field(description="False if the words were background talk or not meant for Spanner.")
    say: str = Field(default="", description="One or two short spoken sentences, or empty.")
    action: Literal["none", "pick_device", "plan_repair", "start", "next", "back", "repeat", "stop", "show_part",
                    "take_apart", "reassemble", "xray", "zoom_in", "zoom_out", "show_mistake", "specs", "sources",
                    "shop_parts", "shop_keyboards"] = "none"
    part: str = Field(default="", description="For show_part: socket, switch, keycap, pcb, plate, case, battery, usb, led, stabilizer, spring")
    device: Literal["", "keyboard", "iphone15", "momentum4", "ipad11", "plug13a"] = ""
    complaint: str = Field(default="", description="For plan_repair: the fault, in the person's words.")


class ViewState(BaseModel):
    session: str
    screen: Literal["pairing", "picking", "ready", "step", "done"] = "ready"
    step: int = 0                  # 1-based step on screen, 0 = none
    total: int = 0
    title: str = ""


_view_state: dict[str, ViewState] = {}
_history: dict[str, list[dict]] = {}      # recent conversation per session (user / assistant turns)
CONVERSE_MODEL = os.getenv("CONVERSE_MODEL", "claude-haiku-5-5")
_LOOK = re.compile(r"\b(see|look|looking|show|camera|view|picture|this|that|these|those|here|which|what'?s|what is|visible|holding)\b", re.I)


@router.post("/state")
async def view_state(st: ViewState):
    """The laptop viewer reports what is on screen, so a spoken turn is answered in context."""
    _view_state[st.session] = st
    return {"ok": True}


# Unmistakable short commands skip Claude entirely (instant).
_QUICK = [(r"(next|continue|go on|done|finished|got it|ok next)", "next"), (r"(back|previous|go back)", "back"),
          (r"(stop|quiet|shut up|be quiet|pause|hold on|enough)", "stop"), (r"(repeat|say (that|it) again|again)", "repeat"),
          (r"(zoom in|closer)", "zoom_in"), (r"(zoom out|further)", "zoom_out"), (r"(take it apart|explode)", "take_apart"),
          (r"(put it back( together)?|reassemble)", "reassemble"), (r"(x-?ray)", "xray"), (r"(start|let'?s (go|start|begin)|begin)", "start")]


def _quick_turn(text: str, session: str) -> Turn | None:
    t = re.sub(r"[^a-z' -]", "", text.lower()).strip()
    t = re.sub(r"^(ok(ay)?|hey|spanner|please|um+|uh+|so|and|now)[ ,]+", "", t).strip()
    if not t or len(t.split()) > 4 or _view_state.get(session, ViewState(session=session)).screen in ("pairing", "picking"):
        return None
    for rx, action in _QUICK:
        if re.fullmatch(rf"{rx}( please)?", t):
            return Turn(addressed=True, action=action)
    return None


def _plan_text(session: str) -> str:
    """The repair plan as text. It only changes when a new plan is made, so it sits in the cached part of the prompt."""
    if session not in _plans:
        return "No repair chosen yet: they haven't said what is wrong."
    plan = _plans[session]["plan"]
    lines = [f"The repair: {plan.get('title') or plan['fault']} ({len(plan['steps'])} steps). Diagnosis: {plan.get('diagnosis', '')}"]
    for s in plan["steps"]:
        lines.append(f"{s['index']}. {s['instruction']} (done when: {s['expected_visual_state']}; mistakes: "
                     f"{'; '.join(s.get('common_mistakes', [])[:2])})")
    return "\n".join(lines)


def _context(session: str) -> str:
    """What changes every turn: the screen, the step on it, what the camera saw."""
    st = _view_state.get(session, ViewState(session=session))
    dp = _device_parts.get(session)
    dev = f" Device open: {dp['device']}. Its parts (use these exact names for show_part): " + ", ".join(p["name"] for p in dp["parts"]) + "." if dp else ""
    lines = [f"Screen: {st.screen}.{dev}" + (f" Step {st.step} of {st.total} is on screen." if st.screen == "step" else "")]
    v = _latest.get(session, {}).get("view")
    if v and v.get("what_i_see"):
        lines.append(f"Camera ({max(0, round(time.time() - v['ts']))} s ago): {v['what_i_see']}"
                     + (f" Possible mistake: {v['mistake']}" if v.get("mistake") else ""))
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _converse_prompt() -> str:
    return (Path(__file__).parent / "brain" / "prompts" / "converse.md").read_text(encoding="utf-8")


def _converse(text: str, session: str, forced: bool = False) -> Turn:
    hist = _history.setdefault(session, [])
    body = f"[live state]\n{_context(session)}\n\n[they said] {text}"
    if forced:
        body += "\n\n[they tapped the screen to talk to you: these words are addressed to you]"
    pic = _last_frame.get(session)
    if pic and time.time() - pic[1] < 10 and (session not in _plans or _LOOK.search(text)):
        # a question about what is in view (or no repair yet, so nothing else has looked): answer from the picture itself
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(pic[0]).decode()}},
                   {"type": "text", "text": "[the camera's latest picture is above: answer questions about what is in view from it]\n" + body}]
    else:
        content = body
    messages = hist[-8:] + [{"role": "user", "content": content}]
    try:
        resp = _client().beta.messages.parse(
            model=CONVERSE_MODEL, max_tokens=400, thinking=_thinking(CONVERSE_MODEL), output_config={"effort": "low"},
            system=[{"type": "text", "text": _converse_prompt()},
                    {"type": "text", "text": _plan_text(session), "cache_control": {"type": "ephemeral"}}],
            messages=messages, output_format=Turn)
        turn = resp.parsed_output or Turn(addressed=True, say="Say that once more?")
    except Exception as e:
        print(f"converse failed: {e}", flush=True)
        return Turn(addressed=True, say="Sorry, I didn't catch that. Say it again?")
    if turn.addressed:        # remember the exchange (plain text, so later turns don't carry old state)
        hist += [{"role": "user", "content": text},
                 {"role": "assistant", "content": turn.say or f"({turn.action.replace('_', ' ')})"}]
        del hist[:-12]
    return turn


class PlanReq(BaseModel):
    session: str
    complaint: str
    device: str = "keyboard"


class DevicePartsReq(BaseModel):
    session: str
    device: str
    parts: list[dict]            # [{name, label, desc, group}] as the hologram page built them


_device_parts: dict[str, dict] = {}


@router.post("/device/parts")
async def device_parts(req: DevicePartsReq):
    """The device hologram reports its real parts, so Claude plans the repair with parts that exist on that model."""
    old = _plans.get(req.session)
    if old and old["plan"].get("device", "keyboard") != req.device:      # a different device: the old repair no longer applies
        _plans.pop(req.session, None)
        _history.pop(req.session, None)
    _device_parts[req.session] = {"device": req.device, "parts": req.parts[:80]}
    if req.device != "keyboard" and os.getenv("ANTHROPIC_API_KEY"):      # write the common-fault plans now, in the background
        from .device_planner import warm

        asyncio.get_running_loop().run_in_executor(None, warm, req.device, req.parts[:80])
    return {"ok": True, "parts": len(req.parts)}


@router.post("/plan/generate")
async def generate_plan(req: PlanReq):
    """What the user said they want fixed -> a Claude-written plan for that fault (slow: 5-10 s with the real model).

    status: ready (plan + parts_card + say), clarify (ask the user `say`), refused (unsafe / out of scope),
    fallback (couldn't write one; the canned plan stays and `say` explains). Only `ready` replaces the session's plan."""
    from .planner import plan_repair

    t0 = time.perf_counter()
    try:
        dp = _device_parts.get(req.session, {})
        parts = dp.get("parts") if dp.get("device") == req.device else None
        out = await asyncio.to_thread(plan_repair, req.complaint, req.session, req.device, parts)
    except Exception as e:                       # API down, bad key...: keep the session usable on the canned plan
        say = ("I couldn't plan that just now, so here is the standard switch repair." if req.device == "keyboard"
               else "I couldn't plan that just now. Tell me again what is wrong and I'll try once more.")     # never a keyboard repair for a plug
        out = {"status": "fallback", "say": say, "errors": [f"{type(e).__name__}: {e}"]}
    if out["status"] == "ready":
        _plans[req.session] = {"broken_key": out.get("broken_key"), "plan": out["plan"], "parts_card": out.get("parts_card"),
                               "plan_id": out["plan_id"]}
        _latest.setdefault(req.session, {})["plan_id"] = out["plan_id"]
    from training.feedback import record_stage_timing

    record_stage_timing(req.session, "planner", round((time.perf_counter() - t0) * 1000), out["status"] == "ready")
    return out


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

    @app.middleware("http")
    async def _viewer_no_stale_cache(request, call_next):
        """The viewer pages and scripts change often: always revalidate, so a browser never runs an old copy
        (an old device.js against a new page was showing the previous UI)."""
        resp = await call_next(request)
        if request.url.path.startswith("/training/viewer/") and not request.url.path.startswith("/training/viewer/vendor/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.on_event("startup")
    async def _warm_plans() -> None:   # every fault class planned before anyone asks: planning becomes instant
        from .planner import warm

        asyncio.get_running_loop().run_in_executor(None, warm)

    app.mount("/data/models", StaticFiles(directory=ROOT / "data" / "models"), name="models")
    app.mount("/training/viewer", StaticFiles(directory=ROOT / "training" / "viewer", html=True), name="viewer")
