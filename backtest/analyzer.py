"""
backtest/analyzer.py
PerformanceAnalyzer：从净值历史和交易记录计算全部绩效指标。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .portfolio import NavPoint, Trade

TRADING_DAYS_PER_YEAR = 252


class PerformanceAnalyzer:
    """计算策略绩效指标。"""

    def __init__(
        self,
        nav_history: List[NavPoint],
        trades: List[Trade],
        initial_capital: float,
        risk_free_rate: float = 0.03,
    ):
        self.nav_history = nav_history
        self.trades = trades
        self.initial_capital = initial_capital
        self.risk_free_rate = risk_free_rate

        if nav_history:
            self.nav_df = pd.DataFrame([n.to_dict() for n in nav_history])
            self.nav_df["date"] = pd.to_datetime(self.nav_df["date"])
            self.nav_df = self.nav_df.set_index("date")
            self.nav_df["daily_return"] = self.nav_df["total_nav"].pct_change()
        else:
            self.nav_df = pd.DataFrame(columns=["date", "total_nav", "cash", "positions_value", "num_positions", "daily_return"])

        self._daily_returns = self.nav_df["daily_return"].dropna() if not self.nav_df.empty else pd.Series(dtype=float)

    # ── 收益指标 ───────────────────────────────────────────────────────

    def total_return(self) -> float:
        final = self.nav_df["total_nav"].iloc[-1] if not self.nav_df.empty else self.initial_capital
        return (final / self.initial_capital) - 1.0

    def annualized_return(self) -> float:
        r = self.total_return()
        n_days = len(self._daily_returns)
        if n_days == 0 or r <= -1:
            return r
        years = n_days / TRADING_DAYS_PER_YEAR
        if years <= 0:
            return 0.0
        return (1 + r) ** (1.0 / years) - 1.0

    def annualized_volatility(self) -> float:
        if len(self._daily_returns) < 2:
            return 0.0
        return float(self._daily_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))

    # ── 风险调整指标 ───────────────────────────────────────────────────

    def sharpe_ratio(self) -> float:
        vol = self.annualized_volatility()
        if vol == 0:
            return 0.0
        daily_rf = self.risk_free_rate / TRADING_DAYS_PER_YEAR
        excess = self.annualized_return() - self.risk_free_rate
        return excess / vol

    def sortino_ratio(self) -> float:
        diff = self._daily_returns - self.risk_free_rate / TRADING_DAYS_PER_YEAR
        downside = diff[diff < 0]
        if downside.empty or len(downside) < 2:
            return 0.0
        downside_vol = float(downside.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
        if downside_vol == 0:
            return 0.0
        return (self.annualized_return() - self.risk_free_rate) / downside_vol

    def max_drawdown(self) -> float:
        """最大回撤（返回负值百分比）。"""
        if self.nav_df.empty:
            return 0.0
        nav = self.nav_df["total_nav"].values
        peak = np.maximum.accumulate(nav)
        dd = (nav - peak) / peak
        return float(np.min(dd))

    def max_drawdown_duration(self) -> int:
        """最大回撤持续交易日数。"""
        if self.nav_df.empty:
            return 0
        nav = self.nav_df["total_nav"].values
        peak = np.maximum.accumulate(nav)
        in_dd = nav < peak
        max_dur = 0
        cur = 0
        for v in in_dd:
            if v:
                cur += 1
                max_dur = max(max_dur, cur)
            else:
                cur = 0
        return max_dur

    def calmar_ratio(self) -> float:
        mdd = abs(self.max_drawdown())
        if mdd == 0:
            return 0.0
        return self.annualized_return() / mdd

    # ── 交易统计 ───────────────────────────────────────────────────────

    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl > 0)
        return wins / len(self.trades)

    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def avg_trade_return(self) -> float:
        if not self.trades:
            return 0.0
        return float(np.mean([t.pnl_pct for t in self.trades]))

    def avg_win(self) -> float:
        wins = [t.pnl_pct for t in self.trades if t.pnl > 0]
        return float(np.mean(wins)) if wins else 0.0

    def avg_loss(self) -> float:
        losses = [t.pnl_pct for t in self.trades if t.pnl < 0]
        return float(np.mean(losses)) if losses else 0.0

    def avg_holding_days(self) -> float:
        if not self.trades:
            return 0.0
        return float(np.mean([t.holding_days for t in self.trades]))

    # ── 时间分段收益 ──────────────────────────────────────────────────

    def monthly_returns(self) -> pd.Series:
        if self.nav_df.empty:
            return pd.Series(dtype=float)
        monthly_nav = self.nav_df["total_nav"].resample("ME").last()
        # 插入初始资金作为基准，计算第一个月的收益
        first_date = self.nav_df.index[0]
        base = pd.Series({first_date - pd.DateOffset(months=1): self.initial_capital})
        monthly_nav = pd.concat([base, monthly_nav]).sort_index()
        return monthly_nav.pct_change().dropna()

    def yearly_returns(self) -> pd.Series:
        if self.nav_df.empty:
            return pd.Series(dtype=float)
        yearly_nav = self.nav_df["total_nav"].resample("YE").last()
        # 插入初始资金作为基准，计算第一年的收益
        first_date = self.nav_df.index[0]
        base = pd.Series({pd.Timestamp(str(first_date.year - 1) + "-12-31"): self.initial_capital})
        yearly_nav = pd.concat([base, yearly_nav]).sort_index()
        return yearly_nav.pct_change().dropna()

    def monthly_win_rate(self) -> pd.Series:
        """逐月胜率。"""
        if self.nav_df.empty:
            return pd.Series(dtype=float)
        daily = self.nav_df["daily_return"].dropna()
        monthly = daily.resample("ME").apply(lambda x: (x > 0).sum() / len(x) if len(x) > 0 else 0)
        return monthly

    def best_month(self) -> float:
        mr = self.monthly_returns()
        return float(mr.max()) if not mr.empty else 0.0

    def worst_month(self) -> float:
        mr = self.monthly_returns()
        return float(mr.min()) if not mr.empty else 0.0

    # ── 汇总 ─────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """返回所有关键指标的扁平字典。"""
        monthly = self.monthly_returns()
        yearly = self.yearly_returns()
        months_positive = int((monthly > 0).sum())
        months_total = len(monthly)

        return {
            "total_return": round(self.total_return(), 4),
            "annualized_return": round(self.annualized_return(), 4),
            "annualized_volatility": round(self.annualized_volatility(), 4),
            "sharpe_ratio": round(self.sharpe_ratio(), 4),
            "sortino_ratio": round(self.sortino_ratio(), 4),
            "max_drawdown": round(self.max_drawdown(), 4),
            "max_drawdown_duration_days": self.max_drawdown_duration(),
            "calmar_ratio": round(self.calmar_ratio(), 4),
            "win_rate": round(self.win_rate(), 4),
            "profit_factor": round(self.profit_factor(), 4) if self.profit_factor() != float("inf") else "inf",
            "avg_trade_return": round(self.avg_trade_return(), 4),
            "avg_win": round(self.avg_win(), 4),
            "avg_loss": round(self.avg_loss(), 4),
            "avg_holding_days": round(self.avg_holding_days(), 1),
            "total_trades": len(self.trades),
            "best_month_return": round(self.best_month(), 4),
            "worst_month_return": round(self.worst_month(), 4),
            "months_positive": f"{months_positive}/{months_total}",
            "monthly_returns": {
                str(k.date()): round(v, 4) for k, v in monthly.items()
            },
            "yearly_returns": {
                str(k.date()): round(v, 4) for k, v in yearly.items()
            },
        }
