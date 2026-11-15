"""The Claude-written repair planner: intake -> retrieve -> generate -> validate. No keys needed (fixtures)."""
from __future__ import annotations

import json

import pytest

from backend import planner
from backend.contracts import RepairPlan
from backend.planner import GenPlan, GenStep, Intake


def _step(kind, **kw):
    return GenStep(title=kind, instruction=f"Do {kind}.", kind=kind, parts=["keycap"],
                   expected_visual_state="Something visible.", **kw)


def _gen(*kinds) -> GenPlan:
    return GenPlan(fault="f", diagnosis="d", difficulty="easy", time_min=5, steps=[_step(k) for k in kinds])


INTAKE = Intake(in_scope=True, fault_class="dead_key", target_key="F")


def test_mock_plans_are_all_valid():
    plans = json.loads(planner.MOCK_PLANS.read_text(encoding="utf-8"))
    for fault in plans:
        intake = Intake(in_scope=True, fault_class=fault, target_key="G")
        assert planner.validate(planner._generate_mock(intake), intake) == [], fault


def test_validator_catches_ordering_and_reassembly():
    ok = _gen("cable-out", "keycap-pull", "switch-pull", "switch-insert", "keycap-place", "cable-in-test")
    assert planner.validate(ok, INTAKE) == []
    assert any("keycap-pull" in e for e in planner.validate(_gen("cable-out", "switch-pull", "switch-insert", "cable-in-test"), INTAKE))
    assert any("never plugged back in" in e for e in planner.validate(
        _gen("cable-out", "keycap-pull", "keycap-place", "other"), INTAKE))
    assert any("unplug" in e for e in planner.validate(
        _gen("keycap-pull", "socket-inspect", "keycap-place", "other"), INTAKE))
    assert any("steps" in e for e in planner.validate(_gen("cable-out", "cable-in-test"), INTAKE))
    bad = _gen("cable-out", "keycap-pull", "keycap-place", "cable-in-test")
    bad.steps[1].expected_visual_state = " "
    assert any("expected_visual_state" in e for e in planner.validate(bad, INTAKE))


@pytest.mark.parametrize("said,fault,key", [
    ("my space bar rattles", "spacebar_stabilizer", "SPACE"),
    ("the F key doesn't work", "dead_key", "F"),
    ("my e key is sticky", "sticky_key", "E"),
    ("the keyboard won't charge", "no_power", ""),
])
def test_mock_intake(said, fault, key):
    i = planner._intake_mock(said)
    assert (i.fault_class, i.target_key) == (fault, key)


def test_different_complaints_give_different_plans():
    a = planner.plan_repair("my G key is dead", "s")
    b = planner.plan_repair("my space bar rattles", "s")
    assert a["status"] == b["status"] == "ready"
    assert a["broken_key"] == "G" and b["broken_key"] == "SPACE"
    ka = [s["kind"] for s in a["plan"]["steps"]]
    kb = [s["kind"] for s in b["plan"]["steps"]]
    assert ka != kb and "switch-pull" in ka and "stabilizer" in kb
    RepairPlan.model_validate(a["plan"])
    # the 3D commands follow the key the user named
    flat = json.dumps(a["plan"]["steps"])
    assert "keycap_G" in flat and "keycap_F" not in flat


def test_plan_steps_carry_scene_commands_and_kind():
    r = planner.plan_repair("the H key won't type", "s")
    for s in r["plan"]["steps"]:
        assert s["kind"] in planner.__dict__["Kind"].__args__
        assert s["scene3d"] or s["kind"] == "other"
    assert r["plan"]["generated"] and r["plan"]["target_key"] == "H" and r["plan"]["fault_signature"] == "dead-key-h"


def test_clarify_when_the_key_is_missing():
    r = planner.plan_repair("one of my keys is dead", "s")
    assert r["status"] == "clarify" and "key" in r["say"].lower()
    assert planner.plan_repair("   ", "s")["status"] == "clarify"


def test_unsafe_requests_are_refused():
    r = planner.plan_repair("my keyboard battery is swollen", "s")
    assert r["status"] == "refused"


def test_bad_claude_plan_is_retried_then_falls_back(monkeypatch):
    monkeypatch.setattr(planner, "_use_mock", lambda: False)
    monkeypatch.setattr(planner, "_intake_claude", lambda c: Intake(in_scope=True, fault_class="dead_key", target_key="F"))
    monkeypatch.setattr(planner, "retrieve", lambda *a, **k: [])
    calls = []

    def bad(intake, complaint, refs, errors):
        calls.append(errors)
        return _gen("switch-pull", "other", "other", "other")

    monkeypatch.setattr(planner, "_generate_claude", bad)
    monkeypatch.setattr(planner, "CACHE_DIR", planner.CACHE_DIR.parent / "plans_test_unused")
    r = planner.plan_repair("my F key is dead", "s")
    assert r["status"] == "fallback" and r["errors"]
    assert len(calls) == 2 and calls[0] is None and calls[1]        # second attempt was told what was wrong


def test_good_claude_plan_is_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(planner, "_use_mock", lambda: False)
    monkeypatch.setattr(planner, "CACHE_DIR", tmp_path / "plans")
    monkeypatch.setattr(planner, "_intake_claude", lambda c: Intake(in_scope=True, fault_class="sticky_key", target_key="E"))
    monkeypatch.setattr(planner, "retrieve", lambda *a, **k: [])
    n = {"gen": 0}

    def good(intake, complaint, refs, errors):
        n["gen"] += 1
        return planner._generate_mock(intake)

    monkeypatch.setattr(planner, "_generate_claude", good)
    first = planner.plan_repair("my E key is sticky", "s")
    second = planner.plan_repair("E key sticky again", "s")
    assert first["status"] == second["status"] == "ready"
    assert n["gen"] == 1 and second["cached"] is True
    assert (tmp_path / "plans" / "class-sticky_key.json").exists()


def test_retrieval_only_uses_rk61_procedures():
    refs = planner.retrieve(Intake(in_scope=True, fault_class="dead_key", target_key="F"), "the F key is dead")
    assert refs
    for r in refs:
        assert "sony" not in r["procedure_id"].lower()


# ---------------------------------------------------------------- the coach API around it
def test_generate_endpoint_switches_the_session_plan():
    from fastapi.testclient import TestClient

    from backend import coach
    from backend.main import create_app

    c = TestClient(create_app())
    default = c.get("/coach/plan", params={"session": "t1"}).json()
    assert not default["plan"].get("generated")
    r = c.post("/coach/plan/generate", json={"session": "t1", "complaint": "my space bar rattles"}).json()
    assert r["status"] == "ready" and r["broken_key"] == "SPACE"
    mine = c.get("/coach/plan", params={"session": "t1"}).json()
    assert mine["plan"]["generated"] and mine["plan"]["fault_signature"] == "spacebar-stabilizer-space"
    assert c.get("/coach/plan", params={"session": "other"}).json()["plan"] == default["plan"]   # other sessions untouched
    assert c.get("/coach/latest", params={"session": "t1"}).json()["plan_id"] == r["plan_id"]
    assert "SPACE" in coach._system(coach._demo("t1")) and "broken key is 'SPACE'" in coach._system(coach._demo("t1"))
    coach._plans.clear()


def test_generate_endpoint_clarify_and_refuse_keep_the_old_plan():
    from fastapi.testclient import TestClient

    from backend import coach
    from backend.main import create_app

    c = TestClient(create_app())
    assert c.post("/coach/plan/generate", json={"session": "t2", "complaint": "a key is dead"}).json()["status"] == "clarify"
    assert c.post("/coach/plan/generate", json={"session": "t2", "complaint": "the battery is swollen"}).json()["status"] == "refused"
    assert "t2" not in coach._plans


def test_class_plan_is_reused_for_any_key(monkeypatch, tmp_path):
    """One Claude call per fault class: the plan is written with {KEY} and every key reuses it instantly."""
    monkeypatch.setattr(planner, "_use_mock", lambda: False)
    monkeypatch.setattr(planner, "CACHE_DIR", tmp_path / "plans")
    monkeypatch.setattr(planner, "retrieve", lambda *a, **k: [])
    monkeypatch.setattr(planner, "_intake_claude", lambda c: (_ for _ in ()).throw(AssertionError("clear sentences skip Claude")))
    raw = json.loads(planner.MOCK_PLANS.read_text(encoding="utf-8"))["dead_key"]
    n = {"gen": 0}

    def gen(intake, complaint, refs, errors):
        n["gen"] += 1
        return GenPlan.model_validate(raw)       # still has the {KEY} placeholder, like the real model is asked to write

    monkeypatch.setattr(planner, "_generate_claude", gen)
    g = planner.plan_repair("my G key doesn't work", "s")
    h = planner.plan_repair("the H key is dead", "s")
    assert g["status"] == h["status"] == "ready" and n["gen"] == 1 and h["cached"]
    assert "keycap_H" in json.dumps(h["plan"]["steps"]) and "keycap_G" not in json.dumps(h["plan"]["steps"])
    assert "H" in h["plan"]["steps"][1]["instruction"] and "{KEY}" not in json.dumps(h["plan"])


def test_fast_intake_only_when_sure():
    assert planner._intake_fast("my space bar doesn't work").target_key == "SPACE"
    assert planner._intake_fast("one of my keys is dead") is None          # which key? ask Claude
    assert planner._intake_fast("my phone won't charge") is None           # not a keyboard
    assert planner._intake_fast("it's being weird") is None                # no fault words


# ---------------------------------------------------------------- spoken turns (phone mic -> reply + action)
def test_quick_commands_skip_claude():
    from backend import coach

    coach._view_state["q"] = coach.ViewState(session="q", screen="step", step=2, total=6)
    for said, action in [("next", "next"), ("ok next please", "next"), ("go back", "back"), ("stop", "stop"), ("repeat", "repeat")]:
        assert coach._quick_turn(said, "q").action == action
    assert coach._quick_turn("which way do I pull it", "q") is None          # a real question goes to Claude
    coach._view_state["q"] = coach.ViewState(session="q", screen="picking")
    assert coach._quick_turn("next", "q") is None                           # on the device question, Claude decides
    coach._view_state.clear()


def test_spoken_turn_context_has_plan_step_and_camera():
    from backend import coach

    coach._plans["c"] = {"plan": {"title": "Space stabilizer fix", "fault": "rattle", "diagnosis": "loose bar",
                                  "steps": [{"index": 1, "instruction": "Unplug it.", "expected_visual_state": "no cable",
                                             "common_mistakes": ["leaving it in"]}]}}
    coach._view_state["c"] = coach.ViewState(session="c", screen="step", step=1, total=1)
    coach._latest["c"] = {"view": {"what_i_see": "hands near the cable", "ts": __import__("time").time(), "mistake": ""}}
    assert "Space stabilizer fix" in coach._plan_text("c") and "Unplug it." in coach._plan_text("c")
    assert "Step 1 of 1" in coach._context("c") and "hands near the cable" in coach._context("c")
    for d in (coach._plans, coach._view_state, coach._latest):
        d.pop("c", None)


# ---------------------------------------------------------------- other devices (iPhone / iPad / headphones)
PHONE_PARTS = [{"name": n, "label": n.replace("_", " ").title(), "desc": f"the {n}", "group": "body"}
               for n in ("frame", "display", "battery", "logic_board", "usb_c", "screws")]


def test_device_plan_uses_only_the_devices_own_parts(monkeypatch, tmp_path):
    from backend import device_planner as dp

    def fake(complaint, device, parts, refs, errors):
        return dp.DevPlan(status="ready", title="iPhone 15 screen replacement", fault="cracked screen", diagnosis="Cracked glass.",
                          steps=[dp.DevStep(title="Power off", instruction="Switch it off.", expected_visual_state="Screen dark."),
                                 dp.DevStep(title="Open", instruction="Remove the screws and lift the display.", expected_visual_state="Display lifted.",
                                            focus=["screws", "display"], view="apart"),
                                 dp.DevStep(title="Test", instruction="Power on.", expected_visual_state="Screen works.", view="together")])

    monkeypatch.setattr(dp, "_generate_claude", fake)
    monkeypatch.setattr(dp, "CACHE_DIR", tmp_path / "plans")
    r = dp.plan_device_repair("my screen is cracked", "iphone15", PHONE_PARTS)
    assert r["status"] == "ready" and r["plan"]["device"] == "iphone15" and r["broken_key"] is None
    ops = [[c["op"] + ":" + (c.get("part") or "") for c in s["scene3d"]] for s in r["plan"]["steps"]]
    assert ops[1] == ["explode:", "highlight:screws", "pulse:display"] and ops[2] == ["reset:"]
    RepairPlan.model_validate(r["plan"])


def test_device_plan_rejects_invented_parts_then_retries(monkeypatch, tmp_path):
    from backend import device_planner as dp

    calls = []

    def fake(complaint, device, parts, refs, errors):
        calls.append(errors)
        part = "flux_capacitor" if not errors else "battery"
        return dp.DevPlan(status="ready", title="t", fault="f", diagnosis="d",
                          steps=[dp.DevStep(title=str(i), instruction="Do it.", expected_visual_state="Seen.", focus=[part]) for i in range(4)])

    monkeypatch.setattr(dp, "_generate_claude", fake)
    monkeypatch.setattr(dp, "CACHE_DIR", tmp_path / "plans")
    r = dp.plan_device_repair("battery drains", "iphone15", PHONE_PARTS)
    assert r["status"] == "ready" and len(calls) == 2 and "flux_capacitor" in calls[1][0]


def test_device_clarify_and_common_fault_classes(monkeypatch, tmp_path):
    from backend import device_planner as dp

    monkeypatch.setattr(dp, "_generate_claude", lambda *a: dp.DevPlan(status="clarify", say="What is it doing?"))
    monkeypatch.setattr(dp, "CACHE_DIR", tmp_path / "plans")
    assert dp.plan_device_repair("it's broken", "iphone15", PHONE_PARTS) == {"status": "clarify", "say": "What is it doing?"}
    assert dp.classify("iphone15", "my screen is cracked") == "screen" and dp.classify("momentum4", "the ear cushion is torn") == "cushion"
    assert dp.classify("momentum4", "my screen is cracked") is None


def test_device_endpoints_register_parts_and_switch_devices():
    from fastapi.testclient import TestClient

    from backend import coach
    from backend.main import create_app

    c = TestClient(create_app())
    assert c.post("/coach/device/parts", json={"session": "d1", "device": "iphone15", "parts": PHONE_PARTS}).json()["parts"] == 6
    r = c.post("/coach/plan/generate", json={"session": "d1", "complaint": "my screen is cracked", "device": "iphone15"}).json()
    assert r["status"] == "ready" and r["plan"]["device"] == "iphone15"
    assert "iphone15" in coach._context("d1") and "logic_board" in coach._context("d1")
    assert "iPhone" in coach._system(coach._demo("d1")) and "keyboard" not in coach._system(coach._demo("d1")).lower()
    c.post("/coach/device/parts", json={"session": "d1", "device": "ipad11", "parts": PHONE_PARTS})     # picked another device
    assert "d1" not in coach._plans
    coach._device_parts.pop("d1", None)


# ---------------------------------------------------------------- shopping: nothing hard-coded, only on request
def _fake_resolve(monkeypatch, **kw):
    from backend import shop

    calls = []
    monkeypatch.setattr(shop, "resolve", lambda text, session="": (calls.append(text), shop.ShopTarget(**kw))[1])
    return shop, calls


def test_shop_asks_instead_of_guessing(monkeypatch):
    shop, _ = _fake_resolve(monkeypatch, action="clarify", question="Which part: the battery or the screen?")
    monkeypatch.setattr(shop, "_search_parts", lambda *a: (_ for _ in ()).throw(AssertionError("must not search")))
    assert shop.shop("I want to order something", "s") == {"kind": "clarify", "say": "Which part: the battery or the screen?"}


def test_shop_searches_exactly_what_was_resolved_and_caches_it(monkeypatch, tmp_path):
    shop, calls = _fake_resolve(monkeypatch, action="part", product="Apple iPhone 15", part="replacement battery for the iPhone 15")
    monkeypatch.setattr(shop, "CACHE_DIR", tmp_path / "shop")
    shop._cache.clear()
    seen = []
    offer = {"name": "iPhone 15 battery", "retailer": "iFixit", "price": 39.0, "currency": "GBP", "url": "https://ifixit.example/b",
             "image_url": "https://img.example/b.jpg", "compatible": True, "compatibility": "Fits your device."}
    monkeypatch.setattr(shop, "_search_parts", lambda part, product: (seen.append((part, product)), ([dict(offer)], []))[1])
    monkeypatch.setattr(shop, "verified_status", lambda product, part: {"status": "researching"})
    first = shop.shop("order a new battery", "s")
    assert seen == [("replacement battery for the iPhone 15", "Apple iPhone 15")]
    assert first["kind"] == "part" and first["product"] == "Apple iPhone 15" and first["part"].startswith("replacement battery")
    second = shop.shop("how much is the battery", "s")                  # different words, same resolved part: cached
    assert second["cached"] is True and len(seen) == 1


def test_shop_alternatives_and_no_model_names_left():
    import inspect

    from backend import shop

    src = inspect.getsource(shop)
    for hard in ("Royal Kludge", "RK61", "PARTS = [", "def classify", "def prefetch", "WARM ="):
        assert hard not in src, hard
    assert "alternatives" in src


def test_nothing_is_searched_automatically_after_planning():
    from backend import coach

    src = open(coach.__file__, encoding="utf-8").read()
    assert "prefetch" not in src and "_warm_shop" not in src and "PART_FOR_FAULT" not in src


def test_shop_route_passes_the_session_and_the_clarifying_question(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import shop
    from backend.main import create_app

    got = {}
    monkeypatch.setattr(shop, "resolve", lambda text, session="": (got.update(text=text, session=session), shop.ShopTarget(action="clarify", question="Which part?"))[1])
    r = TestClient(create_app()).post("/coach/shop", json={"query": "order a part", "session": "abc"}).json()
    assert got == {"text": "order a part", "session": "abc"} and r == {"kind": "clarify", "say": "Which part?"}


# ---------------------------------------------------------------- the UK 13 A plug (demo repair: swapping its fuse)
PLUG_PARTS = [{"name": n, "label": n.replace("_", " ").title(), "desc": f"the {n}", "group": "plug"}
              for n in ("body", "fuse_door", "fuse_holder", "fuse", "live_pin", "neutral_pin", "earth_pin", "cable_boot", "cable")]


def test_plug_is_a_known_device_with_its_faults():
    from backend import device_planner as dp

    assert "mains plug" in dp.DEVICE_NAMES["plug13a"]
    assert dp.classify("plug13a", "the fuse in my plug has blown") == "fuse"
    assert dp.classify("plug13a", "the cable is loose") is None   # a moulded plug's cable is not a home repair


def test_a_plug_fuse_swap_is_not_refused_but_a_burnt_plug_is():
    from backend import planner

    for fine in ("the fuse in my plug has blown", "my kettle plug has no power, I think it's the fuse", "I need to swap a fuse in a mains plug"):
        assert not planner._PLUG_STOP.search(fine), fine
    for unsafe in ("the plug is scorched and melted", "it was sparking in the socket", "the plug is hot to the touch", "I got an electric shock"):
        r = planner.plan_repair(unsafe, "s", "plug13a", PLUG_PARTS)
        assert r["status"] == "refused", unsafe
    # the keyboard rule is unchanged: "mains" alone is still refused there
    assert planner.plan_repair("my keyboard is plugged into the mains and smoking", "s")["status"] == "refused"


def test_plug_plan_uses_plug_parts_and_starts_by_unplugging(monkeypatch, tmp_path):
    from backend import device_planner as dp

    def fake(complaint, device, parts, refs, errors):
        assert device == "plug13a"
        return dp.DevPlan(status="ready", title="Plug fuse replacement", fault="blown fuse", diagnosis="The fuse has blown.",
                          steps=[dp.DevStep(title="Unplug it", instruction="Pull the plug out of the wall.", expected_visual_state="The plug is in your hand, not in a socket."),
                                 dp.DevStep(title="Open", instruction="Prise the fuse door open with a flat screwdriver.", expected_visual_state="The fuse door is open.", focus=["fuse_door"], view="apart"),
                                 dp.DevStep(title="Swap the fuse", instruction="Pop out the old fuse and press in a new one of the same rating.", expected_visual_state="A new fuse sits in the clips.", focus=["fuse", "fuse_holder"]),
                                 dp.DevStep(title="Close", instruction="Press the fuse door shut until it clicks.", expected_visual_state="The fuse door is closed.", focus=["fuse_door"], view="together")])

    monkeypatch.setattr(dp, "_generate_claude", fake)
    monkeypatch.setattr(dp, "CACHE_DIR", tmp_path / "plans")
    r = dp.plan_device_repair("the fuse in my plug blew", "plug13a", PLUG_PARTS)
    assert r["status"] == "ready" and r["plan"]["device"] == "plug13a" and "mains plug" in r["plan"]["object"]
    assert [c["part"] for c in r["plan"]["steps"][2]["scene3d"] if c.get("part")] == ["fuse", "fuse_holder"]
    assert "plug" in r["plan"]["steps"][0]["instruction"].lower() and "unplug" in r["plan"]["steps"][0]["title"].lower()
    assert (tmp_path / "plans" / "device-plug13a-class-fuse.json").exists()


# ---------------------------------------------------------------- tap to talk (override) and the early mute
def _utterance_client(monkeypatch, turn):
    """The /coach/utterance route with speech-to-text and Claude replaced, so no keys are needed."""
    import types

    from fastapi.testclient import TestClient

    from backend import coach
    from backend.main import create_app
    from backend.voice import stt

    async def fake_stt(audio, filename, *a, **k):
        return types.SimpleNamespace(text="lift the old fuse out of the clips")

    monkeypatch.setattr(stt, "transcribe", fake_stt)
    seen = {}

    def fake_converse(text, session, forced=False):
        seen["forced"] = forced
        return turn.model_copy()

    monkeypatch.setattr(coach, "_converse", fake_converse)
    monkeypatch.setattr(coach, "_quick_turn", lambda *a, **k: None)
    monkeypatch.setattr(coach, "_warm_voice", lambda text: _noop())
    return TestClient(create_app()), coach, seen


async def _noop():
    return None


def test_a_tapped_utterance_is_always_addressed(monkeypatch):
    from backend.coach import Turn

    c, coach, seen = _utterance_client(monkeypatch, Turn(addressed=False))
    body = {"session": "tap1", "audio_b64": "AAAA", "forced": True}
    r = c.post("/coach/utterance", json=body).json()
    assert seen["forced"] is True
    assert r["reply"]["addressed"] is True and r["reply"]["forced"] is True
    assert coach._latest["tap1"]["reply"]["forced"] is True
    # untapped: Claude's own judgement stands
    r = c.post("/coach/utterance", json={"session": "tap2", "audio_b64": "AAAA"}).json()
    assert r["reply"]["addressed"] is False and r["reply"]["forced"] is False


def test_the_phone_is_told_spanner_is_speaking_as_soon_as_the_reply_exists(monkeypatch):
    import time

    from backend.coach import Turn

    c, coach, _ = _utterance_client(monkeypatch, Turn(addressed=True, say="Press the fuse door shut until it clicks."))
    c.post("/coach/utterance", json={"session": "mute1", "audio_b64": "AAAA"})
    assert coach._speaking["mute1"] > time.time()           # /coach/latest now says speaking: the phone mutes its mic
    assert c.get("/coach/latest", params={"session": "mute1"}).json()["speaking"] is True
    # no spoken reply -> nothing to mute for
    c2, coach2, _ = _utterance_client(monkeypatch, Turn(addressed=True, say="", action="next"))
    c2.post("/coach/utterance", json={"session": "mute2", "audio_b64": "AAAA"})
    assert coach2._speaking.get("mute2", 0) <= time.time()
