"""标签：集合三方合并、字典校验、大小写/顺序/重复不敏感、上限拒绝。"""

from influencer_sync import export_influencers, import_influencers
from influencer_sync.report import RowOutcome

BASE_TAGS = ["Beauty", "Travel", "Food"]


def _setup(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000,
                    tags=list(BASE_TAGS))
    return export_influencers(store)


def test_file_adds_and_removes_tags(store, make_influencer):
    payload = _setup(store, make_influencer)
    payload["rows"][0]["tags"] = ["Beauty", "Fitness"]  # 删 Travel/Food，加 Fitness

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UPDATED
    assert {t.lower() for t in store.get_influencer(1).tags} == {"beauty", "fitness"}


def test_system_adds_and_removes_tags_file_untouched(store, make_influencer):
    payload = _setup(store, make_influencer)
    store.update_influencer(1, {"tags": ["Beauty", "Tech"]})

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UNCHANGED
    assert {t.lower() for t in store.get_influencer(1).tags} == {"beauty", "tech"}


def test_both_sides_add_and_remove_merged(store, make_influencer):
    payload = _setup(store, make_influencer)
    # 文件：删 Food，加 Fitness
    payload["rows"][0]["tags"] = ["Beauty", "Travel", "Fitness"]
    # 系统：删 Travel，加 Tech
    store.update_influencer(1, {"tags": ["Beauty", "Food", "Tech"]})

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UPDATED
    # 合并：Beauty 都保留；Travel 被系统删；Food 被文件删；Fitness/Tech 各加
    assert {t.lower() for t in store.get_influencer(1).tags} == {
        "beauty", "fitness", "tech"}


def test_tag_order_case_and_duplicates_are_not_changes(store, make_influencer):
    payload = _setup(store, make_influencer)
    payload["rows"][0]["tags"] = ["food", "BEAUTY", "travel", "Food"]

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UNCHANGED
    assert store.get_influencer(1).tags == BASE_TAGS


def test_merged_tags_over_limit_rejects_row(store, make_influencer):
    payload = _setup(store, make_influencer)
    # 文件加 4 个，系统加 4 个，基准 3 个 → 合并 3+4+4=11 > 10
    payload["rows"][0]["tags"] = BASE_TAGS + ["Fitness", "Tech", "Gaming", "Fashion"]
    store.update_influencer(
        1, {"tags": BASE_TAGS + ["Music", "Art", "Sports", "Books"]})

    report = import_influencers(store, payload)

    row = report.rows[0]
    assert row.outcome == RowOutcome.REJECTED
    assert any("超过" in r for r in row.reasons)
    # 整行拒绝：标签保持系统原值
    assert store.get_influencer(1).tags == BASE_TAGS + [
        "Music", "Art", "Sports", "Books"]


def test_exactly_ten_tags_allowed(store, make_influencer):
    payload = _setup(store, make_influencer)
    payload["rows"][0]["tags"] = BASE_TAGS + [
        "Fitness", "Tech", "Gaming", "Fashion", "Music", "Art", "Sports"]

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UPDATED
    assert len(store.get_influencer(1).tags) == 10


def test_tag_not_in_dictionary_rejects_row(store, make_influencer):
    payload = _setup(store, make_influencer)
    payload["rows"][0]["tags"] = ["Beauty", "NotATag"]

    report = import_influencers(store, payload)

    row = report.rows[0]
    assert row.outcome == RowOutcome.REJECTED
    assert any("不在字典" in r for r in row.reasons)
    assert store.get_influencer(1).tags == BASE_TAGS


def test_clear_tags_with_null_and_empty_string(store, make_influencer):
    payload = _setup(store, make_influencer)
    payload["rows"][0]["tags"] = None
    report = import_influencers(store, payload)
    assert report.rows[0].outcome == RowOutcome.UPDATED
    assert store.get_influencer(1).tags == []

    store.update_influencer(1, {"tags": ["Beauty"]})
    payload2 = export_influencers(store)
    payload2["rows"][0]["tags"] = ""
    report2 = import_influencers(store, payload2)
    assert report2.rows[0].outcome == RowOutcome.UPDATED
    assert store.get_influencer(1).tags == []


def test_deleted_tags_field_means_untouched(store, make_influencer):
    payload = _setup(store, make_influencer)
    del payload["rows"][0]["tags"]

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UNCHANGED
    assert store.get_influencer(1).tags == BASE_TAGS
