"""The phone flashlight logic (training/viewer/torch.js) is plain JavaScript: run its scenario tests with node."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_torch_governor_scenarios():
    r = subprocess.run(["node", str(ROOT / "tests" / "torch.test.mjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all 10 scenarios pass" in r.stdout
