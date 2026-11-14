"""The fast loop's detector: YOLO + ByteTrack on one JPEG, in the contract's shape.

    detect(jpeg: bytes, session_id: str) -> list[Detection]     # normalised 0-1 boxes, stable track ids
    warm() -> None                                              # load weights, run one dummy frame

This wraps the same model and tracker as backend/compVisionModel.py (Yash's camera script), without its
camera loop or windows, so the server can import it. Never import compVisionModel from the server: it
opens the webcam as soon as it is imported.

One shared model serves every session. ByteTrack keeps its state on the model, so the tracker is reset
whenever a different session sends a frame (two phones alternating would reset each other's track ids;
the demo has one phone).

Stock yolov8n only knows the 80 COCO classes, so it will say "bicycle", not "brake pad". Part-level
labels need a fine-tuned or open-vocabulary model: point YOLO_WEIGHTS at one and nothing else changes.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import cv2
import numpy as np

from ..contracts import Box, Detection

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEIGHTS = ROOT / "backend" / "yolov8n.pt"
TRACKER = "bytetrack.yaml"
SMOOTH = 0.5                 # weight kept from a track's previous box: higher = boxes glide more
FORGET_AFTER_FRAMES = 30     # drop a track's smoothing state after this many frames unseen

_lock = threading.Lock()     # a YOLO model is not thread-safe
_model = None


class _State:
    """Per-session tracker bookkeeping, reset when the session changes."""

    def __init__(self) -> None:
        self.session: str | None = None
        self.frame = 0
        self.boxes: dict[int, tuple[float, float, float, float]] = {}    # track id -> smoothed (x1, y1, x2, y2)
        self.last_seen: dict[int, int] = {}

    def reset(self, session: str | None) -> None:
        self.session, self.frame = session, 0
        self.boxes.clear()
        self.last_seen.clear()


_state = _State()


def weights_path() -> str:
    return os.getenv("YOLO_WEIGHTS") or str(DEFAULT_WEIGHTS)


def _load_model():
    """Load the YOLO weights. Tests replace this."""
    from ultralytics import YOLO       # heavy import (torch): only when detection is really used

    return YOLO(weights_path())


def reset() -> None:
    """Forget the loaded model and all tracking state (tests; switching weights)."""
    global _model
    with _lock:
        _model = None
        _state.reset(None)


def warm() -> None:
    """Load the model and run one dummy frame, so the first real frame doesn't pay for it."""
    global _model
    with _lock:
        if _model is None:
            _model = _load_model()
        _model.track(np.zeros((480, 640, 3), np.uint8), persist=False, tracker=TRACKER, verbose=False)
        _state.reset(None)


def _decode(jpeg: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("frame is not a decodable image")
    return img


def _clamp(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _smooth(track_id: int, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    prev = _state.boxes.get(track_id)
    if prev is not None:
        box = tuple(SMOOTH * p + (1 - SMOOTH) * c for p, c in zip(prev, box))      # type: ignore[assignment]
    _state.boxes[track_id] = box
    _state.last_seen[track_id] = _state.frame
    return box


def _forget_stale() -> None:
    for tid in [t for t, seen in _state.last_seen.items() if _state.frame - seen > FORGET_AFTER_FRAMES]:
        _state.boxes.pop(tid, None)
        _state.last_seen.pop(tid, None)


def _to_detections(result, width: int, height: int) -> list[Detection]:
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []
    xyxy = boxes.xyxy.tolist()
    confs = boxes.conf.tolist()
    classes = [int(c) for c in boxes.cls.tolist()]
    ids = [int(i) for i in boxes.id.tolist()] if getattr(boxes, "id", None) is not None else [None] * len(xyxy)
    out: list[Detection] = []
    for (x1, y1, x2, y2), conf, cls, tid in zip(xyxy, confs, classes, ids):
        box = (x1 / width, y1 / height, x2 / width, y2 / height)
        if tid is not None:
            box = _smooth(tid, box)
        nx1, ny1, nx2, ny2 = (_clamp(v) for v in box)
        if nx2 <= nx1 or ny2 <= ny1:
            continue
        out.append(Detection(label=str(result.names[cls]), score=round(float(conf), 4),
                             box=Box(x=nx1, y=ny1, w=nx2 - nx1, h=ny2 - ny1), track_id=tid))
    return out


def detect(jpeg: bytes, session_id: str) -> list[Detection]:
    """Detect and track objects in one frame. Blocking: the orchestrator runs it in a worker thread."""
    global _model
    img = _decode(jpeg)
    height, width = img.shape[:2]
    with _lock:
        if _model is None:
            _model = _load_model()
        persist = _state.session == session_id          # a new session starts with a fresh tracker
        if not persist:
            _state.reset(session_id)
        _state.frame += 1
        results = _model.track(img, persist=persist, tracker=TRACKER, verbose=False)
        detections = _to_detections(results[0], width, height) if results else []
        _forget_stale()
    return detections
