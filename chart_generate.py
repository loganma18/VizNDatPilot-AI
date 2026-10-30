#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chart Generate - 调用 charts 目录中的实际图表生成模块"""
import sys
from pathlib import Path
import pandas as pd
from typing import Dict, List, Optional, Any
import logging
import importlib

logger = logging.getLogger(__name__)

CHART_PROJECT = Path(__file__).parent
sys.path.insert(0, str(CHART_PROJECT))

try:
    from charts.base import ChartResult, FieldMapping
except ImportError as e:
    logger.error(f"Failed to import charts.base: {e}")
    # 定义备用类
    class ChartResult:
        def __init__(self, html="", spec=None, warnings=None, meta=None):
            self.html = html
            self.spec = spec or {}
            self.warnings = warnings or []
            self.meta = meta or {}
        def is_valid(self):
            return bool(self.html.strip()) and len(self.html) > 500
    
    class FieldMapping:
        pass


def generate_chart(
    df: pd.DataFrame = None,
    excel_path: str = None,
    chart_type: str = "bar_chart",
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    color_scheme: str = "mckinsey",
    **kwargs
) -> Dict[str, Any]:
    """生成图表 - 调用 charts 目录中的实际模块"""
    try:
        if df is None and excel_path:
            df = pd.read_excel(excel_path)
        
        if df is None or df.empty:
            return {"error": "No data"}
        
        logger.info(f"Generating {chart_type} with {len(df)} rows, {len(df.columns)} columns")

        # ✅ 统一列名类型：避免 heatmap 等图表里对列名做 .lower() 时遇到 int
        df = df.copy()
        df.columns = df.columns.map(str)
        
        # 动态导入图表模块
        try:
            module = importlib.import_module(f"charts.{chart_type}")
            generate_func = getattr(module, "generate", None)
            
            if not generate_func:
                logger.error(f"Module charts.{chart_type} has no generate function")
                return {"error": f"Chart type {chart_type} not found"}
        except ImportError as e:
            logger.error(f"Failed to import charts.{chart_type}: {e}")
            return {"error": f"Chart type {chart_type} not found"}
        
        # 自动检测字段映射
        if not mapping:
            mapping = _auto_detect_mapping(df, chart_type)

        # 统一 mapping 中的列名为字符串（mapping 的 value 通常是列名）
        # 但对于列表类型（如dimensions）保持原样
        if mapping:
            mapping = {k: (v if isinstance(v, list) else (str(v) if v is not None else v)) for k, v in mapping.items()}
        
        # 合并 options，添加 color_scheme
        merged_options = options or {}
        merged_options['color_scheme'] = color_scheme
        
        # 调用图表生成函数
        result = generate_func(df=df, mapping=mapping, options=merged_options)
        
        # 检查返回类型
        if isinstance(result, ChartResult):
            if result.is_valid():
                return {
                    "success": True,
                    "html": result.html,
                    "chart_type": chart_type,
                    "warnings": result.warnings,
                    "meta": result.meta
                }
            else:
                return {"error": "Generated chart is invalid"}
        elif isinstance(result, dict):
            if result.get("html"):
                return {
                    "success": True,
                    "html": result.get("html"),
                    "chart_type": chart_type
                }
            else:
                return {"error": result.get("error", "Unknown error")}
        else:
            return {"error": f"Unexpected result type: {type(result)}"}
    
    except Exception as e:
        logger.error(f"Chart generation error: {e}", exc_info=True)
        return {"error": str(e)}


def _auto_detect_mapping(df: pd.DataFrame, chart_type: str) -> Dict[str, str]:
    """自动检测字段映射（大小写不敏感匹配）"""
    try:
        df = df.copy()
        df.columns = df.columns.map(str)
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        string_cols = df.select_dtypes(include=['object']).columns.tolist()

        # 优先使用低基数字符串列作为分类轴（排除 id/email/ip 等高基数列）
        cat_cols = [c for c in string_cols
                    if df[c].nunique() <= max(50, len(df) * 0.3)]
        if not cat_cols:
            cat_cols = string_cols  # 回退到所有字符串列

        # 优先使用非 id 的数值列（排除纯流水号）
        val_cols = [c for c in numeric_cols
                    if not any(k in c.lower() for k in ('id', '_no', '_key'))]
        if not val_cols:
            val_cols = numeric_cols  # 回退到所有数值列

        mapping = {}
        ct = chart_type.lower()  # 统一转小写后匹配

        if ct in ("bar_chart",):
            if cat_cols and val_cols:
                mapping["x"] = cat_cols[0]
                mapping["y"] = val_cols[0]
            elif string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]

        elif ct in ("grouped_bar_chart", "grouped_bar"):
            if cat_cols and val_cols:
                mapping["x"] = cat_cols[0]
                mapping["y"] = val_cols[0]
                if len(cat_cols) > 1:
                    mapping["series"] = cat_cols[1]

        elif ct in ("stacked_bar_chart", "stacked_bar"):
            if cat_cols and val_cols:
                mapping["x"] = cat_cols[0]
                mapping["y"] = val_cols[0]
                if len(cat_cols) > 1:
                    mapping["series"] = cat_cols[1]

        elif ct in ("line_chart",):
            if cat_cols and val_cols:
                mapping["x"] = cat_cols[0]
                mapping["y"] = val_cols[0]
            elif len(numeric_cols) >= 2:
                mapping["x"] = numeric_cols[0]
                mapping["y"] = numeric_cols[1]

        elif ct in ("scatter_plot",):
            if len(val_cols) >= 2:
                mapping["x"] = val_cols[0]
                mapping["y"] = val_cols[1]
                if len(val_cols) >= 3:
                    mapping["size"] = val_cols[2]

        elif ct in ("pie_chart", "pie"):
            if cat_cols and val_cols:
                mapping["label"] = cat_cols[0]
                mapping["value"] = val_cols[0]

        elif ct in ("heatmap",):
            if len(val_cols) >= 2:
                mapping["x"] = val_cols[0]
                mapping["y"] = val_cols[1]
                if len(val_cols) >= 3:
                    mapping["value"] = val_cols[2]

        elif ct in ("histogram_pareto_chart", "histogram_chart"):
            if val_cols:
                mapping["value"] = val_cols[0]

        elif ct in ("box-and-whisker_plot", "boxplot_chart"):
            if cat_cols and val_cols:
                mapping["x"] = cat_cols[0]
                mapping["y"] = val_cols[0]
            elif val_cols:
                mapping["y"] = val_cols[0]

        elif ct in ("violin_chart",):
            if cat_cols and val_cols:
                mapping["x"] = cat_cols[0]
                mapping["y"] = val_cols[0]
            elif val_cols:
                mapping["y"] = val_cols[0]

        elif ct in ("ridgeline_plot",):
            if cat_cols and val_cols:
                mapping["group"] = cat_cols[0]
                mapping["value"] = val_cols[0]
            elif val_cols:
                mapping["value"] = val_cols[0]

        elif ct in ("beeswarm_plot",):
            if cat_cols and val_cols:
                mapping["group"] = cat_cols[0]
                mapping["value"] = val_cols[0]
            elif val_cols:
                mapping["value"] = val_cols[0]

        elif ct in ("waterfall",):
            if cat_cols and val_cols:
                mapping["x"] = cat_cols[0]
                mapping["y"] = val_cols[0]

        elif ct in ("sunburst_diagram", "sunburst"):
            if len(cat_cols) >= 2 and val_cols:
                mapping["labels"] = cat_cols[0]
                mapping["parents"] = cat_cols[1]
                mapping["values"] = val_cols[0]

        elif ct in ("treemap",):
            if cat_cols and val_cols:
                mapping["labels"] = cat_cols[0]
                mapping["values"] = val_cols[0]
                if len(cat_cols) >= 2:
                    mapping["parents"] = cat_cols[1]

        elif ct in ("parallel_coordinates_plot",):
            if val_cols:
                mapping["dimensions"] = val_cols
                if cat_cols:
                    mapping["color"] = cat_cols[0]

        elif ct in ("connected_scatter",):
            if len(val_cols) >= 2:
                mapping["x"] = val_cols[0]
                mapping["y"] = val_cols[1]
                if len(val_cols) >= 3:
                    mapping["size"] = val_cols[2]
                if cat_cols:
                    mapping["order"] = cat_cols[0]

        elif ct in ("diverging_bar_chart",):
            if cat_cols and val_cols:
                mapping["label"] = cat_cols[0]
                mapping["value"] = val_cols[0]

        elif ct in ("area_chart", "stacked_area_chart"):
            if cat_cols and val_cols:
                mapping["x"] = cat_cols[0]
                mapping["y"] = val_cols[0]

        elif ct in ("bubble_plot",):
            if len(val_cols) >= 3:
                mapping["x"] = val_cols[0]
                mapping["y"] = val_cols[1]
                mapping["size"] = val_cols[2]
                if cat_cols:
                    mapping["label"] = cat_cols[0]

        logger.info(f"Auto-detected mapping for {chart_type}: {mapping}")
        return mapping

    except Exception as e:
        logger.warning(f"Failed to auto-detect mapping: {e}")
        return {}


def recommend_charts(df: pd.DataFrame = None, excel_path: str = None, limit: int = 5) -> List[Dict]:
    """推荐图表"""
    try:
        if df is None and excel_path:
            df = pd.read_excel(excel_path)
        
        if df is None:
            return []
        
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        string_cols = df.select_dtypes(include=['object']).columns.tolist()
        
        recommendations = []
        
        # 基于数据特征推荐
        if string_cols and numeric_cols:
            recommendations.append({"chart_id": "bar_chart", "score": 0.95})
            recommendations.append({"chart_id": "grouped_bar", "score": 0.90})
            recommendations.append({"chart_id": "line_chart", "score": 0.85})
        
        if len(numeric_cols) >= 2:
            recommendations.append({"chart_id": "scatter_plot", "score": 0.80})
            recommendations.append({"chart_id": "heatmap", "score": 0.75})
        
        if string_cols and numeric_cols:
            recommendations.append({"chart_id": "pie", "score": 0.70})
        
        return recommendations[:limit]
    
    except Exception as e:
        logger.error(f"Recommend error: {e}")
        return []
