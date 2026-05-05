"""
backtest/config.py
回测配置 dataclass：BacktestConfig + BrokerConfig，支持 YAML 加载。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class BrokerConfig:
    """A 股执行模拟参数。"""
    commission_bps: float = 2.5        # 佣金 万2.5（买卖双向）
    stamp_tax_bps: float = 10.0        # 印花税 千1（仅卖出）
    slippage_bps: float = 1.0          # 滑点 万1
    t_plus_1: bool = True              # T+1 交收
    respect_price_limits: bool = True  # 涨跌停限制
    entry_price_mode: str = "next_open"   # next_open | same_close
    exit_price_mode: str = "same_close"   # same_close | next_open

    @property
    def commission_rate(self) -> float:
        return self.commission_bps / 10000.0

    @property
    def stamp_tax_rate(self) -> float:
        return self.stamp_tax_bps / 10000.0

    @property
    def slippage_rate(self) -> float:
        return self.slippage_bps / 10000.0


@dataclass
class BacktestConfig:
    """回测引擎完整配置。"""
    start_date: str                   # "2023-01-01"
    end_date: str                     # "2025-12-31"
    initial_capital: float = 1_000_000
    max_positions: int = 10
    hold_days: int = 5
    strategies: List[str] = field(default_factory=lambda: ["b1", "brick"])
    top_m: int = 5000                 # 流动性池取 top-N
    max_new_positions_per_day: int = 3
    n_turnover_days: int = 43         # 滚动成交额窗口
    ranking_field: str = "turnover_n"  # turnover_n | brick_growth
    ranking_ascending: bool = False    # 降序（成交额大的优先）
    data_dir: str = "./data/raw"
    output_dir: str = "./data/backtest"
    risk_free_rate: float = 0.03      # 无风险利率（夏普比率用）
    broker: BrokerConfig = field(default_factory=BrokerConfig)

    # — 策略参数快照（从 rules_preselect.yaml 加载） —
    strategy_params: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # — 止盈止损（per-strategy，null 表示不启用） —
    exit_rules: Dict[str, Dict[str, Optional[float]]] = field(default_factory=dict)

    # — 大盘择时过滤（知行线：zxdq/zxdkx） —
    market_filter_enabled: bool = False
    index_code: str = "000001.SH"          # 上证指数

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_capital": self.initial_capital,
            "max_positions": self.max_positions,
            "hold_days": self.hold_days,
            "strategies": self.strategies,
            "top_m": self.top_m,
            "max_new_positions_per_day": self.max_new_positions_per_day,
            "n_turnover_days": self.n_turnover_days,
            "ranking_field": self.ranking_field,
            "ranking_ascending": self.ranking_ascending,
            "data_dir": self.data_dir,
            "risk_free_rate": self.risk_free_rate,
            "broker": {
                "commission_bps": self.broker.commission_bps,
                "stamp_tax_bps": self.broker.stamp_tax_bps,
                "slippage_bps": self.broker.slippage_bps,
                "t_plus_1": self.broker.t_plus_1,
                "respect_price_limits": self.broker.respect_price_limits,
                "entry_price_mode": self.broker.entry_price_mode,
                "exit_price_mode": self.broker.exit_price_mode,
            },
            "strategy_params": self.strategy_params,
            "exit_rules": self.exit_rules,
        }

    @classmethod
    def from_yaml(cls, path: str | Path) -> "BacktestConfig":
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        eng = raw.get("engine", {})
        pf  = raw.get("portfolio", {})
        sig = raw.get("signals", {})
        brk = raw.get("broker", {})
        met = raw.get("metrics", {})
        out = raw.get("output", {})
        ex  = raw.get("exit_rules", {})
        mf  = raw.get("market_filter", {})

        return cls(
            start_date=eng.get("start_date", "2023-01-01"),
            end_date=eng.get("end_date", "2025-12-31"),
            initial_capital=float(eng.get("initial_capital", 1_000_000)),
            max_positions=int(eng.get("max_positions", pf.get("max_positions", 10))),
            hold_days=int(eng.get("hold_days", pf.get("hold_days", 5))),
            strategies=eng.get("strategies", ["b1", "brick"]),
            top_m=int(sig.get("top_m", 5000)),
            max_new_positions_per_day=int(pf.get("max_new_positions_per_day", 3)),
            n_turnover_days=int(sig.get("n_turnover_days", 43)),
            ranking_field=sig.get("ranking", {}).get("field", "turnover_n"),
            ranking_ascending=sig.get("ranking", {}).get("ascending", False),
            data_dir=sig.get("data_dir", "./data/raw"),
            output_dir=out.get("dir", "./data/backtest"),
            risk_free_rate=float(met.get("risk_free_rate", 0.03)),
            broker=BrokerConfig(
                commission_bps=float(brk.get("commission_bps", 2.5)),
                stamp_tax_bps=float(brk.get("stamp_tax_bps", 10.0)),
                slippage_bps=float(brk.get("slippage_bps", 1.0)),
                t_plus_1=bool(brk.get("t_plus_1", True)),
                respect_price_limits=bool(brk.get("respect_price_limits", True)),
                entry_price_mode=brk.get("entry_price", "next_open"),
                exit_price_mode=brk.get("exit_price", "same_close"),
            ),
            exit_rules={
                k: {
                    "stop_profit_pct": v.get("stop_profit_pct"),
                    "stop_loss_pct": v.get("stop_loss_pct"),
                }
                for k, v in ex.items()
            },
            market_filter_enabled=bool(mf.get("enabled", False)),
            index_code=mf.get("index_code", "000001.SH"),
        )
