"""
strategies/brick/selector.py
砖型图策略 Selector：砖型图形态 + 知行线位置 + 周线多头排列。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from pipeline.Selector import (
    PipelineSelector,
    ZXConditionFilter,
    WeeklyMABullFilter,
    _apply_vec_filters,
    _compute_brick_numba,
    compute_brick_chart,
    compute_zx_lines,
    compute_weekly_ma_bull,
)


# ── 砖型图计算参数 ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BrickComputeParams:
    """砖型图计算参数容器。"""
    n:      int   = 4
    m1:     int   = 4
    m2:     int   = 6
    m3:     int   = 6
    t:      float = 4.0
    shift1: float = 90.0
    shift2: float = 100.0
    sma_w1: int   = 1
    sma_w2: int   = 1
    sma_w3: int   = 1

    def compute(self, df: pd.DataFrame) -> pd.Series:
        return compute_brick_chart(
            df, n=self.n, m1=self.m1, m2=self.m2, m3=self.m3,
            t=self.t, shift1=self.shift1, shift2=self.shift2,
            sma_w1=self.sma_w1, sma_w2=self.sma_w2, sma_w3=self.sma_w3,
        )

    def compute_arr(self, df: pd.DataFrame) -> np.ndarray:
        return _compute_brick_numba(
            df["high"].to_numpy(dtype=np.float64),
            df["low"].to_numpy(dtype=np.float64),
            df["close"].to_numpy(dtype=np.float64),
            self.n, self.m1, self.m2, self.m3,
            float(self.t), float(self.shift1), float(self.shift2),
            self.sma_w1, self.sma_w2, self.sma_w3,
        )


# ── 砖型图形态 Filter ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class BrickPatternFilter:
    """
    砖型图形态过滤：
      1. 今日红柱（brick > 0）
      2. 昨日绿柱（brick[-1] < 0）
      3. 今日红柱高度 >= brick_growth_ratio × 昨日绿柱绝对高度
    """
    brick_growth_ratio: float = 0.5
    brick_params: BrickComputeParams = field(default_factory=BrickComputeParams)

    def _brick_arr(self, hist: pd.DataFrame) -> np.ndarray:
        if "brick" in hist.columns:
            return hist["brick"].to_numpy(dtype=float)
        return self.brick_params.compute_arr(hist)

    def __call__(self, hist: pd.DataFrame) -> bool:
        if len(hist) < 3:
            return False
        vals = self._brick_arr(hist)
        b0, b1 = vals[-1], vals[-2]
        if not (b0 > 0 and b1 < 0):
            return False
        if b0 < self.brick_growth_ratio * abs(b1):
            return False
        return True

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        bv  = self._brick_arr(df)
        bp  = np.empty_like(bv);  bp[0]  = np.nan; bp[1:]  = bv[:-1]
        abp = np.abs(bp)
        cond_red    = bv > 0
        cond_green  = bp < 0
        cond_growth = bv >= self.brick_growth_ratio * abp
        return cond_red & cond_green & cond_growth

    def brick_growth_arr(self, df: pd.DataFrame) -> np.ndarray:
        bv  = self._brick_arr(df)
        bp  = np.empty_like(bv); bp[0] = np.nan; bp[1:] = bv[:-1]
        abp = np.abs(bp)
        safe = np.where(abp > 0, abp, 1.0)
        return np.where(abp > 0, bv / safe, bv)


# ── 知行线位置 Filter ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class ZXDQRatioFilter:
    """close < zxdq × zxdq_ratio。"""
    zxdq_ratio: float = 1.0
    zxdq_span:  int   = 10
    zxdkx_m1: int = 14; zxdkx_m2: int = 28; zxdkx_m3: int = 57; zxdkx_m4: int = 114

    def _zxdq_arr(self, df: pd.DataFrame) -> np.ndarray:
        if "zxdq" in df.columns:
            return df["zxdq"].to_numpy(dtype=float)
        zs, _ = compute_zx_lines(
            df, self.zxdkx_m1, self.zxdkx_m2, self.zxdkx_m3, self.zxdkx_m4,
            zxdq_span=self.zxdq_span,
        )
        return zs.to_numpy(dtype=float)

    def __call__(self, hist: pd.DataFrame) -> bool:
        zxdq_arr = self._zxdq_arr(hist)
        zv = float(zxdq_arr[-1])
        if not np.isfinite(zv) or zv <= 0:
            return False
        return float(hist["close"].iloc[-1]) < zv * self.zxdq_ratio

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        zxdq_v  = self._zxdq_arr(df)
        close_v = df["close"].to_numpy(dtype=float)
        return np.isfinite(zxdq_v) & (zxdq_v > 0) & (close_v < zxdq_v * self.zxdq_ratio)


@dataclass(frozen=True)
class CloseAboveZXDQFilter:
    """close >= zxdq * ratio（默认 0.98）。"""
    ratio: float = 0.98

    def _zxdq_arr(self, df: pd.DataFrame) -> np.ndarray:
        return df["zxdq"].to_numpy(dtype=float)

    def __call__(self, hist: pd.DataFrame) -> bool:
        zv = float(hist["zxdq"].iloc[-1])
        if not np.isfinite(zv) or zv <= 0:
            return False
        return float(hist["close"].iloc[-1]) >= zv * self.ratio

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        zxdq_v  = self._zxdq_arr(df)
        close_v = df["close"].to_numpy(dtype=float)
        return np.isfinite(zxdq_v) & (zxdq_v > 0) & (close_v >= zxdq_v * self.ratio)


# ── BrickChartSelector ──────────────────────────────────────────────────────

class BrickChartSelector(PipelineSelector):
    """
    砖型图选股器，由以下五个独立模块组成：
      ① BrickPatternFilter    — 形态（红/绿柱 + 增长倍数）
      ② ZXDQRatioFilter       — close < zxdq × ratio          [可选]
      ③ ZXConditionFilter     — zxdq > zxdkx                  [可选]
      ④ CloseAboveZXDQFilter  — close >= zxdq × ratio         [可选]
      ⑤ WeeklyMABullFilter    — 周线多头排列                    [可选]
    """

    def __init__(
        self,
        *,
        brick_growth_ratio: float = 0.5,
        n:      int   = 4,  m1: int = 4, m2: int = 6, m3: int = 6,
        t:      float = 4.0, shift1: float = 90.0, shift2: float = 100.0,
        sma_w1: int   = 1,   sma_w2: int = 1, sma_w3: int = 1,
        zxdq_span:  int = 10,
        zxdkx_m1: int = 14, zxdkx_m2: int = 28,
        zxdkx_m3: int = 57, zxdkx_m4: int = 114,
        zxdq_ratio:             Optional[float] = 1.0,
        require_zxdq_gt_zxdkx: bool = True,
        require_close_gt_zxdq:  bool = True,
        zxdq_close_ratio:       float = 0.98,
        require_weekly_ma_bull: bool = True,
        wma_short: int = 20, wma_mid: int = 60, wma_long: int = 120,
        date_col:          str = "date",
        extra_bars_buffer: int = 10,
    ) -> None:
        self._bp = BrickComputeParams(
            n=n, m1=m1, m2=m2, m3=m3,
            t=t, shift1=shift1, shift2=shift2,
            sma_w1=sma_w1, sma_w2=sma_w2, sma_w3=sma_w3,
        )
        self._pattern_filter = BrickPatternFilter(
            brick_growth_ratio=brick_growth_ratio,
            brick_params=self._bp,
        )
        self._zxdq_ratio_filter: Optional[ZXDQRatioFilter] = (
            ZXDQRatioFilter(
                zxdq_ratio=zxdq_ratio, zxdq_span=zxdq_span,
                zxdkx_m1=zxdkx_m1, zxdkx_m2=zxdkx_m2,
                zxdkx_m3=zxdkx_m3, zxdkx_m4=zxdkx_m4,
            ) if zxdq_ratio is not None else None
        )
        self._zxdq_gt_filter: Optional[ZXConditionFilter] = (
            ZXConditionFilter(
                zx_m1=zxdkx_m1, zx_m2=zxdkx_m2,
                zx_m3=zxdkx_m3, zx_m4=zxdkx_m4,
                zxdq_span=zxdq_span,
                require_close_gt_long=False,
                require_short_gt_long=True,
            ) if require_zxdq_gt_zxdkx else None
        )
        self._close_gt_zxdq_filter: Optional[CloseAboveZXDQFilter] = (
            CloseAboveZXDQFilter(ratio=zxdq_close_ratio)
            if require_close_gt_zxdq else None
        )
        self._wma_filter: Optional[WeeklyMABullFilter] = (
            WeeklyMABullFilter(wma_short=wma_short, wma_mid=wma_mid, wma_long=wma_long)
            if require_weekly_ma_bull else None
        )

        _filters: list = [self._pattern_filter]
        if self._zxdq_ratio_filter      is not None: _filters.append(self._zxdq_ratio_filter)
        if self._zxdq_gt_filter         is not None: _filters.append(self._zxdq_gt_filter)
        if self._close_gt_zxdq_filter   is not None: _filters.append(self._close_gt_zxdq_filter)
        if self._wma_filter             is not None: _filters.append(self._wma_filter)

        super().__init__(
            _filters, date_col=date_col,
            min_bars=max(n + 3, zxdkx_m4, wma_long * 5),
            extra_bars_buffer=extra_bars_buffer,
        )
        self.zxdq_span  = zxdq_span
        self.zxdkx_m1, self.zxdkx_m2 = zxdkx_m1, zxdkx_m2
        self.zxdkx_m3, self.zxdkx_m4 = zxdkx_m3, zxdkx_m4
        self.wma_short, self.wma_mid, self.wma_long = wma_short, wma_mid, wma_long
        self.require_weekly_ma_bull = require_weekly_ma_bull

    # ── 预计算 ─────────────────────────────────────────────────────────────

    def _precompute_zx_wma(self, df: pd.DataFrame) -> None:
        zs, zk = compute_zx_lines(
            df, self.zxdkx_m1, self.zxdkx_m2, self.zxdkx_m3, self.zxdkx_m4,
            zxdq_span=self.zxdq_span,
        )
        df["zxdq"] = zs; df["zxdkx"] = zk
        if self.require_weekly_ma_bull:
            df["wma_bull"] = compute_weekly_ma_bull(
                df, ma_periods=(self.wma_short, self.wma_mid, self.wma_long)
            ).to_numpy()

    def _precompute_brick(self, df: pd.DataFrame) -> None:
        bv   = self._bp.compute_arr(df)
        bp_  = np.empty_like(bv); bp_[0] = np.nan; bp_[1:] = bv[:-1]
        abp  = np.abs(bp_)
        safe = np.where(abp > 0, abp, 1.0)
        df["brick"]        = bv
        df["brick_growth"] = np.where(abp > 0, bv / safe, bv)

    def _compute_vec_pick(self, df: pd.DataFrame) -> np.ndarray:
        fs: list = [self._pattern_filter]
        if self._zxdq_ratio_filter      is not None: fs.append(self._zxdq_ratio_filter)
        if self._zxdq_gt_filter         is not None: fs.append(self._zxdq_gt_filter)
        if self._close_gt_zxdq_filter   is not None: fs.append(self._close_gt_zxdq_filter)
        if self._wma_filter             is not None: fs.append(self._wma_filter)
        return _apply_vec_filters(df, fs)

    # ── 公开接口 ───────────────────────────────────────────────────────────

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        self._precompute_zx_wma(df)
        self._precompute_brick(df)
        df["_vec_pick"] = self._compute_vec_pick(df)
        return df

    def prepare_df_brick_only(self, df: pd.DataFrame) -> pd.DataFrame:
        self._precompute_brick(df)
        df["_vec_pick"] = self._compute_vec_pick(df)
        return df

    def brick_growth_on_date(self, df: pd.DataFrame, date: pd.Timestamp) -> float:
        hist = self._get_hist(df, date)
        if len(hist) < 3:
            return -np.inf
        if "brick_growth" in hist.columns:
            val = float(hist["brick_growth"].iloc[-1])
            return val if np.isfinite(val) else -np.inf
        return float(self._pattern_filter.brick_growth_arr(hist)[-1])
