"""
backtest/broker.py
A 股执行模拟器：T+1、涨跌停、佣金/印花税/滑点。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .config import BrokerConfig


@dataclass
class ExecutionResult:
    """执行结果。"""
    success: bool
    fill_price: float
    shares: int = 0
    cost: float = 0.0             # 含佣金的总成本（买入）/ 净回收（卖出）
    reason: str = ""


class Broker:
    """A 股执行模拟器。

    关键规则：
    - T+1：信号日 D → 买入执行在 D+1 开盘价
    - 涨跌停 ±10%（科创/创业板暂不区分，统一 10%）
    - 一字板买不进/卖不出判断
    - 100 股整手取整
    """

    LIMIT_PCT = 0.10  # 10% 涨跌停（主板）

    def __init__(self, config: BrokerConfig = BrokerConfig()):
        self.config = config

    # ── 涨跌停计算 ──────────────────────────────────────────────────────

    def limit_up_price(self, prev_close: float) -> float:
        return round(prev_close * (1 + self.LIMIT_PCT), 2)

    def limit_down_price(self, prev_close: float) -> float:
        return round(prev_close * (1 - self.LIMIT_PCT), 2)

    # ── 涨跌停锁定检测 ──────────────────────────────────────────────────

    def is_limit_up_locked(
        self, df: pd.DataFrame, date: pd.Timestamp
    ) -> bool:
        """一字涨停板：open == limit_up == high，买不进。"""
        if not self.config.respect_price_limits:
            return False
        o, h, l, prev_c = self._ohl_prev(df, date)
        if o is None or h is None or prev_c is None:
            return False
        limit_up = self.limit_up_price(prev_c)
        return o >= limit_up and h <= limit_up  # open 已经顶格

    def is_limit_down_locked(
        self, df: pd.DataFrame, date: pd.Timestamp
    ) -> bool:
        """一字跌停板：open == limit_down == low，卖不出。"""
        if not self.config.respect_price_limits:
            return False
        o, h, l, prev_c = self._ohl_prev(df, date)
        if o is None or l is None or prev_c is None:
            return False
        limit_down = self.limit_down_price(prev_c)
        return o <= limit_down and l >= limit_down

    @staticmethod
    def _ohl_prev(
        df: pd.DataFrame, date: pd.Timestamp,
    ) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """获取指定日期的 (open, high, low, prev_close)。"""
        if "date" in df.columns:
            rows = df[df["date"] == date]
            if rows.empty:
                return None, None, None, None
            idx = rows.index[0]
            o = float(rows["open"].iloc[0])
            h = float(rows["high"].iloc[0])
            l = float(rows["low"].iloc[0])
            # prev close
            if idx > 0:
                prev_c = float(df["close"].iloc[idx - 1])
            else:
                prev_c = float(rows["close"].iloc[0])
            return o, h, l, prev_c
        # DatetimeIndex
        if date not in df.index:
            return None, None, None, None
        row = df.loc[date]
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        pos = df.index.get_loc(date)
        if pos > 0:
            prev_c = float(df["close"].iloc[pos - 1])
        else:
            prev_c = float(row["close"])
        return o, h, l, prev_c

    # ── 价格获取 ────────────────────────────────────────────────────────

    def get_entry_price(
        self, code: str, signal_date: pd.Timestamp,
        market_data: Dict[str, pd.DataFrame],
    ) -> Optional[float]:
        """买入价：默认 next_open（T+1 次日开盘），可选 same_close。"""
        df = market_data.get(code)
        if df is None:
            return None

        if self.config.entry_price_mode == "same_close":
            date = signal_date
            price_col = "close"
        else:
            date = self._next_trading_day(df, signal_date)
            if date is None:
                return None
            price_col = "open"

        price = self._get_price(df, date, price_col)
        if price is None:
            return None

        # 检查涨停锁死
        if (self.config.respect_price_limits
                and self.config.entry_price_mode == "next_open"
                and self.is_limit_up_locked(df, date)):
            return None

        return self._apply_slippage(price, side="buy")

    def get_exit_price(
        self, code: str, date: pd.Timestamp,
        market_data: Dict[str, pd.DataFrame],
    ) -> Optional[float]:
        """卖出价：默认 same_close，可选 next_open。"""
        df = market_data.get(code)
        if df is None:
            return None

        if self.config.exit_price_mode == "next_open":
            trade_date = self._next_trading_day(df, date)
            if trade_date is None:
                return None
            price_col = "open"
        else:
            trade_date = date
            price_col = "close"

        price = self._get_price(df, trade_date, price_col)
        if price is None:
            return None

        # 检查跌停锁死
        if (self.config.respect_price_limits
                and self.config.exit_price_mode == "same_close"
                and self.is_limit_down_locked(df, trade_date)):
            return None

        return self._apply_slippage(price, side="sell")

    # ── 费用计算 ────────────────────────────────────────────────────────

    def buy_cost(self, amount: float) -> float:
        """买入总成本 = 成交额 + 佣金。"""
        return amount * (1 + self.config.commission_rate)

    def sell_proceeds(self, amount: float) -> float:
        """卖出净回收 = 成交额 - 佣金 - 印花税。"""
        return amount * (1 - self.config.commission_rate - self.config.stamp_tax_rate)

    def calculate_shares(
        self, price: float, max_amount: float, lot_size: int = 100,
    ) -> int:
        """计算可买股数（100 股整手），含手续费预算。"""
        # max_amount = shares * price * (1 + commission_rate)
        # shares ≈ max_amount / (price * 1.00025)
        raw_shares = int(max_amount / (price * (1 + self.config.commission_rate)) / lot_size) * lot_size
        return max(raw_shares, 0)

    # ── 辅助 ─────────────────────────────────────────────────────────────

    def _apply_slippage(self, price: float, side: str) -> float:
        sr = self.config.slippage_rate
        if side == "buy":
            return price * (1 + sr)
        return price * (1 - sr)

    @staticmethod
    def _get_price(
        df: pd.DataFrame, date: pd.Timestamp, col: str,
    ) -> Optional[float]:
        if "date" in df.columns:
            rows = df[df["date"] == date]
            if rows.empty:
                return None
            return float(rows[col].iloc[0])
        if date in df.index:
            return float(df.loc[date, col])
        return None

    @staticmethod
    def _next_trading_day(
        df: pd.DataFrame, date: pd.Timestamp,
    ) -> Optional[pd.Timestamp]:
        """在 df 中找到 date 之后的下一个交易日。"""
        if "date" in df.columns:
            future = df[df["date"] > date]["date"]
            return future.min() if not future.empty else None
        # DatetimeIndex
        future = df.index[df.index > date]
        return future.min() if len(future) > 0 else None
