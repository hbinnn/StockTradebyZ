"""
zhipu_review.py
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

"""

~~~~~~~~~~~~~~~
使用智谱 GLM-4.6V 对候选股票进行图表分析评分。
继承自 BaseReviewer 基础架构。

用法：
    python agent/zhipu_review.py
    python agent/zhipu_review.py --config config/zhipu_review.yaml

配置：
    默认读取 config/zhipu_review.yaml。

环境变量：
    ZHIPU_API_KEY  —— 智谱 AI API Key（必填）

输出：
    ./data/review/{pick_date}/{code}.json   每支股票的评分 JSON
    ./data/review/{pick_date}/suggestion.json  汇总推荐建议
"""

import argparse
import base64
import os
import sys
from pathlib import Path
from typing import Any, Dict, Union

import yaml

from base_reviewer import BaseReviewer

# ────────────────────────────────────────────────
# 配置加载
# ────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _ROOT / "config" / "zhipu_review.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    # 路径参数（相对路径默认基于项目根目录）
    "candidates": "data/candidates/candidates_latest.json",
    "kline_dir": "data/kline",
    "output_dir": "data/review",
    "prompt_path": "agent/prompt.md",
    # 智谱模型参数
    "model": "glm-4v",
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


class ZhipuReviewer(BaseReviewer):
    """智谱 GLM-4.6V 图表评审器。"""

    def __init__(self, config):
        super().__init__(config)

        api_key = os.environ.get("ZHIPU_API_KEY", "")
        if not api_key:
            print("[ERROR] 未找到环境变量 ZHIPU_API_KEY，请先设置后重试。", file=sys.stderr)
            sys.exit(1)

        self.api_key = api_key
        self.model = config.get("model", "glm-4v")

    @staticmethod
    def image_to_base64(path: Path) -> str:
        """将图片文件转为 base64 字符串。"""
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def review_stock(self, code: str, day_chart: Path, prompt: str) -> dict:
        """
        调用智谱 GLM-4.6V API，对单支股票进行图表分析，返回解析后的 JSON 结果。
        """
        import urllib.request
        import json as json_lib

        url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
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

        req = urllib.request.Request(
            url,
            data=json_lib.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            response_body = json_lib.loads(resp.read().decode("utf-8"))

        # 提取响应文本
        if "choices" not in response_body or not response_body["choices"]:
            raise RuntimeError(f"智谱 API 返回格式异常（code={code}）：{response_body}")

        response_text = response_body["choices"][0]["message"]["content"]
        if not response_text:
            raise RuntimeError(f"智谱 API 返回空响应，无法解析 JSON（code={code}）")

        result = self.extract_json(response_text)
        result["code"] = code  # 附加股票代码便于追溯
        return result


def main():
    parser = argparse.ArgumentParser(description="智谱 GLM-4.6V 图表复评")
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_CONFIG_PATH),
        help="配置文件路径（默认 config/zhipu_review.yaml）",
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))
    reviewer = ZhipuReviewer(config)
    reviewer.run()


if __name__ == "__main__":
    main()
