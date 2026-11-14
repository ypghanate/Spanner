# training/: turning repair videos and web guides into training data

Claude is not trained. Videos and web guides are turned into labelled data that makes the live
agent better, and everything lands in **Databricks** (catalog `spanner`, schema `training`).

```
LAPTOP                                    DATABRICKS (spanner.training)
Tavily web guides  ─┐                     Volume raw/   keyframes, review pages, web pages
repair videos      ─┼─ databricks sync ─▶ BRONZE  sources, web_pages, transcripts, segment_labels, procedures
Claude labels      ─┘                     SILVER  quality-gated labels + parsed procedures   (Lakeflow expectations)
                                          GOLD    procedure_steps (knowledge base), label_quality, kb_coverage
MLflow: every run + every Claude call ──▶ Vector Search index kb_steps_index ──▶ training/kb.py ──▶ live agent
                                          AI/BI dashboard + Genie (databricks/sql/dashboard_queries.sql)
```

| Output | Where | Status |
|---|---|---|
| **Procedure knowledge base** (videos + web guides, merged into one canonical procedure) | `data/procedures/` → `gold_procedure_steps` → Vector Search | ✅ built |
| **Search for the agent:** `from training.kb import search_steps` | Vector Search when `DATABRICKS_HOST` is set, keyword match offline | ✅ built |
| **Linkup replacement-part agent** (`backend/parts_agent.py`): offers, fit verified by 2+ sources, **repair-or-replace decision**, safety-recall check, manual specs, tutorial at the right chapter, all cited; `TOOL` for the brain | `data/parts/`, `data/manuals/` → `bronze_part_*`, `gold_part_prices` | ✅ built |
| **Adaption step tracker**: dataset (`adaption_dataset.py`), live `step_check()` (`step_tracker.py`), before/after eval (`eval_step_tracker.py`) | `training/adaption/data/` → Volume; results → `gold_eval_scoreboard` | ✅ built |
| **Learning loop**: `feedback.record_live_mistake()` → `gold_live_mistakes` → merged into the knowledge base | `bronze_live_mistakes` | ✅ built |
| **Parts-agent eval** (citation coverage, verified rate): `eval_parts.py` | `gold_eval_scoreboard` | ✅ built |
| **Quality gates** on Claude's labels | `databricks/pipelines/training_quality.sql` | ✅ built |
| **Experiment tracking + Claude traces** | MLflow experiment `/Shared/spanner-training` | ✅ built |
| Genie space + dashboard | `databricks/genie.md`, `databricks/sql/dashboard_queries.sql` (tiles 10–13 are the pitch) | set up in the UI |
| Detector dataset + trained YOLO weights | `training/detector/` | only if time |

### Adaption (launch training by 22:00 Saturday)
1. `python -m training.pipeline run` on the filmed videos, `merge`, then
   `python -m training.adaption_dataset --synthetic 3` → `training/adaption/data/train.jsonl`, `eval.jsonl`, `dataset_card.md`.
2. Upload `train.jsonl` to **Adaptive Data** (clean + expand), then fine-tune with **AutoScientist**.
3. Put the served model's ID in `.env`: `ADAPTION_MODEL_ID`, `TOGETHER_API_KEY` (or `ADAPTION_BASE_URL` + `ADAPTION_API_KEY` for another OpenAI-compatible endpoint).
4. `python -m training.eval_step_tracker` gives the before/after table vs Claude (MLflow + Databricks).

## Setup (once)

```bash
uv sync --group training          # from the repo root; the live backend alone needs only `uv sync`
uv run python -m training.pipeline --help
```
- **Live backend integration:** teammates use one module, `backend/knowledge.py`, which speaks `backend/contracts.py`: `plan_from_knowledge()` → `RepairPlan`, `search_steps()`, `track_step_async()` (step status in the contract's `in_progress | done | mistake`, mistakes logged to the learning loop), `PARTS_TOOL` / `handle_parts_tool()` / `get_parts_card()`. The parts card needs one contract field, `BrainResponse.parts_card` (proposal in `knowledge.py`, P3 to add).
- Tests: `uv run pytest tests/test_training_knowledge.py` (offline, no keys).
- `ffmpeg` on PATH.
- Keys in `.env` at the repo root (variables in `.env.example`). Everything also runs with `--mock` and without Databricks (local copies go to `data/lake/`).
- Databricks CLI for the bundle: `winget install Databricks.DatabricksCLI`, then `databricks auth login --host <workspace url>`.

## Workflow

1. **Set the repair** in `training/repair.json`: `repair_id`, `object`, `fault`, the `parts` and `tools` vocabulary, and `test_videos` (3–4 video IDs held out for evaluation; split by video, never by frame). Optional `web_queries` overrides the Tavily searches.
2. **Web guides:** `python -m training.pipeline web` uses Tavily to search and extract repair guides, and Claude keeps the real step-by-step guides as procedures. Pages that aren't guides are logged and skipped.
3. **Videos** go in `data/videos/` (git-ignored); log each one in `training/sources.csv`. Then run `python -m training.pipeline run`: 1 frame/s plus audio, local transcription, Claude labels every 10 s window, then each video's procedure. Cached and resumable.
4. **Spot-check about 20%** in `data/work/<video_id>/review.html` (red = low confidence). Fix `segments.json`, then `run <video> --rebuild-procedure`.
5. **Merge:** `python -m training.pipeline merge` writes the canonical procedure from the videos and web guides (test videos excluded).
6. **Databricks:**
   ```bash
   python -m training.pipeline databricks init     # once: catalog, schema, volume, bronze tables
   python -m training.pipeline databricks sync     # upload rows + files
   cd databricks && databricks bundle deploy && databricks bundle run training_refresh
   ```
   `training_refresh` runs the Lakeflow quality pipeline (silver/gold), then builds or syncs the Vector Search index.
7. **Dashboard:** create an AI/BI dashboard from `databricks/sql/dashboard_queries.sql`, and a Genie space on the `gold_*` tables so judges can ask questions in plain English.

## Cost and time

Labelling uses Claude Opus 5.5: about $0.02–0.03 per 10 s window, so about $5–8 for 15 three-minute videos; each web guide costs a few cents. Transcription is local and free.
