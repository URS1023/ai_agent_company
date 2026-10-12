# 后续：SQL 派发前的来源版本与权限重验

状态：**仅静态发现，尚未执行该问题的隔离反例，未纳入批次十五已修复范围。**

工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-compound-delivery`。本记录按根代理要求保存下一批边界，不修改产品或测试去扩大本批范围，不连接数据库或模型。

## 当前能证明的边界

批次十五的 `validate_query_lease` 核对租约的工作区、来源、用户、存在性与有效期，并刷新已有单次预算对应的租约窗口。它读取父来源的 ID 以保持锁顺序，**不重新校验当前连接配置、列白名单、启用状态或用户权限**。有效租约不是新的权限凭据。

主查询先 `get_source(active=True)`，再读版本匹配的 `SourceMetadata`，自然语言路径还可能等待语义召回/模型，随后用所持来源对象和元数据授权 SQL。Session 的默认 `expire_on_commit=False` 意味着别的 Session 保存来源时，本次原对象不会自动变成新配置；`validate_query_lease` 只选 ID，也不会刷新这个来源对象。

所以需要明确区分以下已有保护：

- **N01 派生配置 CAS**：`profile_updates.py` 的条件更新防止迟到元数据/语义画像覆盖后来保存的版本、权限范围与停用状态。它保护派生写入，不代表所有 ready 元数据读取后都会在 SQL 前再执行一次 CAS。
- **N02 同进程池退役**：`sources.update_source` 提交配置后调用 `dispose_source_pools(..., minimum_version=current_version + 1)`；删除也提高版本并退役旧池。`connection_pool.pooled_engine` 在建引擎/换池之前拒绝迟到的旧版本。
- **N02 的覆盖条件**：`connections.reusable_source` 仅针对已启用、持久化的 MySQL/PostgreSQL 来源；SQLite 使用短连接，ES 使用自己的 HTTP 客户端，不经过该代际屏障。已经借出的操作按旧合同退栈后归还，也不是被即时杀停。
- **进程边界**：池代际状态位于各进程内。一个进程收到正式编辑动作，不等于另一个进程已获得同一退役屏障。这里仅说明现有保证的范围，不在本记录设计跨进程失效机制。

当前静态路径支持以下疑点：ready 元数据已经取得、准备阶段尚未结束时，另一个 Session 正式收窄该来源列权限或停用来源；SQLite/ES 后续仍可能拿旧对象与旧授权结构派发原 SQL。同进程 MySQL/PostgreSQL 的“新借用旧版本池”会先受到 N02 拦截；不能把这一保护外推给其他 provider、其他进程或已借出的连接。

本记录没有实测这个竞争窗口，因此不能写成“已确认泄露行数据”或给出真实受影响数量。用户角色/成员资格在准备阶段改变是另一条权限缓存边界，本次没有进一步展开。

## 下一批最小隔离反例计划（未执行）

1. 使用既有临时平台 SQLite 与测试私有的受管业务 SQLite 文件，先在正式查询入口取得 ready 元数据。自然语言模型只用内存桩，固定返回 `SELECT amount FROM sales`，以阶段屏障暂缓返回。
2. 独立临时 Session 经**正式来源编辑入口**把允许列由 `id, amount` 收窄为 `id`，保留正确旧版本参数，取得新版本成功回执。同步只访问该测试私有 SQLite；记录真实来源版本与新结构。
3. 放行 A 的模型桩，记录底层业务 SELECT 的调用计数。预期待定义合同：旧版本不再执行，返回明确需要重新规划/重新取得当前授权结构的错误；不得静默切源、放宽列范围或重试原业务 SQL。
4. 正对照：版本未改变时原授权 SQL 仍成功；已停用来源的**历史读取**仍遵守现有“当前授权读者可追溯”合同，不因新增执行守卫一并禁掉历史。
5. ES 只用内存假客户端验证旧请求不被派发，不访问真实 HTTP 服务。MySQL/PostgreSQL 只用现有 fake engine 验证同进程正式更新的 N02 防线仍先拒绝旧 key，不用假驱动宣称服务端权限即时生效。

SQLite 测试可证明本实现的版本/权限判断与事务顺序，不能证明真实 MySQL RR 并发、PostgreSQL 服务端取消或其他进程已收到失效通知。后续修复应分别报告这些边界。

## 实际阅读与证据快照

下列文件在本次附加核对中仅读指定片段；`profile_updates.py` 的 CAS 结论来自此前批次十四已实现并审查的完整模块，本次只复核其 hash，没有再次通读。行号以本次读取工作树为准。

| 文件 | 阅读/定位 | SHA256 |
| --- | --- | --- |
| `backend/app/modules/analytics/sources.py` | 155–294：get/update/remove source | `837AAB94B5C1436D5AF2A2A0248A39324272D024802C271339B7EF2F3F05EE5F` |
| `backend/app/modules/analytics/connections.py` | 110–223：pool/短连接选择、readonly、reusable_source | `AE50AA8709F13DBB88005312E4ED1907E854BA0551B331E520F555C67A9A555A` |
| `backend/app/modules/analytics/connection_pool.py` | 80–105、130–162 代际检查和退役定位 | `90360E22D911D92D71FF4FB6EB9AB3F916DF649A05923C4F0A41E8827922D91E` |
| `backend/app/modules/analytics/profile_updates.py` | 本次 hash 复核；CAS 内容承接批次十四 | `35CF1BBA7C2D3D86E15775BB2A1EC3DAE109453603245B174DB310C2CE65384B` |
| `backend/app/modules/analytics/queries.py` | 主查询入口、metadata→model→authorize→lease→execute 段；本批持有修改文件 | `AC0F00D3305C025F3792F41BBC6ED978DDFD6C2CE18137AF1019A8C0A30E8C87` |
| `backend/app/modules/analytics/metadata.py` | source_metadata ready/版本命中和 CAS 段；本批持有修改文件 | `5B9A6BB2432B84372E005CC47A3C1169F0A1658F316586CF636416C0E39D1B99` |

`governance.py` 的此次判读为批次十五 `validate_query_lease` 新增函数：仅 ID 父锁和租约身份/时效判定。随后本批还将优化日限额历史扫描，因此最终治理源码 hash 以 `.implementation/fix-query-cancellation-batch15.md` 为准。`backend/app/db.py:9,55` 明确默认 `autoflush=False, expire_on_commit=False`，已通过真实临时 Session 的 SQL 次序正对照排除“本项目正常路径被 autoflush 反转锁顺序”的疑点。
