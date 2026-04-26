"""
siliconflow_review.py
~~~~~~~~~~~~~~~~~~~~~
使用 SiliconFlow Kimi-K2.6 对候选股票进行图表分析评分。
继承自 BaseReviewer 基础架构。

用法：
    python agent/siliconflow_review.py
    python agent/siliconflow_review.py --config config/siliconflow_review.yaml

配置：
    默认读取 config/siliconflow_review.yaml。

环境变量：
    SILICONFLOW_API_KEY  —— SiliconFlow API Key（必填）

输出：
    ./data/review/{pick_date}/{code}.json   每支股票的评分 JSON
    ./data/review/{pick_date}/suggestion.json  汇总推荐建议
"""

import argparse
import base64
import os
import sys
import time
import urllib.request
import json as json_lib
from pathlib import Path
from typing import Any, Dict, Union

import yaml

from base_reviewer import BaseReviewer

# ────────────────────────────────────────────────
# 错误模式定义
# ────────────────────────────────────────────────
CONNECTION_CLOSED_PATTERNS = (
    "Remote end closed connection",
    "Connection closed",
    "Connection reset",
    "Connection aborted",
    "10054",  # WSAECONNRESET
    "104",     # ECONNRESET (Linux)
)

# ────────────────────────────────────────────────
# 配置加载
# ────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _ROOT / "config" / "siliconflow_review.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    # 路径参数（相对路径默认基于项目根目录）
    "candidates": "data/candidates/candidates_latest.json",
    "kline_dir": "data/kline",
    "output_dir": "data/review",
    "prompt_path": "agent/prompt.md",
    # SiliconFlow 模型参数
    "model": "Pro/moonshotai/Kimi-K2.6",
    "request_delay": 5,
    "skip_existing": False,
    "suggest_min_score": 4.0,
}


def _resolve_cfg_path(path_like: Union[str, Path], base_dir: Path = _ROOT) -> Path:
    p = Path(path_like)
    return p if p.is_absolute() else (base_dir / p)


def load_config(config_path: Union[Path, None] = None) -> Dict[str, Any]:
    cfg_path = config_path or _DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"找不到配置文件：{cfg_path}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = {**DEFAULT_CONFIG, **raw}

    # BaseReviewer 依赖这些路径字段为 Path 对象
    cfg["candidates"] = _resolve_cfg_path(cfg["candidates"])
    cfg["kline_dir"] = _resolve_cfg_path(cfg["kline_dir"])
    cfg["output_dir"] = _resolve_cfg_path(cfg["output_dir"])
    cfg["prompt_path"] = _resolve_cfg_path(cfg["prompt_path"])

    return cfg


class SiliconFlowReviewer(BaseReviewer):
    """SiliconFlow Kimi-K2.6 图表评审器。"""

    API_URL = "https://api.siliconflow.cn/v1/chat/completions"

    def __init__(self, config):
        super().__init__(config)

        api_key = os.environ.get("SILICONFLOW_API_KEY", "")
        if not api_key:
            print("[ERROR] 未找到环境变量 SILICONFLOW_API_KEY，请先设置后重试。", file=sys.stderr)
            sys.exit(1)

        self.api_key = api_key
        self.model = config.get("model", "Pro/moonshotai/Kimi-K2.6")

    @staticmethod
    def image_to_base64(path: Path) -> str:
        """将图片文件转为 base64 字符串。"""
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _is_connection_closed_error(self, exc: Exception) -> bool:
        """判断是否为连接关闭错误"""
        msg = str(exc)
        return any(pat in msg for pat in CONNECTION_CLOSED_PATTERNS)

    def review_stock(self, code: str, day_chart: Path, prompt: str) -> dict:
        """
        调用 SiliconFlow Kimi-K2.6 API，对单支股票进行图表分析，返回解析后的 JSON 结果。
        支持连接关闭错误的自动重试。
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # 构建用户消息：先传文本说明，再传图片
        image_base64 = self.image_to_base64(day_chart)

        user_content = [
            {
                "type": "text",
                "text": (
                    f"股票代码：{code}\n\n"
                    "以下是该股票的 **日线图**，请按照系统提示中的框架进行分析，"
                    "并严格按照要求输出 JSON。"
                ),
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{image_base64}"
                },
            },
        ]

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "temperature": 0.2,
        }

        # 最大重试次数和超时时间
        max_retries = 3
        timeout = 220  # 220秒超时（测试显示平均120秒，最慢146秒）

        req = urllib.request.Request(
            self.API_URL,
            data=json_lib.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        last_error = None
        for attempt in range(1, max_retries + 1):
            start_time = time.time()
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    response_body = json_lib.loads(resp.read().decode("utf-8"))

                elapsed = time.time() - start_time

                # 提取响应文本
                if "choices" not in response_body or not response_body["choices"]:
                    raise RuntimeError(f"SiliconFlow API 返回格式异常（code={code}）：{response_body}")

                response_text = response_body["choices"][0]["message"]["content"]
                if not response_text:
                    raise RuntimeError(f"SiliconFlow API 返回空响应，无法解析 JSON（code={code}）")

                result = self.extract_json(response_text)
                result["code"] = code  # 附加股票代码便于追溯
                result["elapsed_seconds"] = round(elapsed, 2)  # 记录耗时
                return result

            except Exception as e:
                elapsed = time.time() - start_time
                if self._is_connection_closed_error(e):
                    last_error = e
                    if attempt < max_retries:
                        wait_time = 5 * attempt  # 递增等待时间
                        print(f"[WARN] {code} 连接被关闭，{wait_time}秒后重试 ({attempt}/{max_retries})...")
                        time.sleep(wait_time)
                    else:
                        raise RuntimeError(f"{code} 重试{max_retries}次后仍失败：{e}") from e
                else:
                    raise

        raise last_error  # 理论上不会走到这里


def main():
    parser = argparse.ArgumentParser(description="SiliconFlow Kimi-K2.6 图表复评")
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_CONFIG_PATH),
        help="配置文件路径（默认 config/siliconflow_review.yaml）",
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))
    reviewer = SiliconFlowReviewer(config)
    reviewer.run()


if __name__ == "__main__":
    main()
