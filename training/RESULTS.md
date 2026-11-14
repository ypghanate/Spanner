# Pass 1 eval: Claude Haiku vs the Adaption fine-tune

**Not run yet.** Nothing has been trained or evaluated, so there are no numbers here.

To produce them:

1. `uv run python training/make_dataset.py --yes` builds the dataset (see `ADAPTION_RUNBOOK.md`).
2. Train on Adaption and set `ADAPTION_MODEL_ID` and `TOGETHER_API_KEY` in `.env`.
3. `uv run python training/eval.py` overwrites this file and writes `results.svg`.

The gate for turning the fine-tune on: valid JSON at least 98% **and** intent accuracy within 3 points of Claude Haiku.
