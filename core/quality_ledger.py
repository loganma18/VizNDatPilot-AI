#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quality Fact Ledger — all health metrics computed in Python (PRD §5–§6)."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.fact_extractor import classify_field_role, has_date_hint
from core.rounding import clip100, round_half_up, store_rate
from utils.validator import DataCleaner

N_ROWS_MIN = 3
N_ROWS_MAX = 500_000
IQR_K = 1.5

_MISSING_P0 = 0.40
_MISSING_P1 = 0.05
_OUTLIER_P0 = 0.20
_OUTLIER_P1 = 0.01
_DUP_P0 = 0.10
_PARSE_P0 = 0.20
_TEXT_LEN_P2 = 50
_CARD_LOW = 3
_LAG_P1 = 365
_SPAN_P1_DAYS = 365 * 30
_CURRENCY_RE = re.compile(r"[$¥€£₹,\s]")
_NULL_STRINGS = DataCleaner._NULL_STRINGS
_NULL_STRINGS_CF = frozenset(s.casefold() for s in _NULL_STRINGS)


class HealthLedgerError(ValueError):
    pass


def _quantile(x: pd.Series, q: float) -> float:
    try:
        return float(x.quantile(q, interpolation="linear"))
    except TypeError:
        return float(x.quantile(q, method="linear"))


def _add_allowed(allowed: Dict[float, str], val, desc: str) -> None:
    if val is None:
        return
    try:
        fv = float(val)
    except (TypeError, ValueError):
        return
    if not math.isfinite(fv):
        return
    allowed[fv] = desc
    if abs(fv - round(fv)) < 1e-12:
        allowed[float(int(round(fv)))] = desc


def _norm_text(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    return unicodedata.normalize("NFKC", v).strip().casefold()


def _is_null_token(value: Any) -> bool:
    """True for disguised empty tokens: '', N/A, 未知, '-', etc. (after strip)."""
    if not isinstance(value, str):
        return False
    token = unicodedata.normalize("NFKC", value).strip()
    if token in _NULL_STRINGS or token.casefold() in _NULL_STRINGS_CF:
        return True
    return False


def missing_mask(s: pd.Series) -> pd.Series:
    """
    Unified missing mask for pandas 2/3 (object / string[python] / numeric).
    Counts: NaN/NaT/None/pd.NA, whitespace-only, and disguised null strings.
    """
    native = s.isna().astype(bool)
    extra = s.map(_is_null_token, na_action="ignore")
    extra = extra.fillna(False).astype(bool)
    return native | extra


def _fmt_missing_example(v: Any) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return "<空>"
    text = str(v)
    return text if text.strip() else "<空>"


def _build_df_q(df_raw: pd.DataFrame) -> Tuple[pd.DataFrame, int, Dict[str, pd.Series]]:
    df_q = df_raw.copy()
    df_q.columns = [str(c).strip() for c in df_q.columns]
    masks: Dict[str, pd.Series] = {}
    disguised_null_count = 0
    for col in df_q.columns:
        s = df_q[col]
        mask = missing_mask(s)
        masks[str(col)] = mask
        disguised_null_count += int((mask & ~s.isna().fillna(False).astype(bool)).sum())
        # np.nan is counted reliably by isna() on both object and StringDtype
        df_q[col] = s.where(~mask, np.nan)
    return df_q, disguised_null_count, masks


def _numeric_parse(non_null: pd.Series) -> pd.Series:
    txt = non_null.astype(str).str.strip()
    txt = txt.str.replace(_CURRENCY_RE, "", regex=True)
    return pd.to_numeric(txt, errors="coerce")


def _parse_fail_rate(s: pd.Series, role: str, col_name: str) -> Optional[float]:
    date_hint = has_date_hint(col_name)
    if role == "numeric":
        if pd.api.types.is_numeric_dtype(s):
            return 0.0
        non_null = s.dropna()
        if len(non_null) == 0:
            return None
        parsed = _numeric_parse(non_null)
        return float(parsed.isna().sum() / len(non_null))
    if role == "datetime" or date_hint:
        if pd.api.types.is_datetime64_any_dtype(s):
            return 0.0
        non_null = s.dropna()
        if len(non_null) == 0:
            return None
        parsed = pd.to_datetime(non_null, errors="coerce", utc=False)
        return float(parsed.isna().sum() / len(non_null))
    return None


def _datetime_series(s: pd.Series) -> pd.Series:
    dt = pd.to_datetime(s, errors="coerce", utc=False)
    try:
        if getattr(dt.dt, "tz", None) is not None:
            dt = dt.dt.tz_convert("UTC").dt.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    return dt


def _naive_normalize(v) -> pd.Timestamp:
    ts = pd.Timestamp(v)
    if ts.tz is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


def build_quality_ledger(
    df_raw: pd.DataFrame,
    df_clean: Optional[pd.DataFrame] = None,
    primary_key: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if df_raw is None or df_raw.empty or df_raw.shape[1] == 0:
        raise HealthLedgerError("数据为空，无法生成报告")

    n_rows_raw = int(len(df_raw))
    if n_rows_raw > N_ROWS_MAX:
        raise HealthLedgerError(
            f"数据行数过多（{n_rows_raw} 行，上限 {N_ROWS_MAX}），请拆分后重新体检"
        )

    df_q, disguised_null_count, miss_masks = _build_df_q(df_raw)
    n_rows = int(len(df_q))
    n_cols = int(df_q.shape[1])
    n_rows_clean = int(len(df_clean)) if df_clean is not None else None

    duplicate_row_count = int(df_q.duplicated(keep="first").sum())
    duplicate_row_rate, duplicate_row_rate_pct = store_rate(
        duplicate_row_count / n_rows_raw if n_rows_raw else None
    )
    empty_col_count = int(df_q.isna().all(axis=0).sum())
    full_empty_row_count = int(df_q.isna().all(axis=1).sum())
    constant_col_count = int((df_q.nunique(dropna=True) == 1).sum())
    total_cells = n_rows_raw * n_cols
    missing_cells = int(df_q.isna().sum().sum())
    overall_cell_missing_rate, overall_cell_missing_pct = store_rate(
        missing_cells / total_cells if total_cells else None
    )
    disguised_ratio, disguised_pct = store_rate(
        disguised_null_count / total_cells if total_cells else None
    )

    names = [str(c) for c in df_q.columns]
    unnamed_col_count = sum(
        1 for c in names if c.strip() == "" or c.lower().startswith("unnamed")
    )
    name_cnt = Counter(names)
    duplicate_col_name_count = sum(1 for v in name_cnt.values() if v > 1)

    roles = {
        str(col): classify_field_role(str(col), df_q[col], n_rows) for col in df_q.columns
    }

    columns: Dict[str, Any] = {}
    raw_by_pos = list(df_raw.columns)
    for idx, col in enumerate(df_q.columns):
        col_s = str(col)
        s = df_q[col]
        raw_s = df_raw.iloc[:, idx] if idx < len(raw_by_pos) else s
        role = roles[col_s]
        col_miss = miss_masks.get(col_s, missing_mask(s))
        missing_count = int(col_miss.sum())
        missing_rate, missing_pct = store_rate(
            missing_count / n_rows_raw if n_rows_raw else None
        )
        missing_examples = [
            _fmt_missing_example(v) for v in raw_s[col_miss].head(5).tolist()
        ]
        n_unique = int(s.nunique(dropna=True))
        unique_ratio, unique_pct = store_rate(
            n_unique / n_rows_raw if n_rows_raw else None
        )
        n_non_null = n_rows_raw - missing_count
        avg_per_category = (
            round_half_up(n_non_null / n_unique, 2) if n_unique > 0 else None
        )
        duplicate_key_count = (
            int(s.dropna().duplicated(keep="first").sum()) if role == "identifier" else None
        )

        facts: Dict[str, Any] = {
            "role": role,
            "dtype": str(s.dtype),
            "missing_count": missing_count,
            "missing_rate": missing_rate,
            "missing_pct": missing_pct,
            "n_unique": n_unique,
            "unique_ratio": unique_ratio,
            "unique_pct": unique_pct,
            "n_non_null": n_non_null,
            "avg_per_category": avg_per_category,
            "duplicate_key_count": duplicate_key_count,
            "high_cardinality": False,
            "min": None, "max": None, "mean": None, "std": None,
            "median": None, "q25": None, "q75": None,
            "outlier_skipped": False,
            "outlier_count": None, "outlier_rate": None, "outlier_pct": None,
            "outlier_low": None, "outlier_high": None, "outlier_examples": [],
            "parse_fail_rate": None, "parse_fail_pct": None,
            "variant_gap": None, "n_unique_raw": None, "n_unique_normalized": None,
            "leading_trailing_space_count": None, "avg_text_len": None,
            "top_values": [], "magnitude_span_flag": False,
            "dt_min": None, "dt_max": None, "span_days": None,
            "examples": [str(v)[:80] for v in s.dropna().head(5).tolist()],
            "missing_examples": missing_examples,
        }

        pfr = _parse_fail_rate(s, role, col_s)
        if pfr is not None:
            facts["parse_fail_rate"], facts["parse_fail_pct"] = store_rate(pfr)

        if role == "numeric":
            if pd.api.types.is_numeric_dtype(s):
                s_num = s
            else:
                s_num = _numeric_parse(s.dropna())
            x = pd.to_numeric(s_num, errors="coerce").dropna()
            n_valid = int(len(x))
            if n_valid > 0:
                facts["min"] = round_half_up(float(x.min()), 4)
                facts["max"] = round_half_up(float(x.max()), 4)
                facts["mean"] = round_half_up(float(x.mean()), 4)
                facts["median"] = round_half_up(float(x.median()), 4)
                facts["std"] = round_half_up(float(x.std(ddof=1)), 4) if n_valid >= 2 else None
                facts["q25"] = round_half_up(_quantile(x, 0.25), 4)
                facts["q75"] = round_half_up(_quantile(x, 0.75), 4)
                mn, mx = facts["min"], facts["max"]
                facts["magnitude_span_flag"] = bool(
                    mn not in (None, 0) and mx is not None and mn > 0 and (mx / mn) >= 1e6
                )
            if n_valid < 4:
                facts["outlier_skipped"] = True
            else:
                q1 = _quantile(x, 0.25)
                q3 = _quantile(x, 0.75)
                iqr = q3 - q1
                if (not math.isfinite(iqr)) or iqr == 0:
                    facts["outlier_skipped"] = True
                else:
                    low = round_half_up(q1 - IQR_K * iqr, 4)
                    high = round_half_up(q3 + IQR_K * iqr, 4)
                    mask = (x < low) | (x > high)
                    cnt = int(mask.sum())
                    rate, pct = store_rate(cnt / n_valid)
                    facts.update({
                        "outlier_low": low, "outlier_high": high,
                        "outlier_count": cnt, "outlier_rate": rate, "outlier_pct": pct,
                        "outlier_examples": [round_half_up(float(v), 4) for v in x[mask].head(5).tolist()],
                    })

        src = raw_s if (pd.api.types.is_object_dtype(raw_s) or pd.api.types.is_string_dtype(raw_s)) else s
        if role in ("category", "identifier") and (
            pd.api.types.is_object_dtype(src) or pd.api.types.is_string_dtype(src)
        ):
            non_null = src.dropna().astype(str)
            facts["avg_text_len"] = (
                round_half_up(float(non_null.str.len().mean()), 2) if len(non_null) else None
            )
            facts["n_unique_raw"] = int(src.nunique(dropna=True))
            facts["n_unique_normalized"] = int(src.dropna().map(_norm_text).nunique())
            facts["variant_gap"] = max(0, facts["n_unique_raw"] - facts["n_unique_normalized"])
            facts["leading_trailing_space_count"] = int(
                src.dropna().map(lambda v: isinstance(v, str) and v != v.strip()).sum()
            )
            facts["top_values"] = [
                {"value": str(idx)[:80], "count": int(cnt)}
                for idx, cnt in s.dropna().astype(str).value_counts().head(5).items()
            ]
            facts["high_cardinality"] = bool(
                role == "category"
                and n_unique >= 20
                and avg_per_category is not None
                and avg_per_category < 5
            )

        columns[col_s] = facts

    # primary datetime column
    dt_candidates = []
    for col_s, cf in columns.items():
        pfr = cf["parse_fail_rate"]
        ok = roles[col_s] == "datetime" or (
            has_date_hint(col_s) and pfr is not None and pfr <= _PARSE_P0
        )
        if ok:
            dt_candidates.append((col_s, pfr if pfr is not None else 0.0))
    dt_candidates.sort(key=lambda t: (0 if roles[t[0]] == "datetime" else 1, t[1]))

    as_of_date = pd.Timestamp.now(tz=None).normalize()
    lag_days = span_days = gap_period_count = None
    data_max_date = data_min_date = None
    timeliness_enabled = bool(dt_candidates)
    primary_dt_col = dt_candidates[0][0] if dt_candidates else None

    if primary_dt_col:
        dt = _datetime_series(df_q[primary_dt_col])
        vals = dt.dropna()
        if len(vals) > 0:
            data_min_date = _naive_normalize(vals.min())
            data_max_date = _naive_normalize(vals.max())
            span_days = int((data_max_date - data_min_date).days)
            lag_days = int((as_of_date - data_max_date).days)
            columns[primary_dt_col]["dt_min"] = str(data_min_date.date())
            columns[primary_dt_col]["dt_max"] = str(data_max_date.date())
            columns[primary_dt_col]["span_days"] = span_days
            freq = "D" if span_days <= 90 else "MS"
            full = pd.date_range(data_min_date, data_max_date, freq=freq)
            vals_n = vals.map(_naive_normalize)
            if freq == "D":
                observed = pd.DatetimeIndex(vals_n.unique())
                gap_period_count = int(len(full.difference(observed)))
            else:
                full_p = set(full.to_period("M"))
                obs_p = set(pd.DatetimeIndex(vals_n).to_period("M").unique())
                gap_period_count = int(len(full_p - obs_p))

    first_id = next((c for c, r in roles.items() if r == "identifier"), None)
    declared_key = [str(c).strip() for c in (primary_key or []) if str(c).strip() in columns]
    declared_key_duplicate = None
    key_missing_combo_count = None
    if declared_key:
        sub = df_q[declared_key]
        complete = sub.notna().all(axis=1)
        key_missing_combo_count = int((~complete).sum())
        declared_key_duplicate = int(sub.loc[complete].duplicated(keep="first").sum())

    issues = _collect_issues(
        n_rows_raw, duplicate_row_count, duplicate_row_rate,
        unnamed_col_count, duplicate_col_name_count,
        columns, lag_days, span_days,
        declared_key, declared_key_duplicate, key_missing_combo_count,
    )
    for col_s, cf in columns.items():
        cf["field_grade"] = _grade_for_col(col_s, issues)

    scorecard = _score(
        columns, overall_cell_missing_rate, disguised_null_count, disguised_ratio,
        duplicate_row_rate, unnamed_col_count, duplicate_col_name_count,
        timeliness_enabled, lag_days, span_days, issues,
        declared_key_duplicate,
    )

    allowed: Dict[float, str] = {}
    gvals = {
        "n_rows_raw": n_rows_raw, "n_cols": n_cols, "n_rows_clean": n_rows_clean,
        "duplicate_row_count": duplicate_row_count,
        "duplicate_row_rate": duplicate_row_rate,
        "duplicate_row_rate_pct": duplicate_row_rate_pct,
        "empty_col_count": empty_col_count,
        "constant_col_count": constant_col_count,
        "missing_cells": missing_cells,
        "overall_cell_missing_rate": overall_cell_missing_rate,
        "overall_cell_missing_pct": overall_cell_missing_pct,
        "disguised_null_count": disguised_null_count,
        "disguised_ratio": disguised_ratio, "disguised_pct": disguised_pct,
        "full_empty_row_count": full_empty_row_count,
        "unnamed_col_count": unnamed_col_count,
        "duplicate_col_name_count": duplicate_col_name_count,
        "key_missing_combo_count": key_missing_combo_count,
        "lag_days": lag_days, "span_days": span_days,
        "gap_period_count": gap_period_count,
        "issue_p0_count": scorecard["issue_p0_count"],
        "issue_p1_count": scorecard["issue_p1_count"],
        "issue_p2_count": scorecard["issue_p2_count"],
        "overall_score": scorecard["overall_score"],
        "dim.completeness": scorecard["dims"]["completeness"],
        "dim.uniqueness": scorecard["dims"]["uniqueness"],
        "dim.validity": scorecard["dims"]["validity"],
        "dim.consistency": scorecard["dims"]["consistency"],
        "dim.interpretability": scorecard["dims"]["interpretability"],
        "dim.timeliness": scorecard["dims"]["timeliness"],
        "IQR_K": IQR_K, "grade_A": 85, "grade_B": 70, "grade_C": 50, "p0_cap": 49,
        "n_rows_max": N_ROWS_MAX, "n_rows_min": N_ROWS_MIN,
    }
    for k, v in gvals.items():
        _add_allowed(allowed, v, k)
    for col_s, cf in columns.items():
        for k in (
            "missing_count", "missing_rate", "missing_pct", "n_unique", "unique_ratio",
            "unique_pct", "duplicate_key_count", "n_non_null", "min", "max", "mean",
            "median", "std", "q25", "q75", "outlier_count", "outlier_rate", "outlier_pct",
            "outlier_low", "outlier_high", "parse_fail_rate", "parse_fail_pct",
            "variant_gap", "avg_text_len", "span_days", "avg_per_category",
            "leading_trailing_space_count", "n_unique_normalized",
        ):
            _add_allowed(allowed, cf.get(k), f"{col_s}.{k}")
        for ex in cf.get("outlier_examples") or []:
            _add_allowed(allowed, ex, f"{col_s}.outlier_example")
        for tv in cf.get("top_values") or []:
            _add_allowed(allowed, tv.get("count"), f"{col_s}.top_count")

    field_table = []
    for col_s, cf in columns.items():
        field_table.append({
            "col_name": col_s,
            "role": cf["role"],
            "dtype": cf["dtype"],
            "missing_rate": cf["missing_rate"],
            "missing_pct": cf["missing_pct"],
            "n_unique": cf["n_unique"],
            "unique_ratio": cf["unique_ratio"],
            "outlier_count": cf["outlier_count"],
            "parse_fail_rate": cf["parse_fail_rate"],
            "parse_fail_pct": cf["parse_fail_pct"],
            "variant_gap": cf["variant_gap"],
            "field_grade": cf["field_grade"],
        })

    global_facts = {
        "n_rows_raw": n_rows_raw,
        "n_cols": n_cols,
        "n_rows_clean": n_rows_clean,
        "duplicate_row_count": duplicate_row_count,
        "duplicate_row_rate": duplicate_row_rate,
        "duplicate_row_rate_pct": duplicate_row_rate_pct,
        "empty_col_count": empty_col_count,
        "constant_col_count": constant_col_count,
        "missing_cells": missing_cells,
        "overall_cell_missing_rate": overall_cell_missing_rate,
        "overall_cell_missing_pct": overall_cell_missing_pct,
        "disguised_null_count": disguised_null_count,
        "disguised_ratio": disguised_ratio,
        "disguised_pct": disguised_pct,
        "full_empty_row_count": full_empty_row_count,
        "unnamed_col_count": unnamed_col_count,
        "duplicate_col_name_count": duplicate_col_name_count,
        "key_missing_combo_count": key_missing_combo_count,
        "as_of_date": str(as_of_date.date()),
        "data_min_date": str(data_min_date.date()) if data_min_date is not None else None,
        "data_max_date": str(data_max_date.date()) if data_max_date is not None else None,
        "lag_days": lag_days,
        "span_days": span_days,
        "gap_period_count": gap_period_count,
        "timeliness_enabled": timeliness_enabled,
        "primary_dt_col": primary_dt_col,
        "has_clean_contrast": df_clean is not None,
        "first_identifier": first_id,
        "declared_primary_key": declared_key,
        "declared_key_duplicate": declared_key_duplicate,
    }

    return {
        "global": global_facts,
        "columns": columns,
        "roles": roles,
        "issues": issues,
        "scorecard": scorecard,
        "field_table": field_table,
        "_allowed_numbers": allowed,
    }


def _grade_for_col(col: str, issues: List[Dict]) -> str:
    levels = [i["level"] for i in issues if i.get("column") == col]
    if "P0" in levels:
        return "D"
    if "P1" in levels:
        return "C"
    if "P2" in levels:
        return "B"
    return "A"


def _collect_issues(
    n_rows_raw, duplicate_row_count, duplicate_row_rate,
    unnamed_col_count, duplicate_col_name_count,
    columns, lag_days, span_days,
    declared_key, declared_key_duplicate, key_missing_combo_count,
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []

    def add(level, code, column, message, affected_rows=None, examples=None, extra=None):
        issue_id = f"{code}:{column}"
        issues.append({
            "issue_id": issue_id,
            "level": level,
            "code": code,
            "column": column,
            "message": message,
            "affected_rows": affected_rows,
            "examples": examples or [],
            "extra": extra or {},
        })

    if n_rows_raw < N_ROWS_MIN:
        add("P0", "TOO_FEW_ROWS", "__global__",
            f"数据行数过少（{n_rows_raw} 行）", n_rows_raw)

    if duplicate_row_count > 0:
        rate = duplicate_row_rate or 0
        level = "P0" if rate >= _DUP_P0 else "P1"
        add(level, "DUPLICATE_ROWS", "__global__",
            f"完全重复行 {duplicate_row_count} 行（{duplicate_row_rate and round_half_up(duplicate_row_rate*100, 2)}%）",
            duplicate_row_count)

    if unnamed_col_count > 0:
        add("P2", "UNNAMED_COL", "__global__",
            f"空或 Unnamed 列名 {unnamed_col_count} 个", unnamed_col_count)
    if duplicate_col_name_count > 0:
        add("P2", "DUPLICATE_COL_NAME", "__global__",
            f"重名列 {duplicate_col_name_count} 组", duplicate_col_name_count)

    if span_days is not None and span_days > _SPAN_P1_DAYS:
        add("P1", "DATETIME_SPAN_LONG", "__global__",
            f"主日期列时间跨度 {span_days} 天", None)
    if lag_days is not None and lag_days > _LAG_P1:
        add("P1", "LAG_HIGH", "__global__",
            f"数据最新日期滞后 {lag_days} 天", None)
    if lag_days is not None and lag_days < 0:
        add("P2", "FUTURE_DATE", "__global__",
            f"存在未来日期（滞后天数为 {lag_days}）", None)

    for col, cf in columns.items():
        mr = cf["missing_rate"]
        if mr is not None and mr >= _MISSING_P0:
            add("P0", "MISSING_RATE_HIGH", col,
                f"列「{col}」缺失率 {cf['missing_pct']}%（{cf['missing_count']}/{n_rows_raw}）",
                cf["missing_count"], cf.get("missing_examples") or [])
        elif mr is not None and mr >= _MISSING_P1:
            add("P1", "MISSING_RATE_HIGH", col,
                f"列「{col}」缺失率 {cf['missing_pct']}%（{cf['missing_count']}/{n_rows_raw}）",
                cf["missing_count"], cf.get("missing_examples") or [])

        # 标识符重复只作描述。主键问题只在用户声明主键后产生。

        if not cf["outlier_skipped"] and cf["outlier_rate"] is not None:
            obr = cf["outlier_rate"]
            if obr >= _OUTLIER_P0:
                add("P0", "OUTLIER_HIGH", col,
                    f"列「{col}」IQR 离群率 {cf['outlier_pct']}%（{cf['outlier_count']} 个，统计离群≠录入错误）",
                    cf["outlier_count"], [str(x) for x in cf["outlier_examples"]])
            elif obr >= _OUTLIER_P1:
                add("P1", "OUTLIER_HIGH", col,
                    f"列「{col}」IQR 离群率 {cf['outlier_pct']}%（{cf['outlier_count']} 个，统计离群≠录入错误）",
                    cf["outlier_count"], [str(x) for x in cf["outlier_examples"]])

        pfr = cf["parse_fail_rate"]
        if pfr is not None and pfr >= _PARSE_P0:
            add("P0", "PARSE_FAIL", col,
                f"列「{col}」解析失败率 {cf['parse_fail_pct']}%",
                None)
        elif pfr is not None and pfr >= _MISSING_P1:
            add("P1", "PARSE_FAIL", col,
                f"列「{col}」解析失败率 {cf['parse_fail_pct']}%",
                None)

        if cf["avg_text_len"] is not None and cf["avg_text_len"] > _TEXT_LEN_P2:
            add("P2", "TEXT_TOO_LONG", col,
                f"列「{col}」文本平均长度 {cf['avg_text_len']}", None)
        if cf["role"] == "category" and cf["n_unique"] < _CARD_LOW:
            add("P2", "CARDINALITY_LOW", col,
                f"列「{col}」唯一值过少（{cf['n_unique']}）", cf["n_unique"])
        if cf["high_cardinality"]:
            add("P2", "CARDINALITY_HIGH", col,
                f"列「{col}」高基数（{cf['n_unique']} 类，均 {cf['avg_per_category']} 条/类）",
                cf["n_unique"])
        if (cf["variant_gap"] or 0) > 0:
            add("P2", "VARIANT_GAP", col,
                f"列「{col}」存在 {cf['variant_gap']} 个写法变体",
                cf["variant_gap"])
        if cf["magnitude_span_flag"]:
            add("P2", "MAGNITUDE_SPAN", col,
                f"列「{col}」量级跨度极大，核对本位", None)
        if cf.get("span_days") is not None and cf["span_days"] > _SPAN_P1_DAYS:
            add("P1", "DATETIME_SPAN_LONG", col,
                f"列「{col}」时间跨度 {cf['span_days']} 天", None)

    if declared_key and key_missing_combo_count:
        add("P1", "KEY_MISSING", "+".join(declared_key),
            f"已声明主键有 {key_missing_combo_count} 行缺少组成字段",
            key_missing_combo_count)
    if declared_key and declared_key_duplicate:
        add("P0", "DUPLICATE_KEY", "+".join(declared_key),
            f"已声明主键存在 {declared_key_duplicate} 行重复（不含首现）",
            declared_key_duplicate)

    level_order = {"P0": 0, "P1": 1, "P2": 2}
    issues.sort(key=lambda i: (level_order.get(i["level"], 9), i["code"], i["column"]))
    return issues


def _score(
    columns, overall_cell_missing_rate, disguised_null_count, disguised_ratio,
    duplicate_row_rate, unnamed_col_count, duplicate_col_name_count,
    timeliness_enabled, lag_days, span_days, issues,
    declared_key_duplicate,
) -> Dict[str, Any]:
    p = 0.0
    for cf in columns.values():
        r = cf["missing_rate"]
        if r is None:
            continue
        if r >= _MISSING_P0:
            p += 20
        elif r >= _MISSING_P1:
            p += 8
    if overall_cell_missing_rate is not None:
        p += min(30.0, overall_cell_missing_rate * 100.0)
    if disguised_null_count > 0 and disguised_ratio is not None:
        p += min(10.0, disguised_ratio * 100.0)
    dim_completeness = clip100(p)

    p = 0.0
    if duplicate_row_rate is not None:
        p += duplicate_row_rate * 100.0 * 2.0
    if declared_key_duplicate:
        p += 40
    dim_uniqueness = clip100(p)

    p = 0.0
    for cf in columns.values():
        pfr = cf["parse_fail_rate"]
        if pfr is not None and pfr >= _PARSE_P0:
            p += 20
        if cf["outlier_skipped"]:
            continue
        obr = cf["outlier_rate"]
        if obr is None:
            continue
        if obr >= _OUTLIER_P0:
            p += 15
        elif obr >= _OUTLIER_P1:
            p += 5
    dim_validity = clip100(p)

    p = 0.0
    for cf in columns.values():
        vg = cf["variant_gap"]
        if vg is not None and vg > 0:
            p += min(10, vg)
        if cf["magnitude_span_flag"]:
            p += 5
    dim_consistency = clip100(p)

    dim_timeliness = None
    if timeliness_enabled and lag_days is not None:
        lag_eff = max(0, lag_days)
        extra = 0 if lag_eff <= 30 else math.ceil((lag_eff - 30) / 30)
        p = 5 * extra
        if span_days is not None and span_days > _SPAN_P1_DAYS:
            p += 10
        dim_timeliness = clip100(p)

    p = 0.0
    p += 10 * unnamed_col_count
    if duplicate_col_name_count > 0:
        p += 15
    hc_n = sum(1 for cf in columns.values() if cf["high_cardinality"])
    p += min(20, 5 * hc_n)
    dim_interpretability = clip100(p)

    dims_map = {
        "completeness": dim_completeness,
        "uniqueness": dim_uniqueness,
        "validity": dim_validity,
        "consistency": dim_consistency,
        "interpretability": dim_interpretability,
        "timeliness": dim_timeliness,
    }
    used = [v for k, v in dims_map.items() if v is not None]
    overall_score = int(round_half_up(sum(used) / len(used), 0))
    p0 = sum(1 for i in issues if i["level"] == "P0")
    p1 = sum(1 for i in issues if i["level"] == "P1")
    p2 = sum(1 for i in issues if i["level"] == "P2")
    if p0 > 0:
        overall_score = min(overall_score, 49)
    if overall_score >= 85:
        grade = "A"
    elif overall_score >= 70:
        grade = "B"
    elif overall_score >= 50:
        grade = "C"
    else:
        grade = "D"
    return {
        "overall_score": overall_score,
        "overall_grade": grade,
        "dims": dims_map,
        "issue_p0_count": p0,
        "issue_p1_count": p1,
        "issue_p2_count": p2,
    }
