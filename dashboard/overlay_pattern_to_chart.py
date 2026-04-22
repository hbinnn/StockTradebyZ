"""
dashboard/overlay_pattern_to_chart.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
将完美图形相似度标注叠加到K线图上。

用法：
    python dashboard/overlay_pattern_to_chart.py
    python dashboard/overlay_pattern_to_chart.py --date 2026-04-17

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

# 中文字体路径（macOS + Windows）
FONT_PATHS = [
    "C:/Windows/Fonts/simhei.ttf",      # Windows 黑体
    "C:/Windows/Fonts/simsun.ttc",      # Windows 宋体
    "C:/Windows/Fonts/microsoftyahei.ttf",  # Windows 雅黑
    "/System/Library/Fonts/PingFang.ttc",  # macOS 苹方
    "/System/Library/Fonts/STHeiti Light.ttc",  # macOS 华文黑体
    "/System/Library/Fonts/Hiragino Sans GB.ttc",  # macOS 冬青黑体
]


def get_font(size: int) -> ImageFont.FreeTypeFont:
    """获取支持中文的字体"""
    for font_path in FONT_PATHS:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def load_pattern_results(pattern_dir: Path) -> dict:
    """加载完美图形匹配结果，返回 {code: [matches]} 字典"""
    # 查找最新的匹配结果文件
    if pattern_dir.is_file():
        result_file = pattern_dir
    else:
        json_files = list(pattern_dir.glob("matched_*.json"))
        if not json_files:
            raise FileNotFoundError(f"找不到匹配结果文件：{pattern_dir}")
        result_file = sorted(json_files, key=lambda x: x.name)[-1]

    with open(result_file, encoding="utf-8") as f:
        data = json.load(f)

    return data.get("results", {})


def overlay_pattern(
    image_path: Path,
    matches: list,
    output_path: Path = None,
    panel_height: int = 150,
) -> None:
    """
    在K线图下方扩展一片区域显示完美图形匹配信息。

    Args:
        image_path: K线图路径
        matches: 匹配结果列表
            [{
                "strategy": "b1",
                "case_code": "600026",
                "case_date": "2025-03-15",
                "case_description": "底部放量突破",
                "similarity": 0.85,
                "stars": "★★★"
            }, ...]
        output_path: 输出路径
        panel_height: 扩展区域高度
    """
    if not matches:
        return  # 无匹配，不叠加

    img = Image.open(image_path)
    orig_width, orig_height = img.size

    # 创建新图片：在下方扩展 panel_height 像素
    new_height = orig_height + panel_height
    new_img = Image.new("RGB", (orig_width, new_height), color=(255, 255, 255))
    new_img.paste(img, (0, 0))

    draw = ImageDraw.Draw(new_img)

    # 字体大小
    font_size = max(18, orig_width // 60)
    font = get_font(font_size)
    small_font = get_font(max(14, orig_width // 75))

    # 绘制分隔线
    draw.line([(0, orig_height), (orig_width, orig_height)], fill=(200, 200, 200), width=2)

    # 面板内容
    y_start = orig_height + 15
    line_height = font_size + 15

    # 标题
    draw.text((20, y_start), "完美图形匹配", fill=(50, 50, 50), font=font)
    y_start += line_height

    # 显示前3个最相似的案例
    for i, m in enumerate(matches[:3]):
        stars = m.get("stars", "")
        case_code = m.get("case_code", "")
        case_date = m.get("case_date", "")
        case_desc = m.get("case_description", "")
        similarity = m.get("similarity", 0)

        # 星级颜色：★★★金色，★★橙色，★灰色
        if stars == "★★★":
            star_color = (218, 165, 32)  # 金色
        elif stars == "★★":
            star_color = (255, 140, 0)  # 橙色
        else:
            star_color = (128, 128, 128)  # 灰色

        text = f"{stars} 完美案例: {case_code} ({case_date}) - {case_desc}"
        draw.text((20, y_start), text, fill=star_color, font=small_font)
        y_start += line_height

        text2 = f"    相似度: {similarity:.3f}"
        draw.text((20, y_start), text2, fill=(100, 100, 100), font=small_font)
        y_start += line_height

    # 保存
    out_path = output_path or image_path
    new_img.save(out_path, quality=95)


def main():
    parser = argparse.ArgumentParser(description="将完美图形匹配标注叠加到K线图")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="选股日期（默认从candidates_latest.json读取）",
    )
    parser.add_argument(
        "--pattern-dir",
        type=Path,
        default=ROOT / "data" / "pattern_matched",
        help="匹配结果目录",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.0,
        help="最低相似度阈值（默认0，展示所有匹配）",
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

    kline_dir = ROOT / "data" / "kline" / pick_date
    pattern_dir = args.pattern_dir

    if not kline_dir.exists():
        print(f"[ERROR] 找不到K线图目录：{kline_dir}")
        sys.exit(1)

    print(f"[INFO] K线图目录：{kline_dir}")
    print(f"[INFO] 匹配结果目录：{pattern_dir}")

    # 加载匹配结果
    try:
        all_matches = load_pattern_results(pattern_dir)
        print(f"[INFO] 加载匹配结果：{len(all_matches)} 只股票")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    # 遍历并叠加
    success = 0
    skipped = 0
    for code, matches in all_matches.items():
        # 过滤低于阈值的匹配
        filtered = [m for m in matches if m.get("similarity", 0) >= args.threshold]
        if not filtered:
            skipped += 1
            continue

        chart_file = kline_dir / f"{code}_day.jpg"
        if not chart_file.exists():
            print(f"[SKIP] {code} — 找不到K线图：{chart_file}")
            skipped += 1
            continue

        try:
            overlay_pattern(chart_file, filtered)
            best = filtered[0]
            print(f"[OK] {code} — {best.get('stars', '')} {best.get('case_code', '')} similarity={best.get('similarity', 0):.3f}")
            success += 1
        except Exception as e:
            print(f"[ERROR] {code} — 叠加失败：{e}")

    print(f"\n[完成] 成功：{success}，跳过：{skipped}")
    print(f"[目录] {kline_dir}")


if __name__ == "__main__":
    main()
