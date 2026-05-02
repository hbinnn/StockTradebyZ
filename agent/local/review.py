"""
local_review.py
~~~~~~~~~~~~~~~
使用本地 LM Studio 部署的视觉大模型（如 Qwen3-VL）对候选股票进行图表分析评分。
继承自 BaseReviewer 基础架构。

用法：
    python agent/local_review.py
    python agent/local_review.py --config config/local_review.yaml

配置：
    默认读取 config/local_review.yaml。

环境变量：
    LOCAL_API_URL     —— 本地 API 地址（默认 http://localhost:1234）
    LOCAL_MODEL_NAME  —— 模型名称（默认 qwen/qwen3-vl-8b）

输出：
    ./data/review/{pick_date}/{code}.json   每支股票的评分 JSON
    ./data/review/{pick_date}/suggestion.json  汇总推荐建议
"""

import argparse
import base64
import io
import os
import sys
import time
import urllib.request
import json as json_lib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from typing import Any, Dict, Union

import yaml
from PIL import Image

from base_reviewer import BaseReviewer

# ────────────────────────────────────────────────
# 错误模式定义
# ────────────────────────────────────────────────
CONNECTION_CLOSED_PATTERNS = (
    "Remote end closed connection",
    "Connection closed",
    "Connection reset",
    "Connection aborted",
    "timed out",
    "timeout",
    "10054",
    "104",
)

# ────────────────────────────────────────────────
# 配置加载
# ────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG_PATH = _ROOT / "agent" / "local" / "config.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    # 路径参数（相对路径默认基于项目根目录）
    "candidates": "data/candidates/candidates_latest.json",
    "kline_dir": "data/kline",
    "output_dir": "data/review",
    "strategy_prompts": {"b1": "strategies/b1/prompt.md", "brick": "strategies/brick/prompt.md"},
    # LM Studio 本地模型参数
    "api_url": "http://192.168.1.107:1234/v1/chat/completions",
    "model": "qwen/qwen3-vl-8b",
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
    for s in cfg.get("strategy_prompts", {}): cfg["strategy_prompts"][s] = str(_resolve_cfg_path(cfg["strategy_prompts"][s]))

    return cfg


class LocalReviewer(BaseReviewer):
    """本地 LM Studio 视觉大模型图表评审器。"""

    def __init__(self, config):
        super().__init__(config)

        self.api_url = config.get("api_url", DEFAULT_CONFIG["api_url"])
        self.model = config.get("model", DEFAULT_CONFIG["model"])

        # LM Studio 默认不需要 API Key
        if not self.api_url:
            print("[ERROR] 未配置 LOCAL_API_URL，请检查配置文件。", file=sys.stderr)
            sys.exit(1)

    @staticmethod
    def image_to_base64(path: Path) -> str:
        """
        将图片文件转为 base64 字符串。
        使用原图以保留完整信息。
        """
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _is_retryable_error(self, exc: Exception) -> bool:
        """判断是否为可重试的错误"""
        msg = str(exc)
        return any(pat in msg for pat in CONNECTION_CLOSED_PATTERNS)

    def review_stock(self, code: str, day_chart: Path, prompt: str) -> dict:
        """
        调用本地 LM Studio API，对单支股票进行图表分析，返回解析后的 JSON 结果。
        支持超时和连接错误的自动重试。
        """
        headers = {
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
            "max_tokens": 2048,
        }

        # 最大重试次数和超时时间
        max_retries = 5
        timeout = 300  # 5分钟超时

        req = urllib.request.Request(
            self.api_url,
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
                    raise RuntimeError(f"本地 API 返回格式异常（code={code}）：{response_body}")

                response_text = response_body["choices"][0]["message"]["content"]
                if not response_text:
                    raise RuntimeError(f"本地 API 返回空响应，无法解析 JSON（code={code}）")

                result = self.extract_json(response_text)
                result["code"] = code  # 附加股票代码便于追溯
                result["elapsed_seconds"] = round(elapsed, 2)  # 记录耗时
                return result

            except Exception as e:
                elapsed = time.time() - start_time
                if self._is_retryable_error(e):
                    last_error = e
                    if attempt < max_retries:
                        wait_time = 10 * attempt
                        print(f"[WARN] {code} 请求失败（{str(e)[:50]}），{wait_time}秒后重试 ({attempt}/{max_retries})...")
                        time.sleep(wait_time)
                    else:
                        raise RuntimeError(f"{code} 重试{max_retries}次后仍失败：{e}") from e
                else:
                    raise

        raise last_error


def main():
    parser = argparse.ArgumentParser(description="本地 LM Studio 视觉大模型图表复评")
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_CONFIG_PATH),
        help="配置文件路径（默认 config/local_review.yaml）",
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))
    reviewer = LocalReviewer(config)
    reviewer.run()


if __name__ == "__main__":
    main()
