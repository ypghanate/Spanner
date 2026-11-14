"""Run every stage once on a frame and an audio clip, print each output and its time.

    uv run python scripts/run_pipeline.py fixtures/frames/placeholder.jpg fixtures/audio/question.mp3
    uv run python scripts/run_pipeline.py <frame> <audio> --real          # USE_MOCKS=0
    uv run python scripts/run_pipeline.py --make-audio                    # synthesise the audio fixtures

Stages: preprocess -> detect -> understand -> STT -> Pass 1 -> safety -> Pass 2 -> TTS.
Works with mocks on or off (a stage whose module doesn't exist yet falls back to its mock).
Exits non-zero if any stage fails or returns something that doesn't match its contract.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BUDGETS = {"fast loop (preprocess + detect)": 0.5, "slow loop (understand + pass1 + safety + pass2)": 5.0,
           "voice (stt + pass1 + safety + pass2 + tts)": 5.0}
MAKE_TEXT = "Which size hex key do I need for the pad bolt?"


def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frame", nargs="?", type=Path, help="a JPEG frame")
    ap.add_argument("audio", nargs="?", type=Path, help="an audio clip (m4a/mp3/wav)")
    ap.add_argument("--real", action="store_true", help="USE_MOCKS=0: call real stages where they exist")
    ap.add_argument("--make-audio", action="store_true",
                    help="synthesise fixtures/audio/question.mp3 and question.wav with ElevenLabs, then exit")
    return ap.parse_args()


async def make_audio() -> int:
    from backend.config import get_settings
    from backend.voice import tts

    s = get_settings()
    if not tts.available():
        print("needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in .env", file=sys.stderr)
        return 2
    out = ROOT / "fixtures" / "audio"
    out.mkdir(parents=True, exist_ok=True)
    mp3 = b"".join([c async for c in tts.stream(MAKE_TEXT)])
    (out / "question.mp3").write_bytes(mp3)
    client = tts._get_client()
    pcm = b"".join([c async for c in client.text_to_speech.stream(
        s.elevenlabs_voice_id, text=MAKE_TEXT, model_id=s.eleven_tts_model, output_format="pcm_16000")])
    with wave.open(str(out / "question.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    print(f"wrote {out / 'question.mp3'} ({len(mp3)} bytes) and {out / 'question.wav'} ({len(pcm)} bytes)")
    return 0


def _show(title: str, obj) -> None:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    elif isinstance(obj, list):
        obj = [o.model_dump() if hasattr(o, "model_dump") else o for o in obj]
    text = json.dumps(obj, indent=2, default=str)
    if len(text) > 900:
        text = text[:900] + "\n  ... (truncated)"
    print(f"\n== {title}\n{text}")


async def main() -> int:
    a = _args()
    if a.real:
        os.environ["USE_MOCKS"] = "0"
    if a.make_audio:
        return await make_audio()
    if not a.frame:
        print("usage: run_pipeline.py <frame.jpg> <audio>   (or --make-audio)", file=sys.stderr)
        return 2

    from backend import mocks
    from backend.brain import pass1 as pass1_mod
    from backend.config import get_settings
    from backend.contracts import BrainResponse, Detection, FrameQuality, SceneObservation, SituationReport, Transcript
    from backend.main import call_stage
    from backend.session import Session
    from backend.voice import tts

    s = get_settings()
    stages = mocks.load_stages()
    print(f"stages: {stages.describe()}   USE_MOCKS={s.use_mocks}  PASS1_BACKEND={s.pass1_backend}")

    if not a.frame.exists():
        print(f"frame not found: {a.frame}", file=sys.stderr)
        return 2
    jpeg = a.frame.read_bytes()
    if a.audio and a.audio.exists():
        audio, audio_name = a.audio.read_bytes(), a.audio.name
    elif s.use_mocks:
        audio, audio_name = b"mock-audio", (a.audio.name if a.audio else "question.m4a")
        print(f"(audio file not found; mocks are on, so using fixture speech for '{audio_name}')")
    else:
        print(f"audio not found: {a.audio}. Run with --make-audio first, or pass a recording.", file=sys.stderr)
        return 2

    session = Session(id="pipeline")
    times: dict[str, float] = {}
    failed: list[str] = []

    async def stage(name, fn, *args, expect=None, timeout=30.0, allow_none=False):
        t0 = time.perf_counter()
        try:
            out = await call_stage(fn, *args, timeout=timeout, session=session, name=name)
            ms = (time.perf_counter() - t0) * 1000
            times[name] = ms
            if out is None and allow_none:
                print(f"\n== {name}  ({ms:.0f} ms)\n  (no result)")
                return None
            if expect is not None:
                items = out if isinstance(out, list) else [out]
                for i in items:
                    if not isinstance(i, expect):
                        expect.model_validate(i)   # raises if it doesn't match the contract
            _show(f"{name}  ({ms:.0f} ms)", out)
            return out
        except Exception as e:  # noqa: BLE001
            times[name] = (time.perf_counter() - t0) * 1000
            failed.append(name)
            print(f"\n== {name}  FAILED: {type(e).__name__}: {e}")
            return None

    jpeg2, quality = (await stage("preprocess", stages.preprocess, jpeg, session.id)) or (jpeg, None)
    if quality is not None and not isinstance(quality, FrameQuality):
        quality = FrameQuality.model_validate(quality)
    detections = await stage("detect", stages.detect, jpeg2, session.id, expect=Detection) or []
    obs = await stage("understand", stages.understand, jpeg2, "pipeline-00001", detections, None,
                      expect=SceneObservation)
    if obs is not None:
        session.observations.append(obs)
    transcript = await stage("stt", stages.transcribe, audio, audio_name, [], None, expect=Transcript)
    inp = pass1_mod.build_input(session, transcript)
    report = await stage("pass1", stages.pass1, inp, session.id, stages.store, expect=SituationReport)
    response = None
    if report is not None:
        session.last_report = report
        response = await stage("safety", stages.safety, report, session, expect=BrainResponse, allow_none=True)
        if response is None:
            response = await stage("pass2", stages.pass2, report, session, expect=BrainResponse)
    if response is not None and response.say:
        t0 = time.perf_counter()
        if tts.available():
            name = tts.start(response.say)
            path = await tts.wait_for(name) if name else None
            times["tts"] = (time.perf_counter() - t0) * 1000
            if path is None:
                failed.append("tts")
                print("\n== tts  FAILED: no audio produced")
            else:
                print(f"\n== tts  ({times['tts']:.0f} ms)\n  {path} ({path.stat().st_size} bytes)")
        else:
            print("\n== tts\n  skipped (no ElevenLabs key); the app would speak the reply with expo-speech")

    # --- timing table -------------------------------------------------------
    print("\n== timings")
    for name, ms in times.items():
        print(f"  {name:<11} {ms:7.0f} ms")
    groups = {
        "fast loop (preprocess + detect)": ["preprocess", "detect"],
        "slow loop (understand + pass1 + safety + pass2)": ["understand", "pass1", "safety", "pass2"],
        "voice (stt + pass1 + safety + pass2 + tts)": ["stt", "pass1", "safety", "pass2", "tts"],
    }
    for label, names in groups.items():
        total = sum(times.get(n, 0.0) for n in names) / 1000
        budget = BUDGETS[label]
        flag = "ok" if total < budget else "OVER BUDGET"
        print(f"  {label:<50} {total:5.2f} s  (budget < {budget} s)  {flag}")
    if s.use_mocks:
        print("  (mock stages: these timings say nothing about the real pipeline)")

    if failed:
        print(f"\nFAILED stages: {', '.join(failed)}")
        return 1
    print("\nall stages produced valid output")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
