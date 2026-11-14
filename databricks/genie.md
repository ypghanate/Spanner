# Genie space: "Ask Spanner's repair data"

Create it in Databricks: **Genie → New space**, add the tables below, paste the instructions, and add
the sample questions. In the pitch, a judge types a question and Genie answers from live data.

## Tables
`workspace.training.gold_procedure_steps`, `gold_live_mistakes`, `gold_label_quality`, `gold_kb_coverage`,
`gold_part_prices`, `gold_eval_scoreboard`, `gold_stage_latency`, `bronze_part_cards`, `bronze_sources`

## Instructions (paste into the space)
Spanner is an AI repair assistant. `gold_procedure_steps` is its knowledge base: one row per repair step;
`source_kind` says where it came from (video, web via Tavily, manual via Linkup, canonical = merged).
`gold_live_mistakes` holds mistakes caught during live repairs; a mistake seen in 2+ sessions is added to
the canonical step's `common_mistakes` (`learned_mistake_count`). `gold_part_prices` holds replacement-part
offers found by the Linkup agent; `verified` means the fit was confirmed for the exact model by 2+
independent sites. `bronze_part_cards.verdict` is the repair-or-replace decision; `savings` and
`ewaste_kg_avoided` are its impact. `gold_eval_scoreboard` compares the Adaption fine-tuned step tracker
(`backend = adaption`) with Claude (`backend = claude`). Ignore rows where `is_mock` is true.
`gold_stage_latency` contains p50/p90 milliseconds, sample counts and failures for every live stage.

## Sample questions
- Which repair step do users get wrong most often, and what's the mistake?
- How much money and e-waste have repairs saved so far?
- How does the Adaption step tracker compare with Claude on accuracy and latency?
- Which replacement offers are verified, and what's the cheapest one for each part?
- How many knowledge-base steps came from videos, web guides and manuals?
- Which pipeline stage is slowest, and what is its p90 latency?

## Deploy (no CLI needed)

```bash
python databricks/deploy_dashboard.py --check   # runs every dashboard query, shows row counts
python databricks/deploy_dashboard.py           # creates/updates + publishes the dashboard, creates the Genie space
```

Deployed on 14 Nov:
- Dashboard "Spanner repair intelligence": https://dbc-e2205585-ca78.cloud.databricks.com/dashboardsv3/01f1e0626c6410fcabd7e34a5f6d10d3/published
- Genie space: https://dbc-e2205585-ca78.cloud.databricks.com/genie/rooms/01f1e062be211dd88b29266342579fb7
  (the API does not take sample questions yet: paste the ones above into the space's settings)

The learning loop reads `bronze_live_mistakes` directly (2+ distinct sessions), so a warning appears without a
pipeline refresh. The gold views (`gold_*`) refresh with the Lakeflow pipeline `spanner-training-quality`
(start it from the Pipelines page, or `python -m training.pipeline databricks sync` then a refresh).

For the demo learning moment, two real sessions with the same mistake are enough; `scripts/seed_learning_demo.py`
is the fallback (step 7, "put the switch in rotated") if rehearsal time runs out. Either way, session three
starts step 7 with: "Heads up. People often put the switch in rotated at this step."
