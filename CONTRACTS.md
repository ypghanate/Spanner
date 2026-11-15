# CONTRACTS

`backend/contracts.py` is the single source of truth. This file explains it. If the two disagree, the code wins and this file is wrong: tell P3.

**Change rule:** contract changes go through P3, are announced in chat, and happen at checkpoints (14:00, 18:00, 22:00, 03:00). After a change P1 runs `uv run python scripts/gen_types.py` to refresh the TypeScript types.

## Conventions

- Boxes are **normalised 0-1**, measured from the **top-left** of the frame that was sent, after rotation was fixed (`Box.x, y, w, h`).
- `hud` and `scene3d` are command lists. **Empty means "no change", not "clear".** To clear the HUD send `{"op": "clear"}`.
- `BrainResponse.say` is at most two sentences. **Empty `say` means say nothing.**
- A `BrainResponse` with only `media` filled is a media-only event (it carries the current `phase`).
- `RepairStep.index` is the number the user sees (1-based). `BrainResponse.current_step` is that same number, not a list position.
- Every field the server sends is always present (`null` when empty); TypeScript types mark them required.

## Phase and intent

`Phase` = `identifying` · `diagnosing` · `researching` · `guiding` · `verifying` · `done` · `safety_stop`
`UserIntent` = `start` · `question` · `step_done` · `need_help` · `stop` · `none`

State machine: `identifying -> diagnosing -> researching -> guiding (step 1..N) -> verifying -> done`; any phase -> `safety_stop` (mains / gas); "start over" -> `identifying`.

## Shapes

| Shape | Fields | From -> to |
|---|---|---|
| `Box` | x, y, w, h | |
| `Detection` | label, score, box, track_id | P2 -> app |
| `FrameQuality` | blurry, too_dark, is_keyframe, sharpness, brightness | P2 -> P3 |
| `SceneObservation` | frame_id, object, visible_parts[], state, anomalies[], hands_visible, tools_visible[], confidence | P2 -> P3 |
| `Transcript` | text, language, duration_s | P3 |
| `SituationReport` | summary, user_intent, user_utterance, camera_sees, change_since_last, suspected_fault, confidence | P3 -> P4 |
| `SourceRef` | title, url, kind (ifixit / youtube / web) | P4 |
| `SceneCommand` | op (highlight / pulse / explode / rotate_to / arrow / show_step / reset), part, group, view, amount, direction | P4 -> app |
| `HudCommand` | op (label / arrow / circle / clear), target_label, box, text, color (info / ok / warn / danger) | P4 -> app |
| `RepairStep` | index, instruction, parts[], expected_visual_state, common_mistakes[], scene3d[], sources[], illustration_prompt, illustration_url, clip_url | P4 |
| `RepairPlan` | object, fault, difficulty, time_min, tools_needed[], safety_notes[], steps[], sources[] | P4 -> app |
| `MediaUpdate` | kind (illustration / scan3d / clip), step_index, url | P2 -> app |
| `Alert` | severity (warn / danger), message | P4 -> app |
| `BrainResponse` | phase, say, hud[], scene3d[], current_step, step_status (in_progress / done / mistake), alert, plan, media[], **tts_url**, **parts_card** | P4 -> app |
| `FrameResponse` | frame_id, quality, detections[], events[] (BrainResponses) | server -> app |
| `AudioResponse` | transcript, response, tts_url | server -> app |
| `SessionSummary` | session_id, object, fault, steps_total, steps_done, duration_s, sources[] | server -> app |

### Additions beyond the original plan (announce in chat)

- **`BrainResponse.parts_card: dict | null`** (new). The Linkup replacement-part card, exactly as `backend.parts_agent.card_view()` builds it. The orchestrator sets it when the parts job finishes and pushes it as an `event`.
- **One WebSocket for frames, voice and pushed events** (below), replacing push-to-talk and the 'no WebSocket' rule. Frames, `POST /audio` and the other HTTP routes remain as fallbacks.

- **`BrainResponse.tts_url`** (new). Slow-loop replies travel inside `FrameResponse.events`, which has no other place for audio. P4 leaves it `null`; P3's orchestrator fills it with `/tts/<file>.mp3`.
- **`step_status`** values `in_progress / done / mistake` are P3's proposal; P4 may change them at a checkpoint.
- **Live voice over a WebSocket** replaces push-to-talk as the main voice path (below). `POST /audio` remains as a fallback.

## HTTP API

| Route | In | Out |
|---|---|---|
| `POST /session` | JSON `{"device": "iphone"}` (body optional) | `{"session_id"}` |
| `POST /session/{id}/frame` | multipart: `frame` (JPEG file), optional `meta` (JSON string, e.g. `{"ts": 1731600000.5}`) | `FrameResponse` |
| `POST /session/{id}/audio` | multipart: `audio` (m4a file), optional `meta` (JSON string, e.g. `{"duration_s": 2.4}`) | `AudioResponse` |
| `WS /session/{id}/live` | see below | see below |
| `GET /tts/{file}.mp3` | | `audio/mpeg`; 404 if generation failed (speak the text with expo-speech) |
| `GET /media/{file}` | | png / glb / mp4 from the media cache; 404 for anything outside it |
| `POST /session/{id}/end` | | `SessionSummary` |
| `GET /session/{id}/state` | | session JSON (debugging) |
| `GET /debug` | | live pipeline viewer (HTML) |
| `GET /health` | | `{"status": "ok", "stages": {...real or mock}, ...}` |

- Unknown session -> `404`. Frames over 8 MB and audio over 25 MB -> `413`. Empty frame -> `400`.
- **HTTP fallback only:** with no live socket, slow-loop results wait in a queue and come back on the next frame upload (`FrameResponse.events`), so a frame reply can carry zero, one or several `BrainResponse`s. Play them in order. On the socket they are pushed instead.
- For `/audio`, `AudioResponse.response.tts_url` is `null` and `AudioResponse.tts_url` carries the voice. If it is `null`, speak `response.say` locally.
- HTTP frame handling: one JPEG per second, only when the previous upload has finished.

## Live socket: `WS /session/{id}/live`

**This is the main channel.** One socket per session carries the camera frames, the voice, and every brain result, pushed to the phone the moment it is ready (no waiting for the next upload). The HTTP routes above still work as a fallback and for debugging.

- All messages are JSON text. The only binary frames are two optional cases: raw PCM microphone audio from a dev build (phone -> server) and streamed MP3 in `stream` TTS mode (server -> phone).
- Unknown session: the handshake is refused (close code 4404). One socket per session: a new one closes the old with code 4000.
- Brain results queued while no socket was connected are delivered as `event` messages when one connects.
- Keep-alive uses WebSocket protocol pings (uvicorn's default). There are no application-level ping messages.

### Phone -> server

| Message | Meaning |
|---|---|
| `{"type":"start","keyterms":["brake pad"],"tts":"url"}` | Optional settings, may be sent first or again later. `tts` is `"url"` (default: replies carry `tts_url`, the phone plays it) or `"stream"` (the server streams MP3 chunks on this socket). `keyterms` adds speech-recognition vocabulary (the current step's parts are added automatically). |
| `{"type":"frame","jpeg_b64":"...","ts":1731600000.5}` | One camera frame, about one per second. Base64 JPEG exactly as `expo-camera` can output it. |
| `{"type":"utterance","audio_b64":"...","format":"m4a","duration_s":2.1}` | **Voice, Expo Go path (main).** One finished clip. The phone records, detects the end of speech with the recorder's metering (about 0.7 s of silence) and sends the whole clip. |
| `{"type":"audio","pcm_b64":"..."}` or a binary frame | **Voice, dev-build path.** Streaming microphone audio: 16 kHz, mono, 16-bit little-endian PCM, about 100 ms (3200 bytes) per chunk. Gives live captions. |
| `{"type":"playback","state":"start"}` / `"end"` | The phone started / finished playing a spoken reply (it mutes its mic meanwhile). |
| `{"type":"barge_in"}` | The user talked over the reply: stop playing now. |
| `{"type":"stop"}` | Close. |

### Server -> phone

| Message | Meaning |
|---|---|
| `{"type":"frame_result","result": FrameResponse}` | Fast-loop reply to a `frame`: quality and detections for the HUD boxes. `result.events` is always empty on the socket. |
| `{"type":"event","response": BrainResponse}` | **A brain result pushed when ready:** the slow-loop reply, an alert, media, the plan, the parts card. Apply it like any `BrainResponse`. A non-empty `say` is meant to be spoken (see below). |
| `{"type":"partial","text"}` / `{"type":"final","text"}` | Live caption (PCM path only) / the utterance was understood, the brain is now running. `final` is sent for clips too, with the recognised text. |
| `{"type":"response","response": BrainResponse,"speak": true}` | The answer to what the user said. Apply `hud`, `scene3d`, `alert`, `plan` as usual. `speak=false` means the user started talking again while the brain worked: show the reply, **don't say it**. |
| `{"type":"tts_start"}`, binary frames, `{"type":"tts_end"}` | `stream` mode only: the spoken reply as MP3 chunks (`mp3_44100_64`). |
| `{"type":"error","code","message"}` | `bad_message` (unparseable or invalid message), `bad_frame` (frame not valid base64 or empty/oversized), `stt_unavailable` / `stt_error` (the *streaming* recogniser failed: switch to clips or `POST /audio`), `tts_error`. The socket stays open. A *clip* that can't be transcribed is not an error message: the phone gets an ordinary `response` saying "Sorry, I didn't catch that." |

### What the phone does (P1 checklist)

1. **Frames:** send a `frame` about once a second. The server keeps one in flight and only the **newest** waiting frame, so a late phone never builds a backlog. Draw boxes from each `frame_result`.
2. **Voice:** record each utterance as m4a, stop when the recorder's metering shows about 0.7 s of silence, send one `utterance`. (Expo Go cannot stream raw mic audio; an iOS dev build can, then use `audio` and get live `partial` captions and about 0.7 s lower latency.)
3. **Speaking:** for a `response` with `speak=true`, and for an `event` with a non-empty `say`: play `response.tts_url` if it is set (`GET` it; it streams while it is still being generated). **If `tts_url` is `null`, speak `say` yourself with expo-speech.** Play replies in order and never overlap two. In `stream` mode, play the chunks between `tts_start` and `tts_end` instead.
4. **Mute the mic while you play audio**, and send `playback` `start` / `end` around it. The server drops mic audio and clips that arrive while playback is on and for 0.7 s after `end`, and also discards transcripts that mostly repeat what it just said, but muting on the phone is the real fix for echo.
5. **Barge-in:** if the user speaks over a reply, stop playback, drop buffered audio and send `barge_in`.
6. **Parts card:** an `event` whose `response.parts_card` is set carries the Linkup replacement card (shape: `backend/parts_agent.py` `card_view`). Render it beside the 3D view.
7. If the socket drops, reconnect to the same URL: queued results are delivered on connect. HTTP `POST /frame` and `POST /audio` keep working meanwhile.

**Unverified on a device:** progressive playback of the chunked MP3 from `GET /tts/{file}.mp3`. If a player stalls on it, wait for the file to finish (about 1-2 s later it is a normal complete file with the same URL).

## Stages P2 and P4 must expose

`backend/main.py` imports these by name and falls back to `backend/mocks.py` if a module is missing (or `USE_MOCKS=1`). Functions may be sync (run in a thread) or async. Each is called with a timeout; raise or return slowly and the orchestrator logs it and carries on.

```python
backend.vision.preprocess.preprocess(jpeg: bytes, session_id: str) -> tuple[bytes, FrameQuality]
    # rotated, 768 px JPEG out. Flag keyframes. Fast loop budget: ~50 ms. Timeout 3 s.
backend.vision.detect.detect(jpeg: bytes, session_id: str) -> list[Detection]
    # REAL since 14 Nov: backend/vision/detect.py (YOLO + ByteTrack, the same call as Yash's compVisionModel.py).
    # Normalised boxes, smoothed across frames. Not called on blurry / too-dark frames. Timeout 2 s.
    # warm() loads the model at startup. Stock yolov8n only knows COCO classes ("bicycle", not "brake pad"):
    # set YOLO_WEIGHTS to a part-level model. ~180 ms per frame on a laptop CPU.
backend.vision.understand.understand(jpeg: bytes, frame_id: str, detections: list[Detection],
                                     expected_state: str | None) -> SceneObservation
    # keyframes only. expected_state = current step's expected_visual_state. Timeout 6 s.
backend.brain.safety.check(report: SituationReport, session: Session) -> BrainResponse | None
    # mains / gas / high voltage. Return a response to short-circuit Pass 2. Timeout 2 s.
backend.brain.pass2.respond(report: SituationReport, session: Session) -> BrainResponse
    # the Repair Brain. Runs the state machine. Timeout 10 s.
backend.knowledge.track_step_async(step, observation, said, session_id, repair_id) -> dict
    # Alberto's step tracker. Runs alongside Pass 1 while guiding (3 s budget). Result lands in
    # session.last_step_check before Pass 2 runs: {"step_status": "in_progress"|"done"|"mistake"|None, "mistake": str, ...}
backend.knowledge.get_parts_card_async(job) -> dict
    # Polled every 2 s (up to 3 min) for the job id in session.brain_state["parts_job"].
backend.store.get_store() -> Store
    # Store.write(table: str, row: dict) -> None   (non-blocking; batch internally)
    # Store.read(table: str, limit: int = 100) -> list[dict]
    # Store.flush() -> None
```

Pass 2 may use `session.spawn(name, coro, timeout)` for background work (research, fal) and `session.queue_media(MediaUpdate)` / `session.queue_event(BrainResponse)` to push results to the phone later. Use `session.brain_state` (a dict) for private state.

## Session (public surface)

```python
session.id, session.device, session.phase, session.plan, session.current_step_index  # 0-based
session.current_step() -> RepairStep | None
session.observations          # deque, newest last, max 3
session.last_report           # SituationReport | None
session.last_response         # BrainResponse | None
session.brain_state: dict     # P4's scratch space. Two keys the orchestrator reads (P4 sets them):
                              #   "parts_job"  - job id from handle_parts_tool(...)["job"]; the card is pushed when ready
                              #   "repair_id"  - the repair's id, passed to the step tracker's learning loop
session.last_step_check       # dict | None. The step tracker's latest verdict (set before Pass 2 runs, None otherwise)
session.perception            # backend.perception.Perception | None: what Claude is shown THIS turn (see below). Set before
                              #   Pass 1 runs, cleared at the start of every brain run. Pass 2 reads it to answer questions.
session.recent_frames         # deque of the last 12 usable frames: FrameSnap(frame_id, ts, jpeg, detections)
session.last_observation_at   # when the newest SceneObservation was made (server clock)
session.apply(response)       # P3 calls this after Pass 2; updates phase, plan, step
session.queue_event(r)        # delivered on the next frame reply
session.queue_media(m)        # merged into the next event, or sent as a media-only event
session.spawn(name, coro, timeout=None)   # tracked background task; errors are logged, never raised
```

## What Claude sees on a spoken turn

The camera, the YOLO detector and the person's speech reach Claude together (`backend/perception.py`). When the user speaks, the orchestrator builds a `Perception` from the session's ring of recent frames and gives it to Pass 1 and, for questions, to Pass 2:

| Piece | Rule |
|---|---|
| Latest frame | the newest sharp frame, at most 10 s old (blurry and dark frames never enter the ring) |
| Earlier frame | the frame nearest to when the user started speaking, only if it is at least 1 s older than the latest **and the scene changed** (thumbnail difference of at least 3; a static scene gets one picture, not two) |
| Close-up | a crop of the main detected object (largest non-person, score at least 0.4) when it fills under 40% of the frame and the crop is at least 160 px a side |
| YOLO tracks | the detections on the latest frame: label, track id, confidence, position |
| Text observation age | `seen N s ago` on the newest observation when it is at least 1.5 s old |

- **Pass 1** gets these as images (before the text) plus prompt lines `IMAGES ATTACHED` and `DETECTOR (YOLO, latest frame)`. Stage signature: `backend.brain.pass1.run(inp, session_id, store=None, images=None)`. The fine-tuned Pass 1 backend is text-only (it still sees the detector line).
- **Which model (cost):** a spoken turn that carries pictures uses `PASS1_VISION_MODEL` (default `claude-sonnet-5-5`, low effort, thinking off via `between_tools`); every other Pass 1 call (keyframe-only turns, the dataset teacher) stays on Haiku. Set `PASS1_VISION_MODEL=` (empty) to use Haiku for everything. Why: on real Royal Kludge photos Haiku (low effort) often named the wrong removed key and Sonnet got it right every time. Measured with a real key: a warm spoken turn with two pictures costs about $0.0056 for Pass 1 (about $0.011 for the first call of a 5-minute window, which writes the prompt cache); a static scene (one picture) about $0.0048; a Haiku keyframe turn about $0.0004. The fixed system prompt is cached. Latency about 2.5 s with pictures against about 1.7 s on Haiku.
- **Pass 2** reads `session.perception` (`.images`, `.image_notes`, `.detections`, `.observation_age_s`, `.crop_label`). `backend/brain/pass2.py` `_answer` already uses it to answer questions.
- A keyframe-only turn (nobody spoke) has the detector list and the age but no images: that keyframe was just described by `understand`.
- The step tracker and `understand` still work from the newest keyframe's text; they are not given the live frame.
- **Phone:** keep sending frames while the user is recording. The frames from while they spoke are what Claude is shown.
- To see it: `/debug` shows the pictures, the YOLO count and the observation age of the last spoken turn (`GET /debug/perception/{i}.jpg`).

## Store rows (written by P3's orchestrator)

`sessions` rows are written at session start and again at the end: **treat `write("sessions", ...)` as an upsert on `session_id`.** Other tables are append-only.

| Table | Columns |
|---|---|
| `sessions` | session_id, started_at, ended_at, device, object, fault, status, steps_total, steps_done |
| `frames` | session_id, frame_id, ts, is_keyframe, sharpness, brightness, image_path |
| `detections` | frame_id, label, score, x, y, w, h |
| `observations` | frame_id, ts, json |
| `transcripts` | session_id, ts, text, duration_s, source (`live` on the socket; absent on `/audio`) |
| `situation_reports` | session_id, ts, json |
| `brain_responses` | session_id, ts, phase, current_step, step_status, say, alert_json |
| `pass1_calls` | session_id, ts, backend (`claude` / `adaption` / `adaption_fallback` / `heuristic`), input_json, output_json, latency_ms, valid_json |

`media` and `research_cache` are P4's. Keyframes are saved to `backend/media_cache/keyframes/{frame_id}.jpg` (git-ignored); `image_path` is that path for keyframes and `""` otherwise.
