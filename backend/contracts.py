"""Single source of truth for every data shape that crosses a module or network boundary.

Boxes are normalised 0-1, measured from the top-left of the frame that was sent
(after rotation was fixed). Changes here go through P3, are announced in chat,
and happen at checkpoints. TypeScript types are generated from this file by
scripts/gen_types.py.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Phase = Literal[
    "identifying", "diagnosing", "researching", "guiding", "verifying", "done", "safety_stop"
]
UserIntent = Literal["start", "question", "step_done", "need_help", "stop", "none"]


# --- vision -----------------------------------------------------------------

class Box(BaseModel):
    x: float
    y: float
    w: float
    h: float


class Detection(BaseModel):
    label: str
    score: float
    box: Box
    track_id: int | None = None


class FrameQuality(BaseModel):
    blurry: bool
    too_dark: bool
    is_keyframe: bool
    sharpness: float
    brightness: float


class SceneObservation(BaseModel):
    frame_id: str
    object: str
    visible_parts: list[str] = Field(default_factory=list)
    state: str
    anomalies: list[str] = Field(default_factory=list)
    hands_visible: bool
    tools_visible: list[str] = Field(default_factory=list)
    confidence: float


# --- voice + pass 1 ---------------------------------------------------------

class Transcript(BaseModel):
    text: str
    language: str
    duration_s: float


class SituationReport(BaseModel):
    summary: str
    user_intent: UserIntent
    user_utterance: str | None = None
    camera_sees: str
    change_since_last: str
    suspected_fault: str | None = None
    confidence: float


# --- repair plan ------------------------------------------------------------

class SourceRef(BaseModel):
    title: str
    url: str
    kind: Literal["ifixit", "youtube", "web"]


class SceneCommand(BaseModel):
    op: Literal["highlight", "pulse", "explode", "rotate_to", "arrow", "show_step", "reset"]
    part: str | None = None
    group: str | None = None
    view: str | None = None
    amount: float | None = None
    direction: str | None = None


class HudCommand(BaseModel):
    op: Literal["label", "arrow", "circle", "clear"]
    target_label: str | None = None
    box: Box | None = None
    text: str | None = None
    color: Literal["info", "ok", "warn", "danger"] = "info"


class RepairStep(BaseModel):
    index: int
    instruction: str
    parts: list[str] = Field(default_factory=list)
    expected_visual_state: str
    common_mistakes: list[str] = Field(default_factory=list)
    scene3d: list[SceneCommand] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    illustration_prompt: str | None = None
    illustration_url: str | None = None
    clip_url: str | None = None
    # Set on generated plans (backend/planner.py): a 2-3 word name for the step, and how the hologram viewer
    # animates it (one of training.scene3d.KIND_NAMES).
    title: str | None = None
    kind: str | None = None


class RepairPlan(BaseModel):
    object: str
    fault: str
    difficulty: str
    time_min: int
    tools_needed: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    steps: list[RepairStep]
    sources: list[SourceRef] = Field(default_factory=list)
    # Set when Claude generated this plan from the user's complaint (backend/planner.py).
    generated: bool = False
    title: str = ""                     # short name of the repair for the header, e.g. "F key switch replacement"
    diagnosis: str = ""
    target_key: str | None = None       # layout id of the key the repair is about (e.g. "F", "SPACE")
    fault_signature: str = ""
    device: str = "keyboard"            # keyboard | iphone15 | ipad11 | momentum4 (what the plan is for)


# --- brain output -----------------------------------------------------------

class MediaUpdate(BaseModel):
    kind: Literal["illustration", "scan3d", "clip"]
    step_index: int | None = None
    url: str


class Alert(BaseModel):
    severity: Literal["warn", "danger"]
    message: str


class BrainResponse(BaseModel):
    phase: Phase
    say: str = ""                      # max 2 sentences; empty = say nothing
    hud: list[HudCommand] = Field(default_factory=list)       # empty = no change
    scene3d: list[SceneCommand] = Field(default_factory=list)  # empty = no change
    current_step: int | None = None
    step_status: Literal["in_progress", "done", "mistake"] | None = None
    alert: Alert | None = None
    plan: RepairPlan | None = None
    media: list[MediaUpdate] = Field(default_factory=list)
    # Added by P3: slow-loop replies travel inside FrameResponse.events, which has
    # nowhere else to carry audio. P4 leaves this None; the orchestrator fills it.
    tts_url: str | None = None
    # The Linkup replacement-part card, exactly as backend.parts_agent.card_view() builds it
    # (offers, "fits your <model>" badge, repair-or-replace decision, manual spec, sources).
    # Set by the orchestrator when the parts job finishes; the app renders it beside the 3D view.
    parts_card: dict | None = None


# --- HTTP responses ---------------------------------------------------------

class FrameResponse(BaseModel):
    frame_id: str
    quality: FrameQuality
    detections: list[Detection] = Field(default_factory=list)
    events: list[BrainResponse] = Field(default_factory=list)


class AudioResponse(BaseModel):
    transcript: Transcript
    response: BrainResponse
    tts_url: str | None = None


class SessionCreate(BaseModel):
    device: str = "unknown"


class SessionCreated(BaseModel):
    session_id: str


class SessionSummary(BaseModel):
    session_id: str
    object: str | None = None
    fault: str | None = None
    steps_total: int = 0
    steps_done: int = 0
    duration_s: float = 0.0
    sources: list[SourceRef] = Field(default_factory=list)


# --- live session WebSocket (WS /session/{id}/live) -------------------------
# One socket carries camera frames, voice, and everything the brain pushes back.
# Every message is a JSON text message, except two optional binary cases:
#   phone -> server binary frame: 16 kHz mono PCM16 microphone audio (dev build / live_client.py)
#   server -> phone binary frame: spoken-reply MP3 audio, only in "stream" TTS mode,
#                                  between tts_start and tts_end.

class LiveStart(BaseModel):
    type: Literal["start"] = "start"
    keyterms: list[str] = Field(default_factory=list)
    # "url" (default): replies carry tts_url and the phone plays it. "stream": the server
    # streams MP3 chunks between tts_start and tts_end on this socket.
    tts: Literal["url", "stream"] = "url"


class LiveFrame(BaseModel):
    """One camera frame (about one per second). Base64 JPEG, as expo-camera can output it."""
    type: Literal["frame"] = "frame"
    jpeg_b64: str
    ts: float | None = None


class LiveUtterance(BaseModel):
    """One finished voice clip (Expo Go path): the phone records, detects silence with the
    recorder's metering, and sends the whole clip."""
    type: Literal["utterance"] = "utterance"
    audio_b64: str
    format: str = "m4a"
    duration_s: float | None = None


class LiveAudio(BaseModel):
    """Streaming microphone audio (dev-build path): 16 kHz mono PCM16, base64."""
    type: Literal["audio"] = "audio"
    pcm_b64: str


class LivePlayback(BaseModel):
    """The phone started / finished playing a spoken reply (it mutes the mic meanwhile)."""
    type: Literal["playback"] = "playback"
    state: Literal["start", "end"]


class LiveBargeIn(BaseModel):
    type: Literal["barge_in"] = "barge_in"


class LiveStop(BaseModel):
    type: Literal["stop"] = "stop"


class LivePartial(BaseModel):
    type: Literal["partial"] = "partial"
    text: str


class LiveFinal(BaseModel):
    type: Literal["final"] = "final"
    text: str


class LiveResponse(BaseModel):
    """The brain's answer to something the user said. Apply hud / scene3d / alert / plan as usual.

    speak=True: say the reply (play response.tts_url, or speak response.say locally when
    tts_url is null). speak=False: the user started talking again while the brain worked, so
    show the reply but don't say it."""
    type: Literal["response"] = "response"
    response: BrainResponse
    speak: bool = True


class LiveFrameResult(BaseModel):
    """Fast-loop reply to a LiveFrame: quality and detections for the HUD boxes."""
    type: Literal["frame_result"] = "frame_result"
    result: FrameResponse


class LiveEvent(BaseModel):
    """A brain result pushed the moment it is ready (slow-loop reply, alert, media, parts card)."""
    type: Literal["event"] = "event"
    response: BrainResponse


class LiveTtsStart(BaseModel):
    type: Literal["tts_start"] = "tts_start"


class LiveTtsEnd(BaseModel):
    type: Literal["tts_end"] = "tts_end"


class LiveError(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


# Everything that is a top-level shape for the app. gen_types.py emits these.
EXPORTED_MODELS: list[type[BaseModel]] = [
    Box, Detection, FrameQuality, SceneObservation, Transcript, SituationReport,
    SourceRef, SceneCommand, HudCommand, RepairStep, RepairPlan, MediaUpdate, Alert,
    BrainResponse, FrameResponse, AudioResponse, SessionCreate, SessionCreated,
    SessionSummary, LiveStart, LiveFrame, LiveUtterance, LiveAudio, LivePlayback, LiveBargeIn,
    LiveStop, LivePartial, LiveFinal, LiveResponse, LiveFrameResult, LiveEvent, LiveTtsStart,
    LiveTtsEnd, LiveError,
]
