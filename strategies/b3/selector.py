"""
strategies/b3/selector.py
B3 策略：前一日满足 B2 条件 + 当日缩量小K线休整。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from pipeline.Selector import PipelineSelector, _apply_vec_filters
from strategies.b2.selector import B2Selector


# ── B3 专属 Filter ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class B2YesterdayFilter:
    """前一日满足 B2 选股条件（通过内部 B2Selector 的 _vec_pick 检查）。"""

    def __call__(self, hist: pd.DataFrame) -> bool:
        if len(hist) < 2 or "_b2_vec_pick" not in hist.columns:
            return False
        return bool(hist["_b2_vec_pick"].iloc[-2])

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        b2_pick = df["_b2_vec_pick"].to_numpy(dtype=bool)
        yesterday = np.zeros(len(df), dtype=bool)
        yesterday[1:] = b2_pick[:-1]
        return yesterday


@dataclass(frozen=True)
class SmallCandleFilter:
    """
    当日为小K线（小阳/小阴/十字星）：
      - 涨跌幅绝对值 <= max_change（默认 2%）
      - 振幅 <= max_amplitude（默认 7%）
    """
    max_change:    float = 0.02
    max_amplitude: float = 0.07

    def __call__(self, hist: pd.DataFrame) -> bool:
        if len(hist) < 2:
            return False
        c0 = float(hist["close"].iloc[-1])
        c1 = float(hist["close"].iloc[-2])
        h0 = float(hist["high"].iloc[-1])
        l0 = float(hist["low"].iloc[-1])
        if c1 <= 0 or l0 <= 0:
            return False
        change = c0 / c1 - 1.0
        amplitude = (h0 - l0) / l0
        return abs(change) <= self.max_change and amplitude <= self.max_amplitude

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        cv = df["close"].to_numpy(dtype=float)
        hv = df["high"].to_numpy(dtype=float)
        lv = df["low"].to_numpy(dtype=float)
        cp = np.empty_like(cv); cp[0] = np.nan; cp[1:] = cv[:-1]
        change = cv / cp - 1.0
        amplitude = (hv - lv) / lv
        return (np.abs(change) <= self.max_change) & (amplitude <= self.max_amplitude)


@dataclass(frozen=True)
class VolumeShrinkFilter:
    """当日成交量 < 前一日成交量 × max_ratio（默认 0.70，即缩量至 70% 以下）。"""
    max_ratio: float = 0.70

    def __call__(self, hist: pd.DataFrame) -> bool:
        if len(hist) < 2:
            return False
        v0 = float(hist["volume"].iloc[-1])
        v1 = float(hist["volume"].iloc[-2])
        return v0 < v1 * self.max_ratio if v1 > 0 else False

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        vol = df["volume"].to_numpy(dtype=float)
        vp = np.empty_like(vol); vp[0] = np.nan; vp[1:] = vol[:-1]
        return vol < vp * self.max_ratio


# ── B3Selector ──────────────────────────────────────────────────────────────

class B3Selector(PipelineSelector):
    """
    B3 选股器（B2 衍生）：
      ① B2YesterdayFilter  — 前一日满足 B2 选股条件
      ② SmallCandleFilter   — 当日小K线（涨跌±2%内，振幅≤7%）
      ③ VolumeShrinkFilter  — 当日缩量（< 前日 70%）
    """

    def __init__(
        self,
        *,
        # B3 参数
        max_change:    float = 0.02,
        max_amplitude: float = 0.07,
        max_vol_ratio: float = 0.70,
        # B2 参数（透传给内部 B2Selector）
        daily_gain_threshold: float = 0.0385,
        j_max:               float = 80.0,
        # B1 参数（透传给 B2 → B1）
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
        # 内部 B2Selector（用于"前一日满足 B2"的判断）
        self._b2 = B2Selector(
            daily_gain_threshold=daily_gain_threshold,
            j_max=j_max,
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

        self._b2_yesterday = B2YesterdayFilter()
        self._candle = SmallCandleFilter(max_change=max_change, max_amplitude=max_amplitude)
        self._vol    = VolumeShrinkFilter(max_ratio=max_vol_ratio)

        _b3_filters: list = [self._b2_yesterday, self._candle, self._vol]
        super().__init__(
            filters=_b3_filters,
            date_col=date_col,
            min_bars=max(30, zx_m4),
            extra_bars_buffer=extra_bars_buffer,
        )

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        # 先跑 B2 预计算（内部包含 B1 预计算）
        df = self._b2.prepare_df(df)
        # 保留 B2 的 _vec_pick
        if "_vec_pick" in df.columns:
            df["_b2_vec_pick"] = df["_vec_pick"]
            df.drop(columns=["_vec_pick"], inplace=True)
        else:
            df["_b2_vec_pick"] = False
        # B3 向量化
        df["_vec_pick"] = _apply_vec_filters(df, [self._b2_yesterday, self._candle, self._vol])
        return df
