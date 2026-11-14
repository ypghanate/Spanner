"""The learning loop: live mistakes go to Databricks and come back as warnings.

The live brain calls `record_live_mistake(...)` whenever the step tracker reports an error.
Rows land in data/live/mistakes.jsonl (always) and, with DATABRICKS_HOST set, straight into
spanner.training.bronze_live_mistakes in the background. The Lakeflow pipeline counts them
(gold_live_mistakes), and any mistake seen in 2+ sessions is merged into that step's
common_mistakes in the knowledge base. The next user gets warned about it before they make it.

    from training.feedback import record_live_mistake
    record_live_mistake(session_id, repair_id, step_index=3, mistake="pad pushed in upside down",
                        observation="camera text...")
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "live" / "mistakes.jsonl"
_lock = threading.Lock()


def record_live_mistake(session_id: str, repair_id: str, step_index: int, mistake: str,
                        observation: str = "") -> dict:
    row = {"session_id": session_id, "repair_id": repair_id, "step_index": int(step_index),
           "mistake": mistake.strip(), "observation": observation,
           "ts": datetime.now(timezone.utc).isoformat()}
    with _lock:
        LIVE.parent.mkdir(parents=True, exist_ok=True)
        with LIVE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    if os.getenv("DATABRICKS_HOST"):
        threading.Thread(target=_insert, args=(row,), daemon=True).start()  # never blocks the live loop
    return row


def _insert(row: dict) -> None:
    try:
        from training import databricks_sync as ds

        catalog, schema = ds.names()
        with ds._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {catalog}.{schema}.bronze_live_mistakes "
                "(session_id, repair_id, step_index, mistake, observation, ts, ingested_at) "
                "VALUES (:session_id, :repair_id, :step_index, :mistake, :observation, :ts, current_timestamp())",
                row)
    except Exception as e:  # the jsonl copy is synced later by `databricks sync`
        print(f"live mistake not sent to Databricks yet: {e}")
