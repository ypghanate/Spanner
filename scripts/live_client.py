"""Stand-in for the phone: talk to WS /session/{id}/live and time what comes back.

    uv run python scripts/live_client.py --frame fixtures/frames/placeholder.jpg     # camera frames
    uv run python scripts/live_client.py --clip fixtures/audio/question.m4a          # one voice clip (Expo Go path)
    uv run python scripts/live_client.py --wav fixtures/audio/question.wav           # streamed PCM (dev-build path)
    uv run python scripts/live_client.py --wav q.wav --tts-mode stream --barge-in-ms 300

Modes (combine --frame with --clip; --wav/--silence-s is the streaming-PCM mode):
  --frame JPG    sends --frames frames, one per second, as `frame` messages. Checks every frame gets a
                 `frame_result`, and shows brain results pushed as `event` messages with no further input.
  --clip FILE    sends the file as one `utterance` message. Checks `final` then `response`; in url mode
                 prints the reply's tts_url.
  --wav WAV      streams 16 kHz mono PCM16 in real time (100 ms chunks) plus trailing silence, then checks
                 the reply arrives within 3 s of the end of speech. With --barge-in-ms, sends barge_in that
                 long after the first audio byte and checks tts_end follows within 200 ms (stream mode).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
import wave
from pathlib import Path

import httpx

CHUNK_S = 0.1
BYTES_PER_S = 16000 * 2


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise SystemExit(f"{path} must be 16 kHz mono 16-bit PCM WAV")
        return w.readframes(w.getnframes())


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def show(msg: dict) -> str:
    kind = msg["type"]
    if kind in ("partial", "final"):
        return msg["text"]
    if kind == "response":
        r = msg["response"]
        return f'speak={msg["speak"]} say="{r["say"]}" tts_url={r["tts_url"]} phase={r["phase"]}'
    if kind == "event":
        r = msg["response"]
        extras = [k for k in ("alert", "plan", "parts_card") if r.get(k)] + (["media"] if r.get("media") else [])
        return f'phase={r["phase"]} say="{r["say"]}" tts_url={r["tts_url"]} {" ".join(extras)}'
    if kind == "frame_result":
        res = msg["result"]
        return (f'{res["frame_id"]} keyframe={res["quality"]["is_keyframe"]} '
                f'boxes={[d["label"] for d in res["detections"]]}')
    if kind == "error":
        return f'{msg["code"]}: {msg["message"]}'
    return ""


async def open_session(url: str) -> tuple[str, str]:
    async with httpx.AsyncClient(base_url=url, timeout=10) as http:
        sid = (await http.post("/session", json={"device": "live-client"})).json()["session_id"]
    return sid, url.replace("http", "ws", 1) + f"/session/{sid}/live"


async def json_modes(a: argparse.Namespace) -> int:
    """--frame and/or --clip: JSON messages only."""
    import websockets

    sid, ws_url = await open_session(a.url)
    print(f"session {sid}\n{ws_url}")
    problems: list[str] = []
    counts: dict[str, int] = {}
    t0 = time.perf_counter()
    marks: dict[str, float] = {}

    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "tts": a.tts_mode}))

        async def sender():
            if a.frame:
                data = b64(a.frame)
                for i in range(a.frames):
                    await ws.send(json.dumps({"type": "frame", "jpeg_b64": data, "ts": time.time()}))
                    marks.setdefault("first_frame_sent", time.perf_counter())
                    if i < a.frames - 1:
                        await asyncio.sleep(1.0)
            if a.clip:
                await ws.send(json.dumps({"type": "utterance", "audio_b64": b64(a.clip),
                                          "format": a.clip.suffix.lstrip(".") or "m4a"}))
                marks["clip_sent"] = time.perf_counter()

        send_task = asyncio.create_task(sender())
        quiet_since = time.perf_counter()
        deadline = time.perf_counter() + a.wait_s + (a.frames if a.frame else 0)
        while time.perf_counter() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.25)
            except asyncio.TimeoutError:
                if send_task.done() and time.perf_counter() - quiet_since > a.settle_s:
                    break
                continue
            quiet_since = time.perf_counter()
            if isinstance(raw, bytes):
                counts["audio_bytes"] = counts.get("audio_bytes", 0) + len(raw)
                continue
            msg = json.loads(raw)
            counts[msg["type"]] = counts.get(msg["type"], 0) + 1
            marks.setdefault(msg["type"], time.perf_counter())
            print(f"  +{time.perf_counter() - t0:5.2f}s <- {msg['type']:<12} {show(msg)}")
        send_task.cancel()
        await ws.send(json.dumps({"type": "stop"}))

    if a.frame and counts.get("frame_result", 0) < a.frames:
        problems.append(f"sent {a.frames} frames, got {counts.get('frame_result', 0)} frame_result")
    if a.clip:
        for need in ("final", "response"):
            if not counts.get(need):
                problems.append(f"voice clip never got a '{need}'")
        if "clip_sent" in marks and "response" in marks:
            print(f"\n  clip sent -> response: {(marks['response'] - marks['clip_sent']) * 1000:.0f} ms")
    if a.tts_mode == "stream" and a.clip and not counts.get("tts_start"):
        print("  (no tts_start: the server has no ElevenLabs key, so the phone would speak `say` itself)")
    print(f"\nmessages: {counts}")
    return finish(problems)


async def pcm_mode(a: argparse.Namespace) -> int:
    """--wav (or silence): streamed microphone audio, as a dev build would send it."""
    import websockets

    speech = read_pcm(a.wav) if a.wav else b"\x01\x00" * int(a.silence_s * BYTES_PER_S / 2)
    tail = b"\x00" * int(a.tail_s * BYTES_PER_S)
    sid, ws_url = await open_session(a.url)
    print(f"session {sid}\n{ws_url}")

    marks: dict[str, float] = {}
    audio_bytes = 0
    problems: list[str] = []

    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "keyterms": [], "tts": a.tts_mode}))

        async def sender():
            for payload in (speech, tail):
                for i in range(0, len(payload), int(CHUNK_S * BYTES_PER_S)):
                    await ws.send(payload[i:i + int(CHUNK_S * BYTES_PER_S)])
                    await asyncio.sleep(CHUNK_S)
                if payload is speech:
                    marks["speech_end"] = time.perf_counter()

        send_task = asyncio.create_task(sender())
        deadline = time.perf_counter() + a.wait_s + len(speech) / BYTES_PER_S
        barged = False
        while time.perf_counter() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.25)
            except asyncio.TimeoutError:
                if "tts_end" in marks:
                    break
                if a.barge_in_ms is not None and "first_audio" in marks and not barged \
                        and (time.perf_counter() - marks["first_audio"]) * 1000 >= a.barge_in_ms:
                    barged = True
                    marks["barge_in"] = time.perf_counter()
                    await ws.send(json.dumps({"type": "barge_in"}))
                elif "response" in marks and "tts_start" not in marks and time.perf_counter() - marks["response"] > 1.0:
                    break    # no streamed audio is coming (url mode, or the server has no TTS)
                continue
            now = time.perf_counter()
            if isinstance(msg, bytes):
                audio_bytes += len(msg)
                marks.setdefault("first_audio", now)
                if a.barge_in_ms is not None and not barged and (now - marks["first_audio"]) * 1000 >= a.barge_in_ms:
                    barged = True
                    marks["barge_in"] = now
                    await ws.send(json.dumps({"type": "barge_in"}))
                continue
            data = json.loads(msg)
            marks.setdefault(data["type"], now)
            print(f"  <- {data['type']:<9} {show(data)}")
            if data["type"] == "tts_end":
                break
        send_task.cancel()
        await ws.send(json.dumps({"type": "stop"}))

    print(f"\naudio received: {audio_bytes} bytes")
    end = marks.get("speech_end")
    if end is None:
        problems.append("never finished sending speech")
    for label, key in (("first transcript", "partial"), ("final transcript", "final"), ("response", "response"),
                       ("first audio byte", "first_audio")):
        if key in marks and end:
            delta = (marks[key] - end) * 1000
            when = f"{delta:7.0f} ms" if delta >= 0 else "arrived while speech was still streaming"
            print(f"  speech end -> {label:<17} {when}")
    reply_key = "first_audio" if "first_audio" in marks else "response"
    if reply_key not in marks:
        problems.append("no reply received")
    elif end and marks[reply_key] - end > 3.0:
        problems.append(f"reply took {marks[reply_key] - end:.1f} s after speech ended (target < 3 s)")
    if a.barge_in_ms is not None:
        if "tts_end" in marks and "barge_in" in marks:
            stop_ms = (marks["tts_end"] - marks["barge_in"]) * 1000
            print(f"  barge_in -> tts_end              {stop_ms:7.0f} ms")
            if stop_ms > 200:
                problems.append(f"audio stopped {stop_ms:.0f} ms after barge_in (target < 200 ms)")
        else:
            problems.append("barge-in test needs --tts-mode stream and a server with ElevenLabs keys (USE_MOCKS=0)")
    return finish(problems)


def finish(problems: list[str]) -> int:
    for p in problems:
        print(f"PROBLEM: {p}")
    print("OK" if not problems else "FAILED")
    return 1 if problems else 0


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--frame", type=Path, help="JPEG to send as camera frames")
    ap.add_argument("--frames", type=int, default=3, help="how many frames to send, one per second")
    ap.add_argument("--clip", type=Path, help="audio file (m4a/mp3/wav) to send as one voice clip")
    ap.add_argument("--wav", type=Path, help="16 kHz mono PCM16 WAV to stream (default: faint synthetic audio)")
    ap.add_argument("--silence-s", type=float, default=1.5, help="length of the synthetic audio when no --wav")
    ap.add_argument("--tail-s", type=float, default=1.2, help="trailing silence so the utterance is committed")
    ap.add_argument("--tts-mode", choices=["url", "stream"], default="url",
                    help="url: replies carry a tts_url; stream: MP3 chunks come back on the socket")
    ap.add_argument("--barge-in-ms", type=int, default=None, help="send barge_in this long after the first audio byte")
    ap.add_argument("--wait-s", type=float, default=12.0, help="give up waiting for replies after this long")
    ap.add_argument("--settle-s", type=float, default=2.0, help="stop once nothing has arrived for this long")
    a = ap.parse_args()
    try:
        import websockets  # noqa: F401
    except ImportError:
        print("needs the 'websockets' package (installed with uvicorn[standard])", file=sys.stderr)
        return 2
    if a.frame or a.clip:
        return await json_modes(a)
    return await pcm_mode(a)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
