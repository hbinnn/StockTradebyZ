# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指导。

## 交流语言要求

**强制要求：无论何时，所有与用户的对话必须使用中文。**

本项目所有参与开发者均使用中文交流，Claude Code 必须始终以中文回复，不得使用英文或其他语言。

## 项目概述

AgentTrader 是一个面向 A 股的半自动选股系统，结合量化规则与 LLM 图表分析进行股票筛选。

## 环境变量

```bash
TUSHARE_TOKEN=<你的tushare-token>   # 必填，用于获取股票数据
GEMINI_API_KEY=<你的gemini-key>      # 可选，Gemini图表复评
ZHIPU_API_KEY=<你的智谱-key>         # 可选，智谱GLM-4.6V图表复评
```

## 常用命令

### 一键全流程
```bash
python run_all.py
python run_all.py --skip-fetch      # 跳过数据下载
python run_all.py --start-from 3    # 从第3步开始
```

### 分步运行
```bash
# 步骤1：拉取K线数据
python -m pipeline.fetch_kline

# 步骤2：量化初选
python -m pipeline.cli preselect
python -m pipeline.cli preselect --date 2026-03-13
python -m pipeline.cli preselect --config config/rules_preselect.yaml --data data/raw

# 步骤3：导出候选K线图
python dashboard/export_kline_charts.py

# 步骤4：Gemini图表复评
python agent/gemini_review.py
python agent/gemini_review.py --config config/gemini_review.yaml

# 步骤4（智谱GLM-4.6V替代方案）
python agent/zhipu_review.py
python agent/zhipu_review.py --config config/zhipu_review.yaml
```

### 安装依赖
```bash
pip install -r requirements.txt
```

## 架构设计

### 数据流程（5步流水线）
1. **pipeline/fetch_kline.py** - 从 Tushare 下载日线数据（前复权）到 `data/raw/*.csv`
2. **pipeline/cli.py preselect** - 运行量化初选策略（B1: KDJ+知行均线，或砖型图）生成 `data/candidates/`
3. **dashboard/export_kline_charts.py** - 将候选股票K线图导出到 `data/kline/{日期}/*.jpg`
4. **agent/zhipu_review.py** - 智谱 GLM-4.6V 分析图表，评分输出到 `data/review/{日期}/`
5. **dashboard/overlay_score_to_chart.py** - 将评分结果叠加到K线图下方

### 目录结构
- **pipeline/** - 数据抓取与量化初选逻辑
  - `fetch_kline.py` - Tushare 数据下载，含限流处理
  - `select_stock.py` - B1/BrickChart 策略实现
  - `Selector.py` - B1Selector、BrickChartSelector 类（使用 NumPy/Numba 向量化加速）
  - `pipeline_core.py` - MarketDataPreparer、TopTurnoverPoolBuilder
  - `cli.py` - preselect 命令行入口
  - `schemas.py` - Candidate/CandidateRun 数据类
- **dashboard/** - 图表渲染与导出
  - `components/charts.py` - 基于 Plotly 的日线/周线图表生成
  - `export_kline_charts.py` - 批量图表导出
  - `overlay_score_to_chart.py` - 将评分结果叠加到K线图下方
  - `app.py` - Streamlit 看盘界面
- **agent/** - LLM 复评基础架构
  - `base_reviewer.py` - BaseReviewer 基类（加载候选、查找图表、汇总结果）
  - `gemini_review.py` - GeminiReviewer 实现
  - `zhipu_review.py` - ZhipuReviewer 实现（智谱GLM-4.6V）
  - `prompt.md` - 交易分析提示词（含趋势/位置/量价/异动四个维度评分）
- **config/** - YAML 配置文件
  - `fetch_kline.yaml` - 数据抓取配置（日期范围、股票池、并发数）
  - `rules_preselect.yaml` - B1/Brick 策略参数
  - `gemini_review.yaml` - Gemini 模型、调用间隔、评分阈值
  - `zhipu_review.yaml` - 智谱GLM-4.6V模型、调用间隔、评分阈值
  - `dashboard.yaml` - 看板配置
- **data/** - 运行时数据（gitignored）
  - `raw/` - 原始日线 CSV 文件
  - `candidates/` - 初选候选列表
  - `kline/` - 导出的 K 线图表
  - `review/` - AI 复评结果

### 关键设计模式
- **Numba JIT 编译** - Selector 类使用 `@njit` 加速计算密集型逻辑
- **Pandas 兼容补丁** - `pipeline/fetch_kline.py` 通过 monkey-patch 处理已弃用的 `fillna(method=...)` 参数
- **策略模式** - B1Selector 和 BrickChartSelector 在 `select_stock.py` 中可互换使用
- **BaseReviewer 抽象** - `gemini_review.py` 和 `zhipu_review.py` 继承自 BaseReviewer；其他 LLM 可通过子类化扩展
