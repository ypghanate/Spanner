"""Where a spoken request goes (training/viewer/route.js) and the server's keyboard guard."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_routing_scenarios():
    r = subprocess.run(["node", str(ROOT / "tests" / "route.test.mjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all scenarios pass" in r.stdout


def test_a_keyboard_request_never_becomes_an_object_interview():
    from backend.objects import Job, KEYBOARD_RE

    for text in ["my keyboard", "the J key is dead", "RK68 space bar rattles", "keycap fell off"]:
        assert KEYBOARD_RE.search(text), text
        job = Job("route-test", text)
        out = job.start(text)                     # no Claude call, no photo, no twin
        assert out["is_keyboard"] is True and out["say"] == ""
        assert job.phase["photo"]["status"] == "pending" and job.phase["twin"]["status"] == "pending"
    for text in ["AirPods Pro 2", "Dyson V8 won't charge", "monkey wrench", "Sony headphones"]:
        assert not KEYBOARD_RE.search(text), text
