-- Queries for the AI/BI dashboard "Spanner: training data" (replace spanner.training if you
-- use another catalog). Add each as a dataset in the dashboard editor.
-- THE 4 HEADLINE TILES for the pitch: 10 (Adaption before/after), 11 (learned from live repairs),
-- 12 (repair-or-replace impact), 3 (labels rejected by quality gates).

-- 10. Adaption: fine-tuned step tracker vs Claude (accuracy and latency)
SELECT backend, metric, round(value, 3) AS value, n
FROM spanner.training.gold_eval_scoreboard
WHERE eval_name = 'step_tracker' AND metric IN ('accuracy', 'macro_f1', 'mistake_recall', 'latency_p50_ms')
ORDER BY metric, backend;

-- 11. The learning loop: mistakes real users made, now warned about in the knowledge base
SELECT m.step_index, m.mistake, m.sessions, m.last_seen,
       (m.sessions >= 2) AS now_in_knowledge_base
FROM spanner.training.gold_live_mistakes m ORDER BY m.sessions DESC, m.last_seen DESC;

-- 12. Real-world impact of the Linkup agent: money and e-waste saved by repairing
SELECT count(*) AS repairs_recommended, round(sum(savings), 2) AS money_saved,
       round(sum(ewaste_kg_avoided), 2) AS ewaste_kg_avoided, first(currency) AS currency
FROM spanner.training.bronze_part_cards WHERE verdict = 'repair' AND NOT is_mock;

-- 13. Linkup parts agent quality
SELECT metric, round(value, 3) AS value, n FROM spanner.training.gold_eval_scoreboard
WHERE eval_name = 'parts_agent' ORDER BY metric;

-- 14. Live pipeline latency by stage
SELECT stage, p50_ms, p90_ms, samples, failures
FROM spanner.training.gold_stage_latency
ORDER BY p50_ms DESC;

-- 1. Knowledge base at a glance: procedures and steps by source type
SELECT source_kind, procedures, steps FROM spanner.training.gold_kb_coverage;

-- 2. Teacher-label quality per video
SELECT video_id, windows, relevant_windows, low_confidence_windows, avg_confidence
FROM spanner.training.gold_label_quality ORDER BY video_id;

-- 3. Labels rejected by the quality gates (Lakeflow expectations), per rule
SELECT e.name AS rule, sum(e.failed_records) AS rejected, sum(e.passed_records) AS passed
FROM (
  SELECT explode(from_json(details:flow_progress:data_quality:expectations,
         'ARRAY<STRUCT<name STRING, dataset STRING, passed_records BIGINT, failed_records BIGINT>>')) AS e
  FROM event_log(TABLE(spanner.training.silver_segment_labels))
  WHERE event_type = 'flow_progress' AND details:flow_progress:data_quality IS NOT NULL
)
GROUP BY e.name;

-- 4. Where the knowledge comes from (provenance and licence)
SELECT kind, licence, count(*) AS sources FROM spanner.training.bronze_sources GROUP BY kind, licence;

-- 5. The canonical procedure, step by step (what the live agent follows)
SELECT step_index, title, expected_visual_state, size(common_mistakes) AS mistakes_known
FROM spanner.training.gold_procedure_steps
WHERE source_kind = 'canonical' ORDER BY step_index;

-- 7. Replacement parts: best verified offer per part (Linkup agent)
SELECT brand, model, part_needed, retailer, price, currency, verified, independent_sources, url
FROM spanner.training.gold_part_prices WHERE rank_in_card = 1 ORDER BY searched_at DESC;

-- 8. How often compatibility could be verified (2+ independent sources)
SELECT part_needed, count(*) AS offers, sum(CASE WHEN verified THEN 1 ELSE 0 END) AS verified_offers,
       round(avg(price), 2) AS avg_price
FROM spanner.training.gold_part_prices GROUP BY part_needed;

-- 9. Facts taken from official manuals
SELECT title, spec_name, spec_value, spec_unit, page, manual_url
FROM spanner.training.bronze_manual_facts ORDER BY title, page;

-- 6. Most-mentioned common mistakes across all sources
SELECT mistake, count(*) AS sources
FROM (SELECT explode(common_mistakes) AS mistake FROM spanner.training.gold_procedure_steps)
GROUP BY mistake ORDER BY sources DESC LIMIT 15;
