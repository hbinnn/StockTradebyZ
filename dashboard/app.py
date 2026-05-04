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


@st.cache_data(ttl=30)
def _load_pattern_matches(pick_date: str) -> dict[str, list[dict]]:
    """加载完美图形匹配结果，返回 {code: [match, ...]}。"""
    p = _ROOT / "data" / "pattern_matched" / f"matched_{pick_date}.json"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("results", {})


def _load_pattern_yaml() -> dict:
    """加载完美图形案例 YAML（不缓存，确保写入后立即更新）。"""
    p = _ROOT / "config" / "perfect_patterns.yaml"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_pattern_yaml(data: dict) -> None:
    """原子写入完美图形案例 YAML。"""
    import os
    p = _ROOT / "config" / "perfect_patterns.yaml"
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    os.replace(tmp, p)


@st.cache_data(ttl=3600)
def _load_stock_names() -> dict[str, str]:
    """加载股票代码→名称映射。"""
    p = _ROOT / "pipeline" / "stocklist.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, dtype=str)
    names = {}
    for _, row in df.iterrows():
        symbol = str(row.get("symbol", "")).strip().zfill(6)
        name = str(row.get("name", "")).strip()
        if symbol and name:
            names[symbol] = name
    return names


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
pattern_matches = _load_pattern_matches(pick_date) if pick_date else {}
stock_names = _load_stock_names()

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
        # 图形匹配数（≥0.7）
        pms = [m for m in pattern_matches.get(code, []) if m.get("similarity", 0) >= 0.7]
        match_count = len(pms)
        match_str = f"{match_count}例" if match_count > 0 else "—"

        name = stock_names.get(code, "")

        rows.append({
            "代码": code, "名称": name, "策略": s.upper(),
            "收盘": round(c.get("close", 0), 2),
            "评分": score or 0, "判定": verdict, "点评": comment, "匹配": match_str,
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

    # ── 表格（点击左侧复选框选中行查看详情）─────────────────────────────
    st.markdown(f"共 **{len(rows)}** 条　｜　☑️ 勾选左侧复选框查看个股详情")
    st.markdown("")

    display_cols = ["代码", "名称", "策略", "收盘", "评分", "判定", "匹配", "点评"]
    df = pd.DataFrame(rows)[display_cols]

    def _verdict_style(val):
        if val == "PASS": return "background-color:#d4f5e2;color:#1a7f37;font-weight:600"
        elif val == "WATCH": return "background-color:#fff3cd;color:#856404;font-weight:600"
        elif val == "FAIL": return "background-color:#f8d7da;color:#721c24;font-weight:600"
        return ""

    styled = df.style.map(_verdict_style, subset=["判定"])

    event = st.dataframe(
        styled,
        column_config={
            "代码": st.column_config.TextColumn(width="small"),
            "名称": st.column_config.TextColumn(width="small"),
            "策略": st.column_config.TextColumn(width="small"),
            "收盘": st.column_config.NumberColumn(format="%.2f", width="small"),
            "评分": st.column_config.ProgressColumn(format="%.1f", min_value=1, max_value=5, width="medium"),
            "判定": st.column_config.TextColumn(width="small"),
            "匹配": st.column_config.TextColumn(width="small"),
            "点评": st.column_config.TextColumn(width="large"),
        },
        column_order=display_cols,
        hide_index=True,
        use_container_width=True,
        height=min(38 * len(rows) + 38, 420),
        on_select="rerun",
        selection_mode="single-row",
        key=f"table_{tab_key}",
    )

    sel = event.selection.get("rows", []) if hasattr(event, "selection") else []
    if not sel:
        return
    idx = sel[0]
    if idx >= len(rows):
        return
    row = rows[idx]
    code = row["_code"]
    strategy = row["_strategy"]

    st.markdown(f'<hr class="section-divider">', unsafe_allow_html=True)
    name_label = f" · {stock_names.get(code, '')}" if stock_names.get(code) else ""
    st.markdown(f"### 📈 {code}{name_label}　<span class='strategy-badge strategy-{strategy}'>{strategy.upper()}</span>", unsafe_allow_html=True)

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

        # 图形匹配
        matches = pattern_matches.get(code, [])
        if matches:
            good = sorted(
                [m for m in matches if m.get("similarity", 0) >= 0.7],
                key=lambda x: x.get("similarity", 0), reverse=True
            )[:3]
            if good:
                st.markdown("---")
                st.markdown("**🔗 图形匹配**")
                for m in good:
                    sim = m.get("similarity", 0)
                    bar_c = "#28a745" if sim >= 0.8 else "#ffc107"
                    case_code = m.get("case_code", "")
                    case_date = m.get("case_date", "")
                    desc = m.get("case_description", "")
                    st.markdown(
                        f"""<span style="font-weight:600;font-size:0.9rem">{case_code}</span>
                        <span style="color:#636c76;font-size:0.82rem"> {case_date}</span>
                        <span style="color:{bar_c};font-weight:600;font-size:0.85rem;float:right">相似度 {sim:.2f}</span>
                        <div style="color:#636c76;font-size:0.78rem;margin-bottom:4px">{desc[:80]}</div>""",
                        unsafe_allow_html=True
                    )
                    # 加载案例走势图（全量数据计算均线，再截断60根展示）
                    case_df = _load_raw(case_code)
                    if not case_df.empty:
                        case_df["date"] = pd.to_datetime(case_df["date"])
                        try:
                            case_ts = pd.Timestamp(case_date)
                            # 传全量数据给 make_daily_chart，它内部先算均线再 tail
                            full = case_df[case_df["date"] <= case_ts]
                            if len(full) >= 10:
                                fig = make_daily_chart(
                                    full, case_code,
                                    bars=60, height=300,
                                    show_brick=False, show_kdj=False,
                                )
                                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                            else:
                                st.caption(f"（{case_code} 数据不足）")
                        except Exception:
                            st.caption(f"（{case_code} 图表生成失败）")
                    else:
                        st.caption(f"（{case_code} 无日线数据）")


# ── 可复用：图形案例库标签页 ──────────────────────────────────────────────

def _render_pattern_library():
    """渲染完美图形案例库：浏览 + 添加。"""
    st.markdown("## 📐 完美图形案例库")
    st.caption("管理各策略的历史完美图形案例，用于图形相似度匹配")
    st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

    pat_data = _load_pattern_yaml()
    strategies_pat = pat_data.get("strategies", {}) if "strategies" in pat_data else pat_data
    all_pat_strategies = [s for s in strategies_pat if isinstance(strategies_pat.get(s), list)]

    sub_tabs = st.tabs(["📋 浏览案例", "➕ 添加案例"])

    # ── 子页 1: 浏览案例 ──────────────────────────────────────────────────
    with sub_tabs[0]:
        if not all_pat_strategies:
            st.info("暂无案例，请先添加。")
        else:
            view_strat = st.selectbox("策略", all_pat_strategies, key="pat_view_strat",
                                       format_func=lambda x: STRATEGY_LABELS.get(x, x.upper()))
            cases = strategies_pat.get(view_strat, [])
            if not cases:
                st.info(f"策略 {view_strat.upper()} 下暂无案例。")
            else:
                st.markdown(f"共 **{len(cases)}** 个案例　｜　☑️ 勾选查看走势图")

                # 构建表格
                table_rows = []
                for idx, case in enumerate(cases):
                    code = case.get("code", "")
                    table_rows.append({
                        "代码": code, "名称": stock_names.get(code, ""),
                        "日期": case.get("perfect_date", ""),
                        "描述": case.get("description", ""),
                        "_idx": idx,
                    })
                df = pd.DataFrame(table_rows)
                event = st.dataframe(
                    df,
                    column_config={
                        "代码": st.column_config.TextColumn(width="small"),
                        "名称": st.column_config.TextColumn(width="small"),
                        "日期": st.column_config.TextColumn(width="small"),
                        "描述": st.column_config.TextColumn(width="large"),
                    },
                    hide_index=True,
                    use_container_width=True,
                    height=min(38 * len(cases) + 38, 400),
                    on_select="rerun",
                    selection_mode="single-row",
                    key="pat_case_table",
                )
                sel = event.selection.get("rows", []) if hasattr(event, "selection") else []
                if sel and sel[0] < len(cases):
                    case = cases[sel[0]]
                    code = case.get("code", "")
                    pdate = case.get("perfect_date", "")
                    desc = case.get("description", "")
                    st.markdown(f'<hr class="section-divider">', unsafe_allow_html=True)
                    name_lbl = f" · {stock_names.get(code, '')}" if stock_names.get(code) else ""
                    st.markdown(f"### {code}{name_lbl} · {pdate}")
                    st.caption(desc)

                    # 删除按钮
                    if st.button("🗑️ 删除此案例", key=f"pat_del_{sel[0]}"):
                        full_yaml = _load_pattern_yaml()
                        full_yaml.setdefault("strategies", {}).setdefault(view_strat, [])
                        del full_yaml["strategies"][view_strat][sel[0]]
                        _save_pattern_yaml(full_yaml)
                        st.success("已删除，刷新页面生效")
                        st.rerun()

                    case_df = _load_raw(code)
                    if not case_df.empty:
                        case_df["date"] = pd.to_datetime(case_df["date"])
                        try:
                            pts = pd.Timestamp(pdate)
                            full = case_df[case_df["date"] <= pts]
                            if len(full) >= 10:
                                fig = make_daily_chart(full, code, bars=120, height=500,
                                                       show_brick=False, show_kdj=True)
                                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                        except Exception:
                            st.caption("（图表生成失败）")
                    else:
                        st.caption("（无日线数据）")

    # ── 子页 2: 添加案例 ──────────────────────────────────────────────────
    with sub_tabs[1]:
        st.markdown("### 添加新案例")
        col1, col2 = st.columns(2)
        with col1:
            add_strat = st.selectbox("策略", ["b1", "brick", "b2", "b3"],
                                      key="pat_add_strat",
                                      format_func=lambda x: STRATEGY_LABELS.get(x, x.upper()))
            add_code = st.text_input("股票代码（6位）", max_chars=6, key="pat_add_code",
                                      placeholder="例：600519")
        with col2:
            add_date = st.text_input("完美图形日期", key="pat_add_date",
                                      placeholder="YYYY-MM-DD  例：2026-04-30")
        add_desc = st.text_area("案例描述", key="pat_add_desc",
                                 placeholder="描述该完美图形的特征，如：放量上涨缩量回调，白线在黄线上，股价在白黄线之间")

        col_prev, col_save = st.columns([1, 3])
        with col_prev:
            preview = st.button("🔍 预览 K 线", key="pat_preview", use_container_width=True)
        if preview and add_code and add_date:
            df = _load_raw(add_code.strip().zfill(6))
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
                try:
                    pts = pd.Timestamp(add_date.strip())
                    full = df[df["date"] <= pts]
                    if len(full) >= 10:
                        fig = make_daily_chart(full, add_code.strip().zfill(6), bars=60, height=380,
                                               show_brick=False, show_kdj=True)
                        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                    else:
                        st.warning("该日期前数据不足（需至少 10 根 K 线）")
                except Exception as e:
                    st.error(f"日期格式错误：{e}")
            else:
                st.warning(f"未找到 {add_code} 的日线数据")
        with col_save:
            if st.button("💾 保存案例", key="pat_save", type="primary", use_container_width=True):
                if not add_code or not add_date or not add_desc:
                    st.error("请填写所有字段（代码、日期、描述）")
                else:
                    code_clean = add_code.strip().zfill(6)
                    # 检查代码数据存在
                    df_check = _load_raw(code_clean)
                    if df_check.empty:
                        st.error(f"未找到 {code_clean} 的日线数据，请确认代码正确")
                    else:
                        # 加载原始 YAML，追加后保存
                        full_yaml = _load_pattern_yaml()
                        if "strategies" not in full_yaml:
                            full_yaml = {"strategies": {s: [] for s in ["b1", "brick", "b2", "b3"]}}
                        full_yaml.setdefault("strategies", {}).setdefault(add_strat, [])
                        full_yaml["strategies"][add_strat].append({
                            "code": code_clean,
                            "perfect_date": add_date.strip(),
                            "description": add_desc.strip(),
                        })
                        _save_pattern_yaml(full_yaml)
                        st.success(f"✅ 已保存！{code_clean} @ {add_date.strip()} → {add_strat}")
                        st.caption("切换到「浏览案例」标签页查看")


# ── 主入口：动态标签页 ─────────────────────────────────────────────────────

strategies_in_data = sorted(set(c.get("strategy", "") for c in candidates))
tab_names = ["📋 总览"] + [f"{STRATEGY_LABELS.get(s, s.upper())}" for s in strategies_in_data] + ["📐 图形案例库"]

active_tab = st.radio(
    "导航", tab_names, index=0,
    horizontal=True, label_visibility="collapsed",
    key="nav_radio"
)
idx = tab_names.index(active_tab)

if idx == 0:
    _render_strategy_tab(None)
elif idx < len(tab_names) - 1:
    _render_strategy_tab(strategies_in_data[idx - 1])
else:
    _render_pattern_library()
