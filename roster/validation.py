"""字段值校验。"""
from __future__ import annotations

import re
from typing import Optional

from .models import (
    ACCOUNT_MAX_LEN,
    FOLLOWER_MAX,
    FOLLOWER_MIN,
    STATUS_TRANSITIONS,
    Platform,
    Status,
    Tier,
)

_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9._]{1,%d}$" % ACCOUNT_MAX_LEN)
_TEXT_MAX_LEN = 100


def normalize_account(account: str) -> str:
    """去掉开头 '@' 并转小写，用于比较与唯一性。"""
    return account.lstrip("@").lower()


def validate_account(account: object) -> Optional[str]:
    """合法返回 None，否则返回错误描述。允许一个可选的开头 '@'。"""
    if not isinstance(account, str):
        return "账号必须是字符串"
    stripped = account.lstrip("@")
    if not _ACCOUNT_RE.match(stripped):
        return "账号须为1到%d位字母、数字、点或下划线（可带一个开头@）" % ACCOUNT_MAX_LEN
    return None


def validate_followers(followers: object) -> Optional[str]:
    if isinstance(followers, bool) or not isinstance(followers, int):
        return "粉丝数必须是整数"
    if not (FOLLOWER_MIN <= followers <= FOLLOWER_MAX):
        return "粉丝数须在%d到%d之间" % (FOLLOWER_MIN, FOLLOWER_MAX)
    return None


def tier_for_followers(followers: int) -> Tier:
    if followers < 5000:
        return Tier.NANO
    if followers < 25000:
        return Tier.MICRO
    return Tier.MID_MICRO


def validate_platform(platform: object) -> Optional[str]:
    if not isinstance(platform, str) or platform not in Platform._value2member_map_:
        return "平台必须是 Instagram/TikTok/YouTube/Twitter 之一"
    return None


def validate_status(status: object) -> Optional[str]:
    if not isinstance(status, str) or status not in Status._value2member_map_:
        return "关系状态不合法"
    return None


def validate_tags(tags: object, dictionary: frozenset[str]) -> list[str]:
    """返回错误列表（空列表表示合法）。dictionary 为小写集合。"""
    if not isinstance(tags, list):
        return ["标签必须是数组"]
    errors: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            errors.append("标签必须是字符串")
        elif tag.lower() not in dictionary:
            errors.append("标签不在字典里: %s" % tag)
    return errors


def validate_text_field(value: object, name: str) -> Optional[str]:
    """城市/州：字符串或 None。"""
    if value is None:
        return None
    if not isinstance(value, str):
        return "%s必须是字符串" % name
    if len(value) > _TEXT_MAX_LEN:
        return "%s过长" % name
    return None


def is_valid_transition(old: Status, new: Status) -> bool:
    return new in STATUS_TRANSITIONS[old]
