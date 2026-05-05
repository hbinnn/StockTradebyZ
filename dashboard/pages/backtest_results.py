"""
dashboard/pages/backtest_results.py
Streamlit 回测结果交互看板页面。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

STRATEGY_LABELS = {"b1": "B1 (KDJ+均线)", "brick": "砖型图", "b2": "B2 (突破)", "b3": "B3 (休整)"}

st.set_page_config(
    page_title="回测结果 - AgentTrader",
    page_icon="",
    layout="wide",
)

st.title("回测结果")

# ── 扫描已有回测结果 ─────────────────────────────────────────────────────

def _list_backtest_runs() -> list[tuple[str, Path, str, str, str]]:
    """返回 [(label, path, strategy, start_date, end_date), ...]"""
    d = _PROJECT_ROOT / "data" / "backtest"
    if not d.exists():
        return []
    runs: list[tuple[str, Path, str, str, str]] = []
    for rd in sorted(d.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        mp = rd / "metrics.json"
        if not mp.exists():
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            continue
        strategy = m.get("strategy", "?")
        strategy_label = STRATEGY_LABELS.get(strategy, strategy)
        start = m.get("start_date", "") or ""
        end = m.get("end_date", "") or ""
        label = f"{strategy_label}  [{start} ~ {end}]  —  {rd.name}"
        runs.append((label, rd, strategy, start, end))
    return runs


runs = _list_backtest_runs()

if not runs:
    st.info("尚无回测结果。请先运行回测：")
    st.code("python -m pipeline.cli backtest --config config/backtest.yaml", language="bash")
    st.stop()

# ── 选择运行 ─────────────────────────────────────────────────────────────

run_labels = [r[0] for r in runs]
run_index = st.sidebar.selectbox("选择回测运行", range(len(run_labels)),
                                 format_func=lambda i: run_labels[i])
selected_label, selected_dir, strategy_name, ds_start, ds_end = runs[run_index]

# ── 加载数据 ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_run(run_dir: Path) -> dict:
    data = {}
    for fname in ("metrics.json", "config.json", "signal_stats.json"):
        p = run_dir / fname
        if p.exists():
            data[fname] = json.loads(p.read_text(encoding="utf-8"))

    nav_path = run_dir / "nav_history.csv"
    if nav_path.exists():
        data["nav"] = pd.read_csv(nav_path, parse_dates=["date"]).set_index("date")

    trades_path = run_dir / "trades.csv"
    if trades_path.exists():
        data["trades"] = pd.read_csv(trades_path, dtype={"code": str})

    return data


data = load_run(selected_dir)
metrics = data.get("metrics.json", {})
config = data.get("config.json", {})
nav_df = data.get("nav")
trades_df = data.get("trades")

strategy_display = STRATEGY_LABELS.get(strategy_name, strategy_name)

# ── 策略信息头 ─────────────────────────────────────────────────────────────

st.subheader(f"{strategy_display}")
st.caption(
    f"回测区间: {ds_start or config.get('start_date', '?')} ~ {ds_end or config.get('end_date', '?')}  |  "
    f"初始资金: ¥{config.get('initial_capital', 0):,.0f}  |  "
    f"持仓上限: {config.get('max_positions', '?')} 只  |  "
    f"持有天数: {config.get('hold_days', '?')}"
)

# ── KPI 卡片 ─────────────────────────────────────────────────────────────

st.subheader("关键指标")

kpi_cols = st.columns(5)
kpi_cols[0].metric("年化收益率", f"{metrics.get('annualized_return', 0) * 100:.1f}%")
kpi_cols[1].metric("夏普比率", f"{metrics.get('sharpe_ratio', 0):.2f}")
kpi_cols[2].metric("最大回撤", f"{metrics.get('max_drawdown', 0) * 100:.1f}%")
kpi_cols[3].metric("胜率", f"{metrics.get('win_rate', 0) * 100:.1f}%")
kpi_cols[4].metric("总交易", str(metrics.get("total_trades", 0)))

st.divider()

# ── NAV 净值曲线 ─────────────────────────────────────────────────────────

if nav_df is not None and not nav_df.empty:
    st.subheader("净值曲线 & 回撤")
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            vertical_spacing=0.08,
            row_heights=[0.7, 0.3],
        )

        # NAV
        fig.add_trace(
            go.Scatter(
                x=nav_df.index, y=nav_df["total_nav"],
                mode="lines", name="净值",
                line=dict(color="#16213e", width=1.5),
            ),
            row=1, col=1,
        )

        # 初始资金参考线
        init_cap = config.get("initial_capital", nav_df["total_nav"].iloc[0])
        fig.add_hline(
            y=init_cap, line_dash="dash", line_color="gray",
            annotation_text="初始资金", row=1, col=1,
        )

        # 回撤
        nav = nav_df["total_nav"].values
        peak = pd.Series(nav).expanding().max().values
        dd = (nav - peak) / peak * 100
        fig.add_trace(
            go.Scatter(
                x=nav_df.index, y=dd,
                mode="lines", name="回撤 %",
                fill="tozeroy", fillcolor="rgba(192, 57, 43, 0.15)",
                line=dict(color="#c0392b", width=1),
            ),
            row=2, col=1,
        )
        fig.add_hline(y=0, line_dash="dash", line_color="gray", row=2, col=1)

        fig.update_layout(
            height=500, showlegend=False,
            margin=dict(l=20, r=20, t=20, b=20),
        )
        fig.update_yaxes(title_text="净值", row=1, col=1)
        fig.update_yaxes(title_text="回撤 %", row=2, col=1)

        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        st.line_chart(nav_df["total_nav"])
        st.caption("安装 plotly 以获取更好的图表: pip install plotly")

    st.divider()

# ── 月度收益热力图 ───────────────────────────────────────────────────────

mr = metrics.get("monthly_returns", {})
if mr:
    st.subheader("月度收益")
    try:
        mr_df = pd.DataFrame(
            [{"date": pd.Timestamp(k), "return": v * 100} for k, v in mr.items()]
        ).set_index("date")
        mr_df["year"] = mr_df.index.year.astype(str)
        mr_df["month"] = mr_df.index.month

        pivot = mr_df.pivot_table(values="return", index="year", columns="month", aggfunc="sum")

        # 文本标注热力图
        month_names = ["1月","2月","3月","4月","5月","6月","7月","8月","9月","10月","11月","12月"]
        pivot.columns = [month_names[i-1] for i in pivot.columns]
        # 格式化为字符串显示
        styled = pivot.map(lambda x: f"{x:+.1f}%" if pd.notna(x) else "")
        st.dataframe(styled, use_container_width=True)
    except Exception as e:
        st.caption(f"月度收益数据异常: {e}")
    st.divider()

# ── 年度收益 ─────────────────────────────────────────────────────────────

yr = metrics.get("yearly_returns", {})
if yr:
    st.subheader("年度收益")
    yr_df = pd.DataFrame(
        {"年份": [k[:4] for k in yr.keys()], "收益率": [v * 100 for v in yr.values()]}
    ).set_index("年份")

    st.bar_chart(yr_df, use_container_width=True)
    st.divider()

# ── 交易明细 ─────────────────────────────────────────────────────────────

if trades_df is not None and not trades_df.empty:
    st.subheader("交易明细")

    # 筛选器
    col1, col2 = st.columns(2)
    with col1:
        min_pnl = st.number_input("最低盈亏 (元)", value=None, step=100.0,
                                  placeholder="全部")
    with col2:
        strategy_filter = st.multiselect(
            "策略筛选",
            options=trades_df["strategy"].unique().tolist() if "strategy" in trades_df.columns else [],
            default=[],
        )

    filtered = trades_df.copy()
    if min_pnl is not None:
        filtered = filtered[filtered["pnl"] >= min_pnl]
    if strategy_filter and "strategy" in filtered.columns:
        filtered = filtered[filtered["strategy"].isin(strategy_filter)]

    # 格式化 + 中文表头
    COLUMN_MAP = {
        "code": "代码", "entry_date": "入场日", "exit_date": "出场日",
        "entry_price": "入场价", "exit_price": "出场价",
        "shares": "股数", "pnl": "盈亏", "pnl_pct": "盈亏%",
        "holding_days": "持仓天数", "strategy": "策略",
    }
    display_cols = ["code", "entry_date", "exit_date", "entry_price", "exit_price",
                    "shares", "pnl", "pnl_pct", "holding_days"]
    display_cols = [c for c in display_cols if c in filtered.columns]
    display = filtered[display_cols].copy()
    if "pnl_pct" in display.columns:
        display["pnl_pct"] = (display["pnl_pct"] * 100).round(2).astype(str) + "%"
    if "pnl" in display.columns:
        display["pnl"] = display["pnl"].round(0).astype(int)
    # 重命名为中文
    display.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in display.columns}, inplace=True)
    # 排序用英文列名（rename前先排）
    sort_col = "盈亏" if "pnl" in filtered.columns else None

    st.dataframe(
        display.sort_values(sort_col, ascending=False) if sort_col and sort_col in display.columns else display,
        use_container_width=True,
        height=400,
    )

    # 统计摘要
    total_pnl = filtered["pnl"].sum() if "pnl" in filtered.columns else 0
    win_count = (filtered["pnl"] > 0).sum() if "pnl" in filtered.columns else 0
    st.caption(
        f"筛选结果: {len(filtered)} 笔交易 | 总盈亏: ¥{total_pnl:,.0f} | 盈利: {win_count} 笔 ({(win_count/max(len(filtered),1))*100:.0f}%)"
    )
