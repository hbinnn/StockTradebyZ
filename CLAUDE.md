# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指导。

## 交流语言要求

**强制要求：无论何时，所有与用户的对话必须使用中文。**

本项目所有参与开发者均使用中文交流，Claude Code 必须始终以中文回复，不得使用英文或其他语言。

## 项目概述

AgentTrader 是一个面向 A 股的半自动选股系统，结合量化规则与 LLM 图表分析进行股票筛选。支持 B1（KDJ+知行均线）和砖型图两种策略，可并行初选并独立评审。

## 环境变量

```bash
# 必填
TUSHARE_TOKEN=<你的tushare-token>   # Tushare Pro Token，用于获取股票数据

# AI 图表复评（按需配置）
SILICONFLOW_API_KEY=<key>   # SiliconFlow
ZHIPU_API_KEY=<key>         # 智谱 GLM-4.6V
GEMINI_API_KEY=<key>        # Gemini
BAILIAN_API_KEY=<key>       # 阿里云百炼
```

## 常用命令

### 一键全流程
```bash
python run_all.py                              # 使用本地 LM Studio（默认）
python run_all.py --skip-fetch                 # 跳过数据下载
python run_all.py --start-from 3               # 从第3步开始
python run_all.py --ai-review --reviewer bailian --bailian-model kimi-k2.6  # 百炼 AI 复评
python run_all.py --strategies brick           # 仅运行砖型图策略
python run_all.py --strategies b1,brick        # 运行指定策略
```

### 分步运行
```bash
# 步骤1：拉取K线数据
python -m pipeline.fetch_kline

# 步骤2：量化初选
python -m pipeline.cli preselect
python -m pipeline.cli preselect --strategies brick         # 仅砖型图
python -m pipeline.cli preselect --date 2026-03-13

# 步骤3：导出候选K线图
python dashboard/export_kline_charts.py

# 步骤4：AI图表复评
python agent/bailian/review.py --model kimi-k2.6            # 阿里云百炼
python agent/local/review.py                                # 本地 LM Studio
python agent/siliconflow/review.py                          # SiliconFlow Kimi-K2.6
python agent/zhipu/review.py                                # 智谱 GLM-4.6V

# 步骤5：评分叠加到K线图
python dashboard/overlay_score_to_chart.py

# 步骤6：完美图形相似度匹配
python -m similarity.patternMatcher

# 步骤7：图形匹配标注叠加
python dashboard/overlay_pattern_to_chart.py

# 步骤8：导出东方财富格式
python pipeline/export_for_eastmoney.py
python pipeline/export_for_eastmoney.py --min-score 4.5
python pipeline/export_for_eastmoney.py --format csv
```

## 目录结构

```
StockTradebyZ/
  pipeline/                  # 核心管线
    fetch_kline.py           # 步骤1：Tushare 数据下载
    cli.py                   # 步骤2：量化初选 CLI 入口
    select_stock.py          # 步骤2：策略注册表 + runner
    Selector.py              # PipelineSelector 基类 + 共享 Filter + Numba 核心
    schemas.py               # Candidate / CandidateRun 数据类
    pipeline_core.py         # MarketDataPreparer / TopTurnoverPoolBuilder
    pipeline_io.py           # 候选 JSON 读写
    export_for_eastmoney.py  # 步骤8：导出东方财富文件
    stocklist.csv
  strategies/                # 策略目录（每个策略自包含）
    b1/
      selector.py            # B1Selector + KDJQuantileFilter + MaxVolNotBearishFilter
      prompt.md              # B1 策略 AI 复评提示词
    brick/
      selector.py            # BrickChartSelector + 砖型图专属 Filter
      prompt.md              # 砖型图策略 AI 复评提示词
  agent/                     # LLM 评审
    base_reviewer.py         # BaseReviewer 基类
    local/                   # LocalReviewer（本地 LM Studio）
      review.py
      config.yaml
    siliconflow/             # SiliconFlowReviewer（Kimi-K2.6）
      review.py
      config.yaml
    zhipu/                   # ZhipuReviewer（智谱 GLM-4.6V）
      review.py
      config.yaml
    gemini/                  # GeminiReviewer
      review.py
      config.yaml
    bailian/                 # BailianReviewer（阿里云百炼）
      review.py
      config.yaml
  dashboard/                 # 图表渲染与看盘
    components/charts.py     # Plotly 日线/周线图表（含砖型图子图）
    export_kline_charts.py   # 步骤3：批量导出 K 线图
    overlay_score_to_chart.py # 步骤5：评分叠加
    overlay_pattern_to_chart.py # 步骤7：图形匹配标注
    app.py                   # Streamlit 看盘界面
  similarity/                # 完美图形相似度
    patternMatcher.py
  config/                    # 非 reviewer 的配置文件
    rules_preselect.yaml     # B1 / brick 策略参数
    fetch_kline.yaml         # 数据抓取配置
    dashboard.yaml           # 看板配置
    perfect_patterns.yaml    # 图形匹配配置
  data/                      # 运行数据（不提交）
    raw/                     # 原始日线 CSV
    candidates/              # 初选候选 JSON
    kline/{日期}/            # K 线图
    review/{日期}/           # AI 复评结果
    eastmoney/               # 东方财富导出
    logs/                    # 日志
  run_all.py                 # 一键全流程
  prompts/                   # （已废弃，prompt 移入 strategies/）
```

## 架构设计

### 策略扩展

新增策略 `xxx` 只需三步：
1. `mkdir strategies/xxx`，放入 `selector.py`（继承 `PipelineSelector`）和 `prompt.md`
2. 在 `select_stock.py` 中 `@_register("xxx")` 注册 runner 函数
3. 在 reviewer 配置的 `strategy_prompts` 中添加 `xxx: "strategies/xxx/prompt.md"`

### 策略注册表

`select_stock.py` 使用 `@_register(name, warmup_fn)` 装饰器注册策略 runner：
```python
_STRATEGY_RUNNERS = {}  # 装饰器自动填充
# run_preselect 遍历注册表调度，支持 --strategies 过滤
```

### Prompt 映射

`BaseReviewer` 从配置的 `strategy_prompts` 字典加载策略→prompt 映射，无默认回退。缺 prompt 的策略候选会被跳过并报错。

### 关键设计模式
- **Numba JIT 编译** — `@njit` 加速 KDJ 递推、砖型图核心、最大量非阴线计算
- **Filter 双接口** — 每个 Filter 提供 `__call__(hist) -> bool`（点查）和 `vec_mask(df) -> np.ndarray`（向量化）
- **策略注册表** — `@_register` 装饰器自动注册策略 runner，新增策略零侵入
- **BaseReviewer 抽象** — 五个 reviewer 均继承 BaseReviewer，只需实现 `review_stock()`

## 数据目录

| 目录 | 说明 |
|------|------|
| `data/raw/` | 原始日线 CSV 文件（Tushare 下载） |
| `data/candidates/` | 初选候选列表（JSON，含 strategy 字段） |
| `data/kline/{日期}/` | 导出的 K 线图表（砖型图候选含砖型图子图） |
| `data/review/{日期}/` | AI 复评结果（`{code}_{strategy}.json` + suggestion.json） |
| `data/pattern_matched/` | 完美图形匹配结果 |
| `data/eastmoney/` | 东方财富导出文件 |
| `data/logs/` | 日志文件 |

## AI 评分维度

### B1 策略（prompt_b1.md）
| 维度 | 权重 | 说明 |
|------|------|------|
| trend_structure | 0.20 | 均线多头排列健康度 |
| price_position | 0.20 | 相对历史高点的位置 |
| volume_behavior | 0.30 | 上涨放量、回调缩量健康度 |
| previous_abnormal_move | 0.30 | 主力建仓痕迹 |

### 砖型图策略（prompt_brick.md）
| 维度 | 权重 | 说明 |
|------|------|------|
| 位置与定式 | 核心 | N型起跳/横盘起跳/趋势延续 |
| 砖块强度 | 核心 | 红柱实体占比 |
| K线质量 | 重要 | 阳线实体、振幅 |
| 趋势与环境 | 重要 | 黄线/白线多头排列 |
| 纪律符合度 | 保障 | 数砖纪律、止损位 |

判定规则：PASS(≥4.0) / WATCH(3.2~4.0) / FAIL(<3.2)

## 术语表

| 术语 | 代码变量 | 说明 |
|------|----------|------|
| **白线** | `zxdq` | 知行短期线，双指数移动平均（span=10），橙色线 |
| **黄线** | `zxdkx` | 知行多空线，四均线均值 MA(14,28,57,114)/4，蓝色线 |
| **KDJ** | `k,d,j` | 随机指标 |
| **砖型图/砖高** | `brick` | 通达信 VAR6A 公式计算的砖型图差分值 |

## Git 安全规范

**强制要求：通过 git 提交代码前必须检查，不允许将敏感信息提交到 GitHub。**

### 禁止提交的内容
- API Key（任何平台的 key）
- Token（TUSHARE_TOKEN）
- 任何形式的密钥、密码、凭证
- 脚本文件中硬编码的 API Key 和 Token（如 `run_with_keys.sh`）

### 检查方法
```bash
git diff --cached  # 检查暂存区
git diff           # 检查工作区
```

## 常见问题

### Q1：fetch_kline 报 token 错误
检查 TUSHARE_TOKEN 是否已设置，确认 token 有效且账号权限正常。

### Q2：导出图表时报 write_image 错误
确认已安装 kaleido：`pip install kaleido`

### Q3：AI复评失败
- 检查对应平台的 API_KEY 是否设置
- 网络连接是否正常
- 可尝试增加配置中的 request_delay 间隔

### Q4：Python 版本兼容性问题
项目已适配 Python 3.9。如果遇到类型注解错误（如 `str | Path`），使用 `from typing import Union` 替代。

### Q5：Tushare API 限流
每分钟最多 500 次请求，程序会自动重试。可降低 `config/fetch_kline.yaml` 中的 `workers` 并发数或分批次拉取。
