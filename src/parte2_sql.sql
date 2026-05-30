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
-- Documenta cualquier supuesto en `DECISIONS.md`.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1 (3 pts) — Top 10 merchants brasileños por TPV aprobado de Q3 2025.
-- Devuelve: merchant_id, tpv, approval_rate, mcc
-- -----------------------------------------------------------------------------
-- TODO: tu query aquí
SELECT 1;  -- placeholder, reemplazar


-- -----------------------------------------------------------------------------
-- Q2 (3 pts) — % de merchants con fla_churn90 = 1 por (country, segment)
-- a reference_date = '2025-09-30'. Solo segmentos con >= 100 merchants.
-- -----------------------------------------------------------------------------
-- TODO: tu query aquí
SELECT 1;  -- placeholder, reemplazar


-- -----------------------------------------------------------------------------
-- Q3 (3 pts) — Por merchant, TPV mensual 2025 y TPV mismo mes año anterior (YoY).
-- Solo los 12 meses de 2025.
-- -----------------------------------------------------------------------------
-- TODO: tu query aquí (sugerencia: LAG o self-join por mes)
SELECT 1;  -- placeholder, reemplazar


-- -----------------------------------------------------------------------------
-- Q4 (1 pt) — En 2 líneas: ¿qué ventaja te da que `transactions` esté
-- particionada por `dat_process` al hacer la Q1?
-- -----------------------------------------------------------------------------
-- TODO: tu respuesta como comentario aquí:
--
--
