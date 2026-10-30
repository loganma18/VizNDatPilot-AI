# core/rounding.py
"""ROUND_HALF_UP helpers for the quality ledger (PRD §5.10.1)."""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


def round_half_up(value, ndigits: int) -> Optional[float]:
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    q = Decimal("1").scaleb(-ndigits)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


def store_rate(rate) -> tuple:
    """Return (rate_4dp, pct_2dp) or (None, None)."""
    r4 = round_half_up(rate, 4)
    if r4 is None:
        return None, None
    pct2 = round_half_up(float(rate) * 100.0, 2)
    return r4, pct2


def clip100(penalty: float) -> int:
    raw = max(0.0, min(100.0, 100.0 - float(penalty)))
    return int(round_half_up(raw, 0))
