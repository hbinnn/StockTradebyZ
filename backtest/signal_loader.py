"""
backtest/signal_loader.py
信号桥接器：复用 pipeline 基础设施（MarketDataPreparer、SelectorPickPrecomputer）
生成每日选股信号 {strategy: {date: [codes]}}。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .config import BacktestConfig

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys_path_added = False

logger = logging.getLogger(__name__)


def _ensure_path():
    global sys_path_added
    if not sys_path_added:
        import sys
        sys.path.insert(0, str(_PROJECT_ROOT / "pipeline"))
        sys_path_added = True


class SignalLoader:
    """生成每日选股信号，复用 pipeline 基础设施。

    优化：
    - prepare_base_only() 只跑一次（turnover_n），跨策略共享
    - apply_zx_wma_features() 对 brick 策略共享
    - SelectorPickPrecomputer 走向量化快速路径（vec_picks_from_prepared）
    """

    def __init__(self, config: BacktestConfig):
        self.config = config

    def load(
        self, strategy_params: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> Dict[str, Dict[pd.Timestamp, List[str]]]:
        """返回 {strategy_name: {date: [codes]}}。

        strategy_params: 可选，覆盖 config.strategy_params（优化器用）
        """
        _ensure_path()
        from pipeline_core import MarketDataPreparer, TopTurnoverPoolBuilder, SelectorPickPrecomputer

        start = pd.to_datetime(self.config.start_date)
        end   = pd.to_datetime(self.config.end_date)

        # ── 加载原始数据 ────────────────────────────────────────────
        raw = self._load_raw(end)

        # ── 只做一次基础预处理（turnover_n、时间切片、set_index）─────
        preparer = MarketDataPreparer(
            start_date=start, end_date=end,
            warmup_bars=250,
            n_turnover_days=self.config.n_turnover_days,
        )
        base = preparer.prepare_base_only(raw)
        logger.info("基础数据准备完成: %d 只股票", len(base))

        # ── 逐策略生成信号 ──────────────────────────────────────────
        params_map = strategy_params or self.config.strategy_params
        signals: Dict[str, Dict[pd.Timestamp, List[str]]] = {}

        for strategy_name in self.config.strategies:
            logger.info("生成 %s 策略信号...", strategy_name)
            try:
                selector = self._build_selector(strategy_name, params_map)
                if selector is None:
                    logger.warning("策略 %s 未配置参数，跳过", strategy_name)
                    continue

                prepared = preparer.apply_selector_features(base, selector)
                pool = TopTurnoverPoolBuilder(self.config.top_m).build(prepared)
                precomp = SelectorPickPrecomputer(
                    selector=selector, start_date=start, end_date=end,
                )
                picks = precomp.precompute(prepared, top_turnover_pool=pool)
                signals[strategy_name] = picks
                total_signals = sum(len(v) for v in picks.values())
                logger.info(
                    "%s: %d 个交易日有信号，共 %d 条",
                    strategy_name, len(picks), total_signals,
                )

                # 释放不再用的 prepared 数据，优化内存
                del prepared, pool, precomp, picks

            except Exception as exc:
                logger.error("策略 %s 信号生成失败: %s", strategy_name, exc)
                raise

        return signals

    def load_single(
        self,
        strategy_name: str,
        params: Dict[str, Any],
    ) -> Dict[pd.Timestamp, List[str]]:
        """单策略信号生成（优化器内循环用）。"""
        _ensure_path()
        from pipeline_core import MarketDataPreparer, TopTurnoverPoolBuilder, SelectorPickPrecomputer

        start = pd.to_datetime(self.config.start_date)
        end   = pd.to_datetime(self.config.end_date)

        raw = self._load_raw(end)
        preparer = MarketDataPreparer(
            start_date=start, end_date=end,
            warmup_bars=250,
            n_turnover_days=self.config.n_turnover_days,
        )
        base = preparer.prepare_base_only(raw)

        selector = self._build_selector(strategy_name, {strategy_name: params})
        if selector is None:
            raise ValueError(f"无法构建 {strategy_name} selector")

        prepared = preparer.apply_selector_features(base, selector)
        pool = TopTurnoverPoolBuilder(self.config.top_m).build(prepared)
        precomp = SelectorPickPrecomputer(
            selector=selector, start_date=start, end_date=end,
        )
        return precomp.precompute(prepared, top_turnover_pool=pool)

    # ── 内部方法 ─────────────────────────────────────────────────────

    def _load_raw(self, end: pd.Timestamp) -> Dict[str, pd.DataFrame]:
        from pipeline_core import MarketDataPreparer

        data_dir = self.config.data_dir
        if not os.path.isabs(data_dir):
            data_dir = str(_PROJECT_ROOT / data_dir)
        if not os.path.isdir(data_dir):
            raise FileNotFoundError(f"data_dir 不存在: {data_dir}")

        data: Dict[str, pd.DataFrame] = {}
        for fname in os.listdir(data_dir):
            if not fname.lower().endswith(".csv"):
                continue
            code = fname.rsplit(".", 1)[0].zfill(6)
            fpath = os.path.join(data_dir, fname)
            try:
                df = pd.read_csv(fpath)
                df.columns = [c.lower() for c in df.columns]
                if "date" not in df.columns:
                    continue
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)
                df = df[df["date"] <= end].reset_index(drop=True)
                if not df.empty:
                    data[code] = df
            except Exception:
                continue

        logger.info("加载原始数据: %d 只股票", len(data))
        return data

    def _build_selector(
        self, name: str, params_map: Dict[str, Dict[str, Any]],
    ):
        """从策略名称和参数字典构建对应的 Selector 实例。"""
        cfg = params_map.get(name, {})
        if not cfg:
            return None

        if name == "b1":
            from strategies.b1.selector import B1Selector
            return B1Selector(
                j_threshold=float(cfg.get("j_threshold", 20.0)),
                j_q_threshold=float(cfg.get("j_q_threshold", 0.10)),
                kdj_n=int(cfg.get("kdj_n", 9)),
                zx_m1=int(cfg.get("zx_m1", 14)),
                zx_m2=int(cfg.get("zx_m2", 28)),
                zx_m3=int(cfg.get("zx_m3", 57)),
                zx_m4=int(cfg.get("zx_m4", 114)),
                zxdq_span=int(cfg.get("zxdq_span", 10)),
                require_close_gt_long=bool(cfg.get("require_close_gt_long", True)),
                require_short_gt_long=bool(cfg.get("require_short_gt_long", True)),
                wma_short=int(cfg.get("wma_short", 10)),
                wma_mid=int(cfg.get("wma_mid", 20)),
                wma_long=int(cfg.get("wma_long", 30)),
                max_vol_lookback=cfg.get("max_vol_lookback", 20),
            )

        if name == "brick":
            from strategies.brick.selector import BrickChartSelector
            return BrickChartSelector(
                brick_growth_ratio=float(cfg.get("brick_growth_ratio", 0.5)),
                zxdq_ratio=cfg.get("zxdq_ratio"),
                zxdq_span=int(cfg.get("zxdq_span", 10)),
                require_zxdq_gt_zxdkx=bool(cfg.get("require_zxdq_gt_zxdkx", True)),
                require_close_gt_zxdq=bool(cfg.get("require_close_gt_zxdq", True)),
                zxdq_close_ratio=float(cfg.get("zxdq_close_ratio", 0.98)),
                zxdkx_m1=int(cfg.get("zxdkx_m1", 14)),
                zxdkx_m2=int(cfg.get("zxdkx_m2", 28)),
                zxdkx_m3=int(cfg.get("zxdkx_m3", 57)),
                zxdkx_m4=int(cfg.get("zxdkx_m4", 114)),
                require_weekly_ma_bull=bool(cfg.get("require_weekly_ma_bull", True)),
                wma_short=int(cfg.get("wma_short", 5)),
                wma_mid=int(cfg.get("wma_mid", 10)),
                wma_long=int(cfg.get("wma_long", 20)),
                n=int(cfg.get("n", 4)),
                m1=int(cfg.get("m1", 4)), m2=int(cfg.get("m2", 6)), m3=int(cfg.get("m3", 6)),
                t=float(cfg.get("t", 4.0)),
                shift1=float(cfg.get("shift1", 90.0)),
                shift2=float(cfg.get("shift2", 100.0)),
                sma_w1=int(cfg.get("sma_w1", 1)),
                sma_w2=int(cfg.get("sma_w2", 1)),
                sma_w3=int(cfg.get("sma_w3", 1)),
            )

        if name == "b2":
            from strategies.b2.selector import B2Selector
            return B2Selector(
                daily_gain_threshold=float(cfg.get("daily_gain_threshold", 0.0385)),
                j_max=float(cfg.get("j_max", 80.0)),
                j_threshold=float(cfg.get("j_threshold", 20.0)),
                j_q_threshold=float(cfg.get("j_q_threshold", 0.10)),
                kdj_n=int(cfg.get("kdj_n", 9)),
                zx_m1=int(cfg.get("zx_m1", 14)),
                zx_m2=int(cfg.get("zx_m2", 28)),
                zx_m3=int(cfg.get("zx_m3", 57)),
                zx_m4=int(cfg.get("zx_m4", 114)),
                zxdq_span=int(cfg.get("zxdq_span", 10)),
                require_close_gt_long=bool(cfg.get("require_close_gt_long", True)),
                require_short_gt_long=bool(cfg.get("require_short_gt_long", True)),
                wma_short=int(cfg.get("wma_short", 5)),
                wma_mid=int(cfg.get("wma_mid", 10)),
                wma_long=int(cfg.get("wma_long", 20)),
                max_vol_lookback=cfg.get("max_vol_lookback", 20),
            )

        if name == "b3":
            from strategies.b3.selector import B3Selector
            return B3Selector(
                max_change=float(cfg.get("max_change", 0.02)),
                max_amplitude=float(cfg.get("max_amplitude", 0.07)),
                max_vol_ratio=float(cfg.get("max_vol_ratio", 0.70)),
                daily_gain_threshold=float(cfg.get("daily_gain_threshold", 0.0385)),
                j_max=float(cfg.get("j_max", 80.0)),
                j_threshold=float(cfg.get("j_threshold", 20.0)),
                j_q_threshold=float(cfg.get("j_q_threshold", 0.10)),
                kdj_n=int(cfg.get("kdj_n", 9)),
                zx_m1=int(cfg.get("zx_m1", 14)),
                zx_m2=int(cfg.get("zx_m2", 28)),
                zx_m3=int(cfg.get("zx_m3", 57)),
                zx_m4=int(cfg.get("zx_m4", 114)),
                zxdq_span=int(cfg.get("zxdq_span", 10)),
                require_close_gt_long=bool(cfg.get("require_close_gt_long", True)),
                require_short_gt_long=bool(cfg.get("require_short_gt_long", True)),
                wma_short=int(cfg.get("wma_short", 5)),
                wma_mid=int(cfg.get("wma_mid", 10)),
                wma_long=int(cfg.get("wma_long", 20)),
                max_vol_lookback=cfg.get("max_vol_lookback", 20),
            )

        raise ValueError(f"未知策略: {name}")
