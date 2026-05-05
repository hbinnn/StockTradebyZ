"""
pipeline/cli.py
统一命令行入口。

用法：
  python -m pipeline.cli preselect
  python -m pipeline.cli preselect --date 2025-12-31
  python -m pipeline.cli preselect --config config/rules_preselect.yaml --data data/raw
  python -m pipeline.cli backtest --config config/backtest.yaml
  python -m pipeline.cli backtest --strategies b1 --start 2023-01-01 --end 2025-12-31
  python -m pipeline.cli optimize --config config/backtest.yaml --grid config/grid_b1.yaml

子命令：
  preselect   运行量化初选，写入 data/candidates/
  backtest    运行历史回测
  optimize    参数优化（网格搜索 / Walk-Forward）
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys
from pathlib import Path

# 将 pipeline 目录加入 path（直接用 python cli.py 时需要）
sys.path.insert(0, str(Path(__file__).parent))

from select_stock import run_preselect, resolve_preselect_output_dir
from schemas import CandidateRun
from pipeline_io import save_candidates

# 将项目根目录加入 path（回测包需要）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# ── 日志配置 ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("cli")


def _add_log_file(log_dir: str, pick_date: str) -> None:
    p = Path(log_dir)
    p.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(p / f"pipeline_{pick_date}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)


# =============================================================================
# preselect 子命令
# =============================================================================

def cmd_preselect(args: argparse.Namespace) -> None:
    logger.info("===== 量化初选开始 =====")

    strategy_list = None
    if args.strategies:
        strategy_list = [s.strip() for s in args.strategies.split(",") if s.strip()]
        if strategy_list:
            logger.info("指定策略: %s", strategy_list)

    pick_ts, candidates = run_preselect(
        config_path=args.config or None,
        data_dir=args.data or None,
        end_date=args.end_date or None,
        pick_date=args.date or None,
        strategies=strategy_list,
    )

    pick_date_str = pick_ts.strftime("%Y-%m-%d")
    run_date_str = datetime.date.today().isoformat()

    if args.log_dir:
        _add_log_file(args.log_dir, pick_date_str)

    run = CandidateRun(
        run_date=run_date_str,
        pick_date=pick_date_str,
        candidates=candidates,
        meta={
            "config": args.config,
            "data_dir": args.data,
            "total": len(candidates),
        },
    )

    resolved_output_dir = resolve_preselect_output_dir(
        config_path=args.config or None,
        output_dir=args.output or None,
    )

    paths = save_candidates(run, candidates_dir=resolved_output_dir)

    logger.info("===== 初选完成 =====")
    logger.info("选股日期  : %s", pick_date_str)
    logger.info("候选数量  : %d 只", len(candidates))
    for key, path in paths.items():
        logger.info("%-8s → %s", key, path)

    if candidates:
        print(f"\n{'代码':>8}  {'策略':>6}  {'收盘价':>8}  {'砖型增长':>10}")
        print("-" * 44)
        for c in candidates:
            bg_val = c.extra.get("brick_growth")
            bg = f"{bg_val:.2f}x" if bg_val is not None else "  —"
            print(f"{c.code:>8}  {c.strategy:>6}  {c.close:>8.2f}  {bg:>10}")
    else:
        print("\n(今日无候选股票)")


# =============================================================================
# backtest 子命令
# =============================================================================

def cmd_backtest(args: argparse.Namespace) -> None:
    from backtest import (
        BacktestConfig, BacktestEngine, BrokerConfig,
        print_console_report, generate_html_report,
    )

    logger.info("===== 回测开始 =====")

    # 加载配置
    config_path = args.config or "config/backtest.yaml"
    cfg_path = _resolve_path(config_path)
    if cfg_path.exists():
        cfg = BacktestConfig.from_yaml(str(cfg_path))
        logger.info("加载配置: %s", cfg_path)
    else:
        cfg = BacktestConfig()
        logger.info("使用默认配置")

    # CLI 覆盖
    if args.start_date:
        cfg.start_date = args.start_date
    if args.end_date:
        cfg.end_date = args.end_date
    if args.strategies:
        cfg.strategies = [s.strip() for s in args.strategies.split(",")]
    if args.capital is not None:
        cfg.initial_capital = args.capital
    if args.hold_days is not None:
        cfg.hold_days = args.hold_days
    if args.max_positions is not None:
        cfg.max_positions = args.max_positions
    if args.data_dir:
        cfg.data_dir = args.data_dir

    logger.info("策略: %s, 区间: %s ~ %s, 资金: %.0f万",
                cfg.strategies, cfg.start_date, cfg.end_date,
                cfg.initial_capital / 10000)

    # 加载 rules_preselect.yaml 获取默认策略参数
    _load_strategy_params(cfg)

    # 运行回测
    engine = BacktestEngine(cfg)
    results = engine.run()

    # 输出
    output_dir = _resolve_path(args.output_dir or cfg.output_dir)
    for strategy_name, result in results.items():
        # 保存到文件
        result_dir = result.to_json(str(output_dir))
        logger.info("策略 %s → %s", strategy_name, result_dir)

        # Console 报告
        print_console_report(result)

        # HTML 报告
        if not args.no_report:
            html_path = Path(result_dir) / "report.html"
            generate_html_report(result, html_path)
            logger.info("HTML 报告: %s", html_path)

    logger.info("===== 回测完成 =====")


# =============================================================================
# optimize 子命令
# =============================================================================

def cmd_optimize(args: argparse.Namespace) -> None:
    import json
    import yaml
    from backtest import (
        BacktestConfig, BrokerConfig, GridSearcher, GridConfig,
        WalkForwardOptimizer,
    )

    logger.info("===== 参数优化开始 =====")

    # 加载回测配置
    config_path = args.config or "config/backtest.yaml"
    cfg_path = _resolve_path(config_path)
    if cfg_path.exists():
        cfg = BacktestConfig.from_yaml(str(cfg_path))
    else:
        cfg = BacktestConfig()

    # CLI 覆盖
    if args.data_dir:
        cfg.data_dir = args.data_dir

    _load_strategy_params(cfg)

    # 加载网格配置
    if args.grid:
        grid_path = _resolve_path(args.grid)
        with open(grid_path, "r", encoding="utf-8") as f:
            grid_raw = yaml.safe_load(f)
        strategy = grid_raw.get("strategy", cfg.strategies[0])
        param_grid = grid_raw.get("param_grid", {})
        grid_cfg = GridConfig(strategy=strategy, param_grid=param_grid)
        logger.info("网格配置: %s, %d 个参数, %d 组组合",
                    strategy, len(param_grid),
                    len(list(grid_cfg.combinations())))
    else:
        # 默认用第一个策略生成简单网格
        strategy = cfg.strategies[0]
        params = cfg.strategy_params.get(strategy, {})
        # 取前两个数值参数各 ±20% 生成 3 档
        simple_grid = _auto_grid(params)
        grid_cfg = GridConfig(strategy=strategy, param_grid=simple_grid)
        logger.info("自动生成网格: %s → %s", strategy, simple_grid)
        if not simple_grid:
            logger.error("策略 %s 无可优化参数，请使用 --grid 指定", strategy)
            sys.exit(1)

    output_dir = _resolve_path(args.output_dir or cfg.output_dir)

    if args.wf_windows and args.wf_windows > 0:
        # Walk-Forward 模式
        wf = WalkForwardOptimizer(cfg, grid_cfg, n_windows=args.wf_windows)
        summary = wf.run()

        # 保存
        opt_dir = output_dir / "optimization" / f"wf_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        opt_dir.mkdir(parents=True, exist_ok=True)
        (opt_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info("Walk-Forward 结果: %s", opt_dir)

        # 简要输出
        print()
        print(f"  Walk-Forward 优化: {len(wf.windows)} 个窗口完成")
        print(f"  参数稳定性: {summary['param_stability_score']:.4f}")
        print(f"  OOS 平均夏普: {summary['oos_avg_sharpe']:.4f}")
        print(f"  推荐参数: {summary.get('recommended_params', {})}")

    else:
        # 网格搜索模式
        searcher = GridSearcher(cfg, grid_cfg)
        trials = searcher.search()

        # 保存
        opt_dir = output_dir / "optimization" / f"grid_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        opt_dir.mkdir(parents=True, exist_ok=True)
        df = searcher.to_dataframe()
        df.to_csv(opt_dir / "trials.csv", index=False)

        best = searcher.best()
        if best:
            (opt_dir / "best_params.json").write_text(
                json.dumps(best.to_dict(), indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

        logger.info("优化结果: %s", opt_dir)

        # 简要输出
        print()
        print(f"  网格搜索完成: {len(trials)} 组试验")
        if best:
            print(f"  最优评分: {best.score:.4f}")
            print(f"  最优参数: {best.params}")
            print(f"  夏普: {best.metrics.get('sharpe_ratio', 'N/A'):.3f}" if isinstance(best.metrics.get('sharpe_ratio'), (int, float)) else f"  夏普: {best.metrics.get('sharpe_ratio', 'N/A')}")
            print(f"  收益: {best.metrics.get('total_return', 0) * 100:.2f}%")

    logger.info("===== 参数优化完成 =====")


# =============================================================================
# 辅助函数
# =============================================================================

def _resolve_path(path_like: str) -> Path:
    p = Path(path_like)
    return p if p.is_absolute() else _PROJECT_ROOT / p


def _load_strategy_params(cfg) -> None:
    """从 rules_preselect.yaml 加载策略默认参数（如果 cfg 中还未设置）。"""
    if cfg.strategy_params:
        return
    import yaml
    rules_path = _PROJECT_ROOT / "config" / "rules_preselect.yaml"
    if not rules_path.exists():
        return
    with open(rules_path, "r", encoding="utf-8") as f:
        rules = yaml.safe_load(f) or {}
    for name in cfg.strategies:
        if name in rules:
            cfg.strategy_params[name] = rules[name]


def _auto_grid(params: dict) -> dict:
    """从策略参数字典自动生成简单 3 档网格（±20%）。"""
    grid = {}
    for k, v in params.items():
        if not isinstance(v, (int, float)):
            continue
        if isinstance(v, bool):
            continue
        if k in ("enabled",):
            continue
        grid[k] = [v * 0.8, v, v * 1.2]
        if len(grid) >= 2:  # 最多 2 个参数
            break
    return grid


# =============================================================================
# CLI 解析
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline.cli",
        description="AgentTrader 量化初选 + 回测 CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── preselect ────────────────────────────────────────────────────
    p = sub.add_parser("preselect", help="运行量化初选")
    p.add_argument("--config", default=None, help="rules_preselect.yaml 路径")
    p.add_argument("--data",   default=None, help="CSV 数据目录（覆盖配置文件）")
    p.add_argument("--date",   default=None, help="选股基准日期 YYYY-MM-DD（默认最新）")
    p.add_argument("--end-date", dest="end_date", default=None,
                   help="数据截断日期（回测用）")
    p.add_argument("--output", default=None, help="候选输出目录（默认 data/candidates/）")
    p.add_argument("--log-dir", dest="log_dir", default=None,
                   help="流水日志目录（默认 data/logs/）")
    p.add_argument("--strategies", default=None,
                   help="指定运行策略，逗号分隔（如 b1,brick），默认运行所有已启用策略")

    # ── backtest ─────────────────────────────────────────────────────
    bt = sub.add_parser("backtest", help="运行历史回测")
    bt.add_argument("--config", default="config/backtest.yaml",
                    help="回测配置文件（默认 config/backtest.yaml）")
    bt.add_argument("--start", dest="start_date", default=None,
                    help="回测开始日期 YYYY-MM-DD（覆盖配置文件）")
    bt.add_argument("--end", dest="end_date", default=None,
                    help="回测结束日期 YYYY-MM-DD（覆盖配置文件）")
    bt.add_argument("--strategies", default=None,
                    help="策略列表，逗号分隔（覆盖配置文件）")
    bt.add_argument("--capital", type=float, default=None,
                    help="初始资金（覆盖配置文件）")
    bt.add_argument("--hold-days", dest="hold_days", type=int, default=None,
                    help="持有天数（覆盖配置文件）")
    bt.add_argument("--max-positions", dest="max_positions", type=int, default=None,
                    help="最大持仓数（覆盖配置文件）")
    bt.add_argument("--data-dir", dest="data_dir", default=None,
                    help="原始数据目录")
    bt.add_argument("--output-dir", dest="output_dir", default=None,
                    help="结果输出目录（默认 data/backtest/）")
    bt.add_argument("--no-report", action="store_true",
                    help="跳过 HTML 报告生成")

    # ── optimize ─────────────────────────────────────────────────────
    opt = sub.add_parser("optimize", help="参数扫描与优化")
    opt.add_argument("--config", default="config/backtest.yaml",
                     help="回测配置文件")
    opt.add_argument("--grid", default=None,
                     help="网格搜索配置 YAML（如 config/grid_b1.yaml）")
    opt.add_argument("--data-dir", dest="data_dir", default=None,
                     help="原始数据目录")
    opt.add_argument("--output-dir", dest="output_dir", default=None,
                     help="结果输出目录")
    opt.add_argument("--wf-windows", dest="wf_windows", type=int, default=0,
                     help="Walk-Forward 窗口数（>0 则启用 WF 模式）")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "preselect":
        cmd_preselect(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "optimize":
        cmd_optimize(args)
    else:
        parser.print_help()
        sys.exit(1)


def test():
    """简单测试函数，验证 CLI 逻辑（不依赖外部数据）。"""
    class Args:
        command = "preselect"
        config = None
        data = None
        date = None
        end_date = None
        output = "./data/candidates"
        log_dir = "./data/logs"
        strategies = None

    args = Args()
    cmd_preselect(args)


if __name__ == "__main__":
    main()
