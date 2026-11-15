"""Perception: which frames, detections and ages Claude is shown on a turn."""
from __future__ import annotations

import json
import re
import zlib

import cv2
import numpy as np
import pytest

from backend import perception as pc
from backend.contracts import Box, Detection, SceneObservation
from backend.mocks import fixture_json
from backend.session import FrameSnap, Session
from tests.helpers import run

W, H = 768, 576
NOW = 1000.0


def shade_for(frame_id: str) -> int:
    """Frames f0, f1, ... get shades 0, 1, ...; any other id a stable pseudo-random one."""
    m = re.fullmatch(r"f(\d+)", frame_id)
    return int(m.group(1)) if m else zlib.crc32(frame_id.encode()) % 11


def jpeg(w=W, h=H, shade=0) -> bytes:
    """A test picture. Different `shade` values put a bright bar in a different place, so two frames with
    different shades look like different scenes and equal shades look identical."""
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :, 0] = np.linspace(0, 255, w, dtype=np.uint8)[None, :]
    img[:, :, 1] = np.linspace(0, 255, h, dtype=np.uint8)[:, None]
    x = 20 + (shade % 11) * 65
    img[:, x:x + 50] = 255
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def det(label="keyboard", score=0.9, x=0.2, y=0.3, w=0.5, h=0.4, tid=3) -> Detection:
    return Detection(label=label, score=score, box=Box(x=x, y=y, w=w, h=h), track_id=tid)


def snap(frame_id, ts, dets=None, data=None) -> FrameSnap:
    return FrameSnap(frame_id=frame_id, ts=ts, jpeg=data if data is not None else jpeg(shade=shade_for(frame_id)),
                     detections=dets or [])


def session_with(frames, observed_at=None) -> Session:
    s = Session(id="s")
    s.recent_frames.extend(frames)
    if observed_at is not None:
        s.observations.append(SceneObservation.model_validate(fixture_json("scene_observation_1")))
        s.last_observation_at = observed_at
    return s


def decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


# --- speech window ------------------------------------------------------------

def test_speech_window_subtracts_the_silence_the_phone_waited():
    start, end = pc.speech_window(100.0, 2.0)
    assert (start, end) == pytest.approx((97.3, 99.3))
    assert pc.speech_window(100.0, None) == pytest.approx((99.3, 99.3))
    assert pc.speech_window(100.0, 1.0, silence_s=0.0) == pytest.approx((99.0, 100.0))


# --- the latest frame ----------------------------------------------------------

def test_nothing_to_show_gives_none():
    assert pc.build(session_with([]), speech_end_ts=NOW, duration_s=2, now=NOW) is None
    assert pc.build(session_with([]), now=NOW) is None


def test_a_spoken_turn_shows_the_latest_frame_with_its_age():
    s = session_with([snap("f1", NOW - 0.4)])
    p = pc.build(s, speech_end_ts=NOW, duration_s=None, now=NOW)
    assert [f.frame_id for f in p.snaps] == ["f1"] and p.images == [p.snaps[0].jpeg]
    assert p.image_notes == ["latest frame, 0.4 s old"]


def test_a_frame_older_than_ten_seconds_is_not_what_the_camera_sees_now():
    s = session_with([snap("old", NOW - 10.5, [det()])])
    assert pc.build(s, speech_end_ts=NOW, duration_s=2, now=NOW) is None
    s2 = session_with([snap("edge", NOW - 9.9)])
    assert pc.build(s2, speech_end_ts=NOW, duration_s=2, now=NOW).snaps[0].frame_id == "edge"


def test_a_stale_camera_still_reports_how_old_the_text_observation_is():
    s = session_with([snap("old", NOW - 30)], observed_at=NOW - 12)
    p = pc.build(s, speech_end_ts=NOW, duration_s=2, now=NOW)
    assert p.snaps == [] and p.images == [] and p.observation_age_s == 12.0 and p.detections == []


# --- the earlier frame ----------------------------------------------------------

def ring(n=9, step=1.0, last=NOW - 0.3):
    return [snap(f"f{i}", last - (n - 1 - i) * step) for i in range(n)]      # f8 is the latest


def test_the_earlier_frame_is_the_one_nearest_the_start_of_speech():
    frames = ring()                                        # ts 991.7 ... 999.7
    # clip arrived at 1000.0; speech ended 0.7 s earlier (999.3) and lasted 3 s -> started at 996.3
    p = pc.build(session_with(frames), speech_end_ts=1000.0, duration_s=3.0, now=NOW)
    assert [f.frame_id for f in p.snaps] == ["f5", "f8"], "996.7 is the frame nearest 996.3"
    assert p.image_notes[0] == "earlier frame, 3.0 s before the latest"
    assert p.image_notes[1] == "latest frame, 0.3 s old"


def test_no_earlier_frame_when_it_would_be_nearly_the_same_moment():
    frames = [snap("a", NOW - 0.8), snap("b", NOW - 0.3)]  # only 0.5 s apart
    p = pc.build(session_with(frames), speech_end_ts=NOW, duration_s=2.0, now=NOW)
    assert [f.frame_id for f in p.snaps] == ["b"]


def test_no_earlier_frame_with_a_single_frame_or_an_unknown_duration():
    assert [f.frame_id for f in pc.build(session_with([snap("only", NOW - 0.2)]), speech_end_ts=NOW,
                                         duration_s=3.0, now=NOW).snaps] == ["only"]
    p = pc.build(session_with(ring()), speech_end_ts=NOW, duration_s=None, now=NOW)
    assert len(p.snaps) == 1 and p.snaps[0].frame_id == "f8"


def test_a_very_long_utterance_uses_the_oldest_frame_the_ring_has():
    p = pc.build(session_with(ring(n=5)), speech_end_ts=1000.0, duration_s=30.0, now=NOW)
    assert [f.frame_id for f in p.snaps] == ["f0", "f4"]


def test_the_earlier_frame_is_never_the_latest():
    p = pc.build(session_with(ring()), speech_end_ts=1000.0, duration_s=0.5, now=NOW)
    assert len({f.frame_id for f in p.snaps}) == len(p.snaps)


# --- the close-up crop ------------------------------------------------------------

def test_a_small_keyboard_gets_a_close_up_after_the_two_frames():
    frames = [snap("a", NOW - 3), snap("b", NOW - 0.3, [det(w=0.5, h=0.4, x=0.2, y=0.3, tid=7)])]
    p = pc.build(session_with(frames), speech_end_ts=NOW, duration_s=2.5, now=NOW)
    assert p.crop is not None and p.crop_label == "keyboard#7"
    assert len(p.images) == 3 and p.images[-1] == p.crop
    assert p.image_notes[-1] == "close-up of keyboard#7 from the latest frame"
    img = decode(p.crop)
    full = decode(frames[1].jpeg)
    assert img.shape[1] < full.shape[1] and img.shape[0] < full.shape[0], "a crop is smaller than the frame"
    assert img.shape[1] == pytest.approx(0.56 * W, abs=3) and img.shape[0] == pytest.approx(0.448 * H, abs=3)


def test_no_close_up_when_the_object_already_fills_the_frame():
    big = det(x=0.05, y=0.05, w=0.9, h=0.9)                          # area 0.81
    p = pc.build(session_with([snap("a", NOW - 0.2, [big])]), speech_end_ts=NOW, duration_s=1, now=NOW)
    assert p.crop is None and len(p.images) == 1
    edge = det(x=0.1, y=0.1, w=0.8, h=0.5)                           # area exactly 0.40: not under the limit
    p2 = pc.build(session_with([snap("a", NOW - 0.2, [edge])]), speech_end_ts=NOW, duration_s=1, now=NOW)
    assert p2.crop is None


def test_people_and_weak_detections_never_get_a_close_up():
    cases = [[det("person", 0.95, w=0.3, h=0.3)], [det(score=0.39)], []]
    for dets in cases:
        p = pc.build(session_with([snap("a", NOW - 0.2, dets)]), speech_end_ts=NOW, duration_s=1, now=NOW)
        assert p.crop is None, dets


def test_a_person_does_not_hide_the_object_behind_them():
    dets = [det("person", 0.95, x=0.0, y=0.0, w=0.4, h=0.6, tid=1), det("keyboard", 0.8, tid=2)]
    p = pc.build(session_with([snap("a", NOW - 0.2, dets)]), speech_end_ts=NOW, duration_s=1, now=NOW)
    assert p.crop_label == "keyboard#2"


def test_the_most_prominent_object_is_chosen_by_size_times_confidence():
    small_sure = det("mouse", 0.95, x=0.1, y=0.1, w=0.2, h=0.2, tid=1)       # 0.04 * 0.95
    big_unsure = det("laptop", 0.5, x=0.4, y=0.3, w=0.5, h=0.4, tid=2)       # 0.20 * 0.5
    p = pc.build(session_with([snap("a", NOW - 0.2, [small_sure, big_unsure])]),
                 speech_end_ts=NOW, duration_s=1, now=NOW)
    assert p.crop_label == "laptop#2"


def test_a_box_too_small_to_show_anything_new_is_skipped():
    tiny = det(w=0.12, h=0.12)                                        # about 92 x 69 px
    p = pc.build(session_with([snap("a", NOW - 0.2, [tiny])]), speech_end_ts=NOW, duration_s=1, now=NOW)
    assert p.crop is None
    one_side = det(w=0.5, h=0.2)                                      # 384 wide but only 115 px tall
    p2 = pc.build(session_with([snap("a", NOW - 0.2, [one_side])]), speech_end_ts=NOW, duration_s=1, now=NOW)
    assert p2.crop is None


def test_a_box_at_the_edge_is_padded_but_clamped_to_the_frame():
    d = det(x=0.62, y=0.55, w=0.38, h=0.45, tid=None)                  # runs to the bottom-right corner
    p = pc.build(session_with([snap("a", NOW - 0.2, [d])]), speech_end_ts=NOW, duration_s=1, now=NOW)
    assert p.crop is not None and p.crop_label == "keyboard", "no track id: just the label"
    img = decode(p.crop)
    assert img.shape[1] <= W and img.shape[0] <= H


def test_an_undecodable_frame_means_no_close_up_not_a_crash():
    p = pc.build(session_with([snap("bad", NOW - 0.2, [det()], data=b"not a jpeg")]),
                 speech_end_ts=NOW, duration_s=1, now=NOW)
    assert p.crop is None and [f.frame_id for f in p.snaps] == ["bad"]


# --- turns with no speech ----------------------------------------------------------

def test_a_keyframe_turn_gets_detections_but_no_pictures():
    s = session_with([snap("a", NOW - 2, [det()]), snap("b", NOW - 0.3, [det(tid=9)])])
    p = pc.build(s, speech_end_ts=None, now=NOW)
    assert p.snaps == [] and p.images == [] and p.image_notes == [] and p.crop is None
    assert [d.track_id for d in p.detections] == [9], "detections of the latest frame only"


def test_a_keyframe_turn_with_nothing_new_to_say_is_none():
    assert pc.build(session_with([snap("a", NOW - 0.3)]), speech_end_ts=None, now=NOW) is None


# --- the age of the text observation --------------------------------------------------

@pytest.mark.parametrize("seen_ago,expected", [(0.2, None), (1.4, None), (1.5, 1.5), (4.24, 4.2), (30.0, 30.0)])
def test_observation_age_is_only_stated_when_it_matters(seen_ago, expected):
    s = session_with([snap("a", NOW - 0.2)], observed_at=NOW - seen_ago)
    p = pc.build(s, speech_end_ts=NOW, duration_s=1, now=NOW)
    assert p.observation_age_s == expected


def test_no_age_without_an_observation():
    s = session_with([snap("a", NOW - 0.2)])
    s.last_observation_at = NOW - 20                          # a timestamp but no observation to be old
    assert pc.build(s, speech_end_ts=NOW, duration_s=1, now=NOW).observation_age_s is None


# --- plumbing --------------------------------------------------------------------------

def test_summary_has_no_image_bytes_and_is_json_safe():
    frames = [snap("a", NOW - 3), snap("b", NOW - 0.3, [det()])]
    p = pc.build(session_with(frames, observed_at=NOW - 5), speech_end_ts=NOW, duration_s=2.5, now=NOW)
    text = json.dumps(p.summary())
    assert '"frames": ["a", "b"]' in text and "keyboard#3" in text and '"observation_age_s": 5.0' in text
    assert p.summary()["detections"] == 1


def test_the_bundle_does_not_alias_the_sessions_detection_list():
    d = det()
    s = session_with([snap("a", NOW - 0.2, [d])])
    p = pc.build(s, speech_end_ts=NOW, duration_s=1, now=NOW)
    p.detections.clear()
    assert len(s.recent_frames[-1].detections) == 1


def test_gather_is_build_off_the_event_loop():
    frames = [snap("a", NOW - 3), snap("b", NOW - 0.3, [det()])]
    s = session_with(frames)
    got = run(pc.gather(s, NOW, 2.5, NOW))
    want = pc.build(s, NOW, 2.5, NOW)
    assert [f.frame_id for f in got.snaps] == [f.frame_id for f in want.snaps]
    assert got.image_notes == want.image_notes and got.crop == want.crop


def test_the_ring_keeps_only_the_most_recent_twelve_frames():
    s = Session(id="s")
    for i in range(20):
        s.recent_frames.append(snap(f"f{i}", float(i)))
    assert [f.frame_id for f in s.recent_frames] == [f"f{i}" for i in range(8, 20)]


# --- the earlier frame is only sent when the scene changed (cost) ------------------------------------------

def test_a_static_scene_gets_one_picture_not_two():
    same = jpeg(shade=3)
    frames = [snap("a", NOW - 4, data=same), snap("b", NOW - 0.3, data=same)]
    p = pc.build(session_with(frames), speech_end_ts=NOW, duration_s=3.0, now=NOW)
    assert [f.frame_id for f in p.snaps] == ["b"] and p.image_notes == ["latest frame, 0.3 s old"]


def test_sensor_noise_and_re_encoding_do_not_count_as_change():
    base = cv2.imdecode(np.frombuffer(jpeg(shade=3), np.uint8), cv2.IMREAD_COLOR)
    noisy = np.clip(base.astype(np.int16) + np.random.default_rng(0).normal(0, 4, base.shape), 0, 255).astype(np.uint8)
    for variant in (noisy, cv2.imdecode(cv2.imencode(".jpg", base, [cv2.IMWRITE_JPEG_QUALITY, 50])[1], 1)):
        frames = [snap("a", NOW - 4, data=jpeg(shade=3)), snap("b", NOW - 0.3, data=cv2.imencode(".jpg", variant)[1].tobytes())]
        assert len(pc.build(session_with(frames), speech_end_ts=NOW, duration_s=3.0, now=NOW).snaps) == 1


def test_a_real_change_keeps_the_earlier_frame():
    frames = [snap("a", NOW - 4, data=jpeg(shade=2)), snap("b", NOW - 0.3, data=jpeg(shade=7))]
    p = pc.build(session_with(frames), speech_end_ts=NOW, duration_s=3.0, now=NOW)
    assert [f.frame_id for f in p.snaps] == ["a", "b"]


def test_the_change_threshold_sits_between_noise_and_a_small_real_change():
    """Measured on real photos: noise/re-encoding < 2, a hand entering the frame > 5."""
    assert 2.0 < pc.EARLIER_MIN_CHANGE < 5.0


def test_a_frame_that_cannot_be_decoded_is_kept_rather_than_guessed_about():
    frames = [snap("a", NOW - 4, data=b"not a jpeg"), snap("b", NOW - 0.3)]
    p = pc.build(session_with(frames), speech_end_ts=NOW, duration_s=3.0, now=NOW)
    assert [f.frame_id for f in p.snaps] == ["a", "b"]
