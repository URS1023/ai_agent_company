# 原生 DSH 长任务终态与复合交付专项只读审查

审查日期：2026-10-04。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-capability-contracts`。基线 HEAD：`112bdd3efdc242a95f174e497922b42e56d6f1a9`；审查时存在批次十三未提交改动，不能把 HEAD 等同于被读工作树。文件指纹见末尾。

本次仅读本机源码、既有测试及静态文件；没有执行测试、模型、数据库、HTTP 请求或 8011 服务，没有修改产品代码和测试。采用 `code-review-excellence` 的证据优先审查方法。本报告是下一批修复设计输入，不代表这些发现已经隔离复现、修复或验证通过。统计精度、SQL 取消和连接池生命周期不在本次范围。

## 结论与执行路径

当前正常启动入口已经是原生 DSH。`backend/start_server.ps1` 执行 `backend/run.py`；后者 `host_command()` 19–46 行固定使用 `services/dsh_host/launch.mjs`。Host 在 `services/dsh_host/host.mjs:125–144` 接管聊天发送，以 `chat.prepare → sessions.admit` 进入原生队列。后续链路是：

`SessionHost.execute → 原生 prompt/工具/子代理 → nativeIdle → 原生 flush/事件持久化 → chat.settle → 业务交付核验 → Run/Message 落库 → 最终事件/done`。

实际 Worker 文件是 `backend/app/business_worker/chat.py`、`tools.py`，不是 `business_worker/modules/chat.py`。

`backend/app/modules/runtime/engine.py` 保留 harness/openwebui 的兼容分派，`runtime/agent.py` 仍有历史循环和证据闸门；它们不等于这个 Host 聊天主路径。尤其不能拿旧 Agent 的重写轮数或总时长来解释当前 DSH 为什么结束。`runtime/agent.py` 顶部关于“当前默认”的旧注释早于目前启动链，应以真实入口调用为准。

当前已具备以下保护，审查没有把它们重复登记成缺陷：

- 原生主代理首次 idle 不会直接结算：`sessions.mjs:338–349` 等待当前 run 的子代理、后台 Job 和子代理交付，并重新检查主代理是否 idle。
- 普通业务工具失败会以失败回执反馈给原生工具循环；下一步可纠正参数。某个可修正错误出现过，不足以判定整轮必须失败。
- Worker 在工具锁内收尾未完调用，`prepared/dispatch_started/pending/running` 未完成时，即使原生给出 completed，也会降为 interrupted；附件数量缺少会降为 partial。
- 分批 PPT 的 `final_spec()` 拒绝缺正文页，并把 `presentation_progress` 给回工具循环；不是“缺第 13 页也能通过 finalize”。
- 取消时保留已持久正文，重复结算不会覆盖第一次规范结果；原生重试投影会撤回被放弃的尝试。
- `run.completed` 事件名用于 completed/partial 两种回执，但当前前端 `chat-session.js:193–206` 优先读 `data.status`，不会仅因事件名而把 partial 改成 completed。
- 宿主冷恢复对存在后台委派或未完成新轮次的运行采用 interrupted；这是一种保守的不重复执行策略，不能据此直接说恢复失败或子任务被删除。

仍有 3 项高优先级静态缺口和 1 项连续任务识别缺口，均需下一批先补失败回归。另有 3 项合同/一致性观察，不混入“已复现 bug”计数。

## DSH-C01 · P1 · 恢复没有截断到本次请求结束，可能拿后一轮的终态和正文结算前一轮

**证据位置**：`services/dsh_host/sessions.mjs:471–498`；结算写库失败后内存收尾在 `377–395`；后续队列仍可调度的 `pump/execute` 在 `176–201`、`352–375`。

恢复先找到目标 `request_id` 对应的 `user/message`，随后执行 `remaining = events.slice(index)`，一直包含到会话末尾。最后一个 `turn/end`、最后一条 `assistant/message` 都从这个不限终点的后缀取。它没有依据下一条属于另一业务 Run 的请求做窗口隔离。

**静态可构造前提**：

1. A 已产生原生最终正文和 turn/end，但 `chat.settle` 写库 RPC 失败，数据库 A 仍为 running。
2. Host 的失败兜底给 A 一个内存 failed，释放执行槽位；同会话 B 随后正常执行并完成。
3. 重启后 Worker 将 A 作为未终态运行返回，原生日志同时含 A 与 B。
4. `recover(A)` 从 A 开始一直读到 B 的结束，可能把 B 的正文、completed 终态写入 A。即使 A 因缺附件最后变为 partial，正文归属仍然错误；也可能因 B 的后台命令把 A 错判 interrupted。

本次没有制造上述崩溃窗口，也没有运行任何请求；“可达控制流和取值来源”是静态确认，“发生频率、真实现场是否命中”未确认。

**最小修复方向**：以服务端持久的 Run 请求归属形成原生日志窗口，既有 `prepared.request_id = run.id` 可作为起点。Worker 同时提供该 Run 的 `steering_messages[].request_id`，允许同轮补充；遇到下一条属于其他 Run 的根请求才截断。子代理通知等没有根请求身份的原生事件不能随意当边界。边界无法证明时保守 interrupted，不把下一轮文本复制进当前运行，也不自动重发业务工具。

**必要回归**：A 结算写库失败+B 成功；A 失败+B 成功；A completed 后 B 尚在 turn/start；A 的多条 steer 不提前截断；同轮后台通知与父轮次反复结束；重复 recover 幂等。先用现有生命周期内存桩复现精确文本归属，再用隔离 SQLite+本机模型桩覆盖真实 Worker 的 A 未结算/B 已结算状态。

## DSH-C02 · P1 · 已保存为 failed 的外部“结果未知”回执不参加 completed 拦截

**证据位置**：`business_worker/tools.py:154–166,180–185`；`dispatch_state.py:15,119–146`；`chat.py:519–536`；`tasks.py:186–198`。

外部工具的 `tool_result_unconfirmed`/`operationOutcome=uncertain` 可以经 `finish_tool_call` 持久成 failed，`replay_guard` 也会阻止同一 run 同一副作用工具换调用 ID 后重做。但 `reconcile_dispatches()` 仅扫描 `UNFINISHED_CALLS`，一开始就跳过已 terminal 的 failed 回执。因此“尚未核实”与“是否需要把残留调用转成终态”被混在一起：未知操作一旦有了 failed 回执，结算时 `counts['unknown']` 反而为 0。

如果原生模型此时主动结束、该请求没有必需文件或文件数量已经齐，`tasks.verify` 不查失败回执，Run 可以落 completed。这与 `chat.settle` 对未落最终回执的未知派发采取 interrupted 不一致；防重复执行已存在，但不能据此宣称业务结果已经核实。

**边界**：不建议“只要曾有 failed 工具就禁止 completed”。明确参数校验失败后成功重试、某个不需要的只读探索失败，均可正常完成。本项只约束可信 provider/派发日志绑定的未核实副作用。`verified_alert_reply` 和 `verified_inventory_reply` 会保留技术失败状态，没有发现它们把此处 interrupted 强改 completed。

**最小修复方向**：把“收尾未终态调用”和“查找未解决的未知操作”拆开；结算前按已验证的 run/workspace/owner 内部回执读取所有可信 unknown/uncertain 记录，不受 `runs.view` 展示过滤。复用现有可信元数据/幂等键判定，不信任模型参数。无明确核实回执时不能 completed；新增核实接口以前只能保守 interrupted/partial 并保留真实结果说明，绝不为了完成自动重执行副作用。

**必要回归**：真实 Worker+隔离 SQLite+内存 provider 首次正常返回 uncertain 并落 failed，再 `chat.settle(completed)`；两类 provider HTTP/MCP、换 call-id 被 guard 阻断、没有任何必需附件、有附件已齐、普通参数失败后成功完成、无 `runs.view` 角色、重复结算。原记录应保持不变且 provider 派发总数不增加。

现有 `test_business_worker_dispatch_recovery.py:98` 覆盖的是调用仍为 prepared/dispatch_started 的崩溃收尾；`test_business_worker_replay_guard.py:106` 对已落库 unknown 明确传入 `chat.settle(..., 'failed', ...)`。两者没有验证上述 completed 反例。

## DSH-C03 · P1 · 复合交付只验类型/份数，原生交付不验证用户要求的 PPT 页数或 Word 嵌图关系

**证据位置**：`tasks.py:62–101,168–198`；`native_deliveries.py:71–112,135–147`；`artifact/service.py:78–86,134–147`；`common/artifact_fonts.py:50–140`。

自动合同只输出 `type/count`；显式 required_deliveries 也仅保留这两个字段。15 页不会保存成 15 页约束。`tasks.verify` 按 ready Artifact 类型数量验收，工件 metadata 的实际页数、请求页数、图表或嵌入关系均不参与判断。

原生 present 对真实路径、工作区归属、读写稳定性、大小和指纹做了检查，然后按扩展名建工件并标 ready。Office 字体规范化会读取 ZIP/XML，但不检查 PPT 是否满足请求的物理页数。因而一份**合法、可打开但只有 1 页**的 PPT，通过这条路径可以满足“制作 15 页 PPT”的 presentation:1。这里没有声称任意损坏文件都能通过：坏 ZIP/XML 可能已经在字体规范化阶段报错。

同理，原始复合请求中的“饼图”“把大屏放进 Word”没有对应关系约束。独立 Word 和已保存大屏都存在时，计数可能齐，但 Word 并未嵌入大屏；自动格式检测也没有将图片/图表纳入默认 patterns。此类内容、关系要求尚属**合同实现缺口**，不等于每次运行实际都遗漏。

**已核实的正确分支**：`presentation/draft.py:532–541` 会拒绝缺正文页；`builtin_provider.py` 的 finalize 和直接 render 具有各自页数/密度检查；Gateway `_error_result` 保存 `presentation_progress`。现有缺页提示不是丢失了草稿，写好的页也有专门回归。缺口是最终业务合同没有统一覆盖各条交付通道，不能只修单个 PPT 生成工具。

**最小修复方向**：先保留明确页数/范围，并复用现有 presentation 页数解析口径，避免再写第三套正则；在工件登记/验收时以实际 PPT 包解析所得页数核对，不能只信模型或工具自报 metadata。保留原生工具、业务工具两条生成路径，但交付核验共用事实。随后给复合成果建立稳定 requirement id 和来源/依赖关系，图表类型与 Word 嵌入的目标成果要可核实；未能证明的要求列 missing，不伪造“完成”。内容审美、论述质量不能仅靠结构计数保证，应另列验收范围。

**必要回归**：要求 15 页但 present 合法 1/14/15/16 页 PPT；页数区间、含封面/结尾页、两份不同主题 PPT、原生/分稿两条路径一致；缺正文 13 页时无新工件、补齐后真实 15 页；同路径换版本不能重复计数；Word 与 dashboard 独立存在但未嵌图应不满足关系约束；真实嵌入对应成果后通过；仅说明“如何做 PPT”不强行生成附件。

## DSH-C04 · P2 · 带内容的“继续”请求可能变成新任务，原任务草稿和剩余交付不再自动关联

**证据位置**：`tasks.py:125–156`；`chat.py:347–377`；`presentation/draft.py:39–116`；`apps/web/assets/platform/chat-session-view.js:271–273,307`。

无显式 task_id 时，continuing 只接受几种短语的整串匹配，modifying 只接受限定“改/调整/完善”等句式。例如上轮 PPT 未完成，用户发“继续把剩余的PPT写完”，既不符合短语 fullmatch，也没有 modifying 词，于是 candidates=[]，建立新 task_id。新 run 没有 `previous_task_run`，不会继承原任务资源；命名草稿又按 task 隔离，原 draft_id 不能直接用于新任务。

原草稿并未删除，原生会话历史也还在；准确后果是**业务任务归属和自动恢复关联丢失**，不能写成“文件数据被清空”。同会话模型记得旧文字并不能绕过合法的 task 归属检查。

当前前端发送仅在 `state.dshTask?.id` 已有值时带 task_id。对所查 `app.js` 和 `assets/platform/*.js` 的搜索只找到消费、展示、清空，没有找到为普通继续输入自动设置该值的路径，因此不能默认前端会替这个自然语言分支兜底。外部客户端明确携带 task_id 则不受此点影响。

**最小修复方向**：对唯一未完成当前任务保留明确的“继续现有任务”绑定，在发送协议中携带稳定 task_id；为自然语言明确继续的常见完整句式补保守识别，避免把所有新消息都绑旧任务。数据范围变化仍需按现有资源校验处理，不能为复用草稿跳过范围限制。

**必要回归**：短“继续”、长“继续把剩余的 PPT 写完”、英文明确 continue、带剩余页码、多任务歧义、真正新查询、显式 task_id；断言复用的 task_id、draft_id、已写页和 remaining deliveries 都正确。已有测试只覆盖短继续、具体修改页、A/B 选择和不相关新问题，没有覆盖此完整继续句式。

## 合同策略与一致性观察（不作为上述缺陷的运行复现）

1. **partial 是当前诚实结束方式，没有内置交付补齐回路。** Host 在原生 turn/end 后调用 verify，发现 missing 就返回 partial，不把缺项自动回灌原生代理再执行。只取消时长上限不会改变这个控制流。若产品要在模型先结束后仍自动补齐，需要明确 completion-check 工具或受权限/取消/未知副作用约束的继续机制；不能无条件重发工具，也不能承诺外部故障时必然成功。
2. **任务清单是当前轮重点，未必是永不缩小的项目清单。** `choose_task:137–142` 有意用明确新请求替换旧 required_deliveries；“继续制作 PPT”带显式 task_id 时可能不再要求此前剩下的 Word。是否累计未撤销要求，需确认产品合同；不能把任何需求收窄都判 bug。`native_todos` 当前属于计划投影，不是 mandatory requirement：pending todo 不单独阻止 completed。建议明确业务必需项与模型计划项的关系后再加强验收。
3. **恢复异常的 content/parts 不一致。** `chat.recover:674–686` 认证/配置恢复失败会把 Message.content 改成通用错误，但保留旧 text parts，再追加 error。主聊天渲染 `app.js:1137–1158` 优先展示 text parts，所以不能声称此时 UI 正文必然全部丢失；但是只读 content 的消费者与页面看到的内容会不同，需补隔离回归并统一规范字段。正常 settle 的取消正文保留和幂等分支已有测试，不是这个例外分支。

另：Word/PPT 缺失时 status/error 正确为 partial/delivery_incomplete，但模型正文可以仍说“全部做好”；仅 dashboard 缺失在 `chat.settle:551–557` 有强制首行纠偏。当前页面会追加明确错误，不等于完全隐藏失败；如要避免矛盾文字，通用交付差异说明可从缺项清单生成，而不是只凭模型最后一句。

## 既有回归实际覆盖与缺口

以下是源码断言核对，不是本次执行结果，不引用此前门禁总数冒充本次通过。

| 既有入口 | 已有断言的实际内容 | 对本次问题仍缺少的闭环 |
| --- | --- | --- |
| `tests/dsh/real_platform_smoke.py` | 真实 Host+Worker、临时平台、确定性本机模型桩；真实工件下载、重复请求、缺 Word→partial、队列编辑/移除、取消正文、终态后重启与历史延续 | 非正常中断的复合交付、已落库 uncertain 后模型 completed、A 未结算/B 已完成的恢复窗口、15 页和 Word 嵌图验收；toolerror 场景主要证明下一轮可继续，未断言失败那一轮的业务正确性 |
| `services/dsh_host/tests/native-host.test.mjs` | 真原生 DSH+模型桩+fake Worker；两步 compound 和 todo 全完成；前台/后台子代理、完成事件晚于子代理回执、提问/steer | fake Worker 不等于真实 Python `tasks.verify`；未证明多格式/PPT 页数和已落库 unknown 终态 |
| `lifecycle-recovery.test.mjs` | 后台恢复保守中断、同轮后台通知、后续 turn/start 未结束、原生日志缺失按派发证据决定恢复 | 不同 request 的跨 Run 日志切片；真实 Worker 持久状态与原生终态共同判断 |
| `event-order.test.mjs` / `projection.test.mjs` | 取消采用 Worker 正文、结算 RPC 失败有 failed/done、辅助事件失败仍尝试 done、失败尝试撤回、序列缺口不静默拼接 | RPC 失败后继续 B 再冷恢复 A；恢复异常时 content/parts 一致性 |
| `test_business_worker.py` | 必须有真实附件、短继续复用、具体改页复用草稿、取消/重复结算、队列编辑替换合同 | 长继续句式、页数合同、复合依赖、普通失败被纠正与未知副作用的差异终态 |
| `test_business_worker_dispatch_recovery.py` / `test_business_worker_replay_guard.py` | 崩溃前后派发事实、未终态调用阻止 completed、防新 call-id 重做未知操作、独立只读工具仍可用 | unknown 已终态为 failed 后再次 settle(completed) 的反例 |
| `test_presentation_partial_batches.py` / `test_presentation_request_pages.py` | 无效页不回滚有效页、缺 13 页带进度、补齐真正 15 页；多主题页数范围解析与显式参数优先 | 原生 present 和顶层业务合同同时验证这些约束；整轮模型主动结束时的自动补齐策略 |
| `test_dsh_multiple_presentation_drafts.py` | 同任务多个命名 PPT、草稿独立、跨 run 修改、不同 task 拒绝草稿引用 | 长继续误分新 task 的业务发送路径 |
| `test_dsh_native_deliveries.py` | 路径/属主/真实下载、工件复用、同路径修改 superseded 不重复算份数、批次回滚 | 合法 PPT 的页数/Word 嵌入内容验收 |
| `test_dsh_query_delivery_resume.py` | 同稳定 task 使用真实授权查询快照导出 Excel/PPT/图、大屏；真实文件检查；不重新查询；不相关来源拒绝 | task 被错误换新时的恢复体验；通用多目标语义完成 |
| `test_dsh_dashboard_delivery.py` | 大屏必须有可用报表和匹配快照、组件可展示、失效/删除状态、修改须新回执、todo/提问持久化 | 大屏最终被嵌到 Word 的关系；完成前未知外部工具状态 |

## 建议下一批顺序和未覆盖范围

优先先闭环 C01 和 C02：它们涉及正文串轮和未核实副作用误报成功，失败注入不需要真实模型或业务库。随后为 C03 建立跨交付通道的一致验收，先精确页数，再依赖关系；C04 独立补发送→稳定任务→草稿的真实隔离回归。现有正确行为要一并保留：普通参数失败可修复、已完成稿件可复用、纯说明不强制产文件、权限撤回停止新操作、未知副作用禁止自动重做。

本报告未检查全部上游 DSH 源码、所有 business 工具的业务语义、所有浏览器状态分支、真实 provider 在不同 finish_reason 下的行为、长时间资源消耗或崩溃注入结果；没有对用户历史会话或真实生成文档作任何现场重放。因此不能由本报告推出“所有长任务已修完”“所有模型结束均不正确”或“现有全部工具无失败”。

## 实际阅读文件与指纹

下表范围中的“通读”指本专项已阅读整个文件；“选段”不等于全文件审查。文件 SHA-256 在报告完成时从当前本机工作树计算，只证明对应快照，不代表未列出的依赖也已审查。

本表共 40 个文件。

| 文件 | 实际阅读范围 | SHA-256 |
| --- | --- | --- |
| `backend/run.py` | 通读；重点 host_command/main | `b1369597b35cea369683b30fa90c79b679d464a8437862fcbf6ae167f6d860ef` |
| `backend/start_server.ps1` | 选段：启动 run.py、已有 Host 检测、环境和清理 | `7411f8bcab6078fac629b717067191350748d0f846a86a7908e09295be41da97` |
| `services/dsh_host/host.mjs` | 选段：runStream 与 handle 聊天路由、直接 chat.execute 结果返回 | `e4372de59c56c143c54e5266ae1601c1fa8b7037ce0a9b1765f85ac28f4dd50b` |
| `services/dsh_host/sessions.mjs` | 核心生命周期选段：1–115、117–284、286–519 | `e12bb5f6b51db7cfeb2ccf3e706bbdb9027901c780861b3b21ad3d4eea5ccda3` |
| `services/dsh_host/native-jobs.mjs` | 通读 | `431b102287b5597500242c978f3d89015771f4112cb20ef73b9ea7f36c6fb877` |
| `services/dsh_host/projection.mjs` | 通读 | `692f7073e90581e495aa34a89774da1b5c700d6cef5bdb37983435e56741aa16` |
| `services/dsh_host/profile.mjs` | 通读；确认原生标准预设来源 | `e1cc1da6b8f30beafd3738c0997b412e9a63048da16a43bd27bcea30347d1c0a` |
| `backend/app/business_worker/chat.py` | 选段：202–299、301–437、452–652、662–719 | `472082b8e38b735f9e0616eadeea4e099ba1c8e3b56ab8c45d3a0fdb74eb4ef9` |
| `backend/app/business_worker/tools.py` | 通读 | `5530f66d710b693a26ccdfa920ad49f436d265a89b9f2384dccf31d63e83d46d` |
| `backend/app/business_worker/tasks.py` | 通读 | `147e449acd7a5daef30ad24f2d9b4edb8d203ee4ad5e48c5d29a3ad677d70455` |
| `backend/app/business_worker/dispatch_state.py` | 通读 | `94583bfc16b819835904fc798df63ce3dbbc1c97ce021d9773845a1f367b8dfd` |
| `backend/app/business_worker/native_deliveries.py` | 通读 | `30b11681140f8b0cec55637e1821e65a94bff1efd853d6d65fdd3201275d22bc` |
| `backend/app/business_worker/dashboard_deliveries.py` | 选段：1–65、150–215；未重做其数值/权限专项 | `59e3b65922ea87693892762ae960f4d9fd6ea898658d2cf74c3b9ab34e57bcc0` |
| `backend/app/modules/artifact/service.py` | 选段：75–155；类型和 ready 工件持久化 | `990c63f8357b1175f79c1af9f8f6ab03b0281b3884beb3786a1a0ef99aa8a4b6` |
| `backend/app/common/artifact_fonts.py` | 选段：50–140；Office ZIP/XML 字体规范化 | `069353e234cf049b587e8260c9671cf8fb4c0dac04f40efb029356680142ed67` |
| `backend/app/modules/presentation/draft.py` | 选段：1–116、430–482、524–565；其他函数仅 rg 定位 | `029871420a45a6d4a65b4f02a756fc920eeea5a43b4c76a90d00c96d980b1ef6` |
| `backend/app/providers/tool/builtin_provider.py` | 选段：1011–1076、1089–1177；只核对 PPT 调用链 | `c5986e694da9a624b5587a387b4eb0ef45e0aec746e33b99a0340cd6daed9ef0` |
| `backend/app/providers/tool/gateway.py` | 选段：491–538；异常与 presentation_progress 回执 | `f48bbb7f591d68e9510dd1938477e2586aed41f26a2b34c287286de788659219` |
| `backend/app/modules/business_ai/alert_evidence.py` | 选段：112–168；结算状态保持 | `890da7b2fc350e72828d75b4276a457b2db6ecc72d20db74ae5c4cb1a346599f` |
| `backend/app/modules/business_ai/inventory_evidence.py` | 选段：76–122；结算状态保持 | `29b1ee63fe192cfdf085b23ab8a67e273bf5ac195bcecf548020ab0c780418e6` |
| `backend/app/modules/runtime/engine.py` | 通读；兼容引擎边界 | `407f2cd8c010b62a09ca0304753cdf5da321e3482413ee83260204e1264777ea` |
| `backend/app/modules/runtime/agent.py` | 仅 1–25 行历史说明与定位，不审查整个旧 Agent | `2b7dfbe8036556beee507b4e5d029ad5772dc9e24a44b75e756d1b39d5f0f5e7` |
| `apps/web/assets/platform/chat-session.js` | 选段：1–230、259–317；状态、正文和发送 | `26c786d4d71c517e7e7d9271e8d33accc7c42d58ddd76a0fd05fc18d0886c306` |
| `apps/web/assets/platform/chat-session-view.js` | 选段：1–190、232–315；状态、计划展示、task_id 发送 | `d354fe18baebdabb5d4044b3938556304ce227e87e14f20cea2917600cb5551f` |
| `apps/web/app.js` | 选段：865–929、1116–1165、task 相关检索；不是全文件审查 | `3894a428af0327b970763e6e626b032659b19e0b727fbc588dfbd4e538c166b0` |
| `services/dsh_host/tests/lifecycle-recovery.test.mjs` | 通读 | `1984534a29b2330300dfa393a02d12d7809febc16ae10337172ea6e0c71aa38a` |
| `services/dsh_host/tests/event-order.test.mjs` | 选段：43–59、99–127；其余测试名称定位 | `adb79767e3dd0300ff41642b0cfd3e49fe7925a9bb7b724b24d99e6347312bf3` |
| `services/dsh_host/tests/projection.test.mjs` | 通读 | `d850709cc3ff4026f76c4fccb284b70f9752b700448c6f41b725d4295233497e` |
| `services/dsh_host/tests/native-host.test.mjs` | 选段：1–53、218–289；其余仅相关定位 | `09a01e8debd5d115f4e7b1c514e2e2f1b9819483ac4c1c5da51934e16063c047` |
| `tests/dsh/real_platform_smoke.py` | 选段：20–102、270–459；本次未执行 | `22b02809d505fa3301e768587a72c411395f2a6afc3059f913135825f3c0b318` |
| `tests/backend/test_business_worker.py` | 选段：118–172、199–221；其余相关测试名称定位 | `97e6dc5a23313d23dab3c11ddfd8ef56d7ed72f3ae1d0c1a2eeaff730019da5d` |
| `tests/backend/test_business_worker_dispatch_recovery.py` | 选段：1–170；未执行 | `d1851b6a10e7ae04d54550f71cbc961055811529c7e463774a7a73503310a922` |
| `tests/backend/test_business_worker_replay_guard.py` | 通读；未执行 | `62cd9434f48a166ee795ad95a080a20c3c036a24097706894ffd38a4828e6e28` |
| `tests/backend/test_dsh_native_deliveries.py` | 选段：1–58、121–165；其余用例名称定位 | `3b5fbd96d6e9fe6547fd69d47dd2adacf38fc79353613065b6289a8a0981c3a0` |
| `tests/backend/test_task_clarification_delivery.py` | 通读；未执行 | `67b16cf8dec5ee1fc77c7328da9dddb7882d8b9057a45c9e695f2bb2d53879cb` |
| `tests/backend/test_presentation_partial_batches.py` | 通读；未执行 | `c25be8287c0d7e612c5962cc546f46924607a11b67648fe38f5f20b44b7db44a` |
| `tests/backend/test_presentation_request_pages.py` | 本次只定位测试名和对应关键断言；未执行 | `78c60b25b568284c73d4ad4ca17975a5819e83a62c99cb40c56ba687f01c0cb9` |
| `tests/backend/test_dsh_multiple_presentation_drafts.py` | 通读；未执行 | `28217d5df1bd00e9001f821e6fd19f210f455b6d9bd9a4db9a9a5e4335380eb6` |
| `tests/backend/test_dsh_query_delivery_resume.py` | 通读；未执行 | `6569bf9bd705679a1344bb0a30170146e4fd8cf26eb4c8ffe274498ecce7376b` |
| `tests/backend/test_dsh_dashboard_delivery.py` | 选段：55–104、124–159；其他测试名称定位；未执行 | `87f11b638f125aa0e6d9ded4403d9e41aad9d5b250a80e834e45557393d17993` |
