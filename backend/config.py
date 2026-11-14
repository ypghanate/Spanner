"""Settings from the environment. Read once; cached."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # keys
    anthropic_api_key: str
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    together_api_key: str
    adaption_model_id: str
    adaption_base_url: str
    fal_key: str
    # switches
    pass1_backend: str          # claude | adaption
    fal_enabled: bool
    use_mocks: bool
    store_backend: str          # databricks | sqlite
    mock_latency_ms: int
    # models
    pass1_model: str = "claude-haiku-5-5"
    dataset_model: str = "claude-opus-5-5"
    eleven_tts_model: str = "eleven_flash_v2_5"
    eleven_stt_model: str = "scribe_v1"            # one-shot /audio fallback
    eleven_stt_live_model: str = "scribe_v2_realtime"
    eleven_tts_format: str = "mp3_44100_64"         # cached files and live stream
    live_sample_rate: int = 16000                   # phone mic PCM16 mono
    live_vad_silence_s: float = 0.7                 # silence that ends an utterance
    # timeouts (seconds)
    vision_timeout_s: float = 6.0
    claude_timeout_s: float = 10.0
    research_timeout_s: float = 60.0
    adaption_timeout_s: float = 0.8
    stt_timeout_s: float = 5.0
    # paths
    media_cache_dir: Path = ROOT / "backend" / "media_cache"
    fixtures_dir: Path = ROOT / "fixtures"
    training_data_dir: Path = ROOT / "training" / "data"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", ""),
        elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID", ""),
        together_api_key=os.getenv("TOGETHER_API_KEY", ""),
        adaption_model_id=os.getenv("ADAPTION_MODEL_ID", ""),
        adaption_base_url=os.getenv("ADAPTION_BASE_URL", "https://api.together.xyz/v1"),
        fal_key=os.getenv("FAL_KEY", ""),
        pass1_backend=os.getenv("PASS1_BACKEND", "claude").strip().lower(),
        fal_enabled=_flag("FAL_ENABLED", "0"),
        use_mocks=_flag("USE_MOCKS", "1"),
        store_backend=os.getenv("STORE_BACKEND", "sqlite").strip().lower(),
        mock_latency_ms=int(os.getenv("MOCK_LATENCY_MS", "0") or 0),
        media_cache_dir=Path(os.getenv("MEDIA_CACHE_DIR") or ROOT / "backend" / "media_cache"),
        training_data_dir=Path(os.getenv("TRAINING_DATA_DIR") or ROOT / "training" / "data"),
    )


def reload_settings() -> Settings:
    """Drop the cache (tests change env vars)."""
    get_settings.cache_clear()
    return get_settings()
