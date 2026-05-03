"""
dashboard/components/backtest.py
回测模块（预留）。后续将实现选股后的 N 日走势追踪。
"""

from __future__ import annotations
from typing import Optional
import streamlit as st


def render_backtest_button(code: str, strategy: str, pick_date: str, key: str) -> None:
    """渲染回测按钮（当前禁用，功能即将上线）。"""
    st.button(
        "📈 回测",
        key=key,
        disabled=True,
        help=f"回测功能即将上线。将展示 {code} 在 {pick_date} 选股后 5/10/20 日的走势表现。",
        use_container_width=True,
    )


def calculate_performance(code: str, pick_date: str, horizon_days: int = 20) -> Optional[dict]:
    """
    计算选股后 N 日表现（预留接口）。

    Returns:
        dict: max_return, min_return, final_return, win, max_drawdown
        None: 数据不可用
    """
    raise NotImplementedError("回测计算功能尚未实现")
