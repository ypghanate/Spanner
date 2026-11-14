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
from backend.voice import tts
from tests.conftest import set_env

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
                                   "transcribe", "store"}
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


def recv(ws):
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
