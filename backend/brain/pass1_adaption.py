"""Pass 1 on the fine-tuned model (Adaption), served from an OpenAI-style chat endpoint
(Together AI by default; set ADAPTION_BASE_URL for anything else).

Same interface as Claude's Pass 1. The dispatcher in pass1.run() wraps this in an 800 ms
timeout and falls back to Claude on any failure.
"""
from __future__ import annotations

import json
import re

import httpx
from pydantic import ValidationError

from ..config import get_settings
from ..contracts import SituationReport
from .pass1 import Pass1Input, load_system_prompt, render_user

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)

_client: httpx.AsyncClient | None = None


class AdaptionError(RuntimeError):
    pass


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # keep-alive connection reused across calls; the dispatcher enforces the overall timeout
        _client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0))
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def parse_report(text: str) -> SituationReport:
    """Strip code fences and validate. Raises AdaptionError on bad JSON or a bad shape."""
    cleaned = _FENCE.sub("", text.strip()).strip()
    try:
        return SituationReport.model_validate_json(cleaned)
    except (ValidationError, json.JSONDecodeError, ValueError) as e:
        raise AdaptionError(f"model output is not a valid SituationReport: {e}") from e


async def run_adaption(inp: Pass1Input) -> SituationReport:
    s = get_settings()
    if not s.adaption_model_id or not s.together_api_key:
        raise AdaptionError("ADAPTION_MODEL_ID / TOGETHER_API_KEY not set")
    payload = {
        "model": s.adaption_model_id,
        "messages": [
            {"role": "system", "content": load_system_prompt()},
            {"role": "user", "content": render_user(inp)},
        ],
        "max_tokens": 400,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = await _get_client().post(
            f"{s.adaption_base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {s.together_api_key}"},
        )
        resp.raise_for_status()
        body = resp.json()
        text = body["choices"][0]["message"]["content"]
    except httpx.HTTPError as e:
        raise AdaptionError(f"adaption request failed: {e}") from e
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise AdaptionError(f"unexpected adaption response: {e}") from e
    return parse_report(text or "")
