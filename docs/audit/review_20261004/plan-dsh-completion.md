# DSH C01 / C02 下一批失败注入实施计划

日期：2026-10-04。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-capability-contracts`。

本计划承接 `review-dsh-completion-next.md`，当前仅准备实施方案。产品源码与 tests 继续冻结；没有运行下列命令、没有执行测试、模型、数据库或 HTTP 请求。下述“红断言”是下一批应当在修复前执行并保存证据的断言，不是本次已经取得的失败结果。只新增本 ignored 文档，不修改正式记录或其他人的改动。

## 目标与不变量

- **C01**：一个业务 Run 冷恢复时，只能采用属于自己的原生请求窗口中的终态和正文。不能用同会话下一 Run 的结果结算上一 Run，即使两个 Run 属于同一个长期业务 task。
- **C02**：可信外部副作用仍未知时，原生 completed 不能升级成业务 completed；已修正的普通参数错误不因此污染整轮。
- 不通过恢复重发可能已经执行的操作；不伪造原生成功终态；不覆盖持久原始工具回执；不通过撤销 `runs.view` 隐藏内部执行事实。
- 用户取消、现有资源/客户端授权校验、原生子代理等待、后台 Job 收尾、正常短继续和 steer 仍沿用既有行为。
- 这里不实施新的跨 Run 精确一次保证，也不新增远端“核实后放行”接口。原 run 的 unknown 不能凭下一轮一个普通 success 就自动解除。

## 可复用的现有夹具

| 入口 | 可直接复用 | 本批需要补的最小能力 |
| --- | --- | --- |
| `services/dsh_host/tests/lifecycle-recovery.test.mjs` | `recover(events)`、`message()`、`turnEnd`；真实 `SessionHost.recover`，Worker/原生日志内存桩 | helper 接受多个 Run、完整事件数组、服务端恢复请求归属；返回按 run_id 索引的结果，不再只断言第一个结果 |
| `services/dsh_host/tests/event-order.test.mjs:99–112` | 结算 RPC 失败的真实 `SessionHost.settle` 与 published/settled 捕获 | 将一次结算失败后的 A 持久未终态与 B 成功串成 recovery 用例；不能仅断言发过 failed/done |
| `tests/backend/test_business_worker.py:21–41` | `worker_context`、`prepare`、`request`；真实身份、消息、Run、工具落库与临时 SQLite | C01 的服务器请求归属字段测试、C02 completed 结算断言 |
| `tests/backend/test_dsh_steering.py` | `submit` 和真实 `chat.steer`，已验证同 Run、多用户消息、稳定幂等标识和资源限制 | `chat.recover` 必须返回已持久 steer 请求身份；重复提交不多一条，同用户输入不能伪造恢复边界 |
| `tests/backend/test_business_worker_dispatch_recovery.py` | `dispatch_fixture`、`SimulatedCrash`、`invoke`、`saved`；真实网关校验与派发日志，最终 provider 为内存桩 | provider 正常返回可信 uncertain 的模式；HTTP/MCP 参数化；普通 validation 失败→success 对照；completed 终态后再调用不派发 |
| `tests/backend/test_business_worker_replay_guard.py` | `uncertain_result` 及同 run 新 call-id/改参数仍只派发一次 | 不再手动 settle(failed)掩盖终态缺口，显式调用 settle(completed)并检查规范状态 |
| `tests/backend/test_tool_replay_authorization.py:22–56,221–245` | `replay_actor` 真实受限角色与无 `runs.view` 防重验证 | 新结算检查沿相同内部归属读取回执，撤查看权限后仍不能完成；禁止执行器/解密/请求的断言保留 |
| `tests/dsh/real_platform_smoke.py:126–256` | `Host` 生命周期、独立目录/SQLite、本机随机端口模型桩、网络守卫、登录/发送/历史/清理 | 独立窄 smoke 文件中增加一次性结算前转发故障；固定 A/B 最终文本和模型请求计数；不修改现有大 smoke 的业务断言来迁就失败 |

初始建议新增 `services/dsh_host/tests/recovery-request-window.test.mjs`、`tests/backend/test_dsh_completion_contract.py`，避免把几种不同故障注入混入现有大型夹具。已有夹具可以导入或小范围提取，但保持旧测试的检查条件。真实 Host 闭环可新增 `tests/dsh/completion_recovery_smoke.py`；是否纳入同批，由核心红绿后再决定，不靠新框架或安装依赖。

## C01：先取得确定性红证据

### 第一层：原生日志窗口纯内存复现

对真实 `SessionHost.recover` 注入一个会话中的有序日志，给每条事件固定递增 seq；不要仅用没有 kind/seq 的宽松 mock。A 的根请求用 `A.run_id`，浏览器客户端请求标识另用 `client-A`，防止测试把两者混淆。

最小日志：

```text
user/message(source.kind=user, rpcId=run-A)
turn/start(A)
assistant/message("仅属于A的最终结果")
turn/end(reason=completed)
user/message(source.kind=user, rpcId=run-B)
turn/start(B)
assistant/message("仅属于B的最终结果")
turn/end(reason=completed)
```

Worker `chat.recover` 只返回尚未落库终态的 A，B 已是 completed。现代码预计取 B 正文，以下关键断言应失败：

```js
assert.equal(resultA.content, '仅属于A的最终结果');
assert.ok(!resultA.content.includes('仅属于B'));
assert.equal(resultA.status, 'completed');
assert.equal(admitted.length, 0);
assert.equal(promptCalls, 0);
assert.equal(toolExecuteCalls, 0);
```

`status` 要做独立反例，而不是只测两个 completed：A aborted + B completed 时 A 应 cancelled；A 有未完成 turn/start + B completed 时 A 应 interrupted；A completed + B 后续 bash/后台 subagent 时，B 的后台工具不得污染 A 的状态。修复前预计会得到 B 的 completed 或把 A 错误降为 interrupted。保留实际差异，不预先把所有情况都写成已红。

### 请求归属和正常续写边界

已查到上游本地 `packages/api/session-controller/src/commands.ts:339–343` 将用户提示源写成 `{kind:'user', rpcId: request.requestId}`；`types.ts:401` 声明相同形状。当前 Host 普通 prompt 使用 `prepared.request_id`，Worker 设为服务端 `run.id`；steer 则使用已持久 `steering_messages[].request_id`，与根请求 ID 不同。

下一批实现建议由 Worker 在 recover 的私有回执中追加服务器生成的最小身份结构，例如：

```text
recovery_identity = {
  root_request_id: run.id,
  steering_request_ids: 本Run持久并accepted的补充请求ID,
  // 如实现需要，追加同会话/工作区/所有者的已知根请求归属。
}
```

不从用户 payload 接受这份结构；不携带明文密钥、业务内容或无关会话标识。若需要核对其他根请求，只查同 workspace/user/conversation 的轻量标识，不能借 30 条历史展示窗口决定恢复归属，也不能因 `runs.view` 撤销而漏掉内部 Run。

Host 从 A 根请求开始，在**下一条被证明属于其他 Run 的用户根请求之前**截断。当前 Run 的 steer、重试、压缩、后台通知、模型自发的后续 turn/start/turn/end 都留在当前窗口。无法证明归属的用户 rpcId、缺根请求、ID 冲突不得当成 A 的确定性正文来源；保守 interrupted，保留 A 已持久正文，不自动重放。

必须覆盖以下保护矩阵：

| 场景 | 预期边界和结果 |
| --- | --- |
| A 根请求→A 已持久 steer-1→steer-2→A 最终答案→B | A 使用 steer 后最终答案，不能在 steer-1 提前截断；B 文本不进入 A |
| 重复提交同一 steer ID | Worker 仍只有一条补充记录；恢复 ID 列表不膨胀；不重复 prompt |
| A 一次请求触发多轮工具调用/重试/压缩，没有新根请求 | 采用同窗口最后已提交正文和正确终态，不能在第一个 turn/end 或 assistant/message 停止 |
| A 原生后台通知唤醒父代理 | 通知 source 不是新用户根请求，留在 A；现有后台无完整业务回执→interrupted 策略保留 |
| “继续”形成 B，task_id 与 A 相同 | 仍是两个 Run、两个恢复窗口；只共享任务交付，不能合并最终正文 |
| A/B 浏览器 client_request_id 各不同于 run_id | 按原生真实 rpcId=run_id 定位，不能误用 client_request_id |
| 多条 A/B 都未结算 | 每个 Run 独立窗口，恢复顺序不影响其他 Run 的文本和状态 |
| 归属字段缺失的旧快照 | 可从服务端既有 run.id/steering_messages 重建；无法证明则保守中断；不伪造历史关联 |
| 用户 rpcId 与另一 Run 根 ID 冲突/未知 | 明确不确定，不因 Set 去重悄悄归入 A，也不把非 A 正文写入 A |
| 重复 recover、已 dsh_settled | 规范正文和状态保持一致；没有额外模型/工具调用 |

### 第二层：真实 Host/Worker/SQLite 的故障窗口

复用 `Host` 的随机端口、清洁环境、独立 storage 和双语言网络守卫。新增**只存在 tests 的 Node preload**，包装 `BusinessWorker.prototype.request`：仅在本轮隔离目录、明确选定的 A `chat.settle` 第一次请求、**调用原方法之前**抛一次可辨认的夹具错误，其余全部透传。不加产品故障开关，不杀整个 Worker，不用 timeout 等待制造竞态，不改数据库假装已有失败。

执行顺序在下一批测试中固定为：

1. 本机模型桩对 A 返回唯一文本 A_FINAL；等待 A 的 DSH_SETTLE_FAILED/done。
2. 通过本次独立 SQLite 或历史断言 A 的持久 Run 仍为 running、原生日志有 A 的 turn/end。故障日志证明 A 的 settle 没有转发；不能只凭前端 failed 推断。
3. 发送 B，桩返回 B_FINAL，确认 B 的业务 completed 和持久正文。
4. 停止并重启**本轮创建**的 Host，同一临时 SQLite/原生日志，恢复阶段禁用故障注入。
5. 从重启后的最新历史按 run_id 精确取 A/B，断言 A_FINAL/B_FINAL 各自不串；A 得到自身的可恢复 completed，B 不变。记录重启前后模型桩请求总数，恢复新增模型和工具调用均为 0。

不能直接用旧 `Host.settled(A)` 的第一条 done 作为重启结果：它可能返回故障前已经保存的旧 done。应记录重启前 cursor，读取新事件或查询规范历史，再检查 terminal run/message。证据包括故障注入命中一次、两次运行标识、前后持久状态、A/B 正文、原生日志 seq 窗口、模型/工具计数及本轮进程清理。

## C02：工具调用正常返回 unknown 后主动完成的红断言

### 真实 Worker + SQLite 的最小反例

复用 `dispatch_fixture` 的真实 ToolGateway、`record_tool_call`、派发回调与提交，只替换最终 provider。增加一个普通返回模式，而非 `BaseException` 崩溃模式：

```python
context['_before_tool_dispatch'](provider=provider, external_side_effect=True)
calls.append((context['run_id'], context['idempotency_key'], arguments))
return ToolResult(**uncertain_result(
    key=context['idempotency_key'], provider=provider,
    error_summary='夹具：原操作可能已执行，最终结果待核实',
))
```

HTTP 使用 POST 配置，MCP 使用自己的 provider 名；不把 HTTP GET 人为装成写操作。`tools.execute` 返回后先断言真实 ToolCall 已为 failed，并保存 result_json 深拷贝，再调用 `chat.settle(run_id, 'completed', '模型声称已经完成')`。

关键红断言：

```python
assert receipt['native_status'] == 'completed'
assert receipt['status'] == 'interrupted'
assert receipt['error']['code'] == 'tool_result_unconfirmed'
assert persisted_run.status == persisted_message.status == 'interrupted'
assert persisted_run.context_snapshot['business_task']['status'] == 'interrupted'
assert saved_call.result_json == original_receipt
assert len(calls) == 1
```

目前预计业务 status 会是 completed。不得把测试改成主动传 failed，也不得只断言原 tool status failed 来绕开核心缺陷。

再增加“required_deliveries 为空”和“要求的真实 Word 已生成、数量齐”两组；后者确保不会恰好被附件缺失 partial 遮住未知操作问题。Word 使用现有 `render_document` 临时工件，不用虚假 metadata 假装文件存在。处于 failed/cancelled/interrupted 的原生结果仍保留原终态，不无条件覆盖成另一个失败。

### 普通失败与未知操作的差异矩阵

| 场景 | 预期业务终态/副作用次数 |
| --- | --- |
| 参数 schema 失败、未过派发边界→修正参数成功→模型 completed | completed；副作用只发生在成功调用，计数 1 |
| 普通本地渲染错误→修正成功，所有必需附件齐 | completed；历史 failed 仍保留；不能“曾失败即永远失败” |
| 外部 provider 可信 uncertain 已落 failed→模型 completed | interrupted + tool_result_unconfirmed；发送 1 次 |
| unknown 后换 call-id、序号或参数再调同工具 | 被 replay_guard 拦，仍发送 1 次；随后 completed 仍被拒绝 |
| unknown 后调用独立 current_datetime | 独立只读可成功；未知操作没有因此解决，结算仍 interrupted |
| unknown 后工具被禁用/版本变更/角色丢 runs.view | 当前工具可用性不能抹去已发生的未知事实；内部原始回执仍阻止 completed；不返回旧私有业务数据 |
| 相同持久调用重放成功回执 | 有效授权时复用回执，发送仍为 1 次，正常 completed；保持既有缓存授权检查 |
| arguments_json 伪造 operationOutcome、普通错误码/未绑定幂等键 | 不当成可信外部未知证据；继续原错误/交付策略，不获得重发许可或解除已有真实 unknown |
| 只有 replayBlocked 的派生拒绝回执 | 不当成新的外部操作证据；真正原调用仍决定未知状态；不因重复阻断产生多次派发事实 |
| 不同 Run 或工作区存在 unknown | 不能让无关记录阻断本 Run；固定 run/workspace/owner 范围 |
| settle 后 stale 新调用、重复 completed 结算、冷恢复 | 不能新派发；第一次规范终态/正文保留；原未知回执不被改写为 success |

“普通只读错误尚未解决时是否要求 partial”属于更宽的业务合同，本批不通过所有 failed 一刀切实现。数据库异常停止策略保持现状，不在这里重写 SQL 取消或连接池行为。

### 最小代码改动设计（下一批才实施）

1. `reconcile_dispatches` 继续只负责把 prepared/dispatch_started/pending/running 收尾；不要把它改成覆盖所有 failed 记录，也不抹掉原 `error_code`/派发日志。
2. 新增纯“是否存在可信未核实操作”判断，或从 `replay_guard` 提取共享纯判定，供防重与终态复用；判断以持久 ToolCall/幂等键/服务端 provider 契约为输入，不把模型参数当证据。
3. 回执读取沿现有 `_calls` 或 `_saved_tool_calls` 的固定 run/workspace/started_by 内部 Core 查询。不依赖当前 list_available、连接器最新配置或 `runs.view`，否则禁用工具或撤查看权限会把过去未知操作隐藏。
4. 在 `chat.settle` 现有 tool/events 锁内、收尾调用后、`tasks.verify` 前判断 unresolved。请求 completed 且存在真实未核实操作时使用 interrupted/tool_result_unconfirmed，保留 native_status=completed；ready 附件和局部正文仍保存，不能伪称全部完成。
5. 不保存自由业务 payload 到终态错误，不把旧回执 data/artifacts 复制进新错误；最小记录原操作状态及内部可追溯标识。原结果由各自授权接口访问。
6. 没有明确远端核实能力时不加“自动解除 unknown”的快捷分支。普通重试成功、模型一句“已核实”、修改参数、另一工具成功都不构成核实证据。以后若做核实接口，需要单独权限、可验证来源和指向原幂等键的回执，另批设计。

可选真实 Host 闭环：本机 POST 桩读完请求后断开连接，真实 HTTP provider 构造 unknown；模型桩尝试第二 call-id 然后声称完成，断言 POST 收到次数为 1、规范 run 为 interrupted。此项需要先验证既有 HTTP 目标策略对本轮回环桩的明确许可；不能为跑用例关闭 SSRF 或权限检查，更不能回退真实接口。

## 后续执行顺序、预算和证据

1. 先只新增最小 C01 日志窗口红测、C02 正常返回 uncertain 的红测；保存修复前源码 hash、断言差异和日志。两条核心红断言没有真实失败证据之前，不把所有补充矩阵数量写成“复现数”。
2. 最小修复后跑新专项、上述相邻回归；正常模式回归先通过再做真实 Host/Worker 的一次性故障闭环。只用临时 SQLite、内存 provider 和本轮回环桩，禁止业务 MySQL/远程库/真实模型/8011/8012。
3. 每个 smoke 场景只运行 1 次，模型桩设小总请求预算（例如 A/B 恢复最多 4 次，unknown+阻断+结论最多 6 次），超出即失败并回收本轮实例；不提高超时掩盖恢复挂起，不自动无限重跑。
4. 所有清理只针对测试创建的目录、端口和进程树；保留红绿证据，故障钩子应恰好命中一次；清理失败单独记录，不吞掉主断言。
5. 通过后向根代理报告实际计数与稳定文件清单，由根代理决定统一门禁/构建/提交。当前门禁结果不计入尚未执行的新专项。

候选命令（仅供下一批执行，**本次未运行**；cwd 固定独立树）：

```powershell
node --test services/dsh_host/tests/recovery-request-window.test.mjs services/dsh_host/tests/lifecycle-recovery.test.mjs services/dsh_host/tests/event-order.test.mjs services/dsh_host/tests/projection.test.mjs
& 'E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe' tests/run_isolated_backend.py tests/backend/test_dsh_completion_contract.py tests/backend/test_business_worker_dispatch_recovery.py tests/backend/test_business_worker_replay_guard.py tests/backend/test_tool_replay_authorization.py tests/backend/test_dsh_steering.py tests/backend/test_business_worker.py
```

真实 Host 新 smoke 文件的 CLI 应在实现后按实际参数登记，不在计划阶段编造不存在的启动命令。

## 本计划补读范围

在前一报告范围基础上，本次补读了：`test_business_worker.py:1–118`、`replay_guard.py` 全文、`side_effects.py` 全文、`test_business_worker_dispatch_recovery.py` 全文、`tools.py:1–85`、`worker.mjs:1–109`、`run_isolated_backend.py:1–100`、`test_dsh_steering.py` 全文、`test_tool_replay_authorization.py:22–56,221–245`、`business_worker_fakes.py` 全文、`real_platform_smoke.py:103–276`；并核对本机上游 `session-controller/src/commands.ts:328–349` 和 `types.ts:394–407` 的真实请求源形状。没有读取密钥配置，没有加载应用或执行这些模块。
