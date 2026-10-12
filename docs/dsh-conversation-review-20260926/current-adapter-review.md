# 当前 Python → Node DSH 适配层功能审查

审查日期：2026-09-26。依据当前源码、原生上游事件实现和纯离线复现；不把旧审查文档、源码注释中的判断当作事实。本分项未改运行源码，未启动业务服务，未访问业务数据库或真实模型。范围是 `backend/app/modules/runtime/engine.py`、`backend/app/modules/harness/`、`services/deepseek_harness/`，并沿调用链检查模型选项、工具网关、交付校验与原生 DSH 事件语义。

**当前对话已经使用原生 DSH 执行循环。主要问题集中于公司的适配、业务工具和完成契约；直接把 DSH 目录整体换一遍不能消除这些问题。** 正常工具回调、会话历史恢复、跨进程请求去重均存在真实实现，主审已用真实 Node 子进程和仅本机模型桩验证通过。异常后继续、思考开关、产物完成判定存在具体缺口，见下表。

## 真实调用链与能力边界

| 环节 | 当前实现及条件 | 代码位置 |
|---|---|---|
| 引擎选择 | 读取 Run 快照的 `chat_engine`，否则用配置；默认配置是 `harness`。旧会话可保留 `openwebui`，不能把所有历史故障都视为当前 DSH 故障。 | `backend/app/modules/runtime/engine.py:11-33`；`backend/app/config.py:199` |
| Python 主入口 | 建消息、记录 Run、选模型、列业务工具，然后创建 HarnessClient，发送 initialize 和 session.prompt。 | `backend/app/modules/harness/runtime.py:280-328` |
| 原生 DSH 启动 | Python 启 Node；launch 读取上游 manifest、要求 `0.1.7-alpha.2`、导入上游 `apps/cli/lib/bin.js` 的 runCli，使用 company-runtime profile。上游默认目录是 `.implementation/harness_migration/upstream`。 | `backend/app/modules/harness/client.py:117-125`；`services/deepseek_harness/launch.mjs:8,29-31,41-52` |
| 主执行循环 | profile 加载原生 agent、agent-loop、tools、session-controller、session-persistence-jsonl、llm-retry 等；公司代码通过公开 sessionController.prompt 提交内容，并等待原生 idle、flush、事件游标达到终态。 | `services/deepseek_harness/profile.mjs:12-47`；`src/company-runtime.ts:116-136,185-191` |
| 工具执行 | 模型看到的是 Python 审批列表。Node 记录原生 tool/call 的 seq，调用 tool.invoke；Python 网关执行、登记文件/工件、落工具结果，Node 将真实结果给原生 loop。 | `src/company-preset.ts:11-56`；`runtime.py:86-99,372-475` |
| 工具范围 | 当前公司 profile 只装 company 工具 preset；对话开放约 30 个业务工具，包括取数、文档、表格、PPT、图表、知识与技能资源读取。它没有自动启用上游整个通用 CLI 的 shell、子代理和全部工具。这个范围由公司 profile/白名单决定。 | `runtime.py:86-121`；`profile.mjs:37-40` |
| 正常续聊 | 用户/工作区/会话生成稳定 session identity；每轮新 Node 进程使用相同持久目录。启动恢复 native JSONL，重新选模型、读取历史收据并跟随事件。 | `runtime.py:123-136,293-319`；`client.py:74-75`；`src/company-runtime.ts:38-50,229-241` |
| 去重 | 同一 requestId 活动时不重复接收，已终态时返回历史终态；重启扫描 user/message rpcId 与 turn/end 重建 completed。Python 工具幂等键绑定 session 和原生 invocation seq。 | `src/company-runtime.ts:38-50,166-175`；`runtime.py:377-409` |
| 长上下文 | 确实加载 BasicCompactionEngine 子类；自定义中文检查点摘要保留目标、资源、完成与未完成事项。摘要最多 min(模型输出,4096)，错误、max-tokens、空摘要不保存。 | `profile.mjs:44-47`；`src/company-compaction.ts:3-40` |
| 停止 | 独立 Python 线程发 session.cancel；Node 等消息 admission、取消 agent、等待 idle 和 flush；工具中途取消仍等待 Python 工具结束后回执。完成与取消竞争时按持久化的真实终态报告。 | `client.py:188-196,228-237`；`src/company-runtime.ts:124,179-205`；`src/company-preset.ts:39-48` |
| 输出限制 | 达到模型输出上限时原生返回 max-tokens；公司把它映射为 model_response_incomplete。没有自动重发“继续”来满足未完成业务目标。 | 原生 `packages/core/agent-loop/src/agent.ts:513`；`src/company-runtime.ts:123-125`；`runtime.py:75` |
| 当前性能边界 | 每个对话轮次启动、结束 Node；全局进程数硬上限 2，同会话互斥；工具统一单并发且每项声明不并发安全。等待槽位只有“正在准备”进度，无队列位置。 | `backend/app/config.py:204`；`client.py:87-125`；`profile.mjs:23`；`src/company-preset.ts:31`；`runtime.py:314-315` |

主审的 `native-local-smoke-results.json` 记录真实公司 profile + 原生 DSH + 本机桩验证：正常工具闭环、冷启动历史恢复、跨重启 requestId 去重全部通过；3 次本机模型 HTTP 请求，0 数据库，0 外部模型。一次启动约 2.0 秒、恢复约 1.328 秒，只是该机器这一次小样本，不能推成复杂任务的性能保证。

## 已离线复现的功能问题

### F1 / P1：一次普通工具内部错误会取消本轮，并让同一会话后续新问题持续失败

**触发条件：** 任一业务工具（包括文件读取、图表、文档生成）返回 `tool_error` 或 `tool_internal_error`。不要求它访问数据库，也不要求存在不确定副作用。

工具网关在 `backend/app/providers/tool/gateway.py:446-448` 将所有未预期 provider 异常统一转换为 `tool_internal_error`。`harness/runtime.py:147-160` 不看工具种类，也不区分明确失败与结果不明，直接把这两个代码当成数据库异常；`450-452` 设置工具冻结，`468-469` 取消原生运行。第二轮进入 `207-216` 的历史检查时，即便旧工具状态已经是 failed，仍因这个错误代码抛 `tool_result_unconfirmed`，而且 `300` 在启动模型前调用此检查。它筛选的是整个会话所有旧 Run，用户更换指令、重试新 Run 都不会移除旧记录。

离线用真实函数 AST 和内存历史记录复现：`tool_internal_error / 图表颜色解析失败` 判为数据库故障；随后两次不同 run ID 均被同一 failed 历史工具阻断。相反 validation_error 和 model_unavailable 不被冻结，说明这不是“所有失败都不能恢复”，而是特定泛化错误码路径的问题。

检索 `backend/app` 与 `apps/analytics/src`，未找到针对该检查的操作结果核实/解除入口；相关 `requires_verification` 仅产生记录。当前提示“请先核实”没有对应可完成的产品流程。此结论是代码检索范围内未发现入口，不推断外部运维是否会手工修数据库。

**建议：** 按工具类型与明确错误类型冻结实际数据源；仅对有副作用且结果未知的具体调用阻止重放，允许与它无关的新问题和只读操作继续。提供可审查的核实结果与解除状态。不要因修复此项而放开真正数据库异常后的自动重试。

**归属：公司 Python 适配层。** 替换上游 DSH 目录并保留当前 runtime.py，不会改变此行为。

### F2 / P2：管理员配置的关闭思考在两种协议中不能按字段语义生效

**触发条件：** 模型 runtime_options 设置 `thinking="disabled"`，没有同时设置 native 的 `reasoning_effort="off"`。

- `model/runtime_options.py:33-38,57-70` 接受独立 thinking 和 effort 字段，没有一致性校验。
- DeepSeek Messages：`harness/context.py:81-83` 不读 thinking 设置，默认写 `reasoningEffort=high`，只检查 effort 是否 off 决定 thinking。离线输入 thinking=disabled，实际 Python → Node 输出是 thinking=enabled、reasoningEffort=high，profile 原样送到原生 provider。显式 effort=off 的对照样本才正确关闭。
- OpenAI 兼容：`context.py:78-80` 写了 thinking=disabled，但 `profile.mjs:59-66` 完全不消费这个字段。离线调用真实 companyProfile，设置 disabled 和完全不设置生成的 profile 深度相等。若同时写 high，仍将 high 交给 agent-default-model。
- 原生 provider 明确支持 native thinking=disabled 和 effort=off：原生 `packages/llm/llm-deepseek/src/config.ts:33-36,218-221`、`serialize.ts:135-143`。OpenAI 是否默认思考取决于实际供应商，不能据此断言所有兼容模型必然思考；能确定的是公司的 disabled 开关没有生效控制。原生 pi-ai 的 `adapter.ts:172-182` 也明确说明“省略 reasoning”不等于关闭一个默认思考的服务。

**影响：** 用户为降低延时关闭思考时仍可能持续思考、消耗输出预算；选项显示与实际请求不一致。没有读取当前凭据/实际模型参数来推断用户现有哪一个模型正在命中，因此这里是已验证的配置路径缺陷，不冒充所有慢请求的唯一根因。

**建议：** 将 thinking 与 reasoning_effort 定义成一致合同；native disabled 应映射 off 或拒绝冲突。OpenAI 需依据所选 compat/provider 明确发出供应商支持的关闭字段，并用实际本机桩收到的请求体验收，不能靠删除推理元数据表示关闭。

**归属：公司模型映射与 company profile。** 原生 provider 已有能力。

### F3 / P1：对话原生结束被当成业务交付完成，缺少附件也能显示“产物已交付”

**触发条件：** 用户要求生成文档/表格/PPT，模型只输出“文档已制作完成，请下载附件。”，原生正常结束，没有真实工件。

`harness/runtime.py:521-537` 仅检查原生 status、事件游标和非空已提交正文。`539-546` 执行交付审计但不据结果更改完成状态，随后 `549` 无条件推“任务完成，产物已交付”。原生循环 `packages/core/agent-loop/src/agent.ts:513-516` 在没有 tool-call 的正常回复时就完成，这本身是原生通用循环语义；企业交付验收需要适配层补充。

当前 `runtime/grounding_check.py:94-95` 只有“正文有数字且包含文档且无工件”才追加口头交付警告；无数字的交付声明完全绕过。离线调用真实 check_grounding，以“文档已制作完成，请下载附件。”、零证据、零工件作为输入，得到 `status=pass, checked=0, warnings=[]`。即便别的语句触发 warn，外层也照样 completed。

**影响：** “写了回答”与“按用户要求真的交付可下载文件”没有机器可判定的联系；复杂任务漏某个文件/漏最后 finalize 时无法阻止错误完成状态。不是说所有产物工具都无效：成功工具返回的真实 artifacts 会持久化并发 task.deliverable（runtime.py:433-475），缺口在未交付时也能完成。

**建议：** 在任务开始记录必要交付物和当前 run/conversation 的工件引用；结束时核验必需文件已存在且状态可下载。未达到要求时明确 partial/failed 或继续一次有界修正，完成文案按真实交付事实生成。数字来源审计保留为独立功能，不代替交付清单。

**归属：公司业务完成契约。** 直接使用原生 CLI 也不会自动知道本产品的 Artifact、PPT finalize、下载地址状态。

### F4 / P2：带 token usage 的失败模型尝试会被记为成功

**触发条件：** 原生请求收到 usage，随后 `finish.reason.kind="error"` 或 aborted，产生 assistant/attempt。

`harness/runtime.py:493-506` 只看 data.interrupted 与是否有 usage；有 usage 就 settle_model(completed)，不检查嵌入 stream 的 finish reason。原生 `packages/core/agent-loop/src/agent.ts:470-491` 恰好在 finish 是 error/aborted 时写 assistant/attempt 并决定重试。原生 `tests/loop.spec.ts:182-209` 是“失败尝试后重试成功”用例，不是同一次成功请求必然有 attempt 再 message；公司 `runtime.py:494-498` 注释将它误读了。

离线提取真实结算分支，输入与原生测试相同的 usage=1/0 和 SERVER error，实际得到 completed。usage 应被累计，但调用状态应为 failed；随后重试是独立调用。此项直接影响模型调用记录、错误率和排障，不把它扩大为正常回答必然丢失：正常成功原生只产生 assistant/message，主审真实桩也已确认。

**建议：** 从 native attempt 的 stream finish/error 判定失败原因，成功/失败与 token 使用量独立记账，并补原生错误后成功重试的真实事件序列测试。

**归属：公司事件状态适配。**

### F5 / P1：原生历史可以恢复，但 Harness 下一轮没有绑定上一轮 PPT 草稿

**触发条件：** 第一个 Run 已创建 PPT 大纲或写了部分页面，但未 finalize；用户在同一会话发送“继续”形成第二个 Run。此时对话 engine 为 harness。

主审指出路径后，本分项重新读取源码交叉确认：`chat/router.py:375-378` 只为 openwebui 查 unfinished presentation/continuation source，`385` 的 is_retry 也只允许 openwebui；`477-499` 的 resume_draft 因而不会在 harness 新轮触发。草稿文件键在 `presentation/draft.py:12-15` 绑定 workspace_id + run_id。业务工具从当前上下文取得 run_id，`builtin_provider.py:891-922` 调用 write_draft_slide_batches/final_spec 时均使用当前 Run；它没有接受历史 draft_id 来选择旧草稿。第二轮虽然模型从原生历史知道“刚才已写几页”，实际操作对应的旧文件却没有迁移/关联到新 run，`draft.py:48-50` 会提示“尚未创建 PPT 大纲”。

**影响：** 会话记忆和业务中间成果脱节，用户“继续完成上一份 PPT”容易收到重新建大纲提示、返工，或模型误认为只差 finalize。完整的原生历史持久化不能补上业务草稿关联。

**建议：** 将草稿改为稳定的业务任务/草稿 ID，或给 Harness 新 Run 也做有来源和归属校验的显式草稿恢复。恢复工具应返回真实 written/missing 页面和文件状态；同一会话中的新主题不能错误复用旧草稿。

**此问题独立于 F1。** 缺草稿抛的是 ToolExecutionError，`errors.py:85` 定义的代码是 tool_execution_error，`gateway.py:440-443` 保留这个代码；它不会仅因这个错误代码而必然命中 F1 的两个 generic error。因此不能声称“PPT 续写缺草稿必然永久冻结会话”。本项为静态完整调用链确认，未额外运行真实 PPT 渲染。

**归属：公司业务工具与 chat 恢复桥接。**

## 慢、卡住、停止与复杂任务的进一步判断

1. **正常原生会话恢复已实现且有验证。** 不能把“出现长任务失败”概括成“DSH 无记忆”。可重复失败的恢复缺口是 F1；超长文本到 max-tokens 明确失败而非自动完成也是真实边界。
2. **工具总是串行。** profile 最大工具并发为 1，且每项 isConcurrencySafe=false。对 N 个独立慢查询或工件制作，总耗时会近似累计；这是公司 profile 选择，换同一个上游目录不改变它。不要直接给访问业务数据库的工具加并发，应先区分纯本地排版和查询负载。
3. **Python 同步工具是取消边界。** runtime.py:433-434 同步等待网关与工件持久化；Node company-preset.ts:42-47 在收到 abort 后仍等待工具回执。独立线程能发送取消，但无法让任意同步 provider 立即停下；底层工具是否及时检查 TaskControl、关闭阻塞请求决定停止延时。当前适配有合理的持久化终态等待，不能把“点停止不是瞬间结束”本身报成已确认错误。尚未在全部业务工具上逐一执行取消时延测量。
4. **复杂任务并无交付清单状态机。** 原生工具循环和上下文压缩可以持续多步，但公司封装没有把“用户全部目标完成”作为比 native completed 更强的判定。F3 是用户感觉“看起来完成，实际缺东西”的直接路径。
5. **主审已有独立历史时延证据。** 9/24 旧日志中的业务取数工具内部 Python model.chat 用时约 216 秒，随后 SQL 约 256 毫秒；另一长轮包含多个 90–180 秒模型失败和约 300 秒大屏内部模型失败。详见 `historical-log-summary.json`。这是历史记录，不是当前源码回归测量；但当前工具仍有独立 Python 模型调用，因此仅改对话外层不一定解决慢。该项以主审对当前 queries.py/providers/model.py 的独立核验为准。

## 验证记录与建议次序

本分项新增两个可重复离线脚本：

- `reproduce_adapter_boundaries.py`：用 AST 提取当前模型映射、工具故障判定、历史检查、交付审计、模型结算分支；保留真实函数体，用内存伪对象代替数据库。已通过所有断言，输出 `adapter-boundary-evidence.json`。
- `reproduce_profile_thinking.mjs`：调用真实 companyProfile，证明 OpenAI disabled 字段消失、native disabled 被 Python 改写；已通过断言，输出 `profile-thinking-evidence.json`。

两脚本累计数据库连接 0、HTTP 请求 0、模型调用 0；不导入 app 初始化，不改任何 runtime 源文件。AST 复现证明局部函数分支，不能代替完整 UI/真实模型验收。独立的真实 Node 本机桩验证来自主审，结果在本目录 `native-local-smoke-results.json`。

先修 F1 的失败恢复、F3 的交付完成契约及 F5 的 PPT 草稿恢复，再修 F2 的实际模型参数和 F4 的状态记录；随后按当前真实请求分别计时主模型、工具内嵌模型、SQL、文件渲染、队列等待。保留已验证的原生 DSH 循环、JSONL 历史、去重和取消终态能力。若决定升级/替换上游，先解除并更新明确的版本锁与适配契约测试；不能把上游目录替换当作上述公司代码问题已修复。
