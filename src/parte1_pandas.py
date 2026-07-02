"""
Parte 1 · Análisis exploratorio en pandas
==========================================

4 funciones: load_clean, monthly_kpis, quality_report, merchants_at_risk.

Reglas:
- Código vectorizado. NO loops sobre filas.
- Type hints en las firmas públicas.
- Decisiones documentadas en `DECISIONS.md`, no aquí.
- El CSV tiene problemas de calidad plantados a propósito (ver DECISIONS.md).

Ejecuta con:
    python -m src.parte1_pandas data/transactions_sample.csv
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd


# -----------------------------------------------------------------------------
# 1.1  load_clean
# -----------------------------------------------------------------------------
def load_clean(path: str | Path) -> pd.DataFrame:
    """
    Carga el CSV y devuelve un DataFrame listo para análisis.

    Decisiones documentadas en DECISIONS.md. Resumen:
    - T3: amount en formato BR "1.234,56" - float (quitar puntos de miles, coma-punto).
    - T4: transaction_date mezcla YYYY-MM-DD y DD/MM/YYYY - parseo unificado.
    - T5: duplicados con transaction_id distinto pero resto idéntico - se eliminan.
    - T2: last_complaint_date post reference_date - flag de leakage, NO se usa como feature.
    - T1: cancellation_reason solo relleno cuando fla_churn90=1 - leakage, excluir del modelo.
    - Nulls en amount (~3%) - se imputan con mediana por segmento antes de KPIs.

    Args:
        path: ruta al CSV.

    Returns:
        DataFrame limpio.
    """
    df = pd.read_csv(path, dtype=str, low_memory=False)

    # --- T4: unificar formatos de fecha en transaction_date ---
    # ~90% YYYY-MM-DD, ~10% DD/MM/YYYY — parsear en dos pasadas
    raw_dates = df["transaction_date"]
    df["transaction_date"] = pd.to_datetime(raw_dates, dayfirst=False, errors="coerce")
    nat_mask = df["transaction_date"].isna()
    if nat_mask.any():
        df.loc[nat_mask, "transaction_date"] = pd.to_datetime(
            raw_dates[nat_mask], dayfirst=True, errors="coerce"
        )

    # --- Other date columns ---
    df["reference_date"] = pd.to_datetime(df["reference_date"], errors="coerce")
    df["last_complaint_date"] = pd.to_datetime(df["last_complaint_date"], errors="coerce")

    # --- T3: amount formato BR "1.234,56" - float ---
    df["amount"] = (
        df["amount"]
        .str.replace(".", "", regex=False)   # quitar separador de miles
        .str.replace(",", ".", regex=False)  # coma decimal - punto
        .replace("", pd.NA)                 # vacíos - NA (T3 genera ~3% NaN)
    )
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")

    # --- Tipos básicos ---
    df["transaction_id"] = pd.to_numeric(df["transaction_id"], errors="coerce").astype("Int64")
    df["merchant_id"] = pd.to_numeric(df["merchant_id"], errors="coerce").astype("Int64")
    # fla_churn90 es un flag 0/1: to_numeric(errors="coerce") solo captura
    # strings no-numéricos -> NaN, pero un entero fuera de rango (ej. "200")
    # pasaría to_numeric sin error y luego rompería el cast a Int8. Forzar a
    # {0, 1, NA} explícitamente antes de castear.
    fla_churn90_numeric = pd.to_numeric(df["fla_churn90"], errors="coerce")
    df["fla_churn90"] = fla_churn90_numeric.where(fla_churn90_numeric.isin([0, 1])).astype("Int8")

    # Categoricals para columnas de baja cardinalidad
    for col in ["status", "channel", "segment", "mcc", "cancellation_reason"]:
        if col in df.columns:
            df[col] = df[col].astype("category")

    # --- Imputar amount nulo antes de dedup (NaN != NaN rompe drop_duplicates) ---
    df["amount"] = df["amount"].fillna(
        df.groupby("segment", observed=True)["amount"].transform("median")
    )

    # --- T5: eliminar duplicados (mismo contenido, transaction_id distinto) ---
    dup_cols = ["merchant_id", "transaction_date", "amount", "status", "channel"]
    df = df.drop_duplicates(subset=dup_cols, keep="first").reset_index(drop=True)

    return df


# -----------------------------------------------------------------------------
# 1.2  monthly_kpis
# -----------------------------------------------------------------------------
def monthly_kpis(df: pd.DataFrame) -> pd.DataFrame:
    """
    KPIs mensuales por merchant. Una fila por (merchant_id, month).

    Columnas de salida:
      - merchant_id
      - month               (primer día del mes, dtype datetime64[ns])
      - tpv                 (suma de amount para status == 'approved')
      - approval_rate       (% transacciones aprobadas sobre total)
      - pct_ecom            (% del TPV que viene del canal 'ecom')
      - n_tx                (número de transacciones del mes)

    Vectorizado. Sin loops.

    Args:
        df: DataFrame ya limpio (output de load_clean).

    Returns:
        DataFrame con los KPIs.
    """
    df = df.copy()
    df["month"] = df["transaction_date"].dt.to_period("M").dt.to_timestamp()

    approved = df[df["status"] == "approved"]
    ecom_approved = df[(df["status"] == "approved") & (df["channel"] == "ecom")]

    grp = ["merchant_id", "month"]

    tpv = approved.groupby(grp, observed=True)["amount"].sum().rename("tpv")
    n_tx = df.groupby(grp, observed=True)["transaction_id"].count().rename("n_tx")
    n_approved = approved.groupby(grp, observed=True)["transaction_id"].count().rename("n_approved")
    tpv_ecom = ecom_approved.groupby(grp, observed=True)["amount"].sum().rename("tpv_ecom")

    result = (
        pd.concat([tpv, n_tx, n_approved, tpv_ecom], axis=1)
        .fillna(0)
        .reset_index()
    )
    result["approval_rate"] = result["n_approved"] / result["n_tx"]
    tpv_safe = result["tpv"].where(result["tpv"] != 0)  # float NaN where tpv==0
    # clip: si algún amount 'approved' fuese negativo (reversal mal etiquetado,
    # no se observa en el CSV actual pero no está garantizado por el schema),
    # tpv podría acercarse a 0 o volverse negativo mientras tpv_ecom no, lo
    # que sacaría pct_ecom fuera de [0, 1] — el rango documentado del KPI.
    result["pct_ecom"] = (result["tpv_ecom"] / tpv_safe).fillna(0.0).clip(0.0, 1.0)

    return result[["merchant_id", "month", "tpv", "approval_rate", "pct_ecom", "n_tx"]]


# -----------------------------------------------------------------------------
# 1.3  quality_report
# -----------------------------------------------------------------------------
def quality_report(df: pd.DataFrame, raw_path: str | Path = "data/transactions_sample.csv") -> dict[str, Any]:
    """
    Devuelve un reporte de calidad de datos con al menos 5 problemas detectados.

    Acepta el DataFrame limpio (output de load_clean). Para conteos exactos
    del CSV original re-lee desde `raw_path` si existe; si no, deriva métricas
    del df recibido.

    Estructura esperada:
        {
          "issues": [...],
          "summary": {"n_rows": int, "n_cols": int, "n_issues": int}
        }
    """
    raw_path = Path(raw_path)
    if raw_path.exists():
        raw = pd.read_csv(raw_path, dtype=str, low_memory=False)
    else:
        raw = None  # fallback: usamos el df limpio para lo que podamos

    issues: list[dict[str, Any]] = []

    # T3 — amount en formato BR (coma decimal, punto de miles)
    if raw is not None:
        amount_br = raw["amount"].str.contains(",", na=False)
        n_br = int(amount_br.sum())
        n_null = int((raw["amount"].isna() | (raw["amount"].str.strip() == "")).sum())
    else:
        n_br = int(df["amount"].notna().sum())  # ya parseados, aproximamos
        n_null = int(df["amount"].isna().sum())

    issues.append({
        "column": "amount",
        "rows_affected": n_br,
        "impact": (
            "pd.read_csv interpreta '1.234,56' como string. "
            "Cálculos de TPV fallan silenciosamente si no se parsea."
        ),
        "fix": "Quitar puntos de miles, coma-punto decimal, pd.to_numeric(errors='coerce').",
    })

    issues.append({
        "column": "amount",
        "rows_affected": n_null,
        "impact": "TPV subestimado; introduce sesgo en métricas mensuales.",
        "fix": "Imputar con mediana por segmento antes de agregar en KPIs.",
    })

    # T4 — transaction_date con formato mixto
    if raw is not None:
        parsed_iso = pd.to_datetime(raw["transaction_date"], format="%Y-%m-%d", errors="coerce")
        n_mixed = int(parsed_iso.isna().sum())
    else:
        n_mixed = int(df["transaction_date"].isna().sum())

    issues.append({
        "column": "transaction_date",
        "rows_affected": n_mixed,
        "impact": (
            "~10% de fechas en DD/MM/YYYY producen NaT con parseo estándar - "
            "exclusión silenciosa de transacciones en agregaciones temporales."
        ),
        "fix": "Parsear primero con dayfirst=False, reintentar NaT con dayfirst=True.",
    })

    # T5 — duplicados por contenido (transaction_id distinto)
    dup_cols = ["merchant_id", "transaction_date", "amount", "status", "channel"]
    if raw is not None:
        raw_tmp = raw.copy()
        # Mismo parseo en dos pasadas que load_clean (T4): un solo pase deja
        # ~10% de fechas DD/MM/YYYY en NaT, lo que descuadra qué filas cuentan
        # como duplicado real vs. cuáles solo comparten un NaT espurio.
        raw_dates = raw_tmp["transaction_date"]
        parsed = pd.to_datetime(raw_dates, dayfirst=False, errors="coerce")
        nat_mask = parsed.isna()
        if nat_mask.any():
            parsed.loc[nat_mask] = pd.to_datetime(raw_dates[nat_mask], dayfirst=True, errors="coerce")
        raw_tmp["transaction_date"] = parsed
        raw_tmp["amount"] = pd.to_numeric(
            raw_tmp["amount"].str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
            errors="coerce",
        )
        # Igual que load_clean: imputar amount nulo con la mediana por segment
        # antes de deduplicar. Sin esto, dos filas con amount=NaN se agrupan
        # entre si (NaN==NaN en duplicated()) en vez de con su mediana real,
        # lo que descuadra el conteo final en un puñado de filas.
        raw_tmp["amount"] = raw_tmp["amount"].fillna(
            raw_tmp.groupby("segment", observed=True)["amount"].transform("median")
        )
        n_dups = int(raw_tmp.duplicated(subset=dup_cols).sum())
    else:
        n_dups = int(df.duplicated(subset=[c for c in dup_cols if c in df.columns]).sum())

    issues.append({
        "column": "transaction_id",
        "rows_affected": n_dups,
        "impact": (
            "Duplicados de re-envío POS (transaction_id distinto, resto idéntico) "
            "inflan TPV y n_tx ~2%."
        ),
        "fix": "Deduplicar por (merchant_id, transaction_date, amount, status, channel), keep='first'.",
    })

    # T2 — last_complaint_date con leakage temporal
    if raw is not None:
        ref = pd.to_datetime(raw["reference_date"], errors="coerce")
        lcd = pd.to_datetime(raw["last_complaint_date"], errors="coerce")
        n_leakage = int((lcd > ref).sum())
    else:
        ref = df["reference_date"]
        lcd = df["last_complaint_date"]
        n_leakage = int((lcd > ref).sum()) if "last_complaint_date" in df.columns else 0

    issues.append({
        "column": "last_complaint_date",
        "rows_affected": n_leakage,
        "impact": (
            "Fecha de queja post-reference_date es información futura no disponible en producción. "
            "Usarla como feature causa leakage temporal severo."
        ),
        "fix": "Filtrar last_complaint_date <= reference_date; derivar 'days_since_complaint' con tope.",
    })

    # T1 — cancellation_reason solo rellena para churners (leakage directo al target)
    if raw is not None:
        non_null = raw["cancellation_reason"].notna() & (raw["cancellation_reason"] != "")
        churn_1 = raw["fla_churn90"] == "1"
        n_cancel_churn = int((non_null & churn_1).sum())
        n_cancel_total = int(non_null.sum())
        pct = n_cancel_churn / n_cancel_total if n_cancel_total else 0
    else:
        n_cancel_total = int(df["cancellation_reason"].notna().sum()) if "cancellation_reason" in df.columns else 0
        pct = 1.0  # si no hay raw, documentamos el problema por diseño

    issues.append({
        "column": "cancellation_reason",
        "rows_affected": n_cancel_total,
        "impact": (
            f"cancellation_reason se rellena ~{pct:.0%} de las veces solo cuando fla_churn90=1 - "
            "leakage directo del target si se usa como feature."
        ),
        "fix": "Excluir cancellation_reason de todos los features del modelo.",
    })

    n_rows = len(raw) if raw is not None else len(df)
    n_cols = len(raw.columns) if raw is not None else len(df.columns)

    return {
        "issues": issues,
        "summary": {
            "n_rows": n_rows,
            "n_cols": n_cols,
            "n_issues": len(issues),
        },
    }


# -----------------------------------------------------------------------------
# 1.4  merchants_at_risk
# -----------------------------------------------------------------------------
def merchants_at_risk(df: pd.DataFrame, top_n: int = 200) -> pd.DataFrame:
    """
    Devuelve los `top_n` merchants con mayor "señal débil" de pre-churn.

    Heurística: score compuesto de 3 señales normalizadas (0-1 cada una):
      1. Caída de TPV reciente: (TPV últimos 30d) / (TPV mediano mensual) — menor es peor.
      2. Tasa de aprobación baja: approval_rate de los últimos 30d.
      3. Complaints recientes: 1 si last_complaint_date en los últimos 30d antes de reference_date.

    Justificación detallada en DECISIONS.md.

    Columnas mínimas esperadas:
      - merchant_id
      - risk_score          (float, mayor = más riesgo / higher = more risk)
      - top_signal          (str, qué señal dominó tu score / which signal dominated the score)

    Args:
        df: DataFrame limpio.
        top_n: número de merchants a devolver.

    Returns:
        DataFrame ordenado por risk_score descendente.
    """
    ref_date = df["reference_date"].max()
    window_recent = ref_date - pd.Timedelta(days=30)

    # TPV mensual histórico por merchant (baseline)
    df_monthly = df.copy()
    df_monthly["month"] = df_monthly["transaction_date"].dt.to_period("M")
    monthly_tpv = (
        df_monthly[df_monthly["status"] == "approved"]
        .groupby(["merchant_id", "month"], observed=True)["amount"]
        .sum()
        .reset_index()
    )
    median_tpv = monthly_tpv.groupby("merchant_id", observed=True)["amount"].median().rename("median_monthly_tpv")

    # TPV últimos 30 días
    recent = df[df["transaction_date"] >= window_recent]
    recent_tpv = (
        recent[recent["status"] == "approved"]
        .groupby("merchant_id", observed=True)["amount"]
        .sum()
        .rename("recent_tpv")
    )

    # Tasa de aprobación últimos 30 días
    recent_n = recent.groupby("merchant_id", observed=True)["transaction_id"].count().rename("recent_n")
    recent_approved = (
        recent[recent["status"] == "approved"]
        .groupby("merchant_id", observed=True)["transaction_id"]
        .count()
        .rename("recent_approved")
    )

    # Complaint en últimos 30d
    complaints = (
        df.groupby("merchant_id", observed=True)["last_complaint_date"]
        .first()
        .reset_index()
    )
    complaints["has_recent_complaint"] = (
        (complaints["last_complaint_date"] >= window_recent) &
        (complaints["last_complaint_date"] <= ref_date)
    ).astype(float)
    complaints = complaints.set_index("merchant_id")["has_recent_complaint"]

    # Unir todo
    scores = (
        pd.concat([median_tpv, recent_tpv, recent_n, recent_approved, complaints], axis=1)
        .fillna(0)
    )
    scores.index.name = "merchant_id"
    scores = scores.reset_index()

    # Normalizar
    scores["approval_rate_recent"] = scores["recent_approved"] / scores["recent_n"].replace(0, pd.NA)
    scores["approval_rate_recent"] = scores["approval_rate_recent"].fillna(0)

    # TPV ratio: caída relativa (menor - más riesgo)
    denom = scores["median_monthly_tpv"].where(scores["median_monthly_tpv"] != 0)
    scores["tpv_ratio"] = (scores["recent_tpv"] / denom).fillna(0.0).clip(0, 3).astype(float)

    # Normalizar cada señal al rango 0-1
    def minmax(s: pd.Series, invert: bool = False) -> pd.Series:
        """Sin varianza (Serie vacia, o todos los merchants empatados en esta
        señal) no hay nada contra que comparar -> neutral (0.0). Si se
        invirtiera un minmax de 0.0 (`1 - 0.0 = 1.0`), un merchant sin señal
        de comparacion aparenteria maximo riesgo, que es lo opuesto de lo
        que se quiere decir con "sin señal".
        """
        rng = s.max() - s.min()
        if pd.isna(rng) or rng <= 0:
            return pd.Series(0.0, index=s.index)
        norm = (s - s.min()) / rng
        return 1 - norm if invert else norm

    # Riesgo = bajo TPV ratio + baja approval rate + complaint reciente
    scores["sig_tpv"] = minmax(scores["tpv_ratio"], invert=True)           # menor ratio = más riesgo
    scores["sig_approval"] = minmax(scores["approval_rate_recent"], invert=True)
    scores["sig_complaint"] = minmax(scores["has_recent_complaint"])

    # Score compuesto ponderado
    scores["risk_score"] = (
        0.45 * scores["sig_tpv"] +
        0.35 * scores["sig_approval"] +
        0.20 * scores["sig_complaint"]
    )

    # Señal dominante
    sig_cols = {"tpv_drop": scores["sig_tpv"], "low_approval": scores["sig_approval"], "complaint": scores["sig_complaint"]}
    scores["top_signal"] = pd.concat(sig_cols, axis=1).idxmax(axis=1)

    result = (
        scores[["merchant_id", "risk_score", "top_signal"]]
        .sort_values("risk_score", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    return result


# -----------------------------------------------------------------------------
# Entry point — genera artefactos en outputs/
# -----------------------------------------------------------------------------
def main(csv_path: str) -> None:
    """Carga, calcula KPIs y reporte, y persiste en outputs/.
    """
    outputs = Path("outputs")
    outputs.mkdir(exist_ok=True)

    df = load_clean(csv_path)

    kpis = monthly_kpis(df)
    kpis.to_csv(outputs / "monthly_kpis.csv", index=False)

    report = quality_report(df)
    (outputs / "quality_report.json").write_text(json.dumps(report, indent=2, default=str))

    at_risk = merchants_at_risk(df, top_n=200)
    at_risk.to_csv(outputs / "merchants_at_risk.csv", index=False)

    print("✓ Outputs generados en outputs/")


if __name__ == "__main__":
    csv = sys.argv[1] if len(sys.argv) > 1 else "data/transactions_sample.csv"
    main(csv)
