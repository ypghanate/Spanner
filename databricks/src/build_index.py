"""Job task (runs on Databricks serverless): publish the knowledge base to Vector Search.

1. MERGE gold_procedure_steps into kb_steps, a Delta table with Change Data Feed on
   (Vector Search delta-sync indexes need CDF, which pipeline materialized views don't have).
2. Create the Vector Search endpoint and a delta-sync index with Databricks-hosted
   embeddings on `search_text` the first time; afterwards just trigger a sync.
"""
import argparse
import time

from databricks.vector_search.client import VectorSearchClient
from pyspark.sql import SparkSession

ap = argparse.ArgumentParser()
ap.add_argument("--catalog", default="spanner")
ap.add_argument("--schema", default="training")
ap.add_argument("--endpoint", default="spanner-vs")
ap.add_argument("--embedding-model", default="databricks-gte-large-en")
args = ap.parse_args()

spark = SparkSession.builder.getOrCreate()
base = f"{args.catalog}.{args.schema}"
kb, gold, index_name = f"{base}.kb_steps", f"{base}.gold_procedure_steps", f"{base}.kb_steps_index"

spark.sql(f"""
  CREATE TABLE IF NOT EXISTS {kb}
  TBLPROPERTIES (delta.enableChangeDataFeed = true)
  AS SELECT * FROM {gold} WHERE 1 = 0
""")
spark.sql(f"""
  MERGE INTO {kb} t USING {gold} s ON t.step_id = s.step_id
  WHEN MATCHED THEN UPDATE SET *
  WHEN NOT MATCHED THEN INSERT *
  WHEN NOT MATCHED BY SOURCE THEN DELETE
""")
print(f"{kb}: {spark.table(kb).count()} steps")

vsc = VectorSearchClient()
endpoints = [e["name"] for e in vsc.list_endpoints().get("endpoints", [])]
if args.endpoint not in endpoints:
    if endpoints:  # Free Edition allows one endpoint: reuse the existing one
        print(f"Endpoint {args.endpoint} not found; reusing {endpoints[0]}")
        args.endpoint = endpoints[0]
    else:
        vsc.create_endpoint(name=args.endpoint, endpoint_type="STANDARD")
        while vsc.get_endpoint(args.endpoint).get("endpoint_status", {}).get("state") != "ONLINE":
            print("waiting for endpoint...")
            time.sleep(30)

existing = [i["name"] for i in vsc.list_indexes(args.endpoint).get("vector_indexes", [])]
if index_name in existing:
    vsc.get_index(args.endpoint, index_name).sync()
    print(f"Sync triggered: {index_name}")
else:
    vsc.create_delta_sync_index(
        endpoint_name=args.endpoint,
        index_name=index_name,
        source_table_name=kb,
        pipeline_type="TRIGGERED",
        primary_key="step_id",
        embedding_source_column="search_text",
        embedding_model_endpoint_name=args.embedding_model,
    )
    print(f"Created index {index_name} on endpoint {args.endpoint}")
