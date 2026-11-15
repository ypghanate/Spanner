"""Safety stage (real): hard rules that run before the brain and can force safety_stop."""
from __future__ import annotations

import re
from typing import Any

from backend.contracts import Alert, BrainResponse, SituationReport

_HARD = re.compile(r"\b(gas|high[- ]voltage|live wire|boiler|"
                   r"lithium (?:battery )?(?:swelling|swollen|puncture)|swollen battery)\b", re.I)
# Mains power is only a reason to stop while the thing is connected to it. A plug that is out of the wall (the usual
# case when someone is fixing its fuse) is a normal home repair: the plan's first step is to make sure it stays that way.
_MAINS = re.compile(r"\b(mains|230\s?v|240\s?v|110\s?v)\b", re.I)
_CONNECTED = re.compile(r"(?<!un)(?<!not )(?<!n't )\b(plugged in|plugged into|in the (wall )?socket|switched on|turned on|powered( on| up)?|live)\b", re.I)
_DISCONNECTED = re.compile(r"\b(unplugged|un-plugged|not plugged( in)?|isn'?t plugged( in)?|disconnected|switched off|turned off|out of the (wall|socket)|off at the wall)\b", re.I)


class _Stop:
    """`_STOP.search(text)`: a match when the text describes something unsafe to repair at home, else None."""

    @staticmethod
    def search(text: str):
        m = _HARD.search(text)
        if m:
            return m
        mains = _MAINS.search(text)
        if mains and _CONNECTED.search(text) and not _DISCONNECTED.search(text):
            return mains
        return None


_STOP = _Stop()


async def check(report: SituationReport, session: Any) -> BrainResponse | None:
    text = f"{report.user_utterance or ''} {report.summary} {report.camera_sees} {report.suspected_fault or ''}"
    m = _STOP.search(text)
    if not m:
        return None
    return BrainResponse(
        phase="safety_stop",
        say="Stop there: that's not a safe home repair. Please call a qualified professional.",
        alert=Alert(severity="danger", message=f"Unsafe repair detected ({m.group(0)}): not supported."))
