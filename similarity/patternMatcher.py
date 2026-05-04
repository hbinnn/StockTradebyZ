"""
patternMatcher.py
~~~~~~~~~~~~~~~~~
完美图形相似度比对模块。

功能：
    1. 加载完美图形案例配置
    2. 提取K线特征（价格、量价、KDJ等）
    3. 计算股票与完美图形的相似度
    4. 查找高相似度案例

用法：
    python -m similarity.patternMatcher
    python -m similarity.patternMatcher --code 600026 --date 2026-04-17
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
import yaml

# ─────────────────────────────────────────────────────────────────────────────
# 配置路径
# ─────────────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _ROOT / "config" / "perfect_patterns.yaml"
_DEFAULT_RAW = _ROOT / "data" / "raw"
_DEFAULT_CANDIDATES = _ROOT / "data" / "candidates" / "candidates_latest.json"


# ─────────────────────────────────────────────────────────────────────────────
# 相似度阈值
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_THRESHOLDS = {
    "high": 0.80,   # ★★★
    "medium": 0.75,  # ★★
    "low": 0.70,     # ★
}

DEFAULT_WINDOW = 30  # 特征窗口大小


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def _to_ts_code(code: str) -> str:
    """把6位code映射到标准 ts_code 后缀"""
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "9")):
        return f"{code}.SH"
    elif code.startswith(("4", "8")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


def _to_simple_code(ts_code: str) -> str:
    """从 ts_code 提取简单代码，如 600026.SH -> 600026"""
    return ts_code.split(".")[0] if "." in ts_code else ts_code


# ─────────────────────────────────────────────────────────────────────────────
# 数据加载
# ─────────────────────────────────────────────────────────────────────────────

def load_raw_data(code: str, raw_dir: Path = _DEFAULT_RAW) -> pd.DataFrame:
    """
    加载指定股票的原始日线数据。

    Returns:
        DataFrame，含 date,open,close,high,low,volume 列，按日期排序
    """
    csv_path = raw_dir / f"{code}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"找不到数据文件：{csv_path}")

    df = pd.read_csv(csv_path)
    df.columns = [c.lower() for c in df.columns]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def get_data_at_date(df: pd.DataFrame, target_date: str, window: int = 30) -> pd.DataFrame:
    """
    获取指定日期之前/左右的N根K线数据用于特征提取。

    Args:
        df: 完整日线数据
        target_date: 目标日期（YYYY-MM-DD）
        window: 截取窗口大小

    Returns:
        截取后的 DataFrame
    """
    target = pd.to_datetime(target_date)
    # 找到目标日期在数据中的位置
    idx_list = df[df["date"] <= target].index.tolist()
    if not idx_list:
        raise ValueError(f"目标日期 {target_date} 早于数据最早日期")

    # 取目标日期之前window根K线
    end_idx = idx_list[-1]
    start_idx = max(0, end_idx - window + 1)
    return df.iloc[start_idx:end_idx + 1].copy()


# ─────────────────────────────────────────────────────────────────────────────
# 特征提取
# ─────────────────────────────────────────────────────────────────────────────

def extract_features(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    """
    从K线数据中提取特征向量。

    特征包括：
        - price_norm: 归一化价格序列
        - volume_norm: 归一化成交量序列
        - returns: 收益率序列
        - kdj_k, kdj_d, kdj_j: KDJ指标序列
        - volatility: 波动率序列

    Returns:
        特征字典，每个值是一维numpy数组
    """
    close = df["close"].astype(float).values
    open_ = df["open"].astype(float).values
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    volume = df["volume"].astype(float).values

    # 价格归一化：(close - mean) / std
    price_mean = np.mean(close)
    price_std = np.std(close)
    if price_std == 0:
        price_std = 1e-6
    price_norm = (close - price_mean) / price_std

    # 成交量归一化
    vol_mean = np.mean(volume)
    vol_std = np.std(volume)
    if vol_std == 0:
        vol_std = 1e-6
    volume_norm = (volume - vol_mean) / vol_std

    # 收益率序列
    returns = np.diff(close) / close[:-1]
    if len(returns) > 0:
        returns = (returns - np.mean(returns)) / (np.std(returns) + 1e-6)
    else:
        returns = np.array([])

    # KDJ计算
    n = 9
    m1 = 3
    m2 = 3
    llv = pd.Series(low).rolling(n, min_periods=1).min().values
    hhv = pd.Series(high).rolling(n, min_periods=1).max().values
    denom = hhv - llv
    denom = np.where(denom == 0, 1e-6, denom)
    rsv = (close - llv) / denom * 100.0
    alpha_k = 1.0 / m1
    alpha_d = 1.0 / m2
    k = pd.Series(rsv).ewm(alpha=alpha_k, adjust=False).mean().values
    d = pd.Series(k).ewm(alpha=alpha_d, adjust=False).mean().values
    j = 3 * k - 2 * d

    # 归一化KDJ
    def norm_series(s):
        s_mean = np.mean(s)
        s_std = np.std(s)
        if s_std == 0:
            s_std = 1e-6
        return (s - s_mean) / s_std

    kdj_k_norm = norm_series(k)
    kdj_d_norm = norm_series(d)
    kdj_j_norm = norm_series(j)

    # 波动率（收益率的滚动标准差）
    if len(close) >= 5:
        ret = np.diff(close) / close[:-1]
        vol = pd.Series(ret).rolling(5, min_periods=1).std().values
        vol = np.nan_to_num(vol, nan=0.0)
    else:
        vol = np.zeros(len(close))

    return {
        "price_norm": price_norm,
        "volume_norm": volume_norm,
        "returns": returns,
        "kdj_k": kdj_k_norm,
        "kdj_d": kdj_d_norm,
        "kdj_j": kdj_j_norm,
        "volatility": vol,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 相似度计算
# ─────────────────────────────────────────────────────────────────────────────

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的余弦相似度"""
    a = a.astype(float)
    b = b.astype(float)
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def euclidean_normalized(a: np.ndarray, b: np.ndarray) -> float:
    """计算归一化欧氏距离，转换为相似度（1 - 距离归一化）"""
    a = a.astype(float)
    b = b.astype(float)
    dist = np.linalg.norm(a - b)
    max_dist = np.linalg.norm(a) + np.linalg.norm(b) + 1e-6
    return 1.0 - min(dist / max_dist, 1.0)


def compute_similarity(
    features1: Dict[str, np.ndarray],
    features2: Dict[str, np.ndarray],
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """
    计算两个特征集的综合相似度。

    Args:
        features1: 股票1的特征字典
        features2: 股票2的特征字典
        weights: 各特征权重，默认权重如下

    Returns:
        综合相似度（0~1之间）
    """
    if weights is None:
        weights = {
            "price_norm": 0.30,
            "volume_norm": 0.25,
            "returns": 0.20,
            "kdj_k": 0.10,
            "kdj_d": 0.05,
            "kdj_j": 0.05,
            "volatility": 0.05,
        }

    # 对齐长度（取较短的长度）
    def align(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        min_len = min(len(a), len(b))
        return a[:min_len], b[:min_len]

    total_score = 0.0
    total_weight = 0.0

    for key, weight in weights.items():
        if key in features1 and key in features2:
            a, b = align(features1[key], features2[key])
            if len(a) < 3:
                continue

            # 价格、成交量、KDJ用余弦相似度
            if key in ("price_norm", "volume_norm", "kdj_k", "kdj_d", "kdj_j"):
                sim = cosine_similarity(a, b)
            else:
                sim = euclidean_normalized(a, b)

            total_score += sim * weight
            total_weight += weight

    if total_weight == 0:
        return 0.0

    return total_score / total_weight


# ─────────────────────────────────────────────────────────────────────────────
# 完美图形案例加载
# ─────────────────────────────────────────────────────────────────────────────

def load_perfect_patterns(
    config_path: Path = _DEFAULT_CONFIG,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    加载所有策略的完美图形案例（从 strategies/*/patterns.yaml 读取）。

    Returns:
        字典，key为策略名，value为案例列表
    """
    strategies: Dict[str, List[Dict[str, Any]]] = {}
    strat_dir = _ROOT / "strategies"
    if not strat_dir.exists():
        return strategies
    for d in sorted(strat_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        p = d / "patterns.yaml"
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        cases = data.get("cases", [])
        strategies[d.name] = cases
    return strategies


def get_pattern_stars(similarity: float, thresholds: Dict[str, float] = None) -> str:
    """根据相似度返回星级标注"""
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS

    if similarity >= thresholds["high"]:
        return "★★★"
    elif similarity >= thresholds["medium"]:
        return "★★"
    elif similarity >= thresholds["low"]:
        return "★"
    else:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# 核心功能：查找相似完美图形
# ─────────────────────────────────────────────────────────────────────────────

def find_similar_patterns(
    code: str,
    pick_date: str,
    patterns: Dict[str, List[Dict[str, Any]]],
    raw_dir: Path = _DEFAULT_RAW,
    window: int = DEFAULT_WINDOW,
    threshold: float = 0.70,
    thresholds: Dict[str, float] = None,
) -> List[Dict[str, Any]]:
    """
    查找与候选股票相似的完美图形案例。

    Args:
        code: 股票代码
        pick_date: 选股日期
        patterns: 完美图形案例字典
        raw_dir: 原始数据目录
        window: 特征窗口大小
        threshold: 最低相似度阈值
        thresholds: 星级阈值

    Returns:
        相似案例列表
        [{
            "strategy": "b1",
            "case_code": "600026",
            "case_date": "2025-03-15",
            "case_description": "底部放量突破",
            "similarity": 0.85,
            "stars": "★★★"
        }, ...]
    """
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS

    # 加载候选股票的特征
    try:
        df_candidate = load_raw_data(code, raw_dir)
        df_window = get_data_at_date(df_candidate, pick_date, window)
        features_candidate = extract_features(df_window)
    except Exception as e:
        print(f"[WARN] 加载候选股票 {code} 数据失败：{e}")
        return []

    results = []

    for strategy, cases in patterns.items():
        if cases is None:
            continue
        for case in cases:
            case_code = case.get("code", "")
            case_date = case.get("perfect_date", "")
            case_desc = case.get("description", "")

            if not case_code or not case_date:
                continue

            try:
                # 加载完美案例的特征
                df_case = load_raw_data(case_code, raw_dir)
                df_case_window = get_data_at_date(df_case, case_date, window)
                features_case = extract_features(df_case_window)

                # 计算相似度
                similarity = compute_similarity(features_candidate, features_case)

                if similarity >= threshold:
                    results.append({
                        "strategy": strategy,
                        "case_code": case_code,
                        "case_date": case_date,
                        "case_description": case_desc,
                        "similarity": round(similarity, 3),
                        "stars": get_pattern_stars(similarity, thresholds),
                    })
            except Exception as e:
                print(f"[WARN] 处理案例 {case_code} {case_date} 失败：{e}")
                continue

    # 按相似度降序排序
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="完美图形相似度比对")
    parser.add_argument("--code", type=str, help="股票代码")
    parser.add_argument("--date", type=str, help="选股日期（YYYY-MM-DD）")
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG, help="配置文件路径")
    parser.add_argument("--raw-dir", type=Path, default=_DEFAULT_RAW, help="原始数据目录")
    parser.add_argument("--threshold", type=float, default=0.70, help="最低相似度阈值")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="特征窗口大小")
    args = parser.parse_args()

    # 加载配置
    patterns = load_perfect_patterns(args.config)
    print(f"[INFO] 加载 {len(patterns)} 个策略的完美图形案例")

    # 单股测试模式
    if args.code and args.date:
        print(f"\n[INFO] 股票：{args.code}  日期：{args.date}")
        results = find_similar_patterns(
            code=args.code,
            pick_date=args.date,
            patterns=patterns,
            raw_dir=args.raw_dir,
            window=args.window,
            threshold=args.threshold,
        )

        if results:
            print(f"\n找到 {len(results)} 个相似案例：")
            for r in results:
                print(f"  {r['stars']} [{r['strategy']}] {r['case_code']} ({r['case_date']}) - "
                      f"{r['case_description']}  相似度：{r['similarity']:.3f}")
        else:
            print("\n未找到相似案例")
        return

    # 批量模式：加载候选股票
    if not _DEFAULT_CANDIDATES.exists():
        print(f"[ERROR] 找不到候选股票文件：{_DEFAULT_CANDIDATES}")
        sys.exit(1)

    with open(_DEFAULT_CANDIDATES, encoding="utf-8") as f:
        candidates_data = json.load(f)

    pick_date = candidates_data.get("pick_date", "")
    candidates = candidates_data.get("candidates", [])

    print(f"[INFO] 选股日期：{pick_date}  候选股票：{len(candidates)} 只")

    # 按策略分组处理
    all_results = {}
    for cand in candidates:
        code = cand.get("code", "")
        strategy = cand.get("strategy", "b1").lower()

        results = find_similar_patterns(
            code=code,
            pick_date=pick_date,
            patterns=patterns,
            raw_dir=args.raw_dir,
            window=args.window,
            threshold=args.threshold,
        )

        if results:
            all_results[code] = results
            best = results[0]
            print(f"  {code} [{strategy}]: {best['stars']} {best['case_code']} ({best['case_date']}) similarity={best['similarity']:.3f}")

    # 保存结果
    output_dir = _ROOT / "data" / "pattern_matched"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"matched_{pick_date}.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "date": pick_date,
            "threshold": args.threshold,
            "window": args.window,
            "results": all_results,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] 结果已保存：{output_file}")
    print(f"      共 {len(all_results)} 只股票匹配到相似案例")


if __name__ == "__main__":
    main()
