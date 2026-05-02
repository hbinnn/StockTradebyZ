# AgentTrader 项目完整文档

## 一、项目概述

AgentTrader 是一个面向 A 股的半自动选股系统，结合量化规则与 LLM 图表分析进行股票筛选。

### 核心流程（8步流水线）

```
数据下载 → 量化初选 → K线图导出 → AI图表复评 → 叠加评分 → 图形匹配 → 标注叠加 → 导出东方财富
```

1. **pipeline/fetch_kline.py** - 从 Tushare 下载日线数据（前复权）到 `data/raw/*.csv`
2. **pipeline/cli.py preselect** - 运行量化初选策略生成 `data/candidates/`
3. **dashboard/export_kline_charts.py** - 将候选股票K线图导出到 `data/kline/{日期}/*.jpg`
4. **agent/{platform}/review.py** - LLM 图表复评（百炼/SiliconFlow/智谱/Gemini/LM Studio），评分输出到 `data/review/{日期}/`
5. **dashboard/overlay_score_to_chart.py** - 将评分结果叠加到K线图
6. **similarity/patternMatcher.py** - 完美图形相似度匹配
7. **dashboard/overlay_pattern_to_chart.py** - 将图形匹配标注叠加到K线图
8. **pipeline/export_for_eastmoney.py** - 导出东方财富可导入文件

---

## 二、术语表

### 技术指标

| 指标 | 代码变量 | 说明 |
|------|----------|------|
| **白线** | `zxdq` | 知行短期线，双指数移动平均（span=10），代表短期趋势 |
| **黄线** | `zxdkx` | 知行多空线，四均线均值 MA(14,28,57,114)/4，代表中长期趋势 |
| **KDJ** | `k,d,j` | 随机指标，用于判断超跌反弹机会 |
| **砖型图** | `brick` | 基于通达信 VAR6A 公式计算的砖型图 |
| **MA** | `ma5,ma10...` | 移动平均线 |

### K线图元素

| 元素 | 颜色 | 说明 |
|------|------|------|
| K线（涨） | 红色 `#dc3545` | 收盘价 > 开盘价 |
| K线（跌） | 绿色 `#28a745` | 收盘价 < 开盘价 |
| 白线 | 橙色 `#e67e22` | zxdq 知行短期线 |
| 黄线 | 蓝色 `#2980b9` | zxdkx 知行多空线 |

---

## 三、环境配置

### 2.1 系统要求

- Python 3.9+（已适配，推荐使用系统自带 Python 3.9）
- macOS（已在 macOS 上验证通过）
- 网络连接（用于Tushare数据下载和智谱API调用）

### 2.2 安装依赖

```bash
# 使用清华镜像源（国内推荐）
pip install -r requirements.txt

# 可选：kaleido（用于导出Plotly图表为图片）
pip install kaleido
```

**Python 版本说明：** requirements.txt 中指定的包版本（如 numba==0.64.0、pandas==3.0.1）需要 Python ≥3.10。项目已降级适配 Python 3.9：
- numba==0.59.1
- numpy==1.26.4
- pandas==2.2.3
- protobuf==4.25.9
- streamlit==1.50.0

主要依赖包括：
- `pandas`、`numpy` - 数据处理
- `tushare` - 股票数据接口
- `plotly`、`kaleido` - K线图绘制与导出
- `pillow` - 图片处理（评分叠加）
- `numba` - JIT加速计算
- `pyyaml` - 配置文件解析
- `streamlit` - 看盘界面

### 2.3 环境变量配置

```powershell
# Tushare Token（必填）
[Environment]::SetEnvironmentVariable("TUSHARE_TOKEN", "你的token", "User")

# 智谱API Key（用于图表复评，可选）
[Environment]::SetEnvironmentVariable("ZHIPU_API_KEY", "你的智谱key", "User")

# Gemini API Key（用于图表复评，可选）
[Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "你的gemini-key", "User")
```

**重要**：设置环境变量后需要**重开终端**才能生效。

### 2.4 Tushare Token 获取

1. 注册 Tushare 账号：https://tushare.pro
2. 获取积分（需要 >= 1200 才能使用日线数据接口）
3. 在个人中心 → API Token 获取 token

---

## 三、快速开始

### 3.1 一键全流程

```bash
python run_all.py
python run_all.py --skip-fetch      # 跳过数据下载
python run_all.py --start-from 3    # 从第3步开始
```

### 3.2 分步运行

```bash
# 步骤1：拉取K线数据
python -m pipeline.fetch_kline

# 步骤2：量化初选
python -m pipeline.cli preselect
python -m pipeline.cli preselect --date 2026-04-18

# 步骤3：导出候选K线图
python dashboard/export_kline_charts.py

# 步骤4：AI图表复评
python agent/bailian/review.py --model kimi-k2.6   # 阿里云百炼
python agent/local/review.py                        # 本地 LM Studio
python agent/siliconflow/review.py                  # SiliconFlow Kimi-K2.6

# 步骤5：叠加评分到K线图
python dashboard/overlay_score_to_chart.py

# 步骤6：完美图形相似度匹配
python -m similarity.patternMatcher

# 步骤7：图形匹配标注叠加
python dashboard/overlay_pattern_to_chart.py

# 步骤8：导出东方财富文件
python pipeline/export_for_eastmoney.py
```

---

## 四、目录结构

```
AgentTrader/
├── agent/                    # LLM图表复评
│   ├── base_reviewer.py      # BaseReviewer基类
│   ├── local/                # LocalReviewer（LM Studio）
│   ├── siliconflow/          # SiliconFlowReviewer（Kimi-K2.6）
│   ├── zhipu/                # ZhipuReviewer（GLM-4.6V）
│   ├── gemini/               # GeminiReviewer
│   └── bailian/              # BailianReviewer（阿里云百炼）
├── strategies/               # 策略目录（自包含）
│   ├── b1/
│   │   ├── selector.py       # B1Selector + KDJ Filter
│   │   └── prompt.md         # B1 AI复评提示词
│   └── brick/
│       ├── selector.py       # BrickChartSelector + 砖型图 Filter
│       └── prompt.md         # 砖型图 AI复评提示词
├── pipeline/                 # 核心管线
│   ├── fetch_kline.py        # Tushare数据下载
│   ├── cli.py                # 命令行入口（--strategies 参数）
│   ├── select_stock.py       # 策略注册表 + runner
│   ├── Selector.py           # PipelineSelector 基类 + 共享 Filter
│   ├── pipeline_core.py      # MarketDataPreparer
│   ├── schemas.py            # Candidate/CandidateRun
│   └── export_for_eastmoney.py # 东方财富导出
├── dashboard/                # 图表与看盘
├── config/                   # 全局配置
│   ├── rules_preselect.yaml  # B1/砖型策略参数
│   ├── fetch_kline.yaml      # 数据拉取配置
│   └── dashboard.yaml        # 看板配置
├── data/                     # 运行时数据（gitignored）
├── run_all.py                # 一键全流程脚本
└── requirements.txt
```

---

## 五、量化选股策略：B1策略

### 5.1 策略原理

B1策略是一个基于KDJ指标和知行均线的波段选股策略，包含4个Filter组合：

```
B1策略 = KDJQuantileFilter + ZXConditionFilter + WeeklyMABullFilter + MaxVolNotBearishFilter
```

### 5.2 Filter详解

#### Filter 1：KDJ分位过滤（KDJQuantileFilter）
- **条件**：J值 < 15 或 J值处于10%历史分位
- **作用**：筛选超跌股票，等待反弹机会

#### Filter 2：知行均线条件（ZXConditionFilter）
- **条件**：收盘价 > 知行短线 且 知行多空 > 知行短线
- **作用**：确认短期趋势向上

#### Filter 3：周线均线多头（WeeklyMABullFilter）
- **条件**：周线 MA5 > MA10 > MA20
- **作用**：确认周线级别上升趋势

#### Filter 4：最大量日非阴线（MaxVolNotBearishFilter）
- **条件**：近20日最大量那天不是阴线
- **作用**：排除主力出货嫌疑

### 5.3 代码实现

详见 `strategies/b1/selector.py` 中的 `B1Selector` 类，共享 Filter 在 `pipeline/Selector.py`，使用 `@njit` 加速计算。

### 5.4 通达信公式

```text
{知行短线}
ZXDKX:=EMA(C,5);
{知行多空}
ZXDQ:=EMA(MA(C,20),5);

{选股条件}
KDJ_J:=RSV.N;
KDJ_J<15 OR KDJ_J<HHVRSV(CLOSE,60)*0.1, FILTER;
CLOSE>ZXDKX AND ZXDQ>ZXDKX, FILTER;
MA5:=MA(C,5);
MA10:=MA(C,10);
MA20:=MA(C,20);
COUNT(MA5>MA10 AND MA10>MA20,5)=5, FILTER;
MAXV:=HHV(VOL,20);
MAXV=MAXV AND CLOSE>=OPEN, FILTER;
```

---

## 六、AI图表复评

### 6.1 评分维度

AI复评从4个维度分析股票：

| 维度 | 权重 | 说明 |
|------|------|------|
| trend_structure（趋势结构） | 0.20 | 均线多头排列健康度 |
| price_position（价格位置） | 0.20 | 相对历史高点的位置 |
| volume_behavior（量价行为） | 0.30 | 上涨放量、回调缩量健康度 |
| previous_abnormal_move（前期异动） | 0.30 | 主力建仓痕迹 |

**总分计算公式**：
```
total_score = trend_structure × 0.20 + price_position × 0.20 + volume_behavior × 0.30 + previous_abnormal_move × 0.30
```

**总分范围**：1.0 ~ 5.0

### 6.2 判定规则

| 判定 | 分数范围 | 说明 |
|------|---------|------|
| PASS | total_score ≥ 4.0 | 推荐关注 |
| WATCH | 3.2 ≤ total_score < 4.0 | 观望 |
| REJECT | total_score < 3.2 | 不推荐 |

**特殊规则**：volume_behavior = 1 → 必须 REJECT

### 6.3 支持的LLM模型

目前支持三种LLM进行图表复评：

1. **阿里云百炼** — 配置：`agent/bailian/config.yaml`
2. **SiliconFlow Kimi-K2.6** — 配置：`agent/siliconflow/config.yaml`
3. **智谱GLM-4.6V** — 配置：`agent/zhipu/config.yaml`
4. **Gemini** — 配置：`agent/gemini/config.yaml`
5. **本地 LM Studio** — 配置：`agent/local/config.yaml`

### 6.4 SiliconFlow Kimi-K2.6 配置

```yaml
# agent/siliconflow/config.yaml
model: "Pro/moonshotai/Kimi-K2.6"  # 模型名称
request_delay: 5                    # 请求间隔（秒）
skip_existing: false                # 跳过已存在文件
suggest_min_score: 4.0              # 推荐门槛
```

### 6.5 智谱API配置

```yaml
# agent/zhipu/config.yaml
model: "glm-4.6v"           # 模型名称
request_delay: 5             # 请求间隔（秒）
skip_existing: false        # 跳过已存在文件
suggest_min_score: 4.0      # 推荐门槛
```

---

## 七、评分叠加功能

### 7.1 功能说明

将AI评分结果叠加到K线图右侧区域，便于同时查看K线和评分。

### 7.2 叠加内容

- 评分详情（标题）
- 总分
- 判定（PASS/WATCH/REJECT，颜色编码）
- 信号类型（trend_start/rebound/distribution_risk）
- 简评

### 7.3 颜色编码

| 判定 | 颜色 |
|------|------|
| PASS | 绿色 |
| WATCH | 橙色 |
| REJECT | 红色 |

### 7.4 使用方式

```bash
# 重新生成K线图并叠加评分
python dashboard/export_kline_charts.py
python dashboard/overlay_score_to_chart.py

# 指定日期
python dashboard/overlay_score_to_chart.py --date 2026-04-17
```

### 7.5 注意事项

- 评分叠加不会遮挡K线图，扩展区域在图片下方
- 需要中文字体支持（自动使用Windows系统字体）
- 评论字段过长时会自动换行

---

## 八、东方财富导出

### 8.1 功能说明

将 AI 复评通过的股票导出为东方财富可导入的文件格式，方便在东方财富软件中直接查看和分析。

### 8.2 使用方式

```bash
# 默认导出（评分≥4.0）
python pipeline/export_for_eastmoney.py

# 调整评分门槛
python pipeline/export_for_eastmoney.py --min-score 4.5

# 导出CSV格式（带股票名称）
python pipeline/export_for_eastmoney.py --format csv

# 导出纯代码格式
python pipeline/export_for_eastmoney.py --format plain
```

### 8.3 输出文件名

```
eastmoney_{策略名}_{日期}.txt
# 示例：eastmoney_B1_2026-04-17.txt
```

### 8.4 支持格式

| 格式 | 说明 | 文件后缀 |
|------|------|----------|
| eastmoney | 股票代码带交易所后缀（默认） | .txt |
| csv | CSV 格式（股票代码,股票名称） | .csv |
| plain | 纯代码文本（每行一个代码） | .txt |

### 8.5 股票代码后缀规则

| 后缀 | 交易所 | 代码特征 |
|------|--------|----------|
| .SH | 上海证券交易所 | 6/9 开头 |
| .SZ | 深圳证券交易所 | 0/3 开头 |
| .BJ | 北京证券交易所 | 4/8 开头 |

### 8.6 导入东方财富方法

1. 打开东方财富软件
2. 自选股 → 右键 → 导入自选股 → 选择文件 → 确认

### 8.7 文件内容示例

```
000669.SZ
000890.SZ
001216.SZ
002082.SZ
002470.SZ
002620.SZ
...
600026.SH
600988.SH
601872.SH
...
```

---

## 九、配置文件说明

### 8.1 fetch_kline.yaml

```yaml
# 数据拉取配置
start: "2019-01-01"           # 开始日期
end: "2026-04-19"             # 结束日期
stocklist: "data/stock_list.csv"  # 股票列表文件
exclude_boards: []           # 排除板块（空=全部股票）
out: "data/raw"               # 输出目录
workers: 8                    # 并发线程数
```

### 8.2 rules_preselect.yaml

```yaml
b1:                          # B1策略配置
  enabled: true
  top_m: 5000                 # 流动性池大小
```

### 8.3 zhipu_review.yaml

```yaml
candidates: "data/candidates/candidates_latest.json"
kline_dir: "data/kline"
output_dir: "data/review"
prompt_path: "strategies/b1/prompt.md"
model: "glm-4.6v"
request_delay: 5
skip_existing: false
suggest_min_score: 4.0
```

---

## 九、输出结果解读

### 9.1 候选文件

`data/candidates/candidates_latest.json` 结构：
```json
{
  "pick_date": "2026-04-17",
  "candidates": [
    {"code": "600498", "strategy": "b1", "close": 49.27},
    ...
  ]
}
```

### 9.2 AI复评汇总

`data/review/{日期}/suggestion.json` 结构：
```json
{
  "date": "2026-04-17",
  "min_score_threshold": 4.0,
  "total_reviewed": 35,
  "recommendations": [
    {
      "rank": 1,
      "code": "600726",
      "verdict": "PASS",
      "total_score": 4.0,
      "signal_type": "trend_start",
      "comment": "主力建仓迹象明显，上涨趋势良好"
    },
    ...
  ],
  "excluded": []
}
```

### 9.3 单股评分

`data/review/{日期}/{code}.json` 结构：
```json
{
  "trend_reasoning": "上涨趋势良好...",
  "position_reasoning": "股价位于...",
  "volume_reasoning": "成交量持续放大...",
  "abnormal_move_reasoning": "前期出现过...",
  "signal_reasoning": "由于上涨趋势良好...",
  "scores": {
    "trend_structure": 4,
    "price_position": 3,
    "volume_behavior": 4,
    "previous_abnormal_move": 3
  },
  "total_score": 4.0,
  "signal_type": "trend_start",
  "verdict": "PASS",
  "comment": "主力建仓迹象明显，上涨趋势良好，可继续持有",
  "code": "600726"
}
```

---

## 十、常见问题

### Q1：fetch_kline 报 token 错误
- 检查 TUSHARE_TOKEN 是否已设置
- 确认 token 有效且账号权限正常

### Q2：导出图表时报 write_image 错误
- 确认已安装 kaleido：`pip install -U kaleido`

### Q3：智谱API调用失败
- 检查 ZHIPU_API_KEY 是否设置
- 网络连接是否正常
- 可尝试增加 request_delay 间隔

### Q4：评分叠加后中文显示乱码
- 确保Windows系统安装了中文字体（黑体、宋体、微软雅黑）
- 检查Pillow版本是否支持中文

### Q5：没有候选股票
- 检查 data/raw 是否有最新数据
- 确认 pick_date 是否在有效交易日

### Q6：评分总分超过5分
- 这是GLM返回的简单相加值而非加权值
- 已修改 strategies/b1/prompt.md 使用加权计算公式
- 重新运行 `agent/zhipu/review.py` 可获得正确评分

---

## 十一、注意事项

1. **数据更新频率**：建议每个交易日结束后运行一次
2. **API限流**：Tushare和智谱API都有频次限制，勿频繁调用
3. **备份数据**：`data/` 目录在gitignore中，重要数据请定期备份
4. **网络稳定**：图表复评需要稳定的网络连接

---

## 十二、联系方式与许可

本项目采用 CC BY-NC 4.0 协议发布。

- 允许：学习、研究、非商业用途的使用与分发
- 禁止：任何形式的商业使用、出售或以盈利为目的的部署
- 要求：转载或引用须注明原作者与来源
