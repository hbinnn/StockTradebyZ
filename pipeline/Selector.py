"""
Selector.py — 选股框架基类 + 共享 Filter + Numba 加速核心函数

策略专属 Selector 位于 strategies/{b1,brick}/selector.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Sequence

import numpy as np
import pandas as pd
from numba import njit as _njit

# =============================================================================
# Numba 加速核心函数
# =============================================================================

# ── KDJ 核心递推 ──────────────────────────────────────────────────────────
@_njit(cache=True)
def _kdj_core(rsv: np.ndarray) -> tuple:          # noqa: UP006
    n = len(rsv)
    K = np.empty(n, dtype=np.float64)
    D = np.empty(n, dtype=np.float64)
    K[0] = D[0] = 50.0
    for i in range(1, n):
        K[i] = 2.0 / 3.0 * K[i - 1] + 1.0 / 3.0 * rsv[i]
        D[i] = 2.0 / 3.0 * D[i - 1] + 1.0 / 3.0 * K[i]
    J = 3.0 * K - 2.0 * D
    return K, D, J

# ── 成交量最大日非阴线核心 ───────────────────────────────────────────────
@_njit(cache=True)
def _max_vol_not_bearish(
    vol: np.ndarray, open_: np.ndarray, close: np.ndarray, n: int,
) -> np.ndarray:
    """滚动 n 日窗口内，成交量最大那天不为阴线（close >= open）。"""
    length = len(vol)
    mask = np.zeros(length, dtype=np.bool_)
    for i in range(length):
        start = max(0, i - n + 1)
        max_v   = vol[start]
        max_idx = start
        for j in range(start + 1, i + 1):
            if vol[j] > max_v:
                max_v   = vol[j]
                max_idx = j
        mask[i] = close[max_idx] >= open_[max_idx]
    return mask

# ── 砖型图核心 ────────────────────────────────────────────────────────────
@_njit(cache=True)
def _compute_brick_numba(
    high: np.ndarray, low: np.ndarray, close: np.ndarray,
    n: int, m1: int, m2: int, m3: int,
    t: float, shift1: float, shift2: float,
    sma_w1: int, sma_w2: int, sma_w3: int,
) -> np.ndarray:
        length = len(close)
        hhv = np.empty(length, dtype=np.float64)
        llv = np.empty(length, dtype=np.float64)
        for i in range(length):
            start = max(0, i - n + 1)
            h_max = high[start]; l_min = low[start]
            for j in range(start + 1, i + 1):
                if high[j] > h_max: h_max = high[j]
                if low[j]  < l_min: l_min = low[j]
            hhv[i] = h_max; llv[i] = l_min

        a1 = sma_w1 / m1; b1 = 1.0 - a1
        var2a = np.empty(length, dtype=np.float64)
        for i in range(length):
            rng = hhv[i] - llv[i]
            if rng == 0.0: rng = 0.01
            v1 = (hhv[i] - close[i]) / rng * 100.0 - shift1
            var2a[i] = (v1 + shift2) if i == 0 else (a1 * v1 + b1 * (var2a[i - 1] - shift2) + shift2)

        a2 = sma_w2 / m2; b2 = 1.0 - a2
        a3 = sma_w3 / m3; b3 = 1.0 - a3
        var4a = np.empty(length, dtype=np.float64)
        var5a = np.empty(length, dtype=np.float64)
        for i in range(length):
            rng = hhv[i] - llv[i]
            if rng == 0.0: rng = 0.01
            v3 = (close[i] - llv[i]) / rng * 100.0
            if i == 0:
                var4a[i] = v3; var5a[i] = v3 + shift2
            else:
                var4a[i] = a2 * v3 + b2 * var4a[i - 1]
                var5a[i] = a3 * var4a[i] + b3 * (var5a[i - 1] - shift2) + shift2

        raw = np.empty(length, dtype=np.float64)
        for i in range(length):
            diff = var5a[i] - var2a[i]
            raw[i] = diff - t if diff > t else 0.0

        brick = np.empty(length, dtype=np.float64)
        brick[0] = 0.0
        for i in range(1, length):
            brick[i] = raw[i] - raw[i - 1]
        return brick


# =============================================================================
# 指标计算辅助函数
# =============================================================================

def compute_kdj(df: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    """返回带 K/D/J 列的 DataFrame（Numba 加速 KDJ 递推）。"""
    if df.empty:
        return df.assign(K=np.nan, D=np.nan, J=np.nan)
    low_n  = df["low"].rolling(window=n, min_periods=1).min()
    high_n = df["high"].rolling(window=n, min_periods=1).max()
    rsv    = ((df["close"] - low_n) / (high_n - low_n + 1e-9) * 100).to_numpy(dtype=np.float64)

    K, D, J = _kdj_core(rsv)
    return df.assign(K=K, D=D, J=J)


def _tdx_sma(series: pd.Series, period: int, weight: int = 1) -> pd.Series:
    """通达信 SMA(X,N,M)，alpha = weight/period。"""
    return series.ewm(alpha=weight / period, adjust=False).mean()


def compute_zx_lines(
    df: pd.DataFrame,
    m1: int = 14, m2: int = 28, m3: int = 57, m4: int = 114,
    zxdq_span: int = 10,
) -> tuple[pd.Series, pd.Series]:
    """返回 (zxdq, zxdkx)。zxdq=double-EWM；zxdkx=四均线平均。"""
    close = df["close"].astype(float)
    zxdq  = close.ewm(span=zxdq_span, adjust=False).mean().ewm(span=zxdq_span, adjust=False).mean()
    zxdkx = (
        close.rolling(m1, min_periods=m1).mean()
        + close.rolling(m2, min_periods=m2).mean()
        + close.rolling(m3, min_periods=m3).mean()
        + close.rolling(m4, min_periods=m4).mean()
    ) / 4.0
    return zxdq, zxdkx


def compute_weekly_close(df: pd.DataFrame) -> pd.Series:
    """日线 → 周线收盘价（每周最后一个实际交易日）。

    不依赖固定 resample 锚点（周日/周五），而是直接按
    ISO 周编号分组取最后一行，index 保持为真实交易日日期。
    """
    close = (
        df["close"].astype(float)
        if isinstance(df.index, pd.DatetimeIndex)
        else df.set_index("date")["close"].astype(float)
    )
    # 按 ISO 年+周分组，取每组最后一个交易日的收盘价
    # isocalendar().week 返回 1-53，加上年份避免跨年混淆
    idx = close.index
    year_week = idx.isocalendar().year.astype(str) + "-" + idx.isocalendar().week.astype(str).str.zfill(2)
    weekly = close.groupby(year_week).last()
    # 将 index 换回真实日期（每周最后交易日）
    last_date_per_week = close.groupby(year_week).apply(lambda s: s.index[-1])
    weekly.index = pd.DatetimeIndex(last_date_per_week.values)
    return weekly.dropna()


def compute_weekly_ma_bull(
    df: pd.DataFrame,
    ma_periods: tuple[int, int, int] = (20, 60, 120),
) -> pd.Series:
    """
    周线均线多头排列标志（MA_short > MA_mid > MA_long），
    forward-fill 到日线 index，返回 bool Series。

    周线收盘价 index 为真实交易日，reindex 后 ffill 可正确对齐。
    """
    weekly_close = compute_weekly_close(df)
    s, m, l = ma_periods
    ma_s = weekly_close.rolling(s, min_periods=s).mean()
    ma_m = weekly_close.rolling(m, min_periods=m).mean()
    ma_l = weekly_close.rolling(l, min_periods=l).mean()
    bull = (ma_s > ma_m) & (ma_m > ma_l)

    daily_index = (
        df.index if isinstance(df.index, pd.DatetimeIndex)
        else pd.DatetimeIndex(df["date"])
    )
    # 转 float（1.0/0.0/NaN）→ reindex → ffill → 填 0 → bool
    # 避免 bool reindex 后升级为 object dtype 触发 FutureWarning
    bull_daily = (
        bull.astype(float)
        .reindex(daily_index)
        .ffill()
        .fillna(0.0)
        .astype(bool)
    )
    return bull_daily


def compute_brick_chart(
    df: pd.DataFrame,
    *,
    n: int = 4, m1: int = 4, m2: int = 6, m3: int = 6,
    t: float = 4.0, shift1: float = 90.0, shift2: float = 100.0,
    sma_w1: int = 1, sma_w2: int = 1, sma_w3: int = 1,
) -> pd.Series:
    """通达信砖型图公式 → 砖高 Series（red>0，green<0）。"""
    arr = _compute_brick_numba(
        df["high"].to_numpy(dtype=np.float64),
        df["low"].to_numpy(dtype=np.float64),
        df["close"].to_numpy(dtype=np.float64),
        n, m1, m2, m3, float(t), float(shift1), float(shift2),
        sma_w1, sma_w2, sma_w3,
    )
    return pd.Series(arr, index=df.index, name="brick")



# =============================================================================
# Protocol / 基类
# =============================================================================

class StockFilter(Protocol):
    """单股票过滤器：给定截至 date 的历史 DataFrame，返回是否通过。"""
    def __call__(self, hist: pd.DataFrame) -> bool: ...


class PipelineSelector:
    """
    通用 Selector 基类。

    子类通过 ``prepare_df()`` 预计算中间列（含 ``_vec_pick``），
    之后调用 ``vec_picks_from_prepared()`` 批量获取通过日期（回测提速 10-50×）。
    """

    def __init__(
        self,
        filters: Sequence[StockFilter],
        *,
        date_col: str = "date",
        min_bars: int = 1,
        extra_bars_buffer: int = 0,
    ) -> None:
        self.filters           = list(filters)
        self.date_col          = date_col
        self.min_bars          = int(min_bars)
        self.extra_bars_buffer = int(extra_bars_buffer)

    # ── 内部工具 ─────────────────────────────────────────────────────────────

    def _get_hist(self, df: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
        if self.date_col in df.columns:
            return df[df[self.date_col] <= date]
        if isinstance(df.index, pd.DatetimeIndex):
            return df.loc[:date]
        raise KeyError(
            f"DataFrame must have '{self.date_col}' column or a DatetimeIndex."
        )

    def _passes(self, hist: pd.DataFrame) -> bool:
        for f in self.filters:
            if not f(hist):
                return False
        return True

    # ── 公开 API ─────────────────────────────────────────────────────────────

    def get_hist(self, df: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
        return self._get_hist(df, date)

    def passes_hist(self, hist: pd.DataFrame) -> bool:
        if hist is None or hist.empty:
            return False
        if len(hist) < self.min_bars + self.extra_bars_buffer:
            return False
        return self._passes(hist)

    def passes_df_on_date(self, df: pd.DataFrame, date: pd.Timestamp) -> bool:
        return self.passes_hist(self._get_hist(df, date))

    def select(self, date: pd.Timestamp, data: Dict[str, pd.DataFrame]) -> List[str]:
        return [
            code for code, df in data.items()
            if self.passes_df_on_date(df, date)
        ]

    # ── 向量化批量接口（子类实现 prepare_df） ────────────────────────────────

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """子类重写：预计算所有中间列及 ``_vec_pick``。"""
        return df

    def vec_picks_from_prepared(
        self,
        df: pd.DataFrame,
        start: Optional[pd.Timestamp] = None,
        end: Optional[pd.Timestamp] = None,
    ) -> List[pd.Timestamp]:
        """从已 prepare_df 的 df 快速获取通过日期列表（比逐日调用快 10-50×）。"""
        if "_vec_pick" not in df.columns:
            return []
        mask = df["_vec_pick"].astype(bool)
        if start is not None:
            mask = mask & (df.index >= start)
        if end is not None:
            mask = mask & (df.index <= end)
        return list(df.index[mask])


# =============================================================================
# ── 独立 Filter 模块 ──────────────────────────────────────────────────────────
#
# 每个 Filter 提供两套接口：
#   __call__(hist: pd.DataFrame) -> bool       点查（含 fallback 计算，调试用）
#   vec_mask(df: pd.DataFrame)  -> np.ndarray  全量向量化（prepare_df 内调用）
#
# 策略专属 Filter（KDJQuantileFilter、MaxVolNotBearishFilter 等）
# 位于 strategies/{b1,brick}/selector.py。
# =============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# 1. 知行线条件过滤（B1 + 砖型图共用）
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ZXConditionFilter:
    """
    知行线过滤：
      - close > zxdkx（长期均线）      [require_close_gt_long]
      - zxdq  > zxdkx（快线在均线上）  [require_short_gt_long]

    优先读 df 中预计算列 'zxdq' / 'zxdkx'。
    """
    zx_m1:  int = 10
    zx_m2:  int = 50
    zx_m3:  int = 200
    zx_m4:  int = 300
    zxdq_span: int = 10
    require_close_gt_long: bool = True
    require_short_gt_long: bool = True

    def _zx_vals(self, hist: pd.DataFrame) -> tuple[float, float, float]:
        """返回 (zxdq, zxdkx, close) 最新值。"""
        c = float(hist["close"].iloc[-1])
        if "zxdq" in hist.columns and "zxdkx" in hist.columns:
            s = float(hist["zxdq"].iloc[-1])
            lv = hist["zxdkx"].iloc[-1]
            l  = float(lv) if pd.notna(lv) else float("nan")
        else:
            zxdq, zxdkx = compute_zx_lines(
                hist, self.zx_m1, self.zx_m2, self.zx_m3, self.zx_m4,
                zxdq_span=self.zxdq_span,
            )
            s = float(zxdq.iloc[-1])
            l = float(zxdkx.iloc[-1]) if pd.notna(zxdkx.iloc[-1]) else float("nan")
        return s, l, c

    def __call__(self, hist: pd.DataFrame) -> bool:
        if hist.empty:
            return False
        s, l, c = self._zx_vals(hist)
        if not (np.isfinite(s) and np.isfinite(l)):
            return False
        if self.require_close_gt_long and not (c > l):
            return False
        if self.require_short_gt_long and not (s > l):
            return False
        return True

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        if "zxdq" in df.columns and "zxdkx" in df.columns:
            zxdq_v  = df["zxdq"].to_numpy(dtype=float)
            zxdkx_v = df["zxdkx"].to_numpy(dtype=float)
        else:
            zs, zk  = compute_zx_lines(
                df, self.zx_m1, self.zx_m2, self.zx_m3, self.zx_m4,
                zxdq_span=self.zxdq_span,
            )
            zxdq_v  = zs.to_numpy(dtype=float)
            zxdkx_v = zk.to_numpy(dtype=float)
        close_v = df["close"].to_numpy(dtype=float)
        mask    = np.isfinite(zxdq_v) & np.isfinite(zxdkx_v)
        if self.require_close_gt_long:
            mask &= close_v > zxdkx_v
        if self.require_short_gt_long:
            mask &= zxdq_v > zxdkx_v
        return mask


# ─────────────────────────────────────────────────────────────────────────────
# 2. 周线均线多头排列过滤（B1 + 砖型图共用）
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WeeklyMABullFilter:
    """
    周线均线多头排列：MA_short > MA_mid > MA_long（默认 20/60/120 周）。
    优先读预计算列 'wma_bull'。
    """
    wma_short: int = 20
    wma_mid:   int = 60
    wma_long:  int = 120

    def __call__(self, hist: pd.DataFrame) -> bool:
        if "wma_bull" in hist.columns:
            return bool(hist["wma_bull"].iloc[-1])
        wc = compute_weekly_close(hist)
        if len(wc) < self.wma_long:
            return False
        ma_s = wc.rolling(self.wma_short, min_periods=self.wma_short).mean()
        ma_m = wc.rolling(self.wma_mid,   min_periods=self.wma_mid).mean()
        ma_l = wc.rolling(self.wma_long,  min_periods=self.wma_long).mean()
        sv, mv, lv = float(ma_s.iloc[-1]), float(ma_m.iloc[-1]), float(ma_l.iloc[-1])
        return bool(np.isfinite(sv) and np.isfinite(mv) and np.isfinite(lv) and sv > mv > lv)

    def vec_mask(self, df: pd.DataFrame) -> np.ndarray:
        if "wma_bull" in df.columns:
            return df["wma_bull"].to_numpy(dtype=bool)
        return compute_weekly_ma_bull(
            df, ma_periods=(self.wma_short, self.wma_mid, self.wma_long)
        ).to_numpy(dtype=bool)


# =============================================================================
# ── 工具函数 ────────────────────────────────────────────────────────────────
# =============================================================================

def _apply_vec_filters(df: pd.DataFrame, filters: list) -> np.ndarray:
    """对列表中所有实现了 vec_mask 的 Filter 取交集，返回布尔数组。"""
    mask = np.ones(len(df), dtype=bool)
    for f in filters:
        mask &= f.vec_mask(df)
    return mask


# =============================================================================
# AnySelector Protocol（外部类型提示用）
# =============================================================================

class AnySelector(Protocol):
    """外部代码面向接口编程时使用的 Protocol 类型。"""
    def passes_df_on_date(self, df: pd.DataFrame, date: pd.Timestamp) -> bool: ...
    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame: ...
    def vec_picks_from_prepared(
        self, df: pd.DataFrame,
        start: Optional[pd.Timestamp] = None,
        end:   Optional[pd.Timestamp] = None,
    ) -> List[pd.Timestamp]: ...
