# Adaption runbook: fine-tune Pass 1 (OPTIONAL: not the team's Adaption entry)

> **Read this first.** On 14 Nov the team decided that Adaption fine-tunes the **step tracker**, not Pass 1
> (`VISION.md`, "Fine-tuning"; steps in `training/README.md`: `adaption_dataset.py`, `step_tracker.py`,
> `eval_step_tracker.py`). Pass 1 stays on Claude. **Do not spend Adaption credits on Pass 1** unless the
> team reverses that decision; the 1250 credits are for the step tracker.
>
> The two use different model variables: the step tracker reads `ADAPTION_MODEL_ID`; Pass 1 reads its own
> `PASS1_ADAPTION_MODEL_ID`. Putting the step tracker's id in the Pass 1 variable is refused at runtime.
>
> What follows is kept as a reference in case Pass 1 is fine-tuned later.

A person does the Adaption steps (their website or API). Nothing in this repo launches a training run. Target launch: **about 01:00 Sunday**, so it trains overnight while the team sleeps.

**What gets trained:** only Pass 1 (camera observations + speech -> `SituationReport` JSON). The goal is a small model that answers in under 0.5 s (Claude Haiku takes about 1 s), with Claude as the automatic fallback. Vision and detection models are not fine-tuned (out of scope).

## The credit rule: 1250 credits, spend them once

- **Know the price before launching anything.** Find what one Adaptive Data run and one AutoScientist run cost, in credits, for ~800 rows.
- **Budget one full run plus one retry.** If the price of two full runs is more than 1250, plan for one run and a smaller dataset instead.
- **A smoke run first only if it costs under about 125 credits (10%).** Otherwise skip it and check the dataset by eye instead (step 1).
- Write every spend in the log at the bottom.

## Step 0: questions for the Adaption booth / docs (fill in the answers)

| Question | Answer |
|---|---|
| Accepted dataset format? (chat JSONL `{"messages": [...]}` is the default we write) | |
| Credits per Adaptive Data run on ~800 rows? | |
| Credits per AutoScientist run? Does it pick the base model itself? | |
| Which base models can it train? Smallest one that handles JSON reliably (7-8B class) is the target. | |
| Is there an API / SDK to submit data and launch training, or only the website? | |
| How is the trained model served? Together AI model ID? Another endpoint? | |
| Rate limits / cold-start delay on the served model? | |
| Does it hold out a validation split of its own? | |

If the answer to "format" is not chat JSONL, regenerate with `--format prompt_completion` or `--format alpaca` (step 1). If it is something else, add a format to `to_format()` in `make_dataset.py`; don't hand-edit files.

## Step 1: build the dataset

```bash
# 1. Estimate the API spend. Calls nothing, changes nothing.
uv run python training/make_dataset.py

# 2. Generate it (Claude Opus writes scenarios, Haiku labels them). Spends API money:
uv run python training/make_dataset.py --yes
#    add --format prompt_completion | alpaca if Adaption needs another shape
```

It writes `training/data/train.jsonl` (about 700 rows), `eval.jsonl` (100 rows) and `eval_gold.jsonl` (gold intent and fault per eval row). Results are cached in `training/data/cache/`, so rerunning is free.

Check before uploading:

- The printed counts: roughly equal rows per intent, and "real" rows from live testing if any. If `train` is well under 700, raise `--n` or rerun after the demo has logged more real calls (`training/data/raw/pass1_calls.jsonl`).
- Open 5 random lines of `train.jsonl`. The assistant message must be one JSON object and the user message must look like the live prompt (`PHASE: ...`, `OBSERVATIONS ...`, `USER SAID: ...`).
- `dropped_intent_mismatch` in the stats should be a small share. A big share means the scenario prompt (`training/prompts/scenario_gen.md`) or the Pass 1 prompt needs a fix before spending credits.

**Do not upload `eval.jsonl` for training.** It is the held-out set.

## Step 2: train (about 01:00)

1. Upload `train.jsonl`. Run **Adaptive Data** (cleaning) first.
2. Run **AutoScientist** on the cleaned data, choosing a small base model if it lets you.
3. Note the model ID or endpoint when it finishes.

## Step 3: switch it on, only if it earns it

```bash
# .env
TOGETHER_API_KEY=...
PASS1_ADAPTION_MODEL_ID=<the trained model id>   # NOT ADAPTION_MODEL_ID (the step tracker's)
ADAPTION_BASE_URL=https://api.together.xyz/v1     # change if Adaption serves it elsewhere

uv run python training/eval.py                    # prints the table, writes RESULTS.md and results.svg
```

`eval.py` ends with **PASS** or **FAIL**:

- **PASS** (valid JSON at least 98% and intent accuracy within 3 points of Claude): set `PASS1_BACKEND=adaption`.
- **FAIL:** keep `PASS1_BACKEND=claude`. The Claude row and the eval numbers are still worth showing in the pitch.

Whatever you pick, the live app falls back to Claude by itself on bad JSON or after 800 ms, and logs those calls as `adaption_fallback`.

If there is no API and no Together serving, there is nothing to call from code; tell the team lead, because the fine-tune can then only be shown as the eval table.

## Log

| When | What | Credits spent | Credits left |
|---|---|---|---|
| | start | 0 | 1250 |
| | | | |
