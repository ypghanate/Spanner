"""Data shapes produced by the training pipeline.

`Procedure` is what lands in data/procedures/*.json. The brain (Pass 2) reads it as the
reference repair; its step fields match RepairStep in the plan (instruction, parts,
expected_visual_state, common_mistakes). Run `python -m training.pipeline schema`
to export the JSON Schema to data/procedures/schema.json for teammates.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SegmentLabel(BaseModel):
    """Claude's label for one ~10 s window of a repair video."""

    relevant: bool = Field(description="False if no repair work is visible (intro, talking head, cut-away, black frames).")
    step: str = Field(description="Short imperative name of the step being done, e.g. 'Lift chain onto smallest cog'. Empty if not relevant.")
    action: str = Field(description="The physical action in a few words, e.g. 'pushing derailleur back'.")
    tools: list[str] = Field(description="Tools visible or used, from the vocabulary when possible.")
    parts: list[str] = Field(description="Parts visible or touched, from the vocabulary when possible.")
    visual_state: str = Field(description="What the camera shows at the END of the window: the state of the object, concretely.")
    warnings: list[str] = Field(description="Mistakes, risks or safety notes shown or said in this window.")
    confidence: float = Field(description="0 to 1: how sure you are of this label.")


class ProcedureStep(BaseModel):
    index: int = Field(description="1-based step number.")
    title: str = Field(description="Short imperative title.")
    instruction: str = Field(description="One or two sentences telling a beginner exactly what to do.")
    parts: list[str]
    tools: list[str]
    expected_visual_state: str = Field(description="What the camera should see when this step is done. Concrete and checkable.")
    common_mistakes: list[str] = Field(description="Mistakes a beginner makes at this step, each detectable on camera if possible.")
    safety_notes: list[str]
    start_s: float = Field(description="Where this step starts in the source video, seconds.")
    end_s: float = Field(description="Where this step ends in the source video, seconds.")


class Procedure(BaseModel):
    object: str = Field(description="The object being repaired, e.g. 'road bike'.")
    fault: str = Field(description="The fault being fixed, e.g. 'chain came off the rear cog'.")
    tools_needed: list[str]
    parts: list[str]
    steps: list[ProcedureStep]
    notes: str = Field(description="Anything the steps don't capture: variations, tips, caveats.")


class WebExtraction(BaseModel):
    """Claude's reading of one web page found by Tavily."""

    is_repair_guide: bool = Field(description="True only if the page actually explains how to do this repair step by step.")
    reason: str = Field(description="One sentence: why it is or isn't a usable guide.")
    procedure: Procedure


class ProcedureRecord(BaseModel):
    """A Procedure plus where it came from. This is the file format in data/procedures/."""

    procedure_id: str
    repair_id: str
    source_kind: str = "video"  # video | web | canonical
    source_videos: list[str]   # source IDs: video IDs, or web page IDs for web procedures
    sources: list[dict]        # provenance: url, title, creator, licence
    keyframes: dict[str, str] = {}  # step index -> frame path relative to the repo
    procedure: Procedure
