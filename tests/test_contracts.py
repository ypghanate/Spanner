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


FILES = [f for f in sorted(OUT.glob("*.json")) if f.stem != "parts_card"]    # parts_card is a plain dict: tested below


def test_fixture_set_is_complete():
    names = {p.stem for p in OUT.glob("*.json")}
    expected = (
        {f"scene_observation_{i}" for i in (1, 2, 3)}
        | {f"transcript_{n}" for n in ("start", "question", "done")}
        | {f"situation_report_{n}" for n in ("start", "question", "step_done", "need_help", "none")}
        | {f"brain_response_{n}" for n in ("identifying", "diagnosing", "researching", "guiding", "alert", "done")}
        | {"repair_plan", "frame_response", "audio_response", "parts_card"}
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


def test_parts_card_fixture_has_the_shape_the_app_renders():
    card = json.loads((OUT / "parts_card.json").read_text(encoding="utf-8"))
    r = c.BrainResponse(phase="guiding", parts_card=card)                # fits the contract field
    assert r.parts_card["title"] == card["title"]
    assert {"card_id", "title", "model", "decision", "safety", "recall_checked", "options", "manual",
            "tutorial", "as_of", "sources_count"} <= set(card)
    assert card["options"] and all(
        {"retailer", "part", "price", "in_stock", "url", "verified", "badge", "why", "evidence"} <= set(o)
        for o in card["options"])
    assert {"verdict", "headline", "why", "sources"} <= set(card["decision"])


def test_brain_response_parts_card_defaults_to_none():
    assert c.BrainResponse(phase="guiding").parts_card is None


def test_socket_messages_validate_and_reject_garbage():
    assert c.LiveStart().tts == "url" and c.LiveStart(tts="stream").tts == "stream"
    assert c.LiveFrame(jpeg_b64="AAAA").type == "frame" and c.LiveFrame(jpeg_b64="AAAA", ts=1.5).ts == 1.5
    assert c.LiveUtterance(audio_b64="AAAA").format == "m4a"
    assert c.LivePlayback(state="start").state == "start"
    assert c.LiveResponse(response=c.BrainResponse(phase="guiding")).speak is True
    for bad in (lambda: c.LiveStart(tts="pigeon"), lambda: c.LivePlayback(state="pause"),
                lambda: c.LiveFrame(), lambda: c.LiveUtterance()):
        with pytest.raises(Exception):
            bad()


def test_gen_types_covers_the_socket_protocol_and_the_parts_card():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_types.py"), "--stdout"],
                         capture_output=True, text=True, cwd=ROOT, check=True).stdout
    assert "parts_card: Record<string, unknown> | null;" in out
    for name in ("LiveFrame", "LiveUtterance", "LiveAudio", "LivePlayback", "LiveFrameResult", "LiveEvent"):
        assert f"export interface {name} " in out, name
    assert "tts?: " in out and 'jpeg_b64: string;' in out and "ts?: number | null;" in out
    client = next(l for l in out.splitlines() if l.startswith("export type LiveClientMessage"))
    server = next(l for l in out.splitlines() if l.startswith("export type LiveServerMessage"))
    for name in ("LiveFrame", "LiveUtterance", "LiveAudio", "LivePlayback"):
        assert name in client, name
    for name in ("LiveFrameResult", "LiveEvent", "LiveResponse"):
        assert name in server, name
    assert "speak: boolean;" in out
