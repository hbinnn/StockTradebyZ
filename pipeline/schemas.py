"""
pipeline/schemas.py
候选股票的数据结构定义（纯 dataclass，无第三方依赖）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Candidate:
    """单只候选股票的结构化信息。"""
    code: str                          # 股票代码，如 "600519"
    date: str                          # 选股日期，ISO 格式 "YYYY-MM-DD"
    strategy: str                      # 来源策略，如 "b1" / "brick"
    close: float                       # 选股日收盘价
    turnover_n: float                  # 滚动成交额（流动性代理）
    extra: Dict[str, Any] = field(default_factory=dict)  # 策略自定义字段（如 brick_growth）

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not d["extra"]:
            d.pop("extra")
        # 展平 extra 到顶层（向后兼容）
        for k, v in d.get("extra", {}).items():
            if v is not None:
                d[k] = v
        return d


@dataclass
class CandidateRun:
    """一次完整初选运行的结果，写入 candidates_YYYY-MM-DD.json。"""
    run_date: str                          # 运行日期（ISO）
    pick_date: str                         # 选股基准日期（ISO）
    candidates: List[Candidate] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)   # 参数快照等

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_date": self.run_date,
            "pick_date": self.pick_date,
            "candidates": [c.to_dict() for c in self.candidates],
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CandidateRun":
        candidates = [
            Candidate(**{k: v for k, v in c.items() if k in Candidate.__dataclass_fields__})
            for c in d.get("candidates", [])
        ]
        return cls(
            run_date=d["run_date"],
            pick_date=d["pick_date"],
            candidates=candidates,
            meta=d.get("meta", {}),
        )
