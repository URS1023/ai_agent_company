# 聊天界面与原生 DSH 对接审查

审查日期：2026-09-26。以 test5 与用户指定的 deepseek-harness-master 当前源码为准；只审查聊天功能、协议和可见结果，不开展安全审计。已读取原生根目录及 packages/client 的 AGENTS.md。没有启动服务、访问数据库、发送模型或外网请求，没有修改业务源码。

## 结论

**不能保留当前聊天页而直接把后端地址切到原生 DSH。** 当前页依赖 test5 的 Conversation/Run/Message.parts、业务资源 ID 与自定义 SSE；原生 Web UI 依赖 session-controller、会话事件日志、投影、临时 Assistant stream 与 Typert Remote。两者的请求、事件、历史、取消及附件语义均不相同。

**当前“网页搜索 / 深度研究”开关在 harness 路径没有执行作用；思考可以在后端启用，但界面和投影都不显示思考；原生技能、子代理、计划审阅也不会因后端替换自动出现在当前界面。** 这几项会直接影响用户感知的“对话效果”。这不等于已证明模型回答质量差；静态审查无法评定真实任务成功率。

原生源码确实提供更完整的对话交互：按会话选择模型/思考等级、思考折叠块、输入排队与中途补充、技能选择、计划预览/审阅、子代理会话、原生文件交付及会话事件恢复。具体部署能否使用仍取决于所选 profile、插件与 provider 配置，不能把“源码存在”直接写成“当前环境已启用”。

## 功能与接口对照

| 用户功能 | test5 当前真实入口与契约 | 原生 DSH 契约 / UI | 对接判断与必要改造 |
|---|---|---|---|
| 会话列表、新建、历史 | [apps/web/app.js:675](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:675) 新建 POST /api/chat/conversations；[apps/web/app.js:704](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:704) 详情包含 messages、last_run、business_module | [packages/api/session-controller/src/index.ts:251](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/index.ts:251) list/search/create；[packages/api/session-controller/src/types.ts:282](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/types.ts:282) create 使用 workspaceId/cwd/sessionId/agentPreset | P1：映射 Conversation→Session；business_module 并非原生字段。历史不能把原生 events 直接当 messages 渲染 |
| 普通发送 | [apps/web/app.js:1704](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1704) 一次请求带 content、model_profile、file_ids、knowledge_base_ids、datasource_ids、plugin/template；[apps/web/app.js:1738](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1738) POST 返回 SSE | [packages/api/session-controller/src/types.ts:330](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/types.ts:330) session.prompt 为 requestId/sessionId/content[]/mode/clientTimeZone；[packages/api/session-controller/src/index.ts:407](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/index.ts:407) 返回受理回执 | P1：受理与订阅拆分；保留 requestId 幂等关联；业务资源另设 provider/context 协议 |
| 生成中继续输入 | [apps/web/app.js:1690](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1690) 生成中调用 sendMessage 只会停止；409 时提示完成后重发 | [packages/api/session-controller/src/client/contract/session.ts:75](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/client/contract/session.ts:75) queue/steer；[packages/client/ui-conversation/src/client/skeleton/InputBar.tsx:153](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-conversation/src/client/skeleton/InputBar.tsx:153) 有中途补充入口 | P2：需要队列、steering 状态及受理回执；不能只替换发送 URL |
| 停止 | [apps/web/app.js:1772](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1772) POST Run cancel，区分 stopping/终态并同步已保存结果 | [packages/api/session-controller/src/types.ts:370](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/types.ts:370) Session cancel 返回 accepted；队列与活动轮次分开 | P1：取消回执不是已停止；按 session control/turn 终态收尾，定义尚未运行队列项的处理 |
| 模型选择 | [apps/web/app.js:657](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:657) /api/chat/models；每轮发送 profile_name；[backend/app/modules/chat/model_selection.py:22](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/chat/model_selection.py:22) 只返回 profile/model/label/default | [packages/api/session-controller/src/types.ts:99](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/types.ts:99) provider/model/reasoningEffort；[packages/api/session-controller/src/index.ts:282](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/index.ts:282) selectModel/modelCatalog；[packages/client/ui-model-selection/src/client/ModelSelect.tsx:94](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-model-selection/src/client/ModelSelect.tsx:94) 原生选择努力等级 | P1：映射配置档到 provider/model；P2：增设 effort 选择及已生效状态 |
| 网页搜索、深度研究 | [apps/web/app.js:2131](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:2131) 展示“查找实时信息和来源 / 执行多步骤分析”；[apps/web/app.js:2167](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:2167) 设置 activeMode；[apps/web/app.js:1717](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1717) 发送 web/deep | 原生 prompt.mode 是 queue/steer；[packages/web/tool-web/src/search.ts:325](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/web/tool-web/src/search.ts:325) 注册 web_search，[packages/web/web-search-deepseek/src/index.ts:128](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/web/web-search-deepseek/src/index.ts:128) provider 独立注册 | P2 当前缺陷：harness 未消费 activeMode；迁移需另设 research/search 设置，明确工具/provider、预算与结果来源。原生没有与此字段直接对应的 web/deep 协议 |
| 文本流 | [apps/web/app.js:1966](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1966) 识别 run.created、message.delta/reset/replace、tool.started/completed、done 等 | [packages/api/session-controller/src/types.ts:549](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/types.ts:549) snapshot/event/assistant-stream；[packages/api/gateway/src/stream-protocol.ts:7](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/gateway/src/stream-protocol.ts:7) WebSocket /api/remote.mux | P1：完全不同协议；需要原生 Client 数据层，或完整桥接，不能把 native event 名直接喂给 handleFrame |
| 思考过程 | [backend/app/modules/harness/projection.py:42](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/projection.py:42) 只投影 text-delta；[apps/web/app.js:1007](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1007) 历史 parts 没有 reasoning 分支 | [packages/client/ui-chat/src/client/conversation-nodes/assistant.ts:111](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-chat/src/client/conversation-nodes/assistant.ts:111) reasoning-delta；[packages/client/ui-chat/src/client/chat/ReasoningRow.tsx:35](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-chat/src/client/chat/ReasoningRow.tsx:35) 可展开思考块 | P2：实时和历史两处都会丢展示；增加独立 reasoning 数据块和折叠组件，不能混进最终答案 |
| 工具过程 | [apps/web/app.js:2044](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:2044) 仅名称、状态及最多 110 字摘要；[apps/web/app.js:1011](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1011) 历史成功结果只显示识别到的业务结果 / 工件 | [packages/client/ui-chat/src/client/conversation-nodes/tool.ts:39](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-chat/src/client/conversation-nodes/tool.ts:39) 原生 tool/call/result/子调用树；各插件 toolview | P2：普通名称 chip 可保留，但参数/输出、嵌套调用、原生扩展工具需要对应数据结构/视图；现有工具并非完全不可见 |
| 技能选择与执行展示 | 当前是已发布企业插件选择，[apps/web/app.js:2174](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:2174) plugin_version_id；其 instructions/skill_refs 在 [backend/app/modules/harness/context.py:30](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/context.py:30) 注入 | [packages/client/ui-skill/src/client/index.ts:126](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-skill/src/client/index.ts:126) 原生 skills.list；[packages/client/ui-skill/src/client/index.ts:160](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-skill/src/client/index.ts:160) / 补全；[packages/client/ui-skill/src/client/index.ts:81](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-skill/src/client/index.ts:81) skill 专用工具行 | P2：企业插件与原生技能概念需分开；手打 /name 只是文本，能否执行取决于 Host 是否装了对应处理；现页没有原生 catalog/预览 |
| 计划模式及审阅 | 当前没有 /plan 控件、plan projection、待审阅 RPC 生命周期；business_question 只是业务提问 | [packages/client/ui-plan/src/client/index.ts:116](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-plan/src/client/index.ts:116) 计划控件；[packages/client/ui-plan/src/client/plan.ts:33](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-plan/src/client/plan.ts:33) exit_plan_mode 工具参数形成计划文档；[packages/client/ui-user-questions/src/client/PlanReviewPanel.tsx:43](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-user-questions/src/client/PlanReviewPanel.tsx:43) 回答审阅请求 | P1 若启用原生等待确认工具：必须接请求→展示→回答/取消闭环，否则模型可能等待一个页面无法回答的问题；不能只显示“工具已调用” |
| 子代理 | 当前消息模型无 parent/child Session 地址，也无子任务会话侧栏 | [packages/api/session-controller/src/types.ts:406](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/types.ts:406) Subagent Address；[packages/client/ui-subagent/src/client/index.ts:56](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-subagent/src/client/index.ts:56) 注册目录及导航；[packages/client/ui-subagent/src/client/sidebar-chat/index.tsx:176](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-subagent/src/client/sidebar-chat/index.tsx:176) 子会话侧栏 | P2：需要子任务状态、父子导航与子会话 follow；将所有事件压成主会话文本会丢失归属和可追踪过程 |
| 文件、图片输入 | [apps/web/app.js:1652](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1652) multipart /api/files；prompt 只发 file_ids；[backend/app/modules/harness/runtime.py:327](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/runtime.py:327) 实际 native prompt 只有 text，附件 ID 通过系统上下文让业务 read_file 按需读 | [packages/api/session-controller/src/types.ts:90](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/types.ts:90) content 支持 text/image/file(receiptId)；[packages/client/file-upload/src/protocol.ts:2](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/file-upload/src/protocol.ts:2) /api/session/uploadFileBinary；[packages/client/file-upload/src/client/runtime.ts:192](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/file-upload/src/client/runtime.ts:192) 按 Session 上传原始字节 | P1：业务 file_id 不能当 receiptId。需选择保留业务读取工具或导入原生附件；图片也不能仅以 ID 替代原生图像块 |
| 结果文件、继续使用 | [apps/web/app.js:958](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:958) parts/tool_result 中收集 artifact；[apps/web/app.js:1062](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1062) /api/artifacts/{id}/use；[apps/web/app.js:1146](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1146) 预览/下载卡片 | [packages/client/ui-deliverables/src/client/index.ts:63](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-deliverables/src/client/index.ts:63) 从原生日志建立交付视图，关联 workspace 文件与侧栏资源 | P1：要桥接 artifact ID、下载、预览、再次附加；原生 filesystem/path 不自动变成企业资料库中的 Artifact |
| 引用与业务结果 | [apps/web/app.js:1015](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1015) businessResultMarkup；[apps/web/app.js:1018](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1018) citation 仅文件名标记；harness [backend/app/modules/harness/runtime.py:463](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/runtime.py:463) 将知识检索结果写引用 | 原生 web_search 有 source URLs，工具结果有自有形状 | P2：保留企业结果卡片并新增搜索引用；不要把 search_knowledge 的企业引用误当网页检索功能 |
| 刷新、断线、历史恢复 | [apps/web/app.js:744](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:744) created/running 时接回 Run；[apps/web/app.js:1864](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1864) GET events?after=数字；[apps/web/app.js:1894](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1894) 终态后重新 GET 保存的 Message.parts | [packages/api/session-controller/src/types.ts:491](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/types.ts:491) live attempt baseline；[packages/api/session-controller/src/types.ts:549](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/types.ts:549) snapshot 带完整 records/projections/assistantStream；[packages/api/session-controller/src/client/sessions/session.ts:645](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/client/sessions/session.ts:645) 重装/追加/结算 | P1：既有恢复是真实现，但绑定 test5 Run；切换原生必须同时恢复事件窗口、attempt、seq、队列及 projections |
| 历史分页、分叉 | 当前详情全量 messages，重试是编辑再发；未使用原生 turn/seq 分叉 | [packages/api/session-controller/src/types.ts:318](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/types.ts:318) fork atSeq；[packages/api/session-controller/src/types.ts:469](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/types.ts:469) page/throughSeq/beforeSeq/turnWindow | P2：如果保留旧页，需单独实现原生日志分页/分叉入口；不能宣称替换自动拥有 |

## 已确认的当前功能缺陷与边界

### F-UI-01 / P2：搜索与研究开关在 harness 下没有消费方

触发：聊天页选择“网页搜索”或“深度研究”再发送。页面弹出“已启用”，并把 web/deep 写入 Run snapshot；但 harness 的工具初始化由统一 approved_tools 决定，system_prompt 的资源字典不包含 mode，session.prompt 的 mode 固定 queue。

证据：[apps/web/app.js:2167](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:2167)、[backend/app/modules/chat/router.py:455](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/chat/router.py:455)、[backend/app/modules/harness/context.py:117](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/context.py:117)、[backend/app/modules/harness/runtime.py:309](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/runtime.py:309)、[backend/app/modules/harness/runtime.py:327](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/runtime.py:327)。

影响：用户以为启用了实时信息检索或专门研究策略，实际运行没有因这个选项改变。模型仍可能基于普通提示或其他工具完成多步骤工作，但这不是开关生效。legacy [backend/app/modules/runtime/agent.py:1188](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/runtime/agent.py:1188) 把标签加进提示词，openwebui [backend/app/modules/openwebui/runtime.py:182](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/openwebui/runtime.py:182) 加入上下文；也不能据此声称存在独立研究流水线。

建议：若保留菜单，为 search/research 定义真实独立设置与运行反馈；至少串起 provider/工具可用性、来源和策略，不能复用原生 queue/steer 的 mode。

### F-UI-02 / P2：原生思考与能力过程在投影/界面丢失

当前 NativeProjection 对 reasoning-delta 返回空列表，assistant/message 只拼接 type=text；原生 tool/call（如 exit_plan_mode）也没有对应投影。前端 handleFrame 对未知 event 静默忽略，历史 renderer 没有 reasoning、plan、subagent 分支。

证据：[backend/app/modules/harness/projection.py:18](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/projection.py:18)、[backend/app/modules/harness/projection.py:42](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/projection.py:42)、[backend/app/modules/harness/projection.py:59](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/projection.py:59)、[apps/web/app.js:1966](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1966)、[apps/web/app.js:1007](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1007)。

这不表示现有企业工具没有展示：runtime 自己处理企业 tool 请求，并发送 tool.started/completed，保留成功结果、引用和工件。问题在于原生新增能力无法原样穿过当前管道。原生工具若真的启用，除了转文字还需要计划审阅 / 子任务等交互。

离线执行生产 NativeProjection，确认 text-delta 正常投影、reasoning-delta 被忽略、持久化 reasoning 被滤除、原生 exit_plan_mode tool/call 无 UI 事件输出；共 4 项断言通过。

### F-UI-03 / P2：deepseek-messages 下 thinking=disabled 会被 effort 默认值覆盖

模型档位选择有传入 harness，不能说“前端选模型无效”。管理配置也有 reasoning_effort→reasoningEffort 映射。但当协议是 deepseek-messages，只有 runtime_options.thinking=disabled、没有 reasoning_effort=off 时，model_configuration 最后会写 thinking=enabled。

证据：[backend/app/modules/harness/context.py:60](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/context.py:60) 与 [backend/app/modules/harness/context.py:78](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/context.py:78)。已抽取生产纯函数离线复现：disabled 单独配置得到 enabled；补 reasoning_effort=off 得到 disabled。未使用真实模型。

聊天页当前没有每轮思考开关或 effort 控件；profile_name=reasoning 只是配置档名，不证明本轮实际启用了何种思考策略。迁移原生 selectModel 需要展示并保存已解析的 model/effort，修正参数优先级，并与“是否在 UI 展示思考”分别验收。

### F-UI-04 / P2：聊天模式的恢复与清空不完整

newChat 在非 preserveComposerContext 分支重置模型/文件/插件/模板，但不清 activeMode；openConversation 也不先清空 activeMode，仅在上一轮 failed 时恢复 snapshot.mode。模式徽标只在菜单选择/手工关闭时更新。

证据：[apps/web/app.js:687](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:687)、[apps/web/app.js:716](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:716)、[apps/web/app.js:727](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:727)、[apps/web/app.js:740](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:740)、[apps/web/app.js:2167](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:2167)。

影响：同页切换会话可能把上个会话的 mode 带到另一会话；页面刷新后成功会话也不恢复原 mode。当前 harness 中 mode 尚未生效，主要造成 UI/请求语义不一致；若迁移时正式启用 search/research，应同时修复此状态生命周期。

### F-UI-05 / P2：附件图片经过独立识图转成文字，没有送入主聊天模型的原生图像块

这是一条已实现但有信息边界的图片处理链路，不能说系统完全不支持图片，也不能因为有 input_modalities 配置就说主聊天已支持多模态。

当前链路：

1. 前端“添加图片 / 文件”都经 [apps/web/app.js:1652](E:/code/Yun_tian/the_company_agent/test5/apps/web/app.js:1652) 上传 /api/files；发送仅携带 file_ids。[backend/app/modules/harness/runtime.py:327](E:/code/Yun_tian/the_company_agent/test5/backend/app/modules/harness/runtime.py:327) 的原生 session.prompt.content 始终只有 text，没有 image 或 file receipt。
2. 主模型被系统提示引导按需调用 read_file。[backend/app/providers/tool/builtin_provider.py:1093](E:/code/Yun_tian/the_company_agent/test5/backend/app/providers/tool/builtin_provider.py:1093) 默认 include_images=true，默认至多 4 张、参数上限 8 张；提取图片后走 [backend/app/providers/tool/builtin_provider.py:1200](E:/code/Yun_tian/the_company_agent/test5/backend/app/providers/tool/builtin_provider.py:1200) 的 analyze_image。
3. 该辅助视觉调用是真图像请求：[backend/app/providers/model.py:697](E:/code/Yun_tian/the_company_agent/test5/backend/app/providers/model.py:697) 构造 base64 data URL，[backend/app/providers/model.py:721](E:/code/Yun_tian/the_company_agent/test5/backend/app/providers/model.py:721) 发 image_url；但使用独立 vision 配置档，提示词固定为企业 OCR、图表、流程图、截图信息提取，[backend/app/providers/model.py:702](E:/code/Yun_tian/the_company_agent/test5/backend/app/providers/model.py:702) 只追加文件位置上下文，未把当前具体问题传入。
4. [backend/app/providers/tool/builtin_provider.py:1219](E:/code/Yun_tian/the_company_agent/test5/backend/app/providers/tool/builtin_provider.py:1219) 将视觉结果变成“图片识别”纯文本；[backend/app/providers/tool/builtin_provider.py:1126](E:/code/Yun_tian/the_company_agent/test5/backend/app/providers/tool/builtin_provider.py:1126) read_file 返回 text/计数/warnings。[services/deepseek_harness/src/company-preset.ts:27](E:/code/Yun_tian/the_company_agent/test5/services/deepseek_harness/src/company-preset.ts:27) 的 output.render 再将所有企业工具输出 JSON.stringify 为一个 type:text 块。即使工具 JSON 内将来放了 base64 或 image 字段，这段 render 也不会把它升级为 DSH ImageBlock。

影响：主聊天模型只能依据辅助 OCR 的文字转述回答图片问题，无法结合用户问题直接检查原图细节；识图遗漏、默认抽样之外的页面、与固定 OCR 目标不同的问题可能影响效果。read_file 是否调用和辅助 vision 是否可用也影响闭环。此结论来自源码，没有发起识图请求。

原生路径不同：[packages/client/ui-conversation/src/client/service.ts:259](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-conversation/src/client/service.ts:259) 区分 image 与普通 file receipt；[packages/client/ui-conversation/src/client/service.ts:576](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/client/ui-conversation/src/client/service.ts:576) 把图像编码为 mediaType/data/name；[packages/api/session-controller/src/commands.ts:347](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/api/session-controller/src/commands.ts:347) 检查当前模型图像能力并调用 admitPromptContent；[packages/attachment/attachment/src/index.ts:122](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/attachment/attachment/src/index.ts:122) 保存图像并返回 type:image/attachment。例如 [packages/llm/llm-pi-ai/src/context.ts:77](E:/code/Yun_tian/the_company_agent/deepseek-harness-master/packages/llm/llm-pi-ai/src/context.ts:77) 在模型请求中解析图片字节并构造独立 image 内容块。原生图像路径仍须有对应 provider 的真实多模态能力，并非每个模型都支持。

迁移建议：需要主聊天直接看图时，上传/历史/请求应保存原生 ImageBlock；企业 read_file 的 OCR 文本可继续用于检索或普通文件抽取。工具生成图片若也需交给主模型看，应使用原生附件存储及图像返回内容块；仅保留 company tool 的 JSON 文本 render 无法实现。普通文件 raw-byte receipt 与图像 base64 prompt 是两条不同入口，不应混用。

## 替换前必须确定的前端边界

1. **选定会话事实来源。** 若采用原生 Session 日志，则列表、聊天、恢复、历史分页、模型选择、工具过程都围绕同一个 Session ID 和事件序列；旧 Conversation/Run 要有明确映射和旧记录读取方式。不能一边直接接原生 stream，一边还从旧 Message.parts 收尾，否则会丢结果或重复显示。
2. **保留企业选择器的业务语义。** 现有知识库、数据源、模板、插件、业务模块并非原生 prompt 字段。保留 test5 企业服务，通过 DSH provider/tool/context 接入，或开发原生 UI 插件。直接删除后端会使这些现有菜单无法完成闭环。
3. **采用完整的原生事件和投影层。** 原生控制流、持久事件、临时思考/正文流、重试 attempt、工具树不能只映射为 message.delta。原生 packages/api/session-controller/src/client 已实现这一层，使用它比复制局部事件判断更完整；当前聊天外壳可逐步复用业务卡片。
4. **接通交互式工具后再开放能力。** plan review、用户提问、子代理会话、queue/steer 各自有回执和生命周期。先有 UI 可回答/取消/查看，才开放会等待用户交互的原生工具。
5. **统一文件交付。** 原生 receipt/attachment/workspace 文件与 test5 File/Artifact 的映射必须覆盖上传、分析、生成、预览、下载、再次使用六个动作。
6. **思考与研究分开建模。** 模型 reasoningEffort、是否展开思考、联网搜索、研究策略、输入 queue/steer 是不同维度，不能继续塞进一个 mode 或以 profile 名称代替。

从前端实现完整度看，可选择“以原生 Web UI / session-controller Client 为聊天内核，再将企业选择器和业务卡片做插件”或“保留 test5 外壳，完整移植原生数据层与必要交互”。仅更换运行后端、保留当前扁平文本投影，能获得的原生能力可见性有限；后端能力增加与聊天体验改善不是同一件事。

## 最小验收范围

| 场景 | 必须观察的功能结果 |
|---|---|
| 选模型及 effort 后发送、刷新 | 同一 Session 保留已生效选择；实际请求字段与 UI 一致 |
| 点网页搜索 / 研究 | 有明确执行策略和真实来源；未配置 provider 时入口准确反馈，不仅 toast |
| 思考→工具→正文→工件 | 思考独立折叠；工具关联正确；正文不夹带思考；工件可预览/下载/复用 |
| 运行中输入新要求 | 明确排队或 steer，受理一次；停止与发送不会混淆 |
| 计划等待确认 | 展示计划全文，可选答复/取消，返回后运行继续；刷新可恢复待处理状态 |
| 主会话派生子代理 | 可看每个子任务状态/结果、父子关系及子会话历史 |
| 上传普通文件与图片 | 原生 receipt/attachment 或企业读取工具路径有明确边界，历史恢复仍能查看 |
| 断线与刷新 | 受理后不断重复提交；durable seq/attempt/正文/工件/待交互状态恢复一致 |
| 旧企业历史及结果 | 历史问题、引用、业务卡片、附件、报告链接可正常读取 |

## 验证与范围说明

- 已执行 [tests/ui/chat_stream_recovery.test.js:1](E:/code/Yun_tian/the_company_agent/test5/tests/ui/chat_stream_recovery.test.js:1)：**PASS 19 isolated production chat recovery scenarios**。该测试在 Node VM 中替换 fetch/API，验证 test5 的既有恢复，不证明可直接适配原生。
- 已新增并执行 [docs/dsh-conversation-review-20260926/chat-ui-offline-check.py:1](E:/code/Yun_tian/the_company_agent/test5/docs/dsh-conversation-review-20260926/chat-ui-offline-check.py:1)：**6 个纯定义离线断言通过**，覆盖正文/思考/原生工具投影及 thinking 配置覆盖。
- 本次没有执行原生 GUI/e2e、真实模型请求、数据库或服务测试；没有构建覆盖在用资源。原生 UI 结论来自组件、注册、契约和状态恢复代码，不来自文档功能宣称。
- 没有以代码静态分析替代真实回答质量评测。待集成后，应用用户代表性任务对比正确性、工具执行成功率、文件可用性、恢复一致性及人工修正次数，再判断迁移是否改善效果。
