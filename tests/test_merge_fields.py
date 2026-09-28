"""逐字段三方比较：同字段冲突/同值、不同字段各改各的、与单条修改并发。"""

import threading

from influencer_sync import export_influencers, import_influencers
from influencer_sync.report import RowOutcome

from conftest import export_rows


def test_both_sides_change_same_field_to_different_values(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000,
                    city="NYC")
    payload = export_influencers(store)
    payload["rows"][0]["city"] = "LA"          # 文件侧改
    store.update_influencer(1, {"city": "SF"})  # 系统侧改

    report = import_influencers(store, payload)

    row = report.rows[0]
    assert row.outcome == RowOutcome.CONFLICT
    assert len(row.conflicts) == 1
    conflict = row.conflicts[0]
    assert conflict.field == "city"
    assert conflict.baseline == "NYC"
    assert conflict.file_value == "LA"
    assert conflict.system_value == "SF"
    assert store.get_influencer(1).city == "SF"  # 冲突保留系统值


def test_both_sides_change_same_field_to_same_value(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000,
                    city="NYC")
    payload = export_influencers(store)
    payload["rows"][0]["city"] = "LA"
    store.update_influencer(1, {"city": "LA"})  # 两边改成同一个值

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UNCHANGED
    assert report.rows[0].conflicts == []
    assert store.get_influencer(1).city == "LA"


def test_both_sides_change_different_fields(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000,
                    city="NYC", state="NY")
    payload = export_influencers(store)
    payload["rows"][0]["city"] = "LA"                    # 文件改城市
    store.update_influencer(1, {"state": "CA"})          # 系统改州

    report = import_influencers(store, payload)

    row = report.rows[0]
    assert row.outcome == RowOutcome.UPDATED
    assert row.applied_fields == ["city"]
    assert row.conflicts == []
    inf = store.get_influencer(1)
    assert inf.city == "LA" and inf.state == "CA"  # 两边的改动都保留


def test_file_unchanged_keeps_system_changes(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000)
    payload = export_influencers(store)
    store.update_influencer(1, {"followers": 7000, "city": "SF"})

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UNCHANGED
    inf = store.get_influencer(1)
    assert inf.followers == 7000 and inf.city == "SF"


def test_conflict_does_not_block_other_fields_in_same_row(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000,
                    city="NYC", state="NY")
    payload = export_influencers(store)
    payload["rows"][0]["city"] = "LA"   # 冲突
    payload["rows"][0]["state"] = "NJ"  # 系统没改，应生效
    store.update_influencer(1, {"city": "SF"})

    report = import_influencers(store, payload)

    row = report.rows[0]
    assert row.outcome == RowOutcome.CONFLICT
    assert "state" in row.applied_fields
    inf = store.get_influencer(1)
    assert inf.city == "SF" and inf.state == "NJ"


def test_import_and_single_update_on_same_influencer(store, make_influencer):
    """导入与单条修改并发改同一位达人：结果等价于两者串行。"""
    make_influencer(account="alice", platform="Instagram", followers=5000,
                    city="NYC")
    payload = export_influencers(store)
    payload["rows"][0]["city"] = "LA"

    errors = []

    def single_update():
        try:
            for _ in range(20):
                store.update_influencer(1, {"state": "CA"})
                store.update_influencer(1, {"state": "NY"})
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=single_update) for _ in range(3)]
    for t in threads:
        t.start()
    report = import_influencers(store, payload)
    for t in threads:
        t.join()

    assert not errors
    inf = store.get_influencer(1)
    # 导入整体原子：城市要么被导入改成 LA，要么导入时系统已改过城市
    # （此处系统只改 state，不碰 city，所以导入后 city 必为 LA）
    assert inf.city == "LA"
    assert inf.state in ("CA", "NY")
    assert report.rows[0].outcome in (RowOutcome.UPDATED, RowOutcome.CONFLICT)


def test_status_conflict_keeps_system_and_validates_nothing(store, make_influencer):
    """状态两边改得不同 → 冲突保留系统值，不校验文件侧的流转。"""
    make_influencer(account="alice", platform="Instagram", followers=5000)
    payload = export_influencers(store)
    # 文件侧：Prospect → Active Ambassador（本身是不合法流转，但不会被采用）
    payload["rows"][0]["relationship_status"] = "Active Ambassador"
    # 系统侧：Prospect → Outreach
    store.update_influencer(1, {"relationship_status": "Outreach"})

    report = import_influencers(store, payload)

    row = report.rows[0]
    assert row.outcome == RowOutcome.CONFLICT
    assert store.get_influencer(1).relationship_status.value == "Outreach"


def test_status_adopted_change_must_be_legal_transition(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000)
    payload = export_influencers(store)
    payload["rows"][0]["relationship_status"] = "Active Ambassador"  # 跳步

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.REJECTED
    assert any("直接变为" in r for r in report.rows[0].reasons)
    assert store.get_influencer(1).relationship_status.value == "Prospect"


def test_status_legal_transition_applied(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000)
    payload = export_influencers(store)
    payload["rows"][0]["relationship_status"] = "Outreach"

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UPDATED
    assert store.get_influencer(1).relationship_status.value == "Outreach"
