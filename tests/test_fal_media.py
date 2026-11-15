"""fal "on your keyboard": the honest states, without calling fal (no key, no frame, limits, prompts)."""
from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from backend import fal_media
from backend import main as main_mod


@pytest.fixture
def client():
    fal_media._jobs.clear(); fal_media._count.clear()
    from backend.coach import _latest

    _latest.clear()
    with TestClient(main_mod.create_app()) as c:
        yield c


def test_disabled_without_key(client):
    r = client.post("/coach/visualize", json={"session": "t1", "step": 2, "key": "J", "label": "J"})
    assert r.status_code == 200 and r.json()["status"] == "disabled"


def test_waits_for_a_frame_and_never_calls_fal(client, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key-not-used"); monkeypatch.setenv("FAL_ENABLED", "1")
    started = []
    monkeypatch.setattr(fal_media.threading, "Thread", lambda **kw: started.append(kw) or types.SimpleNamespace(start=lambda: None))
    r = client.get("/coach/visualize", params={"session": "t2", "step": 2, "key": "J", "label": "J"})
    assert r.json()["status"] == "waiting_frame" and not started


def test_starts_once_a_frame_exists_and_is_idempotent(client, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key-not-used"); monkeypatch.setenv("FAL_ENABLED", "1")
    started = []
    monkeypatch.setattr(fal_media.threading, "Thread", lambda **kw: started.append(kw) or types.SimpleNamespace(start=lambda: None))
    view = types.SimpleNamespace(missing_keys=["J"], gap_x=0.5, gap_y=0.6)
    fal_media.remember_frame("t3", b"\xff\xd8jpeg", view)
    q = {"session": "t3", "step": 4, "key": "J", "label": "J"}
    a = client.post("/coach/visualize", json=q).json()
    b = client.get("/coach/visualize", params=q).json()
    assert a["status"] == b["status"] == "generating" and len(started) == 1
    job_args = started[0]["args"]
    assert job_args[5]["key"] == "J"            # the located gap travels with the job (for the ring)


def test_gap_of_another_key_is_not_used(client, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key-not-used"); monkeypatch.setenv("FAL_ENABLED", "1")
    started = []
    monkeypatch.setattr(fal_media.threading, "Thread", lambda **kw: started.append(kw) or types.SimpleNamespace(start=lambda: None))
    fal_media.remember_frame("t4", b"\xff\xd8jpeg", types.SimpleNamespace(missing_keys=["K"], gap_x=0.5, gap_y=0.5))
    client.post("/coach/visualize", json={"session": "t4", "step": 4, "key": "J", "label": "J"})
    assert started[0]["args"][5] is None


def test_session_limit(client, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key-not-used"); monkeypatch.setenv("FAL_ENABLED", "1")
    monkeypatch.setattr(fal_media, "MAX_JOBS", 1)
    monkeypatch.setattr(fal_media.threading, "Thread", lambda **kw: types.SimpleNamespace(start=lambda: None))
    fal_media.remember_frame("t5", b"\xff\xd8jpeg", types.SimpleNamespace(missing_keys=[], gap_x=None, gap_y=None))
    assert client.post("/coach/visualize", json={"session": "t5", "step": 1, "key": "J"}).json()["status"] == "generating"
    r = client.post("/coach/visualize", json={"session": "t5", "step": 2, "key": "J"}).json()
    assert r["status"] == "failed" and "limit" in r["error"]


def test_bad_step(client, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key-not-used"); monkeypatch.setenv("FAL_ENABLED", "1")
    assert client.post("/coach/visualize", json={"session": "t6", "step": 99, "key": "J"}).status_code == 400


def test_prompts_name_the_key_and_the_motion():
    s = {"index": 4, "kind": "switch-pull", "instruction": "Pull the switch straight up."}
    p = fal_media._edit_prompt(s, "J", True)
    assert "J key" in p and "magenta ring" in p and "Do not add any text" in p
    assert "magenta" not in fal_media._edit_prompt({**s, "kind": "cable-out"}, "J", False)
    assert "lifts the switch" in fal_media._video_prompt(s)
