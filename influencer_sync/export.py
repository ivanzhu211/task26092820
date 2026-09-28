"""导出：全部或按关系状态。

每行除七个可改字段与只读层级外，还带 ``_meta``：
- ``baseline``：导出时七个字段的快照，导入时作为三方比较的基准；
- ``checksum``：覆盖 id 与 baseline，导入时据此发现对系统写入内容的篡改。

导出是无状态的：不在库里记录任何导出历史，存储占用不随导出次数增长；
几个月前的旧文件只要未被篡改，仍能正确三方合并。
"""

from __future__ import annotations

import hashlib
import json

from .models import Influencer, RelationshipStatus

FORMAT_NAME = "influencer-roster"
FORMAT_VERSION = 1

_BASELINE_FIELDS = (
    "account",
    "platform",
    "followers",
    "tags",
    "city",
    "state",
    "relationship_status",
)


def _baseline_of(inf: Influencer) -> dict:
    return {
        "account": inf.account,
        "platform": inf.platform.value,
        "followers": inf.followers,
        "tags": sorted(t.lower() for t in inf.tags),
        "city": inf.city,
        "state": inf.state,
        "relationship_status": inf.relationship_status.value,
    }


def _checksum(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _row_for(inf: Influencer) -> dict:
    baseline = _baseline_of(inf)
    return {
        "id": inf.id,
        "account": inf.account,
        "platform": inf.platform.value,
        "followers": inf.followers,
        "tags": list(inf.tags),
        "city": inf.city,
        "state": inf.state,
        "relationship_status": inf.relationship_status.value,
        "tier": inf.tier.value,
        "_meta": {
            "baseline": baseline,
            "checksum": _checksum({"id": inf.id, "baseline": baseline}),
        },
    }


def export_influencers(store, relationship_status=None) -> dict:
    """导出为可 JSON 序列化的 dict；可按关系状态过滤。"""
    status = None
    if relationship_status is not None:
        status = RelationshipStatus(relationship_status)
    influencers = store.list_influencers(status)
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "rows": [_row_for(inf) for inf in influencers],
    }


def verify_row_meta(row: dict) -> bool:
    """校验一行的 _meta 是否完整且未被篡改。"""
    meta = row.get("_meta")
    if not isinstance(meta, dict):
        return False
    baseline = meta.get("baseline")
    checksum = meta.get("checksum")
    if not isinstance(baseline, dict) or not isinstance(checksum, str):
        return False
    if set(baseline) != set(_BASELINE_FIELDS):
        return False
    expect = _checksum({"id": row.get("id"), "baseline": baseline})
    return checksum == expect
