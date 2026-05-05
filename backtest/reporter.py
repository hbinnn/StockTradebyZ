"""
backtest/reporter.py
报告生成：Console 文本表格 + 独立 HTML 报告。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .io import BacktestResult


# ── Console 文本报告 ──────────────────────────────────────────────────────

def print_console_report(result: BacktestResult) -> None:
    """在终端打印回测结果摘要表格。"""
    m = result.metrics
    ss = result.signal_stats

    print()
    print("=" * 64)
    print(f"  回测结果 - {result.strategy}")
    print(f"  Run ID: {result.run_id}")
    print(f"  生成时间: {result.generated_at}")
    print("=" * 64)

    # 收益指标
    print()
    print("  ── 收益指标 ──")
    _row("总收益率", f"{m.get('total_return', 0) * 100:.2f}%")
    _row("年化收益率", f"{m.get('annualized_return', 0) * 100:.2f}%")
    _row("年化波动率", f"{m.get('annualized_volatility', 0) * 100:.2f}%")

    # 风险调整
    print()
    print("  ── 风险调整指标 ──")
    _row("夏普比率", f"{m.get('sharpe_ratio', 0):.3f}")
    _row("索提诺比率", f"{m.get('sortino_ratio', 0):.3f}")
    _row("最大回撤", f"{m.get('max_drawdown', 0) * 100:.2f}%")
    _row("回撤持续期", f"{m.get('max_drawdown_duration_days', 0)} 天")
    _row("卡尔玛比率", f"{m.get('calmar_ratio', 0):.3f}")

    # 交易统计
    print()
    print("  ── 交易统计 ──")
    _row("总交易次数", str(m.get("total_trades", 0)))
    _row("胜率", f"{m.get('win_rate', 0) * 100:.1f}%")
    _row("盈亏比", str(m.get("profit_factor", "N/A")))
    _row("平均每笔收益", f"{m.get('avg_trade_return', 0) * 100:.2f}%")
    _row("  - 平均盈利", f"{m.get('avg_win', 0) * 100:.2f}%")
    _row("  - 平均亏损", f"{m.get('avg_loss', 0) * 100:.2f}%")
    _row("平均持仓天数", f"{m.get('avg_holding_days', 0):.1f}")

    # 月度/年度
    print()
    print("  ── 时间分布 ──")
    _row("最佳月份", f"{m.get('best_month_return', 0) * 100:.2f}%")
    _row("最差月份", f"{m.get('worst_month_return', 0) * 100:.2f}%")
    _row("盈利月份", m.get("months_positive", "N/A"))

    # 信号统计
    if ss:
        print()
        print("  ── 信号统计 ──")
        _row("交易日数", str(ss.get("total_trading_days", "N/A")))
        _row("信号总数", str(ss.get("total_signals", "N/A")))
        _row("日均信号", str(ss.get("avg_signals_per_day", "N/A")))

    # 年度收益
    yr = m.get("yearly_returns", {})
    if yr:
        print()
        print("  ── 年度收益 ──")
        for year, ret in yr.items():
            _row(year[:4], f"{ret * 100:.2f}%")

    print()
    print("=" * 64)
    print()


def _row(label: str, value: str, width: int = 20) -> None:
    print(f"    {label:<{width}} {value}")


# ── HTML 独立报告 ────────────────────────────────────────────────────────

def generate_html_report(result: BacktestResult, output_path: str | Path) -> str:
    """生成独立 HTML 报告文件。"""
    m = result.metrics
    ss = result.signal_stats

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>回测报告 - {result.strategy}</title>
<style>
body {{ font-family: -apple-system, 'Segoe UI', sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; color: #333; background: #fafafa; }}
h1 {{ color: #1a1a2e; border-bottom: 3px solid #16213e; padding-bottom: 8px; }}
h2 {{ color: #0f3460; margin-top: 28px; }}
.meta {{ color: #666; font-size: 14px; margin-bottom: 20px; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0 24px; }}
th, td {{ padding: 8px 14px; text-align: left; border-bottom: 1px solid #ddd; }}
th {{ background: #16213e; color: #fff; font-weight: 600; }}
tr:nth-child(even) {{ background: #f0f0f5; }}
.kpi {{ display: inline-block; background: #fff; border-radius: 8px; padding: 16px 24px; margin: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.08); text-align: center; min-width: 140px; }}
.kpi .value {{ font-size: 24px; font-weight: 700; color: #16213e; }}
.kpi .label {{ font-size: 12px; color: #888; margin-top: 4px; }}
.positive {{ color: #16a085; }}
.negative {{ color: #c0392b; }}
</style>
</head>
<body>

<h1>回测报告 — {result.strategy}</h1>
<div class="meta">
  Run ID: {result.run_id}<br>
  生成时间: {result.generated_at}<br>
  初始资金: ¥ {result.config.get('initial_capital', 0):,.0f}<br>
  回测期间: {result.config.get('start_date', '')} ~ {result.config.get('end_date', '')}
</div>

<h2>关键指标</h2>
<div>
  <div class="kpi"><div class="value">{_pct(m.get('total_return', 0))}</div><div class="label">总收益率</div></div>
  <div class="kpi"><div class="value">{_pct(m.get('annualized_return', 0))}</div><div class="label">年化收益</div></div>
  <div class="kpi"><div class="value">{m.get('sharpe_ratio', 0):.2f}</div><div class="label">夏普比率</div></div>
  <div class="kpi"><div class="value">{_pct(m.get('max_drawdown', 0))}</div><div class="label">最大回撤</div></div>
  <div class="kpi"><div class="value">{_pct(m.get('win_rate', 0))}</div><div class="label">胜率</div></div>
</div>

<h2>收益与风险</h2>
<table>
<tr><th>指标</th><th>数值</th></tr>
<tr><td>年化收益率</td><td>{_pct(m.get('annualized_return', 0))}</td></tr>
<tr><td>年化波动率</td><td>{_pct(m.get('annualized_volatility', 0))}</td></tr>
<tr><td>夏普比率</td><td>{m.get('sharpe_ratio', 0):.3f}</td></tr>
<tr><td>索提诺比率</td><td>{m.get('sortino_ratio', 0):.3f}</td></tr>
<tr><td>最大回撤</td><td>{_pct(m.get('max_drawdown', 0))}</td></tr>
<tr><td>回撤持续期</td><td>{m.get('max_drawdown_duration_days', 0)} 天</td></tr>
<tr><td>卡尔玛比率</td><td>{m.get('calmar_ratio', 0):.3f}</td></tr>
</table>

<h2>交易统计</h2>
<table>
<tr><th>指标</th><th>数值</th></tr>
<tr><td>总交易次数</td><td>{m.get('total_trades', 0)}</td></tr>
<tr><td>胜率</td><td>{_pct(m.get('win_rate', 0))}</td></tr>
<tr><td>盈亏比</td><td>{m.get('profit_factor', 'N/A')}</td></tr>
<tr><td>平均每笔收益</td><td>{_pct(m.get('avg_trade_return', 0))}</td></tr>
<tr><td>平均盈利</td><td>{_pct(m.get('avg_win', 0))}</td></tr>
<tr><td>平均亏损</td><td>{_pct(m.get('avg_loss', 0))}</td></tr>
<tr><td>平均持仓天数</td><td>{m.get('avg_holding_days', 0):.1f}</td></tr>
</table>

<h2>时间分布</h2>
<table>
<tr><th>指标</th><th>数值</th></tr>
<tr><td>最佳月份</td><td>{_pct(m.get('best_month_return', 0))}</td></tr>
<tr><td>最差月份</td><td>{_pct(m.get('worst_month_return', 0))}</td></tr>
<tr><td>盈利月份比例</td><td>{m.get('months_positive', 'N/A')}</td></tr>
</table>

<h2>年度收益</h2>
<table>
<tr><th>年份</th><th>收益率</th></tr>
"""
    yr = m.get("yearly_returns", {})
    if yr:
        for year, ret in sorted(yr.items()):
            cls = "positive" if ret > 0 else "negative"
            html += f'<tr><td>{year[:4]}</td><td class="{cls}">{_pct(ret)}</td></tr>\n'
    else:
        html += '<tr><td colspan="2">无数据</td></tr>\n'

    html += """
</table>
</body>
</html>"""

    path = Path(output_path)
    path.write_text(html, encoding="utf-8")
    return str(path)


def _pct(v: float) -> str:
    return f"{v * 100:+.2f}%"
