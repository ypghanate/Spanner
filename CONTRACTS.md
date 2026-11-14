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
| `BrainResponse` | phase, say, hud[], scene3d[], current_step, step_status (in_progress / done / mistake), alert, plan, media[], **tts_url** | P4 -> app |
| `FrameResponse` | frame_id, quality, detections[], events[] (BrainResponses) | server -> app |
| `AudioResponse` | transcript, response, tts_url | server -> app |
| `SessionSummary` | session_id, object, fault, steps_total, steps_done, duration_s, sources[] | server -> app |

### Additions beyond the original plan (announce in chat)

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
- Events from the slow loop come back **on the next frame upload** (`FrameResponse.events`), so a frame reply can carry zero, one or several `BrainResponse`s. Play them in order.
- For `/audio`, `AudioResponse.response.tts_url` is `null` and `AudioResponse.tts_url` carries the voice. If it is `null`, speak `response.say` locally.
- Frame handling: the app uploads one JPEG per second, only when the previous upload has finished.

## Live voice: `WS /session/{id}/live`

Unknown session -> the handshake is refused (close code 4404). One connection per session: a new one closes the old with code 4000.

**Phone -> server**

| Message | Meaning |
|---|---|
| binary frame | microphone audio: **16 kHz, mono, 16-bit little-endian PCM**. Any chunk size; about 100 ms (3200 bytes) is good. The first audio frame starts the speech stream if `start` wasn't sent. |
| `{"type":"start","keyterms":["brake pad"]}` | optional: open the stream with extra vocabulary (the server adds the current step's parts itself) |
| `{"type":"barge_in"}` | the phone heard the user talk over the reply: stop the speech now |
| `{"type":"stop"}` | close the stream |

**Server -> phone**

| Message | Meaning |
|---|---|
| `{"type":"partial","text"}` | live caption, may change |
| `{"type":"final","text"}` | utterance committed (about 0.7 s of silence); the brain is now running |
| `{"type":"response","response": BrainResponse}` | Pass 2 result: apply `hud`, `scene3d`, `alert`, `plan`, ... |
| `{"type":"tts_start"}` then binary frames then `{"type":"tts_end"}` | the spoken reply, **MP3** (`mp3_44100_64`) chunks. Concatenate and play. |
| `{"type":"error","code","message"}` | `stt_unavailable`, `stt_error`, `tts_error`, `bad_message`. On `stt_*` fall back to `POST /audio`. |

Rules the phone must follow:

- **If a `response` arrives with a non-empty `say` and no `tts_start` follows, speak `say` yourself (expo-speech).** That is what happens when the server has no ElevenLabs key.
- **Stop the mic from hearing the speaker** while audio plays: mute it, use echo cancellation, or recommend headphones. The server drops transcripts that mostly repeat the words it is currently speaking (and for 2 s after), but that is a safety net, not echo cancellation.
- Send `barge_in` when the phone detects the user speaking over playback; stop playing and drop buffered audio. The server also cancels its own speech when it hears a non-echo transcript.
- A reply is **not spoken** if the user started a new utterance while the brain was working; the `response` message still arrives.
- Keep-alive uses WebSocket protocol pings (uvicorn's default). No application-level ping messages exist.

Open item for P1: **confirm Expo Go can stream raw microphone PCM.** If it cannot, the fallback is short m4a chunks (about 2 s, with silence detection on the phone) sent to `POST /audio`.

## Stages P2 and P4 must expose

`backend/main.py` imports these by name and falls back to `backend/mocks.py` if a module is missing (or `USE_MOCKS=1`). Functions may be sync (run in a thread) or async. Each is called with a timeout; raise or return slowly and the orchestrator logs it and carries on.

```python
backend.vision.preprocess.preprocess(jpeg: bytes, session_id: str) -> tuple[bytes, FrameQuality]
    # rotated, 768 px JPEG out. Flag keyframes. Fast loop budget: ~50 ms. Timeout 3 s.
backend.vision.detect.detect(jpeg: bytes, session_id: str) -> list[Detection]
    # normalised boxes, smoothed across frames. Not called on blurry / too-dark frames. Timeout 2 s.
backend.vision.understand.understand(jpeg: bytes, frame_id: str, detections: list[Detection],
                                     expected_state: str | None) -> SceneObservation
    # keyframes only. expected_state = current step's expected_visual_state. Timeout 6 s.
backend.brain.safety.check(report: SituationReport, session: Session) -> BrainResponse | None
    # mains / gas / high voltage. Return a response to short-circuit Pass 2. Timeout 2 s.
backend.brain.pass2.respond(report: SituationReport, session: Session) -> BrainResponse
    # the Repair Brain. Runs the state machine. Timeout 10 s.
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
session.brain_state: dict     # P4's scratch space
session.apply(response)       # P3 calls this after Pass 2; updates phase, plan, step
session.queue_event(r)        # delivered on the next frame reply
session.queue_media(m)        # merged into the next event, or sent as a media-only event
session.spawn(name, coro, timeout=None)   # tracked background task; errors are logged, never raised
```

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
