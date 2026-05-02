"""
strategies/b2/selector.py
B2 策略：前一日满足 B1 条件 + 当日涨幅放量突破。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from pipeline.Selector import PipelineSelector, _apply_vec_filters
from strategies.b1.selector import B1Selector


# ── B2 专属 Filter ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class B1YesterdayFilter:
    """前一日满足 B1 选股条件（通过 _b1_vec_pick 列检查）。"""

    def __call__(self, hist: pd.DataFrame) -> bool:
        if len(hist) < 2 or "_b1_vec_pick" not in hist.columns:
            return False
        return bool(hist["_b1_vec_pick"].iloc[-2])

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        b1_pick = df["_b1_vec_pick"].to_numpy(dtype=bool)
        yesterday = np.zeros(len(df), dtype=bool)
        yesterday[1:] = b1_pick[:-1]
        return yesterday


@dataclass(frozen=True)
class DailyGainFilter:
    """当日涨幅 >= threshold（默认 3.85%）。"""
    threshold: float = 0.0385

    def __call__(self, hist: pd.DataFrame) -> bool:
        if len(hist) < 2:
            return False
        c0 = float(hist["close"].iloc[-1])
        c1 = float(hist["close"].iloc[-2])
        return (c0 / c1 - 1.0) >= self.threshold if c1 > 0 else False

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        cv = df["close"].to_numpy(dtype=float)
        cp = np.empty_like(cv); cp[0] = np.nan; cp[1:] = cv[:-1]
        return (cv / cp - 1.0) >= self.threshold


@dataclass(frozen=True)
class VolumeIncreaseFilter:
    """当日成交量 > 前一日成交量。"""

    def __call__(self, hist: pd.DataFrame) -> bool:
        if len(hist) < 2:
            return False
        return float(hist["volume"].iloc[-1]) > float(hist["volume"].iloc[-2])

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        vol = df["volume"].to_numpy(dtype=float)
        vp = np.empty_like(vol); vp[0] = np.nan; vp[1:] = vol[:-1]
        return vol > vp


@dataclass(frozen=True)
class KDJJLimitFilter:
    """KDJ J 值 < max_j（默认 80）。"""
    max_j: float = 80.0

    def __call__(self, hist: pd.DataFrame) -> bool:
        if "J" not in hist.columns:
            return False
        return float(hist["J"].iloc[-1]) < self.max_j

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        return df["J"].to_numpy(dtype=float) < self.max_j


# ── B2Selector ──────────────────────────────────────────────────────────────

class B2Selector(PipelineSelector):
    """
    B2 选股器（B1 衍生）：
      ① B1YesterdayFilter   — 前一日满足 B1 选股条件
      ② DailyGainFilter      — 当日涨幅 >= 3.85%
      ③ VolumeIncreaseFilter — 当日放量
      ④ KDJJLimitFilter      — KDJ J 值 < 80
    """

    def __init__(
        self,
        *,
        # B2 参数
        daily_gain_threshold: float = 0.0385,
        j_max:               float = 80.0,
        # B1 参数（透传给内部 B1Selector）
        j_threshold:          float = 20.0,
        j_q_threshold:        float = 0.10,
        kdj_n:                int   = 9,
        zx_m1:                int   = 14,
        zx_m2:                int   = 28,
        zx_m3:                int   = 57,
        zx_m4:                int   = 114,
        zxdq_span:            int   = 10,
        require_close_gt_long: bool = True,
        require_short_gt_long: bool = True,
        wma_short:            int   = 5,
        wma_mid:              int   = 10,
        wma_long:             int   = 20,
        max_vol_lookback:     Optional[int] = 20,
        date_col:          str = "date",
        extra_bars_buffer: int = 20,
    ) -> None:
        # 内部 B1Selector（用于"前一日满足 B1"的判断）
        self._b1 = B1Selector(
            j_threshold=j_threshold,
            j_q_threshold=j_q_threshold,
            kdj_n=kdj_n,
            zx_m1=zx_m1, zx_m2=zx_m2, zx_m3=zx_m3, zx_m4=zx_m4,
            zxdq_span=zxdq_span,
            require_close_gt_long=require_close_gt_long,
            require_short_gt_long=require_short_gt_long,
            wma_short=wma_short, wma_mid=wma_mid, wma_long=wma_long,
            max_vol_lookback=max_vol_lookback,
            date_col=date_col,
            extra_bars_buffer=extra_bars_buffer,
        )

        self._b1_yesterday = B1YesterdayFilter()
        self._gain  = DailyGainFilter(threshold=daily_gain_threshold)
        self._vol   = VolumeIncreaseFilter()
        self._j_lim = KDJJLimitFilter(max_j=j_max)

        _b2_filters: list = [self._b1_yesterday, self._gain, self._vol, self._j_lim]
        super().__init__(
            filters=_b2_filters,
            date_col=date_col,
            min_bars=max(30, zx_m4),
            extra_bars_buffer=extra_bars_buffer,
        )

        self.daily_gain_threshold = daily_gain_threshold
        self.j_max = j_max

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        # 先跑 B1 预计算（得到 K/D/J、zxdq/zxdkx、wma_bull、_vec_pick）
        df = self._b1.prepare_df(df)
        # 重命名为 B1 专用列，避免 _vec_pick 冲突
        if "_vec_pick" in df.columns:
            df["_b1_vec_pick"] = df["_vec_pick"]
            df.drop(columns=["_vec_pick"], inplace=True)
        else:
            df["_b1_vec_pick"] = False
        # B2 向量化
        df["_vec_pick"] = _apply_vec_filters(df, [self._b1_yesterday, self._gain, self._vol, self._j_lim])
        return df
