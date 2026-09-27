"""导入报告结构。仅类型定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class RowResult(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    NO_CHANGE = "no_change"
    CONFLICT = "conflict"
    REJECTED = "rejected"


@dataclass
class FieldConflict:
    field: str
    exported: Any
    file: Any
    system: Any


@dataclass
class RowReport:
    row: int                      # 文件中的行号（从 1 开始）
    result: RowResult
    influencer_id: Optional[int] = None
    adopted_fields: list[str] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)
    reject_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # 例如“层级改动不生效”


@dataclass
class ImportReport:
    rows: list[RowReport] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        result = {r.value: 0 for r in RowResult}
        for row in self.rows:
            result[row.result.value] += 1
        return result
