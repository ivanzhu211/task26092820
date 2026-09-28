"""导入流程：新旧导出顺序、篡改拒绝、重复导入幂等、新建行、层级只读。"""

import json

import pytest

from influencer_sync import export_influencers, import_influencers
from influencer_sync.exceptions import ImportFileError
from influencer_sync.report import RowOutcome


def _one(store, make_influencer, **overrides):
    data = {"account": "alice", "platform": "Instagram", "followers": 5000}
    data.update(overrides)
    make_influencer(**data)
    return export_influencers(store)


def test_import_newer_export_then_older_export(store, make_influencer):
    """先导入新导出、再导入旧导出：旧文件相对其基准没改的字段不覆盖新值。"""
    make_influencer(account="alice", platform="Instagram", followers=5000,
                    city="NYC", state="NY")
    old_payload = export_influencers(store)          # 旧导出（基准 NYC/NY）

    store.update_influencer(1, {"city": "SF"})
    new_payload = export_influencers(store)          # 新导出（基准 SF/NY）
    new_payload["rows"][0]["state"] = "CA"           # 新文件里改州
    report_new = import_influencers(store, new_payload)
    assert report_new.rows[0].outcome == RowOutcome.UPDATED
    assert store.get_influencer(1).state == "CA"

    # 旧文件里只把粉丝数改成 7000（城市还是基准 NYC，州还是基准 NY）
    old_payload["rows"][0]["followers"] = 7000
    report_old = import_influencers(store, old_payload)

    row = report_old.rows[0]
    assert row.outcome == RowOutcome.UPDATED
    assert row.applied_fields == ["followers"]
    inf = store.get_influencer(1)
    assert inf.followers == 7000
    assert inf.city == "SF"   # 旧文件没改城市（相对其基准）→ 保留新值
    assert inf.state == "CA"  # 旧文件没改州 → 保留新值


def test_old_export_with_edit_conflicts_against_newer_state(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000,
                    city="NYC")
    old_payload = export_influencers(store)
    store.update_influencer(1, {"city": "SF"})
    new_payload = export_influencers(store)
    import_influencers(store, new_payload)  # 新文件无改动

    old_payload["rows"][0]["city"] = "LA"  # 旧文件改了城市，系统也改过 → 冲突
    report = import_influencers(store, old_payload)

    row = report.rows[0]
    assert row.outcome == RowOutcome.CONFLICT
    assert row.conflicts[0].baseline == "NYC"
    assert row.conflicts[0].file_value == "LA"
    assert row.conflicts[0].system_value == "SF"
    assert store.get_influencer(1).city == "SF"


def test_tampered_id_rejected(store, make_influencer):
    payload = _one(store, make_influencer)
    payload["rows"][0]["id"] = 999  # 篡改 id，checksum 对不上

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.REJECTED
    assert any("被改过" in r for r in report.rows[0].reasons)


def test_tampered_baseline_rejected(store, make_influencer):
    payload = _one(store, make_influencer)
    payload["rows"][0]["_meta"]["baseline"]["city"] = "FAKE"

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.REJECTED
    assert any("被改过" in r for r in report.rows[0].reasons)


def test_missing_meta_rejected(store, make_influencer):
    payload = _one(store, make_influencer)
    del payload["rows"][0]["_meta"]

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.REJECTED


def test_reimport_same_file_is_noop(store, make_influencer):
    payload = _one(store, make_influencer, city="NYC")
    payload["rows"][0]["city"] = "LA"
    payload["rows"].append({"account": "carol", "platform": "TikTok",
                            "followers": 1500, "tags": ["Beauty"]})

    report1 = import_influencers(store, payload)
    assert report1.rows[0].outcome == RowOutcome.UPDATED
    assert report1.rows[1].outcome == RowOutcome.CREATED

    snapshot = [(i.id, i.version, i.account, i.city, i.tags)
                for i in store.list_influencers()]
    report2 = import_influencers(store, payload)
    snapshot2 = [(i.id, i.version, i.account, i.city, i.tags)
                 for i in store.list_influencers()]

    assert snapshot == snapshot2  # 不产生任何数据变化
    assert report2.rows[0].outcome == RowOutcome.UNCHANGED
    assert report2.rows[1].outcome == RowOutcome.REJECTED  # 账号已存在


def test_new_row_requires_account_platform_followers(store, make_influencer):
    payload = _one(store, make_influencer)
    payload["rows"] = [
        {"platform": "TikTok", "followers": 1500},            # 缺账号
        {"account": "bob", "followers": 1500},                # 缺平台
        {"account": "carol", "platform": "TikTok"},           # 缺粉丝数
        {"account": "dan", "platform": "TikTok", "followers": 1500},
    ]

    report = import_influencers(store, payload)

    assert [r.outcome for r in report.rows] == [
        RowOutcome.REJECTED, RowOutcome.REJECTED,
        RowOutcome.REJECTED, RowOutcome.CREATED,
    ]
    for row in report.rows[:3]:
        assert any("必填" in r for r in row.reasons)


def test_new_row_status_must_be_prospect_or_absent(store, make_influencer):
    payload = _one(store, make_influencer)
    payload["rows"] = [
        {"account": "bob", "platform": "TikTok", "followers": 1500,
         "relationship_status": "Outreach"},
        {"account": "carol", "platform": "TikTok", "followers": 1500},
    ]

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.REJECTED
    assert any("Prospect" in r for r in report.rows[0].reasons)
    assert report.rows[1].outcome == RowOutcome.CREATED
    assert store.get_influencer(
        report.rows[1].influencer_id).relationship_status.value == "Prospect"


def test_same_influencer_multiple_rows_all_rejected(store, make_influencer):
    payload = _one(store, make_influencer)
    row2 = json.loads(json.dumps(payload["rows"][0]))
    row2["city"] = "LA"
    payload["rows"].append(row2)

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.REJECTED
    assert report.rows[1].outcome == RowOutcome.REJECTED
    assert any("多行" in r for r in report.rows[0].reasons)


def test_tier_change_ignored_with_notice(store, make_influencer):
    payload = _one(store, make_influencer)
    payload["rows"][0]["tier"] = "Nano"
    payload["rows"][0]["city"] = "LA"

    report = import_influencers(store, payload)

    row = report.rows[0]
    assert row.outcome == RowOutcome.UPDATED
    assert any("只读" in n for n in row.notices)
    assert store.get_influencer(1).tier.value == "Micro"  # 层级不生效


def test_tier_change_alone_still_reports_unchanged(store, make_influencer):
    payload = _one(store, make_influencer)
    payload["rows"][0]["tier"] = "Nano"

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UNCHANGED
    assert any("只读" in n for n in report.rows[0].notices)


def test_deleted_row_means_untouched(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000)
    make_influencer(account="bob", platform="Instagram", followers=6000)
    payload = export_influencers(store)
    payload["rows"] = [payload["rows"][0]]  # 删掉 bob 的行

    report = import_influencers(store, payload)

    assert len(report.rows) == 1
    assert store.get_influencer(2).account == "bob"


def test_all_invalid_values_listed_together(store, make_influencer):
    payload = _one(store, make_influencer)
    payload["rows"][0]["account"] = "bad account!"
    payload["rows"][0]["followers"] = 10
    payload["rows"][0]["tags"] = ["NotATag"]
    payload["rows"][0]["platform"] = "MySpace"

    report = import_influencers(store, payload)

    row = report.rows[0]
    assert row.outcome == RowOutcome.REJECTED
    assert len(row.reasons) >= 4  # 错误全部列出
    assert any("账号" in r for r in row.reasons)
    assert any("粉丝数" in r for r in row.reasons)
    assert any("不在字典" in r for r in row.reasons)
    assert any("平台" in r for r in row.reasons)


def test_clear_city_state_with_null_or_empty(store, make_influencer):
    payload = _one(store, make_influencer, city="NYC", state="NY")
    payload["rows"][0]["city"] = None
    payload["rows"][0]["state"] = ""

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UPDATED
    inf = store.get_influencer(1)
    assert inf.city is None and inf.state is None


def test_null_on_non_clearable_field_rejected(store, make_influencer):
    payload = _one(store, make_influencer)
    payload["rows"][0]["account"] = None
    payload["rows"][0]["followers"] = ""

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.REJECTED


def test_file_level_errors(store, make_influencer):
    _one(store, make_influencer)
    with pytest.raises(ImportFileError):
        import_influencers(store, "not json")
    with pytest.raises(ImportFileError):
        import_influencers(store, {"format": "wrong", "version": 1, "rows": []})
    with pytest.raises(ImportFileError):
        import_influencers(store, {"format": "influencer-roster",
                                   "version": 99, "rows": []})
    big = export_influencers(store)
    big["rows"] = big["rows"] * 1001
    with pytest.raises(ImportFileError):
        import_influencers(store, big)


def test_export_by_status_filter(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000)
    make_influencer(account="bob", platform="Instagram", followers=6000)
    store.update_influencer(2, {"relationship_status": "Outreach"})

    payload = export_influencers(store, "Outreach")

    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["account"] == "bob"


def test_row_results_follow_file_order(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000)
    make_influencer(account="bob", platform="Instagram", followers=6000)
    payload = export_influencers(store)
    payload["rows"] = list(reversed(payload["rows"]))  # 乱序
    payload["rows"][0]["city"] = "LA"  # bob 的行

    report = import_influencers(store, payload)

    assert [r.file_line for r in report.rows] == [1, 2]
    assert report.rows[0].influencer_id == 2
    assert report.rows[0].outcome == RowOutcome.UPDATED
    assert report.rows[1].outcome == RowOutcome.UNCHANGED
    assert store.get_influencer(2).city == "LA"
