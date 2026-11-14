-- Spanner training data on Databricks: catalog, schema, volume and BRONZE tables.
-- Run by `python -m training.pipeline databricks init` (placeholders are filled in).
-- Silver and gold tables are built by the Lakeflow pipeline in databricks/pipelines/.

CREATE CATALOG IF NOT EXISTS {catalog};
CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}
  COMMENT 'Spanner: training data built from repair videos and web guides';
CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.raw
  COMMENT 'Keyframes, review pages, scraped web pages and (optionally) source videos';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.bronze_sources (
  source_id STRING NOT NULL, kind STRING COMMENT 'video | web',
  repair_id STRING, url STRING, title STRING, creator STRING, licence STRING,
  ingested_at TIMESTAMP
) COMMENT 'Every source used for training, with provenance and licence';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.bronze_web_pages (
  page_id STRING NOT NULL, repair_id STRING, url STRING, title STRING, query STRING,
  raw_content STRING, fetched_at STRING, ingested_at TIMESTAMP
) COMMENT 'Repair guides found and extracted by Tavily';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.bronze_transcripts (
  video_id STRING NOT NULL, repair_id STRING, language STRING, n_words INT,
  words_json STRING, ingested_at TIMESTAMP
) COMMENT 'Word-level transcripts (faster-whisper)';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.bronze_segment_labels (
  video_id STRING NOT NULL, repair_id STRING, start_s DOUBLE, end_s DOUBLE,
  transcript STRING, frames_json STRING, label_json STRING, label_model STRING,
  ingested_at TIMESTAMP
) COMMENT 'Claude teacher labels, one row per 10 s window of a video';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.bronze_part_cards (
  card_id STRING NOT NULL, brand STRING, model STRING, model_number STRING,
  part_needed STRING, fault STRING, manual_url STRING, n_sources INT, is_mock BOOLEAN,
  verdict STRING COMMENT 'repair | replace | unclear', repair_cost DOUBLE, new_price DOUBLE,
  savings DOUBLE, ewaste_kg_avoided DOUBLE, currency STRING, recall_status STRING,
  card_json STRING, searched_at STRING, ingested_at TIMESTAMP
) COMMENT 'Linkup research results: one replacement card per (model, part), with the repair-or-replace decision';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.bronze_live_mistakes (
  session_id STRING NOT NULL, repair_id STRING, step_index INT, mistake STRING,
  observation STRING, ts STRING, ingested_at TIMESTAMP
) COMMENT 'Mistakes the step tracker caught in live repairs (feeds the learning loop)';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.bronze_eval_results (
  run_id STRING NOT NULL, eval_name STRING, backend STRING, metric STRING, value DOUBLE,
  n INT, created_at STRING, ingested_at TIMESTAMP
) COMMENT 'Evaluation runs: Adaption step tracker vs Claude, Linkup parts-agent citation coverage';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.bronze_stage_timings (
  timing_id STRING NOT NULL, session_id STRING, ts DOUBLE, stage STRING,
  ms DOUBLE, ok BOOLEAN, ingested_at TIMESTAMP
) COMMENT 'Per-stage latency measured by the live repair loop';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.bronze_part_offers (
  card_id STRING NOT NULL, part_name STRING, part_number STRING, retailer STRING,
  price DOUBLE, currency STRING, in_stock STRING, url STRING,
  fits STRING, verified BOOLEAN, independent_sources INT, verdict_reason STRING,
  searched_at STRING, ingested_at TIMESTAMP
) COMMENT 'Every offer Linkup found, with the compatibility check and its evidence count';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.bronze_manual_facts (
  card_id STRING NOT NULL, manual_url STRING, title STRING,
  spec_name STRING, spec_value STRING, spec_unit STRING, page STRING, ingested_at TIMESTAMP
) COMMENT 'Specs extracted from official manuals (torques, sizes, part numbers)';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.bronze_procedures (
  procedure_id STRING NOT NULL, repair_id STRING, source_kind STRING COMMENT 'video | web | canonical',
  record_json STRING, ingested_at TIMESTAMP
) COMMENT 'Procedures extracted by Claude (per video, per web page, and the merged canonical one)';
