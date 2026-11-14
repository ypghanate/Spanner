"""The 'teacher' pass: Claude labels repair videos.

1. label_segment     frames + transcript slice of one window  -> SegmentLabel
2. build_procedure   all segment labels of one video           -> Procedure
3. merge_procedures  procedures from several training videos   -> one canonical Procedure

Every call uses structured outputs (Pydantic), so the result is always valid JSON.
Refusal fallbacks are on: if the model declines, the API retries on a fallback model.
`mock=True` returns fake labels with no API call, for testing the pipeline without a key.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from training.schemas import Procedure, ProcedureStep, SegmentLabel, WebExtraction

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LabelError(RuntimeError):
    pass


def _vocab(cfg: dict) -> str:
    parts = ", ".join(cfg.get("parts") or []) or "(none given, name parts plainly)"
    tools = ", ".join(cfg.get("tools") or []) or "(none given, name tools plainly)"
    return f"Part vocabulary: {parts}\nTool vocabulary: {tools}"


def _system(cfg: dict) -> str:
    return (
        "You label repair videos to build training data for Spanner, an assistant that guides "
        "beginners through repairs from a live phone camera.\n"
        f"The repair: {cfg['object']}, fault: {cfg['fault']}.\n"
        f"{_vocab(cfg)}\n"
        "Use vocabulary names exactly when they fit; otherwise use the plainest common name. "
        "Describe only what is visible in the frames or said in the transcript. If unsure, say so "
        "through a lower confidence rather than guessing. Visual states must be concrete enough "
        "that someone could check them from a single photo."
    )


def _ask(client, cfg: dict, content: list | str, schema):
    resp = client.beta.messages.parse(
        model=cfg["label_model"],
        max_tokens=16000,
        system=_system(cfg),
        messages=[{"role": "user", "content": content}],
        output_format=schema,
        output_config={"effort": cfg.get("effort", "medium")},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        raise LabelError(f"Model declined (request {resp._request_id})")
    if resp.parsed_output is None:
        raise LabelError(f"No structured output (stop_reason={resp.stop_reason}, request {resp._request_id})")
    return resp.parsed_output


def _image_block(frame: Path) -> dict:
    data = base64.standard_b64encode(frame.read_bytes()).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def label_segment(client, cfg: dict, frames: list[tuple[float, Path]], transcript: str,
                  start: float, end: float, mock: bool = False) -> SegmentLabel:
    if mock:
        return SegmentLabel(relevant=True, step=f"Mock step at {start:.0f}s", action="mock action",
                            tools=[], parts=(cfg.get("parts") or ["part"])[:1],
                            visual_state=f"mock state at {end:.0f}s", warnings=[], confidence=0.5)
    content: list = []
    for t, frame in frames:
        content.append({"type": "text", "text": f"Frame at {t:.0f}s:"})
        content.append(_image_block(frame))
    content.append({"type": "text", "text": (
        f"These frames cover {start:.0f}s to {end:.0f}s of the video.\n"
        f"Transcript for this window: {transcript or '(nothing said)'}\n\n"
        "Label this window."
    )})
    return _ask(client, cfg, content, SegmentLabel)


def build_procedure(client, cfg: dict, video_id: str, segments: list[dict],
                    mock: bool = False) -> Procedure:
    """segments: [{"start": s, "end": s, "transcript": str, "label": SegmentLabel-dict}, ...]"""
    relevant = [s for s in segments if s["label"]["relevant"]]
    if mock:
        steps = [ProcedureStep(index=i + 1, title=s["label"]["step"], instruction="Mock instruction.",
                               parts=s["label"]["parts"], tools=s["label"]["tools"],
                               expected_visual_state=s["label"]["visual_state"], common_mistakes=[],
                               safety_notes=[], start_s=s["start"], end_s=s["end"])
                 for i, s in enumerate(relevant)]
        return Procedure(object=cfg["object"], fault=cfg["fault"], tools_needed=[],
                         parts=cfg.get("parts") or [], steps=steps, notes="mock")
    prompt = (
        f"Below are labels for consecutive windows of video '{video_id}', in order, with what was "
        "said in each window. Merge them into the ordered repair procedure shown in this video: "
        "combine windows that belong to the same step, drop irrelevant ones, and write each step "
        "for a beginner. Use the windows' times for start_s/end_s. Put any mistakes, risks or tips "
        "the video shows or mentions into common_mistakes / safety_notes of the right step.\n\n"
        + json.dumps(segments, indent=1)
    )
    return _ask(client, cfg, prompt, Procedure)


def procedure_from_web(client, cfg: dict, page: dict, mock: bool = False) -> WebExtraction:
    """Read one scraped web page and extract the repair procedure it describes."""
    if mock:
        step = ProcedureStep(index=1, title="Mock web step", instruction="Mock instruction.", parts=[],
                             tools=[], expected_visual_state="mock", common_mistakes=[], safety_notes=[],
                             start_s=0, end_s=0)
        return WebExtraction(is_repair_guide=True, reason="mock",
                             procedure=Procedure(object=cfg["object"], fault=cfg["fault"], tools_needed=[],
                                                 parts=[], steps=[step], notes="mock"))
    prompt = (
        f"Below is the text of a web page ({page['url']}, title: {page.get('title') or 'unknown'}). "
        "Decide whether it really explains how to do this repair. If it does, extract its procedure "
        "for a beginner: ordered steps, what the object should look like after each step (concrete, "
        "checkable from a photo), common mistakes and safety notes. Use 0 for start_s/end_s. If it "
        "doesn't, set is_repair_guide to false and return an empty steps list.\n\n"
        f"PAGE TEXT:\n{page['raw_content']}"
    )
    return _ask(client, cfg, prompt, WebExtraction)


def merge_procedures(client, cfg: dict, procedures: dict[str, Procedure],
                     mock: bool = False) -> Procedure:
    """Merge several videos' procedures into one canonical procedure for the repair."""
    if mock:
        return next(iter(procedures.values()))
    prompt = (
        "Below are the procedures extracted from several sources (repair videos and web guides) "
        "for the same repair. Write the "
        "single best canonical procedure: keep steps that most videos agree on, in the most common "
        "order; merge equivalent steps; keep every distinct common mistake and safety note; make "
        "expected_visual_state concrete. For start_s/end_s use 0 (they no longer refer to one video).\n\n"
        + json.dumps({vid: p.model_dump() for vid, p in procedures.items()}, indent=1)
    )
    return _ask(client, cfg, prompt, Procedure)
