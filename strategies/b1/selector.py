"""
strategies/b1/selector.py
B1 策略 Selector：KDJ 分位 + 知行线 + 周线多头排列。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from pipeline.Selector import (
    PipelineSelector,
    ZXConditionFilter,
    WeeklyMABullFilter,
    _apply_vec_filters,
    _max_vol_not_bearish,
    compute_kdj,
    compute_zx_lines,
    compute_weekly_ma_bull,
)


# ── KDJ 分位 Filter ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KDJQuantileFilter:
    """J 值过滤：今日 J < j_threshold OR 今日 J ≤ 历史累积 j_q_threshold 分位。"""
    j_threshold:   float = -5.0
    j_q_threshold: float = 0.10
    kdj_n:         int   = 9

    def _j_series(self, hist: pd.DataFrame) -> pd.Series:
        if "J" in hist.columns:
            return hist["J"].astype(float)
        return compute_kdj(hist, n=self.kdj_n)["J"].astype(float)

    def __call__(self, hist: pd.DataFrame) -> bool:
        j = self._j_series(hist).dropna()
        if j.empty:
            return False
        j_today = float(j.iloc[-1])
        j_q     = float(j.quantile(self.j_q_threshold))
        return (j_today < self.j_threshold) or (j_today <= j_q)

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        J = self._j_series(df)
        j_vals  = J.to_numpy(dtype=float)
        j_q_exp = J.expanding(min_periods=1).quantile(self.j_q_threshold).to_numpy(dtype=float)
        return (j_vals < self.j_threshold) | (j_vals <= j_q_exp)


# ── 最大成交量非阴线 Filter ────────────────────────────────────────────────

@dataclass(frozen=True)
class MaxVolNotBearishFilter:
    """过去 n 日成交量最大那天不为阴线（close >= open）。"""
    n: int = 20

    def __call__(self, hist: pd.DataFrame) -> bool:
        window = hist.tail(self.n)
        if window.empty or "volume" not in window.columns:
            return False
        idx_max_vol = window["volume"].idxmax()
        row = window.loc[idx_max_vol]
        return float(row["close"]) >= float(row["open"])

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        return _max_vol_not_bearish(
            df["volume"].to_numpy(dtype=np.float64),
            df["open"].to_numpy(dtype=np.float64),
            df["close"].to_numpy(dtype=np.float64),
            self.n,
        )


# ── B1Selector ──────────────────────────────────────────────────────────────

class B1Selector(PipelineSelector):
    """
    B1 选股器：
      ① KDJQuantileFilter    — J 值低位
      ② ZXConditionFilter    — close > zxdkx，zxdq > zxdkx
      ③ WeeklyMABullFilter   — 周线多头排列
      ④ MaxVolNotBearishFilter — 最大量非阴线
    """

    def __init__(
        self,
        j_threshold:          float = -5.0,
        j_q_threshold:        float = 0.10,
        kdj_n:                int   = 9,
        zx_m1:                int   = 10,
        zx_m2:                int   = 50,
        zx_m3:                int   = 200,
        zx_m4:                int   = 300,
        zxdq_span:            int   = 10,
        require_close_gt_long: bool = True,
        require_short_gt_long: bool = True,
        wma_short:            int   = 10,
        wma_mid:              int   = 20,
        wma_long:             int   = 30,
        max_vol_lookback:     Optional[int] = 20,
        *,
        date_col:          str = "date",
        extra_bars_buffer: int = 20,
    ) -> None:
        self._kdj_filter = KDJQuantileFilter(
            j_threshold=j_threshold, j_q_threshold=j_q_threshold, kdj_n=kdj_n,
        )
        self._zx_filter = ZXConditionFilter(
            zx_m1=zx_m1, zx_m2=zx_m2, zx_m3=zx_m3, zx_m4=zx_m4,
            zxdq_span=zxdq_span,
            require_close_gt_long=require_close_gt_long,
            require_short_gt_long=require_short_gt_long,
        )
        self._wma_filter = WeeklyMABullFilter(
            wma_short=wma_short, wma_mid=wma_mid, wma_long=wma_long,
        )
        self._max_vol_filter: MaxVolNotBearishFilter = MaxVolNotBearishFilter(n=max_vol_lookback)
        _b1_filters: list = [self._kdj_filter, self._zx_filter, self._wma_filter, self._max_vol_filter]
        super().__init__(
            filters=_b1_filters,
            date_col=date_col,
            min_bars=max(30, zx_m4),
            extra_bars_buffer=extra_bars_buffer,
        )
        self.kdj_n    = kdj_n
        self.zx_m1, self.zx_m2, self.zx_m3, self.zx_m4 = zx_m1, zx_m2, zx_m3, zx_m4
        self.zxdq_span = zxdq_span
        self.wma_short, self.wma_mid, self.wma_long = wma_short, wma_mid, wma_long

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        zs, zk = compute_zx_lines(
            df, self.zx_m1, self.zx_m2, self.zx_m3, self.zx_m4,
            zxdq_span=self.zxdq_span,
        )
        df["zxdq"] = zs; df["zxdkx"] = zk
        kdj = compute_kdj(df, n=self.kdj_n)
        df["K"] = kdj["K"]; df["D"] = kdj["D"]; df["J"] = kdj["J"]
        df["wma_bull"] = compute_weekly_ma_bull(
            df, ma_periods=(self.wma_short, self.wma_mid, self.wma_long)
        ).to_numpy()
        _b1_vec_filters: list = [self._kdj_filter, self._zx_filter, self._wma_filter]
        if self._max_vol_filter is not None:
            _b1_vec_filters.append(self._max_vol_filter)
        df["_vec_pick"] = _apply_vec_filters(df, _b1_vec_filters)
        return df
