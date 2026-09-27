"""SQLite 存储层。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Optional

from .models import TAG_DICT_MAX, Influencer, Platform, Status, Tier
from .validation import normalize_account

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS influencers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL,
    account_norm TEXT NOT NULL,
    platform TEXT NOT NULL,
    followers INTEGER NOT NULL,
    tier TEXT NOT NULL,
    tags TEXT NOT NULL,
    city TEXT,
    state TEXT,
    status TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_influencers_account
    ON influencers (platform, account_norm);
"""

_BUSY_TIMEOUT_MS = 10000


class Store:
    """封装 SQLite 连接与 influencers 表。

    连接使用 WAL 与 busy_timeout：导入用 BEGIN IMMEDIATE 事务，
    单条修改同样走短事务，二者按锁顺序串行化。
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        if db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(
            db_path, timeout=_BUSY_TIMEOUT_MS / 1000, check_same_thread=False
        )
        # 同一连接不跨线程并发使用；不同连接间由 SQLite 锁串行化
        self.lock = threading.RLock()
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    # ---- 元数据 ----

    def _meta_get(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def _meta_set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def initialize_tag_dictionary(self, tags: list[str]) -> None:
        if len(tags) > TAG_DICT_MAX:
            raise ValueError("标签字典最多%d个" % TAG_DICT_MAX)
        lowered = [t.lower() for t in tags]
        if len(set(lowered)) != len(lowered):
            raise ValueError("标签字典存在重复（不分大小写）")
        if self._meta_get("tag_dictionary") is None:
            self._meta_set("tag_dictionary", json.dumps(tags, ensure_ascii=False))
            self._conn.commit()

    def tag_dictionary(self) -> list[str]:
        raw = self._meta_get("tag_dictionary")
        return json.loads(raw) if raw else []

    def secret(self) -> bytes:
        """导出行完整性校验用的密钥（惰性生成并持久化）。"""
        raw = self._meta_get("export_secret")
        if raw is None:
            raw = os.urandom(32).hex()
            self._meta_set("export_secret", raw)
            self._conn.commit()
        return bytes.fromhex(raw)

    # ---- 达人行 ----

    @staticmethod
    def _row_to_influencer(row: sqlite3.Row) -> Influencer:
        return Influencer(
            id=row["id"],
            account=row["account"],
            platform=Platform(row["platform"]),
            followers=row["followers"],
            tier=Tier(row["tier"]),
            tags=json.loads(row["tags"]),
            city=row["city"],
            state=row["state"],
            status=Status(row["status"]),
        )

    def insert(self, influencer: Influencer) -> int:
        cur = self._conn.execute(
            "INSERT INTO influencers "
            "(account, account_norm, platform, followers, tier, tags, city, state, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                influencer.account,
                normalize_account(influencer.account),
                influencer.platform.value,
                influencer.followers,
                influencer.tier.value,
                json.dumps(influencer.tags, ensure_ascii=False),
                influencer.city,
                influencer.state,
                influencer.status.value,
            ),
        )
        return int(cur.lastrowid)

    def update(self, influencer: Influencer, norm_override: Optional[str] = None) -> None:
        self._conn.execute(
            "UPDATE influencers SET account=?, account_norm=?, platform=?, followers=?, "
            "tier=?, tags=?, city=?, state=?, status=? WHERE id=?",
            (
                influencer.account,
                norm_override
                if norm_override is not None
                else normalize_account(influencer.account),
                influencer.platform.value,
                influencer.followers,
                influencer.tier.value,
                json.dumps(influencer.tags, ensure_ascii=False),
                influencer.city,
                influencer.state,
                influencer.status.value,
                influencer.id,
            ),
        )

    def get(self, influencer_id: int) -> Optional[Influencer]:
        row = self._conn.execute(
            "SELECT * FROM influencers WHERE id = ?", (influencer_id,)
        ).fetchone()
        return self._row_to_influencer(row) if row else None

    def get_many(self, ids: list[int]) -> dict[int, Influencer]:
        result: dict[int, Influencer] = {}
        if not ids:
            return result
        placeholders = ",".join("?" for _ in ids)
        for row in self._conn.execute(
            "SELECT * FROM influencers WHERE id IN (%s)" % placeholders, ids
        ):
            influencer = self._row_to_influencer(row)
            result[influencer.id] = influencer
        return result

    def list_all(self, status: Optional[str] = None) -> list[Influencer]:
        if status is None:
            rows = self._conn.execute("SELECT * FROM influencers ORDER BY id")
        else:
            rows = self._conn.execute(
                "SELECT * FROM influencers WHERE status = ? ORDER BY id", (status,)
            )
        return [self._row_to_influencer(r) for r in rows]

    def account_index(self) -> dict[tuple[str, str], int]:
        """(platform, normalize 后账号) -> influencer id，用于整批唯一性判断。"""
        return {
            (row["platform"], row["account_norm"]): row["id"]
            for row in self._conn.execute(
                "SELECT id, platform, account_norm FROM influencers"
            )
        }
