"""Pass 2 answers questions about what is on camera: the live pictures and detector list reach Claude."""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

# Yash rewrote backend/brain/pass2.py (RAG + analyze(), commit e8c9edd) and removed _answer(), which these tests
# exercised. Skipped until the camera-view question answering is ported to the new Pass 2.
pytestmark = pytest.mark.skip(reason="pass2._answer removed in the RAG rewrite (e8c9edd); port pending")


from backend.brain import pass2
from backend.contracts import BrainResponse, Box, Detection, RepairPlan, SituationReport
from backend.mocks import fixture_json
from backend.perception import Perception
from backend.session import FrameSnap, Session
from tests.helpers import run


def plan() -> RepairPlan:
    return RepairPlan.model_validate(fixture_json("repair_plan"))


def report(text="which way do I turn this") -> SituationReport:
    return SituationReport(summary="The user asks about a part.", user_intent="question", user_utterance=text,
                           camera_sees="a keyboard with the plate showing", change_since_last="No change.",
                           confidence=0.8)


def det(label="keyboard", tid=3) -> Detection:
    return Detection(label=label, score=0.91, box=Box(x=0.2, y=0.3, w=0.5, h=0.4), track_id=tid)


def view(images=(b"\xff\xd8earlier", b"\xff\xd8latest"), notes=("earlier frame, 2.0 s before the latest", "latest frame, 0.3 s old"),
         detections=None) -> Perception:
    snaps = [FrameSnap(f"f{i}", float(i), img, []) for i, img in enumerate(images)]
    return Perception(snaps=snaps, detections=[det()] if detections is None else detections, image_notes=list(notes))


class StubClient:
    def __init__(self):
        self.kwargs = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.kwargs = kw
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=" Press it straight down. ")])


@pytest.fixture
def stub(monkeypatch):
    s = StubClient()
    monkeypatch.setattr(pass2, "_client", lambda: s)
    return s


def test_a_question_with_a_camera_view_sends_the_pictures_then_the_text(stub):
    answer = pass2._answer(plan(), 0, report(), view())
    assert answer == "Press it straight down."
    content = stub.kwargs["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "image", "text"]
    assert base64.b64decode(content[0]["source"]["data"]) == b"\xff\xd8earlier"
    assert base64.b64decode(content[1]["source"]["data"]) == b"\xff\xd8latest"
    text = content[2]["text"]
    assert "Pictures attached, in order: earlier frame, 2.0 s before the latest; latest frame, 0.3 s old" in text
    assert "Detector (YOLO, latest frame): keyboard#3 0.91 x=0.20 y=0.30 w=0.50 h=0.40" in text
    assert text.endswith("The user says: which way do I turn this")
    assert "Current step 1/5" in text and "Known mistakes:" in text, "the step context is still there"
    assert "pictures are attached" in stub.kwargs["system"]
    assert stub.kwargs["model"] == "claude-haiku-5-5" and "temperature" not in stub.kwargs


def test_a_question_with_no_camera_view_is_exactly_the_old_text_prompt(stub):
    pass2._answer(plan(), 0, report(), None)
    content = stub.kwargs["messages"][0]["content"]
    assert isinstance(content, str) and "Pictures attached" not in content and "Detector" not in content
    assert content.endswith("The user says: which way do I turn this")
    pass2._answer(plan(), 0, report())                         # the old three-argument call still works
    assert isinstance(stub.kwargs["messages"][0]["content"], str)


def test_detections_without_pictures_add_only_the_detector_line(stub):
    pass2._answer(plan(), 0, report(), view(images=(), notes=(), detections=[det("laptop", 7)]))
    content = stub.kwargs["messages"][0]["content"]
    assert isinstance(content, str) and "Detector (YOLO, latest frame): laptop#7" in content
    assert "Pictures attached" not in content


def test_pictures_with_nothing_detected_say_none(stub):
    pass2._answer(plan(), 0, report(), view(detections=[]))
    assert "Detector (YOLO, latest frame): none" in stub.kwargs["messages"][0]["content"][-1]["text"]


def test_respond_hands_the_sessions_camera_view_to_the_answer(monkeypatch):
    p = plan()
    monkeypatch.setattr(pass2, "_plan", lambda: p)
    seen = {}

    def fake_answer(plan_, pos, report_, perception=None):
        seen["perception"] = perception
        return "Turn it clockwise."
    monkeypatch.setattr(pass2, "_answer", fake_answer)
    s = Session(id="s")
    s.apply(BrainResponse(phase="guiding", plan=p, current_step=1, step_status="in_progress"))
    s.perception = view()
    r = run(pass2.respond(report(), s))
    assert r.say == "Turn it clockwise." and r.phase == "guiding"
    assert seen["perception"] is s.perception

    s2 = Session(id="s2")
    s2.apply(BrainResponse(phase="guiding", plan=p, current_step=1, step_status="in_progress"))
    run(pass2.respond(report(), s2))
    assert seen["perception"] is None, "no view this turn: the answer is text-only"
