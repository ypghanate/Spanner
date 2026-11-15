"""Claude-written repair plans for the devices that are not the keyboard (iPhone 15, iPad, Momentum 4 headphones).

    plan_device_repair(complaint, device, parts) -> same shape as backend.planner.plan_repair

The device hologram page (training/viewer/device.js) reports its real parts (name, label, what it is) when it loads;
Claude writes the steps using only those parts, and each step carries 3D commands the page can play: take apart /
put together, highlight the part, fly the camera to it. One Claude call decides ready / clarify / refused and writes
the plan, so a new repair takes about 10-15 s; repeats come from data/plans/device-<hash>.json.

Prompt: backend/brain/prompts/planner_device.md.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .contracts import RepairPlan, RepairStep, SceneCommand, SourceRef

log = logging.getLogger("spanner.device_planner")

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "plans"
PROMPT = Path(__file__).parent / "brain" / "prompts" / "planner_device.md"

DEVICE_NAMES = {"iphone15": "Apple iPhone 15", "ipad11": "Apple iPad (11th generation)",
                "momentum4": "Sennheiser Momentum 4 wireless headphones",
                "plug13a": "UK 13 A moulded three-pin mains plug (BS 1363, sealed, no screws)"}
# which knowledge-base procedures are about which kind of device (their `object` text is matched against these)
KB_WORDS = {"iphone15": r"iphone", "ipad11": r"ipad|tablet", "momentum4": r"head ?phones?|wh-1000|momentum|ear ?cups?|earbuds?", "plug13a": r"\bplug\b|fuse|mains|13 ?a\b"}
MIN_STEPS, MAX_STEPS = 3, 10

# The faults people actually bring: one plan per (device, fault), written once (when the device page opens) and reused,
# so a common complaint is answered at once. Anything else is planned live.
_PHONE = [("screen", r"screen|display|crack|glass|touch|lcd|oled|shatter", "my screen is cracked"),
          ("battery", r"battery|drain|dies? (fast|quick)|power off", "the battery drains too fast"),
          ("charging", r"charg|port|usb|lightning|plug", "it won't charge"),
          ("sound", r"speaker|sound|audio|mic|volume|earpiece|crackl", "the speaker has no sound"),
          ("camera", r"camera|lens|photo|blurry", "the camera is blurry")]
FAULTS = {"iphone15": _PHONE, "ipad11": _PHONE,
          "momentum4": [("cushion", r"cushion|ear ?pads?|foam|leather|peel|torn|flak", "the ear cushion is torn"),
                        ("battery", r"battery|drain|dies? (fast|quick)", "the battery drains too fast"),
                        ("charging", r"charg|port|usb", "it won't charge"),
                        ("sound", r"sound|audio|driver|speaker|crackl|one side|noise|quiet", "one side has no sound"),
                        ("headband", r"headband|snap|hinge|yoke|broken band|creak", "the headband is cracked")],
          "plug13a": [("fuse", r"fuse|blown|blew|no power|dead|stopped working|not working|tripp", "the fuse in my plug has blown")]}   # a moulded plug has nothing else a person can repair


# A plug has one stock fault (a blown fuse) with one pre-written plan. That plan is only for that fault: "the kettle keeps tripping
# the fuse", a frayed cable, a cracked case, a plug that is warm or loose in the socket, or just "it is dead" are different
# problems (sometimes ones to stop at), so they are planned by the model from what the person actually said, never answered
# with the stock plan.
_PLUG_FUSE = re.compile(r"\bfuse\b|blown|blew", re.I)
_PLUG_SOMETHING_ELSE = re.compile(r"fray|bare|expos|crack|broke|bent|loose|wobbl|warm|hot|smell|burn|scorch|spark|melt|wet|water|damp|"
                                  r"keeps? (blowing|tripping)|keeps? going|trips?\b|tripp|again and again|every time|new fuse|rcd|breaker|"
                                  r"short|socket|wall|cable|cord|wire|pin\b", re.I)


def classify(device: str, complaint: str) -> str | None:
    t = complaint.lower()
    if device == "plug13a":
        return "fuse" if _PLUG_FUSE.search(t) and not _PLUG_SOMETHING_ELSE.search(t) else None
    for cls, rx, _ in FAULTS.get(device, []):
        if re.search(rx, t):
            return cls
    return None


class DevStep(BaseModel):
    title: str
    instruction: str
    expected_visual_state: str
    common_mistakes: list[str] = Field(default_factory=list)
    focus: list[str] = Field(default_factory=list, description="Exact part names this step works on, most important first.")
    view: Literal["keep", "apart", "together"] = "keep"
    source_ids: list[str] = Field(default_factory=list)


class DevPlan(BaseModel):
    status: Literal["ready", "clarify", "refused"]
    say: str = Field(default="", description="For clarify / refused: one short spoken sentence or question.")
    title: str = ""
    fault: str = ""
    diagnosis: str = ""
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    time_min: int = 20
    tools_needed: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    steps: list[DevStep] = Field(default_factory=list)


def _parts_text(parts: list[dict]) -> str:
    return "\n".join(f"- {p['name']}: {p.get('label', '')}: {p.get('desc', '')[:160]}" for p in parts)


def _retrieve(device: str, complaint: str, k: int = 8) -> list[dict]:
    """Reference steps from the knowledge base about this kind of device (only the headphones guide exists so far)."""
    from training.kb import search_steps

    from .knowledge import to_source_ref

    rx = re.compile(KB_WORDS.get(device, device), re.I)
    out: list[dict] = []
    try:
        hits = search_steps(f"{complaint} {DEVICE_NAMES.get(device, device)}", k=k * 3, repair_id=None)
    except Exception as e:
        log.warning("knowledge search failed (%s)", e)
        return out
    recs: dict[str, dict] = {}
    for p in (ROOT / "data" / "procedures").rglob("*.json"):
        if p.name != "schema.json":
            r = json.loads(p.read_text(encoding="utf-8"))
            recs[r["procedure_id"]] = r
    for h in hits:
        pid = h.get("procedure_id") or str(h.get("step_id", "")).split("#")[0]
        rec = recs.get(pid)
        if not rec or not rx.search(rec.get("procedure", {}).get("object", "") + " " + rec.get("repair_id", "")):
            continue
        out.append({"source_id": h.get("step_id") or f"{pid}#{h.get('step_index')}", "title": h.get("title", ""),
                    "instruction": h.get("instruction", ""), "done_when": h.get("expected_visual_state", ""),
                    "sources": [to_source_ref(s) for s in rec.get("sources", []) if s.get("url")]})
        if len(out) >= k:
            break
    return out


def _refs_text(refs: list[dict]) -> str:
    return "\n".join(f"[{r['source_id']}] {r['title']}: {r['instruction'][:220]} | done when: {r['done_when'][:140]}" for r in refs) \
        or "(no reference guide for this device: use your own repair knowledge)"


def _generate_claude(complaint: str, device: str, parts: list[dict], refs: list[dict], errors: list[str] | None) -> DevPlan:
    from .planner import PLANNER_MODEL, _client, _model_kwargs

    system = PROMPT.read_text(encoding="utf-8").replace("{DEVICE}", DEVICE_NAMES.get(device, device)).replace("{PARTS}", _parts_text(parts))
    user = f"The person said: {complaint!r}\n\nReference steps from repair guides:\n{_refs_text(refs)}"
    if errors:
        user += "\n\nYour previous plan was rejected. Fix exactly these problems and return the full plan again:\n" + \
                "\n".join(f"- {e}" for e in errors)
    resp = _client().beta.messages.parse(
        model=PLANNER_MODEL, max_tokens=8000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=DevPlan, output_config={"effort": "low"}, **_model_kwargs(PLANNER_MODEL))
    if resp.parsed_output is None:
        raise RuntimeError(f"device planner gave no answer (stop_reason={resp.stop_reason})")
    return resp.parsed_output


def _generate_mock(complaint: str, device: str, parts: list[dict]) -> DevPlan:
    """Deterministic plan from the part list (USE_MOCKS=1 / no key): power off, open up, look at the main parts, test."""
    names = [p["name"] for p in parts]
    main = next((n for n in ("display", "battery", "logic_board", "headband", "pcb") if n in names), names[0] if names else "")
    return DevPlan(status="ready", title=f"{DEVICE_NAMES.get(device, device)} repair", fault=complaint, diagnosis="Let's open it up and look.",
                   steps=[DevStep(title="Power off", instruction="Switch the device off and unplug anything attached.",
                                  expected_visual_state="The screen or lights are off and nothing is plugged in."),
                          DevStep(title="Open it up", instruction="Take the device apart carefully, keeping the screws in order.",
                                  expected_visual_state="The back or case is open and the parts are visible.", view="apart"),
                          DevStep(title="Check the part", instruction="Look closely at the part you suspect and check for damage.",
                                  expected_visual_state="The part is in view and nothing looks cracked or burnt.", focus=[main] if main else []),
                          DevStep(title="Put it back", instruction="Reassemble in the reverse order.",
                                  expected_visual_state="The device is closed with every screw back.", view="together"),
                          DevStep(title="Test it", instruction="Power it on and check that the fault is gone.",
                                  expected_visual_state="The device turns on and works normally.")])


def validate(gen: DevPlan, parts: list[dict]) -> list[str]:
    errors: list[str] = []
    names = {p["name"] for p in parts}
    n = len(gen.steps)
    if not MIN_STEPS <= n <= MAX_STEPS:
        errors.append(f"the plan has {n} steps; write between {MIN_STEPS} and {MAX_STEPS}")
    for i, s in enumerate(gen.steps, 1):
        if not s.instruction.strip():
            errors.append(f"step {i} has no instruction")
        if not s.expected_visual_state.strip():
            errors.append(f"step {i} has no expected_visual_state (what a phone camera would see when it is done)")
        bad = [f for f in s.focus if f not in names]
        if bad:
            errors.append(f"step {i} names parts that do not exist: {', '.join(bad)}. Use only: {', '.join(sorted(names))}")
    return errors


def _assemble(gen: DevPlan, device: str, parts: list[dict], refs: list[dict]) -> RepairPlan:
    ref_sources = {r["source_id"]: r["sources"] for r in refs}
    steps: list[RepairStep] = []
    plan_sources: list[SourceRef] = []
    for i, s in enumerate(gen.steps, 1):
        cmds: list[SceneCommand] = []
        if s.view == "apart":
            cmds.append(SceneCommand(op="explode", amount=1.0))
        elif s.view == "together":
            cmds.append(SceneCommand(op="reset"))
        for j, part in enumerate(s.focus[:3]):
            cmds.append(SceneCommand(op="highlight" if j == 0 else "pulse", part=part))
        step_sources: list[SourceRef] = []
        for sid in s.source_ids:
            for src in ref_sources.get(sid, [])[:2]:
                if src.url not in {x.url for x in step_sources}:
                    step_sources.append(src)
        for src in step_sources:
            if src.url not in {x.url for x in plan_sources}:
                plan_sources.append(src)
        steps.append(RepairStep(index=i, title=s.title.strip()[:40] or None, instruction=s.instruction.strip(), parts=list(s.focus),
                                expected_visual_state=s.expected_visual_state.strip(),
                                common_mistakes=[m.strip() for m in s.common_mistakes if m.strip()][:3],
                                scene3d=cmds, sources=step_sources[:3], kind="device"))
    sig = re.sub(r"[^a-z0-9]+", "-", f"{device}-{gen.title or gen.fault}".lower()).strip("-")[:60]
    return RepairPlan(object=DEVICE_NAMES.get(device, device), fault=gen.fault or gen.title, difficulty=gen.difficulty,
                      time_min=max(1, gen.time_min), tools_needed=gen.tools_needed, safety_notes=gen.safety_notes, steps=steps,
                      sources=plan_sources[:6], generated=True, title=gen.title or "Repair", diagnosis=gen.diagnosis,
                      target_key=None, fault_signature=sig, device=device)


def plan_device_repair(complaint: str, device: str, parts: list[dict], mock: bool = False) -> dict:
    """Blocking. Same return shape as backend.planner.plan_repair."""
    t0 = time.perf_counter()
    if not parts:
        return {"status": "clarify", "say": "Give me a moment to load the hologram, then tell me what's wrong."}
    cls = classify(device, complaint)
    h = hashlib.sha1(f"{device}|{re.sub(r'[^a-z0-9 ]', '', complaint.lower()).strip()}".encode()).hexdigest()[:12]
    cache = CACHE_DIR / (f"device-{device}-class-{cls}.json" if cls else f"device-{device}-{h}.json")
    refs: list[dict] = []
    gen: DevPlan | None = None
    if not mock and cache.exists():
        gen = DevPlan.model_validate_json(cache.read_text(encoding="utf-8"))
        if validate(gen, parts):
            gen = None
    cached = gen is not None
    if gen is None:
        if mock:
            gen = _generate_mock(complaint, device, parts)
        else:
            refs = _retrieve(device, complaint)
            errors: list[str] | None = None
            for attempt in range(2):
                gen = _generate_claude(complaint, device, parts, refs, errors)
                errors = validate(gen, parts) if gen.status == "ready" else []
                if not errors:
                    break
                log.warning("device plan rejected (attempt %d): %s", attempt + 1, errors)
            if errors:
                return {"status": "fallback", "say": "I couldn't write a plan for that. Can you describe the problem another way?",
                        "errors": errors}
    if gen.status != "ready":
        return {"status": gen.status, "say": gen.say or ("Could you tell me a bit more about what's wrong?" if gen.status == "clarify"
                                                          else "That isn't something I can safely help with at home.")}
    if not mock and not cached:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(gen.model_dump_json(indent=1), encoding="utf-8")
    plan = _assemble(gen, device, parts, refs)
    plan_id = hashlib.sha1(f"{plan.fault_signature}{time.time()}".encode()).hexdigest()[:10]
    return {"status": "ready", "plan_id": plan_id, "broken_key": None, "plan": plan.model_dump(), "parts_card": None,
            "say": plan.diagnosis, "fault_class": "device", "cached": cached, "mock": mock,
            "timings": {"total_ms": round((time.perf_counter() - t0) * 1000)}}


_warming: set[str] = set()


def warm(device: str, parts: list[dict]) -> list[str]:
    """Write the plan for each common fault of this device that has none yet (blocking; run it in a thread)."""
    from concurrent.futures import ThreadPoolExecutor

    from .planner import _use_mock

    if _use_mock() or device in _warming or not parts:
        return []
    _warming.add(device)
    todo = [(cls, sentence) for cls, _, sentence in FAULTS.get(device, [])
            if not (CACHE_DIR / f"device-{device}-class-{cls}.json").exists()]

    def one(item: tuple[str, str]) -> str | None:
        cls, sentence = item
        try:
            r = plan_device_repair(sentence, device, parts)
            print(f"device warm-up: {device}/{cls} {r['status']}", flush=True)
            return cls if r["status"] == "ready" else None
        except Exception as e:
            print(f"device warm-up: {device}/{cls} failed: {e}", flush=True)
            return None

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            return [c for c in pool.map(one, todo) if c]
    finally:
        _warming.discard(device)
