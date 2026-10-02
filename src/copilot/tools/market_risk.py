"""Market risk tool: portfolio VaR/ES, backtest and risk attribution (C++ riskcore)."""
from __future__ import annotations

from typing import Any

from src.copilot.formatting import pct
from src.copilot.schemas import Evidence
from src.risk.engine import RiskRequest, portfolio_risk


def risk_evidence(r: dict[str, Any]) -> list[Evidence]:
    bt = r["backtest"]
    params = {"confidence": r["confidence"], "window_days": float(r["window_days"]),
              "horizon_days": float(r["monte_carlo"]["horizon_days"]), "paths": float(r["monte_carlo"]["paths"]),
              "base_horizon_days": 1.0}
    out = [
        Evidence(
            id=f"{r['risk_id']}:var", kind="risk_metric", label="Portfolio VaR / Expected Shortfall",
            values={
                "historical_var_1d": r["historical_1d"]["var"], "historical_es_1d": r["historical_1d"]["es"],
                "parametric_var_1d": r["parametric_1d"]["var"], "parametric_es_1d": r["parametric_1d"]["es"],
                "mc_var": r["monte_carlo"]["var"], "mc_es": r["monte_carlo"]["es"], **params,
            },
            unit="ratio",
            source={"engine": r["backend"], "as_of": r["as_of"], "mc_distribution": r["monte_carlo"]["distribution"]},
        ),
        Evidence(
            id=f"{r['risk_id']}:backtest", kind="risk_metric", label="Historical VaR backtest (Kupiec POF)",
            values={"observations": float(bt["observations"]), "exceptions": float(bt["exceptions"]),
                    "expected_exceptions": float(bt["expected_exceptions"]), "kupiec_p_value": bt["kupiec_p_value"],
                    "confidence": r["confidence"], "window_days": 250.0, "significance_level": 0.05},
            source={"test": "Kupiec proportion of failures", "calibrated": bt["calibrated"]},
        ),
    ]
    for p in r["positions"]:
        out.append(Evidence(
            id=f"{r['risk_id']}:{p['ticker']}", kind="risk_metric", ticker=p["ticker"],
            label=f"{p['ticker']} position risk",
            values={"weight": p["weight"], "annualized_vol": p["annualized_vol"],
                    "component_var_share": p["component_var_share"]},
            unit="ratio", source={"method": "Euler allocation of parametric VaR"},
        ))
    return out


def risk_findings(r: dict[str, Any]) -> list[str]:
    conf = pct(r["confidence"], 0)
    h, mc, bt = r["historical_1d"], r["monte_carlo"], r["backtest"]
    vid = f"{r['risk_id']}:var"
    lines = [
        f"As of {r['as_of']}, the portfolio's 1-day {conf} historical VaR is {pct(h['var'], 2)} of capital "
        f"(expected shortfall {pct(h['es'], 2)}), using {r['window_days']} trading days [{vid}].",
        f"The {mc['horizon_days']}-day {conf} Monte Carlo VaR with fat-tailed (Student-t) shocks is "
        f"{pct(mc['var'], 2)} (expected shortfall {pct(mc['es'], 2)}) [{vid}].",
    ]
    top = max(r["positions"], key=lambda p: p["component_var_share"])
    lines.append(
        f"{top['ticker']} contributes {pct(top['component_var_share'])} of portfolio VaR at a "
        f"{pct(top['weight'])} weight [{r['risk_id']}:{top['ticker']}]."
    )
    verdict = "consistent with" if bt["calibrated"] else "rejected by"
    lines.append(
        f"Backtest: {bt['exceptions']} VaR exceptions in {bt['observations']} days against "
        f"{bt['expected_exceptions']} expected, {verdict} the Kupiec test at the 5% level [{r['risk_id']}:backtest]."
    )
    return lines


def run_market_risk(positions: dict[str, float], as_of: str | None) -> dict[str, Any]:
    req = RiskRequest(tickers=tuple(positions), weights=tuple(positions.values()), as_of=as_of)
    r = portfolio_risk(req)
    return {
        "evidence": risk_evidence(r), "findings": risk_findings(r), "gaps": [], "raw": r,
        "summary": f"VaR/ES for {len(positions)} position(s) via {r['backend']}",
    }
