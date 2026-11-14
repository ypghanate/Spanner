"""The real detect stage: normalised boxes, labels, track ids, smoothing, tracker resets, warm-up."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.contracts import Detection
from backend.vision import detect as vd
from tests.helpers import StubBoxes, StubResult, StubYolo

ROOT = Path(__file__).resolve().parent.parent


def jpeg(w=640, h=480) -> bytes:
    ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), 90, np.uint8))
    assert ok
    return buf.tobytes()


@pytest.fixture
def yolo(monkeypatch):
    """A stub model installed as the loader. Returns (model, loads) where loads counts _load_model calls."""
    vd.reset()
    model = StubYolo()
    loads = []
    monkeypatch.setattr(vd, "_load_model", lambda: (loads.append(1), model)[1])
    yield model, loads
    vd.reset()


def one_box(x1=64, y1=48, x2=320, y2=240, conf=0.9, cls=0, tid=7):
    return StubResult(StubBoxes([[x1, y1, x2, y2]], [conf], [cls], None if tid is None else [tid]))


def test_boxes_are_normalised_and_in_contract_shape(yolo):
    model, _ = yolo
    model.results = [one_box()]
    (d,) = vd.detect(jpeg(), "s1")
    assert isinstance(d, Detection)
    assert (d.label, d.track_id, d.score) == ("bicycle", 7, 0.9)
    assert (d.box.x, d.box.y, d.box.w, d.box.h) == pytest.approx((0.1, 0.1, 0.4, 0.4))     # 640x480 frame


def test_several_objects_keep_their_own_labels_and_ids(yolo):
    model, _ = yolo
    model.results = [StubResult(StubBoxes([[0, 0, 64, 48], [320, 240, 640, 480]], [0.8, 0.6], [1, 0], [3, 4]))]
    a, b = vd.detect(jpeg(), "s1")
    assert (a.label, a.track_id) == ("person", 3) and (b.label, b.track_id) == ("bicycle", 4)


def test_no_boxes_means_no_detections(yolo):
    model, _ = yolo
    model.results = [StubResult(None), StubResult(StubBoxes([], [], [], []))]
    assert vd.detect(jpeg(), "s1") == [] and vd.detect(jpeg(), "s1") == []


def test_no_track_ids_gives_none_and_no_smoothing(yolo):
    model, _ = yolo
    model.results = [one_box(tid=None, x1=0, x2=128), one_box(tid=None, x1=256, x2=384)]
    first, second = vd.detect(jpeg(), "s1")[0], vd.detect(jpeg(), "s1")[0]
    assert first.track_id is None and second.track_id is None
    assert second.box.x == pytest.approx(0.4), "an untracked box is passed through as detected"


def test_a_tracked_box_glides_instead_of_jumping(yolo):
    model, _ = yolo
    model.results = [one_box(x1=128, x2=256), one_box(x1=384, x2=512)]       # x moves 0.2 -> 0.6
    first, second = vd.detect(jpeg(), "s1")[0], vd.detect(jpeg(), "s1")[0]
    assert first.box.x == pytest.approx(0.2)
    assert second.box.x == pytest.approx(0.2 * vd.SMOOTH + 0.6 * (1 - vd.SMOOTH))
    assert 0.2 < second.box.x < 0.6
    assert second.box.w == pytest.approx(0.2)                                # size is smoothed too


def test_boxes_outside_the_frame_are_clamped_and_degenerate_boxes_dropped(yolo):
    model, _ = yolo
    model.results = [StubResult(StubBoxes([[-50, -20, 700, 500], [100, 100, 100, 200], [650, 10, 700, 50]],
                                          [0.9, 0.9, 0.9], [0, 0, 0], [1, 2, 3]))]
    out = vd.detect(jpeg(), "s1")
    assert len(out) == 1, "zero-width and fully off-frame boxes are dropped"
    b = out[0].box
    assert (b.x, b.y, b.w, b.h) == pytest.approx((0.0, 0.0, 1.0, 1.0))
    assert all(0.0 <= v <= 1.0 for v in (b.x, b.y, b.x + b.w, b.y + b.h))


def test_tracker_state_is_kept_within_a_session_and_reset_when_it_changes(yolo):
    model, _ = yolo
    model.results = [one_box(x1=128, x2=256), one_box(x1=384, x2=512), one_box(x1=384, x2=512)]
    vd.detect(jpeg(), "s1")
    vd.detect(jpeg(), "s1")
    other = vd.detect(jpeg(), "s2")[0]
    assert [c["persist"] for c in model.calls] == [False, True, False]
    assert all(c["tracker"] == "bytetrack.yaml" for c in model.calls)
    assert other.box.x == pytest.approx(0.6), "a new session starts with no smoothing history"


def test_stale_tracks_are_forgotten(yolo):
    model, _ = yolo
    model.results = [one_box(tid=9)]
    vd.detect(jpeg(), "s1")
    assert 9 in vd._state.boxes
    for _ in range(vd.FORGET_AFTER_FRAMES + 2):
        vd.detect(jpeg(), "s1")                                              # nothing detected any more
    assert 9 not in vd._state.boxes and 9 not in vd._state.last_seen


def test_the_model_is_loaded_lazily_and_only_once(yolo):
    _, loads = yolo
    assert loads == []
    vd.detect(jpeg(), "s1")
    vd.detect(jpeg(), "s2")
    assert loads == [1]


def test_an_undecodable_frame_raises_and_never_loads_the_model(yolo):
    _, loads = yolo
    with pytest.raises(ValueError, match="decodable"):
        vd.detect(b"this is not a jpeg", "s1")
    assert loads == []


def test_warm_loads_the_model_runs_a_dummy_frame_and_leaves_no_state(yolo):
    model, loads = yolo
    vd.warm()
    assert loads == [1] and len(model.calls) == 1 and model.calls[0]["persist"] is False
    assert vd._state.session is None
    model.results = [one_box()]
    vd.detect(jpeg(), "s1")
    assert model.calls[1]["persist"] is False, "the first real frame starts a fresh tracker"
    assert loads == [1]


def test_weights_default_to_the_committed_file_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("YOLO_WEIGHTS", raising=False)
    assert vd.weights_path() == str(ROOT / "backend" / "yolov8n.pt")
    assert Path(vd.weights_path()).exists(), "the nano weights are committed in backend/"
    monkeypatch.setenv("YOLO_WEIGHTS", "custom.pt")
    assert vd.weights_path() == "custom.pt"


def test_importing_the_stage_does_not_import_ultralytics():
    import subprocess
    import sys
    code = "import sys, backend.vision.detect; sys.exit(1 if 'ultralytics' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT).returncode == 0


def test_the_real_model_runs_on_a_real_frame():
    """Needs ultralytics + the committed weights; skipped in the light CI environment."""
    pytest.importorskip("ultralytics")
    if not Path(vd.weights_path()).exists():
        pytest.skip("weights not present")
    vd.reset()
    frame = (ROOT / "fixtures" / "frames" / "placeholder.jpg").read_bytes()
    try:
        first = vd.detect(frame, "real-1")
        second = vd.detect(frame, "real-1")
    finally:
        vd.reset()
    for d in first + second:
        assert isinstance(d, Detection) and 0 <= d.box.x <= 1 and 0 <= d.box.y <= 1
        assert d.box.x + d.box.w <= 1.0001 and d.box.y + d.box.h <= 1.0001 and d.label
