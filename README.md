# AgentTrader

一个面向 A 股的半自动选股项目：

- 使用 Tushare 拉取股票日线数据
- 用量化规则做初选（B1 策略 + 砖型图策略）
- 导出候选股票 K 线图（砖型图候选含砖型图红绿柱子图）
- 调用 LLM（百炼/智谱/SiliconFlow/Gemini/LM Studio）对图表进行 AI 复评打分
- 多策略可并行初选、独立评审，互不干扰

## 更新说明

- 重构目录结构：策略代码与 prompt 按策略独立目录存放（`strategies/{name}/`）
- 新增砖型图策略（通达信原版公式），含专用 AI 复评 prompt
- 新增阿里云百炼评审器
- 新增 `--strategies` CLI 参数支持按策略筛选
- 支持多策略并行初选，同一股票可命中多个策略分别评审
- 评审器配置文件与代码同目录存放

## 1. 项目流程

完整流程对应 [run_all.py](run_all.py)：

1. 下载 K 线数据（pipeline.fetch_kline）
2. 量化初选（pipeline.cli preselect）
3. 导出候选图表（dashboard/export_kline_charts.py）
4. AI 图表复评（agent/{platform}/review.py）
5. 叠加评分到K线图（dashboard/overlay_score_to_chart.py）
6. 完美图形相似度匹配（similarity.patternMatcher）
7. 图形匹配标注叠加（dashboard/overlay_pattern_to_chart.py）
8. 导出东方财富文件（pipeline/export_for_eastmoney.py）

## 2. 目录说明

```
pipeline/       核心管线（Selector 基类、数据抓取、初选、导出）
strategies/     策略目录（b1/、brick/，每个含 selector.py + prompt.md）
agent/          LLM 评审器（base_reviewer.py + local/siliconflow/zhipu/gemini/bailian）
dashboard/      看盘界面与图表导出
config/         全局配置（非 reviewer 专属）
data/           运行数据与结果
```

## 3. 快速开始

### 3.1 克隆项目

```bash
git clone https://github.com/SebastienZh/StockTradebyZ
cd StockTradebyZ
```

### 3.2 安装依赖

```bash
pip install -r requirements.txt
```

### 3.3 设置环境变量

```bash
export TUSHARE_TOKEN="你的TushareToken"
export BAILIAN_API_KEY="你的百炼Key"   # 按需
```

### 3.4 运行

```bash
# 全流程（需先启用 AI 复评）
python run_all.py --ai-review --reviewer bailian --bailian-model kimi-k2.6

# 仅砖型图策略
python run_all.py --strategies brick --ai-review --reviewer bailian --bailian-model kimi-k2.6

# 跳过数据下载
python run_all.py --skip-fetch
```

## 4. 分步运行

```bash
# 步骤1：拉取K线
python -m pipeline.fetch_kline

# 步骤2：量化初选（--strategies 可选：b1, brick, b1,brick）
python -m pipeline.cli preselect
python -m pipeline.cli preselect --strategies brick

# 步骤3：导出K线图
python dashboard/export_kline_charts.py

# 步骤4：AI复评
python agent/bailian/review.py --model kimi-k2.6

# 步骤8：导出东方财富
python pipeline/export_for_eastmoney.py
```

## 5. 选股策略

### B1 策略（KDJ + 知行均线）

4 个 Filter：KDJ 分位过滤 + 知行线条件 + 周线多头排列 + 最大量非阴线

### 砖型图策略（通达信原版公式）

5 个 Filter：砖型图形态（红绿柱 + 增长倍数）+ 知行线位置 + 知行多空条件 + 周线多头排列 + CloseAboveZXDQ

详见 `strategies/b1/prompt.md` 和 `strategies/brick/prompt.md`。

## 6. AI 评分说明

每个策略有独立的 prompt 和评分维度，评审输出为 `{code}_{strategy}.json`。同一股票命中多个策略时分别评审、互不覆盖。

## 7. 常见问题

### Q1：fetch_kline 报 token 错误
检查 TUSHARE_TOKEN 是否已设置。

### Q2：导出图表时报 write_image 错误
`pip install -U kaleido`

### Q3：AI复评失败
检查对应平台 API_KEY，确认网络连接，可增加 request_delay。

## License

CC BY-NC 4.0 — 学习研究可自由使用，禁止商业用途。
