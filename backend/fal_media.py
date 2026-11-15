"""On your keyboard: fal redraws the user's OWN camera frame doing the current repair step, then animates it.

For each (session, step, key) Spanner takes the latest keyboard crop the camera sent to /coach/frame, rings the
broken key where the coach located it, and asks a fal image-edit model to show that exact photo with a hand doing
the step on that key. The still then goes to a fal image-to-video model for a short loop. Both are generated in
the background as soon as a step starts and shown only when the user asks ("show me on mine").

No stand-ins: a job is a real fal call, or an honest state (waiting_frame, disabled, failed with the error).

    POST /coach/visualize  {session, step, key, label}   start (idempotent) -> status
    GET  /coach/visualize?session&step&key&label         status: waiting_frame | generating | image_ready | ready | failed | disabled
Models were picked by a live test on a real RK61 photo (15 Nov): Nano Banana edit kept the board and every legend
intact (~12 s); Kling 2.1 standard kept the board still while the hand moved (~2 min for 5 s).
"""
from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "data" / "media"
EDIT_MODEL = os.getenv("FAL_EDIT_MODEL", "fal-ai/nano-banana/edit")
VIDEO_MODEL = os.getenv("FAL_VIDEO_MODEL", "fal-ai/kling-video/v2.1/standard/image-to-video")
MAX_JOBS = int(os.getenv("FAL_MAX_JOBS", "24"))           # per session: protects the credits
GAP_MAX_AGE_S = 600                                      # a located gap older than this is not trusted for the ring

router = APIRouter(prefix="/coach", tags=["fal"])
_jobs: dict[tuple[str, int, str], dict] = {}
_count: dict[str, int] = {}
_lock = threading.Lock()

# steps that act on the broken key itself (ring it); the others are about the cable or the spare switch
KEY_STEPS = {"keycap-pull", "puller-place", "switch-pull", "socket-inspect", "switch-align", "switch-insert",
             "switch-test", "keycap-place", "cable-in-test"}
# the one motion of each step, for the clip
MOTION = {
    "cable-out": "the hand pulls the USB-C cable straight out of the keyboard's port",
    "keycap-pull": "the hand lifts the keycap puller straight up, pulling the keycap off the switch",
    "puller-place": "the hand lowers the two prongs of the switch puller onto the switch's top and bottom clips",
    "switch-pull": "the hand squeezes the switch puller and lifts the switch straight up out of the plate",
    "socket-inspect": "the camera holds still while a short puff of compressed air clears the empty socket",
    "new-switch-inspect": "the fingers press the new switch's stem down a few times and it springs back",
    "switch-align": "the hand lowers the new switch over the empty hole, pins lined up with the socket",
    "switch-insert": "a finger presses the switch straight down until it clicks flush into the plate",
    "switch-test": "a finger presses the switch stem a few times and it springs back smoothly",
    "keycap-place": "the hand lines the keycap up with the stem and presses it straight down",
    "cable-in-test": "the hand plugs the USB-C cable back in and a finger taps the repaired key",
}


def enabled() -> bool:
    return bool(os.getenv("FAL_KEY")) and os.getenv("FAL_ENABLED", "1") not in ("0", "false", "False")


def _latest() -> dict:
    from .coach import _latest as latest

    return latest


def _step(step: int) -> dict:
    from .coach import _demo

    steps = _demo()["plan"]["steps"]
    if not 1 <= step <= len(steps):
        raise HTTPException(400, f"step must be 1..{len(steps)}")
    return steps[step - 1]


def _ring(jpeg: bytes, x: float, y: float) -> bytes:
    """A magenta ring on the broken key, so the edit model acts on THAT key (it misses without one)."""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    cv2.circle(img, (int(x * w), int(y * h)), max(12, int(0.035 * w)), (255, 0, 255), max(3, w // 300))
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()


def _edit_prompt(s: dict, label: str, ringed: bool) -> str:
    where = (f"the {label} key, marked by the magenta ring" if ringed else f"the {label} key") \
        if s.get("kind") in KEY_STEPS else "the keyboard"
    return (f"This is a live photo of the user's own mechanical keyboard during a repair (the broken key is {label}). "
            f"Edit the photo to show this repair step being done right now, on {where}: \"{s['instruction']}\" "
            "Show a person's hand and the tool the step needs, doing exactly that. "
            + ("Remove the magenta ring. " if ringed else "")
            + "Keep the keyboard, every other keycap and legend, the colours, the camera angle and the lighting exactly "
              "as in the photo. Photorealistic. Do not add any text, arrows or labels.")


def _video_prompt(s: dict) -> str:
    return (f"{MOTION.get(s.get('kind', ''), 'the hand slowly does the repair step')}. Only the hand and the tool move, "
            "slowly and clearly; the keyboard and the camera stay completely still. Realistic close-up.")


def _record(session: str, stage: str, ms: int, ok: bool) -> None:
    try:
        from training.feedback import record_stage_timing

        record_stage_timing(session, stage, ms, ok)     # -> Databricks stage_timings, next to the coach's latency
    except Exception:
        pass


def _run(job: dict, session: str, s: dict, label: str, frame: dict, gap: dict | None) -> None:
    import fal_client
    import httpx

    out = MEDIA / re.sub(r"[^A-Za-z0-9_-]", "_", session)
    out.mkdir(parents=True, exist_ok=True)
    base = f"step{s['index']:02d}_{re.sub(r'[^A-Za-z0-9]', '_', label)}"
    ringed = bool(gap) and s.get("kind") in KEY_STEPS
    try:
        src = _ring(gap["jpeg"], gap["x"], gap["y"]) if ringed else frame["jpeg"]
        t0 = time.perf_counter()
        url = fal_client.upload(src, "image/jpeg")
        r = fal_client.subscribe(EDIT_MODEL, arguments={"prompt": _edit_prompt(s, label, ringed), "image_urls": [url]})
        img_url = (r.get("images") or [r.get("image")])[0]["url"]
        (out / f"{base}.png").write_bytes(httpx.get(img_url, timeout=60).content)
        job.update(status="image_ready", image_url=f"/data/media/{out.name}/{base}.png", fal_image_url=img_url,
                   image_ms=round((time.perf_counter() - t0) * 1000), ringed=ringed)
        _record(session, "fal_image", job["image_ms"], True)
    except Exception as e:
        job.update(status="failed", error=f"image: {str(e)[:240]}")
        _record(session, "fal_image", round((time.perf_counter() - job["t0"]) * 1000), False)
        return
    try:
        t1 = time.perf_counter()
        r = fal_client.subscribe(VIDEO_MODEL, arguments={"prompt": _video_prompt(s), "image_url": img_url, "duration": "5"})
        (out / f"{base}.mp4").write_bytes(httpx.get(r["video"]["url"], timeout=180).content)
        job.update(status="ready", video_url=f"/data/media/{out.name}/{base}.mp4",
                   video_ms=round((time.perf_counter() - t1) * 1000))
        _record(session, "fal_video", job["video_ms"], True)
    except Exception as e:      # the still is real and stays usable; say what went wrong with the clip
        job.update(status="image_ready", video_error=str(e)[:240])
        _record(session, "fal_video", round((time.perf_counter() - t1) * 1000), False)


def ensure(session: str, step: int, key: str, label: str) -> dict:
    """Start the job for this step if it can start; always return its current public state."""
    if not enabled():
        return {"status": "disabled", "error": "FAL_KEY is not set"}
    s = _step(step)
    k = (session, step, key.upper())
    with _lock:
        job = _jobs.get(k)
        retry = job and job["status"] == "failed" and time.perf_counter() - job["t0"] > 20
        if job and job["status"] != "waiting_frame" and not retry:
            return _public(job)
        latest = _latest().get(session, {})
        frame = latest.get("frame")
        if not frame:
            _jobs[k] = job = {"status": "waiting_frame", "t0": time.perf_counter()}
            return _public(job)
        if _count.get(session, 0) >= MAX_JOBS:
            return {"status": "failed", "error": f"limit of {MAX_JOBS} generations per session reached"}
        _count[session] = _count.get(session, 0) + 1
        gap = latest.get("gap")
        if gap and (gap.get("key", "").upper() != label.upper() or time.time() - gap["ts"] > GAP_MAX_AGE_S):
            gap = None
        _jobs[k] = job = {"status": "generating", "t0": time.perf_counter(), "step": step, "key": label,
                          "frame_ts": frame["ts"], "edit_model": EDIT_MODEL, "video_model": VIDEO_MODEL}
    threading.Thread(target=_run, args=(job, session, s, label, frame, gap), daemon=True).start()
    return _public(job)


def _public(job: dict) -> dict:
    keep = ("status", "image_url", "video_url", "error", "video_error", "image_ms", "video_ms", "ringed", "step", "key",
            "edit_model", "video_model")
    out = {k: job[k] for k in keep if k in job}
    out["elapsed_s"] = round(time.perf_counter() - job["t0"], 1)
    return out


def remember_frame(session: str, jpeg: bytes, view) -> None:
    """Called by /coach/frame with the keyboard crop it already made: the newest frame, plus the frame where the
    broken key was last located (so the ring lands on the gap even if the camera has moved since)."""
    latest = _latest().setdefault(session, {})
    latest["frame"] = {"jpeg": jpeg, "ts": time.time()}
    if view.missing_keys and view.gap_x is not None and view.gap_y is not None \
            and 0 <= view.gap_x <= 1 and 0 <= view.gap_y <= 1:
        latest["gap"] = {"key": view.missing_keys[0], "x": view.gap_x, "y": view.gap_y, "jpeg": jpeg, "ts": time.time()}


class VisualizeReq(BaseModel):
    session: str
    step: int
    key: str = "F"
    label: str = ""


@router.post("/visualize")
async def visualize(req: VisualizeReq):
    return await asyncio.to_thread(ensure, req.session, req.step, req.key, req.label or req.key)


@router.get("/visualize")
async def visualize_status(session: str, step: int, key: str = "F", label: str = ""):
    return await asyncio.to_thread(ensure, session, step, key, label or key)
