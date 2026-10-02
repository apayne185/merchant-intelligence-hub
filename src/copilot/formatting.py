"""
Number formatting for answers. The deterministic synthesizer formats every
figure through these functions, and the verifier infers a claim's precision
from how it is written, so a template answer always verifies.
"""
from __future__ import annotations


def usd(value: float) -> str:
    """$416.16 billion / $102.47 million / $7.46"""
    a = abs(value)
    sign = "-" if value < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.2f} billion"
    if a >= 1e6:
        return f"{sign}${a / 1e6:,.2f} million"
    return f"{sign}${a:,.2f}"


def pct(ratio: float, digits: int = 1) -> str:
    return f"{ratio * 100:.{digits}f}%"


def ratio_x(value: float) -> str:
    return f"{value:.2f}x"


def fmt_value(value: float, unit: str | None) -> str:
    if unit == "USD":
        return usd(value)
    if unit == "USD/shares":
        return f"${value:,.2f}"
    if unit == "ratio_x":
        return ratio_x(value)
    if unit == "ratio":
        return pct(value)
    return f"{value:,.2f}"
