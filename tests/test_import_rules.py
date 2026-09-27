"""账号唯一性（互换/抢占/连锁拒绝）、新建行、重复身份、篡改、幂等、新旧导出。"""
from __future__ import annotations

import json

import pytest

from roster import InfluencerPatch, Platform, RowResult, Status

from helpers import add, export_doc, import_doc, make_service


def test_account_swap_is_legal():
    svc = make_service()
    a = add(svc, "alpha")
    b = add(svc, "beta")
    doc = export_doc(svc)
    doc["rows"][0]["account"] = "beta"
    doc["rows"][1]["account"] = "alpha"
    report = import_doc(svc, doc)
    assert [r.result for r in report.rows] == [RowResult.UPDATED, RowResult.UPDATED]
    assert svc.get_influencer(a.id).account == "beta"
    assert svc.get_influencer(b.id).account == "alpha"
    svc.close()


def test_swap_cascades_when_one_side_rejected():
    svc = make_service()
    a = add(svc, "alpha")
    b = add(svc, "beta")
    doc = export_doc(svc)
    doc["rows"][0]["account"] = "beta"
    doc["rows"][0]["followers"] = 5   # a 行因粉丝数非法被拒 -> alpha 没让出
    doc["rows"][1]["account"] = "alpha"
    report = import_doc(svc, doc)
    assert report.rows[0].result == RowResult.REJECTED
    assert report.rows[1].result == RowResult.REJECTED
    assert any("#%d" % a.id in r for r in report.rows[1].reject_reasons)
    assert svc.get_influencer(a.id).account == "alpha"
    assert svc.get_influencer(b.id).account == "beta"
    svc.close()


def test_multiple_rows_grabbing_same_account_all_rejected():
    svc = make_service()
    a = add(svc, "alpha")
    b = add(svc, "beta")
    doc = export_doc(svc)
    doc["rows"][0]["account"] = "shared"
    doc["rows"][1]["account"] = "@Shared"   # 规范化后相同
    report = import_doc(svc, doc)
    assert [r.result for r in report.rows] == [RowResult.REJECTED, RowResult.REJECTED]
    assert any("第 2 行" in r for r in report.rows[0].reject_reasons)
    assert any("第 1 行" in r for r in report.rows[1].reject_reasons)
    assert svc.get_influencer(a.id).account == "alpha"
    assert svc.get_influencer(b.id).account == "beta"
    svc.close()


def test_grabbing_account_not_vacated_is_rejected():
    svc = make_service()
    a = add(svc, "alpha")
    b = add(svc, "beta")
    doc = export_doc(svc)
    doc["rows"][1]["account"] = "ALPHA"   # b 抢 a 的账号（仅大小写差异也算同一个）
    report = import_doc(svc, doc)
    assert report.rows[1].result == RowResult.REJECTED
    assert any("#%d" % a.id in r for r in report.rows[1].reject_reasons)
    assert svc.get_influencer(b.id).account == "beta"
    svc.close()


def test_case_or_at_only_account_change_is_noop():
    svc = make_service()
    a = add(svc, "alpha")
    doc = export_doc(svc)
    doc["rows"][0]["account"] = "@Alpha"
    report = import_doc(svc, doc)
    assert report.rows[0].result == RowResult.NO_CHANGE
    assert svc.get_influencer(a.id).account == "alpha"
    svc.close()


def test_new_row_rules():
    svc = make_service()
    add(svc, "alpha")
    doc = export_doc(svc)
    doc["rows"].append({"account": "newbie", "platform": "TikTok", "followers": 30000})
    doc["rows"].append({"account": "nostatus", "platform": "TikTok", "followers": 3000,
                        "status": "Active Ambassador"})
    doc["rows"].append({"platform": "TikTok", "followers": 3000})  # 缺账号
    report = import_doc(svc, doc)
    assert report.rows[1].result == RowResult.CREATED
    created = svc.get_influencer(report.rows[1].influencer_id)
    assert created.status == Status.PROSPECT and created.tier.value == "Mid-Micro"
    assert report.rows[2].result == RowResult.REJECTED   # 状态只能 Prospect
    assert report.rows[3].result == RowResult.REJECTED   # 缺必填
    assert any("account" in r for r in report.rows[3].reject_reasons)
    svc.close()


def test_same_influencer_multiple_rows_all_rejected():
    svc = make_service()
    a = add(svc, "alpha")
    doc = export_doc(svc)
    doc["rows"].append(dict(doc["rows"][0]))  # 同一 id 再来一行
    doc["rows"][0]["city"] = "X"
    doc["rows"][1]["city"] = "Y"
    report = import_doc(svc, doc)
    assert [r.result for r in report.rows] == [RowResult.REJECTED, RowResult.REJECTED]
    assert svc.get_influencer(a.id).city is None
    svc.close()


def test_tampered_system_content_rejected():
    svc = make_service()
    a = add(svc, "alpha", city="Paris")
    # 篡改基线
    doc = export_doc(svc)
    doc["rows"][0]["_meta"]["base"]["city"] = "London"
    report = import_doc(svc, doc)
    assert report.rows[0].result == RowResult.REJECTED
    assert any("篡改" in r for r in report.rows[0].reject_reasons)
    # 篡改 id
    doc = export_doc(svc)
    doc["rows"][0]["id"] = doc["rows"][0]["id"] + 1000
    report = import_doc(svc, doc)
    assert report.rows[0].result == RowResult.REJECTED
    # 删掉 _meta
    doc = export_doc(svc)
    del doc["rows"][0]["_meta"]
    report = import_doc(svc, doc)
    assert report.rows[0].result == RowResult.REJECTED
    assert svc.get_influencer(a.id).city == "Paris"
    svc.close()


def test_reimport_same_file_is_idempotent():
    svc = make_service()
    a = add(svc, "alpha", city="Paris")
    doc = export_doc(svc)
    doc["rows"][0]["city"] = "Berlin"
    payload = json.dumps(doc)
    report1 = svc.import_json(payload)
    assert report1.rows[0].result == RowResult.UPDATED
    before = svc.get_influencer(a.id)
    report2 = svc.import_json(payload)
    assert report2.rows[0].result == RowResult.NO_CHANGE
    after = svc.get_influencer(a.id)
    assert before == after
    svc.close()


def test_import_newer_export_then_older_export():
    svc = make_service()
    a = add(svc, "alpha", city="Paris", state="IDF")
    old_doc = export_doc(svc)                      # 旧导出
    svc.update_influencer(a.id, InfluencerPatch(city="Lyon"))
    new_doc = export_doc(svc)                      # 新导出（base 含 Lyon）
    new_doc["rows"][0]["state"] = "BE"
    report_new = import_doc(svc, new_doc)          # 先导入新的
    assert report_new.rows[0].result == RowResult.UPDATED
    old_doc["rows"][0]["city"] = "Marseille"       # 旧文件里用户改过城市
    report_old = import_doc(svc, old_doc)          # 再导入旧的
    row = report_old.rows[0]
    # 旧文件 base=Paris，file=Marseille，system=Lyon -> 冲突，保留系统值
    assert row.result == RowResult.CONFLICT
    got = svc.get_influencer(a.id)
    assert got.city == "Lyon" and got.state == "BE"
    svc.close()


def test_old_export_untouched_file_keeps_system_changes():
    svc = make_service()
    a = add(svc, "alpha", city="Paris")
    old_doc = export_doc(svc)
    svc.update_influencer(a.id, InfluencerPatch(city="Lyon", followers=9000))
    report = import_doc(svc, old_doc)   # 几个月前的导出，文件没改 -> 全部保留系统值
    assert report.rows[0].result == RowResult.NO_CHANGE
    got = svc.get_influencer(a.id)
    assert got.city == "Lyon" and got.followers == 9000
    svc.close()


def test_import_row_limit():
    svc = make_service()
    doc = export_doc(svc)
    doc["rows"] = [{"account": "u%d" % i, "platform": "TikTok", "followers": 2000}
                   for i in range(1001)]
    with pytest.raises(ValueError):
        import_doc(svc, doc)
    svc.close()


def test_export_filter_by_status():
    svc = make_service()
    add(svc, "alpha")
    b = add(svc, "beta")
    svc.update_influencer(b.id, InfluencerPatch(status=Status.OUTREACH))
    doc = export_doc(svc, status="Outreach")
    assert len(doc["rows"]) == 1 and doc["rows"][0]["id"] == b.id
    svc.close()
