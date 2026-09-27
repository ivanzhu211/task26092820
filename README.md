# roster — 达人名单导出/导回库

Python + SQLite 实现的达人名单库，供上层达人关系管理服务直接调用（无接口层）。

## 用法

```python
from roster import RosterService, InfluencerPatch, Platform, Status

svc = RosterService("roster.db", tag_dictionary=["fashion", "tech"])  # 字典最多 50 个

# 新建（账号/平台/粉丝数必填，状态只能 Prospect 或不填）
inf = svc.create_influencer(InfluencerPatch(
    account="@alice", platform=Platform.INSTAGRAM, followers=1200, tags=["fashion"]))

# 单条修改（与导入遵守同样的校验）
svc.update_influencer(inf.id, InfluencerPatch(city="Paris"))

# 按编号查看
svc.get_influencer(inf.id)

# 导出（全部或按关系状态过滤），返回 JSON 字符串
payload = svc.export_json(status="Prospect")

# 导回，返回逐行报告
report = svc.import_json(payload)
for row in report.rows:
    print(row.row, row.result, row.adopted_fields, row.conflicts, row.reject_reasons)
```

## 规则要点

- 层级由粉丝数决定（1000–4999 Nano，5000–24999 Micro，25000–50000 Mid-Micro），只读；
  文件中改了层级不生效，报告中以 notes 提示。
- 导出行的 `_meta` 含导出时基线与 HMAC 校验和；基线/id 被篡改的行整行拒绝。
- 逐字段三方合并：文件没改保留系统值；文件改了系统没改采用文件值；
  两边改一样不算冲突；改不一样算冲突、保留系统值，同行其他字段照常生效。
- 标签按集合三方合并（两边增删都保留），合并后超过 10 个整行拒绝；
  标签不分大小写、顺序与重复不算改动，必须在字典内。
- 账号忽略开头 `@`、不分大小写比较；同平台唯一，按整批生效后的结果判断
  （互换合法；多行抢一个或抢没被改走的，相关行拒绝并连锁重判直到稳定）。
- 关系状态单步迁移：Prospect→Outreach→Active Ambassador；Active Ambassador↔Paused；
  任意→Churned；Churned→Prospect。只校验真正会被采用的变更。
- `null`/空串表示清空，仅可用于城市、州、标签；缺行/缺字段表示不动。
- 无身份的行是新建；同一达人出现多行则全部拒绝；单次导入最多 1000 行。

## 并发与存储

- 导入整体在一个 `BEGIN IMMEDIATE` 事务内完成，与单条修改按 SQLite 锁串行化：
  结果等同于导入整体发生在修改之前或之后，不会半生效。连接开了 WAL 与 busy_timeout。
- 合并所需基线随导出文件走，不落库，存储占用不随导出次数增长；旧导出可随时导回。
- 同一文件重复导入不产生数据变化（报告行为 `no_change`）。

## 测试

```bash
python3 -m pytest tests/ -q
```
