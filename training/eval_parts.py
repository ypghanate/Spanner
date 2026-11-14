"""Evaluation of the Linkup parts agent: is every claim cited, and how often is fit verified?

Scores every replacement card in data/parts/ (real ones only unless --include-mock):
  citation_coverage  share of claims on the card that carry a source URL (target: 1.0)
  verified_rate      share of offers verified for the exact model on 2+ independent sites
  decision_rate      share of cards with a repair-or-replace verdict
  manual_rate        share of cards with official-manual facts
Logged to MLflow and written to data/eval/ for the Databricks dashboard.

  python -m training.eval_parts
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from training import tracking  # noqa: E402


def claims(card: dict) -> list[bool]:
    """One bool per claim shown on the card: does it carry a source?"""
    out = []
    for c in card["options"]:
        out.append(bool(c["offer"]["url"]))                 # price / stock claim
        out.append(bool(c["verdict"]["supporting_urls"]))   # fit claim
    if card.get("decision"):
        out.append(len(card["decision"]["sources"]) >= 2)   # part price + new price
    if card.get("recall") and card["recall"]["status"] == "recall_found":
        out.append(bool(card["recall"]["urls"]))
    if card.get("manual"):
        out.append(bool(card.get("manual_url")))
    out += [bool(t.get("url")) for t in card.get("tutorials", [])]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-mock", action="store_true")
    a = ap.parse_args()
    cards = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "data" / "parts").glob("*.json"))]
    cards = [c for c in cards if a.include_mock or not c.get("mock")]
    if not cards:
        sys.exit("No replacement cards yet. Run `python -m backend.parts_agent ...` first.")
    all_claims = [x for c in cards for x in claims(c)]
    offers = [o for c in cards for o in c["options"]]
    res = {
        "n": len(cards),
        "citation_coverage": sum(all_claims) / len(all_claims) if all_claims else 0,
        "verified_rate": sum(o["verified"] for o in offers) / len(offers) if offers else 0,
        "decision_rate": sum(bool(c.get("decision")) for c in cards) / len(cards),
        "manual_rate": sum(bool(c.get("manual")) for c in cards) / len(cards),
        "avg_sources_per_card": sum(len(c.get("sources", [])) for c in cards) / len(cards),
    }
    with tracking.run("parts-agent-eval", {"cards": len(cards)}) as mlf:
        mlf.log_metrics(res)
    run_id = uuid.uuid4().hex[:12]
    out = ROOT / "data" / "eval" / f"parts_agent_{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"run_id": run_id, "eval_name": "parts_agent", "backend": "linkup",
                               "created_at": datetime.now(timezone.utc).isoformat(), **res}, indent=1),
                   encoding="utf-8")
    for k, v in res.items():
        print(f"{k:<22} {v:.2f}" if isinstance(v, float) else f"{k:<22} {v}")


if __name__ == "__main__":
    main()
