"""
scripts/export_kline_charts.py
AgentTrader · 批量导出候选股票 K线图（日线 + 周线）

用法：
    python scripts/export_kline_charts.py [--date YYYY-MM-DD] [--bars 120] [--weekly-bars 60]

输出目录：
    data/kline/<date>/<code>_day.jpg
    data/kline/<date>/<code>_week.jpg

依赖：
    pip install kaleido   （Plotly 静态图导出必需）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

# ── 路径设置 ──────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "dashboard"))

from components.charts import make_daily_chart, make_weekly_chart  # noqa: E402


# ── 数据加载 ──────────────────────────────────────────────────────────────────

def _load_candidates(candidates_path: Path) -> tuple[list[dict], str]:
    """从 candidates JSON 文件中读取候选股票列表及 pick_date。

    Returns:
        (candidates, pick_date)  candidates 包含完整 dict（含 code、strategy 等字段）
    """
    if not candidates_path.exists():
        print(f"[ERROR] 候选文件不存在：{candidates_path}")
        sys.exit(1)
    with open(candidates_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    candidates: list[dict] = data.get("candidates", [])
    pick_date = data.get("pick_date", "")
    brick_count = sum(1 for c in candidates if c.get("strategy") == "brick")
    print(f"[INFO] 候选股票数量：{len(candidates)}（砖型图 {brick_count} 只） pick_date：{pick_date or '(未设置)'}  来源：{candidates_path.name}")
    return candidates, pick_date


def _dedup_candidates(candidates: list[dict]) -> list[dict]:
    """按 code 去重，若任一策略是 brick 则标记为砖型图图表。"""
    seen: dict[str, dict] = {}
    for c in candidates:
        code = c["code"]
        if code in seen:
            # 合并策略标记：已有或新来的是 brick 则标记
            if c.get("strategy") == "brick":
                seen[code]["_is_brick"] = True
                seen[code]["strategies"] = seen[code].get("strategies", []) + [c.get("strategy")]
        else:
            c["_is_brick"] = c.get("strategy") == "brick"
            c["strategies"] = [c.get("strategy", "")]
            seen[code] = c
    return list(seen.values())


def _load_raw(code: str, raw_dir: Path) -> pd.DataFrame:
    """加载单只股票日线 CSV。"""
    csv = raw_dir / f"{code}.csv"
    if not csv.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv)
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


# ── 导出单张图 ────────────────────────────────────────────────────────────────

def _export_fig(fig, out_path: Path, width: int, height: int) -> None:
    """将 Plotly Figure 导出为 JPEG。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_image(
        str(out_path),
        format="jpg",
        width=width,
        height=height,
        scale=2,        # 2× 分辨率，适合屏幕阅读
    )


# ── 主流程 ────────────────────────────────────────────────────────────────────

# 配置字典（直接修改此处）
CONFIG = {
    "candidates": str(_ROOT / "data" / "candidates" / "candidates_latest.json"),
    "raw_dir":    str(_ROOT / "data" / "raw"),
    "out_dir":    str(_ROOT / "data" / "kline"),
    "bars":       120,   # 日线显示 K 线数量（0 = 全部）
    "weekly_bars": 60,   # 周线显示 K 线数量（0 = 全部）
    "day_width":  1400,
    "day_height": 700,
    "week_width": 1400,
    "week_height": 700,
}


# 砖型图计算参数（与 rules_preselect.yaml 保持一致）
BRICK_PARAMS = {"n": 4, "m1": 4, "m2": 6, "m3": 6, "t": 4.0, "shift1": 90.0, "shift2": 100.0, "sma_w1": 1, "sma_w2": 1, "sma_w3": 1}


def main() -> None:
    candidates_path = Path(CONFIG["candidates"])
    raw_dir         = Path(CONFIG["raw_dir"])

    candidates, pick_date = _load_candidates(candidates_path)

    # 导出日期直接读取 candidates.json 的 pick_date
    export_date = pick_date
    if not export_date:
        print("[ERROR] candidates.json 中未设置 pick_date，无法确定导出日期。")
        sys.exit(1)
    print(f"[INFO] 导出日期：{export_date}")

    out_root = Path(CONFIG["out_dir"]) / export_date

    ok_count    = 0
    skip_count  = 0

    codes_dedup = _dedup_candidates(candidates)

    for c in codes_dedup:
        code: str = c["code"]
        strategies: list = c.get("strategies", [])
        is_brick = c.get("_is_brick", False)
        df_raw = _load_raw(code, raw_dir)
        if df_raw.empty:
            print(f"[SKIP] {code}  — 无日线数据")
            skip_count += 1
            continue

        # ── 日线图 ────────────────────────────────────────────────────
        day_path = out_root / f"{code}_day.jpg"
        try:
            chart_height = CONFIG["day_height"] + 140 if is_brick else CONFIG["day_height"]
            strategy_label = "+".join(strategies) if strategies else ""
            fig_day = make_daily_chart(
                df_raw, code,
                bars=CONFIG["bars"],
                height=chart_height,
                show_brick=is_brick,
                brick_params=BRICK_PARAMS,
                strategy=strategy_label,
            )
            _export_fig(fig_day, day_path, CONFIG["day_width"], chart_height)
        except Exception as e:
            print(f"[ERROR] {code} 日线导出失败：{e}")
            skip_count += 1
            continue

        print(f"[OK]   {code}  → {day_path.name}")
        ok_count += 1

    print(
        f"\n导出完成：成功 {ok_count} 只，跳过 {skip_count} 只。"
        f"\n输出目录：{out_root}"
    )


if __name__ == "__main__":
    main()
