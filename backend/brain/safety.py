"""Safety stage (real): hard rules that run before the brain and can force safety_stop."""
from __future__ import annotations

import re
from typing import Any

from backend.contracts import Alert, BrainResponse, SituationReport

_STOP = re.compile(r"\b(gas|mains|230\s?v|240\s?v|110\s?v|high[- ]voltage|live wire|boiler|"
                   r"lithium (?:battery )?(?:swelling|swollen|puncture)|swollen battery)\b", re.I)


async def check(report: SituationReport, session: Any) -> BrainResponse | None:
    text = f"{report.user_utterance or ''} {report.summary} {report.camera_sees} {report.suspected_fault or ''}"
    m = _STOP.search(text)
    if not m:
        return None
    return BrainResponse(
        phase="safety_stop",
        say="Stop there: that's not a safe home repair. Please call a qualified professional.",
        alert=Alert(severity="danger", message=f"Unsafe repair detected ({m.group(0)}): not supported."))
