"""
fetch_kline.py
~~~~~~~~~~~~~~
使用 AkShare 从东方财富抓取 A 股日线 K 线数据。

用法：
    python pipeline/fetch_kline.py
    python pipeline/fetch_kline.py --config config/fetch_kline.yaml

配置：
    默认读取 config/fetch_kline.yaml。

依赖：
    pip install akshare pandas pyyaml tqdm

注意：
    - 无需注册/Token，完全免费
    - 东方财富数据源，请遵守访问频率限制
    - 默认每次请求间隔 1.5 秒，勿随意降低
"""

from __future__ import annotations

import datetime as dt
import logging
import random
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

import pandas as pd
import yaml
from tqdm import tqdm

warnings.filterwarnings("ignore")

# AkShare 依赖 urllib3 v2，需要 OpenSSL 1.1.1+
try:
    import akshare as ak
except ImportError:
    print("[ERROR] 请先安装 AkShare：pip install akshare", file=sys.stderr)
    sys.exit(1)

# --------------------------- 全局日志配置 --------------------------- #
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_DIR = _PROJECT_ROOT / "data" / "logs"


def _resolve_cfg_path(path_like: str, base_dir: Path = _PROJECT_ROOT) -> Path:
    p = Path(path_like)
    return p if p.is_absolute() else (base_dir / p)


def _default_log_path() -> Path:
    today = dt.date.today().strftime("%Y-%m-%d")
    return _DEFAULT_LOG_DIR / f"fetch_{today}.log"


def setup_logging(log_path: Optional[Path] = None) -> None:
    if log_path is None:
        log_path = _default_log_path()
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, mode="a", encoding="utf-8"),
        ],
    )


logger = logging.getLogger("fetch_from_stocklist")

# --------------------------- 限流/封禁处理 --------------------------- #
# 东方财富对频繁访问比较敏感，建议不要随意降低此值
DEFAULT_REQUEST_DELAY = 1.5  # 每次请求间隔（秒）
COOLDOWN_SECS = 600          # 被封禁后冷却时间（秒）

BAN_PATTERNS = (
    "访问频繁", "请稍后", "超过频率", "频繁访问",
    "too many requests", "429",
    "forbidden", "403",
    "max retries exceeded",
    "net Error", "网络错误",
    "remotedisconnected",
    "connection aborted",
    "connection reset",
    "connection refused",
)


def _looks_like_ban(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(pat.lower() in msg for pat in BAN_PATTERNS)


class RateLimitError(RuntimeError):
    """命中限流/封禁，需长时间冷却后重试。"""
    pass


def _cool_sleep(base_seconds: int) -> None:
    jitter = random.uniform(0.9, 1.2)
    sleep_s = max(1, int(base_seconds * jitter))
    logger.warning("疑似被限流/封禁，进入冷却期 %d 秒...", sleep_s)
    time.sleep(sleep_s)


# --------------------------- AkShare K线抓取 --------------------------- #

# AkShare 列名映射（东方财富中文列名 → 内部标准列名）
_COLUMN_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
}


def _get_kline_akshare(code: str, start: str, end: str) -> pd.DataFrame:
    """
    使用 AkShare 抓取单支股票的日线数据（前复权）。
    AkShare 的 stock_zh_a_hist 直接使用 6 位纯数字代码。
    """
    try:
        df = ak.stock_zh_a_hist(
            symbol=code.zfill(6),        # 6 位代码，如 '000001'
            period="daily",
            start_date=start,            # YYYYMMDD
            end_date=end,
            adjust="qfq",               # 前复权
        )
    except Exception as e:
        if _looks_like_ban(e):
            raise RateLimitError(str(e)) from e
        raise

    if df is None or df.empty:
        return pd.DataFrame()

    # 列名映射
    df = df.rename(columns=_COLUMN_MAP)

    # 只保留需要的列（与 pipeline_io 等模块保持一致）
    needed = ["date", "open", "close", "high", "low", "volume"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    df = df[needed].copy()

    # 类型转换
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.sort_values("date").reset_index(drop=True)


def validate(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    if df["date"].isna().any():
        raise ValueError("存在缺失日期！")
    if (df["date"] > pd.Timestamp.today()).any():
        raise ValueError("数据包含未来日期，可能抓取错误！")
    return df


# --------------------------- 读取 stocklist.csv & 过滤板块 --------------------------- #

def _filter_by_boards_stocklist(df: pd.DataFrame, exclude_boards: set) -> pd.DataFrame:
    """根据 ts_code 过滤板块（创业板/科创板/北交所）。"""
    ts = df["ts_code"].astype(str).str.upper()
    num = ts.str.extract(r"(\d{6})", expand=False).str.zfill(6)
    mask = pd.Series(True, index=df.index)

    if "gem" in exclude_boards:
        mask &= ~((ts.str.endswith(".SZ")) & num.str.startswith(("300", "301")))
    if "star" in exclude_boards:
        mask &= ~((ts.str.endswith(".SH")) & num.str.startswith(("688",)))
    if "bj" in exclude_boards:
        mask &= ~(ts.str.endswith(".BJ") | num.str.startswith(("4", "8")))

    return df[mask].copy()


def load_codes_from_stocklist(stocklist_csv: Path, exclude_boards: set) -> List[str]:
    df = pd.read_csv(stocklist_csv)
    df = _filter_by_boards_stocklist(df, exclude_boards)
    codes = df["symbol"].astype(str).str.zfill(6).tolist()
    codes = list(dict.fromkeys(codes))
    logger.info("从 %s 读取到 %d 只股票（排除板块：%s）",
                stocklist_csv, len(codes), ",".join(sorted(exclude_boards)) or "无")
    return codes


# --------------------------- 单只抓取（全量覆盖保存） --------------------------- #

def fetch_one(
    code: str,
    start: str,
    end: str,
    out_dir: Path,
    request_delay: float = DEFAULT_REQUEST_DELAY,
) -> bool:
    """
    抓取单支股票数据并保存为 CSV。
    返回 True 表示成功，False 表示失败。
    """
    csv_path = out_dir / f"{code}.csv"

    for attempt in range(1, 4):
        try:
            new_df = _get_kline_akshare(code, start, end)
            if new_df.empty:
                logger.debug("%s 无数据，生成空表。", code)
                new_df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
            new_df = validate(new_df)
            new_df.to_csv(csv_path, index=False)
            return True

        except RateLimitError:
            logger.error("%s 第 %d 次抓取疑似被封禁，沉睡 %d 秒", code, attempt, COOLDOWN_SECS)
            _cool_sleep(COOLDOWN_SECS)

        except Exception as e:
            if _looks_like_ban(e):
                logger.error("%s 第 %d 次抓取疑似被封禁，沉睡 %d 秒", code, attempt, COOLDOWN_SECS)
                _cool_sleep(COOLDOWN_SECS)
            else:
                silent_seconds = 30 * attempt
                logger.info("%s 第 %d 次抓取失败，%d 秒后重试：%s", code, attempt, silent_seconds, e)
                time.sleep(silent_seconds)

    logger.error("%s 三次抓取均失败，已跳过！", code)
    return False


# --------------------------- 配置加载 --------------------------- #

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "fetch_kline.yaml"


def _load_config(config_path: Path = _CONFIG_PATH) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(f"找不到配置文件：{config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    logger.info("已加载配置文件：%s", config_path.resolve())
    return cfg


# --------------------------- 主入口 --------------------------- #

def main(log_path: Optional[Path] = None):
    cfg = _load_config()

    if log_path is None:
        cfg_log = cfg.get("log")
        log_path = _resolve_cfg_path(cfg_log) if cfg_log else _default_log_path()
    setup_logging(log_path)
    logger.info("日志文件：%s", Path(log_path).resolve())

    # 读取请求间隔配置（秒）
    request_delay = float(cfg.get("request_delay", DEFAULT_REQUEST_DELAY))
    if request_delay < 1.0:
        logger.warning("请求间隔 %.1f 秒过小，东方财富可能封禁 IP，建议 ≥1.0 秒", request_delay)

    # 日期解析
    raw_start = str(cfg.get("start", "20190101"))
    raw_end = str(cfg.get("end", "today"))
    start = dt.date.today().strftime("%Y%m%d") if raw_start.lower() == "today" else raw_start
    end = dt.date.today().strftime("%Y%m%d") if raw_end.lower() == "today" else raw_end

    out_dir = _resolve_cfg_path(cfg.get("out", "./data"))
    out_dir.mkdir(parents=True, exist_ok=True)

    # 读取股票池
    stocklist_path = _resolve_cfg_path(cfg.get("stocklist", "./pipeline/stocklist.csv"))
    exclude_boards = set(cfg.get("exclude_boards") or [])
    codes = load_codes_from_stocklist(stocklist_path, exclude_boards)

    if not codes:
        logger.error("stocklist 为空或被过滤后无代码，请检查。")
        sys.exit(1)

    logger.info(
        "开始抓取 %d 支股票 | 数据源:AkShare/东方财富(日线,qfq) | "
        "间隔:%.1f秒 | 日期:%s → %s | 排除:%s",
        len(codes), request_delay, start, end, ",".join(sorted(exclude_boards)) or "无",
    )

    # 多线程抓取（每个线程内部自己控制请求间隔）
    workers = int(cfg.get("workers", 4))  # 默认降为 4，避免东方财富封禁
    if workers > 4:
        logger.warning("并发数 > 4 会增加被东方财富封禁的风险，建议 ≤ 4")

    success_count = 0
    fail_count = 0

    def _fetch_with_delay(code):
        nonlocal success_count, fail_count
        time.sleep(random.uniform(request_delay * 0.5, request_delay * 1.5))
        ok = fetch_one(code, start, end, out_dir, request_delay)
        if ok:
            success_count += 1
        else:
            fail_count += 1
        return ok

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_with_delay, code): code for code in codes}
        for f in tqdm(as_completed(futures), total=len(futures), desc="下载进度"):
            pass  # 结果在全局计数器里

    logger.info("全部任务完成 | 成功:%d | 失败:%d | 数据已保存至 %s",
                success_count, fail_count, out_dir.resolve())


if __name__ == "__main__":
    main()
