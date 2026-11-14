"""Seed the two witnessed demo mistakes that make session three warn the user.

Run before the rehearsal:
    uv run python scripts/seed_learning_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.feedback import learned_mistake_for, record_live_mistake

REPAIR_ID = "rk61_switch_replacement"
STEP_INDEX = 6
MISTAKE = "put the switch in rotated"


def main() -> None:
    for session_id in ("demo-learning-1", "demo-learning-2"):
        record_live_mistake(session_id, REPAIR_ID, STEP_INDEX, MISTAKE,
                            "Demo seed: the replacement switch was rotated before insertion.")
    learned = learned_mistake_for(REPAIR_ID, STEP_INDEX)
    if learned != MISTAKE:
        raise SystemExit("learning-loop seed did not reach the two-session threshold")
    print(f'Seeded 2 sessions. Session 3 warning: "Users often {learned}."')


if __name__ == "__main__":
    main()
