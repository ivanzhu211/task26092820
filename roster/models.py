"""领域模型：枚举、常量、数据类。仅类型定义，无实现逻辑。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Platform(str, Enum):
    INSTAGRAM = "Instagram"
    TIKTOK = "TikTok"
    YOUTUBE = "YouTube"
    TWITTER = "Twitter"


class Tier(str, Enum):
    NANO = "Nano"
    MICRO = "Micro"
    MID_MICRO = "Mid-Micro"


class Status(str, Enum):
    PROSPECT = "Prospect"
    OUTREACH = "Outreach"
    ACTIVE = "Active Ambassador"
    PAUSED = "Paused"
    CHURNED = "Churned"


# 粉丝数 -> 层级
FOLLOWER_MIN = 1000
FOLLOWER_MAX = 50000

# 标签约束
TAG_MAX_PER_INFLUENCER = 10
TAG_DICT_MAX = 50

# 账号约束
ACCOUNT_MAX_LEN = 30

# 单次导入上限
IMPORT_MAX_ROWS = 1000

# 允许的状态迁移（单步）：
# Prospect→Outreach→Active Ambassador；Active Ambassador↔Paused；
# 任意状态→Churned；Churned→Prospect。
STATUS_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.PROSPECT: frozenset({Status.OUTREACH, Status.CHURNED}),
    Status.OUTREACH: frozenset({Status.ACTIVE, Status.CHURNED}),
    Status.ACTIVE: frozenset({Status.PAUSED, Status.CHURNED}),
    Status.PAUSED: frozenset({Status.ACTIVE, Status.CHURNED}),
    Status.CHURNED: frozenset({Status.PROSPECT}),
}

# 七个可编辑字段
EDITABLE_FIELDS: tuple[str, ...] = (
    "account",
    "platform",
    "followers",
    "tags",
    "city",
    "state",
    "status",
)

# 允许用 null/空串清空的字段
CLEARABLE_FIELDS: frozenset[str] = frozenset({"city", "state", "tags"})


@dataclass
class Influencer:
    id: int
    account: str
    platform: Platform
    followers: int
    tier: Tier
    tags: list[str]
    city: Optional[str]
    state: Optional[str]
    status: Status


@dataclass
class InfluencerPatch:
    """单条修改/新建时的字段集；None 表示不修改（新建时按必填规则解释）。"""
    account: Optional[str] = None
    platform: Optional[Platform] = None
    followers: Optional[int] = None
    tags: Optional[list[str]] = None
    city: Optional[str] = None
    state: Optional[str] = None
    status: Optional[Status] = None
