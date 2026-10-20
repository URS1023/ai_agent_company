# Open WebUI 功能、界面与复用核查

作者：agent-1020-webui；任务：T-agent-1020-webui-01；日期：2026-10-20。对应需求 R-agent-1020-root-16。项目：`E:/code/Yun_tian/new_agent/open-webui-main`。

## 核查范围与证据口径

- 本地 `package.json:3` 版本为 **0.12.3**。本次针对当前目录源码，不用网上其他版本的能力代替本地实现。
- 全量枚举 **48 个 `+page.svelte` 路由文件**，同时提取对应组件与 API 导入；额外枚举聊天、工作区、管理、自动任务、日历、笔记、频道、试验场、首页、认证、布局等功能目录中的 **379 个 Svelte 子界面/组件**，共 **427 行界面索引**。图标和通用原子控件不计为业务界面。
- 后端 `routers/` 有 35 个 Python 文件，静态枚举到 548 处 `@router.get/post/put/patch/delete/websocket` 声明。此数表示声明数量，不是已逐个动态验收的 API 数量。
- 已实际查看仓库 `demo.png`：左侧新会话、搜索、笔记、工作区、频道、文件夹及会话历史；中间为模型选择、空态建议和对话输入。截图不等于新增加模块的全部实际渲染状态。
- 业务代码只读；未启动服务、未连接数据库、未调用模型、未调用外部工具。不把静态实现、README 宣传与运行验收混为一谈。
- 完整清单：`OpenWebUI_界面源码索引_20261020.csv`。每行包含路由/组件、功能组、源码路径、直接组件依赖、直接 API 依赖和核查状态。

## 独立路由覆盖

| 路由组 | 文件数 | 对应界面 |
|---|---:|---|
| `/`、`/c/[id]`、`/s/[id]` | 3 | 新会话、历史会话、分享会话 |
| `/workspace/**` | 17 | 工作区入口；模型/Agent 列表、创建、编辑；知识库列表、创建、详情；提示词列表、创建、详情；工具列表、创建、编辑；技能列表、创建、编辑；函数创建 |
| `/admin/**` | 12 | 管理入口、用户与动态分组页、分析与动态子页、评测与动态子页、设置与动态子页、函数列表/创建/编辑 |
| `/notes/**` | 3 | 笔记列表、新建、详情 |
| `/automations/**` | 2 | 自动任务列表、编辑详情 |
| `/calendar` | 1 | 日历 |
| `/channels/[id]/**` | 2 | 协作频道、加密频道 |
| `/folders/[folderId]` | 1 | 文件夹及关联会话 |
| `/playground/**` | 3 | 试验场入口、图像生成、文本补全 |
| `/home`、`/auth`、`/watch`、`/error` | 4 | 首页、认证、观看入口、错误页 |

动态 `[tab]`、设置弹窗、编辑器面板不是每项各一个路由，不能仅按路由数判断功能是否存在；CSV 另列相应子界面组件。

## 功能核查与源码入口

| 功能 | 已找到的实现与边界 | 关键证据（项目内路径与行号） |
|---|---|---|
| 对话入口与内核 | Svelte 对话页调用真实后端完成处理，统一走模型、过滤器、知识、工具和响应流程；不需要另写一套聊天内核 | `src/routes/(app)/+page.svelte:5`；`src/lib/components/chat/Chat.svelte`；`backend/open_webui/main.py:1148`；`backend/open_webui/utils/middleware.py:2021,6466` |
| 历史、多轮与分支 | `history.messages/currentId` 维护消息关系，含父子节点、分支、重新生成、编辑及保存；另有文件夹、标签、分享、导入导出和归档相关组件/API | `Chat.svelte:467,1246,1258,1274`；`routers/chats.py`；`routers/folders.py`；`components/chat/Messages/ResponseMessage.svelte` |
| 流式回答与消息同步 | 真实流处理、WebSocket 房间、事件类型、排队消息、停止任务和响应流缓存已存在；这能支持断线后同步，但不能由此推定所有任意进程重启都能从执行中间点恢复 | `Chat.svelte:113,120,481,1455,1562`；`backend/open_webui/tasks.py:204,237,267,313`；`main.py:873` |
| Agent 配置 | 名称为 Models 的工作区可配置基础模型、系统提示、知识、工具、Skills、能力、终端、访问授权和版本；可直接作为对话 Agent 资产复用 | `components/workspace/Models/ModelEditor.svelte:157,188,220,228,234,395`；`routers/models.py` |
| 多步工具循环 | 原生 function calling 实际存在循环执行、工具结果回填、继续调用与最大轮次；同时保留 legacy 路径。运行效果依赖所用模型及工具契约 | `utils/middleware.py:2811,2912,5555,5614,6078` |
| 子 Agent | 本地版本具备 `delegate_task`/`timer` 内建工具；委派实现含前台/后台、并发、最大轮次和输出上限，受配置开关及非临时会话等条件控制 | `utils/tools.py:769` 附近；`utils/subagents.py:279,301,307,462`；`components/chat/Messages/SubagentResultRow.svelte`；`components/admin/Settings/Subagents.svelte` |
| 工具审批与人机协作 | 工具 approve/reject/answer、pending/queued 状态及恢复 payload 真实存在；有 AskUserCard。不是只做一个状态动画 | `utils/tool_approval.py:18,119,164,188`；`utils/middleware.py:3142`；`components/chat/AskUserCard.svelte` |
| Python 工具、MCP、OpenAPI | 自定义工具编辑/历史/发布与用户参数；工具服务目录、MCP 连接和发现、OpenAPI 转工具规格及用户/分组权限 | `routers/tools.py:77,145,206,386,1081,1121`；`utils/tools.py:199,345,1751`；`components/workspace/Tools/ToolkitEditor.svelte` |
| Functions 与扩展 | Filters、Actions、Pipes、Pipelines 的管理和处理入口存在；应优先用其扩展契约集成业务接口，减少对核心循环打补丁 | `routers/functions.py`；`routers/pipelines.py`；`components/admin/Functions.svelte`；`utils/middleware.py` |
| Skills | 工作区创建、编辑、导入、文件、版本等组件，后端 Skills 与读取文件工具；可与业务技能分类绑定 | `routers/skills.py`；`utils/skills.py`；`utils/skill_files.py`；`components/workspace/Skills/SkillEditor.svelte` |
| 知识与 RAG | 本地知识库、文件/目录浏览、文本内容、向量检索、混合检索和 rerank；也有外部知识连接及检索测试 API。并非仅附件全文拼接 | `routers/knowledge.py:691,784,879,894`；`routers/retrieval.py:79,80,291`；`utils/tools.py:727` 附近；`components/workspace/Knowledge/KnowledgeBase/Browser.svelte` |
| 记忆 | 独立记忆路由、检索/更新/删除以及个人化管理界面；与领域知识库不同 | `routers/memories.py:229,327,478`；`utils/memory.py`；`components/chat/Settings/Personalization/MemoryModal.svelte` |
| 附件与文件 | 文件 API、上传及模型文件上下文控制；内建工具提供 list/query/grep/view 聊天文件；文件存储提供本地及对象存储适配，具体选用待部署契约确定 | `routers/files.py`；`utils/tools.py:701` 附近；`storage/`；`components/chat/MessageInput.svelte` |
| Artifacts 与代码执行 | 有 HTML 成果侧栏、iframe、下载和文件预览；另有应用集成 API 与代码解释器配置。HTML 成果呈现不等于具备 Test5 报表模板、数据绑定和编辑契约 | `components/chat/Artifacts.svelte:32,82`；`routers/apps.py:46,104,215`；`utils/code_interpreter.py:27,192`；`components/admin/Settings/CodeExecution.svelte` |
| 终端/文件系统 Agent | 已有终端连接、代理、终端工具规格、文件预览事件和终端工作目录；实际执行环境是独立 Open Terminal/兼容服务，不能认为拉取本仓库即具备全部环境。多用户隔离需单独验证部署方案 | `utils/tools.py:1501,1558`；`utils/terminals.py`；`routers/terminals.py`；`components/chat/TerminalDock.svelte`；`README.md:36,96` |
| 自动任务 | 此版本已自带周期提示词自动任务，列表/编辑器/运行记录、RRULE、后台 scheduler、每轮创建真实会话或频道消息，并在执行时重新检查拥有者权限 | `routers/automations.py:209,335,390`；`utils/automations.py:70,353`；`main.py:434`；`components/automations/AutomationEditor.svelte` |
| 日历与提醒 | 月/周/日、事件、共享日历、重复事件及提醒相关组件与后台调度路径；应与工业流程排程在业务上区分归属 | `routers/calendar.py`；`utils/automations.py:573`；`components/calendar/CalendarView.svelte` |
| 笔记与协作 | 笔记编辑、AI 改写和附加上下文；频道、线程、反应、访问控制；存在加密频道专用路由 | `routers/notes.py`；`routers/channels.py`；`src/routes/(app)/channels/[id]/encrypted/+page.svelte`；`components/notes/`、`components/channels/` |
| 多模态 | 语音、视频通话、STT/TTS、图像生成与编辑及多模型对话相关实现；效果依赖所配置服务，不把供应商能力视为已随项目部署完成 | `routers/audio/`；`routers/images.py`；`components/chat/MessageInput/CallOverlay.svelte`；`components/admin/Settings/Audio.svelte`；`components/admin/Settings/Images.svelte` |
| 用户、权限和身份 | 用户/分组/资源访问控制；OAuth、LDAP、可信头、MFA、SCIM 路由；需要与统一门户做身份和业务授权映射，不能只同步前端用户名 | `routers/auths.py`；`routers/users.py`；`routers/groups.py`；`routers/mfa.py`；`routers/scim.py`；`utils/access_control.py` |
| 管理、评测与观测 | 后台用户、用量分析、模型评测/反馈/排行榜、连接/模型/子Agent/文档/检索/代码执行/图像/音频/认证/数据库等设置子页 | `components/chat/SettingsModal.svelte:272` 起；`components/admin/Analytics.svelte`；`components/admin/Evaluations/`；`utils/telemetry/` |

## 界面复用建议

1. **完整保留聊天应用边界。** 对话主页面、消息树、流式处理、工具交互、Agent 配置、历史记录及成果面板一起复用。只拷输入框和消息气泡会丢失用户要求的内核效果。
2. **保留其 SvelteKit 前端与 Python/FastAPI 后端。** `svelte.config.js` 使用静态前端构建，`main.py` 挂载前端/静态资源和 API。先作为独立应用部署，用统一入口、登录和反向代理连接；不建议为了统一视觉立刻把 Svelte 全部改写为 Vue/React。
3. **最小微调集中在主题、侧边导航、业务入口、Agent 模板和结构化工具结果。** 工业对象详情/质检复核/报表设计保留专业页面，工具结果返回稳定的报告/检测/设备资源 ID 与可打开链接。
4. **Dify 用于完整应用开发与固定流程。** 保留 Dify Agent、知识库、工作流及所有有用功能，不削减成仅 HTTP 脚本引擎。Open WebUI 的 Agent 是用户会话内动态调用与委派；Dify 的 Agent 可作为可发布应用/流程节点，经工具适配被聊天侧调用。两者均存在，避免让一个对话请求在两边同时自行规划同一任务。
5. **知识数据设一个权威来源，保留两套原有能力。** 建议企业正式知识库以 Dify 的数据集/索引链路为主要管理入口，通过适配工具向 Open WebUI 提供检索与引用；Open WebUI 原生附件、个人记忆和临时知识仍可用。其外部知识连接证明有可扩展入口，但本轮未核验 Dify 协议能否零适配直连，不能写成开箱即通。
6. **排程明确分工。** Open WebUI 原生自动任务适合个人/会话类定时提示；固定质检和数据处理优先走 Dify 触发与运行记录。统一门户可汇总两侧任务，不能默认删除任一侧原有功能。
7. **报表通过业务工具接入。** Open WebUI 做发起、追问、解释和成果展示；用户称为“NoSQL”的分析能力，需要按 Test5 审计结果区分免写 SQL 的自然语言分析、受控 SQL 查询和实际数据库引擎。协调者本轮已核查 `analytics/schemas.py:20` 的 provider 范围为 mysql/postgres/sqlite/elasticsearch，`analytics/router.py:194` 的 `/query` 支持自然语言或手工 SQL；尚无 MongoDB/InfluxDB 实现证据，不能把当前资产写成已具备这些引擎。其查询能力、报表组件模板、报表生成/编辑可经独立报表服务接入，应交付可编辑的结构化报表资源，不能只返回一段图片或 HTML 伪装为报表系统。此处 Test5 证据采纳协调者审计，完整路径与范围以其专项报告为准。

## 复用时要纳入需求的具体项

- 对话 Agent 模板与模型绑定、用户/组授权、工具与 Skill 范围、最大工具轮次和执行预算。
- 用户/会话/消息/工具调用 ID 与平台业务任务 ID 映射；工具事件、取消、失败与部分成功展示。
- Dify 应用/工作流版本与 Open WebUI 模型/Agent 版本分离保存，执行时记录实际版本。
- 附件和结果的资源归属、下载授权、引用原文、报告 ID 与有效链接；通过 API 转发身份，避免共用管理员身份执行。
- 终端、Jupyter、模型、向量库、对象存储、Redis 等独立依赖清单；仅按首阶段实际需要部署，功能保留不等于一次性启用全部服务。
- Open WebUI 本地 `LICENSE:20-34` 包含品牌保留条款和明确例外条件；`LICENSE_HISTORY` 列出历史许可范围。统一门户与界面微调方案应同时记录对这些原文条款的处理，不能默认整套换标没有限制。本次仅记录源码随附条款，不作法律结论。

## 验收与未验证事项

已完成：全量路由枚举、主要功能目录子界面枚举、关键执行路径静态核查、仓库示意图查看、部署入口与附带许可文件读取、两个独占分析文件输出。

未验证：48 个页面的逐页动态截图、每个设置开关在当前部署的实际可见性、模型多步工具质量、真实 Dify/业务服务连通、断线及进程重启恢复、并发/性能、真实文件和知识检索效果。这些都需要后续明确本地服务和测试数据后运行验收。本轮零数据库连接；没有读取/打印环境凭据，也没有改动现有工作流数据源。

任务状态：本轮静态盘点交付完成；产品集成、动态验收和逐页视觉验收尚未实施。共享进度/索引由协调者串行追加作者回执。
