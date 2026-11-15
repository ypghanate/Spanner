"""The plug repair: no crashed camera coach, no stock plan for a different problem, no mock when a real key is there."""
import importlib
import symtable
from pathlib import Path

import pytest

from tests.conftest import set_env

ROOT = Path(__file__).resolve().parents[1]


def _undefined_globals(module_name: str) -> list[str]:
    """Names a function reads as globals that do not exist in the module (the NameError a request hits at runtime).
    A bad merge once left backend/coach.py's /coach/frame handler using helpers that were gone: every camera frame was a 500
    and the whole suite stayed green, because no test sent a frame through a real plan."""
    mod = importlib.import_module(module_name)
    src = Path(mod.__file__).read_text(encoding="utf-8")
    missing: list[str] = []

    def walk(table: symtable.SymbolTable) -> None:
        if table.get_type() == "function":
            for sym in table.get_symbols():
                if sym.is_global() and sym.is_referenced() and not sym.is_assigned():
                    if not hasattr(mod, sym.get_name()) and not hasattr(__builtins__, sym.get_name()) \
                            and sym.get_name() not in __builtins__:
                        missing.append(f"{module_name}.{table.get_name()}: {sym.get_name()}")
        for child in table.get_children():
            walk(child)

    walk(symtable.symtable(src, mod.__file__, "exec"))
    return missing


@pytest.mark.parametrize("module", ["backend.coach", "backend.planner", "backend.device_planner", "backend.shop", "backend.brain.pass2"])
def test_no_function_reads_a_name_that_does_not_exist(module):
    assert _undefined_globals(module) == []


def test_frame_for_a_plug_plan_reaches_the_coach_and_returns_a_view(monkeypatch):
    """A camera frame for a plug repair goes through the real handler (only the Claude call is replaced)."""
    import asyncio

    from backend import coach

    session = "plug-frame-test"
    plan = {"device": "plug13a", "object": "UK 13 A plug", "fault": "blown fuse", "fault_signature": "plug-fuse", "generated": True,
            "steps": [{"index": 1, "instruction": "Unplug it.", "expected_visual_state": "Plug out of the socket.", "common_mistakes": []},
                      {"index": 2, "instruction": "Open the fuse door.", "expected_visual_state": "Door lifted, fuse visible.", "common_mistakes": []}]}
    coach._plans[session] = {"broken_key": None, "plan": plan, "parts_card": None, "plan_id": "p1"}
    view = coach.CoachView(object_visible=True, what_i_see="A plug with its fuse door open.", step_index=2, step_status="in_progress",
                           mistake="", confidence=0.9, say="Good, the door is open.")
    monkeypatch.setattr(coach, "_ask", lambda b64, step, sess: view)

    class Upload:
        async def read(self):
            return b"\xff\xd8\xff\xd9" * 50

    try:
        out = asyncio.run(coach.frame(image=Upload(), current_step=1, session=session))
    finally:
        coach._plans.pop(session, None)
    assert out["step_index"] == 2 and out["object_visible"] is True and out["say"] == "Good, the door is open."
    assert out["latency_ms"] >= 0


def test_the_stock_plan_is_only_for_a_plain_blown_fuse():
    from backend.device_planner import classify

    for text in ["the fuse in my plug has blown", "my fuse blew", "blown fuse"]:
        assert classify("plug13a", text) == "fuse", text
    for text in ["my kettle keeps tripping the fuse every time I plug it in", "the cable is frayed with bare wires showing and the plug is not working",
                 "my plug is dead", "plug stopped working", "the plug feels warm", "it wobbles in the socket", "the case is cracked"]:
        assert classify("plug13a", text) is None, text


def test_a_plug_complaint_that_is_not_the_stock_fault_is_planned_by_the_model_not_the_cache(monkeypatch, tmp_path):
    from backend import device_planner as dp

    seen = []

    def fake(complaint, device, parts, refs, errors):
        seen.append(complaint)
        return dp.DevPlan(status="refused", say="Keep swapping fuses and you hide a fault in the kettle: stop using it and have it checked.")

    monkeypatch.setattr(dp, "_generate_claude", fake)
    monkeypatch.setattr(dp, "_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(dp, "CACHE_DIR", tmp_path / "plans")
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "device-plug13a-class-fuse.json").write_text("{}", encoding="utf-8")   # a stock plan exists
    parts = [{"name": "fuse", "label": "Fuse", "desc": "", "group": "fuse"}]
    r = dp.plan_device_repair("my kettle keeps tripping the fuse every time", "plug13a", parts)
    assert r["status"] == "refused" and seen == ["my kettle keeps tripping the fuse every time"]


def test_devices_use_the_real_model_whenever_there_is_a_key_even_with_mocks_on(monkeypatch):
    from backend import planner

    set_env(monkeypatch, USE_MOCKS="1", ANTHROPIC_API_KEY="sk-test")
    assert planner._device_mock() is False         # USE_MOCKS defaults to on: it must not mock the plug at the demo
    set_env(monkeypatch, USE_MOCKS="1", ANTHROPIC_API_KEY="")
    assert planner._device_mock() is True          # no key at all (CI, offline): the mock keeps everything runnable


def test_a_failed_plan_for_a_plug_never_says_standard_switch_repair(monkeypatch):
    import asyncio

    from backend import coach

    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr("backend.planner.plan_repair", boom)
    out = asyncio.run(coach.generate_plan(coach.PlanReq(session="s", complaint="my plug is dead", device="plug13a")))
    assert out["status"] == "fallback" and "switch" not in out["say"].lower()
