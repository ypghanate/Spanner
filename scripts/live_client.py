"""Stand-in for the phone: stream speech into WS /session/{id}/live and time the replies.

    uv run python scripts/live_client.py                       # 1.5 s of silence (mock server)
    uv run python scripts/live_client.py --wav fixtures/audio/question.wav
    uv run python scripts/live_client.py --wav q.wav --barge-in-ms 300

Streams 16 kHz mono PCM16 in real time (100 ms chunks), then trailing silence so the server's
voice-activity detection ends the utterance. Prints every message and checks:
  * speech end -> first audio byte (or the response, when the server has no TTS) is under 3 s
  * with --barge-in-ms: tts_end arrives within 200 ms of the barge_in
"""
from __future__ import annotations

import argparse
import asyncio
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


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--wav", type=Path, help="16 kHz mono PCM16 WAV to stream (default: silence)")
    ap.add_argument("--silence-s", type=float, default=1.5, help="length of generated silence when no --wav")
    ap.add_argument("--tail-s", type=float, default=1.2, help="trailing silence so the utterance is committed")
    ap.add_argument("--barge-in-ms", type=int, default=None, help="send barge_in this long after the first audio byte")
    ap.add_argument("--wait-s", type=float, default=12.0, help="give up waiting for the reply after this long")
    a = ap.parse_args()

    try:
        import websockets
    except ImportError:
        print("needs the 'websockets' package (installed with uvicorn[standard])", file=sys.stderr)
        return 2

    # without --wav, send a faint non-zero signal: a mock server counts all-zero audio as silence
    speech = read_pcm(a.wav) if a.wav else b"\x01\x00" * int(a.silence_s * BYTES_PER_S / 2)
    tail = b"\x00" * int(a.tail_s * BYTES_PER_S)

    async with httpx.AsyncClient(base_url=a.url, timeout=10) as http:
        sid = (await http.post("/session", json={"device": "live-client"})).json()["session_id"]
    ws_url = a.url.replace("http", "ws", 1) + f"/session/{sid}/live"
    print(f"session {sid}\n{ws_url}")

    marks: dict[str, float] = {}
    audio_bytes = 0
    problems: list[str] = []

    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "keyterms": []}))

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
                    break    # server has no TTS: nothing more is coming
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
            kind = data["type"]
            marks.setdefault(kind, now)
            shown = data.get("text") or data.get("message") or (data.get("response", {}) or {}).get("say", "")
            print(f"  <- {kind:<9} {shown}")
            if kind == "tts_end":
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
            problems.append("barge-in test needs a server that streams TTS (set ElevenLabs keys, USE_MOCKS=0)")
    for p in problems:
        print(f"PROBLEM: {p}")
    print("OK" if not problems else "FAILED")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
