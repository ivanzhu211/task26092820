"""非功能：20 万达人规模下，1000 行导入 3 秒内完成。"""

import json
import sqlite3
import time

import pytest

from influencer_sync import Store, TagDictionary, export_influencers, import_influencers
from influencer_sync.models import tier_for_followers

from conftest import TAGS

SCALE = 200_000
IMPORT_ROWS = 1000


def _bulk_seed(db_path, count):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    rows = []
    for i in range(count):
        followers = 1000 + (i % 49000)
        rows.append((
            f"user_{i}", f"user_{i}", "Instagram", followers,
            tier_for_followers(followers).value, '["Beauty"]',
            "City", "ST", "Prospect", 1,
        ))
    conn.executemany(
        """INSERT INTO influencers(account, account_key, platform, followers,
           tier, tags, city, state, relationship_status, version)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def big_store(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("perf") / "big.db")
    store = Store(db_path, TagDictionary(TAGS))
    store.close()
    _bulk_seed(db_path, SCALE)
    store = Store(db_path)
    yield store
    store.close()


def test_export_200k_and_import_1000_within_3s(big_store):
    payload = export_influencers(big_store)
    assert len(payload["rows"]) == SCALE

    # 取 1000 行做混合修改：改城市、加标签、改粉丝数、换账号
    rows = payload["rows"][:IMPORT_ROWS]
    for i, row in enumerate(rows):
        row["city"] = f"NewCity{i}"
        row["tags"] = ["Beauty", "Travel"]
        row["followers"] = 2000 + i
        row["account"] = f"renamed_{i}"
    payload["rows"] = rows

    start = time.monotonic()
    report = import_influencers(big_store, payload)
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, f"导入耗时 {elapsed:.2f}s 超过 3 秒"
    assert all(r.outcome.value == "updated" for r in report.rows)
    inf = big_store.get_influencer(1)
    assert inf.city == "NewCity0" and inf.account == "renamed_0"


def test_import_1000_new_rows_within_3s(big_store):
    payload = {"format": "influencer-roster", "version": 1, "rows": []}
    for i in range(IMPORT_ROWS):
        payload["rows"].append({
            "account": f"fresh_{i}", "platform": "TikTok",
            "followers": 3000, "tags": ["Food"],
        })
    start = time.monotonic()
    report = import_influencers(big_store, payload)
    elapsed = time.monotonic() - start
    assert elapsed < 3.0, f"导入耗时 {elapsed:.2f}s 超过 3 秒"
    assert all(r.outcome.value == "created" for r in report.rows)
