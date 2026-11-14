"""Every fixture validates against its contract; gen_types emits the shapes the app needs."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend import contracts as c

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures" / "outputs"

MODEL_FOR_PREFIX = {
    "scene_observation": c.SceneObservation,
    "transcript": c.Transcript,
    "situation_report": c.SituationReport,
    "repair_plan": c.RepairPlan,
    "brain_response": c.BrainResponse,
    "frame_response": c.FrameResponse,
    "audio_response": c.AudioResponse,
}


def _model(path: Path):
    for prefix, model in MODEL_FOR_PREFIX.items():
        if path.stem.startswith(prefix):
            return model
    raise AssertionError(f"no model registered for fixture {path.name}")


FILES = sorted(OUT.glob("*.json"))


def test_fixture_set_is_complete():
    names = {p.stem for p in FILES}
    expected = (
        {f"scene_observation_{i}" for i in (1, 2, 3)}
        | {f"transcript_{n}" for n in ("start", "question", "done")}
        | {f"situation_report_{n}" for n in ("start", "question", "step_done", "need_help", "none")}
        | {f"brain_response_{n}" for n in ("identifying", "diagnosing", "researching", "guiding", "alert", "done")}
        | {"repair_plan", "frame_response", "audio_response"}
    )
    assert expected <= names, expected - names


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_fixture_validates(path):
    _model(path).model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_situation_report_fixtures_cover_every_intent():
    intents = {c.SituationReport.model_validate_json(p.read_text(encoding="utf-8")).user_intent
               for p in OUT.glob("situation_report_*.json")}
    assert intents == {"start", "question", "step_done", "need_help", "none"}


def test_repair_plan_has_mistakes_and_sequential_steps():
    plan = c.RepairPlan.model_validate_json((OUT / "repair_plan.json").read_text(encoding="utf-8"))
    assert 4 <= len(plan.steps) <= 5
    assert [s.index for s in plan.steps] == list(range(1, len(plan.steps) + 1))
    assert all(s.common_mistakes for s in plan.steps)


def test_brain_response_say_is_two_sentences_or_less():
    import re
    for p in OUT.glob("brain_response_*.json"):
        r = c.BrainResponse.model_validate_json(p.read_text(encoding="utf-8"))
        assert len([s for s in re.split(r"[.!?]+\s", r.say.strip()) if s]) <= 2, p.name


def test_rejects_bad_values():
    with pytest.raises(Exception):
        c.SituationReport(summary="x", user_intent="shout", camera_sees="x", change_since_last="x", confidence=1)
    with pytest.raises(Exception):
        c.BrainResponse(phase="flying")


def test_gen_types_stdout_has_every_exported_model():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_types.py"), "--stdout"],
                         capture_output=True, text=True, cwd=ROOT, check=True).stdout
    assert "export interface BrainResponse" in out
    for model in c.EXPORTED_MODELS:
        assert f"export interface {model.__name__} " in out, model.__name__
    assert "tts_url: string | null;" in out
    assert "export type LiveClientMessage" in out and "export type LiveServerMessage" in out
    assert 'export type Phase = "identifying"' in out
    # fields with defaults the phone may omit are optional
    assert "keyterms?: string[];" in out
