-- =============================================================================
-- Parte 2 · SQL sobre warehouse (10 pts)
-- =============================================================================
-- Estilo esperado: Spark SQL / Databricks SQL (equivalente a ANSI con LAG, etc.)
-- No necesitas ejecutar las queries. Escríbelas y comenta supuestos.
--
-- Esquema:
--   merchants(merchant_id, country, mcc, onboarding_date, segment)
--   transactions(transaction_id, merchant_id, transaction_date, amount, status,
--                channel, dat_process)
--   churn_labels(merchant_id, reference_date, fla_churn90)
--
-- Supuestos documentados también en DECISIONS.md.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1 (3 pts) — Top 10 merchants brasileños por TPV aprobado de Q3 2025.
-- Devuelve: merchant_id, tpv, approval_rate, mcc
-- -----------------------------------------------------------------------------
-- Supuesto: Q3 2025 = 2025-07-01 a 2025-09-30 (inclusive).
-- Supuesto: TPV = suma de amount donde status = 'approved'.
-- Supuesto: approval_rate = n_approved / n_total por merchant en el periodo.
-- Supuesto: country = 'BR' identifica merchants brasileños en la tabla merchants.
-- Optimización: filtro sobre dat_process (columna de partición) acota el scan
--   al rango Q3 2025 sin leer particiones fuera del periodo (ver Q4).

SELECT
    t.merchant_id,
    SUM(CASE WHEN t.status = 'approved' THEN t.amount ELSE 0 END)          AS tpv,
    ROUND(
        SUM(CASE WHEN t.status = 'approved' THEN 1 ELSE 0 END)
        / COUNT(*)
    , 4)                                                                    AS approval_rate,
    m.mcc
FROM transactions t
JOIN merchants m
    ON t.merchant_id = m.merchant_id
WHERE
    m.country = 'BR'
    -- Partition pruning: el optimizador Spark/Databricks elimina particiones fuera del rango
    AND t.dat_process BETWEEN '2025-07-01' AND '2025-09-30'
    -- Filtro adicional sobre transaction_date: no asumimos dat_process == transaction_date
    -- (dat_process es la fecha de ETL/procesamiento, no necesariamente la de negocio;
    -- si difieren cerca de un limite de trimestre, este filtro evita incluir/excluir
    -- transacciones por error aunque dat_process ya haya hecho partition pruning grueso)
    AND t.transaction_date BETWEEN '2025-07-01' AND '2025-09-30'
GROUP BY
    t.merchant_id,
    m.mcc
ORDER BY tpv DESC
LIMIT 10;


-- -----------------------------------------------------------------------------
-- Q2 (3 pts) — % de merchants con fla_churn90 = 1 por (country, segment)
-- a reference_date = '2025-09-30'. Solo segmentos con >= 100 merchants.
-- -----------------------------------------------------------------------------
-- Supuesto: churn_labels puede tener múltiples reference_dates por merchant;
--   filtramos la snapshot concreta del 30-sep.
-- Supuesto: un merchant = una fila en churn_labels por reference_date.
-- Supuesto: "segmentos con >= 100 merchants" aplica por (country, segment) combinado.

SELECT
    m.country,
    m.segment,
    COUNT(*)                                                                 AS n_merchants,
    SUM(cl.fla_churn90)                                                      AS n_churned,
    ROUND(SUM(cl.fla_churn90) / COUNT(*), 4)                                AS pct_churn
FROM churn_labels cl
JOIN merchants m
    ON cl.merchant_id = m.merchant_id
WHERE
    cl.reference_date = '2025-09-30'
GROUP BY
    m.country,
    m.segment
HAVING
    COUNT(*) >= 100
ORDER BY
    pct_churn DESC;


-- -----------------------------------------------------------------------------
-- Q3 (3 pts) — Por merchant, TPV mensual 2025 y TPV mismo mes año anterior (YoY).
-- Solo los 12 meses de 2025.
-- -----------------------------------------------------------------------------
-- Supuesto: TPV = suma de amount donde status = 'approved'.
-- Supuesto: "mismo mes año anterior" = mismo número de mes en 2024.
-- LAG(tpv, 12) OVER (PARTITION BY merchant_id ORDER BY month_start) sería
-- sintaxis Spark SQL válida (offset fijo, no hay nada no-estándar en eso),
-- pero asume una serie mensual contigua sin huecos: si un merchant no tuvo
-- transacciones en algún mes, ese mes simplemente no existe como fila en
-- monthly_tpv, y LAG(tpv,12) se desplazaría al mes anterior disponible en
-- vez de al mismo mes del año anterior — comparando, ej., enero 2025 con
-- noviembre 2024. El self-join empareja explícitamente por (merchant_id, mo),
-- así que es robusto a huecos en la serie mensual.

WITH monthly_tpv AS (
    SELECT
        merchant_id,
        DATE_TRUNC('month', transaction_date)                               AS month_start,
        YEAR(transaction_date)                                              AS yr,
        MONTH(transaction_date)                                             AS mo,
        SUM(CASE WHEN status = 'approved' THEN amount ELSE 0 END)          AS tpv
    FROM transactions
    WHERE
        -- Cubre tanto 2024 (para YoY) como 2025
        YEAR(transaction_date) IN (2024, 2025)
        -- Partition pruning: acota el scan a los 2 años que necesita el YoY
        -- (Q2 no tiene un filtro equivalente porque no toca `transactions`
        -- en absoluto — solo hace join contra churn_labels/merchants)
        AND dat_process BETWEEN '2024-01-01' AND '2025-12-31'
    GROUP BY
        merchant_id,
        DATE_TRUNC('month', transaction_date),
        YEAR(transaction_date),
        MONTH(transaction_date)
)
SELECT
    cur.merchant_id,
    cur.month_start                                                         AS month_2025,
    cur.tpv                                                                 AS tpv_2025,
    prev.tpv                                                                AS tpv_2024,
    ROUND((cur.tpv - prev.tpv) / NULLIF(prev.tpv, 0), 4)                  AS tpv_yoy_pct
FROM monthly_tpv cur
LEFT JOIN monthly_tpv prev
    ON  cur.merchant_id = prev.merchant_id
    AND cur.mo          = prev.mo
    AND prev.yr         = 2024
WHERE
    cur.yr = 2025
ORDER BY
    cur.merchant_id,
    cur.month_start;


-- -----------------------------------------------------------------------------
-- Q4 (1 pt) — Ventaja del particionado por dat_process en Q1
-- -----------------------------------------------------------------------------
-- Al filtrar `dat_process BETWEEN '2025-07-01' AND '2025-09-30'`, Spark/Databricks
-- aplica partition pruning: descarta directamente los ficheros de particiones fuera
-- del rango Q3 y evita leer y deserializar los ~9 meses restantes del año.
-- En una tabla con 200M filas/año, esto reduce el scan de datos en ~75% y elimina
-- cualquier shuffle innecesario para el filtro de fechas.
