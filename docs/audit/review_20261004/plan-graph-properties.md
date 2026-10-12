# 下一批图谱属性、抽取与删除依赖：只读实施计划

日期：2026-10-04。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-capability-contracts`。本报告承接 `docs/audit/review_20261004/followup-graph.md` 的 KG-001–008 和 Bot 审查交叉项，汇总下一批可分配的复现与实施边界。

**这是只读计划，不是修复完成或新测试通过报告。** 本次仅写当前 Git 忽略的 Markdown，不修改源码、测试、配置、索引或数据库；没有启动服务、执行测试、连接模型或网络。批次十三 Bot 源码与新测试的冻结 SHA 未变。共享工作树中其他参与者的文件未改动或还原。原批次十三 30 项测试通过不算这里各项的动态验证。

## 1. 执行边界与共同夹具

下一批仍只能使用主树已存在解释器 `E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe`，从独立树经 `tests/run_isolated_backend.py` 运行明确的测试文件。该入口强制内存 SQLite、一次性本机存储，禁用 MySQL/PostgreSQL 驱动和外网、回环 9/8011/8012。HTTP 采用 FastAPI TestClient 进程内路由链，不启动监听服务。新测试本轮未创建，以下名称只是建议。

可复用代码：

- `tests/backend/conftest.py:68` 的 client、`:79` 的 administrator、`:87` 的 db：完整认证、事务与权限链，数据库为 `sqlite:///:memory:`。
- `tests/backend/test_knowledge_graph.py:48` 的 result（读响应 data）、`:53` 的 create_published_ontology、`:78` 的 structured_payload。
- 同文件 `:103–202` 的本体→结构化抽取→接受候选→物化→图谱发布→公开 Bot Key 调用链；`:267–299` 的固定本体修订约束对照。
- `tests/backend/test_knowledge_search_authority.py:15–65` 的 MemoryIndex、index 与 create_segment：使用真实 HTTP 创建知识库/分段，只桩外部索引和向量边界。
- `tests/backend/test_knowledge_tool_scope.py:30–54` 的 shared、reader、grant 可参考普通成员、显式资源授权、管理员原文件的关系；无需把整个检索测试模块当作新生产依赖。

本体属性用例可先调用 create_published_ontology，随后 PUT `/api/v1/ontologies/{ontologyId}/concepts/{conceptId}`，提交当前 concept.version 与 code/name/attributes，再 POST `/api/v1/ontologies/{ontologyId}/publish`，最后 POST `/api/v1/graphs` 指定 ontologyId，让新图谱绑定新修订。读取最新 HTTP 响应中的 version，不硬编码连续请求后的版本号。也可在新测试文件建立未发布的最小本体 helper，避免多余修订；保持真实 HTTP 建模、发布和图谱写入路径。

## 2. KG-001：unique 属性没有执行

证据：`schemas.py:21` 接收 unique；`graphs.py:181–206` 仅检查必填、原生类型及枚举；`:227–263` / `:296–342` 保存实体与关系，没有按 unique 属性值检查其他记录。

### HTTP 红灯和绿灯

1. 定义 `{name: serial, label: 序列号, type: string, unique: true}` 并发布。对同 graphId/conceptId 连续 POST `/api/v1/graphs/{graphId}/entities`，提交不同 businessKey 和相同 `properties.serial = SAME`。
2. 源码推导当前两次均 201；目标是第二次 409，后续 GET `/entities` 仍只有首项，首项内容与版本未变。不要用相同 businessKey，否则会先触发既有业务键唯一约束，未复现属性 unique。
3. PUT `/entities/{entityId}` 改成另一实体占用的 serial 应 409；仅更新同一实体其他字段、serial 保持原值应成功，避免把自己判重。
4. 关系属性采用 many_to_many，创建两组不同端点，再 POST `/relations` 提交相同 unique 属性。避免相同三元组或 one_to_many 基数先阻断。PUT 自身/冲突路径同实体。
5. 正例覆盖不同概念、不同图谱、不同工作区不会互相冲突；权限不允许跨工作区读取。可选值 null、空字符串、大小写、数字 1/1.0 的唯一口径要先显式确定，不能悄悄由 Python 字符串化决定。
6. 先建立固定旧修订图谱，再编辑/发布本体 unique 设置，确认旧图谱仍按其固定定义检查，不能拿当前草稿或新修订替代。

### 物化与事务

在 structured_payload 的 equipment `propertyFields` 增加 serial，records 使用两个不同 equipment_id、相同 serial，经 POST `/api/v1/extractions` → `/{jobId}/execute` → GET `/{jobId}/runs/{runId}/candidates` → POST `.../review` 全部接受 → POST `/{jobId}/materialize`。

冲突应使物化整次失败：实体/关系行数和图谱版本/更新时间不发生半提交，run 不变 completed，job 不新增成功 materialization 摘要。至少让首项合法写入、后项冲突，才能证明异常路径回滚前项，不能只测首项失败。`graphs.py:261–262/340–341` 的 commit=False 当前会 flush，下一候选能查询到同事务前项；`extraction.py:787–882` 包含统一 try/rollback。保留该可见性，不因 autoflush=False 漏掉批内重复。

### 所有权和并发边界

建议由属性校验 owner 负责 `graphs.py` 的共用唯一值校验、`schemas.py` 的定义契约和新 `tests/backend/test_graph_property_contracts.py`；抽取 owner 只通过既有 save_entity/save_relation(commit=False) 使用该约束。不要复制两套判重算法。

唯一性不是“先 SELECT 再 INSERT”即可证明。若选择锁图谱父行串行化，所有手工创建/更新与物化必须在同一事务按一致顺序持锁，直到提交；仅某条路径 FOR UPDATE 没有整体保证。SQLite 顺序请求和事务回滚测试不证明 MySQL 行锁、隔离级别或竞态安全。若采用独立约束表和唯一索引，需要明确的模型/迁移所有者及既有重复数据策略。本次没有 MySQL 并发复现，也不批准连接业务库或扩大测试范围。

## 3. KG-002：日期只查字符串，枚举与数值类型不相容

证据：`graphs.py:200` 把 date/datetime 仅判为 str；`schemas.py:23–32` 的 enumValues 是 list[str]，而 `graphs.py:197–205` 要求 integer/number/boolean 使用原生类型，再直接与字符串枚举比较。

### HTTP 复现矩阵

- date：建模/发布后 POST 实体或关系 properties 分别传 `2025-02-30`、`不是日期`；源码推导当前可保存，应 422 且行数不变；合法 `2024-02-29` 为正例。
- datetime：传 `2025-01-01T25:00:00`、无时间的日期、任意字符串；前两项是否接受由明确 datetime 格式契约确定，任意字符串必须拒绝。增加合法秒、偏移或 Z 的正例，别把“解析器能解析”当作全部业务规范。
- integer 枚举分两层：定义 `enumValues:[1]` 当前请求模型就 422；定义 `enumValues:["1"]` 可以保存，但属性值 1 在执行层不属于字符串枚举而 422，属性值 "1" 又不满足 integer。boolean 与 number 同样参数化。
- 必须验证数字与布尔没有互相串通：Python 的 bool 是 int 子类且 True == 1；保持类型判断与枚举比较一致。不要把所有业务值强制转字符串。
- 正例包括无枚举、合法/非法枚举值、可选 null、必填遗漏、实体和关系属性。创建与 PUT 失败都检查内容、版本不变。

### 最小实现边界

AttributeDefinition 按声明 type 校验枚举；共用属性校验器按同一规则验证原生业务值。可在 knowledge_graph 内独立纯函数模块抽取类型规则，供 schema、graphs 与后续 review 共用，避免依赖倒置或循环导入。date 应定义 ISO 日期格式，datetime 应明确需要时间以及时区语义；date.fromisoformat / datetime.fromisoformat 的宽松接受范围不能直接当接口定义。

已发布旧快照可能带字符串数字枚举，需要显式兼容策略：可以按固定定义在内存中严格解释合法旧枚举，但不能改写历史 snapshot，不能接受任意字符串转换失败后降级放行。新建定义、旧固定修订与新版定义都要有回归。该策略由下一批 owner 说明；本轮未作产品契约选择。

## 4. KG-003：关系属性重名后末项覆盖

证据：ConceptInput 在 `schemas.py:65–74` 有 name 去重，RelationTypeInput `:77–88` 没有；`ontology.py:257` 原样保存；`graphs.py:185` 用字典折叠同名字段。

POST 或 PUT `/api/v1/ontologies/{ontologyId}/relation-types`，attributes 先放 `{name:x,label:数量,type:integer,required:true}`，再放 `{name:x,label:文本,type:string,required:false}`。源码推导当前保存后发布，图谱关系 `properties:{}` 或 `{x:"任意文本"}` 会按末项通过。修复目标是在保存前 422：POST 无新增关系类型；PUT 原定义、版本不变。增加反转顺序用例，防止只处理一种顺序。

最小修复可共享 name 去重逻辑，但不要把 ConceptInput 的 businessKey 约束套在关系类型上。字段名按当前大小写契约判断，不顺手变成大小写不敏感。共享属性定义消费端也应拒绝已有重复定义，避免已发布歧义快照继续静默取最后一项；对旧数据返回明确字段错误，不重写历史版本。该防御与 KG-002 共用纯校验器，由同一 owner 整合。

## 5. KG-004：探索结果丢失中心点与错误 truncated

证据：`graphs.py:443` 把中心点加入 selected，`:451–462` 扩散；`:465` 又按全图创建顺序截断；`:484` 仅以返回数量 >= limit 判断 truncated。路由 `router.py:346–352` 接收 entityId、depth 0–4、limit 1–500。

复现只需小图：先创建三个 Component，再创建中心 Equipment，用三条 one_to_many 关系连到中心（每个 target 只被使用一次）。GET `/api/v1/graphs/{graphId}/explore?entityId={centerId}&depth=1&limit=2`。源码推导当前前两个老节点占满返回，后创建中心点消失。目标是中心必留，其余按 BFS 层级和确定性键排序；所有 edges 的两端必须出现在 nodes。

另建恰好两个可选节点，limit=2 且没有其他符合条件的节点，应 truncated=false。三个符合节点、limit=2 才 true。覆盖 depth=0、中心无邻居、query 多个种子、query 无命中、不带 query/entityId 的列表模式、重复请求顺序稳定。要定义 truncated 表示因 limit 漏掉符合本次深度/查询的节点，不把主动 depth 限制外的节点一概记作截断。

修复所有权：`graphs.py:430–484` 与新 `tests/backend/test_graph_explore_contract.py`，可由 graphs owner 一并处理 KG-001 与删除保护，避免多人同文件修改。当前实现先全量取节点/边，limit 不是数据库工作量上限；小图逻辑回归不能宣称解决性能问题。本次不做大数据/压测。

## 6. KG-005：非结构化抽取未核对当前来源状态

证据：`extraction.py:193–215` 仅显式 documentIds 检查文档 ready；`:514–524` 直接选 KnowledgeChunk，未联表 File、Document、Metadata 状态。`:601–603` 将结果喂入确定性的 _unstructured_specs，并不需要真实模型。

### 夹具和 HTTP 链

优先复用 create_segment 创建完整 KB→Document→File→Chunk 关系，正文含 create_published_ontology 的识别词，例如“设备A压缩机与部件B轴承”。如果采用 ORM 合成，必须实际插入 ready File，不能仅伪造 file_id。**既有 `test_knowledge_graph.py:205–224` 非结构化正例只给 Document.file_id=new_id，没有 File 行**；下一批补齐来源联表校验时，必须同时修正这条夹具，不得为让旧测试过而放宽 File 校验。

POST `/api/v1/extractions` 使用 mode=unstructured、ontologyId、knowledgeBaseId，分别覆盖 documentIds=[] 整库和 [documentId] 显式选择。在创建任务后、下一次 POST `/{jobId}/execute` 前提交来源状态变化：File.deleted_at、File.status!=ready、Document.status!=ready、KnowledgeChunkMetadata.enabled=false、KB inactive；每项独立测试，并保留合法来源对照。显式文档状态路径可能在保存阶段先拒绝，因此在任务已存在后变更更能证明执行时重验。

若有一个有效和一个无效来源，成功结果候选 evidence 中不应出现无效 chunkId/documentId/正文哨兵；若全部无效，应明确无可抽取文本且无候选，运行失败记录诚实存在。重启下一次调用后的授权撤销测试用普通 self 范围成员 + 显式 KB grant，然后撤 grant；不能只拿管理员测试 source 状态。

必须覆盖合法共享 KB 的管理员原 File：没有 files.view 或仅 files.view/self 的被授权成员仍可抽取已授权片段；抽取后同 Session 的 read_file/selectFile/Artifact/下载不因临时知识范围获得权限。再加跨工作区 KB/File/Document 以及 Chunk 与 Document 的 KB 关系不一致防御，避免仅查 ID 而跨关联取文。

### 最小修复所有权

抽取 owner 负责 extraction.py 的来源校验与 _load_chunks SQL，新增 `tests/backend/test_extraction_source_authority.py`，以及修正旧非结构化测试 File 夹具。保持知识授权与 File 授权例外仅限读取知识来源的阶段；不能移除 File 授权或扩大通用 files.view。

可在 extraction 内用有界临时知识 scope，保存/恢复 db.info 三字段 resource_access_criteria、actor_user_id、actor_workspace_id；不要把原 ctx.request_permission 长期改成 knowledge.view。失败/嵌套也必须 finally 恢复。查询需当前授权和完整关系/状态校验；若复用共用知识来源 helper，必须先确认接口语义，不能直接借搜索 top_k/rerank 等路径替代抽取加载。

Document ready 与 processing 的口径要统一说明：抽取现有显式 documentIds 要求 ready，知识检索可能另有 processing 保留旧正文规则。本项不能未经说明把较宽检索规则等同于抽取规则。阶段内外部事务撤权仍受 MySQL RR 约束，下一请求验证不代表中途立即撤权。

## 7. KG-006：同键复用静默丢弃已确认值

证据：`extraction.py:790–800` 实体按 graph/concept/businessKey 找 existing 即 reused；`:824–834` 关系按类型/端点找到即 reused，均未比较候选的已确认业务内容。

复现先通过 `/graphs/{graphId}/entities` 建 EQ-001，name 或 properties.status 与 structured_payload 中的值不同。生成并接受同键候选，再 POST `/extractions/{jobId}/materialize`。源码推导当前返回 completed、reusedEntities++，但 GET 实体仍为原值；该结果未执行用户确认内容。关系正例在本体定义属性，预建相同端点/类型关系但属性与候选不同，避免只有空 properties 无法证明差异。

推荐最小行为是 409 明确冲突且整批回滚，不擅自覆盖已有图谱；若产品要允许更新，必须独立确认与版本约束，不能把“reuse”改成静默覆盖。按规范化后实际持久化的 businessKey/name/properties 比较，provenance 等审计元数据不宜默认作为幂等差异。完全相同内容重放应幂等，行数不增加，仍返回正确 reused 计数。

事务用例令一条新实体先写入，后一实体/关系与 existing 冲突；冲突后新增行、更新和成功摘要都回滚，run 保持 review。候选排序来自 kind/created_at（`:773`），需要控制创建顺序或记录候选顺序来证明冲突确实发生在至少一次写入之后。已有 target 不应被异常处理删除。必要时在隔离测试对末段提交设置受控故障，验证没有半成品，不把模型失败当作数据库提交失败。

所有权：extraction.py 的 materialize，同一 owner 处理 KG-007/008；测试 `tests/backend/test_extraction_materialization_contract.py`。并发同键复用/创建的原子保证另行验证，单请求回滚不是并发证明。

## 8. KG-007：旧运行被当前任务配置和目标影响

证据：`extraction.py:571–579` 保存 run.input_snapshot 的 ontologyId/ontologyRevision；`:240–248` 编辑任务会改本体并清空 target_graph_id；物化 `:765–768` 却按当前 job.target_graph_id 与当前 job 本体判断。

复现 A：按本体 A 生成 run1 并接受，PUT `/extractions/{jobId}` 改为本体 B（提交有效 B 映射及最新 job.version），再 materialize run1 到绑定 A 的图谱。源码推导当前错用 B 导致拒绝；应按 run1 的固定输入判定，依然要求当前用户有目标图谱和必要资源权限。

复现 B：run1 已 completed 到图谱 G1；仅编辑同一 job 会把 target_graph_id 清空，或执行 run2 并物化到 G2 会把它改为 G2。用当前 graphVersion 重新物化 run1→G1，当前会错误 409；更需测试 run1→G2 不得因为 job 最近目标恰为 G2 就被接受。目标应锁定 run1 自身首次成功物化记录，与当前 job 无关。

最小实现可读 run.input_snapshot 的固定本体和 `run.summary.materialization.graphId`（`:853–865` 已落 run 自身），并在首次成功时与图谱写入同事务记录。job.target_graph_id 只做“最近结果”展示，不作历史运行授权/幂等依据。旧历史记录缺完整 run metadata 时应明确兼容或拒绝，不能无条件回退到当前 job 重新解释历史。若改为专用列/关联表则扩展到 models/migration owner，需记录迁移边界。

所有权同 KG-006；测试同 `test_extraction_materialization_contract.py`。涉及正常权限、跨工作区 runId/graphId、错误 graphVersion、重复物化的正反例。不要因历史快照而绕过当前资源授权或恢复已删除图谱。

## 9. KG-008：复核接受只校验外形

证据：`extraction.py:677–701` 仅校验概念/关系 ID、名字/业务键非空和 properties 为 dict；`:724–731` 先验证整批、`:733–747` 才写入这一结构值得保留，但校验未用 run 固定本体定义。

经正常 HTTP 抽取拿到候选，POST `/extractions/{jobId}/runs/{runId}/review`，decision=accepted，payload 复制原 candidate.payload 后将已声明 integer 属性改为字符串、加入未定义属性、删除必填值或给非法日期/枚举。关系候选同样修改其属性。当前源码推导可接受；目标是 422 字段错误，整批每一项的 payload、reviewStatus、version、reviewedAt 与 run summary 不变。

至少将合法候选放在同批前项、非法候选放后项，验证现有“全部验证后再修改”的原子性；只测一个非法项不能证明整批不变。另测不传 replacement 而接受原始非法抽取属性，防止只验证人工改写路径。合法 rejected 项允许保留非法原值用于审计；接受与拒绝的校验强度要明确，不能让用户无法驳回坏数据。

使用 KG-002/003 的共享纯属性校验器，定义来自 run.input_snapshot 指向的本体正式修订，不用当前 job 或草稿；物化仍再次校验以防绕过复核入口/旧记录。需要共同落地 KG-006，避免 same-key reuse 绕过后续属性校验。全图 unique 与并发冲突仍以 materialize 写入时为最终门禁，不承诺 review 时已锁定未来图谱状态。

所有权：extraction owner，新增 `tests/backend/test_extraction_review_contract.py`；共享纯校验模块由属性 owner 提供接口，双方先约定函数签名与错误契约，避免直接 import graphs 私有 ORM 逻辑。

## 10. 图谱删除：草稿解绑不能遗漏当前正式 BotRevision

证据：`graphs.py:537–552` 只读未删除 BusinessBot 的当前 graph_ids；`bots.py:417–425` 编辑已发布 Bot 草稿的 graphIds 不清除 published_revision，也不把 published 状态改为 draft；`:1821–1836` 公开 Key 使用当前正式 BotRevision；`:700–716` 在读 GraphRevision 前检查当前图谱，`graphs.py:56–60` 对 deleted 返回不存在。因此删除不是“历史快照可以继续独立运行”，而是能使仍在使用它的正式 Bot 失败。

最短全 HTTP 序列：复用 test_knowledge_graph.py 的已发布图谱和带 Key Bot → 先公开 invoke retrieval 成功 → GET Bot 最新 version → PUT `/api/v1/bots/{botId}` 保持 apiEnabled=true、提交 graphIds=[]，不重新发布 → DELETE `/api/v1/graphs/{graphId}?version={currentVersion}`。当前源码推导删除成功，原 Key 下一调用失败；目标删除 409，Graph 未 deleted，正式 Key 仍成功。

再 POST `/bots/{botId}/publish` 发布已解绑草稿，之后 DELETE 应可成功；过期历史 BotRevision 本身不应永久锁住图谱，除非产品明确定义历史修订可继续执行。另测删掉 Bot 后可删图、只关闭 apiEnabled 但保留正式 Bot 的 debug(usePublished=true) 仍需受保护、其他工作区 Bot ID/修订不能产生错误引用、缺失/畸形当前修订应有诚实错误而不是偷偷忽略。保留现有草稿绑定保护。

最小修复由 root 或唯一 graphs owner 负责 `graphs.py:537–552`：在当前工作区未删除 Bot 上查询其当前 published_revision 对应 BotRevision（同时约束 workspace_id、bot_id、revision），检查 snapshot.bindings.graphs[*].id。不扫描所有历史快照永久阻止删除；不改 Bot 的运行授权去绕过 deleted 源资源。新测试建议 `tests/backend/test_graph_bot_delete_dependencies.py`，可参考新 Bot 专项的 published_bot/invoke，但独立测试保持固定版本语义。

并发边界：delete 的检查与 Bot publish 同时进行可能有竞态；顺序 HTTP 红绿只证明检查当前状态有效，不能证明并发发布/删除原子。若要求并发保证，需 Bot publish 与 Graph delete 共同遵循父资源锁定/依赖约束；仅在 delete 单边加锁不自动覆盖另一写入路径。当前批次 bots.py 保持冻结，下一批若扩大修改需明确重新分配 Bot publish 所有权和回归范围。

## 11. 建议分配与验收层级

| 所有者 | 文件/责任 | 建议专项 |
| --- | --- | --- |
| 属性与 graphs owner | schemas.py、graphs.py 属性/探索/删除依赖；必要纯属性校验模块 | graph_property_contracts、graph_explore_contract、graph_bot_delete_dependencies |
| 抽取 owner | extraction.py，引用属性校验公共接口；修正旧 File 夹具 | extraction_source_authority、extraction_review_contract、extraction_materialization_contract |
| root | 协调共享 graphs.py、决定唯一性并发方案及任何迁移/发布事务扩展 | 汇总红绿、兼容回归、浏览器与下一批冻结记录 |

各 owner 不是独占工作树，不能还原他人变更。新代码注释/文档字符串使用中文。先保存基线失败证据，再实现，再运行目标回归；通过后仅在有新改动或未解决问题时扩大测试，避免重复大门禁。

证据应分清三层：①顺序 HTTP 返回及持久化状态；②单事务内后项失败/提交失败的回滚与幂等；③多 Session/真实数据库并发和隔离。前两层在隔离 SQLite 可证明具体实现行为，不能推出第三层。当前本报告连第一层新用例也未执行；这里“当前会”均为已读源码推导。真实 MySQL 并发、RR 撤权可见性、锁覆盖、发布一致性及性能均未验证。

## 12. 本次实际阅读范围和文件 SHA-256

只登记本次可见片段，不把 grep 命中、整文件 hash 或 root 先前全文审查算作本代理本轮全文覆盖。此前批次 Bot 审查的全文证据另在 review-bots-next.md；本报告无需重复全文阅读。

| 文件 | 实际精读片段（一基行号） |
| --- | --- |
| docs/audit/review_20261004/followup-graph.md | 全文 |
| knowledge_graph/schemas.py | 1–110、151–203；118–148 来源参数/validator 片段 |
| knowledge_graph/graphs.py | 33–61、172–342、430–484、537–552；407–427统计上下文 |
| knowledge_graph/ontology.py | 178–205、231–264；169–175概念归属片段 |
| knowledge_graph/extraction.py | 48–119、193–255、514–642、677–748、751–882；其余函数名由 rg 定位 |
| knowledge_graph/router.py | 149–234、346–376；其他属性 CRUD 路由由 rg 定位 |
| knowledge_graph/models.py | 100–117、225–235；其他类/字段仅 rg 定位 |
| knowledge_graph/bots.py | 390–430、482–533、569–735、1808–1849；批次十三上下文另有实现读取 |
| tests/backend/test_knowledge_graph.py | 1–240、267–299 |
| tests/backend/test_knowledge_search_authority.py | 1–65 |
| tests/backend/test_knowledge_tool_scope.py | 1–89；其他用例名与状态操作仅 rg 定位 |
| tests/backend/conftest.py | 1–100（前一 Bot 实施阶段已读并沿用） |

文件原始字节摘要用于定位此次计划依据，不表示整文件精读：

| 文件 | SHA-256 |
| --- | --- |
| backend/app/modules/knowledge_graph/schemas.py | `ADE7955B17E24C6EBC0E043A396D691E55CEE0D97ADFA555F37347AC7D01CD53` |
| backend/app/modules/knowledge_graph/graphs.py | `E195B5D187B4D4952A79D791A7AB4D3A2624518E7469A51229465131A3497D7E` |
| backend/app/modules/knowledge_graph/extraction.py | `0B6716564CE3C642F17D54AADF6982D24AA17C1F29F9831EA522A88A457CA802` |
| backend/app/modules/knowledge_graph/ontology.py | `9E84FCF125778A89223E9BDEF1070ADE1EA85F4C712B7FDE0BF85A66F92D3157` |
| backend/app/modules/knowledge_graph/router.py | `27415EE2FD823982A3B49A3C2F41DE4592D6AE4A0B4F760EA57A4C118F6C5413` |
| backend/app/modules/knowledge_graph/models.py | `929A1DCEC503210AFC54724DD02A09565E9E219C41EA0D692941964D0C82F089` |
| backend/app/modules/knowledge_graph/bots.py | `1A409D2566F7AADA515CBBD6F58C7CA1E50F0665FD452CF576660FAF139E79C6` |
| tests/backend/test_bot_runtime_authorization.py | `2715E4B744D9AEFF2FA312F5A3E146933AF8452FC2486A4C931D4538E3E3CB07` |
| tests/backend/test_knowledge_graph.py | `7637FA96C2323B1A2AFE0652CC128B7C8E8D0E6E05B3F0E4D83DCAC62272D32B` |
| tests/backend/test_knowledge_search_authority.py | `8B3C959AA441091C8006AEEE7E393160155238C49D4F6904F8F1D606ABB76C28` |
| tests/backend/test_knowledge_tool_scope.py | `8EDA65544F013C84717246B6AE38F13972B1D8B7207B104638195062258DB9E6` |

后续代理应先核对当前文件和归属；共享工作树可能在报告之后继续变化，不应凭此摘要覆盖他人修改。
