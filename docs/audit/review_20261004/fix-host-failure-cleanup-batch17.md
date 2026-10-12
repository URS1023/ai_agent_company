# 大版本 B17 有限修复：Host 开始发布失败清理与双故障主异常保留

2026-10-04。本项实现候选计划红例 3 的两个已确认错误边界，以及根代理审查后追加的同段开始发布取消竞态。唯一写入工作树为 `E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-recovery-continuation`，起始及验证时 HEAD 均为 `7ed8f58b9fd81a151bc0d078216e822f9670aa15`。本报告及日志位于被 Git 忽略的 `.implementation/`。未提交、未推送，也未写 B16 兄弟树或正式 `docs/`。

## 修改范围与行为

本代理仅改动 `services/dsh_host/sessions.mjs`、`services/dsh_host/tests/business-tool-receipt.test.mjs` 和本地证据。本树其他代理的 Worker/后端测试改动未覆盖、未回退。

- `tool.started` 发布纳入既有 `try/finally`：发布失败时 `tool.execute` 请求为 0，调用身份删除，abort 监听完成清理。`started` 只在发布成功返回后置位，因此不会为开始发布失败伪造第二条 `tool.failed`。
- 尚未取得结构化 Worker 结果且开始发布已成功时，仍只尝试一次 `tool.failed`。该补充发布也失败时，以现有中文 stderr 方式记录副异常及 `run_id/call_id`；不额外打印 arguments、result 或模型配置。随后重新抛出原主异常对象，不包装、不修改其 code/message，也不重发 `tool.execute`。
- 保留 B16 的 `receivedResult` 边界：已有结构化结果不二次覆盖；业务失败/取消仍由真实 ToolRuntime 走原生失败路径。stderr 写入本身同步失败也不会替代主异常。
- 开始发布成功返回后、安装监听及派发 Worker 前再次 `throwIfAborted`，防止 await 期间已取消却仍派发业务。AbortSignal 对已发生的 abort 不会补发给新监听，因此该检查是必要的。

## 原始红例

先只增加测试，保持产品 `sessions.mjs` 与本树 HEAD 完全一致。测试经真实 `SessionHost.installBusiness` 注册到现有真实原生 ToolRuntime；Worker 请求、publish 为内存故障桩，没有启动 Worker 进程或模型。为了验证宿主自己的监听清理，注册观察器仅捕获真实工具体的融合 `AbortSignal`，不替换工具执行行为；直接以 `getEventListeners` 检查该信号，避免原生运行时收尾后移除外层信号转发而掩盖宿主监听泄漏。

命令（Node `v24.16.0`，每个测试显式 10 秒上限）：

```powershell
node --test --test-reporter=tap --test-timeout=10000 --test-name-pattern='started发布失败|Worker主异常' services/dsh_host/tests/business-tool-receipt.test.mjs *> .implementation/host-failure-cleanup-batch17-red.log
```

结果：**2 项失败、0 项通过、0 取消、0 跳过，184.5757 ms，退出码 1**。这是预期的原始产品红例，无超时或运行环境故障。

1. 开始发布在记录前抛错：原生报错、execute 0 次及仅一次开始发布均满足，但 `invocations.size` 实际 1、期望 0。
2. Worker 主异常后补充失败发布再抛错：原生 `error.message` 实际为“受控失败回执副发布失败”，期望为“受控Worker主传输失败”，证明主异常被副异常遮蔽。

## 修后验证

命令：

```powershell
node --test --test-reporter=tap --test-timeout=10000 services/dsh_host/tests/business-tool-receipt.test.mjs services/dsh_host/tests/recovery-request-window.test.mjs services/dsh_host/tests/lifecycle-recovery.test.mjs services/dsh_host/tests/event-order.test.mjs services/dsh_host/tests/projection.test.mjs *> .implementation/host-failure-cleanup-batch17-green.log
```

初版结果：**51/51 通过、0 失败、0 取消、0 跳过，390.6664 ms，退出码 0**。统计为当时新增 2 项 + 原 B16 49 项相邻组，不把红例与绿例相加。该日志原件未覆盖；后续根代理明确追加的相关取消边界见下节。

两个新增例还明确断言：调用身份及融合信号 abort 监听均归零；调用收尾后再 abort 不发 `run.cancel`；execute 分别为 0/1 次；双故障只尝试一次 `tool.failed`；诊断恰为一次且含运行/调用身份和副异常。双故障的原生输出保持 `message=受控Worker主传输失败`、`info={name: PrimaryError, code: FIXTURE_WORKER_PRIMARY}`。

原生工具运行时 `errorInfo` 只为真实 `HarnessError` 投影 `name/code`；因此红绿例使用既有上游 `HarnessError` 子类验证其实际可保持的原生合同，没有假定任意 `Error.code` 或 `Error.cause` 会自动进入模型历史。Host 仍原样抛出普通 Error；本项没有改写上游投影能力。

日志中的两段结算/辅助事件模拟失败栈来自既有 `event-order` 故障注入通过例。它们不是实际服务故障。额外 `git diff --check` 为退出码 0；Node 测试命令已正常退出，结束后 Win32 进程查询中匹配 `node --test ... business-tool-receipt.test.mjs` 的进程数为 0。

## 审查追加：开始发布等待期间已取消

根代理读 diff 后要求补充：`tool.started` 在 await 期间可能已取消；如果只在 publish 后安装 abort 监听，会错过已经发生的 abort，进而仍调用 Worker。按此明确扩充，在初版 51 绿产品上先增加真实 ToolRuntime 测试，publish 桩在开始事件记录后、成功返回前调用已有 AbortController。

```powershell
node --test --test-reporter=tap --test-timeout=10000 --test-name-pattern='started发布等待期间取消' services/dsh_host/tests/business-tool-receipt.test.mjs *> .implementation/host-failure-cleanup-batch17-cancel-red.log
```

追加原始红例：**1 项失败、0 项通过，180.5207 ms，退出码 1**。原生错误已经是原取消 message 和 `AbortError/ABORTED`，但 `tool.execute` 实际 1 次、期望 0 次。此时产品 SHA 为下表“初版修后”值；未改写前两项原始红日志。

随后在 `started=true` 之后、安装 abort 监听和调用 Worker 之前增加一次 `exec.signal.throwIfAborted()`。不包装取消异常，保留既有开始/结构回执 guard 和 finally。取消后仅已开始事件及其失败收口，不产生业务完成结果。

最终沿同五文件相邻组运行（仅将初版命令输出文件改为 `.implementation/host-failure-cleanup-batch17-final-green.log`），**52/52 通过、0 失败、0 取消、0 跳过，351.6004 ms，退出码 0**，即新增 3 项 + 原 49 项。新增取消例明确断言原生原取消 code/name/message、`tool.execute=0`、`run.cancel=0`、身份/监听归零和事件顺序 `tool.started → tool.failed`。最终 diff 检查退出码 0，进程查询匹配数仍为 0。初版 51 与最终 52 是两次开发验证，不相加为独立用例。

## 指纹

以下为本树实际文件字节 SHA256（保留工作树换行口径，不冒用 B16 旧日志 SHA）：

| 文件/阶段 | SHA256 |
| --- | --- |
| 修前 `services/dsh_host/sessions.mjs` | `76b96b5b2b55b5e9b2f5d5cfcf9d3eff967a7e1c9ef3666284f1a9980373583f` |
| 修前原始测试（新增红例前） | `2aa83ef7c5932ee517a06ab37241f21f1536dfcfdc0347f3cf0cc942c8aded7b` |
| 初版修后 `services/dsh_host/sessions.mjs`（51 绿及追加取消红） | `37f9aa61034783b40aff539a06b3acefe7e7bf8e945f6930a60aa56ef4765e46` |
| 初始 2 红 / 51 绿的同一版测试 | `02b1e2ee4d9c7337038652c30f0332b55ef6cad4f6583e2e74627d832a25e4f9` |
| 最终 `services/dsh_host/sessions.mjs`（52 绿） | `57f677e65a23ed05ef336a62c54fc65590ee0c9b7f5db00e3c579524875deb82` |
| 最终测试（追加取消红 / 52 绿同版） | `b18a4f34d9ba0d68929f5fdfcc4e929c30df5e846b274d0f402bd48518c63e78` |
| `.implementation/host-failure-cleanup-batch17-red.log` | `ad5cd58f69d1071b4e8ecf0e185f05071b153186d6d6ff56a71f9b6214a1b0f5` |
| `.implementation/host-failure-cleanup-batch17-green.log` | `4737fe46b4d937fe62ff7fbfc894f34a66e028e3aecb0bdbd07cbd755273814b` |
| `.implementation/host-failure-cleanup-batch17-cancel-red.log` | `ef90eb94f3595cd4dacb1c8465d682c4d2bb97d24dc7180a73ab30029b8685b7` |
| `.implementation/host-failure-cleanup-batch17-final-green.log` | `8625c32ed7852c158ba6460a748adca1232fd83160319b6380512a932588411d` |

HEAD 中产品 Git blob 为 `4f1846fa226a45f4290011d47ff9c255f7e5fd15`，原测试 Git blob 为 `85fd425fc76a36b74642b9aac29c96964264f258`。新增测试后、改产品前已经核实产品未出现在 Git diff 中；原始红日志未重写。

## 明确限制

本项未实现或承诺回执 schema/迁移、事务持久化、丢 ACK 查询、冷恢复或副作用 exactly-once。B16“Worker 提前返回 + 首次完成发布在持久化前失败”的边界仍未关闭。开始发布失败只证明本 Host 分支没有派发业务，不证明开始事件一定没有落存。

未改 Worker、协议、门禁、构建资产或正式文档。未运行浏览器、业务数据库、SQLite、HTTP provider、真实模型、8011 或整批门禁，也未重启/切换在用服务。本轮数据库连接及查询均为 0，业务/模型网络请求均为 0，所有新业务请求为内存计数桩。原生依赖只读复用新树 junction 指向的主仓 `.implementation/harness_migration/upstream`，未安装、构建或修改依赖。

`run.cancel` 自身传输失败、完整 SessionHost.cancel → execute → settle 组合路径及工具体最前置校验失败清理，均未作为本有限修复新增验证范围；现有取消/结算相邻测试通过不能替代这些组合路径验收。当前 52 绿仅是本专项及原相邻组通过，不表示整批或全功能验收完成。
