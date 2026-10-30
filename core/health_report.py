#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Data health report: program computes metrics, LLM only narrates (PRD)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from core.number_whitelist import validate_whitelist
from core.quality_ledger import HealthLedgerError, N_ROWS_MIN, build_quality_ledger

logger = logging.getLogger(__name__)

_VAGUE = (
    "可能存在", "或许", "也许", "似乎", "看起来", "有待", "不排除",
    "总体较好", "质量尚可", "整体健康", "可能需要", "进一步分析",
)
_METHOD_BAN = (
    "聚类", "K-means", "Kmeans", "DBSCAN", "回归", "ARIMA",
    "预测", "随机森林", "神经网络", "深度学习", "XGBoost",
)
_DIGIT_RE = re.compile(r"\d")
_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _pct(v) -> str:
    if v is None:
        return "—"
    return f"{v}"


def _na(v, suffix="") -> str:
    if v is None:
        return "N/A"
    return f"{v}{suffix}"


def render_section0(ledger: Dict) -> str:
    g = ledger["global"]
    roles = ledger["roles"]
    ids = [c for c, r in roles.items() if r == "identifier"]
    nums = [c for c, r in roles.items() if r == "numeric"]
    cats = [c for c, r in roles.items() if r == "category"]
    dts = [c for c, r in roles.items() if r == "datetime"]
    hc = [c for c, cf in ledger["columns"].items() if cf.get("high_cardinality")]
    n = g["n_rows_raw"]
    lines = [
        "**本节由系统自动生成，是本报告的事实基准。所有数字以本节及评分卡为准；后文叙事不得新增数字。**",
        "",
        f"- **体检对象**：原始表（清洗前）",
        f"- **数据规模**：{n} 行 × {g['n_cols']} 列",
        f"- **标识符字段（不分析均值/分布/离群点）**：{', '.join(ids) if ids else '无'}",
        f"- **数值字段**：{', '.join(nums[:12]) if nums else '无'}" + ("…" if len(nums) > 12 else ""),
        f"- **分类字段**：{', '.join(cats[:12]) if cats else '无'}" + ("…" if len(cats) > 12 else ""),
        f"- **时间字段**：{', '.join(dts) if dts else '无'}",
    ]
    if g.get("declared_primary_key"):
        lines.append("- **已声明主键**：" + " + ".join(g["declared_primary_key"]))
    else:
        lines.append("- **未声明主键**：标识符重复只作描述，不按主键错误评级，唯一性维度不评估业务主键。")
    if not g["timeliness_enabled"]:
        lines.append("- **无可用时间字段**：及时性为 N/A，不计入综合分。")
    if hc:
        notes = []
        for c in hc[:4]:
            cf = ledger["columns"][c]
            notes.append(f"{c}（{cf['n_unique']} 个唯一值，均 {cf['avg_per_category']} 条/类）")
        lines.append(f"- **高基数类别（仅支持 Top-N）**：{'; '.join(notes)}")
    if g["has_clean_contrast"] and g["n_rows_clean"] is not None:
        lines.append(
            f"- **清洗对照（附录）**：清洗后 {g['n_rows_clean']} 行；"
            f"伪装空值 {g['disguised_null_count']} 个单元格"
        )
    lines.append("- **本报告不做**：业务真伪核验、因果/预测/聚类/回归、跨表参照完整性。IQR 离群是统计现象，不等于录入错误。")
    if n < N_ROWS_MIN:
        lines.append(f"\n> ⚠️ **样本不足警告**：当前仅 {n} 条记录，无法生成有效分析。")
    elif n < 30:
        lines.append(
            f"\n> ⚠️ **样本不足警告**：当前仅 {n} 条记录，所有统计结论可靠性极低。"
            f"**样本不足，不做可用性推断。**"
        )
    elif n < 100:
        lines.append(f"\n> ⚠️ **样本偏少**：当前 {n} 条记录，统计量存在较大抽样误差，结论仅供参考。")
    return "\n".join(lines)


def render_section1(ledger: Dict) -> str:
    sc = ledger["scorecard"]
    dims = sc["dims"]
    dim_names = [
        ("completeness", "完整性"),
        ("uniqueness", "唯一性"),
        ("validity", "有效性"),
        ("consistency", "一致性"),
        ("timeliness", "及时性"),
        ("interpretability", "可解释性"),
    ]
    rows = []
    for key, label in dim_names:
        v = dims.get(key)
        rows.append(f"| {label} | {_na(v)} |")
    return "\n".join([
        f"**综合分**：{sc['overall_score']}　**等级**：{sc['overall_grade']}",
        f"P0 {sc['issue_p0_count']} 条 · P1 {sc['issue_p1_count']} 条 · P2 {sc['issue_p2_count']} 条",
        "",
        "| 维度 | 分数 |",
        "|---|---|",
        *rows,
        "",
        "等级门槛：A≥85，B≥70，C≥50，D<50。任一 P0 时综合分封顶 49。",
    ])


def render_section2(ledger: Dict) -> str:
    issues = ledger["issues"]
    if not issues:
        return "未发现达到阈值的质量问题。"
    lines = [
        "| 级别 | 代码 | 字段 | 说明 | 影响行数 |",
        "|---|---|---|---|---|",
    ]
    for i in issues:
        ex = ""
        if i.get("examples"):
            ex = " 示例：" + ", ".join(str(x) for x in i["examples"][:5])
        aff = i["affected_rows"] if i["affected_rows"] is not None else "—"
        msg = (i["message"] + ex).replace("|", "/")
        lines.append(
            f"| {i['level']} | `{i['code']}` | {i['column']} | {msg} | {aff} |"
        )
    return "\n".join(lines)


def render_section3(ledger: Dict) -> str:
    lines = [
        "| 字段 | 角色 | 类型 | 缺失率% | 唯一值 | 离群数 | 解析失败% | 变体差 | 评级 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in ledger["field_table"]:
        oc = row["outlier_count"]
        oc_s = "—" if oc is None else str(oc)
        vg = row["variant_gap"]
        vg_s = "—" if vg is None else str(vg)
        lines.append(
            f"| {row['col_name']} | {row['role']} | {row['dtype']} | "
            f"{_pct(row['missing_pct'])} | {row['n_unique']} | {oc_s} | "
            f"{_pct(row['parse_fail_pct'])} | {vg_s} | {row['field_grade']} |"
        )
    skipped = [
        c for c, cf in ledger["columns"].items()
        if cf.get("outlier_skipped") and cf["role"] == "numeric"
    ]
    extra = ""
    if skipped:
        extra = "\n\n以下数值列无变异或有效值不足 4，未做离群检测：" + ", ".join(skipped)
    return "\n".join(lines) + extra


def render_section7(ledger: Dict) -> str:
    g = ledger["global"]
    contrast = ""
    if g["has_clean_contrast"] and g["n_rows_clean"] is not None:
        contrast = (
            f"\n### 清洗对照\n\n"
            f"- 原始行数 {g['n_rows_raw']}，清洗后 {g['n_rows_clean']}\n"
            f"- 伪装空值单元格 {g['disguised_null_count']}\n"
        )
    return (
        "### 计算说明\n\n"
        "- 缺失：质量副本上将伪装空值（N/A、未知、空 等）标为 NA 后再计 `isna()`。\n"
        "- 离群：IQR×1.5，分位线性插值；分母为非空有效值；IQR=0 或 n<4 则跳过。\n"
        "- 标准差：样本标准差 ddof=1。\n"
        "- 比率入账：四位小数远离 0 的四舍五入；展示百分数两位。\n"
        "- 及时性无日期列则为 N/A，不参与综合分。\n"
        f"{contrast}"
    )


def ledger_summary_for_prompt(ledger: Dict) -> str:
    g = ledger["global"]
    sc = ledger["scorecard"]
    lines = [
        f"n_rows_raw={g['n_rows_raw']} n_cols={g['n_cols']}",
        f"overall_score={sc['overall_score']} grade={sc['overall_grade']}",
        f"dims={sc['dims']}",
        f"P0={sc['issue_p0_count']} P1={sc['issue_p1_count']} P2={sc['issue_p2_count']}",
        f"duplicate_row_count={g['duplicate_row_count']} disguised_null_count={g['disguised_null_count']}",
        "issues:",
    ]
    for i in ledger["issues"][:24]:
        lines.append(
            f"  {i['issue_id']} level={i['level']} affected={i['affected_rows']} | {i['message']}"
        )
    return "\n".join(lines)


def _strip_banned(text: str) -> str:
    out = []
    for line in (text or "").split("\n"):
        if any(m in line for m in _METHOD_BAN):
            continue
        out.append(line)
    return "\n".join(out)


def _has_vague(text: str) -> bool:
    return any(w in (text or "") for w in _VAGUE)


def _parse_claims(raw: str) -> Optional[dict]:
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    m = _JSON_RE.search(raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _fallback_narrative(ledger: Dict) -> Dict[str, str]:
    sc = ledger["scorecard"]
    issues = ledger["issues"]
    top = issues[:8]
    if ledger["global"]["n_rows_raw"] < 30:
        availability = "样本不足，不做可用性推断。"
    else:
        if sc["overall_grade"] in ("A", "B"):
            availability = "评分卡显示数据可用于描述性汇总；仍须先处理问题清单中的 P0/P1。"
        else:
            availability = "评分卡显示数据可用性受限，建议先按问题清单处理 P0 后再做图表或汇总。"
    insight_lines = []
    for i in top:
        insight_lines.append(f"- 【{i['level']}】`{i['issue_id']}` {i['message']}")
    if not insight_lines:
        insight_lines = ["- 问题清单为空，字段级质量未触及设定阈值。"]
    sug = []
    for i in top:
        if i["level"] == "P0":
            pri = "高"
        elif i["level"] == "P1":
            pri = "中"
        else:
            pri = "低"
        aff = i["affected_rows"]
        aff_s = f"影响 {aff} 行。" if aff is not None else ""
        sug.append(f"- 【优先级{pri}】针对 `{i['issue_id']}`：{aff_s}按该问题代码对应规则修复后重新体检。")
    if not sug:
        sug = ["- 保持当前采集规范，定期复检缺失与重复。"]
    poor = [r for r in ledger["field_table"] if r["field_grade"] in ("C", "D")]
    fields = "\n".join(
        f"- `{r['col_name']}` 评级 {r['field_grade']}，缺失率 {r['missing_pct']}%"
        for r in poor[:12]
    ) or "- 无 C/D 级字段。"
    return {
        "overview": f"综合分 {sc['overall_score']}，等级 {sc['overall_grade']}。详见评分卡与问题清单，不另估分数。",
        "fields": fields,
        "insights": "\n".join(insight_lines),
        "anomalies": availability,
        "suggestions": "\n".join(sug),
    }


def _claims_to_sections(claims: dict, ledger: Dict) -> Dict[str, str]:
    issue_map = {i["issue_id"]: i for i in ledger["issues"]}
    pri_map = {"P0": "高", "P1": "中", "P2": "低"}

    def take(items, kind: str) -> List[str]:
        lines = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            iid = str(item.get("issue_id") or "")
            if iid not in issue_map:
                continue
            issue = issue_map[iid]
            text = str(item.get("comment") if kind != "suggestions" else item.get("action") or "")
            text = _strip_banned(text).strip()
            if _DIGIT_RE.search(text) or _has_vague(text):
                text = ""
            aff = issue["affected_rows"]
            aff_s = f"影响 {aff} 行。" if aff is not None else ""
            if kind == "suggestions":
                pri = pri_map.get(issue["level"], "中")
                body = text or "按该问题代码对应规则修复后重新体检。"
                lines.append(f"- 【优先级{pri}】`{iid}` {aff_s}{body}")
            else:
                body = text
                prefix = f"- 【{issue['level']}】`{iid}` {issue['message']}"
                lines.append(prefix + (f"。{body}" if body else ""))
        return lines

    insights = take(claims.get("insights"), "insights")
    suggestions = take(claims.get("suggestions"), "suggestions")
    availability = claims.get("availability")
    if isinstance(availability, str):
        availability = _strip_banned(availability)
        if _DIGIT_RE.search(availability) or _has_vague(availability):
            availability = None
    fb = _fallback_narrative(ledger)
    return {
        "overview": fb["overview"],
        "fields": fb["fields"],
        "insights": "\n".join(insights) if insights else fb["insights"],
        "anomalies": availability or fb["anomalies"],
        "suggestions": "\n".join(suggestions) if suggestions else fb["suggestions"],
    }


def _call_llm(ledger: Dict, query: str, provider: Optional[str]) -> Optional[dict]:
    try:
        import sys
        from pathlib import Path
        _root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(_root / "LLM"))
        from llm_config_manager import get_config_manager, get_llm_client
        manager = get_config_manager()
        _provider = provider or manager.get_default_provider()
        if not _provider:
            return None
        config = manager.get_config(_provider)
        if not config:
            return None
        client = get_llm_client(_provider)
        prompt = f"""你是数据质量工程师。只根据「事实账本」解读已列出的问题，禁止发明新问题，禁止输出任何阿拉伯数字。
用户需求：{query or '请对数据进行全面质量体检'}

事实账本：
{ledger_summary_for_prompt(ledger)}

只输出 JSON（不要 markdown 围栏）：
{{
  "insights": [{{"issue_id": "必须来自账本", "comment": "不含数字的定性说明"}}]
}}
insights 最多 8 条。禁止聚类/回归/预测。禁止「总体较好」「质量尚可」「可能」等模糊词。禁止写样本不足。
"""
        resp = client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=2000,
        )
        raw = (resp.choices[0].message.content or "").strip()
        return _parse_claims(raw)
    except Exception as e:
        logger.warning(f"health LLM failed: {e}")
        return None


def _plain(text: str) -> str:
    return (text or "").replace("**", "").replace("`", "")


def _field_label(column: str) -> str:
    if not column or column == "__global__":
        return "这张表"
    return f"「{column}」"


def plain_scope(ledger: Dict) -> List[Dict[str, str]]:
    g = ledger["global"]
    if g.get("declared_primary_key"):
        key_rule = "按已声明的主键列检查编号是否重复。"
    else:
        key_rule = "未声明主键列，本次不评估编号是否重复。"
    if g.get("timeliness_enabled"):
        time_rule = "按识别到的日期列评估数据新旧。"
    else:
        time_rule = "未识别到日期列，本次不评估数据新旧。"
    rows = [
        {"term": "对象", "rule": "体检对象为上传的原始表。计分前不改写数据。"},
        {"term": "缺失", "rule": "空单元格，以及取值为「无」「未知」「N/A」的单元格，均计为未填。"},
        {"term": "离群", "rule": "明显偏大或偏小的数值只提示核对，不直接认定为填错。"},
        {"term": "主键", "rule": key_rule},
        {"term": "时间", "rule": time_rule},
    ]
    if g["n_rows_raw"] < 30:
        rows.append({"term": "样本", "rule": "有效行数不足 30，本次不做可用性推断。"})
    return rows


def plain_reads(ledger: Dict) -> List[str]:
    lines = []
    for issue in ledger["issues"][:8]:
        name = _field_label(issue.get("column"))
        n = issue.get("affected_rows")
        code = issue.get("code")
        if code == "MISSING_RATE_HIGH" and n is not None:
            lines.append(f"{name}有 {n} 格没填。")
        elif code == "DUPLICATE_ROWS" and n is not None:
            lines.append(f"有 {n} 行和前面某一行完全一样。")
        elif code == "DUPLICATE_KEY" and n is not None:
            lines.append(f"你指定的编号有 {n} 行是重复的。")
        elif code == "KEY_MISSING" and n is not None:
            lines.append(f"你指定的编号有 {n} 行是空的。")
        elif code == "OUTLIER_HIGH" and n is not None:
            lines.append(f"{name}有 {n} 个特别大或特别小的数，先核对，不一定是错。")
        elif code == "PARSE_FAIL":
            lines.append(f"{name}里有些内容格式对不上，比如该填数字的地方写了别的字。")
        elif code == "VARIANT_GAP":
            lines.append(f"{name}里同一个意思写成了好几种样子，比如多了空格或大小写不同。")
        elif code == "CARDINALITY_HIGH":
            lines.append(f"{name}里的类别很多，每一类只有很少几条，不适合拿来互相比较。")
        elif code == "CARDINALITY_LOW":
            lines.append(f"{name}里几乎只有一两种取值。")
        elif code == "TEXT_TOO_LONG":
            lines.append(f"{name}里的文字偏长，直接当标签会看不清。")
        elif code == "LAG_HIGH":
            lines.append("表里最近的日期已经比较久了。")
        elif code == "FUTURE_DATE":
            lines.append("表里有比今天更晚的日期。")
        elif code == "DATETIME_SPAN_LONG":
            lines.append(f"{name}的日期拉得很长。")
        elif code == "UNNAMED_COL":
            lines.append("有的列没有名字。")
        elif code == "DUPLICATE_COL_NAME":
            lines.append("有几列用了同一个名字。")
        elif code == "MAGNITUDE_SPAN":
            lines.append(f"{name}里的数字相差特别大，请核对是不是单位不一致。")
        elif code == "TOO_FEW_ROWS":
            lines.append("行数太少，看不出稳定的情况。")
    if not lines:
        lines.append("没有需要特别指出的问题。")
    return lines


def build_report_view(ledger: Dict, sections: Dict, boundary: str, appendix: str) -> Dict[str, Any]:
    g = ledger["global"]
    sc = ledger["scorecard"]
    dim_labels = [
        ("completeness", "完整性", "格子有没有填上。空着，或写成「无」「未知」「N/A」的，都算没填。空得越多，分越低。"),
        ("uniqueness", "唯一性", "有没有整行完全一样。你指定了编号之后，编号重复也会扣分。"),
        ("validity", "有效性", "数字和日期能不能按原样读懂。读不懂，或特别大、特别小的数越多，分越低。"),
        ("consistency", "一致性", "同一种内容有没有写成好几种样子，数字之间是不是差得特别大。"),
        ("timeliness", "及时性", "日期离今天有多远、跨了多少年。表里没有日期时，这一项不计入综合分。"),
        ("interpretability", "结构", "列名清不清楚、有没有重名，取值是不是多到不好对照。"),
    ]
    dims = []
    for key, label, meaning in dim_labels:
        value = sc["dims"].get(key)
        note = None
        if key == "timeliness" and value is None:
            note = "没有可用日期列"
        if key == "uniqueness" and not g.get("declared_primary_key"):
            note = "未声明主键"
        dims.append({"key": key, "label": label, "value": value, "note": note, "meaning": meaning})
    return {
        "score": sc["overall_score"],
        "grade": sc["overall_grade"],
        "rows": g["n_rows_raw"],
        "cols": g["n_cols"],
        "subject": "原始表",
        "p0": sc["issue_p0_count"],
        "p1": sc["issue_p1_count"],
        "p2": sc["issue_p2_count"],
        "dims": dims,
        "scored_dims": sum(1 for d in dims if d["value"] is not None),
        "boundary": _plain(boundary),
        "scope": plain_scope(ledger),
        "reads": plain_reads(ledger),
        "issues": ledger["issues"],
        "fields": ledger["field_table"],
        "insights": _plain(sections.get("insights", "")),
        "appendix": _plain(appendix),
        "sample_warning": g["n_rows_raw"] < 30,
    }


def generate_health_report(
    df_raw,
    df_clean=None,
    query: str = "",
    provider: Optional[str] = None,
    primary_key: Optional[List[str]] = None,
) -> Dict[str, Any]:
    try:
        ledger = build_quality_ledger(df_raw, df_clean, primary_key=primary_key)
    except HealthLedgerError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"ledger failed: {e}", exc_info=True)
        return {"success": False, "error": f"数据分析失败：{e}"}

    log: List[str] = ["ReportType: health"]
    cap = render_section0(ledger)
    md1 = render_section1(ledger)
    md2 = render_section2(ledger)
    md3 = render_section3(ledger)
    md7 = render_section7(ledger)

    claims = _call_llm(ledger, query, provider)
    if claims:
        sections = _claims_to_sections(claims, ledger)
        log.append("LLM: JSON claims 解析成功")
        combined = "\n".join([sections.get(k, "") for k in
                              ("overview", "fields", "insights", "anomalies", "suggestions")])
        bad = validate_whitelist(combined, ledger["_allowed_numbers"])
        # claims path should have no digits in comments; leftover digits from program-injected issue messages are allowed
        if bad:
            log.append(f"Whitelist: 叙事含账本外数字 {len(bad)} 处，改用模板")
            sections = _fallback_narrative(ledger)
        else:
            log.append("Whitelist: 通过")
    else:
        sections = _fallback_narrative(ledger)
        log.append("LLM: 不可用，叙事使用程序模板")

    md = "\n".join([
        "## 检查范围\n\n" + cap,
        "## 一、健康评分卡\n\n" + md1,
        "## 二、问题清单\n\n" + md2,
        "## 三、字段质量明细\n\n" + md3,
        "## 四、主要问题解读\n\n" + sections.get("insights", ""),
        "## 五、附录\n\n" + md7,
    ])

    g = ledger["global"]
    sc = ledger["scorecard"]
    return {
        "success": True,
        "report_type": "health",
        "markdown": md,
        "sections": sections,
        "capability_boundary": cap,
        "stats": {
            "n_rows": g["n_rows_raw"],
            "n_cols": g["n_cols"],
            "quality_summary": {
                "ok": sc["issue_p0_count"] == 0,
                "error_count": sc["issue_p0_count"],
                "warning_count": sc["issue_p1_count"] + sc["issue_p2_count"],
                "issue_list": [
                    {"col": i["column"], "level": i["level"], "code": i["code"], "msg": i["message"]}
                    for i in ledger["issues"]
                ],
            },
            "health_scorecard": sc,
            "issue_list": ledger["issues"],
            "field_table": ledger["field_table"],
        },
        "view": build_report_view(ledger, sections, cap, md7),
        "postprocess_log": log,
    }
