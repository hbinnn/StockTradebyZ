"""
backtest/optimizer.py
参数优化：GridSearcher（网格搜索）+ WalkForwardOptimizer（滚动窗口）。
"""
from __future__ import annotations

import itertools
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .config import BacktestConfig
from .engine import BacktestEngine

logger = logging.getLogger(__name__)


@dataclass
class GridConfig:
    """单策略参数网格定义。"""
    strategy: str
    param_grid: Dict[str, List[Any]]  # {"j_threshold": [-10, -5, 0, 5]}

    def combinations(self) -> List[Dict[str, Any]]:
        """生成所有参数组合。"""
        keys = list(self.param_grid.keys())
        values = list(self.param_grid.values())
        for combo in itertools.product(*values):
            yield dict(zip(keys, combo))


@dataclass
class TrialResult:
    """单次试验结果。"""
    params: Dict[str, Any]
    metrics: Dict[str, Any]
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "params": self.params,
            "score": round(self.score, 4),
            "metrics": {k: v for k, v in self.metrics.items()
                        if k not in ("monthly_returns", "yearly_returns")},
        }


class GridSearcher:
    """网格搜索参数优化。

    优化策略：
    - prepare_base_only() 只跑一次（通过 SignalLoader 内部实现）
    - 每个 trial 修改策略参数，重新生成信号 + 运行回测
    - 支持多进程并行 trial（但需要谨慎处理内存）
    """

    def __init__(
        self,
        backtest_config: BacktestConfig,
        grid_config: GridConfig,
        scoring_weights: Optional[Dict[str, float]] = None,
    ):
        self.bt_config = backtest_config
        self.grid_config = grid_config
        self.scoring_weights = scoring_weights or {
            "sharpe_ratio": 0.5,
            "calmar_ratio": 0.3,
            "win_rate": 0.2,
        }
        self.trials: List[TrialResult] = []

    def search(self) -> List[TrialResult]:
        """遍历所有参数组合，运行回测，按评分降序返回。"""
        combos = list(self.grid_config.combinations())
        logger.info(
            "策略 %s: 共 %d 组参数组合",
            self.grid_config.strategy, len(combos),
        )

        for i, params in enumerate(combos):
            logger.info("[%d/%d] 参数: %s", i + 1, len(combos), params)

            # 构建 trial config（浅拷贝 + 覆盖策略参数）
            trial_cfg = deepcopy(self.bt_config)
            trial_cfg.strategies = [self.grid_config.strategy]
            trial_cfg.strategy_params = {self.grid_config.strategy: params}

            try:
                engine = BacktestEngine(trial_cfg)
                result = engine.run_single(
                    self.grid_config.strategy, params
                )
                score = self._score(result.metrics)
                trial = TrialResult(
                    params=dict(params),
                    metrics=result.metrics,
                    score=score,
                )
                self.trials.append(trial)
                logger.info("  → 评分: %.4f, 夏普: %.3f, 回撤: %.2f%%",
                            score,
                            result.metrics.get("sharpe_ratio", 0),
                            result.metrics.get("max_drawdown", 0) * 100)
            except Exception as exc:
                logger.warning("  失败: %s", exc)
                continue

        self.trials.sort(key=lambda t: t.score, reverse=True)
        return self.trials

    def best(self) -> Optional[TrialResult]:
        return self.trials[0] if self.trials else None

    def to_dataframe(self) -> pd.DataFrame:
        rows = []
        for t in self.trials:
            row = dict(t.params)
            row["score"] = t.score
            for k in ("sharpe_ratio", "annualized_return", "max_drawdown",
                      "win_rate", "total_return", "total_trades"):
                row[k] = t.metrics.get(k)
            rows.append(row)
        return pd.DataFrame(rows)

    def _score(self, metrics: dict) -> float:
        s = 0.0
        w = self.scoring_weights

        # 夏普比率
        sharpe = metrics.get("sharpe_ratio", 0) or 0
        s += w.get("sharpe_ratio", 0) * max(sharpe, 0)  # floor at 0

        # 卡尔玛比率
        calmar = metrics.get("calmar_ratio", 0) or 0
        s += w.get("calmar_ratio", 0) * max(calmar, 0)

        # 胜率
        wr = metrics.get("win_rate", 0) or 0
        s += w.get("win_rate", 0) * wr

        return s


class WalkForwardOptimizer:
    """Walk-Forward 滚动窗口优化。

    将回测区间切分为 K 个滚动窗口，每个窗口：
      1. 在样本内（IS）用 GridSearcher 找最优参数
      2. 在样本外（OOS）用最优参数运行回测
      3. 记录 OOS 表现和参数稳定性
    """

    def __init__(
        self,
        backtest_config: BacktestConfig,
        grid_config: GridConfig,
        n_windows: int = 4,
        is_months: int = 8,
        oos_months: int = 4,
    ):
        self.bt_config = backtest_config
        self.grid_config = grid_config
        self.n_windows = n_windows
        self.is_months = is_months
        self.oos_months = oos_months
        self.windows: List[Dict[str, Any]] = []

    def run(self) -> Dict[str, Any]:
        """运行 Walk-Forward 优化。"""
        dates = self._generate_windows()
        if not dates:
            raise ValueError("无法生成 Walk-Forward 窗口，回测区间太短")

        logger.info("Walk-Forward: %d 个窗口", len(dates))

        all_optimal_params = []
        all_oos_metrics = []

        for i, (is_start, is_end, oos_start, oos_end) in enumerate(dates):
            logger.info(
                "窗口 %d/%d: IS=%s~%s, OOS=%s~%s",
                i + 1, len(dates),
                is_start.strftime("%Y-%m-%d"), is_end.strftime("%Y-%m-%d"),
                oos_start.strftime("%Y-%m-%d"), oos_end.strftime("%Y-%m-%d"),
            )

            # ── IS 网格搜索 ───────────────────────────────────────
            is_cfg = deepcopy(self.bt_config)
            is_cfg.start_date = is_start.strftime("%Y-%m-%d")
            is_cfg.end_date = is_end.strftime("%Y-%m-%d")

            searcher = GridSearcher(is_cfg, self.grid_config)
            searcher.search()
            best = searcher.best()
            if best is None:
                logger.warning("IS 网格搜索无结果，跳过此窗口")
                continue

            logger.info("  最优参数: %s, IS评分: %.4f", best.params, best.score)
            all_optimal_params.append(best.params)

            # ── OOS 验证 ─────────────────────────────────────────
            oos_cfg = deepcopy(self.bt_config)
            oos_cfg.start_date = oos_start.strftime("%Y-%m-%d")
            oos_cfg.end_date = oos_end.strftime("%Y-%m-%d")

            try:
                engine = BacktestEngine(oos_cfg)
                result = engine.run_single(self.grid_config.strategy, best.params)
                all_oos_metrics.append(result.metrics)

                self.windows.append({
                    "window": i + 1,
                    "is_start": is_start.strftime("%Y-%m-%d"),
                    "is_end": is_end.strftime("%Y-%m-%d"),
                    "oos_start": oos_start.strftime("%Y-%m-%d"),
                    "oos_end": oos_end.strftime("%Y-%m-%d"),
                    "best_params": best.params,
                    "is_score": best.score,
                    "oos_sharpe": result.metrics.get("sharpe_ratio"),
                    "oos_return": result.metrics.get("total_return"),
                    "oos_max_dd": result.metrics.get("max_drawdown"),
                })
            except Exception as exc:
                logger.warning("OOS 验证失败: %s", exc)
                continue

        # ── 汇总 ─────────────────────────────────────────────────
        stability = self._param_stability(all_optimal_params)
        oos_avg_sharpe = (
            sum(m.get("sharpe_ratio", 0) or 0 for m in all_oos_metrics)
            / max(len(all_oos_metrics), 1)
        )
        oos_avg_return = (
            sum(m.get("total_return", 0) or 0 for m in all_oos_metrics)
            / max(len(all_oos_metrics), 1)
        )

        selected = (all_optimal_params[-1] if all_optimal_params else {})

        summary = {
            "n_windows_completed": len(self.windows),
            "param_stability_score": round(stability, 4),
            "oos_avg_sharpe": round(oos_avg_sharpe, 4),
            "oos_avg_total_return": round(oos_avg_return, 4),
            "optimal_params": {k: str(v) for k, v in all_optimal_params[-1].items()}
            if all_optimal_params else {},
            "recommended_params": selected,
            "windows": self.windows,
        }

        logger.info("Walk-Forward 完成")
        logger.info("  参数稳定性: %.4f (越小越稳定)", stability)
        logger.info("  OOS 平均夏普: %.4f", oos_avg_sharpe)
        logger.info("  推荐参数: %s", selected)

        return summary

    @staticmethod
    def _param_stability(params_list: List[Dict[str, Any]]) -> float:
        """计算参数稳定性：各参数在窗口间的归一化标准差的平均。"""
        if len(params_list) < 2:
            return 0.0

        all_keys = set()
        for p in params_list:
            all_keys.update(p.keys())

        variances = []
        for key in all_keys:
            vals = [float(p.get(key, 0)) for p in params_list]
            mean_v = sum(vals) / len(vals)
            if mean_v == 0:
                continue
            variances.append((sum((v - mean_v) ** 2 for v in vals) / len(vals)) ** 0.5 / abs(mean_v))

        return sum(variances) / max(len(variances), 1)

    def _generate_windows(self) -> List[tuple]:
        """根据回测区间生成 Walk-Forward 窗口。"""
        start = pd.to_datetime(self.bt_config.start_date)
        end = pd.to_datetime(self.bt_config.end_date)
        total_months = (end.year - start.year) * 12 + (end.month - start.month)

        if total_months < self.is_months + self.oos_months:
            return []

        step_months = max(1, self.oos_months)
        windows = []

        for i in range(self.n_windows):
            is_start = start + pd.DateOffset(months=i * step_months)
            is_end = is_start + pd.DateOffset(months=self.is_months) - pd.DateOffset(days=1)
            oos_start = is_end + pd.DateOffset(days=1)
            oos_end = oos_start + pd.DateOffset(months=self.oos_months) - pd.DateOffset(days=1)

            if oos_end > end:
                break

            windows.append((is_start, is_end, oos_start, oos_end))

        return windows
