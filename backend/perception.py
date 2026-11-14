"""What Claude is shown when someone talks to Spanner: the camera, the YOLO tracks and the timing.

On a spoken turn Pass 1 (and Pass 2, when it answers a question) should reason about what the camera
sees *now*, not about a text description made seconds ago. build() picks, from the session's ring of
recent frames:

  * the latest frame (at most MAX_FRAME_AGE_S old),
  * one earlier frame from about when the person started speaking (so Claude sees what changed while
    they talked), only if it is a different frame at least MIN_SEPARATION_S before the latest,
  * a close-up crop of the main detected object when it fills less than CROP_MAX_AREA of the frame
    (stock YOLO can locate the keyboard but can't read its switches; the crop lets Claude do that),
  * the YOLO detections on the latest frame, as a short list,
  * the age of the newest text observation, when it is old enough to matter.

Keyframe-only turns (nobody spoke) get no images: the keyframe was just described by `understand`.
"""
from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .contracts import Detection
from .frames import crop as crop_pixels, encode_jpeg, thumb
from .session import FrameSnap, Session

MAX_FRAME_AGE_S = 10.0       # a frame older than this is not "what the camera sees now"
MIN_SEPARATION_S = 1.0       # the earlier frame must be at least this much older than the latest
# The earlier frame is only worth its tokens if the scene changed. Mean abs difference of 32x18 grey
# thumbnails: sensor noise and re-encoding stay under 2, a hand entering the frame is over 5.
EARLIER_MIN_CHANGE = 3.0
AGE_TAG_MIN_S = 1.5          # only mention an observation's age when it is at least this old
CROP_MAX_AREA = 0.40         # no crop when the object already fills this much of the frame
CROP_MIN_SCORE = 0.40        # detections below this confidence are not worth a close-up
CROP_MIN_PX = 160            # a crop smaller than this on either side wouldn't show anything new
CROP_PAD = 0.06              # crop margin, as a fraction of the box on every side
CROP_LONG_EDGE = 768
IGNORE_LABELS = {"person"}   # the hands/body are never the repair object

PHONE_SILENCE_S = 0.7        # the phone waits this long after speech ends before sending a clip
MAX_DETECTIONS_SHOWN = 12    # the detector list in a prompt never has more entries than this


def detector_line(dets: list[Detection]) -> str:
    """The YOLO tracks as one prompt line: strongest first, with track ids and 0-1 positions."""
    if not dets:
        return "none"
    shown = sorted(dets, key=lambda d: -d.score)[:MAX_DETECTIONS_SHOWN]
    return "; ".join(
        f"{d.label}{'#' + str(d.track_id) if d.track_id is not None else ''} {d.score:.2f} "
        f"x={d.box.x:.2f} y={d.box.y:.2f} w={d.box.w:.2f} h={d.box.h:.2f}" for d in shown)


def image_blocks(images: list[bytes]) -> list[dict]:
    """JPEGs as Anthropic image content blocks (they go before the text of the message)."""
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(img).decode("ascii")}}
            for img in images]


@dataclass
class Perception:
    snaps: list[FrameSnap]                    # frames shown, oldest first (0, 1 or 2)
    crop: bytes | None = None                 # close-up JPEG of the main object on the latest frame
    crop_label: str | None = None
    detections: list[Detection] = field(default_factory=list)    # YOLO tracks on the latest frame
    image_notes: list[str] = field(default_factory=list)         # one line per attached image, in order
    observation_age_s: float | None = None    # age of the newest text observation, if worth stating

    @property
    def images(self) -> list[bytes]:
        """JPEGs in the order image_notes describes them: earlier frame, latest frame, close-up."""
        return [s.jpeg for s in self.snaps] + ([self.crop] if self.crop else [])

    def summary(self) -> dict:
        """The same thing without image bytes: for /debug, logs and the session state dump."""
        return {"frames": [s.frame_id for s in self.snaps], "image_notes": list(self.image_notes),
                "crop": self.crop_label, "detections": len(self.detections),
                "observation_age_s": self.observation_age_s}


def speech_window(end_ts: float, duration_s: float | None, silence_s: float = PHONE_SILENCE_S) -> tuple[float, float]:
    """(start, end) of the speech on the server clock, given when its clip/final arrived.

    The phone (or the realtime recogniser) only reports speech after `silence_s` of quiet, so the speech
    ended that long before the message arrived."""
    end = end_ts - silence_s
    return end - (duration_s or 0.0), end


def _area(d: Detection) -> float:
    return d.box.w * d.box.h


def _main_object(detections: list[Detection]) -> Detection | None:
    cands = [d for d in detections if d.label not in IGNORE_LABELS and d.score >= CROP_MIN_SCORE]
    return max(cands, key=lambda d: _area(d) * d.score, default=None)


def _crop_for(snap: FrameSnap) -> tuple[bytes, str] | None:
    """A close-up of the main detected object, or None when it isn't needed or wouldn't help."""
    obj = _main_object(snap.detections)
    if obj is None or _area(obj) >= CROP_MAX_AREA:
        return None
    img = cv2.imdecode(np.frombuffer(snap.jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    b = obj.box
    x1, x2 = max(0.0, b.x - CROP_PAD * b.w), min(1.0, b.x + b.w * (1 + CROP_PAD))
    y1, y2 = max(0.0, b.y - CROP_PAD * b.h), min(1.0, b.y + b.h * (1 + CROP_PAD))
    px = (int(x1 * w), int(y1 * h), int(round(x2 * w)), int(round(y2 * h)))
    if px[2] - px[0] < CROP_MIN_PX or px[3] - px[1] < CROP_MIN_PX:
        return None
    close_up = crop_pixels(img, px, pad=0)
    name = obj.label + (f"#{obj.track_id}" if obj.track_id is not None else "")
    return encode_jpeg(close_up, long_edge=CROP_LONG_EDGE, quality=85), name


def _scene_change(a: FrameSnap, b: FrameSnap) -> float | None:
    """How different two frames look (0 = identical). None when a frame can't be decoded."""
    ia = cv2.imdecode(np.frombuffer(a.jpeg, np.uint8), cv2.IMREAD_COLOR)
    ib = cv2.imdecode(np.frombuffer(b.jpeg, np.uint8), cv2.IMREAD_COLOR)
    if ia is None or ib is None:
        return None
    return float(np.abs(thumb(ia) - thumb(ib)).mean())


def _earlier(ring: list[FrameSnap], latest: FrameSnap, speech_start: float) -> FrameSnap | None:
    """The frame nearest the start of speech, unless the scene looks the same as the latest one
    (a static scene: a second picture costs tokens and tells Claude nothing)."""
    older = [f for f in ring if latest.ts - f.ts >= MIN_SEPARATION_S]
    best = min(older, key=lambda f: abs(f.ts - speech_start), default=None)
    if best is None:
        return None
    change = _scene_change(best, latest)
    return best if change is None or change >= EARLIER_MIN_CHANGE else None


def build(session: Session, speech_end_ts: float | None = None, duration_s: float | None = None,
          now: float | None = None) -> Perception | None:
    """The perception for one brain run. speech_end_ts is when the spoken message arrived (server clock),
    or None for a keyframe-only turn. Returns None when there is nothing to add to the text-only prompt."""
    now = time.time() if now is None else now
    ring = list(session.recent_frames)
    latest = ring[-1] if ring and now - ring[-1].ts <= MAX_FRAME_AGE_S else None

    age = None
    if session.observations and session.last_observation_at is not None:
        a = now - session.last_observation_at
        age = round(a, 1) if a >= AGE_TAG_MIN_S else None

    p = Perception(snaps=[], detections=list(latest.detections) if latest else [], observation_age_s=age)
    if latest is not None and speech_end_ts is not None:               # a spoken turn: show the pictures
        earlier = None
        if duration_s:
            earlier = _earlier(ring, latest, speech_window(speech_end_ts, duration_s)[0])
        if earlier is not None:
            p.snaps.append(earlier)
            p.image_notes.append(f"earlier frame, {latest.ts - earlier.ts:.1f} s before the latest")
        p.snaps.append(latest)
        p.image_notes.append(f"latest frame, {max(0.0, now - latest.ts):.1f} s old")
        close = _crop_for(latest)
        if close is not None:
            p.crop, p.crop_label = close
            p.image_notes.append(f"close-up of {p.crop_label} from the latest frame")
    return p if (p.snaps or p.detections or p.observation_age_s is not None) else None


async def gather(session: Session, speech_end_ts: float | None = None, duration_s: float | None = None,
                 now: float | None = None) -> Perception | None:
    """build() off the event loop (it decodes and re-encodes a JPEG when it makes a close-up)."""
    return await asyncio.to_thread(build, session, speech_end_ts, duration_s, now)
