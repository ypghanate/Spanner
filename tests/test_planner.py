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
    assert (tmp_path / "plans" / "sticky-key-e.json").exists()


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
