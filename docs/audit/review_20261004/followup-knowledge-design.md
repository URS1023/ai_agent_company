# 下一批知识库修复设计：FK-002 与 FK-004

日期：2026-10-04。状态：只读设计，尚未实施或运行复现。

本批产品源代码与构建保持冻结。本文件位于已确认 ignored 的 `.implementation/`，不改正式审查记录，不访问真实数据库、模型或网络。

## 建议顺序与边界

先单独修复 FK-004 的调用作用域差异；它可以用现有授权规则、小范围修改和真实网关隔离回归完成。随后修复 FK-002：保留当前同步 HTTP 合同，增加可靠的执行归属与恢复控制，使硬中断后的下一次已授权重试能够继续，不在本批引入完整任务队列。

不能用 `updated_at` 超过某个时长就把 `processing` 改成失败，也不能为了 AI 检索恢复结果而去掉 File 联表或扩大通用文件权限。执行权、资料授权和索引可见性必须分别证明。

## FK-002：当前执行链和确证缺口

| 位置 | 当前行为及含义 |
| --- | --- |
| `knowledge/router.py:194–220` | `/api/knowledge/documents` 在请求内同步调用 `service.add_document`；成功后再写审计。 |
| `knowledge/router.py:714–747`、`service.py:870–909` | 重建先查文档和源文件，之后仍进入同一 `add_document`。 |
| `knowledge/governance.py:669–758` | 批量同步先提交 `KnowledgeSyncRun(status=running)`，逐文件导入/重建并单独提交计数，最后改为 completed/partial。 |
| `knowledge/service.py:470–508` | 锁 KB 父记录与文档记录，任何历史文档为 processing 即 409；随后把 processing 状态提交。数据库事务锁在此释放，没有覆盖后续解析/模型调用。 |
| `knowledge/service.py:511–698` | 解析原文件、识图、嵌入、写新 SQL 分块及 ES；没有持久化执行归属或接管凭据。 |
| `knowledge/service.py:699–706` | 新分块与删除旧分块在一个 SQL commit 中切换，再清旧 ES。这个原子切换需要保留。 |
| `knowledge/service.py:709–724` | 普通 Exception 会回滚新分块，并恢复旧 ready/failed；进程硬退出不会执行这里。`previous_status`、`previous_parser`、新 ES ID 列表只保存在内存。 |
| `models.py:288–301` | KnowledgeDocument 没有 attempt、执行者、租约或恢复记录。 |
| `knowledge/service.py:1115–1143` | 当前检索允许 processing 文档的旧 SQL 分块，但要求有效 KB、Document、File、分段状态。重建过程中旧内容仍可用；崩溃留下的无 SQL 对应 ES 分块会被拒绝。 |
| `business_worker/application.py:108–147`、`server.py:112–113` | 业务 API 在 Worker 内分发，不是知识库专用后台队列。Worker 现有维护只做连接池清理/调度，没有知识入库恢复器。 |

因此：有普通异常恢复，不代表有硬中断恢复；请求连接断开也不能作为执行器已经停止的证明。`KnowledgeSyncRun.running` 还存在关联的中断遗留问题，不能只修文档 409 却把批量状态继续显示为正常执行。

## FK-002：最小安全方案

### 1. 加执行期互斥，持有到真正执行返回

为 `(数据库部署命名空间, workspace_id, kb_id, file_id)` 建立固定键的非阻塞内核文件锁。复用现有 `system_tools/scheduler_lease.py:41–168` 的 Windows `msvcrt` / POSIX `flock` 实现或提取同等小型公共锁组件；必须传独立知识入库路径，不能使用调度器默认的 `scheduler.lock`。

- 每次执行使用单独锁对象，覆盖 claim、全部解析/模型调用、SQL 切换与异常收尾。关闭请求只能请求取消，不能在执行线程还在运行时提前解锁。
- 锁被占用，立即返回可识别的“正在执行”；不等待某个总时限后抢锁。
- 锁文件打不开、权限异常、锁目录不可确认时拒绝运行，不能降级成无锁执行。
- 锁文件采用可信部署配置和哈希后的内部资源键；不可由用户提供路径，不在解锁后删除文件，避免不同进程锁住不同 inode/文件对象。
- 相同部署的多个 Worker 必须使用同一锁命名空间及固定目录。新工作树默认 `.implementation` 目录不能拿来证明另一工作树中的在用执行器已经退出。

**支持范围必须明确为同一受控主机与锁命名空间。** 本地锁无法协调多主机；持久记录的主机/命名空间不匹配时不得自动接管。未来多主机部署需数据库连接级互斥或统一协调器，并继续保留下面的 fencing 校验；不能直接照搬本地锁宣称分布式安全。

### 2. 增加小型持久执行控制记录

建议在 `knowledge/models.py` 增加一张附加控制表 `KnowledgeIngestionState`，唯一键为 `(workspace_id, knowledge_base_id, file_id)`。不把控制字段塞进 parser_version 或 error_summary。

至少保存：

- document_id、当前 attempt_id、单调 generation、状态和当前阶段；
- owner_host、受控部署/锁命名空间、锁协议版本、执行启动代号；PID 仅辅助排查，不作为单独接管依据；
- 本次尝试前的文档状态与 parser_version；源文件 ID/SHA256、分块参数和嵌入配置标识；
- 发起用户与可选 sync_run_id，仅用于归属和审计；不保存明文令牌、摘要、缓存权限或模型正文；
- started_at/updated_at/completed_at 和安全错误摘要；时间字段只用于展示、诊断与筛选，不能授权抢占。

取得内核锁后，在短事务中锁定控制记录及目标文档，使用唯一约束处理首次并发创建；把新 attempt、此前可用状态及文档 processing **一次提交**。不能先提交 processing 再补归属记录。

SQL 切换和异常恢复都用完整的 `(workspace, kb, file, document, attempt, generation)` 条件确认仍是当前执行者。核对及切换必须在同一最终事务中持有控制行锁或执行受影响行数为 1 的 CAS；仅在 Python 中先比较再普通赋值不够。旧 attempt 的迟到成功/失败不得更改新 attempt 的状态、分块或清理列表。

### 3. 恢复依赖执行权证据，不依赖年龄

对具备新协议控制记录的 processing 文档：

1. 新请求先按当前身份完成 KB 动作和源文件范围检查，再尝试同一个内核锁。
2. 只有确认命名空间一致且重新取得锁，才说明遵守该协议的旧执行器已经退场。保留锁，再在短事务中核对原 attempt/generation。
3. 若上次最终 SQL 切换已提交，应直接识别 completed/ready；不能再次嵌入或回退到旧内容。
4. 若未完成：原来 ready 的文档恢复为 ready 并保留原分块；首次入库恢复为可重试失败。记录 interrupted 和真实原因，然后由这一次已授权请求开启新 attempt。
5. 不从历史 actor_user_id 或角色快照自动取得继续执行权限。默认恢复是“下一次当前用户明确发起导入/重建时重试”；后台冷恢复只整理确证的遗留状态，不自动调用模型。

**升级前的旧 processing 没有可靠 owner 证据。** 不能因新锁拿得到就断言旧版执行器停止，因为旧代码从未持有这把锁。必须先建立受控的升级静止点：确认所有能写同一平台库的旧实例已经退出，再执行限定资源、带审计的旧状态迁移。无法证明时返回 `knowledge_recovery_verification_required`，保留状态和旧可用分块，不自动覆盖。

当前 `services/dsh_host/worker.mjs:33` 有子进程 exit 事件可作为受控实例退场证据的一部分；`close():94–103` 发出 kill 后没有再次等待 exit，单凭 close 已返回或 PID 不存在不能当成完整静止证明。下一批若覆盖旧记录迁移，需要明确升级协调归属，不应把未经证明的“冷启动即全重置”藏进入库函数。

### 4. 保留当前 SQL 原子替换与 ES 降级语义

新分块仍只在最终事务中成为当前 SQL 版本。新的 ES 候选在 SQL 未提交前不能越过现有 `_current_chunk_query`，继续保留 FK-001 本轮修复的正文与来源复核。

每个新 ES 分块可携带内部 attempt_id，供中断后的有界清理使用；只清理已确认中断 attempt 的新候选，不能按整个 file/kb 删除，否则会伤到上一版可用索引。清理失败只记待清理状态，不阻塞 SQL 重试或把可用文档改成失败。新 SQL 已提交而旧 ES 尚未清理时，以当前 SQL 为准，不重复嵌入。

### 5. 撤权、删除与失败收尾

- 入口权限保持现有 HTTP 合同：普通导入使用路径映射的 `knowledge.create`；reindex/sync 使用 `knowledge.execute`，不是一律改成 knowledge.edit。
- 源文件必须经过现有受控 File 读取路径；新的文件导入不能借恢复函数绕过资料权限，已授权 KB 引用源文件的知识域例外只在对应知识动作内使用，不扩展成文件下载权。
- 新 attempt 必须读取当前账号、成员关系、工作区/组织状态、客户端 scope/令牌有效性和 KB/File 范围，不沿用旧尝试的权限。角色扩大不能使旧客户端 scope 增权。
- 长入库若要求撤权后停止后续模型调用，需在每次图片请求、嵌入批次、索引发布前提供真实的当前权限检查。使用新短事务/鉴权会话，不复用已缓存的 `_access_policy`；`populate_existing=True` 不会突破 MySQL REPEATABLE READ 的旧快照。
- 可复用客户端的纯身份校验 `resolve_service_principal`；若抽取通用当前身份 helper，不能让它在入库事务内通过会话心跳意外 commit。恢复控制记录无需保存任何凭据。
- 被撤权后仍允许必要的内部生命周期收尾，但仅以已绑定的 workspace/document/attempt/generation 更新控制状态、恢复上一版可用状态；不读取并返回业务正文、不重新发模型请求、不重新授予 File 权限。文档/KB 已删除时不重新创建。
- 删除、停用与分段编辑可能与索引重建并行。最终事务必须重新验证源文件和 KB 状态，并有文档变更代号或同键互斥，避免重建覆盖中途人工编辑；不能只在开始时缓存对象。

### 6. 批量同步关联状态

`sync_files` 的 running 状态也需要可验证的批次归属，或在受控冷恢复中标记为 interrupted。不能仅凭若干子文档已 ready 就把整个批次标为 completed。

逐文件成功与批次计数之间目前有提交间隙。若下一批承诺自动恢复准确进度，需要持久化候选文件清单与每文件结果/attempt 关联，并按唯一结果计数，防止“文档成功后计数前中断”造成漏记或重记。若暂不加入逐文件账本，应明确只恢复文档可重试性，旧批次显示中断且计数为最后确认值，不冒充完整进度。

## FK-002：必须覆盖的隔离测试

全部使用临时磁盘 SQLite（多进程中断测试不能用各进程独立的 `:memory:` 库）、临时锁目录、内存模型/ES 桩；从仓库隔离 harness 进入。不得沿用任何在用 storage、业务库或服务端口。

| 场景 | 必须断言 |
| --- | --- |
| processing 提交后，解析前终止子进程 | 父进程等待真实 exit；新执行者取得同锁后可重试，单一文档与单一当前分块集，没有永久 409。 |
| 重建期间硬中断 | 原 ready 分块仍可搜索；恢复保留原 parser/status，新尝试成功后才原子切换。 |
| 长模型请求仍活跃 | 用事件屏障阻塞模型桩并把时间推进很久；第二执行者始终不能抢占，模型调用次数仍为 1。不能靠 sleep 判断时序。 |
| 两个恢复者同时到达 | 一个取得执行权；另一个清晰冲突；首次控制记录唯一约束也有效，不能形成两套文档。 |
| 旧 attempt 迟到成功/失败 | 改变 generation 后旧 finalize/fail 的 CAS 影响 0 行；不覆盖当前 ready、不删除新 attempt 的 SQL 或 ES。 |
| ES 写入后、SQL 切换前硬中断 | 新 ES 残留不能返回；旧 SQL 正文仍有效；清理仅针对中断 attempt。 |
| SQL 切换提交后、旧 ES 清理前硬中断 | 新文档保持 ready，重试不重复嵌入；旧候选被 SQL 复核过滤，清理失败不降级文档。 |
| 普通解析/向量失败 | 保留当前异常恢复语义；释放锁；首次失败可重试，重建失败保留上次可用内容。 |
| 用户停用/移出工作区、KB grant 撤销、客户端 scope 收窄/令牌失效 | 下一次恢复请求拒绝且零模型调用；若实现阶段重验，则在下一次模型派发和最终发布前拒绝，旧可用数据不被删除。 |
| File 撤权/删除、KB 删除、分段并发修改 | 当前授权或版本检查阻止发布；不复活被删除资源、不覆盖中途人工修改。原文/下载没有新增权限。 |
| 旧 processing 无 owner、不同主机/锁命名空间、锁目录失败 | 一律不自动抢占、不发送模型请求、不改旧正文；得到明确的待核验/锁不可用结果。 |
| 批量同步中途退出 | 已提交子文档保留；批次不假装完成；有逐文件账本才重建准确计数，且不重记已完成项。 |
| 普通 ready 文档重复导入 | 返回原 ID，模型/ES 调用次数为 0；控制表引入不破坏原幂等行为。 |

测试中的硬终止只针对测试亲自创建并持有句柄的子进程，等待回收；不能根据端口或名称杀进程。网络断开、客户端超时、心跳滞后本身都不是执行器死亡证明。

## FK-004：当前授权链和最小修改

独立窄范围只读复核确认以下链路：

| 环节 | HTTP | AI |
| --- | --- | --- |
| 动作授权 | `authorization.py:425–430` 将 `/api/knowledge/search` 设为 knowledge.view | `builtin_provider.py:368` 声明 knowledge.view，网关检查动作权限 |
| 身份/范围 | `deps.py:325–327` 按 request_permission 绑定 | `business_ai/context.py:48–51` 重建身份后绑定，没有 request_permission |
| File 来源授权 | `data_scope.py:141–150` 启用“可见 KB → 同工作区文档 → 引用文件”的知识域例外 | 回落到 files.view 的普通范围或恒假 |
| 查询 | 共用 `knowledge.service.search` | `_search_knowledge` 在 `builtin_provider.py:1190–1203` 直接调用，没有临时动作绑定 |
| 结果 | 授权共享库可返回 | `service.py:1139` 的 File INNER JOIN 在最终 SQL 复核/回退时过滤掉共享片段 |

最小修复仅给 **本次 AI 知识检索** 设置临时的 knowledge.view 范围：

1. 使用网关新解析的 AuthContext；缺失时调用现有 `auth_context_from_tool`，不从原始字典自造管理员或丢弃 client_id/client_scopes。
2. 显式 `require_permission(actor, "knowledge.view")`。
3. 保存 `Session.info` 中 `resource_access_criteria`、`actor_user_id`、`actor_workspace_id` 的原值及是否存在。进入临时范围时调用 `bind_data_scope(actor, "knowledge.view")`，执行现有 `search`。
4. `finally` 按原有/不存在状态恢复这三项，保留其它 Session.info。`bind_data_scope` 本身不修改 ctx.request_permission，不要永久把它改成 knowledge.view。
5. 不移除 File 联表、SQL 当前正文复核、KB/Document/File 工作区和状态条件、分段启停规则；不改通用 files.view，不改为 Core SQL 绕过 ORM 范围。

最小实现可在 `common/data_scope.py` 提供一个小型作用域上下文管理器，由 `_search_knowledge` 使用；如果只该点需要，可先在知识检索适配层局部封装，避免为修一个入口改写整个授权体系。两种实现只选一种，不叠加双套权限规则。

知识片段的来源授权并不等于原文授权。搜索返回的是普通结果字典，不能把临时范围里加载的 File ORM 对象交给后续工具当作授权证明。现有 `files.service.get_file` 使用带条件 SELECT，恢复范围后仍会查权限；`db.get` 命中 identity map 或手里已有对象都不能替代重新授权。

### FK-004 必须测试

| 场景 | 必须断言 |
| --- | --- |
| 共享库成功基线 | 他人拥有的 KB/File，成员分别只有 knowledge.view + files.view/self、只有 knowledge.view；真实 HTTP 与 ToolGateway 返回同一片段、正文和来源。不能用管理员或本人文件代替。 |
| 用户/角色显式 KB grant | knowledge.view/self 加显式 view grant 后成功；撤销后下一请求/工具调用不再返回，另一仍授权的库可正常回退返回。 |
| ES 残留与 SQL 回退 | 有效 ES 候选、空索引、已撤权候选、旧正文候选分别覆盖；旧索引不能绕过当前 SQL 授权。 |
| 跨工作区/未授权 KB | 直接给 KB/File ID、伪造 ES 来源、不给 KB 列表都不能获得未授权正文；沿用当前异常/过滤语义。 |
| 服务客户端 scope | 用户本身权限宽，client_scopes 缺 knowledge.view 仍拒绝；仅有知识权限时可以拿片段，不能获得文件原文、导出或 Artifact 权限。 |
| 原文与下载隔离 | 搜索成功后 `/api/files/{id}/text`、`/download`、AI read_file 仍拒绝他人原文件；分别测无动作权限和有 files.view/export 但仅 self 范围。独立授予文件权限后才成功。 |
| 成功/异常后的作用域恢复 | 同一 Session 搜索后执行 select(File)、get_file、真实 read_file、select(Artifact)；仍保持进入前范围。异常路径与嵌套范围也必须恢复，不能只依赖下一次网关重建身份。 |
| 删除/启停 | File 软删除、KB 失效、分段停用仍不返回，保持本轮 SQL 权威校验。 |

现有 `test_knowledge_search_authority.py:182` 只覆盖 HTTP 撤 grant、陈旧候选和下载动作拒绝；缺 AI 网关、已有文件动作但 self 范围、同 Session 恢复回归。

**撤权保证的准确范围：** 这个最小修复保证下一次能观察到撤权提交的新请求/新事务按当前权限判断。不能宣称同一次检索在 rerank 期间撤权也被立即收回：新的 AuthContext 不会打破 MySQL RR 旧快照，已经发送给重排服务的内容也无法撤回。如下一批额外要求该边界，必须单列“出站前与最终返回前用新短事务再鉴权”的变更与事件屏障测试，不混入一个 finally 绑定修复后宣称已实现。

## 下一批预计文件范围

- FK-004：`providers/tool/builtin_provider.py` 的 `_search_knowledge`，可选 `common/data_scope.py` 的作用域 helper；新增专门 HTTP/AI 共享权限隔离测试。无需改本批 `_current_chunk_query` 或 File 下载合同。
- FK-002：`knowledge/models.py`、新 `knowledge/ingestion_control.py`、`knowledge/service.py`，及 router/governance 中传递身份与批次收尾；如覆盖阶段撤权，增加最小公共纯鉴权 helper；旧实例静止证明属于启动/宿主协调的单独明确改动，不能悄悄推定已存在。
- 不引入普通线程池作为“恢复队列”，不沿用请求结束后被关闭的 Session，不把所有知识库重置成 failed，不自动重发未知副作用。

## 本次只读覆盖与限制

核对了知识导入/重建/批量同步/搜索、模型定义、文件读取与内容依赖、动作映射、数据范围、工具身份、Worker API 分发/生命周期、现有内核互斥和相关隔离测试源码。FK-004 已由独立只读代理交叉复核。

未进行真实数据库或模型调用、服务启停、进程硬终止实验或产品修改。本文件的测试清单是下一批验收要求，不是已通过结果；特别是多进程 SQLite 锁、MySQL 事务可见性和旧部署静止证明尚待实施验证。

设计快照 SHA256：

| 文件 | SHA256 |
| --- | --- |
| knowledge/service.py | `bf90c6330f63e6d4f735d9b7bf10bf01e1196b7aa681f5631b9270368c3479bb` |
| knowledge/governance.py | `881fe211a546612fecbe4d19054945bed61da3700cbe52f766bdd477cce5e2ad` |
| knowledge/models.py | `2930da2a4b19a69e237a671025842e78e81c82e6d0d8600192580ce7b6fe2e9c` |
| common/data_scope.py | `910673325e7033998390973077c45b178846aab2470a9be571f35c39ef2c1703` |
| common/authorization.py | `a8dd477faf94e8b4471bd405984fa34120d42a5c321781e48f15c65a7312c190` |
| modules/business_ai/context.py | `4faf7d87a5cb9ba534f9698e89cd86ca0aba40393f5afcd378c158555c4d9513` |
| providers/tool/builtin_provider.py | `3765c188a55cae0615fed4329baf9932702814e5c6c38ab9d70976aa3456479a` |
| system_tools/scheduler_lease.py | `ef29d3be39b71c93b8d5f8a5150101224aa6e53e39cd0fd2a8dd05edbf15d3d7` |
