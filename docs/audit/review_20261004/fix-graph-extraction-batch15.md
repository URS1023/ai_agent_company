# 批次十五：KG-005/006/007/008 抽取契约修复证据

日期：2026-10-04。唯一修改工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-compound-delivery`，分支 `codex/major-compound-delivery`。开始基线 `ea810271af13df7cb4d88037ebd12fdc38e4c0c4`；执行中根代理 fast-forward 至 `e36533e69abb374a7b8795eab8c383e7206e5de8`（上一批前端修复），本报告对应的后端修改均保留。

当前状态：本代理负责的产品与测试已冻结；最终组合 123 passed / 0 failed / 0 errors / 0 skipped，JUnit 耗时 206.892 s。`git diff --check` 通过（仅现有 LF→CRLF 提示）。不再修改产品或测试，交由根代理统一门禁与验收。

## 修改所有权

本代理仅修改：

- `backend/app/modules/knowledge_graph/extraction.py`。
- 新 `tests/backend/test_extraction_source_authority.py`。
- 新 `tests/backend/test_extraction_materialization_contract.py`。
- 新 `tests/backend/test_extraction_review_contract.py`。
- `tests/backend/test_knowledge_graph.py`：既有非结构化合法 fixture 补真实 ready File；以前只有不存在的 file_id。

没有修改旧 `major-task-lifecycle` 树、正式 docs、门禁、models/migration、graphs.py、schemas.py 或其他域。KG-004 explore 属于根代理独立修改；共享属性校验与父锁接口沿用批次十四。未安装依赖、提交代码、连接业务库/真实模型/外网或 8011。

## 最终行为

### KG-005：只读取当前可用且已授权的来源

`extraction.py:212` 的 KB 与显式文档读取改为带 workspace 条件的 select + populate_existing，保留 knowledge.view 和资源授权，不以 identity map 中的旧对象替代当前查询。

`:536` 新局部 `_knowledge_scope` 仅在来源加载期间绑定 knowledge.view；使用原 AuthContext，保留 user/workspace/client_id/client_scopes 和 HTTP 身份验证链，不复制管理员身份。finally 恢复 db.info 的 resource_access_criteria、actor_user_id、actor_workspace_id 三字段，区分原键缺省和有值；ctx.request_permission 完全不改，其他会话信息保留。成功、读取异常、绑定中断、嵌套调用均有回归。

`:552` `_load_chunks` 联表校验 KB→Document→File→Chunk 和可选 metadata：所有来源工作区匹配；文档 KB 与 chunk KB 相同；KB active；File ready 且未软删除；Document ready；metadata 缺省或与当前工作区/KB 相同且 enabled。显式选文档和整库抽取统一要求 ready，不借用检索允许 processing 的宽口径。先过滤再计算 5000 chunk / 500 万字符上限，正文来自当前 SQL。

共享 KB 授权允许读取其合法片段；不扩大通用 files.view。无 files.view 与 files.view/self 两种普通成员，成功及在临时范围内加载原 File 后抛错两种分支，均断言恢复后同 Session `select(File)`、get_file、ToolGateway read_file、Artifact 查询，以及 HTTP 原文/下载仍拒绝。测试特意保留原 File identity map 实例，未通过 expunge 回避缓存边界。

来源全部失效时保持 422“没有可抽取文本”，或沿原来源授权/文档检查返回 403/404/422；已提交的运行尝试记录 failed 且无候选。没有错误要求删除失败运行历史。grant 撤销、角色 knowledge.view 撤销作用于下一次 HTTP 执行；真实 service token 验证缺少/拥有知识 scope，以及轮换后的旧令牌 401。

### KG-006：同键必须同内容才算复用

`:768` `_same_properties` 复用批次十四 `graph_service._validate_properties` 和 `property_value_key`，保留未知字段、必填、原生类型、日期和枚举规则。名字及业务键按原写入规则去除首尾空白；name/properties 不同返回 409，不覆盖旧行也不虚假 completed。

业务内容等价沿用固定定义：number 的 1 与 1.0、datetime 的相同时刻、json 的对象键顺序可以等价；json 的 true 与 1 不等价。缺省字段与显式 null 保留差别，provenance/evidence 不作为幂等差值。已有记录自身无效时不视为合法复用。

`:836` materialize 从入口起锁 run，再调用既有 `_lock_graph_write` 锁当前已授权 graph；取得父行后校验 graphVersion。候选与已有实体/关系采用 FOR UPDATE + populate_existing 当前读取；全复用路径也经过这些锁，不再绕过父锁和属性校验。新写入仍调用 save_entity/save_relation(commit=False)，沿用图谱唯一属性与端点基数规则。

实体、关系、run 成功状态与首次目标、job 最近结果、audit 仍在同一事务提交。内容冲突测试强制新实体先发生真实 INSERT，再遇到同键差值，验证全部回滚；提交故障测试在真实 prior INSERT 后给 before_commit 注入一次 IntegrityError，验证无图谱半成品，run 保持 review，无成功摘要。

### KG-007：固定 run 快照和首次目标

`:84` `_get_run` 采用 job/workspace 限定的强制查询，复核/物化请求加运行行锁；`:97` `_run_snapshot` 只解释 run.input_snapshot 的 ontologyId/ontologyRevision，再执行当前本体授权。元数据缺失、非正整数修订等明确拒绝，不回退当前 job 猜测。

物化目标匹配 run 固定本体；已完成 run 读取自身 summary.materialization.graphId 保护首次目标，拒绝缺失/畸形首次目标或更换目标。编辑 job 改成本体 B、同一本体再次发布并编辑 job 为修订 2、后续 run 成功到其他图谱，都不会重解释旧 run 或允许更换其目标。

跨 job run、跨 workspace graph、错误 graphVersion 仍拒绝。固定快照不恢复当前资源权限，也不复制管理员权限。job.target_graph_id 继续表示最近结果，不再作为历史 run 依据。

### KG-008：整批接受前验证固定属性定义

`:730` `_validate_review_payload` 增加固定快照定义校验。`:786` review 先锁 run，接受项从 run 正式修订取定义，对实体/关系都执行批次十四属性规则。保留“全部验证完成后才修改候选”的两阶段结构。

测试将合法项排在非法项前，验证 integer 字符串/布尔、boolean 数值、非法 date/datetime、枚举外值、缺必填、未知字段均 422，候选 payload/status/version/reviewedAt 等完整输出不变。不传替换 payload 而接受历史非法原值同样拒绝；rejected 可以保留坏属性作为审计证据。materialize 对历史已接受值再次验证，已有同键记录也不能绕过。

## 真实红绿及夹具问题

所有命令仅使用主树解释器，经新树 `tests/run_isolated_backend.py`；入口强制内存 SQLite、一次性存储、拒绝外部数据库驱动和外网。FastAPI TestClient 在进程内运行，没有启动业务监听服务。来源索引和 embedding 使用既有内存桩。

| 证据 | 真实结果 | 说明 |
| --- | --- | --- |
| extraction-first-red.xml / .log | 3 failed | 其中 2 项是实体/关系差值被旧实现返回 200；来源项是 fixture 误读索引 document_id 导致 KeyError，不能算产品红例 |
| extraction-confirmed-red.xml / .log | 3 failed，4.839 s | 修正来源 fixture 后，三项均进入实际 HTTP 行为断言：软删除 File 仍产生 8 候选且 200 对预期 422；实体/关系不同已确认值均 200 对预期 409 |
| extraction-first-green.xml / .log | 9 passed，11.72 s | 三项红例转绿，加既有完整 KG 业务链 6 项 |
| extraction-expanded.xml / .log | 103 passed / 6 failed，131.29 s | 四项用了不存在的工作区创建 API；两项历史 JSON 将 1 改 True 被 SQLAlchemy 宽松相等判断忽略，实际未写下坏值；均属夹具错误，保留原始证据 |
| extraction-contracts-green.xml / .log | 72 passed，116.76 s | 工作区改为真实临时 SQLite Workspace；坏 JSON 使用 flag_modified 并 refresh 后断言 True 确已持久化；三份专项全绿 |
| extraction-final-green.xml / .log | 123 passed，206.892 s | 74 专项（新增普通成员异常恢复两项）+ 49 既有 KG/属性测试；0 failures/errors/skipped |

最终组合命令：

```powershell
& E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe tests/run_isolated_backend.py tests/backend/test_extraction_source_authority.py tests/backend/test_extraction_materialization_contract.py tests/backend/test_extraction_review_contract.py tests/backend/test_knowledge_graph.py tests/backend/test_graph_property_contracts.py --disable-warnings --junitxml=.implementation/extraction-final-green.xml
```

父锁验证捕获真实 HTTP 请求发出的 ORM 语句，再编译 MySQL 方言，确认全复用顺序为 ExtractionRun→KnowledgeGraph→ExtractionCandidate→GraphEntity→GraphEntity→GraphRelation，全部 FOR UPDATE + populate_existing。它验证的是调用顺序与 SQL 结构，不能冒充真实 MySQL 并发证明。

## 尚未验证和行为边界

- 没有连接或测试真实 MySQL；RR 事务快照、跨请求并发同键创建、同 run 不同目标的锁等待/死锁、数据库提交时连接中断仍未动态验证。设计采用 run→graph→子记录锁顺序；其他入口与部署数据库的实际并发行为须另行在明确授权环境验证。
- 新 AuthContext 不会突破既有 MySQL RR 快照。本轮验证“撤权后的下一次 HTTP 调用”，不声称抽取已加载后、执行中途的外部撤权可立即生效。
- 不追加已成功保存候选或图谱的追溯删除策略；历史副本的生命周期未扩大本批范围。
- 未做浏览器验收、性能/大规模压力测试或真实模型调用；根代理统一门禁和九域验收另行记录。
- 非结构化候选可能尚缺必填业务属性，仍允许生成待复核候选；接受/物化必须补成有效值。复核不承诺未来图谱 unique 已锁定，最终唯一性在图谱写事务校验。

## 文件 SHA-256

| 文件 | SHA-256 |
| --- | --- |
| backend/app/modules/knowledge_graph/extraction.py | `3DA1E4D139F28FA1AD30C971EC4CE2999743CBC8974930196DC5511D0485EAF2` |
| tests/backend/test_extraction_source_authority.py | `A3F348C4529A73CF27E50C65FF905CFF346BB01345780B15DCFFD713B113BEB7` |
| tests/backend/test_extraction_materialization_contract.py | `92A6D26C18971639AE9842199259D4D3E21CE5836F86C351A2DE46B2EA08DC23` |
| tests/backend/test_extraction_review_contract.py | `0118E01703BA47F387511B50D790046332CD0B818FE4C84A318996CB0F71E305` |
| tests/backend/test_knowledge_graph.py | `C215835F9A83213F688FA00948E2D20CB7E60B6D81EFE7B6880708440E3BAB12` |
| .implementation/extraction-first-red.xml | `9ABB29A1CC75F78F50A24ED6469E3F01FD1A28DCC4CA77C4AD7F29D0DF0B6982` |
| .implementation/extraction-confirmed-red.xml | `EE72D0282E773C91773E5EFA72E244CECE32AAED9EC4D402100B57479BF54A1C` |
| .implementation/extraction-first-green.xml | `5646425D779D62CBFF0EB5A40CAD6E0D23CC8D148E4010968D52B9F318341838` |
| .implementation/extraction-expanded.xml | `BEE45EA20CDC0B44A1DF03516316D40F205AB90ECA06A45B97B46519823F130A` |
| .implementation/extraction-contracts-green.xml | `4A5F247F016C0525A6344946E9CE06413514E57969D4E4A4461059B73596CA07` |
| .implementation/extraction-final-green.xml | `86BC9193A64D739C6E197466D0ADEF4AA923F414A72FA95591CB13560CE02097` |
| .implementation/extraction-final-green.log | `6207F7496CA28B13AFB2B641E0EDC26759896B46E2E8DB2035FD281F71958A9D` |

上述产品/测试哈希在最终组合结束后再次核实一致。根代理另有其他域和 explore 的并发修改，未回退、覆盖或计入本代理修复结果。
