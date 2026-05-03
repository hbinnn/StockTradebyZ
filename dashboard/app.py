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
    strategy_labels = {"b1": "B1", "brick": "砖型图", "b2": "B2", "b3": "B3"}

    st.markdown("### 📊 统计")
    st.metric("候选总数", len(filtered))
    counts = {}
    for c in filtered:
        s = c.get("strategy", "")
        counts[s] = counts.get(s, 0) + 1
    cols = st.columns(len(counts) if counts else 1)
    for i, (s, n) in enumerate(sorted(counts.items())):
        label = strategy_labels.get(s, s.upper())
        cols[i].metric(label, n)

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
    colors = {"b1": ("#daeeff", "#0969da"), "brick": ("#d4f5e2", "#1a7f37"), "b2": ("#fef3c7", "#b45309"), "b3": ("#e8daef", "#6c3483")}
    bg, fg = colors.get(s, ("#e9ecef", "#636c76"))
    return f"<span style='background:{bg};color:{fg};padding:2px 8px;border-radius:12px;font-weight:600;font-size:0.75rem'>{s.upper()}</span>"


# ── 主体 ─────────────────────────────────────────────────────────────────────

if not candidates:
    st.info("暂无候选股票，请先运行量化初选。")
    st.stop()

label = "历史选股" if selected_date else "今日选股"
st.markdown(f"## 📊 {label} · {pick_date}")

# ── Level 1: 策略总览卡片 ──────────────────────────────────────────────────

strategy_labels_map = {"b1": "B1", "brick": "砖型图", "b2": "B2", "b3": "B3"}
filtered_cs = [c for c in candidates if c.get("strategy", "") in selected_strategies]

# 按策略统计
strat_stats: dict[str, dict] = {}
for c in filtered_cs:
    s = c.get("strategy", "")
    if s not in strat_stats:
        strat_stats[s] = {"total": 0, "PASS": 0, "WATCH": 0, "FAIL": 0, "no_review": 0}
    strat_stats[s]["total"] += 1
    code = c["code"]
    rm = review_map.get(code, {}).get(s)
    if rm:
        v = rm.get("verdict", "")
        strat_stats[s][v] = strat_stats[s].get(v, 0) + 1
    else:
        strat_stats[s]["no_review"] += 1

total_count = len(filtered_cs)
pass_count = sum(v["PASS"] for v in strat_stats.values())
watch_count = sum(v["WATCH"] for v in strat_stats.values())
fail_count = sum(v["FAIL"] for v in strat_stats.values())

cols = st.columns(len(strat_stats) + 1 if strat_stats else 1)
cols[0].metric("总计", total_count, help=f"PASS:{pass_count} WATCH:{watch_count} FAIL:{fail_count}")
for i, (s, stats) in enumerate(sorted(strat_stats.items())):
    label_s = strategy_labels_map.get(s, s.upper())
    detail = f"PASS:{stats['PASS']} WATCH:{stats['WATCH']} FAIL:{stats['FAIL']}"
    if stats["no_review"] > 0:
        detail += f" 待评:{stats['no_review']}"
    cols[i + 1].metric(label_s, stats["total"], help=detail)

st.markdown("---")

# ── 构建表格数据 ────────────────────────────────────────────────────────────

table_rows = []
for c in filtered_cs:
    code = c["code"]
    s = c.get("strategy", "")
    close_val = c.get("close", 0)
    rm = review_map.get(code, {}).get(s)
    score = rm.get("total_score") if rm else None
    verdict = rm.get("verdict", "") if rm else ""
    comment = (rm.get("comment", "") or "")[:60] if rm else ""

    if min_score > 0 and (score is None or score < min_score):
        continue

    table_rows.append({
        "代码": code,
        "策略": s.upper(),
        "收盘": f"{close_val:.2f}",
        "评分": f"{score:.1f}" if score is not None else "—",
        "判定": verdict,
        "点评": comment,
        "_code": code, "_strategy": s, "_score": score or 0, "_verdict": verdict,
    })

if not table_rows:
    st.info("当前筛选条件下无候选股票。")
    st.stop()

# 按评分降序排列
table_rows.sort(key=lambda r: r["_score"], reverse=True)

df_table = pd.DataFrame(table_rows)

# 判定列色标
def _highlight_verdict(val):
    colors = {"PASS": "background-color:#d4f5e2;color:#1a7f37;font-weight:600",
              "WATCH": "background-color:#fff3cd;color:#856404;font-weight:600",
              "FAIL": "background-color:#f8d7da;color:#721c24;font-weight:600"}
    return colors.get(val, "")

styled = df_table.style.map(_highlight_verdict, subset=["判定"])

st.markdown(f"共 **{len(table_rows)}** 条记录")

# ── Level 2: 数据表格 ──────────────────────────────────────────────────────

event = st.dataframe(
    styled,
    column_config={
        "代码": st.column_config.TextColumn(width="small"),
        "策略": st.column_config.TextColumn(width="small"),
        "收盘": st.column_config.TextColumn(width="small"),
        "评分": st.column_config.TextColumn(width="small"),
        "判定": st.column_config.TextColumn(width="small"),
        "点评": st.column_config.TextColumn(width="large"),
    },
    hide_index=True,
    use_container_width=True,
    height=min(38 * len(table_rows) + 38, 500),
    on_select="rerun",
    selection_mode="single-row",
)

# ── Level 3: 个股详情 ──────────────────────────────────────────────────────

selected_rows = event.selection.get("rows", []) if hasattr(event, "selection") else []
selected_idx = selected_rows[0] if selected_rows else None

if selected_idx is not None and selected_idx < len(table_rows):
    row = table_rows[selected_idx]
    code = row["_code"]
    strategy = row["_strategy"]

    st.markdown("---")
    st.markdown(f"### 📈 {code}  [{strategy.upper()}]")

    df_raw = _load_raw(code)
    if df_raw.empty:
        st.warning("无日线数据")
    else:
        col_chart, col_review = st.columns([3, 2])

        with col_chart:
            bars_val = st.selectbox("K线数量", [60, 120, 250, 0], index=1, key=f"bars_{code}_{strategy}",
                                    format_func=lambda x: f"近{x}根" if x else "全部")
            has_brick = "brick" in [s.get("strategy","") for s in filtered_cs if s.get("code")==code]
            has_kdj = strategy in ("b1", "b2", "b3")
            fig = make_daily_chart(
                df_raw, code,
                volume_up_color=vol_up, volume_down_color=vol_down,
                bars=bars_val, height=720 if (has_brick or has_kdj) else 560,
                show_brick=True, show_kdj=True,
                strategy=strategy.upper(),
            )
            st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

        with col_review:
            rm = review_map.get(code, {}).get(strategy)
            if rm:
                score = rm.get("total_score", "?")
                verdict = rm.get("verdict", "")
                color = _SCORE_COLORS.get(verdict, "#636c76")
                st.markdown(f"**评分**：<span style='font-size:1.6rem;color:{color};font-weight:700'>{score}</span>&nbsp;{_verdict_badge(verdict)}", unsafe_allow_html=True)
                st.caption(rm.get("summary", ""))
                st.markdown("**点评**")
                st.markdown(rm.get("comment", "—"))

                dims = rm.get("dimension_scores", rm.get("scores", {}))
                if dims:
                    st.markdown("**各维度评分**")
                    for dim, info in dims.items():
                        if isinstance(info, dict):
                            ds = info.get("score", "?")
                            dr = info.get("reason", "")
                            st.caption(f"{dim}: **{ds}** {dr[:60]}")
                        else:
                            st.caption(f"{dim}: **{info}**")
            else:
                st.info("暂无 AI 复评结果")
                st.caption(f"收盘价：{row['收盘']}")

            # 策略候选信息
            cand = next((c for c in filtered_cs if c.get("code")==code and c.get("strategy")==strategy), None)
            if cand:
                extra = cand.get("extra", {})
                if extra.get("brick_growth"):
                    st.caption(f"砖型增长：{extra['brick_growth']:.2f}x")
