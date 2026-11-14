"""STT / TTS: filenames, caching, streaming, cancellation, live transcriber plumbing."""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from backend.voice import stt, tts
from tests.conftest import set_env
from tests.helpers import run


@pytest.fixture
def keys(monkeypatch):
    return set_env(monkeypatch, ELEVENLABS_API_KEY="k", ELEVENLABS_VOICE_ID="voice-a")


@pytest.fixture(autouse=True)
def clean_tts_state():
    for d in (tts._inflight, tts._errors, tts._progress):
        d.clear()
    yield
    for d in (tts._inflight, tts._errors, tts._progress):
        d.clear()


def fake_stream(chunks, calls, delay=0.0):
    async def _open(text):
        calls.append(text)

        async def gen():
            for c in chunks:
                if delay:
                    await asyncio.sleep(delay)
                yield c
        return gen()
    return _open


# --- tts --------------------------------------------------------------------

def test_filename_is_stable_and_depends_on_voice_and_text(monkeypatch):
    set_env(monkeypatch, ELEVENLABS_API_KEY="k", ELEVENLABS_VOICE_ID="voice-a")
    a1, a2 = tts.tts_filename("hello"), tts.tts_filename("hello")
    assert a1 == a2 and a1.endswith(".mp3") and len(a1) == 16 + 4
    assert tts.tts_filename("hello there") != a1
    set_env(monkeypatch, ELEVENLABS_API_KEY="k", ELEVENLABS_VOICE_ID="voice-b")
    assert tts.tts_filename("hello") != a1


def test_start_returns_none_without_keys_or_text(keys, monkeypatch):
    async def main():
        assert tts.start("") is None and tts.start("   ") is None
    run(main())
    set_env(monkeypatch, ELEVENLABS_API_KEY="", ELEVENLABS_VOICE_ID="")

    async def main2():
        assert tts.start("hello") is None
    run(main2())


def test_start_generates_file_and_second_call_uses_cache(keys, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(tts, "_open_stream", fake_stream([b"abc", b"def"], calls))

    async def main():
        name = tts.start("Step one")
        assert name == tts.tts_filename("Step one")
        path = await tts.wait_for(name)
        assert path is not None and path.read_bytes() == b"abcdef"
        assert tts.start("Step one") == name           # cached: no new call
        assert tts.start("Step one") == name
        await asyncio.sleep(0.01)

    run(main())
    assert calls == ["Step one"]


def test_start_returns_immediately_while_generation_continues(keys, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(tts, "_open_stream", fake_stream([b"a", b"b", b"c"], calls, delay=0.05))

    async def main():
        t0 = asyncio.get_running_loop().time()
        name = tts.start("slow one")
        assert asyncio.get_running_loop().time() - t0 < 0.02
        assert not (tts.cache_dir() / name).exists()
        assert await tts.wait_for(name, timeout=2) is not None

    run(main())


def test_failed_generation_gives_none_and_leaves_no_file(keys, monkeypatch):
    async def boom(text):
        raise RuntimeError("elevenlabs down")
    monkeypatch.setattr(tts, "_open_stream", boom)

    async def main():
        name = tts.start("hello")
        assert await tts.wait_for(name, timeout=1) is None
        assert not list(tts.cache_dir().glob("*"))

    run(main())
    assert "elevenlabs down" in next(iter(tts._errors.values()))


def test_wait_for_rejects_unsafe_names(keys):
    async def main():
        assert await tts.wait_for("../secret.mp3") is None
        assert await tts.wait_for("x/y.mp3") is None
        assert await tts.wait_for("notmp3.wav") is None
    run(main())


def test_stream_yields_chunks_and_caches_for_replay(keys, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(tts, "_open_stream", fake_stream([b"one", b"two", b"three"], calls))

    async def collect(text):
        return [c async for c in tts.stream(text)]

    async def main():
        first = await collect("Say it")
        assert first == [b"one", b"two", b"three"]
        again = await collect("Say it")
        assert b"".join(again) == b"onetwothree"

    run(main())
    assert calls == ["Say it"], "second stream must come from the cache"


def test_cancelled_stream_leaves_no_partial_file(keys, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(tts, "_open_stream", fake_stream([b"x"] * 50, calls, delay=0.01))

    async def main():
        got: list[bytes] = []

        async def consume():
            async for chunk in tts.stream("long reply"):
                got.append(chunk)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        task.cancel()                                   # barge-in
        with pytest.raises(asyncio.CancelledError):
            await task
        assert 0 < len(got) < 50
        await asyncio.sleep(0.02)
        assert not list(tts.cache_dir().glob("*")), "a half-written file must never reach the cache"

    run(main())


def test_stream_with_nothing_to_say_yields_nothing(keys):
    async def main():
        assert [c async for c in tts.stream("")] == []
    run(main())


# --- stt: one shot ----------------------------------------------------------

def _client_returning(resp=None, exc=None, delay=0.0):
    async def convert(**kwargs):
        convert.kwargs = kwargs
        if delay:
            await asyncio.sleep(delay)
        if exc:
            raise exc
        return resp
    return SimpleNamespace(speech_to_text=SimpleNamespace(convert=convert)), convert


def test_transcribe_maps_response_and_passes_keyterms(keys, monkeypatch):
    resp = SimpleNamespace(text=" Which size key? ", language_code="en",
                           words=[SimpleNamespace(end=0.4), SimpleNamespace(end=2.6), SimpleNamespace(end=None)])
    client, convert = _client_returning(resp)
    monkeypatch.setattr(stt, "_get_client", lambda: client)
    t = run(stt.transcribe(b"audio", "q.m4a", keyterms=["brake_pad", "x" * 40, "rim"], duration_hint_s=9))
    assert t.text == "Which size key?" and t.language == "en" and t.duration_s == 2.6
    assert convert.kwargs["keyterms"] == ["brake pad", "rim"]       # long ones dropped, _ -> space
    assert convert.kwargs["file"][0] == "q.m4a" and convert.kwargs["file"][1] == b"audio"


def test_transcribe_falls_back_to_duration_hint(keys, monkeypatch):
    client, _ = _client_returning(SimpleNamespace(text="hi", language_code=None, words=[]))
    monkeypatch.setattr(stt, "_get_client", lambda: client)
    t = run(stt.transcribe(b"a", "a.m4a", duration_hint_s=1.5))
    assert t.duration_s == 1.5 and t.language == "en"


def test_transcribe_errors_become_stt_error(keys, monkeypatch):
    client, _ = _client_returning(exc=RuntimeError("503"))
    monkeypatch.setattr(stt, "_get_client", lambda: client)
    with pytest.raises(stt.SttError):
        run(stt.transcribe(b"a", "a.m4a"))


def test_transcribe_times_out(keys, monkeypatch):
    client, _ = _client_returning(SimpleNamespace(text="x", language_code="en", words=[]), delay=1.0)
    monkeypatch.setattr(stt, "_get_client", lambda: client)
    import dataclasses
    from backend import config
    fast = dataclasses.replace(config.get_settings(), stt_timeout_s=0.05)
    monkeypatch.setattr(stt, "get_settings", lambda: fast)
    with pytest.raises(stt.SttError, match="timed out"):
        run(stt.transcribe(b"a", "a.m4a"))


def test_transcribe_without_key_is_stt_error():
    with pytest.raises(stt.SttError):
        run(stt.transcribe(b"a", "a.m4a"))


# --- stt: live --------------------------------------------------------------

def test_mock_live_transcriber_emits_partial_then_final():
    async def main():
        t = stt.MockLiveTranscriber(text="Which size hex key", utterance_s=0.1)
        await t.start()
        out = []

        async def reader():
            async for r in t.results():
                out.append(r)

        task = asyncio.create_task(reader())
        for _ in range(2):
            await t.send(b"\x01\x00" * 800)      # 0.05 s of 16 kHz PCM16 each = one 0.1 s utterance
        for _ in range(5):
            await t.send(b"\x00" * 1600)         # silence is ignored: no extra utterance
        await asyncio.sleep(0.01)
        await t.close()
        await task
        return out

    out = run(main())
    assert [r.kind for r in out] == ["partial", "final"]
    assert out[1].text == "Which size hex key" and out[1].duration_s > 0


def test_make_live_transcriber_picks_mock_without_key_or_with_mocks(monkeypatch):
    assert isinstance(stt.make_live_transcriber(), stt.MockLiveTranscriber)
    set_env(monkeypatch, ELEVENLABS_API_KEY="k", USE_MOCKS="0")
    assert isinstance(stt.make_live_transcriber(), stt.ElevenLabsLiveTranscriber)
    set_env(monkeypatch, USE_MOCKS="1")
    assert isinstance(stt.make_live_transcriber(), stt.MockLiveTranscriber)


def test_elevenlabs_live_callbacks_feed_the_results_stream():
    async def main():
        t = stt.ElevenLabsLiveTranscriber()
        t._bytes_since_final = 16000 * 2 * 3                      # 3 s of audio before the commit
        t._on_partial({"text": "which size"})
        t._on_partial({"text": "   "})                           # ignored
        t._on_final({"text": " Which size key? "})
        t._on_error({"error": "queue overflow"})
        await t.close()
        return [r async for r in t.results()]

    partial, final, err = run(main())
    assert (partial.kind, partial.text) == ("partial", "which size")
    assert (final.kind, final.text, round(final.duration_s)) == ("final", "Which size key?", 3)
    assert err.kind == "error" and "queue overflow" in err.message


def test_elevenlabs_live_send_encodes_pcm_and_counts_bytes():
    sent = []

    class Conn:
        async def send(self, data):
            sent.append(data)

        async def close(self):
            pass

    async def main():
        t = stt.ElevenLabsLiveTranscriber()
        t._conn = Conn()
        await t.send(b"\x01\x02\x03\x04")
        await t.send(b"")                                         # empty chunks are skipped
        await t.close()

    run(main())
    assert sent == [{"audio_base_64": "AQIDBA=="}]


def test_live_reconnects_once_then_reports_error(monkeypatch):
    attempts = {"n": 0}

    async def main():
        t = stt.ElevenLabsLiveTranscriber()

        async def failing_connect():
            attempts["n"] += 1
            raise stt.SttError("nope")

        t._connect = failing_connect                              # type: ignore[method-assign]
        await t._reconnect()                                      # first drop: tries once, reports failure
        await t._reconnect()                                      # second drop: gives up straight away
        await t.close()
        return [r async for r in t.results()]

    out = run(main())
    assert attempts["n"] == 1
    assert [r.kind for r in out] == ["error", "error"]


# --- live round trips (need real keys) --------------------------------------

@pytest.mark.live
def test_realtime_stt_hears_streamed_speech():
    if not (os.getenv("ELEVENLABS_API_KEY") and os.getenv("ELEVENLABS_VOICE_ID")):
        pytest.skip("needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID")
    from backend.config import get_settings

    async def main():
        s = get_settings()
        pcm = b"".join([c async for c in tts._get_client().text_to_speech.stream(
            s.elevenlabs_voice_id, text="Which size hex key do I need for the pad bolt?",
            model_id=s.eleven_tts_model, output_format="pcm_16000")])
        t = stt.ElevenLabsLiveTranscriber()
        await t.start(["hex key"])

        async def feed():
            for i in range(0, len(pcm), 3200):                   # 100 ms chunks, in real time
                await t.send(pcm[i:i + 3200])
                await asyncio.sleep(0.1)
            for _ in range(25):                                  # trailing silence ends the utterance
                await t.send(b"\x00" * 3200)
                await asyncio.sleep(0.1)

        feeder = asyncio.create_task(feed())

        async def first_final():
            async for r in t.results():
                assert r.kind != "error", r.message
                if r.kind == "final":
                    return r

        final = await asyncio.wait_for(first_final(), timeout=30)
        feeder.cancel()
        await t.close()
        assert "hex" in final.text.lower()
        assert final.duration_s > 0

    run(main())



@pytest.mark.live
def test_tts_then_stt_round_trip():
    if not (os.getenv("ELEVENLABS_API_KEY") and os.getenv("ELEVENLABS_VOICE_ID")):
        pytest.skip("needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID")

    async def main():
        audio = b"".join([c async for c in tts.stream("Which size hex key do I need?")])
        assert len(audio) > 1000
        t = await stt.transcribe(audio, "round_trip.mp3")
        assert "hex" in t.text.lower()

    run(main())


# --- progressive serving -----------------------------------------------------

def test_progressive_yields_the_first_audio_before_generation_finishes(keys, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(tts, "_open_stream", fake_stream([b"a", b"b", b"c", b"d"], calls, delay=0.1))

    async def main():
        name = tts.start("a longer reply")
        stream = await tts.progressive(name)
        assert stream is not None
        t0 = asyncio.get_running_loop().time()
        it = stream.__aiter__()
        first = await it.__anext__()
        assert asyncio.get_running_loop().time() - t0 < 0.3, "first chunk should not wait for the whole reply"
        assert first == b"a"
        assert not (tts.cache_dir() / name).exists(), "generation is still running"
        rest = [c async for c in it]
        assert first + b"".join(rest) == b"abcd"
        await asyncio.sleep(0.05)
        assert (tts.cache_dir() / name).read_bytes() == b"abcd"
        assert await tts.progressive(name) is None, "finished files are served from disk"

    run(main())
    assert calls == ["a longer reply"]


def test_two_listeners_both_get_every_chunk(keys, monkeypatch):
    monkeypatch.setattr(tts, "_open_stream", fake_stream([b"1", b"2", b"3"], [], delay=0.05))

    async def main():
        name = tts.start("shared")
        s1, s2 = await tts.progressive(name), await tts.progressive(name)

        async def drain(s):
            return b"".join([c async for c in s])
        return await asyncio.gather(drain(s1), drain(s2))

    assert run(main()) == [b"123", b"123"]


def test_progressive_is_none_when_generation_fails_before_any_audio(keys, monkeypatch):
    async def boom(text):
        raise RuntimeError("elevenlabs down")
    monkeypatch.setattr(tts, "_open_stream", boom)

    async def main():
        name = tts.start("hello")
        return await tts.progressive(name)

    assert run(main()) is None


def test_progressive_ends_cleanly_if_generation_dies_midway(keys, monkeypatch):
    async def partial(text):
        async def gen():
            yield b"abc"
            raise RuntimeError("connection reset")
        return gen()
    monkeypatch.setattr(tts, "_open_stream", partial)

    async def main():
        name = tts.start("cut short")
        stream = await tts.progressive(name)
        got = [c async for c in stream]
        await asyncio.sleep(0.05)
        return got, (tts.cache_dir() / name).exists()

    got, cached = run(main())
    assert got == [b"abc"] and cached is False, "a truncated reply must not be cached"


def test_progressive_rejects_unknown_and_unsafe_names(keys):
    async def main():
        assert await tts.progressive("0000000000000000.mp3") is None
        assert await tts.progressive("../x.mp3") is None
        assert await tts.progressive("x.wav") is None

    run(main())
