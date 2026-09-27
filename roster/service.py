"""对上层达人关系管理服务暴露的门面。"""
from __future__ import annotations

import json
from typing import Optional

from . import validation
from .exporting import build_export_document
from .importing import run_import
from .models import (
    TAG_MAX_PER_INFLUENCER,
    Influencer,
    InfluencerPatch,
    Platform,
    Status,
)
from .report import ImportReport
from .store import Store


class RosterService:
    def __init__(self, db_path: str, tag_dictionary: Optional[list[str]] = None) -> None:
        self._store = Store(db_path)
        if tag_dictionary is not None:
            self._store.initialize_tag_dictionary(tag_dictionary)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "RosterService":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 新建 / 修改 / 查看 ----

    def create_influencer(self, patch: InfluencerPatch) -> Influencer:
        with self._store.lock:
            return self._create_influencer(patch)

    def _create_influencer(self, patch: InfluencerPatch) -> Influencer:
        errors: list[str] = []
        if patch.account is None:
            errors.append("账号必填")
        if patch.platform is None:
            errors.append("平台必填")
        if patch.followers is None:
            errors.append("粉丝数必填")
        status = patch.status or Status.PROSPECT
        if patch.status is not None and patch.status != Status.PROSPECT:
            errors.append("新建的关系状态只能是 Prospect 或不填")
        errors.extend(self._validate_patch(patch))
        if errors:
            raise ValueError("; ".join(errors))

        conn = self._store.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._check_account_free(patch.platform.value, patch.account, exclude_id=None)
            influencer = Influencer(
                id=0,
                account=patch.account,
                platform=patch.platform,
                followers=patch.followers,
                tier=validation.tier_for_followers(patch.followers),
                tags=self._canonical_tags(patch.tags or []),
                city=patch.city,
                state=patch.state,
                status=status,
            )
            influencer.id = self._store.insert(influencer)
            conn.commit()
            return influencer
        except Exception:
            conn.rollback()
            raise

    def update_influencer(self, influencer_id: int, patch: InfluencerPatch) -> Influencer:
        with self._store.lock:
            return self._update_influencer(influencer_id, patch)

    def _update_influencer(self, influencer_id: int, patch: InfluencerPatch) -> Influencer:
        errors = self._validate_patch(patch)
        if errors:
            raise ValueError("; ".join(errors))

        conn = self._store.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = self._store.get(influencer_id)
            if current is None:
                raise KeyError("达人(#%d)不存在" % influencer_id)
            new_status = patch.status or current.status
            if new_status != current.status and not validation.is_valid_transition(
                current.status, new_status
            ):
                raise ValueError(
                    "关系状态不能从 %s 直接变为 %s"
                    % (current.status.value, new_status.value)
                )
            new_platform = patch.platform or current.platform
            new_account = patch.account or current.account
            if (
                validation.normalize_account(new_account)
                != validation.normalize_account(current.account)
                or new_platform != current.platform
            ):
                self._check_account_free(
                    new_platform.value, new_account, exclude_id=influencer_id
                )
            new_followers = (
                patch.followers if patch.followers is not None else current.followers
            )
            updated = Influencer(
                id=current.id,
                account=new_account,
                platform=new_platform,
                followers=new_followers,
                tier=validation.tier_for_followers(new_followers),
                tags=self._canonical_tags(patch.tags)
                if patch.tags is not None
                else current.tags,
                city=patch.city if patch.city is not None else current.city,
                state=patch.state if patch.state is not None else current.state,
                status=new_status,
            )
            self._store.update(updated)
            conn.commit()
            return updated
        except Exception:
            conn.rollback()
            raise

    def get_influencer(self, influencer_id: int) -> Optional[Influencer]:
        with self._store.lock:
            return self._store.get(influencer_id)

    # ---- 导出 / 导入 ----

    def export_json(self, status: Optional[str] = None) -> str:
        with self._store.lock:
            influencers = self._store.list_all(status)
            document = build_export_document(influencers, self._store.secret())
            return json.dumps(document, ensure_ascii=False, indent=2)

    def import_json(self, payload: str) -> ImportReport:
        document = json.loads(payload)
        with self._store.lock:
            return run_import(self._store, document)

    # ---- 内部 ----

    def _canonical_tags(self, tags: list[str]) -> list[str]:
        dictionary = {t.lower(): t for t in self._store.tag_dictionary()}
        return sorted((dictionary[t.lower()] for t in tags), key=str.lower)

    def _validate_patch(self, patch: InfluencerPatch) -> list[str]:
        errors: list[str] = []
        if patch.account is not None:
            error = validation.validate_account(patch.account)
            if error:
                errors.append(error)
        if patch.followers is not None:
            error = validation.validate_followers(patch.followers)
            if error:
                errors.append(error)
        if patch.tags is not None:
            dictionary = frozenset(
                t.lower() for t in self._store.tag_dictionary()
            )
            errors.extend(validation.validate_tags(patch.tags, dictionary))
            if len({t.lower() for t in patch.tags}) > TAG_MAX_PER_INFLUENCER:
                errors.append("标签超过%d个上限" % TAG_MAX_PER_INFLUENCER)
        for name, value in (("城市", patch.city), ("州", patch.state)):
            error = validation.validate_text_field(value, name)
            if error:
                errors.append(error)
        return errors

    def _check_account_free(
        self, platform: str, account: str, exclude_id: Optional[int]
    ) -> None:
        norm = validation.normalize_account(account)
        occupant = self._store.account_index().get((platform, norm))
        if occupant is not None and occupant != exclude_id:
            raise ValueError("账号已被达人(#%d)占用" % occupant)
