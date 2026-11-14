"""Store (real): every row is kept in memory, appended to data/live/<table>.jsonl, and shipped to
Databricks in batches every 5 s (workspace.training.bronze_live_events) by a background thread, so the
live loop never waits on the warehouse. Without DATABRICKS_HOST the jsonl files are the store."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "data" / "live"
log = logging.getLogger(__name__)


class DatabricksStore:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self._pending: list[tuple[str, str, str]] = []
        self._lock = threading.Lock()
        self._ready = False
        self.remote = bool(os.getenv("DATABRICKS_HOST"))
        if self.remote:
            threading.Thread(target=self._loop, daemon=True, name="store-flush").start()

    def write(self, table: str, row: dict) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        line = json.dumps(row, default=str)
        with self._lock:
            self.tables.setdefault(table, []).append(row)
            self.tables[table] = self.tables[table][-1000:]
            LIVE.mkdir(parents=True, exist_ok=True)
            with (LIVE / f"{table}.jsonl").open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            if self.remote:
                self._pending.append((table, line, ts))

    def read(self, table: str, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self.tables.get(table, []))[-limit:]

    def flush(self) -> None:
        if not self.remote:
            return
        with self._lock:
            batch, self._pending = self._pending, []
        if not batch:
            return
        try:
            from training import databricks_sync as ds

            catalog, schema = ds.names()
            name = f"{catalog}.{schema}.bronze_live_events"
            with ds._connect() as conn, conn.cursor() as cur:
                if not self._ready:
                    cur.execute(f"CREATE TABLE IF NOT EXISTS {name} (table_name STRING, row_json STRING, "
                                "ts STRING, ingested_at TIMESTAMP)")
                    self._ready = True
                for i in range(0, len(batch), 200):
                    chunk = batch[i:i + 200]
                    params, values = {}, []
                    for j, (t, r, ts) in enumerate(chunk):
                        params.update({f"t{j}": t, f"r{j}": r, f"s{j}": ts})
                        values.append(f"(:t{j}, :r{j}, :s{j}, current_timestamp())")
                    cur.execute(f"INSERT INTO {name} VALUES " + ", ".join(values), params)
        except Exception as e:   # keep the rows: they're also in data/live/*.jsonl
            log.warning("store flush to Databricks failed (%s); %d rows kept locally", e, len(batch))

    def _loop(self) -> None:
        while True:
            time.sleep(5)
            self.flush()


_store: DatabricksStore | None = None


def get_store() -> DatabricksStore:
    global _store
    if _store is None:
        _store = DatabricksStore()
    return _store
