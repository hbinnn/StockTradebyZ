"""
export_for_eastmoney.py
~~~~~~~~~~~~~~~~~~~~~~~
将 AI 复评通过的股票导出为东方财富可导入的文件格式。

支持格式：
    1. TXT/CSV 纯代码格式 - 每行一个股票代码
    2. CSV 带名称格式 - 股票代码,股票名称

用法：
    python export_for_eastmoney.py
    python export_for_eastmoney.py --format csv_with_name
    python export_for_eastmoney.py --min-score 4.5
"""
import argparse
import csv
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
DEFAULT_SUGGESTION = _ROOT / "data" / "review"
DEFAULT_CANDIDATES = _ROOT / "data" / "candidates" / "candidates_latest.json"


def load_suggestion(review_dir: Path) -> tuple[list[dict], str, str]:
    """从 AI 复评结果目录加载 suggestion.json，返回 (recommendations, pick_date, strategy_str)"""
    review_dir = Path(review_dir)
    # 查找最新的 suggestion.json
    suggestion_files = sorted(review_dir.glob("*/suggestion.json"))
    if not suggestion_files:
        raise FileNotFoundError(f"在 {review_dir} 下找不到 suggestion.json")

    latest = suggestion_files[-1]
    with open(latest, encoding="utf-8") as f:
        data = json.load(f)

    pick_date = data.get("date", "")
    recommendations = []
    for r in data.get("recommendations", []):
        recommendations.append({
            "code": r.get("code", ""),
            "total_score": r.get("total_score", 0),
            "verdict": r.get("verdict", ""),
            "signal_type": r.get("signal_type", ""),
            "comment": r.get("comment", ""),
        })

    return recommendations, pick_date, ""


def load_candidates_directly(candidates_file: Path) -> tuple[list[dict], str, str]:
    """直接加载候选股票文件，返回 (candidates, pick_date, strategy_names)"""
    if not candidates_file.exists():
        raise FileNotFoundError(f"找不到候选股票文件：{candidates_file}")

    with open(candidates_file, encoding="utf-8") as f:
        data = json.load(f)

    pick_date = data.get("pick_date", "")
    candidates = data.get("candidates", [])

    strategies = set()
    for cand in candidates:
        strategy = cand.get("strategy", "B1")
        strategies.add(strategy.upper() if isinstance(strategy, str) else "B1")
    strategy_str = "_".join(sorted(strategies)) if strategies else "B1"

    # 转换为统一格式
    recommendations = []
    for cand in candidates:
        recommendations.append({
            "code": cand.get("code", ""),
            "strategy": cand.get("strategy", ""),
            "close": cand.get("close", 0),
            "total_score": 5.0,  # 默认满分，无AI评分时所有股票都导出
        })

    return recommendations, pick_date, strategy_str


def load_strategies(candidates_file: Path) -> str:
    """从候选股票文件加载使用的策略名称"""
    if not candidates_file.exists():
        return "B1"

    with open(candidates_file, encoding="utf-8") as f:
        data = json.load(f)

    strategies = set()
    for cand in data.get("candidates", []):
        strategy = cand.get("strategy", "B1")
        strategies.add(strategy.upper() if isinstance(strategy, str) else "B1")

    if not strategies:
        return "B1"

    return "_".join(sorted(strategies))


def load_stock_names(candidates_file: Path) -> dict[str, str]:
    """加载候选股票的名称映射"""
    names = {}
    if candidates_file.exists():
        with open(candidates_file, encoding="utf-8") as f:
            data = json.load(f)
            for cand in data.get("candidates", []):
                code = cand.get("code", "")
                name = cand.get("name", "")
                if code and name:
                    names[code] = name
    return names


def get_stock_suffix(code: str) -> str:
    """判断股票代码对应的交易所后缀"""
    if code.startswith("6") or code.startswith("9"):
        return "SH"  # 上海
    elif code.startswith("0") or code.startswith("3"):
        return "SZ"  # 深圳
    elif code.startswith("4") or code.startswith("8"):
        return "BJ"  # 北交所
    return ""


def export_plain_text(recommendations: list[dict], output_path: Path, min_score: float) -> None:
    """导出纯代码文本格式（每行一个代码）"""
    codes = []
    for r in recommendations:
        if r.get("total_score", 0) >= min_score:
            codes.append(r["code"])

    with open(output_path, "w", encoding="utf-8") as f:
        for code in sorted(codes):
            f.write(f"{code}\n")

    print(f"[OK] 导出纯代码文本：{output_path}（{len(codes)} 只）")


def export_csv_with_name(recommendations: list[dict], output_path: Path,
                        min_score: float, names: dict[str, str]) -> None:
    """导出 CSV 格式（股票代码,股票名称）"""
    rows = []
    for r in recommendations:
        if r.get("total_score", 0) >= min_score:
            code = r["code"]
            name = names.get(code, "")
            rows.append([code, name])

    rows.sort(key=lambda x: x[0])

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["股票代码", "股票名称"])
        writer.writerows(rows)

    print(f"[OK] 导出 CSV 格式：{output_path}（{len(rows)} 只）")


def export_eastmoney_format(recommendations: list[dict], output_path: Path,
                          min_score: float, names: dict[str, str]) -> None:
    """
    导出东方财富格式（股票代码带交易所后缀）
    格式：600026,600026
    每行：代码,代码（东方财富导入时只需代码列）
    """
    codes = []
    for r in recommendations:
        if r.get("total_score", 0) >= min_score:
            code = r["code"]
            suffix = get_stock_suffix(code)
            if suffix:
                codes.append((f"{code}.{suffix}", code, names.get(code, "")))

    codes.sort(key=lambda x: x[1])

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        # 东方财富格式：股票代码
        for full_code, code, name in codes:
            writer.writerow([full_code])

    print(f"[OK] 导出东方财富格式：{output_path}（{len(codes)} 只）")
    print(f"     格式说明：每行一个股票代码（如 600026.SH），可直接导入东方财富")


def main():
    parser = argparse.ArgumentParser(description="导出东方财富可导入的股票文件")
    parser.add_argument(
        "--review-dir",
        type=Path,
        default=DEFAULT_SUGGESTION,
        help="评审结果目录（默认 data/review）"
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=DEFAULT_CANDIDATES,
        help="候选股票文件（默认 data/candidates/candidates_latest.json）"
    )
    parser.add_argument(
        "--format",
        choices=["plain", "csv", "eastmoney"],
        default="eastmoney",
        help="导出格式：plain=纯代码, csv=带名称, eastmoney=带后缀（默认 eastmoney）"
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=4.0,
        help="最低评分门槛（默认 4.0）"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出文件路径（默认自动生成）"
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="无 AI 复评模式，直接从候选股票导出（不使用评分门槛）"
    )
    args = parser.parse_args()

    # 加载数据
    names = load_stock_names(args.candidates)

    if args.no_ai:
        # 无 AI 复评模式：直接从候选股票导出
        recommendations, pick_date, strategy_name = load_candidates_directly(args.candidates)
        print(f"[INFO] 模式：无 AI 复评，直接导出候选股票")
    else:
        # 有 AI 复评模式：从复评结果加载
        recommendations, pick_date, _ = load_suggestion(args.review_dir)
        strategy_name = load_strategies(args.candidates)
        print(f"[INFO] 模式：基于 AI 复评结果")

    print(f"[INFO] 选股日期：{pick_date}")
    print(f"[INFO] 策略：{strategy_name}")
    print(f"[INFO] 评分≥{args.min_score} 的股票：{len([r for r in recommendations if r.get('total_score', 0) >= args.min_score])} 只")

    # 生成输出路径
    if args.output:
        output_path = args.output
    else:
        eastmoney_dir = _ROOT / "data" / "eastmoney"
        eastmoney_dir.mkdir(parents=True, exist_ok=True)
        output_path = eastmoney_dir / f"eastmoney_{strategy_name}_{pick_date}.txt"
        if args.format == "csv":
            output_path = output_path.with_suffix(".csv")

    # 导出
    if args.format == "plain":
        export_plain_text(recommendations, output_path, args.min_score)
    elif args.format == "csv":
        export_csv_with_name(recommendations, output_path, args.min_score, names)
    else:
        export_eastmoney_format(recommendations, output_path, args.min_score, names)

    print(f"\n📁 文件位置：{output_path}")
    print("💡 导入东方财富方法：自选股 → 右键 → 导入自选股 → 选择文件")


if __name__ == "__main__":
    main()
