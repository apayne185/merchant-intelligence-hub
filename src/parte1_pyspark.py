"""
Parte 1 · PySpark rewrite
=========================

DataFrame-API rewrite of `parte1_pandas.py`, same 4 functions and same
business rules (documented in DECISIONS.md), aimed at a local Spark
session (no cluster needed) with a Delta Lake write in `main`.

Run with:
    uv run --extra pyspark python -m src.parte1_pyspark data/transactions_sample.csv
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from delta import configure_spark_with_delta_pip
from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F


def get_spark(app_name: str = "merchant-intelligence-hub") -> SparkSession:
    """Local Spark session with Delta Lake support, no cluster required.

    Only for `main()` / local runs. On Databricks the notebook uses the
    runtime's own ambient `spark` (already Delta-enabled) instead of this —
    if pyspark/delta-spark versions are bumped in pyproject.toml, check they
    still match the target Databricks Runtime's bundled versions.
    """
    builder = (
        SparkSession.builder.appName(app_name)
        .master("local[*]")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
    )
    return configure_spark_with_delta_pip(builder).getOrCreate()


def _path_exists(spark: SparkSession, path: str) -> bool:
    """Existence check via Hadoop FS, unlike pathlib this understands dbfs:/, s3://, etc."""
    hadoop_conf = spark._jsc.hadoopConfiguration()
    jvm_path = spark._jvm.org.apache.hadoop.fs.Path(path)
    fs = jvm_path.getFileSystem(hadoop_conf)
    return fs.exists(jvm_path)


DUP_COLS = ["merchant_id", "transaction_date", "amount", "status", "channel"]


# -----------------------------------------------------------------------------
# 1.1  load_clean
# -----------------------------------------------------------------------------
def load_clean(spark: SparkSession, path: str | Path) -> DataFrame:
    """
    Lee el CSV y devuelve un DataFrame limpio. Mismas reglas que la version
    pandas (ver DECISIONS.md):
      - T3: amount en formato BR "1.234,56" -> double.
      - T4: transaction_date mezcla YYYY-MM-DD y DD/MM/YYYY -> parseo en dos pasadas.
      - T5: duplicados con transaction_id distinto pero resto identico -> se eliminan.
      - Nulls en amount -> imputados con la mediana por segment antes del dedup.
    """
    df = spark.read.csv(str(path), header=True, inferSchema=False)

    # --- T4: fechas mixtas YYYY-MM-DD / DD/MM/YYYY, dos pasadas (ISO primero) ---
    # Aplicado a las 3 columnas de fecha, no solo transaction_date: el CSV no
    # garantiza que reference_date/last_complaint_date esten siempre en ISO.
    for date_col in ["transaction_date", "reference_date", "last_complaint_date"]:
        iso_date = F.to_date(date_col, "yyyy-MM-dd")
        br_date = F.to_date(date_col, "dd/MM/yyyy")
        df = df.withColumn(date_col, F.coalesce(iso_date, br_date))

    # --- T3: amount formato BR -> double ---
    amount_clean = F.regexp_replace(F.col("amount"), "\\.", "")
    amount_clean = F.regexp_replace(amount_clean, ",", ".")
    amount_clean = F.when(F.trim(F.col("amount")) == "", None).otherwise(amount_clean)
    df = df.withColumn("amount", amount_clean.cast("double"))

    # --- Tipos basicos ---
    df = (
        df.withColumn("transaction_id", F.col("transaction_id").cast("long"))
        .withColumn("merchant_id", F.col("merchant_id").cast("long"))
        .withColumn("fla_churn90", F.col("fla_churn90").cast("int"))
    )

    # --- Imputar amount nulo con la mediana por segment (antes del dedup) ---
    median_by_segment = df.groupBy("segment").agg(
        F.expr("percentile(amount, 0.5)").alias("segment_median")
    )
    df = df.join(median_by_segment, on="segment", how="left")
    df = df.withColumn(
        "amount", F.coalesce(F.col("amount"), F.col("segment_median"))
    ).drop("segment_median")

    # --- T5: eliminar duplicados (mismo contenido, transaction_id distinto) ---
    # Spark no tiene un equivalente real a pandas' keep='first' (que conserva
    # la primera fila en el orden de lectura del CSV; en Spark el orden de
    # lectura no es estable entre particiones). Como proxy determinista,
    # nos quedamos con el transaction_id mas bajo por grupo — esto coincide
    # con "mas antiguo" solo si transaction_id es monotono con el orden del
    # archivo, lo cual NO esta verificado contra los datos. Ver DECISIONS.md
    # "Parte 1b" para el detalle de esta diferencia de semantica pandas/Spark.
    w = Window.partitionBy(*DUP_COLS).orderBy(F.col("transaction_id").asc())
    df = (
        df.withColumn("_rn", F.row_number().over(w))
        .filter(F.col("_rn") == 1)
        .drop("_rn")
    )

    return df


# -----------------------------------------------------------------------------
# 1.2  monthly_kpis
# -----------------------------------------------------------------------------
def monthly_kpis(df: DataFrame) -> DataFrame:
    """
    KPIs mensuales por merchant. Una fila por (merchant_id, month).
    Columnas: merchant_id, month, tpv, approval_rate, pct_ecom, n_tx.
    """
    df = df.withColumn("month", F.trunc("transaction_date", "month"))

    is_approved = F.col("status") == "approved"
    is_ecom_approved = is_approved & (F.col("channel") == "ecom")

    agg = df.groupBy("merchant_id", "month").agg(
        F.sum(F.when(is_approved, F.col("amount")).otherwise(0.0)).alias("tpv"),
        F.count("transaction_id").alias("n_tx"),
        F.sum(F.when(is_approved, 1).otherwise(0)).alias("n_approved"),
        F.sum(F.when(is_ecom_approved, F.col("amount")).otherwise(0.0)).alias("tpv_ecom"),
    )

    result = agg.withColumn(
        "approval_rate", F.col("n_approved") / F.col("n_tx")
    ).withColumn(
        "pct_ecom",
        F.when(F.col("tpv") != 0, F.col("tpv_ecom") / F.col("tpv")).otherwise(0.0),
    )

    return result.select("merchant_id", "month", "tpv", "approval_rate", "pct_ecom", "n_tx")


# -----------------------------------------------------------------------------
# 1.3  quality_report
# -----------------------------------------------------------------------------
def quality_report(
    spark: SparkSession, df: DataFrame, raw_path: str | Path = "data/transactions_sample.csv"
) -> dict[str, Any]:
    """
    Reporte de calidad de datos con al menos 5 problemas detectados. Re-lee
    `raw_path` (CSV crudo, sin parsear) para conteos exactos cuando existe.

    Nota: el fallback `raw is None` (cuando raw_path no existe) da conteos
    aproximados a partir de `df` ya limpio, no exactos — pensado para tests
    unitarios con fixtures pequenos sin CSV real, no para producción. Si se
    cambia el parseo de fechas/amount en `load_clean`, revisar tambien las
    ramas `if raw is not None` de esta funcion (T3-T1 abajo), que reparsean
    el CSV crudo por separado.
    """
    raw_path = str(raw_path)
    raw = None
    if _path_exists(spark, raw_path):
        raw = spark.read.csv(raw_path, header=True, inferSchema=False).cache()

    issues: list[dict[str, Any]] = []

    # T3 — amount en formato BR (coma decimal, punto de miles)
    if raw is not None:
        amount_counts = raw.select(
            F.sum(F.when(F.col("amount").contains(","), 1).otherwise(0)).alias("n_br"),
            F.sum(
                F.when(F.col("amount").isNull() | (F.trim(F.col("amount")) == ""), 1).otherwise(0)
            ).alias("n_null"),
        ).first()
        n_br = amount_counts["n_br"] or 0
        n_null = amount_counts["n_null"] or 0
    else:
        n_br = df.filter(F.col("amount").isNotNull()).count()
        n_null = df.filter(F.col("amount").isNull()).count()

    issues.append({
        "column": "amount",
        "rows_affected": n_br,
        "impact": (
            "El CSV representa '1.234,56' como string. "
            "Calculos de TPV fallan silenciosamente si no se parsea."
        ),
        "fix": "Quitar puntos de miles, coma-punto decimal, cast a double.",
    })
    issues.append({
        "column": "amount",
        "rows_affected": n_null,
        "impact": "TPV subestimado; introduce sesgo en metricas mensuales.",
        "fix": "Imputar con mediana por segment antes de agregar en KPIs.",
    })

    # T4 — transaction_date con formato mixto
    if raw is not None:
        parsed_iso = F.to_date("transaction_date", "yyyy-MM-dd")
        n_mixed = raw.withColumn("_iso", parsed_iso).filter(F.col("_iso").isNull()).count()
    else:
        n_mixed = df.filter(F.col("transaction_date").isNull()).count()

    issues.append({
        "column": "transaction_date",
        "rows_affected": n_mixed,
        "impact": (
            "~10% de fechas en DD/MM/YYYY producen null con parseo ISO estandar - "
            "exclusion silenciosa de transacciones en agregaciones temporales."
        ),
        "fix": "Parsear primero como ISO, reintentar los null como DD/MM/YYYY.",
    })

    # T5 — duplicados por contenido (transaction_id distinto)
    if raw is not None:
        raw_tmp = raw.withColumn(
            "transaction_date",
            F.coalesce(
                F.to_date("transaction_date", "yyyy-MM-dd"),
                F.to_date("transaction_date", "dd/MM/yyyy"),
            ),
        )
        amount_clean = F.regexp_replace(F.regexp_replace(F.col("amount"), "\\.", ""), ",", ".")
        raw_tmp = raw_tmp.withColumn("amount", amount_clean.cast("double")).cache()
        n_total = raw_tmp.count()
        n_dedup = raw_tmp.dropDuplicates(DUP_COLS).count()
        n_dups = n_total - n_dedup
    else:
        n_total = df.count()
        n_dedup = df.dropDuplicates([c for c in DUP_COLS if c in df.columns]).count()
        n_dups = n_total - n_dedup

    issues.append({
        "column": "transaction_id",
        "rows_affected": n_dups,
        "impact": (
            "Duplicados de re-envio POS (transaction_id distinto, resto identico) "
            "inflan TPV y n_tx."
        ),
        "fix": "Deduplicar por (merchant_id, transaction_date, amount, status, channel), keep first.",
    })

    # T2 — last_complaint_date con leakage temporal
    if raw is not None:
        ref = F.to_date("reference_date")
        lcd = F.to_date("last_complaint_date")
        n_leakage = raw.withColumn("_ref", ref).withColumn("_lcd", lcd).filter(
            F.col("_lcd") > F.col("_ref")
        ).count()
    else:
        n_leakage = (
            df.filter(F.col("last_complaint_date") > F.col("reference_date")).count()
            if "last_complaint_date" in df.columns
            else 0
        )

    issues.append({
        "column": "last_complaint_date",
        "rows_affected": n_leakage,
        "impact": (
            "Fecha de queja post-reference_date es informacion futura no disponible en produccion. "
            "Usarla como feature causa leakage temporal severo."
        ),
        "fix": "Filtrar last_complaint_date <= reference_date; derivar 'days_since_complaint' con tope.",
    })

    # T1 — cancellation_reason solo rellena para churners (leakage directo al target)
    if raw is not None:
        non_null = (F.col("cancellation_reason").isNotNull()) & (F.col("cancellation_reason") != "")
        churn_1 = F.col("fla_churn90") == "1"
        row = raw.select(
            F.sum(F.when(non_null & churn_1, 1).otherwise(0)).alias("n_cancel_churn"),
            F.sum(F.when(non_null, 1).otherwise(0)).alias("n_cancel_total"),
        ).first()
        n_cancel_churn = row["n_cancel_churn"] or 0
        n_cancel_total = row["n_cancel_total"] or 0
        pct = n_cancel_churn / n_cancel_total if n_cancel_total else 0
    else:
        n_cancel_total = (
            df.filter(F.col("cancellation_reason").isNotNull()).count()
            if "cancellation_reason" in df.columns
            else 0
        )
        pct = 1.0

    issues.append({
        "column": "cancellation_reason",
        "rows_affected": n_cancel_total,
        "impact": (
            f"cancellation_reason se rellena ~{pct:.0%} de las veces solo cuando fla_churn90=1 - "
            "leakage directo del target si se usa como feature."
        ),
        "fix": "Excluir cancellation_reason de todos los features del modelo.",
    })

    # n_total (raw_tmp) is raw with recast columns, same row count — reuse instead of re-scanning.
    n_rows = n_total if raw is not None else df.count()
    n_cols = len(raw.columns) if raw is not None else len(df.columns)

    return {
        "issues": issues,
        "summary": {"n_rows": n_rows, "n_cols": n_cols, "n_issues": len(issues)},
    }


# -----------------------------------------------------------------------------
# 1.4  merchants_at_risk
# -----------------------------------------------------------------------------
def merchants_at_risk(df: DataFrame, top_n: int = 200) -> DataFrame:
    """
    Top `top_n` merchants con mayor senal debil de pre-churn. Score compuesto
    de 3 senales normalizadas 0-1 (detalle en DECISIONS.md):
      1. Caida de TPV reciente (30d) vs. mediana mensual historica.
      2. Approval rate bajo en los ultimos 30d.
      3. Complaint reciente (ultimos 30d antes de reference_date).
    """
    ref_date = df.select(F.max("reference_date")).first()[0]
    window_recent = F.date_sub(F.lit(ref_date), 30)

    is_approved = F.col("status") == "approved"

    # Mediana mensual historica de TPV por merchant
    monthly_tpv = (
        df.filter(is_approved)
        .withColumn("month", F.trunc("transaction_date", "month"))
        .groupBy("merchant_id", "month")
        .agg(F.sum("amount").alias("amount"))
    )
    median_tpv = monthly_tpv.groupBy("merchant_id").agg(
        F.expr("percentile(amount, 0.5)").alias("median_monthly_tpv")
    )

    recent = df.filter(F.col("transaction_date") >= window_recent)
    recent_agg = recent.groupBy("merchant_id").agg(
        F.sum(F.when(is_approved, F.col("amount")).otherwise(0.0)).alias("recent_tpv"),
        F.count("transaction_id").alias("recent_n"),
        F.sum(F.when(is_approved, 1).otherwise(0)).alias("recent_approved"),
    )

    complaints = (
        df.groupBy("merchant_id")
        # max(), not first(): deterministic across partitions and picks the
        # most recent complaint if a merchant ever has more than one on file.
        .agg(F.max("last_complaint_date").alias("last_complaint_date"))
        .withColumn(
            "has_recent_complaint",
            F.when(
                (F.col("last_complaint_date") >= window_recent)
                & (F.col("last_complaint_date") <= F.lit(ref_date)),
                1.0,
            ).otherwise(0.0),
        )
        .select("merchant_id", "has_recent_complaint")
    )

    scores = (
        median_tpv.join(recent_agg, "merchant_id", "outer")
        .join(complaints, "merchant_id", "outer")
        .na.fill(0.0)
    )

    scores = scores.withColumn(
        "approval_rate_recent",
        F.when(F.col("recent_n") != 0, F.col("recent_approved") / F.col("recent_n")).otherwise(0.0),
    )
    scores = scores.withColumn(
        "tpv_ratio",
        F.when(
            F.col("median_monthly_tpv") != 0, F.col("recent_tpv") / F.col("median_monthly_tpv")
        ).otherwise(0.0),
    )
    scores = scores.withColumn("tpv_ratio", F.least(F.greatest(F.col("tpv_ratio"), F.lit(0.0)), F.lit(3.0)))

    # Min-max global para cada senal (calculado una sola vez, sin loops por fila)
    bounds = scores.select(
        F.min("tpv_ratio").alias("tpv_min"),
        F.max("tpv_ratio").alias("tpv_max"),
        F.min("approval_rate_recent").alias("appr_min"),
        F.max("approval_rate_recent").alias("appr_max"),
        F.min("has_recent_complaint").alias("comp_min"),
        F.max("has_recent_complaint").alias("comp_max"),
    ).first()

    def minmax(col: str, lo: float, hi: float):
        rng = hi - lo
        if rng and rng > 0:
            return (F.col(col) - F.lit(lo)) / F.lit(rng)
        return F.lit(0.0)

    scores = (
        scores.withColumn("sig_tpv", F.lit(1.0) - minmax("tpv_ratio", bounds["tpv_min"], bounds["tpv_max"]))
        .withColumn(
            "sig_approval",
            F.lit(1.0) - minmax("approval_rate_recent", bounds["appr_min"], bounds["appr_max"]),
        )
        .withColumn(
            "sig_complaint", minmax("has_recent_complaint", bounds["comp_min"], bounds["comp_max"])
        )
    )

    scores = scores.withColumn(
        "risk_score",
        0.45 * F.col("sig_tpv") + 0.35 * F.col("sig_approval") + 0.20 * F.col("sig_complaint"),
    )

    scores = scores.withColumn(
        "top_signal",
        F.when(
            (F.col("sig_tpv") >= F.col("sig_approval")) & (F.col("sig_tpv") >= F.col("sig_complaint")),
            F.lit("tpv_drop"),
        )
        .when(F.col("sig_approval") >= F.col("sig_complaint"), F.lit("low_approval"))
        .otherwise(F.lit("complaint")),
    )

    return (
        scores.select("merchant_id", "risk_score", "top_signal")
        .orderBy(F.col("risk_score").desc(), F.col("merchant_id").asc())
        .limit(top_n)
    )


# -----------------------------------------------------------------------------
# Entry point — genera artefactos en outputs/ y tablas Delta en outputs/delta/
# -----------------------------------------------------------------------------
def main(csv_path: str) -> None:
    outputs = Path("outputs")
    outputs.mkdir(exist_ok=True)
    delta_root = outputs / "delta"

    spark = get_spark()
    try:
        df = load_clean(spark, csv_path).cache()
        df.write.format("delta").mode("overwrite").save(str(delta_root / "transactions_clean"))

        kpis = monthly_kpis(df)
        kpis.write.format("delta").mode("overwrite").save(str(delta_root / "monthly_kpis"))
        kpis.toPandas().to_csv(outputs / "monthly_kpis_spark.csv", index=False)

        report = quality_report(spark, df, raw_path=csv_path)
        (outputs / "quality_report_spark.json").write_text(json.dumps(report, indent=2, default=str))

        at_risk = merchants_at_risk(df, top_n=200)
        at_risk.write.format("delta").mode("overwrite").save(str(delta_root / "merchants_at_risk"))
        at_risk.toPandas().to_csv(outputs / "merchants_at_risk_spark.csv", index=False)

        print("Outputs generados en outputs/ y tablas Delta en outputs/delta/")
    finally:
        spark.stop()


if __name__ == "__main__":
    csv = sys.argv[1] if len(sys.argv) > 1 else "data/transactions_sample.csv"
    main(csv)
