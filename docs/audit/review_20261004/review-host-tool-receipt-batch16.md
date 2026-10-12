# 批次十六 Host 工具回执修复独立只读复核

2026-10-04。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-delivery-acceptance`。本轮只读产品、测试、依赖源码和现有日志，仅新增本报告；没有运行测试、模型、数据库、Host、浏览器或构建，没有改动图表资产、Host 源码及其他代理负责的 HTTP 导出文件。

## 结论

主修复正确：`receivedResult` 在收到对象回执后、发布 `tool.completed` 前置为真，可以阻止业务失败经过原生抛错后又发布无结构 `tool.failed`，从而避免覆盖 PPT 缺页进度等原始结构。原生失败、取消后的失败传播及无回执传输异常的失败分支仍在。

有一处 P2 边界需要处理或明确收窄承诺：Worker 存在未落存的结构化提前返回，不能将“已经收到对象回执”一律视为“业务回执已经持久化”。这类返回遇到持久化前的事件发布失败时，当前测试不能证明结构仍可从历史恢复。下面给出完整静态路径。正常执行已落存的 PPT 缺页路径不受此发现否定。

## P2：未落存的 Worker 提前返回遇到发布失败，可能没有任何最终业务工具回执

位置：[sessions.mjs:317](E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-delivery-acceptance/services/dsh_host/sessions.mjs:317)、[business-tool-receipt.test.mjs:33](E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-delivery-acceptance/services/dsh_host/tests/business-tool-receipt.test.mjs:33)，以及只读查阅的 `backend/app/business_worker/tools.py:84、103、109、111、113`。

首次复核版本的 `sessions.mjs:318` 注释称“业务回执已经落存”（后续已修正为“已取得结构化业务回执”，见报告末尾增补），但 `tools.execute` 的以下分支在统一 `finish_tool_call` / `message.parts_json` / `db.commit`（`tools.py:184–194`）之前返回：

- 新工具执行时运行已结算：`run_already_settled`（84 行）。
- 当前工具不可用：`tool_not_available`（103 行）。
- 已保存调用重新鉴权被拒绝的 `denied`（109 行），此时保存的是原回执，不一定是本次拒绝回执。
- 先前未派发或结果未知：`tool_not_dispatched` / `operation_unknown`（111、113 行）。

具体触发顺序可以是：`tool.started` 成功写入 running part → Worker 在工具可用性复核时返回 `tool_not_available`、未写最终 part → Host 设置 `receivedResult=true` → `tool.completed` 的 `chat.event` 在提交前抛错 → catch 跳过 `tool.failed` → finally 删除调用身份。原生 ToolRuntime 随后仍返回 `isError=true`，但其错误是发布异常；`follow` 的业务工具过滤不会再为该工具补一份原生结果（`sessions.mjs:351–359`）。后续运行结算不等同于工具结果补存，因此不能据此保证历史中的该工具 part 已从 running 收口，或错误原码/metadata 已保存。

这不是对 RPC 故障的假设性归因：`receivedResult` 与上述提前返回的控制流在当前源码中直接成立。静态复核未运行该故障组合，因此没有声称已观察到其浏览器表现。

后续关闭此边界的方向是让这些结构化提前返回也经过可信的回执持久化出口，或为“尚未持久化但已有结构回执”保留一个携带原始结构的收口路径；不能直接恢复原先无结构 `tool.failed` 覆盖。本批已经修正注释并收窄发布失败保证，未改变上述运行路径。

首次复核的测试 `host.publish` 先把事件放入 `events` 数组再抛异常（当时测试 33–36 行），且发布失败只用正常执行应已持久化的 `missingPage`（当时84–90行）。所以它证明的是“发布调用后不再尝试无结构覆盖”，不证明真实 `chat.event` 持久化前失败时有可恢复历史。主代理后续已增加发布前失败负边界例，证明该异常仍失败和清理，并未将其解释为结果已经持久化；详见最终49项增补。

## 已确认保持的语义

| 路径 | 当前代码行为 | 静态依据与现有测试 |
| --- | --- | --- |
| `status=failed/cancelled` 或旧 `isError=true` | 先以原 result 和相应 status 发布一次 `tool.completed`，随后仍抛出 JSON 错误；不会变成原生成功 | `sessions.mjs:319–327`；测试 48–66 行四种回执 |
| 成功结果 | 原样返回，未引入字符串再解析或数值转换 | `sessions.mjs:324`；测试 69–74 行使用精确大整数文本 |
| Worker 传输请求拒绝 | 尚未置位 receivedResult，仍发布 `tool.failed` 并继续抛出；正常发布时保留传输异常 message | `sessions.mjs:315、326–327`；测试 76–82 行 |
| null/数组/字符串回执 | 在 receivedResult 置位前明确失败，不误报成功 | `sessions.mjs:317`；测试 102–109 行三例 |
| 已收到有效结果后发布抛错 | 不再发无结构失败；发布异常继续进入真实 ToolRuntime 失败分支 | `sessions.mjs:320、326–327`；测试 84–90 行。持久化保证受上述 P2 边界限制 |
| 发布完成后用户取消 | 原业务结果仍留在完成回执中；`throwIfAborted` 使原生工具返回失败，取消监听请求 `run.cancel` 一次 | `sessions.mjs:311、322`；测试 92–100 行 |
| 进入 try 后的清理 | 成功、结构失败、格式错误、传输异常、发布异常和随后取消均移除 abort 监听与调用身份 | `sessions.mjs:328`；多数新增测试断言 invocations 归零 |

这里 `tool.completed` 是工具执行返回这一事件名称，不表示业务一定成功；决定业务状态的仍是 result.status / isError。只读核对了 `backend/app/business_worker/chat.py:473–487`：原始问题来自第二个 `tool.failed` 没有 result 时合成简化错误，并按相同 call_id 替换已有 tool_result；本次 guard 确实消除了这一覆盖。

原生失败语义并非仅来自模拟 runtime：测试加载本地真实 `packages/core/tools`。只读核对其 `src/index.ts:1558–1590、1907–1915`，tool.execute 的 throw 被转换为 `toolErrorResult`，输出 `isError:true` 及错误正文；因此保留 `throw error` 是必要的，当前仍保留。

## 非本次新增的问题与测试边界

1. `sessions.mjs:326` 仍先 await 失败事件，再 throw 原错误。如果 Worker 传输已失败、随后 `tool.failed` 发布也失败，第二个异常会遮住第一个传输异常。这是修前已有行为，本补丁未新增；不会变成成功，但无法保证原始传输错误还在最终 `native.error`。现有传输测试只模拟单故障，且未断言原生 error.message。建议单独补双故障的原因链保留用例，不将这一点记作本次回执去覆盖修复的新增回归。
2. 新格式校验仅保证非空对象且非数组，没有保证完整 ToolResult schema。`{}` 或 `{error_summary: 'x'}` 仍沿兼容分支成为 success。Worker 当前正常返回契约具有 status，本次也没有引入这条兼容默认，因此仅记录校验范围，不能宣称已经全面拒绝所有畸形对象。
3. 新取消例触发点是 `tool.completed` 回调内部，未覆盖 Worker 尚在等待时取消、发布前取消、`run.cancel` 自身传输失败，也未运行完整 SessionHost.cancel → execute → settle 生命周期。已有 37 项中有独立结算/取消相关检查，但不能将分开的检查当成新增组合路径的端到端验收。
4. `tool.started` 位于 try/finally 之前；它失败时不会经过本段 finally 清理调用身份。这也是原有路径，补丁未改动，新增测试未覆盖；本次不据此扩大修改范围。

## 查阅的既有红绿证据与摘要

只读查看主代理生成的 `.implementation/host-tool-receipt-before.log`：1 例失败，实际事件为 `tool.started → tool.completed → tool.failed`，期望只有前两项。该失败与代码覆盖路径吻合。

只读查看 `.implementation/host-tool-receipt-after.log`：48 项通过、0 失败，其中新增 11 项，既有 37 项。日志中的模拟结算/事件失败栈对应通过的故障注入测试，不是本代理新执行结果。**本代理本轮没有重新运行任何测试。**

| 文件 | 首次复核读取 SHA256（48项阶段，保留历史） |
| --- | --- |
| `services/dsh_host/sessions.mjs` | `679eecb78c65b3d8f0d408152e9ee3c48aeb3826ec61e44c7ad1440842c7a810` |
| `services/dsh_host/tests/business-tool-receipt.test.mjs` | `9a39e093b04e1dd0218efd73771e80d92ad47bb72f153cdc91182e97ee6a6549` |
| `.implementation/host-tool-receipt-before.log` | `8db8c17ff3cd1ac7c7e34b6fe368ebee135207eae9a5c8373d8ef11ecc083442` |
| `.implementation/host-tool-receipt-after.log` | `3bcb6265b5fa870281d2493af5a71b216a294a5adebc44d59f335667ccd07e18` |

图表源码和已冻结构建资产本轮未改动。HTTP 历史导出及复合浏览器后续复测由对应代理继续负责。

## 主代理回应与明确未关闭边界

主代理已接受上述持久化前提不成立，已将注释改为“已取得结构化业务回执”，正式交付仅承诺“不二次覆盖”，不承诺所有发布失败都可从平台恢复。**提前返回 + 事件首次持久化前失败仍是本批明确未关闭边界**；本报告不要求为此引入无界事件重试，也不把正常已持久化 PPT 回执的去覆盖修复判为无效。

首次报告完成时尚未读取补充实现/日志，所以上表 SHA 和48绿只记录此前审阅版本。主代理随后完成补例，并通过 followup 要求只读增补；下面记录实际再次读取到的内容，不改写48项这一步的历史。

## 最终49项日志的只读增补

本次增补实际读取范围限于 `sessions.mjs` 的当前 Git diff、完整 `business-tool-receipt.test.mjs`、完整 `.implementation/host-tool-receipt-final.log`、本报告既有尾部，并读取这三个外部文件的 SHA256。没有重新执行测试，也没有重新检查或修改 Worker、图表、HTTP导出、其他产品及测试文件；唯一写入是本报告。

相对首次复核内容，产品去覆盖逻辑不变，仅 `sessions.mjs:318` 的中文注释由“业务回执已经落存”改为“已取得结构化业务回执”。测试夹具在第34行新增 `publishBeforeFailure`，在 `events.push` 之前抛错；第103行新增“提前返回回执首次发布前失败仍向原生传播，不能冒充已持久化”用例，使用 `tool_not_available` 结构回执，断言真实 ToolRuntime 返回 `isError=true`、可记录事件只有 `tool.started`、`tool.execute` 请求一次、调用身份已清理。

此新增例明确记录本批负边界，不是假装补存成功：它不运行真实 Worker 提前返回分支或真实 `chat.event` 数据库提交，也不保证平台历史恢复。因此 P2 未关闭边界保留；双故障遮住原传输错误的问题继续单列为原有行为。

最终日志实际记录 **49/49 通过、0失败、0取消、0跳过，303.7043ms**，其中 **12项新增 + 37项既有**。新增发布前失败用例位于日志第9行并通过；测试统计位于67–74行。此前 `host-tool-receipt-after.log` 的48/48仍是有效中间验证，不累计为97个独立用例。日志中的两段模拟结算失败栈与其各自通过的故障注入检查相对应。本代理仅阅读日志，执行者为主代理。

| 文件 | 最终增补读取 SHA256 |
| --- | --- |
| `services/dsh_host/sessions.mjs` | `df2d80bcde9873bcb03ddafb63f80b5cc934b3a6453100506182718d169328ca` |
| `services/dsh_host/tests/business-tool-receipt.test.mjs` | `96dbf789793e26d7abd1914e89b78f5304616bdbbf975845a2ece748c38d1839` |
| `.implementation/host-tool-receipt-final.log` | `339b0268e4b4c3fb3c5491837d58ebb6a84aa0a265c2344e1b2153d3ec5eb637` |
