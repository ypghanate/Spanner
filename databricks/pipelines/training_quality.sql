-- Lakeflow Declarative Pipeline: bronze -> silver -> gold for Spanner's training data.
-- Expectations are the quality gates on Claude's labels; dropped rows are counted in the
-- pipeline event log (see databricks/sql/dashboard_queries.sql).
-- ${catalog} and ${schema} come from the pipeline configuration (databricks.yml).

-- SILVER: Claude's window labels that pass the quality gates (relevant windows only)
CREATE OR REFRESH MATERIALIZED VIEW silver_segment_labels (
  CONSTRAINT confident_label EXPECT (confidence >= 0.6) ON VIOLATION DROP ROW,
  CONSTRAINT checkable_state EXPECT (length(trim(visual_state)) >= 10) ON VIOLATION DROP ROW,
  CONSTRAINT named_step      EXPECT (length(trim(step)) > 0) ON VIOLATION DROP ROW
)
COMMENT 'Teacher labels per 10 s window that passed the quality gates'
AS SELECT
  video_id, repair_id, start_s, end_s, transcript, label_model,
  l.step AS step, l.action AS action, l.tools AS tools, l.parts AS parts,
  l.visual_state AS visual_state, l.warnings AS warnings, l.confidence AS confidence
FROM (
  SELECT *, from_json(label_json,
    'relevant BOOLEAN, step STRING, action STRING, tools ARRAY<STRING>, parts ARRAY<STRING>, visual_state STRING, warnings ARRAY<STRING>, confidence DOUBLE') AS l
  FROM ${catalog}.${schema}.bronze_segment_labels
)
WHERE l.relevant;

-- SILVER: parsed procedures (per video, per web guide, and the canonical merge)
CREATE OR REFRESH MATERIALIZED VIEW silver_procedures (
  CONSTRAINT has_steps EXPECT (size(steps) > 0) ON VIOLATION DROP ROW
)
COMMENT 'Procedures extracted by Claude, parsed into columns'
AS SELECT
  procedure_id, repair_id, source_kind,
  r.procedure.object AS object, r.procedure.fault AS fault,
  r.procedure.tools_needed AS tools_needed, r.procedure.parts AS parts,
  r.procedure.steps AS steps, r.procedure.notes AS notes,
  r.sources AS sources, r.keyframes AS keyframes, ingested_at
FROM (
  SELECT *, from_json(record_json,
    'procedure_id STRING, repair_id STRING, source_kind STRING, source_videos ARRAY<STRING>, sources ARRAY<MAP<STRING,STRING>>, keyframes MAP<STRING,STRING>, procedure STRUCT<object STRING, fault STRING, tools_needed ARRAY<STRING>, parts ARRAY<STRING>, steps ARRAY<STRUCT<index INT, title STRING, instruction STRING, parts ARRAY<STRING>, tools ARRAY<STRING>, expected_visual_state STRING, common_mistakes ARRAY<STRING>, safety_notes ARRAY<STRING>, start_s DOUBLE, end_s DOUBLE>>, notes STRING>') AS r
  FROM ${catalog}.${schema}.bronze_procedures
);

-- GOLD: mistakes the step tracker caught in live repairs (the learning loop)
CREATE OR REFRESH MATERIALIZED VIEW gold_live_mistakes
COMMENT 'Real mistakes from live sessions, counted per step'
AS SELECT
  repair_id, step_index, lower(trim(mistake)) AS mistake,
  count(*) AS times_seen, count(DISTINCT session_id) AS sessions, max(ts) AS last_seen
FROM ${catalog}.${schema}.bronze_live_mistakes
WHERE length(trim(mistake)) > 0
GROUP BY repair_id, step_index, lower(trim(mistake));

-- GOLD: one row per step = the knowledge base the live agent searches (Vector Search source).
-- Canonical steps also get every mistake real users made in 2+ sessions, so the next user is warned.
CREATE OR REFRESH MATERIALIZED VIEW gold_procedure_steps (
  CONSTRAINT checkable_expected_state EXPECT (length(trim(expected_visual_state)) >= 10)
)
COMMENT 'Knowledge base: every step of every procedure (+ mistakes learned from live repairs), ready for embedding'
AS WITH steps AS (
  SELECT *, explode(steps) AS s FROM silver_procedures
), learned AS (
  SELECT repair_id, step_index, collect_list(mistake) AS learned_mistakes
  FROM gold_live_mistakes WHERE sessions >= 2
  GROUP BY repair_id, step_index
), merged AS (
  SELECT st.*,
    CASE WHEN st.source_kind = 'canonical' AND l.learned_mistakes IS NOT NULL
         THEN array_distinct(concat(st.s.common_mistakes, l.learned_mistakes))
         ELSE st.s.common_mistakes END AS mistakes,
    CASE WHEN st.source_kind = 'canonical' THEN coalesce(size(l.learned_mistakes), 0) ELSE 0 END AS learned_count
  FROM steps st
  LEFT JOIN learned l ON l.repair_id = st.repair_id AND l.step_index = st.s.index
)
SELECT
  concat(procedure_id, '#', s.index) AS step_id,
  procedure_id, repair_id, source_kind, object, fault,
  s.index AS step_index, s.title AS title, s.instruction AS instruction,
  s.parts AS parts, s.tools AS tools,
  s.expected_visual_state AS expected_visual_state,
  mistakes AS common_mistakes, learned_count AS learned_mistake_count, s.safety_notes AS safety_notes,
  keyframes[cast(s.index AS STRING)] AS keyframe_path,
  concat_ws(' | ',
    s.title, s.instruction,
    concat('Looks like when done: ', s.expected_visual_state),
    concat('Parts: ', array_join(s.parts, ', ')),
    concat('Tools: ', array_join(s.tools, ', ')),
    concat('Common mistakes: ', array_join(mistakes, '; '))) AS search_text
FROM merged;

-- GOLD: evaluation scoreboard (Adaption step tracker vs Claude, Linkup parts agent)
CREATE OR REFRESH MATERIALIZED VIEW gold_eval_scoreboard
COMMENT 'Latest value of every evaluation metric per backend'
AS SELECT eval_name, backend, metric, max_by(value, created_at) AS value, max_by(n, created_at) AS n,
  max(created_at) AS last_run
FROM ${catalog}.${schema}.bronze_eval_results
GROUP BY eval_name, backend, metric;

-- GOLD: live pipeline latency by stage (for the dashboard)
CREATE OR REFRESH MATERIALIZED VIEW gold_stage_latency
COMMENT 'P50, P90 and sample count for each live pipeline stage'
AS SELECT stage,
  round(percentile_approx(ms, 0.50), 1) AS p50_ms,
  round(percentile_approx(ms, 0.90), 1) AS p90_ms,
  count(*) AS samples,
  sum(CASE WHEN ok THEN 0 ELSE 1 END) AS failures
FROM ${catalog}.${schema}.bronze_stage_timings
GROUP BY stage;

-- GOLD: label quality per video (for the dashboard)
CREATE OR REFRESH MATERIALIZED VIEW gold_label_quality
COMMENT 'How good the teacher labels are, per video'
AS SELECT
  video_id, repair_id,
  count(*) AS windows,
  sum(CASE WHEN l.relevant THEN 1 ELSE 0 END) AS relevant_windows,
  sum(CASE WHEN l.relevant AND l.confidence < 0.6 THEN 1 ELSE 0 END) AS low_confidence_windows,
  round(avg(CASE WHEN l.relevant THEN l.confidence END), 3) AS avg_confidence
FROM (
  SELECT video_id, repair_id, from_json(label_json, 'relevant BOOLEAN, confidence DOUBLE') AS l
  FROM ${catalog}.${schema}.bronze_segment_labels
)
GROUP BY video_id, repair_id;

-- GOLD: replacement-part price comparison, verified offers only count as "safe to buy"
CREATE OR REFRESH MATERIALIZED VIEW gold_part_prices (
  CONSTRAINT priced EXPECT (price > 0) ON VIOLATION DROP ROW,
  CONSTRAINT has_url EXPECT (url LIKE 'http%') ON VIOLATION DROP ROW
)
COMMENT 'Live prices found by the Linkup agent, ranked per part, with compatibility evidence'
AS SELECT
  c.brand, c.model, c.model_number, c.part_needed,
  o.retailer, o.part_number, o.price, o.currency, o.in_stock, o.url,
  o.fits, o.verified, o.independent_sources, o.verdict_reason, o.searched_at,
  rank() OVER (PARTITION BY o.card_id ORDER BY o.verified DESC, o.price ASC) AS rank_in_card
FROM ${catalog}.${schema}.bronze_part_offers o
JOIN ${catalog}.${schema}.bronze_part_cards c USING (card_id)
WHERE NOT c.is_mock;

-- GOLD: knowledge-base coverage by source type (for the dashboard)
CREATE OR REFRESH MATERIALIZED VIEW gold_kb_coverage
COMMENT 'How many procedures and steps each kind of source contributed'
AS SELECT repair_id, source_kind,
  count(DISTINCT procedure_id) AS procedures, count(*) AS steps
FROM gold_procedure_steps
GROUP BY repair_id, source_kind;
