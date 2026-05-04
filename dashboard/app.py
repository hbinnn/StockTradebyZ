"""
dashboard/app.py
AgentTrader · 量化初选看板 — Streamlit 主入口

启动方式：
    streamlit run dashboard/app.py
"""
from __future__ import annotations

import json, sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import yaml

_ROOT = Path(__file__).parent.parent
_DASH = Path(__file__).parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_DASH))

from components.charts import make_daily_chart
from components.backtest import render_backtest_button

# ── 常量 ────────────────────────────────────────────────────────────────────

STRATEGY_LABELS = {"b1": "B1", "brick": "砖型图", "b2": "B2", "b3": "B3"}
STRATEGY_FEATURES = {
    "b1":    {"show_kdj": True,  "show_brick": True},
    "b2":    {"show_kdj": True,  "show_brick": True},
    "b3":    {"show_kdj": True,  "show_brick": True},
    "brick": {"show_kdj": False, "show_brick": True},
}
VERDICT_COLORS = {"PASS": ("#d4f5e2", "#1a7f37"), "WATCH": ("#fff3cd", "#856404"), "FAIL": ("#f8d7da", "#721c24")}
SCORE_COLORS  = {"PASS": "#1a7f37", "WATCH": "#856404", "FAIL": "#721c24", "": "#636c76"}

# ── 数据加载（缓存）──────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def _load_cfg() -> dict:
    p = _ROOT / "config" / "dashboard.yaml"
    return yaml.safe_load(open(p, encoding="utf-8")) if p.exists() else {}


def _list_available_dates() -> list[str]:
    d = _ROOT / "data" / "candidates"
    if not d.exists():
        return []
    dates = set()
    for f in d.glob("candidates_*.json"):
        stem = f.stem
        if stem.startswith("candidates_"):
            dates.add(stem[len("candidates_"):])
    return sorted(dates, reverse=True)


@st.cache_data(ttl=30)
def _load_candidates(pick_date: str = "") -> tuple[list[dict], str]:
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
    return json.load(open(p, encoding="utf-8")) if p.exists() else None


def _normalize_review(r: dict) -> dict:
    """统一 Schema A（scores 字段）和 Schema B（dimension_scores 字段）。"""
    dims = r.get("dimension_scores") or r.get("scores") or {}
    summary = r.get("summary") or ""
    if not summary:
        # Schema A 无 summary，用 signal_reasoning 截断
        for key in ("signal_reasoning", "trend_reasoning"):
            v = r.get(key, "")
            if v:
                summary = v[:120]
                break
    return {
        "total_score": r.get("total_score"),
        "verdict": r.get("verdict", ""),
        "comment": r.get("comment", ""),
        "summary": summary,
        "dimension_scores": dims,
    }


@st.cache_data(ttl=30)
def _load_review_map(pick_date: str) -> dict[str, dict[str, dict]]:
    """{code: {strategy: normalized_review}}"""
    review_dir = _ROOT / "data" / "review" / pick_date
    result: dict[str, dict[str, dict]] = {}
    if not review_dir.exists():
        return result
    for f in sorted(review_dir.glob("*.json")):
        if f.name == "suggestion.json":
            continue
        stem = f.stem
        if "_" in stem:
            pos = stem.rfind("_")
            code, strategy = stem[:pos], stem[pos+1:]
        else:
            code, strategy = stem, ""
        with open(f, encoding="utf-8") as fh:
            r = json.load(fh)
        result.setdefault(code, {})[strategy] = _normalize_review(r)
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


# ── 页面配置 ────────────────────────────────────────────────────────────────

cfg = _load_cfg()
st.set_page_config(
    page_title=cfg.get("server", {}).get("title", "AgentTrader · 量化初选看板"),
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)
css_path = _DASH / "assets" / "style.css"
if css_path.exists():
    st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

# ── 顶部栏 ──────────────────────────────────────────────────────────────────

available_dates = _list_available_dates()
if "selected_date" not in st.session_state:
    st.session_state["selected_date"] = ""

col_title, col_date = st.columns([4, 1])
with col_title:
    st.markdown("## 📊 AgentTrader · 量化初选看板")
with col_date:
    if available_dates:
        date_options = ["最新"] + available_dates
        cur = st.session_state["selected_date"]
        default_idx = 0 if not cur else (date_options.index(cur) if cur in date_options else 0)
        selected_label = st.selectbox(
            "选股日期", date_options, index=default_idx, key="date_selector", label_visibility="collapsed"
        )
        st.session_state["selected_date"] = "" if selected_label == "最新" else selected_label
    else:
        st.caption("无历史数据")

selected_date = st.session_state["selected_date"]
candidates, pick_date = _load_candidates(selected_date)
if not pick_date and candidates:
    pick_date = candidates[0].get("date", "")

review_map = _load_review_map(pick_date) if pick_date else {}
suggestion = _load_suggestion(pick_date) if pick_date else None

if not candidates:
    st.info("暂无候选股票，请先运行量化初选。")
    st.stop()

st.caption(f"选股日：{pick_date}　｜　共 {len(candidates)} 条候选记录")
st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

# ── 图表参数 ────────────────────────────────────────────────────────────────

chart_cfg = cfg.get("chart", {})
vol_up   = chart_cfg.get("volume_up_color", "rgba(220,53,69,0.7)")
vol_down = chart_cfg.get("volume_down_color", "rgba(40,167,69,0.7)")


# ── 可复用：策略标签页 ──────────────────────────────────────────────────────

def _render_strategy_tab(strategy_name: str | None):
    """渲染一个策略标签页的全部内容。strategy_name=None 表示总览。"""
    tab_key = strategy_name or "overview"

    # 筛选候选
    if strategy_name is None:
        tab_candidates = candidates
    else:
        tab_candidates = [c for c in candidates if c.get("strategy") == strategy_name]
    if not tab_candidates:
        st.info("该策略无候选股票。")
        return

    # ── 统计卡片 ─────────────────────────────────────────────────────────
    stats = {"total": len(tab_candidates), "PASS": 0, "WATCH": 0, "FAIL": 0, "no_review": 0}
    for c in tab_candidates:
        code, s = c["code"], c.get("strategy", "")
        rm = review_map.get(code, {}).get(s)
        if rm:
            v = rm.get("verdict", "")
            stats[v] = stats.get(v, 0) + 1
        else:
            stats["no_review"] += 1

    card_configs = [
        ("总计", "total", "#1f2328", "#e9ecef"),
        ("PASS", "PASS", "#1a7f37", "#d4f5e2"),
        ("WATCH", "WATCH", "#856404", "#fff3cd"),
        ("FAIL", "FAIL", "#721c24", "#f8d7da"),
    ]
    cols = st.columns(4)
    for col, (label, key, fg, bg) in zip(cols, card_configs):
        with col:
            st.markdown(
                f"""<div style="background:{bg};border-radius:10px;padding:14px 18px;text-align:center">
                <div style="font-size:1.8rem;font-weight:700;color:{fg}">{stats[key]}</div>
                <div style="font-size:0.78rem;color:#636c76">{label}</div></div>""",
                unsafe_allow_html=True
            )

    st.markdown("")

    # ── 筛选 ─────────────────────────────────────────────────────────────
    with st.expander("🔍 筛选与排序", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            verdicts = ["全部"] + sorted({v for c in tab_candidates for v in [
                (review_map.get(c["code"], {}).get(c.get("strategy", ""), {}).get("verdict") or "")
            ] if v})
            sel_verdict = st.selectbox("判定", verdicts, key=f"fv_{tab_key}")
        with c2:
            score_range = st.slider("评分区间", 1.0, 5.0, (1.0, 5.0), 0.1, key=f"fs_{tab_key}")
        with c3:
            sort_options = ["评分↓", "评分↑", "收盘价↓", "收盘价↑"]
            sort_by = st.selectbox("排序", sort_options, key=f"so_{tab_key}")

    # 应用筛选
    rows = []
    for c in tab_candidates:
        code = c["code"]
        s = c.get("strategy", "")
        rm = review_map.get(code, {}).get(s)
        score = rm.get("total_score") if rm else None
        verdict = rm.get("verdict", "") if rm else ""
        comment = (rm.get("comment", "") or "")[:80] if rm else ""
        # 判定过滤
        if sel_verdict != "全部" and verdict != sel_verdict:
            continue
        # 评分过滤
        sc = score or 0
        if sc < score_range[0] or sc > score_range[1]:
            continue
        rows.append({
            "代码": code, "策略": s.upper(), "收盘": round(c.get("close", 0), 2),
            "评分": score or 0, "判定": verdict, "点评": comment,
            "_code": code, "_strategy": s,
        })

    # 排序
    if sort_by == "评分↓":
        rows.sort(key=lambda r: r["评分"], reverse=True)
    elif sort_by == "评分↑":
        rows.sort(key=lambda r: r["评分"])
    elif sort_by == "收盘价↓":
        rows.sort(key=lambda r: r["收盘"], reverse=True)
    elif sort_by == "收盘价↑":
        rows.sort(key=lambda r: r["收盘"])

    if not rows:
        st.info("筛选条件下无候选股票。")
        return

    # ── HTML 表格（代码可点击跳转详情）─────────────────────────────────────
    st.markdown(f"共 **{len(rows)}** 条　｜　💡 点击股票代码查看详情")
    st.markdown("")

    v_colors = {"PASS": ("#d4f5e2", "#1a7f37"), "WATCH": ("#fff3cd", "#856404"), "FAIL": ("#f8d7da", "#721c24")}

    html_rows = []
    for r in rows:
        code = r["代码"]
        strat = r["策略"]
        score = r["评分"]
        verdict = r["判定"]
        comment = r["点评"]
        close_p = r["收盘"]
        bg, fg = v_colors.get(verdict, ("#f0f0f0", "#666"))
        score_bar_pct = min(max((score or 0) / 5.0 * 100, 2), 100)
        score_color = "#28a745" if score >= 4 else ("#ffc107" if score >= 3 else "#dc3545")

        # 评分小进度条
        bar_html = f"""<div style="display:flex;align-items:center;gap:6px">
            <span style="font-weight:600;color:{score_color};min-width:28px">{score:.1f}</span>
            <div style="flex:1;background:#e9ecef;border-radius:3px;height:5px;min-width:40px">
            <div style="background:{score_color};width:{score_bar_pct}%;height:5px;border-radius:3px"></div></div></div>"""

        # 判定色标
        v_html = f'<span style="background:{bg};color:{fg};padding:2px 7px;border-radius:4px;font-weight:600;font-size:0.78rem">{verdict}</span>' if verdict else "—"

        # 代码可点击链接
        code_link = f'<a href="?code={code}&strat={strat}&tab={tab_key}" style="color:#0969da;font-weight:700;text-decoration:none;font-size:0.92rem" title="点击查看{code}详情">{code}</a>'

        html_rows.append(
            f"""<tr style="border-bottom:1px solid #e9ecef">
            <td style="padding:6px 8px;white-space:nowrap">{code_link}</td>
            <td style="padding:6px 8px;font-size:0.82rem;color:#636c76;white-space:nowrap">{strat}</td>
            <td style="padding:6px 8px;text-align:right;font-size:0.85rem;white-space:nowrap">{close_p:.2f}</td>
            <td style="padding:6px 8px;min-width:100px">{bar_html}</td>
            <td style="padding:6px 8px;white-space:nowrap">{v_html}</td>
            <td style="padding:6px 8px;font-size:0.82rem;color:#636c76;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{comment}</td>
            </tr>"""
        )

    table_html = f"""<table style="width:100%;border-collapse:collapse;font-size:0.88rem">
    <thead><tr style="border-bottom:2px solid #d0d7de;color:#636c76;font-size:0.8rem">
    <th style="text-align:left;padding:6px 8px">代码</th>
    <th style="text-align:left;padding:6px 8px">策略</th>
    <th style="text-align:right;padding:6px 8px">收盘</th>
    <th style="text-align:left;padding:6px 8px">评分</th>
    <th style="text-align:left;padding:6px 8px">判定</th>
    <th style="text-align:left;padding:6px 8px">点评</th>
    </tr></thead>
    <tbody>{''.join(html_rows)}</tbody></table>"""

    st.markdown(table_html, unsafe_allow_html=True)

    # ── 通过 URL 参数跳转个股详情 ──────────────────────────────────────────
    qp = st.query_params
    sel_code = qp.get("code", "")
    sel_strat = qp.get("strat", "")
    sel_tab  = qp.get("tab", "")

    if not sel_code or sel_tab != tab_key:
        return
    # 在当前标签页的 rows 中查找选中股票
    sel_row = next((r for r in rows if r["_code"] == sel_code and r["_strategy"] == sel_strat), None)
    if not sel_row:
        return
    row = sel_row
    code = row["_code"]
    strategy = row["_strategy"]
    # 清除 URL 参数（避免刷新后仍展开）
    st.query_params.clear()

    st.markdown(f'<hr class="section-divider">', unsafe_allow_html=True)
    st.markdown(f"### 📈 {code}　<span class='strategy-badge strategy-{strategy}'>{strategy.upper()}</span>", unsafe_allow_html=True)

    feats = STRATEGY_FEATURES.get(strategy, {"show_kdj": False, "show_brick": False})
    show_kdj = feats.get("show_kdj", False)
    show_brick = feats.get("show_brick", False)

    col_chart, col_review = st.columns([3, 2])

    with col_chart:
        df_raw = _load_raw(code)
        if df_raw.empty:
            st.warning("无日线数据")
        else:
            bars_val = st.selectbox(
                "K线数量", [60, 120, 250, 0], index=1, key=f"bars_{tab_key}_{code}",
                format_func=lambda x: f"近{x}根" if x else "全部"
            )
            chart_height = 560 + (130 if show_kdj else 0) + (100 if show_brick else 0)
            fig = make_daily_chart(
                df_raw, code,
                volume_up_color=vol_up, volume_down_color=vol_down,
                bars=bars_val, height=chart_height,
                show_brick=show_brick, show_kdj=show_kdj,
                strategy=strategy.upper(),
            )
            st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

    # ── AI 评审详情 ──────────────────────────────────────────────────────
    with col_review:
        rm = review_map.get(code, {}).get(strategy)
        if not rm:
            st.info("暂无 AI 复评结果")
            st.caption(f"收盘价：{row['收盘']}")
        else:
            score = rm.get("total_score", 0)
            verdict = rm.get("verdict", "")
            verdict_color = SCORE_COLORS.get(verdict, "#636c76")

            st.markdown(
                f"""<div class="score-display">
                <span class="score-number" style="color:{verdict_color}">{score:.1f}</span>
                <span class="verdict-badge" style="background:{verdict_color}">{verdict}</span>
                </div>""",
                unsafe_allow_html=True
            )

            summary = rm.get("summary", "")
            if summary:
                st.caption(f"*{summary[:150]}*")

            with st.expander("📝 完整点评", expanded=False):
                st.markdown(rm.get("comment", "—"))

            dims = rm.get("dimension_scores", {})
            if dims:
                st.markdown("**各维度评分**")
                for dim, info in dims.items():
                    if isinstance(info, dict):
                        ds = float(info.get("score", 0))
                        dr = info.get("reason", "")[:80]
                    else:
                        ds = float(info)
                        dr = ""
                    bar_color = "#dc3545" if ds < 2 else ("#ffc107" if ds < 3 else "#28a745")
                    pct = max(ds / 5.0 * 100, 2)
                    st.markdown(
                        f"""<div style="margin:4px 0">
                        <div style="display:flex;justify-content:space-between;font-size:0.82rem">
                        <span>{dim}</span><span style="color:{bar_color};font-weight:600">{ds:.0f}</span></div>
                        <div style="background:#e9ecef;border-radius:4px;height:6px">
                        <div style="background:{bar_color};width:{pct}%;height:6px;border-radius:4px"></div></div>
                        <div style="font-size:0.74rem;color:#636c76">{dr}</div></div>""",
                        unsafe_allow_html=True
                    )

        # 回测按钮
        st.markdown("")
        render_backtest_button(code, strategy, pick_date, key=f"bt_{tab_key}_{code}")

        # 候选信息
        cand = next((c for c in candidates if c.get("code")==code and c.get("strategy")==strategy), None)
        if cand:
            extra = cand.get("extra", {})
            if extra.get("brick_growth"):
                st.caption(f"砖型增长：{extra['brick_growth']:.2f}x")


# ── 主入口：动态标签页 ─────────────────────────────────────────────────────

strategies_in_data = sorted(set(c.get("strategy", "") for c in candidates))
tab_names = ["📋 总览"] + [f"{STRATEGY_LABELS.get(s, s.upper())}" for s in strategies_in_data]
tabs = st.tabs(tab_names)

for i, tab in enumerate(tabs):
    with tab:
        if i == 0:
            _render_strategy_tab(None)
        else:
            _render_strategy_tab(strategies_in_data[i - 1])
