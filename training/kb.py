"""Knowledge-base lookup for the live agent: "which known repair step matches this?"

    from training.kb import search_steps
    hits = search_steps("chain hanging below the derailleur, user lifting it", k=3)
    # -> [{"step_id", "title", "instruction", "expected_visual_state", "common_mistakes", "score", ...}]

Uses the Databricks Vector Search index (spanner.training.kb_steps_index) when
DATABRICKS_HOST is set, otherwise a simple keyword match over data/procedures/ so it works
offline and in tests.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ["step_id", "procedure_id", "source_kind", "step_index", "title", "instruction",
           "expected_visual_state", "common_mistakes", "learned_mistake_count", "safety_notes", "keyframe_path"]


def _index_name() -> str:
    return (f"{os.getenv('DATABRICKS_CATALOG', 'spanner')}."
            f"{os.getenv('DATABRICKS_TRAINING_SCHEMA', 'training')}.kb_steps_index")


def search_steps(query: str, k: int = 5, canonical_only: bool = False) -> list[dict]:
    if os.getenv("DATABRICKS_HOST"):
        return _search_databricks(query, k, canonical_only)
    return _search_local(query, k, canonical_only)


def _search_databricks(query: str, k: int, canonical_only: bool) -> list[dict]:
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    res = w.vector_search_indexes.query_index(
        index_name=_index_name(), columns=COLUMNS, query_text=query, num_results=k,
        filters_json=json.dumps({"source_kind": "canonical"}) if canonical_only else None)
    cols = [c.name for c in res.manifest.columns]
    return [dict(zip(cols, row)) for row in (res.result.data_array or [])]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", text.lower()))


def _search_local(query: str, k: int, canonical_only: bool) -> list[dict]:
    q = _tokens(query)
    hits = []
    for p in (ROOT / "data" / "procedures").rglob("*.json"):
        if p.name == "schema.json":
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        kind = rec.get("source_kind", "video")
        if canonical_only and kind != "canonical":
            continue
        for st in rec["procedure"]["steps"]:
            text = " ".join([st["title"], st["instruction"], st["expected_visual_state"],
                             " ".join(st["parts"]), " ".join(st["common_mistakes"])])
            score = len(q & _tokens(text)) / (len(q) or 1)
            if score > 0:
                hits.append({"step_id": f"{rec['procedure_id']}#{st['index']}",
                             "procedure_id": rec["procedure_id"], "source_kind": kind,
                             "step_index": st["index"], "title": st["title"],
                             "instruction": st["instruction"],
                             "expected_visual_state": st["expected_visual_state"],
                             "common_mistakes": st["common_mistakes"], "safety_notes": st["safety_notes"],
                             "keyframe_path": rec.get("keyframes", {}).get(str(st["index"])),
                             "score": round(score, 3)})
    return sorted(hits, key=lambda h: h["score"], reverse=True)[:k]
