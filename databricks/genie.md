# Genie space: "Ask Spanner's repair data"

Create it in Databricks: **Genie → New space**, add the tables below, paste the instructions, and add
the sample questions. In the pitch, a judge types a question and Genie answers from live data.

## Tables
`spanner.training.gold_procedure_steps`, `gold_live_mistakes`, `gold_label_quality`, `gold_kb_coverage`,
`gold_part_prices`, `gold_eval_scoreboard`, `bronze_part_cards`, `bronze_sources`

## Instructions (paste into the space)
Spanner is an AI repair assistant. `gold_procedure_steps` is its knowledge base: one row per repair step;
`source_kind` says where it came from (video, web via Tavily, manual via Linkup, canonical = merged).
`gold_live_mistakes` holds mistakes caught during live repairs; a mistake seen in 2+ sessions is added to
the canonical step's `common_mistakes` (`learned_mistake_count`). `gold_part_prices` holds replacement-part
offers found by the Linkup agent; `verified` means the fit was confirmed for the exact model by 2+
independent sites. `bronze_part_cards.verdict` is the repair-or-replace decision; `savings` and
`ewaste_kg_avoided` are its impact. `gold_eval_scoreboard` compares the Adaption fine-tuned step tracker
(`backend = adaption`) with Claude (`backend = claude`). Ignore rows where `is_mock` is true.

## Sample questions
- Which repair step do users get wrong most often, and what's the mistake?
- How much money and e-waste have repairs saved so far?
- How does the Adaption step tracker compare with Claude on accuracy and latency?
- Which replacement offers are verified, and what's the cheapest one for each part?
- How many knowledge-base steps came from videos, web guides and manuals?
