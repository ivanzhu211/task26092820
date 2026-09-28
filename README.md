# influencer_sync — 达人名单导出/导回库

Python + SQLite 实现的达人名单管理库，由上层达人关系管理服务直接调用（无接口层）。
解决"导出 JSON 批量修改后导回"场景下的数据覆盖问题：导回时按导出快照做
**逐字段三方合并**，导出后系统内的修改不会被悄悄冲掉。

## 快速开始

```python
from influencer_sync import (
    Store, TagDictionary,
    export_influencers, import_influencers,
)

# 初始化（首次建库必须给标签字典，最多 50 个；之后可不传）
store = Store("roster.db", TagDictionary(["Beauty", "Travel", "Food"]))

# 新建（账号、平台、粉丝数必填；状态只能是 Prospect）
inf = store.create_influencer({
    "account": "@alice", "platform": "Instagram", "followers": 5000,
    "tags": ["beauty"], "city": "NYC",
})

# 单条修改 / 按编号查看（与导入遵守同样的校验）
store.update_influencer(inf.id, {"relationship_status": "Outreach"})
store.get_influencer(inf.id)

# 导出（全部或按关系状态），返回可 json.dumps 的 dict
payload = export_influencers(store)                 # 全部
payload = export_influencers(store, "Outreach")     # 按状态

# 导回（dict 或 JSON 字符串均可），返回逐行报告
report = import_influencers(store, payload)
for row in report.rows:
    print(row.file_line, row.outcome, row.applied_fields,
          row.conflicts, row.reasons, row.notices)
```

## 业务规则要点

- **层级只读**：由粉丝数决定（1000–4999 Nano，5000–24999 Micro，
  25000–50000 Mid-Micro），范围外拒绝；文件里改层级不生效，报告中提示。
- **三方合并**：以导出时快照为基准，逐字段比较文件与系统当前值——
  文件没动保留系统值；系统没动采用文件值；两边改成同一个值不算冲突；
  改成不同值算冲突，保留系统值，同行其他字段照常生效。
- **标签集合合并**：双方的增删都生效；合并后超过 10 个整行拒绝；
  不分大小写，顺序和重复不算改动；必须在字典内。
- **账号唯一**：同一平台下唯一（去 `@` 后不分大小写）；整批判断，
  互换账号合法；抢账号的行拒绝并连锁重判，报告指明撞上哪行/哪位达人。
- **状态机**：Prospect→Outreach→Active Ambassador；
  Active Ambassador↔Paused；任意→Churned；Churned→Prospect。
  只校验真正会被采用的变更。
- **行语义**：删掉行/字段表示不动；`null` 或空串表示清空（仅城市、州、
  标签）；无 `id` 的行为新建；同一达人多行全部拒绝；
  篡改 `id` 或导出快照（`_meta`）的行整行拒绝。

## 非功能设计

- **原子导入**：整批在单个 `BEGIN IMMEDIATE` 事务内完成，与并发单条修改
  串行化，任何时刻看不到只生效一半的导入。
- **无状态导出**：导出不在库里留任何历史，存储占用不随导出次数增长；
  几个月前的旧文件靠行内快照仍能正确三方合并。
- **幂等**：同一文件导入第二次不产生任何数据变化。
- **性能**：20 万达人规模下 1000 行导入 < 3 秒（见性能测试）。

## 测试

```bash
python3 -m pytest tests/ -q
```

覆盖：同字段/不同字段并发修改、标签增删与上限、账号互换与连锁拒绝、
导入与单条修改并发、新旧导出先后导入、篡改拒绝、重复导入幂等、
20 万规模性能。
