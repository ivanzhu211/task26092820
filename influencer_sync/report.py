"""导入报告模型。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class RowOutcome(str, enum.Enum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"
    REJECTED = "rejected"


@dataclass
class FieldConflict:
    field: str
    baseline: object = None
    file_value: object = None
    system_value: object = None


@dataclass
class RowReport:
    file_line: int
    outcome: RowOutcome | None = None
    influencer_id: int | None = None
    applied_fields: list[str] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass
class ImportReport:
    rows: list[RowReport] = field(default_factory=list)
