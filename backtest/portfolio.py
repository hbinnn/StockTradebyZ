"""
backtest/portfolio.py
持仓管理：Position（单只持仓）+ Portfolio（组合），含净值记录和交易日志。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class Position:
    """单只股票持仓。"""
    code: str
    entry_date: pd.Timestamp
    entry_price: float            # 实际成交价（含滑点）
    shares: int                   # 股数（100 股整手）
    allocated_capital: float      # 入场成本（含佣金）
    strategy: str = ""            # 来源策略
    stop_loss_price: float = 0.0  # 止损价（B1/B2/B3：信号日最低价*0.99）
    signal_date: str = ""         # 策略信号发出日

    def market_value(self, close: float) -> float:
        return self.shares * close

    def unrealized_pnl(self, close: float) -> float:
        return self.market_value(close) - self.allocated_capital

    def unrealized_pnl_pct(self, close: float) -> float:
        if self.allocated_capital <= 0:
            return 0.0
        return self.unrealized_pnl(close) / self.allocated_capital

    def holding_days(self, current_date: pd.Timestamp) -> int:
        return (current_date - self.entry_date).days

    def to_dict(self, close: float, current_date: pd.Timestamp) -> dict:
        return {
            "code": self.code,
            "entry_date": str(self.entry_date.date()),
            "entry_price": self.entry_price,
            "shares": self.shares,
            "allocated_capital": round(self.allocated_capital, 2),
            "market_value": round(self.market_value(close), 2),
            "unrealized_pnl": round(self.unrealized_pnl(close), 2),
            "unrealized_pnl_pct": round(self.unrealized_pnl_pct(close), 4),
            "holding_days": self.holding_days(current_date),
            "strategy": self.strategy,
        }


@dataclass
class NavPoint:
    """单日净值快照。"""
    date: pd.Timestamp
    total_nav: float
    cash: float
    positions_value: float
    num_positions: int

    def to_dict(self) -> dict:
        return {
            "date": str(self.date.date()),
            "total_nav": round(self.total_nav, 2),
            "cash": round(self.cash, 2),
            "positions_value": round(self.positions_value, 2),
            "num_positions": self.num_positions,
        }


@dataclass
class Trade:
    """已完成交易记录。"""
    code: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    pnl_pct: float
    holding_days: int
    strategy: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "entry_date": str(self.entry_date.date()),
            "exit_date": str(self.exit_date.date()),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "shares": self.shares,
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 4),
            "holding_days": self.holding_days,
            "strategy": self.strategy,
        }


class Portfolio:
    """组合管理器：资金、持仓、净值、交易日志。"""

    def __init__(self, initial_capital: float, max_positions: int = 10):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.max_positions = max_positions
        self.positions: Dict[str, Position] = {}
        self.nav_history: List[NavPoint] = []
        self.trade_log: List[Trade] = []

    # ── 查询 ─────────────────────────────────────────────────────────────

    def can_open(self) -> bool:
        return len(self.positions) < self.max_positions

    def has_position(self, code: str) -> bool:
        return code in self.positions

    def allocation_per_position(self) -> float:
        """等权分配：现金 / 剩余仓位。"""
        remaining = self.max_positions - len(self.positions)
        if remaining <= 0:
            return 0.0
        return self.cash / remaining

    # ── 净值核算 ─────────────────────────────────────────────────────────

    def mark_to_market(
        self, date: pd.Timestamp, market_data: Dict[str, pd.DataFrame]
    ) -> None:
        """逐日核算：用收盘价计算持仓市值，记录 NAV 点。"""
        positions_value = 0.0
        for code, pos in self.positions.items():
            df = market_data.get(code)
            if df is None:
                continue
            close = self._get_close(df, date)
            if close is not None:
                positions_value += pos.market_value(close)
            else:
                positions_value += pos.allocated_capital  # 无数据时用成本

        total_nav = self.cash + positions_value
        self.nav_history.append(NavPoint(
            date=date,
            total_nav=total_nav,
            cash=self.cash,
            positions_value=positions_value,
            num_positions=len(self.positions),
        ))

    # ── 买卖操作 ─────────────────────────────────────────────────────────

    def open_position(
        self, code: str, date: pd.Timestamp,
        fill_price: float, shares: int,
        actual_cost: float, strategy: str = "",
        stop_loss_price: float = 0.0,
        signal_date: str = "",
    ) -> Position:
        """开仓：扣除资金，添加持仓。"""
        if actual_cost > self.cash:
            raise ValueError(f"资金不足: 需要 {actual_cost}, 可用 {self.cash}")
        self.cash -= actual_cost
        pos = Position(
            code=code, entry_date=date,
            entry_price=fill_price, shares=shares,
            allocated_capital=actual_cost, strategy=strategy,
            stop_loss_price=stop_loss_price,
            signal_date=signal_date,
        )
        self.positions[code] = pos
        return pos

    def reduce_position(
        self, code: str, date: pd.Timestamp,
        fill_price: float, fraction: float,
    ) -> Optional[Trade]:
        """减仓：卖出 fraction 比例的持仓（0.5 = 卖一半）。"""
        pos = self.positions.get(code)
        if pos is None:
            return None
        sell_shares = max(int(pos.shares * fraction / 100) * 100, 100)
        if sell_shares >= pos.shares:
            # 接近全仓，直接平仓
            gross = fill_price * pos.shares
            net = self._broker_sell(gross)
            return self.close_position(code, date, fill_price, net)

        gross = fill_price * sell_shares
        net = self._broker_sell(gross)
        cost_portion = pos.allocated_capital * (sell_shares / pos.shares)
        pnl = net - cost_portion

        pos.shares -= sell_shares
        pos.allocated_capital -= cost_portion
        self.cash += net

        trade = Trade(
            code=code,
            entry_date=pos.entry_date,
            exit_date=date,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            shares=sell_shares,
            pnl=pnl,
            pnl_pct=pnl / cost_portion if cost_portion > 0 else 0.0,
            holding_days=pos.holding_days(date),
            strategy=pos.strategy,
        )
        self.trade_log.append(trade)
        return trade

    @staticmethod
    def _broker_sell(gross: float) -> float:
        """卖出净回收（默认费率：佣金万2.5 + 印花税千1）。"""
        return gross * (1 - 0.00025 - 0.001)

    def close_position(
        self, code: str, date: pd.Timestamp,
        fill_price: float, net_proceeds: float,
    ) -> Optional[Trade]:
        """平仓：回收资金，记录交易。"""
        pos = self.positions.pop(code, None)
        if pos is None:
            return None
        self.cash += net_proceeds
        pnl = net_proceeds - pos.allocated_capital
        trade = Trade(
            code=code,
            entry_date=pos.entry_date,
            exit_date=date,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            shares=pos.shares,
            pnl=pnl,
            pnl_pct=pnl / pos.allocated_capital if pos.allocated_capital > 0 else 0.0,
            holding_days=pos.holding_days(date),
            strategy=pos.strategy,
        )
        self.trade_log.append(trade)
        return trade

    # ── 辅助 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _get_close(df: pd.DataFrame, date: pd.Timestamp) -> Optional[float]:
        if "date" in df.columns:
            mask = df["date"] == date
            if mask.any():
                val = df.loc[mask, "close"]
                return float(val.iloc[0]) if len(val) > 0 else None
        if isinstance(df.index, pd.DatetimeIndex):
            if date in df.index:
                return float(df.loc[date, "close"])
        return None

    def summary(self) -> dict:
        total_nav = self.nav_history[-1].total_nav if self.nav_history else self.initial_capital
        return {
            "initial_capital": self.initial_capital,
            "final_nav": round(total_nav, 2),
            "total_return_pct": round((total_nav / self.initial_capital - 1) * 100, 2),
            "cash": round(self.cash, 2),
            "num_positions": len(self.positions),
            "total_trades": len(self.trade_log),
        }
