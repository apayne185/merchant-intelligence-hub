# Model Card — Merchant Churn Classifier

## Model
- **Type**: LightGBM binary classifier
- **Target**: `fla_churn90` (churn within 90 days of reference date)
- **Reference date**: 2025-09-30

## Features (20 total)
- **Numeric**: tpv_total, n_tx_total, approval_rate_total, n_months_active, tpv_3m, n_tx_3m, approval_rate_3m, pct_ecom_3m, avg_tx_3m, tpv_6m, n_tx_6m, approval_rate_6m, pct_ecom_6m, avg_tx_6m, tpv_trend_3m_6m, log_tpv_total, log_tpv_3m, days_since_complaint
- **Categorical**: segment, mcc

## Training Data
- 7,973 merchants in train, 1,994 in test (80/20 stratified)
- Churn rate: ~8.75% (1:10.4 imbalance handled via `scale_pos_weight=10`)

## Metrics (test set)
- ROC-AUC:       0.5828
- PR-AUC:        0.1077
- Brier score:   0.1248
- Recall@1%:     0.0172
- Recall@5%:     0.069
- Recall@10%:    0.1379

## Key Exclusions (leakage prevention)
- `cancellation_reason` — T1: filled only for churners (direct leakage)
- `last_complaint_date` raw — T2: some dates are post-reference (temporal leakage)
- Transactions dated > reference_date — undocumented trap: future activity

## Limitations
1. **Discrimination is weak (ROC-AUC 0.5828 ≈ near-random)** — only
   safe to use for coarse deprioritization at high k (≥10%), not for precise
   targeting at low k. At recall@1% (0.0172), the model
   catches essentially none of the churners in the top 1% ranked — not
   meaningfully better than a random selection of that size.
2. Single snapshot — no temporal CV possible with this dataset. Train/test is
   a stratified split of merchants sharing the same reference_date, not a
   held-out future period, so these metrics don't validate generalization to
   a genuinely future snapshot — only to unseen merchants from the same period.
3. Synthetic data — distribution may not match production Brazil merchants
4. TPV trend limited to 3m/6m windows; longer lookbacks could improve signal
5. No calibration step applied — probabilities may be miscalibrated
