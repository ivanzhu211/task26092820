"""字段级与整记录级校验（纯函数，便于单条修改与导入共用）。"""

from __future__ import annotations

import re

from .models import Platform, RelationshipStatus, tier_for_followers

_ACCOUNT_BODY_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

_ALLOWED_TRANSITIONS: dict[RelationshipStatus, set[RelationshipStatus]] = {
    RelationshipStatus.PROSPECT: {
        RelationshipStatus.OUTREACH,
        RelationshipStatus.CHURNED,
    },
    RelationshipStatus.OUTREACH: {
        RelationshipStatus.ACTIVE_AMBASSADOR,
        RelationshipStatus.CHURNED,
    },
    RelationshipStatus.ACTIVE_AMBASSADOR: {
        RelationshipStatus.PAUSED,
        RelationshipStatus.CHURNED,
    },
    RelationshipStatus.PAUSED: {
        RelationshipStatus.ACTIVE_AMBASSADOR,
        RelationshipStatus.CHURNED,
    },
    RelationshipStatus.CHURNED: {RelationshipStatus.PROSPECT},
}


class TagDictionary:
    """标签字典：最多 50 个，不分大小写匹配，保留初始化时的写法。"""

    def __init__(self, tags):
        canonical: dict[str, str] = {}
        for tag in tags:
            if not isinstance(tag, str) or not tag.strip():
                raise ValueError("标签必须是非空字符串")
            key = tag.strip().lower()
            if key in canonical:
                raise ValueError(f"标签字典存在重复（不分大小写）: {tag}")
            canonical[key] = tag.strip()
        if len(canonical) > 50:
            raise ValueError("标签字典最多 50 个")
        self._by_key = canonical

    @property
    def all(self) -> list[str]:
        return list(self._by_key.values())

    def lookup(self, raw: str) -> str | None:
        """返回字典中的标准写法，不在字典里返回 None。"""
        if not isinstance(raw, str):
            return None
        return self._by_key.get(raw.strip().lower())


def normalize_account(raw) -> str:
    """账号比较键：去掉开头的一个 @ 后小写。"""
    body = raw[1:] if isinstance(raw, str) and raw.startswith("@") else raw
    return body.lower()


def validate_account(raw) -> tuple[str | None, list[str]]:
    """返回去掉 @ 的账号原文（保留大小写）与错误列表。"""
    if not isinstance(raw, str):
        return None, ["账号必须是字符串"]
    body = raw[1:] if raw.startswith("@") else raw
    if not _ACCOUNT_BODY_RE.fullmatch(body):
        return None, ["账号须为 1 到 30 位字母、数字、点或下划线（可带前导 @）"]
    return body, []


def validate_platform(raw) -> tuple[Platform | None, list[str]]:
    if not isinstance(raw, str):
        return None, ["平台必须是字符串"]
    try:
        return Platform(raw), []
    except ValueError:
        allowed = ", ".join(p.value for p in Platform)
        return None, [f"平台必须是以下之一: {allowed}"]


def validate_followers(raw) -> tuple[int | None, list[str]]:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, ["粉丝数必须是整数"]
    if tier_for_followers(raw) is None:
        return None, ["粉丝数超出可接受范围（1000 到 50000）"]
    return raw, []


def validate_text(raw, field_name: str) -> tuple[str | None, list[str]]:
    """城市/州：None 或空串表示清空；非空字符串原样保留。"""
    if raw is None:
        return None, []
    if not isinstance(raw, str):
        return None, [f"{field_name}必须是字符串或 null"]
    stripped = raw.strip()
    return (stripped if stripped else None), []


def validate_tags(raw, dictionary: TagDictionary) -> tuple[list[str] | None, list[str]]:
    """标签：null / "" / [] 表示清空；返回字典标准写法的去重列表。"""
    if raw is None or raw == "":
        return [], []
    if not isinstance(raw, list):
        return None, ["标签必须是数组、null 或空串"]
    errors: list[str] = []
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            errors.append("标签元素必须是非空字符串")
            continue
        canonical = dictionary.lookup(item)
        if canonical is None:
            errors.append(f"标签不在字典中: {item}")
            continue
        key = canonical.lower()
        if key not in seen:
            seen.add(key)
            result.append(canonical)
    if errors:
        return None, errors
    return result, []


def validate_status_value(raw) -> tuple[RelationshipStatus | None, list[str]]:
    """校验状态取值本身是否合法（不校验流转）。"""
    if not isinstance(raw, str) or raw == "":
        return None, ["关系状态必须是非空字符串"]
    try:
        return RelationshipStatus(raw), []
    except ValueError:
        allowed = ", ".join(s.value for s in RelationshipStatus)
        return None, [f"关系状态必须是以下之一: {allowed}"]


def validate_status_transition(
    old: RelationshipStatus, new: RelationshipStatus
) -> bool:
    """每次只能走一步；相同状态视为无变化，合法。"""
    if old == new:
        return True
    return new in _ALLOWED_TRANSITIONS.get(old, set())
