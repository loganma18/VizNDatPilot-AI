#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Field role classifier — identifier / category / numeric / datetime (PRD §5.9)."""
from __future__ import annotations

import re
from typing import Any, Dict

import pandas as pd

_EXPLICIT_ID_RE = re.compile(
    r'_(?:id|uuid|guid|no|sn|sku)$'
    r'|^(?:id|uuid|guid)$'
    r'|^(?:编号|序号|流水号|编码|标识)$'
    r'|(?<=[a-z])(?:Id|Uuid|Guid)$',
    re.IGNORECASE,
)
_MODERATE_ID_RE = re.compile(r'\b(?:code|key|sn|sku)\b', re.IGNORECASE)
_NUMERIC_SEMANTIC_RE = re.compile(
    r'\b(?:spend|amount|price|cost|revenue|sales|salary|wage|fee|charge'
    r'|payment|total|sum|balance|profit|loss|income|value|score|qty'
    r'|quantity|volume|weight|height|duration|age|rate|ratio'
    r'|金额|价格|费用|花费|收入|支出|工资|余额|利润|损失|数量|重量'
    r'|高度|时长|分数|评分|成本|花销|月薪|年薪)\b',
    re.IGNORECASE,
)
_CATEGORY_SEMANTIC_RE = re.compile(
    r'\b(?:city|region|country|state|province|county|district|area'
    r'|category|gender|sex|type|status|color|colour|brand'
    r'|department|dept|team|group|segment|tier|level|grade|rank|flag'
    r'|channel|platform|source|medium|campaign|label|tag|class|division'
    r'|城市|地区|省份|国家|类别|性别|类型|状态|颜色|品牌|部门|团队'
    r'|分组|渠道|来源|标签|等级|级别|分类|地域)\b',
    re.IGNORECASE,
)
_MONETARY_STR_RE = re.compile(r'^[\$¥€£₹]?\s*[\d,]+\.?\d*\s*[\$¥€£₹]?$')
DATE_HINTS = ("date", "time", "日期", "时间", "datetime", "year", "month")


def has_date_hint(col_name: str) -> bool:
    name_l = str(col_name).lower()
    return any(k in name_l for k in DATE_HINTS)


def _looks_monetary(series: pd.Series, threshold: float = 0.7) -> bool:
    sample = series.dropna().astype(str).head(30)
    if len(sample) == 0:
        return False
    matches = sum(1 for v in sample if _MONETARY_STR_RE.match(v.strip()))
    return matches / len(sample) >= threshold


def classify_field_role(col_name: str, series: pd.Series, n_rows: int) -> str:
    col_str = str(col_name)
    kind = series.dtype.kind
    n_unique = int(series.nunique(dropna=True))
    unique_ratio = n_unique / n_rows if n_rows > 0 else 0.0

    if kind == "O" and _NUMERIC_SEMANTIC_RE.search(col_str) and _looks_monetary(series):
        return "numeric"
    if _EXPLICIT_ID_RE.search(col_str) and not _NUMERIC_SEMANTIC_RE.search(col_str):
        return "identifier"
    if _NUMERIC_SEMANTIC_RE.search(col_str):
        return "numeric"
    if _CATEGORY_SEMANTIC_RE.search(col_str):
        return "category"
    if kind in ("M", "m"):
        return "datetime"
    if kind in ("i", "u", "f"):
        if unique_ratio > 0.95 and n_unique > 50 and _MODERATE_ID_RE.search(col_str):
            return "identifier"
        if unique_ratio > 0.99 and n_unique > 200:
            return "identifier"
        return "numeric"
    if kind == "O":
        has_id_hint = bool(_EXPLICIT_ID_RE.search(col_str) or _MODERATE_ID_RE.search(col_str))
        if unique_ratio > 0.90 and n_unique > 50 and has_id_hint:
            return "identifier"
        return "category"
    return "category"


def extract_field_roles(df: pd.DataFrame) -> Dict[str, str]:
    n_rows = len(df)
    return {str(col): classify_field_role(str(col), df[col], n_rows) for col in df.columns}
