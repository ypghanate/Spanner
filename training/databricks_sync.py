"""Push the training data to Databricks (Unity Catalog bronze tables + a Volume).

  python -m training.pipeline databricks init   # create catalog/schema/volume/bronze tables
  python -m training.pipeline databricks sync   # upload everything produced locally

Without DATABRICKS_HOST it writes the same rows to data/lake/<table>.jsonl instead, so the
pipeline can be tested before the workspace is connected.

Env: DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_WAREHOUSE_HTTP_PATH,
     DATABRICKS_CATALOG (default spanner), DATABRICKS_TRAINING_SCHEMA (default training).
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LAKE = DATA / "lake"
SETUP_SQL = ROOT / "databricks" / "sql" / "setup.sql"
BATCH = 50

# table -> the column that identifies one source; rows for a source are replaced on re-sync
TABLE_KEYS = {
    "bronze_sources": "source_id",
    "bronze_web_pages": "page_id",
    "bronze_transcripts": "video_id",
    "bronze_segment_labels": "video_id",
    "bronze_procedures": "procedure_id",
    "bronze_part_cards": "card_id",
    "bronze_part_offers": "card_id",
    "bronze_manual_facts": "card_id",
    "bronze_live_mistakes": "session_id",
    "bronze_eval_results": "run_id",
    "bronze_stage_timings": "timing_id",
}


def names() -> tuple[str, str]:
    return (os.getenv("DATABRICKS_CATALOG", "spanner"),
            os.getenv("DATABRICKS_TRAINING_SCHEMA", "training"))


def remote() -> bool:
    return bool(os.getenv("DATABRICKS_HOST"))


# ---------------------------------------------------------------- collect rows from local files
def collect(cfg: dict) -> dict[str, list[dict]]:
    now = datetime.now(timezone.utc).isoformat()
    repair = cfg["repair_id"]
    rows: dict[str, list[dict]] = {t: [] for t in TABLE_KEYS}

    sources_csv = ROOT / "training" / "sources.csv"
    if sources_csv.exists():
        with sources_csv.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("video_id"):
                    rows["bronze_sources"].append({
                        "source_id": r["video_id"], "kind": "video", "repair_id": repair,
                        "url": r.get("url", ""), "title": r.get("notes", ""), "creator": r.get("creator", ""),
                        "licence": r.get("licence", ""), "ingested_at": now})

    for p in sorted((DATA / "web").glob("*.json")):
        page = json.loads(p.read_text(encoding="utf-8"))
        rows["bronze_web_pages"].append({
            "page_id": page["page_id"], "repair_id": repair, "url": page["url"], "title": page["title"],
            "query": page["query"], "raw_content": page["raw_content"], "fetched_at": page["fetched_at"],
            "ingested_at": now})
        rows["bronze_sources"].append({
            "source_id": page["page_id"], "kind": "web", "repair_id": repair, "url": page["url"],
            "title": page["title"], "creator": "", "licence": "web page (see URL)", "ingested_at": now})

    for work in sorted((DATA / "work").glob("*")):
        tr, seg = work / "transcript.json", work / "segments.json"
        if tr.exists():
            t = json.loads(tr.read_text(encoding="utf-8"))
            rows["bronze_transcripts"].append({
                "video_id": work.name, "repair_id": repair, "language": t.get("language") or "",
                "n_words": len(t["words"]), "words_json": json.dumps(t["words"]), "ingested_at": now})
        if seg.exists():
            for s in json.loads(seg.read_text(encoding="utf-8")):
                rows["bronze_segment_labels"].append({
                    "video_id": work.name, "repair_id": repair, "start_s": s["start"], "end_s": s["end"],
                    "transcript": s["transcript"], "frames_json": json.dumps(s["frames"]),
                    "label_json": json.dumps(s["label"]), "label_model": cfg.get("label_model", ""),
                    "ingested_at": now})

    proc_dir = DATA / "procedures"
    for p in sorted(proc_dir.rglob("*.json")):
        if p.name == "schema.json":
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        rows["bronze_procedures"].append({
            "procedure_id": rec["procedure_id"], "repair_id": rec["repair_id"],
            "source_kind": rec.get("source_kind", "video"), "record_json": json.dumps(rec),
            "ingested_at": now})

    for p in sorted((DATA / "parts").glob("*.json")):  # Linkup replacement cards
        card = json.loads(p.read_text(encoding="utf-8"))
        ident = card["identity"]
        d = card.get("decision") or {}
        rows["bronze_part_cards"].append({
            "card_id": card["card_id"], "brand": ident["brand"], "model": ident["model"],
            "model_number": ident["model_number"], "part_needed": card["part_needed"], "fault": card["fault"],
            "manual_url": card.get("manual_url", ""), "n_sources": len(card.get("sources", [])),
            "is_mock": bool(card.get("mock")), "verdict": d.get("verdict", ""),
            "repair_cost": float(d.get("repair_cost", 0)), "new_price": float(d.get("new_price", 0)),
            "savings": float(d.get("savings", 0)), "ewaste_kg_avoided": float(d.get("ewaste_kg_avoided", 0)),
            "currency": d.get("currency", ""), "recall_status": (card.get("recall") or {}).get("status", ""),
            "card_json": json.dumps(card), "searched_at": card["searched_at"], "ingested_at": now})
        for c in card["options"]:
            o, v = c["offer"], c["verdict"]
            rows["bronze_part_offers"].append({
                "card_id": card["card_id"], "part_name": o["part_name"], "part_number": o["part_number"],
                "retailer": o["retailer"], "price": o["price"], "currency": o["currency"],
                "in_stock": o["in_stock"], "url": o["url"], "fits": v["fits"], "verified": c["verified"],
                "independent_sources": c["independent_sources"], "verdict_reason": v["reason"],
                "searched_at": card["searched_at"], "ingested_at": now})
        for s in (card.get("manual") or {}).get("specs", []):
            rows["bronze_manual_facts"].append({
                "card_id": card["card_id"], "manual_url": card.get("manual_url", ""),
                "title": card["manual"]["title"], "spec_name": s["name"], "spec_value": s["value"],
                "spec_unit": s["unit"], "page": s["page"], "ingested_at": now})

    live = DATA / "live" / "mistakes.jsonl"
    if live.exists():
        for line in live.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows["bronze_live_mistakes"].append({**json.loads(line), "ingested_at": now})

    timings = DATA / "live" / "stage_timings.jsonl"
    if timings.exists():
        for line in timings.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows["bronze_stage_timings"].append({**json.loads(line), "ingested_at": now})

    for p in sorted((DATA / "eval").glob("*.json")):  # one row per metric (long format, easy to chart)
        r = json.loads(p.read_text(encoding="utf-8"))
        for metric, value in r.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and metric != "n":
                rows["bronze_eval_results"].append({
                    "run_id": r["run_id"], "eval_name": r["eval_name"], "backend": r.get("backend", ""),
                    "metric": metric, "value": float(value), "n": int(r.get("n", 0)),
                    "created_at": r["created_at"], "ingested_at": now})
    return rows


def files_to_upload(include_videos: bool = False) -> list[Path]:
    """Keyframes used by procedures, review pages, scraped pages (and optionally the videos)."""
    files: set[Path] = set()
    for p in (DATA / "procedures").rglob("*.json"):
        if p.name == "schema.json":
            continue
        for kf in json.loads(p.read_text(encoding="utf-8")).get("keyframes", {}).values():
            if (ROOT / kf).exists():
                files.add(ROOT / kf)
    files |= set((DATA / "work").glob("*/review.html"))
    files |= set((DATA / "web").glob("*.json"))
    adaption = ROOT / "training" / "adaption" / "data"   # the fine-tuning dataset, versioned on the Volume
    files |= {f for f in adaption.glob("*") if f.is_file()}
    if include_videos:
        files |= {f for f in (DATA / "videos").glob("*") if f.is_file()}
    return sorted(files)


# ---------------------------------------------------------------- Databricks
def _connect():
    from databricks import sql

    return sql.connect(server_hostname=os.environ["DATABRICKS_HOST"].replace("https://", "").rstrip("/"),
                       http_path=os.environ["DATABRICKS_WAREHOUSE_HTTP_PATH"],
                       access_token=os.environ["DATABRICKS_TOKEN"])


def init() -> None:
    catalog, schema = names()
    sql_text = SETUP_SQL.read_text(encoding="utf-8").format(catalog=catalog, schema=schema)
    sql_text = "\n".join(l for l in sql_text.splitlines() if not l.strip().startswith("--"))
    statements = [s.strip() for s in sql_text.split(";") if s.strip()]
    if not remote():
        print("No DATABRICKS_HOST: would run these statements:\n")
        print(";\n\n".join(statements) + ";")
        return
    with _connect() as conn, conn.cursor() as cur:
        for stmt in statements:
            try:
                cur.execute(stmt)
            except Exception as e:
                if stmt.startswith("CREATE CATALOG"):
                    print(f"! Could not create catalog '{catalog}' ({e}).\n"
                          "  On Free Edition, set DATABRICKS_CATALOG to an existing catalog, e.g. 'workspace'.")
                    raise SystemExit(1)
                raise
    print(f"Ready: {catalog}.{schema} (volume raw + {len(TABLE_KEYS)} bronze tables)")


def _replace_rows(cur, table: str, key: str, rows: list[dict]) -> None:
    catalog, schema = names()
    fq = f"{catalog}.{schema}.{table}"
    keys = sorted({r[key] for r in rows})
    for i in range(0, len(keys), BATCH):
        chunk = keys[i:i + BATCH]
        ph = ", ".join(f":k{j}" for j in range(len(chunk)))
        cur.execute(f"DELETE FROM {fq} WHERE {key} IN ({ph})", {f"k{j}": k for j, k in enumerate(chunk)})
    cols = list(rows[0].keys())
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        values, params = [], {}
        for j, r in enumerate(chunk):
            values.append("(" + ", ".join(
                f"CAST(:{c}_{j} AS TIMESTAMP)" if c == "ingested_at" else f":{c}_{j}" for c in cols) + ")")
            params.update({f"{c}_{j}": r[c] for c in cols})
        cur.execute(f"INSERT INTO {fq} ({', '.join(cols)}) VALUES {', '.join(values)}", params)


def sync(cfg: dict, include_videos: bool = False) -> dict[str, int]:
    rows = collect(cfg)
    counts = {t: len(r) for t, r in rows.items()}
    files = files_to_upload(include_videos)
    catalog, schema = names()

    if not remote():
        LAKE.mkdir(parents=True, exist_ok=True)
        for table, rs in rows.items():
            (LAKE / f"{table}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rs), encoding="utf-8")
        print(f"No DATABRICKS_HOST: wrote local copies to {LAKE.relative_to(ROOT)}/ "
              f"({', '.join(f'{t}={n}' for t, n in counts.items())}); {len(files)} files would be uploaded.")
        return counts

    with _connect() as conn, conn.cursor() as cur:
        for table, rs in rows.items():
            if rs:
                _replace_rows(cur, table, TABLE_KEYS[table], rs)
                print(f"   {catalog}.{schema}.{table}: {len(rs)} rows")

    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    for f in files:
        dest = f"/Volumes/{catalog}/{schema}/raw/{f.relative_to(DATA).as_posix()}"
        w.files.upload(dest, io.BytesIO(f.read_bytes()), overwrite=True)
    print(f"   uploaded {len(files)} files to /Volumes/{catalog}/{schema}/raw/")
    return counts
