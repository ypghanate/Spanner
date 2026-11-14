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
uv sync                                                   # once; torch is ~2 GB
cp .env.example .env                                      # then fill in keys
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000
cloudflared tunnel --url http://localhost:8000            # phone -> laptop (venue Wi-Fi blocks LAN)
uv run pytest                                             # no keys needed; add -m live for real API tests
uv run python scripts/run_pipeline.py fixtures/frames/placeholder.jpg fixtures/audio/question.mp3
uv run python scripts/live_client.py --url http://127.0.0.1:8000   # fake phone for the live voice socket
uv run python scripts/gen_types.py                        # P1: refresh app/types/contracts.ts
```

Open `http://localhost:8000/debug` to watch the live frame, boxes, observation, report, response and stage timings.

## Mock mode

`USE_MOCKS=1` (the default in `.env.example`) makes every stage return fixture data from `fixtures/outputs/`, so the app can be built with no keys and no other module. The mock Pass 2 plays a whole fake repair (identifying -> diagnosing -> researching -> guiding steps 1..5 -> done). `GET /health` shows which stages are `real` and which are `mock`.

With `USE_MOCKS=0`, `backend/main.py` calls the real stage when its module exists and falls back to the mock when it doesn't, so teammates can merge at different times.

Switches (in `.env`): `PASS1_BACKEND=claude|adaption`, `FAL_ENABLED=0|1`, `USE_MOCKS=0|1`, `STORE_BACKEND=databricks|sqlite`, `MOCK_LATENCY_MS`.

## Architecture in one screen

- **Fast loop** (inline in `POST /frame`, under 0.5 s): preprocess -> detect -> boxes for the HUD.
- **Slow loop** (background, per keyframe, under 5 s): understand -> Pass 1 -> safety -> Pass 2 -> TTS -> event queued for the next frame reply. One run per session at a time; the newest keyframe waits its turn.
- **Voice**: live WebSocket (`WS /session/{id}/live`): mic PCM -> ElevenLabs realtime STT -> Pass 1 -> safety -> Pass 2 -> streamed TTS, with barge-in. `POST /audio` is the fallback.
- **Pass 1** (`backend/brain/pass1.py`): camera observations + speech -> `SituationReport`. Claude Haiku by default; `pass1_adaption.py` serves the fine-tuned model and falls back to Claude on bad JSON or after 800 ms. Every call is logged to `pass1_calls` and `training/data/raw/pass1_calls.jsonl` (the fine-tune's training source).
- **Fine-tune**: `training/` (see `training/ADAPTION_RUNBOOK.md`).
