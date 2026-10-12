# 前端功能审查（2026-09-26）

本文以代码中的页面、事件处理、请求与结果展示为依据。重点是“用户能做什么、调用什么、结果怎么回来、哪里会失败”。“已接通”表示已在源码确认页面操作与请求、返回展示之间有实现，**不等于已对运行环境逐页验收**。本轮未启动服务、浏览器，也未连接任何数据库或模型。

## 直接影响功能的已确认问题

### F1 [P2] 新工作流首次保存期间继续编辑，会使保存永久等待

- 定位：`apps/analytics/src/composables/useWorkflows.ts:168`；相关守卫 `apps/analytics/src/components/workflows/WorkflowWorkbench.vue:95-101`。
- 触发：打开 `/workflows/new`，修改内容开始自动保存，在请求返回之前再修改名称、描述或画布。
- 链路：第一次保存捕获旧快照；新修改使 `dirty` 保持为 true。保存成功后 `await router.replace('/workflows/' + saved.id)`。路由更新守卫执行 `confirmLeave()`，它等待 `flushDraft()`，后者等待尚未结束的同一个 `savePromise`。该保存 Promise 又在等待路由完成。
- 结果：保存、发布、试运行及离开确认都可能卡在等待，第二批编辑无法落库。服务器已有第一份草稿，但界面仍停在新建路径并显示保存中。
- 建议：完成本次保存状态收尾后再更新新建路由，并将后续编辑作为新的保存批次；新建 id 迁移不应触发等待当前保存 Promise 的离开守卫。
- 离线证据：`node docs/code-audit-20260926/frontend-workflow-repro.cjs` 已复现路由进入、dirty=true、saving=true、保存 Promise 不结束。使用假接口及假计时器。

### F2 [P2] 自动保存失败后每 700ms 无上限重发写请求

- 定位：`apps/analytics/src/composables/useWorkflows.ts:170-176`；定时器在同文件 `100`；新建/更新请求在 `apps/analytics/src/api/workflows.ts:75-78`。
- 触发：保存返回 409 版本冲突、403 权限改变、或断网导致结果不明确。
- 链路：catch 只设置错误，保留 dirty；finally 无条件再次 `scheduleAutoSave()`。没有新编辑也继续保存，没有次数上限、退避或错误类型判断。
- 结果：版本冲突无法自行恢复却持续请求；新建 POST 已提交但响应丢失时，因为尚未取得 id、创建请求又没有幂等键，还可能产生重复草稿。源码头部“保存幂等键”说明实际与实现不符：`pending` 变量用于运行，并未用于保存。
- 建议：失败后停止自动重试，保留草稿与错误，明确提供用户重试/重新载入冲突版本；新建操作使用稳定幂等键或可回查的客户端创建标识。
- 离线证据：同一复现脚本模拟持续 409，未发生新编辑仍发起 3 次写请求，并继续安排下一次。

### F3 [P2] 正式运行页面使用草稿输入字段，而实际执行发布版本

- 定位：`apps/analytics/src/components/workflows/WorkflowPublishedRunner.vue:35`。
- 触发：已发布 v1 的开始节点必填 `sampleId`；编辑草稿 v2，把变量改为 `batchId`，保存但不发布，然后打开“Web 应用 / 运行正式版本”。
- 前端仍从 `flow.graph` 构造输入表单；详情接口的 `workflow_view` 返回的是 `row.graph`（`backend/app/modules/workflows/service.py:52`）。提交时 `runPublished` 指定 mode=published（`WorkflowWorkbench.vue:121`），后端选择 `published_revision` 的不可变快照（`backend/app/modules/workflows/execution.py:136-143`）。
- 结果：页面要求填写 batchId，实际 v1 仍要求 sampleId；简单字段表单无法直接完成正式执行，用户会得到缺参错误或使用错误默认值。展开 JSON 手工补字段只能绕过界面缺陷。
- 建议：正式运行页加载并固定到发布修订的开始节点契约，草稿调试页继续使用草稿契约。页面显示的版本与提交版本也应保持一致。

### F4 [P2] 工作流结构化 JSON 输入会先舍入，再绕过精度校验

- 定位：`apps/analytics/src/components/workflows/WorkflowStartInputForm.vue:64`（`editJson`），相关 `setValue` 在 `46-52`。
- 触发：在开始节点 `json` / `json_object` 字段输入 `{"amount":0.10000000000000000001}`。
- 表单先 `JSON.parse` 再 `JSON.stringify`，变成 `{"amount":0.1}`。运行阶段虽使用 `parseWorkflowJson` 拒绝不能准确表示的数字，此时原始数字已丢失，校验只看见 0.1。
- 结果：字段编辑器展示原文本，但发送值已经变化。直接在完整 JSON 输入里写同样内容会拒绝，结构化字段输入却能通过，两种入口业务口径不一致。
- 建议：结构化 JSON 字段也使用同一个 `parseWorkflowJson`，保留非法草稿和明确的精度错误；精确金额/长编号要求字符串时须在编辑阶段就提示。
- 离线证据：`node docs/code-audit-20260926/frontend-json-precision-repro.cjs` 已验证原文本被拒绝、字段编辑后的值可通过。

### F5 [P2] 修改已有 Skill 的名称或说明，会在新版本中丢掉其工具和资源配置

- 定位：`apps/web/assets/platform/capabilities.js:472-478`；保存调用 `494-496`。
- 触发：已有 Skill 包含 recommended_tools、required_resources、examples 或 input_description/output_description（例如通过“从运行沉淀 Skill”生成）；用户打开“创建 Skill 新版本”，仅修改名称或一句说明后保存。
- 前端虽然读取了 `record.versions[0].spec` 回填可见文本，提交却重新构造 spec：三个数组固定为 `[]`，输入/输出说明完全不传。后端 `backend/app/modules/skill/router.py:125` 原样传入；`service.py:94-97` 直接以 `spec=spec` 创建新版本，不合并旧字段。能力沉淀确实会写这些字段，见 `backend/app/modules/capability/service.py:142-147`。
- 结果：旧版本仍可查，但新草稿已经丢失这些配置，继续发布后正式 Skill 也缺少它们；这不是仅缺少可视化编辑控件，而是普通编辑会改变未编辑内容。
- 建议：从最新完整 spec 复制后，仅覆盖表单实际编辑的字段；若支持删除工具/资源，需要明确的编辑控件和删除动作。

## 页面功能矩阵

入口按 `enterprise-navigation.js:28-43` 的六个业务域，以及 `main.js:202-235` 和 `router.ts:17-46` 中实际注册页面整理。后台下发导航控制某账号是否看得到入口；下表不假定管理员当前具备全部配置。

| 功能 / 入口 | 页面操作与结果 | 调用接口 | 主要实现与定位 | 源码确认程度 / 缺口 |
|---|---|---|---|---|
| 工作台 `/`、`/app` | 资源数量、业务域卡片、最近任务、最近结果、最近访问 | GET `/api/v1/platform/overview` | `assets/platform/home.js:42` | 已接通；这里是业务首页，不是聊天页 |
| 登录、改临时密码、企业登录回调 | 本地登录、必改临时密码、SSO 提供方选择、回调后进入首页 | `/api/auth/login`、`password`、`sso/*`、`me` | `assets/platform/main.js:70-154` | 已接通 |
| 数据源 `/datasources` | 数据库/文件列表、创建编辑、启停、元数据、健康检查、策略、配置导入导出 | `/api/v1/analytics/datasources/*` | `views/DataSourcesWorkspace.vue:16`；`components/DataSourceManage.vue`；`composables/useDataSources.ts:43` | 已接通；服务可达性未实测 |
| 表格导入 `/datasources` | 选择 CSV/XLSX、编码和表名，建立独立数据源，刷新列表 | POST `/api/v1/analytics/datasources/import` | `views/DataSourcesWorkspace.vue:47-57` | 已接通，前端明确展示 20MB/5万行限制 |
| HTTP 采集源 `/datasources?type=http` | 请求模板、凭据、分页、字段筛选、测试采集及结果展示 | `/api/v1/collection/sources/*` | `components/collection/HttpSourcesManager.vue`、`HttpSourceForm.vue`；`api/collection.ts` | 已接通；测试采集会真实访问所配来源 |
| 写入目标 `/datasources?type=writeback` | 写表授权目标、字段映射、插入/更新配置、删除目标 | `/api/v1/writeback/*` | `components/writeback/WriteTargetsManager.vue`、`composables/useWriteTargets.ts`、`api/writeback.ts` | 已接通；这是登记写入目标，与实际回写执行分开 |
| 智能问数 `/analytics` | 选择数据源、自然语言/SQL 查询、参数、分析结果、推荐图表、可视化看板 | POST `/api/v1/analytics/query`；数据源元数据；历史 | `components/IntelligentQuery.vue:267`、`business/QueryResultPanel.vue:79-99` | 已接通；图表来自返回结果，不是独立首页模块 |
| SQL 工作区 `/analytics/sql` | 表浏览、样例 SELECT、参数、取消等待、模板保存/复用、结果导出 | `/api/v1/analytics/query`、`query-templates/*`、`export` | `composables/useSqlQuery.ts:57`；`views/SqlWorkspace.vue` | 已接通；取消等待不等同于服务端查询取消 |
| 查询历史 `/analytics/history` | 分页、状态、失败原因、保存结果回看、复用 SQL/问题 | `/api/v1/analytics/history/recent`、`history/{id}` | `views/QueryHistory.vue:31-69` | 已接通；21条探测、每页展示20条 |
| 数据质量 `/analytics/quality` | 对表字段做缺失/重复/离群样本检查、摘要和问题建议、导出 | POST `/api/v1/analytics/quality-check`、`export` | `views/QualityWorkspace.vue:56-86` | 已接通；与产品质检 `/quality` 是两套功能 |
| 大屏列表 `/dashboards` | 新建/重命名/删除、选模板、打开设计与预览、报表转模板 | `/api/v1/reports`、`list`、`/{id}`、`/api/v1/report-templates/from-report/{id}` | `components/ReportManage.vue:414-558` | 已接通 |
| 大屏设计 `/report/designer/:id?` | 拖放组件、图层、属性、对齐、组合、复制/粘贴、撤销、SQL/HTTP 绑定、保存与版本、AI 整屏/组件生成 | `/api/v1/reports/*`、`screen/ai-generate`、`widget/ai-generate`、查询/钻取 | `components/JimuBigScreenReplica.vue`；`components/jimu-screen/*`；`api/report.js` | 已接通；当前使用GenerationStatusPanel显示不确定进度，旧模拟百分比控制台未接入当前页面 |
| 基础报表设计 `/report/basic/:id` | 原有基础组件编辑器与报表保存 | `/api/v1/reports/{id}` 等 | `components/ReportDesigner.vue`；`router.ts:40` | 保留的真实路由，不是默认新建大屏入口 |
| 大屏预览 `/report/view/:id` | 全屏展示、自动刷新、保存组件数据读取、下钻、导出明细；原模板专用呈现 | `/api/v1/reports/{id}`、`/{id}/widgets/{widgetId}/data`、`drilldown` | `components/ReportViewer.vue`；`composables/useWidgetData.ts:83`、`useReportDrilldown.ts` | 已接通；数据读取依赖组件真实绑定 |
| 大屏模板 `/templates` | 模板搜索预览、创建编辑、复制、启停、历史、使用记录、回收站、空报表或 AI 填数据 | `/api/v1/report-templates/*`、`/api/v1/reports`、`/api/v1/ai/business-actions/dashboards/drafts` | `views/TemplateCenter.vue:74-157` | 已接通；模板预览内容不等同于已经查询到的业务数据 |
| 提示词 `/prompts` | 分类、搜索、新增编辑、另存、复制、删除、应用到问数或大屏 | `/api/v1/report-prompts/categories/*`、`templates/*` | `views/PromptCenter.vue:32-114` | 已接通；使用通过 sessionStorage 回填目标页面 |
| 资产目录 `/assets-catalog` | 资源总目录及详情、目录注解、业务术语、指标定义、关系管理 | `/api/v1/assets/types`、`catalog/*`、`terms/*`、`metrics/*`、`relations/*` | `assets/platform/asset-catalog.js:204,357,401,477,526,552` | 已接通；资产是聚合目录，不是另一份业务资源库 |
| 文件 `/files` | 文件/生成结果列表、分类、上传、批量上传、编辑、版本上传/历史、下载 | `/api/files*`、`/api/artifacts*` | `assets/platform/files.js:55-217` | 已接通；上传资料库与加入知识库是独立动作 |
| 知识库 `/knowledge` | 建库、文档批量加入、解析重试、分段编辑/导入、检索召回、设置、同步、授权 | `/api/knowledge/*`、文件列表 | `assets/platform/knowledge.js:76,115,254,354,409,444,495,544,566` | 已接通；上传队列具备失败条目重试/复用已上传文件 id |
| 本体 `/ontology` | 本体创建、概念和关系类型编辑、发布、版本展示 | `/api/v1/ontologies/*` | `assets/platform/knowledge-graph.js:187-252` | 已接通 |
| 知识抽取 `/extraction` | 创建抽取任务、执行、运行记录、候选审核、物化图谱 | `/api/v1/extractions/*`、`runs/{id}/candidates`、`review`、`materialize` | `assets/platform/knowledge-graph.js:321-412` | 已接通；审核与物化是显式步骤 |
| 知识图谱 `/graphs` | 实体关系列表、检索探索、编辑、发布 | `/api/v1/graphs/*`、`entities`、`relations`、`explore` | `assets/platform/knowledge-graph.js:453-534` | 已接通 |
| 设备预警首页 `/alerts` | 设备分页/搜索、设置、设备工作流入口 | `/api/v1/alerts/device-catalogue`、`devices/*` | `views/AlertWorkspace.vue`；`composables/useBusinessDevices.ts:57` | 已接通；当前首页是设备工作台 |
| 预警分页面 `/alerts/points|advice|rules|reports|problems` | 测点、处置建议、预警规则、报告及问题处理 | `/api/v1/alerts/*` | `components/alerts/AlertWorkbench.vue`；`composables/useAlerts.ts`、`api/alerts.ts` | 已接通；`/alerts/devices` 重定向首页 |
| 产品质检首页 `/quality` | 产品目录、按产品分组的标准/流程、未关联/未配置提示、质检工作流入口 | `/api/v1/quality/products/*` 及目录接口 | `views/QualityInspectionWorkspace.vue`；`components/quality/QualityProductWorkspace.vue`；`composables/useQualityProducts.ts` | 已接通；不能再按文档称其为通用设备页 |
| 质检基础页 `/quality/metrics|templates|inspections` | 检验指标、标准模板、记录与报告；批次执行、图片证据、缺陷定位、让步处理 | `/api/v1/quality/*`；质检工作流/运行 API | `components/quality/QualityWorkbench.vue`、`QualityRunPanel.vue`、`QualityConcessionPanel.vue`；`composables/useQuality.ts`、`useQualityRun.ts` | 已接通；“样本输入→报告”请求与恢复状态均存在 |
| 工作流 `/workflows`、`/workflows/:id` | 列表、创建、图编辑、节点配置、自动保存、校验、发布、停用、AI生成、导入导出、版本回滚、调试 | `/api/v1/workflows/*` | `components/workflows/WorkflowWorkbench.vue`、`WorkflowEditor.vue`；`composables/useWorkflows.ts` | 已接通但存在 F1/F2；无法据静态检查宣称稳定完成 |
| 工作流访问点/日志/监测/正式运行 `/:id/access-point|logs|overview|run` | 发布入口、执行日志、指标、输入表单、正式运行、结果/继续/取消 | `/api/v1/workflows/{id}/*` | `WorkflowAccessPoint.vue`、`WorkflowLogs.vue`、`WorkflowOverview.vue`、`WorkflowPublishedRunner.vue` | 已接通但正式表单存在 F3；结构化输入存在 F4 |
| 设备工作流 `/alerts|quality/devices/:id/workflow` | 首次打开获取或创建设备草稿，带设备上下文复用完整工作流工作台 | 设备工作流入口 API；`/api/v1/workflows/*` | `views/DeviceWorkflowWorkspace.vue`、`composables/useDeviceWorkflowEntry.ts` | 已接通；打开入口可能创建草稿，不能当只读浏览测试 |
| 应用与解决方案 `/applications` | 助手/自动化/数据应用/方案的资源组合、版本、复制、发布、启动、停用、归档、启动记录 | `/api/v1/applications/*`、`catalog`、`launch`、`launches` | `assets/platform/applications.js:81,222,294,356` | 已接通；列表固定 limit=200 无分页控件，超过后只能通过筛选缩小范围 |
| 业务运行中心 `/operations`（`/runs` 兼容） | 运行列表/详情、步骤/模型/工具/产物、恢复中断记录、审计视图、业务运营视图 | `/api/v1/operations/*` | `assets/platform/operations.js:169,227,302,412,471` | 已接通；运行与业务审计不是旧 admin 页面实现 |
| AI 对话 `/ai` | 会话历史、模型选择、附件/知识/数据源/插件/模板上下文、SSE回答、工具阶段、取消、断线恢复、产物和后续问题、语音输入 | `/api/chat/*`、`/api/models/*`、资源目录、文件/产物 | `apps/web/app.js:1688-1940` 及对应各功能段 | 已接通；19个现有离线恢复场景通过；语音依赖浏览器能力 |
| 文档与 PPT `/documents` | 生成记录、来源选择、AI/人工内容、大纲及分批幻灯片、执行、预览下载、复制归档、PPT模板上传/移除 | `/api/v1/documents/generations/*`、`templates/*` | `assets/platform/documents.js:192,274,314,339,375,445,489,506` | 已接通；与对话中的 PPT 生成入口并存 |
| Bot `/bots` | 资源绑定、提示设置、发布、调试、运行记录、API key管理 | `/api/v1/bots/*`、`binding-options`、`debug`、`runs`、`api-keys` | `assets/platform/knowledge-graph.js:577-662` | 已接通 |
| 能力目录 `/capabilities` | 可执行工具目录、参数预览/测试、从运行沉淀、分类与方法管理、批量动作 | `/api/v1/capabilities/*` | `assets/platform/capabilities.js:106,138,183,675`；`capability-governance.js:103,258,365` | 已接通 |
| 连接器 `/connectors` | HTTP/数据库/MCP接入配置、发现/测试、发布、版本/复制/分享等 | `/api/v1/capabilities/connectors/*` | `assets/platform/capabilities.js:230-466` | 已接通；列表固定200条；真实工具可用性未实测 |
| Skill `/skills` | 名称、说明、步骤、规则、版本、测试、发布、复制/分享/归档 | `/api/skills/*` | `assets/platform/capabilities.js:473-567` | 部分编辑能力：recommended_tools/required_resources/examples没有编辑控件，保存固定写空数组；编辑已有包含这些字段的 Skill 会丢失其值，应保留未编辑字段 |
| Plugin `/plugins` | 技能/工具组合、输入/能力包配置、版本、测试、发布、复制/分享/归档 | `/api/plugins/*` | `assets/platform/capabilities.js:574-665` | 已接通 |
| 用户 `/system/users`、`/:id` | 查询/分页、建用户、资料/状态、角色部门、密码及用户详情 | `/api/v1/system/users/*` | `assets/platform/users.js:192,221` | 已接通 |
| 工作区/组织/成员调动 `/system/workspaces` | 工作区、组织设置，成员跨工作区调动 | `/api/v1/system/workspaces*`、`organizations*` 等 | `assets/platform/workspaces.js:98,133,161,193` | 已接通 |
| 部门、岗位 `/system/departments`、`/system/positions` | 组织层级、岗位、成员与筛选分页、增改移除 | `/api/v1/system/departments*`、`positions*` | `assets/platform/organization.js:96` | 已接通 |
| 角色、权限 `/system/roles`、`/system/permissions` | 角色配置、权限目录、资源授权/撤销、变更记录 | `/api/v1/system/roles*`、`permissions`、`resource-grants`、`permission-history` | `assets/platform/roles.js:96`；`permissions.js:106,116,136,172` | 已接通 |
| 菜单、设置、会话 `/system/menus|settings|sessions` | 导航标签排序、平台参数、登录会话查看/撤销 | `/api/v1/system/menus*`、`settings*`、`sessions*` | `assets/platform/administration.js:28,48,68` | 已接通；站点名渲染被platformName固定为“智慧云脑 Cloud Brain”，不随site.name改变 |
| 登录与企业身份配置 `/system/security` | 登录策略、可信设备、登录事件、SSO提供方管理 | `/api/v1/system/security/*` 等 | `assets/platform/security-governance.js:56,103,152,231,266` | 已接通 |
| 模型与用量 `/system/models` | 模型角色配置、模型目录、连通测试、计价和使用统计 | `/api/models/*` | `assets/platform/model-governance.js:106,149,201,262` | 已接通；实际供应商调用未执行 |
| 系统运维 `/system/operations` | 服务健康、公告、站内消息、调度任务、备份清单/创建/下载 | `/api/v1/system-tools/*` | `assets/platform/system-tools.js:85,172,197,250,289,312` | 已接通；任务执行和备份有实际副作用，本轮未触发 |
| 开发与集成 `/system/development` | API目录/契约、OpenAPI/Postman、客户端代码、认证客户端、证书登记、数据迁移 | `/api/v1/system-tools/*` | `assets/platform/system-tools.js:325,387,411,429,458` | 已接通；当前路由调用这里，不应把旧 renderApiCenter 当现行入口 |
| 个人资料 `/account/profile` | 个人资料修改、密码与会话相关操作 | `/api/auth/*`及资料接口 | `assets/platform/profile.js:44,77,106` | 已接通 |

表中 `assets/platform/*` 均以 `apps/web/` 为根，`views/*` / `components/*` / `composables/*` / `api/*` 均以 `apps/analytics/src/` 为根。

## 需要在功能文档中更正的边界

1. 产品当前不是“只有聊天/知识/文件的静态前端”。业务导航覆盖上表六个业务域；数据分析、大屏、工作流、预警及质检是实际组件和请求链路。
2. 首页实际 `renderHome`；`apps/web/index.html:5` 的“首页也渲染聊天”注释已过时。聊天是 `/ai`。若记录页面数，应按当前路由与子视图计，不能把遗留 HTML 文件数直接当功能数。
3. `/quality` 是产品质检目录；`/analytics/quality` 是数据库样本质量检查；`/alerts` 是设备预警目录。三者功能不同。
4. 大屏的 `DataAnalysisDashboard` 和 `SmartChart` 由 `QueryResultPanel.vue:14-15,79-99` 使用，是查询结果页里的图表/看板；独立报表设计和分享预览属于另一套 `/report/*` 流程。
5. 当前大屏 AI 生成请求是真实接口，设计器在 `JimuBigScreenReplica.vue:46` 引入 `GenerationStatusPanel`，它在 `business/GenerationStatusPanel.vue:22` 明确展示不确定进度。智能查询生成入口使用 `DashboardGenerationStatus` 显示等待秒数。旧 `AiGenerationConsole.vue` 及 `utils/aiGenerationProgress.js` 包含本地模拟百分比/遥测，但当前业务源码无调用方；其“设计器使用”注释已过时，不能把它算作当前页面功能，更不能当真实执行遥测。
6. Skill表单只覆盖四类文字字段。未提供全部 Skill spec 的可视化编辑，且当前 `skillSpec` 会清空未提供的数组。这是实际功能范围/数据保留缺口，不能写“完整 Skill 配置编辑”。
7. 应用、连接器等列表存在固定数量上限；“全量管理”说明需与分页/筛选能力区分。
8. 当前生产构建入口是 `apps/web/assets/analytics/index.html` 引用的 `index-CEtXmRcG.js` 与 `index-Bi4LxymL.css`；历史哈希 chunk 留存不能当成多套活跃业务版本。当前工作流 chunk `WorkflowWorkbench.vue_vue_type_script_setup_true_lang-BbsYDqO_.js` 中也确认存在 F1/F2。
9. `apps/analytics/public/jimu-screen-reference/**` 是抓取的第三方参考产物，应单列参考文件，不纳入维护源码逐行审查；已集成修改的 Lynx衍生组件仍属于本项目维护功能。

## 本轮覆盖和验证

- 静态功能/调用特征扫描 397 文件：analytics/src 239（159 Vue、41 TS、25 JS、11 CSS、3 JSON），web顶层及platform维护文件55，tests/ui 103。结果在 `frontend-static-scan.json`；所有JS/TS脚本及Vue解析均无语法错误。
- 当前analytics入口可达的JS/CSS/HTML依赖闭包80文件，未发现缺失资源。检查结果不覆盖远程资源可达性、图片像素正确性，也不等同页面功能实测。
- 深入追踪：页面注册、登录进入工作台、查询及历史回填、SQL/数据源、模板与大屏编辑/查询展示、工作流加载/保存/发布/运行/恢复、质检批次、文件/知识上传、业务与系统模块操作入口。
- `npm run typecheck`（apps/analytics）：通过，未构建、未覆盖在用静态产物。
- `node --test tests/offline/*.mjs`：45 passed。
- tests/ui中的11个纯离线脚本：52 passed（导航、知识上传队列、草稿检查点、指标映射、表头、组件文字/缩放、工作流JSON/连线、语音分支）。
- `node tests/ui/chat_stream_recovery.test.js`：19个场景通过。
- 本次新增两份独立离线复现脚本：确认F1、F2、F4，共3个实际功能问题复现；F3由前后端输入契约代码交叉确认，F5由前端提交、后端版本保存和实际创建来源三处交叉确认。
- 没有执行tests/ui内浏览器验收、LAN脚本、真实variantorm查询、模型调用、工作流执行或定时任务。存在这类测试文件不代表本轮已运行。
- 未修改应用源码。新增的报告/扫描清单/复现脚本仅用于审查证据。
