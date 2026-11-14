"""Publish the Spanner AI/BI dashboard and Genie space to the workspace (no Databricks CLI needed).

    python databricks/deploy_dashboard.py            # create or update + publish, print the URLs
    python databricks/deploy_dashboard.py --check    # only run every dataset query and show row counts

Reads DATABRICKS_HOST / DATABRICKS_TOKEN / DATABRICKS_WAREHOUSE_HTTP_PATH / DATABRICKS_CATALOG from .env.
The dashboard file uses ${catalog}.${schema} placeholders so the same file works with `databricks bundle deploy`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DASH = ROOT / "databricks" / "dashboards" / "spanner_training.lvdash.json"
TITLE = "Spanner repair intelligence"
GENIE_TITLE = "Spanner repair intelligence (Genie)"
GENIE_INSTRUCTIONS = (
    "Spanner is an AI repair assistant. gold_procedure_steps is its knowledge base: one row per repair step; "
    "source_kind = canonical is the final procedure. bronze_live_mistakes holds mistakes the camera caught in live "
    "sessions (repair_id, step_index, mistake, session_id); a mistake seen in 2 or more distinct sessions is a "
    "'learned warning'. bronze_stage_timings holds measured latency per pipeline stage in milliseconds ('coach' = "
    "one webcam frame judged). bronze_part_cards is the Linkup agent's repair-or-replace decision per device "
    "(verdict, repair_cost, new_price, savings, ewaste_kg_avoided, GBP); gold_part_prices are the offers it found, "
    "verified = fit confirmed on independent sites. gold_eval_scoreboard compares the Adaption fine-tuned step judge "
    "(backend = adaption) with Claude (backend = claude). Ignore rows where is_mock is true. The demo repair_id is "
    "rk61_switch_replacement.")


def _env() -> tuple[str, str, str, str]:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from training.databricks_sync import names

    catalog, schema = names()
    wid = os.environ["DATABRICKS_WAREHOUSE_HTTP_PATH"].rstrip("/").rsplit("/", 1)[-1]
    return os.environ["DATABRICKS_HOST"], os.environ["DATABRICKS_TOKEN"], f"{catalog}.{schema}", wid


def rendered(catalog_schema: str) -> dict:
    catalog, schema = catalog_schema.split(".")
    text = DASH.read_text(encoding="utf-8").replace("${catalog}", catalog).replace("${schema}", schema)
    return json.loads(text)


def check(doc: dict) -> bool:
    from training.databricks_sync import _connect

    ok = True
    with _connect() as conn, conn.cursor() as cur:
        for ds in doc["datasets"]:
            try:
                cur.execute(ds["query"])
                rows = cur.fetchall()
                print(f"  {ds['name']:<14} {len(rows):>4} rows   {[d[0] for d in cur.description][:6]}")
            except Exception as e:
                ok = False
                print(f"  {ds['name']:<14} FAILED  {str(e)[:160]}")
    return ok


def deploy(doc: dict, host: str, token: str, wid: str, catalog_schema: str) -> None:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.dashboards import Dashboard

    w = WorkspaceClient(host=host, token=token)
    me = w.current_user.me().user_name
    folder = f"/Workspace/Users/{me}/spanner"
    try:
        w.workspace.mkdirs(folder)
    except Exception:
        pass
    serialized = json.dumps(doc)
    existing = next((d for d in w.lakeview.list() if d.display_name == TITLE), None)
    if existing:
        d = w.lakeview.update(existing.dashboard_id, dashboard=Dashboard(serialized_dashboard=serialized, warehouse_id=wid))
        print("updated dashboard", d.dashboard_id)
    else:
        d = w.lakeview.create(dashboard=Dashboard(display_name=TITLE, serialized_dashboard=serialized,
                                                  warehouse_id=wid, parent_path=folder))
        print("created dashboard", d.dashboard_id)
    w.lakeview.publish(d.dashboard_id, embed_credentials=True, warehouse_id=wid)
    print(f"dashboard: {host}/dashboardsv3/{d.dashboard_id}/published")

    # Genie: the same tables, with the instructions from databricks/genie.md.
    # The export format the API accepts today: version 2, tables sorted by identifier, instruction content
    # as a list of strings. Sample questions are added in the UI (see genie.md).
    catalog, schema = catalog_schema.split(".")
    tables = sorted(f"{catalog}.{schema}.{t}" for t in [
        "gold_procedure_steps", "bronze_live_mistakes", "bronze_stage_timings", "bronze_part_cards",
        "gold_part_prices", "gold_kb_coverage", "gold_eval_scoreboard", "bronze_sources"])
    space = {"version": 2,
             "data_sources": {"tables": [{"identifier": t} for t in tables]},
             "instructions": {"text_instructions": [{"content": [GENIE_INSTRUCTIONS]}]}}
    try:
        spaces = w.genie.list_spaces().spaces or []
        existing_space = next((s for s in spaces if s.title == GENIE_TITLE), None)
        if existing_space:
            print(f"genie space exists: {host}/genie/rooms/{existing_space.space_id}")
        else:
            s = w.genie.create_space(warehouse_id=wid, serialized_space=json.dumps(space), title=GENIE_TITLE,
                                     description="Ask the repair data anything", parent_path=folder)
            print(f"genie space: {host}/genie/rooms/{s.space_id}")
    except Exception as e:
        print(f"Genie space not created automatically ({str(e)[:200]}). Create it in the UI with the tables and "
              f"instructions in databricks/genie.md.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="run the dataset queries only")
    args = ap.parse_args()
    host, token, catalog_schema, wid = _env()
    doc = rendered(catalog_schema)
    print(f"{catalog_schema} on warehouse {wid}: checking {len(doc['datasets'])} datasets")
    ok = check(doc)
    if args.check:
        return
    if not ok:
        raise SystemExit("fix the failing queries before publishing")
    deploy(doc, host, token, wid, catalog_schema)


if __name__ == "__main__":
    main()
