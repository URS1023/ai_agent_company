# 批次十五候选：KG-004–008 图谱探索、抽取来源与物化契约

日期：2026-10-04。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-task-lifecycle`。只读时 HEAD：`782d346bc1e3df6a0e63dd199c0157c9ec47476b`，分支 `codex/major-task-lifecycle`。本报告只写 ignored 文件；产品、测试、正式 docs 未修改，未运行用例、启动服务、连接业务库或模型。旧 `major-capability-contracts` 树未修改。

本轮针对当前源码细化既有计划，不属于已验证修复。以下“当前表现”除明确标为既有批次证据外，均为调用链和分支的静态推导；三个首选反例尚未执行，不能计入批次十四的 73 项通过记录。下一批实际红绿仍须使用主树绝对 Python，经 `tests/run_isolated_backend.py` 建临时 SQLite，并使用内存索引/传输桩；不得访问真实库、模型、外网或 8011。

## 1. 建议顺序与最小所有权

| 顺序 | 问题 | 确认的具体分支 | 最小修改入口 / 建议测试 |
| --- | --- | --- | --- |
| 1 | KG-005 来源权限和状态 | `_load_chunks` 直接读 chunk，缺 File、文档、片段状态与完整关系校验；已有 KB 权限检查仍在 | `extraction.py:193–215,514–532`；新 `test_extraction_source_authority.py`，同时补旧非结构化正例的真实 File 行 |
| 2 | KG-006 同键复用丢值 | entity / relation 查到 existing 即计数并 continue，完全不比较已接受内容 | `extraction.py:751–882`；新 `test_extraction_materialization_contract.py` |
| 3 | KG-007 历史运行受当前 job 影响 | run 已保存固定本体和物化 graphId，但判定仍读可编辑的 job | 同一 extraction owner，复用上项测试文件 |
| 4 | KG-008 接受复核缺属性校验 | 只检查 ID、键、名称和 properties 为 dict；接受非法属性后，复用分支还能绕过写入验证 | `extraction.py:677–748` 及 materialize；新 `test_extraction_review_contract.py` |
| 5 | KG-004 探索中心点与截断标记 | 扩散后再按全图创建顺序截断，可能丢中心；恰好 limit 也标 truncated | 唯一 graphs owner 修改 `graphs.py:492–547`；新 `test_graph_explore_contract.py` |

KG-005/006/007/008 应由同一 extraction owner 串行落实，避免来源动作绑定、run 固定快照、候选验证和物化事务彼此覆盖。KG-004 可以另分派唯一 graphs owner，但须待本批冻结解除。共享纯属性验证函数和图谱父锁应先与 graphs/schemas owner 约定可复用接口，不在两处复制规则，也不让抽取模块导入 builtin provider。此计划不展开删除引用保护或其他问题。

## 2. 首选反例一：软删除原文件仍进入非结构化候选（KG-005）

建议用例名：`test_execute_excludes_soft_deleted_source_file`；先取这一条红，再参数化片段停用和整库/显式文档模式。

最短、可完全隔离的准备和 HTTP 链：

1. 复用 `test_knowledge_graph.py:54–75` 的 `create_published_ontology`，得到含“设备A/压缩机、部件B/轴承”识别词的正式本体。
2. 复用 `test_knowledge_search_authority.py:55` 附近的 `create_segment` 和该文件内存索引夹具，通过 `/api/knowledge`、`/api/knowledge/{kbId}/segments` 创建真实 KB→Document→File→Chunk；正文放入识别词和唯一哨兵“已停用来源不得进入抽取证据”。这些接口的嵌入与索引必须使用原夹具的内存桩。
3. POST `/api/v1/extractions`：`mode=unstructured`、该 `ontologyId`、`knowledgeBaseId`、`documentIds=[documentId]`、`mappings={}`。创建成功后，在临时 SQLite 中只修改此夹具 File 的 `deleted_at` 并提交；保留 KB active、Document ready、原 chunk，隔离唯一触发条件。
4. POST `/api/v1/extractions/{jobId}/execute`，使用最新 `job.version`；随后读取该 job runs 与该 run candidates。

当前源码预期：`_knowledge_source` 仍通过；`_load_chunks` 查到旧 chunk；确定性的 `_unstructured_specs:438–513` 直接识别文字并复制到 evidence，无需真实模型；请求成功进入 review，候选含原 chunkId/documentId/哨兵。原因是 `extraction.py:514–524` 没有 File 联表，也没有 `deleted_at/status` 条件。

修复目标：当唯一来源已失效时返回明确的“无可抽取文本”错误（保持现有无 chunk 的 422 契约即可），无候选落库。`execute_job:580–594` 在取来源前已经提交运行尝试，异常由 `535–553` 记录失败；因此应断言 run/job 为失败、候选数为零，不能错误要求“没有运行记录”。

第一条通过后增补：`documentIds=[]`；`KnowledgeChunkMetadata.enabled=false`；File 非 ready；整库模式下 Document 非 ready；KB 非 active；一份有效和一份无效来源并存，成功候选 evidence 只能含有效来源。无效源必须在计数/文本容量限制前过滤，防止无效 chunk 先占据 limit 并挤掉有效文本。显式选择的非 ready 文档当前会先拒绝，测试应保留这一路径，不把已有防线描述成缺失。

重要夹具问题：`test_knowledge_graph.py:205–224` 现有非结构化正例只有 `document.file_id=new_id()`，没有 File 行。下一批必须补齐它；不得为了保持这个不完整夹具通过而允许无原文件的 chunk。

### 2.1 最小安全实现与权限必测项

`_knowledge_source:197–201` 已执行 `require_permission(knowledge.view)`、workspace/active 检查和 `require_resource(knowledge.view, kb)`，本项不是“KB 完全无授权”。需要把这些已有检查变成可靠的当前 ORM 读取，并在限定阶段完成来源 SQL 校验：

- 保留当前调用者 AuthContext 的 user/workspace/client ID/scopes/令牌约束；不能复制管理员身份。读取 KB 时宜使用显式 select + `populate_existing=True`，避免 `Session.get` 直接返回缓存对象；这只解决 ORM 缓存，不突破 MySQL RR。
- 仅在加载已授权知识来源的阶段临时 `bind_data_scope(ctx, "knowledge.view")`。现有 `common/data_scope.py:141–149` 会把可见知识库引用的原文件纳入这一知识动作，不要求用户因此拥有通用 files.view。不能跳过 File 授权、关闭 loader criteria 或扩大通用 files.view。
- 完整关联至少为 `chunk.document_id=Document.id`、`Document.knowledge_base_id=chunk.knowledge_base_id`、`chunk.knowledge_base_id=KB.id`、`Document.file_id=File.id`；对每个含 workspace_id 的来源模型约束当前 workspace。KB active；File ready 且 deleted_at 为空；Document 必须遵守明确的抽取可用状态；metadata 缺省或 enabled=true。`knowledge/service.py:1115–1142` 的 `_current_chunk_query` 可作为完整关系与条件的参照，但它允许 Document ready/processing，不能未经说明直接当作抽取规则。当前抽取显式选文档要求 ready，建议整库也统一 ready。
- 临时 scope 保存/恢复 `db.info` 的 `resource_access_criteria`、`actor_user_id`、`actor_workspace_id` 三字段，并区分“原键不存在”与“原值为 None”；覆盖绑定时异常和查询时异常，嵌套作用域也应正常恢复。现有 `bind_data_scope:85–164` 只写这三字段，不改 `ctx.request_permission`，所以无需也不应把 ctx 的动作永久换成 knowledge.view。若新 helper 额外变更上下文，必须显式恢复额外副作用。
- `builtin_provider.py:1191–1228` 已有局部恢复范式，可照其边界设计；不要引入 provider 依赖或搜索 top_k/rerank，抽取需要全量的有界可用片段集合。

权限用例必须另外覆盖：普通 self 范围成员创建自己的本体/任务，使用管理员所有的 KB 原文件，凭显式 KB grant 可以抽取。成员需真正具有本体/抽取对应权限；`test_knowledge_tool_scope.py:39` 的 reader 默认只有 home/knowledge 等权限，不能照搬后把 extraction 的 403 误判为来源问题。再覆盖 grant 撤销后的下一次请求、角色授予撤销、服务 client scopes 缺少/拥有 knowledge.view、跨工作区 KB/Document/File、chunk 与 Document 的 KB 不一致。重复请求需新建/更新对应合法版本，避免版本错误掩盖授权错误。

同一 Session 在抽取成功和异常后，`read_file`、`selectFile`、Artifact 与下载仍须拒绝该成员访问原文件。刻意保留已加载的 File 身份映射对象再调用原文入口，验证 scope 恢复后没有缓存旁路；不得仅证明 db.info 相等便宣称隔离成功。当前 get_file 的强制查询和来源查询的 populate_existing 是防线，应纳入回归，不建议批量 expunge 破坏整个会话。

“撤 grant 后下一调用拒绝”与“本次运行加载后、处理中立即撤权”是两个不同契约。新建 AuthContext 不会突破同一 MySQL RR 事务快照；若产品要求后者，需要独立新事务/Session 再验及明确失败/审计处理，本批计划不虚构这一保证。

## 3. 首选反例二：同业务键实体的已接受新值被静默丢弃（KG-006）

建议用例名：`test_materialize_rejects_existing_entity_with_different_confirmed_values`。

1. 使用 `test_graph_property_contracts.py:24–51` 的最小本体/图谱夹具，或对现有 equipment 本体使用 string `status`。先通过 POST `/api/v1/graphs/{graphId}/entities` 建 `businessKey=E-1,name=旧名称,properties={"status":"旧值"}`，保存实体 ID 与版本。
2. 通过正常结构化抽取生成同 concept、同 `E-1`、`name=新名称,properties={"status":"新值"}` 的候选。沿 `structured_payload:78–99` 的 `entityMappings` 指定 `keyField/nameField/propertyFields`，不直接伪造“成功物化”状态。
3. POST execute，GET candidates，POST `/api/v1/extractions/{jobId}/runs/{runId}/review` 接受全部候选，再 GET 目标图谱版本，POST `/api/v1/extractions/{jobId}/materialize`，参数含 runId/graphId/graphVersion。

当前源码预期：`extraction.py:790–800` 查到原实体，直接 `reusedEntities += 1`，run 变 completed；图谱实体仍为旧名称/旧属性，已确认值没有被执行。修复目标为 409 清晰业务冲突，既有实体内容不变，run 不得虚假 completed。最小修复不要擅自改为覆盖既有实体。

事务加强版：让一条全新实体候选先处理，随后才遇到同键差值冲突。候选排序仅为 `kind,created_at`（`:773`），时间相同不能声称稳定先后；隔离夹具可给候选设置明确递增时间，或读取真实顺序后按顺序布置冲突。沿 `test_graph_property_contracts.py:86–128` 的 SQL 事件记录证明前一新实体 INSERT 确实执行，再断言事务回滚后新增实体数为零、既有实体和 run/job 成功摘要未变化。只看最终行数为零不能证明发生过写后回滚。另可对最终提交设置一次受控故障，验证无半成品，不用模型异常代替提交异常。

对应正例：同业务键、同规范化名称及同业务属性重复物化应返回 reuse，实体 ID/数量和业务内容稳定。不同 provenance/evidence 等审计来源不宜自动算业务冲突。属性比较必须使用固定定义的类型口径，避免 Python 的 `True == 1` 被误当同值；先验证候选，明确日期/number/json 等采用批次十四的何种等价规则，而非裸 dict 相等。缺失与显式 null 是否等价也应明确，不宜无说明地抹掉差别。

## 4. 首选反例三：同端点关系的已接受属性被静默丢弃（KG-006）

建议用例名：`test_materialize_rejects_existing_relation_with_different_confirmed_properties`。

1. 使用最小正式本体：Source/Target 两概念，关系类型采用 many_to_many，声明 string `note` 或 integer `amount` 属性。先经 HTTP 创建两个实体与 `properties={"note":"旧值"}` 的关系。
2. 结构化输入的实体业务键、名称、属性与两条已有实体完全一致，避免先触发实体冲突。`relationMappings` 指向相同 relationTypeId/端点，并用 `propertyFields={"note":"relation_note"}` 从记录抽取“新值”。
3. 沿 execute→candidates→accept→materialize 正常 HTTP 链，使用最新 graphVersion。

当前源码预期：`:824–834` 找到同类型、同端点关系后直接 reused，run completed，但关系仍为旧值。修复目标为 409、全批回滚，实体/关系数与原内容不变。因为两实体和关系全都复用，这一反例同时暴露：批次十四加在 `save_entity/save_relation` 的父行锁及严格属性检查不会在该路径执行，不能把已有 helper 修复当作 materialize 已全部受保护。

保持相同属性的正例应幂等复用。关系无属性的既有夹具不能证明此缺陷；many_to_many 是为了不让端点基数校验先遮蔽复用问题。扩展为复核接受错误类型的 relation 属性，可联动验证 KG-008，但首条红例应使用合法类型的新值，把“丢值”和“类型错误”分开。

## 5. 物化公共修复顺序：锁、固定运行、验证、复用

建议整个 materialize 维持一个事务，按以下顺序执行；此处是待实现设计，不是已测数据库并发保证：

1. 当前授权下读取 job，校验 run 确属 job/workspace。对该 run 做带 workspace/job 条件的 `FOR UPDATE` 当前读、`populate_existing=True`，再判断 run 状态及首次物化目标。若要求同 run 不得并发写两个不同图谱，必须由 run 锁或等价数据库原子条件保护；只锁各自目标图谱不能串行这两个请求。
2. 调用协商后的图谱写锁接口，采用当前 `graphs.py:217–229` 的授权父行锁逻辑，获得当前 graph 后再检查 graphVersion。锁顺序固定为 run→graph→按确定顺序的图谱子行；手工图谱写入为 graph→子行，不反向申请 run。更改 job/run 的其他抽取入口也需检查相容性，不把单一入口的顺序当作全局无死锁证明。
3. 本体取自 `run.input_snapshot` 的 ontologyId/ontologyRevision；先校验其与目标图谱固定版本相同，并按同一快照验证每个已接受候选的实体/关系业务属性。需要保留当前目标资源权限，不能因持有旧快照就恢复源资源权限。
4. 在父锁之后，对业务键实体和同端点关系做当前锁定读取并 `populate_existing=True`；按固定定义和规范化字段比较。完全相同才 reuse，不同返回 409；非法候选即使已有同键行也须拒绝。历史已有行自身非法或无法按固定定义解释时，应明确冲突，不能默默覆盖或视为同值。
5. 不存在时继续调用共享 `save_entity/save_relation(...,commit=False)`，保持批次十四唯一属性检查和 parent→child 锁规则。原来 `existing` 查询在 helper 之前，必须移入受锁定阶段；全复用分支也必须受保护。
6. 图谱写入、run 自身首次目标和成功状态、job 最近结果、audit 在同一事务提交，异常整体 rollback。不要在候选循环或新 helper 中提前 commit。重复成功执行不能把首次目标字段改成其他图谱；重复请求的 audit/events 是否追加需明确，不影响业务幂等。

批次十四提供了临时 SQLite 的实际 SQL/回滚证据和 MySQL 方言 `FOR UPDATE` 编译断言；它没有实际验证 MySQL RR、并发锁等待或死锁。本批计划同样不把 SQLite 的成功当作这些保证。下一批隔离测试可以验证调用顺序、SQL 编译、全复用路径也锁父行，以及强制错误后的行/状态原子性；MySQL 并发同键创建、同 run 不同目标、目标版本冲突和事务快照必须另行在授权的本地 MySQL 环境验证，未获授权前不连接。

## 6. KG-007：run 的固定本体与首次目标不可被 job 改写

准确触发：`execute_job:571–579` 已把本体 ID/修订记入 `run.input_snapshot`；`save_job:240–248` 编辑任务会换本体并清空 `target_graph_id`；`materialize:765–768` 却读当前 job 的本体和目标。

反例 A：本体 A 生成并接受 run1；使用最新 job.version 编辑任务为合法本体 B 与 B 映射；将 run1 物化到固定 A 的图谱。当前错用 B 比较而拒绝；目标按 run1 的 A 判定，当前授权仍须通过。

反例 B：run1 已成功到 G1；编辑相同 job（清空目标）后，以最新 G1 version 重放 run1→G1，当前错误 409。再让 run2 成功到 G2，当前 job 目标为 G2：run1→G1 仍错误拒绝；run1→G2 在本体相同且候选满足其他条件时会被错误允许。这后一项必须有负例，不能只修“原图谱可以重放”。

最小记录复用：`run.summary.materialization.graphId` 在 `:853–865` 已存在，可作为该 run 自身首次成功目标；input_snapshot 本体作为固定依据；job.target_graph_id 仅代表最近任务结果。历史缺失或畸形 run metadata 应有明确兼容策略或诚实拒绝，不能无条件回退当前 job 重新解释历史。若引入专用列/唯一约束/迁移，须先协调模型 owner。原授权、跨工作区 runId/graphId、错误 graphVersion、已删除图谱均需保留失败回归。

## 7. KG-008：接受复核必须使用 run 固定属性定义

`_validate_review_payload:677–701` 只校验概念/关系 ID 不变、字符串业务键/名称、端点候选键和 properties 是 dict。`review:724–731` 先收集整批验证结果，`:733–747` 才写入，这个两阶段结构应保留。

最短 HTTP 反例：先正常执行生成候选；review accepted 时复制候选 payload，把已声明 integer 属性改为字符串、增加未知属性、删必填值、给非法日期/枚举之一。当前可接受；修复应为 422，整批 payload/reviewStatus/version/reviewedAt 和 run summary 不变化。合法项放前、非法项放后，才能证明整批原子；另外测试不传替换 payload 而接受非法原始属性，防止只验证人工改写。坏候选可被 rejected 留作审计，不应因属性无效而无法驳回。

复用批次十四 `graphs.py:183–214` 的固定属性定义解析/校验及 schemas 类型口径；这段本身不读取 ORM，可由 owners 协商提为纯共享 helper，保持错误契约一致，不复制日期/枚举/数值精度逻辑。定义来自 run.input_snapshot 指向的正式本体修订，不读当前 job 或草稿。materialize 仍须独立验证全部接受记录，包括 existing 分支和历史已接受数据。review 不负责承诺未来目标图谱的 unique 冲突已经锁定；唯一性最终在写入事务门禁处理。

## 8. KG-004：探索结果中心点和 truncated

当前 `graphs.py:498–500` 加载全图有效节点/边；`:502–505` 把中心加入集合，`:513–523` 扩散，`:527` 又按全图节点顺序截断，`:546` 以 `len(selected)>=limit` 标截断。

小图反例：先创建三个 Component，再创建中心 Equipment，分别以 one_to_many 边连到中心（每个 target 只用一次），GET `/api/v1/graphs/{graphId}/explore?entityId={centerId}&depth=1&limit=2`（路由 `router.py:346–352`）。静态推导旧节点占满结果而后建中心消失。目标中心必留，邻居按 BFS 层级再用稳定键排序；返回每条 edge 的两端必须在 nodes 中。

另以恰好两个符合节点、limit=2 验证 truncated=false；三个符合而只返回两个才 true。明确 truncated 仅指因 limit 漏掉本次深度/查询范围内合格节点，深度外节点不算。覆盖 depth=0、孤立中心、query 多种子/零命中、普通列表模式、重复顺序稳定。这个修复不改变“先加载全图”的成本，不能借小图测试声称性能或数据库工作量已有上限。

## 9. 实际读取范围、快照哈希与未验证事项

本轮主要精读 extraction.py 的 193–255、438–642、677–882；graphs.py 的 183–246、492–547；data_scope.py 的 85–164；knowledge/service.py 的 1115–1142；builtin_provider.py 的 1191–1228。对 router 以函数/路径定位读取相关段，不声称全文复审。测试读取 `test_knowledge_graph.py:54–100,205–242`，`test_graph_property_contracts.py` 的 helper 与回滚/锁断言段，`test_knowledge_search_authority.py:1–70`，`test_knowledge_tool_scope.py:1–62`。正式计划本轮读取 72–148（最后的删除引用段仅理解已有边界，未纳入当前任务）。schemas 的批次十四实现和验证沿用被冻结交付，本轮仅定位/哈希，没有宣称新行为重测。

以下为整文件 SHA-256，不表示已全文读取；用于根代理把本计划绑定到实际源码快照：

| 文件 | SHA-256 |
| --- | --- |
| backend/app/modules/knowledge_graph/extraction.py | `0B6716564CE3C642F17D54AADF6982D24AA17C1F29F9831EA522A88A457CA802` |
| backend/app/modules/knowledge_graph/graphs.py | `15410F64B31F120E3973D7C0B9657DF0AB9044153E046011876FCDD6E6DC2003` |
| backend/app/modules/knowledge_graph/schemas.py | `C81932E3C2DDCB41DB326D412AC81B418DDF10B98C084EFA34BDFB8CAC9747C1` |
| backend/app/modules/knowledge_graph/router.py | `27415EE2FD823982A3B49A3C2F41DE4592D6AE4A0B4F760EA57A4C118F6C5413` |
| backend/app/common/data_scope.py | `910673325E7033998390973077C45B178846AAB2470A9BE571F35C39EF2C1703` |
| backend/app/modules/knowledge/service.py | `65A4985B242E18EA8C576B32FFA4E12CB9CF4C4B6DF04DF151660BEA8580F7F2` |
| backend/app/providers/tool/builtin_provider.py | `DFC6F95CBB1A710306231E0A1A43AB5511D111C0443B5801E9D9C2989646CBF5` |
| tests/backend/test_knowledge_graph.py | `7637FA96C2323B1A2AFE0652CC128B7C8E8D0E6E05B3F0E4D83DCAC62272D32B` |
| tests/backend/test_graph_property_contracts.py | `3962D5F8C1DA62C68F81D99E5F0B32C51C952283072A2B87CD673A8263C0802B` |
| tests/backend/test_knowledge_search_authority.py | `287E1F7EA200558E9A4C16F77D1B995A26B94CD7470AF11147F126605485920E` |
| tests/backend/test_knowledge_tool_scope.py | `2E58837660F002553236E309D0F8C2A88FACB37E51B8E4C868341913AB2FA90D` |
| docs/audit/review_20261004/plan-graph-properties.md | `DA69B11379EF9D19B52864A398904800075712426C867FBE46A0852FF76C6DC2` |

尚未验证：上述新反例实际 HTTP 状态/响应、全部正反例、MySQL 实际锁与隔离行为、外部并发撤权中途生效、性能、浏览器展示。当前可确认的是具体读取/比较分支缺失及其可达调用链；只有下一批按隔离 harness 执行获得红绿后才可升级为动态验证结论。报告不新增运行时授权，不承诺未实施的并发保证。
