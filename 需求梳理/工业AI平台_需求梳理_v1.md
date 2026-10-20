# 工业 AI 平台（智慧云脑）需求梳理 v1.0

> 需求来源：豆包对话线程 `xtlnQmSMZunC1D576`（6 轮需求 + 6 轮方案）
> 需求落点：当前工作目录 `E:/code/Yun_tian/new_agent/` 下 7 个实际项目
> 梳理日期：2026-10-20 ｜ 角色：产品经理（司南）
> 文档性质：需求梳理 + 功能/实现/界面三列映射，非最终 PRD

---

## 0. 结论摘要（先看这一段）

1. **平台定位已确定**：面向食品/化工/纺织制造业的「工业版 WorkBuddy」——不是成品 AI 应用，而是可插拔的工业 AI 能力底座。
2. **主平台已经存在**，不是从零开始：`the_company_agent-...20261020`（智慧云脑）已落地 **15 个业务插件**（`configurations/business_apps.json` 实测，README 记为 14）、34 个路由模块、20+ 前端路由，覆盖对话、数据分析、工作流、知识库、文档、报表大屏、设备预警、质量检查、应用与系统管理。
3. **四个开源/自研项目各有明确角色**：Dify=编排层、Open WebUI=对话前端参照、qKnow=知识中枢、自研报表引擎（已内化为 `data.analytics`+`reports`）=取数与出图。
4. **两个新增工程资产补齐了「行业味」**：`erp-demo` 提供非 AI 业务页面样板（6 模块），`设备预测维护数据包` 提供 9 台设备/233.28 万行分钟级时序 + 3 类设备 Skill + 14 册知识文档。
5. **真正的缺口收敛在 12 个方向**（见第 7 节），对应 15 项新建功能；其中 C08（14 册知识文档）、D12（3 类设备 Skill）、I10（ERP 行业页原型）三项**内容已备、只差接入**。缺口集中在：工业协议网关、常驻规则引擎、CV 推理服务、工具网关、统一 SSO、MLOps、任务托管、主动推送。
6. **最大架构红线**：Dify 是编排层不是实时引擎；数据库是唯一可信事实源；AI 对话不是唯一入口。

---

## 1. 需求溯源（6 轮对话 → 需求主题）

| 轮次 | 用户原话要点 | 提炼出的需求主题 | 承接章节 |
|---|---|---|---|
| 1 | 已数字化企业的 AI 化怎么做 | 三层 AI 落地策略（感知/预测优化/大模型）、数据资产评估、行业差异化 | 全文基调 |
| 2 | 想做一个通用平台，现在通用平台需要什么 | 7 大必备模块（接入/特征/MLOps/模型市场/编排/门户/运维） | 域 A、B、F、I |
| 3 | 打算用 dify+open web ui+qknow+自研 nosql 报表，媲美 workbuddy | 4 组件职责划分 + 4 个必须自研补齐的模块 | 第 3、5 节 |
| 4 | 数据从已准备好的数据库取，缺失的 open web ui 和 dify 补齐 | 「数据库=唯一事实源 + AI 只补推理」的双源标注规则 | 域 A、C、F |
| 5 | dify 是工作流，负责设备预警、质检、数据处理、自动化运行，主功能在对话界面，内部全部打通 | ① 纠正 Dify 边界 ② 四功能真实归属 ③ 对话为唯一入口 | 域 D、E、H |
| 6 | 参考 Test5 全功能业务平台重构方案 V2 | 业务系统为主体、AI 对话为独立模块、三入口复用同一后端、P0–P6 分期 | 全文骨架 |

**需求核心矛盾（已在方案中裁决）**：用户希望「主功能在对话界面」，而工业场景要求「实时性 + 强审计 + 业务页面可用」。裁决结果——**对话是主入口，不是唯一出口**；实时判定走常驻服务，对话负责解析、解释、生成与回溯。

---

## 2. 平台定位与边界

**定位**：制造业工业 AI 能力底座。内核通用、业务插件化；配置优先、代码为辅；不绑定特定 MES/SCADA。

**三条不可越过的边界**：

| 边界 | 含义 | 违反后果 |
|---|---|---|
| 不替代业务系统 | 不做 MES/SCADA/WMS/ERP 的替代品，只做 AI 增强层 | 项目周期失控、客户替换成本过高 |
| 不让 AI 覆盖真实数据 | 数值必须来自数据库；知识库内容必须标注【参考知识，非实时业务数据】 | 生产决策被幻觉污染 |
| 不做成全站聊天 | 业务页面与对话并列，共用同一后端服务 | 无审计、无权限、无法验收 |

---

## 3. 现有资产盘点（7 个项目 → 平台角色）

| # | 项目（目录） | 实际形态（已核实） | 在平台中的角色 | 复用方式 |
|---|---|---|---|---|
| 1 | `the_company_agent-codex-python-snapshot-20261020` | 智慧云脑：Node DSH 宿主 + Python business_worker（JSON-RPC）；**15 业务插件** / 34 路由模块 / 20+ 前端路由；默认端口 8011 | **主平台（承载约 80% 需求）** | 直接演进，不重写 |
| 2 | `dify-main` | Dify **v1.17.1** 源码（api / web / dify-agent / sdks） | **编排层**：可视化工作流、Agent、工具调用、Trace | 二选一：作为 M02/M11 运行时接入，或仅作画布/编排范式参照 |
| 3 | `open-webui-main` | Open WebUI **v0.12.3** 源码（Svelte 前端 + Python 后端） | **对话前端参照 / 多模型网关参照** | 参照其对话交互与模型管理，主入口仍用平台 `/ai` |
| 4 | `qKnow-develop` | Java Spring Boot + Vue；模块 ai / app / dm / ext / kb / kg / kmc / system；Neo4j + MySQL + Redis | **知识中枢**：RAG + 本体 + 知识图谱 | 以服务方式并入 `knowledge` 插件，不合并代码 |
| 5 | `qknow-design-test` | 单文件设计稿（5.5 MB），8 个页面：knowledge / documents / graph / agent / workflow / apps / components / models | **知识域 UI 规范来源** | 抽取信息架构与组件规范，供 `/knowledge` `/graphs` `/workflows` 对齐 |
| 6 | `erp-demo` | 单文件 ERP 原型（143 KB）；6 模块：工作台/采购/销售/库存/财务/审批；25 项布局 + 13 组业务校验通过 | **非 AI 业务页面样板**：证明「业务系统为主体」长什么样 | 作为行业应用包（FR-I10）的设计基线，示例数据不可当真实数据 |
| 7 | `设备预测维护数据包` | 9 台设备 / 2,332,800 行分钟数据（Parquet）；3 类设备 Skill（食品均质机、化工离心泵、喷气织机）；14 册知识文档（KB-FD/CH/TX/GN）；运行记录 + 工单 + 振动片段 | **工业数据与知识底座 + Skill 编写样板** | 数据入时序库；Skill 迁入 `plugins/business/`；文档入知识库 |

> 关键判断：**不需要新建平台，需要做的是「把 4 个组件装进 1 个已有的壳，并补齐 12 个缺口」**。

---

## 4. 功能清单（功能 / 实现方式 / 界面）

图例：状态 **✅已有**（接口与页面均在）｜**🔧改造**（有基础需扩展）｜**🆕新建**（无实现）
实现方式列给出的是当前目录里的**真实模块路径与接口前缀**，可直接定位代码。

### 域 A｜数据与语义底座

| 编号 | 功能 | 实现方式 | 界面 | 状态 |
|---|---|---|---|---|
| FR-A01 | 多源数据源接入与连接管理 | `modules/datasource` → `/api/datasources` | `/datasources` | ✅ |
| FR-A02 | 采集任务（定时/增量） | `modules/collection` → `/api/v1/collection` | `/datasources` | ✅ |
| FR-A03 | 数据回写业务库（受控） | `modules/writeback` → `/api/v1/writeback` | 无独立页（能力级） | 🔧 |
| FR-A04 | 业务数据目录与指标口径 | `modules/analytics/business_catalog.py`；工具 `list_business_data_sources` | `/data-query` | ✅ |
| FR-A05 | 取数查询与表格导出 | `/api/v1/analytics`；工具 `query_business_data` / `export_query_spreadsheet` | `/analytics`、`/data-query` | ✅ |
| FR-A06 | 数据画像与质量概览 | `modules/analytics/data_profile.py` | `/analytics`（子页） | 🔧 |
| FR-A07 | 设备时序数据接入与测点绑定 | 设备包 Parquet → 需加时序适配层 | `/alerts`（测点页） | 🆕 |
| FR-A08 | 工业协议网关（OPC UA / MQTT / Modbus / Kafka） | 无实现；建议独立 edge-gateway 服务 | 无（后台） | 🆕 |
| FR-A09 | 数据质量规则（断点、空值、时间戳对齐、单位统一） | 参照设备包 `字段字典/` + `验证报告/` 建设规则引擎 | 数据质量看板 | 🆕 |

### 域 B｜AI 对话与任务执行

| 编号 | 功能 | 实现方式 | 界面 | 状态 |
|---|---|---|---|---|
| FR-B01 | 统一对话入口（流式、思考、工具事件） | `modules/chat` + DSH 宿主；`/api/chat` | `/ai` | ✅ |
| FR-B02 | 请求幂等与任务标识（request_id / task_id / run_id） | `modules/chat` + `modules/runtime` | `/ai`、`/runs` | ✅ |
| FR-B03 | 会话恢复（刷新续接、SSE 快照回放） | `/api/chat` 事件流 + 持久记录 | `/ai` | ✅ |
| FR-B04 | 能力注册表（模型可见工具集合） | `modules/capability` → `/api/v1/capabilities` | `/capabilities` | ✅ |
| FR-B05 | 业务 Skill 加载与资源读取 | `plugins/business/*/SKILL.md`；`list_business_skills` / `read_business_skill_resource` | `/skills` | ✅ |
| FR-B06 | 代码执行沙箱（Python / 自定义工具） | 工具 `python_execute` / `add_tool` | `/capabilities` | 🔧 |
| FR-B07 | 运行记录与调用链 Trace | `/api/runs`、`/api/v1/operations` | `/runs`、`/operations` | ✅ |
| FR-B08 | 对话产物工件化（报告/图表/文件持久化） | `modules/artifact`、`modules/files` → `/api/artifacts` | `/files` | ✅ |
| FR-B09 | 多轮任务草稿（跨轮保持业务草稿） | `task_id / draft_id` 稳定标识 | `/ai` | ✅ |
| FR-B10 | 自动结果回流为对话卡片 + 外部推送 | `modules/alerts/card_images.py` 已有卡片；企微/钉钉待接 | `/ai` + 外部 | 🔧 |
| FR-B11 | 统一提示词防护（数值必须查库、来源标注） | Dify/平台系统提示词 + 输出校验 | `/prompts` | 🔧 |
| FR-B12 | MLOps（数据集、训练、模型版本、漂移监控、双推理） | 无实现；一期建议只做模型版本 + 推理网关 + 指标监控 | 无（后台） | 🆕 |

### 域 C｜知识库与知识图谱

| 编号 | 功能 | 实现方式 | 界面 | 状态 |
|---|---|---|---|---|
| FR-C01 | 文档库、解析与切片 | `modules/knowledge` → `/api/knowledge` | `/knowledge` | ✅ |
| FR-C02 | 混合检索与重排 | `modules/knowledge/reranking.py`；工具 `search_knowledge` | `/knowledge` | ✅ |
| FR-C03 | 本体管理与实体抽取 | `/api/v1/ontologies`、`/api/v1/extractions` | `/ontology`、`/extraction` | ✅ |
| FR-C04 | 知识图谱构建与查询 | `/api/v1/graphs`；qKnow `qknow-module-kg`（Neo4j） | `/graphs` | ✅ |
| FR-C05 | 资料目录（设备手册、厂家资料） | `/api/v1/assets` | `/assets-catalog` | ✅ |
| FR-C06 | 知识 Bot（对内/对外问答） | `/api/v1/bots`、`/api/public/v1/bots` | `/bots` | ✅ |
| FR-C07 | 知识治理（来源核验、冲突标记、版本） | `modules/knowledge/governance.py` | `/knowledge`（治理页） | 🔧 |
| FR-C08 | 工业知识文档批量入库（14 册 KB-*） | 设备包 `知识库正式文档/` → 解析入库 | `/knowledge` | 🆕（数据已备） |
| FR-C09 | 故障—根因—参数 图谱联动 | qKnow kg + 设计稿 graph 页范式 | `/graphs`、`/graph-detail` | 🔧 |

### 域 D｜设备预警（对应 M16）

| 编号 | 功能 | 实现方式 | 界面 | 状态 |
|---|---|---|---|---|
| FR-D01 | 设备台账与状态 | `modules/alerts` → `/api/v1/alerts/devices` | `/alerts` | ✅ |
| FR-D02 | 测点管理与采集配置 | `/api/v1/alerts/points` | `/alerts`（测点页） | ✅ |
| FR-D03 | 预警规则编辑与版本留痕 | `/rules`、`/rules/{id}/revisions` | `/alert/rule` | ✅ |
| FR-D04 | 规则试运行 | 规则页试算接口 | `/alert/rule` | 🔧 |
| FR-D05 | **常驻规则运算 worker（不进 Dify）** | 无实现；建议独立 worker 订阅时序流 | 无（后台） | 🆕 |
| FR-D06 | 问题列表、认领与处置动作 | `/problems`、`/problems/{id}/actions` | `/alert/problem` | ✅ |
| FR-D07 | 专家经验与处置建议库 | `/advice` | `/alerts` | ✅ |
| FR-D08 | 预警报告生成与导出 | `/reports`、`/reports/{id}/export` | `/alert/report` | ✅ |
| FR-D09 | 预警卡片图（对话内呈现） | `modules/alerts/card_images.py` | `/ai` | ✅ |
| FR-D10 | 预警后自动触发维护工作流 | `POST /alerts/workflows`；`equipment_maintenance/alert_bridge.py` | `/alerts/devices/{id}/workflow` | ✅ |
| FR-D11 | 设备维护分析与节能建议（食品设备） | `food.equipment` 插件 8 个工具 | 无前端（对话驱动） | 🔧 |
| FR-D12 | 三类设备 Skill 化（均质机/离心泵/喷气织机） | 设备包 `技能/` → `plugins/business/` | `/skills` | 🆕（内容已备） |

### 域 E｜质量检查（对应 M17）

| 编号 | 功能 | 实现方式 | 界面 | 状态 |
|---|---|---|---|---|
| FR-E01 | 产品与检测指标定义 | `modules/quality` → `/api/v1/quality/products`、`/metrics` | `/quality` | ✅ |
| FR-E02 | 检测模板与版本管理 | `/templates`、`/templates/{id}/revisions` | `/quality` | ✅ |
| FR-E03 | 检测任务下发与执行 | `/inspections`、`/workflows/{id}/run-plan`、`/runs` | `/quality` | ✅ |
| FR-E04 | 图像上传与缺陷明细 | `/images`、`/inspections/{id}/images/{file_id}` | `/quality` | ✅ |
| FR-E05 | 人工复核 | `/inspections/{id}/reviews` | `/quality` | ✅ |
| FR-E06 | 让步接收评估与决策 | `/concession-rules`、`/concessions/evaluate`、`/decision` | `/quality` | ✅ |
| FR-E07 | 检测报告导出 | `/inspections/{id}/export`；工具 `export_inspection_report` | `/qc/{page}` | ✅ |
| FR-E08 | **CV 推理服务（GPU 独立部署）** | 无实现；YOLO 系列独立服务 | 无（后台） | 🆕 |
| FR-E09 | 增量检测与批处理 | `modules/quality/incremental.py`、`batch.py` | `/quality` | 🔧 |

### 域 F｜数据分析与报表大屏

| 编号 | 功能 | 实现方式 | 界面 | 状态 |
|---|---|---|---|---|
| FR-F01 | 自然语言取数（NL2Query） | `/api/v1/analytics` + Agent 意图解析 | `/analytics` | ✅ |
| FR-F02 | 分析任务异步执行与取消 | `modules/analytics/execution.py`、`dsh_query_execution.py` | `/analytics` | ✅ |
| FR-F03 | 报表模板管理 | `/api/v1/report-templates` | `/templates` | ✅ |
| FR-F04 | 报表设计与生成 | `/api/v1/reports`、`modules/reports/generation*.py` | `/report`、`/report/designer` | ✅ |
| FR-F05 | 数字大屏 / 积木屏 | `create_business_dashboard`、`update_business_dashboard` | `/dashboards`、`/jimu-screen` | ✅ |
| FR-F06 | 提示词模板库 | `/api/v1/report-prompts` | `/prompts` | ✅ |
| FR-F07 | 图表渲染出图 | `render_chart_image`、`render_dashboard_image` | `/report` | ✅ |
| FR-F08 | 大屏与报表持久化、版本可回溯 | reports + artifact | `/dashboards` | ✅ |
| FR-F09 | **NL2NoSQL 时序查询语义层** | 无实现；Mongo/Influx 语义映射需自建 | `/data-query` | 🆕 |

### 域 G｜文档与演示制作

| 编号 | 功能 | 实现方式 | 界面 | 状态 |
|---|---|---|---|---|
| FR-G01 | 文档生成（Word / Markdown） | `documents` 插件；`render_document` | `/documents` | ✅ |
| FR-G02 | PPT 四步生成（规划→写页→定稿→渲染） | `create_presentation_plan` / `write_presentation_slides` / `finalize_presentation` / `render_presentation` | `/documents` | ✅ |
| FR-G03 | 表格生成 | `render_spreadsheet` | `/documents` | ✅ |
| FR-G04 | 模板填充（Word/PPT 模板） | `fill_template`、`/api/presentation-templates` | `/templates` | ✅ |
| FR-G05 | 文件资产（上传/预览/版本/权限） | `modules/files` → `/api/files` | `/files` | ✅ |

### 域 H｜工作流与自动化运行

| 编号 | 功能 | 实现方式 | 界面 | 状态 |
|---|---|---|---|---|
| FR-H01 | 可视化工作流画布 | `modules/workflows` → `/api/v1/workflows`；设计稿 workflow 页；Dify 画布可替代 | `/workflows` | ✅ |
| FR-H02 | 工作流引擎与容器化执行 | `workflows/engine.py`、`containers.py` | `/workflows/{id}` | ✅ |
| FR-H03 | 表达式与数据绑定 | `workflows/expressions.py` | `/workflows/{id}` | ✅ |
| FR-H04 | AI 生成工作流草稿 | `workflows/generation.py` | `/workflows` | ✅ |
| FR-H05 | 工作流结果回看 | 工具 `query_workflow_results` | `/runs` | ✅ |
| FR-H06 | 定时调度（cron） | 平台 `SCHEDULER_ENABLED` + 调度器 | `/operations` | 🔧 |
| FR-H07 | **长任务托管（持久化/重试/失败告警）** | 部分具备；需补任务队列与补偿 | `/runs` | 🆕 |
| FR-H08 | 事件触发（预警命中 → 工作流） | monitoring `POST /workflows` + `alert_bridge` | `/alerts` | ✅ |
| FR-H09 | 外部触发 API / Webhook | 无实现；参照 Dify API 触发模式 | 无（开放接口） | 🆕 |
| FR-H10 | 主动推送通道（企微/钉钉/短信/邮件） | 无实现；`notifications.py` 仅站内 | `/ai` + 外部 | 🆕 |

### 域 I｜平台治理与行业应用

| 编号 | 功能 | 实现方式 | 界面 | 状态 |
|---|---|---|---|---|
| FR-I01 | 用户、组织、角色管理 | `platform.core` → `/api/auth`、`/api/v1/system` | `/system/users` | ✅ |
| FR-I02 | **统一 SSO（跨 Dify/OpenWebUI/qKnow）** | 无实现；Keycloak 或自研 | 登录页 | 🆕 |
| FR-I03 | **行级数据权限上下文透传** | 无实现；需在工具网关注入 | 后台 | 🆕 |
| FR-I04 | 审计日志 | `/api/audit` | `/system/audit` | ✅ |
| FR-I05 | 密钥与凭据管理 | `/api/secrets` | `/system/api` | ✅ |
| FR-I06 | 模型配置与用量统计 | `/api/models` | `/system/models` | ✅ |
| FR-I07 | 运行运维（健康、日志、诊断） | `/api/v1/operations`、`/api/v1/system-tools` | `/operations` | ✅ |
| FR-I08 | 应用与解决方案（行业模板包） | `modules/applications` → `/api/v1/applications` | `/applications` | ✅ |
| FR-I09 | **工具网关（权限矩阵/参数校验/限流/副作用确认）** | 部分具备（capability）；缺强制拦截层 | `/capabilities` | 🔧 |
| FR-I10 | ERP 行业业务页面（工作台/采购/销售/库存/财务/审批） | `erp-demo` 原型 → 改造为真实模块 | 独立业务页 | 🆕（原型已有） |

**功能统计**：✅ 已有 58 项 ｜ 🔧 改造 12 项 ｜ 🆕 新建 15 项 ｜ 合计 85 项（脚本核对，非估算）。
分布：A 数据底座 9 ｜ B 对话执行与 MLOps 12 ｜ C 知识图谱 9 ｜ D 设备预警 12 ｜ E 质量检查 9 ｜ F 分析报表 9 ｜ G 文档制作 5 ｜ H 工作流自动化 10 ｜ I 平台治理 10。

---

## 5. 界面清单（页面树 → 承载项目 → 现状）

### 5.1 主平台页面（`apps/web/platform.html` SPA，由 `backend/app/static_router.py` 按路径分发）

| 一级域 | 页面路由 | 说明 | 现状 |
|---|---|---|---|
| AI | `/ai`（`index.html`） | 对话主入口：流式、思考、工具、队列、交付事件 | ✅ |
| AI | `/runs` | 运行记录与任务详情 | ✅ |
| 数据 | `/datasources` | 数据源、采集任务 | ✅ |
| 数据 | `/data-query` | 数据目录与取数 | ✅ |
| 分析 | `/analytics`、`/analytics-app` | 分析工作台（独立 SPA） | ✅ |
| 报表 | `/report`、`/report/designer`、`/dashboards`、`/jimu-screen`、`/prompts`、`/templates` | 报表设计、大屏、提示词、模板 | ✅ |
| 知识 | `/knowledge`、`/ontology`、`/extraction`、`/graphs`、`/bots`、`/assets-catalog` | 知识库、本体、抽取、图谱、Bot、资料目录 | ✅ |
| 文档 | `/documents` | 文档/PPT/表格生成 | ✅ |
| 工作流 | `/workflows`、`/workflows/{id}` | 画布与运行详情 | ✅ |
| 预警 | `/alerts`、`/alert/rule`、`/alert/problem`、`/alert/report`、`/alerts/{page}`、`/alerts/devices/{id}/workflow` | 台账、测点、规则、问题、报告、维护工作流 | ✅ |
| 质检 | `/quality`、`/qc/{page}`、`/quality/{page}`、`/quality/devices/{id}/workflow` | 检测、复核、让步接收、报告 | ✅ |
| 能力 | `/capabilities`、`/connectors`、`/skills`、`/plugins` | 工具、连接器、技能、插件 | ✅ |
| 系统 | `/system/users`、`/system/models`、`/system/api`、`/system/audit`、`/account/profile`、`/operations`、`/applications` | 账号、模型、密钥、审计、运维、应用 | ✅ |
| 文件 | `/files` | 文件资产 | ✅ |

### 5.2 设计稿页面（`qknow-design-test`，用于知识域 UI 对齐）

| 页面 | 文件 | 可借鉴点 |
|---|---|---|
| 知识库 | `knowledge-desktop.png` | 文档树 + 切片预览 + 检索测试 |
| 文档 | `documents-desktop.png` | 文档解析状态与分块可视化 |
| 图谱 | `graph-desktop.png`、`graph-detail-desktop.png` | 力导向图 + 实体详情侧栏 |
| Agent | `agent-desktop.png`、`agent-conversation-desktop.png` | Agent 配置 + 会话过程展示 |
| 工作流 | `workflow-desktop.png`、`workflow-canvas-desktop.png` | 画布节点样式与连线规范 |
| 应用 | `apps-desktop.png` | 应用市场卡片 |
| 组件 | `components-desktop.png` | 组件库与设计令牌 |
| 模型 | `models-desktop.png` | 模型接入与参数面板 |

### 5.3 业务页面样板（`erp-demo`，用于「业务系统为主体」的界面基线）

| 模块 | 截图 | 说明 |
|---|---|---|
| 工作台 | `dashboard-desktop.png` | 指标卡 + 待办 + 趋势 |
| 采购 | `purchase-desktop.png` | 单据列表 + 明细 + 审批状态 |
| 销售 | `sales-desktop.png` | 订单与发货 |
| 库存 | `inventory-desktop.png` | 仓库台账 + 可用量（现存−占用） |
| 财务 | `finance-desktop.png` | 应收应付与收付款登记 |
| 审批 | `approvals-desktop.png` | 分栏审批视图 |
| 滚动隔离 | `dashboard-scroll-isolation.png` | 外壳不滚动、内容区独立滚动 |

### 5.4 需新增的界面

| 页面 | 归属域 | 原因 |
|---|---|---|
| 数据质量看板 | A | 断点/空值/时间戳对齐无从查看 |
| 协议网关配置页 | A | OPC UA/MQTT 点位映射无界面 |
| 长任务与调度中心 | H | 定时任务、重试、失败告警无集中视图 |
| 推送通道配置 | H | 企微/钉钉/邮件无配置入口 |
| SSO 与权限矩阵 | I | 跨组件账号与行级权限无可视化 |
| 设备维护分析页 | D | `food.equipment` 8 个工具只有对话入口 |

---

## 6. 实现方式矩阵（按技术层）

| 层 | 选型 | 落地位置 | 说明 |
|---|---|---|---|
| 交互层 | 平台 `platform.html` SPA + `index.html` 对话 + `apps/analytics` 独立 SPA | the_company_agent | Open WebUI 仅作交互参照，不并列部署第二套前端 |
| 编排层 | `modules/workflows`（默认）／Dify 1.17.1（可选运行时） | the_company_agent ／ dify-main | 二选一，禁止两套同时持有业务编排状态 |
| 执行层 | Node DSH 宿主 + Python business_worker（私有 JSON-RPC） | `services/dsh_host`、`backend/app/business_worker` | 已在用，任务排队/取消/事件订阅由宿主统一管理 |
| 业务服务层 | 34 个 Python 路由模块（FastAPI ASGI，进程内） | `backend/app/modules/*` | 业务页面与 AI 对话共用同一套 service，禁止对话直连数据库 |
| 知识层 | 平台 `knowledge` 插件（应用层）+ qKnow（RAG/本体/图谱引擎） | 双写风险需治理 | 图谱数据以 qKnow 为权威，平台只读展示 |
| 数据层 | 业务库（默认 `szh_new`）+ 时序库（Mongo/Influx，待建）+ 文件存储 | datasource/collection | 数值唯一来源；AI 不得覆盖 |
| 模型层 | DSH 模型适配（DeepSeek Harness）+ 视觉角色 | `modules/model` | 私有化部署与幻觉校验待补 |
| 治理层 | 工具网关 + 审计 + 密钥管理 | `capability`、`audit`、`secret` | 工具网关需强化为强制拦截 |

---

## 7. 缺口清单（12 项，按优先级）

| 编号 | 缺口 | 影响 | 建议做法 | 优先级 |
|---|---|---|---|---|
| GAP-01 | 工业协议网关（OPC UA/MQTT/Modbus） | 无法直连产线，只能靠定时导出 | 独立 edge-gateway 服务，统一转 MQTT→时序库 | P1 |
| GAP-02 | 常驻规则引擎 worker | 实时预警无法实现（Dify 无状态） | 独立 worker 订阅时序流，命中后投事件 | P1 |
| GAP-03 | 工具网关（权限矩阵/参数 Schema/限流/副作用确认） | Agent 可能越权写业务数据 | 在 capability 前加一道强制拦截网关 | P1 |
| GAP-04 | 统一 SSO + 行级权限透传 | 四组件四套账号，权限孤岛 | Keycloak 或自研，令牌带租户与数据范围 | P1 |
| GAP-05 | CV 推理服务（GPU） | 质检只能人工录入结果 | 独立推理服务，batch 回调 `/inspections` | P2 |
| GAP-06 | MLOps（数据集/训练/模型版本/漂移监控/双推理） | 模型上线后无运维，必然漂移失效 | 一期只做模型版本 + 推理网关 + 指标监控 | P2 |
| GAP-07 | 长任务托管（持久化/重试/失败告警） | 长报告生成易中断且无补偿 | 复用 runs + 调度器，补重试策略与告警 | P2 |
| GAP-08 | 主动推送（企微/钉钉/邮件/短信） | 预警只能站内看，值班会漏 | 事件 → 推送适配器，统一模板 | P2 |
| GAP-09 | NL2NoSQL 时序语义层 | 时序问数只能靠预置查询 | 语义映射 + 受控查询模板，不让模型自由写查询 | P3 |
| GAP-10 | 工业知识文档入库（14 册） | 知识库空转，无行业内容 | 批量解析 + 切片 + 来源标注 | P3 |
| GAP-11 | 三类设备 Skill 迁移 | 设备维护能力只在数据包里 | Skill 目录迁入 `plugins/business/` 并注册 | P3 |
| GAP-12 | 数字孪生 / 三维对接 | 阶段三「全链路决策」缺可视化载体 | 大屏先用 2D 组态，孪生后置 | P4 |

---

## 8. 分期路线（对齐现有资产）

| 阶段 | 目标 | 交付界面 | 交付接口 | 起点 |
|---|---|---|---|---|
| P0 基线冻结 | 能力注册表、接口前缀、数据口径、Dify 定位一次性定死 | — | `configurations/business_apps.json`（已是事实源） | 已有 |
| P1 底座 | SSO、工具网关、审计、数据源连接器、qKnow 接入 | 登录页、`/system/audit`、`/datasources` | `/api/auth`、`/api/audit`、`/api/datasources` | GAP-01~04 |
| P2 业务模块（不依赖对话） | 预警、质检、分析报表、文档独立可用 | `/alerts`、`/quality`、`/report`、`/documents` | `/api/v1/alerts`、`/api/v1/quality`、`/api/v1/reports` | 已有 80% |
| P3 AI 对话打通 | 工具注册、双链路、工件体系 | `/ai`、`/capabilities`、`/files` | `/api/chat`、`/api/v1/capabilities` | 已有 |
| P4 知识与工作流 | 图谱、画布、知识 Bot、工作流生成 | `/graphs`、`/workflows`、`/bots` | `/api/v1/graphs`、`/api/v1/workflows` | 已有 + GAP-10/11 |
| P5 自动化运行 | 定时、事件触发、推送、长任务托管 | 调度中心（新建）、`/runs` | 调度器 + Webhook | GAP-07/08 |
| P6 行业化与验收 | 三类设备 Skill、ERP 行业包、全量回归 | `/applications`、`/skills` | `/api/v1/applications` | erp-demo + 数据包 |

---

## 9. 风险登记册

| 编号 | 风险 | 概率 | 影响 | 等级 | 应对 |
|---|---|---|---|---|---|
| RK-01 | 四组件权限孤岛（Dify/OpenWebUI/qKnow/平台各一套账号） | 5 | 4 | 20 高 | P1 必做统一 SSO，令牌携带租户与数据范围 |
| RK-02 | Agent 幻觉写入业务数据 | 3 | 5 | 15 高 | 工具网关拦截写操作；高危动作二次确认；数值只来自数据库 |
| RK-03 | 实时性错配（把预警塞进 Dify 工作流） | 4 | 4 | 16 高 | 明确 Dify 只做编排；常驻 worker 独立部署（GAP-02） |
| RK-04 | 时序数据量增长（现 233 万行/9 台） | 4 | 3 | 12 中 | 时序库分区 + 预聚合，查询走聚合表 |
| RK-05 | 预警/质检误报导致一线不信任 | 4 | 4 | 16 高 | 冷启动只告警不干预；误报回收闭环；阈值人工复核 |
| RK-06 | 三个上游版本升级冲突（Dify/OpenWebUI/qKnow） | 3 | 4 | 12 中 | 上游只以服务方式接入，不改源码；锁版本 + 契约测试 |
| RK-07 | 演示数据被误当真实数据（erp-demo 明确为虚构） | 3 | 3 | 9 中 | 数据源标注 demo 标识；界面加示例水印 |
| RK-08 | 生产数据合规（工艺参数属商业秘密） | 3 | 5 | 15 高 | 私有化部署；审计留痕；导出审批 |

---

## 10. 待确认问题（3 个关键决策点）

1. **编排层到底用哪一套？** 平台已有 `modules/workflows`，Dify 1.17.1 也在目录里。若两套并存，工作流状态会分裂。建议：业务工作流用平台自带，Dify 仅用于「AI 生成的临时编排」；或反过来，需你拍板。
2. **实时预警的承载方式？** 目前 `alerts` 只有规则 CRUD 与人工触发，没有常驻运算。确认是自建 worker，还是依赖客户的 SCADA/规则引擎做初筛、平台只接事件？
3. **三类设备 Skill 是否一期就上？** 设备包内容完备（3 类设备、14 册文档），但入库需要解析与标注工时。若一期只做食品（`food.equipment` 已有 8 工具），化工/纺织可放 P3。

---

## 附录 A：需求追溯矩阵（摘要）

| 需求主题 | 功能编号 | 界面 | 阶段 |
|---|---|---|---|
| 数据接入与语义 | FR-A01~A09 | `/datasources`、`/data-query` | P1–P3 |
| 对话与执行 | FR-B01~B12 | `/ai`、`/capabilities`、`/runs` | P3 |
| 知识中枢 | FR-C01~C09 | `/knowledge`、`/graphs`、`/bots` | P4 |
| 设备预警 | FR-D01~D12 | `/alerts`、`/alert/*` | P2、P6 |
| 质量检查 | FR-E01~E09 | `/quality`、`/qc/*` | P2 |
| 分析报表 | FR-F01~F09 | `/analytics`、`/report`、`/dashboards` | P2 |
| 文档制作 | FR-G01~G05 | `/documents`、`/files` | P2 |
| 工作流与自动化 | FR-H01~H10 | `/workflows`、`/runs`、调度中心 | P4–P5 |
| 平台治理 | FR-I01~I10 | `/system/*`、`/operations`、业务页 | P1、P6 |

## 附录 B：合规声明

本方案为产品设计参考，具体实施请结合公司合规与法务要求确认。文中所有接口前缀、模块路径均来自当前目录实际代码核查；`erp-demo` 数据为虚构示例，不得用于真实业务；涉及生产数据、个人信息与商业秘密的处理需另行通过安全评审。
