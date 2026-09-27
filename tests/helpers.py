"""测试公共工具。"""
from __future__ import annotations

import json

from roster import InfluencerPatch, Platform, RosterService

TAGS = ["fashion", "tech", "beauty", "sports", "travel",
        "food", "music", "gaming", "art", "fitness",
        "books", "pets"]


def make_service(db_path=":memory:"):
    return RosterService(db_path, tag_dictionary=TAGS)


def add(svc, account, platform=Platform.INSTAGRAM, followers=2000,
        tags=None, city=None, state=None, status=None):
    patch = InfluencerPatch(
        account=account, platform=platform, followers=followers,
        tags=tags, city=city, state=state, status=status,
    )
    return svc.create_influencer(patch)


def export_doc(svc, status=None):
    return json.loads(svc.export_json(status))


def import_doc(svc, doc):
    return svc.import_json(json.dumps(doc))
