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
COACH_MODEL = os.getenv("COACH_MODEL", "claude-sonnet-5-5")  # Sonnet reads key legends far more reliably than Haiku (~3 s, thinking off)
FAST_MODEL = os.getenv("COACH_FAST_MODEL", "claude-haiku-5-5")  # spoken-command intents, device choice, short answers: ~0.6 s
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


def _system(demo: dict) -> str:
    plan = demo["plan"]
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


def _keyboard_crop(data: bytes, max_w: int = 1280) -> tuple[bytes, dict]:
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
    if cw > max_w:
        img = cv2.resize(img, (max_w, int(ch * max_w / cw)), interpolation=cv2.INTER_AREA)
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


def _key_confirmed(session: str) -> bool:
    g = _gap_cache.get(session)
    return bool(g and g["n"] >= 2 and time.time() - g["ts"] < GAP_CACHE_TTL_S)


def _ask(image_b64: str, current_step: int, session: str = "", model: str | None = None) -> CoachView:
    """One frame -> the step. Measured 15 Nov on real frames: Sonnet 2.6 s at 1280 px, 2.2 s at 1024 px; Haiku 1.8 s but it
    misread the missing key on 2 of 3 frames. Sonnet stays; the image shrinks once the key is confirmed (no legends to read)."""
    model = model or COACH_MODEL
    demo = _demo(session)
    hint = (f"The screen currently shows step {current_step}." if current_step > 0
            else "The repair has not started on screen yet.")
    resp = _client().beta.messages.parse(
        model=model, max_tokens=2048,
        system=[{"type": "text", "text": _system(demo), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": hint + " Which step is the user on?"}]}],
        output_format=CoachView, output_config={"effort": "low"},
        thinking=_thinking(model),   # one frame -> one label: thinking only adds latency
        **({"betas": [FALLBACK_BETA], "fallbacks": "default"} if "haiku" not in model else {}))
    if resp.parsed_output is None:
        raise HTTPException(502, f"vision model gave no answer (stop_reason={resp.stop_reason})")
    out = resp.parsed_output
    out.step_index = max(0, min(out.step_index, len(demo["plan"]["steps"])))
    resolved = resolve_gap(out)
    if resolved:                                   # neighbours beat a read of the absent legend
        out.missing_keys = [resolved]
    elif out.missing_keys and (out.gap_left or out.gap_right):
        out.missing_keys = []                      # neighbours given but inconsistent with the layout: don't trust the guess
    out.confidence = max(0.0, min(out.confidence, 1.0))
    return out


MAX_IMAGE_BYTES = 8 * 1024 * 1024
_frame_inflight: set[str] = set()        # sessions with a vision call running: a second frame is dropped, not queued


_previews: dict[str, bytes] = {}      # newest phone frame per session (JPEG), shown live in the viewer
# The missing key, once the zoomed second look has confirmed it twice, holds for the rest of the repair: the gap stays
# visible from the keycap pull to the keycap place, and re-reading it on every frame cost a second Sonnet call (~3 s).
_gap_cache: dict[str, dict] = {}
GAP_CACHE_TTL_S = 180


def _cached_gap(session: str, first_pass: str | None) -> str | None:
    g = _gap_cache.get(session)
    if not g or g["n"] < 2 or time.time() - g["ts"] > GAP_CACHE_TTL_S:
        return None
    return g["key"] if first_pass in (None, g["key"]) else None     # a different first-pass read re-opens the question


def _remember_gap(session: str, key: str) -> None:
    g = _gap_cache.get(session)
    _gap_cache[session] = {"key": key, "n": g["n"] + 1 if g and g["key"] == key else 1, "ts": time.time()}
    if _gap_cache[session]["n"] >= 2:            # confirmed: the second look is no longer needed, so its misses are forgotten
        _verify_miss.pop(session, None)


# When neither pass can name the key, the second look fails frame after frame (a hand over the gap, a bad angle) and
# costs ~2 s each time. After two misses in a row it rests for a while; the first pass alone keeps the step tracking.
_verify_miss: dict[str, dict] = {}
VERIFY_REST_S = 45


def _verify_resting(session: str) -> bool:
    m = _verify_miss.get(session)
    return bool(m and m["n"] >= 2 and time.time() - m["ts"] < VERIFY_REST_S)


def _verify_missed(session: str) -> None:
    m = _verify_miss.get(session)
    n = m["n"] + 1 if m and time.time() - m["ts"] < 2 * VERIFY_REST_S else 1
    _verify_miss[session] = {"n": n, "ts": time.time()}


@router.get("/preview.jpg")
async def preview(session: str):
    from fastapi.responses import Response

    data = _previews.get(session)
    if not data:
        raise HTTPException(404, "no frame yet")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/frame")
async def frame(image: UploadFile = File(...), current_step: int = Form(0), session: str = Form("laptop")):
    data = await image.read()
    if not data:
        raise HTTPException(400, "empty image")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image too large")
    if session in _frame_inflight:       # the previous frame is still being read: never stack Claude calls behind it
        raise HTTPException(429, "still reading the previous frame")
    _previews[session] = data            # what the phone sees, for the laptop viewer's camera panel
    _latest.setdefault(session, {})["alive"] = time.time()     # the phone is sending: liveness, before any slow call
    if session not in _plans:
        # The person hasn't said what is wrong yet, so there is no repair to check frames against (and no Claude call
        # to pay for). Answer with an idle view so the phone link still shows as live.
        out = {"object_visible": False, "what_i_see": "", "step_index": 0, "step_status": "not_started", "mistake": "",
               "confidence": 0.0, "say": "", "latency_ms": 0, "model": COACH_MODEL, "ts": time.time()}
        _latest.setdefault(session, {})["view"] = out
        return out
    _frame_inflight.add(session)
    t0 = time.perf_counter()
    timing: dict[str, int] = {}
    try:
        confirmed = _key_confirmed(session)
        data, crop = await asyncio.to_thread(_keyboard_crop, data, 1024 if confirmed else 1280)
        timing["crop"] = round((time.perf_counter() - t0) * 1000)
        crop["model"] = COACH_MODEL
        t1 = time.perf_counter()
        view = await asyncio.to_thread(_ask, base64.standard_b64encode(data).decode(), current_step, session)
        timing["ask"] = round((time.perf_counter() - t1) * 1000)
        gap_suspected = bool(view.missing_keys or view.gap_left or view.gap_right or view.gap_x is not None)
        first_pass = resolve_gap(view)               # a layout-validated read (both neighbours in one row) counts
        if first_pass and (not confirmed or first_pass == _gap_cache[session]["key"]):
            _remember_gap(session, first_pass)       # a confirmed key is only ever changed by the zoomed second look
        cached = _cached_gap(session, first_pass) if gap_suspected else None
        verified = None
        if gap_suspected and not cached and not _verify_resting(session):
            t2 = time.perf_counter()
            verified = await asyncio.to_thread(verify_gap, data, view)
            timing["verify"] = round((time.perf_counter() - t2) * 1000)
            if verified:
                _remember_gap(session, verified)
            else:
                _verify_missed(session)
        elif gap_suspected and not cached:
            crop["verify_resting"] = True
    finally:
        _frame_inflight.discard(session)
    if cached:
        view.missing_keys = [cached]
        crop["verified"] = "cached"
    elif verified:
        view.missing_keys = [verified]
        crop["verified"] = True
    elif gap_suspected and not view.missing_keys:
        crop["verified"] = False
    crop["timing_ms"] = timing
    latency = round((time.perf_counter() - t0) * 1000)
    from training.feedback import record_live_mistake, record_stage_timing
    from training.kb import current_repair_id

    record_stage_timing(session, "coach", latency, True)          # measured latency -> Databricks dashboard
    if view.mistake and view.step_index > 0:   # the learning loop: real mistakes go to Databricks
        plan = _demo(session)["plan"]          # generated plans have their own step numbering: log them under their own id
        rid = f"{current_repair_id()}:{plan['fault_signature']}" if plan.get("generated") else current_repair_id()
        record_live_mistake(session, rid, view.step_index, view.mistake, view.what_i_see)
    out = {**view.model_dump(), "latency_ms": latency, "model": crop.get("model", COACH_MODEL), "ts": time.time(), "crop": crop}
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
            model=FAST_MODEL, max_tokens=250, thinking=_thinking(FAST_MODEL),
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
                "shop_parts (buy/replace/price of a part: keycaps, switches, stabilizers, cable), shop_keyboards "
                "(other/alternative/new keyboards to buy instead), camera_on (watch me), camera_off, calibrate (align the "
                "hologram with the real keyboard, set as zero), show_part (look at a part: fill part), zoom_in, zoom_out, "
                "lights_on, lights_off, specs, sources, ask (a real question about the repair that needs an answer), "
                "new_repair (the person says what is broken or what they want fixed, e.g. 'my space bar rattles', "
                "'the E key is sticky', 'I want to fix my keyboard', 'it won't turn on'), "
                "none (noise, not addressed to Spanner). Fillers like ok, please, hey Spanner are ignored.")
    def run() -> Intent:
        resp = _client().beta.messages.parse(
            model=FAST_MODEL, max_tokens=120, thinking=_thinking(FAST_MODEL),
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
    device: Literal["keyboard", "iphone15", "momentum4", "ipad11", "none"]
    say: str = Field(default="", description="If none: one short spoken sentence asking again, naming the four options.")


@router.post("/device")
async def device(req: DeviceReq):
    """Free speech -> which device Spanner should open (keyboard repair, or a hologram of the others)."""
    def run() -> DevicePick:
        resp = _client().beta.messages.parse(
            model=FAST_MODEL, max_tokens=120, thinking=_thinking(FAST_MODEL),
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
    return {"lan_ip": "" if not ip or ip.startswith("127.") else ip}


class Signal(BaseModel):
    session: str
    signal: Literal["stop"]


@router.post("/signal")
async def signal(sig: Signal):
    """The phone saw a gesture (✋ = stop talking); the laptop picks it up from /coach/latest."""
    _latest.setdefault(sig.session, {})["signal"] = {"signal": sig.signal, "ts": time.time()}
    return {"ok": True}


@router.get("/latest")
async def latest(session: str):
    """The phone's last results for this session: the coach's view (every ~2 s) and the keyboard pose (~4/s)."""
    return _latest.get(session, {})


@router.get("/plan")
async def plan(session: str = ""):
    """The session's own plan once Claude has written one. `chosen` is false while the person hasn't said what is wrong:
    then this is only the canned demo plan, as a fallback for old clients, and nothing should start from it."""
    return {**_demo(session), "chosen": session in _plans}


class UtteranceReq(BaseModel):
    session: str
    audio_b64: str
    filename: str = "clip.m4a"


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
    try:
        tr = await stt.transcribe(audio, req.filename)
    except Exception as e:
        if "speech_to_text" in str(e) and "permission" in str(e):
            raise HTTPException(502, "the ElevenLabs key needs the Speech to Text permission turned on")
        raise HTTPException(502, f"couldn't transcribe: {str(e)[:200]}")
    text = tr.text.strip()
    if text:
        _latest.setdefault(req.session, {})["utterance"] = {"text": text, "id": f"{time.time():.3f}", "ts": time.time()}
    return {"text": text}


class PlanReq(BaseModel):
    session: str
    complaint: str


@router.post("/plan/generate")
async def generate_plan(req: PlanReq):
    """What the user said they want fixed -> a Claude-written plan for that fault (slow: 5-10 s with the real model).

    status: ready (plan + parts_card + say), clarify (ask the user `say`), refused (unsafe / out of scope),
    fallback (couldn't write one; the canned plan stays and `say` explains). Only `ready` replaces the session's plan."""
    from .planner import plan_repair

    t0 = time.perf_counter()
    try:
        out = await asyncio.to_thread(plan_repair, req.complaint, req.session)
    except Exception as e:                       # API down, bad key...: keep the session usable on the canned plan
        out = {"status": "fallback", "say": "I couldn't plan that just now, so here is the standard switch repair.",
               "errors": [f"{type(e).__name__}: {e}"]}
    if out["status"] == "ready":
        _plans[req.session] = {"broken_key": out["broken_key"], "plan": out["plan"], "parts_card": out.get("parts_card"),
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

    import logging

    http_log = logging.getLogger("spanner.http")
    if not http_log.handlers:
        h = logging.StreamHandler(); h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S")); http_log.addHandler(h); http_log.setLevel(logging.INFO); http_log.propagate = False
    QUIET = ("/coach/latest", "/coach/preview.jpg", "/coach/health", "/training/viewer/", "/data/models/")

    @app.middleware("http")
    async def _log_requests(request, call_next):   # what the demo actually did: every real call with its status and time
        t0 = time.perf_counter()
        resp = await call_next(request)
        path = request.url.path
        if not any(path.startswith(q) for q in QUIET):
            http_log.info("%s %s %s %dms", request.method, path, resp.status_code, round((time.perf_counter() - t0) * 1000))
        return resp

    @app.on_event("startup")
    async def _warm_detector() -> None:   # the coach's keyboard crop needs YOLO loaded: the first frame must not pay 15 s
        from backend.vision import detect as det

        asyncio.get_running_loop().run_in_executor(None, det.warm)

    @app.on_event("startup")
    async def _warm_shop() -> None:
        if os.getenv("SHOP_WARMUP", "1") == "1" and os.getenv("LINKUP_API_KEY"):   # live Linkup + Tavily, cached for the demo
            from .shop import warm as warm_shop

            asyncio.get_running_loop().run_in_executor(None, warm_shop)
    app.mount("/data/models", StaticFiles(directory=ROOT / "data" / "models"), name="models")
    app.mount("/training/viewer", StaticFiles(directory=ROOT / "training" / "viewer", html=True), name="viewer")
