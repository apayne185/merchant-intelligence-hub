# Databricks notebook source
# MAGIC %md
# MAGIC # Merchant Intelligence Hub — Part 1 on Databricks
# MAGIC
# MAGIC Walks through ingestion -> transform -> Delta write on Databricks Community
# MAGIC Edition, reusing the same PySpark DataFrame-API logic from
# MAGIC `src/parte1_pyspark.py` (verified against the pandas version — see
# MAGIC `DECISIONS.md`, "Parte 1b · PySpark rewrite").
# MAGIC
# MAGIC **Setup, one-time:**
# MAGIC 1. Clone this repo into Databricks via **Repos** (Workspace > Repos > Add Repo).
# MAGIC 2. Upload `data/transactions_sample.csv` to a DBFS/Volumes path (or a notebook
# MAGIC    widget below lets you point at wherever you put it).
# MAGIC 3. Attach this notebook to any cluster with Databricks Runtime >= 13.3 LTS
# MAGIC    (Delta Lake and `spark` are already provided by the runtime — no local
# MAGIC    `delta-spark` bootstrap needed, unlike the local script).

# COMMAND ----------

dbutils.widgets.text("csv_path", "/FileStore/merchant_intelligence_hub/transactions_sample.csv")
dbutils.widgets.text("repo_path", "/Workspace/Repos/<you>/merchant-intelligence-hub")
dbutils.widgets.text("catalog_schema", "default")

csv_path = dbutils.widgets.get("csv_path")
repo_path = dbutils.widgets.get("repo_path")
catalog_schema = dbutils.widgets.get("catalog_schema")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Ingestion
# MAGIC
# MAGIC Import the same `load_clean` / `monthly_kpis` / `quality_report` /
# MAGIC `merchants_at_risk` functions used locally, instead of re-implementing the
# MAGIC business rules inline — the transform logic should only exist in one place.

# COMMAND ----------

import os
import sys

if not os.path.isdir(repo_path):
    raise ValueError(
        f"repo_path widget points at '{repo_path}', which doesn't exist on this cluster. "
        "Set it to wherever this repo was cloned via Workspace > Repos."
    )
if repo_path not in sys.path:
    sys.path.append(repo_path)

from src.parte1_pyspark import load_clean, monthly_kpis, quality_report, merchants_at_risk

# COMMAND ----------

# MAGIC %md
# MAGIC `spark` is already provided by the Databricks notebook runtime — no
# MAGIC `get_spark()` / `configure_spark_with_delta_pip` bootstrap needed here
# MAGIC (that's only required to make Delta work on a local, non-Databricks JVM).

# COMMAND ----------

df = load_clean(spark, csv_path).cache()
display(df.limit(20))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Transform

# COMMAND ----------

kpis = monthly_kpis(df).cache()
display(kpis.orderBy("merchant_id", "month").limit(20))

# COMMAND ----------

at_risk = merchants_at_risk(df, top_n=200).cache()
display(at_risk.limit(20))

# COMMAND ----------

report = quality_report(spark, df, raw_path=csv_path)
report["summary"]

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Delta write
# MAGIC
# MAGIC Managed Delta tables in the workspace catalog, so they show up in Catalog
# MAGIC Explorer and can be queried with SQL directly (`SELECT * FROM
# MAGIC <catalog_schema>.monthly_kpis`), unlike the local run which just writes
# MAGIC Delta files under `outputs/delta/`.

# COMMAND ----------

df.write.format("delta").mode("overwrite").saveAsTable(f"{catalog_schema}.transactions_clean")
kpis.write.format("delta").mode("overwrite").saveAsTable(f"{catalog_schema}.monthly_kpis")
at_risk.write.format("delta").mode("overwrite").saveAsTable(f"{catalog_schema}.merchants_at_risk")

print(f"Delta tables written under schema '{catalog_schema}': transactions_clean, monthly_kpis, merchants_at_risk")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Sanity check
# MAGIC
# MAGIC Confirm the Delta tables round-trip correctly and match the row counts
# MAGIC computed in-memory above.

# COMMAND ----------

assert spark.table(f"{catalog_schema}.transactions_clean").count() == df.count()
assert spark.table(f"{catalog_schema}.monthly_kpis").count() == kpis.count()
assert spark.table(f"{catalog_schema}.merchants_at_risk").count() == at_risk.count()
print("OK — Delta tables match in-memory row counts")
