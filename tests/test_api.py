"""HTTP + WebSocket API on mocks: full repair flow, errors, media guard, debug, live voice."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import main as main_mod
from backend.contracts import AudioResponse, FrameResponse, SessionSummary, Transcript
from backend.vision import detect as vision_detect
from backend.voice import tts
from tests.conftest import set_env
from tests.helpers import StubBoxes, StubResult, StubYolo

ROOT = Path(__file__).resolve().parent.parent
JPEG = (ROOT / "fixtures" / "frames" / "placeholder.jpg").read_bytes()
AUDIO = b"\x00\x01fake-m4a-bytes"


@pytest.fixture
def client():
    with TestClient(main_mod.create_app()) as c:
        yield c


def new_session(client) -> str:
    r = client.post("/session", json={"device": "test-phone"})
    assert r.status_code == 200
    return r.json()["session_id"]


def post_frame(client, sid, data=JPEG):
    return client.post(f"/session/{sid}/frame", files={"frame": ("f.jpg", data, "image/jpeg")},
                       data={"meta": json.dumps({"ts": time.time()})})


def post_audio(client, sid, name="question.m4a"):
    r = client.post(f"/session/{sid}/audio", files={"audio": (name, AUDIO, "audio/mp4")},
                    data={"meta": json.dumps({"duration_s": 2.0})})
    assert r.status_code == 200, r.text
    return AudioResponse.model_validate(r.json())


def wait_until(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def store_of(client):
    return client.app.state.orch.stages.store


# --- basics -----------------------------------------------------------------

def test_health_lists_every_stage_as_mock(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["use_mocks"] is True
    assert set(body["stages"]) == {"preprocess", "detect", "understand", "safety", "pass2", "pass1",
                                   "transcribe", "track_step", "parts_card", "store"}
    assert set(body["stages"].values()) == {"mock"}
    assert body["pass1_backend"] == "claude"


def test_unknown_session_is_404_everywhere(client):
    assert post_frame(client, "nope").status_code == 404
    r = client.post("/session/nope/audio", files={"audio": ("a.m4a", AUDIO, "audio/mp4")})
    assert r.status_code == 404
    assert client.get("/session/nope/state").status_code == 404
    assert client.post("/session/nope/end").status_code == 404


def test_create_session_without_a_body(client):
    assert client.post("/session").status_code == 200


def test_empty_frame_is_rejected(client):
    sid = new_session(client)
    assert post_frame(client, sid, data=b"").status_code == 400


def test_frame_response_shape_and_boxes(client):
    sid = new_session(client)
    r = post_frame(client, sid)
    assert r.status_code == 200
    fr = FrameResponse.model_validate(r.json())
    assert fr.frame_id == f"{sid}-00001" and fr.quality.is_keyframe
    assert {d.label for d in fr.detections} == {"brake_pad", "rim"}
    assert all(0 <= v <= 1 for d in fr.detections for v in (d.box.x, d.box.y, d.box.w, d.box.h))
    assert FrameResponse.model_validate(post_frame(client, sid).json()).frame_id == f"{sid}-00002"


def test_session_state_endpoint(client):
    sid = new_session(client)
    post_frame(client, sid)
    st = client.get(f"/session/{sid}/state").json()
    assert st["id"] == sid and st["phase"] == "identifying" and "last_frame_jpeg" not in st


# --- full mock repair -------------------------------------------------------

def test_full_repair_flow_to_done(client):
    sid = new_session(client)

    # frame 1 is a keyframe: the slow loop runs in the background; its event rides the next frame reply
    assert FrameResponse.model_validate(post_frame(client, sid).json()).events == []
    assert wait_until(lambda: client.get(f"/session/{sid}/state").json()["queued_events"] == 1)
    f2 = FrameResponse.model_validate(post_frame(client, sid).json())
    assert len(f2.events) == 1 and f2.events[0].phase == "identifying"
    assert FrameResponse.model_validate(post_frame(client, sid).json()).events == [], "events drain once"

    # talk until the mock brain has a plan and starts guiding
    phases = []
    for _ in range(8):
        a = post_audio(client, sid, "question.m4a")
        phases.append(a.response.phase)
        if a.response.phase == "guiding":
            break
    assert phases[-1] == "guiding", phases
    assert phases.index("guiding") == len(phases) - 1
    assert a.response.plan is not None and a.response.current_step == 1

    # say "done" until the repair completes; steps advance one at a time
    steps = [a.response.current_step]
    for _ in range(8):
        a = post_audio(client, sid, "done.m4a")
        steps.append(a.response.current_step)
        if a.response.phase == "done":
            break
    assert a.response.phase == "done", steps
    assert steps[:5] == [1, 2, 3, 4, 5]

    summary = SessionSummary.model_validate(client.post(f"/session/{sid}/end").json())
    assert summary.object == "bicycle rim brake" and summary.steps_total == 5 and summary.steps_done == 5
    assert summary.sources and summary.duration_s >= 0

    # every table got rows
    store = store_of(client)
    for table in ("sessions", "frames", "detections", "observations", "transcripts",
                  "situation_reports", "brain_responses"):
        assert store.read(table), f"no rows in {table}"
    sessions_rows = store.read("sessions")
    assert sessions_rows[0]["status"] == "active" and sessions_rows[-1]["status"] == "done"
    assert any(r["is_keyframe"] and r["image_path"].endswith(".jpg") for r in store.read("frames"))
    assert all(set(r) >= {"frame_id", "label", "score", "x", "y", "w", "h"} for r in store.read("detections"))


def test_keyframes_are_saved_to_the_media_cache(client, settings=None):
    sid = new_session(client)
    post_frame(client, sid)
    wait_until(lambda: not client.get(f"/session/{sid}/state").json()["slow_busy"])
    saved = list((main_mod.get_settings().media_cache_dir / "keyframes").glob("*.jpg"))
    assert [p.name for p in saved] == [f"{sid}-00001.jpg"]
    assert saved[0].read_bytes() == JPEG


def test_audio_response_says_something_and_has_no_tts_without_keys(client):
    sid = new_session(client)
    a = post_audio(client, sid, "start.m4a")
    assert a.transcript.text and a.response.say
    assert a.tts_url is None, "no ElevenLabs key: the app falls back to expo-speech"


def test_failed_stt_gets_an_apology_and_skips_the_brain(client):
    sid = new_session(client)

    async def boom(*a, **k):
        raise RuntimeError("scribe down")
    client.app.state.orch.stages.transcribe = boom
    a = post_audio(client, sid)
    assert a.response.say == main_mod.APOLOGY_NO_SPEECH and a.transcript.text == ""
    assert not store_of(client).read("situation_reports"), "brain must not run without speech"
    assert not client.get(f"/session/{sid}/state").json()["voice_waiting"]


def test_empty_transcript_gets_an_apology(client):
    sid = new_session(client)

    async def silent(*a, **k):
        return Transcript(text="  ", language="en", duration_s=1.0)
    client.app.state.orch.stages.transcribe = silent
    assert post_audio(client, sid).response.say == main_mod.APOLOGY_NO_SPEECH


@pytest.mark.parametrize("stage", ["pass1", "pass2"])
def test_brain_failure_gives_apology_and_keeps_phase(client, stage):
    sid = new_session(client)

    async def boom(*a, **k):
        raise RuntimeError("model down")
    setattr(client.app.state.orch.stages, stage, boom)
    a = post_audio(client, sid)
    assert a.response.say == main_mod.APOLOGY_BRAIN and a.response.phase == "identifying"
    assert not client.get(f"/session/{sid}/state").json()["voice_waiting"]


def test_safety_stop_on_mains_or_gas(client):
    sid = new_session(client)

    async def gas(*a, **k):
        return Transcript(text="my gas boiler is leaking, how do I fix it", language="en", duration_s=3)
    client.app.state.orch.stages.transcribe = gas
    a = post_audio(client, sid)
    assert a.response.phase == "safety_stop" and "professional" in a.response.say
    assert a.response.alert and a.response.alert.severity == "danger"
    assert client.get(f"/session/{sid}/state").json()["phase"] == "safety_stop"


def test_real_mode_without_any_keys_degrades_instead_of_crashing(monkeypatch):
    """USE_MOCKS=0 with no API keys: real Pass 1 / STT fail, the heuristic and apologies take over."""
    set_env(monkeypatch, USE_MOCKS="0")
    vision_detect.reset()
    monkeypatch.setattr(vision_detect, "_load_model", lambda: StubYolo())      # not the real model
    with TestClient(main_mod.create_app()) as c:
        stages = c.get("/health").json()["stages"]
        assert stages["pass1"] == "real" and stages["transcribe"] == "real"
        assert stages["pass2"] == "mock" and stages["preprocess"] == "mock", "P2/P4 modules don't exist yet"
        sid = new_session(c)
        post_frame(c, sid)                                       # keyframe -> slow loop -> real Pass 1 fails
        assert wait_until(lambda: c.get(f"/session/{sid}/state").json()["queued_events"] == 1)
        a = post_audio(c, sid)                                   # real STT has no key
        assert a.response.say == main_mod.APOLOGY_NO_SPEECH
        rows = store_of(c).read("pass1_calls")
        assert [(r["backend"], r["valid_json"]) for r in rows] == [("heuristic", False)]


def test_slow_loop_failure_is_survivable(client):
    sid = new_session(client)

    async def boom(*a, **k):
        raise RuntimeError("claude vision down")
    client.app.state.orch.stages.understand = boom
    post_frame(client, sid)
    assert wait_until(lambda: not client.get(f"/session/{sid}/state").json()["slow_busy"])
    assert post_frame(client, sid).status_code == 200


def test_fast_loop_survives_stage_failures(client):
    sid = new_session(client)

    async def boom(*a, **k):
        raise RuntimeError("x")
    client.app.state.orch.stages.preprocess = boom
    r = post_frame(client, sid)
    assert r.status_code == 200 and FrameResponse.model_validate(r.json()).quality.is_keyframe is False


def test_blurry_frames_skip_detect_and_the_slow_loop(client):
    from backend.contracts import FrameQuality
    sid = new_session(client)

    async def blurry(jpeg, session_id):
        return jpeg, FrameQuality(blurry=True, too_dark=False, is_keyframe=True, sharpness=5, brightness=100)

    async def detect_must_not_run(*a):
        raise AssertionError("detect ran on a blurry frame")
    stages = client.app.state.orch.stages
    stages.preprocess, stages.detect = blurry, detect_must_not_run
    fr = FrameResponse.model_validate(post_frame(client, sid).json())
    assert fr.detections == [] and not client.get(f"/session/{sid}/state").json()["slow_busy"]


# --- tts / media ------------------------------------------------------------

def test_tts_route_serves_generated_audio_and_404s_otherwise(client, monkeypatch):
    set_env(monkeypatch, ELEVENLABS_API_KEY="k", ELEVENLABS_VOICE_ID="v")

    async def fake_open(text):
        async def gen():
            yield b"ID3"
            yield b"audio-bytes"
        return gen()
    monkeypatch.setattr(tts, "_open_stream", fake_open)
    sid = new_session(client)
    a = post_audio(client, sid, "start.m4a")
    assert a.tts_url and a.tts_url.startswith("/tts/") and a.tts_url.endswith(".mp3")
    r = client.get(a.tts_url)
    assert r.status_code == 200 and r.content == b"ID3audio-bytes" and r.headers["content-type"] == "audio/mpeg"
    assert client.get("/tts/0000000000000000.mp3").status_code == 404
    assert client.get("/tts/..%2fsecret.mp3").status_code == 404


def test_media_serves_cached_files(client):
    base = main_mod.get_settings().media_cache_dir
    base.mkdir(parents=True, exist_ok=True)
    (base / "scan.glb").write_bytes(b"glTF")
    (base / "step1.png").write_bytes(b"\x89PNG")
    r = client.get("/media/scan.glb")
    assert r.status_code == 200 and r.content == b"glTF" and r.headers["content-type"] == "model/gltf-binary"
    assert client.get("/media/step1.png").headers["content-type"] == "image/png"
    assert client.get("/media/missing.png").status_code == 404


@pytest.mark.parametrize("path", ["../secret.txt", "..%2fsecret.txt", "%2e%2e/secret.txt",
                                  "..%5csecret.txt", "sub/../../secret.txt", "%2e%2e%2fsecret.txt"])
def test_media_rejects_path_escapes(client, path):
    base = main_mod.get_settings().media_cache_dir
    base.mkdir(parents=True, exist_ok=True)
    (base.parent / "secret.txt").write_text("TOP-SECRET")        # a real file just outside the cache
    (base / "ok.png").write_bytes(b"png")
    r = client.get(f"/media/{path}")
    assert r.status_code in (400, 404)
    assert b"TOP-SECRET" not in r.content
    assert client.get("/media/ok.png").status_code == 200


# --- debug ------------------------------------------------------------------

def test_debug_page_data_and_frame(client):
    assert client.get("/debug").status_code == 200
    assert "Spanner pipeline" in client.get("/debug").text
    assert client.get("/debug/data").json()["session"] is None
    sid = new_session(client)
    post_frame(client, sid)
    wait_until(lambda: not client.get(f"/session/{sid}/state").json()["slow_busy"])
    d = client.get("/debug/data").json()
    assert d["session"] == sid and d["frame_id"] == f"{sid}-00001"
    assert d["detections"] and d["observation"] and d["report"] and d["response"]
    assert {t["stage"] for t in d["timings"]} >= {"preprocess", "detect", "understand", "pass1", "pass2"}
    assert d["stages"]["pass2"] == "mock"
    img = client.get(f"/debug/frame/{d['frame_id']}.jpg")
    assert img.status_code == 200 and img.content == JPEG
    assert client.get("/debug/data?session=bogus").json()["session"] is None


# --- live voice websocket ---------------------------------------------------

def guarded(fn, seconds=15):
    """Run a websocket scenario in a daemon thread so a protocol bug fails instead of hanging."""
    box: dict = {}

    def target():
        try:
            fn()
        except BaseException as e:      # noqa: BLE001
            box["err"] = e
    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        pytest.fail(f"websocket scenario still running after {seconds}s")
    if "err" in box:
        raise box["err"]


def recv(ws, timeout=10.0):
    """Next websocket message as ("json", obj) or ("bytes", data). Raises instead of blocking forever:
    a test that waits on a message that never comes must fail, not hang the run and the TestClient."""
    import anyio
    rx = getattr(ws, "_send_rx", None)                # Starlette 1.x TestClient: an anyio receive stream
    if rx is not None:
        async def get():
            with anyio.fail_after(timeout):
                return await rx.receive()
        try:
            m = ws.portal.call(get)
        except TimeoutError:
            raise AssertionError(f"no websocket message within {timeout}s") from None
    else:                                             # other versions: plain blocking receive
        m = ws.receive()
    if m["type"] == "websocket.close":
        raise WebSocketDisconnect(m.get("code", 1000))
    if m.get("bytes") is not None:
        return "bytes", m["bytes"]
    return "json", json.loads(m["text"])


def collect_until(ws, kind, limit=60):
    out = []
    for _ in range(limit):
        t, v = recv(ws)
        out.append((t, v))
        if t == "json" and v["type"] == kind:
            return out
    raise AssertionError(f"never saw {kind}: {[(a, b if a == 'json' else len(b)) for a, b in out]}")


ONE_SECOND = b"\x01\x00" * 16000   # 1 s of non-silent 16 kHz mono PCM16 = one mock utterance


def inject(client, conn, kind, text, duration=0.0):
    """Make the live transcriber 'hear' something. asyncio.Queue is not thread-safe, so the put
    must run on the server's event loop (the TestClient portal), not on the test thread."""
    item = main_mod.stt_mod.LiveTranscript(kind, text, duration_s=duration)
    client.portal.call(conn.transcriber._queue.put_nowait, item)


def test_live_voice_round_trip_on_mocks(client):
    sid = new_session(client)

    def scenario():
        with client.websocket_connect(f"/session/{sid}/live") as ws:
            ws.send_text(json.dumps({"type": "start", "keyterms": ["brake_pad"]}))
            ws.send_bytes(ONE_SECOND)
            got = collect_until(ws, "response")
            types = [v["type"] for t, v in got if t == "json"]
            assert types == ["partial", "final", "response"]
            final = next(v for t, v in got if t == "json" and v["type"] == "final")
            assert "hex key" in final["text"]
            resp = got[-1][1]["response"]
            assert resp["say"] and resp["phase"] in ("identifying", "diagnosing")
            ws.send_text(json.dumps({"type": "stop"}))

    guarded(scenario)
    assert wait_until(lambda: client.get(f"/session/{sid}/state").json()["voice_waiting"] is False)
    assert store_of(client).read("transcripts")[-1]["source"] == "live"
    assert store_of(client).read("situation_reports")


def test_live_auto_starts_on_first_audio_without_a_start_message(client):
    sid = new_session(client)

    def scenario():
        with client.websocket_connect(f"/session/{sid}/live") as ws:
            ws.send_bytes(ONE_SECOND)
            collect_until(ws, "response")

    guarded(scenario)


def test_live_unknown_session_is_rejected(client):
    def scenario():
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect("/session/nope/live"):
                pass
        assert e.value.code == 4404

    guarded(scenario)


def test_live_bad_control_message_gets_an_error_and_stays_open(client):
    sid = new_session(client)

    def scenario():
        with client.websocket_connect(f"/session/{sid}/live") as ws:
            ws.send_text("not json")
            assert recv(ws)[1]["type"] == "error"
            ws.send_text(json.dumps({"type": "dance"}))
            err = recv(ws)[1]
            assert err["type"] == "error" and "dance" in err["message"]
            ws.send_bytes(ONE_SECOND)
            collect_until(ws, "response")

    guarded(scenario)


def test_second_live_connection_replaces_the_first(client):
    sid = new_session(client)

    def scenario():
        with client.websocket_connect(f"/session/{sid}/live") as first:
            first.send_text(json.dumps({"type": "start"}))
            assert wait_until(lambda: client.get(f"/session/{sid}/state").status_code == 200)
            with client.websocket_connect(f"/session/{sid}/live") as second:
                with pytest.raises(WebSocketDisconnect) as e:
                    for _ in range(5):
                        recv(first)
                assert e.value.code == 4000
                second.send_bytes(ONE_SECOND)
                collect_until(second, "response")

    guarded(scenario)


def fake_tts(monkeypatch, chunks, delay=0.0):
    import asyncio
    set_env(monkeypatch, ELEVENLABS_API_KEY="k", ELEVENLABS_VOICE_ID="v")

    async def open_stream(text):
        async def gen():
            for c in chunks:
                if delay:
                    await asyncio.sleep(delay)
                yield c
        return gen()
    monkeypatch.setattr(tts, "_open_stream", open_stream)


def test_live_streams_spoken_reply_between_tts_start_and_end(client, monkeypatch):
    fake_tts(monkeypatch, [b"aaaa", b"bbbb", b"cccc"])
    sid = new_session(client)

    def scenario():
        with client.websocket_connect(f"/session/{sid}/live") as ws:
            ws.send_text(json.dumps({"type": "start", "tts": "stream"}))
            ws.send_bytes(ONE_SECOND)
            collect_until(ws, "response")
            got = collect_until(ws, "tts_end")
            assert got[0] == ("json", {"type": "tts_start"})
            assert [v for t, v in got if t == "bytes"] == [b"aaaa", b"bbbb", b"cccc"]

    guarded(scenario)


def test_barge_in_stops_the_spoken_reply_quickly(client, monkeypatch):
    fake_tts(monkeypatch, [b"x" * 100] * 200, delay=0.02)       # ~4 s of audio
    sid = new_session(client)

    def scenario():
        with client.websocket_connect(f"/session/{sid}/live") as ws:
            ws.send_text(json.dumps({"type": "start", "tts": "stream"}))
            ws.send_bytes(ONE_SECOND)
            collect_until(ws, "response")
            assert recv(ws) == ("json", {"type": "tts_start"})
            assert recv(ws)[0] == "bytes"
            t0 = time.perf_counter()
            ws.send_text(json.dumps({"type": "barge_in"}))
            frames_after = 0
            while True:
                t, v = recv(ws)
                if t == "json" and v["type"] == "tts_end":
                    break
                frames_after += 1
            assert time.perf_counter() - t0 < 1.0
            assert frames_after < 30, "audio kept flowing after barge_in"

    guarded(scenario)
    assert not list(tts.cache_dir().glob("*")), "an interrupted reply must not be cached half-written"


def test_echo_of_our_own_speech_is_ignored(client, monkeypatch):
    """The mic hearing the speaker must not trigger a reply or cut the speech off."""
    fake_tts(monkeypatch, [b"x" * 100] * 100, delay=0.02)
    sid = new_session(client)

    def scenario():
        with client.websocket_connect(f"/session/{sid}/live") as ws:
            ws.send_text(json.dumps({"type": "start", "tts": "stream"}))
            ws.send_bytes(ONE_SECOND)
            collect_until(ws, "response")
            assert recv(ws) == ("json", {"type": "tts_start"})
            conn = client.app.state.orch.sessions.get(sid).live_conn
            spoken = conn.spoken_text
            assert spoken
            # the transcriber "hears" the assistant's own words
            inject(client, conn, "partial", spoken.split(".")[0])
            inject(client, conn, "final", spoken, duration=2)
            # a marker the loop processes after the echo items proves they were consumed
            inject(client, conn, "error", "marker")
            seen = []
            while True:
                t, v = recv(ws)
                if t == "json":
                    seen.append(v["type"])
                    if v["type"] == "error":
                        break
            assert seen == ["error"], f"echo leaked to the phone as {seen}"
            assert conn.speaking, "echo must not interrupt the reply"
            assert conn.queue.empty() and conn.pending == 0
            ws.send_text(json.dumps({"type": "barge_in"}))
            collect_until(ws, "tts_end")

    guarded(scenario)


def test_real_speech_over_the_reply_interrupts_and_is_answered(client, monkeypatch):
    fake_tts(monkeypatch, [b"x" * 100] * 200, delay=0.02)
    sid = new_session(client)

    def scenario():
        with client.websocket_connect(f"/session/{sid}/live") as ws:
            ws.send_text(json.dumps({"type": "start", "tts": "stream"}))
            ws.send_bytes(ONE_SECOND)
            collect_until(ws, "response")
            assert recv(ws) == ("json", {"type": "tts_start"})
            conn = client.app.state.orch.sessions.get(sid).live_conn
            t0 = time.perf_counter()
            inject(client, conn, "partial", "actually wait")
            collect_until(ws, "tts_end")                   # speech cut off by the user's voice
            assert time.perf_counter() - t0 < 1.5, "reply should stop right away, not run its ~4 s course"
            inject(client, conn, "final", "I'm done with that step", duration=2)
            got = collect_until(ws, "response")
            kinds = [v["type"] for t, v in got if t == "json"]
            assert kinds == ["partial", "final", "response"], kinds
            assert got[-1][1]["response"]["phase"] in ("diagnosing", "researching", "guiding")

    guarded(scenario)


def test_looks_like_echo():
    spoken = "Step 2: slide the pad so it sits flat on the metal braking surface."
    assert main_mod.looks_like_echo("slide the pad so it sits flat", spoken)
    assert main_mod.looks_like_echo("Step two slide the pad", spoken)          # one misheard word is still echo
    assert not main_mod.looks_like_echo("stop wait a second", spoken)
    assert not main_mod.looks_like_echo("", spoken)
    assert not main_mod.looks_like_echo("anything", "")


def test_keyterms_come_from_step_parts_and_observations():
    from backend.contracts import BrainResponse, RepairPlan, SceneObservation
    from backend.mocks import fixture_json
    from backend.session import Session
    s = Session(id="s")
    s.apply(BrainResponse(phase="guiding", plan=RepairPlan.model_validate(fixture_json("repair_plan")),
                          current_step=2, step_status="in_progress"))
    s.observations.append(SceneObservation.model_validate(fixture_json("scene_observation_2")))
    terms = main_mod.keyterms_for(s)
    assert terms[:3] == ["brake_pad", "rim", "tyre"] and "allen_bolt" in terms and "hex key" not in terms
    assert len(terms) == len(set(terms))
    assert "4 mm or 5 mm hex key" in terms


# --- live socket: frames, clips, push, playback mute ------------------------

import base64  # noqa: E402

B64_JPEG = base64.b64encode(JPEG).decode()
B64_CLIP = base64.b64encode(AUDIO).decode()


def connect(client, sid):
    return client.websocket_connect(f"/session/{sid}/live")


def send(ws, **msg):
    ws.send_text(json.dumps(msg))


def next_json(ws, timeout=10.0):
    t, v = recv(ws, timeout)
    assert t == "json", f"expected a JSON message, got {len(v)} binary bytes"
    return v


def assert_silent(ws, seconds=0.6):
    """Nothing at all arrives within `seconds`."""
    try:
        got = recv(ws, seconds)
    except AssertionError as e:
        assert "no websocket message" in str(e), e
        return
    pytest.fail(f"unexpected message: {got if got[0] == 'json' else ('bytes', len(got[1]))}")


def test_frame_over_the_socket_returns_a_frame_result(client):
    sid = new_session(client)

    def scenario():
        with connect(client, sid) as ws:
            send(ws, type="frame", jpeg_b64=B64_JPEG, ts=1.5)
            m = next_json(ws)
            assert m["type"] == "frame_result"
            fr = FrameResponse.model_validate(m["result"])
            assert fr.frame_id == f"{sid}-00001" and fr.quality.is_keyframe
            assert {d.label for d in fr.detections} == {"brake_pad", "rim"}
            assert fr.events == [], "on the socket, brain results are pushed, never piggy-backed"

    guarded(scenario)
    assert store_of(client).read("frames")[0]["ts"] == 1.5


def test_slow_loop_reply_is_pushed_without_another_frame(client):
    import asyncio
    sid = new_session(client)
    stages = client.app.state.orch.stages
    orig = stages.pass2

    async def slow_pass2(report, session):          # the reply is ready well after the frame was answered,
        await asyncio.sleep(0.4)                    # so nothing but a push can deliver it
        return await orig(report, session)
    stages.pass2 = slow_pass2

    def scenario():
        with connect(client, sid) as ws:
            send(ws, type="frame", jpeg_b64=B64_JPEG)               # frame 1 is a keyframe -> slow loop
            assert next_json(ws)["type"] == "frame_result"
            ev = next_json(ws)                                      # arrives on its own: no second frame sent
            assert ev["type"] == "event" and ev["response"]["phase"] == "identifying"
            assert ev["response"]["say"]

    guarded(scenario)
    assert client.get(f"/session/{sid}/state").json()["queued_events"] == 0, "pushed, not queued"


def test_events_queued_before_the_socket_connected_are_delivered_on_connect(client):
    sid = new_session(client)
    from backend.contracts import BrainResponse
    client.app.state.orch.sessions.get(sid).queue_event(BrainResponse(phase="diagnosing", say="while you were away"))

    def scenario():
        with connect(client, sid) as ws:
            ev = next_json(ws)
            assert ev["type"] == "event" and ev["response"]["say"] == "while you were away"

    guarded(scenario)


def test_media_is_pushed_as_a_media_only_event(client):
    from backend.contracts import MediaUpdate
    sid = new_session(client)
    session = client.app.state.orch.sessions.get(sid)

    def scenario():
        with connect(client, sid) as ws:
            send(ws, type="start")
            assert wait_until(lambda: session.live_conn is not None and session.live_conn.loop is not None)
            session.queue_media(MediaUpdate(kind="illustration", step_index=1, url="/media/s1.png"))   # another thread
            ev = next_json(ws)
            assert ev["type"] == "event" and ev["response"]["say"] == ""
            assert ev["response"]["media"][0]["url"] == "/media/s1.png"

    guarded(scenario)


def test_a_burst_of_frames_keeps_only_the_newest_waiting_frame(client):
    import asyncio
    sid = new_session(client)
    orch = client.app.state.orch
    seen: list[float] = []
    original = orch.handle_frame

    async def slow(session, jpeg, meta, drain_events=True):
        seen.append(meta["ts"])
        await asyncio.sleep(0.25)
        return await original(session, jpeg, meta, drain_events)
    orch.handle_frame = slow

    def scenario():
        with connect(client, sid) as ws:
            for ts in (1.0, 2.0, 3.0, 4.0, 5.0):
                send(ws, type="frame", jpeg_b64=B64_JPEG, ts=ts)
            results = []
            while len(results) < 2:
                m = next_json(ws)
                if m["type"] == "frame_result":
                    results.append(m)
            assert_silent(ws, 0.8)                                  # frames 2-4 were dropped, not queued

    guarded(scenario)
    assert seen == [1.0, 5.0]


@pytest.mark.parametrize("msg,code", [
    ({"type": "frame", "jpeg_b64": "!!!not base64!!!"}, "bad_frame"),
    ({"type": "frame", "jpeg_b64": ""}, "bad_frame"),
    ({"type": "utterance", "audio_b64": "!!!"}, "bad_message"),
    ({"type": "audio", "pcm_b64": "!!!"}, "bad_message"),
    ({"type": "playback", "state": "pause"}, "bad_message"),
    ({"type": "frame"}, "bad_message"),
    ({"type": "start", "tts": "carrier-pigeon"}, "bad_message"),
])
def test_malformed_messages_get_an_error_and_the_socket_stays_open(client, msg, code):
    sid = new_session(client)

    def scenario():
        with connect(client, sid) as ws:
            send(ws, **msg)
            err = next_json(ws)
            assert err["type"] == "error" and err["code"] == code, err
            send(ws, type="frame", jpeg_b64=B64_JPEG)               # still alive
            assert next_json(ws)["type"] == "frame_result"

    guarded(scenario)


def test_voice_clip_gets_a_transcript_a_response_and_a_tts_url(client, monkeypatch):
    fake_tts(monkeypatch, [b"ID3", b"audio"])
    sid = new_session(client)

    def scenario():
        with connect(client, sid) as ws:
            send(ws, type="start")
            send(ws, type="utterance", audio_b64=B64_CLIP, format="m4a", duration_s=2.0)
            final = next_json(ws)
            assert final["type"] == "final" and "hex key" in final["text"]
            resp = next_json(ws)
            assert resp["type"] == "response" and resp["speak"] is True
            url = resp["response"]["tts_url"]
            assert url and url.startswith("/tts/") and url.endswith(".mp3")
            assert client.get(url).content == b"ID3audio"
            assert_silent(ws)                                       # url mode: no audio on the socket

    guarded(scenario)
    assert store_of(client).read("transcripts")[-1]["source"] == "live"
    assert not client.get(f"/session/{sid}/state").json()["voice_waiting"]


def test_voice_clip_without_tts_keys_has_no_url_but_still_speaks_via_say(client):
    sid = new_session(client)

    def scenario():
        with connect(client, sid) as ws:
            send(ws, type="utterance", audio_b64=B64_CLIP)
            next_json(ws)
            resp = next_json(ws)
            assert resp["speak"] is True and resp["response"]["say"] and resp["response"]["tts_url"] is None

    guarded(scenario)


def test_failed_clip_gets_an_apology_and_no_brain_run(client):
    sid = new_session(client)

    async def boom(*a, **k):
        raise RuntimeError("scribe down")
    client.app.state.orch.stages.transcribe = boom

    def scenario():
        with connect(client, sid) as ws:
            send(ws, type="utterance", audio_b64=B64_CLIP)
            resp = next_json(ws)
            assert resp["type"] == "response" and resp["response"]["say"] == main_mod.APOLOGY_NO_SPEECH
            assert_silent(ws)

    guarded(scenario)
    assert not store_of(client).read("situation_reports")
    assert not client.get(f"/session/{sid}/state").json()["voice_waiting"]


def test_audio_and_clips_are_dropped_while_the_phone_is_playing_a_reply(client):
    sid = new_session(client)
    calls = []
    orig = client.app.state.orch.stages.transcribe

    async def counting(*a, **k):
        calls.append(1)
        return await orig(*a, **k)
    client.app.state.orch.stages.transcribe = counting

    def scenario():
        with connect(client, sid) as ws:
            send(ws, type="playback", state="start")
            send(ws, type="utterance", audio_b64=B64_CLIP)          # its own speaker: dropped
            ws.send_bytes(ONE_SECOND)                               # streaming audio: dropped
            send(ws, type="audio", pcm_b64=base64.b64encode(ONE_SECOND).decode())
            assert_silent(ws, 0.8)
            assert calls == []
            send(ws, type="playback", state="end")
            send(ws, type="utterance", audio_b64=B64_CLIP)          # still inside the reverberation tail: dropped
            assert_silent(ws, 0.3)
            assert calls == []
            time.sleep(main_mod.LiveConnection.MUTE_TAIL_S + 0.2)   # now the tail has passed
            send(ws, type="utterance", audio_b64=B64_CLIP)
            assert next_json(ws)["type"] == "final"
            assert next_json(ws)["type"] == "response"
            assert calls == [1]

    guarded(scenario)


def test_a_reply_is_shown_but_not_spoken_if_the_user_started_talking_again(client, monkeypatch):
    import asyncio
    fake_tts(monkeypatch, [b"x"])
    sid = new_session(client)
    stages = client.app.state.orch.stages
    orig = stages.pass2

    async def slow_pass2(report, session):
        await asyncio.sleep(0.6)
        return await orig(report, session)
    stages.pass2 = slow_pass2

    def scenario():
        with connect(client, sid) as ws:
            send(ws, type="utterance", audio_b64=B64_CLIP)
            assert next_json(ws)["type"] == "final"
            conn = client.app.state.orch.sessions.get(sid).live_conn
            inject(client, conn, "partial", "actually hold on")      # new speech while the brain works
            got = collect_until(ws, "response")
            resp = got[-1][1]
            assert any(t == "json" and v["type"] == "partial" for t, v in got)
            assert resp["speak"] is False and resp["response"]["tts_url"] is None
            assert resp["response"]["say"], "the reply itself is still delivered"

    guarded(scenario)


def test_pcm_audio_over_a_json_message_works_like_a_binary_frame(client):
    sid = new_session(client)

    def scenario():
        with connect(client, sid) as ws:
            send(ws, type="audio", pcm_b64=base64.b64encode(ONE_SECOND).decode())
            assert [next_json(ws)["type"], next_json(ws)["type"], next_json(ws)["type"]] == ["partial", "final", "response"]

    guarded(scenario)


def test_tts_url_streams_progressively_while_it_is_still_being_generated(client, monkeypatch):
    import asyncio
    set_env(monkeypatch, ELEVENLABS_API_KEY="k", ELEVENLABS_VOICE_ID="v")

    async def open_stream(text):
        async def gen():
            for i in range(8):
                await asyncio.sleep(0.1)
                yield bytes([i]) * 10
        return gen()
    monkeypatch.setattr(tts, "_open_stream", open_stream)
    sid = new_session(client)
    a = post_audio(client, sid, "start.m4a")                        # starts generation, returns at once
    t0 = time.perf_counter()
    with client.stream("GET", a.tts_url) as r:
        assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
        chunks = iter(r.iter_bytes())
        first = next(chunks)
        t_first = time.perf_counter() - t0
        rest = b"".join(chunks)
    assert t_first < 0.5, f"first audio took {t_first:.2f}s; the whole reply takes ~0.8s"
    assert first + rest == b"".join(bytes([i]) * 10 for i in range(8))
    assert client.get(a.tts_url).content == first + rest             # now a plain cached file


# --- knowledge hooks: step tracker and the Linkup parts card ------------------

def drive_to_guiding(client, sid, via_socket_ws=None):
    """Talk to the mock brain until it is guiding step 1 (the mock needs a few turns)."""
    for _ in range(8):
        if via_socket_ws is not None:
            send(via_socket_ws, type="utterance", audio_b64=B64_CLIP)
            collect_until(via_socket_ws, "response")
        else:
            post_audio(client, sid, "question.m4a")
        if client.get(f"/session/{sid}/state").json()["phase"] == "guiding":
            return
    raise AssertionError("never reached guiding")


def card_events(messages):
    return [v for t, v in messages if t == "json" and v["type"] == "event" and v["response"].get("parts_card")]


def test_parts_card_is_pushed_once_when_the_job_finishes(client):
    sid = new_session(client)
    orch = client.app.state.orch
    orch.parts_poll_s = 0.05

    def scenario():
        with connect(client, sid) as ws:
            drive_to_guiding(client, sid, via_socket_ws=ws)
            got = collect_until(ws, "event", limit=20)               # the card arrives on its own
            ev = card_events(got)[0]
            card = ev["response"]["parts_card"]
            assert card["title"] == "Replace the brake pads" and len(card["options"]) == 3
            assert card["options"][0]["badge"].startswith("✓ Fits your")
            assert card["decision"]["verdict"] == "repair" and ev["response"]["phase"] == "guiding"
            send(ws, type="utterance", audio_b64=B64_CLIP)           # another turn: the same job is not pushed again
            more = collect_until(ws, "response")
            assert card_events(more) == []
            assert_silent(ws, 0.4)

    guarded(scenario)
    job = client.app.state.orch.sessions.get(sid).brain_state["parts_job"]
    assert job.startswith("mock-parts-") and job in client.app.state.orch.sessions.get(sid).parts_jobs


def test_parts_card_without_a_socket_waits_in_the_queue_for_the_next_frame(client):
    sid = new_session(client)
    client.app.state.orch.parts_poll_s = 0.05
    drive_to_guiding(client, sid)
    assert wait_until(lambda: client.get(f"/session/{sid}/state").json()["queued_events"] >= 1)
    fr = FrameResponse.model_validate(post_frame(client, sid).json())
    cards = [e.parts_card for e in fr.events if e.parts_card]
    assert len(cards) == 1 and cards[0]["title"] == "Replace the brake pads"


@pytest.mark.parametrize("result", [{"status": "failed", "error": "linkup down"}, {"status": "unknown_job"}, "raise"])
def test_a_failed_parts_job_pushes_nothing_and_breaks_nothing(client, result):
    sid = new_session(client)
    client.app.state.orch.parts_poll_s = 0.05

    async def poll(job):
        if result == "raise":
            raise RuntimeError("boom")
        return result
    client.app.state.orch.stages.parts_card = poll
    drive_to_guiding(client, sid)
    time.sleep(0.4)
    state = client.get(f"/session/{sid}/state").json()
    assert state["phase"] == "guiding" and state["queued_events"] == 0


def test_a_parts_job_that_never_finishes_is_abandoned(client):
    sid = new_session(client)
    orch = client.app.state.orch
    orch.parts_poll_s, orch.parts_timeout_s = 0.02, 0.2
    polls = []

    async def forever(job):
        polls.append(1)
        return {"status": "researching"}
    orch.stages.parts_card = forever
    drive_to_guiding(client, sid)
    time.sleep(0.8)
    n = len(polls)
    time.sleep(0.3)
    assert 3 <= n and len(polls) == n, "polling must stop after the timeout"


def tracker_recording(client, result=None, exc=None):
    calls = []

    async def fake(step, observation, said="", session_id=None, repair_id=None):
        calls.append({"step": getattr(step, "index", None), "said": said, "session": session_id, "repair": repair_id})
        if exc:
            raise exc
        return result
    client.app.state.orch.stages.track_step = fake
    return calls


def test_step_tracker_is_not_called_before_guiding(client):
    sid = new_session(client)
    calls = tracker_recording(client, {"step_status": "done"})
    post_frame(client, sid)                                          # an observation exists
    wait_until(lambda: not client.get(f"/session/{sid}/state").json()["slow_busy"])
    post_audio(client, sid)                                          # phase is still identifying/diagnosing
    assert calls == []
    assert client.app.state.orch.sessions.get(sid).last_step_check is None


def test_step_tracker_needs_something_the_camera_has_seen(client):
    sid = new_session(client)
    drive_to_guiding(client, sid)                                    # no frame was ever sent: no observation
    calls = tracker_recording(client, {"step_status": "done"})
    post_audio(client, sid)
    assert calls == []


def test_step_tracker_runs_while_guiding_and_a_mistake_becomes_an_alert(client):
    sid = new_session(client)
    post_frame(client, sid)                                          # give it an observation
    wait_until(lambda: not client.get(f"/session/{sid}/state").json()["slow_busy"])
    drive_to_guiding(client, sid)
    calls = tracker_recording(client, {"step_status": "mistake", "mistake": "pad pushed in upside down",
                                       "status": "error", "backend": "adaption"})
    client.app.state.orch.sessions.get(sid).brain_state["repair_id"] = "bike-brake"
    a = post_audio(client, sid, "question.m4a")
    assert a.response.alert and a.response.alert.severity == "danger"
    assert a.response.alert.message == "Pad pushed in upside down."          # the tracker's words, as a sentence
    assert calls and calls[0]["step"] == 1 and calls[0]["said"] and calls[0]["session"] == sid
    assert calls[0]["repair"] == "bike-brake"
    assert client.get(f"/session/{sid}/state").json()["last_step_check"]["step_status"] == "mistake"
    assert "track_step" in {t["stage"] for t in client.get(f"/session/{sid}/state").json()["timings"]}


def test_a_done_check_from_the_camera_advances_the_step_without_the_user_saying_so(client):
    sid = new_session(client)
    post_frame(client, sid)
    wait_until(lambda: not client.get(f"/session/{sid}/state").json()["slow_busy"])
    drive_to_guiding(client, sid)
    before = client.get(f"/session/{sid}/state").json()["current_step_index"]
    tracker_recording(client, {"step_status": "done", "mistake": "", "status": "done"})

    def scenario():
        orch = client.app.state.orch
        session = orch.sessions.get(sid)

        async def keyframe_turn():          # what the slow loop does on a keyframe: no speech
            async with session.brain_lock:
                return await orch.run_brain(session, None)
        return client.portal.call(keyframe_turn)

    _, response = scenario()
    assert response.current_step == before + 2, "step index is 1-based, so one step further"


@pytest.mark.parametrize("failure", [RuntimeError("tracker down"), TimeoutError()])
def test_a_failing_step_tracker_never_blocks_the_brain(client, failure):
    sid = new_session(client)
    post_frame(client, sid)
    wait_until(lambda: not client.get(f"/session/{sid}/state").json()["slow_busy"])
    drive_to_guiding(client, sid)
    calls = tracker_recording(client, exc=failure)
    # a verdict left over from an earlier turn must never reach Pass 2 (the mock would raise an alert on it)
    client.app.state.orch.sessions.get(sid).last_step_check = {"step_status": "mistake", "mistake": "stale"}
    a = post_audio(client, sid)
    assert calls and a.response.phase == "guiding" and a.response.alert is None
    assert client.get(f"/session/{sid}/state").json()["last_step_check"] is None


def test_real_mode_uses_the_real_knowledge_functions_for_the_new_stages(monkeypatch):
    set_env(monkeypatch, USE_MOCKS="0")
    vision_detect.reset()
    monkeypatch.setattr(vision_detect, "_load_model", lambda: StubYolo())      # not the real model
    with TestClient(main_mod.create_app()) as c:
        stages = c.get("/health").json()["stages"]
        assert stages["track_step"] == "real" and stages["parts_card"] == "real"


# --- the real detect stage in the fast loop -----------------------------------

def test_real_detect_is_warmed_at_startup_and_feeds_the_fast_loop(monkeypatch):
    set_env(monkeypatch, USE_MOCKS="0")
    vision_detect.reset()
    box = StubResult(StubBoxes([[76.8, 57.6, 384, 288]], [0.88], [0], [5]))        # placeholder.jpg is 768x576
    model = StubYolo([StubResult(None), box])                                       # first call is the warm-up frame
    monkeypatch.setattr(vision_detect, "_load_model", lambda: model)
    try:
        with TestClient(main_mod.create_app()) as c:
            assert c.get("/health").json()["stages"]["detect"] == "real"
            assert wait_until(lambda: len(model.calls) == 1), "warm-up should run at startup, before any frame"
            sid = new_session(c)
            fr = FrameResponse.model_validate(post_frame(c, sid).json())
            assert [(d.label, d.track_id) for d in fr.detections] == [("bicycle", 5)]
            b = fr.detections[0].box
            assert (b.x, b.y, b.w, b.h) == pytest.approx((0.1, 0.1, 0.4, 0.4))
            assert [call["persist"] for call in model.calls] == [False, False]    # fresh tracker for the new session
            assert "detect" in {t["stage"] for t in c.get(f"/session/{sid}/state").json()["timings"]}
    finally:
        vision_detect.reset()


def test_a_broken_detector_does_not_stop_frames(monkeypatch):
    set_env(monkeypatch, USE_MOCKS="0")
    vision_detect.reset()

    def boom():
        raise RuntimeError("weights missing")
    monkeypatch.setattr(vision_detect, "_load_model", boom)
    try:
        with TestClient(main_mod.create_app()) as c:
            sid = new_session(c)
            r = post_frame(c, sid)
            assert r.status_code == 200 and FrameResponse.model_validate(r.json()).detections == []
    finally:
        vision_detect.reset()
