# AI 对话、模型、工具与产物功能审查

审查日期：2026-09-26。以当前代码为依据，重点说明功能实际走什么链路、结果落在哪里、哪些入口已接通或仍有缺口。本报告取代同名首版，聚焦功能，不包含安全审查结论。没有修改业务源码，没有启动服务、调用模型、发出网络请求、连接数据库或读取秘密配置。

## 阅读覆盖与结论口径

范围为 `backend/app/modules/{runtime,harness,openwebui,chat,chat_artifacts,artifact,presentation,model,skill}`、`backend/app/providers`、`backend/app/workers`、`services` 一方代码；不存在 `modules/tool`。文件清单与 Python AST 函数/类位置见 `runtime-inventory.json`：109 个源码文件、23,941 行，包含 4 个生成 JS 和 2 个一方测试文件，排除第三方 `services/openwebui/upstream`、依赖与缓存。

109 个文件均完成全文读取用于结构扫描、行数统计和 Python AST 解析；这不等于每一行都经过人工语义审查。深读覆盖 chat 提交/事件/取消、后台任务/孤儿恢复、Harness RPC/工具/终态、各 provider 入口、worker、模型配置、技能、产物持久化、PPT 草稿/模板/规格入口、服务包装与代理。旧 `runtime/agent.py` 的长推理循环、PPT 图形布局/规格兼容器的全部分支、Open WebUI 补交与事实依据算法仅做入口和关键段落审查，未宣称全面无缺陷。

下文“链路接通”表示前后端入口、函数调用、存储和结果回传在代码中能串起来；不等于已在真实模型、真实服务、真实业务数据和浏览器中验收通过。PPT 视觉质量与外部模型/模板渲染依赖均未实测。

## 实际主链路

1. 对话页 `/ai` 使用 `apps/web/index.html` 与 `apps/web/app.js`；管理平台使用 `apps/web/platform.html` 和 `assets/platform/*`，不能把两个入口混为同一前端。
2. `POST /api/chat/conversations/{id}/send` → `chat/router.py:317,346` → 选择模型、保存资源快照和 Message/Run → `runtime/chat_jobs.py:167` 后台执行 → `runtime/engine.py:20` 分发。源码默认 `config.py:199` 为 `CHAT_ENGINE=harness`；本次未读取部署配置，不能据此宣称当前部署一定使用 Harness。
3. Harness 分支 → `harness/runtime.py:219` → `harness/context.py:42,87` 构建模型和提示词 → `harness/client.py` 启动原生 Node DSH → `services/deepseek_harness/profile.mjs` 注册模型适配器和 company 插件 → 插件 RPC 回调 Python 工具网关。模型推理由原生 Harness 驱动，业务工具仍由本项目 Python 执行。
4. Open WebUI 分支是完整保留的显式旧引擎，通过 `openwebui/client.py` 的 HTTP/Socket.IO 与服务通信；其模型/工具回调返回项目 provider 和工具网关。它有独立的补交、任务完整性和重试逻辑，并非 Harness 失败后的自动 fallback。
5. 引擎事件由 `chat_jobs.py` 写入 `RunEvent(chat.stream)` 并实时发送；`GET /api/chat/runs/{id}/events?after=` 重放。Run、Message、ModelCall、ToolCall、Artifact 等保存各自状态。断线重放、孤儿终止和“新一轮继续任务”是三个不同机制。

## 功能矩阵

| 功能 | 前端 / API 入口 | 核心实现 | 存储、结果与当前状态 |
| --- | --- | --- | --- |
| 会话与普通 AI 对话 | `/ai`；`/api/chat/conversations`、`/{id}/send` | `chat/router.py:155,168,184,317,346`；`runtime/engine.py:20`；`harness/runtime.py:219` | Conversation/Message/Run；SSE 文本与最终消息。主入口接通；默认引擎为源码配置的 Harness，部署值未核验。 |
| 多轮原生会话 | 同一会话继续发送 | `harness/runtime.py:123,128` 的 session identity/native binding；`services/deepseek_harness/profile.mjs` 会话持久化插件 | DSH 会话日志与数据库 Run 绑定；模型上下文可续接，不代表项目外部 PPT 草稿自动跨 Run 迁移。 |
| 流式展示、刷新恢复、停止 | `runs/{id}/events`、`runs/{id}/cancel`、`runs/{id}/trace` | `chat/router.py:530,544,574`；`runtime/chat_jobs.py:167,295,312`；`runtime/chat_liveness.py` | RunEvent 可按游标重放，取消信号和孤儿恢复已有实现；孤儿 ModelCall 提交存在 F2。真实多进程取消未验收。 |
| 网页搜索 / 深度研究 | `apps/web/app.js:2131-2132` 的加号菜单，发送 `mode` | `app.js:1717`；`chat/router.py:455` 仅保存 mode；`harness/context.py:87` 未消费它 | **部分接通：UI 和快照有值，默认 Harness 没有对应搜索/研究执行分支。** 见 F4。 |
| 模型选择与档位 | 对话模型选择器；`GET /api/chat/models` | `chat/router.py:148`；`chat/model_selection.py`；`modules/model/service.py` | 从启用、支持 chat/reasoning、密钥已配置的有效档位产生选择项；发送时再次选择档位。链路接通，未作模型连通测试。 |
| 模型配置、市场、探测 | `/system/models`；`/api/models/profiles`、`marketplace`、`probe/*` | `apps/web/assets/platform/model-governance.js`；`model/router.py:137,232,248,299,349,376` | ModelProfile、ModelProbeResult；支持新增/修改/停用和真实探测。高级 runtime_options 有 API，但 UI 无字段，见 F3。 |
| 模型协议与思考配置 | ProfileCreate/Update 的 `runtime_options` | `model/runtime_options.py`；`harness/context.py:42`；`services/deepseek_harness/profile.mjs:51-66` | 支持 DeepSeek native 与 OpenAI compatible 适配；thinking 参数传递不完整，见 F5。 |
| 模型用量、费用、预算 | 模型管理 usage；`/api/models/usage`、`usage/export`、`cost-policy` | `model/router.py:150,172,198,211`；model service；Harness model authorize/usage 回调 | ModelUsageRecord 与预算设置；有导出及费用统计链路。Run/ModelCall 与独立用量账本的恢复一致性存在 F2。 |
| 企业业务工具调用 | 对话工具选择/模型 function call | `harness/runtime.py:96` approved_tools → `providers/tool/gateway.py` → provider | ToolCall、RunStep 与工具回执；HTTP/database/MCP/builtin/sandbox 分发实现存在。工具能否运行取决于已发布连接器、资源与依赖，未实际请求。 |
| Python / 文件操作工具 | 网关工具能力，非默认 Harness 可见工具 | `providers/tool/sandbox_provider.py`；`workers/python_worker.py`；`harness/runtime.py:87-98` | 子进程执行、超时、输入输出文件和结果解析已实现；**默认 Harness 明确排除 python_execute、write/copy/rename/delete_file、add_tool**，不能据 provider 存在就声称聊天可任意执行 Python。 |
| 文件读取与图片理解 | 对话附件；list_files/read_file 工具 | `providers/tool/builtin_provider.py:1067,1149,1281` → files service/extractor → vision | 提取文本、分页/偏移、匹配片段和图片识别结果；识图可调用 vision profile 并缓存。模型主要收到工具文本，不能等同所有附件原样作为多模态消息送入 Harness。 |
| 知识库检索与引用 | 对话知识库选择；search_knowledge 工具 | `builtin_provider.py:1051` → knowledge service；运行时记录引用 | 检索结果成为工具回执并可保存引用 parts；选择的 KB 进入提示词。检索/嵌入/索引模块由其他审查范围覆盖，本次未连真实索引验证。 |
| 企业查询、告警和工作流结果 | list_business_data_sources/query_business_data/view_device_alerts/query_workflow_results 等 | `builtin_provider.py` 分发到 business_ai / analytics / workflow / device 模块 | 使用已保存查询、工作流和业务结果产生回执；本报告确认 AI 接线，业务查询语义由对应模块报告负责。 |
| 业务技能与 DB 技能 | `/api/skills` CRUD/版本/发布/测试/分享；`business-inventory`；对话 list/read_business_skill | `skill/router.py:58-198`、`skill/service.py:175`；`builtin_provider.py:824`；`harness/context.py:16` | 两套来源并存：数据库 Skill/SkillVersion，以及磁盘业务技能清单/资源。选择的插件/技能指令进入上下文；技能 test-run 是实际模型调用，不是纯语法检查。 |
| 文档 DOCX / PDF | 对话 render_document | `artifact/renderers.py:66,94,747`；chat_artifacts/document_images | 支持标题、段落、列表、表格、代码、图片、分页；DOCX/PDF 字节 → Artifact 与下载入口。链路接通；复杂排版与字体环境未渲染验收。 |
| 模板填充 | 对话 fill_template | `builtin_provider.py:1393` → `files/template_filler.py` | 已上传模板经文件模块读取和填充，生成新产物；不是空占位工具。各 Office 格式细节本次未全量分支验证。 |
| 表格生成与真实查询导出 | render_spreadsheet / export_query_spreadsheet | `artifact/renderers.py:678`；builtin → `analytics/exports.py` | 前者从规格生成 XLSX；后者导出保存的 QueryResult，保留来源/截断信息，避免模型重新抄写行数据。多 sheet/列/行处理存在；未打开 Excel 验收。 |
| 单图生成 | render_chart_image | `chat_artifacts/chart_renderer.py`，读取保存的 query result | 产生静态 PNG Artifact；是由已有查询结果渲染的图，不应描述成实时查询引擎或交互式图表。 |
| 业务大屏 / 报表 | create_business_dashboard / create_dashboard_from_queries / get/update_business_dashboard | `chat_artifacts/query_dashboard.py:174` 与 reports 模块 | 保存 Report 和 widgets；从保存的 query result 创建且可指纹复用。UI 交互和 reports 模块由其他审查覆盖。 |
| 大屏图片与巡检报告 | render_dashboard_image / export_inspection_report | `chat_artifacts/dashboard_image.py:204`，inspection 适配代码 | 大屏图片是程序绘制的结果摘要，限制面板与行列数，**不是浏览器完整大屏截图**；巡检报告复用业务结果形成 Artifact。 |
| PPT 模板选择/上传 | 对话 PPT 模板选择；`/api/presentation-templates` | presentation router/service、premium_catalog、preview | 内置模板 + 上传 PPTX，上传模板保存 File、元数据和预览。真实预览依赖本地 Node/artifact-tool；不可用时可降级占位，不保证每台环境都有真实预览。 |
| 分阶段 PPT 生成 | create_presentation_plan → write_presentation_slides → finalize_presentation | `presentation/draft.py:71,161,206,399`；`builtin_provider.py:880-1003` | 草稿 JSON 按 workspace/run 保存，密度/页数检查后渲染 PPTX；单次 Run 内接通，Harness 新 Run 继续未迁移旧草稿，见 F6。 |
| 直接 PPT 生成与模板适配 | render_presentation | `presentation/spec_normalizer.py`；`artifact/renderers.py:166,654`；`presentation/engine.py` | 内置排版产生可编辑文本/图形/表格/图表；上传模板采用原母版/布局或主题适配。不是保证逐页原样复制参考 PPT，视觉质量需逐页渲染验收。 |
| JSON / 工件下载 / 再作为输入 | save_json_artifact；`/api/artifacts`、`/{id}/download`、`/{id}/use` | `artifact/service.py:120`；`chat_artifacts/artifacts.py:11`；`artifact/router.py:25,39,60,90` | 文件入 storage、Artifact 入数据库，回传引用和下载链接；use 再建 File。`content` 当前只支持图片内联，不是任意文本/JSON 通用预览。 |
| 旧 Open WebUI 引擎 | 显式 `CHAT_ENGINE=openwebui` | `runtime/engine.py:20`；`openwebui/runtime.py`；`services/openwebui/serve.py` | 完整兼容分支，使用本地独立 Open WebUI 服务、桥接 key 和回调；有 DeliveryTracker/TaskCompletionGuard/草稿续作。它们不能自动算作 Harness 已拥有的功能。 |
| OpenCode 模型代理 | 配置模型端点使用本地代理 | `services/opencode_proxy.py` | 转发模型请求并补充约定字段；当前 SSE 定长读取造成事件积压，见 F1。普通直连模型不受此代理缺陷影响。 |

默认 Harness 可用的业务工具主要包括：时间、文件读取、知识检索、业务数据源/查询、设备告警、工作流结果、巡检报告、监控快照、大屏模板/查询/修改/生成/图片、DOCX/PDF、XLSX、查询结果导出、图表图片、PPT 分阶段/直接生成、模板填充、JSON 工件、业务技能读取；名单见 `harness/runtime.py:87-98`。已发布且显式配置的企业连接器可加入；HTTP/MCP provider 的存在不等于默认拥有互联网搜索工具。

## 具体功能故障与未接完整项

### F1 — P2：OpenCode 代理扣留 SSE 小块，流式回答变成末尾集中显示

`services/opencode_proxy.py:84-89` 的 `response.read(65536)` 会继续读到指定字节数或 EOF，之后才写下游。上游逐 token/SSE 发送但整段不足 64 KB 时，前端或 Harness 无法及时得到首批内容。后面的 flush 不能消除前面的缓存。

纯内存 `HTTPResponse` 输入两段 chunk：当前 read 一次返回 `data: A` 和 `data: B`；read1 一次只返回第一块。未创建 socket。证据为 `runtime-offline-results.json` 的 `proxy_read`。建议使用 read1 或按可用块读取并逐块 flush，验证首段先于后续段到达。

### F2 — P2：中断恢复未提交 ModelCall 终态，用量页与运行轨迹不一致

`runtime/chat_liveness.py:127-133` 先 commit Run，再调用 `_settle_dangling_model_calls`；`:137-163` 修改 ModelCall 并 db.add，但没有后续 commit。`db.py:68-70` 的 new_session 返回普通 Session，`chat_jobs.replay` 的 with 退出关闭时不会自动提交。Run 已终态，后续恢复又提前返回，ModelCall 可永久停留 started，而独立用量账本已经记录 failed。

纯内存伪 DB 执行提取的实际恢复函数证明：唯一 commit 时 ModelCall 还是 started，函数结束内存对象变成 failed，独立用量记录已有一行。证据为 `runtime-offline-results.json` 的 `recovery_commit`。建议把 ModelCall 状态与 Run 一并提交，账本在该事务成功后更新，并保留 commit=False 调用方控制。

### F3 — 功能未接完整：模型高级参数有后端 API，没有管理表单

`model/router.py:77,95,276,326` 接收并保存 runtime_options，`model/runtime_options.py` 定义 protocol/context_window/max_output_tokens/reasoning_effort/thinking/compat。新管理页面 `apps/web/assets/platform/model-governance.js:64-101` 的 profileDialog，以及旧页面 `apps/web/app.js:3249-3278`，均没有对应输入或 payload 字段。

用户仅通过页面无法配置这些参数，需要 API。`model/service.py` 对未传入的 runtime_options 会保留旧值，不能误报为每次 UI 编辑都清空配置。建议补充高级表单、字段解释和保存回显；文档应明确当前哪些设置只能通过 API。

### F4 — P2：网页搜索 / 深度研究菜单没有对应默认引擎能力

`apps/web/app.js:2131-2132` 展示“网页搜索：查找实时信息和来源”和“深度研究：执行多步骤分析”，`:1717` 提交 mode。`chat/router.py:455` 把值保存到快照，但 `harness/context.py:87` 构建上下文时没有消费 mode，`harness/runtime.py:87-98` 没有网络搜索工具或模式执行分支；原生 preset 在 `services/deepseek_harness/profile.mjs:37` 禁用了模式选择。

旧 Open WebUI 分支仅将 mode 写成提示文字，且 `openwebui/client.py:167` 明确 `web_search=False`。因此现有实现不能支撑该 UI 对实时网页查询的描述。“深度研究”同样没有独立管线，仅凭模型普通推理不能证明模式已实现。建议真实接入搜索/研究链路或先隐藏/准确改名这些选项。

### F5 — P2：thinking=disabled 没有按声明传到模型适配器

`harness/context.py:78-83`：OpenAI compatible 分支把 thinking=disabled 放进中间结果，但 `services/deepseek_harness/profile.mjs:60-66` 构建该适配器时未消费 model.thinking；只有 DeepSeek native 分支在 `:53` 消费它。反过来，DeepSeek native 的 `context.py:83` 只根据 reasoning_effort 是否为 off 设置 thinking，忽略 runtime.thinking，显式 disabled 且 effort 未设置时被转为 enabled。

这是配置到请求构建路径的静态确定性缺口，未实际调用模型。使用额外代理时代理可能补写禁用字段，但无法修复直连适配器的参数丢失。建议统一定义 thinking 与 reasoning_effort 的优先级，并在两种协议适配器分别转换为该协议真实支持的字段。

### F6 — 功能未接完整：Harness 新一轮“继续”不会恢复分阶段 PPT 草稿

`presentation/draft.py:12-16,40` 按当前 workspace/run_id 读取草稿；create/write/finalize 工具使用当前 run_id。`chat/router.py:375-378` 只在 openwebui 引擎查找未完成 PPT，`:477-499` 的 resume_draft 也在旧引擎继续/重试链路内。

Harness 可以恢复原生会话历史，但没有把前一个 Run 的 `presentation_drafts/.../{old_run}.json` 复制/绑定给新 Run。中断后发“继续”并直接调用 write/finalize，当前 Run 会读不到草稿；模型可能重新建计划和重写，不能视为已保存草稿的无损继续。建议让新引擎也显式保存和恢复 draft provenance，并在用户继续时复用原草稿；如本来只支持单 Run 草稿，应修正文档和 UI 表述。

## 文档与代码之间的差异

- README 的“默认引擎为 Harness”符合 `config.py:199` 的源码默认；“当前运行一定为 Harness”必须另验部署配置。`chat/router.py`、`chat_jobs.py` 的部分说明仍只写 Open WebUI，已落后于 engine 分发实现。
- README 关于长任务按阶段逐项交付应区分目标行为和实现保证：Harness 的阶段事件与提示词存在，但旧 Open WebUI 的 DeliveryTracker/TaskCompletionGuard 没有等价地全部迁入 Harness。不能把普通阶段 SSE 或模型遵循提示词描述成持久化的多阶段工作流编排器。
- README:46 写“产物落地校验 delivery.audit，正文关键数字须可溯源”。实际 `runtime/grounding_check.py:74-106` 为数字匹配启发式审查，`harness/runtime.py:539-541` 发送告警；它不是完备的事实证明，也不是缺数字即阻止完成的强制验收。
- 模型 runtime_options 是后端能力，管理 UI 尚未覆盖；网页搜索和深度研究的菜单描述超出了目前默认执行链路；PPT 草稿自动继续目前有引擎差异。
- 大屏图片导出是摘要绘图，PPT 上传模板适配是原布局/主题再排版；不能写成完整页面截图或对参考 PPT 逐页原样复刻。
- `artifact/router.py:60` 的注释声称文本/JSON 预览，但实现只允许图片 MIME；应以实际实现为准。

## 已有测试结果的功能解释

主审查进程运行了隔离测试，本子任务只读取和归类输出，没有再次执行现有测试脚本。

- `offline-python-results.txt`：491 passed / 4 failed。chat_liveness 用例仍预期 3 次 execute，实际新增 dangling ModelCall 查询产生第 4 次；mock run 还缺少 model_profile。这属于测试夹具漂移，与 F2 的真实提交顺序问题是两个独立结论。其余 NL schema 截断字段和 request_timeout 默认值失败由相应模块报告解释。
- `isolated-backend-results.txt`：达到 maxfail=12 停止，138 passed / 12 failed。12 个失败均有前置 `OpenWebUIClient.__init__` 读取 `storage/openwebui/bridge.key` 的 FileNotFoundError：alert_chat_evidence 4 个、alert_history_grounding 1 个、alert_receipt_scope 4 个、analytics_task_control 2 个、business_ai 1 个。business_ai 最后的 IndexError 是前置失败后没有工具结果导致的下游断言。
- 这些旧 Open WebUI 路径用例没有建立当前所需桥接 client fixture，未走到目标业务能力；不能据此宣称 Harness 线上对话故障，也不能宣称业务断言已验收。应固定每组测试的目标引擎，分别 mock 原生/旧引擎边界；真实端到端模型验收另列，不应通过补真实密钥或启动真实依赖来掩盖隔离测试前置错误。

本报告确认功能连接关系、若干确定性代码缺口和两项纯离线复现的功能故障。仍需验收的项目包括原生服务实际启动/版本、模型协议真实响应、业务数据库与知识检索、大屏交互、Office 文件视觉质量和跨进程恢复；本次没有用静态阅读代替这些验收。