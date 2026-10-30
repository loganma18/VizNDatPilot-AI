#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zero-tolerance number whitelist for health-report LLM text (PRD §8.3)."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

_NUM_RE = re.compile(
    r"(?<![A-Za-z_])"
    r"[+-]?"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?"
    r"(?![A-Za-z_])"
)


def extract_numbers(text: str) -> List[Tuple[str, float]]:
    found = []
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group(0)
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        found.append((raw, val))
    return found


def _matches(value: float, allowed: Dict[float, str]) -> bool:
    if value in allowed:
        return True
    for k in allowed:
        if abs(k - value) <= 1e-9:
            return True
    return False


def validate_whitelist(text: str, allowed: Dict[float, str]) -> List[dict]:
    """Return list of unmatched numbers (ERROR). Empty list = pass."""
    violations = []
    for raw, val in extract_numbers(text):
        if not _matches(val, allowed):
            violations.append({"raw_text": raw, "number": val})
    return violations
