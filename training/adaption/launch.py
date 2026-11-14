"""Launch the Adaption chain for the step judge: Adaptive Data clean -> augment -> AutoScientist.
Writes every id to training/adaption/run_state.json so `python training/adaption/launch.py --status` can poll.
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[2]; load_dotenv(ROOT / ".env")
from adaption import Adaption
STATE = ROOT / "training" / "adaption" / "run_state.json"
MODEL = "Qwen/Qwen3.5-2B"
CM = {"prompt": "prompt", "completion": "completion"}

def save(**kw):
    s = json.loads(STATE.read_text()) if STATE.exists() else {}
    s.update(kw); s["updated"] = time.strftime("%H:%M:%S"); STATE.write_text(json.dumps(s, indent=2)); return s

def main():
    c = Adaption()
    if "--status" in sys.argv:
        s = json.loads(STATE.read_text())
        if s.get("run_id"):
            r = c.autoscientist.get(s["run_id"])
            print(f"run {s['run_id']}: {r.status} iterations {r.iterations_completed}/{r.max_iterations} best_win_rate {r.best_win_rate} download {r.download_available} error {r.error}")
        else:
            print(s)
        return
    ds = (ROOT / "training/adaption/data/dataset_id.txt").read_text().strip()
    print("1/3 Adaptive Data: clean + rephrase", ds)
    run = c.datasets.adapt(ds, column_mapping=CM, training_type="instruction_dataset",
                           recipe_specification={"deduplication": True, "prompt_rephrase": True})
    rid = getattr(run, "run_id", None) or getattr(run, "id", None)
    adapted = getattr(run, "dataset_id", None) or ds
    save(source_dataset=ds, adapt_run=rid, adapted_dataset=adapted)
    print("   adapt run", rid, "-> dataset", adapted)
    c.datasets.wait_for_completion(adapted, timeout=3600)
    st = c.datasets.get_status(adapted); print("   adapted:", st.status, "rows", st.row_count)
    try:
        q = c.datasets.get_evaluation(adapted); print("   quality:", getattr(q, "quality", None) or q.status)
        save(quality=str(getattr(q, "quality", None)))
    except Exception as e: print("   quality n/a:", str(e)[:100])

    print("2/3 Augment: +600 domain, +400 general")
    aug = c.datasets.augment(adapted, domain_rows=600, general_rows=400, idempotency_key=f"spanner-aug-{adapted}")
    aug_ds = getattr(aug, "dataset_id", None) or adapted
    save(augmented_dataset=aug_ds); print("   augment ->", aug_ds, getattr(aug, "status", ""))
    c.datasets.wait_for_completion(aug_ds, timeout=3 * 3600)
    st = c.datasets.get_status(aug_ds); print("   augmented:", st.status, "rows", st.row_count)

    print("3/3 AutoScientist:", MODEL)
    r = c.autoscientist.create(dataset_id=aug_ds, model=MODEL, training_method="instruction", max_iterations=2,
                               column_mapping=CM, idempotency_key=f"spanner-judge-{aug_ds}")
    save(run_id=r.id, run_status=r.status, model=MODEL)
    print("   run", r.id, r.status)

if __name__ == "__main__":
    main()
