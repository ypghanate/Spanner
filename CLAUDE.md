# Spanner

A live AI repair assistant for iPhone, built at the Imperial AI Hackathon (14-15 Nov 2026). Point the phone at something broken: Spanner finds the fault, researches the fix, tracks your progress, guides each step with a HUD over the camera plus 3D animations, talks you through it, listens to you, and alerts you when you make a mistake.

Read these first, every session: `VISION.md`, `PLAN.md`, `CONTRACTS.md`, this file, and your own section of the plan.

## Who owns what

| Owner | Folders / files |
|---|---|
| **P1 App** | `app/`, the Figma file |
| **P2 Eyes** | `backend/vision/`, `backend/media/`, `backend/packs/` |
| **P3 Voice** | `backend/main.py`, `contracts.py`, `config.py`, `session.py`, `mocks.py`, `backend/voice/`, `backend/brain/pass1*.py`, `backend/brain/prompts/pass1.md`, `training/`, `scripts/gen_types.py`, `run_pipeline.py`, `live_client.py`, `tests/` for those, root docs and config |
| **P4 Brain** | `backend/brain/pass2.py`, `research.py`, `safety.py`, `backend/store/`, `scripts/databricks_setup.sql`, `dashboard/` |

## Rules

- **Edit only your own folders.** Contract changes go through P3, are announced in chat, and happen at checkpoints.
- **No new packages without asking in the team chat.** P3 updates `pyproject.toml` and `uv.lock`.
- **Every module must run on fixtures** (`USE_MOCKS=1`, no API keys).
- **Prompts live in `prompts/*.md`**, not in Python strings.
- **No secrets in code.** Keys go in `.env`, which is never committed.
- **Load the `/claude-api` skill before writing any Claude call.** Model IDs: `claude-haiku-5-5` (vision, Pass 1), `claude-sonnet-5-5` (Pass 2), `claude-opus-5-5` (research, dataset generation). No date suffixes. Haiku 5.5 / Sonnet 5.5 / Opus 5.5 reject `temperature`, `top_p`, `top_k` and assistant prefill.
- Review loop: Claude builds -> a human runs it -> reads the diff -> `/code-review` -> merge.
- Branches: `feat/app`, `feat/eyes`, `feat/voice`, `feat/brain`; merge to `main` at 14:00, 18:00, 22:00, 03:00.

## Run it

```bash
./start.sh                                                # one-command demo launcher: backend, Cloudflare, viewer and Expo
uv sync                                                   # once; torch is ~2 GB
cp .env.example .env                                      # then fill in keys
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000
cloudflared tunnel --url http://localhost:8000            # phone -> laptop (venue Wi-Fi blocks LAN)
uv run pytest                                             # no keys needed; add -m live for real API tests
uv run python scripts/run_pipeline.py fixtures/frames/placeholder.jpg fixtures/audio/question.mp3
uv run python scripts/live_client.py --url http://127.0.0.1:8000 --frame fixtures/frames/placeholder.jpg   # fake phone; also --clip FILE, --wav WAV
uv run python scripts/gen_types.py                        # P1: refresh app/types/contracts.ts
```

Open `http://localhost:8000/debug` to watch the live frame, boxes, observation, report, response and stage timings.

## Mock mode

`USE_MOCKS=1` (the default in `.env.example`) makes every stage return fixture data from `fixtures/outputs/`, so the app can be built with no keys and no other module. The mock Pass 2 plays a whole fake repair (identifying -> diagnosing -> researching -> guiding steps 1..5 -> done). `GET /health` shows which stages are `real` and which are `mock`.

With `USE_MOCKS=0`, `backend/main.py` calls the real stage when its module exists and falls back to the mock when it doesn't, so teammates can merge at different times.

Switches (in `.env`): `PASS1_BACKEND=claude|adaption`, `FAL_ENABLED=0|1`, `USE_MOCKS=0|1`, `STORE_BACKEND=databricks|sqlite`, `MOCK_LATENCY_MS`.

## Architecture in one screen

- **One WebSocket per session** (`WS /session/{id}/live`, protocol in `CONTRACTS.md`) carries camera frames, voice, and every brain result **pushed the moment it is ready**. HTTP `POST /frame` and `POST /audio` stay as fallbacks (results then wait for the next frame reply).
- **Fast loop** (per frame, under 0.5 s): preprocess -> detect -> `frame_result` with boxes for the HUD. Only the newest waiting frame is processed. `detect` is real (`backend/vision/detect.py`, YOLO + ByteTrack); the other vision stages are still mocks until P2 lands them.
- **Slow loop** (background, per keyframe, under 5 s): understand -> step check + Pass 1 -> safety -> Pass 2 -> `event` pushed to the phone. One run per session at a time; the newest keyframe waits its turn.
- **Voice**: Expo Go sends each utterance as an m4a clip (`utterance`); a dev build can stream PCM (`audio`, gives live captions). Replies carry a `tts_url` (streams while being generated) or, in `stream` mode, MP3 chunks on the socket. The phone mutes its mic during playback and reports it with `playback` messages; barge-in stops the reply.
- **Camera + detector + speech reach Claude together** (`backend/perception.py`): on a spoken turn Pass 1 gets the latest camera frame, one from when the user started speaking, a close-up of the main detected object, the YOLO tracks and the words; Pass 2 gets the same when it answers a question. The older text observation is tagged with its age. Those spoken turns use Sonnet 5.5 at low effort (`PASS1_VISION_MODEL`, about 0.6 cents a turn with the prompt cache warm); turns without pictures stay on Haiku. See `CONTRACTS.md` ("What Claude sees on a spoken turn") and `/debug` for what the last turn showed.
- **Knowledge layer** (`backend/knowledge.py`, `backend/parts_agent.py`, `training/`; Alberto): the orchestrator calls the **step tracker** alongside Pass 1 while guiding (result in `session.last_step_check`, read by Pass 2) and polls the **Linkup parts job** that Pass 2 leaves in `session.brain_state["parts_job"]`, pushing the finished card as `BrainResponse.parts_card`.
- **Pass 1** (`backend/brain/pass1.py`): camera observations + speech -> `SituationReport`. Claude Haiku by default, and it stays on Claude. `pass1_adaption.py` can serve a Pass 1 fine-tune (`PASS1_BACKEND=adaption` + `PASS1_ADAPTION_MODEL_ID`) and falls back to Claude on bad JSON or after 800 ms. Every call is logged to `pass1_calls` and `training/data/raw/pass1_calls.jsonl`.
- **Adaption**: the team's entry is the **step tracker** (`training/README.md`, model in `ADAPTION_MODEL_ID`). Do not spend the credits on Pass 1. `training/make_dataset.py`, `eval.py` and `ADAPTION_RUNBOOK.md` are an optional Pass 1 fine-tune kept for reference; they use a different variable (`PASS1_ADAPTION_MODEL_ID`) and a Pass 1 setting equal to the step tracker's id is refused at runtime.
