# AI 对话链路专项审查报告

> 版本：v1.1（四路专项审计全部合并；十、详细技术设计已按用户要求先行产出；用户已批准的低风险注释/文档清理已完成并通过 `compile_check.py`）。待用户对批次 A/B/C 详细设计二次确认后实施。
> 审查范围：`POST /api/chat/conversations/{id}/send` 全链路：入口调度 → 引擎分派（harness 默认 / openwebui 回退）→ 原生 Agent 循环 → 工具网关 → 产物落地 → SSE 事件投影 → 前端渲染/恢复/取消。
> 痛点口径：①回答质量/准确性 ②长任务执行失败率 ③产物可靠性 ④流式/恢复/取消 ⑤治理边界。

## 执行摘要（结论先行）

AI 对话"达不到想要的结果"不是单点 bug，而是**默认引擎（DSH/harness）的可靠性协议、产物数据链路与平台既有质量承诺之间存在系统性断层**，叠加"机制存在但未生效/不可见"的治理缺口。按影响排序（含默认引擎与回退引擎两路径）：

1. **产物数字无"取数回执绑定"，编造/手抄数值可全链通过（T-P0-1）**：唯一回执绑定工具是 `render_chart_image`，而 PPT/Word/Excel 渲染工具（write_presentation_slides/render_presentation/render_spreadsheet）无 query_id 绑定，模型可手写数字；`grounding_check` 是 warn 且前端不可见（R1），delivery 只核文件非空。→ "交付假报表"的直通通道。
2. **上下文爆炸与压缩打地鼠（H-P0-1 + R7）**：工具回执全量进模型上下文（`company-preset.js:28-30`），行级查询可塞爆 32768 窗口；压缩摘要仅 4096 token（`profile.mjs:45`），长任务"压缩→再爆→整轮 failed"或事实被摘要化。→ "长任务失败率高"与"越到后面越不准"的共同第一根因。
3. **会话级可靠性协议缺陷（H-P0-2/3/4）**：单会话单活动任务+每 Run 新进程抢锁 → 追问/重试被拒；run.settled 依赖内存 lastTurnEnd 与 cancel 竞态 → **已完成被判失败并冻结恢复**；取消 30s 硬超时把取消打成失败。
4. **质量承诺未执行（R10 + R2）**：证据闸门全家（clarification/query_answer/report_grounding/business_grounding）挂在休眠死代码 agent.py 下零调用；harness 完成判定不核对"用户要的交付物是否真实产出"。
5. **回退引擎的特殊缺陷（O-P0-1/2）**：openwebui 路径 10 秒"静默判死"（模型推理期间队列空→直接中断并反向取消原生任务）与"残次消息即整轮失败不补交"；前端 finishChatStream 无限重试（O-P1-3）。历史会话/回退引擎下长任务失败率极高。
6. **网关与治理逃逸面（T-P0-2 + H-P1-4）**：add_tool 零权限发布 HTTP 工具且无 SSRF 防护；DB 冻结过激（任意 timeout 整轮永久 halt）；内置渲染工具无单工具超时（T-P1-1）。

**结论：需要的是"可靠性协议重写 + 三条质量闸门接回执行路径 + 产物数据绑定"——而不是重写整个对话系统。** 工具面（34 个内置工具）经审计确认**齐备**（H-P1-5），主要缺口在"查询结果→PPT/Word 一步装配"（T-P1-5）。

## 严重度定义
- P0 致命：直接导致对话结果错误、任务丢失、数据疑义的链路缺陷，必须修复。
- P1 高：显著拉低任务成功率/结果可信度/用户体验的主要根因。
- P2 中：特定场景下的缺陷或能力缺口。
- P3 低：一致性/健壮性问题。

---

## 一、主审查人已核实的根因（第一手，附文件:行证据）

### R1【P1】落地校验（delivery.audit）前端完全不展示 —— "编造数字"无任何可见拦截
- 证据：`backend/app/modules/runtime/grounding_check.py:134-144` 每次收尾调用 `run_delivery_audit`；结果只写入 `run.context_snapshot.delivery_audit`（仅 `/api/chat/runs/{id}/trace` 可见）并发出 `delivery.audit` SSE 事件（`harness/runtime.py:539-542`）。
- 前端：`apps/web/app.js` 全文仅处理 `task.deliverable`（grep 证据：`ev === "task.deliverable"`），**不处理 `delivery.audit`** → 用户永远看不到"正文有 N 处数字断言无法溯源"。
- 影响：①回答质量/治理边界。机制存在但不可见，等于没有。`check_grounding`（grounding_check.py:70-105）只是给出 warn 状态与最多 8 条示例，不阻断、不修正。
- 修复方向：前端渲染 delivery.audit（警告条/折叠卡片）；后端可升级为"文档类交付缺失时给出硬性失败前提示"。

### R2【P1】harness（默认引擎）路径没有"交付完整性"硬闸门
- 证据：`harness/runtime.py:521-536` run.settled 的 completed 判定仅要求 `projection.committed_text.strip()` 非空；无"用户要求的 PPT/大屏/Excel 是否真实产出 Artifact"核对。
- 对照：openwebui 路径有 `openwebui/requirements.py`（requested_outputs 中文提取必交付清单）+ `openwebui/task_completion.py`（TaskCompletionGuard 原生任务清单核对）。
- 影响：①回答质量 ③产物可靠性。长任务"只做一半就收尾"在 harness 下只能依赖系统提示词（`harness/context.py:101-115` 长任务协议），无机器约束；模型可提交"已生成"文案而无真实工件（grounding_check 的"口头交付"检测同样只 warn）。
- 修复方向：为 harness 引入轻量 delivered-requirements 核对：从用户请求提取必交付物关键词 → 对照本轮 Artifact 类型清单 → 缺失时触发一次原生补交（re-prompt）或明确失败提示。

### R3【P1】"继续/重试/PPT 草稿恢复"语义只对 openwebui 分支生效
- 证据：`chat/router.py:374-405`：`presentation_source`/`continuation_source`/`is_retry` 全部 `chat_engine == "openwebui"` 才计算；`resume_snapshot`/`resume_draft` 分支（router.py:473-503）同样只在 openwebui。
- harness 路径：`harness/runtime.py:312` `_migration_background` 只做一次性历史迁移；同会话复用靠 `resumeExpected`（native_binding + 原生 JSONL 会话恢复），"继续生成 PPT"时无草稿续写机制（presentation_drafts 以旧 run_id 落盘，write_presentation_slides 会在新 run 下重建）。
- 影响：②长任务执行 ③产物可靠性。用户说"继续/重新生成"在默认引擎下行为不可控：要么模型把整串历史当新请求，要么重复执行副作用工具。
- 修复方向：统一"控制指令"识别与快照恢复逻辑到引擎无关层；harness 下把 draft 恢复改为按 session_identity 判断而不是 run_id。

### R4【P2】原生事件投影对"片段缺失"零容忍，单帧丢失即整轮失败
- 证据：`harness/projection.py:38-40`：`index != self.next_index` 直接 `raise HarnessUnavailable("实时事件缺失，不能继续拼接不完整正文")`。
- 影响：④流式/恢复。任何一帧 `assistant-stream chunk` 丢失（网络/管道抖动）→ 整轮 run 判失败，已产出正文保留但"任务失败"，体验差。
- 修复方向：缺失帧用"已提交正文"兜底续拼（类似 abandoned 分支 projection.py:46-48），或允许跳过并标记不完整，不直接失败。

### R5【P2】HARNESS_MAX_PROCESSES=2 全局槽位，第三个并发会话无限等待无提示
- 证据：`harness/client.py:95-108` start() 持会话锁后循环抢 2 个全局槽位（`slots/{index}.lock`），`control.cancelled.wait(.2)` 空转等待；无排队时间上限。
- 影响：②长任务执行 ④流式体验。>2 个用户同时对话时，后续任务一直停在"正在准备对话运行环境"，用户看来就是卡死（只能取消）。
- 修复方向：等待超时/明确排队队列 + 前端进度提示"排队第 N 位"；或按会话串行释放后唤醒。

### R6【P2】compaction（上下文压缩）可能丢失事实细节，长任务后续阶段依赖压缩检查点
- 证据：`services/deepseek_harness/lib/company-compaction.js:6-14` 摘要指令要求"准确保留日期范围、币种、单位、统计口径、关键数值、资源标识"；`profile.mjs:44-47` maxTokens=min(model.maxTokens,4096)。
- 影响：①回答质量 ②长任务。多阶段长任务中被压缩掉的中间计算细节无法恢复，模型后段可能"凭检查点推断"，而 `BUSINESS_FACT_POLICY`/grounding 均无法拦截"从推断到编造"的过渡。
- 修复方向：压缩策略增强——关键数值/单位/口径字段强制保留（结构化附加到检查点）；压缩后提供"证据缺口"提示；可对"关键数字"做压缩前后核对。

### R7【P2】工具回执整体直送模型，无大小裁剪
- 证据：`harness/runtime.py:467` `client.respond(packet["id"], payload)` 把完整 ToolResult 返回原生；`builtin_provider.py:698` query 结果序列化上限 100KB 仅作用于 query_receipts 缓存，实际回执仍可更大（max_rows 1000）。
- 影响：①回答质量 ③产物。大结果会把上下文撑向压缩阈值，触发 R6；或模型"读不全"就总结。
- 修复方向：工具回执统一裁剪/摘要策略（列级截断、行数上限、数值摘要），与 AGENT_TOOL_RESULT_MAX_CHARS 对齐。

### R8【P2】重放语义：相同 request_id+相同指纹 → 永久重放原运行（含已完成）
- 证据：`chat/router.py:284-313` `_replay_submission` 对已终态历史运行也直接返回原事件流重放。
- 影响：②长任务 ④流式。客户端重试机制可能把"旧结果"当作新结果展示（设计上是幂等正确，但 UX 上用户会疑惑）；同时重放期间 session.frame 会再次占用 slot 吗？不会（重放只读 run_events），但"同一会话继续发新消息"时旧流与新流混排。
- 修复方向：重放携带已终态标记（`replayed: true`）供前端区分；或仅重放至上次 done 点。

### R9【P3】message sequence 无锁可能重复
- 证据：`runtime/service.py:113-121` next_sequence 查最大+1，注释明示并发时可能重复。
- 影响：④流式（排序抖动，低概率）。修复方向：按 (conversation_id) 行锁/序列生成器。

### R10【P1】`agent.py`（149KB 平台自有 Agent 循环）为休眠死代码，**证据闸门全家未生效**
- 证据（grep 全局验证）：`agent_run` 的定义在 `runtime/agent.py:1072`，全部引用均为 docstring/注释——`runtime/service.py:4`、`runtime/report_grounding.py:3`、`runtime/query_answer.py:3`、`runtime/clarification.py:5`、`runtime/chat_jobs.py:175`，**无任何真实 import/调用**。
- 连带失效（设计承诺但未执行）：
  - `clarification.py`（question_card 澄清问句卡）→ 无调用方，执行中不产生"需要补充信息"的澄清流程；
  - `query_answer.py`（模型不可用时直付真实查询值）→ 无调用方，兜底不存在；
  - `report_grounding.py` / `business_grounding.py`（"断言 vs 工具回执"强闸门）→ 无调用方，报告/业务取数类回答没有机器证据校验；
  - `agent.py` 的 `_prepare_messages_for_model` 三级上下文压缩与 ContextBudgetError → 无调用方。
- 影响：①回答质量 ⑤治理边界。当前 harness 路径的证据约束只剩：系统提示词 + `delivery.audit`（warn、前端不可见）+ `_require_reconciled_tools`（防重放）。"平台会核对答案数字是否有工具回执支撑"的既有设计承诺与实现严重错位。
- 修复方向：明确取舍——(a) 删除残留引用与注释，把仍在需要的闸门（澄清、直付真实值）移植到 harness/openwebui runtime；(b) 或把 delivery.audit 升级为可执行校验（见 R1/R2）。短中期至少修正全部过时注释。

### R11【P3】agent.py 的格式化器/上下文组装与 openwebui/context.py 重复实现
- 证据：`agent.py` 与 `openwebui/context.py` 的 `_attached_file_summaries`/`_kb_descriptions`/`_load_plugin_context` 近乎逐字重复（初始化走读确认）。
- 影响：维护性/一致性风险。修复方向：下沉到公共模块，单点修改。

---

## 二、五类痛点 vs 根因映射（当前）

| 痛点 | 关联根因 |
|---|---|
| ①回答质量/准确性 | R1、R2、R6、R7、R10（证据闸门失效）、H-P0-1（事实压缩漂移）、H-P0-5（正文截断）、T-P0-1（编造数字全链通过）、T-P1-4（max_rows 失真）、T-P1-5（手抄转写失真） |
| ②长任务执行失败率 | R2、R3、R5、R6、H-P0-1/2/3/4/5、O-P0-1（静默判死）、O-P0-2（残次即失败） |
| ③产物可靠性 | R2、R3、R7、H-P0-1/3、O-P1-2（恢复链断档）、T-P0-1、T-P1-2（补位/一次性通道）、T-P1-3（半填充+公式注入）、T-P1-5 |
| ④流式/恢复/取消 | R4、R5、R8、R9、H-P0-2/3/4、H-P1-2/3、O-P0-1、O-P1-1（取消无法中断）、O-P1-3（前端无限重试）、O-P2 第 3/4 条 |
| ⑤治理边界 | R1、R10、H-P1-5（EXTRA_TOOLS 网络过松）、T-P0-2（add_tool 逃逸）、T-P1-3（公式注入） |

## 三、待合并
- [x] harness 引擎质量审计（8ea460d9）→ 已合并到「五、专项审计合并 A」。
- [x] 回退引擎+前端审计（9b6edaea）→ 已合并到「六、专项审计合并 B」。
- [x] 工具与产物审计（2e014b22）→ 已合并到「七、专项审计合并 C」。
- [x] 执行调度审计（80bc425c）→ 已合并到「八、专项审计合并 D」。

---

## 四、修复/重写方案框架（分层，待确认批次后细化）

### 分层 1：入口与调度层（chat/router、chat_jobs、chat_liveness、task_control）
- 统一"控制指令"（继续/重试/重新生成）识别与快照恢复逻辑为**引擎无关**：从 router.py 抽到 `runtime/continuation.py`，harness 与 openwebui 共用语义、各自实现恢复动作（R3）。
- 重放语义区分"重放的旧结果"与"新执行"：响应头/事件带 `replayed=true`，前端可展示"这是重放"（R8）。
- 排队可见性：全局槽位满时进入显式排队（队位置/progress 事件），不无限空转（R5）。
- sequence 生成下沉到会话级锁或独立序列表（R9）。

### 分层 2：引擎适配层（harness/*、openwebui/*、engine.py）
- 新增"交付完整性核对"（engine 无关）：`deliverables_expected(content) -> [交付类型]`（PPT/Excel/Word/大屏/图片），运行结束时对照本 run 的 Artifact 清单；缺失且正文声称已交付 → 触发一次原生"补交付"提示（re-prompt 原文+缺口），仍缺失则终态标记 `deliverable_missing`（R2）。
- 事件投影宽容化：缺失 chunk 用已提交正文兜底续拼，不再整轮失败（R4）。
- 压缩策略增强：检查点结构化保留关键数值/单位/口径清单，压缩后事件带 `compaction.evidence_gaps`（R6）。
- 工具回执统一裁剪（引擎无关，放 gateway 层）：按 AGENT_TOOL_RESULT_MAX_CHARS 策略化截断+摘要标记 truncated（R7）。

### 分层 3：工具面与产物
- 补齐长任务协议闭环缺口（待工具审计确认）：如"Word 中嵌入已生成图片/大屏快照"的显式工具衔接、导出 CSV/描述性统计的衔接。
- 产物渲染失败模式收敛：失败必须携带稳定 error_code+可恢复动作（重试/换工具），禁止半成品冒充交付。

### 分层 4：事件投影与前端（app.js / chat 流）
- 新增 `delivery.audit` 渲染：状态徽标 + 折叠告警列表（"3 处数字无法溯源，点击查看"）（R1）。
- 重放/排队/compaction 事件可视化。
- 恢复首屏：运行时优先展示"任务进行中/排队中"而非空白。

### 分层 5：可观测性
- 每轮对话出量化指标：工具成功率、阶段完成率（对照交付清单）、compaction 次数、audit warn 次数 → 日志 JSONL 已有基础（observability），补齐对话级聚合视图（ops 页面或 trace 增强）。

### 验证方式（先审查后改，确认后执行）
1. `tests/run_isolated_backend.py tests/backend` 全量回归（隔离 SQLite，禁外网）。
2. 新增专项用例：
   - 交付完整性：请求含"PPT+Excel"→ 只出 Excel 时终态必须 deliverable_missing（或触发补交）。
   - delivery.audit：编造数字脚本→事件含 warn+前端渲染契约测试。
   - 继续语义（harness 通道）：真实 harness 进程冒烟（隔离实例，模型桩或本地端点）→ "继续生成"只补缺项不重放副作用。
   - 投影宽容：模拟 chunk 缺失→ run 不失败。
3. `tests/ui` 回归：chat_shell.js / chat_stream_recovery.test.js 保持绿（事件契约不能破坏）。
4. 手工验收场景（5 类痛点各 1 剧本）在隔离实例上跑通。

---

## 五、专项审计合并 A：harness（DSH 默认引擎）链路（agent 8ea460d9）

> 方法：逐行精读 client.py/runtime.py/context.py/policy.py/projection.py/launch.mjs/profile.mjs/protocol.mjs/company-*.js，交叉验证 config.py、gateway、内置工具面（实测 34 个）。

### H-P0-1【P0】工具回执全量进上下文 → 窗口爆炸 → compaction 反复失败 → 整轮 failed
- 证据：`services/deepseek_harness/lib/company-preset.js:28-30`（output.render 把 `JSON.stringify(回执)` 原样注入）；`builtin_provider.py:257`（max_rows≤1000，默认 100）；`config.py:210`（HARNESS_CONTEXT_WINDOW 默认 32768 偏小）；`company-compaction.js:26`（压缩把 input.messages 全量重发）、`:35`（摘要超 maxTokens 即抛错）、`profile.mjs:45`（摘要 maxTokens=min(model.maxTokens,4096)）；`runtime.py:70`（CONTEXT_WINDOW_EXCEEDED→failed）。
- 触发：任一行级查询/大 JSON 工件单次塞爆窗口。
- 影响：①回答质量 ②长任务。压缩失败→整轮 failed；压缩成功也把数值/口径摘要化，事实一致性被破坏；"压缩→再爆"打地鼠。
- 修复：工具回执在 Node 出口按 token 预算裁剪（列名+前 N 行+统计+query_id 票据）；大结果只留票据；压缩用独立预算/专用模型；按供应商容量配置 contextWindow。

### H-P0-2【P0】单会话单活动任务 + 每 Run 新进程抢文件锁 → 续问/重试被拒或堵死
- 证据：`company-runtime.js:173-176`（"同一会话只允许一个活动任务"）；`client.py:89-108`（每 Run 新建 HarnessClient 抢 session.lock+slot）；`runtime.py:123-125`（身份=会话哈希）。
- 影响：②长任务 ④流式。首轮未收尾时追问答失败/卡死（与 R5 槽位限制叠加）。
- 修复：会话内真队列（串行入队/合并）；进程按会话复用。

### H-P0-3【P0】run.settled 判据依赖内存 lastTurnEnd，与 cancel 竞态 → 已完成被判 failed 并冻结恢复
- 证据：`company-runtime.js:122-125`（无 terminal→failed/MISSING_TERMINAL）、`:126`（deliveredThrough(seq-1)）、`:200-209`（cancel 与 settle 并发 whenIdle+flush）；`runtime.py:207-216`（_require_reconciled_tools 冻结后续）；`runtime.py:530-535`（终态早于归档/无已提交正文→失败）。
- 影响：②长任务 ④取消。**已完成结果被丢弃判失败**，且下一次恢复被 tool_result_unconfirmed 封死——丢结果的直接来源。
- 修复：以持久化 turn/end + sessions.flush 双确认判 completed；settle 超时兜底并去重；取消与结算合并不互覆。

### H-P0-4【P0】cancel 竞态：Python 只发不等，30s 硬超时把取消打成失败
- 证据：`client.py:228-238`（cancel 只 submit 不等待）；`runtime.py:333-334`（HARNESS_CANCEL_TIMEOUT_SECONDS=30 超时即 raise）；`runtime.py:434`（业务工具同线程同步执行，慢 SQL 期间不消费 tool.cancel）；`runtime.py:570-572`（收尾把 running 工具标 tool_result_unconfirmed）。
- 影响：②长任务 ④取消。取消变 failed+未确认→下一 Run 被冻结。
- 修复：取消后等 settle 而非硬超时；工具执行加可中断点/子进程隔离。

### H-P0-5【P0】4096 token 输出上限截断长任务正文且计失败
- 证据：`config.py:211-214`（默认 4096）；`context.py:55` 透传；`runtime.py:72`（max-tokens→model_response_incomplete）；而 system_prompt 又要求输出完整交付清单（context.py:111）。
- 影响：①回答质量 ②长任务。多阶段交付必然触顶：阶段未交付+正文截断+"不完整"失败。
- 修复：提高默认 maxTokens 或按阶段结算正文、清单单独成轮。

### H-P1-1 model.authorize 每流往返 + 用量归因错账
- 证据：`runtime.py:342-371`（每次 LLM 流 Python 往返+2 次 SQLite 提交）；`runtime.py:487-515`（按第一个 pending 归因，compaction/重试下失败尝试记成功、真实成功挂账）。
- 修复：会话级预检+配额快照，去掉每流往返；修正归因。

### H-P1-2 16MB 管道上限：大回执 direct transport.failed → 整轮失败+未确认
- 证据：`client.py:157-161` + `protocol.mjs:16`（32MiB）。
- 修复：与 H-P0-1 同源——回执裁剪/票据化后天然规避。

### H-P1-3 流式 chunk 缺失整轮失败（与主审 R4 一致）
- 证据：`projection.py:38-39`；另 `projection.py:46-48` abandoned 丢弃已积累 delta、`projection.py:28-31` 每次 start 清空可见文本闪烁。
- 修复：同 R4。

### H-P1-4 DB 冻结过激：任意 timeout/连接错误整轮永久 halt
- 证据：`runtime.py:147-159` + `:450-452`（harness_tools_halted）+ `:524-525`（halted→整轮 failed）。
- 修复：区分可恢复（重试一次小查询）与不可恢复；按工具维度冻结而非整轮。

### H-P1-5 工具面结论：白名单 28/34 覆盖，长任务闭环工具齐备，**不存在"缺工具导致编造"的明显缺口**
- 过松点：`runtime.py:431` HARNESS_EXTRA_TOOLS 的 http/mcp 开网络。delivery.audit 只告警不阻断（与 R1 一致）。

### H-P2/P3
- `runtime.py:196-204` 迁移背景 50KB 截断可能陈旧；`runtime.py:394-412` callId 冲突即整轮 halted；`profile.mjs:27` session-query openAt:never 形同摆设。

### H-重写三件事（harness 链路）
1. 工具→模型回执通道：token 预算裁剪/票据化（根治上下文爆炸与压缩打地鼠）。
2. 会话生命周期与 settle/cancel 协议：进程复用+队列、以持久化终态判 completed、取消与结算合并。
3. model.authorize 改造：会话级预检+配额，去掉每流往返与 compaction/retry 错账。

---

## 六、专项审计合并 B：openwebui 回退引擎 + 前端流式渲染（agent 9b6edaea）

> 方法：精读 openwebui/runtime.py（39KB）、client.py、delivery.py、resume.py、task_completion.py、tool_failures.py、requirements.py、chat_jobs.py 与 app.js 流式段。注：此路径为回退引擎（CHAT_ENGINE=openwebui 或历史会话），但"继续/恢复/取消"协议与前端共享，缺陷会传导到任意引擎。

### O-P0-1【P0】10 秒"静默判死"——openwebui 长任务失败率高企的头号根因
- 证据：`openwebui/runtime.py:265-282` — 队列空>10s → 查 `/api/tasks/chat/{id}`；task_ids 为空且 chat 未 done → `raise BackendUnavailable("原生任务已经停止…")`；`client.py:85` 仅 tool/model 回调入队，生成期不向队列发事件 → 首个 10s 空转即触发；`runtime.py:576` except 后 finally `client.stop()` 反向取消仍运行的原生任务。
- 触发：无 create_tasks 清单的任一轮（纯分析/长输出）、工具循环两次回调间隙>10s、Open WebUI 断连>10s。
- 影响：②长任务 ④体验。凡静默>10s 的轮次必失败，答案丢失。
- 修复：静默不判死——以 model.completed/model.failed 轨迹事件与持久化 done 为唯一终态；断连期跳过该检查。

### O-P0-2【P0】finish_or_continue 遇残次消息即整轮失败，不补交
- 证据：`openwebui/runtime.py:204-207`（done 未持久化即 raise）、`:227-230`（structural_errors/missing_tool_results/unfinished_ids 一律 raise）；`openwebui/completion.py:157-162,174-205`（inspect_completion 把掉线产生的半个 function_call 判 incomplete_tool_arguments→结构错误）。
- 触发：socketio 抖动后消息落库残缺、done 先于 DB flush。
- 影响：②长任务。补交机制形同虚设。
- 修复：结构错误改为发起补交而非 raise；incomplete_tool_arguments 单独归类为可恢复。

### O-P1-1 停止无法中断同步工具执行
- 证据：`openwebui/runtime.py:262`（checkpoint 仅循环顶）、`:414-415`（gateway.execute 同步无取消点）、`chat_jobs.py:307`（cancel 只置标志）。
- 影响：④取消。提示"正在停止"却长期不结束（感知卡死）。
- 修复：工具执行包装周期性 checkpoint/超时，取消后丢弃结果。

### O-P1-2 换行级恢复链断档——"多次继续必失败"
- 证据：`openwebui/delivery.py:33-40`（allowed_runs 仅{当前 run, 单层 resumed_from_run_id, trusted_source_run_id}）；`resume.py:122` 每级只带上轮。
- 触发：A→B→C 连续"继续"，C 轮核验 A 轮图表/大屏。
- 影响：③产物。图类缺项→补交空转→stalled≥2（delivery.py:183-185）DeliveryIncomplete 失败。
- 修复：allowed_runs 沿 resumed_from_run_id 链递归闭包。

### O-P1-3 前端 finishChatStream 无限重试
- 证据：`app.js:1892` `while(stream.done||...)`；`:1903-1907` 仅 400/401/403/404/422 退出，5xx/断网 waitChatReconnect 无上限。
- 影响：④流式。done 后服务端不可达→永久"正在同步结果"。
- 修复：加重试上限，超时用本地 parts 兜底。

### O-P2 简列
- `openwebui/runtime.py:487-495` 前缀不一致时 reset+全量重发→滚动处闪变；
- `tools_seen`（runtime.py:396-401）进程内存去重，跨补交消息重复执行风险；
- openwebui 事件全 iteration=0 且无 attempt_id，与 harness 引擎 attempt 防回退语义不一致（app.js:2007 守卫退化）→ 切引擎行为漂移；
- 崩溃重启后 replay 用 Run.status 合成 terminal（chat_jobs.py:334-349），可能把真实结果播成"已中断"；
- openwebui 未就绪时 connect 立即失败不等待（client.py:57-59）；
- chart_renderer 质量面扎实（总行数/截断/重复标签/宋体字形全校验），仅 _RENDER_LOCK 全局串行化。

### O-重写三件事
1. runtime 循环的"静默判死+结构错误即失败"改为单一终态收敛器（done/轨迹/轮询，绝不 raise 中止）。
2. 恢复来源证明（delivery/resume allowed_runs）沿 resumed_from_run_id 链闭包。
3. 前后端统一"终态协议"：前端重试上限 + 后端可中断工具执行，失败/恢复/取消收敛为服务端单一权威终态。

---

## 七、专项审计合并 C：工具网关 / 内置工具 / 产物链路（agent 2e014b22）

> 方法：精读 gateway.py、validator/normalizer/compiler、builtin_provider.py（1446 行）、chat_artifacts/*、runtime/grounding_check、openwebui/delivery、files/template_fill、presentation/draft、analytics（query_receipts）等。已排除项：渲染工具产物经 `persist_artifacts`（chat_artifacts/artifacts.py:13-15 拾取 data.artifact_id）会进 result.artifacts 且 delivery.observe 可核验——"渲染产物不被交付核验"不成立。

### T-P0-1【P0】产物数值无"取数回执绑定"——编造数字可全链通过
- 证据：`write_presentation_slides` 的 chart/table 无 query_id 参数（`builtin_provider.py:391-403`）；`render_presentation` 同（:410-466）；`render_spreadsheet` 接受任意模型 spec（:467-472）；密度门只测载荷长度（`presentation/draft.py:384-395` len<120；builtin_provider.py:41-153 只查结构数量）；`grounding_check.py:86-105/134-144` 仅启发式 warn 且异常旁路；delivery 只核文件结构非空（`openwebui/delivery.py:100-126` + `artifact_inspection.py:114-204`），不比数字。
- 触发：模型跳过 `render_chart_image`（唯一回执绑定工具），改走 write_presentation_slides/render_spreadsheet 或 python_execute 自绘。
- 影响：①回答质量 ③产物。PPT/Word/Excel 图表与统计数字可模型手写，交付"假报表"。
- 修复：渲染层 chart/table/指标数字必须引用 query_id/回执哈希，服务端注入数据，未绑定即拒绝或强制"未溯源"标记；grounding 升为交付门。

### T-P0-2【P0】add_tool 零权限发布 HTTP 工具且无 SSRF 防护（治理边界）
- 证据：`builtin_provider.py:531-542`（add_tool 的 ToolSpec 未声明 permissions，_tool 缺省 permissions=[] 见 :170-180）；`connector/service.py:147-172`（publish 无权限/审批检查直接置 published）；`builtin_provider.py:1366-1368`（url 仅校验 http/https scheme，可注册 127.0.0.1/内网）。
- 影响：⑤治理边界。越权注册内网探测/调用工具。
- 修复：声明 connectors.manage+审批；URL 拒绝回环/内网段；发布链补权限。

### T-P1-1 内置渲染工具无单工具超时，chat 默认无整轮时限 → 对话挂死
- 证据：`gateway.py:437-448`（_dispatch 裸调 invoke）；timeout_seconds 仅 http/mcp/sandbox 消费（http_provider.py:122/mcp_provider.py:92/sandbox_provider.py:78），builtin 渲染路径无 timeout（builtin_provider.py:1012-1024）；`task_control.py:3-5` 默认无 deadline；`gateway.py:387` checkpoint 仅 execute 开头。
- 触发：50 页 PPT/大文档/慢模板。
- 修复：_dispatch 按 spec.timeout_seconds 包超时 + 渲染循环内 checkpoint。

### T-P1-2 PPT 分阶段有一性通道与自动补位逃逸
- 证据：`builtin_provider.py:410-466/958-1008`（render_presentation 兼容通道任意会话可用，唯一门是结构密度 :1004-1008，无数据/大纲一致性校验）；`presentation/draft.py:257-271`（write_draft_slide_batches 缺页码时自动匹配任意待写页→乱序内容安到错页）。
- 影响：③产物。
- 修复：强制分阶段、无页码即拒绝、render_presentation 限管理台通道。

### T-P1-3 fill_template 静默部分写入 + 无公式注入防护
- 证据：`files/template_fill.py:94-116/126-141`（_apply 吞异常只返计数）；`builtin_provider.py:1429-1446`（返回 written 不比对目标）；`template_fill.py:111`（fill_xlsx 裸写 cell.value，而 render_spreadsheet 有 safe_value（renderers.py:689-693）、exports.py:67-69 用 data_type="s"）。
- 影响：③产物 ⑤治理。半填充当成功交付、活公式注入。
- 修复：复用水洗函数 + written 比对不达标即 failed。

### T-P1-4 query_receipts 复用与 max_rows 契约失真
- 证据：`builtin_provider.py:682`（缓存仅查 len(data)<=max_rows）；max_rows=5 的完整结果被 max_rows=100 请求复用并标 same_turn_result_reused（:688-689），无实际行数提示；缓存键含 plannerTask（:669-671）模型改写即 miss。
- 修复：键纳入 max_rows 或复用元数据带实际行数。

### T-P1-5 链路缺口：查询结果→PPT/Word 无一步数据装配
- 证据：`render_chart_image` 仅 pie/bar/line（chart_renderer.py:16/88-90）；无 "query_ids→PPT 数据页/文档表格块" 工具；chart 返回无进 Word 指引（chart_renderer.py:217-219，对比 dashboard_image.py:239）；python_execute 自绘无回执绑定（delivery.py:119-120 仅 validation_note）。
- 影响：①回答质量 ③产物。模型必须手抄列名数字（转写即失真源，同 T-P0-1）。
- 修复：新增服务端取回执直填的报表合成工具（query_ids → PPT 数据页/Word 表格块）。

### T-P2 简列
- overview 样本被当总量风险（business_overview.py:71-92，已有 guidance 缓解）；read_file 图片识别失败仅告警（builtin_provider.py:1206-1217）；grounding 证据展开把 ID/时间戳当数字致误匹配（grounding_check.py:43-59）；`_normalize_model_arguments` 别名面过窄致 additionalProperties 误拒重试循环（gateway.py:79-93）；create_dashboard_from_queries 快照组件仍带 sql+datasourceId（query_dashboard.py:80-88）前端若重执行破坏不刷新承诺；query_workflow_results reference 三选一宽松易取旧（workflows/business_results.py:9-14）。

### T-P3
- write_file 无大小限制（builtin_provider.py:1306-1331）；PDF 是重渲染非 docx→pdf；validator 拒绝 $ref 属防御性。

### T-重写三件事
1. 数据回执绑定渲染器：render/presentation/spreadsheet 数值一律由 query 回执注入并校验，模型只写结构与文字，未绑定数字不过质量门。
2. 网关执行层：真正执行 spec.timeout_seconds/retry_policy + 取消传导，重写 add_tool 权限与 SSRF/审批治理。
3. 交付账本统一：render_*、fill_template、python_execute 输出统一走 artifacts+delivery 账本，补 written 比对与数字溯源核验，消除换路。

---

## 八、专项审计合并 D：入口与执行调度层（agent 80bc425c）

> 方法：逐行精读 chat/router.py、service.py、model_selection.py、business_modules.py、runtime/service.py、chat_jobs.py、chat_liveness.py、task_control.py、engine.py、recovery.py、clarification.py、grounding_check.py、harness/context.py、policy.py，并核对 scheduler_lease、chat_artifacts/resume、agent、providers/model、RunEvent、config 默认值。

### S-P0-1【P0】挂死线程永占租约 → 孤儿判定失灵 + cancel 无效
- 证据：`chat_jobs.py:69-71`（无总时限，CHAT_TASK_TIMEOUT_SECONDS 默认 0）；`task_control.py:66-75`（取消/超时仅检查点生效）；`chat_jobs.py:295-309`（cancel 只置标志）；`chat_liveness.py:180-187`（孤儿判定需 lease.acquire() 成功，卡死线程持有文件锁→永远判不死）；`providers/model.py:514`（流式阻塞段无 checkpoint）。
- 触发：供应商长流无 chunk、工具挂起、DB 慢，worker 停在非检查点。
- 影响：②长任务 ④取消。任务假活；`router.py:260-270` `_active_conversation_run` 以 409 挡死同会话后续全部发送。
- 修复：控制面与执行分离——心跳看门狗（进度时间戳）强制终止并置 failed+释放租约；所有阻塞点补 checkpoint；每模型/工具调用强制超时。

### S-P0-2【P0】事件逐条独立短事务提交 → DB 抖动即杀整轮且可能无终态
- 证据：`chat_jobs.py:148-164`（_save 每次 new_session+commit）；`:247-279`（emit 落库失败→run.failed 再落库失败→仅 logger.exception）；`replay 365-371`（无终态只能等下轮读触发 recover）。
- 触发：DB 锁等待/连接尖峰/慢磁盘，任一事件落库失败。
- 影响：②长任务 ④流式。数千帧=数千次独立提交的写放大；推送与执行同 DB 寿命，DB 一抖整轮失败且失败都写不进去。
- 修复：事件内存队列+批量落库；终态独立"可靠直写（单条带重试）"；执行线程不因事件落库失败而死。

### S-P1-3 continue/resume 只对 openwebui 引擎生效（与主审 R3 一致，证据更全）
- 证据：`router.py:375-378`（仅 chat_engine=="openwebui" 取 continuation_source）；`openwebui/resume.py:80`（要求 snapshot.openwebui.chat_id 才判可续）；`chat_artifacts/resume.py:72-91`；`config.py:199` 默认 harness。
- 影响：②长任务。默认引擎中断后"继续完成"拿不到成功回执/进度继承，只能重头再来。
- 修复：以快照字段（task_resume/resumed_from_run_id/user_message_id）为续跑判定的通用原语，与引擎解耦。

### S-P1-4 取消/超时终态以 run.failed 表达 + 无状态机闭环
- 证据：`chat_jobs.py:259-266`（cancelled 也 emit run.failed）；`router.py:544-555`（cancel 仅返回 "stopping"，终态依赖 worker 检查点）。
- 影响：④取消。取消被误展示为失败；无"取消请求→取消完成"的持久状态。
- 修复：显式 `run.cancelled` 事件；cancel 接口落库取消请求标记，读/重放按标记收敛终态，不依赖 worker 存活。

### S-P1-5 幂等重放活跃 Run = 双流风险 + 假活挡门
- 证据：`router.py:284-313`（活跃 run 也直接 replay from after=0，与在途流并发→前端重复渲染）；`:310-311`（活跃+不同载荷 409 且无 TTL）。
- 影响：④流式。双流歧义；假活 run 卡死 request_id。
- 修复：活跃命中返回"续传句柄"（after=上次游标）；活跃判定加租约存活+TTL 过期。

### S-P2/P3 简列
- P2 `model_selection.py:37-40` 默认 reasoning 档不校验可用性，未配 key 照建 Run，执行期才失败；`router.py:300-303` _replay_submission 每次 send 无 LIMIT 全表扫描会话全部 Run+全量快照（长会话放大 DB 负载）；读接口写副作用（每次 GET /events、/conversations 都经 recover_abandoned_chat 可能提交状态迁移并追加"已中断"消息，chat_liveness.py:190-203）；快照只存 profile 名不存 resolved endpoint，resume 读 CURRENT 配置→历史行为漂移（business_modules 实时拼接、harness/context.py:124-127）。
- P3 强杀窗口丢 ≤0.25s 聚合尾部 delta（chat_jobs.py:82-96）；cancel 返回与终态无确认握手；`?after=` 依赖全局自增 RunEvent.id，run_events 一旦清理游标错位。

### S-重写三件事（本层）
1. ChatJob 事件通道：内存队列+批量持久化+独立可靠终态直写，执行/推送与 DB 寿命解耦。
2. 租约/取消/超时三合一控制面：解除"线程存活=租约存活"耦合，心跳看门狗+控制面独立收敛终态。
3. continue/resume 通用恢复原语：以快照字段为准、与引擎解耦，harness/openwebui 共用同一续跑契约。

---

## 九、实施清单（待用户确认批次范围后执行；每项 = 改动文件 → 新增测试）

### 批次 A：harness 可靠性协议（H-P0-1/2/3/4、H-P0-5、R5、R8、S-P0-1/2）
- A1 工具回执 token 预算裁剪/票据化：改 `services/deepseek_harness/lib/company-preset.js`（output.render 按 tokens 截断：保留列名+前 N 行+统计+query_id）；配合 `backend/app/providers/tool/normalizer.py` 加票据字段。测试：offline 协议桩 + `tests/backend/test_harness_runtime.py` 增"大回执截断后上下文不失控"。
- A2 会话进程复用 + 入队：改 `backend/app/modules/harness/client.py`（会话级进程复用、排队与 HARNESS_MAX_PROCESSES 扩容语义）；`company-runtime.js`（同会话任务队列替代"仅一个活动任务"）。测试：并发/续问场景桩。
- A3 settle/cancel 协议重写：`lib/company-runtime.js`（以持久化 turn/end+flush 双确认判 completed；settle 超时兜底；取消与结算合并去重）；`harness/runtime.py`（run.settled 消费与 `_require_reconciled_tools` 放宽为仅结果不明时冻结）。测试：`test_harness_runtime.py` 增"完成后取消不判失败""取消竞态终态唯一"。
- A4 取消不等 30s 硬超时：`harness/client.py cancel()` + `runtime.py` 取消后等待真实 settle（上限放宽/事件驱动）。测试同上。
- A5 输出上限治理：`config.py` 默认 HARNESS_MAX_OUTPUT_TOKENS 提高（按档位容量）或分阶段结算。测试：配置生效断言。
- A6 排队可见性 + 重放标记：`chat/router.py`（replayed 标记）、`chat_jobs.py`（排队 progress 事件）、前端展示。
- A7 ChatJob 事件通道重写（S-P0-2）：内存队列+批量落库（如 100 条/批或 500ms 合并）+ 终态独立"可靠直写（单条带 3 次重试）"；执行线程与事件落库解耦（落库失败只降级内存，不杀线程）。测试：模拟落库失败的用例。
- A8 看门狗/控制面三合一（S-P0-1）：心跳进度时间戳（每事件/每 5s 更新）+ 独立巡逻任务对超时无心跳的执行线程强制终止置 failed/cancelled 并释放租约；RUN/模型/工具层补 checkpoint 与强制单调用超时。测试：假死线程回收用例。

### 批次 B：产物数据回执绑定 + 落地校验可见（T-P0-1、R1/R2、T-P1-2/3/4/5）
- B1 渲染层强制 query_id 绑定：`builtin_provider.py`（write_presentation_slides/render_presentation/render_spreadsheet 增 `query_ids` 参数，服务端从 query_receipts/回执注入数字，未绑定数字拒绝或打"未溯源"水印）；`presentation/draft.py`（无页码拒绝而非自动补位；density 门加数据来源检查）。测试：`test_presentation_*.py`、`test_reports.py` 增"手写数字被拒"。
- B2 新增"查询回执→报表合成"工具：`builtin_provider.py`（query_ids → PPT 数据页/Word 表格块直填）；`chat_artifacts/` 支持。测试：全链装配用例。
- B3 delivery.audit 前端化：`apps/web/app.js` 渲染 delivery.audit（警告条+示例列表）；`tests/ui/chat_shell.js` 断言。服务端保持事件契约。
- B4 交付完整性核对（engine 无关）：`runtime/` 新模块 `deliverables_check.py`，收尾对照 Artifact 清单，缺失标记 `deliverable_missing` + 触发一次补交。测试：`test_chat_unlimited_lifecycle.py` 等增"要 PPT 只出 Excel → 终态标记"。
- B5 fill_template 水洗与 written 比对：`files/template_fill.py` 复用 safe_value；`builtin_provider.py` 比对目标不达标即 failed。测试：`test_file_*.py`。
- B6 query_receipts max_rows 契约修正。测试：`test_analytics.py`。

### 批次 C：闸门接回执行路径 + 网关治理（R10/R11、T-P0-2、T-P1-1、H-P1-4、O-P0-1/2、O-P1-1/3）
- C1 澄清/直付真实值/取数证据闸门移植：从 `agent.py` 把 `clarification.question_card`、`query_answer.query_values_answer` 迁到 `harness/runtime.py`/`openwebui/runtime.py` 可调用点（或引擎无关层）；删除/标注 agent.py 与 `runtime/service.py` 等过时注释。测试：新增澄清与兜底用例。
- C2 add_tool 治理：`builtin_provider.py`（声明 connectors.manage + URL 拒绝回环/内网）；`connector/service.py`（发布补权限/审批）。测试：`test_capabilities_*.py` 增越权用例。
- C3 网关执行超时/取消传导：`gateway.py _dispatch` 按 spec.timeout_seconds 包超时；渲染循环内 checkpoint。测试：`test_sandbox_provider.py`/渲染超时用例。
- C4 DB 冻结按工具粒度：`harness/runtime.py _database_failure` 调整为可恢复分类。测试：`test_harness_runtime.py`。
- C5 openwebui 终态收敛器（静默不判死、残次转补交）：`openwebui/runtime.py` + `completion.py`。测试：`test_openwebui_*.py` 增长生成/抖动场景。
- C6 前端重试上限 + 取消可中断：`apps/web/app.js`（finishChatStream 上限）、工具执行可中断点。测试：`tests/ui/chat_stream_recovery.test.js` 增场景。

### 验证门槛（每批完成后）
1. `python backend/compile_check.py` 语法门禁。
2. `python tests/run_isolated_backend.py tests/backend` 全量回归（隔离 SQLite、禁外网、禁 8011/8012）。
3. 对涉及 harness 的改动：`tests/backend/test_harness_runtime.py` 专项 + 离线协议桩。
4. 前端改动：`node tests/ui/chat_stream_recovery.test.js` + `npm run ui:check`（需隔离服务）。
5. 批内新增用例全绿后方可进入下一批。

---

## 十、详细技术设计（用户确认：先出设计、二次确认后动代码；v1.0）

> 本设计以"默认引擎 harness 调整为主、引擎无关公共层优先、不改变对外事件契约名称"为原则。每个设计项标注：目标 → 关键改动（文件 + 数据结构/协议/接口）→ 兼容性 → 测试。

### 批次 A 详细设计 —— harness 可靠性协议 + 事件通道 + 控制面

#### A1 工具回执 token 预算裁剪 / 票据化
- 目标：根治"大查询回执塞爆上下文→压缩打地鼠→整轮失败/事实漂移"（H-P0-1/R7）。
- 关键改动：
  1. `services/deepseek_harness/lib/company-preset.js` `output.render`：计算回执 JSON 序列化后的估算 token（`Math.ceil(JSON.stringify(v).length / 3)`），预算常量 `TOOL_RESULT_TOKEN_BUDGET = 6000`（可配置）。超出时输出结构化裁剪：
     ```js
     {
       kind: 'truncated',
       ticket: { tool, invocationSeq, query_id: v?.data?.query_id ?? null, rows_kept: N, total_rows: M },
       schema: <列名数组>,          // 完整列名
       preview: <前 5 行>,          // 样例行
       aggregates: <数值列摘要：count/sum/avg/min/max>,
       note: '已截断，query_id 可复现完整结果'
     }
     ```
  2. `backend/app/providers/tool/normalizer.py`：ToolResult 增加 `truncated`/`ticket` 元数据透传（不回退现有字段）。
  3. 依赖正确性：`query_business_data` 的 `query_id`（analytics 层已返回）必须进入裁剪票，供后续 B 批"回执绑定渲染"使用。
- 兼容性：模型工具契约的 result 形状变化属预期增强（B 批消费 ticket）；平台日志/trace 不变。
- 测试：协议桩返回 >6000 token 的假回执 → 断言截断票结构 + 上下文注入量受控；`test_harness_runtime.py` 增用例。

#### A2 会话进程复用 + 同会话入队
- 目标：消除"单会话单活动任务+每 Run 新进程抢锁"导致的追问/重试被拒（H-P0-2），并缓解槽位无限等待（R5）。
- 关键改动：
  1. `harness/client.py`：引入 `SessionSlotPool`（进程内 按 identity 复用一个已启动进程）；`SchedulerLease` 仍用于跨进程互斥，但改为"会话进程租约（长期持有）+ 任务级排队"两层：
     - 首启：进程启动后持有 `sessions/{identity}/.process.lock`；
     - 后续同会话 Run：复用进程（检查 `health` RPC），不重建。
  2. `company-runtime.js`：`session.prompt` 从"仅一个活动任务(throw)"改为**任务队列**（FIFO）；队列等待事件 `task.queued = { position }` 通知；取消只取消当前任务并跳过排队项（可配置丢弃策略）。
  3. `HARNESS_MAX_PROCESSES` 语义从"进程数上限"改为"并发会话数上限"，排队态通过 `task.progress` 透传前端。
- 兼容性：`HARNESS_START_TIMEOUT_SECONDS`/`HARNESS_CANCEL_TIMEOUT_SECONDS` 语义保留；`/api/chat` 事件契约新增可选 `task.queued`（前端忽略未知事件，安全）。
- 测试：同会话连续两条 prompt 桩 → 第二条排队并在首条 settle 后执行；并发 3 会话（>2 槽位）→ 第三会话进入排队且可取消。

#### A3 settle/cancel 协议重写（终态收敛器）
- 目标：消灭"已完成被判 failed"与"取消与完成竞态"（H-P0-3/P0-4、S-P1-4）。
- 关键改动：
  1. `company-runtime.js`：
     - `settle()` 判定 completed 的双确认：`lastTurnEnd.seq >= run.startSeq` **且** `sessions.flush()` 成功（持久化）；不满足时进入"待收敛"状态，由 `shutdown`/下一次 `health` 轮询触发重判，不再直接 failed（去掉以 `MISSING_TERMINAL` 即失败的语义）。
     - `session.cancel` 与 `run.settled` 竞态：取消结果与结算结果在 `completed` Map 合并去重（同 requestId 仅一次 notify）；`run.settled` 增加 `final_reason` 字段（completed/cancelled/failed + error）。
  2. `harness/runtime.py`：
     - 消费 run.settled 改为按 `final_reason` 分支；取消路径不再 30s 硬超时 raise——改为等待 settle（受 `HARNESS_CANCEL_TIMEOUT_SECONDS` + 事件驱动，超时后仅提示"结果待核实"而非直接 failed）；
     - `_require_reconciled_tools` 放宽：只对 `error_code` 为 `tool_result_unconfirmed`/`tool_result_unknown` 的行冻结，`chat_cancelled` 不再冻结后续新 Run（取消≠结果不明）。
  3. `chat_jobs.py`：新增显式 `run.cancelled` 事件（前端停止等待的正式终态之一），与 run.failed 分离；`cancel` 接口落库 `runs.context_snapshot.cancel_requested_at` 供重放收敛。
- 兼容性：事件名新增 `run.cancelled`（前端需识别，见 C6）；既有 `?after=` 续传逻辑不变。
- 测试：`test_harness_runtime.py`："settle 与 cancel 竞速"参数化 → 终态唯一且为 cancelled 时工具不冻结；"完成后再 cancel" → 保持 completed。

#### A4 输出上限治理（4096 → 按档位）
- 目标：长任务正文不被 4096 截断（H-P0-5）。
- 关键改动：`config.py` 默认 `HARNESS_MAX_OUTPUT_TOKENS` 提升至 `min(⌊context_window×0.5⌋, 16384)`（档位可覆盖）；`harness/context.py model_configuration` 校验 `max_tokens < context_window-2048` 不变。
- 影响面：对话 token 成本上升，需配套 A1 裁剪以对冲；`model.service.enforce_budget` 已按量扣费，无协议变化。
- 测试：配置断言 + 长正文场景（桩）不触发 max-tokens。

#### A5 事件通道与控制面（S-P0-1/S-P0-2）
- 目标：执行/推送与 DB 寿命解耦；消除"挂死线程永占租约"。
- 关键改动：
  1. `chat_jobs.py ChatJob`：事件写入改为"内存队列（无界轻量元组）+ 批量落库协程"：`_save` 入队即唤醒；批量任务每 500ms 或满 100 条写一次 `run_events`（单事务多行）；**终态事件（run.completed/run.failed/cancelled）单独走"可靠直写"**（新 session + 3 次重试 + 内存兜底）。
  2. 看门狗：`chat_jobs` 增加巡逻线程（每 10s）：检查每个活跃 job 的 `last_progress_at`（由 emit/checkpoint 更新时间戳），超过 `CHAT_STALL_TIMEOUT_SECONDS`（新配置，默认 120s）→ 置 `control.stalled` → 触发强制 `finalize_run(failed, stale_run_recovered)` + 释放租约 + 从 `_jobs` 移除。`TaskControl.check()` 增加 stalled 分支。
  3. `task_control.py`：新增 `stalled` 标志与 `last_progress` 更新原语；`providers/model.py` 流式阻塞段与 `gateway._dispatch` 工具调用段补 `checkpoint()`/`remaining_timeout`。
- 兼容性：`replay` 对外行为不变（按事件 id 续传）；新增配置项默认关闭秒数可调。
- 测试：模拟 20s 无心跳 → 巡逻线程在阈值后终态化；模拟单事件落库失败 → 批量重试成功且线程存活。

#### A6 中间态可观测
- `chat_liveness` 孤儿判定与看门狗去重（一次只有一个执行者收敛）；`trace` 暴露 `delivery_audit`/`stalled`/`cancel_requested_at`。

### 批次 B 详细设计 —— 产物数据回执绑定 + 落地校验可见（设计要点）

- B1 渲染绑定：`write_presentation_slides`/`render_presentation`/`render_spreadsheet` 的 schema 增加可选 `query_ids: string[]`；渲染时对每个 chart/table/指标块匹配 `query_id` → 服务端从 `control.query_receipts`/QueryHistory 取回执列名与行，注入数据并写 `data_source: {query_id, sha256}`；**模型提供的数字与回执不一致 → 覆盖为回执值并记录 `corrected` 标记**；无 query_ids 且含数字的块 → 打"未溯源"水印（可配置降级为拒绝）。
- B2 报表合成工具：新增 `compose_report_tables`（query_ids+布局说明 → Word/PPT 表格块/数据页），服务端直填数字，交互副作用为零。
- B3 delivery.audit 前端：`app.js` 增 `delivery.audit` 分支渲染状态徽标与折叠告警；离线切片测试补哨兵。
- B4 交付完整性核对：`runtime/deliverables_check.py`（正则+语义关键词提取 PPT/Excel/Word/大屏/图 → 对照本 run Artifact 类型集合）→ 缺失且正文含"已生成/已交付" → 终态 `deliverable_missing` + 一次补交 re-prompt（A3 队列支持）。
- B5/B6 见九。

### 批次 C 详细设计 —— 闸门接回 + 网关治理 + 前端终态协议（设计要点）

- C1 澄清/直付真实值移植：`clarification.question_card` 在 harness 路径挂到"消息结束无正文/模型询问"检查点；`query_answer.query_values_answer` 仅在模型不可用时由 runtime 直接回填真实查询值。`agent.py` 加模块级 @deprecated 注明休眠（配合用户已批准的低风险清理）。
- C2 add_tool 治理：permissions 强制 `connectors.manage`；URL 解析后拒绝 loopback/内网/保留段（复用 collection.transport 的校验函数）；publish 前加审批钩子。
- C3 网关超时/取消传导：`_dispatch` 对 builtin 路径也执行 `spec.timeout_seconds`（线程包装+取消传导）；渲染循环内周期 checkpoint。
- C4 DB 冻结按工具粒度：`_database_failure` 改为返回"可重试/不可重试"两档，可重试不冻结整轮。
- C5 openwebui 终态收敛器：`runtime.py` 取消 10s 判死（改轨迹+持久化 done 收敛）、结构错误转补交。
- C6 前端终态协议：`finishChatStream` 重试上限（如 5 次/总 60s，超时用本地 parts 兜底）；识别 `run.cancelled`；取消后工具结果丢弃。

### 已批准的低风险清理（随本报告一并执行）
- `runtime/agent.py`：模块 docstring 标注"休眠代码（无调用方，仅供参考/迁移）"。
- `runtime/service.py:4`、`runtime/chat_jobs.py:175`、`runtime/report_grounding.py:3`、`runtime/query_answer.py:3`、`runtime/clarification.py:5`：修正过时"agent_run 调用方"表述，指向 engine.run_chat/harness/openwebui runtime。