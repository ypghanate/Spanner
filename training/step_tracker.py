"""Step tracker: Spanner's most frequent live decision, the one Adaption makes fast.

Every few seconds the live loop asks: given what the camera sees and what the user said,
is the current step NOT STARTED, IN PROGRESS, DONE, or was there a MISTAKE?

    from training.step_tracker import step_check
    r = step_check(step_title, expected_visual_state, common_mistakes, observation, said)
    # -> {"status": "done", "mistake": "", "backend": "adaption", "latency_ms": 380}

Backends:
  adaption  the small model fine-tuned with Adaption (OpenAI-compatible endpoint, e.g. Together);
            used when ADAPTION_MODEL_ID is set. Falls back to Claude on timeout or bad output.
  claude    Claude Haiku with structured output (the baseline the fine-tune is measured against).
The same SYSTEM prompt and input format are used for training data, evaluation and live calls.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Literal

from pydantic import BaseModel, Field

SYSTEM = (
    "You track progress in a hands-on repair. Given one step of the procedure, what that step looks like "
    "when done, the common mistakes at that step, and a description of what the camera currently shows "
    "(plus anything the user said), decide the step's status:\n"
    "- not_started: the step's work hasn't begun\n"
    "- in_progress: the user is doing it, not finished\n"
    "- done: the camera matches the done state\n"
    "- error: a mistake is happening (name it)\n"
    'Answer with JSON only: {"status": "...", "mistake": "..."} (mistake empty unless status is error).'
)
CLAUDE_MODEL = os.getenv("STEP_TRACKER_CLAUDE_MODEL", "claude-haiku-5-5")
ADAPTION_TIMEOUT_S = float(os.getenv("ADAPTION_TIMEOUT_S", "0.8"))  # same budget as backend/config.py


class StepStatus(BaseModel):
    status: Literal["not_started", "in_progress", "done", "error"]
    mistake: str = Field(description="The mistake, only when status is error; else empty.")


def make_input(step_instruction: str, expected_visual_state: str, common_mistakes: list[str],
               observation: str, said: str = "") -> str:
    """The exact text the model sees, in training AND live. Uses the step's INSTRUCTION (the field
    the live contract's RepairStep has), so the fine-tuned model sees the same input it was trained on."""
    mistakes = "; ".join(common_mistakes) or "none listed"
    return (f"STEP: {step_instruction}\nDONE LOOKS LIKE: {expected_visual_state}\nCOMMON MISTAKES: {mistakes}\n"
            f"CAMERA NOW: {observation}\nUSER SAID: {said or '(nothing)'}")


def describe_observation(state: str, action: str, parts: list[str]) -> str:
    """One wording for 'what the camera shows', used by the training data AND the live call."""
    return f"{state}. Action: {action or 'unclear'}. Parts visible: {', '.join(parts) or 'none named'}."


# The live contract (backend/contracts.py BrainResponse.step_status) has 3 values; the tracker has 4.
CONTRACT_STATUS = {"not_started": None, "in_progress": "in_progress", "done": "done", "error": "mistake"}


def parse_output(text: str) -> StepStatus:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in model output: {text[:120]!r}")
    return StepStatus.model_validate(json.loads(m.group(0)))


# ---------------------------------------------------------------- backends
def _claude(user: str) -> StepStatus:
    import anthropic

    resp = anthropic.Anthropic().messages.parse(
        model=CLAUDE_MODEL, max_tokens=1024, system=SYSTEM,
        messages=[{"role": "user", "content": user}], output_format=StepStatus,
        output_config={"effort": "low"})
    if resp.parsed_output is None:
        raise RuntimeError(f"Claude gave no structured output (stop_reason={resp.stop_reason})")
    return resp.parsed_output


def _adaption(user: str) -> StepStatus:
    """The fine-tuned model behind any OpenAI-compatible chat endpoint (Together by default)."""
    import httpx

    base = os.getenv("ADAPTION_BASE_URL", "https://api.together.xyz/v1").rstrip("/")
    key = os.getenv("ADAPTION_API_KEY") or os.environ["TOGETHER_API_KEY"]
    r = httpx.post(f"{base}/chat/completions", timeout=ADAPTION_TIMEOUT_S,
                   headers={"Authorization": f"Bearer {key}"},
                   json={"model": os.environ["ADAPTION_MODEL_ID"], "temperature": 0, "max_tokens": 120,
                         "messages": [{"role": "system", "content": SYSTEM},
                                      {"role": "user", "content": user}]})
    r.raise_for_status()
    return parse_output(r.json()["choices"][0]["message"]["content"])


LOCAL_ADAPTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "models", "adaption", "judge")


def _local(user: str) -> StepStatus:
    """The Adaption-trained judge on this machine (Qwen3.5-2B + the AutoScientist LoRA, CPU). No API call."""
    from training.adaption.local_judge import judge

    text, _ = judge(user)
    return parse_output(text)


def _base(user: str) -> StepStatus:
    """The same base model WITHOUT the Adaption adapter: the 'before' in the before/after."""
    from training.adaption.local_judge import judge

    text, _ = judge(user, adapter=False)
    return parse_output(text)


def local_available() -> bool:
    return os.path.isfile(os.path.join(LOCAL_ADAPTER, "adapter_model.safetensors")) and os.getenv("STEP_TRACKER_LOCAL", "1") != "0"


def _rule(user: str) -> StepStatus:
    """Offline stand-in for tests (--mock): keyword overlap between camera text and done state."""
    done = set(re.findall(r"[a-z]{4,}", user.split("DONE LOOKS LIKE:")[1].split("\n")[0].lower()))
    cam = set(re.findall(r"[a-z]{4,}", user.split("CAMERA NOW:")[1].split("\n")[0].lower()))
    overlap = len(done & cam) / (len(done) or 1)
    return StepStatus(status="done" if overlap > 0.5 else "in_progress" if overlap > 0.15 else "not_started",
                      mistake="")


BACKENDS = {"claude": _claude, "adaption": _adaption, "local": _local, "base": _base, "rule": _rule}


def adaption_available() -> bool:
    return bool(os.getenv("ADAPTION_MODEL_ID")) and bool(os.getenv("ADAPTION_API_KEY") or os.getenv("TOGETHER_API_KEY"))


def check_input(user: str, backend: str | None = None) -> dict:
    """Run one prepared input. backend=None: Adaption if configured, else Claude (with fallback)."""
    if backend:
        order = [backend]
    else:  # the fine-tuned judge first (hosted, then local), Claude as the fallback
        order = (["adaption"] if adaption_available() else []) + (["local"] if local_available() else []) + ["claude"]
    last_err = None
    for name in order:
        t0 = time.perf_counter()
        try:
            out = BACKENDS[name](user)
            return {**out.model_dump(), "backend": name, "latency_ms": round((time.perf_counter() - t0) * 1000)}
        except Exception as e:  # fall through to the next backend
            last_err = e
    raise RuntimeError(f"step check failed on {order}: {last_err}")


def step_check(step_instruction: str, expected_visual_state: str, common_mistakes: list[str],
               observation: str, said: str = "", backend: str | None = None) -> dict:
    """The live call for the brain. Adds `step_status` in the contract's vocabulary."""
    r = check_input(make_input(step_instruction, expected_visual_state, common_mistakes, observation, said), backend)
    return {**r, "step_status": CONTRACT_STATUS[r["status"]]}
