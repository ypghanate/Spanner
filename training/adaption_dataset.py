"""Build the Adaption fine-tuning dataset for the step tracker.

Real examples come from the labelled repair videos, with no hand-labelling:
for every 10 s window Claude labelled, and for the step it falls in plus its neighbours,
the status follows from the time ranges of the video's procedure:
  window before the step                -> not_started
  window inside the step                -> in_progress
  window after the step                 -> done
  window inside the step with a warning -> error (the warning is the mistake)
If there are few or no videos yet, `--synthetic N` adds Claude-written examples for every step of
the canonical procedure (marked source=synthetic). Adaptive Data then cleans and expands the set.

Split is BY VIDEO (test_videos in repair.json are held out), never by frame, so evaluation is honest.

  python -m training.adaption_dataset                 # real examples only
  python -m training.adaption_dataset --synthetic 3   # + 3 synthetic examples per step per status
Output: training/adaption/data/{train,eval}.jsonl (chat format) + dataset_card.md
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from training import label, tracking  # noqa: E402
from training.schemas import ProcedureRecord  # noqa: E402
from training.step_tracker import SYSTEM, StepStatus, describe_observation, make_input  # noqa: E402

DATA = ROOT / "data"
OUT = ROOT / "training" / "adaption" / "data"
STATUSES = ["not_started", "in_progress", "done", "error"]


def _status(a: float, b: float, s: float, e: float) -> str:
    if b <= s:
        return "not_started"
    if a >= e:
        return "done"
    inside_after_end = max(0.0, b - e)
    return "done" if inside_after_end > (b - a) / 2 else "in_progress"


def from_videos(cfg: dict) -> list[dict]:
    rows = []
    for rec_path in sorted((DATA / "procedures" / "videos").glob("*.json")):
        rec = ProcedureRecord.model_validate_json(rec_path.read_text(encoding="utf-8"))
        vid = rec.source_videos[0]
        seg_path = DATA / "work" / vid / "segments.json"
        if not seg_path.exists():
            continue
        steps = rec.procedure.steps
        for seg in json.loads(seg_path.read_text(encoding="utf-8")):
            lab = seg["label"]
            if not lab["relevant"]:
                continue
            a, b = seg["start"], seg["end"]
            overlap = [(min(b, st.end_s) - max(a, st.start_s), i) for i, st in enumerate(steps)]
            best = max(overlap, default=(0, None))
            cur = best[1] if best[0] > 0 else None
            idxs = {i for i in ([cur - 1, cur, cur + 1] if cur is not None else range(len(steps)))
                    if 0 <= i < len(steps)}
            obs = describe_observation(lab["visual_state"], lab["action"], lab["parts"])
            for i in sorted(idxs):
                st = steps[i]
                status = _status(a, b, st.start_s, st.end_s)
                mistake = ""
                if status == "in_progress" and lab["warnings"]:
                    status, mistake = "error", lab["warnings"][0]
                rows.append({"source": "video", "video_id": vid, "step_index": st.index,
                             "input": make_input(st.instruction, st.expected_visual_state, st.common_mistakes,
                                                 obs, seg["transcript"]),
                             "output": {"status": status, "mistake": mistake}})
    return rows


class SyntheticExample(BaseModel):
    observation: str
    said: str
    status: str
    mistake: str


class SyntheticSet(BaseModel):
    examples: list[SyntheticExample]


def synthetic(cfg: dict, per_status: int, mock: bool) -> list[dict]:
    canon = DATA / "procedures" / f"{cfg['repair_id']}.json"
    if not canon.exists():
        print("   no canonical procedure yet: run `python -m training.pipeline merge` first")
        return []
    rec = ProcedureRecord.model_validate_json(canon.read_text(encoding="utf-8"))
    client = None if mock else __import__("anthropic").Anthropic()
    rows = []
    for st in rec.procedure.steps:
        if mock:
            exs = [SyntheticExample(observation=f"mock view {s} {k}", said="", status=s,
                                    mistake="mock mistake" if s == "error" else "")
                   for s in STATUSES for k in range(per_status)]
        else:
            prompt = (
                f"Repair: {rec.procedure.object}, fault: {rec.procedure.fault}.\nStep {st.index}: {st.title}\n"
                f"Instruction: {st.instruction}\nDone looks like: {st.expected_visual_state}\n"
                f"Common mistakes: {st.common_mistakes}\n\n"
                f"Write {per_status} realistic examples for EACH status ({', '.join(STATUSES)}) of what a phone "
                "camera would show during this step, described the way a vision model would (1-2 sentences, "
                "concrete, varied hands/angles/lighting), plus what the user might say (often nothing). "
                "Make some cases hard (almost done, partly hidden, ambiguous). For error, use a realistic mistake.")
            exs = label._ask(client, cfg, prompt, SyntheticSet).examples
        for ex in exs:
            if ex.status not in STATUSES:
                continue
            rows.append({"source": "synthetic", "video_id": "", "step_index": st.index,
                         "input": make_input(st.instruction, st.expected_visual_state, st.common_mistakes,
                                             ex.observation, ex.said),
                         "output": StepStatus(status=ex.status,
                                              mistake=ex.mistake if ex.status == "error" else "").model_dump()})
    return rows


def to_chat(row: dict) -> dict:
    return {"messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": row["input"]},
                         {"role": "assistant", "content": json.dumps(row["output"])}],
            "metadata": {k: row[k] for k in ("source", "video_id", "step_index")}}


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="Build the Adaption step-tracker dataset")
    ap.add_argument("--synthetic", type=int, default=0, help="synthetic examples per step per status")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    cfg = json.loads((ROOT / "training" / "repair.json").read_text(encoding="utf-8"))
    test_videos = set(cfg.get("test_videos") or [])

    real = from_videos(cfg)
    synth = synthetic(cfg, a.synthetic, a.mock) if a.synthetic else []
    rng = random.Random(a.seed)
    train = [r for r in real if r["video_id"] not in test_videos]
    evals = [r for r in real if r["video_id"] in test_videos]
    if real and not evals:  # no held-out videos configured: hold out whole videos (~25%)
        vids = sorted({r["video_id"] for r in real})
        held = set(vids[-max(1, len(vids) // 4):]) if len(vids) > 1 else set()
        train = [r for r in real if r["video_id"] not in held]
        evals = [r for r in real if r["video_id"] in held]
    rng.shuffle(synth)
    cut = int(len(synth) * 0.8)
    train += synth[:cut]
    evals += synth[cut:]

    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in (("train", train), ("eval", evals)):
        (OUT / f"{name}.jsonl").write_text("".join(json.dumps(to_chat(r)) + "\n" for r in rows), encoding="utf-8")
    stats = {name: Counter(r["output"]["status"] for r in rows) for name, rows in (("train", train), ("eval", evals))}
    sources = Counter(r["source"] for r in train + evals)
    card = (
        f"# Spanner step-tracker dataset ({cfg['repair_id']})\n\n"
        "Task: given a repair step, what it looks like when done, its common mistakes, and what the camera "
        "shows now (plus speech), output the step status as JSON.\n\n"
        f"- Sources: {dict(sources)} (video = derived automatically from Claude-labelled repair videos and "
        "procedure time ranges; synthetic = Claude-written, marked)\n"
        f"- Train: {len(train)} {dict(stats['train'])}\n- Eval: {len(evals)} {dict(stats['eval'])} "
        "(held out by video)\n- Format: chat JSONL (system / user / assistant)\n"
        "- Built from Databricks-curated data (spanner.training) by training/adaption_dataset.py\n")
    (OUT / "dataset_card.md").write_text(card, encoding="utf-8")
    print(card)
    with tracking.run("adaption-dataset", {"repair_id": cfg["repair_id"], "synthetic_per_status": a.synthetic,
                                           "mock": a.mock}) as mlf:
        mlf.log_metrics({"train_examples": len(train), "eval_examples": len(evals),
                         "video_examples": sources.get("video", 0), "synthetic_examples": sources.get("synthetic", 0)})
        mlf.log_artifact(str(OUT / "dataset_card.md"), "dataset")


if __name__ == "__main__":
    main()
