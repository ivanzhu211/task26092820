import json

import pytest

from influencer_sync import Store, TagDictionary, export_influencers


TAGS = ["Beauty", "Travel", "Food", "Fitness", "Tech",
        "Gaming", "Fashion", "Music", "Art", "Sports",
        "Books", "Pets"]


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "roster.db"), TagDictionary(TAGS))
    yield s
    s.close()


@pytest.fixture()
def make_influencer(store):
    def _make(**kwargs):
        return store.create_influencer(kwargs)
    return _make


def export_rows(store, status=None):
    return export_influencers(store, status)["rows"]


def as_json(payload):
    return json.dumps(payload, ensure_ascii=False)
