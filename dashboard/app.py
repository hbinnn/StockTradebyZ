"""
dashboard/app.py
AgentTrader · 今日选股看板 — Streamlit 主入口

启动方式：
    cd <项目根>
    streamlit run dashboard/app.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

_ROOT = Path(__file__).parent.parent
_DASH = Path(__file__).parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_DASH))


# ── 数据加载 ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def _load_cfg() -> dict:
    p = _ROOT / "config" / "dashboard.yaml"
    return yaml.safe_load(open(p, encoding="utf-8")) if p.exists() else {}


def _list_available_dates() -> list[str]:
    """扫描 data/candidates/ 下所有存档日期（降序）。"""
    d = _ROOT / "data" / "candidates"
    if not d.exists():
        return []
    dates = set()
    for f in d.glob("candidates_*.json"):
        # candidates_2026-04-30.json → 2026-04-30
        stem = f.stem
        if stem.startswith("candidates_"):
            dates.add(stem[len("candidates_"):])
    return sorted(dates, reverse=True)


@st.cache_data(ttl=30)
def _load_candidates(pick_date: str = "") -> tuple[list[dict], str]:
    """加载指定日期的候选列表。pick_date 为空则取 latest。"""
    if pick_date:
        p = _ROOT / "data" / "candidates" / f"candidates_{pick_date}.json"
    else:
        cfg = _load_cfg()
        p = _ROOT / cfg.get("paths", {}).get("candidates_latest", "data/candidates/candidates_latest.json")
    if not p.exists():
        return [], ""
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("candidates", []), data.get("pick_date", "")


@st.cache_data(ttl=30)
def _load_suggestion(pick_date: str) -> dict | None:
    p = _ROOT / "data" / "review" / pick_date / "suggestion.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=30)
def _load_review_map(pick_date: str) -> dict[str, dict]:
    """返回 {code: {strategy: review_result, ...}, ...}"""
    review_dir = _ROOT / "data" / "review" / pick_date
    result: dict[str, dict] = {}
    if not review_dir.exists():
        return result
    for f in sorted(review_dir.glob("*.json")):
        if f.name == "suggestion.json":
            continue
        # 文件名格式: {code}_{strategy}.json 或 {code}.json（旧格式）
        stem = f.stem
        if "_" in stem:
            parts = stem.rsplit("_", 1)
            code, strategy = parts[0], parts[1]
        else:
            code, strategy = stem, ""
        with open(f, encoding="utf-8") as fh:
            r = json.load(fh)
        result.setdefault(code, {})[strategy] = r
    return result


@st.cache_data(show_spinner=False)
def _load_raw(code: str) -> pd.DataFrame:
    cfg = _load_cfg()
    raw_dir = _ROOT / cfg.get("paths", {}).get("raw_data_dir", "data/raw")
    csv = raw_dir / f"{code}.csv"
    if not csv.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv)
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


# ── 页面配置 ─────────────────────────────────────────────────────────────────

cfg = _load_cfg()
page_title = cfg.get("server", {}).get("title", "AgentTrader · 今日选股")

st.set_page_config(
    page_title=page_title,
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

css_path = _DASH / "assets" / "style.css"
if css_path.exists():
    st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

from components.charts import make_daily_chart, make_weekly_chart

# ── 图表参数 ─────────────────────────────────────────────────────────────────

chart_cfg = cfg.get("chart", {})
weekly_ma_wins = chart_cfg.get("weekly_ma_windows", [5, 10, 20, 60])
weekly_ma_colors = {int(k): v for k, v in chart_cfg.get("weekly_ma_colors", {}).items()}
vol_up = chart_cfg.get("volume_up_color", "rgba(220,53,69,0.7)")
vol_down = chart_cfg.get("volume_down_color", "rgba(40,167,69,0.7)")

# ── 会话状态 + 侧边栏（日期选择 → 数据加载）────────────────────────────────

available_dates = _list_available_dates()
if "selected_date" not in st.session_state:
    st.session_state["selected_date"] = ""

with st.sidebar:
    st.markdown("## 📈 AgentTrader")

    # 日期选择器
    if available_dates:
        date_options = ["最新"] + available_dates
        cur = st.session_state["selected_date"]
        default_idx = 0 if not cur else (date_options.index(cur) if cur in date_options else 0)
        selected_label = st.selectbox("选股日期", date_options, index=default_idx)
        st.session_state["selected_date"] = "" if selected_label == "最新" else selected_label

selected_date = st.session_state["selected_date"]

# 按选中日期加载数据
candidates, pick_date = _load_candidates(selected_date)
if not pick_date:
    for c in candidates:
        if c.get("date"):
            pick_date = c["date"]
            break

review_map = _load_review_map(pick_date) if pick_date else {}
suggestion = _load_suggestion(pick_date) if pick_date else None

# ── 侧边栏（续）──────────────────────────────────────────────────────────────

with st.sidebar:
    if pick_date:
        st.caption(f"选股日：{pick_date}")
    st.markdown("---")

    # 策略筛选
    all_strategies = sorted(set(c.get("strategy", "") for c in candidates))
    selected_strategies = st.multiselect(
        "策略筛选", all_strategies, default=all_strategies,
        format_func=lambda x: x.upper(),
    )

    # 评分门槛
    if suggestion:
        min_score = st.slider("最低评分", 1.0, 5.0, 3.0, 0.1)
    else:
        min_score = 0
        st.caption("（暂无 AI 复评结果）")

    st.markdown("---")

    # 统计
    filtered = [c for c in candidates if c.get("strategy", "") in selected_strategies]
    b1_count = sum(1 for c in filtered if c.get("strategy") == "b1")
    brick_count = sum(1 for c in filtered if c.get("strategy") == "brick")

    st.markdown("### 📊 统计")
    st.metric("候选总数", len(filtered))
    col1, col2 = st.columns(2)
    col1.metric("B1", b1_count)
    col2.metric("砖型图", brick_count)

    if suggestion:
        recs = suggestion.get("recommendations", [])
        st.metric("AI 推荐", len(recs))


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

_VERDICT_COLORS = {
    "PASS": ("#d4f5e2", "#1a7f37"),
    "WATCH": ("#fff3cd", "#856404"),
    "FAIL": ("#f8d7da", "#721c24"),
}
_SCORE_COLORS = {
    "PASS": "#1a7f37", "WATCH": "#856404", "FAIL": "#721c24", "": "#636c76",
}


def _verdict_badge(verdict: str) -> str:
    if not verdict:
        return ""
    bg, fg = _VERDICT_COLORS.get(verdict, ("#e9ecef", "#636c76"))
    return f"<span style='background:{bg};color:{fg};padding:2px 8px;border-radius:4px;font-weight:600;font-size:0.8rem'>{verdict}</span>"


def _strategy_badge(s: str) -> str:
    colors = {"b1": ("#daeeff", "#0969da"), "brick": ("#d4f5e2", "#1a7f37")}
    bg, fg = colors.get(s, ("#e9ecef", "#636c76"))
    return f"<span style='background:{bg};color:{fg};padding:2px 8px;border-radius:12px;font-weight:600;font-size:0.75rem'>{s.upper()}</span>"


# ── 主体 ─────────────────────────────────────────────────────────────────────

if not candidates:
    st.info("暂无候选股票，请先运行量化初选。")
    st.stop()

label = "历史选股" if selected_date else "今日选股"
st.markdown(f"## 📊 {label} · {pick_date}")

# 聚合候选（按 code 合并多策略）
by_code: dict[str, dict] = {}
for c in candidates:
    s = c.get("strategy", "")
    if s not in selected_strategies:
        continue
    code = c["code"]
    if code not in by_code:
        by_code[code] = {"code": code, "close": c.get("close", 0), "strategies": {}}
    by_code[code]["strategies"][s] = c

codes_sorted = sorted(by_code.keys())

if not codes_sorted:
    st.info("当前筛选条件下无候选股票。")
    st.stop()

# ── 候选列表 ─────────────────────────────────────────────────────────────────

st.markdown(f"共 **{len(codes_sorted)}** 只股票")

for code in codes_sorted:
    info = by_code[code]
    strategies = info["strategies"]

    # 获取最优评分（取所有策略中的最高分）
    best_score = None
    best_verdict = ""
    best_comment = ""
    for s_name in strategies:
        rm = review_map.get(code, {}).get(s_name)
        if rm:
            s = rm.get("total_score", 0)
            if best_score is None or s > best_score:
                best_score = s
                best_verdict = rm.get("verdict", "")
                best_comment = rm.get("comment", "")

    # 评分过滤
    if best_score is not None and best_score < min_score:
        continue

    with st.container():
        st.markdown('<div class="candidate-card">', unsafe_allow_html=True)

        col1, col2, col3, col4 = st.columns([2, 2, 1.5, 3])

        with col1:
            st.markdown(f"### {code}")
            badges = "&nbsp;".join(_strategy_badge(s) for s in strategies)
            st.markdown(badges, unsafe_allow_html=True)

        with col2:
            st.markdown(f"收盘价：**{info['close']:.2f}**")
            for s_name, sc in strategies.items():
                bg_val = sc.get("brick_growth") or (sc.get("extra", {}).get("brick_growth"))
                if bg_val:
                    st.caption(f"砖型增长：{bg_val:.2f}x")

        with col3:
            if best_score is not None:
                color = _SCORE_COLORS.get(best_verdict, "#636c76")
                st.markdown(f"评分：<b style='color:{color};font-size:1.4rem'>{best_score:.1f}</b>", unsafe_allow_html=True)
                st.markdown(_verdict_badge(best_verdict), unsafe_allow_html=True)
            else:
                st.caption("待评审")

        with col4:
            if best_comment:
                st.caption(best_comment[:80] + ("..." if len(best_comment) > 80 else ""))
            # 每个策略的评审详情
            for s_name in strategies:
                rm = review_map.get(code, {}).get(s_name)
                if rm:
                    s = rm.get("total_score", "?")
                    v = rm.get("verdict", "")
                    color = _SCORE_COLORS.get(v, "#636c76")
                    st.caption(f"{s_name}: <b style='color:{color}'>{s}</b> {v}", unsafe_allow_html=True)

        # 展开查看 K 线图
        with st.expander(f"📈 {code} K线图"):
            df_raw = _load_raw(code)
            if df_raw.empty:
                st.warning("无日线数据")
            else:
                bars_val = st.selectbox("K线数量", [60, 120, 250, 0], index=1, key=f"bars_{code}", format_func=lambda x: f"近{x}根" if x else "全部")
                # 砖型图策略候选显示砖型图子图
                has_brick = "brick" in strategies
                fig = make_daily_chart(
                    df_raw, code,
                    volume_up_color=vol_up, volume_down_color=vol_down,
                    bars=bars_val, height=720 if has_brick else 600,
                    show_brick=has_brick,
                    strategy="+".join(strategies.keys()),
                )
                st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

        st.markdown('</div>', unsafe_allow_html=True)
