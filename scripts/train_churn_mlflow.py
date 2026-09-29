"""
Retrains the churn model with an MLflow-tracked hyperparameter sweep and
logs feature drift (src/mlops/churn_training.py, DECISIONS.md D57).

    # Against the compose tracking server (`docker compose --profile mlops up -d`):
    MLFLOW_TRACKING_URI=http://localhost:5000 uv run --extra mlops python -m scripts.train_churn_mlflow

    # Fully offline (local file store under ./mlruns):
    uv run --extra mlops python -m scripts.train_churn_mlflow --tracking-uri file:./mlruns

    # Drift against a newer snapshot instead of the holdout split:
    ... --current-csv path/to/new_snapshot.csv
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.copilot.tools.data_analyst import default_csv_path, get_clean_transactions
from src.mlops.churn_training import train_and_log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=None, help="training transactions (default: real CSV, else fixture)")
    ap.add_argument("--current-csv", type=Path, default=None, help="newer snapshot to measure drift against")
    ap.add_argument("--tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI", "file:./mlruns"))
    ap.add_argument("--experiment", default="merchant-churn")
    ap.add_argument("--register-as", default=None, help="Model Registry name (needs a tracking server)")
    args = ap.parse_args(argv)

    csv = args.csv or default_csv_path()
    result = train_and_log(
        get_clean_transactions(csv),
        tracking_uri=args.tracking_uri,
        experiment=args.experiment,
        current_df=get_clean_transactions(args.current_csv) if args.current_csv else None,
        register_as=args.register_as,
        data_source=csv.name,
    )
    print(
        json.dumps(
            {
                "parent_run_id": result.parent_run_id,
                "best_params": result.best_params,
                "test_metrics": {k: round(v, 4) for k, v in result.best_metrics.items()},
                "drift_max_psi": result.drift["max_psi"],
                "drift_features_in_alert": result.drift["features_in_alert"],
                "model_uri": result.model_uri,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
