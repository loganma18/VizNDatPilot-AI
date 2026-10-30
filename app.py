#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
import pandas as pd
from pathlib import Path
import sys
from charts.registry import list_charts
from charts.color_schemes import list_color_schemes, get_color_scheme
import os
from datetime import datetime
import logging
import json
from utils.validator import DataCleaner, DataValidator, IssueLevel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHART_PROJECT = Path(__file__).parent
sys.path.insert(0, str(CHART_PROJECT))
sys.path.insert(0, str(CHART_PROJECT / "LLM"))
import llm_config_manager as lcm

try:
    from chart_generate import generate_chart
    from llm_recommender import analyze_data_with_llm
    from llm_config_manager import LLMConfigManager
    from charts.registry import REGISTRY
    from core.health_report import generate_health_report
except ImportError as e:
    logger.error(f"Import error: {e}")
    sys.exit(1)

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.template_folder = str(CHART_PROJECT / 'templates')

# 环境自适应路径
BASE_DIR = Path('/tmp') if os.environ.get('VERCEL') else CHART_PROJECT

UPLOAD_FOLDER = BASE_DIR / "uploads"
OUTPUT_FOLDER = BASE_DIR / "outputs"
CHART_MAPPING_FILE = BASE_DIR / "chart_mapping.json"

UPLOAD_FOLDER.mkdir(exist_ok=True, parents=True)
OUTPUT_FOLDER.mkdir(exist_ok=True, parents=True)

llm_manager = LLMConfigManager()

# 加载或初始化图表映射（记录每个 chart_id 最后生成的文件）
def load_chart_mapping():
    if CHART_MAPPING_FILE.exists():
        try:
            with open(CHART_MAPPING_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_chart_mapping(mapping):
    with open(CHART_MAPPING_FILE, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)

chart_mapping = load_chart_mapping()

def get_all_charts():
    """从 registry.py 获取所有图表元数据"""
    return [
        {
            "chart_id": c.chart_id,
            "name": c.name,
            "category": c.category,
            "desc": c.desc,
            "data_format": c.data_format,
            "options": c.options  # 返回图表特定选项
        }
        for c in list_charts()
    ]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/charts')
def get_charts():
    try:
        charts = get_all_charts()
        return jsonify({"charts": charts})
    except Exception as e:
        logger.error(f"Get charts error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/color-schemes')
def get_color_schemes():
    """获取所有可用的配色方案"""
    try:
        schemes = list_color_schemes()
        return jsonify({"success": True, "color_schemes": schemes})
    except Exception as e:
        logger.error(f"Get color schemes error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload():
    try:
        logger.info("Upload request received")
        file = request.files.get('file')
        if not file:
            return jsonify({"error": "No file"}), 400
        
        filename = file.filename
        filepath = UPLOAD_FOLDER / filename
        file.save(str(filepath))
        logger.info(f"File saved: {filepath}")
        
        try:
            if filename.endswith('.xlsx'):
                df = pd.read_excel(filepath, engine='openpyxl')
            else:
                df = pd.read_csv(filepath)
        except Exception as e:
            logger.warning(f"Read failed: {e}, trying fallback")
            if 'openpyxl' in str(e) or 'NoneType' in str(e):
                try:
                    df = pd.read_excel(filepath, engine='xlrd')
                except:
                    df = pd.read_excel(filepath)
            else:
                raise
        
        logger.info(f"File read: {len(df)} rows, {len(df.columns)} columns")

        # 数据质量检测
        try:
            validation = DataValidator(df).validate()
            quality_summary = validation.summary()
            logger.info(f"Validation: {quality_summary['error_count']} errors, {quality_summary['warning_count']} warnings")
        except Exception as ve:
            logger.warning(f"Validation failed (non-fatal): {ve}")
            quality_summary = {"ok": True, "error_count": 0, "warning_count": 0, "issue_list": []}

        return jsonify({
            "success": True,
            "filepath": str(filepath),
            "rows": len(df),
            "columns": list(df.columns),
            "quality": quality_summary
        })
    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
def analyze():
    try:
        data = request.json
        filepath = data.get('filepath')
        query = data.get('query', '')
        provider = data.get('provider', 'deepseek')
        
        logger.info(f"Analyze: {filepath}, provider={provider}")
        
        if not Path(filepath).exists():
            return jsonify({"error": f"File not found: {filepath}"}), 400
        
        try:
            if filepath.endswith('.xlsx'):
                df = pd.read_excel(filepath, engine='openpyxl')
            else:
                df = pd.read_csv(filepath)
        except Exception as e:
            if 'openpyxl' in str(e) or 'NoneType' in str(e):
                try:
                    df = pd.read_excel(filepath, engine='xlrd')
                except:
                    df = pd.read_excel(filepath)
            else:
                raise
        
        # 清洗数据后再交给 LLM
        try:
            df = DataCleaner().clean(df)
            logger.info(f"DataCleaner done: {df.shape[0]} rows x {df.shape[1]} cols after cleaning")
        except Exception as ce:
            logger.warning(f"DataCleaner failed (non-fatal): {ce}")

        config = llm_manager.get_config(provider)
        if config:
            analysis = analyze_data_with_llm(df, query, provider=config.provider, api_key=config.api_key, base_url=config.base_url, model=config.model)
        else:
            analysis = analyze_data_with_llm(df, query)
        
        return jsonify({
            "success": True,
            "analysis": analysis,
            "recommendations": analysis.get('recommendations', [])
        })
    except Exception as e:
        logger.error(f"Analyze error: {e}", exc_info=True)
        return jsonify({"error": str(e), "analysis": {"summary": "Analysis failed"}, "recommendations": []}), 500

@app.route('/api/generate', methods=['POST'])
def generate():
    try:
        data = request.json
        filepath = data.get('filepath')
        chart_type = data.get('chart_type')
        color_scheme = data.get('color_scheme', 'mckinsey')
        mapping = data.get('mapping')
        options = data.get('options')
        
        logger.info(f"Generate request: chart_type={chart_type}, filepath={filepath}, color_scheme={color_scheme}")
        
        if not chart_type:
            logger.error("chart_type is empty")
            return jsonify({"error": "chart_type is required"}), 400
        
        if filepath.endswith('.xlsx'):
            df = pd.read_excel(filepath, engine='openpyxl')
        else:
            df = pd.read_csv(filepath)

        # 清洗数据
        try:
            df = DataCleaner().clean(df)
            logger.info(f"DataCleaner done: {df.shape[0]} rows x {df.shape[1]} cols")
        except Exception as ce:
            logger.warning(f"DataCleaner failed (non-fatal): {ce}")

        # 数据质量校验：只有行数不足（TOO_FEW_ROWS）才真正阻止图表生成；
        # OUTLIER_HIGH / MISSING_RATE_HIGH / DUPLICATE_ROWS 等清洗后仍可绘图，降级为警告
        try:
            validation = DataValidator(df).validate()
            fatal = [i for i in validation.get_issues(IssueLevel.ERROR) if i.code == "TOO_FEW_ROWS"]
            if fatal:
                logger.warning(f"Fatal data errors: {[i.message for i in fatal]}")
                return jsonify({"error": "数据质量问题阻止图表生成：" + "；".join(i.message for i in fatal)}), 400
            non_fatal = validation.get_issues(IssueLevel.ERROR)
            if non_fatal:
                logger.warning(f"Non-fatal data warnings (continuing): {[i.message for i in non_fatal]}")
        except Exception as ve:
            logger.warning(f"DataValidator failed (non-fatal): {ve}")

        logger.info(f"Calling generate_chart with chart_type={chart_type}, color_scheme={color_scheme}")
        result = generate_chart(df, chart_type=chart_type, mapping=mapping, options=options, color_scheme=color_scheme)
        logger.info(f"generate_chart returned: {result}")
        
        if isinstance(result, dict):
            if result.get('success'):
                output_file = OUTPUT_FOLDER / f"{chart_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(result.get('html', '<html><body>Chart generated</body></html>'))
                logger.info(f"Chart saved to {output_file}")
                
                # 记录映射：chart_id -> 最新生成的文件路径
                chart_mapping[chart_type] = str(output_file)
                save_chart_mapping(chart_mapping)
                logger.info(f"Updated chart mapping: {chart_type} -> {output_file}")
                
                return jsonify({"success": True, "download_url": f"/api/download/{output_file.name}"})
            else:
                logger.error(f"Chart generation failed: {result}")
                return jsonify({"error": result.get('error', 'Unknown error')}), 400
        else:
            logger.error(f"Invalid result type: {type(result)}")
            return jsonify({"error": "Invalid result type"}), 400
    except Exception as e:
        logger.error(f"Generate error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/download/<filename>')
def download(filename):
    try:
        file_path = OUTPUT_FOLDER / filename
        if file_path.exists():
            return send_file(str(file_path), as_attachment=True)
        return jsonify({"error": "File not found"}), 404
    except Exception as e:
        logger.error(f"Download error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/chart-detail')
def chart_detail_page():
    return render_template('chart-detail.html')

# ==================== LLM 配置管理接口 ====================

@app.route('/api/llm/list', methods=['GET'])
def llm_list():
    """获取所有 LLM 配置列表"""
    try:
        # 建议每次 list 前强制从磁盘重载，避免内存残留
        llm_manager.load_configs(load_from_env=False)

        logger.info(f"[LLM LIST] configs={list(llm_manager.configs.keys())}")
        logger.info(f"[LLM LIST] file={lcm.LLM_CONFIG_FILE.resolve()}")
        logger.info(f"[LLM LIST] manager_module={lcm.__file__}")

        configs = llm_manager.list_configs()
        default = llm_manager.get_default_provider()
        custom = llm_manager.get_custom_models()

        return jsonify({
            "success": True,
            "configs": configs,
            "default_provider": default,
            "custom_models": custom,
            "config_file": str(lcm.LLM_CONFIG_FILE.resolve()),   # 方便前端也看到真实路径
            "manager_module": lcm.__file__
        })
    except Exception as e:
        logger.error(f"LLM list error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/llm/add-custom', methods=['POST'])
def llm_add_custom():
    """添加自定义 OpenAI SDK 兼容的模型"""
    try:
        data = request.json
        name = data.get('name', '').strip()
        base_url = data.get('base_url', '').strip()
        model_name = data.get('model_name', '').strip()
        api_key = data.get('api_key', '').strip()
        
        if not all([name, base_url, model_name, api_key]):
            return jsonify({
                "success": False,
                "error": "缺少必要参数：name, base_url, model_name, api_key"
            }), 400
        
        success, message = llm_manager.add_custom_model(
            name=name,
            base_url=base_url,
            model_name=model_name,
            api_key=api_key
        )
        
        if success:
            provider_id = f"custom_{name.lower().replace(' ', '_')}"
            return jsonify({
                "success": True,
                "message": message,
                "provider_id": provider_id
            })
        else:
            return jsonify({
                "success": False,
                "error": message
            }), 400
    
    except Exception as e:
        logger.error(f"LLM add custom error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/llm/delete/<provider>', methods=['DELETE'])
def llm_delete(provider):
    """删除自定义模型配置"""
    try:
        success, message = llm_manager.delete_config(provider)
        
        if success:
            return jsonify({
                "success": True,
                "message": message
            })
        else:
            return jsonify({
                "success": False,
                "error": message
            }), 400
    
    except Exception as e:
        logger.error(f"LLM delete error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/llm/test/<provider>', methods=['POST'])
def llm_test(provider):
    """测试 LLM 配置是否有效"""
    try:
        result = llm_manager.test_config(provider)
        
        if result.get('success'):
            return jsonify(result)
        else:
            return jsonify(result), 400
    
    except Exception as e:
        logger.error(f"LLM test error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/llm/config', methods=['POST'])
def llm_config():
    """
    保存 LLM 配置（仅用于内置提供商）
    自定义模型请使用 /api/llm/add-custom
    """
    try:
        data = request.json
        provider = data.get('provider', '').strip()
        api_key = data.get('api_key', '').strip()
        
        if not provider or not api_key:
            return jsonify({
                "success": False,
                "error": "缺少必要参数：provider, api_key"
            }), 400
        
        # 检查是否是自定义模型
        config = llm_manager.get_config(provider)
        if config and config.is_custom:
            return jsonify({
                "success": False,
                "error": f"自定义模型 '{provider}' 不能使用此接口修改，请使用 /api/llm/delete 删除后重新添加"
            }), 400
        
        # 保存内置提供商配置
        success = llm_manager.set_config(
            provider=provider,
            api_key=api_key
        )
        
        if success:
            return jsonify({
                "success": True,
                "message": f"配置已保存: {provider}"
            })
        else:
            return jsonify({
                "success": False,
                "error": f"不支持的提供商: {provider}"
            }), 400
    
    except Exception as e:
        logger.error(f"LLM config error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/llm/clear/<provider>', methods=['POST'])
def llm_clear(provider):
    try:
        success, message = llm_manager.clear_builtin_config(provider)
        if success:
            return jsonify({"success": True, "message": message})
        return jsonify({"success": False, "error": message}), 400
    except Exception as e:
        logger.error(f"LLM clear error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/chart-detail/<chart_id>')
def chart_detail(chart_id):
    try:
        chart_dir = CHART_PROJECT / 'charts' / chart_id
        if not chart_dir.exists():
            return jsonify({"error": "Chart not found"}), 404
        
        # 读取 README
        readme_file = chart_dir / 'README.md'
        readme_content = ""
        if readme_file.exists():
            with open(readme_file, 'r', encoding='utf-8') as f:
                readme_content = f.read()
        
        # 始终返回图表目录下的 result.html（示例文件）
        result_html = ""
        result_file = chart_dir / 'result.html'
        if result_file.exists():
            with open(result_file, 'r', encoding='utf-8') as f:
                result_html = f.read()
            logger.info(f"Returning chart example: {result_file}")
        else:
            logger.warning(f"Example file not found: {result_file}")
        
        return jsonify({
            "success": True,
            "chart_id": chart_id,
            "readme": readme_content,
            "example_html": result_html
        })
    except Exception as e:
        logger.error(f"Chart detail error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _read_upload_df(filepath: str):
    if filepath.endswith('.xlsx') or filepath.endswith('.xls'):
        try:
            return pd.read_excel(filepath, engine='openpyxl')
        except Exception:
            try:
                return pd.read_excel(filepath, engine='xlrd')
            except Exception:
                return pd.read_excel(filepath)
    return pd.read_csv(filepath)


@app.route('/api/report', methods=['POST'])
def report():
    try:
        data = request.json or {}
        filepath = (data.get('filepath') or '').strip()
        query = data.get('query', '')
        provider = data.get('provider', '')
        report_type = data.get('report_type') or 'health'
        primary_key = data.get('primary_key') or []
        if report_type != 'health':
            return jsonify({"error": "当前只生成数据体检报告"}), 400
        if primary_key and not isinstance(primary_key, list):
            return jsonify({"error": "primary_key 必须是列名数组"}), 400

        if not filepath:
            return jsonify({"error": "filepath 不能为空"}), 400
        if not Path(filepath).exists():
            return jsonify({"error": f"文件不存在：{filepath}"}), 400

        df_raw = _read_upload_df(filepath)
        df_clean = None
        try:
            df_clean = DataCleaner().clean(df_raw.copy())
        except Exception as ce:
            logger.warning(f"DataCleaner failed (non-fatal): {ce}")

        _provider = None
        if provider:
            config = llm_manager.get_config(provider)
            if config:
                _provider = config.provider

        logger.info(f"Health report: filepath={filepath}, provider={_provider}")
        result = generate_health_report(
            df_raw, df_clean=df_clean, query=query, provider=_provider,
            primary_key=primary_key,
        )
        if result.get('success'):
            return jsonify(result)
        return jsonify({"error": result.get('error', '报告生成失败')}), 500
    except Exception as e:
        logger.error(f"Report error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    print("=" * 60)
    print("VizNDatPilot AI")
    print("=" * 60)
    print(f"Server: http://localhost:5017")
    print(f"Upload: {UPLOAD_FOLDER}")
    print(f"Output: {OUTPUT_FOLDER}")
    print("=" * 60)
    print(f"[APP] llm_config_manager module: {lcm.__file__}")
    print(f"[APP] llm config file: {lcm.LLM_CONFIG_FILE.resolve()}")
    app.run(host='0.0.0.0', port=5017, debug=True)
