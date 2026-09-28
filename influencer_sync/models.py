"""核心领域模型：平台、关系状态、层级与达人数据类。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Platform(str, enum.Enum):
    INSTAGRAM = "Instagram"
    TIKTOK = "TikTok"
    YOUTUBE = "YouTube"
    TWITTER = "Twitter"


class RelationshipStatus(str, enum.Enum):
    PROSPECT = "Prospect"
    OUTREACH = "Outreach"
    ACTIVE_AMBASSADOR = "Active Ambassador"
    PAUSED = "Paused"
    CHURNED = "Churned"


class Tier(str, enum.Enum):
    NANO = "Nano"
    MICRO = "Micro"
    MID_MICRO = "Mid-Micro"


# 七个可改字段（不含只读的层级）
EDITABLE_FIELDS = (
    "account",
    "platform",
    "followers",
    "tags",
    "city",
    "state",
    "relationship_status",
)


_TIER_RANGES = (
    (1000, 4999, Tier.NANO),
    (5000, 24999, Tier.MICRO),
    (25000, 50000, Tier.MID_MICRO),
)


def tier_for_followers(followers: int) -> Tier | None:
    """按粉丝数返回层级；范围外返回 None（上层据此拒绝）。"""
    for low, high, tier in _TIER_RANGES:
        if low <= followers <= high:
            return tier
    return None


@dataclass
class Influencer:
    """系统内一位达人的完整记录。"""

    id: int
    account: str
    platform: Platform
    followers: int
    tags: list[str] = field(default_factory=list)
    city: str | None = None
    state: str | None = None
    relationship_status: RelationshipStatus = RelationshipStatus.PROSPECT
    tier: Tier | None = None
    version: int = 1
