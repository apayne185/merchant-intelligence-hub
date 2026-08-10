"""Generator for data/copilot_fixture_transactions.csv.

Produces a small (~200 row), deterministic transactions fixture with 4
merchants of deliberately distinct profiles, so copilot tool tests and the
golden-set eval (data/golden_set_copilot.json) have known-good expected
values to assert against. See data/README.md for the profile table and the
verified monthly_kpis/merchants_at_risk output this fixture produces.

Amounts are written in the same BR format ("1.234,56") as the real CSV so
src.parte1_pandas.load_clean() parses this fixture identically to the real
data — no special-casing needed in any tool that consumes it.

Usage:
    uv run python -m scripts.generate_copilot_fixture
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "data" / "copilot_fixture_transactions.csv"

SEED = 42
REFERENCE_DATE = date(2025, 9, 30)

MERCHANTS = {
    90001: dict(segment="SMB", mcc="5812", fla_churn90=1, base_amount=160.0, base_n=5,
                base_approval=0.94, complaint_date=date(2025, 9, 20),
                cancellation_reason="switching_provider"),
    90002: dict(segment="Enterprise", mcc="5411", fla_churn90=0, base_amount=1050.0, base_n=6,
                base_approval=0.98, complaint_date=None, cancellation_reason=None),
    90003: dict(segment="SMB", mcc="5691", fla_churn90=0, base_amount=210.0, base_n=4,
                base_approval=0.90, complaint_date=None, cancellation_reason=None),
    90004: dict(segment="Enterprise", mcc="5812", fla_churn90=0, base_amount=2400.0, base_n=6,
                base_approval=0.97, complaint_date=None, cancellation_reason=None),
}

# 90001/90002/90004 get Jul-Sep 2024 (so a YoY comparison exists) plus
# Jan-Sep 2025. 90003 gets only Apr-Sep 2025 — a deliberate edge case: no
# YoY comparison is possible for it.
FULL_MONTHS = [(2024, 7), (2024, 8), (2024, 9)] + [(2025, m) for m in range(1, 10)]
SHORT_MONTHS = [(2025, m) for m in range(4, 10)]

COLUMNS = ["transaction_id", "merchant_id", "transaction_date", "amount", "status", "channel",
           "cancellation_reason", "reference_date", "fla_churn90", "last_complaint_date",
           "segment", "mcc", "dat_process"]


def _month_days(year: int, month: int) -> int:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (nxt - date(year, month, 1)).days


def _fmt_amount_br(x: float) -> str:
    """"1234.56" -> "1.234,56" (thousands '.', decimal ',') — the same
    convention load_clean() expects (DECISIONS.md D1)."""
    whole, frac = f"{x:.2f}".split(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return ".".join(groups) + "," + frac


def generate_rows() -> list[dict]:
    random.seed(SEED)
    rows = []
    tx_id = 700000

    for merchant_id, cfg in MERCHANTS.items():
        months = FULL_MONTHS if merchant_id != 90003 else SHORT_MONTHS
        for (yr, mo) in months:
            # The churn story: 90001 is healthy through Jun-2025, then TPV
            # and approval rate collapse Jul-Sep 2025.
            if merchant_id == 90001 and (yr, mo) >= (2025, 7):
                months_into_decline = (yr - 2025) * 12 + (mo - 7) + 1
                amount_mult = max(0.15, 1.0 - 0.30 * months_into_decline)
                approval = max(0.35, cfg["base_approval"] - 0.20 * months_into_decline)
                n_tx = max(2, cfg["base_n"] - months_into_decline)
            else:
                month_idx = (FULL_MONTHS if (yr, mo) in FULL_MONTHS else SHORT_MONTHS).index((yr, mo))
                amount_mult = 1.0 + 0.01 * month_idx if merchant_id != 90001 else 1.0
                approval = cfg["base_approval"]
                n_tx = cfg["base_n"]

            n_days = _month_days(yr, mo)
            for i in range(n_tx):
                day = min(n_days, 2 + (i * (n_days - 3) // max(1, n_tx - 1)) if n_tx > 1 else 15)
                tx_date = date(yr, mo, day)
                if tx_date > REFERENCE_DATE:
                    continue

                amount = round(cfg["base_amount"] * amount_mult * random.uniform(0.85, 1.15), 2)
                roll = random.random()
                if roll < approval:
                    status = "approved"
                elif roll < approval + 0.06:
                    status = "reversed"
                else:
                    status = "denied"
                channel = "ecom" if random.random() < 0.6 else "pos"

                tx_id += 1
                rows.append(dict(
                    transaction_id=tx_id,
                    merchant_id=merchant_id,
                    transaction_date=tx_date.isoformat(),
                    amount=_fmt_amount_br(amount),
                    status=status,
                    channel=channel,
                    cancellation_reason=(cfg["cancellation_reason"] or "") if cfg["fla_churn90"] == 1 else "",
                    reference_date=REFERENCE_DATE.isoformat(),
                    fla_churn90=cfg["fla_churn90"],
                    last_complaint_date=cfg["complaint_date"].isoformat() if cfg["complaint_date"] else "",
                    segment=cfg["segment"],
                    mcc=cfg["mcc"],
                    dat_process=(tx_date + timedelta(days=random.randint(0, 1))).isoformat(),
                ))

    rows.sort(key=lambda r: (r["transaction_date"], r["transaction_id"]))
    return rows


def main() -> None:
    rows = generate_rows()
    with OUT_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUT_PATH.relative_to(REPO_ROOT)}")
    for merchant_id in MERCHANTS:
        n = sum(1 for r in rows if r["merchant_id"] == merchant_id)
        print(f"  merchant {merchant_id}: {n} rows")


if __name__ == "__main__":
    main()
