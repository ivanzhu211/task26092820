"""单条新建/修改/查看：与导入遵守同样的校验规则。"""

import pytest

from influencer_sync import RelationshipStatus as S
from influencer_sync.exceptions import NotFoundError, ValidationError


def test_create_and_get(store):
    inf = store.create_influencer({
        "account": "@Alice", "platform": "Instagram", "followers": 5000,
        "tags": ["beauty"], "city": "NYC", "state": "NY",
    })
    assert inf.id == 1
    assert inf.account == "Alice"  # 去掉 @，保留大小写
    assert inf.tier.value == "Micro"
    assert inf.tags == ["Beauty"]  # 字典标准写法
    fetched = store.get_influencer(1)
    assert fetched.account == "Alice" and fetched.city == "NYC"


def test_tier_boundaries(store):
    cases = [(1000, "Nano"), (4999, "Nano"), (5000, "Micro"),
             (24999, "Micro"), (25000, "Mid-Micro"), (50000, "Mid-Micro")]
    for followers, tier in cases:
        inf = store.create_influencer({
            "account": f"u{followers}", "platform": "TikTok",
            "followers": followers})
        assert inf.tier.value == tier
    for bad in (0, 999, 50001, 10 ** 6):
        with pytest.raises(ValidationError):
            store.create_influencer({
                "account": f"x{bad}", "platform": "TikTok", "followers": bad})


def test_create_requires_fields_and_prospect_status(store):
    with pytest.raises(ValidationError):
        store.create_influencer({"platform": "TikTok", "followers": 1500})
    with pytest.raises(ValidationError):
        store.create_influencer({
            "account": "a", "platform": "TikTok", "followers": 1500,
            "relationship_status": "Outreach"})


def test_create_duplicate_account_same_platform(store):
    store.create_influencer({"account": "alice", "platform": "Instagram",
                             "followers": 5000})
    with pytest.raises(ValidationError):
        store.create_influencer({"account": "@ALICE", "platform": "Instagram",
                                 "followers": 5000})
    # 不同平台不冲突
    store.create_influencer({"account": "alice", "platform": "TikTok",
                             "followers": 5000})


def test_update_validation_and_unknown_fields(store):
    store.create_influencer({"account": "alice", "platform": "Instagram",
                             "followers": 5000})
    with pytest.raises(ValidationError) as exc:
        store.update_influencer(1, {"followers": "lots", "tier": "Nano"})
    assert any("整数" in e for e in exc.value.errors)
    assert any("只读" in e for e in exc.value.errors)
    with pytest.raises(ValidationError):
        store.update_influencer(1, {"unknown_field": 1})
    with pytest.raises(NotFoundError):
        store.update_influencer(999, {"city": "LA"})
    with pytest.raises(NotFoundError):
        store.get_influencer(999)


def test_update_account_uniqueness(store):
    store.create_influencer({"account": "alice", "platform": "Instagram",
                             "followers": 5000})
    store.create_influencer({"account": "bob", "platform": "Instagram",
                             "followers": 5000})
    with pytest.raises(ValidationError):
        store.update_influencer(2, {"account": "alice"})
    # 只改大小写允许
    inf = store.update_influencer(1, {"account": "Alice"})
    assert inf.account == "Alice"


def test_status_machine_full_chain(store):
    store.create_influencer({"account": "alice", "platform": "Instagram",
                             "followers": 5000})
    def move(status):
        return store.update_influencer(
            1, {"relationship_status": status}).relationship_status

    assert move("Outreach") is S.OUTREACH
    assert move("Active Ambassador") is S.ACTIVE_AMBASSADOR
    assert move("Paused") is S.PAUSED
    assert move("Active Ambassador") is S.ACTIVE_AMBASSADOR  # 互转
    assert move("Churned") is S.CHURNED                      # 任意 → Churned
    assert move("Prospect") is S.PROSPECT                    # Churned → Prospect
    with pytest.raises(ValidationError):
        move("Paused")  # Prospect 不能到 Paused
    with pytest.raises(ValidationError):
        store.update_influencer(
            1, {"relationship_status": "Active Ambassador"})  # 跳步


def test_tags_limit_and_dictionary_on_single_ops(store):
    store.create_influencer({"account": "alice", "platform": "Instagram",
                             "followers": 5000})
    with pytest.raises(ValidationError):
        store.update_influencer(1, {"tags": ["NoSuchTag"]})
    from conftest import TAGS
    with pytest.raises(ValidationError):
        store.update_influencer(1, {"tags": TAGS[:11]})  # 超过 10 个
    inf = store.update_influencer(1, {"tags": TAGS[:10]})
    assert len(inf.tags) == 10


def test_dictionary_persisted_and_checked(tmp_path):
    from influencer_sync import Store, TagDictionary
    db = str(tmp_path / "x.db")
    Store(db, TagDictionary(["Beauty"])).close()
    Store(db, TagDictionary(["beauty"])).close()  # 大小写不同也算一致
    Store(db).close()  # 不传字典：用库里的
    with pytest.raises(ValueError):
        Store(db, TagDictionary(["Other"]))
