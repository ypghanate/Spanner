"""Shared test setup: mocks on, no keys, scratch output dirs, fresh settings per test."""
from __future__ import annotations

import pytest

from backend import config, mocks

KEY_VARS = ["ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "TOGETHER_API_KEY",
            "ADAPTION_API_KEY", "ADAPTION_MODEL_ID", "PASS1_ADAPTION_MODEL_ID", "FAL_KEY"]


def set_env(monkeypatch, **env) -> config.Settings:
    """Change environment variables mid-test and rebuild the (shared, cached) settings."""
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    mocks.reset_stages()
    return config.reload_settings()


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    for var in KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("USE_MOCKS", "1")
    monkeypatch.setenv("PASS1_BACKEND", "claude")
    monkeypatch.setenv("MOCK_LATENCY_MS", "0")
    monkeypatch.setenv("MEDIA_CACHE_DIR", str(tmp_path / "media_cache"))
    monkeypatch.setenv("TRAINING_DATA_DIR", str(tmp_path / "training_data"))
    mocks.reset_stages()
    yield config.reload_settings()
    mocks.reset_stages()
    config.reload_settings()
