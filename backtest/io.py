"""
backtest/io.py
回测结果序列化：BacktestResult dataclass + JSON/CSV 读写。
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


@dataclass
class BacktestResult:
    """完整回测运行结果，可序列化为 JSON/CSV。"""
    run_id: str                    # UUID
    strategy: str                  # 策略名称（或 "combined"）
    config: dict                   # BacktestConfig.to_dict()
    nav_history: List[dict] = field(default_factory=list)
    trades: List[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    signal_stats: dict = field(default_factory=dict)  # {total_days, total_signals, avg_per_day}
    generated_at: str = ""

    def to_json(self, output_dir: str | Path) -> str:
        """保存结果到 output_dir/{run_id}/，返回 run 目录路径。"""
        run_dir = Path(output_dir) / self.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # config.json
        (run_dir / "config.json").write_text(
            json.dumps(self.config, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # metrics.json
        (run_dir / "metrics.json").write_text(
            json.dumps({
                "run_id": self.run_id,
                "strategy": self.strategy,
                "generated_at": self.generated_at,
                **self.metrics,
            }, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # nav_history.csv
        if self.nav_history:
            pd.DataFrame(self.nav_history).to_csv(
                run_dir / "nav_history.csv", index=False
            )

        # trades.csv
        if self.trades:
            pd.DataFrame(self.trades).to_csv(
                run_dir / "trades.csv", index=False
            )

        # signal_stats.json
        if self.signal_stats:
            (run_dir / "signal_stats.json").write_text(
                json.dumps(self.signal_stats, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

        return str(run_dir)

    @classmethod
    def from_dir(cls, run_dir: str | Path) -> "BacktestResult":
        """从磁盘目录加载回测结果。"""
        run_dir = Path(run_dir)
        config_path = run_dir / "config.json"
        metrics_path = run_dir / "metrics.json"
        nav_path = run_dir / "nav_history.csv"
        trades_path = run_dir / "trades.csv"
        signals_path = run_dir / "signal_stats.json"

        config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}

        nav = []
        if nav_path.exists():
            nav_df = pd.read_csv(nav_path)
            nav = nav_df.to_dict(orient="records")

        trades = []
        if trades_path.exists():
            trades_df = pd.read_csv(trades_path)
            trades = trades_df.to_dict(orient="records")

        signal_stats = {}
        if signals_path.exists():
            signal_stats = json.loads(signals_path.read_text(encoding="utf-8"))

        run_id = run_dir.name
        return cls(
            run_id=run_id,
            strategy=metrics.get("strategy", config.get("strategies", [""])[0] if config.get("strategies") else ""),
            config=config,
            nav_history=nav,
            trades=trades,
            metrics=metrics,
            signal_stats=signal_stats,
            generated_at=metrics.get("generated_at", ""),
        )

    @staticmethod
    def generate_run_id() -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{ts}_{uuid.uuid4().hex[:6]}"
