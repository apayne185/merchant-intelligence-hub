"""
Pre-trade check: deterministic risk-limit evaluation of a proposed portfolio.

Rules, not an LLM, decide APPROVE/REJECT. The LLM may explain the decision
but cannot change it: the verdict and every breach are evidence the answer
is verified against. Nothing here routes or submits orders; the output is a
decision record an execution system (or a human) acts on.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.copilot.entities import universe
from src.copilot.formatting import pct
from src.copilot.schemas import Evidence
from src.copilot.tools.market_risk import risk_evidence
from src.risk.engine import RiskRequest, portfolio_risk

LIMITS_PATH = Path(__file__).resolve().parents[3] / "data" / "risk_limits.json"


def load_limits() -> dict[str, Any]:
    return dict(json.loads(LIMITS_PATH.read_text()))


def run_pretrade_check(positions: dict[str, float], as_of: str | None) -> dict[str, Any]:
    limits = load_limits()
    checks: list[tuple[str, float, float, bool]] = []  # (name, observed, limit, passed)

    for t, w in positions.items():
        checks.append((f"single-name weight {t}", abs(w), limits["max_single_name_weight"],
                       abs(w) <= limits["max_single_name_weight"]))
    sectors: dict[str, float] = {}
    for t, w in positions.items():
        sector = str(universe().get(t, {}).get("sector", "Unknown"))
        sectors[sector] = sectors.get(sector, 0.0) + abs(w)
    for s, w in sorted(sectors.items()):
        checks.append((f"sector weight {s}", w, limits["max_sector_weight"], w <= limits["max_sector_weight"]))
    gross = sum(abs(w) for w in positions.values())
    checks.append(("gross exposure", gross, limits["max_gross_exposure"], gross <= limits["max_gross_exposure"] + 1e-9))

    restricted = sorted(set(positions) & set(limits["restricted_list"]))
    r = portfolio_risk(RiskRequest(tickers=tuple(positions), weights=tuple(positions.values()), as_of=as_of))
    var = r["historical_1d"]["var"]
    checks.append(("1-day 99% historical VaR", var, limits["max_var_1d_99"], var <= limits["max_var_1d_99"]))

    breaches = [c for c in checks if not c[3]]
    approved = not breaches and not restricted
    evidence = [
        Evidence(
            id=f"limit:{name.replace(' ', '_')}", kind="limit_check", ticker=None, label=f"Limit: {name}",
            values={"observed": observed, "limit": limit, "horizon_days": 1.0, "confidence": 0.99}, unit="ratio",
            source={"passed": passed, "limits_file": "data/risk_limits.json"},
        )
        for name, observed, limit, passed in checks
    ] + risk_evidence(r)
    evidence.append(Evidence(
        id="limit:summary", kind="limit_check", label="Pre-trade limit summary",
        values={"limits_checked": float(len(checks)), "breaches": float(len(breaches))},
        source={"decision": "APPROVE" if approved else "REJECT", "restricted": restricted},
    ))

    findings = [f"Pre-trade decision: {'APPROVE' if approved else 'REJECT'}."]
    for name, observed, limit, _ in breaches:
        findings.append(f"Breach: {name} is {pct(observed)} against a limit of {pct(limit)} "
                        f"[limit:{name.replace(' ', '_')}].")
    if restricted:
        findings.append(f"Restricted list: {', '.join(restricted)}.")
    if approved:
        findings.append(f"All {len(checks)} limits pass, including 1-day 99% VaR of {pct(var, 2)} against "
                        f"{pct(limits['max_var_1d_99'], 2)} [limit:summary] [limit:1-day_99%_historical_VaR].")
    return {
        "evidence": evidence, "findings": findings, "gaps": [], "decision": "APPROVE" if approved else "REJECT",
        "summary": f"{'APPROVE' if approved else 'REJECT'}: {len(breaches)} breach(es) of {len(checks)} limit(s)",
    }
