"""
🔒 INTERNO — NO INCLUIR EN EL ZIP DEL CANDIDATO 🔒
================================================================================
Generador determinista de los datasets del take-home Getnet AI Lab.

Produce:
  - data/transactions_sample.csv   (~200k filas con 5 trampas plantadas)
  - data/merchants_context.json    (~500 merchants para la tool del agente)

Trampas plantadas (NO documentar al candidato):
  T1 · `cancellation_reason` post-evento → solo rellena cuando fla_churn90 = 1.
  T2 · `last_complaint_date` calculada sobre TODO el histórico, incluyendo
       fechas posteriores a reference_date (leakage temporal).
  T3 · `amount` como string formato BR/ES `"1.234,56"` con coma decimal y
       punto como separador de miles.
  T4 · `transaction_date` mezcla formatos: ~90% YYYY-MM-DD, ~10% DD/MM/YYYY.
  T5 · ~2% duplicados con `transaction_id` distinto pero el resto idéntico
       (simula re-envío de POS).

Bonus implícito:
  - Target ~8% positivos (desbalanceo).
  - Algunos merchants sin contexto en `merchants_context.json`.

⚠️ Generador stdlib-only (sin pandas/numpy) para poder ejecutarlo en entornos
restringidos. Es deliberadamente lento (~30-60s para 200k filas) pero portable.

Uso:
    python data/_generator.py --seed 42 --n-merchants 10000 --n-transactions 200000
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFERENCE_DATE = date(2025, 9, 30)
WINDOW_START = date(2024, 1, 1)
WINDOW_END = date(2025, 12, 31)
WINDOW_DAYS = (WINDOW_END - WINDOW_START).days + 1

SEGMENTS_WEIGHTS = [("SMB", 0.85), ("MidMarket", 0.12), ("Enterprise", 0.03)]
CHANNELS_WEIGHTS = [("pos", 0.55), ("ecom", 0.20), ("pix", 0.15), ("tef", 0.10)]
STATUS_WEIGHTS = [("approved", 0.88), ("denied", 0.10), ("reversed", 0.02)]
MCCS = ["5411", "5812", "5732", "5912", "7011", "5651", "4789", "5311", "5499", "7995"]

CANCEL_REASONS_WEIGHTS = [
    ("high_fees", 0.20),
    ("customer_service", 0.18),
    ("technical_issues", 0.15),
    ("competitor_better_terms", 0.20),
    ("bankruptcy", 0.05),
    ("fraud_concerns", 0.04),
    ("device_problems", 0.10),
    (None, 0.08),
]

CHURN_PROB_BY_SEGMENT = {"SMB": 0.10, "MidMarket": 0.05, "Enterprise": 0.02}
AMOUNT_MU_BY_SEGMENT = {"SMB": 4.0, "MidMarket": 6.0, "Enterprise": 7.5}


def weighted_choice(rng: random.Random, items_weights: list[tuple]) -> object:
    items, weights = zip(*items_weights)
    return rng.choices(items, weights=weights, k=1)[0]


def fmt_amount_br(value: float) -> str:
    """1234.56 → '1.234,56' (formato BR/ES con coma decimal)."""
    s = f"{value:,.2f}"  # US: 1,234.56
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_date_mixed(d: date, dd_mm: bool) -> str:
    return d.strftime("%d/%m/%Y") if dd_mm else d.strftime("%Y-%m-%d")


def generate_merchants(n: int, rng: random.Random) -> list[dict]:
    merchants = []
    for i in range(n):
        seg = weighted_choice(rng, SEGMENTS_WEIGHTS)
        onboard = REFERENCE_DATE - timedelta(days=rng.randint(30, 365 * 4))
        base_p = CHURN_PROB_BY_SEGMENT[seg]
        p_churn = max(0.001, min(0.4, base_p + rng.gauss(0, 0.02)))
        merchants.append(
            {
                "merchant_id": 10_000_000 + i,
                "segment": seg,
                "mcc": rng.choice(MCCS),
                "onboarding_date": onboard,
                "p_churn": p_churn,
                "fla_churn90": 1 if rng.random() < p_churn else 0,
            }
        )
    return merchants


def generate_complaints(merchants: list[dict], rng: random.Random) -> dict[int, date]:
    """Trampa T2: leakage temporal — churners reciben complaint POST reference_date."""
    complaints = {}
    for m in merchants:
        if rng.random() >= 0.30:
            continue  # 70% sin complaint
        if m["fla_churn90"] == 1 and rng.random() < 0.70:
            # leakage: queja JUSTO antes del churn, post-reference
            days_after = rng.randint(1, 90)
            complaints[m["merchant_id"]] = REFERENCE_DATE + timedelta(days=days_after)
        else:
            days_back = rng.randint(1, 365)
            complaints[m["merchant_id"]] = REFERENCE_DATE - timedelta(days=days_back)
    return complaints


def sample_merchant_weights(merchants: list[dict], rng: random.Random) -> list[float]:
    """Pareto-like: pocos merchants concentran muchas transacciones."""
    raw = [rng.paretovariate(1.5) for _ in merchants]
    total = sum(raw)
    return [r / total for r in raw]


def generate_transactions_streaming(
    merchants: list[dict],
    complaints: dict[int, date],
    n_transactions: int,
    csv_path: Path,
    rng: random.Random,
) -> None:
    """Genera y escribe el CSV directamente. Aplica trampas T1, T3, T4, T5."""
    weights = sample_merchant_weights(merchants, rng)
    merchant_ids = [m["merchant_id"] for m in merchants]
    seg_lookup = {m["merchant_id"]: m["segment"] for m in merchants}
    mcc_lookup = {m["merchant_id"]: m["mcc"] for m in merchants}
    churn_lookup = {m["merchant_id"]: m["fla_churn90"] for m in merchants}

    fieldnames = [
        "transaction_id",
        "merchant_id",
        "transaction_date",
        "amount",
        "status",
        "channel",
        "cancellation_reason",
        "reference_date",
        "fla_churn90",
        "last_complaint_date",
        "segment",
        "mcc",
        "dat_process",
    ]

    n_dup = int(n_transactions * 0.02)
    total = n_transactions + n_dup
    next_dup_id = n_transactions + 1
    duplicate_pool: list[dict] = []  # guardamos algunas filas para duplicar al final

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for tid in range(1, n_transactions + 1):
            mid = rng.choices(merchant_ids, weights=weights, k=1)[0]
            seg = seg_lookup[mid]

            # amount lognormal
            mu = AMOUNT_MU_BY_SEGMENT[seg]
            amount_val = math.exp(rng.gauss(mu, 1.0))
            amount_val = round(amount_val, 2)

            # ~3% NaN en amount → string vacío
            if rng.random() < 0.03:
                amount_str = ""  # NaN
            else:
                amount_str = fmt_amount_br(amount_val)  # T3

            txn_date = WINDOW_START + timedelta(days=rng.randint(0, WINDOW_DAYS - 1))
            # T4 · 10% en DD/MM/YYYY
            date_dd_mm = rng.random() < 0.10
            txn_date_str = fmt_date_mixed(txn_date, date_dd_mm)
            dat_process_str = txn_date.strftime("%Y-%m-%d")

            status = weighted_choice(rng, STATUS_WEIGHTS)
            channel = weighted_choice(rng, CHANNELS_WEIGHTS)

            # T1 · cancellation_reason solo si churner
            cancel = ""
            if churn_lookup[mid] == 1:
                reason = weighted_choice(rng, CANCEL_REASONS_WEIGHTS)
                cancel = "" if reason is None else reason

            # T2 · last_complaint_date (puede ser post-reference para churners)
            lcd = complaints.get(mid)
            lcd_str = "" if lcd is None else lcd.strftime("%Y-%m-%d")

            row = {
                "transaction_id": tid,
                "merchant_id": mid,
                "transaction_date": txn_date_str,
                "amount": amount_str,
                "status": status,
                "channel": channel,
                "cancellation_reason": cancel,
                "reference_date": REFERENCE_DATE.strftime("%Y-%m-%d"),
                "fla_churn90": churn_lookup[mid],
                "last_complaint_date": lcd_str,
                "segment": seg,
                "mcc": mcc_lookup[mid],
                "dat_process": dat_process_str,
            }
            writer.writerow(row)

            # acumulamos algunas filas candidatas a duplicar
            if len(duplicate_pool) < n_dup * 3 and rng.random() < 0.05:
                duplicate_pool.append(row)

        # T5 · escribir duplicados con transaction_id distinto
        rng.shuffle(duplicate_pool)
        for row in duplicate_pool[:n_dup]:
            dup = dict(row)
            dup["transaction_id"] = next_dup_id
            next_dup_id += 1
            writer.writerow(dup)

    print(f"   ✓ {csv_path.name} → {total:,} filas (incl. {n_dup:,} duplicados)")


def build_merchants_context(
    merchants: list[dict],
    complaints: dict[int, date],
    n_export: int,
    rng: random.Random,
) -> list[dict]:
    """Sample de merchants con campos para la tool del agente."""
    chosen = rng.sample(merchants, k=min(n_export, len(merchants)))
    records = []
    for m in chosen:
        mid = m["merchant_id"]
        # tpv_last_3m ≈ aleatorio plausible por segmento
        mu = AMOUNT_MU_BY_SEGMENT[m["segment"]]
        n_tx_3m = max(0, int(rng.gauss(45, 15)))
        tpv = round(sum(math.exp(rng.gauss(mu, 1.0)) for _ in range(n_tx_3m)), 2)

        lcd = complaints.get(mid)
        days_since = None
        if lcd is not None and lcd <= REFERENCE_DATE:
            days_since = (REFERENCE_DATE - lcd).days

        records.append(
            {
                "merchant_id": mid,
                "segment": m["segment"],
                "tpv_last_3m": tpv,
                "n_complaints_30d": rng.randint(0, 3),
                "days_since_last_complaint": days_since,
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-merchants", type=int, default=10_000)
    parser.add_argument("--n-transactions", type=int, default=200_000)
    parser.add_argument("--n-context", type=int, default=500)
    parser.add_argument("--output-dir", type=Path, default=HERE)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"⏳ Generating {args.n_merchants:,} merchants…")
    merchants = generate_merchants(args.n_merchants, rng)
    churn_rate = sum(m["fla_churn90"] for m in merchants) / len(merchants)
    print(f"   ✓ churn rate global = {churn_rate:.2%}")

    print("⏳ Generating complaints (con leakage T2)…")
    complaints = generate_complaints(merchants, rng)
    print(f"   ✓ merchants con complaint = {len(complaints):,}")

    print(f"⏳ Generating {args.n_transactions:,} transactions + trampas T1, T3, T4, T5…")
    csv_path = args.output_dir / "transactions_sample.csv"
    generate_transactions_streaming(
        merchants, complaints, args.n_transactions, csv_path, rng
    )
    size_mb = csv_path.stat().st_size / 1024**2
    print(f"   ✓ {csv_path.name} · {size_mb:.1f} MB")

    print(f"⏳ Building merchants_context.json ({args.n_context} merchants)…")
    context = build_merchants_context(merchants, complaints, args.n_context, rng)
    json_path = args.output_dir / "merchants_context.json"
    json_path.write_text(json.dumps(context, indent=2, default=str))
    print(f"   ✓ {json_path.name} → {len(context):,} merchants")

    print("\n✅ Done. Trampas plantadas (NO documentar al candidato):")
    print("   T1 · cancellation_reason solo cuando fla_churn90=1")
    print("   T2 · last_complaint_date con leakage temporal (post-reference_date)")
    print("   T3 · amount formato BR '1.234,56' con coma decimal")
    print("   T4 · transaction_date mezcla YYYY-MM-DD (90%) + DD/MM/YYYY (10%)")
    print("   T5 · ~2% duplicados con transaction_id distinto")


if __name__ == "__main__":
    main()
