"""
backtest/exit_rules.py
策略专属止盈止损检查器。

B1/B2/B3:
  - 止损：收盘价 <= 信号日最低价 * 0.99 → 全卖
  - 止盈 zxdq：close < zxdq → 减半（可重复触发）
  - 止盈 zxdkx：close < zxdkx → 全卖

砖型图：
  - 连续 4 根红砖 → 减半
  - 出现绿砖 → 全卖
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .portfolio import Portfolio, Position

logger = logging.getLogger(__name__)


@dataclass
class ExitAction:
    action: str  # "none" | "sell_half" | "sell_all"
    reason: str


class B1ExitChecker:
    """B1 / B2 / B3 退出规则。

    需要 zxdq / zxdkx 列，若 data 中不存在则按参数计算。
    """

    def __init__(
        self,
        stop_loss_ratio: float = 0.99,
        zx_m1: int = 14, zx_m2: int = 28, zx_m3: int = 57, zx_m4: int = 114,
        zxdq_span: int = 10,
    ):
        self.stop_loss_ratio = stop_loss_ratio
        self.zx_m1, self.zx_m2 = zx_m1, zx_m2
        self.zx_m3, self.zx_m4 = zx_m3, zx_m4
        self.zxdq_span = zxdq_span
        # 缓存已计算的 zxdq/zxdkx，避免重复计算
        self._zx_cache: Dict[str, pd.DataFrame] = {}

    def check(
        self, pos: Position, date: pd.Timestamp, df: pd.DataFrame,
    ) -> ExitAction:
        if df is None or pos.shares <= 0:
            return ExitAction("none", "")

        close = self._get_val(df, date, "close")
        if close is None:
            return ExitAction("none", "")

        # ── 1) 止损：收盘价 <= 信号日最低价 * stop_loss_ratio ────
        if pos.stop_loss_price > 0 and close <= pos.stop_loss_price:
            return ExitAction("sell_all",
                f"止损 close={close:.2f} <= sl={pos.stop_loss_price:.2f}")

        # ── 确保 zxdq / zxdkx 列存在 ──────────────────────────────
        df = self._ensure_zx(df)

        zxdkx_val = self._get_val(df, date, "zxdkx")
        zxdq_val  = self._get_val(df, date, "zxdq")

        if zxdkx_val is None or zxdq_val is None:
            return ExitAction("none", "")

        # ── 2) 跌破 zxdkx（长期均线）→ 全卖 ─────────────────────
        if close < zxdkx_val:
            return ExitAction("sell_all",
                f"止盈(长期) close={close:.2f} < zxdkx={zxdkx_val:.2f}")

        # ── 3) 跌破 zxdq（短期均线）→ 减半 ───────────────────────
        if close < zxdq_val:
            return ExitAction("sell_half",
                f"止盈(短期) close={close:.2f} < zxdq={zxdq_val:.2f}")

        return ExitAction("none", "")

    def _ensure_zx(self, df: pd.DataFrame) -> pd.DataFrame:
        """确保 df 有 zxdq / zxdkx 列，未缓存则计算一次。"""
        if "zxdq" in df.columns and "zxdkx" in df.columns:
            return df
        # 用 index 做缓存 key（每个 stock df 的内存地址唯一，用 id 即可）
        cache_key = str(id(df))
        if cache_key in self._zx_cache:
            return self._zx_cache[cache_key]
        from pipeline.Selector import compute_zx_lines
        zs, zk = compute_zx_lines(
            df, self.zx_m1, self.zx_m2, self.zx_m3, self.zx_m4,
            zxdq_span=self.zxdq_span,
        )
        out = df.copy()
        out["zxdq"] = zs
        out["zxdkx"] = zk
        self._zx_cache[cache_key] = out
        return out

    @staticmethod
    def _get_val(df: pd.DataFrame, date: pd.Timestamp, col: str) -> Optional[float]:
        if "date" in df.columns:
            rows = df[df["date"] == date]
            if rows.empty:
                return None
            v = rows[col].iloc[0]
        elif date in df.index:
            v = df.loc[date, col]
        else:
            return None
        f = float(v)
        return f if np.isfinite(f) else None


class BrickExitChecker:
    """砖型图退出规则。

    入场后逐日追踪砖块颜色：
      - 连续 4 根红砖 → 减半（计数器重置）
      - 绿砖 → 全卖
    """

    def __init__(self,
        n: int = 4, m1: int = 4, m2: int = 6, m3: int = 6,
        t: float = 4.0, shift1: float = 90.0, shift2: float = 100.0,
        sma_w1: int = 1, sma_w2: int = 1, sma_w3: int = 1,
    ):
        self.brick_params = dict(
            n=n, m1=m1, m2=m2, m3=m3,
            t=t, shift1=shift1, shift2=shift2,
            sma_w1=sma_w1, sma_w2=sma_w2, sma_w3=sma_w3,
        )
        # 每个 code 的连续红砖计数器
        self._red_counters: Dict[str, int] = {}

    def check(
        self, pos: Position, date: pd.Timestamp, df: pd.DataFrame,
    ) -> ExitAction:
        if df is None or pos.shares <= 0:
            return ExitAction("none", "")

        brick_val = self._get_brick(df, date)
        if brick_val is None:
            return ExitAction("none", "")

        # ── 绿砖 → 全卖 ──────────────────────────────────────────
        if brick_val < 0:
            self._red_counters.pop(pos.code, None)
            return ExitAction("sell_all",
                f"止盈(绿砖) brick={brick_val:.3f}")

        # ── 红砖 → 计数器 +1 ────────────────────────────────────
        if brick_val > 0:
            cnt = self._red_counters.get(pos.code, 0) + 1
            self._red_counters[pos.code] = cnt
            if cnt >= 4:
                self._red_counters[pos.code] = 0  # 重置，从头计数
                return ExitAction("sell_half",
                    f"止盈(连续{cnt}红砖)")
        else:
            # brick == 0，重置
            self._red_counters[pos.code] = 0

        return ExitAction("none", "")

    def reset_counter(self, code: str) -> None:
        self._red_counters.pop(code, None)

    def _get_brick(
        self, df: pd.DataFrame, date: pd.Timestamp,
    ) -> Optional[float]:
        """获取指定日期的砖型图值，列不存在则临时计算。"""
        if "brick" not in df.columns:
            from pipeline.Selector import compute_brick_chart
            bv = compute_brick_chart(df, **self.brick_params)
            df = df.copy()
            df["brick"] = bv

        if "date" in df.columns:
            rows = df[df["date"] == date]
            if rows.empty:
                return None
            v = float(rows["brick"].iloc[0])
        elif date in df.index:
            v = float(df.loc[date, "brick"])
        else:
            return None
        return v if np.isfinite(v) else None
