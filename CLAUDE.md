# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指导。

## 交流语言要求

**强制要求：无论何时，所有与用户的对话必须使用中文。**

本项目所有参与开发者均使用中文交流，Claude Code 必须始终以中文回复，不得使用英文或其他语言。

## 项目概述

AgentTrader 是一个面向 A 股的半自动选股系统，结合量化规则与 LLM 图表分析进行股票筛选。

## 环境变量

```bash
# 必填
TUSHARE_TOKEN=<你的tushare-token>   # Tushare Pro Token，用于获取股票数据

# AI 图表复评（可选，默认为 SiliconFlow Kimi-K2.6）
SILICONFLOW_API_KEY=<你的siliconflow-key>  # SiliconFlow API Key
ZHIPU_API_KEY=<你的智谱-key>               # 智谱 GLM-4.6V（备选）
GEMINI_API_KEY=<你的gemini-key>            # Gemini（备选）
```

**提示：** 环境变量已写入 `~/.zshrc`，新终端会话自动生效。

## 常用命令

### 一键全流程
```bash
python run_all.py                       # 使用 SiliconFlow Kimi-K2.6（默认）
python run_all.py --skip-fetch          # 跳过数据下载
python run_all.py --start-from 3        # 从第3步开始
python run_all.py --reviewer zhipu      # 使用智谱 GLM-4.6V
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

# 步骤4：AI图表复评（SiliconFlow Kimi-K2.6 或 智谱GLM-4.6V）
python agent/siliconflow_review.py                    # SiliconFlow Kimi-K2.6（默认）
python agent/siliconflow_review.py --config config/siliconflow_review.yaml
python agent/zhipu_review.py                         # 智谱 GLM-4.6V（备选）
python agent/zhipu_review.py --config config/zhipu_review.yaml

# 步骤5：评分叠加到K线图
python dashboard/overlay_score_to_chart.py

# 步骤6：完美图形相似度匹配
python -m similarity.patternMatcher

# 步骤7：图形匹配标注叠加
python dashboard/overlay_pattern_to_chart.py

# 步骤8：导出东方财富格式
python export_for_eastmoney.py
python export_for_eastmoney.py --min-score 4.5  # 调整评分门槛
python export_for_eastmoney.py --format csv    # 导出CSV格式
```

### 安装依赖
```bash
# 使用清华镜像源（国内推荐）
pip install -r requirements.txt

# 可选： kaleido（用于导出Plotly图表为图片）
pip install kaleido
```

## 环境要求

- **Python 版本**：项目当前使用 **Python 3.9**（系统自带）
- **注意事项**：requirements.txt 中指定的包版本（如 numba==0.64.0、pandas==3.0.1）需要 Python ≥3.10，已降级适配 Python 3.9：
  - numba==0.59.1（Python 3.9 最高支持）
  - numpy==1.26.4
  - pandas==2.2.3
  - protobuf==4.25.9
  - streamlit==1.50.0

## 数据目录

| 目录 | 说明 |
|------|------|
| `data/raw/` | 原始日线 CSV 文件（Tushare 下载） |
| `data/candidates/` | 初选候选列表（JSON） |
| `data/kline/{日期}/` | 导出的 K 线图表（含评分叠加） |
| `data/review/{日期}/` | AI 复评结果（每只股票 JSON + suggestion.json） |
| `data/pattern_matched/` | 完美图形匹配结果 |
| `data/eastmoney/` | 东方财富导出文件 |
| `data/logs/` | 日志文件 |

## 架构设计

### 数据流程（8步流水线）
1. **pipeline/fetch_kline.py** - 从 Tushare 下载日线数据（前复权）到 `data/raw/*.csv`
2. **pipeline/cli.py preselect** - 运行量化初选策略（B1: KDJ+知行均线，或砖型图）生成 `data/candidates/`
3. **dashboard/export_kline_charts.py** - 将候选股票K线图导出到 `data/kline/{日期}/*.jpg`
4. **agent/siliconflow_review.py** - SiliconFlow Kimi-K2.6 分析图表（默认），评分输出到 `data/review/{日期}/`
5. **dashboard/overlay_score_to_chart.py** - 将评分结果叠加到K线图下方
6. **similarity/patternMatcher.py** - 完美图形相似度匹配
7. **dashboard/overlay_pattern_to_chart.py** - 将图形匹配标注叠加到K线图
8. **export_for_eastmoney.py** - 导出东方财富可导入文件

### 目录结构
- **pipeline/** - 数据抓取与量化初选逻辑
  - `fetch_kline.py` - Tushare 数据下载，含限流处理
  - `select_stock.py` - B1/BrickChart 策略实现
  - `Selector.py` - B1Selector、BrickChartSelector 类（使用 NumPy/Numba 向量化加速）
  - `pipeline_core.py` - MarketDataPreparer、TopTurnoverPoolBuilder
  - `cli.py` - preselect 命令行入口
  - `schemas.py` - Candidate/CandidateRun 数据类
  - `stocklist.csv` - 股票清单（含 5487 只 A 股）
- **similarity/** - 完美图形相似度比对
  - `patternMatcher.py` - 特征提取与相似度计算
- **dashboard/** - 图表渲染与导出
  - `components/charts.py` - 基于 Plotly 的日线/周线图表生成
  - `export_kline_charts.py` - 批量图表导出
  - `overlay_score_to_chart.py` - 将评分结果叠加到K线图下方
  - `app.py` - Streamlit 看盘界面
- **agent/** - LLM 复评基础架构
  - `base_reviewer.py` - BaseReviewer 基类（加载候选、查找图表、汇总结果）
  - `siliconflow_review.py` - SiliconFlowReviewer 实现（Kimi-K2.6，默认）
  - `gemini_review.py` - GeminiReviewer 实现
  - `zhipu_review.py` - ZhipuReviewer 实现（智谱GLM-4.6V，备选）
  - `prompt.md` - 交易分析提示词（含趋势/位置/量价/异动四个维度评分）
- **root/**
  - `export_for_eastmoney.py` - 导出东方财富可导入的股票文件
  - `run_all.py` - 一键全流程脚本
- **config/** - YAML 配置文件
  - `fetch_kline.yaml` - 数据抓取配置（日期范围、股票池、并发数）
  - `rules_preselect.yaml` - B1/Brick 策略参数
  - `siliconflow_review.yaml` - SiliconFlow Kimi-K2.6 模型、调用间隔、评分阈值
  - `gemini_review.yaml` - Gemini 模型、调用间隔、评分阈值
  - `zhipu_review.yaml` - 智谱GLM-4.6V模型、调用间隔、评分阈值
  - `dashboard.yaml` - 看板配置

### 关键设计模式
- **Numba JIT 编译** - Selector 类使用 `@njit` 加速计算密集型逻辑
- **Pandas 兼容补丁** - `pipeline/fetch_kline.py` 通过 monkey-patch 处理已弃用的 `fillna(method=...)` 参数
- **策略模式** - B1Selector 和 BrickChartSelector 在 `select_stock.py` 中可互换使用
- **BaseReviewer 抽象** - `gemini_review.py` 和 `zhipu_review.py` 继承自 BaseReviewer；其他 LLM 可通过子类化扩展

## 东方财富导出

### 功能说明
`export_for_eastmoney.py` 将 AI 复评通过的股票导出为东方财富可导入的文件格式。

### 支持格式
| 格式 | 说明 | 文件后缀 |
|------|------|----------|
| eastmoney | 股票代码带交易所后缀（默认） | .txt |
| csv | CSV 格式（股票代码,股票名称） | .csv |
| plain | 纯代码文本（每行一个代码） | .txt |

### 输出文件名
```
eastmoney_{策略名}_{日期}.txt
# 示例：eastmoney_B1_2026-04-17.txt
```

### 导入东方财富方法
1. 打开东方财富软件
2. 自选股 → 右键 → 导入自选股 → 选择文件 → 确认

### 股票代码后缀规则
- `.SH` - 上海证券交易所（6/9 开头）
- `.SZ` - 深圳证券交易所（0/3 开头）
- `.BJ` - 北京证券交易所（4/8 开头）

## AI 评分维度

| 维度 | 权重 | 说明 |
|------|------|------|
| trend_structure | 0.20 | 均线多头排列健康度 |
| price_position | 0.20 | 相对历史高点的位置 |
| volume_behavior | 0.30 | 上涨放量、回调缩量健康度 |
| previous_abnormal_move | 0.30 | 主力建仓痕迹 |

判定规则：PASS(≥4.0) / WATCH(3.2~4.0) / REJECT(<3.2)

## 术语表

| 术语 | 代码变量 | 说明 |
|------|----------|------|
| **白线** | `zxdq` | 知行短期线，双指数移动平均（span=10），橙色线 |
| **黄线** | `zxdkx` | 知行多空线，四均线均值 MA(14,28,57,114)/4，蓝色线 |
| **KDJ** | `k,d,j` | 随机指标 |

## Git 安全规范

**强制要求：通过 git 提交代码前必须检查，不允许将敏感信息提交到 GitHub。**

### 禁止提交的内容
- API Key（`SILICONFLOW_API_KEY`、`ZHIPU_API_KEY`、`GEMINI_API_KEY`）
- Token（`TUSHARE_TOKEN`）
- 任何形式的密钥、密码、凭证
- 脚本文件中硬编码的 API Key 和 Token（如 `run_with_keys.sh`）

### 检查方法
**每次提交前必须执行以下命令检查：**
```bash
git diff --cached  # 检查暂存区
git diff           # 检查工作区
```
确保提交内容中不包含任何 token、apikey、secret、password 等敏感信息。

### 如果已提交敏感信息
如发现已提交敏感信息，立即：
1. 从 git 历史中清除：`git filter-branch` 或 `git push --force`（需谨慎操作）
2. 轮换相关 API Key/Token
3. 更新 .gitignore 确保文件被忽略

## 常见问题

### Q1：fetch_kline 报 token 错误
检查 TUSHARE_TOKEN 是否已设置，确认 token 有效且账号权限正常。

### Q2：导出图表时报 write_image 错误
确认已安装 kaleido：`pip install kaleido`

### Q3：AI复评失败
- 检查 API_KEY 是否设置
- 网络连接是否正常
- 可尝试增加 request_delay 间隔

### Q4：Python 版本兼容性问题
项目已适配 Python 3.9。如果遇到类型注解错误（如 `str | Path`），需要：
- 使用 `from typing import Union` 替代 `str | Path`
- 使用 `Dict[str, Any]` 替代 `dict[str, Any]`

### Q5：Tushare API 限流
每分钟最多 500 次请求，程序会自动重试。如频繁遇到限流，可考虑：
- 降低 `config/fetch_kline.yaml` 中的 `workers` 并发数
- 分批次拉取数据
