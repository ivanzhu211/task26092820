"""达人名单导出/导回库（由上层达人关系管理服务调用，无接口层）。

用法::

    from influencer_sync import Store, TagDictionary
    from influencer_sync import export_influencers, import_influencers

    store = Store("roster.db", TagDictionary(["Beauty", "Travel"]))
    payload = export_influencers(store)            # dict，可 json.dumps
    report = import_influencers(store, payload)    # 逐行报告
"""

from __future__ import annotations

from .exceptions import (
    ImportFileError,
    InfluencerSyncError,
    NotFoundError,
    StorageError,
    ValidationError,
)
from .export import export_influencers
from .importer import import_influencers
from .models import (
    Influencer,
    Platform,
    RelationshipStatus,
    Tier,
    tier_for_followers,
)
from .report import FieldConflict, ImportReport, RowOutcome, RowReport
from .storage import Store
from .validation import TagDictionary

__all__ = [
    "Store",
    "TagDictionary",
    "Influencer",
    "Platform",
    "RelationshipStatus",
    "Tier",
    "tier_for_followers",
    "export_influencers",
    "import_influencers",
    "ImportReport",
    "RowReport",
    "RowOutcome",
    "FieldConflict",
    "InfluencerSyncError",
    "StorageError",
    "NotFoundError",
    "ValidationError",
    "ImportFileError",
]
