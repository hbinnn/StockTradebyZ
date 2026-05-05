"""
backtest/ — AgentTrader 回测系统

用法：
    from backtest import BacktestEngine, BacktestConfig, PerformanceAnalyzer
"""

from .config import BacktestConfig, BrokerConfig
from .broker import Broker
from .portfolio import Portfolio, Position, NavPoint
from .analyzer import PerformanceAnalyzer, Trade
from .engine import BacktestEngine, BacktestResult
from .optimizer import GridSearcher, GridConfig, WalkForwardOptimizer
from .reporter import print_console_report, generate_html_report
