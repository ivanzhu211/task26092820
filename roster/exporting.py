"""导出：生成带三方合并所需基线与完整性校验的 JSON。

存储占用不随导出次数增长：基线（导出时的七个字段值）随文件走，
不落库；每行附 HMAC 校验和，覆盖 id 与基线，篡改即整行拒绝。
层级（tier）只读但不纳入校验和：改了不生效，仅在报告中提示。
"""
from __future__ import annotations

import hashlib
import hmac
import json

from .models import EDITABLE_FIELDS, Influencer

FORMAT_NAME = "influencer-roster-export"
FORMAT_VERSION = 1


def _field_values(influencer: Influencer) -> dict:
    return {
        "account": influencer.account,
        "platform": influencer.platform.value,
        "followers": influencer.followers,
        "tags": list(influencer.tags),
        "city": influencer.city,
        "state": influencer.state,
        "status": influencer.status.value,
    }


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def row_checksum(secret: bytes, influencer_id: int, base: dict) -> str:
    """对 id + 基线内容计算 HMAC，用于发现篡改。"""
    digest = hmac.new(
        secret, _canonical({"id": influencer_id, "base": base}), hashlib.sha256
    )
    return digest.hexdigest()


def build_export_document(influencers: list[Influencer], secret: bytes) -> dict:
    rows = []
    for influencer in influencers:
        base = _field_values(influencer)
        rows.append(
            {
                "id": influencer.id,
                **base,
                "tier": influencer.tier.value,
                "_meta": {
                    "tier": influencer.tier.value,
                    "base": base,
                    "checksum": row_checksum(secret, influencer.id, base),
                },
            }
        )
    return {"format": FORMAT_NAME, "version": FORMAT_VERSION, "rows": rows}
