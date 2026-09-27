"""逐字段三方合并、标签集合合并、状态迁移、层级只读、清空语义。"""
from __future__ import annotations

import json

from roster import InfluencerPatch, Platform, RowResult, Status

from helpers import add, export_doc, import_doc, make_service


def test_both_change_same_field_differently_is_conflict():
    svc = make_service()
    inf = add(svc, "alice", city="Paris")
    doc = export_doc(svc)
    svc.update_influencer(inf.id, InfluencerPatch(city="Lyon"))   # 系统改
    doc["rows"][0]["city"] = "Berlin"                            # 文件改
    report = import_doc(svc, doc)
    row = report.rows[0]
    assert row.result == RowResult.CONFLICT
    conflict = row.conflicts[0]
    assert (conflict.field, conflict.exported, conflict.file, conflict.system) == (
        "city", "Paris", "Berlin", "Lyon")
    assert svc.get_influencer(inf.id).city == "Lyon"             # 保留系统值
    svc.close()


def test_both_change_to_same_value_is_not_conflict():
    svc = make_service()
    inf = add(svc, "alice", city="Paris")
    doc = export_doc(svc)
    svc.update_influencer(inf.id, InfluencerPatch(city="Lyon"))
    doc["rows"][0]["city"] = "Lyon"
    report = import_doc(svc, doc)
    assert report.rows[0].result == RowResult.NO_CHANGE
    assert svc.get_influencer(inf.id).city == "Lyon"
    svc.close()


def test_file_changes_and_system_changes_different_fields():
    svc = make_service()
    inf = add(svc, "alice", city="Paris", followers=2000)
    doc = export_doc(svc)
    svc.update_influencer(inf.id, InfluencerPatch(followers=6000))  # 系统改粉丝数
    doc["rows"][0]["city"] = "Berlin"                               # 文件改城市
    report = import_doc(svc, doc)
    row = report.rows[0]
    assert row.result == RowResult.UPDATED
    assert row.adopted_fields == ["city"]
    got = svc.get_influencer(inf.id)
    assert got.city == "Berlin" and got.followers == 6000
    assert got.tier.value == "Micro"                                # 层级随系统改动
    svc.close()


def test_conflict_does_not_block_other_fields_in_same_row():
    svc = make_service()
    inf = add(svc, "alice", city="Paris", state="IDF")
    doc = export_doc(svc)
    svc.update_influencer(inf.id, InfluencerPatch(city="Lyon"))
    doc["rows"][0]["city"] = "Berlin"   # 冲突
    doc["rows"][0]["state"] = "BE"      # 系统没动 -> 采用
    report = import_doc(svc, doc)
    row = report.rows[0]
    assert row.result == RowResult.CONFLICT
    assert row.adopted_fields == ["state"]
    got = svc.get_influencer(inf.id)
    assert got.city == "Lyon" and got.state == "BE"
    svc.close()


def test_tag_set_merge_both_sides_add_and_remove():
    svc = make_service()
    inf = add(svc, "alice", tags=["fashion", "tech", "beauty"])
    doc = export_doc(svc)
    # 系统：删 beauty，加 sports
    svc.update_influencer(inf.id, InfluencerPatch(tags=["fashion", "tech", "sports"]))
    # 文件：删 tech，加 travel
    doc["rows"][0]["tags"] = ["fashion", "beauty", "travel"]
    report = import_doc(svc, doc)
    assert report.rows[0].result == RowResult.UPDATED
    got = svc.get_influencer(inf.id)
    # fashion 保留；tech 被文件删；beauty 被系统删；sports 系统加；travel 文件加
    assert sorted(got.tags) == ["fashion", "sports", "travel"]
    svc.close()


def test_tag_merge_over_limit_rejects_row():
    svc = make_service()
    inf = add(svc, "alice", tags=["fashion", "tech", "beauty", "sports", "travel", "food"])
    doc = export_doc(svc)
    svc.update_influencer(
        inf.id,
        InfluencerPatch(tags=["fashion", "tech", "beauty", "sports", "travel",
                              "food", "music", "gaming", "art"]),
    )
    doc["rows"][0]["tags"] = ["fashion", "tech", "beauty", "sports", "travel",
                              "food", "fitness", "books", "pets"]
    report = import_doc(svc, doc)
    row = report.rows[0]
    assert row.result == RowResult.REJECTED
    assert any("上限" in reason for reason in row.reject_reasons)
    assert len(svc.get_influencer(inf.id).tags) == 9  # 整行拒绝，系统值不变
    svc.close()


def test_tag_order_case_and_duplicates_are_not_changes():
    svc = make_service()
    inf = add(svc, "alice", tags=["fashion", "tech"])
    doc = export_doc(svc)
    doc["rows"][0]["tags"] = ["TECH", "Fashion", "tech"]
    report = import_doc(svc, doc)
    assert report.rows[0].result == RowResult.NO_CHANGE
    svc.close()


def test_tag_not_in_dictionary_rejects_row_with_all_errors():
    svc = make_service()
    inf = add(svc, "alice")
    doc = export_doc(svc)
    doc["rows"][0]["tags"] = ["fashion", "unknown1", "unknown2"]
    doc["rows"][0]["followers"] = 5  # 同时制造第二个错误
    report = import_doc(svc, doc)
    row = report.rows[0]
    assert row.result == RowResult.REJECTED
    assert any("unknown1" in r for r in row.reject_reasons)
    assert any("unknown2" in r for r in row.reject_reasons)
    assert any("粉丝数" in r for r in row.reject_reasons)
    svc.close()


def test_status_transition_validated_only_for_adopted_changes():
    svc = make_service()
    inf = add(svc, "alice")  # Prospect
    doc = export_doc(svc)
    # 文件试图 Prospect -> Active Ambassador（跨步），且系统未改 -> 会被采用 -> 拒绝
    doc["rows"][0]["status"] = "Active Ambassador"
    report = import_doc(svc, doc)
    assert report.rows[0].result == RowResult.REJECTED
    assert svc.get_influencer(inf.id).status == Status.PROSPECT

    # 系统已改成 Outreach，文件（基于旧导出）改 Active Ambassador：
    # 与系统不一致 -> 冲突而不采用 -> 不校验迁移，不拒绝
    svc.update_influencer(inf.id, InfluencerPatch(status=Status.OUTREACH))
    report2 = import_doc(svc, doc)
    assert report2.rows[0].result == RowResult.CONFLICT
    assert svc.get_influencer(inf.id).status == Status.OUTREACH

    # 合法单步：Outreach -> Active Ambassador
    doc2 = export_doc(svc)
    doc2["rows"][0]["status"] = "Active Ambassador"
    report3 = import_doc(svc, doc2)
    assert report3.rows[0].result == RowResult.UPDATED
    assert svc.get_influencer(inf.id).status == Status.ACTIVE
    svc.close()


def test_tier_change_in_file_is_ignored_but_noted():
    svc = make_service()
    inf = add(svc, "alice", followers=2000)  # Nano
    doc = export_doc(svc)
    doc["rows"][0]["tier"] = "Mid-Micro"
    report = import_doc(svc, doc)
    row = report.rows[0]
    assert row.result == RowResult.NO_CHANGE
    assert any("层级" in note for note in row.notes)
    assert svc.get_influencer(inf.id).tier.value == "Nano"
    svc.close()


def test_null_and_empty_clear_only_clearable_fields():
    svc = make_service()
    inf = add(svc, "alice", city="Paris", state="IDF", tags=["fashion"])
    doc = export_doc(svc)
    doc["rows"][0]["city"] = None
    doc["rows"][0]["state"] = ""
    doc["rows"][0]["tags"] = []
    report = import_doc(svc, doc)
    assert report.rows[0].result == RowResult.UPDATED
    got = svc.get_influencer(inf.id)
    assert got.city is None and got.state is None and got.tags == []

    # 不可清空字段
    doc2 = export_doc(svc)
    doc2["rows"][0]["account"] = ""
    doc2["rows"][0]["followers"] = None
    report2 = import_doc(svc, doc2)
    row = report2.rows[0]
    assert row.result == RowResult.REJECTED
    assert len(row.reject_reasons) == 2
    svc.close()


def test_missing_field_means_untouched():
    svc = make_service()
    inf = add(svc, "alice", city="Paris")
    doc = export_doc(svc)
    del doc["rows"][0]["city"]
    svc.update_influencer(inf.id, InfluencerPatch(city="Lyon"))
    report = import_doc(svc, doc)
    assert report.rows[0].result == RowResult.NO_CHANGE
    assert svc.get_influencer(inf.id).city == "Lyon"
    svc.close()
