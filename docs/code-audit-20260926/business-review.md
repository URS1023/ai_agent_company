# 业务后端功能审查（2026-09-26）

本报告按用户最新澄清，审查“有哪些功能、入口是否接通、实际执行什么、结果如何保存、哪些说法超出实现”。结论以源码为准，不把 README、注释或路由摘要当成功运行的证据。未启动服务、未连接业务数据库、未调用模型或 HTTP 服务、未执行写回。下文“已实现”表示能追踪到完整代码路径，不代表已经完成真实环境验收。

## 覆盖范围与方法

- 范围为 `backend/app/modules` 中除 `runtime/harness/openwebui/chat/chat_artifacts/artifact/presentation/tool/model/skill` 的业务模块，其余由其他审查分工处理。
- 对本范围 **237 个 Python 文件、61,688 行、2,055 个函数、498 个路由函数**完成 AST 解析、函数与路由清点、持久化模型和关键调用的静态扫描；清单见 `business-source-inventory.json`。
- 深读数据分析、工作流执行与版本、调度、设备预警、质检、知识图谱抽取与 Bot、文档生成、应用启动、报表生成和刷新、写回执行的关键路径；文件/资产/治理的低风险 CRUD 为结构与调用关系核查。不能把这次覆盖说成对 61,688 行逐行动态验证。
- 对调度状态映射做了原函数 + 伪会话的离线复现，脚本与结果见 `reproduce_schedule_status.py`、`schedule-status-evidence.json`。

## 功能矩阵

| 功能域 | API 与主要实现 | 实际输入 → 处理 → 输出/存储 | 当前功能状态和边界 |
|---|---|---|---|
| 数据源管理 | `analytics/router.py:28`；`analytics/sources.py`；`analytics/metadata.py:36`；`analytics/imports.py:21` | 配置数据源、测试连接、同步表字段；CSV/XLSX 导入到受管 SQLite；`DataSource` + `SourceMetadata` + 治理策略/健康检查历史 | 有创建、修改、删除、导入、配置导入导出、健康检查和元数据闭环。数据库提供者包括 MySQL、Postgres、SQLite，另有 Elasticsearch；HTTP 采集是另一条模块路径。配置能力不代表当前环境都已可用。 |
| 数据分析/SQL | `analytics/router.py:194` → `analytics/queries.py:396` → `analytics/execution.py:159`；`analytics/exports.py:34` | 自然语言或 SQL → 元数据/模型生成 → 查询 → 结果、列信息、统计、截断标记 → `analytics_query_history`；历史导出 JSON/CSV/XLSX/Markdown/HTML | 真实执行路径和历史恢复均已实现。自然语言依赖可用模型；提示压缩与完整查询校验使用不同结构，测试旧断言需要同步。不能用离线 mock 测试证明模型回答正确。 |
| HTTP 采集 | `collection/router.py:47` → `collection/execution.py:74,141` → `collection/transport.py:360` | 保存 HTTP 源和提取配置 → 单条/批量请求 → JSONPath 提取 → 逐条成功/失败/跳过状态、合并数据与采集历史 | 有单条、批量、字段预览、逐条结果、部分成功和总量限制；不是浏览器直接抓任意地址。此路径可能发送请求，未做真实联调。 |
| 通用工作流 | `workflows/router.py` → `workflows/service.py:306,347` → `workflows/execution.py:98` → `workflows/engine.py:347` → `workflows/nodes.py:922` | 图编辑/校验 → 草稿修订/发布修订 → 按节点执行 → `workflow_executions`、统一 Run/Step、节点输出、业务状态 | 草稿、发布、修订恢复、运行、暂停/恢复/取消、子流程、循环、规则、模型、工具、HTTP/数据库、业务节点均有执行分支。**正式运行输入表单与发布修订错位，见 F1。parallel 仅表示扇出，实际引擎按拓扑顺序串行执行。** |
| 设备预警 | `alerts/router.py:203,210,230` → `alerts/workflow_builder.py` → `alerts/reports.py:41,240` | 设备/测点/策略/专家经验 → 固定规则修订 → 工作流数据判定 → 预警报告 + 问题 + 确认/解决/忽略/重开事件 | 台账、策略版本、报告、问题处置和 HTML 报告导出有闭环；结论区分 normal/issues/no_data。`alerts/monitoring.py:32` 中巡检/保养/维修 SQL 绑定现场 MySQL 表名，不是通用工业协议采集器。 |
| 预警通知 | `alerts/notifications.py:34` → `InboxMessage` | 持久化报告 → 按结论/严重程度筛选 → 当前执行人或工作区成员的站内信 | 实现的是站内信；此模块没有短信、邮件或企业微信发送。每轮产生新报告及新通知；尚无同一问题跨周期合并、冷却或恢复通知状态机。不能把“已通知”理解成外部渠道送达。 |
| 质量检查 | `quality/router.py:122,129,136,272,284` → `quality/run_plans.py`/`quality/runs.py:80` → `quality/detection.py`/`quality/inspections.py:144` → `quality/assessment.py:33` | 指标/模板修订 + 设备/样本/图片 → 检测器或人工观察 → 逐指标判定 → `quality_inspections`、检测记录、人工复核记录、导出 | 支持多设备多样本表单、图像证据、外观/成分检测、固定标准、人工复核与逐样本缺结果状态。实际自动检测依赖所配 HTTP 检测服务；算法不在本模块本地训练或部署。缺证据产生 incomplete，不能当作合格。 |
| 让步放行 | `quality/router.py:61,100,108,115` → `quality/concessions.py:581,729,869` | 放行规则 + 已有质检/工作流证据 → 计算候选结论 → 人工决定 → `quality_concession_requests` | 有规则、证据来源、申请和决定记录，属于质检结果后的业务处置链；并非把原始检测证据覆盖为通过。 |
| 知识库/文件 | `knowledge/router.py` → `knowledge/service.py`/`knowledge/governance.py`；`files/router.py` → `files/service.py` | 上传/引用文件 → 解析、分段、索引 → 检索/召回日志；手工文本/问答分段及批量导入；文件版本/分类/预览/下载 | 有入库、重建、检索、分类同步、分段维护和文件版本链。搜索索引不可用时存在关键词回退；因此“检索可返回”不能证明向量索引已工作。 |
| 本体/知识图谱/抽取 | `knowledge_graph/router.py:153,194,221,229,237` → `ontology.py`、`extraction.py:556,704,751`、`graphs.py:227,296,501` | 本体概念/关系及发布修订 → 结构化映射或文本候选抽取 → 人工审核 → 实体/关系入图 → 图探索、统计和发布快照 | 链路完整。**非结构化抽取是 recognitionTerms 字面匹配 + 同文本块共现候选**（`extraction.py:438`），不是大模型语义抽取；候选不会直接成为已确认图谱事实。图谱用关系型 `kg_*` 表存储，没有此链路中的独立图数据库依赖。 |
| 业务 Bot | `knowledge_graph/router.py:430,447,463,487` → `bots.py:1483,1625,1734` | 配置/发布 Bot → 知识库与图谱引用、技能、绑定工作流/工具、模型回答或证据清单 → `kg_bot_debug_runs` + 统一 Run | 有调试、发布、API Key 和对外调用；实际输出取决于模型与所绑定资源。Bot 位于 knowledge_graph 模块，不应因目录名只把它当图谱管理。 |
| 报表/数字大屏 | `reports/router.py:77,83,89,101,107,113` → `reports/generation.py:1252`/`reports/service.py:87`/`reports/data.py:24,63` | 组件/整屏需求、模板、来源 → 设计和实际数据绑定 → 内容/画布配置、查询追溯、生成记录 → 保存 Report/Revision → 刷新/钻取 | 有单组件、整屏生成、模板/提示词中心、保存/修订、真实数据刷新和钻取。生成结果与保存报表是不同动作；模型依赖、模板槽位、SQL 结果和前端组件渲染共同决定最终可用性。 |
| 文档/PPT/表格/PDF | `documents/router.py:43,82,106,115,124,137` → `documents/service.py:378,495,523,667` | 草稿/来源/模板/人工结构或 AI → 内容编写 → 对应渲染器 → 真实字节 → `Artifact` + `document_generations`/events → 预览/下载 | 输出类型明确是 DOCX、PPTX、XLSX、PDF（`service.py:40`）。有 PPT 大纲/分批页的暂存路径与页数核验。后端入口是同步生成函数；是否在前端提供进度及长请求体验由前端审查确认。没有据此证明所有模板版式渲染均正确。 |
| 应用中心 | `applications/router.py` → `applications/service.py:416,451,485,638,723` | 应用定义/版本/受众/资源绑定 → 发布 → 输入校验与幂等认领 → 工作流运行或站内入口 manifest → `business_application_launches` | **工作流入口会真实运行绑定的发布修订；其余资源入口只解析站内路径并记录启动完成**（`service.py:657-694`）。这是一层资源组合与入口封装，没有创建新的独立部署应用。 |
| 写回 | `writeback/router.py` → `writeback/targets.py:117,172` → 工作流 output-table → `writeback/execution.py:121` | 已配置目标表、字段映射、insert/update、明确确认 → 外部事务 → `WriteOperation` 准备/提交/成功/失败/不确定回执 | 存在真实 INSERT/UPDATE 与提交，并非全系统业务数据只读。UPDATE 要求唯一键且恰好命中一行；不确定提交回执需核对。此次未执行任何写回。 |
| 资产目录/治理 | `assets/router.py` → `assets/service.py:309,405,727,807` | 聚合已有业务资源 + 注释/标签/指标定义/关系 → 资产目录、详情、关系视图 | 是对已有资源的统一目录与治理附加信息，不是新数据采集或独立指标计算引擎。 |
| 登录/账号/工作区 | `auth/router.py` → `auth/service.py:101,290`、`system/oidc_service.py:659`、`system_tools/integrations.py:205`；`workspace/router.py` | 本地登录/企业 OIDC/客户端令牌 → 用户与工作区身份；个人资料、改密、成员维护；SessionToken/SessionContext/成员关系持久化 | 三类认证入口均有实际实现。本地账号可离线验证；OIDC 依赖配置的企业身份服务，尚未进行真实 SSO 联调。旧 workspace 路由与新 system 管理路由同时存在，不应把旧入口误当成全部管理功能。 |
| 工具/连接器/插件能力管理 | `capabilities/router.py:173` → `capabilities/service.py:629,731,785,907`；`connector/router.py`；`plugin/router.py`；`capability/router.py` | 定义工具/参数/连接器配置 → 草稿版本/发布/复制/分享/试调用；从已有运行生成能力、编译参数或按指令修改；Tool/Connector/Plugin 与版本记录 | 新 `/api/v1/capabilities` 与旧 `/api/connectors` 并存，旧连接器路由转入新能力服务。插件有版本与发布及试运行，调用最终仍需工具提供者和模型。不能仅创建一条定义就判定外部能力接通。 |
| AI 业务动作桥接 | `business_ai/router.py:16,29,36,43` → `business_ai/service.py`/`dashboard_actions.py` | 数据源发现、预警查看、大屏草稿及受控业务动作 → 调用现有业务服务 → 动作回执/统一运行 | 是复用分析、预警、报表等能力的桥接层，不是第二套业务实现。交互 Agent 的整体链路在主审运行时分工报告中。 |
| 平台总览/API 中心 | `platform/router.py:35,70,111,119` → `platform/catalog.py`/`openapi_contract.py`；`audit/router.py` | 导航目录、各域统计、运行中 OpenAPI 与 API collection、审计记录 | 导航和接口目录有后端实现；API 列出和 Schema 可读不代表对应外部服务已接通。统计聚合范围和界面路由由前后端共同决定。 |
| 历史桥接辅助 | `octop_bridge/completion.py`、`skill_packages.py`、`trajectory/*` | 技能包资源读取/校验、运行轨迹投影与完成性判断 | 是兼容与辅助库，未在 `router_index.py` 注册成独立业务产品页面；不能将代码目录存在解释成已部署 Octop 应用。 |
| 系统与运维 | `system/router.py`/`governance_router.py`；`system_tools/router.py` → `schedules.py:378,487`、`notifications.py`；`operations/service.py` | 用户/组织/工作区/角色管理；Cron/interval 调度工作流、抽取、通知、健康检查；站内通知；运行统计与日志 | 主流程存在。**调度会把业务执行失败记成 completed，见 F2。**工作区归档未停止后台已启用调度，见 F3。定时执行受服务进程及 scheduler 开关影响，不是独立队列平台。 |
| 备份/恢复 | `system_tools/monitoring.py:281,370` | 生成平台库备份、保存校验值/文件、下载；恢复接口返回停服、核验、恢复到新库与切换步骤 | 备份有实际生成路径；**恢复只返回人工计划，`automaticApply=False`**。不存在此接口中的一键在线恢复执行。此次未创建备份或执行恢复。 |

## 已确认功能问题

### F1 — P2：正式运行表单使用草稿字段，执行却使用发布修订

- 前端：`apps/analytics/src/components/workflows/WorkflowPublishedRunner.vue:35` 从 `props.flow.graph` 读取开始节点变量。
- 后端返回：`workflows/service.py:51` 中 `workflow_view.graph = row.graph`，即当前草稿。`save_workflow:324-330` 保存草稿时不改变 `published_revision`。
- 后端执行：`workflows/execution.py:138-145` 在 published 模式选中已发布修订的 `snapshot.graph`。`workflows/nodes.py:999-1012` 拒绝未声明字段及缺少必填字段。
- 触发：先发布开始节点必填变量 `a`；然后仅保存草稿，把变量改成 `b`；业务人员打开“正式发布版本”页，表单生成 `b` 并提交；后端继续用发布版本 `a` 的契约，运行失败。
- 建议：正式运行页加载发布修订快照（包括变量、说明和写回摘要），同时提交当前定义版本用于并发检查；草稿调试继续使用当前图。
- 验证等级：前后端真实源码调用链交叉确认；未启动服务或执行外部节点。

### F2 — P2：工作流业务失败被调度运行历史标成完成

- 定位：`backend/app/modules/system_tools/schedules.py:413-415`。
- 调度 `_dispatch` 调用 `workflows.execution.start`；后者在 `execution.py:172-173` 正常返回 `ExecutionView`，其 `status` 可以是 failed。调度仅把“抛异常”视为失败，因此正常返回失败视图时无条件写 `run.status = "completed"`，`error_summary` 留空。
- 触发：创建已发布工作流调度，其中某节点执行时出错；工作流详情正确显示 failed，但调度历史显示 completed，按调度失败筛选/统计会漏掉它。
- 证据：`reproduce_schedule_status.py` 提取当前原函数，所有会话读写和业务分发都换为伪对象。业务返回 failed 时，实际输出为 `scheduleResult.status=completed`、`scheduleResult.output.status=failed`、`errorSummary=""`。
- 同一脚本也显示 waiting/cancelled 被归为 completed；这两个状态是否表示“派发完成”需产品明确，failed 至少应成为调度失败或显式业务失败状态。
- 建议：为各调度目标定义结果适配层，传播业务状态与错误原因，保留子运行标识；清楚区分“成功发起”与“业务执行完成”。

### F3 — P2：归档工作区后，已启用定时任务仍继续执行

- 定位：`system_tools/schedules.py:508-520`；`system/lifecycle_service.py:400` 起的 `update_workspace`。
- `update_workspace` 修改 `WorkspaceLifecycle.status`，没有同步暂停已有 job。轮询执行器只检查用户 active、Workspace 存在、成员关系存在，并没有查询工作区/组织生命周期。因此从另一个工作区归档目标工作区后，目标原定时工作流仍可到期执行。
- 外层确认：主审已核实后台 worker 只通过租约调用 `run_due_jobs`；没有补充生命周期检查。
- 用户可见影响：界面已经归档的空间仍可能继续采集、生成报告、发站内通知或执行已配置写回；“归档”与后台行为不一致。
- 建议：明确归档业务语义，在派发前统一核实生命周期，对不再启用的空间暂停定时任务并保存原因。
- 验证等级：静态链路确认；未修改工作区、未触发实际调度。

## 测试结果的正确解释

主审本轮安全隔离运行全量 offline 后反馈 491 passed / 4 failed。其中本分工涉及三条失败已核对：

1. `tests/offline/test_business_catalog.py:50` 要求模型提示 schema 与原字典完全相同，但 `analytics/queries.py:95-119` 新增 `truncatedSchema` 标记和提示裁剪；失败断言不能直接证明查询功能坏了。
2. `tests/offline/test_natural_sql_field_repair.py:134` 的两个参数分支要求 `request_timeout is None`，而 `queries.py:200-205` 已使用配置限时。属于测试仍期待旧行为，需要按当前预算规则更新断言。

这些测试偏差不应混同于 F1/F2/F3。模型质量、真实数据源兼容性、现场检测服务、文档渲染观感和完整浏览器流程仍需相应环境的功能验收，不能用这份静态报告宣布全部可用。

主审随后补充关键功能隔离用例 **26 项通过，用时 30.47 秒**，记录见 `functional-smoke-results.txt`。覆盖工作流开始参数与发布/恢复、报表保存/冲突/修订、质检标准快照、从真实内存 chunk 生成非结构化图谱候选、应用工作流输入/修订、站内公告投递，以及运行时分工的 Harness 网关与伪协议终态。使用隔离内存平台库和伪提供者，没有业务库或真实模型调用。已覆盖功能可提高“后端闭环存在”的置信度，但没有覆盖 F1 的浏览器草稿/发布组合输入或全部部署条件。

## 应改正的项目描述

- README 的“业务数据只读”应收窄为分析路径；系统实际有明确确认后的工作流写回。
- “工作流并行”应说明目前是逻辑扇出，执行按拓扑顺序串行。
- “知识抽取”应说明结构化映射与词项/共现候选规则；当前非结构化路径不是模型语义抽取。
- “应用启动完成”只在工作流入口对应业务执行；其它入口通常表示已解析并返回入口。
- “通知”应说明当前审到的是站内信投递。
- `README.md:16,81` 把平台库 `company_agent` 写进允许测试范围，不能替代当前任务约束。此次严格未连接任何业务数据库。
