"""Second AutoScientist run for the step judge: balanced classes, real training settings.

Run 1 (bcb8e3a0) trained one epoch at lr 5e-5 on 1,436 rows (745 ours + 691 general) and stopped after
21 steps: 0.58 accuracy on the held-out set vs Claude Haiku 0.72, and it answered "in_progress" far too often
(the minority classes were 15% of the data). This run fixes both:
  - domain rows only (training/adaption/data/train.jsonl, 745), minority classes upsampled to the majority
  - LoRA, 3 epochs, lr 2e-4, cosine, 3% warmup; AutoScientist still searches around that for 2 iterations

    python training/adaption/relaunch.py            # build train_balanced.csv, upload, launch; state -> run_state.json (run2_*)
    python training/adaption/relaunch.py --status   # poll
Idempotent: the upload is named by content hash and the run by dataset id, so re-running never pays twice.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
from adaption import Adaption  # noqa: E402

DATA = ROOT / "training" / "adaption" / "data"
STATE = ROOT / "training" / "adaption" / "run_state.json"
MODEL = "Qwen/Qwen3.5-2B"
HYPER = {"training_type": "lora", "n_epochs": 3, "learning_rate": 2e-4, "batch_size": "max", "lora_r": 16, "lora_alpha": 32,
         "lora_dropout": 0.05, "lr_scheduler_type": "cosine", "warmup_ratio": 0.03, "min_lr_ratio": 0.1,
         "max_grad_norm": 1.0, "weight_decay": 0.0, "train_on_inputs": False}


def save(**kw) -> dict:
    s = json.loads(STATE.read_text()) if STATE.exists() else {}
    s.update(kw)
    s["updated"] = time.strftime("%H:%M:%S")
    STATE.write_text(json.dumps(s, indent=2))
    return s


def balanced_rows(seed: int = 7) -> list[dict]:
    rows = [json.loads(l) for l in (DATA / "train.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    by_class: dict[str, list[dict]] = {}
    for r in rows:
        sys_t, user, asst = (m["content"] for m in r["messages"])
        status = json.loads(asst)["status"]
        by_class.setdefault(status, []).append({"prompt": f"{sys_t}\n\n{user}", "completion": asst, "kind": status})
    target = max(len(v) for v in by_class.values())
    rng = random.Random(seed)
    out: list[dict] = []
    for status, items in sorted(by_class.items()):
        extra = [rng.choice(items) for _ in range(target - len(items))]      # upsample by resampling, never invent
        out += items + extra
    rng.shuffle(out)
    return out


def status(c: Adaption) -> None:
    s = json.loads(STATE.read_text())
    rid = s.get("run2_id")
    if not rid:
        print("no run2 yet")
        return
    r = c.autoscientist.get(rid)
    print(f"run2 {rid}: {r.status} iter {r.iterations_completed}/{r.max_iterations} best_win_rate {r.best_win_rate} "
          f"download {r.download_available} error {r.error}")


def main() -> None:
    c = Adaption()
    if "--status" in sys.argv:
        return status(c)
    rows = balanced_rows()
    print("balanced:", len(rows), dict(Counter(r["kind"] for r in rows)))
    path = DATA / "train_balanced.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["prompt", "completion", "kind"])
        w.writeheader()
        w.writerows(rows)
    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:10]
    s = json.loads(STATE.read_text()) if STATE.exists() else {}
    ds = s.get("run2_dataset") if s.get("run2_dataset_digest") == digest else None
    if not ds:
        up = c.datasets.upload_file(path, name=f"spanner-step-judge-balanced-{digest}", processing_mode="raw",
                                    column_mapping={"prompt": "prompt", "completion": "completion"})
        ds = getattr(up, "dataset_id", None) or getattr(up, "id", None)
        save(run2_dataset=ds, run2_dataset_digest=digest, run2_rows=len(rows))
        print("uploaded dataset", ds)
        c.datasets.wait_for_completion(ds, timeout=1800)
    st = c.datasets.get_status(ds)
    print("dataset:", st.status, "rows", st.row_count)
    r = c.autoscientist.create(dataset_id=ds, model=MODEL, training_method="instruction", hyperparams=HYPER,
                               max_iterations=2, target_win_rate=0.7, idempotency_key=f"spanner-judge-v2-{ds}")
    save(run2_id=r.id, run2_status=r.status, run2_model=MODEL, run2_hyperparams=HYPER, run2_started=time.strftime("%H:%M:%S"))
    print("run2", r.id, r.status)


if __name__ == "__main__":
    main()
