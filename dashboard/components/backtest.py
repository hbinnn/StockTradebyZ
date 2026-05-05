"""
dashboard/components/backtest.py
回测模块：单只股票 N 日前向收益 + 跳转完整回测看板。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def render_backtest_button(code: str, strategy: str, pick_date: str, key: str) -> None:
    """选股日 N 日前向收益展开面板。"""
    with st.expander(f"回测: {code} ({strategy}) - {pick_date} 选股后表现"):
        result = calculate_performance(code, pick_date)
        if result is None:
            st.info("无可用行情数据")
            return

        cols = st.columns(5)
        cols[0].metric("1日", _fmt(result.get("d1")))
        cols[1].metric("3日", _fmt(result.get("d3")))
        cols[2].metric("5日", _fmt(result.get("d5")))
        cols[3].metric("10日", _fmt(result.get("d10")))
        cols[4].metric("20日", _fmt(result.get("d20")))

        st.caption(f"期间最高: {_fmt(result.get('max_return'))}  |  最低: {_fmt(result.get('min_return'))}")


def calculate_performance(code: str, pick_date: str, horizon_days: int = 20) -> Optional[dict]:
    """计算选股日之后的 N 日表现。

    Returns:
        dict: d1/d3/d5/d10/d20 收益率, max_return, min_return
    """
    data_dir = _PROJECT_ROOT / "data" / "raw"
    fpath = data_dir / f"{code}.csv"
    if not fpath.exists():
        return None

    try:
        df = pd.read_csv(fpath)
        df.columns = [c.lower() for c in df.columns]
        if "date" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    except Exception:
        return None

    target = pd.to_datetime(pick_date)
    dates = df["date"].values
    pos = int(np.searchsorted(dates, target.to_datetime64(), side="right")) - 1
    if pos < 0:
        return None
    # 如果日期差距太大（>5天），可能数据有问题
    actual_date = dates[pos]
    if (target - actual_date) > pd.Timedelta(days=5):
        return None

    base_close = float(df["close"].iloc[pos])

    def _ret(offset: int) -> Optional[float]:
        tgt = pos + offset
        if tgt >= len(df):
            return None
        return float((df["close"].iloc[tgt] / base_close - 1.0))

    horizon = min(horizon_days + 1, len(df) - pos)
    forward_returns = [
        float((df["close"].iloc[pos + i] / base_close - 1.0))
        for i in range(1, horizon)
    ]

    return {
        "d1":  _ret(1),
        "d3":  _ret(3),
        "d5":  _ret(5),
        "d10": _ret(10),
        "d20": _ret(20),
        "max_return": max(forward_returns) if forward_returns else None,
        "min_return": min(forward_returns) if forward_returns else None,
    }


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    color = "green" if v >= 0 else "red"
    return f":{color}[{v * 100:+.1f}%]"
