"""The learning loop: live mistakes go to Databricks and come back as warnings.

The live brain (phone pipeline) and the laptop coach call `record_live_mistake(...)` whenever they see
the user make a mistake. Rows land in data/live/mistakes.jsonl (always) and, with DATABRICKS_HOST set,
straight into <catalog>.training.bronze_live_mistakes in the background. `learned_mistakes(repair_id)`
asks Databricks which mistakes were seen in 2+ distinct sessions: those become the warning the next
user hears before that step ("People often put the switch in rotated here"). The Lakeflow pipeline
also folds them into gold_procedure_steps.common_mistakes for the knowledge base.

Per-stage latencies (`record_stage_timing`) take the same road into bronze_stage_timings, so the
dashboard's "how long each live decision takes" is measured, not estimated.

Set SPANNER_LIVE_LOG=0 (tests do) to switch all of this off.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "live" / "mistakes.jsonl"
TIMINGS = ROOT / "data" / "live" / "stage_timings.jsonl"
_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("SPANNER_LIVE_LOG", "1") != "0"


def _remote() -> bool:
    return enabled() and bool(os.getenv("DATABRICKS_HOST"))


# ---------------------------------------------------------------- mistakes -> Databricks
def record_live_mistake(session_id: str, repair_id: str, step_index: int, mistake: str,
                        observation: str = "") -> dict:
    row = {"session_id": session_id, "repair_id": repair_id, "step_index": int(step_index),
           "mistake": mistake.strip(), "observation": observation,
           "ts": datetime.now(timezone.utc).isoformat()}
    if not enabled():
        return row
    with _lock:
        LIVE.parent.mkdir(parents=True, exist_ok=True)
        with LIVE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    if _remote():
        threading.Thread(target=_insert_mistake, args=(row,), daemon=True).start()  # never blocks the live loop
    return row


def _insert_mistake(row: dict) -> None:
    try:
        from training import databricks_sync as ds

        catalog, schema = ds.names()
        with ds._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {catalog}.{schema}.bronze_live_mistakes "
                "(session_id, repair_id, step_index, mistake, observation, ts, ingested_at) "
                "VALUES (:session_id, :repair_id, :step_index, :mistake, :observation, :ts, current_timestamp())",
                row)
        _learned_cache.clear()          # the next question gets the fresh count
    except Exception as e:  # the jsonl copy is synced later by `databricks sync`
        print(f"live mistake not sent to Databricks yet: {e}")


# ---------------------------------------------------------------- Databricks -> warnings
_learned_cache: dict[str, tuple[float, dict[int, str]]] = {}
LEARNED_TTL_S = 30.0
MIN_SESSIONS = 2


def _learned_local(repair_id: str) -> dict[int, str]:
    if not LIVE.exists():
        return {}
    sessions: dict[tuple[int, str], set[str]] = defaultdict(set)
    for line in LIVE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        mistake = str(row.get("mistake", "")).strip().lower()
        if row.get("repair_id") == repair_id and mistake:
            sessions[(int(row.get("step_index", -1)), mistake)].add(str(row.get("session_id", "")))
    out: dict[int, str] = {}
    for (step, mistake), ids in sorted(sessions.items(), key=lambda kv: -len(kv[1])):
        if len(ids) >= MIN_SESSIONS and step not in out:
            out[step] = mistake
    return out


def _learned_databricks(repair_id: str) -> dict[int, str]:
    from training import databricks_sync as ds

    catalog, schema = ds.names()
    with ds._connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT step_index, lower(trim(mistake)) AS mistake, count(DISTINCT session_id) AS sessions "
            f"FROM {catalog}.{schema}.bronze_live_mistakes "
            "WHERE repair_id = :repair_id AND length(trim(mistake)) > 0 "
            "GROUP BY step_index, lower(trim(mistake)) HAVING count(DISTINCT session_id) >= :min_sessions "
            "ORDER BY sessions DESC", {"repair_id": repair_id, "min_sessions": MIN_SESSIONS})
        out: dict[int, str] = {}
        for step, mistake, _ in cur.fetchall():
            out.setdefault(int(step), mistake)
        return out


def learned_mistakes(repair_id: str) -> dict[int, str]:
    """{step_index: mistake} for every mistake seen in 2+ distinct sessions of this repair.
    Databricks is the source of truth; the local jsonl stands in when it is unreachable."""
    hit = _learned_cache.get(repair_id)
    if hit and time.monotonic() - hit[0] < LEARNED_TTL_S:
        return hit[1]
    out: dict[int, str] | None = None
    if _remote():
        try:
            out = _learned_databricks(repair_id)
        except Exception as e:
            print(f"learned mistakes: Databricks unreachable ({e}); using the local copy")
    if out is None:
        out = _learned_local(repair_id)
    _learned_cache[repair_id] = (time.monotonic(), out)
    return out


def learned_mistake_for(repair_id: str, step_index: int | None = None) -> str | None:
    """One learned mistake: for a step, or the most-seen one for the whole repair."""
    learned = learned_mistakes(repair_id)
    if step_index is not None:
        return learned.get(int(step_index))
    return next(iter(learned.values()), None)


# ---------------------------------------------------------------- latency -> Databricks
_pending: list[dict] = []
_flusher_started = False


def record_stage_timing(session_id: str, stage: str, ms: float, ok: bool = True) -> None:
    """One measured stage latency (phone pipeline stages, or the laptop coach's 'coach' stage)."""
    global _flusher_started
    if not enabled():
        return
    row = {"timing_id": f"{session_id}:{time.time_ns()}", "session_id": session_id, "ts": time.time(),
           "stage": stage, "ms": round(float(ms), 1), "ok": bool(ok)}
    with _lock:
        TIMINGS.parent.mkdir(parents=True, exist_ok=True)
        with TIMINGS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if _remote():
            _pending.append(row)
            if not _flusher_started:
                _flusher_started = True
                threading.Thread(target=_flush_loop, daemon=True, name="timing-flush").start()


def _flush_loop() -> None:
    while True:
        time.sleep(5)
        flush_timings()


def flush_timings() -> None:
    with _lock:
        batch, _pending[:] = list(_pending), []
    if not batch:
        return
    try:
        from training import databricks_sync as ds

        catalog, schema = ds.names()
        with ds._connect() as conn, conn.cursor() as cur:
            for i in range(0, len(batch), 100):
                chunk = batch[i:i + 100]
                params, values = {}, []
                for j, r in enumerate(chunk):
                    params.update({f"i{j}": r["timing_id"], f"s{j}": r["session_id"], f"t{j}": r["ts"],
                                   f"g{j}": r["stage"], f"m{j}": r["ms"], f"o{j}": r["ok"]})
                    values.append(f"(:i{j}, :s{j}, :t{j}, :g{j}, :m{j}, :o{j}, current_timestamp())")
                cur.execute(f"INSERT INTO {catalog}.{schema}.bronze_stage_timings "
                            "(timing_id, session_id, ts, stage, ms, ok, ingested_at) VALUES " + ", ".join(values),
                            params)
    except Exception as e:
        print(f"stage timings not sent to Databricks yet ({len(batch)} rows kept locally): {e}")
        with _lock:
            _pending[:0] = batch
