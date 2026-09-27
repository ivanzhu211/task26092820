"""导入与单条修改并发：结果等同于二者按某种顺序串行执行，不见半生效状态。"""
from __future__ import annotations

import json
import threading

from roster import InfluencerPatch, RowResult

from helpers import add, export_doc, make_service


def test_import_and_single_update_on_same_influencer(tmp_path):
    db = str(tmp_path / "roster.db")
    svc = make_service(db)
    influencers = [add(svc, "user%03d" % i, city="Paris") for i in range(50)]
    target = influencers[0]
    doc = export_doc(svc)
    for row in doc["rows"]:
        row["city"] = "FromFile"
    payload = json.dumps(doc)

    svc2 = make_service(db)  # 第二个连接，模拟另一个调用方
    outcome = {}

    def run_import():
        outcome["report"] = svc.import_json(payload)

    thread = threading.Thread(target=run_import)
    thread.start()
    # 在导入进行期间对同一批达人做单条修改（会按锁与导入串行化）
    svc2.update_influencer(target.id, InfluencerPatch(city="FromApi"))
    thread.join()

    report = outcome["report"]
    got = svc.get_influencer(target.id)
    # 无论谁先谁后，单条修改的值都必须最终可见
    assert got.city == "FromApi"
    row0 = report.rows[0]
    if row0.result == RowResult.CONFLICT:
        # 修改先于导入生效：文件值与系统值冲突，保留系统值
        conflict = row0.conflicts[0]
        assert conflict.system == "FromApi" and conflict.file == "FromFile"
    else:
        # 导入先整体生效，修改随后覆盖
        assert row0.result == RowResult.UPDATED
    # 其余行：导入完整生效，不存在半生效
    for inf in influencers[1:]:
        assert svc.get_influencer(inf.id).city == "FromFile"
    assert report.counts["rejected"] == 0
    svc.close()
    svc2.close()
