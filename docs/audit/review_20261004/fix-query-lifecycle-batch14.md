# 批次十四：配置并发写回与进程内连接池代际保护

工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-task-lifecycle`。开始基线 `057ce1fd7ec2137df732c6dd75c1ff90bf7119d1`；其他负责人同时推进本批内容，本代理未提交、未改主树或旧 `major-capability-contracts` 树。2026-10-04 实施。

仅实施 LIFE-N01/N02。LIFE-N03–05 的取消传播、查询租约与旁路治理不在本次已修范围。未连接业务 MySQL/PostgreSQL、真实模型、8011 或远程数据库；所有执行均经主树 Python 的隔离入口，平台和样例业务数据都只在一次性 SQLite 中。池工厂专项使用 Mock，引擎创建次数可以直接核对。

## 首次失败证据

`tests/backend/test_analytics_config_lifecycle.py` 初版 5 项，运行入口：

```powershell
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_analytics_config_lifecycle.py --junitxml=.implementation/lifecycle-before.xml
```

原始记录：`.implementation/lifecycle-before.log`、`.implementation/lifecycle-before.xml`，**5 failed / 11.11 秒**。

1. `inspection`：旧任务 A 已读取 v1 和列范围。独立请求 B 通过正式 PUT 收窄为仅 `sales.id` 并提交 v2；A 结构返回后竟将配置覆盖回 v1。
2. `embedding`：同样的正式 B 编辑发生在 A 向量回调期间；`semantic.build_semantic_index` 自身先整包写回，所以只保护 `metadata` 最后一步不足，实际仍从 v2 回退 v1。
3. `automatic_failure`：旧任务在 B 提交 v2 后失败，自动同步错误分支把新配置标为失败。
4. 空闲旧池：v1 已退役且回收后，迟到 v1 可以再次调用工厂，重新得到旧引擎。
5. 忙旧池：迟到 v1 即使最终被拒，也先把 v2 池退役并销毁。

平台夹具使用文件 SQLite、两个真实 Session 和不同 DBAPI 连接；通过引擎事件核对正式编辑所用物理连接不是旧任务连接。不是同一内存 StaticPool 的伪并发。顺序由结构/向量桩中的受控 HTTP 编辑形成，不依赖 sleep。

## 修复内容

- 新增 `profile_updates.py`：只允许画像派生字段；用数据库当前 JSON 的字段补丁，条件同时匹配工作区、来源 ID、原配置版本和 active 状态。成功后才刷新该对象的配置字段；不先 refresh 接受新版本，也不把旧字典整包写回。
- `metadata.py` 与 `semantic.py` 共享这一条件更新。索引的旧文档删除移动到条件校验之后，模型等待期间不提前写锁平台或删除旧索引。版本冲突回滚本次待保存目录及语义文档，抛 409。
- `sources.py` 自动失败状态沿用发起时版本。配置已经修改、停用或删除时，失败标记也被废弃；普通本版本连接失败仍明确显示 failed。
- `connection_pool.py` 将来源版本屏障与引擎条目分开保存，先检查代际再退役其他键。回收空池和容量淘汰不清除版本屏障。正式编辑/删除通过 `minimum_version` 记录新下限，尚未创建过的旧版本池也不能创建；迟到 v2 清理不退役已经存在的 v3。
- `connections.py` 把数字配置版本传给池；密码预览、写入、SQLite 的 NullPool 合同不变。

## 回归记录

| 阶段 | 原始证据 | 结果 | 代码快照说明 |
| --- | --- | --- | --- |
| 原始反例 | `lifecycle-before.xml` / `.log` | 5 failed，11.11 秒 | 产品未修改 |
| 核心修复 | `lifecycle-after-1.xml` / `.log` | 5 passed，10.57 秒 | 首版 CAS / 代际屏障 |
| 扩展专项与现有查询/池 | `lifecycle-after-2.xml` / `.log` | 95 passed，103.52 秒 | 22 新专项 + 16 池 + 57 analytics；该进程导入后又删去非必要 provider 谓词并新增一例，因此最终验证另记下一行 |
| 最终目录/治理相邻 | `lifecycle-after-3.xml` / `.log` | 40 passed，63.98 秒 | 最终冻结代码：23 新专项 + 13 semantic_relation_contract + 4 datasource_governance |

最终扩展覆盖四个时点（结构、向量、自动失败、惰性 cached_index）× 三种正式修改（收窄列、停用、删除）。另核对：B 自动同步已提交 v2 结构和索引后，A 迟到失败仍保留 v2 的真实字段；成功与失败正常合同；旧无 version 字段的 v1；同版本其他事务写入的无关字段保留；非画像字段拒绝；MySQL SQL 编译含 JSON 字段补丁与原子版本条件。

池专项验证新/旧版本、旧池忙闲两种情况、尚未建池、空闲回收、工作区隔离、迟到清理、真实工厂次数和当前引擎身份。原有回归另涵盖：只读连接归还、工作流批次复用、故障不重试、密码轮换、临时预览和写入绕过持久池。

## 必须保留的边界

1. **SQLite 与 MySQL RR 的证据不同。** 真正执行的是临时 SQLite 的独立事务受控交错和条件 UPDATE。MySQL 只验证生成的 SQL：版本谓词位于 UPDATE，因此依赖 MySQL 的当前读/行锁语义，不以 RR 快照 SELECT 当成新鲜度证明；本次没有连接真实 MySQL，不能宣称完成 MySQL RR 并发实测。
2. **N02 是同进程的正式配置编辑/删除屏障。** 跨进程旧 Session 在新 SQL 前的全局版本重验、广播失效没有实现，也没有被本批测试证明。已借出的操作按原合同完成、归还；本次未增加取消能力。
3. **同版本不同指纹。** 派生画像不参与连接指纹；普通画像更新继续复用原池。密码或连接参数产生不同指纹时，新指纹退役此前指纹，迟到的旧指纹不能在同一版本内重新启用。正式配置更改按 API 递增版本；下一版本清理旧版本指纹集合，旧版本仍被数字下限拒绝。
4. **屏障内存生命周期。** `_generations` 按本进程见过的 `(workspace_id, source_id)` 保留，不随 `MAX_POOLS` 空池回收而丢失；每条保存当前版本和本版本退役指纹，内存规模随已见来源/同版本指纹变化，不是连接数。全局关闭/显式注册表重置在无存活池时清空；进程重启自然清空。业务编辑/删除不会调用该全局重置。跨进程/重启后的全局权限保证仍需单独设计。
5. **没有扩大修复范围。** 没有修改 `queries.py`、`execution.py`、租约、取消、数值编码、图谱或 Bot。本报告的 95 项也不是全业务验收或所有并发时序穷举。

## 最终文件 SHA256

以下只列本代理文件，路径相对本独立树。

| 文件 | SHA256 |
| --- | --- |
| `backend/app/modules/analytics/metadata.py` | `048844FD2594B6065373F4217CF7174F120A0667E3B5646E7C9D5BDF50558A85` |
| `backend/app/modules/analytics/semantic.py` | `15DB17DE2CE4682A9BC660B5887F7C9770F5F35EC9BA2F5919029E7DDAF80E40` |
| `backend/app/modules/analytics/profile_updates.py` | `B34DE77B8A3D92F2F36AEFDA43EEDE7234B2E4521867591AF6A9275C42A6B6BF` |
| `backend/app/modules/analytics/sources.py` | `214BA25E8E1325E458A54034CCE6E0020D99443C956CA7E522765FA63E6C5B6A` |
| `backend/app/modules/analytics/connection_pool.py` | `69EC705D845C3671DC4C42B45075F1623839D0B602C161E5C3F091CACC28FA83` |
| `backend/app/modules/analytics/connections.py` | `BF20406D81747480DD430C2520CB9C90B2E6C1B661BE7A1BD6AF6718F0D6002B` |
| `tests/backend/test_analytics_config_lifecycle.py` | `F422A2B77EA6A3DE7D16ED08FF4651929F02E29291F86757D1B596C4E330FC90` |

证据 XML SHA256：

- `lifecycle-before.xml`：`CA3CA11254ED7DEEEEB9747F53DD343309DC3938D552F6BFD41D2F8A3212A714`
- `lifecycle-after-1.xml`：`3A0F5E63C2EEAEE9EADFAC8B94C480BEA8A88C58C2ED29EF637CEF5795509428`
- `lifecycle-after-2.xml`：`357AF2EE0C1FCE79A02C9DA8B7744FF7C6112C2290DAFE447908553C779F21A1`
- `lifecycle-after-3.xml`：`9129E28242B036EBEB9DEB8CF60442982A3A147ACE2EA2385CB32110647F8F3E`

最终针对本代理文件执行 `git diff --check` 通过。产品与测试已冻结，后续统一门禁由根代理执行。

## 门禁收集复核后的夹具清理加固

根代理授权只修改 `test_analytics_config_lifecycle.py` 的假引擎夹具。原用例若在 `keep_old_busy=True` 场景中途断言失败，恢复 Mock `checkedout=0` 的步骤可能未执行，生产池清理会如约保留“忙池”，污染同一 pytest 进程中的后续用例。现在 `engines` 夹具用 `try/finally`，只将本夹具创建的 Mock 占用归零后再执行 `dispose_read_pools`。没有改产品清理策略、没有放宽断言，也没有触碰真实连接。

加固前测试文件 SHA256 为 `7C9F414525BE0C0262F3767F951AC647FE521D4C6CAD2791AA4882502DDDE525`，表内已更新为加固后 SHA。仅本文件专项追加执行一次，结果 **23 passed，56.94 秒**，记录为 `.implementation/lifecycle-fixture-after.xml` / `.log`。没有扩大测试范围；产品及测试再次冻结。

追加 XML SHA256：`535830343C3D5BA3960A327B7548301B4CE53000F3E98A918F70A27886007E1A`。
