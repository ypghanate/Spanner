"""Before/after evaluation for Adaption: the fine-tuned step tracker vs Claude Haiku.

Runs every held-out example (training/adaption/data/eval.jsonl) through each backend and reports
accuracy, macro-F1, mistake recall and latency. Results go to MLflow (one run per backend) and to
data/eval/ so `databricks sync` puts them in bronze_eval_results for the dashboard.

  python -m training.eval_step_tracker                     # claude, plus adaption if configured
  python -m training.eval_step_tracker --backends rule     # offline check of the harness
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from training import tracking  # noqa: E402
from training.step_tracker import adaption_available, check_input  # noqa: E402

EVAL_FILE = ROOT / "training" / "adaption" / "data" / "eval.jsonl"
RESULTS = ROOT / "data" / "eval"
LABELS = ["not_started", "in_progress", "done", "error"]


def macro_f1(pairs: list[tuple[str, str]]) -> float:
    f1s = []
    for lab in LABELS:
        tp = sum(p == lab and t == lab for t, p in pairs)
        fp = sum(p == lab and t != lab for t, p in pairs)
        fn = sum(p != lab and t == lab for t, p in pairs)
        if tp + fp + fn:
            f1s.append(2 * tp / (2 * tp + fp + fn))
    return sum(f1s) / len(f1s) if f1s else 0.0


def evaluate(backend: str, rows: list[dict], workers: int) -> dict:
    def one(row):
        user = row["messages"][1]["content"]
        truth = json.loads(row["messages"][2]["content"])["status"]
        try:
            r = check_input(user, backend)
            return truth, r["status"], r["latency_ms"]
        except Exception:
            return truth, "failed", None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(one, rows))
    pairs = [(t, p) for t, p, _ in out]
    lat = sorted(l for *_, l in out if l is not None)
    errors = [(t, p) for t, p in pairs if t == "error"]
    return {
        "backend": backend, "n": len(pairs),
        "accuracy": sum(t == p for t, p in pairs) / len(pairs) if pairs else 0,
        "macro_f1": macro_f1(pairs),
        "mistake_recall": sum(p == "error" for _, p in errors) / len(errors) if errors else 0,
        "failures": sum(p == "failed" for _, p in pairs),
        "latency_p50_ms": statistics.median(lat) if lat else 0,
        "latency_p90_ms": lat[int(len(lat) * 0.9) - 1] if lat else 0,
    }


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--backends", nargs="*", help="claude, adaption, rule (default: claude + adaption if set)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="only the first N examples")
    a = ap.parse_args()
    if not EVAL_FILE.exists():
        sys.exit("No eval set. Run `python -m training.adaption_dataset` first.")
    rows = [json.loads(l) for l in EVAL_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = rows[: a.limit] if a.limit else rows
    backends = a.backends or (["claude", "adaption"] if adaption_available() else ["claude"])

    RESULTS.mkdir(parents=True, exist_ok=True)
    results = []
    for b in backends:
        print(f"evaluating {b} on {len(rows)} held-out examples...")
        res = evaluate(b, rows, a.workers)
        results.append(res)
        with tracking.run(f"step-tracker-eval-{b}", {"backend": b, "examples": len(rows)}) as mlf:
            mlf.log_metrics({k: v for k, v in res.items() if isinstance(v, (int, float))})
        run_id = uuid.uuid4().hex[:12]
        (RESULTS / f"step_tracker_{b}_{run_id}.json").write_text(json.dumps({
            "run_id": run_id, "eval_name": "step_tracker", "created_at": datetime.now(timezone.utc).isoformat(),
            **res}, indent=1), encoding="utf-8")

    print(f"\n{'backend':<10} {'acc':>6} {'F1':>6} {'mistake rec.':>13} {'p50 ms':>8} {'p90 ms':>8}")
    for r in results:
        print(f"{r['backend']:<10} {r['accuracy']:>6.2f} {r['macro_f1']:>6.2f} {r['mistake_recall']:>13.2f} "
              f"{r['latency_p50_ms']:>8.0f} {r['latency_p90_ms']:>8.0f}")


if __name__ == "__main__":
    main()
