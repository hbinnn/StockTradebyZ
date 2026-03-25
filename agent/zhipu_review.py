"""
zhipu_review.py
~~~~~~~~~~~~~~~~
使用智谱 AI GLM-4V-Flash 对候选股票进行图表分析评分。
继承自 BaseReviewer 基础架构。

用法：
    python agent/zhipu_review.py
    python agent/zhipu_review.py --config config/gemini_review.yaml

配置：
    默认读取 config/gemini_review.yaml。

环境变量：
    ZHIPU_API_KEY  —— 智谱 AI API Key（必填）

输出：
    ./data/review/{pick_date}/{code}.json   每支股票的评分 JSON
    ./data/review/{pick_date}/suggestion.json  汇总推荐建议
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from base_reviewer import BaseReviewer

# ────────────────────────────────────────────────
# 配置加载
# ────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _ROOT / "config" / "gemini_review.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    # 路径参数（相对路径默认基于项目根目录）
    "candidates": "data/candidates/candidates_latest.json",
    "kline_dir": "data/kline",
    "output_dir": "data/review",
    "prompt_path": "agent/prompt.md",
    # 智谱 AI 模型参数
    "model": "glm-4v-flash",      # 可选 glm-4v（标准版）
    "request_delay": 3,
    "skip_existing": False,
    "suggest_min_score": 4.0,
}


def _resolve_cfg_path(path_like: Union[str, Path], base_dir: Path = _ROOT) -> Path:
    p = Path(path_like)
    return p if p.is_absolute() else (base_dir / p)


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    cfg_path = config_path or _DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"找不到配置文件：{cfg_path}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg: Dict[str, Any] = {**DEFAULT_CONFIG, **raw}

    # BaseReviewer 依赖这些路径字段为 Path 对象
    cfg["candidates"] = _resolve_cfg_path(cfg["candidates"])
    cfg["kline_dir"] = _resolve_cfg_path(cfg["kline_dir"])
    cfg["output_dir"] = _resolve_cfg_path(cfg["output_dir"])
    cfg["prompt_path"] = _resolve_cfg_path(cfg["prompt_path"])

    return cfg


class ZhipuReviewer(BaseReviewer):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        api_key = os.environ.get("ZHIPU_API_KEY", "")
        if not api_key:
            print("[ERROR] 未找到环境变量 ZHIPU_API_KEY，请先设置后重试。", file=sys.stderr)
            sys.exit(1)

        try:
            from zhipuai import ZhipuAI
        except ImportError:
            print("[ERROR] 请先安装智谱 SDK：pip install zhipuai", file=sys.stderr)
            sys.exit(1)

        self.client = ZhipuAI(api_key=api_key)

    @staticmethod
    def image_to_base64(path: Path) -> tuple:
        """将图片文件转为 base64，返回 (data_url, suffix)。"""
        suffix = path.suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
        mime_type = mime_map.get(suffix, "image/jpeg")
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("utf-8")
        return f"data:{mime_type};base64,{b64}", suffix.lstrip(".")

    def review_stock(self, code: str, day_chart: Path, prompt: str) -> Dict[str, Any]:
        """
        调用智谱 AI GLM-4V-Flash API，对单支股票进行图表分析，返回解析后的 JSON 结果。
        """
        image_url, _ = self.image_to_base64(day_chart)

        user_content: List[Dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {"url": image_url}
            },
            {
                "type": "text",
                "text": f"股票代码：{code}\n\n以下是该股票的日线图，请按照系统提示中的框架进行分析，并严格按照要求输出 JSON。"
            }
        ]

        response = self.client.chat.completions.create(
            model=self.config.get("model", "glm-4v-flash"),
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.2,
        )

        response_text: str = response.choices[0].message.content
        if not response_text:
            raise RuntimeError(f"智谱返回空响应，无法解析 JSON（code={code}）")

        result = self.extract_json(response_text)
        result["code"] = code  # 附加股票代码便于追溯
        return result


def main():
    parser = argparse.ArgumentParser(description="智谱 AI 图表复评")
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_CONFIG_PATH),
        help="配置文件路径（默认 config/gemini_review.yaml）",
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))
    reviewer = ZhipuReviewer(config)
    reviewer.run()


if __name__ == "__main__":
    main()
