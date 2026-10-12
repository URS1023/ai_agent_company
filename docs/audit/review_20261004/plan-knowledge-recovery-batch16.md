# 批次十六计划：知识入库硬中断后的状态恢复（FK-002）

日期：2026-10-04。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-compound-delivery`，分支 `codex/major-compound-delivery`，本次读取 HEAD 为 `e36533e69abb374a7b8795eab8c383e7206e5de8`。

本报告是下一批的只读设计依据，不是已实现或已通过验收的结论。只写此 ignored 报告；没有修改源码、测试、正式文档，没有执行 Python、测试、服务、模型、数据库或网络请求。其他参与者的修改保持原状。已登记设计参考 `docs/audit/review_20261004/followup-knowledge-design.md` 的 FK-002 部分；以下用当前树重新核对实际链路，不把该设计当作已经存在的产品能力。

## 1. 确定的静态结论

1. 当前没有持久化的知识文档执行队列。页面有“上传→解析”的内存队列，后端入库、重建及逐文件同步都是同步 HTTP 执行。文件收到并提交成功，不等于知识文档已建立，也没有自动扫描文件后补入知识库的消费者。
2. `knowledge/service.py:480–508` 在事务中锁定 KB 与已有文档，拒绝任意同 KB/File 的 `processing`，随后提交 `processing`；解析/识图/向量化在提交之后开始。锁没有覆盖整个执行期，状态也没有 owner、attempt、代际或可证明执行已停止的记录。
3. 从这个提交之后到最终 SQL 提交之前硬终止进程，会留下 `processing`；下一次导入和重建仍在第 480 行得到冲突。重启链中没有知识恢复入口。重建前的旧分块通常仍完整；首次导入则没有已提交的新分块。
4. 普通异常收尾已有实现，但不是所有失败都能收口：首个 `processing` 提交在 `try` 外；错误收尾本身也可能失败；`BaseException`、进程强制终止、断电不运行 `except Exception`。不能用一次模型异常测试代替进程恢复证明。
5. `KnowledgeSyncRun` 也会遗留 `running`。文档最终提交与批次计数提交分开，存在“文档成功、计数尚未增加”的窗口。当前仅有计数和查询条件，没有逐文件持久执行清单，不能保证原批次精确续跑或计数恰好一次。
6. 入库的 SQL 与 ES 是不同提交域。新 SQL 分块逐个 `flush`，但统一在最终事务提交；ES 候选在此之前已可落地。硬中断可以留下没有 SQL 对应行的 ES 候选。当前检索会按 SQL 再鉴权/核验，孤儿 ES 不应成为有效知识结果；但它们不会自动得到清理。

## 2. 实际入口、持久点和重启链

| 阶段 | 精确入口与位置 | 当前持久化/执行语义 |
| --- | --- | --- |
| 页面队列 | `apps/web/assets/platform/knowledge-upload.js:18–80`；`knowledge.js:115–126,225–246` | `items/cached/running` 仅在 JS 闭包内。逐个上传，再等待解析接口完成；同一页面中失败保留 `fileId`，成功项跳过；刷新或重建对话框不恢复这个队列。 |
| 文件接收 | `files/router.py:253–274`，批量 `171–219`；`files/service.py:215–265` | 先 `put_bytes`，再新增 File 和 FileMetadata，物理文件行直接为 `ready`，由路由提交。没有此处创建 KnowledgeDocument 的调用。硬退出可能留下存储孤儿，不能把该情况误报成知识 processing；本批不扩展为文件存储回收。 |
| 文档导入 | `knowledge/router.py:193–220` | `/api/knowledge/documents` 同步调用 `add_document`；解析全部结束才返回 201。服务先提交成果，路由后补审计并再次提交；响应或审计失败不能据此推定成果没有提交。 |
| 重建 | `knowledge/router.py:713–747`；`service.py:870–909` | `/api/knowledge/{kb_id}/documents/{document_id}/reindex` 校验文档和源文件后进入同一个 `add_document`，不另有后台消费者。 |
| 批量同步 | `knowledge/router.py:540–563`；`governance.py:669–758` | `/api/knowledge/{kb_id}/sync` 先提交 `KnowledgeSyncRun(running)`（698），再查询当前候选并串行处理，每个文档/计数独立提交，最后提交 completed/partial。 |
| 入库前校验 | `service.py:460–478` | 参数、KB、File、存储存在性、embedding 配置先校验，再锁父 KB 与同 KB/File 文档。新导入无文档时靠父行锁串行化检查/创建，但只覆盖这一事务。 |
| processing 持久点 | `service.py:480–508` | ready 的重复普通导入返回原 ID（487–489）；其余复用已有失败行或创建新行，提交 processing。`previous_status/previous_parser` 只在内存。 |
| 解析与模型 | `service.py:511–643` | 读旧分块、解析、识图、分块、一次批量向量调用；无阶段检查点/持久 owner。视觉单图错误可成为 warning，向量数量/空值/维度错误为硬失败。 |
| 候选写出 | `service.py:647–691` | 每块随机新 ID、SQL add/flush、ES index；SQL 尚未提交。ES ID 仅收集在本地 `indexed_es_ids`，SQL 分块中也存 ID，但当前事务硬退出后候选 SQL 回滚。 |
| 成果切换 | `service.py:692–708`；`930–945` | 文档 ready、新分块、旧分块及旧 metadata 删除在第 706 行同一 SQL commit 生效；第 707 行再清旧 ES。真正断在 706 已成功返回之后，新的 SQL 成果应直接保留。 |
| 普通失败收尾 | `service.py:709–724` | rollback 本轮 SQL；尽力删本轮 ES IDs；按内存 previous_status 恢复 ready 或 failed、恢复旧 parser、提交错误摘要。清理 ES 内部失败只记日志（1040–1057）。 |
| DSH API 运行 | `business_worker/application.py:20–39,108–148`；`server.py:103–113` | 保留原 HTTP 路由与认证依赖，`api.dispatch` 在当前 Worker 分发 ASGI，请求线程内执行原服务。没有入队持久化或知识专用恢复 RPC。 |
| DSH 启动/维护 | `business_worker/server.py:31–100` | initialize 调 bootstrap，activate 仅启动连接池维护和调度器；shutdown 不枚举/归档 processing 文档。 |
| 兼容应用启动 | `main.py:100–190`；`seed.py:175–201`；`schema.py:65–139` | 建结构/种子、系统角色/生命周期、连接池维护、调度器；没有知识状态接管。schema 初始化仅创建/核验结构，不能当作旧执行器静止证明。 |
| 宿主进程退出 | `services/dsh_host/worker.mjs:25–33,87–103` | 子进程 exit 会拒绝 pending RPC；close 等待至多 1.5 秒后发 kill，并未在 kill 后再等待退出确认。不能把调用 close 返回当作“所有旧知识执行器都退出”的证据；更不能由某一个 Worker exit 推断所有共享库写入进程都停止。 |

对应结构：`backend/app/models.py:288–322` 的 Document 只有状态/parser/错误/时间，Chunk 只有正文、位置、embedding 状态/模型/维度、ES ID，没有向量本体或 attempt；`knowledge/models.py:86–104` 的 SyncRun 没有 owner/清单/逐项状态。

## 3. 哪些失败能收口，哪些无法自动恢复

| 失败点 | 当前静态结果 | 下一批应验证/保留 |
| --- | --- | --- |
| 校验/源存储不存在，尚未提交 processing | 抛出错误，不触发本轮索引写出；已有成功分块未删除 | 不应为了“恢复”绕过当前 File 可见性、被删除状态或存储存在性。 |
| 解析失败、无文本、向量调用或形状错误 | 进入 709–724；在回滚和收尾提交也正常的前提下，首次失败为 failed，重建恢复旧 ready | 保留原 Document ID、旧 parser/分块/metadata/ES；可以按当前授权重试。 |
| 单张识图失败 | 549–562 转 warning，继续其它图/文本；最后若仍无可索引文本，则落普通失败 | 不把有 warning 的 ready 当成未完成 attempt。 |
| ES index/refresh/cleanup 普通 Exception | 983–1057 内部吞为降级/日志；索引写失败仍可 ready + SQL 关键词回退 | 不自动重做整个成功文档；清理失败应独立记待清理，不应翻转 ready。 |
| 初次 processing commit 失败或确认结果不明 | 第 508 行在 try 外；是否已提交取决于实际事务结果，代码不做重新核实 | 不能按客户端异常直接置 failed/重跑。需要 attempt 状态和新事务确认，列为待执行事务边界测试。 |
| processing 已提交、解析/embedding 等待时进程硬退出 | 无 finally/except 执行，processing 永存；再次导入/重建 409 | 新执行者须先证明旧执行权释放，不能用 updated_at 老化抢占仍在运行的慢请求。 |
| 写出部分 ES，最终 SQL commit 前硬退出 | 新 SQL 事务回滚；旧已提交 SQL 分块保持；新 ES 可残留 | 保留旧成功内容；孤儿候选按 attempt 清理；没有候选清单时不得按整 File/KB 宽删。 |
| 最终 SQL commit 成功后、旧 ES 清理/HTTP 响应前硬退出 | 已有新 ready/新分块；旧 ES 可能未删；审计/调用方响应未完成 | 成果已成功，不能清新分块或重新 embedding。普通 import 重放复用 ready；精确重放 reindex 还需要请求幂等键。 |
| 收尾 rollback/查询/最终收尾 commit 自己失败 | 709–724 没有第二层可靠收尾保证，仍可能残留 processing | 恢复协议不能依赖 except 一定完成；原成果仍以已提交 SQL 为准。 |
| sync 的候选查询/循环外 final commit 发生普通异常 | running 已在 698 提交；这些代码不在逐文件 except 内，可能一直 running | “只有硬 kill 才遗留批次”并不准确；批次终态同样需要独立控制/收尾。 |
| 文档已提交，sync 739–740 计数尚未提交即退出 | 文档完成但旧 run 计数偏小；不能从旧计数反推逐项成功集合 | 需要与文档结果同事务的逐文件 ledger，或明确本批只标 interrupted 并保留“最后确认计数”。 |

数据库在进程关闭连接后回滚未提交事务是上述推导的前提；本次未用真实终止证明 SQLite/MySQL 的实际行为。提交结果不明是独立边界：尤其不能假定 `commit()` 抛错就代表数据库端没提交，也不能沿旧失败清理盲删候选索引。

## 4. 恢复时必须保留的成果清单

1. **文件身份和原始字节**：已提交 File ID、sha256、storage_key、物理版本和 FileMetadata；知识解析失败不重传文件、不生成新 File ID。恢复固定原物理版本，不静默切到 logical_id 的最新版本。页面当前保留 fileId 的行为继续成立。
2. **上一次完成的文档**：Document ID、原 ready/parser 与旧 SQL 分块集；包括 ordinal、正文、location、embedding_model/dimensions、ES ID。初次导入没有旧成果可恢复时才转为可重试失败。不要仅凭“有若干分块”猜 previous_status，新增控制记录应保存前态。
3. **旧分块业务元数据**：`knowledge/models.py:43–63` 的 enabled、source、question/answer、更新者，以及已手工修改的正文。失败恢复不调用 `_delete_chunk_rows`。现有显式“重建”成功会替换这些解析分块，UI `knowledge.js:282` 已提示；不能让自动恢复偷偷成为一次新的显式重建，从而抹掉编辑或复活停用分段。
4. **旧 ES 索引**：最终 SQL 未切换之前继续保留旧 ES ID。新旧 SQL 原子切换后再仅清旧 IDs。遇到 ES 删除失败，旧孤儿可能继续占空间，但不得为了清理而移除当前 SQL 对应的新索引。
5. **最终提交已完成的新成果**：doc ready 与新分块共同提交成功，即使还没有 HTTP 成功响应或批次计数也应保留。不要根据外层调用失败再把它判成待重算；需要稳定 attempt 的完成标记/结果集合帮助确认提交结果。
6. **本批次之前已完成的其它文件**：失败只重试明确中断的项，不清空整个 KB，不按原 sync 查询重新导入所有内容。尤其 `reindex=True` 不能在恢复时对所有已完成项再次向量化和替换。
7. **手工分段容器和其他文档**：sync 当前排除 `knowledge_manual`（governance:705）。恢复扫描不得忽略这个边界，不能批量重建手工容器或合并其他 file/document 的成果。

当前仅保存 SQL 分块正文和向量元数据，**向量本体在 ES**；进程内 `blocks/texts/vectors/indexed_es_ids` 丢失后，并无可验证、可续算的逐块持久检查点。最小恢复应保留旧成功成果、只重算当前未完成文档，不能声称能够从“已写成功几个 ES 块”继续且零重复模型调用。若未来要求逐块续算，须另有固定 source SHA/解析版本/分块参数/embedding 配置/内容 hash 的候选 manifest，持久向量或等价可验证缓存、确定的候选 ID 与完成标记；不在本次最小范围暗加。

检索侧现有护栏必须保留：`service.py:1115–1142` 要求同 workspace、active KB、ready/processing Document、ready 且未删除 File、enabled 分段；`1288–1311` 将 ES 候选映射到当前 SQL，正文也必须相同。保留旧 processing 分块的可用性与阻止孤儿 ES 被引用是两个不同约束。

## 5. 最小安全实施方案（尚未实现）

### 5.1 执行归属与持久状态

沿既有正式 FK-002 方案，保留同步 API，增加执行期互斥和小型持久控制记录，不先引入全套后台任务队列。

- 执行键为受控数据库部署命名空间 + workspace_id + kb_id + **物理 file_id**。在单机受控部署中可用非阻塞内核文件锁，持有至真正解析/模型/提交/收尾线程结束，HTTP 超时或 await 取消不能提前释放。
- 可参考现有 `system_tools/scheduler_lease.py` 的内核锁机制；知识锁必须用独立路径，不能使用调度器默认 `scheduler.lock`。稳定锁目录须跨所有共享库进程一致，不能按 worktree 路径或临时目录各建一套；不能删除仍可能被持有的锁文件来“重置”。获取异常应拒绝执行。
- 单机内核锁不能证明多主机互斥；不同主机/命名空间不能自动抢占。多主机需要专门验证的数据库连接级互斥/协调器及代际保护。本批不声称已证明 MySQL 多主机并发安全。
- 新增控制记录建议唯一键 `(workspace_id,kb_id,file_id)`，保存 document_id、attempt_id、递增 generation、当前 stage/state、协议版本、owner host/namespace/执行实例、previous_status/parser、固定源 SHA/参数/embedding 配置、请求动作/actor/client/令牌记录 ID（不保存明文令牌）、可选 sync 项关联、时间和安全错误摘要。
- 获取执行锁后，短事务锁父 KB、控制记录和目标文档，遵守统一顺序；不存在的控制行用唯一约束处理首次并发。**attempt + 前态 + processing 一次提交**。不能先提交 processing，稍后才补 owner。
- 最终提交与失败恢复必须同时核对 workspace/kb/file/document/attempt/generation；旧 attempt 的迟到提交/清理不能命中新 attempt。SQL 成果切换与控制状态 completed 在同一事务；不要在模型执行期持有长事务父锁替代执行互斥。

### 5.2 硬中断后的接管

1. 下一次明确重试先按当前调用身份完成动作、KB、源 File 和工作区校验，再获取同键执行锁。锁拿不到即仍在执行，不按 elapsed time 偷占。
2. 新协议 owner 与命名空间一致，且持有同锁后再用新事务检查持久 attempt。若已 completed/ready，返回已有结果或只安排旧 ES 清理；若未完成，保留旧成果、记录 interrupted，按本次请求动作开启新 attempt。
3. 普通 import 的重放保持现有 ready 复用语义；重建仍是显式 `knowledge.execute` 动作。恢复不能替一个只有 create 的请求自动执行历史 reindex，也不能将历史执行者身份委托给当前调用者。
4. **旧版 processing 没有 owner**，新协议拿到锁并不能证明旧版执行器停止，因为旧版根本不持这把锁。必须有升级静止点证据（所有能写共享库的旧进程已退出）和限定范围迁移/审计；否则返回需要核验的明确状态，保留旧正文，不调用模型。单个 Worker exit、PID 不存在或时间够久均不是全体执行器静止证明。
5. 启动时可仅检查/标记可证明已中断的控制状态；默认不以历史 actor 身份自动读取文件或发模型请求。实际续做由当前已授权请求触发。若需后台自动续做，另行明确凭据重新解析、当前授权、成本/限额与任务所有者合同。

### 5.3 索引和幂等

- 保持当前“新 SQL 分块 + 旧分块删除 + doc ready”原子切换，不能逐块 commit 后让旧新集合混杂可见。
- 新候选 ES 必须带 attempt/generation 或有持久候选清单，便于只清当前失败 attempt。ES 写成功但响应丢失也可能留下候选，因此不能只信内存 returned IDs；按 attempt 的清理仍须排除已成为当前成功成果的候选。
- SQL 已提交后的 ES 清理失败记录 cleanup_pending，可重试清理；不得回退 ready 或重新模型调用。无权访问新内容的内部清理只能执行限定 attempt 的元数据收尾，不能返回正文/重新建索引。
- 普通 import 当前按 KB/File ready 复用；显式 reindex 没有客户端 operation key，两个相同 reindex 请求既可能是重发，也可能是用户真要重建。若要求“最终 commit 后响应丢失，再重发 reindex 不重复计费”，必须有持久幂等键/attempt token 与固定载荷指纹；不同载荷同键必须冲突。仅看 parser 参数相同无法区分意图，不能在无键时宣称恰好一次。

### 5.4 同步运行记录

有两种诚实范围，建议根代理实施前选定其一：

- **最小批次**：文档级恢复；旧 sync 只在可证明执行已终止后标 interrupted，保留最后确认的计数并说明并非精确完成清单；新的明确 sync 作为新运行。禁止自动重放旧 `reindex=True` 全批。
- **包含原批次精确续跑**：新增固定候选 manifest 和每文件唯一结果 ledger（至少 run_id/file_id/document/attempt/status/result），与文档完成同事务确认结果；计数由 ledger 推导/幂等聚合。恢复只处理未完成项，不重新查询扩大的分类集合，不因文件新增/移类/版本变化扩大旧任务。各项再次执行前仍做当前授权。

计数提交间隙不能靠“下一次看到 ready 就算本次 imported”可靠弥补：该 ready 也可能由另一次请求产生。没有 run→attempt→result 关联，就只能报告最后确认计数，不能制造精确审计。

## 6. 授权、撤销与删除边界

- HTTP 当前链为 `common/deps.py:283–328` 解析真实身份、固定服务客户端 workspace、检查工作区/成员、动作权限、绑定数据范围。`authorization.py:405–430` 使导入为 knowledge.create，reindex/sync 为 knowledge.execute；不要统一改成 knowledge.edit，也不要用恢复操作隐式扩大原动作。
- File 读取继续经 `files/service.py:288–301` 和 ORM 数据范围。`data_scope.py:137–150` 只允许当前 knowledge 动作中读取可授权 KB 已引用源文件，不能扩大 `/api/files` 列表、原文或下载权限。首次导入尚无引用，仍需合法的文件范围；恢复不是把任意 file_id 建一条引用后再绕过 File 授权的理由。
- 服务客户端恢复须保留 client_id、原 token 记录绑定与当前 token/client scopes 交集；`deps.py:139–160` 已有不写心跳的纯身份解析 helper。撤销/过期、client 停用/转移执行者、用户停用/移出工作区、scope 收缩均应拒绝后续执行，不能复制管理员权限或只保留 user_id。
- 现有 AccessPolicy 缓存在 ctx（authorization:73–88），数据范围绑定是一次性快照（data_scope:105–106），长请求不会凭新建一个普通 Python ctx 自动取得最新数据库快照。若要保证模型前或最终发布时的中途撤权，必须独立的新事务/会话重新解析当前身份与范围；`populate_existing` 不会突破 MySQL RR 的事务快照。需要明确检查点与撤销语义，不能宣称即时全程撤销。
- 不要在带有候选 INSERT/DELETE 的主事务中调用可能更新心跳并 commit 的 `resolve_principal`（deps:178–195）；纯重验应隔离事务，主提交用 attempt/generation/当前资源状态再次核验。
- 后台会话没有 `bind_data_scope` 就不受 ORM 访问护栏保护（data_scope:177–186）。恢复服务不能直接用裸 db 扫描后以超级权限重新解析所有文件。内部状态恢复与用户内容访问必须分别授权。
- 源 File/KB/Document 被删除、KB 停用、分段被停用/编辑，不能被恢复复活或覆盖。执行锁只覆盖入库函数并不能阻止另一路删除/编辑；若要求并发最终提交安全，删除/相关编辑也须参与同键互斥，或提交时对相关版本/状态做能与写事务串行化的核验。这是需协调的最小共享入口，不能只加前置校验就声称解决。

## 7. 优先执行的三个反例（本次全部未执行）

统一使用主树绝对解释器 `E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe` 经目标树 `tests/run_isolated_backend.py`；不得改用真实数据库/模型/网络。需要跨进程看持久点的测试应使用 **tmp_path 下磁盘 SQLite**，不能用每进程独立内存库。子进程内 TestClient/Worker ASGI 分发真实认证 HTTP，无监听端口；embedding/vision/ES/存储为受控本地桩，模型桩计数，ES 桩可用共享文件记录写出与清理以跨进程核验。父进程只在约定事件到达后终止受控测试子进程并 join/确认 exit。

### R1：首次导入 processing 已提交后硬终止

上传文件并创建 KB；子进程通过真实导入 HTTP，替换 extract_text 为“通知父进程已到解析入口、等待”的桩。此时第 508 行已返回。父进程终止子进程，另起新进程/Session 读同一临时 SQLite，确认 Document=processing、无当前分块；原 API 再 POST 导入预期当前实现 409。目标实现必须在新协议归属证据有效时恢复并只产生一份 Document/当前分块集，模型执行计数符合一次新的 attempt；跨工作区/撤销后重试必须拒绝且零模型调用。测试 setup 必须明确区分新协议 attempt 与旧版无 owner：后者应要求静止点核验，不能直接自动成功。

### R2：重建中部分 ES 候选已写出、SQL 尚未提交时硬终止

先成功导入含多个分块的文档，保存旧 Chunk IDs/正文/位置/metadata（包括 enabled=False、手工编辑、QA 字段）及旧 ES IDs。对重建的首个 ES 桩写入后设屏障，终止子进程。新进程断言旧 SQL/metadata 逐项一致，候选 SQL 无已提交新行，Document 仍 processing；ES 日志中有新孤儿。旧可用分块应按原授权可检索，停用块与新孤儿不可返回。目标恢复保留旧集，按 attempt 仅清候选；故意让 cleanup 失败也不应破坏旧可用成果。另测重试 embedding 再失败，仍保持原旧集及 parser；最后一次明确成功重建才一次原子切换。

### R3：最终 SQL 成功、批次计数/旧 ES 清理前硬终止

运行含两个文件的 sync，在某文件最终 SQL commit 成功后的旧 ES 清理入口或 service 返回与 run imported_count 更新之间设屏障并终止。新进程确认该文件已 ready/新块完整，而旧 SyncRun 仍 running、计数可能缺一；第一个已完成文件不得重算，后续未开始文件不得假报成功。普通 import 同文件应返回原 document_id，模型桩设为一调用就失败来证明复用。对旧批次恢复，目标必须按已选合同：有 ledger 则只续未完成项并精确计数；无 ledger 则标 interrupted 和最后确认计数，不自动重跑 reindex=True。提交后响应丢失的显式 reindex 重放另需 operation key；本反例不能证明现有无键 API 恰好一次。

## 8. 后续必要用例与证明边界

- 两个进程同键并发：慢执行者不被年龄阈值抢占；不同 KB/文件/工作区不互相阻塞；取消 HTTP 等待后执行线程尚活时锁仍持有；真实进程 exit 后锁才可重新取得。
- 旧 attempt 的迟到 finalize/rollback/cleanup 不能覆盖新 generation；清理条件中包含完整资源键，不能凭 chunk_id 列表越过当前 attempt。
- 锁目录失败、不同 owner host/namespace、旧协议 processing 没有可靠归属：明确拒绝接管、零模型调用、旧正文不动。
- 普通解析/embedding 错误、错误收尾 commit 再失败、ES index/refresh/delete 降级；最终 commit 结果不明必须另设受控桩/事务证据，不能以普通 raise 伪造“服务端已提交但客户端不知道”。
- 同工作区共享 KB 的合法引用恢复与陌生 File 拒绝；跨工作区 ID 404/403；导入 create 与重建 execute 分离；client scope 收缩/吊销/过期/停用；用户/成员/工作区停用；恢复后同一 Session 的文件列表/原文/下载仍无新增权限。
- 首次重试前撤权与长执行中途撤权分别测试；中途测试用独立事务提交变更和独立身份重验，不声称新 ctx 自动突破 RR。删除/停用/编辑的最终写入冲突需测试持久结果，不能只看返回码。
- 重放固定文件版本/SHA/分块/模型参数，变更载荷同幂等键应冲突；相同文件名但不同物理 File ID 不能误合并。
- 启动与 UI 边界：刷新后用户能找到旧 file/document 并明确重试；不得展示“恢复中”而实际无消费进程。若增加状态值，列表统计、状态徽章、同步历史序列化一并覆盖。

当前 `tests/backend/test_knowledge_recovery.py:15–99` 仅静态阅读：覆盖重复 ready 导入不重 embedding、普通向量失败保留旧块、failed 同步重试、陈旧 ES 禁用/删除过滤；没有硬 kill、真实重启、执行锁、代际、批次 ledger 或提交结果不明用例。本次没有运行它，也没有把它的既有断言当作新恢复协议通过证据。

SQLite 子进程红绿只能证明本地持久状态、进程互斥和顺序/回滚行为；不能证明 MySQL FOR UPDATE、RR 当前读、连接失联期间提交结果、多主机锁或死锁次序。MySQL 证明需另获符合全局数据库边界的受控本地实例与明确测试预算；本次不连接 `szh_new` 或任何业务库。

## 9. 下一批实施所有权建议

- 核心：`knowledge/service.py`、新增 `knowledge/ingestion_control.py`、`knowledge/models.py`；新增结构需 schema 版本/迁移协调，`schema.py:122–124` 会拒绝同版本不同结构摘要，不能只加 ORM 字段就认为部署可用。
- 入口：`knowledge/router.py`/`governance.py` 传递当前执行身份与固定尝试/批次上下文；若支持精确 sync 续跑，需要 ledger 模型和治理服务单独明确范围。
- 生命周期：默认按已授权重试恢复；若要旧协议冷启动迁移或自动状态归档，由 root 指定 Worker/宿主所有权，不能让知识 worker 擅自修改共有启动/关闭逻辑。
- 授权公共函数与删除/分段编辑的并发协调属于共享入口，先给最小方案再改；不得直接放宽通用 data_scope/File 权限。
- 验证：新增专项硬中断测试文件；沿用既有普通恢复/文件完整性/SQL 权威检索/共享授权相邻回归。不修改上一批冻结的 extraction/graph 产品代码。

## 10. 实际读取范围与快照

行号对应本报告生成时文件。SHA256 是整文件内容标识，**不代表整文件已通读**；读取范围如下。搜索没有命中恢复入口的结论限于列出的入库/启动链和模型，未进行全仓业务审计。

| 文件 | 实际读取/检索范围 | 整文件 SHA256 |
| --- | --- | --- |
| backend/app/modules/knowledge/service.py | 76–103、426–744、850–1079、1115–1158、1235–1324；函数/processing 全文件检索 | 65A4985B242E18EA8C576B32FFA4E12CB9CF4C4B6DF04DF151660BEA8580F7F2 |
| backend/app/modules/knowledge/router.py | 1–38、185–228、530–580、685–780；入口检索 | BB91C122F25E067DF8041A21EC7164C5D57536B53BE67818DCD6FBC349381557 |
| backend/app/modules/knowledge/governance.py | 628–775；sync/recovery 检索 | C76BAA8A7A9AA83D15ED8194184D7A7329978A609EF938A70538D2A9A48DD45C |
| backend/app/modules/knowledge/models.py | 全文 1–104 | 2930DA2A4B19A69E237A671025842E78E81C82E6D0D8600192580CE7B6FE2E9C |
| backend/app/models.py | 261–333 | DCE44685054D4EFC2713B88362CB553F63C9E34AD9EFB4EFAB16222F74620340 |
| backend/app/modules/files/service.py | 211–314 | 688C1F32549FE9D348AE2362329813A43D26C9ACEA30174A15BA298A68F8EE88 |
| backend/app/modules/files/router.py | 59–88、93–105、169–303；_commit 位置检索 | C96E2B11DF5EA55CA40E62ACBE0BBB1D70BB5AA0BC52255E967DCA4107A9C0A9 |
| backend/app/common/deps.py | 53–67、139–211、233–281、283–329；认证函数检索 | 232D683120A0416762076D84D11560F7B9F09C0D9061091A7C4B3111539EF27D |
| backend/app/common/authorization.py | 71–124、391–433；knowledge/scope 检索 | 257554D9942ACF38F241F70D674EA880BB7B7EC1A1B5578FEF25E7AC95B05DA8 |
| backend/app/common/data_scope.py | 85–186；根/子模型与knowledge检索 | 910673325E7033998390973077C45B178846AAB2470A9BE571F35C39EF2C1703 |
| backend/app/main.py | 93–195；启动/恢复检索 | 9D920B5D6B5FC3C485512566C11BE6BE6DED92635200204E8CDAC39ACE32D357 |
| backend/app/business_worker/application.py | 全文 1–148 | 189FA665417DB3B701BF52A8F912573CDE5D0839817C66A3A9E0022AC02EEB4F |
| backend/app/business_worker/server.py | 1–115；启动/恢复检索 | F2D389753C4DA2534B078A59CB2FC239661316B9DC920034330356E50BE4022A |
| backend/app/seed.py | 175–205；bootstrap/knowledge/recover 检索 | 12E67F9E5E22917D8C46FB30319B9262E938219E855A1B5DDBEE3C2D38247C8E |
| backend/app/schema.py | 65–139 | FD6AF3494FE7362470E6CDB5D265BF868A484BA5BA21C4379D3DF5944E31E7FA |
| backend/app/db.py | autoflush/expire/sessionfactory 文本检索，仅核对默认值 | 3020883D529CE5B65625C29C756395D5E2EE71045C79505BFA1912BAFC4E3973 |
| services/dsh_host/worker.mjs | 14–35、85–105；exit/kill 检索 | 35830BC723575836EAE151FB54153D8B7F7A785D7042D89436F4591A85D87201 |
| apps/web/assets/platform/knowledge-upload.js | 全文 1–81 | 61CB3178770D9646D4373A1E828931F464DB32B8AEA58C606A205F98E766186F |
| apps/web/assets/platform/knowledge.js | 110–132、224–249、254–290、495–522；入口检索 | 48233B661CC32AFA85272D0F0EC7754CDB64254530FF91923DB56A6FC3AFB7C6 |
| backend/app/modules/system_tools/scheduler_lease.py | 类/方法/锁调用检索，未重新通读实现；只作为复用候选，不给出新锁正确性验收 | EF29D3BE39B71C93B8D5F8A5150101224AA6E53E39CD0FD2A8DD05EDBF15D3D7 |
| tests/backend/test_knowledge_recovery.py | 全文 1–99，只读未运行 | AF6283452C4006B217611859426F1E1A04984E36813FA2E892BC80694F32AFE2 |
| docs/audit/review_20261004/followup-knowledge-design.md | FK-002 设计段 1–116 与预计范围/边界 159–末尾；标题/关键约束检索；未把 FK-004 重新纳入审查 | BF7ED081F0C4D1684E8BDFB587E6EE98B51D853CF87E825C8BF803B757429144 |

本报告未证实：真实硬终止红例、任何新修复红绿、MySQL 并发/RR、宿主多进程升级静止点、外部 ES 清理效果、真实模型计费幂等、完整 UI 重启体验。上述都保留为下一批要执行的证据，不混入本次静态结论。
