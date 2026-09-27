"""导入：解析导出文件，逐字段三方合并，事务落库并生成报告。

整体在一个 BEGIN IMMEDIATE 事务内完成：与并发的单条修改按锁串行化，
效果等同于导入整体发生在那次修改之前或之后，不会出现半生效状态。
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Any, Optional

from . import validation
from .exporting import FORMAT_NAME, FORMAT_VERSION, row_checksum
from .models import (
    CLEARABLE_FIELDS,
    EDITABLE_FIELDS,
    IMPORT_MAX_ROWS,
    TAG_MAX_PER_INFLUENCER,
    Influencer,
    Platform,
    Status,
)
from .report import FieldConflict, ImportReport, RowReport, RowResult
from .store import Store


@dataclass
class _Row:
    rowno: int
    raw: Any
    is_new: bool = False
    influencer_id: Optional[int] = None
    base: Optional[dict] = None
    exported_tier: Optional[str] = None
    provided: set[str] = field(default_factory=set)
    file_values: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    adopted: dict[str, Any] = field(default_factory=dict)
    conflicts: list[FieldConflict] = field(default_factory=list)
    rejected: bool = False

    def reject(self, reason: str) -> None:
        self.rejected = True
        if reason not in self.errors:
            self.errors.append(reason)


def run_import(store: Store, document: Any) -> ImportReport:
    rows_raw = _check_document(document)
    dictionary = {t.lower(): t for t in store.tag_dictionary()}

    conn = store.conn
    report = ImportReport()
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = _parse_rows(store, rows_raw, dictionary)
        _merge_rows(store, rows, dictionary)
        _enforce_account_uniqueness(store, rows)
        _apply(store, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    for row in rows:
        report.rows.append(_to_report(row))
    return report


# ---- 文档级检查 ----


def _check_document(document: Any) -> list:
    if not isinstance(document, dict):
        raise ValueError("导入内容必须是 JSON 对象")
    if document.get("format") != FORMAT_NAME:
        raise ValueError("不是本系统导出的文件（format 不匹配）")
    if document.get("version") != FORMAT_VERSION:
        raise ValueError("导出文件版本不支持")
    rows = document.get("rows")
    if not isinstance(rows, list):
        raise ValueError("导出文件缺少 rows 数组")
    if len(rows) > IMPORT_MAX_ROWS:
        raise ValueError("单次导入最多%d行" % IMPORT_MAX_ROWS)
    return rows


# ---- 阶段一：结构、身份、篡改、重复 ----


def _parse_rows(store: Store, rows_raw: list, dictionary: dict[str, str]) -> list[_Row]:
    secret = store.secret()
    rows: list[_Row] = []
    for index, raw in enumerate(rows_raw, start=1):
        row = _Row(rowno=index, raw=raw)
        rows.append(row)
        if not isinstance(raw, dict):
            row.reject("行必须是 JSON 对象")
            continue
        identity = raw.get("id")
        if identity is None:
            row.is_new = True
        else:
            if isinstance(identity, bool) or not isinstance(identity, int):
                row.reject("身份(id)不合法")
                continue
            row.influencer_id = identity
            meta = raw.get("_meta")
            if not isinstance(meta, dict) or not isinstance(meta.get("base"), dict):
                row.reject("系统写入的内容缺失或被篡改")
                continue
            base = meta["base"]
            checksum = meta.get("checksum")
            if not isinstance(checksum, str) or not hmac.compare_digest(
                checksum, row_checksum(secret, identity, base)
            ):
                row.reject("系统写入的内容被篡改，整行拒绝")
                continue
            row.base = base
            row.exported_tier = meta.get("tier")
            if "tier" in raw and raw["tier"] != row.exported_tier:
                row.notes.append("层级为只读，文件中的层级改动不生效")

    # 同一达人出现多行：全部拒绝
    by_id: dict[int, list[_Row]] = {}
    for row in rows:
        if not row.is_new and row.influencer_id is not None and not row.rejected:
            by_id.setdefault(row.influencer_id, []).append(row)
    for influencer_id, group in by_id.items():
        if len(group) > 1:
            for row in group:
                row.reject("同一达人(#%d)在文件中出现多行" % influencer_id)

    for row in rows:
        if not row.rejected:
            _parse_fields(row, dictionary)
    return rows


def _parse_fields(row: _Row, dictionary: dict[str, str]) -> None:
    raw = row.raw
    for name in EDITABLE_FIELDS:
        if name not in raw:
            continue
        value = raw[name]
        if value is None or value == "":
            if name in CLEARABLE_FIELDS:
                row.provided.add(name)
                row.file_values[name] = [] if name == "tags" else None
            else:
                row.errors.append("字段 %s 不允许清空" % name)
            continue
        error = _validate_value(name, value, dictionary)
        if error:
            row.errors.extend(error)
            continue
        row.provided.add(name)
        row.file_values[name] = value

    if row.is_new:
        for name in ("account", "platform", "followers"):
            if name not in row.provided:
                row.errors.append("新建行缺少必填字段 %s" % name)
        status = row.file_values.get("status")
        if status is not None and status != Status.PROSPECT.value:
            row.errors.append("新建行的关系状态只能是 Prospect 或不填")
    if row.errors:
        row.rejected = True


def _validate_value(name: str, value: Any, dictionary: dict[str, str]) -> list[str]:
    if name == "account":
        error = validation.validate_account(value)
        return [error] if error else []
    if name == "platform":
        error = validation.validate_platform(value)
        return [error] if error else []
    if name == "followers":
        error = validation.validate_followers(value)
        return [error] if error else []
    if name == "status":
        error = validation.validate_status(value)
        return [error] if error else []
    if name == "tags":
        return validation.validate_tags(value, frozenset(dictionary))
    error = validation.validate_text_field(value, name)
    return [error] if error else []


# ---- 阶段二：逐字段三方合并 ----


def _merge_rows(store: Store, rows: list[_Row], dictionary: dict[str, str]) -> None:
    ids = [r.influencer_id for r in rows if not r.is_new and not r.rejected]
    current = store.get_many(ids)
    for row in rows:
        if row.rejected:
            continue
        if row.is_new:
            _merge_new_row(row, dictionary)
            continue
        system = current.get(row.influencer_id)
        if system is None:
            row.reject("达人(#%d)在系统中不存在" % row.influencer_id)
            continue
        _merge_existing_row(row, system, dictionary)


def _merge_new_row(row: _Row, dictionary: dict[str, str]) -> None:
    values = row.file_values
    tags = _canonical_tags(values.get("tags") or [], dictionary)
    if len(tags) > TAG_MAX_PER_INFLUENCER:
        row.reject("标签超过%d个上限" % TAG_MAX_PER_INFLUENCER)
        return
    row.adopted = {
        "account": values["account"],
        "platform": values["platform"],
        "followers": values["followers"],
        "tags": tags,
        "city": values.get("city"),
        "state": values.get("state"),
        "status": values.get("status", Status.PROSPECT.value),
    }


def _merge_existing_row(row: _Row, system: Influencer, dictionary: dict[str, str]) -> None:
    base = row.base
    for name in row.provided:
        file_value = row.file_values[name]
        base_value = base.get(name)
        if name == "tags":
            _merge_tags(row, file_value, base_value, system, dictionary)
            continue
        if name == "account":
            file_cmp = validation.normalize_account(file_value)
            base_cmp = (
                validation.normalize_account(base_value)
                if isinstance(base_value, str)
                else None
            )
            system_cmp = validation.normalize_account(system.account)
            if file_cmp == base_cmp or file_cmp == system_cmp:
                continue  # 只改大小写/加@不算改动；与系统一致也不算改动
            if system_cmp == base_cmp:
                row.adopted[name] = file_value
            else:
                row.conflicts.append(
                    FieldConflict(name, base_value, file_value, system.account)
                )
            continue
        system_value = _system_value(system, name)
        if file_value == base_value or file_value == system_value:
            continue
        if system_value == base_value:
            row.adopted[name] = file_value
        else:
            row.conflicts.append(FieldConflict(name, base_value, file_value, system_value))

    adopted_status = row.adopted.get("status")
    if adopted_status is not None and adopted_status != system.status.value:
        if not validation.is_valid_transition(system.status, Status(adopted_status)):
            row.reject(
                "关系状态不能从 %s 直接变为 %s" % (system.status.value, adopted_status)
            )


def _merge_tags(
    row: _Row,
    file_value: Any,
    base_value: Any,
    system: Influencer,
    dictionary: dict[str, str],
) -> None:
    base_set = {t.lower() for t in base_value} if isinstance(base_value, list) else set()
    file_set = {t.lower() for t in file_value}
    system_set = {t.lower() for t in system.tags}
    merged = (file_set | system_set) - (base_set - file_set) - (base_set - system_set)
    if len(merged) > TAG_MAX_PER_INFLUENCER:
        row.reject("标签合并后超过%d个上限" % TAG_MAX_PER_INFLUENCER)
        return
    if merged != system_set:
        row.adopted["tags"] = _canonical_tags(sorted(merged), dictionary)


def _canonical_tags(tags: list[str], dictionary: dict[str, str]) -> list[str]:
    return sorted((dictionary[t.lower()] for t in tags), key=str.lower)


def _system_value(system: Influencer, name: str) -> Any:
    value = getattr(system, name)
    if hasattr(value, "value"):
        return value.value
    return value


# ---- 阶段三：整批账号唯一性（定点迭代） ----


def _enforce_account_uniqueness(store: Store, rows: list[_Row]) -> None:
    index = store.account_index()  # (platform, norm) -> id，导入前的占用情况
    current = store.get_many(
        [r.influencer_id for r in rows if not r.is_new and not r.rejected]
    )

    def target(row: _Row) -> tuple[str, str]:
        if row.is_new:
            return (
                row.adopted["platform"],
                validation.normalize_account(row.adopted["account"]),
            )
        system = current[row.influencer_id]
        platform = row.adopted.get("platform", system.platform.value)
        account = row.adopted.get("account", system.account)
        return (platform, validation.normalize_account(account))

    while True:
        active = [r for r in rows if not r.rejected]
        targets = {id(r): target(r) for r in active}
        newly_rejected = False

        # 多行抢同一个账号（原地不动的行是占用者，不算抢）
        by_target: dict[tuple[str, str], list[_Row]] = {}
        for row in active:
            if not row.is_new:
                system = current[row.influencer_id]
                held = (system.platform.value, validation.normalize_account(system.account))
                if targets[id(row)] == held:
                    continue
            by_target.setdefault(targets[id(row)], []).append(row)
        for group in by_target.values():
            if len(group) > 1:
                for row in group:
                    others = [str(r.rowno) for r in group if r is not row]
                    row.reject("账号与第 %s 行冲突，这些行都被拒绝" % "、".join(others))
                    newly_rejected = True

        # 抢一个没被改走的账号
        for row in active:
            if row.rejected:
                continue
            occupant = index.get(targets[id(row)])
            if occupant is None or occupant == row.influencer_id:
                continue
            occupant_rows = [
                r
                for r in active
                if not r.is_new and not r.rejected and r.influencer_id == occupant
            ]
            if occupant_rows and targets[id(occupant_rows[0])] != targets[id(row)]:
                continue  # 占用者在本批中改走，账号被让出（互换合法）
            row.reject(
                "账号被达人(#%d)占用且未改走" % occupant
            )
            newly_rejected = True

        if not newly_rejected:
            return


# ---- 阶段四：落库 ----


def _apply(store: Store, rows: list[_Row]) -> None:
    current = store.get_many(
        [r.influencer_id for r in rows if not r.is_new and not r.rejected]
    )
    updates: list[Influencer] = []
    for row in rows:
        if row.rejected:
            continue
        adopted = row.adopted
        if row.is_new:
            influencer = Influencer(
                id=0,
                account=adopted["account"],
                platform=Platform(adopted["platform"]),
                followers=adopted["followers"],
                tier=validation.tier_for_followers(adopted["followers"]),
                tags=adopted["tags"],
                city=adopted["city"],
                state=adopted["state"],
                status=Status(adopted["status"]),
            )
            row.influencer_id = store.insert(influencer)
            continue
        if not adopted:
            continue
        system = current[row.influencer_id]
        updates.append(Influencer(
            id=system.id,
            account=adopted.get("account", system.account),
            platform=Platform(adopted.get("platform", system.platform.value)),
            followers=adopted.get("followers", system.followers),
            tier=validation.tier_for_followers(adopted.get("followers", system.followers)),
            tags=adopted.get("tags", system.tags),
            city=adopted.get("city", system.city),
            state=adopted.get("state", system.state),
            status=Status(adopted.get("status", system.status.value)),
        ))
    # 互换账号时唯一索引不能中途冲突：先把要挪走的行占位，再写最终值
    for influencer in updates:
        store.update(influencer, norm_override="\x00moving-%d" % influencer.id)
    for influencer in updates:
        store.update(influencer)


# ---- 报告 ----


def _to_report(row: _Row) -> RowReport:
    if row.rejected:
        result = RowResult.REJECTED
    elif row.is_new:
        result = RowResult.CREATED
    elif row.conflicts:
        result = RowResult.CONFLICT
    elif row.adopted:
        result = RowResult.UPDATED
    else:
        result = RowResult.NO_CHANGE
    return RowReport(
        row=row.rowno,
        result=result,
        influencer_id=row.influencer_id,
        adopted_fields=list(row.adopted.keys()),
        conflicts=row.conflicts,
        reject_reasons=list(row.errors),
        notes=row.notes,
    )
