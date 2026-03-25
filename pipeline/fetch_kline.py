"""
fetch_kline.py
~~~~~~~~~~~~~~
使用 Baostock 抓取 A 股日线 K 线数据（前复权）。

用法：
    python pipeline/fetch_kline.py
    python pipeline/fetch_kline.py --config config/fetch_kline.yaml

配置：
    默认读取 config/fetch_kline.yaml。

依赖：
    pip install baostock pandas pyyaml tqdm

数据源：
    Baostock → 中国结算（BSGS）
    - 无需注册/Token，完全免费
    - 支持日线/周线/月线/分钟线
    - 支持前复权(qfq)/后复权(hfq)/不复权

注意：
    Baostock 对高频请求有限制，默认每次请求间隔 1 秒。
    建议不要并发太高，否则可能被临时限制。
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
from typing import Dict, List, Optional, Set

import pandas as pd
import yaml
from tqdm import tqdm

warnings.filterwarnings("ignore")

try:
    import baostock as bs
except ImportError:
    print("[ERROR] 请先安装 baostock：pip install baostock", file=sys.stderr)
    sys.exit(1)

# --------------------------- 全局日志配置 --------------------------- #

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_DIR = _PROJECT_ROOT / "data" / "logs"


def _resolve_cfg_path(path_like: str, base_dir: Path = _PROJECT_ROOT) -> Path:
    p = Path(path_like)
    return p if p.is_absolute() else (base_dir / p)


def _default_log_path() -> Path:
    return _DEFAULT_LOG_DIR / f"fetch_{dt.date.today().strftime('%Y-%m-%d')}.log"


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

# --------------------------- 限流配置 --------------------------- #

DEFAULT_REQUEST_DELAY = 1.0   # 每次请求间隔（秒）
BAN_PATTERNS = (
    "error_code': '10103012",
    "error_msg': '请求频率过快",
    "系统繁忙",
    "too many",
    "频率",
)


def _looks_like_ban(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(pat.lower() in msg for pat in BAN_PATTERNS)


class RateLimitError(RuntimeError):
    """命中频率限制，需冷却后重试。"""
    pass


def _cool_sleep(base: float) -> None:
    jitter = random.uniform(0.9, 1.2)
    sleep_s = max(1, base * jitter)
    logger.warning("疑似频率限制，进入冷却期 %d 秒...", int(sleep_s))
    time.sleep(sleep_s)


# --------------------------- Baostock K线抓取 --------------------------- #

# 复权标志映射
_ADJUSTFLAG_MAP = {
    "qfq": "2",   # 前复权
    "hfq": "1",   # 后复权
    "": "3",      # 不复权
}


def _to_baostock_code(code: str) -> str:
    """
    将 6 位代码转换为 baostock 格式。
    - 60/68 开头 → sh.600xxx（上交所）
    - 4/8 开头   → sh.4xxxx / sh.8xxxx（北交所-上交所）
    - 其他       → sz.00xxxx（深交所）
    - 4/8 开头   → bj.4xxxx / bj.8xxxx（北交所）
    """
    code = str(code).zfill(6)
    if code.startswith(("60", "68")):
        return f"sh.{code}"
    elif code.startswith(("4", "8")):
        return f"bj.{code}"
    else:
        return f"sz.{code}"


def _get_kline_baostock(
    code: str,
    start: str,
    end: str,
    adjustflag: str = "qfq",
) -> pd.DataFrame:
    """
    使用 Baostock 获取单支股票的日线数据。
    代码示例: 'sh.600519'（茅台）
    日期格式: '2024-01-01'
    """
    bs_code = _to_baostock_code(code)

    # 转换日期格式 YYYYMMDD → YYYY-MM-DD
    start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:8]}" if len(start) == 8 else start
    end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:8]}" if len(end) == 8 else end

    adj_flag = _ADJUSTFLAG_MAP.get(adjustflag, "2")

    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,open,close,high,low,volume",
        start_date=start_fmt,
        end_date=end_fmt,
        frequency="d",
        adjustflag=adj_flag,
    )

    if rs.error_code != "0":
        raise RuntimeError(f"Baostock 查询失败: {rs.error_msg}")

    data_list: List[tuple] = []
    while rs.error_code == "0" and rs.next():
        data_list.append(rs.get_row_data())

    if not data_list:
        return pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])

    df = pd.DataFrame(data_list, columns=["date", "open", "close", "high", "low", "volume"])

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

def _filter_by_boards_stocklist(df: pd.DataFrame, exclude_boards: Set[str]) -> pd.DataFrame:
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


def load_codes_from_stocklist(stocklist_csv: Path, exclude_boards: Set[str]) -> List[str]:
    df = pd.read_csv(stocklist_csv)
    df = _filter_by_boards_stocklist(df, exclude_boards)
    codes = df["symbol"].astype(str).str.zfill(6).tolist()
    codes = list(dict.fromkeys(codes))
    logger.info("从 %s 读取到 %d 只股票（排除板块：%s）",
                stocklist_csv, len(codes), ",".join(sorted(exclude_boards)) or "无")
    return codes


# --------------------------- 单只抓取 --------------------------- #

def fetch_one(
    code: str,
    start: str,
    end: str,
    out_dir: Path,
    adjustflag: str = "qfq",
    request_delay: float = DEFAULT_REQUEST_DELAY,
) -> bool:
    csv_path = out_dir / f"{code}.csv"

    for attempt in range(1, 4):
        try:
            new_df = _get_kline_baostock(code, start, end, adjustflag)
            if new_df.empty:
                logger.debug("%s 无数据，生成空表。", code)
                new_df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
            new_df = validate(new_df)
            new_df.to_csv(csv_path, index=False)
            return True

        except Exception as e:
            if _looks_like_ban(e):
                logger.error("%s 第 %d 次疑似频率限制，沉睡 %d 秒", code, attempt, int(request_delay * 60))
                _cool_sleep(request_delay * 60)
            else:
                silent_seconds = 30 * attempt
                logger.info("%s 第 %d 次失败，%d 秒后重试：%s", code, attempt, silent_seconds, e)
                time.sleep(silent_seconds)

    logger.error("%s 三次抓取均失败，已跳过！", code)
    return False


# --------------------------- 配置加载 --------------------------- #

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "fetch_kline.yaml"


def _load_config(config_path: Path = _CONFIG_PATH) -> Dict:
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

    request_delay = float(cfg.get("request_delay", DEFAULT_REQUEST_DELAY))
    adjustflag = str(cfg.get("adjustflag", "qfq"))

    # Baostock 全局登录（会话复用）
    lg = bs.login()
    if lg.error_code != "0":
        logger.error("Baostock 登录失败：%s", lg.error_msg)
        sys.exit(1)
    logger.info("Baostock 登录成功")

    # 日期解析
    raw_start = str(cfg.get("start", "20190101"))
    raw_end = str(cfg.get("end", "today"))
    start = dt.date.today().strftime("%Y%m%d") if raw_start.lower() == "today" else raw_start
    end = dt.date.today().strftime("%Y%m%d") if raw_end.lower() == "today" else raw_end

    out_dir = _resolve_cfg_path(cfg.get("out", "./data"))
    out_dir.mkdir(parents=True, exist_ok=True)

    # 读取股票池
    stocklist_path = _resolve_cfg_path(cfg.get("stocklist", "./pipeline/stocklist.csv"))
    exclude_boards: Set[str] = set(cfg.get("exclude_boards") or [])
    codes = load_codes_from_stocklist(stocklist_path, exclude_boards)

    if not codes:
        logger.error("stocklist 为空或被过滤后无代码，请检查。")
        bs.logout()
        sys.exit(1)

    logger.info(
        "开始抓取 %d 支股票 | 数据源:Baostock | 复权:%s | "
        "间隔:%.1f秒 | 日期:%s → %s | 排除:%s",
        len(codes), adjustflag, request_delay, start, end,
        ",".join(sorted(exclude_boards)) or "无",
    )

    workers = int(cfg.get("workers", 4))
    success_count = 0
    fail_count = 0

    def _fetch_with_delay(code: str) -> bool:
        nonlocal success_count, fail_count
        time.sleep(random.uniform(request_delay * 0.8, request_delay * 1.2))
        ok = fetch_one(code, start, end, out_dir, adjustflag, request_delay)
        if ok:
            success_count += 1
        else:
            fail_count += 1
        return ok

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_fetch_with_delay, code): code for code in codes}
            for f in tqdm(as_completed(futures), total=len(futures), desc="下载进度"):
                pass
    finally:
        bs.logout()

    logger.info("全部任务完成 | 成功:%d | 失败:%d | 数据已保存至 %s",
                success_count, fail_count, out_dir.resolve())


if __name__ == "__main__":
    main()
