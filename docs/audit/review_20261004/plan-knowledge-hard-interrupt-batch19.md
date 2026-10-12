# 第 19 批知识/图谱硬中断：只读审查与最小验证合同

审查日期：2026-10-04。范围：本工作树的 Node DSH Host + 私有 Python Worker、知识入库/同步、图谱抽取与物化。已读取根 AGENTS.md。本报告只依据源码，未运行测试、浏览器、服务、数据库或网络，未读取 .env 或凭据。未改产品或测试文件。

## 当前机制和明确边界

- `backend/app/modules/knowledge_graph/models.py:78-116`：ExtractionJob/ExtractionRun 没有进程 owner、generation、lease/heartbeat。继承的 owner_user_id 是业务创建人，不是 Worker 身份。抽取执行状态是 running/review/completed/failed；模型中的 pending 是 ExtractionCandidate.review_status（134 行），不是可接管的后台任务队列。
- `backend/app/models.py:288-300`：KnowledgeDocument 只有 processing/ready/failed，也没有持久化执行 owner 或上一次状态。`backend/app/modules/knowledge/models.py` 的 KnowledgeSyncRun 同样只有运行状态和汇总计数。
- `backend/app/business_worker/server.py:31-89`：initialize 只 bootstrap 和创建业务应用；activate 只启动连接池维护与调度。`backend/app/business_worker/chat.py:684-720` 恢复的是 DSH Run，并没有扫描知识/抽取表。因此当前新 Worker 不会接管这些 running/processing 业务操作。
- `backend/app/modules/system_tools/scheduler_lease.py:60-135`：同一项目路径的调度器用内核文件锁互斥，实际轮询返回前不释放。它保护调度轮询，不覆盖直接知识/抽取 API，也不是每个业务运行的恢复凭证。
- `backend/app/modules/knowledge_graph/extraction.py:843-864` 先锁运行、再锁图谱及候选；实体和关系写入均传 commit=False（913、954 行），子函数仅 flush（`graphs.py:315-321,397-403`），运行 completed、目标记录及审计在 978 行一次 commit。`models.py:161,178` 又有实体业务键和关系端点唯一约束。硬终止发生在事务提交前时，SQL 子记录与完成状态应整体回滚；提交后丢回执时，读取已完成运行可判断实际结果。这里没有足够源码依据声称会重复创建实体/关系。
- 旧 Worker 未确认死亡时：当前实现没有业务自动接管，因此不能描述为“恢复会重复物化”。同时它也没有 Worker fencing。`services/dsh_host/worker.mjs:59-62,87-103` 的 unavailable 不必由 exit 触发，close 在 kill 之后没有等待 exit；`host.mjs:205-218` 又先做 Run 收尾再 close Worker。未来若加“启动时直接重置活动状态”恢复，不能把 unavailable 或超时直接当旧 Worker 已死亡；否则会引入旧执行与新执行同时继续的窗口。这一并发后果需要受控双 Worker 故障验证，现阶段不是已复现问题。

## 风险 1：已提交的活动状态在硬终止后没有收尾，阻塞原资源后续操作（源码确定）

证据：`extraction.py:615-647` 把 Job 和 Run 置 running 并先 commit，候选与 review 在 688 行才 commit；失败收尾只在 690-695 行的异常分支。进程硬终止不执行这些分支，且上述启动流程没有扫描这些表。遗留 running 会被 execute（617-618 行）、edit（252-253 行）、delete（283-284 行）同时拒绝。正常 API 路径无法解除；这不是仅仅前端残留状态。

知识入库有同类窗口：`knowledge/service.py:480-508` 先检查 processing 并拒绝并发，然后提交 processing；只有正常完成 706 行或异常处理 709-723 行能改变该状态。上次 ready、parser 信息只存在局部变量（491-492 行），新 Worker 无法按原异常分支可靠还原。批量同步也先提交 KnowledgeSyncRun.running（`knowledge/governance.py:688-698`），最后才提交终态（747-757 行）；单文件入库完成与 imported_count 增加分属两次提交（733-740 行），硬终止可能留下真实材料已入库而计数尚未增加的记录。

通过计划任务调用时还会被稳定幂等键固化：`system_tools/schedules.py:400-419` 先提交 ScheduledJobRun.running，命中旧键直接返回；`541-544` 从未更新的 next_run_at 重建同一键。中断后新调度器虽可得到内核锁，仍只是重复返回旧 running，既不接续业务也不推进 next_run_at。这是同一恢复缺口的调度入口表现。

最小修复合同：先实现“确认中断并可明确重试”，无需直接做自动断点续跑。为活动操作记录稳定 operation_id、Worker 启动身份/代次及恢复所需的旧状态；新 Worker 必须先证明旧 owner 不再持有执行权，再以条件更新关闭该 owner 的遗留运行。对无法证明归属的旧记录标为待核对，不凭超时重跑。抽取运行与 Job 同事务转 interrupted/failed；知识重建依据持久化旧状态保留旧块；同步按已提交 document 结果核对计数；关联 ScheduledJobRun 同时收尾并明确后续调度策略。旧 Worker 的最终提交必须校验 operation_id/代次，不能覆写已被回收的状态。

需要故障验证：分别在初始活动状态 commit 后、候选/分块 flush 后但终态 commit 前、业务 commit 后但同步计数/宿主回执前杀死私有 Worker，再启动新 Worker。断言无永久 running/processing、旧成功材料保留、单个运行计数与材料一致、明确重试最多新增一个新运行。另保留旧 Worker 在提交前闸门停住而非杀死，新 Worker 不得重置/接管；释放旧 Worker 后只有当前代次能提交。

## 风险 2：知识外部索引写入与 SQL 事务分离，硬终止会绕过仅存内存的补偿（源码确定的窗口，残留规模未实测）

证据：`knowledge/service.py:648-687` 为新块生成随机 ID、flush SQL 后即写 Elasticsearch；新 ES ID 只追加到本次调用的局部 indexed_es_ids（509、687 行）。SQL 新旧块切换与 ready 在 705-706 行 commit，旧 ES 删除在 707 行。失败补偿只在 except 的 710-711 行，硬终止不会执行。故“ES 已成功写入、SQL 尚未 commit”以及“SQL 已 commit、旧 ES 尚未删除”都有不可由事务回滚修复的外部残留窗口，当前启动没有相关重建/清理入口。

影响边界：`knowledge/service.py:1288-1301` 会把 ES 命中重新映射到当前授权 SQL chunk，并核对正文，因此不能把外部残留直接报告为旧内容泄漏或重复用户答案。确定的问题是持久补偿缺口；残留是否挤占向量 top-k、带来可测的召回损失，属于需要受控索引桩/本地隔离索引验证的推断。

最小修复合同：把索引新增/旧 ID 删除转为与 SQL 材料切换同事务写入的持久 outbox，以稳定 chunk/operation ID 幂等执行；SQL 保持权威，索引未完成时沿用已有关键词回退。新 Worker 只消费持久意图并按当前 operation/generation 核对，不依赖失落的内存 ID 列表。不要仅在启动时把 processing 改成 failed 后重新调用全部外部写入。

需要故障验证：索引桩持久记录已接受的 ID，在 ES 接受新块后/SQL commit 前、SQL commit 后/旧 ES 删除前各中断一次；恢复后断言当前 SQL 材料正确、outbox 最终完成、索引仅保留应存 ID，重放同一意图不生成额外 ID。检索验证分别断言孤儿命中被排除和有效命中补足，不把“过滤了旧内容”当成“召回完整”。

## 验证范围约束

现有 `tests/backend/test_extraction_materialization_contract.py:136-172` 对普通异常/提交前异常已经编写实际 flush 后回滚合同，但不是进程硬杀或跨进程接管证据；本次没有运行它。后续若获准执行，仅用临时本机 SQLite 与文件/模型/索引桩，进程数 2、每个故障点 1 次、单个小文档/单次抽取、每阶段有限超时并清理已启动进程；需要数据库特有锁语义时另核实本机服务归属和指定本地库范围。禁止使用现存远程配置、真实模型或真实业务数据源。超过时限或资源异常立即停止，不自动重试。
