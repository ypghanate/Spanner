"""Small test helpers (pytest-asyncio is not installed, so async tests use run())."""
from __future__ import annotations

import asyncio


def run(coro):
    return asyncio.run(coro)
