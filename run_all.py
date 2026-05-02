"""
run_all.py
~~~~~~~~~~
一键运行完整交易选股流程：

  步骤 1  pipeline/fetch_kline.py              — 拉取最新 K 线数据
  步骤 2  pipeline/cli.py preselect            — 量化初选，生成候选列表
  步骤 3  dashboard/export_kline_charts.py     — 导出候选股 K 线图
  步骤 4  agent/local/review.py                — 本地 LM Studio 大模型图表分析评分
          (可选 agent/siliconflow/review.py    — SiliconFlow Kimi-K2.6 图表分析评分)
          (可选 agent/zhipu/review.py          — 智谱 GLM-4.6V 图表分析评分)
  步骤 5  dashboard/overlay_score_to_chart.py  — 将评分叠加到 K 线图
  步骤 6  similarity/patternMatcher.py         — 完美图形相似度匹配
  步骤 7  dashboard/overlay_pattern_to_chart.py — 将图形匹配标注叠加到 K 线图
  步骤 8  export_for_eastmoney.py              — 导出东方财富可导入文件

用法：
    python run_all.py
    python run_all.py --skip-fetch     # 跳过行情下载（已有最新数据时）
    python run_all.py --start-from 3   # 从第 3 步开始（跳过前两步）
    python run_all.py --ai-review --reviewer local     # 启用本地 LM Studio AI 复评
    python run_all.py --ai-review --reviewer siliconflow # 启用 SiliconFlow AI 复评
    python run_all.py --reviewer zhipu       # 使用智谱 GLM-4.6V
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable  # 与当前进程同一个 Python 解释器


def _run(step_name: str, cmd: list[str]) -> None:
    """运行子进程，失败时终止整个流程。"""
    print(f"\n{'='*60}")
    print(f"[步骤] {step_name}")
    print(f"  命令: {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"\n[ERROR] 步骤「{step_name}」返回非零退出码 {result.returncode}，流程已中止。")
        sys.exit(result.returncode)


def _print_recommendations() -> None:
    """读取最新 suggestion.json，打印推荐购买的股票。"""
    candidates_file = ROOT / "data" / "candidates" / "candidates_latest.json"
    if not candidates_file.exists():
        print("[ERROR] 找不到 candidates_latest.json，无法定位 suggestion.json。")
        return

    with open(candidates_file, encoding="utf-8") as f:
        pick_date: str = json.load(f).get("pick_date", "")

    if not pick_date:
        print("[ERROR] candidates_latest.json 中未设置 pick_date。")
        return

    suggestion_file = ROOT / "data" / "review" / pick_date / "suggestion.json"
    if not suggestion_file.exists():
        print(f"[ERROR] 找不到评分汇总文件：{suggestion_file}")
        return

    with open(suggestion_file, encoding="utf-8") as f:
        suggestion: dict = json.load(f)

    recommendations: list[dict] = suggestion.get("recommendations", [])
    min_score: float = suggestion.get("min_score_threshold", 0)
    total: int = suggestion.get("total_reviewed", 0)

    print(f"\n{'='*60}")
    print(f"  选股日期：{pick_date}")
    print(f"  评审总数：{total} 只   推荐门槛：score ≥ {min_score}")
    print(f"{'='*60}")

    if not recommendations:
        print("  暂无达标推荐股票。")
        return

    header = f"{'排名':>4}  {'代码':>8}  {'总分':>6}  {'信号':>10}  {'研判':>6}  备注"
    print(header)
    print("-" * len(header))
    for r in recommendations:
        rank        = r.get("rank",        "?")
        code        = r.get("code",        "?")
        score       = r.get("total_score", "?")
        signal_type = r.get("signal_type", "")
        verdict     = r.get("verdict",     "")
        comment     = r.get("comment",     "")
        score_str   = f"{score:.1f}" if isinstance(score, (int, float)) else str(score)
        print(f"{rank:>4}  {code:>8}  {score_str:>6}  {signal_type:>10}  {verdict:>6}  {comment}")

    print(f"\n✅ 推荐购买 {len(recommendations)} 只股票（详见 {suggestion_file}）")


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentTrader 全流程自动运行脚本")
    parser.add_argument(
        "--skip-fetch", action="store_true",
        help="跳过步骤 1（行情下载），直接从初选开始",
    )
    parser.add_argument(
        "--start-from", type=int, default=1, metavar="N",
        help="从第 N 步开始执行（1~8），跳过前面的步骤",
    )
    parser.add_argument(
        "--reviewer",
        choices=["local", "siliconflow", "zhipu", "bailian"],
        default="local",
        help="选择 AI 图表评审器：local=本地LM Studio，siliconflow=Kimi-K2.6，zhipu=GLM-4.6V，bailian=阿里云百炼",
    )
    parser.add_argument(
        "--ai-review",
        action="store_true",
        help="启用 AI 图表复评（默认关闭，需要时手动启用）",
    )
    parser.add_argument(
        "--bailian-model",
        type=str,
        default=None,
        help="阿里云百炼使用的模型名称（如 kimi-k2.6、qwen3.6-plus 等）",
    )
    parser.add_argument(
        "--strategies",
        type=str,
        default=None,
        help="指定选股策略，逗号分隔（如 b1,brick），默认运行所有已启用策略",
    )
    args = parser.parse_args()

    start = args.start_from

    if args.skip_fetch and start == 1:
        start = 2

    # ── 步骤 1：拉取 K 线数据 ─────────────────────────────────────────
    if start <= 1:
        _run(
            "1/8  拉取 K 线数据（fetch_kline）",
            [PYTHON, "-m", "pipeline.fetch_kline"],
        )

    # ── 步骤 2：量化初选 ─────────────────────────────────────────────
    if start <= 2:
        step2_cmd = [PYTHON, "-m", "pipeline.cli", "preselect"]
        if args.strategies:
            step2_cmd += ["--strategies", args.strategies]
        strategy_desc = f"（策略: {args.strategies}）" if args.strategies else ""
        _run(f"2/8  量化初选（cli preselect）{strategy_desc}", step2_cmd)

    # ── 步骤 3：导出 K 线图 ──────────────────────────────────────────
    if start <= 3:
        _run(
            "3/8  导出 K 线图（export_kline_charts）",
            [PYTHON, str(ROOT / "dashboard" / "export_kline_charts.py")],
        )

    # ── 步骤 4：AI 图表分析（默认关闭，需 --ai-review 启用）─────────
    if start <= 4 and args.ai_review:
        if args.reviewer == "local":
            _run(
                "4/8  本地 LM Studio 图表分析",
                [PYTHON, str(ROOT / "agent" / "local" / "review.py")],
            )
        elif args.reviewer == "siliconflow":
            _run(
                "4/8  SiliconFlow Kimi-K2.6 图表分析",
                [PYTHON, str(ROOT / "agent" / "siliconflow" / "review.py")],
            )
        elif args.reviewer == "bailian":
            if not args.bailian_model:
                print("[ERROR] 使用阿里云百炼时必须指定 --bailian-model 参数")
                sys.exit(1)
            _run(
                f"4/8  阿里云百炼 {args.bailian_model} 图表分析",
                [PYTHON, str(ROOT / "agent" / "bailian" / "review.py"), "--model", args.bailian_model],
            )
        else:
            _run(
                "4/8  智谱 GLM-4.6V 图表分析",
                [PYTHON, str(ROOT / "agent" / "zhipu" / "review.py")],
            )

    # ── 步骤 5：评分叠加到 K 线图（依赖 AI 复评）──────────────────────
    if start <= 5 and args.ai_review:
        _run(
            "5/8  评分叠加到 K 线图（overlay_score_to_chart）",
            [PYTHON, str(ROOT / "dashboard" / "overlay_score_to_chart.py")],
        )

    # ── 步骤 6：完美图形相似度匹配（依赖 AI 复评）──────────────────────
    if start <= 6 and args.ai_review:
        _run(
            "6/8  完美图形相似度匹配（patternMatcher）",
            [PYTHON, "-m", "similarity.patternMatcher"],
        )

    # ── 步骤 7：图形匹配标注叠加（依赖 AI 复评）─────────────────────────
    if start <= 7 and args.ai_review:
        _run(
            "7/8  图形匹配标注叠加（overlay_pattern_to_chart）",
            [PYTHON, str(ROOT / "dashboard" / "overlay_pattern_to_chart.py")],
        )

    # ── 步骤 8：导出东方财富文件 ──────────────────────────────────────
    if start <= 8:
        if args.ai_review:
            # 有 AI 复评：基于评分结果导出
            _run(
                "8/8  导出东方财富文件（基于AI评分）",
                [PYTHON, str(ROOT / "pipeline" / "export_for_eastmoney.py")],
            )
        else:
            # 无 AI 复评：直接从候选股票导出
            _run(
                "8/8  导出东方财富文件（基于候选股票）",
                [PYTHON, str(ROOT / "pipeline" / "export_for_eastmoney.py"), "--no-ai"],
            )

    # ── 步骤 9：打印推荐结果 ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print("[步骤] 9/9  推荐购买的股票")
    _print_recommendations()


if __name__ == "__main__":
    main()
