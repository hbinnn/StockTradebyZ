"""
backtest/engine.py
回测引擎：编排信号 → 每日模拟循环 → 绩效分析。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from pipeline.Selector import compute_zx_lines
from .exit_rules import B1ExitChecker, BrickExitChecker, ExitAction
import pandas as pd

from .config import BacktestConfig
from .broker import Broker
from .portfolio import Portfolio
from .analyzer import PerformanceAnalyzer
from .signal_loader import SignalLoader
from .io import BacktestResult

logger = logging.getLogger(__name__)


class BacktestEngine:
    """顶层回测编排器：信号 → 每日模拟 → 结果。"""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.broker = Broker(config.broker)
        self.signal_loader = SignalLoader(config)

    # ── 公开 API ─────────────────────────────────────────────────────────

    def run(self) -> Dict[str, BacktestResult]:
        """运行完整回测，返回 {strategy_name: BacktestResult}。"""
        signals = self.signal_loader.load()
        return self._run_signals(signals)

    def run_single(
        self, strategy_name: str, params: Dict[str, Any]
    ) -> BacktestResult:
        """单策略回测（优化器内循环用）。"""
        signals = self.signal_loader.load_single(strategy_name, params)
        results = self._run_signals({strategy_name: signals})
        return results[strategy_name]

    def run_combined(self) -> BacktestResult:
        """多策略合并回测：所有策略信号混合，统一排序买入。"""
        signals = self.signal_loader.load()
        return self._simulate("combined", signals)

    # ── 内部 ─────────────────────────────────────────────────────────────

    def _run_signals(
        self, signals: Dict[str, Dict[pd.Timestamp, List[str]]]
    ) -> Dict[str, BacktestResult]:
        results = {}
        for strategy_name, picks in signals.items():
            logger.info("开始回测策略: %s", strategy_name)
            result = self._simulate(strategy_name, {strategy_name: picks})
            results[strategy_name] = result
        return results

    def _simulate(
        self, strategy_name: str,
        signals: Dict[str, Dict[pd.Timestamp, List[str]]],
    ) -> BacktestResult:
        """核心模拟循环。"""
        portfolio = Portfolio(self.config.initial_capital, self.config.max_positions)

        # 按日期排序所有交易日
        all_dates = self._collect_trading_dates(signals)
        if not all_dates:
            raise ValueError("没有找到任何交易日期")

        # 加载各股票市场数据（按需加载已持仓的股票）
        market_data = self._load_market_data_for_dates(all_dates)

        start = pd.to_datetime(self.config.start_date)
        end = pd.to_datetime(self.config.end_date)
        trading_days = [d for d in all_dates if start <= d <= end]

        # 统计信号
        all_merged: Dict[pd.Timestamp, List[str]] = {}
        for picks in signals.values():
            for d, codes in picks.items():
                all_merged.setdefault(d, []).extend(codes)

        total_signals = sum(len(v) for v in all_merged.values())
        logger.info(
            "策略 %s: %d 个交易日, %d 条信号",
            strategy_name, len(trading_days), total_signals,
        )

        # ── 构建退出规则检查器 ──────────────────────────────────────
        exit_checker = self._build_exit_checker(strategy_name)

        # ── 大盘择时：加载上证指数，计算知行线 ────────────────────────
        index_zx = self._load_index_zx(trading_days)
        market_skip_days = 0

        # ── 每日模拟循环 ──────────────────────────────────────────────
        for date in trading_days:
            # Step A: 持仓市值核算
            try:
                portfolio.mark_to_market(date, market_data)
            except Exception:
                pass

            # Step B: 退出检查（止盈 / 止损）
            self._check_exits_v2(portfolio, date, market_data, exit_checker)

            # Step C: 新信号处理（大盘择时：白线<黄线 → 不开新仓）
            allow_open = True
            if self.config.market_filter_enabled and index_zx is not None:
                if date in index_zx.index:
                    zxdq_v = float(index_zx.loc[date, "zxdq"])
                    zxdkx_v = float(index_zx.loc[date, "zxdkx"])
                else:
                    # 用 searchsorted 找最近交易日
                    idx_dates = index_zx.index.values
                    pos = int(np.searchsorted(idx_dates, date.to_datetime64(), side="right")) - 1
                    if pos >= 0:
                        zxdq_v = float(index_zx["zxdq"].iloc[pos])
                        zxdkx_v = float(index_zx["zxdkx"].iloc[pos])
                    else:
                        zxdq_v, zxdkx_v = None, None
                if zxdq_v is not None and zxdkx_v is not None and np.isfinite(zxdq_v) and np.isfinite(zxdkx_v):
                    allow_open = zxdq_v > zxdkx_v
                if not allow_open:
                    market_skip_days += 1

            if allow_open and self.config.max_new_positions_per_day > 0:
                today_codes = all_merged.get(date, [])
                if today_codes:
                    self._open_positions(portfolio, date, today_codes, market_data, strategy_name, all_merged)

        # ── 最终清仓（强制以最后一日收盘价卖出所有持仓）─────────────────
        final_date = trading_days[-1]
        for code in list(portfolio.positions.keys()):
            fill_price = self.broker.get_exit_price(code, final_date, market_data)
            if fill_price is None:
                fill_price = portfolio.positions[code].entry_price  # fallback
            gross = fill_price * portfolio.positions[code].shares
            net = self.broker.sell_proceeds(gross)
            portfolio.close_position(code, final_date, fill_price, net)

        # 最后一次核算
        try:
            portfolio.mark_to_market(final_date, market_data)
        except Exception:
            pass

        # ── 绩效分析 ─────────────────────────────────────────────────
        analyzer = PerformanceAnalyzer(
            nav_history=portfolio.nav_history,
            trades=portfolio.trade_log,
            initial_capital=self.config.initial_capital,
            risk_free_rate=self.config.risk_free_rate,
        )

        metrics = analyzer.summary()
        metrics["strategy"] = strategy_name
        metrics["start_date"] = self.config.start_date
        metrics["end_date"] = self.config.end_date

        return BacktestResult(
            run_id=BacktestResult.generate_run_id(),
            strategy=strategy_name,
            config=self.config.to_dict(),
            nav_history=[n.to_dict() for n in portfolio.nav_history],
            trades=[t.to_dict() for t in portfolio.trade_log],
            metrics=metrics,
            signal_stats={
                "total_trading_days": len(trading_days),
                "total_signals": total_signals,
                "avg_signals_per_day": round(total_signals / max(len(trading_days), 1), 2),
                "signal_dates_count": len(all_merged),
                "market_skip_days": market_skip_days,
            },
            generated_at=datetime.now().isoformat(),
        )

    # ── 退出检查（策略专属止盈 / 止损）────────────────────────────

    def _build_exit_checker(self, strategy_name: str):
        """从策略参数构建对应的退出规则检查器。"""
        params = self.config.strategy_params.get(strategy_name, {})

        if strategy_name in ("b1", "b2", "b3"):
            return B1ExitChecker(
                zx_m1=int(params.get("zx_m1", 14)),
                zx_m2=int(params.get("zx_m2", 28)),
                zx_m3=int(params.get("zx_m3", 57)),
                zx_m4=int(params.get("zx_m4", 114)),
                zxdq_span=int(params.get("zxdq_span", 10)),
            )
        elif strategy_name == "brick":
            return BrickExitChecker(
                n=int(params.get("n", 4)), m1=int(params.get("m1", 4)),
                m2=int(params.get("m2", 6)), m3=int(params.get("m3", 6)),
                t=float(params.get("t", 4.0)),
                shift1=float(params.get("shift1", 90.0)),
                shift2=float(params.get("shift2", 100.0)),
                sma_w1=int(params.get("sma_w1", 1)),
                sma_w2=int(params.get("sma_w2", 1)),
                sma_w3=int(params.get("sma_w3", 1)),
            )
        return None

    def _check_exits_v2(
        self, portfolio: Portfolio, date: pd.Timestamp,
        market_data: Dict[str, pd.DataFrame],
        exit_checker,
    ) -> None:
        """每日检查所有持仓，调用策略专属退出规则。"""
        if exit_checker is None:
            return
        for code in list(portfolio.positions.keys()):
            pos = portfolio.positions[code]
            df = market_data.get(code)
            if df is None:
                continue

            action: ExitAction = exit_checker.check(pos, date, df)
            if action.action == "none":
                continue

            # 跌停检查（一字跌停卖不出，跳过）
            if self.broker.is_limit_down_locked(df, date):
                continue

            fill_price = self.broker.get_exit_price(code, date, market_data)
            if fill_price is None:
                continue

            if action.action == "sell_half":
                trade = portfolio.reduce_position(code, date, fill_price, 0.5)
                if trade:
                    logger.debug("%s %s", code, action.reason)

            elif action.action == "sell_all":
                gross = fill_price * pos.shares
                net = self.broker.sell_proceeds(gross)
                trade = portfolio.close_position(code, date, fill_price, net)
                if trade:
                    logger.debug("%s %s", code, action.reason)

    # ── 开仓 ───────────────────────────────────────────────────────────

    def _open_positions(
        self, portfolio: Portfolio, date: pd.Timestamp,
        today_codes: List[str],
        market_data: Dict[str, pd.DataFrame],
        strategy_name: str = "",
        all_signals: Optional[Dict[pd.Timestamp, List[str]]] = None,
    ) -> None:
        """按排名买入 top-N 新信号。"""
        # 排序：按 ranking_field
        ranked = self._rank_codes(today_codes, date, market_data)

        for code in ranked:
            if not portfolio.can_open():
                break
            if portfolio.has_position(code):
                continue  # 已在持仓中

            # T+1 买入：先确定入场日，获取入场开盘价
            exec_date = self._resolve_entry_date(code, date, market_data)
            fill_price = self.broker.get_entry_price(code, date, market_data)
            if fill_price is None:
                continue  # 涨跌停锁死或无下一日数据

            # 跳空过滤（仅 B1）：信号日收盘 → 入场日开盘，涨跌超 3% 则放弃
            # B2/B3 本身就是突破策略，跳空是预期行为，不过滤
            if strategy_name == "b1" and self._is_gap_too_wide(code, date, exec_date, market_data):
                continue

            # 计算止损价：信号日最低价 * 0.99（B1/B2/B3）
            stop_loss_price = self._calc_stop_loss(
                strategy_name, code, date, market_data
            )

            # 单票仓位上限：不超过总资金的 15%
            total_nav = portfolio.cash + sum(
                p.market_value(self._get_price(market_data.get(c), date, "close") or p.entry_price)
                for c, p in portfolio.positions.items()
            )
            max_alloc = total_nav * 0.15
            alloc = min(portfolio.allocation_per_position(), max_alloc)
            shares = self.broker.calculate_shares(fill_price, alloc)
            if shares < 100:
                continue  # 资金不足以买 1 手

            cost = self.broker.buy_cost(fill_price * shares)
            if cost > portfolio.cash:
                # 按实际可用资金调整
                shares = self.broker.calculate_shares(fill_price, portfolio.cash)
                if shares < 100:
                    continue
                cost = self.broker.buy_cost(fill_price * shares)

            try:
                portfolio.open_position(
                    code=code, date=exec_date,
                    fill_price=fill_price, shares=shares,
                    actual_cost=cost, strategy=strategy_name,
                    stop_loss_price=stop_loss_price,
                    signal_date=date.strftime("%Y-%m-%d"),
                )
            except ValueError:
                continue

    def _calc_stop_loss(
        self, strategy_name: str, code: str,
        signal_date: pd.Timestamp,
        market_data: Dict[str, pd.DataFrame],
    ) -> float:
        """B1/B2/B3: 止损价 = 信号日最低价 * 0.99；砖型图无需止损。"""
        if strategy_name not in ("b1", "b2", "b3"):
            return 0.0
        df = market_data.get(code)
        if df is None:
            return 0.0
        low = self._get_price(df, signal_date, "low")
        return round(low * 0.99, 2) if low else 0.0

    def _is_gap_too_wide(
        self, code: str, signal_date: pd.Timestamp,
        entry_date: pd.Timestamp,
        market_data: Dict[str, pd.DataFrame],
    ) -> bool:
        """信号日收盘 → 入场日开盘，跳空超过 ±3% 则放弃交易。"""
        df = market_data.get(code)
        if df is None:
            return True  # 无数据，保守放弃
        signal_close = self._get_price(df, signal_date, "close")
        entry_open = self._get_price(df, entry_date, "open")
        if signal_close is None or entry_open is None or signal_close <= 0:
            return True
        gap = abs(entry_open / signal_close - 1)
        return gap > 0.03

    # ── 辅助 ─────────────────────────────────────────────────────────────

    def _rank_codes(
        self, codes: List[str], date: pd.Timestamp,
        market_data: Dict[str, pd.DataFrame],
    ) -> List[str]:
        """按 ranking_field 对代码列表排序。"""
        field = self.config.ranking_field

        def _get_val(code: str) -> float:
            df = market_data.get(code)
            if df is None:
                return -np.inf if not self.config.ranking_ascending else np.inf
            try:
                if "date" in df.columns:
                    row = df[df["date"] == date]
                else:
                    row = df.loc[[date]] if date in df.index else None
                if row is None or row.empty:
                    return -np.inf if not self.config.ranking_ascending else np.inf
                return float(row[field].iloc[0]) if field in row.columns else 0.0
            except (KeyError, IndexError, TypeError):
                return 0.0

        return sorted(codes, key=_get_val, reverse=not self.config.ranking_ascending)

    def _resolve_entry_date(
        self, code: str, signal_date: pd.Timestamp,
        market_data: Dict[str, pd.DataFrame],
    ) -> pd.Timestamp:
        """确定买入执行日（T+1 时为下一交易日）。"""
        if not self.config.broker.t_plus_1:
            return signal_date
        df = market_data.get(code)
        if df is None:
            return signal_date
        next_day = self.broker._next_trading_day(df, signal_date)
        return next_day if next_day is not None else signal_date

    @staticmethod
    def _collect_trading_dates(
        signals: Dict[str, Dict[pd.Timestamp, List[str]]],
    ) -> List[pd.Timestamp]:
        """从信号字典收集所有出现过的交易日。"""
        all_dates: set = set()
        for picks in signals.values():
            all_dates.update(picks.keys())
        return sorted(all_dates)

    def _load_market_data_for_dates(
        self, dates: List[pd.Timestamp],
    ) -> Dict[str, pd.DataFrame]:
        """按需加载已持仓股票的行情数据。"""
        # 先加载所有策略涉及的股票
        import os
        from pathlib import Path

        _PROJECT_ROOT = Path(__file__).resolve().parent.parent

        data_dir = self.config.data_dir
        if not os.path.isabs(data_dir):
            data_dir = str(_PROJECT_ROOT / data_dir)
        if not os.path.isdir(data_dir):
            return {}

        min_date = min(dates).strftime("%Y-%m-%d") if dates else "2019-01-01"
        max_date = max(dates).strftime("%Y-%m-%d") if dates else "2025-12-31"
        min_ts = pd.to_datetime(min_date)
        max_ts = pd.to_datetime(max_date)

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
                df = df[(df["date"] >= min_ts) & (df["date"] <= max_ts)]
                df = df.set_index("date")
                if not df.empty:
                    data[code] = df
            except Exception:
                continue
        return data

    @staticmethod
    def _get_price(df: pd.DataFrame, date: pd.Timestamp, col: str) -> Optional[float]:
        """获取指定日期的价格字段。"""
        if "date" in df.columns:
            rows = df[df["date"] == date]
            if rows.empty:
                return None
            return float(rows[col].iloc[0])
        if date in df.index:
            return float(df.loc[date, col])
        return None

    def _load_index_zx(
        self, trading_days: List[pd.Timestamp],
    ) -> Optional[pd.DataFrame]:
        """加载上证指数日线，计算知行线 zxdq/zxdkx。"""
        if not self.config.market_filter_enabled:
            return None

        from pathlib import Path
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent
        idx_path = _PROJECT_ROOT / "data" / "index" / f"{self.config.index_code}.csv"
        if not idx_path.exists():
            logger.warning("指数数据不存在: %s", idx_path)
            return None

        df = pd.read_csv(idx_path)
        df.columns = [c.lower() for c in df.columns]
        date_col = "trade_date" if "trade_date" in df.columns else "date"
        # 兼容两种格式：Tushare YYYYMMDD 和 ISO YYYY-MM-DD
        dates = df[date_col].astype(str).str.strip()
        try:
            dates = pd.to_datetime(dates, format="%Y%m%d")
        except ValueError:
            dates = pd.to_datetime(dates)
        df[date_col] = dates
        df = df.sort_values(date_col).set_index(date_col)

        # 用 B1 默认参数计算知行线
        from pipeline.Selector import compute_zx_lines
        zs, zk = compute_zx_lines(df, 14, 28, 57, 114, zxdq_span=10)
        df["zxdq"] = zs
        df["zxdkx"] = zk

        logger.info(
            "大盘择时已启用: 上证指数, %d 条日线", len(df)
        )
        return df
