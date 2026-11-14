"""Build the Pass 1 fine-tuning dataset.

    uv run python training/make_dataset.py                 # estimate the API spend, change nothing
    uv run python training/make_dataset.py --yes           # generate (spends API money)

1. Scenarios: Claude Opus writes Pass 1 inputs (observations + speech) for slots whose intent,
   phase, object and fault are assigned here, so the gold intent and fault are known by construction.
2. Real calls: successful Claude Pass 1 calls logged by the running app are added to training.
3. Labels: the production Claude Pass 1 (the teacher) labels every synthetic input. Rows where the
   teacher's intent disagrees with the gold intent are dropped.
4. Split: 100 synthetic rows stratified over the six intents are held out for eval; the rest train.

Outputs (in --out-dir, default training/data): train.jsonl, eval.jsonl, eval_gold.jsonl.
Prompts come from backend.brain.pass1 (load_system_prompt / render_user), so the data matches
serving byte for byte. Generated scenarios and teacher labels are cached, so a rerun is free.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402
from pydantic import BaseModel, Field, ValidationError  # noqa: E402

from backend.brain import pass1  # noqa: E402
from backend.brain.pass1 import Pass1Input, StepBrief  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.contracts import SceneObservation, SituationReport, Transcript  # noqa: E402

INTENTS = ["start", "question", "step_done", "need_help", "stop", "none"]
GEN_PROMPT = Path(__file__).parent / "prompts" / "scenario_gen.md"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Faults come from a short fixed list per object so "fault match" is tractable.
OBJECTS: dict[str, list[str]] = {
    "bicycle rim brake": ["brake pad rubbing the rim", "brake pad worn down", "brake cable too slack", "brake pad touching the tyre"],
    "bicycle tyre": ["slow puncture in the inner tube", "tyre not seated on the rim", "valve leaking air"],
    "bicycle chain": ["chain skipping under load", "chain dry and noisy", "chain too loose"],
    "dripping kitchen tap": ["worn washer in the tap", "loose tap handle", "limescale blocking the aerator"],
    "wobbly chair": ["loose leg bolt", "cracked leg joint", "uneven leg length"],
    "squeaky door": ["dry hinge", "loose hinge screw", "door rubbing the frame"],
    "jammed zip": ["fabric caught in the zip teeth", "slider worn and loose", "teeth misaligned"],
    "laptop fan": ["dust blocking the fan", "fan screw loose and rattling", "fan connector loose"],
    "bathroom extractor fan": ["dust-clogged blades", "loose fan cover", "worn fan bearing"],
    "kettle": ["limescale on the heating element", "loose lid hinge", "worn base connector"],
    "skateboard": ["loose truck nut", "worn bearings", "wheel rubbing the deck"],
    "desk lamp": ["loose bulb socket", "frayed switch wire", "stiff arm joint"],
}


# --- slots ------------------------------------------------------------------

@dataclass
class Slot:
    id: str
    object: str
    fault: str
    fault_visible: bool
    intent: str
    phase: str
    n_obs: int
    garbled: bool
    confidence: str
    demo: bool

    @property
    def needs_step(self) -> bool:
        return self.phase in ("guiding", "verifying")

    @property
    def gold_fault(self) -> str | None:
        return self.fault if self.fault_visible else None

    def brief(self) -> dict:
        return {"slot_id": self.id, "object": self.object, "fault": self.fault,
                "fault_visible": self.fault_visible, "intent": self.intent, "phase": self.phase,
                "n_obs": self.n_obs, "garbled": self.garbled, "confidence": self.confidence,
                "needs_current_step": self.needs_step, "needs_transcript": self.intent != "none"}


_PHASES = {
    "start": ["identifying", "identifying", "diagnosing"],
    "question": ["identifying", "diagnosing", "guiding", "guiding", "guiding"],
    "step_done": ["guiding", "guiding", "guiding", "verifying"],
    "need_help": ["guiding", "guiding", "diagnosing"],
    "stop": ["identifying", "diagnosing", "guiding", "verifying"],
    "none": ["identifying", "diagnosing", "guiding", "guiding", "verifying"],
}


def make_slots(n: int, seed: int, demo_object: str, demo_fault: str, demo_share: float = 0.4) -> list[Slot]:
    rng = random.Random(seed)
    others = [o for o in OBJECTS if o != demo_object]
    slots: list[Slot] = []
    for i in range(n):
        intent = INTENTS[i % len(INTENTS)]                      # balanced across intents
        demo = rng.random() < demo_share
        obj = demo_object if demo else rng.choice(others)
        fault = demo_fault if demo else rng.choice(OBJECTS[obj])
        phase = rng.choice(_PHASES[intent])
        s = Slot(id="", object=obj, fault=fault, fault_visible=rng.random() < 0.8, intent=intent, phase=phase,
                 n_obs=1 if phase == "identifying" and rng.random() < 0.6 else rng.choice([1, 2, 3]),
                 garbled=intent != "none" and rng.random() < 0.15,
                 confidence=rng.choice(["low", "mid", "high", "high"]), demo=demo)
        digest = hashlib.sha1(json.dumps(s.brief(), sort_keys=True).encode()).hexdigest()[:6]
        s.id = f"s{i:04d}-{digest}"                             # content hash: a changed slot never reuses a stale cache entry
        slots.append(s)
    rng.shuffle(slots)
    return slots


# --- scenario generation (Claude Opus) --------------------------------------

class ScenarioOut(BaseModel):
    slot_id: str
    observations: list[SceneObservation]
    transcript_text: str | None = None
    current_step: StepBrief | None = None
    previous_summary: str | None = None


class ScenarioBatch(BaseModel):
    scenarios: list[ScenarioOut] = Field(default_factory=list)


ScenarioFn = Callable[[list[Slot]], Awaitable[list[ScenarioOut]]]
TeacherFn = Callable[[Pass1Input], Awaitable[SituationReport]]


def make_opus_scenario_fn(per_call: int = 20, concurrency: int = 8) -> ScenarioFn:
    s = get_settings()
    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key or None, max_retries=2)
    system = GEN_PROMPT.read_text(encoding="utf-8").strip()
    sem = asyncio.Semaphore(concurrency)

    async def one(batch: list[Slot]) -> list[ScenarioOut]:
        async with sem:
            try:
                msg = await client.beta.messages.parse(
                    model=s.dataset_model,
                    max_tokens=16000,
                    system=system,
                    messages=[{"role": "user", "content":
                               "Slots:\n" + json.dumps([b.brief() for b in batch], indent=1)}],
                    output_format=ScenarioBatch,
                    output_config={"effort": "medium"},
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                    timeout=600,
                )
            except anthropic.APIError as e:
                print(f"  scenario batch failed ({type(e).__name__}: {e}); those slots are skipped", file=sys.stderr)
                return []
            if msg.stop_reason in ("refusal", "max_tokens") or msg.parsed_output is None:
                print(f"  scenario batch unusable (stop_reason={msg.stop_reason}); skipped", file=sys.stderr)
                return []
            return msg.parsed_output.scenarios

    async def run(slots: list[Slot]) -> list[ScenarioOut]:
        batches = [slots[i:i + per_call] for i in range(0, len(slots), per_call)]
        results = await asyncio.gather(*(one(b) for b in batches))
        return [sc for r in results for sc in r]

    return run


def build_input(slot: Slot, sc: ScenarioOut) -> Pass1Input | None:
    """Turn a generated scenario into a Pass1Input, or None if it doesn't fit its slot."""
    obs = list(sc.observations)[: slot.n_obs]
    if not obs:
        return None
    obs = [o.model_copy(update={"frame_id": f"{slot.id}-f{i + 1}"}) for i, o in enumerate(obs)]
    text = (sc.transcript_text or "").strip()
    if slot.intent == "none":
        transcript = None
    elif not text:
        return None
    else:
        transcript = Transcript(text=text, language="en", duration_s=round(max(1.0, 0.35 * len(text.split())), 1))
    if slot.needs_step and sc.current_step is None:
        return None
    return Pass1Input(phase=slot.phase, observations=obs, transcript=transcript,  # type: ignore[arg-type]
                      current_step=sc.current_step if slot.needs_step else None,
                      previous_summary=(sc.previous_summary or None))


# --- examples, cache, real calls --------------------------------------------

@dataclass
class Example:
    id: str
    source: str               # synthetic | real
    inp: Pass1Input
    report: SituationReport
    gold_intent: str | None = None
    gold_fault: str | None = None
    object: str | None = None


def input_key(inp: Pass1Input) -> str:
    return hashlib.sha256((pass1.load_system_prompt() + "\n" + pass1.render_user(inp)).encode()).hexdigest()


def messages_for(ex: Example) -> list[dict]:
    return [
        {"role": "system", "content": pass1.load_system_prompt()},
        {"role": "user", "content": pass1.render_user(ex.inp)},
        {"role": "assistant", "content": ex.report.model_dump_json()},
    ]


def to_format(messages: list[dict], fmt: str) -> dict:
    system, user, assistant = (m["content"] for m in messages)
    if fmt == "chat":
        return {"messages": messages}
    if fmt == "prompt_completion":
        return {"prompt": f"{system}\n\n{user}", "completion": assistant}
    if fmt == "alpaca":
        return {"instruction": system, "input": user, "output": assistant}
    raise ValueError(f"unknown format {fmt!r}")


class JsonlCache:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, str] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.data[row["key"]] = row["value"]

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def put(self, key: str, value: str) -> None:
        self.data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "value": value}) + "\n")


def load_real(raw_jsonl: Path, store_rows: list[dict] | None = None) -> list[Example]:
    """Successful Claude Pass 1 calls logged by the app (local file and, if given, the store)."""
    rows: list[dict] = []
    if raw_jsonl.exists():
        for line in raw_jsonl.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue                  # blank or half-written line (a crash mid-append): skip it
    rows += store_rows or []
    out: dict[str, Example] = {}
    for r in rows:
        if r.get("backend") != "claude" or not r.get("valid_json"):
            continue
        try:
            inp = Pass1Input.model_validate_json(r["input_json"])
            report = SituationReport.model_validate_json(r["output_json"])
        except (ValidationError, KeyError, ValueError):
            continue
        key = input_key(inp)
        out.setdefault(key, Example(id=f"real-{key[:10]}", source="real", inp=inp, report=report))
    return list(out.values())


# --- pipeline ---------------------------------------------------------------

async def build_examples(slots: list[Slot], scenario_fn: ScenarioFn, teacher_fn: TeacherFn, cache_dir: Path,
                         concurrency: int = 8) -> tuple[list[Example], Counter]:
    stats: Counter = Counter()
    scen_cache = JsonlCache(cache_dir / "scenarios.jsonl")
    teach_cache = JsonlCache(cache_dir / "teacher.jsonl")

    missing = [s for s in slots if scen_cache.get(s.id) is None]
    stats["scenarios_cached"] = len(slots) - len(missing)
    if missing:
        for sc in await scenario_fn(missing):
            scen_cache.put(sc.slot_id, sc.model_dump_json())
        stats["scenarios_generated"] = sum(1 for s in missing if scen_cache.get(s.id) is not None)

    sem = asyncio.Semaphore(concurrency)

    async def label(slot: Slot) -> Example | None:
        raw = scen_cache.get(slot.id)
        if raw is None:
            stats["dropped_no_scenario"] += 1
            return None
        inp = build_input(slot, ScenarioOut.model_validate_json(raw))
        if inp is None:
            stats["dropped_bad_scenario"] += 1
            return None
        key = input_key(inp)
        cached = teach_cache.get(key)
        if cached is not None:
            report = SituationReport.model_validate_json(cached)
            stats["teacher_cached"] += 1
        else:
            async with sem:
                try:
                    report = await teacher_fn(inp)
                except pass1.Pass1Error as e:
                    stats["dropped_teacher_error"] += 1
                    print(f"  teacher failed on {slot.id}: {e}", file=sys.stderr)
                    return None
            teach_cache.put(key, report.model_dump_json())
            stats["teacher_calls"] += 1
        if report.user_intent != slot.intent:
            stats["dropped_intent_mismatch"] += 1
            return None
        return Example(id=slot.id, source="synthetic", inp=inp, report=report, gold_intent=slot.intent,
                       gold_fault=slot.gold_fault, object=slot.object)

    examples = [e for e in await asyncio.gather(*(label(s) for s in slots)) if e is not None]
    return examples, stats


def split(examples: list[Example], real: list[Example], eval_size: int, seed: int) -> tuple[list[Example], list[Example]]:
    """Eval: `eval_size` synthetic rows, stratified evenly over the intents. Train: the rest + real."""
    rng = random.Random(seed)
    by_intent: dict[str, list[Example]] = defaultdict(list)
    for e in examples:
        by_intent[e.gold_intent or ""].append(e)
    for rows in by_intent.values():
        rng.shuffle(rows)
    base, extra = divmod(eval_size, len(INTENTS))
    eval_rows: list[Example] = []
    for i, intent in enumerate(INTENTS):
        want = base + (1 if i < extra else 0)
        pool = by_intent.get(intent, [])
        if len(pool) < want:
            print(f"  warning: only {len(pool)} usable '{intent}' rows for an eval quota of {want}", file=sys.stderr)
        eval_rows += pool[:want]
    eval_ids = {e.id for e in eval_rows}
    train = [e for e in examples if e.id not in eval_ids]
    eval_keys = {input_key(e.inp) for e in eval_rows}
    train += [r for r in real if input_key(r.inp) not in eval_keys]
    rng.shuffle(train)
    return train, eval_rows


def write_outputs(out_dir: Path, train: list[Example], eval_rows: list[Example], fmt: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "train.jsonl").open("w", encoding="utf-8") as f:
        for e in train:
            f.write(json.dumps(to_format(messages_for(e), fmt), ensure_ascii=False) + "\n")
    with (out_dir / "eval.jsonl").open("w", encoding="utf-8") as f:
        for e in eval_rows:
            f.write(json.dumps(to_format(messages_for(e), fmt), ensure_ascii=False) + "\n")
    with (out_dir / "eval_gold.jsonl").open("w", encoding="utf-8") as f:
        for e in eval_rows:
            f.write(json.dumps({"id": e.id, "intent": e.gold_intent, "fault": e.gold_fault, "object": e.object,
                                "input": e.inp.model_dump()}, ensure_ascii=False) + "\n")


def estimate_cost(n_scenario_calls: int, n_teacher_calls: int) -> float:
    """Rough dollars: Opus 5.5 ($4 / $20 per MTok) ~2.5k in + ~9k out per batch; Haiku 5.5 ($0.10 / $0.50) ~1.4k in + 0.25k out."""
    opus = n_scenario_calls * (2_500 * 4 + 9_000 * 20) / 1e6
    haiku = n_teacher_calls * (1_400 * 0.10 + 250 * 0.50) / 1e6
    return opus + haiku


def report_counts(train: list[Example], eval_rows: list[Example], stats: Counter) -> None:
    print("\nstats:", dict(stats))
    for name, rows in (("train", train), ("eval", eval_rows)):
        intents = Counter((e.report.user_intent) for e in rows)
        sources = Counter(e.source for e in rows)
        print(f"{name}: {len(rows)} rows | by intent {dict(sorted(intents.items()))} | by source {dict(sources)}")


async def run(args: argparse.Namespace, scenario_fn: ScenarioFn | None = None,
              teacher_fn: TeacherFn | None = None, store_rows: list[dict] | None = None) -> int:
    s = get_settings()
    slots = make_slots(args.n, args.seed, args.demo_object, args.demo_fault)
    cache_dir = args.out_dir / "cache"
    scen_cache = JsonlCache(cache_dir / "scenarios.jsonl")
    todo_slots = [x for x in slots if scen_cache.get(x.id) is None]
    scen_calls = -(-len(todo_slots) // args.per_call)
    est = estimate_cost(scen_calls, len(slots))
    print(f"{len(slots)} slots ({sum(x.demo for x in slots)} on the demo object), {len(todo_slots)} not cached.")
    print(f"Will call {s.dataset_model} ~{scen_calls}x and {s.pass1_model} up to {len(slots)}x "
          f"(rough cost ~${est:.2f}; cached work is free).")
    if not args.yes and (scenario_fn is None or teacher_fn is None):
        print("Nothing was called. Re-run with --yes to spend that.")
        return 0
    if (scenario_fn is None or teacher_fn is None) and not s.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 2

    scenario_fn = scenario_fn or make_opus_scenario_fn(args.per_call, args.concurrency)
    teacher_fn = teacher_fn or pass1.call_claude
    examples, stats = await build_examples(slots, scenario_fn, teacher_fn, cache_dir, args.concurrency)

    real: list[Example] = []
    if args.include_real:
        if store_rows is None:
            store_rows = _store_rows()
        real = load_real(s.training_data_dir / "raw" / "pass1_calls.jsonl", store_rows)
        stats["real_rows"] = len(real)

    train, eval_rows = split(examples, real, args.eval_size, args.seed)
    write_outputs(args.out_dir, train, eval_rows, args.format)
    report_counts(train, eval_rows, stats)
    print(f"\nwrote {args.out_dir}/train.jsonl, eval.jsonl, eval_gold.jsonl (format: {args.format})")
    if len(train) < 700:
        print(f"note: {len(train)} training rows (target ~700). Raise --n or rerun after more real calls.")
    return 0


def _store_rows() -> list[dict]:
    try:
        from backend import mocks
        return mocks.get_stages().store.read("pass1_calls", 10_000)
    except Exception:
        return []


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=900, help="synthetic slots (some are dropped by validation and the teacher check)")
    ap.add_argument("--eval-size", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--demo-object", default="bicycle rim brake")
    ap.add_argument("--demo-fault", default="brake pad rubbing the rim")
    ap.add_argument("--format", choices=["chat", "prompt_completion", "alpaca"], default="chat",
                    help="dataset shape Adaption expects (default: chat JSONL)")
    ap.add_argument("--out-dir", type=Path, default=get_settings().training_data_dir)
    ap.add_argument("--per-call", type=int, default=20, help="scenarios per Opus call")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--no-real", dest="include_real", action="store_false", help="skip logged real calls")
    ap.add_argument("--yes", action="store_true", help="actually call the API (otherwise only print the estimate)")
    return ap.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
