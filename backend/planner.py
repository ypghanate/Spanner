"""Claude-written repair plans: what the user says they want fixed decides the steps.

    plan_repair("my space bar rattles", session) -> {"status": "ready", "plan": RepairPlan dict, "broken_key": "SPACE", ...}

Pipeline (all of it runs on fixtures when USE_MOCKS=1 or there is no ANTHROPIC_API_KEY):
  1. intake    Haiku turns the spoken complaint into {fault_class, target key} (or a clarifying question / refusal).
  2. retrieve  training.kb.search_steps over the RK61 knowledge base: real video + web procedures, each with sources.
  3. generate  Sonnet writes the steps. For every step it picks one of the hologram's animation kinds
               (training.scene3d.KIND_NAMES), so the viewer can play any plan it produces.
  4. validate  deterministic checks (parts, ordering, reassembly); one retry with the exact errors; else a fallback.
  5. cache     data/plans/<fault>-<key>.json, so a repeated fault (and the demo faults) is instant.

Prompts live in backend/brain/prompts/planner_*.md.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .config import get_settings
from .contracts import RepairPlan, RepairStep, SceneCommand, SourceRef

log = logging.getLogger("spanner.planner")

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = Path(__file__).parent / "brain" / "prompts"
MODEL_DIR = ROOT / "data" / "models" / "rk61"
CACHE_DIR = ROOT / "data" / "plans"
MOCK_PLANS = ROOT / "fixtures" / "planner" / "plans.json"

INTAKE_MODEL = os.getenv("PLANNER_INTAKE_MODEL", "claude-haiku-5-5")
PLANNER_MODEL = os.getenv("PLANNER_MODEL", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

FaultClass = Literal["dead_key", "sticky_key", "missing_keycap", "spacebar_stabilizer", "no_power", "backlight",
                     "other_keyboard_fault"]
TITLES = {"dead_key": "{K} key switch replacement", "sticky_key": "{K} key clean-up", "missing_keycap": "{K} keycap replacement",
          "spacebar_stabilizer": "{K} stabilizer fix", "no_power": "Power and cable check", "backlight": "Backlight check",
          "other_keyboard_fault": "Keyboard check"}
KEY_FAULTS = {"dead_key", "sticky_key", "missing_keycap"}          # cannot be planned without knowing which key
DEFAULT_KEY = {"spacebar_stabilizer": "SPACE"}
Kind = Literal["cable-out", "cable-in-test", "keycap-pull", "keycap-place", "new-switch-inspect", "switch-align",
               "switch-insert", "switch-test", "puller-place", "switch-pull", "socket-inspect", "stabilizer", "other"]

# kind -> kinds that must appear earlier (what the viewer's world state needs to look right)
NEEDS_BEFORE = {
    "puller-place": ["keycap-pull"], "switch-pull": ["keycap-pull"],
    "switch-align": ["switch-pull"], "switch-insert": ["switch-pull"], "cable-in-test": ["cable-out"],
}
NEEDS_CABLE_OUT = {"switch-pull", "socket-inspect"}                # never open a key with the keyboard plugged in
MIN_STEPS, MAX_STEPS = 4, 10
_PLUG_STOP = re.compile(r"\b(gas|boiler|high[- ]voltage|electric(ally)? shock|shocked|exposed live|scorch\w*|melt\w*|burn(t|ed|ing)|sparks?|sparking|hot to the touch)\b", re.I)
# brain/safety.py's pattern only catches "swollen battery" as a phrase; people say it many ways
_BATTERY_DANGER = re.compile(r"\b(swoll?en|swelling|bulging|puffed|puffy|smok(e|ing)|burning smell|caught fire|on fire|"
                             r"overheat\w*|spilled (liquid|water|coffee) .*(charg|plugged))\b", re.I)


# ---------------------------------------------------------------- Claude output shapes
class Intake(BaseModel):
    in_scope: bool = Field(description="False if this is not a keyboard fault Spanner can safely help with.")
    refusal_reason: str = Field(default="", description="One friendly spoken sentence, only when in_scope is false.")
    needs_clarification: bool = False
    question: str = Field(default="", description="One short spoken question, only when needs_clarification is true.")
    fault_class: FaultClass
    target_key: str = Field(default="", description="Layout id of the key (e.g. F, SPACE, ENTER), or empty.")
    symptom: str = Field(default="", description="The problem in one short sentence, plain words.")


class GenStep(BaseModel):
    title: str
    instruction: str
    kind: Kind
    parts: list[str] = Field(default_factory=list)
    expected_visual_state: str
    common_mistakes: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class GenPlan(BaseModel):
    fault: str
    diagnosis: str
    difficulty: Literal["easy", "medium", "hard"]
    time_min: int
    tools_needed: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    steps: list[GenStep]


# ---------------------------------------------------------------- data the prompts need
@lru_cache(maxsize=1)
def _pack() -> dict:
    return json.loads((MODEL_DIR / "pack.json").read_text(encoding="utf-8"))


def _key_label(key: str) -> str:
    return (_pack().get("keys", {}).get(key) or {}).get("label", key)


def _prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _parts_vocab() -> dict[str, str]:
    """lower-case -> canonical spelling, for the part words the viewer understands."""
    return {p.lower(): p for p in _pack().get("parts", {})}


@lru_cache(maxsize=1)
def _procedure_index() -> dict[str, dict]:
    """procedure_id -> {repair_id, sources} for every RK61 procedure in the knowledge base."""
    from .knowledge import to_source_ref

    out: dict[str, dict] = {}
    for p in (ROOT / "data" / "procedures").rglob("*.json"):
        if p.name == "schema.json":
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        text = f"{rec.get('repair_id', '')} {rec.get('procedure', {}).get('object', '')}".lower()
        if "rk61" not in text.replace(" ", ""):
            continue
        out[rec["procedure_id"]] = {
            "repair_id": rec.get("repair_id"), "kind": rec.get("source_kind", "video"),
            "sources": [to_source_ref(s) for s in rec.get("sources", []) if s.get("url")]}
    return out


def retrieve(intake: Intake, complaint: str, k: int = 12) -> list[dict]:
    """Knowledge-base steps relevant to this fault (RK61 only); padded with the canonical procedure."""
    from training.kb import search_steps

    idx = _procedure_index()
    query = f"{complaint} {intake.symptom} {intake.fault_class.replace('_', ' ')} keyboard {_key_label(intake.target_key)}"
    try:
        hits = search_steps(query, k=k * 2, repair_id=None)
    except Exception as e:                      # Databricks unreachable etc.: the plan can still be written
        log.warning("knowledge search failed (%s)", e)
        hits = []
    refs: list[dict] = []
    seen: set[str] = set()

    def add(h: dict) -> None:
        pid = h.get("procedure_id") or str(h.get("step_id", "")).split("#")[0]
        sid = h.get("step_id") or f"{pid}#{h.get('step_index')}"
        if pid in idx and sid not in seen and len(refs) < k:
            seen.add(sid)
            refs.append({"source_id": sid, "procedure_id": pid, "kind": idx[pid]["kind"], "title": h.get("title", ""),
                         "instruction": h.get("instruction", ""), "done_when": h.get("expected_visual_state", ""),
                         "mistakes": h.get("common_mistakes") or []})

    for h in hits:
        add(h)
    if len(refs) < 8:                           # sparse keyword match: add the canonical RK61 steps as general context
        for h in _canonical_steps():
            add(h)
    return refs


def _canonical_steps() -> list[dict]:
    from training.kb import current_repair_id

    path = ROOT / "data" / "procedures" / f"{current_repair_id()}.json"
    if not path.exists():
        return []
    rec = json.loads(path.read_text(encoding="utf-8"))
    return [{"procedure_id": rec["procedure_id"], "step_id": f"{rec['procedure_id']}#{s['index']}", "step_index": s["index"],
             "title": s["title"], "instruction": s["instruction"], "expected_visual_state": s["expected_visual_state"],
             "common_mistakes": s["common_mistakes"]} for s in rec["procedure"]["steps"]]


def _refs_text(refs: list[dict]) -> str:
    lines = []
    for r in refs:
        mist = "; ".join(r["mistakes"][:2])
        lines.append(f"[{r['source_id']}] ({r['kind']}) {r['title']}: {r['instruction'][:220]} "
                     f"| done when: {r['done_when'][:140]}" + (f" | mistakes: {mist[:160]}" if mist else ""))
    return "\n".join(lines) or "(no reference steps found: write the steps from general repair knowledge)"


# ---------------------------------------------------------------- Claude calls
@lru_cache(maxsize=1)
def _client():
    import anthropic

    return anthropic.Anthropic()


def _model_kwargs(model: str) -> dict:
    """Haiku is run with thinking off; the larger models keep their default and fall back on a safety refusal."""
    if "haiku" in model:
        return {"thinking": {"type": "disabled"}}
    return {"betas": [FALLBACK_BETA], "fallbacks": "default"}


def _intake_claude(complaint: str) -> Intake:
    system = _prompt("planner_intake.md").replace("{KEY_IDS}", ", ".join(_pack()["keys"]))
    resp = _client().beta.messages.parse(
        model=INTAKE_MODEL, max_tokens=700,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": complaint}],
        output_format=Intake, output_config={"effort": "low"}, **_model_kwargs(INTAKE_MODEL))
    if resp.parsed_output is None:
        raise RuntimeError(f"intake gave no answer (stop_reason={resp.stop_reason})")
    return resp.parsed_output


def _generate_claude(intake: Intake, complaint: str, refs: list[dict], errors: list[str] | None) -> GenPlan:
    system = _prompt("planner_steps.md").replace("{PARTS}", ", ".join(_pack()["parts"]))
    key = intake.target_key
    user = (f"Fault class: {intake.fault_class}\nSymptom: {intake.symptom}\n"
            "Key: the key is decided later (the plan is reused for every key). Wherever you name it, write exactly "
            "the token {KEY}, for example 'Pull the {KEY} keycap straight up'.\n" if key else
            f"Fault class: {intake.fault_class}\nSymptom: {intake.symptom}\nKey: none (not about one key)\n")
    user += f"The person said: {complaint!r}\n\nReference steps from the knowledge base:\n{_refs_text(refs)}"
    if errors:
        user += "\n\nYour previous plan was rejected. Fix exactly these problems and return the full plan again:\n" + \
                "\n".join(f"- {e}" for e in errors)
    resp = _client().beta.messages.parse(
        model=PLANNER_MODEL, max_tokens=8000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=GenPlan, output_config={"effort": "low"}, **_model_kwargs(PLANNER_MODEL))
    if resp.parsed_output is None:
        raise RuntimeError(f"planner gave no answer (stop_reason={resp.stop_reason})")
    return resp.parsed_output


# ---------------------------------------------------------------- mock (fixtures, no keys)
_MOCK_KEYWORDS = [
    ("spacebar_stabilizer", r"space ?bar|rattl|wobbl|stabili[sz]er|uneven|enter key|backspace"),
    ("missing_keycap", r"missing|lost|cracked|broken (key ?)?cap|keycap (is )?(gone|off|broken)|worn"),
    ("sticky_key", r"stick|sticky|gritty|spill|slow|stuck|spring"),
    ("no_power", r"power|charg|cable|usb|detect|not (turn|work).*on|won'?t (turn|start)"),
    ("backlight", r"light|led|rgb|glow|dim"),
    ("dead_key", r"dead|not (work|regist|typ)|doesn'?t (work|regist|type)|double|chatter|broken|switch"),
]
_KEY_WORDS = {"space bar": "SPACE", "spacebar": "SPACE", "space": "SPACE", "enter": "ENTER", "return": "ENTER",
              "backspace": "BACKSPACE", "escape": "ESC", "esc": "ESC", "tab": "TAB", "caps": "CAPS",
              "left shift": "LSHIFT", "right shift": "RSHIFT", "shift": "LSHIFT"}


def _intake_mock(complaint: str) -> Intake:
    t = complaint.lower()
    fault = next((c for c, rx in _MOCK_KEYWORDS if re.search(rx, t)), "dead_key")
    key = ""
    for word, kid in sorted(_KEY_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(word)}\b", t):
            key = kid
            break
    if not key:
        m = re.search(r"\b([a-z0-9])\s+key\b", t) or re.search(r"\bkey\s+([a-z0-9])\b", t)
        key = m.group(1).upper() if m and m.group(1) not in ("a",) else ""
    return Intake(in_scope=True, fault_class=fault, target_key=key, symptom=complaint.strip()[:140])


_OTHER_DEVICE = re.compile(r"\b(i ?phone|phone|ipad|tablet|laptop|mouse|head ?phones?|earbuds?|bike|bicycle|car|tv|monitor|printer)\b", re.I)


def _intake_fast(complaint: str) -> Intake | None:
    """The word rules, used instead of a Claude call when they are sure: a fault matched, the key found when the
    fault needs one, and no other device mentioned. Saves 2-9 s on a clear sentence."""
    t = complaint.lower()
    if _OTHER_DEVICE.search(t) or not any(re.search(rx, t) for _, rx in _MOCK_KEYWORDS):
        return None
    intake = _intake_mock(complaint)
    if intake.fault_class in KEY_FAULTS and intake.target_key not in _pack()["keys"]:
        return None
    return intake


def _template_path(fault_class: str) -> Path:
    """One plan per fault class, written with a {KEY} placeholder and reused for every key."""
    return CACHE_DIR / f"class-{fault_class}.json"


def _from_template(raw: str, key: str) -> GenPlan:
    return GenPlan.model_validate(json.loads(raw.replace("{KEY}", _key_label(key or "F"))))


def _generate_mock(intake: Intake) -> GenPlan:
    plans = json.loads(MOCK_PLANS.read_text(encoding="utf-8"))
    raw = json.dumps(plans[intake.fault_class]).replace("{KEY}", _key_label(intake.target_key or "F"))
    return GenPlan.model_validate(json.loads(raw))


# ---------------------------------------------------------------- validation
def validate(gen: GenPlan, intake: Intake) -> list[str]:
    """Everything the viewer, the webcam check and the user rely on. Returns human-readable problems."""
    errors: list[str] = []
    n = len(gen.steps)
    if not MIN_STEPS <= n <= MAX_STEPS:
        errors.append(f"the plan has {n} steps; write between {MIN_STEPS} and {MAX_STEPS}")
    kinds = [s.kind for s in gen.steps]
    for i, s in enumerate(gen.steps):
        before = kinds[:i]
        for need in NEEDS_BEFORE.get(s.kind, []):
            if need not in before:
                errors.append(f"step {i + 1} ({s.kind}) needs a {need} step before it")
        if s.kind in NEEDS_CABLE_OUT and "cable-out" not in before:
            errors.append(f"step {i + 1} ({s.kind}) opens the key: unplug the keyboard first with a cable-out step")
        if not s.instruction.strip():
            errors.append(f"step {i + 1} has no instruction")
        if not s.expected_visual_state.strip():
            errors.append(f"step {i + 1} has no expected_visual_state (what a webcam would see when it is done)")
    if "cable-out" in kinds and "cable-in-test" not in kinds[kinds.index("cable-out"):]:
        errors.append("the keyboard is unplugged but never plugged back in: finish with a cable-in-test step")
    if "keycap-pull" in kinds and "keycap-place" not in kinds[kinds.index("keycap-pull"):]:
        errors.append("a keycap is taken off but never put back: add a keycap-place step")
    if "switch-pull" in kinds and "switch-insert" not in kinds[kinds.index("switch-pull"):]:
        errors.append("a switch is pulled but no new switch is inserted: add switch-align and switch-insert steps")
    return errors


def _clean_parts(parts: list[str]) -> list[str]:
    vocab = _parts_vocab()
    out = []
    for p in parts:
        canon = vocab.get(p.strip().lower())
        if canon and canon not in out:
            out.append(canon)
    return out


# ---------------------------------------------------------------- assemble the plan the viewer + app consume
def _signature(intake: Intake) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{intake.fault_class}-{intake.target_key or 'none'}".lower()).strip("-")


def assemble(gen: GenPlan, intake: Intake, refs: list[dict]) -> RepairPlan:
    from training.scene3d import scene3d_for_kind

    idx = _procedure_index()
    ref_sources = {r["source_id"]: idx[r["procedure_id"]]["sources"] for r in refs}
    key = intake.target_key or DEFAULT_KEY.get(intake.fault_class, "")
    steps: list[RepairStep] = []
    plan_sources: list[SourceRef] = []
    for i, s in enumerate(gen.steps, 1):
        parts = _clean_parts(s.parts)
        step_sources: list[SourceRef] = []
        for sid in s.source_ids:
            for src in ref_sources.get(sid, [])[:2]:
                if src.url not in {x.url for x in step_sources}:
                    step_sources.append(src)
        for src in step_sources:
            if src.url not in {x.url for x in plan_sources}:
                plan_sources.append(src)
        steps.append(RepairStep(
            index=i, title=s.title.strip()[:40] or None, instruction=s.instruction.strip(), parts=parts, expected_visual_state=s.expected_visual_state.strip(),
            common_mistakes=[m.strip() for m in s.common_mistakes if m.strip()][:3],
            scene3d=[SceneCommand(**c) for c in scene3d_for_kind(s.kind, parts, key or "F")],
            sources=step_sources[:3], kind=s.kind))
    return RepairPlan(
        object="Mechanical keyboard", fault=gen.fault, difficulty=gen.difficulty,
        time_min=max(1, gen.time_min), tools_needed=gen.tools_needed, safety_notes=gen.safety_notes, steps=steps,
        sources=plan_sources[:6], generated=True, title=TITLES[intake.fault_class].format(K=_key_label(key or "F")),
        diagnosis=gen.diagnosis, target_key=key or None,
        fault_signature=_signature(intake))


def _wrap(plan: RepairPlan, intake: Intake, **extra) -> dict:
    card = None
    if intake.fault_class in ("dead_key", "sticky_key"):         # switches are the part you might need to buy
        try:
            from training.scene3d import latest_card_view

            card = latest_card_view()
        except Exception as e:
            log.warning("no parts card (%s)", e)
    plan_id = hashlib.sha1(f"{plan.fault_signature}{time.time()}".encode()).hexdigest()[:10]
    return {"status": "ready", "plan_id": plan_id, "broken_key": plan.target_key or "F", "plan": plan.model_dump(),
            "parts_card": card, "say": plan.diagnosis, "fault_class": intake.fault_class, **extra}


# ---------------------------------------------------------------- the entry point
def _use_mock() -> bool:
    return get_settings().use_mocks or not os.getenv("ANTHROPIC_API_KEY")


def plan_repair(complaint: str, session: str = "", device: str = "keyboard", device_parts: list[dict] | None = None) -> dict:
    """Blocking. Returns {"status": ready|clarify|refused, ...}; run it in a thread from async code.
    Other devices (iPhone, iPad, headphones) go to backend.device_planner with the parts their hologram reported."""
    t0 = time.perf_counter()
    complaint = complaint.strip()[:500]
    from .brain.safety import _STOP

    if not complaint:
        return {"status": "clarify", "say": "What would you like me to help you fix?"}
    # A plug fuse is a normal home repair when the plug is unplugged, so for it "mains" alone is not a reason to refuse;
    # gas, boilers, shocks, scorching, smoke and heat still are.
    stop = _PLUG_STOP.search(complaint) if device == "plug13a" else _STOP.search(complaint)
    if stop or _BATTERY_DANGER.search(complaint):
        return {"status": "refused", "say": "Stop there: that's not a safe home repair. Please call a qualified professional."}

    mock = _use_mock()
    if device != "keyboard":
        from .device_planner import plan_device_repair

        return plan_device_repair(complaint, device, device_parts or [], mock=mock)
    intake = _intake_mock(complaint) if mock else (_intake_fast(complaint) or _intake_claude(complaint))
    t_intake = time.perf_counter()
    if not intake.in_scope:
        return {"status": "refused", "say": intake.refusal_reason or "I can only help with keyboard repairs right now."}
    keys = _pack()["keys"]
    intake.target_key = intake.target_key.strip().upper() if intake.target_key.strip().upper() in keys else ""
    if not intake.target_key:
        intake.target_key = DEFAULT_KEY.get(intake.fault_class, "")
    if intake.needs_clarification or (intake.fault_class in KEY_FAULTS and not intake.target_key):
        return {"status": "clarify", "say": intake.question or "Which key is it?"}

    cache = _template_path(intake.fault_class)
    if not mock and cache.exists():                # written once per fault class, any key: no Claude call
        gen = _from_template(cache.read_text(encoding="utf-8"), intake.target_key)
        if not validate(gen, intake):
            plan = assemble(gen, intake, [])
            return _wrap(plan, intake, cached=True, mock=False,
                         timings={"intake_ms": round((t_intake - t0) * 1000), "total_ms": round((time.perf_counter() - t0) * 1000)})

    refs = retrieve(intake, complaint)
    errors: list[str] = []
    gen: GenPlan | None = None
    template = ""
    for attempt in range(2):
        if mock:
            gen = _generate_mock(intake)
        else:
            template = _generate_claude(intake, complaint, refs, errors or None).model_dump_json(indent=1)
            gen = _from_template(template, intake.target_key)
        errors = validate(gen, intake)
        if not errors:
            break
        log.warning("plan rejected (attempt %d): %s", attempt + 1, errors)
    t_gen = time.perf_counter()
    timings = {"intake_ms": round((t_intake - t0) * 1000), "generate_ms": round((t_gen - t_intake) * 1000),
               "total_ms": round((t_gen - t0) * 1000)}
    if errors or gen is None:                    # fall back to the canonical procedure, honestly labelled
        return {"status": "fallback", "say": "I couldn't write a custom plan for that, so here is the standard switch repair.",
                "errors": errors, "timings": timings}
    plan = assemble(gen, intake, refs)
    if not mock:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(template, encoding="utf-8")
    return _wrap(plan, intake, cached=False, mock=mock, timings=timings)


# one sentence per fault class: the class plans are written before anyone asks (coach.mount runs warm())
WARM = {"dead_key": "my F key doesn't work", "sticky_key": "my E key is sticky", "missing_keycap": "my G keycap is missing",
        "spacebar_stabilizer": "my space bar rattles", "no_power": "my keyboard won't power on",
        "backlight": "the keyboard backlight is dead", "other_keyboard_fault": "something is wrong with my keyboard"}


def warm() -> list[str]:
    """Write the plan for every fault class that has none yet (blocking; a few cents, once)."""
    done: list[str] = []
    if _use_mock():
        return done
    for fault, sentence in WARM.items():
        if _template_path(fault).exists():
            continue
        try:
            key = _intake_mock(sentence).target_key or DEFAULT_KEY.get(fault, "")
            intake = Intake(in_scope=True, fault_class=fault, target_key=key, symptom=sentence)
            refs, errors = retrieve(intake, sentence), None
            for _ in range(2):                         # same as a live request: one retry told exactly what was wrong
                raw = _generate_claude(intake, sentence, refs, errors).model_dump_json(indent=1)
                errors = validate(_from_template(raw, key), intake)
                if not errors:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    _template_path(fault).write_text(raw, encoding="utf-8")
                    done.append(fault)
                    break
            print(f"planner warm-up: {fault} {'ready' if not errors else 'failed: ' + '; '.join(errors)}", flush=True)
        except Exception as e:
            print(f"planner warm-up: {fault} failed: {e}", flush=True)
    return done
