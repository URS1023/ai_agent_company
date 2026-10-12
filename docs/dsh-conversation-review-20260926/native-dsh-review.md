# 原生 DSH 对话核心独立功能审查

日期：2026-09-26。审查对象：`E:/code/Yun_tian/the_company_agent/deepseek-harness-master`，并对照 test5 的 `services/deepseek_harness/profile.mjs`、`launch.mjs`。已阅读 DSH 根及 `packages/AGENTS.md`。以下结论来自代码与实际 profile 配置，不以 README 的功能介绍作为验证。本子任务没有启动服务、调用外部模型、连接数据库或修改运行源码；主审查另进行本地 stub 验证，结果应以其记录为准。

## 核心判断

**应改造当前 DSH 接入层与 profile，不能期待把同一份原生源码换一个目录就解决问题；也不适合用 DSH 替换整个企业后端。** DSH 有扎实、具体的执行循环、会话日志、模型适配、压缩和子代理机制，但它不是企业查询、告警、知识索引和 PPT/报表业务规则的实现者。

两个目录的 package.json 均为 `0.1.7-alpha.2`。主审查的 `snapshot-comparison.json` 进一步核对 UPSTREAM.json 登记的 13,244 个文件：外部源码和现有内嵌副本均全部匹配，0 缺失、0 改动；4 个公司插件生成 JS 也与对应 TypeScript 转换结果匹配。因此这里不是“旧引擎换新引擎”，而是“同一引擎目前如何被组合、限制、映射和展示”。文件一致性的完整执行记录由主审查持有，本报告没有重复哈希。

原生 DSH 可以改善缺少委派、上下文管理、运行追踪、原生会话控制等问题；改善前提是把这些机制接到产品。它不会改变所选模型的知识与推理能力，不会自动纠正业务 SQL、数据口径、模板设计，也不会替 Python 产物流程补出中断后的业务检查点。

## 阅读范围和实际深度

深读包括：`core/agent-loop` 的 turn/step/stream/tool 调度与 create/resume；session crash repair、持久化 JSONL 关键写入/lease/checkpoint；SDK TS/Python 的启动、server、协议、run 收集；SessionController 创建/恢复/提示/取消/日志接口；compaction-basic 的压力预算、摘要、溢出恢复；subagent spawn/fork、continuation 生命周期和结算；系统提示与 AGENTS 上下文；DeepSeek/pi-ai 的配置和关键适配路径；base/sdk/web-standard profile。Windows 检查覆盖 profile 分支、PowerShell provider、session lease 和持久化 Win32 helper。

这是一份对话核心独立深审，不是声称 DSH 全仓每行已读。未逐行审查整个 GUI、浏览器/计算机控制、全部模型 wire 转换、所有 native C++ 实现与每个失败分支。没有用单元测试文件的存在代替通过证据，也没有声称真实模型或 Windows 全功能已验收。

以下 DSH 相对路径均基于上述原生仓库；`test5/...` 表示当前项目。

## 原生执行与会话能力

| 能力 | 代码证据 | 实际行为与限制 |
| --- | --- | --- |
| 多步 Agent 循环 | `packages/core/agent-loop/src/agent.ts:293-523` | 每 turn 可多 step：模型输出 tool-call → 工具结果 → 下一步模型。没有工具调用的回答结束 turn；max-tokens 被明确记录，不能等同交付完成。每一步先重组上下文与配置。 |
| 顺序与并行工具 | `core/agent-loop/src/tool-calls.ts:61-97,131-215`；`constants.ts:6` | parallel 工具走有限滚动池，exclusive 工具构成顺序屏障；结果按模型顺序提交；默认并行上限 10。test5 profile 明设 1，因此当前慢任务的串行性是接入配置，并非 DSH 缺并行执行器。真正可并行仍取决于工具 executionMode，不能仅调大数值就假定所有企业查询应并发。 |
| 转向、追加、暂停当前活动 | `agent.ts:151-178,202-235` | followup 排到 next-turn；steer 排到 next-step；inject 可不唤醒；cancel 把原因传入 AbortSignal，可选择保留 inbox。它不是任意异步函数的抢占恢复，底层工具必须响应信号并收敛。 |
| 暂态文本与最终日志分离 | `agent.ts:404-512`；`assistant-stream.ts` | 每次模型尝试有独立流状态/版本；成功写 assistant/message，失败/重试记 attempt；被取消时可保存 interrupted 消息。客户端必须正确区分暂态流、重试替换和已提交消息，否则仍会重复/闪回/错判完成。 |
| 会话恢复与崩溃尾部修复 | `core/agent-loop/src/index.ts:807-888`；`core/session/src/repair.ts:68-172` | resume 先取得写句柄，再读日志、补齐未结束 tool/step/turn，重建 session。已开始但未记录结果的工具被标为 TOOL_OUTCOME_UNKNOWN；未开始标 TOOL_NOT_STARTED。不会凭空宣称工具成功，也不会自动重放任意外部操作。 |
| 持久化与单写者 | `session/session-persistence-jsonl/src/index.ts:342-415,1175-1225,1320-1372`；`lease.ts:70-104` | JSONL 日志、原子首次落盘、追加并 fsync、物理尾部修复、跨进程写占有机制存在。session/checkpoint-policy/src/index.ts:35,70-80 在请求/顶层工具等边界 flush。当前 company profile 已加载 JSONL/checkpoint，因此这些不是替换才新增的能力。 |
| 多轮与分支 | `api/session-controller/src/agent.ts:407-495`；`commands.ts:239-282`；`core/session/src/fork.ts` | 完整控制器能 cold resume、沿持久化模型选择/预设恢复、fork 会话；不会恢复企业 Run 外另存的 PPT 草稿、业务任务阶段或模型用量事务。 |
| 历史分页、断线续流 | `api/session-controller/src/index.ts:449-463` | page 读历史；follow 从 committed seq 提供 opening snapshot、连续 durable events，以及可选 cursorless assistant-stream。客户端要分别保存日志游标与当前流状态，不能将它们合成一个序号。 |
| 人类提示幂等/队列 | `api/session-controller/src/commands.ts:329-384,430-524,603-613` | 按 requestId 检查已入队/已记录提示；支持 followup/steer、编辑移除队列、取消时保留 inbox。当前自定义 Python 接入有自己的 admission/Run 状态，双层状态必须对齐。 |
| 自动压缩/溢出恢复 | `compaction/compaction-basic/src/index.ts:155-237`；`config.ts:68-107,153-215` | 默认阈值比例 0.8、保留比例 0.16；扣除输出预算和 headroom，按实际 provider/model 计算；压缩前可剪裁工具结果；context-overflow 只有发生真实 surface reduction 才有限重试。摘要也是模型调用，会耗时、耗费 token，也可能失败/丢细节。 |
| 压缩失败行为 | `compaction-basic/src/index.ts:169-185,209-235` | 普通压力压缩失败会告警后继续；溢出重试默认 1 次，无法无限救回错误容量配置。summary 不是事务数据库，关键数值/文件清单应保存在业务结果及工件中。 |
| 系统提示与上下文 | `core/system-prompt/src/index.ts:48-111`；`core/agent-loop/src/agent.ts:256-285,397-410` | 系统 sections、dynamic context、工具 schema 可组合；模型可见的 context 落到日志。`context/agent-instructions/src/index.ts:74-135` 针对文件工具加载 AGENTS 基线/变化。当前 company profile 禁用通用环境提示和技能扫描，并由 Python 传企业 prompt，是产品组合差异。 |
| 模型协议与切换 | `llm/llm-deepseek/src/index.ts:56-121`、`config.ts:218-236,308-327`；`llm/llm-pi-ai/src/adapter.ts:1-20,116-135` | 原生 DeepSeek Messages 与 pi-ai 多提供商适配；一次请求冻结配置，下一步可切换；原生支持图片引用、token/上下文、thinking/effort、空闲流超时。中间公司参数转换仍会影响结果，不能把字段没传到适配器的问题归咎于 DSH 模型能力。 |
| 可观察重试 | `llm/llm-retry/src/index.ts:157-191,194-225` | provider 控制正常有限重试或 always 模式，重试及等待事件写日志；取消中断等待。重试增加等待时间，always 策略可能持续重试；当前公司设置 normal/maxRetries=2，没有必要无条件改为无限。 |
| 子代理新建与继承 | `subagent/subagent-spawn-in-process/src/index.ts:41-65`；`subagent-fork-in-process/src/index.ts:47-89` | spawn 使用新上下文；fork 继承父会话最后 completed turn 的前缀，不包含当前未结束 turn。支持 persona、toolFilter、模型路由和结构化输出。模型若未把当前新事实写进委派提示，fork 不会自动获得本 turn 的最新工具结果。 |
| 可继续子代理 | `subagent/subagent/src/continuation.ts:101-180,204-230,403-455` | 有持久化 descriptor、父子消息、冷恢复和独立中断；不只是一次 Promise.all。需要 sessionPersistence 和相应 provider/工具注册。原生默认 maxDepth=1、maxActiveSubagents=8（index.ts:191-203）；不是无限递归代理。 |
| 子任务完成语义 | `subagent/subagent/src/run-settlement.ts:37-72` | one-shot completed 才按成功结算，max-tokens/refusal/error 失败；continuable children 没有每条消息的独立 Task 结果，不能机械映射为企业工作流每一步必完成。 |
| 长目标推进 | `goal/goal-round-driver/src/index.ts:164-200,260-275` | 已激活且 armed 的持久化 goal 可在 idle 后追加下一轮，达到 maxGoalRounds 会 blocked；普通聊天不会自动变成无限持续目标。company profile 没加载 goal driver；新增需明确产品目标/预算/完成条件。 |

## “原生默认功能”必须按 profile 区分

`boot/app-boot/src/profile.ts:157-170` 中，sdk = base + sdk-app，web = base + web-app，headless = base + headless，sdk-minimal 是另一套独立组合。当前公司 profile 是 `bundles: []` 上的自建插件列表，并未继承 base。

原生 base 配置含 shell/文件/技能、goal、压缩、subagent、工具结果剪裁、图片 offload、重复调用提醒、web_search/web_fetch 等（`bundle/base/cordis.patch.yml:219-303,312-436,454-488`）。Web standard preset 另按 Agent 注册 shell/文件/技能/委派/goal/web 等（`bundle/web-app/presets/standard.patch.yml`）。网页搜索还依赖专门 DeepSeek search provider/凭据与独立 endpoint 配置，并非只要普通聊天能连某个 OpenAI 兼容端点就一定能搜索。

当前 `test5/services/deepseek_harness/profile.mjs:13-47` 实际保留 agent-loop、JSONL、session-controller、企业工具 preset、token meter、checkpoint、自定义 compaction、retry；设置 `maxParallelToolCalls:1`、`modeSelectionEnabled:false`。没有原生 subagent/goal/web/通用 shell/通用文件读写/工具结果 pruner 等完整组合。接入原生这些能力需要注册插件、对接资源、呈现事件、实现用户交互与业务结果归档，不能仅把 models 或 profile 名称改成 standard。

## SDK、server 和“不经 CLI”的精确答案

### 官方 SDK 可以不经交互终端使用，但内部仍启动 dsh CLI profile

- TypeScript `sdk/client/src/launch.ts:132-159` 构建 `node <dsh bin> --profile sdk --patch ...`；`client.ts:211-221` spawn，`api.ts:20-112` 管理一个可复用子进程和多个会话。
- Python `python/sdk/src/deepseek_harness/client.py:23-37,73-98` 同样是 subprocess + stdio JSON-RPC；不是 Python 内直接跑 TS engine，也没有一个现成纯 Python 推理循环。
- TS SDK 自动核对与 dsh 相同版本（launch.ts:55-68），有 built bin 或 tsx source fallback（:86-113）；默认 Node 需求见根 package.json:8-10，为 `^22.19.0 || >=24.0.0`。
- DSH 根 AGENTS 明确支持的应用启动路径是 dsh profiles；在平台后台管理子进程不要求用户看到 CLI 窗口，但“完全移除 CLI launcher”不是当前公开 SDK 的标准路线。

### 官方 stdio SDK 是窄接口，不能直接替代完整聊天服务

`sdk/server/src/server.ts:248-258` 只处理三种请求：initialize、session/prompt、shutdown；`:276-295` 对未知 session 使用 agents.create，不做持久化 resume。没有独立 session/cancel、history、fork、model-selection、队列操作或会话恢复 API。底层 DSH 有这些能力，SDK 没暴露它们。

更关键的是，server.ts:95-128 只转发 session/event、agent/status、session/created、subagent/end；`sdk/protocol/src/types.ts:107-118` 明确只有四种通知。它没有转发 `agent/assistant-stream`，因此官方 SDK 的通知流是日志和状态流，不能直接代替现有聊天逐 token 暂态输出。assistant/message 在一次模型尝试结束才进入 durable log。

`HarnessSession.run`（sdk/client/src/api.ts:174-225）从本次 inbox receipt 收集到整个 Agent idle，返回 finalResponse/events/notifications；它没有替业务校验产物齐全。sdk-app/cordis.patch.yml:23 默认 maxTokensAsSuccess=true，该映射用于 subagent.finished.status（server.ts:65,121），原始 stopReason 仍保留。集成必须读取主 turn/end 和子 stopReason，不能把 idle、非异常返回或子代理 status=ok 当作业务任务完成。

### 完整 server/API 路线

原生 `api/session-controller` 已提供 create/模型选择/rename/fork/prompt/attachment/updateQueue/cancel/page/follow/projections/control（index.ts 的各 @Remote 方法；核心取消 :438、历史 :449、流 :461）。`api/gateway/src/index.ts:222-261` 挂接远程调用/流载体；`stream-server.ts:42-80,130-165` 使用 WebSocket multiplex 并在断开时清理订阅。它是项目自身 Typert Remote/BFF 协议，不是开箱即用的 OpenAI `/v1/chat/completions` 或现有 FastAPI `/api/chat` 协议。

可通过自定义 dsh profile 长驻运行这些 controller，Python 侧作为客户端，或保留简洁自定义 RPC 调用 controller/agent service。这样应用不必“模拟终端输入”，且可以使用成熟会话控制。当前 company 插件已经调用原生服务，继续收敛这层通常比删掉重写整个 Python 后端更直接。

技术上这些 Cordis service/plugin 可以在 Node 同进程 Context 中加载，SDK server 类也可接指定 Readable/Writable（sdk/server/src/index.ts:24-31,50-67）；这只是组合构件，不是现成、不需生命周期/依赖组装的公共 embedded-server 产品。原生仓库还明确要求支持的应用经 dsh profile 启动，因此应采用受支持的 profile/plugin 路线，避免维护第二套 boot/loader/退出流程。

## Windows 与部署边界

源码并非 Linux 专用：base/cordis.patch.yml:234-272 在 win32 选择 pwsh provider/tool；`shell/pwsh-local/src/index.ts:68-77` 可查找 PowerShell 7、PATH 和 Windows PowerShell 5.1；`:40-49` 针对旧 PowerShell 统一 UTF-8。持久化 lease.ts:77-89 有 Windows 路径；win32.ts:56-70 通过 Koffi 调 Win32 API，日志发布使用写穿透移动机制；不是依赖 POSIX flock 的硬移植。

但完整 base profile 比公司精简 profile 依赖更多：Node 版本、pnpm workspace/built lib、native addon/Koffi、PowerShell、模型端点，以及选用的 Office/浏览器等辅助运行库都要就位。`sdk-app/cordis.patch.yml:32-43` 的 Office skill/workspace-dependencies 只有相应 primary runtime 路径存在才启用。不能把源码中有 office skill 描述为当前 Windows 已能高质量生成所有 Office 文件。

会话写句柄被活进程持有时不允许其他进程接管（lease.ts:1-25,70-104），这是单写者设计。服务失联但进程仍活着时，必须由宿主管理实际进程生命周期；更换目录不会解决卡住的旧进程。单进程可保有多会话，但在该进程共用模型连接、文件与工具资源前，需要企业层明确会话映射和并发预算；原生不是现成的多租户企业调度器。

## 对用户三类症状的改善范围

| 用户症状 | 原生机制能改善什么 | 单纯替换无法解决什么 | 当前应落在哪一层 |
| --- | --- | --- | --- |
| 回答、工具、产物质量差 | 增加缺失工具、子代理独立检查、保留更完整上下文、明确模型协议/effort、支持产物检查技能 | 同一模型的推理上限；业务口径/SQL 错误；工具返回字段错误；PPT 模板和布局质量；产物事实校验 | 模型档位与 prompt、业务工具结果契约、产物渲染/验收、company profile |
| 复杂任务做不完、继续断 | 原生多步循环、goal 轮次、子代理、压缩、持久化会话、cancel/resume、带游标日志流 | 已开始但结果未知的外部操作；Python 中按 Run 保存的草稿/结果未迁移；业务多产物完成条件缺失 | 让原生 session 成为对话真源；另保存企业任务与产物检查点，按语义恢复 |
| 慢、卡住、状态乱 | 长驻进程复用、有限工具并行、原生流状态、可观察重试、统一 SessionController、明确 cancel/idle/turn-end | 上游模型慢；定长读取代理缓冲；全局 slot 排队；同步业务工具阻塞；Python Run/ModelCall 未一致提交 | 先修代理/投影/事务与超时预算，再衡量 profile 的并行和进程生命周期 |

主审查已读取当前实际配置：CHAT_ENGINE=harness，上下文 32768 / 输出 4096，全局 2 槽位，总任务超时 0。这里只引用其核验结果；本子任务没有读取秘密配置。DSH DeepSeek adapter 源码默认上下文 1,000,000 / 输出 256,000 / 流空闲 300,000ms（llm-deepseek/src/defaults.ts:3-8），这只是该适配器的默认配置，不能未经所选模型支持能力验证就把当前上限调成这些数字。正确预算应依据实际模型与任务，盲目加大输出还会增加首答/完成等待。

## 建议的替换边界

1. **保留企业 Python 后端和业务工具。** 包括账户/工作区、数据源、知识库、查询结果、工作流、告警、业务审计、模型用量和 DOCX/XLSX/PPT/报表产物。将这些作为稳定工具/API 提供给 DSH，不能期待通用 shell 自动等价实现。
2. **收敛 AI 对话运行控制。** 以原生 session id、durable event seq、turn/end、assistant-stream 为对话状态真源；Python 保存必要业务映射与摘要，避免两边各自猜 running/completed。可沿用 company profile 并调用 SessionController，或重新组合受支持的长驻 service profile；不要改回只能新建 session 的裸 SDK。
3. **按目标补插件。** 先接 subagent、可控并行、工具结果裁剪和明确任务目标/交付检查；web 搜索单独接 provider。让子代理有明确的只读分析或检查职责，并把企业工具资源与产物回传接完整。插件更多不自动意味着任务成功率更高。
4. **单独修当前可证实缺陷。** 代理 SSE 读取、Run/ModelCall 提交、thinking 参数传递、PPT draft 跨 Run 继续、前端 mode 空入口、企业产物完整性。换 profile 不会自动消除这些代码路径。
5. **用三类相同任务对照验收。** 固定模型、数据和工具，比较现有 company profile 与补齐能力后的 profile 的首事件/首文本/总耗时、重复工具次数、产物完整性、重启/中断继续和最终数字来源。先本地 stub 验协议，再由用户授权的环境验真实业务与模型；不要把一般聊天 smoke 通过写成复杂任务质量通过。

不存在仅靠“直接用原生源码替换后端”即可一并修复三类症状的代码依据。原生 DSH 已在项目中运行；合理变化是减少重复会话控制、接回需要的原生能力，并保留和修正企业任务与产物的业务实现。