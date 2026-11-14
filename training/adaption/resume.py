"""Resume the Adaption chain after the clean step, without paying for it again.

    python training/adaption/resume.py            # wait for the clean -> augment -> AutoScientist
    python training/adaption/resume.py --train    # skip augment (dataset already >= 1000 rows) -> AutoScientist

Reads/writes training/adaption/run_state.json like launch.py. Augment and training use idempotency keys,
so running this twice never starts a second paid job.
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
    c = Adaption(); s = json.loads(STATE.read_text())
    ds = s.get("adapted_dataset") or s["source_dataset"]
    st = c.datasets.get_status(ds); print("clean:", st.status, "rows", st.row_count)
    if st.status not in ("succeeded", "ready", "completed"):
        print("waiting for the clean step..."); c.datasets.wait_for_completion(ds, timeout=3 * 3600)
        st = c.datasets.get_status(ds); print("clean:", st.status, "rows", st.row_count)
    try:
        q = c.datasets.get_evaluation(ds); save(quality=str(getattr(q, "quality", None))); print("quality:", getattr(q, "quality", None))
    except Exception as e:
        print("quality n/a:", str(e)[:100])
    train_ds = ds
    if "--train" not in sys.argv and (st.row_count or 0) < 1000:
        print("augment: +600 domain, +400 general")
        aug = c.datasets.augment(ds, domain_rows=600, general_rows=400, idempotency_key=f"spanner-aug-{ds}")
        train_ds = getattr(aug, "dataset_id", None) or ds; save(augmented_dataset=train_ds)
        c.datasets.wait_for_completion(train_ds, timeout=3 * 3600)
        st = c.datasets.get_status(train_ds); print("augmented:", st.status, "rows", st.row_count)
    print("AutoScientist:", MODEL)
    r = c.autoscientist.create(dataset_id=train_ds, model=MODEL, training_method="instruction", max_iterations=2,
                               column_mapping=CM, idempotency_key=f"spanner-judge-{train_ds}")
    save(run_id=r.id, run_status=r.status, model=MODEL); print("run", r.id, r.status)


if __name__ == "__main__":
    main()
