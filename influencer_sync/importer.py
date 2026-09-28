"""导入：逐字段三方比较 + 标签集合合并 + 账号唯一性定点判断。

整批在 store.exclusive() 事务内完成读取、合并与写入：
- 与并发的单条修改串行化，结果等价于导入整体发生在修改之前或之后；
- 任何时刻都看不到只生效了一半的导入。
"""

from __future__ import annotations

import json

from .exceptions import ImportFileError
from .export import FORMAT_NAME, FORMAT_VERSION, verify_row_meta
from .models import Platform, RelationshipStatus
from .report import FieldConflict, ImportReport, RowOutcome, RowReport
from .validation import (
    normalize_account,
    validate_account,
    validate_followers,
    validate_platform,
    validate_status_transition,
    validate_status_value,
    validate_tags,
    validate_text,
)

MAX_ROWS = 1000
MAX_TAGS = 10

_CLEARABLE_FIELDS = ("city", "state", "tags")


class _RowCtx:
    """一行的导入上下文。"""

    def __init__(self, index: int, raw):
        self.index = index  # 文件行序，从 1 开始
        self.raw = raw
        self.report = RowReport(file_line=index)
        self.rejected = False
        self.is_new = True
        self.influencer_id: int | None = None
        self.current = None  # 系统当前 Influencer
        self.baseline: dict | None = None
        self.desired: dict = {}  # 合并后的目标值（七个字段）
        self.desired_key: tuple[str, str] | None = None  # (platform, account_key)

    def reject(self, *reasons: str) -> None:
        self.rejected = True
        self.report.outcome = RowOutcome.REJECTED
        self.report.reasons.extend(reasons)


def import_influencers(store, payload) -> ImportReport:
    """导入导出格式的 JSON（dict 或字符串），返回逐行报告。"""
    data = _parse_payload(payload)
    rows = data["rows"]
    if len(rows) > MAX_ROWS:
        raise ImportFileError(f"单次导入最多 {MAX_ROWS} 行")

    report = ImportReport()
    with store.exclusive():
        ctxs = _prepare_rows(store, rows)
        _load_currents(store, ctxs)
        for ctx in ctxs:
            if not ctx.rejected:
                _merge_row(store, ctx)
        _resolve_account_collisions(store, ctxs)
        _apply(store, ctxs)
        report.rows = [ctx.report for ctx in ctxs]
    return report


# ---- 文件级解析 ------------------------------------------------------------


def _parse_payload(payload) -> dict:
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ImportFileError(f"导入内容不是合法 JSON: {exc}") from None
    if not isinstance(payload, dict):
        raise ImportFileError("导入内容必须是导出格式的 JSON 对象")
    if payload.get("format") != FORMAT_NAME:
        raise ImportFileError("文件格式标识不正确")
    if payload.get("version") != FORMAT_VERSION:
        raise ImportFileError(f"不支持的文件版本: {payload.get('version')}")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ImportFileError("文件缺少 rows 数组")
    return payload


# ---- 行级准备：身份、篡改、重复 --------------------------------------------


def _prepare_rows(store, rows) -> list[_RowCtx]:
    ctxs = [_RowCtx(i + 1, raw) for i, raw in enumerate(rows)]
    by_id: dict[int, list[_RowCtx]] = {}
    for ctx in ctxs:
        if not isinstance(ctx.raw, dict):
            ctx.reject("行必须是 JSON 对象")
            continue
        raw_id = ctx.raw.get("id")
        if raw_id is None:
            continue  # 无身份：新建行
        if isinstance(raw_id, bool) or not isinstance(raw_id, int):
            ctx.reject("id 必须是整数")
            continue
        ctx.is_new = False
        ctx.influencer_id = raw_id
        ctx.report.influencer_id = raw_id
        if not verify_row_meta(ctx.raw):
            ctx.reject("系统写入的内容（id 或导出快照）被改过，整行拒绝")
            continue
        ctx.baseline = ctx.raw["_meta"]["baseline"]
        by_id.setdefault(raw_id, []).append(ctx)
    for same_id in by_id.values():
        if len(same_id) > 1:
            lines = "、".join(str(c.index) for c in same_id)
            for ctx in same_id:
                ctx.reject(f"同一达人（编号 {ctx.influencer_id}）出现多行: 第 {lines} 行")
    return ctxs


def _load_currents(store, ctxs) -> None:
    ids = [
        ctx.influencer_id
        for ctx in ctxs
        if not ctx.rejected and not ctx.is_new
    ]
    currents = store.get_many(ids)
    for ctx in ctxs:
        if ctx.rejected or ctx.is_new:
            continue
        current = currents.get(ctx.influencer_id)
        if current is None:
            ctx.reject(f"达人不存在（编号 {ctx.influencer_id}），可能已被删除")
        else:
            ctx.current = current


# ---- 逐行合并 --------------------------------------------------------------


def _merge_row(store, ctx: _RowCtx) -> None:
    if ctx.is_new:
        _merge_new_row(store, ctx)
    else:
        _merge_existing_row(store, ctx)


def _validate_present_fields(store, raw: dict) -> tuple[dict, list[str]]:
    """校验行里出现的七个字段，返回 (规范化值, 全部错误)。"""
    values: dict = {}
    errors: list[str] = []
    if "account" in raw:
        value, errs = validate_account(raw["account"])
        errors.extend(errs)
        if value is not None:
            values["account"] = value
    if "platform" in raw:
        value, errs = validate_platform(raw["platform"])
        errors.extend(errs)
        if value is not None:
            values["platform"] = value
    if "followers" in raw:
        value, errs = validate_followers(raw["followers"])
        errors.extend(errs)
        if value is not None:
            values["followers"] = value
    if "tags" in raw:
        value, errs = validate_tags(raw["tags"], store.dictionary)
        errors.extend(errs)
        if value is not None:
            values["tags"] = value
    for field_name, label in (("city", "城市"), ("state", "州")):
        if field_name in raw:
            value, errs = validate_text(raw[field_name], label)
            errors.extend(errs)
            if not errs:
                values[field_name] = value
    if "relationship_status" in raw:
        value, errs = validate_status_value(raw["relationship_status"])
        errors.extend(errs)
        if value is not None:
            values["relationship_status"] = value
    return values, errors


def _merge_new_row(store, ctx: _RowCtx) -> None:
    raw = ctx.raw
    values, errors = _validate_present_fields(store, raw)
    for required in ("account", "platform", "followers"):
        if required not in raw:
            errors.append(f"新建行缺少必填字段: {required}")
    status = values.get("relationship_status")
    if status is not None and status is not RelationshipStatus.PROSPECT:
        errors.append("新建行的关系状态只能是 Prospect 或不填")
    tags = values.get("tags", [])
    if len(tags) > MAX_TAGS:
        errors.append(f"标签最多 {MAX_TAGS} 个")
    if "tier" in raw:
        ctx.report.notices.append("层级为只读字段，文件中的修改不生效")
    if errors:
        ctx.reject(*errors)
        return
    ctx.desired = {
        "account": values["account"],
        "platform": values["platform"],
        "followers": values["followers"],
        "tags": tags,
        "city": values.get("city"),
        "state": values.get("state"),
        "relationship_status": status or RelationshipStatus.PROSPECT,
    }
    ctx.report.applied_fields = [
        name for name in ("account", "platform", "followers", "tags",
                          "city", "state", "relationship_status")
        if name in raw
    ]
    ctx.desired_key = (
        values["platform"].value,
        normalize_account(values["account"]),
    )


def _scalar_equal(field_name: str, left, right) -> bool:
    if field_name == "account":
        return normalize_account(left) == normalize_account(right)
    return left == right


def _merge_existing_row(store, ctx: _RowCtx) -> None:
    raw = ctx.raw
    current = ctx.current
    baseline = ctx.baseline
    values, errors = _validate_present_fields(store, raw)

    if "tier" in raw and raw["tier"] != current.tier.value:
        ctx.report.notices.append("层级为只读字段，文件中的修改不生效")

    system_values = {
        "account": current.account,
        "platform": current.platform,
        "followers": current.followers,
        "city": current.city,
        "state": current.state,
        "relationship_status": current.relationship_status,
    }
    desired = dict(system_values)
    applied: list[str] = []

    for field_name, system_val in system_values.items():
        if field_name not in raw:
            continue  # 字段被删掉：不动
        base_val = _baseline_scalar(field_name, baseline)
        file_present_valid = field_name in values
        file_val = values.get(field_name)
        # 文件侧是否改了：用原始文件值与基准比较（非法值也算改了，但行已被拒）
        raw_file_val = raw[field_name]
        file_changed = not _file_scalar_unchanged(
            field_name, raw_file_val, base_val
        )
        system_changed = not _scalar_equal(field_name, system_val, base_val)
        if not file_changed:
            continue  # 保留系统当前值
        if not file_present_valid:
            continue  # 非法值：错误已收集，行将被拒
        if not system_changed:
            desired[field_name] = file_val
            applied.append(field_name)
        elif _scalar_equal(field_name, file_val, system_val):
            pass  # 两边改成同一个值：不算冲突
        else:
            ctx.report.conflicts.append(
                FieldConflict(
                    field=field_name,
                    baseline=base_val,
                    file_value=_display_value(field_name, file_val),
                    system_value=_display_value(field_name, system_val),
                )
            )

    # 标签：集合三方合并
    tags_ok = _merge_tags(store, ctx, raw, values, baseline, current)
    if tags_ok is None:
        errors.append(f"标签合并后超过 {MAX_TAGS} 个，整行拒绝")
    else:
        desired["tags"] = tags_ok
        if {t.lower() for t in tags_ok} != {t.lower() for t in current.tags}:
            applied.append("tags")

    # 关系状态：只校验真正会被采用的变更
    final_status = desired["relationship_status"]
    if final_status != current.relationship_status and not (
        validate_status_transition(current.relationship_status, final_status)
    ):
        errors.append(
            f"关系状态不能从 {current.relationship_status.value} "
            f"直接变为 {final_status.value}"
        )

    if errors:
        ctx.reject(*errors)
        return
    ctx.desired = desired
    ctx.desired_key = (
        desired["platform"].value,
        normalize_account(desired["account"]),
    )
    ctx.report.applied_fields = applied


def _baseline_scalar(field_name: str, baseline: dict):
    value = baseline[field_name]
    if field_name == "platform":
        return Platform(value)
    if field_name == "relationship_status":
        return RelationshipStatus(value)
    return value


def _file_scalar_unchanged(field_name: str, raw_file_val, base_val) -> bool:
    """文件值相对基准是否没改（账号忽略大小写与前导 @）。"""
    if field_name == "account":
        if not isinstance(raw_file_val, str):
            return False
        body = raw_file_val[1:] if raw_file_val.startswith("@") else raw_file_val
        return normalize_account(body) == normalize_account(base_val)
    if field_name in ("city", "state"):
        if raw_file_val is None:
            return base_val is None
        if isinstance(raw_file_val, str):
            stripped = raw_file_val.strip()
            return (stripped or None) == base_val
        return False
    if field_name == "platform":
        return raw_file_val == base_val.value
    if field_name == "relationship_status":
        return raw_file_val == base_val.value
    return raw_file_val == base_val


def _display_value(field_name: str, value):
    if hasattr(value, "value"):
        return value.value
    return value


def _merge_tags(store, ctx, raw, values, baseline, current):
    """标签集合三方合并；返回合并后的标签列表，超过上限返回 None。"""
    dictionary = store.dictionary
    baseline_keys = set(baseline["tags"])  # 导出时已存小写集合
    system_keys = {t.lower() for t in current.tags}
    if "tags" not in raw:
        return list(current.tags)  # 字段被删：不动
    if "tags" not in values:
        return list(current.tags)  # 非法值：行将被拒，占位即可
    file_keys = {t.lower() for t in values["tags"]}
    file_removed = baseline_keys - file_keys
    file_added = file_keys - baseline_keys
    merged_keys = (system_keys - file_removed) | file_added
    if len(merged_keys) > MAX_TAGS:
        return None
    canonical = {}
    for tag in list(current.tags) + list(values["tags"]):
        canonical.setdefault(tag.lower(), dictionary.lookup(tag) or tag)
    ordered = [t for t in current.tags if t.lower() in merged_keys]
    ordered += [
        canonical[k] for k in file_added if k not in {t.lower() for t in ordered}
    ]
    return ordered


# ---- 账号唯一性定点判断 ------------------------------------------------------


def _resolve_account_collisions(store, ctxs) -> None:
    active = [c for c in ctxs if not c.rejected]
    if not active:
        return
    pairs = [c.desired_key for c in active]
    occupants = store.find_occupants(pairs)
    # 只有"新建行"或"账号/平台键真被改走的行"才算认领者；
    # 键没动的行是静态占用方（等同库内未涉及的达人），不参与争抢。
    current_keys = {
        c.influencer_id: (c.current.platform.value,
                          normalize_account(c.current.account))
        for c in active
        if not c.is_new
    }

    def is_mover(ctx) -> bool:
        if ctx.is_new:
            return True
        return current_keys[ctx.influencer_id] != ctx.desired_key

    while True:
        movers = [c for c in active if is_mover(c)]
        mover_ids = {c.influencer_id for c in movers if not c.is_new}
        claims: dict[tuple[str, str], list] = {}
        for ctx in movers:
            claims.setdefault(ctx.desired_key, []).append(("row", ctx))
        for key, occupant_id in occupants.items():
            if key not in claims:
                continue
            if occupant_id in mover_ids:
                continue  # 占用者本次会被改走
            claims[key].append(("db", occupant_id))
        newly_rejected: list[_RowCtx] = []
        for key, claimants in claims.items():
            row_claimants = [c for kind, c in claimants if kind == "row"]
            db_claimants = [i for kind, i in claimants if kind == "db"]
            if len(row_claimants) + len(db_claimants) <= 1:
                continue
            for ctx in row_claimants:
                reasons = []
                for other in row_claimants:
                    if other is not ctx:
                        reasons.append(
                            f"账号与第 {other.index} 行争抢"
                            f"（平台 {key[0]} 账号 {key[1]}）"
                        )
                for occupant_id in db_claimants:
                    reasons.append(
                        f"账号被达人（编号 {occupant_id}）占用且未被改走"
                        f"（平台 {key[0]} 账号 {key[1]}）"
                    )
                newly_rejected.append(ctx)
                ctx.reject(*reasons)
        if not newly_rejected:
            return
        active = [c for c in active if not c.rejected]


# ---- 落库 ------------------------------------------------------------------


def _apply(store, ctxs) -> None:
    ops = []
    op_ctxs = []
    for ctx in ctxs:
        if ctx.rejected:
            continue
        if ctx.is_new:
            ops.append({"kind": "insert", "id": None, **ctx.desired})
            op_ctxs.append(ctx)
        else:
            changed = _has_changes(ctx)
            if changed:
                ops.append({"kind": "update", "id": ctx.influencer_id, **ctx.desired})
                op_ctxs.append(ctx)
    ids = store.apply_batch(ops) if ops else []
    for ctx, new_id in zip(op_ctxs, ids):
        if ctx.is_new:
            ctx.report.influencer_id = new_id
    for ctx in ctxs:
        if ctx.rejected:
            continue
        if ctx.report.conflicts:
            ctx.report.outcome = RowOutcome.CONFLICT
        elif ctx.is_new:
            ctx.report.outcome = RowOutcome.CREATED
        elif ctx.report.applied_fields:
            ctx.report.outcome = RowOutcome.UPDATED
        else:
            ctx.report.outcome = RowOutcome.UNCHANGED


def _has_changes(ctx) -> bool:
    current = ctx.current
    desired = ctx.desired
    if normalize_account(desired["account"]) != normalize_account(current.account):
        return True
    if desired["platform"] != current.platform:
        return True
    if desired["followers"] != current.followers:
        return True
    if {t.lower() for t in desired["tags"]} != {t.lower() for t in current.tags}:
        return True
    if desired["city"] != current.city or desired["state"] != current.state:
        return True
    if desired["relationship_status"] != current.relationship_status:
        return True
    return False
