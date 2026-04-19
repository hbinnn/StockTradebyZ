# AgentTrader

一个面向 A 股的半自动选股项目：

- 使用 Tushare 拉取股票日线数据
- 用量化规则做初选（B1策略）
- 导出候选股票 K 线图
- 调用智谱 GLM-4.6V 对图表进行 AI 复评打分
- 将评分结果叠加到K线图上

---

## 更新说明

- 推翻旧版选股模式
- 新加入AI看图打分精选功能
- 支持智谱GLM-4.6V和Gemini两种AI模型
- 新增评分叠加功能，直接在K线图上显示评分

---

## 1. 项目流程

完整流程对应 [run_all.py](run_all.py)：

1. 下载 K 线数据（pipeline.fetch_kline）
2. 量化初选（pipeline.cli preselect）
3. 导出候选图表（dashboard/export_kline_charts.py）
4. AI 图表复评（agent/zhipu_review.py 或 agent/gemini_review.py）
5. 叠加评分到K线图（dashboard/overlay_score_to_chart.py）
6. 打印推荐结果（读取 suggestion.json）

输出主链路：

- data/raw：原始日线 CSV
- data/candidates：初选候选列表
- data/kline/日期：候选图表（含评分叠加）
- data/review/日期：AI 单股评分与汇总建议

---

## 2. 目录说明

- [pipeline](pipeline)：数据抓取与量化初选
- [dashboard](dashboard)：看盘界面与图表导出
- [agent](agent)：LLM 评审逻辑
- [config](config)：抓取、初选、AI复评配置
- [data](data)：运行数据与结果
- [run_all.py](run_all.py)：全流程一键入口

---

## 3. 快速开始

### 3.1 克隆项目

~~~bash
git clone https://github.com/SebastienZh/StockTradebyZ
cd StockTradebyZ
~~~

### 3.2 安装依赖

~~~bash
pip install -r requirements.txt
~~~

### 3.3 设置环境变量

Windows PowerShell：

~~~powershell
[Environment]::SetEnvironmentVariable("TUSHARE_TOKEN", "你的TushareToken", "User")
[Environment]::SetEnvironmentVariable("ZHIPU_API_KEY", "你的智谱ApiKey", "User")
~~~

写入后重开终端，环境变量才会在新会话中生效。

### 3.4 运行一键脚本

~~~bash
python run_all.py
~~~

常用参数：

~~~bash
python run_all.py --skip-fetch
python run_all.py --start-from 3
~~~

参数说明：

- --skip-fetch：跳过数据下载，直接进入初选
- --start-from N：从第 N 步开始执行

---

## 4. 分步运行攻略

### 步骤 1：拉取 K 线

~~~bash
python -m pipeline.fetch_kline
~~~

### 步骤 2：量化初选

~~~bash
python -m pipeline.cli preselect
python -m pipeline.cli preselect --date 2026-04-17
~~~

### 步骤 3：导出候选图表

~~~bash
python dashboard/export_kline_charts.py
~~~

### 步骤 4：AI 图表复评（智谱）

~~~bash
export ZHIPU_API_KEY='你的智谱key' && python agent/zhipu_review.py
~~~

或使用Gemini：

~~~bash
export GEMINI_API_KEY='你的gemini-key' && python agent/gemini_review.py
~~~

### 步骤 5：叠加评分到K线图

~~~bash
python dashboard/overlay_score_to_chart.py
~~~

---

## 5. B1 选股策略

B1策略是基于KDJ指标和知行均线的波段选股策略，包含4个Filter：

1. **KDJ分位过滤**：J值 < 15 或处于10%历史分位
2. **知行均线条件**：收盘价 > 知行短线 且 知行多空 > 知行短线
3. **周线均线多头**：周线 MA5 > MA10 > MA20
4. **最大量日非阴线**：近20日最大量那天不是阴线

详见 [DOCUMENTATION.md](DOCUMENTATION.md) 第五章。

---

## 6. AI 评分说明

AI复评从4个维度分析股票：

| 维度 | 权重 | 说明 |
|------|------|------|
| trend_structure | 0.20 | 均线多头排列健康度 |
| price_position | 0.20 | 相对历史高点的位置 |
| volume_behavior | 0.30 | 上涨放量、回调缩量健康度 |
| previous_abnormal_move | 0.30 | 主力建仓痕迹 |

总分范围 1.0~5.0，计算公式：
```
total_score = trend_structure×0.20 + price_position×0.20 + volume_behavior×0.30 + previous_abnormal_move×0.30
```

判定规则：PASS(≥4.0) / WATCH(3.2~4.0) / REJECT(<3.2)

---

## 7. 常见问题

### Q1：fetch_kline 报 token 错误

检查 TUSHARE_TOKEN 是否已设置，确认 token 有效且账号权限正常。

### Q2：导出图表时报 write_image 错误

确认已安装 kaleido：`pip install -U kaleido`

### Q3：AI复评失败

- 检查 API_KEY 是否设置
- 网络连接是否正常
- 可尝试增加 request_delay 间隔

### Q4：评分总分超过5分

这是GLM返回的简单相加值而非加权值，已修改 prompt.md 使用加权计算公式，重新运行复评即可。

---

## License

本项目采用 CC BY-NC 4.0 协议发布。

- 允许：学习、研究、非商业用途的使用与分发
- 禁止：任何形式的商业使用、出售或以盈利为目的的部署
- 要求：转载或引用须注明原作者与来源

Copyright © 2026 SebastienZh. All rights reserved.
