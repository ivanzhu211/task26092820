"""性能：20万达人规模下，1000 行导入在 3 秒内完成。"""
from __future__ import annotations

import json
import time

from roster import RowResult

from helpers import export_doc, make_service


def test_import_1000_rows_with_200k_influencers(tmp_path):
    db = str(tmp_path / "perf.db")
    svc = make_service(db)
    conn = svc._store.conn
    rows = []
    for i in range(200_000):
        status = "Outreach" if i < 1000 else "Prospect"
        rows.append(("user%d" % i, "user%d" % i, "Instagram", 2000,
                     "Nano", "[]", None, None, status))
    conn.executemany(
        "INSERT INTO influencers "
        "(account, account_norm, platform, followers, tier, tags, city, state, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()

    doc = export_doc(svc, status="Outreach")
    assert len(doc["rows"]) == 1000
    for index, row in enumerate(doc["rows"]):
        row["city"] = "City%d" % index
        row["tags"] = ["fashion"]

    start = time.monotonic()
    report = svc.import_json(json.dumps(doc))
    elapsed = time.monotonic() - start
    assert report.counts["updated"] == 1000
    assert elapsed < 3.0, "导入耗时 %.2fs" % elapsed
    svc.close()
