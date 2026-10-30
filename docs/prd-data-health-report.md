# PRD：数据体检报告（Data Health Check Report）

| 项 | 内容 |
|---|---|
| 产品 | VizNDatPilot AI |
| 文档状态 | 草案 v0.4（补齐指标分母、评分适用性、隐私与复现验收） |
| 日期 | 2026-10-27 |
| 范围 | **仅**生成「数据体检报告」；业务分析报告 / 混合报告不在本次交付范围 |
| 关联代码 | `app.py` `/api/report`、`core/health_report.py`、`core/quality_ledger.py`、`core/fact_extractor.py`、`core/number_whitelist.py`、`core/rounding.py`、`core/data_profiler.py`、`utils/validator.py` |

---

## 1. 背景与目标

### 1.1 背景

当前报告模块已支持 `report_type=health`，章节名为「质量总览 / 字段质量评级 / 主要问题发现 / 异常与一致性 / 清洗建议」，并具备事实账本与多层后处理管道。但体检能力仍有以下差距：

- 完整性、准确性、一致性及 A/B/C/D 评级由模型自行叙述，程序未给出六维分数。
- 正文中的数字由模型书写，程序仅事后用账本比对（相对容差 5%），模型仍可能先造数再被标红。
- 请求链路先 `DataCleaner.clean()` 再体检，伪装空值、重复行等原始问题容易被洗掉，体检结果偏乐观。
- 事实摘要偏数值列（mean/std/IQR），对类别格式、值变体、类型解析失败等质量问题覆盖不足。
- 当前空值标记集合由 `DataCleaner` 硬编码，且把部分可能是合法值的标记视为空值；PRD 目标要求将 null-token 策略显式配置并记录，避免静默误判。

### 1.2 目标

交付一份**可复现、可审计**的单表技术质量体检报告，回答三件事：

1. 这张表的结构与统计质量如何（分数、等级、适用维度）。
2. 具体哪里不健康（字段、规则代码、影响行数、示例）。
3. 先清洗什么（优先级来自规则，话术可来自模型）。

**原则：程序算数，模型作文。报告中每一个数字都必须能回溯到质量事实账本（Quality Fact Ledger）。评分只表示本系统可检测的技术质量，不等于业务准确性，也不单独证明数据适合某项分析。**

### 1.3 成功标准（产品层）

- 同一文件、同一代码版本，连续两次体检：评分卡与问题清单数值完全一致。
- LLM 不可用时，仍能输出评分卡、问题清单、字段明细与能力边界（无叙事也可交付）。
- 终稿中不得出现账本中不存在的数字（见第 8 节）。

---

## 2. 范围与非目标

### 2.1 范围内（In Scope）

- 单张已上传的 Excel / CSV 表。
- 描述性数据质量检测：缺失、完全重复、已声明主键重复、类型/格式可解析、非有限数值、IQR 离群、类别基数、日期跨度/间隔、值变体、列名结构。
- 用户可确认或覆盖字段角色；用户可声明单列或复合主键及预期日期频率。
- 程序生成的健康评分卡、问题清单、字段明细表、能力边界、指标附录。
- 模型仅对**已计算问题**做解读与清洗建议话术。
- 清洗前后对照作为附录，不替代「原始表体检」。

### 2.2 范围外（Out of Scope）

- 业务分析报告、KPI、趋势洞察、预测、聚类、回归。
- 跨表 / 多 Sheet 关联、外键与参照完整性。
- 业务真伪核验（身份证是否真实、金额是否造假）。
- 自动执行清洗写回文件。
- 主数据治理、数据血缘、权限分级以外的企业 DQ 平台能力。
- 在没有业务规则或可信基准时，验证数据是否符合业务事实（例如“金额必须大于 0”或身份证号是否真实）。

本次产品默认：**报告入口只生成数据体检报告**。报告 UI 隐藏分析/混合类型选择；`/api/report` 的类型固定为 `health`，若兼容参数传入其他类型则返回明确错误，不静默切换。其他图表分析 API 不属于此报告入口。报告中的“可用性”只描述技术检测覆盖范围；特定业务用途的可用性必须结合用户声明的目标和规则判断。

---

## 3. 用户与场景

| 角色 | 场景 | 期望 |
|---|---|---|
| 分析师 | 拿到业务导出的脏表，决定能否做图/做分析 | 5 分钟内看到总分、P0 问题和不可分析字段 |
| 数据同学 | 评估接入前质量 | 要字段级缺失率、重复键、格式失败率，以及可复现口径 |
| 业务同学 | 看不懂统计量 | 要「能不能用」的结论和「先改哪几列」的清单 |

主路径：上传文件 → 选择「体检报告」→ 阅读评分卡与问题清单 → 按建议清洗 → 重新体检对比。

---

## 4. 报告结构（读者目录）

报告分为 **程序章** 与 **模型章**。程序章必须先于模型章渲染，并作为模型的唯一事实输入。

| 节号 | 标题 | 生成方 | 说明 |
|---|---|---|---|
| 0 | 能力边界与体检口径 | 程序 | 规模、字段角色/置信度、主键声明、时间频率、样本量警告、已检查与未检查项目、体检对象=原始表 |
| 1 | 健康评分卡 | 程序 | 综合分 0–100、等级 A–D、六维分数；及时性无数据则为 N/A |
| 2 | 问题清单 | 程序 | 按 P0 / P1 / P2；字段、规则代码、影响行数、明确分母、占比、原始行号/位置、脱敏示例、检测方法 |
| 3 | 字段质量明细 | 程序 | 每列一行：推断/确认角色、角色置信度、类型、缺失、唯一、异常、解析失败、非有限值、变体差、列评级 |
| 4 | 主要问题解读 | 模型 | 只解释第 2 节 Top 问题（建议 Top 8），禁止引入新数字 |
| 5 | 风险与可用性 | 模型 | 哪些分析可做/不可做；须引用评分卡与问题代码 |
| 6 | 清洗建议 | 模型 | 操作说明；影响行数与优先级必须引用问题清单槽位 |
| 7 | 附录 | 程序 | 指标定义、阈值、IQR 口径、清洗对照（可选） |

### 4.1 与现有五段 JSON 的映射（兼容前端）

现有 `health` schema 为 `overview / fields / insights / anomalies / suggestions`。过渡期映射：

| 现字段 | 对应新结构 |
|---|---|
| `overview` | 节 4 对评分卡的解读（节 1 本身不进 LLM） |
| `fields` | 节 4 对字段明细中差评列的说明（节 3 本身不进 LLM） |
| `insights` | 节 4 主要问题解读 |
| `anomalies` | 节 5 可用性（不再让模型「发现新异常」） |
| `suggestions` | 节 6 清洗建议 |

前端若可改：优先用 `stats` JSON 画评分卡和表格，Markdown 只承载节 0 与节 4–6。

---

## 5. 指标字典

所有指标由 Python（pandas / 现有 `DataValidator` + 扩展账本）计算，写入 **Quality Fact Ledger**，并进入 `_allowed_numbers`。

命名约定：`{scope}.{metric_id}`，例如 `global.n_rows_raw`、`col.col_0001.missing_rate`。列指标使用按原始位置生成的 `column_id`（如 `col_0001`）作为内部键；`col_name` 仅用于显示，不能作为唯一键，以支持重名列。

### 5.1 全局指标

| ID | 定义 | 备注 |
|---|---|---|
| `n_rows_raw` | 原始行数 | 体检主口径 |
| `n_cols` | 列数 | 原始列位置数；列名规范化后重名不合并 |
| `n_rows_clean` | 清洗后行数 | 仅对照附录 |
| `duplicate_row_count` | 完全重复行数（不含首现） | 以 raw 计 |
| `duplicate_row_rate` | `duplicate_row_count / n_rows_raw` | 比率存 4 位小数；展示百分数 2 位，见 5.10 |
| `empty_col_count` | 全空列数 | |
| `constant_col_count` | 非空唯一值 = 1 的列数 | |
| `overall_cell_missing_rate` | 空单元格 / (行×列) | 在质量视图计算；原生空值与本次配置确认的空值标记均计入 |
| `disguised_null_count` | 命中空值字符串的单元格数 | 在标 NA 之前计数；必须记录实际使用的 null-token 配置及各 token 命中数 |
| `issue_p0_count` / `issue_p1_count` / `issue_p2_count` | 各级问题条数 | |
| `overall_score` | 综合分 | 见第 6 节 |
| `overall_grade` | A/B/C/D | 见第 6 节 |
| `dim.{completeness,uniqueness,validity,consistency,timeliness,interpretability}` | 六维分数 | 及时性可为 `null`（N/A） |

### 5.2 完整性 Completeness（列级 + 全局）

| ID | 定义 |
|---|---|
| `missing_count` | 质量视图中原生空值与用户确认的 null token 总数；每列按位置计算 |
| `missing_rate` | `missing_count / n_rows_raw` |
| `full_empty_row_count` | 所有列皆空的行数 |
| `key_missing_combo_count` | 用户声明主键中至少一个组成字段缺失的行数；未声明主键为 `null`。identifier 候选字段不自动视作主键 |

阈值（与现 `DataValidator.THRESHOLDS` 对齐，写入附录，禁止模型改）：

- `missing_rate ≥ 0.40` → P0（ERROR）
- `0.05 ≤ missing_rate < 0.40` → P1（WARNING）

### 5.3 唯一性 Uniqueness

| ID | 适用 | 定义 |
|---|---|---|
| `duplicate_row_count` | 全局 | 全行重复 |
| `n_unique` | 列 | `nunique(dropna=True)` |
| `unique_ratio` | 列 | `n_unique / n_rows` |
| `duplicate_key_count` | 用户声明的主键列或复合主键 | 非空键中除首次出现外的重复行数；未声明主键时为 `null` |

- 全行重复率 ≥ 10% → P0；> 0 → P1。
- **identifier 候选字段不等同主键**。只有用户声明或可信 schema 声明为主键后，重复键才按主键规则评级；未声明时重复值仅作描述性指标，不判为主键错误。
- 支持单列主键和复合主键；复合键仅在主键组成字段均非空的记录上计算唯一性，并单独报告主键字段缺失。

### 5.4 有效性 Validity

| ID | 适用 | 定义 |
|---|---|---|
| `parse_fail_count` / `parse_fail_rate` | 被判定为 numeric/datetime 或用户指定解析格式的字段 | 同时记录失败数和失败率；分母为按相同解析规则尝试的非空输入行 |
| `non_finite_count` | numeric | `+inf` / `-inf` 的数量；从描述统计和离群值分母中排除并单独报告 |
| `min` / `max` / `mean` / `median` / `std` / `q25` / `q75` | numeric 且非 identifier | 见 5.10.5：`dropna` 后计算，`std(ddof=1)`，分位 `linear`，四位 HALF_UP；叙事默认不强调 mean |
| `trimmed_mean` | numeric 且非 identifier | 剔除按 IQR×1.5 规则标记的离群值后计算的均值；与原始有效值均值并列展示，排除数量也须入账；离群检测跳过或剩余样本不足时为 `null` |
`trimmed_mean` 在本 PRD 中是兼容字段名，严格含义为 `mean_after_iqr_exclusion`（IQR 标记值剔除后的均值），不是固定比例截尾均值。报告同时显示原始有效值 `mean`、剔除后均值和被剔除计数，避免把两种统计口径混为一谈。
| `outlier_count` / `outlier_rate` | numeric | 见 5.10.6：IQR×1.5；分母为 `n_valid`；`n_valid<4` 或 `IQR=0` 则 `outlier_skipped=true`，count 为 null |
| `outlier_low` / `outlier_high` | numeric | IQR 边界；未舍入值用于检测，舍入值仅供展示 |
| `outlier_examples` | numeric | 最多 5 个 |
| `n_unique` / `top_values` | category | Top 5 及频次 |
| `avg_text_len` | category | 平均字符串长度；> 50 记 P2 |
| `dt_min` / `dt_max` / `span_days` | datetime | 描述性范围；跨度本身不作为质量问题，需结合用户声明规则判断 |

离群阈值：`outlier_rate ≥ 0.20` → P0；`≥ 0.01` → P1。

**口径声明（必须印在节 0 / 节 7）**：IQR 离群是统计现象，**不等于录入错误**。模型不得写成「错误值 / 脏数据已确认」。

### 5.5 一致性 Consistency（单表）

| ID | 定义 |
|---|---|
| `n_unique_raw` | 排除原生空值及本次配置 null token 后，保留原始非空字符串的唯一数；不 strip、不做大小写或 Unicode 归一化 |
| `n_unique_normalized` | 经明确声明的规范化副本（默认 strip + casefold；全角转换须单独启用）后的唯一值 |
| `variant_gap` | `n_unique_raw - n_unique_normalized`；> 0 表示同一语义多种写法 |
| `leading_trailing_space_count` | 含首尾空白的单元格数 |

不做跨表外键。数值列若 `max/min ≥ 1e6` 且 `min>0`，记 P2「量级跨度极大，核对本位」。

### 5.6 及时性 Timeliness

仅当用户确认主日期列、数据更新频率和预期最大滞后（freshness SLA），且解析有效日期达到最低门槛时计算及时性分数或超期 issue。历史日期本身不表示数据过期。日期间隔缺口在 MVP 仅按用户声明的预期频率计算；未确认时只报告观测范围，不将未出现日期直接判为缺失。自动推断频率属于后续能力，需单独验证置信门槛。

| ID | 定义 |
|---|---|
| `as_of_date` | 本次报告上下文固定的统计基准日期（默认生成日，保存到报告元数据） |
| `data_max_date` | 日期列最大值 |
| `lag_days` | `(as_of_date.normalize() - data_max_date.normalize()).days`，见 5.10.9 |
| `expected_gap_period_count` | 用户声明预期频率下，预期周期全集 − 观测周期集合；未确认时为 `null`（MVP 不自动推断） |
| `expected_max_lag_days` | 用户声明的数据新鲜度 SLA；未声明时为 `null`，不计算及时性分数或超期 issue |

无日期列：**及时性 = N/A**，**不得记 100 分**，且不进入综合分加权。

未声明 `expected_max_lag_days` 时，及时性为 N/A，`lag_days` 只作描述，不评级。声明 SLA 后：`lag_days > expected_max_lag_days` → P1；`lag_days ≥ 2 × expected_max_lag_days` → P0。日期间隔缺口必须按用户声明的 cadence 判断，不能仅由稀疏日期序列判缺失。

### 5.7 可解释性 / 结构健康 Interpretability

| ID | 定义 |
|---|---|
| `unnamed_col_count` | 列名匹配 `Unnamed` 或空 |
| `duplicate_col_name_count` | 重名列 |
| `role_counts` | identifier_candidate / primary_key / numeric / category / datetime / unknown 各多少列 |
| `role_confidence` / `role_reason` | 每列角色推断置信度及原因；低置信度字段提示用户确认 |
| `numeric_semantic_fields` / `category_semantic_fields` | 按字段名语义辅助类型判断，见下方判定优先级 |
| `high_cardinality_cols` | 类别列 `n_unique≥20` 且 `avg_per_category<5` |
| `avg_per_category` | 类别非空记录数 / 唯一类别数；用于概览，不足以证明每个类别样本都稀疏 |
| `category_count_min/median/p10` | 高基数类别的组内记录数分布；没有类别组时为 `null`，用于避免仅凭均值推断每组都稀疏 |
高基数字段的组内计数基于 null-mask 后的类别值计算：`counts = value_counts(dropna=True)`；`category_count_min/median/p10` 分别为 counts 的最小值、中位数和 10 分位数。所有结果为整数计数；空字段为 `null`。这些是描述性指标，不自动触发组间显著性或业务策略结论。

高基数列只提示分组样本稀疏风险；Top-N 是可选浏览方式，不构成可靠组间比较或业务策略依据。边界文案：「该字段基数较高，组内样本可能稀疏；报告不据此作组间推断」。

**角色判定优先级**：先由用户确认/覆盖角色；否则先识别明确 ID 语义（如 `order_id`、`uuid`、编号/序号），不能仅凭高 `unique_ratio` 认定主键。字段名语义优先于基数：`spend/amount/price/cost/revenue/sales` 等优先 numeric，`city/region/category/gender/status` 等优先 category。原生 numeric dtype 且无明确 ID 语义时为 numeric；`unique_ratio` 只作辅助，不能单独把 numeric 判成 category，也不能单独把 category 判成 identifier。金额类字符串先按声明的货币符号/千分位解析策略生成数值视图，再分类；解析失败进入 `parse_fail_count`，不得把无法解析的文本静默转成 0。

### 5.8 字段明细表（节 3 一行）

`column_id | col_name | inferred_role | confirmed_role | role_confidence | dtype | missing_count | missing_rate | n_unique | unique_ratio | duplicate_key_count | min | max | mean | median | std | q25 | q75 | outlier_count | outlier_rate | trimmed_mean | parse_fail_count | parse_fail_rate | non_finite_count | variant_gap | avg_per_category | high_cardinality | field_grade`

`field_grade` 由程序按第 6.2 节计算。

### 5.9 现有模块已覆盖 vs 需扩展

| 已有 | 来源 | 需扩展 |
|---|---|---|
| 行/列、重复 | `core/quality_ledger.py` | 按原始列位置稳定计算；raw duplicate 与配置 token 后的缺失统计分开 |
| 缺失率、IQR、数值统计 | `core/quality_ledger.py` + `DataValidator` | 统一空值口径；增加解析失败数/率与非有限值 |
| 角色 | `core/fact_extractor.py` | 支持角色确认；identifier 不计算数值分布 |
| 高基数、时间字段 | `core/health_report.py` | 六维适用性、格式失败、variant_gap、列评级及用户 cadence/SLA 限制 |
| issue_list | `core/quality_ledger.py` | 统一 P0/P1/P2、分母及影响行定义 |

### 5.10 计算口径与 pandas 实现（规范性，实施必须遵守）

本节是指标计算的唯一规范。代码、账本、附录必须与此一致。pandas 以 **2.x** 行为为准，禁止任何静默抽样；任何抽样若未来获批，必须独立标识样本口径且不得冒充全表分数。缺失值的纳入/排除按每项指标的明示分母处理。

#### 5.10.1 通用约定

| 项 | 规范 |
|---|---|
| 体检表 | `df_raw`：上传后、`DataCleaner.clean()` 前的副本。原始列位置和值均保留；列名仅生成规范化显示名，重名仍保留 |
| 质量视图 `df_q` | 逐列位置构造的分析视图，仅把配置确认的 null token 标记为缺失；不修改非空字符串、不写回用户文件 |
| 列身份 | 每列生成稳定的 `column_id`（本次输入内按位置编号）；任何检测不得用可能重名的列名做 DataFrame 唯一索引 |
| Null token 配置 | 默认只识别空字符串及明确配置的标记；`unknown`、`-`、`暂无` 等可能是有效业务值，必须由用户/配置确认后才纳入；报告记录 token 清单与命中数 |
| 原始值 | 除 null-token 匹配外，不对原始数据执行 strip、大小写折叠、货币符号删除等规范化；各类解析使用独立派生视图 |
| 空表 | `len(df_raw)==0` 或 `df_raw.shape[1]==0` → 不计算分数，接口返回失败「数据为空，无法生成报告」 |
| 除零 | 分母为 0 时该指标记 `null`，不记 `0`，不写入 `_allowed_numbers` |
| 非有限值 | `inf` / `-inf` / `NaN` 统计量记 `null`，不入账 |
| 比率存储 | 阈值与评分使用未舍入原始比率；账本同时保存原始精度值及展示值。展示百分数两位 HALF_UP，禁止用展示值反向判定阈值 |
| 百分数展示 | `rate × 100`，**两位小数 HALF_UP**（例：`0.1234` → `12.34`）。账本同时收纳比率与百分数 |
| 计数 | Python `int`，零容差匹配 |
| 描述统计 | 四位小数 HALF_UP；若四舍五入后与整数差 `< 1e-12`，账本同时收纳 `int` 形式 |
| 分位数插值 | `Series.quantile(q, interpolation='linear')`；不得使用 `method='linear'` 参数 |
| 标准差 | 样本标准差 `std(ddof=1)` |
| 均值/分位 | 在有效数值派生列上先排除缺失和非有限值，再计算；记录 `n_valid` 与排除数量 |

比率入账示例：

```python
from decimal import Decimal, ROUND_HALF_UP
import math

def round_half_up(value, ndigits: int):
    if value is None:
        return None
    x = float(value)
    if not math.isfinite(x):
        return None
    q = Decimal("1").scaleb(-ndigits)  # ndigits=4 → 0.0001
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))

def store_rate(rate):
    raw = float(rate) if rate is not None and math.isfinite(float(rate)) else None
    r4 = round_half_up(raw, 4)
    pct2 = round_half_up(raw * 100.0, 2) if raw is not None else None
    return raw, r4, pct2  # 比较用 raw；展示/白名单分别登记规范显示值
```

禁止使用 `float.round` / pandas `Series.round` 作为账本入账方式（银行家舍入会导致 `0.125` 变成 `0.12`）。

#### 5.10.2 质量视图 `df_q` 构造（先于一切列统计）

顺序固定，不得调换：

```python
NULL_STRINGS = report_config.null_tokens  # 本次请求/部署配置；写入报告元数据

# 按列位置构造质量视图，原始值保留在 df_raw，避免重名列覆盖
df_q = pd.DataFrame(index=range(len(df_raw)))
disguised_null_count = 0
disguised_null_by_token = Counter()
for position in range(df_raw.shape[1]):
    column_id = f"col_{position + 1:04d}"
    s = df_raw.iloc[:, position]
    if pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s):
        token = s.map(lambda x: x.strip() if isinstance(x, str) else None)
        disguised = token.isin(NULL_STRINGS)
        for value, count in token[disguised].value_counts().items():
            disguised_null_by_token[value] += int(count)
        disguised_null_count += int(disguised.sum())
        df_q[column_id] = s.mask(disguised, pd.NA)
    else:
        df_q[column_id] = s

# 注意：此处不 drop_duplicates、不 fillna、不 dropna(how='all')
n_rows = int(len(df_q))          # == n_rows_raw
n_cols = int(df_q.shape[1])
```

`disguised_null_count` 只统计「剥空白后精确命中本次配置 token」的单元格；pandas 自带的 `NaN`/`NaT`/`None` **不计入** disguised，只计入后续 `missing_count`。剥空白仅用于 token 匹配，非空原始字符串保持原样。`NULL_STRINGS` 默认只含空字符串；部署或用户添加 token 时必须在报告元数据记录配置版本。

#### 5.10.3 全局规模与结构

```python
n_rows_raw = int(len(df_raw))
n_cols = int(df_q.shape[1])
n_rows_clean = int(len(df_clean)) if df_clean is not None else None

# 完全重复行：在按列位置构造的原始值矩阵上比较；原生缺失视为相同缺失，
# 字符串 null-token 仅按原值比较，不 strip/casefold。`df_raw` 不依赖列标签对齐。
duplicate_row_count = duplicate_rows_by_position(df_raw, keep="first", null_tokens=None)
duplicate_row_rate = (
    duplicate_row_count / n_rows_raw if n_rows_raw > 0 else None
)

empty_col_count = count_all_missing_columns_by_position(df_q)
full_empty_row_count = count_all_missing_rows_by_position(df_q)

# 非空唯一值恰好 1；全空列 nunique=0，不算 constant
constant_col_count = count_nonempty_single_value_columns_by_position(df_q)

total_cells = n_rows_raw * n_cols
missing_cells = int(df_q.isna().sum().sum())
overall_cell_missing_rate = (
    missing_cells / total_cells if total_cells > 0 else None
)
disguised_ratio = (
    disguised_null_count / total_cells if total_cells > 0 else None
)
```

列名结构：

```python
display_names = [normalize_display_name(c) for c in df_raw.columns]
unnamed_col_count = sum(
    1 for c in display_names
    if str(c).strip() == "" or str(c).lower().startswith("unnamed")
)
# 重名组数：同一名字出现次数 > 1 的名字个数（不是多余列数）
from collections import Counter
cnt = Counter(display_names)
duplicate_col_name_count = sum(1 for _, v in cnt.items() if v > 1)
```

#### 5.10.4 列级完整性 / 唯一性

对每一列按位置使用 `s = df_q[column_id]`：

```python
missing_count = int(s.isna().sum())
missing_rate = missing_count / n_rows_raw if n_rows_raw > 0 else None

n_unique = int(s.nunique(dropna=True))
unique_ratio = n_unique / n_rows_raw if n_rows_raw > 0 else None
n_non_null = n_rows_raw - missing_count
avg_per_category = (
    round_half_up(n_non_null / n_unique, 2)
    if role == "category" and n_unique > 0 else None
)

# 主键重复只按用户声明的单列/复合主键计算；identifier 候选列的重复值
# 仅属于普通唯一性描述，不作为主键重复。
duplicate_key_count = (
    count_duplicate_declared_key_rows(df_q, declared_key_column_ids)
    if declared_key_column_ids else None
)
```

`key_missing_combo_count`：只对用户声明主键计算，统计至少一个主键组成字段缺失的行；未声明主键时为 `null`。不从 identifier 候选列自动推断业务主键。

#### 5.10.5 数值解析与描述统计（仅 `role==numeric`）

identifier 即使 dtype 为数字：**不算** min/max/mean/std/IQR。

```python
# 字符串数值使用同一个 locale-aware parser 生成派生列：识别并剥离配置允许的
# 货币符号/千分位分隔符；百分号仅在用户声明单位规则后转换。
# parse_fail_count 的分母是非空、非 null-token 原始输入值。
s_num, parse_fail_count, parse_fail_denominator = parse_numeric_by_policy(s, numeric_policy)
non_finite_count = int((s_num.notna() & ~np.isfinite(s_num)).sum())
x = s_num[np.isfinite(s_num)].dropna()
n_valid = int(len(x))

if n_valid == 0:
    min_ = max_ = mean_ = median_ = std_ = q25 = q75 = None
else:
    min_    = round_half_up(float(x.min()), 4)
    max_    = round_half_up(float(x.max()), 4)
    mean_   = round_half_up(float(x.mean()), 4)           # skipna 默认 True
    median_ = round_half_up(float(x.median()), 4)
    std_    = round_half_up(float(x.std(ddof=1)), 4) if n_valid >= 2 else None
    q25     = round_half_up(float(x.quantile(0.25, interpolation="linear")), 4)
    q75     = round_half_up(float(x.quantile(0.75, interpolation="linear")), 4)
```

`parse_fail_rate = parse_fail_count / parse_fail_denominator`；分母为 0 时为 `null`。`inf/-inf` 不进入 `n_valid`、描述统计或离群率分母。`n_valid==1` 时 `std` 为 `null`（样本方差未定义），不要写成 `0`。所有阈值使用未舍入统计量与比率。

#### 5.10.6 IQR 离群（仅 numeric，非 identifier）

与现 `DataValidator._check_outliers` 对齐，并补全跳过条件：

```python
IQR_K = 1.5
outlier_skipped = False
outlier_count = 0
outlier_low = outlier_high = None  # rounded display values
outlier_low_raw = outlier_high_raw = None  # comparison values
outlier_examples = []
outlier_rate = None

if n_valid < 4:
    outlier_skipped = True
else:
    q1 = float(x.quantile(0.25, interpolation="linear"))
    q3 = float(x.quantile(0.75, interpolation="linear"))
    iqr = q3 - q1
    if (not math.isfinite(iqr)) or iqr == 0:
        outlier_skipped = True
    else:
        outlier_low_raw = q1 - IQR_K * iqr
        outlier_high_raw = q3 + IQR_K * iqr
        outlier_low = round_half_up(outlier_low_raw, 4)
        outlier_high = round_half_up(outlier_high_raw, 4)
        mask = (x < outlier_low_raw) | (x > outlier_high_raw)  # 比较不得使用舍入边界
        outlier_count = int(mask.sum())
        outlier_rate  = outlier_count / n_valid         # 分母 = 非空有效值，不是 n_rows
        outlier_examples = [
            round_half_up(float(v), 4) for v in x[mask].head(5).tolist()
        ]
        outlier_mask = mask

# 使用同一 IQR mask 计算异常值前后均值；不重新估计边界。
trimmed_mean = (round_half_up(float(x[~outlier_mask].mean()), 4)
                if not outlier_skipped and int((~outlier_mask).sum()) >= 3 else None)
```

**分母锁定**：`outlier_rate = outlier_count / n_valid`。缺失行不进入分子分母。`outlier_skipped==True` 时 `outlier_count` 展示为 `null`（不是 0），避免「0 个异常 = 数据完美」。

#### 5.10.7 解析失败率

先按字段角色/用户解析规则决定是否检测。`role∈{identifier, category}` 且用户未明确要求解析为 numeric/datetime → `parse_fail_rate = null`（不适用）；列名中的日期语义只提示确认，不自动改变角色或开启解析。

日期语义词只用于提示用户确认，不足以单独开启解析或及时性评分；可配置的提示词包括 `date,time,日期,时间,datetime,year,month`。

```python
def parse_fail_metrics(s, role, parse_policy) -> tuple[int | None, int | None, float | None]:
    if role not in {"numeric", "datetime"} and parse_policy is None:
        return None, None, None
    non_null = values_after_null_masking(s)
    denominator = int(len(non_null))
    if denominator == 0:
        return 0, 0, None
    parsed = parse_with_declared_policy(non_null, role, parse_policy)
    failed = int(parsed.isna().sum())
    return failed, denominator, failed / denominator
```

解析失败数/率只统计确实尝试解析的非空值。原生数值或日期 dtype 在解析规则兼容时失败数为 0；没有可解析值时，计数为 0、分母为 0、比率为 `null`。日期和数值共用统一的 `parse_fail_count/denominator/rate` 口径，不另建重复别名指标。

及时性启用条件：用户确认主日期列、数据 cadence、`expected_max_lag_days`，且至少有 3 个不同有效日期。仅有日期列名提示不能启用及时性评分。

#### 5.10.8 类别文本与一致性

```python
# 仅 object/string 且 role∈{category, identifier}；s_raw 是 df_raw 同位置原始列
non_null = raw_values_excluding_native_na_and_configured_tokens(s_raw, NULL_STRINGS).astype(str)
avg_text_len = round_half_up(float(non_null.str.len().mean()), 2) if len(non_null) else None

n_unique_raw = int(non_null.nunique(dropna=True))

def _norm(v):
    if not isinstance(v, str):
        return v
    # 默认规范化仅 strip + casefold；全角转半角须由设置显式启用
    import unicodedata
    return v.strip().casefold()

n_unique_normalized = int(non_null.map(_norm).nunique())
variant_gap = max(0, n_unique_raw - n_unique_normalized)

leading_trailing_space_count = int(
    non_null.map(lambda v: isinstance(v, str) and v != v.strip()).sum()
)

top_values = [
    {"value": redact_value(idx), "count": int(cnt)}
    for idx, cnt in non_null.value_counts().head(5).items()
]
```

量级跨度（仅 numeric 且 `min_>0` 且 `max_`/`min_` 均可有限）：

```python
magnitude_span_flag = bool(min_ not in (None, 0) and max_ / min_ >= 1e6)
```

#### 5.10.9 时间字段

主日期列：必须由用户确认；未确认时仅逐列报告可计算的范围，不生成单一 `lag_days`、及时性分数或日期缺口结论。日期分析使用已按报告时区解析的字段：

```python
vals = dt.dropna()
if len(vals) == 0:
    dt_min = dt_max = span_days = lag_days = expected_gap_period_count = None
else:
    dt_min = vals.min()
    dt_max = vals.max()
    span_days = int((dt_max.normalize() - dt_min.normalize()).days)

    as_of_date = report_context.as_of_date.normalize()  # 固定的 UTC 报告上下文日期
    data_max_date = convert_to_report_timezone(dt_max, report_context.timezone).normalize()
    lag_days = int((as_of_date - data_max_date).days)    # 可为负（未来日期）

    # 仅在用户确认频率时计算缺口；月频使用完整首末日历月，
    # 不能由跨度 90 天阈值任意切换日/月频率。
    if report_context.user_confirmed_frequency:
        expected = build_expected_periods(dt_min, dt_max, report_context.confirmed_frequency,
                                          report_context.timezone)
        observed = normalize_observed_periods(vals, report_context.confirmed_frequency,
                                              report_context.timezone)
        expected_gap_period_count = int(len(expected.difference(observed)))
    else:
        expected_gap_period_count = None
```

`lag_days < 0`：及时性分数按 `lag_days=0` 计（不扣分），另记 P2「存在未来日期」。

#### 5.10.10 高基数

```python
high_cardinality = bool(
    role == "category"
    and n_unique >= 20
    and avg_per_category is not None
    and avg_per_category < 5
)
```

#### 5.10.11 问题条数

先按第 6.1 节规则为每条 issue 赋 P0/P1/P2，再：

```python
issue_p0_count = sum(1 for i in issues if i.level == "P0")
issue_p1_count = sum(1 for i in issues if i.level == "P1")
issue_p2_count = sum(1 for i in issues if i.level == "P2")
```

同一列同一规则只产生 **1 条** issue（不按脏行拆条）。`affected_rows` 字段单独记录影响行数（如 `missing_count`）。

#### 5.10.12 `_allowed_numbers` 必收清单

每个非 `null` 的计数、四位比率、两位百分数、四位统计量、分数（整数）、等级不收（非数字）。衍生值若要出现在报告中，必须先在此入账，**禁止模型临场加减乘除**。至少包括：

- 全局：`n_rows_raw, n_cols, n_rows_clean, duplicate_row_count, duplicate_row_rate, duplicate_row_rate_pct, empty_col_count, constant_col_count, missing_cells, overall_cell_missing_rate(+pct), disguised_null_count, disguised_ratio(+pct), full_empty_row_count, unnamed_col_count, duplicate_col_name_count, issue_p0/p1/p2_count, overall_score, 各维分数`
- 列：`missing_count/rate(+pct), n_unique, unique_ratio(+pct), duplicate_key_count, n_non_null, min/max/mean/median/std/q25/q75/trimmed_mean, outlier_count/rate(+pct)/low/high, parse_fail_count/denominator/rate(+pct), non_finite_count, variant_gap, avg_text_len, avg_per_category, category_count_min/median/p10, high_cardinality, span_days, lag_days, expected_gap_period_count, expected_max_lag_days`

---

## 6. 评级规则（全部程序计算）

模型不得修改分数、等级、问题级别。

### 6.1 问题级别

| 级别 | 含义 | 示例 |
|---|---|---|
| P0 | 严重阻碍使用 | 任一字段缺失率 ≥ 40%；已声明主键存在重复；离群率 ≥ 20%；全行重复率 ≥ 10%；关键字段解析失败率 ≥ 20%；有效记录少于 3 行；滞后达到用户声明 SLA 两倍 |
| P1 | 影响可信度 | 字段缺失率 ≥ 5%；离群率 ≥ 1%；已尝试字段解析失败率 ≥ 5%；存在非有限数值；全空行/空列；重名列导致字段歧义；已确认日期频率存在缺口；滞后超过用户声明 SLA |
| P2 | 体验/规范 | 长文本、值变体、量级跨度、Unnamed 列名、高基数或常量字段提示、未来日期 |

**事件粒度与分母**：每个 `issue` 必须包含 `issue_code`、`column_id`（全局问题可为空）、`affected_rows`、`affected_rate`、`denominator_metric`、`severity`、检测方法及脱敏示例。缺失率分母为原始总行数；解析失败率分母为尝试解析的非空值数；离群率分母为有限且解析成功的数值数；全行重复率分母为原始总行数；主键重复率分母为主键组成字段均非空的行数。缺失/无效分母为 0 时，比率为 `null`，不得产生基于该比例的 issue。

补充阈值：解析失败率 ≥ 20% 为 P0、≥ 5% 为 P1；任意非有限值为 P1（同时报告计数和有效值分母）；全空列与全空行为 P1；重名列为 P1；常量字段和高基数类别字段为 P2。主键组成字段缺失单独报告，不把缺失行误算为主键重复。

### 6.2 字段等级

按**最差命中**取：

- D：该列存在任一条 P0
- C：无 P0，存在 P1
- B：无 P0/P1，存在 P2
- A：无问题

### 6.3 六维分数（0–100）

每维 `score = clip(100 - penalty, 0, 100)`。扣分项**按下列顺序相加**（同一列缺失率只命中最高档，不重复扣 20 又扣 8）。阈值判断用原始精度值，展示舍入不参与评级。阈值变更必须改代码与附录，不得靠 prompt。得分用浮点计算后 `round_half_up(..., 0)` 入账。每个维度须返回 `applicable`、`score` 或 `null`、`coverage` 和 `skip_reason`；无可检测对象/无有效分母时记 `null / N/A`，不能按 100 分处理。

```python
def clip100(penalty: float) -> int:
    raw = max(0.0, min(100.0, 100.0 - penalty))
    return int(round_half_up(raw, 0))
```

**完整性 `dim.completeness`**（有至少一行一列时适用）

```python
p = 0.0
scored_cols = [c for c in column_ids if missing_rate[c] is not None]
n_scored_cols = len(scored_cols)
# 先计算列级扣分率，再乘 20 分预算，避免列数多时扣分线性爆炸
column_penalty_rate = (
    sum(1.0 if missing_rate[c] >= 0.40 else
        0.4 if missing_rate[c] >= 0.05 else 0.0 for c in scored_cols)
    / n_scored_cols if n_scored_cols else 0.0
)
p += 20.0 * column_penalty_rate
# 全局单元格缺失：每 1 个百分点扣 1，最多 30
if overall_cell_missing_rate is not None:
    p += min(30.0, overall_cell_missing_rate * 100.0)
# null token 命中已计入缺失率，不重复扣分；单列其数量用于可审计与优先定位。
dim_completeness = clip100(p)
```

**唯一性 `dim.uniqueness`**

```python
p = 0.0
if duplicate_row_rate is not None:
    p += duplicate_row_rate * 100.0 * 2.0   # 每 1% 重复 −2
if declared_primary_key_column_ids and declared_primary_key_duplicate_count > 0:
    p += 40
dim_uniqueness = clip100(p)
```

唯一性维度在至少有行时适用；未声明主键时只按全行重复情况评分，并在 coverage 中注明「未评估业务主键唯一性」。

**有效性 `dim.validity`**

```python
p = 0.0
eligible_validity_cols = [
    c for c in column_ids
    if validity_metrics_applicable[c] and validity_denominator.get(c, 0) > 0
]
if not eligible_validity_cols:
    dim_validity = None  # N/A：无可检验的数值/日期字段
else:
    per_col = []
    for col in eligible_validity_cols:
        cp = 0.0
        pfr = parse_fail_rate.get(col)
        if pfr is not None and pfr >= 0.20:
            cp += 20
        elif pfr is not None and pfr >= 0.05:
            cp += 8
        if non_finite_count.get(col, 0) > 0:
            cp += 10
        if not outlier_skipped.get(col):
            obr = outlier_rate.get(col)
            if obr is not None and obr >= 0.20:
                cp += 15
            elif obr is not None and obr >= 0.01:
                cp += 5
        per_col.append(min(cp, 35.0))
    p = sum(per_col) / len(per_col)
    dim_validity = clip100(p)
```

**一致性 `dim.consistency`**

```python
p = 0.0
eligible_consistency_cols = [c for c in column_ids if consistency_metrics_applicable[c]]
if not eligible_consistency_cols:
    dim_consistency = None
else:
    per_col = []
    for col in eligible_consistency_cols:
        cp = 0.0
        vg = variant_gap.get(col)
        if vg is not None and vg > 0:
            cp += min(10, vg)
        if leading_trailing_space_count.get(col, 0) > 0:
            cp += 5
        if magnitude_span_flag.get(col):
            cp += 5
        per_col.append(min(cp, 20.0))
    p = sum(per_col) / len(per_col)
    dim_consistency = clip100(p)
```

**及时性 `dim.timeliness`**

不满足 5.10.7 启用条件 → `dim_timeliness = None`（N/A），**禁止写 100**。

```python
sla = expected_max_lag_days
lag_eff = max(0, lag_days)  # 未来日期按 0 滞后天
p = 0 if lag_eff <= sla else min(100, 100 * (lag_eff - sla) / max(sla, 1))
dim_timeliness = clip100(p)
```

在 SLA 内得 100 分；超过 SLA 后按超出比例扣分，达到 SLA 两倍时为 0 分。无 SLA 不评分。该公式是产品启发式规则，必须与版本化阈值同时展示。

**可解释性 `dim.interpretability`**

```python
p = 0.0
p += min(20, 10.0 * unnamed_col_count / max(n_cols, 1))
p += 15.0 if duplicate_col_name_count > 0 else 0.0
hc_n = sum(1 for c in column_ids if high_cardinality.get(c))
p += min(20, 20.0 * hc_n / max(n_cols, 1))
dim_interpretability = clip100(p)
```

可解释性维度始终适用；完整性/唯一性在输入非空时适用；有效性和一致性按上面的 eligible 列集合决定；及时性只有用户确认主日期列、cadence、SLA 且达到日期数据门槛时适用。所有未适用维度从总分分母中排除，并在报告标为 N/A。

### 6.4 综合分与总评
**评分解释**：该评分是本产品按本节规则定义的启发式技术质量指数，不是行业统一标准、概率或业务正确性证明。发布前须用固定合成数据集和代表性真实样表校准权重，并记录评分规则版本；权重调整需版本升级，历史报告不得静默重算。

```python
dims = [score for score in [dim_completeness, dim_uniqueness, dim_validity,
        dim_consistency, dim_timeliness, dim_interpretability] if score is not None]
if dims:
    overall_score = int(round_half_up(sum(dims) / len(dims), 0))
else:
    overall_score = None

if issue_p0_count > 0 and overall_score is not None:
    overall_score = min(overall_score, 49)

if overall_score is None:
    overall_grade = None
elif overall_score >= 85:
    overall_grade = "A"
elif overall_score >= 70:
    overall_grade = "B"
elif overall_score >= 50:
    overall_grade = "C"
else:
    overall_grade = "D"
```

- N/A 维度不参与算术平均；综合分同时展示实际参与维度数。
- 六维中只有适用维度参与平均；若没有任何适用维度，整体分为 `null`，报告提示无法评分。
- **任一 P0：综合分封顶 49**（即使平均 90 也是 D）。
- `overall_score` 为 0–100 的整数，必须入账。

样本量：`n_rows_raw < 30` 时，节 5 必须使用固定句「样本不足，不做可用性推断」，由程序在能力边界写出，模型不得改口。分数与问题清单**仍按上式计算并展示**。

---

## 7. 技术边界

### 7.1 系统承诺可做

- 单表、默认最多 500,000 行的全表描述统计；MVP 超出上限直接拒绝并提示拆分，不静默抽样。
- 基于列角色的合法指标集合（identifier 只出唯一/重复/缺失）。
- 规则化评分、问题代码、影响行数、示例值。
- 清洗对照：重复减少量、伪装空值转 NA 数量、类型转换成功数。
- 模型叙事限制在账本已有事实上。

**大表策略（写入边界）**：MVP 的 `n_rows_max=500_000`。超过上限时不启动体检、不返回抽样分数，明确提示用户拆分或另行使用大表处理版本。上限内所有质量指标均全表计算；示例仅取最多 5 个且需脱敏。

### 7.2 系统明确不做

- 不知道真实世界对错，不声称「数据造假」或「已修复业务错误」。
- 不推断缺失机制（MCAR/MAR），不补全业务含义。
- 不做因果、预测、聚类、回归；体检场景**关闭**「建议用 K-means/回归」类方法门槛话术，改为清洗与字段修复。
- 不把 IQR 离群等同于错误。
- 单表无法验证参照完整性、多源对账。
- 不在 prompt 中发送全量明细行。

### 7.3 体检对象

- **主体检对象 = 原始表 `df_raw`**（上传后、`DataCleaner` 前）。
- `df_clean` 仅用于节 7「清洗对照」，避免「先洗再检」造成质量虚高。
- 若清洗失败：对照附录省略，主体检仍对 raw 进行。

### 7.4 模型能力边界（须印在节 0）

节 0 由程序生成，禁止 LLM 重写。至少包括：

- 数据规模、角色列表、identifier 禁分析项。
- 有无时间字段；无则「无法评估及时性」。
- 高基数列限制。
- `n < 30` / `n < 100` 的统计可靠性警告。
- 「所有数字以本节及评分卡为准；后文叙事不得新增数字。」

### 7.5 安全与隐私

- Prompt 仅含：账本摘要、问题清单、每列最多 5 个已脱敏示例（可截断长文本）；样例功能默认关闭或仅发送经隐私规则批准的类别代表值。
- 不把整表 `to_string()` 作为体检主输入（现 `head(5)` 仅作过渡，v1 删除，防止模型数格子造数）。

**隐私默认值**：禁止将原始行、自由文本、疑似个人信息、账号/证件/联系方式等值发给 LLM。Issue 示例默认只输出类型、格式类别或不可逆掩码；类别值样例须通过敏感字段检测并满足最小频数门槛，否则不发送。用户需显式启用样例共享才可放宽，且需在 UI 说明发送范围。下载报告也执行相同脱敏规则。

---

## 8. 减少幻觉与禁止模型造数

本章是本 PRD 的硬约束，实施时不得弱化为「请模型自觉」。

### 8.1 职责拆分

| 产物 | 唯一生成方 |
|---|---|
| 一切数值、分数、等级、影响行数、占比、示例 | Python 账本 |
| 节 0–3、节 7 的表格与列表 | 模板渲染，**不经过 LLM** |
| 问题原因的定性解释、清洗步骤的自然语言 | LLM |
| 建议中的数字 | 槽位替换自问题清单，LLM 不得手写 |

**字段角色硬规则**：identifier 只允许缺失率、唯一值数、重复检查和格式检查；禁止均值、分布、离群值分析。numeric 字段（包括按语义确认为金额的 `spend/amount/price/cost`）必须从程序账本读取均值、中位数、最小/最大值、分位数、IQR 异常值数与剔除异常值后均值；金额字段禁止只输出 Top 值。category 字段不得被模型当作连续数值解释。

### 8.2 目标协议（v1）：槽位 / 无数字叙事

模型输出只允许两种形式之一（实现选一种，推荐 JSON claims）：

**形式 A — 占位符**

```text
字段「{{col.销售额}}」缺失率为 {{col.销售额.missing_rate_pct}}%，
影响 {{issue.MISSING_RATE_HIGH.销售额.affected_rows}} 行，建议优先处理。
```

程序用账本替换；未知占位符 → 删除该句。

**形式 B — JSON claims（推荐）**

```json
{
  "insights": [
    {
      "issue_id": "MISSING_RATE_HIGH:销售额",
      "comment": "该字段空值集中，会阻断金额汇总，建议先规定必填后再导入。"
    }
  ],
  "suggestions": [
    {
      "issue_id": "MISSING_RATE_HIGH:销售额",
      "priority": "high",
      "action": "与业务确认空值是未发生还是未采集，再决定删除或填补规则。"
    }
  ]
}
```

约束：

- `comment` / `action` 中**禁止阿拉伯数字**（可用「该问题」「上述字段」指代）。
- `issue_id` 必须存在于程序问题清单，否则丢弃该条。
- `priority` 若与规则默认冲突，以程序级别为准（P0=高，P1=中，P2=低）。
- 最终 Markdown 由程序拼接：「【P0】列 X：缺失率 Y%，N 行。模型话术……」

这样模型**没有通道**写出 `37.2%`。

### 8.3 过渡协议（MVP）：数字白名单（兼容现有 Markdown 五段）

在槽位改造完成前，允许模型写带数字的中文，但必须同时满足：

1. **账本先行**：先算完全部指标并渲染节 0–3，再调用 LLM；prompt 中的「事实账本」必须包含体检所需的缺失/重复/异常/问题条数，而不是只有 numeric mean。
2. **temperature = 0**；体检专用 system/user prompt（角色=数据质量工程师，禁止业务预测）。
3. **白名单**：只校验 LLM 生成的正文片段；其中每个数据数字（含百分数、整数、小数）必须能在 `_allowed_numbers` 命中。程序模板里的章节编号、评分阈值和固定指标定义来自版本化规则，不要求进入数据账本，也不参与模型段落白名单判断。
4. **容差收紧**：计数类相对容差 0、绝对容差 0；比率只允许账本已写入的四舍五入结果（如 12.34），禁止模型再四舍五入成 12%。
5. **禁止衍生运算**：不得自行计算「约一半」「合计」「同比」；任何衍生指标必须先在 Python 入账。
6. **减少预览**：不提供或仅提供 schema，避免从 `head` 清点行数。
7. **失败策略**：Layer 3 发现 ERROR → 该段丢弃，替换为程序模板句（「详见问题清单 {issue_id}」）；最多重试 LLM 1 次；仍失败则节 4–6 全用规则模板，报告仍 `success=true`。
8. **模糊词**：保留现有 Layer 6；额外禁止无分数的「总体较好 / 质量尚可 / 整体健康」。

**禁止**把 5% 容差当作「模型可以近似编造」。MVP 起体检路径取消 5% 相对容差。

### 8.4 如何避免「模型不用程序的数、自己编数」

这是同一问题的操作化清单：

| 手段 | 作用 |
|---|---|
| 程序章节不经 LLM | 分数与表格物理上不在模型输出里 |
| 槽位 / 无数字 comment | 模型无法生成数值 token |
| `_allowed_numbers` 覆盖所有展示值 | 校验有依据 |
| 衍生指标先入账 | 堵上「模型心算」 |
| 不传全表 | 堵上「从样本外推全表」 |
| 校验失败删段而非标红混排 | 用户看不到假数 |
| LLM 宕机走 `_fallback` 程序摘要 | 不编造解读数字 |

**反模式（本需求禁止）**：

- 让模型「根据预览估计缺失率」。
- 先生成全文再「尽量」校验。
- 校验失败只加 `⛔` 但仍展示错误数字。
- 把清洗后的更好指标写进总评却不声明口径。

### 8.5 其它幻觉来源与对策

| 风险 | 对策 |
|---|---|
| 清洗后体检虚高 | 默认 raw |
| 无时间维却 100 分 | 及时性 N/A，不加权 |
| 恒定数列 IQR | 跳过并声明 |
| ID 当度量 | Layer 4 + 角色账本 |
| 「异常=错误」 | 节 0/7 定义 + prompt |
| 模型发明新问题 | 只允许 `issue_id` 引用 |
| Layer 6 LLM 重写引入新数 | 重写后必须再跑白名单；失败则回退规则替换结果 |

现有 Layer 4（identifier）、Layer 6（模糊词）、Layer 7（建议角色）对体检仍然有效。Layer 5（聚类/回归样本量）在体检中改为：**若模型仍写出此类方法，直接删除该句**，不要改写成「样本不足仍建议尝试」。

**建议可追溯校验**：每条 LLM 建议必须引用一个已存在的 `issue_id`，并关联对应 `column_id` 与已计算事实；无 issue/事实来源的建议丢弃。字段角色为 identifier/category 时，禁止建议对其计算均值、回归或数值分布。高基数类别若 `n_unique≥20` 且 `avg_per_category<5`，禁止输出把 Top-N 城市/类别用于业务策略、排名结论或组间比较的建议；只允许说明组内样本稀疏风险和数据采集/分类治理动作。建议被规则改写后，必须重新执行数字白名单和角色检查；仍违规则删除该建议。

---

## 9. 与现有实现的关系

不在本 PRD 展开代码 diff，只锁定模块职责。

| 模块 | 职责变化 |
|---|---|
| `app.py` `/api/report` | 保留 `df_raw` 并可选生成 `df_clean` 对照；调用 `generate_health_report(...)`；该端点本 PRD 范围仅生成 health 报告 |
| `core/health_report.py` | 体检报告主入口：程序生成评分卡/问题清单/字段表；LLM 只生成受约束的解释与建议 |
| `core/quality_ledger.py` | 作为当前账本实现的扩展点；统一原始值/质量视图、字段角色、指标和 `_allowed_numbers`，避免独立账本分叉 |
| `core/fact_extractor.py` | 角色继续作为合法指标开关；支持用户确认角色、置信度与原因 |
| `core/number_whitelist.py` | health 模式：账本数字白名单与精确校验；数字校验失败时丢弃模型段落 |
| `core/rounding.py` | 统一 HALF_UP 舍入实现；比较阈值必须使用舍入前值 |
| `utils/validator.py` | 复用检测；补充一致性/伪装空值/P0 封顶所需字段；避免三套缺失率 |
| `core/data_profiler.py` | 类型推断可复用；评分不放在 profiler 以免和账本分叉 |
| 前端 `index.html` / `report.html` | 用 `stats` 渲染评分卡与问题表 |

返回 JSON 建议在现有字段上扩展：

```json
{
  "success": true,
  "report_type": "health",
  "markdown": "...",
  "sections": { "overview": "...", "fields": "...", "insights": "...", "anomalies": "...", "suggestions": "..." },
  "capability_boundary": "...",
  "stats": {
    "n_rows": 0,
    "n_cols": 0,
    "quality_summary": {},
    "health_scorecard": {},
    "issue_list": [],
    "field_table": []
  },
  "postprocess_log": []
}
```

`health_scorecard` / `field_table` / 完整 `issue_list` 均为程序产物。

---

## 10. 交互与体验（产品）

- 入口：现有「数据体检」类 Tab（`data-type="health"`）即为本报告。
- 生成中：提示「正在计算质量指标」与「正在生成解读」两阶段（若前端可改）。
- 展示：评分卡与问题表优先结构化；Markdown 下载仍包含程序表，保证离线可读。
- 失败：指标计算失败 → 整单失败；仅 LLM 失败 → 仍返回程序章节。

---

## 11. 验收标准

### 11.1 正确性

- [ ] 相同输入快照和相同配置上下文重复运行，`overall_score`、适用维分、字段指标与 `issue_*_count` 完全一致。
- [ ] 人为将一列 50% 置空后，该列至少 P0，综合分 ≤ 49。
- [ ] 无日期列时 `timeliness` 为 N/A，综合分不含该维。
- [ ] identifier 列终稿不出现均值/标准差/离群分析。
- [ ] IQR=0 的列不出现「0 个异常值表示数据完美」的误导句，而应「无变异，未做离群检测」。
- [ ] 相同数据快照、代码/指标规范版本、角色覆盖、主键、null-token 配置、时区、预期频率及 `as_of_date` 下重复运行，指标和 issue 清单完全一致；上下文变化须记录差异原因。
- [ ] 未声明主键时，重复的 identifier 候选列不触发主键重复 P0；声明单列/复合主键后，重复组合及缺失组成字段分别正确统计。
- [ ] 重名列使用不同 `column_id` 分别统计，结果不覆盖；显示层提示列名歧义。
- [ ] 前后空白或大小写不同的类别值在原始唯一性统计中保持区别，规范化差异由 `variant_gap` / 空白计数体现。
- [ ] 未配置的常见占位值不自动判空；启用 token 后报告命中数和配置版本。
- [ ] 货币/千分位文本按声明规则解析；非法值的 `parse_fail_count` 与分母正确；未声明单位的百分数字符串不自动换算。
- [ ] `+inf/-inf` 计入 `non_finite_count`，不进入均值、分位数或离群率分母。
- [ ] 阈值边界前后各一例使用未舍入 rate 判断；展示舍入不得改变 issue severity 或评分。
- [ ] IQR 边界附近的值按未舍入边界判定，展示舍入不改变 `outlier_count`。
- [ ] 未确认日期频率时 `expected_gap_period_count=null`；确认月频时完整覆盖首末日历月。
- [ ] 无适用字段的维度显示 N/A，不作为 100 分纳入综合分；报告显示参与综合分的维度数。
- [ ] 指标版本、输入快照 hash、角色覆盖、主键、null-token 配置、时区、`as_of_date` 与日期频率写入复现元数据。
- [ ] 报告与下载文件不暴露疑似个人信息或自由文本原值；不通过隐私规则的样例只输出掩码/类型说明。

### 11.2 反造数

- [ ] 向模型输出注入账本不存在的数字（如缺失率 12.34% 当账本为 10.00%）后，终稿该数字不得保留。
- [ ] 节 1–3 的 HTML/Markdown 表格与 `stats` JSON 一致，且生成路径不经过 LLM。
- [ ] `comment` 方案落地后，抽检模型原始输出不含阿拉伯数字（过渡期除外）。

### 11.3 降级

- [ ] 断开 LLM：报告仍含节 0–3 与附录，`success=true`。
- [ ] 清洗器异常：仍对 raw 体检，附录注明无对照。

### 11.4 范围

- [ ] 体检报告不含预测/聚类/业务 KPI 结论。
- [ ] 默认口径为原始表；若展示清洗后指标，必须标明「对照」。
- [ ] 评分不得被描述为业务真实性、业务准确性或对特定分析任务的适用性证明。

---

## 12. 分期

### MVP（本需求第一期）

- 主体检对象改为 raw。
- 程序输出节 0–3 最小集：规模、综合分与等级、适用维度及 N/A 原因、P0/P1/P2 清单、字段明细（现有指标 + 列评级）。
- LLM 仍走五段 JSON，但 prompt 换成体检专用 + temperature=0 + 白名单零容差；失败删段。
- 扩展账本：问题条数、缺失、重复、离群进入 `ledger_summary_text`。
- 固化复现元数据：输入快照 hash、代码/指标版本、角色覆盖、主键、null-token 配置、时区、as_of_date、日期频率。
- 引入列位置 ID、配置化 null token、原始值保持、未舍入阈值判断和示例脱敏。
- 关闭体检路径中的建模类建议。

### v1

- JSON claims / 占位符，模型正文无数字。
- 清洗对照附录、额外格式解析策略和用户可配置的日期/数值 locale 规则。
- 前端评分卡与问题表结构化渲染。
- 删除体检 prompt 中的全表预览。

### 明确不做的后续（除非新 PRD）

- 跨表 DQ、自动清洗写回、业务规则引擎（如「金额必须>0」用户自定义）。

---

## 13. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 白名单过严导致误杀页码、年份 | 叙事被删光 | 年份仅允许来自日期 min/max；章节序号由模板写，不经 LLM |
| 六维公式与用户直觉不符 | 不信任分数 | 附录公开公式；MVP 可调权重但需改代码 |
| 大表计算慢 | 超时 | 先失败提示；v1 再评估分块 |
| 角色误判 ID | 漏报均值 | 沿用语义优先规则；错误案例列入回归 |

---

## 14. 开放问题（默认值已定，实施可改）

| 问题 | 本文默认 |
|---|---|
| 报告入口是否提供分析/混合类型 | 否；报告 UI 只提供 health，API 对其他值明确拒绝 |
| 防造数先做槽位还是白名单 | MVP 白名单，v1 槽位 |
| 体检对象 | 原始表 |
| 综合封顶规则 | 任一 P0 → 分 ≤ 49 |
| null token 是否计入缺失率 | 本次配置 token 计入缺失并单列命中数；不重复扣分，未配置的占位字符串按普通值处理 |
| IQR 分位数插值 | `Series.quantile(..., interpolation="linear")` |
| 标准差 | `ddof=1`；n<2 为 null |
| 离群率分母 | 非空有效值 `n_valid`，不是总行数 |
| 入账舍入 | ROUND_HALF_UP 仅用于展示/入账；阈值与评分保留未舍入精度，rate 同时保存 raw 与 display |
| PRD 路径 | `docs/prd-data-health-report.md` |

---

## 15. 附录：与「分析报告管道」的差异摘要

| 项 | 分析报告（现状） | 数据体检（本 PRD） |
|---|---|---|
| 主问题 | 数据说明了什么业务 | 数据能不能用、哪里脏 |
| 数字来源 | 模型书写 + 5% 容差校验 | 程序书写；模型无数字或零容差白名单 |
| 异常 | 业务/统计异常洞察 | IQR + 格式/重复，且不称为业务错误 |
| 建议 | 业务建议 + 方法门槛 | 仅清洗与字段修复 |
| 清洗 | 常在清洗后分析 | 在清洗前体检 |
| 降级 | 统计摘要 | 完整评分卡仍可交付 |
