"""SQLite 存储层。

- 账号在同一平台下唯一（比较键为去掉 @ 后的小写形式）。
- 所有写入经过同一把锁与同一条连接，导入使用 BEGIN IMMEDIATE，
  保证与并发单条修改串行化、整批原子可见。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager

from .exceptions import NotFoundError, ValidationError
from .models import (
    EDITABLE_FIELDS,
    Influencer,
    Platform,
    RelationshipStatus,
    Tier,
    tier_for_followers,
)
from .validation import (
    TagDictionary,
    normalize_account,
    validate_account,
    validate_followers,
    validate_platform,
    validate_status_transition,
    validate_status_value,
    validate_tags,
    validate_text,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS influencers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL,
    account_key TEXT NOT NULL,
    platform TEXT NOT NULL,
    followers INTEGER NOT NULL,
    tier TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    city TEXT,
    state TEXT,
    relationship_status TEXT NOT NULL DEFAULT 'Prospect',
    version INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_influencer_platform_account
    ON influencers(platform, account_key);
CREATE INDEX IF NOT EXISTS idx_influencer_status
    ON influencers(relationship_status);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _row_to_influencer(row: sqlite3.Row) -> Influencer:
    return Influencer(
        id=row["id"],
        account=row["account"],
        platform=Platform(row["platform"]),
        followers=row["followers"],
        tags=json.loads(row["tags"]),
        city=row["city"],
        state=row["state"],
        relationship_status=RelationshipStatus(row["relationship_status"]),
        tier=Tier(row["tier"]),
        version=row["version"],
    )


class Store:
    def __init__(self, db_path: str, tag_dictionary=None):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(f"PRAGMA busy_timeout={10_000}")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='tag_dictionary'"
        ).fetchone()
        if tag_dictionary is None:
            if row is None:
                raise ValueError("首次建库必须提供标签字典")
            self._dictionary = TagDictionary(json.loads(row["value"]))
        else:
            if isinstance(tag_dictionary, TagDictionary):
                dictionary = tag_dictionary
            else:
                dictionary = TagDictionary(tag_dictionary)
            self._dictionary = dictionary
            self._persist_dictionary(dictionary)

    # ---- 基础维护 ----------------------------------------------------------

    @property
    def dictionary(self) -> TagDictionary:
        return self._dictionary

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _persist_dictionary(self, dictionary: TagDictionary) -> None:
        stored = json.dumps(dictionary.all, ensure_ascii=False)
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='tag_dictionary'"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES('tag_dictionary', ?)",
                (stored,),
            )
        elif {t.lower() for t in json.loads(row["value"])} != {
            t.lower() for t in dictionary.all
        }:
            raise ValueError("标签字典与库内已初始化的字典不一致")

    # ---- 读取 --------------------------------------------------------------

    def get_influencer(self, influencer_id: int) -> Influencer:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM influencers WHERE id=?", (influencer_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"达人不存在: {influencer_id}")
        return _row_to_influencer(row)

    def list_influencers(
        self, relationship_status: RelationshipStatus | None = None
    ) -> list[Influencer]:
        sql = "SELECT * FROM influencers"
        params: tuple = ()
        if relationship_status is not None:
            sql += " WHERE relationship_status=?"
            params = (relationship_status.value,)
        sql += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_influencer(row) for row in rows]

    # ---- 单条写入（上层服务直接调用） --------------------------------------

    def create_influencer(self, data: dict) -> Influencer:
        normalized, errors = self._validate_fields(data)
        for required in ("account", "platform", "followers"):
            if required not in data:
                errors.append(f"缺少必填字段: {required}")
        if errors:
            raise ValidationError(errors)
        status = normalized.get(
            "relationship_status", RelationshipStatus.PROSPECT
        )
        if status is not RelationshipStatus.PROSPECT:
            errors.append("新建达人的关系状态只能是 Prospect")
        tags = normalized.get("tags", [])
        if len(tags) > 10:
            errors.append("标签最多 10 个")
        if errors:
            raise ValidationError(errors)
        with self._lock:
            self._begin_immediate()
            try:
                cur = self._conn.execute(
                    """
                    INSERT INTO influencers(account, account_key, platform,
                        followers, tier, tags, city, state,
                        relationship_status, version)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        normalized["account"],
                        normalize_account(normalized["account"]),
                        normalized["platform"].value,
                        normalized["followers"],
                        tier_for_followers(
                            normalized["followers"]
                        ).value,
                        json.dumps(tags, ensure_ascii=False),
                        normalized.get("city"),
                        normalized.get("state"),
                        status.value,
                    ),
                )
                new_id = cur.lastrowid
                self._conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                self._conn.execute("ROLLBACK")
                raise ValidationError(
                    ["同一平台下账号已存在"]
                ) from None
        return self.get_influencer(new_id)

    def update_influencer(
        self, influencer_id: int, changes: dict
    ) -> Influencer:
        unknown = [k for k in changes if k not in EDITABLE_FIELDS]
        early_errors: list[str] = []
        if "tier" in changes:
            early_errors.append("层级为只读字段，修改不生效")
            unknown = [k for k in unknown if k != "tier"]
        early_errors.extend(f"不可修改的字段: {k}" for k in unknown)
        with self._lock:
            self._begin_immediate()
            try:
                row = self._conn.execute(
                    "SELECT * FROM influencers WHERE id=?", (influencer_id,)
                ).fetchone()
                if row is None:
                    raise NotFoundError(f"达人不存在: {influencer_id}")
                # 在写事务内读取当前值，保证与并发导入串行化
                current = _row_to_influencer(row)
                normalized, errors = self._validate_fields(changes)
                errors = early_errors + errors
                merged = self._merge_for_single(current, normalized)
                new_status = merged["relationship_status"]
                if not validate_status_transition(
                    current.relationship_status, new_status
                ):
                    errors.append(
                        f"关系状态不能从 {current.relationship_status.value} "
                        f"直接变为 {new_status.value}"
                    )
                if len(merged["tags"]) > 10:
                    errors.append("标签最多 10 个")
                if errors:
                    raise ValidationError(errors)
                self._write_row(
                    influencer_id,
                    merged["account"],
                    merged["platform"],
                    merged["followers"],
                    merged["tags"],
                    merged["city"],
                    merged["state"],
                    new_status,
                )
                self._conn.execute("COMMIT")
            except (NotFoundError, ValidationError):
                self._conn.execute("ROLLBACK")
                raise
            except sqlite3.IntegrityError:
                self._conn.execute("ROLLBACK")
                raise ValidationError(
                    ["同一平台下账号已存在"]
                ) from None
        return self.get_influencer(influencer_id)

    def _merge_for_single(
        self, current: Influencer, changes: dict
    ) -> dict:
        return {
            "account": changes.get("account", current.account),
            "platform": changes.get("platform", current.platform),
            "followers": changes.get("followers", current.followers),
            "tags": changes.get("tags", list(current.tags)),
            "city": changes.get("city", current.city),
            "state": changes.get("state", current.state),
            "relationship_status": changes.get(
                "relationship_status", current.relationship_status
            ),
        }

    # ---- 字段校验（单条与导入共用） ----------------------------------------

    def _validate_fields(self, data: dict) -> tuple[dict, list[str]]:
        normalized: dict = {}
        errors: list[str] = []
        if "account" in data:
            value, errs = validate_account(data["account"])
            errors.extend(errs)
            if value is not None:
                normalized["account"] = value
        if "platform" in data:
            value, errs = validate_platform(data["platform"])
            errors.extend(errs)
            if value is not None:
                normalized["platform"] = value
        if "followers" in data:
            value, errs = validate_followers(data["followers"])
            errors.extend(errs)
            if value is not None:
                normalized["followers"] = value
        if "tags" in data:
            value, errs = validate_tags(data["tags"], self._dictionary)
            errors.extend(errs)
            if value is not None:
                normalized["tags"] = value
        if "city" in data:
            value, errs = validate_text(data["city"], "城市")
            errors.extend(errs)
            if "城市必须" not in "".join(errs):
                normalized["city"] = value
        if "state" in data:
            value, errs = validate_text(data["state"], "州")
            errors.extend(errs)
            if "州必须" not in "".join(errors):
                normalized["state"] = value
        if "relationship_status" in data:
            value, errs = validate_status_value(
                data["relationship_status"]
            )
            errors.extend(errs)
            if value is not None:
                normalized["relationship_status"] = value
        return normalized, errors

    # ---- 内部写入原语 ------------------------------------------------------

    def _begin_immediate(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    @contextmanager
    def exclusive(self):
        """整批事务：进程内加锁 + BEGIN IMMEDIATE，提交前其他写入进不来。"""
        with self._lock:
            self._begin_immediate()
            try:
                yield
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ---- 批量读取（导入用，须在 exclusive 事务内调用） ----------------------

    def get_many(self, ids) -> dict[int, Influencer]:
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT * FROM influencers WHERE id IN ({marks})", ids
        ).fetchall()
        return {row["id"]: _row_to_influencer(row) for row in rows}

    def find_occupants(self, pairs) -> dict[tuple[str, str], int]:
        """{(platform_value, account_key): influencer_id}，只查给定组合。"""
        pairs = list(dict.fromkeys(pairs))
        if not pairs:
            return {}
        marks = ",".join("(?,?)" for _ in pairs)
        params = [v for pair in pairs for v in pair]
        rows = self._conn.execute(
            f"""SELECT platform, account_key, id FROM influencers
                WHERE (platform, account_key) IN ({marks})""",
            params,
        ).fetchall()
        return {
            (row["platform"], row["account_key"]): row["id"] for row in rows
        }

    # ---- 整批写入（导入用，须在 exclusive 事务内调用） ----------------------

    def apply_batch(self, ops: list[dict]) -> list[int]:
        """原子落库一整批操作；账号互换靠两阶段 account_key 更新实现。

        每个 op：{"kind": "insert"|"update", "id": int|None, 以及
        account/platform/followers/tags/city/state/relationship_status}。
        返回每个 op 对应的达人编号。
        """
        for op in ops:
            if op["kind"] == "update":
                self._conn.execute(
                    "UPDATE influencers SET account_key=? WHERE id=?",
                    # '#' 不是合法账号字符，临时键不会与真实账号冲突
                    (f"#pending#{op['id']}", op["id"]),
                )
        result_ids: list[int] = []
        for op in ops:
            if op["kind"] == "insert":
                new_id = self._insert_row(
                    op["account"],
                    op["platform"],
                    op["followers"],
                    op["tags"],
                    op["city"],
                    op["state"],
                    op["relationship_status"],
                )
                result_ids.append(new_id)
            else:
                self._write_row(
                    op["id"],
                    op["account"],
                    op["platform"],
                    op["followers"],
                    op["tags"],
                    op["city"],
                    op["state"],
                    op["relationship_status"],
                )
                result_ids.append(op["id"])
        return result_ids

    def _write_row(
        self,
        influencer_id: int,
        account: str,
        platform: Platform,
        followers: int,
        tags: list[str],
        city: str | None,
        state: str | None,
        status: RelationshipStatus,
    ) -> None:
        self._conn.execute(
            """
            UPDATE influencers SET
                account=?, account_key=?, platform=?, followers=?, tier=?,
                tags=?, city=?, state=?, relationship_status=?,
                version=version+1
            WHERE id=?
            """,
            (
                account,
                normalize_account(account),
                platform.value,
                followers,
                tier_for_followers(followers).value,
                json.dumps(tags, ensure_ascii=False),
                city,
                state,
                status.value,
                influencer_id,
            ),
        )

    def _insert_row(
        self,
        account: str,
        platform: Platform,
        followers: int,
        tags: list[str],
        city: str | None,
        state: str | None,
        status: RelationshipStatus,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO influencers(account, account_key, platform,
                followers, tier, tags, city, state,
                relationship_status, version)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                account,
                normalize_account(account),
                platform.value,
                followers,
                tier_for_followers(followers).value,
                json.dumps(tags, ensure_ascii=False),
                city,
                state,
                status.value,
            ),
        )
        return cur.lastrowid
