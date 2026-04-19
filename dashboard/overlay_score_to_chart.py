"""
dashboard/overlay_score_to_chart.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
将AI评分结果叠加到K线图上。

用法：
    python dashboard/overlay_score_to_chart.py
    python dashboard/overlay_score_to_chart.py --date 2026-04-17

依赖：
    Pillow
"""

import argparse
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent

# 中文字体路径（Windows常用）
FONT_PATHS = [
    "C:/Windows/Fonts/simhei.ttf",      # 黑体
    "C:/Windows/Fonts/simsun.ttc",      # 宋体
    "C:/Windows/Fonts/microsoftyahei.ttf",  # 微软雅黑
    "C:/Windows/Fonts/msyh.ttc",        # 微软雅黑
]


def get_font(size: int) -> ImageFont.FreeTypeFont:
    """获取支持中文的字体。"""
    for font_path in FONT_PATHS:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
    # 回退到默认字体（可能不支持中文）
    return ImageFont.load_default()


def load_review_results(review_dir: Path) -> dict:
    """加载评分结果，返回 {code: review_data} 字典。"""
    suggestion_file = review_dir / "suggestion.json"
    if not suggestion_file.exists():
        raise FileNotFoundError(f"找不到汇总文件：{suggestion_file}")

    with open(suggestion_file, encoding="utf-8") as f:
        suggestion = json.load(f)

    results = {}
    for r in suggestion.get("recommendations", []):
        code = r["code"]
        code_file = review_dir / f"{code}.json"
        if code_file.exists():
            with open(code_file, encoding="utf-8") as f:
                results[code] = json.load(f)

    return results


def overlay_score(
    image_path: Path,
    review_data: dict,
    output_path: Path = None,
    panel_height: int = 300,
) -> None:
    """在K线图下方扩展一片区域显示评分信息。"""
    img = Image.open(image_path)
    orig_width, orig_height = img.size

    # 评分信息
    score = review_data.get("total_score", 0)
    verdict = review_data.get("verdict", "UNKNOWN")
    signal_type = review_data.get("signal_type", "")
    comment = review_data.get("comment", "")

    # 颜色映射
    color_map = {
        "PASS": (40, 167, 69),     # 绿色
        "WATCH": (225, 126, 34),  # 橙色
        "REJECT": (220, 53, 69),  # 红色
        "UNKNOWN": (108, 117, 125),  # 灰色
    }
    text_color = color_map.get(verdict, (108, 117, 125))

    # 创建新图片：在下方扩展 panel_height 像素
    new_height = orig_height + panel_height
    new_img = Image.new("RGB", (orig_width, new_height), color=(255, 255, 255))
    new_img.paste(img, (0, 0))

    draw = ImageDraw.Draw(new_img)

    # 字体大小
    font_size = max(20, orig_width // 50)
    font = get_font(font_size)
    small_font = get_font(max(16, orig_width // 65))

    # 绘制分隔线
    draw.line([(0, orig_height), (orig_width, orig_height)], fill=(200, 200, 200), width=2)

    # 评分面板内容 - 左右分栏
    left_x = 30
    right_x = orig_width // 2 + 50
    y_start = orig_height + 20
    line_height = font_size + 20

    # 左列：第一行标题
    draw.text((left_x, y_start), f"评分详情", fill=(0, 0, 0), font=font)

    # 右列：总分 + 判定（竖向排列在右下角）
    draw.text((right_x, y_start), f"总分: {score}", fill=text_color, font=font)
    draw.text((right_x, y_start + line_height), f"判定: {verdict}", fill=text_color, font=font)

    # 左列：信号
    y_start += line_height
    draw.text((left_x, y_start), f"信号: {signal_type}", fill=(100, 100, 100), font=small_font)

    # 左列：注释
    if comment:
        y_start += line_height
        draw.text((left_x, y_start), f"简评: {comment}", fill=(80, 80, 80), font=small_font)

    # 保存
    out_path = output_path or image_path
    new_img.save(out_path, quality=95)


def main():
    parser = argparse.ArgumentParser(description="将评分叠加到K线图")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="选股日期（默认从candidates_latest.json读取）",
    )
    args = parser.parse_args()

    # 确定日期
    if args.date:
        pick_date = args.date
    else:
        candidates_file = ROOT / "data" / "candidates" / "candidates_latest.json"
        if candidates_file.exists():
            with open(candidates_file, encoding="utf-8") as f:
                pick_date = json.load(f).get("pick_date", "")
        if not pick_date:
            print("[ERROR] 无法确定pick_date，请通过--date指定")
            sys.exit(1)

    review_dir = ROOT / "data" / "review" / pick_date
    kline_dir = ROOT / "data" / "kline" / pick_date

    if not review_dir.exists():
        print(f"[ERROR] 找不到评分目录：{review_dir}")
        sys.exit(1)

    print(f"[INFO] 评分目录：{review_dir}")
    print(f"[INFO] K线图目录：{kline_dir}")

    # 加载评分结果
    results = load_review_results(review_dir)
    print(f"[INFO] 加载评分结果：{len(results)} 只股票")

    # 遍历并叠加
    success = 0
    skipped = 0
    for code, review_data in results.items():
        chart_file = kline_dir / f"{code}_day.jpg"
        if not chart_file.exists():
            print(f"[SKIP] {code} — 找不到K线图：{chart_file}")
            skipped += 1
            continue

        try:
            overlay_score(chart_file, review_data)
            print(f"[OK] {code} — 评分已叠加")
            success += 1
        except Exception as e:
            print(f"[ERROR] {code} — 叠加失败：{e}")

    print(f"\n[完成] 成功：{success}，跳过：{skipped}")
    print(f"[目录] {kline_dir}")


if __name__ == "__main__":
    main()
