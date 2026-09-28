"""账号：规范化比较、互换合法、抢占拒绝与连锁拒绝、跨平台不冲突。"""

from influencer_sync import export_influencers, import_influencers
from influencer_sync.report import RowOutcome


def _two(store, make_influencer):
    make_influencer(account="alice", platform="Instagram", followers=5000)
    make_influencer(account="bob", platform="Instagram", followers=6000)
    return export_influencers(store)


def test_swap_accounts_is_legal(store, make_influencer):
    payload = _two(store, make_influencer)
    payload["rows"][0]["account"] = "bob"
    payload["rows"][1]["account"] = "alice"

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UPDATED
    assert report.rows[1].outcome == RowOutcome.UPDATED
    assert store.get_influencer(1).account == "bob"
    assert store.get_influencer(2).account == "alice"


def test_swap_with_at_and_case_only_is_not_a_change(store, make_influencer):
    payload = _two(store, make_influencer)
    payload["rows"][0]["account"] = "@ALICE"  # 只改大小写加 @：不算改动

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UNCHANGED
    assert store.get_influencer(1).account == "alice"


def test_swap_blocked_when_other_row_rejected(store, make_influencer):
    """互换中一方因别的原因被拒 → 另一方跟着被拒，双方保持原账号。"""
    payload = _two(store, make_influencer)
    payload["rows"][0]["account"] = "bob"          # alice → bob
    payload["rows"][1]["account"] = "alice"        # bob → alice
    payload["rows"][1]["followers"] = 99999        # 非法：整行拒绝

    report = import_influencers(store, payload)

    assert report.rows[1].outcome == RowOutcome.REJECTED
    assert any("粉丝数" in r for r in report.rows[1].reasons)
    assert report.rows[0].outcome == RowOutcome.REJECTED
    assert any("编号 2" in r for r in report.rows[0].reasons)
    assert store.get_influencer(1).account == "alice"
    assert store.get_influencer(2).account == "bob"


def test_multiple_rows_grab_same_account_all_rejected(store, make_influencer):
    payload = _two(store, make_influencer)
    payload["rows"][0]["account"] = "carol"
    payload["rows"][1]["account"] = "@Carol"  # 规范化后相同

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.REJECTED
    assert report.rows[1].outcome == RowOutcome.REJECTED
    assert any("第 2 行" in r for r in report.rows[0].reasons)
    assert any("第 1 行" in r for r in report.rows[1].reasons)
    assert store.get_influencer(1).account == "alice"
    assert store.get_influencer(2).account == "bob"


def test_grab_account_not_being_moved_away(store, make_influencer):
    payload = _two(store, make_influencer)
    payload["rows"][0]["account"] = "bob"  # bob 没改走 → 抢占

    report = import_influencers(store, payload)

    row = report.rows[0]
    assert row.outcome == RowOutcome.REJECTED
    assert any("编号 2" in r for r in row.reasons)
    assert report.rows[1].outcome == RowOutcome.UNCHANGED
    assert store.get_influencer(1).account == "alice"


def test_grab_account_of_influencer_not_in_file(store, make_influencer):
    payload = _two(store, make_influencer)
    # 文件只保留 alice 的行，bob 的行被删掉（不动 bob）
    payload["rows"] = [payload["rows"][0]]
    payload["rows"][0]["account"] = "bob"

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.REJECTED
    assert any("编号 2" in r for r in report.rows[0].reasons)
    assert store.get_influencer(1).account == "alice"
    assert store.get_influencer(2).account == "bob"


def test_chain_move_is_legal(store, make_influencer):
    """a→b, b→c（c 是空账号）：链式让位合法。"""
    payload = _two(store, make_influencer)
    payload["rows"][0]["account"] = "bob"
    payload["rows"][1]["account"] = "carol"

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UPDATED
    assert report.rows[1].outcome == RowOutcome.UPDATED
    assert store.get_influencer(1).account == "bob"
    assert store.get_influencer(2).account == "carol"


def test_new_row_grabs_existing_account(store, make_influencer):
    payload = _two(store, make_influencer)
    payload["rows"].append({"account": "alice", "platform": "Instagram",
                            "followers": 1500})

    report = import_influencers(store, payload)

    assert report.rows[2].outcome == RowOutcome.REJECTED
    assert any("编号 1" in r for r in report.rows[2].reasons)
    assert len(store.list_influencers()) == 2


def test_two_new_rows_same_account_both_rejected(store, make_influencer):
    payload = _two(store, make_influencer)
    payload["rows"].append({"account": "carol", "platform": "TikTok",
                            "followers": 1500})
    payload["rows"].append({"account": "@CAROL", "platform": "TikTok",
                            "followers": 1600})

    report = import_influencers(store, payload)

    assert report.rows[2].outcome == RowOutcome.REJECTED
    assert report.rows[3].outcome == RowOutcome.REJECTED
    assert len(store.list_influencers()) == 2


def test_same_account_different_platforms_ok(store, make_influencer):
    payload = _two(store, make_influencer)
    payload["rows"].append({"account": "alice", "platform": "TikTok",
                            "followers": 1500})

    report = import_influencers(store, payload)

    assert report.rows[2].outcome == RowOutcome.CREATED


def test_invalid_account_chars_rejected(store, make_influencer):
    payload = _two(store, make_influencer)
    payload["rows"][0]["account"] = "bad account!"
    payload["rows"][1]["account"] = "x" * 31

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.REJECTED
    assert report.rows[1].outcome == RowOutcome.REJECTED
    assert store.get_influencer(1).account == "alice"
    assert store.get_influencer(2).account == "bob"


def test_account_case_only_rename_via_system_then_import(store, make_influencer):
    """系统侧只改了大小写，文件没动 → 不算冲突，保留系统写法。"""
    payload = _two(store, make_influencer)
    store.update_influencer(1, {"account": "Alice"})

    report = import_influencers(store, payload)

    assert report.rows[0].outcome == RowOutcome.UNCHANGED
    assert store.get_influencer(1).account == "Alice"
