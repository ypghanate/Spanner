"""Preprocess stage (real): decode -> fix size -> sharpness, brightness, keyframe.

A frame is a keyframe when the scene changed enough since the last keyframe (mean abs diff of a tiny
grayscale thumbnail) or 5 s have passed. Blurry or dark frames are never keyframes, so the slow loop
(Claude vision) only spends time on frames worth looking at.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from backend.contracts import FrameQuality
from backend.frames import encode_jpeg, resize_long_edge, sharpness, thumb

BLUR_MIN = 60.0          # variance of Laplacian below this = motion blur
DARK_MAX = 45.0          # mean grey level below this = too dark
CHANGE_MIN = 12.0        # mean abs thumbnail diff that counts as a new scene
KEYFRAME_EVERY_S = 5.0

_last: dict[str, tuple[np.ndarray, float]] = {}


async def preprocess(jpeg: bytes, session_id: str) -> tuple[bytes, FrameQuality]:
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return jpeg, FrameQuality(blurry=True, too_dark=True, is_keyframe=False, sharpness=0.0, brightness=0.0)
    img = resize_long_edge(img, 768)
    sharp = sharpness(img)
    bright = float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean())
    blurry, dark = sharp < BLUR_MIN, bright < DARK_MAX
    t = thumb(img)
    now = time.monotonic()
    prev = _last.get(session_id)
    changed = prev is None or float(np.abs(t - prev[0]).mean()) >= CHANGE_MIN or now - prev[1] >= KEYFRAME_EVERY_S
    key = changed and not blurry and not dark
    if key:
        _last[session_id] = (t, now)
    return encode_jpeg(img, long_edge=768), FrameQuality(
        blurry=blurry, too_dark=dark, is_keyframe=key, sharpness=round(sharp, 1), brightness=round(bright, 1))
