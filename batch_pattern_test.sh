#!/bin/bash
# 批量测试过去一个月的模式匹配

DATES=(
  '2026-03-19' '2026-03-20' '2026-03-23' '2026-03-24' '2026-03-25'
  '2026-03-26' '2026-03-27' '2026-03-30' '2026-03-31' '2026-04-01'
  '2026-04-02' '2026-04-03' '2026-04-07' '2026-04-08' '2026-04-09'
  '2026-04-10' '2026-04-13' '2026-04-14' '2026-04-15' '2026-04-16' '2026-04-17'
)

echo "即将处理 ${#DATES[@]} 个交易日..."
echo "================================"

for DATE in "${DATES[@]}"; do
  echo "[$(date '+%H:%M:%S')] 处理 $DATE ..."

  # 运行preselect
  python3 -m pipeline.cli preselect --date "$DATE" --end-date "$DATE" 2>&1 | grep -E "(INFO|选出)"

  # 运行patternMatcher
  python3 -m similarity.patternMatcher --threshold 0.5 2>&1 | grep -E "(INFO|找到|★★★|★★|★|未找到)"

  echo "---"
done

echo "全部完成！"