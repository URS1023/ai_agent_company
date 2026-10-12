# 批次十四 C01 / C02 修复与验收记录

日期：2026-10-04。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-task-lifecycle`。分支：`codex/major-task-lifecycle`。本轮起点 `057ce1f`；期间根代理快进至 `782d346`，只带入批次十三验收文档，产品基线没有变化。

本代理仅修改下列 3 个产品文件和 4 个新测试文件。没有修改旧 `major-capability-contracts` 工作树、`tools.py`、`tasks.py`、正式审计记录或门禁脚本，没有提交。其他代理的并发修改均保留。

## 修复结果

### C01：恢复窗口不能跨业务运行取正文和终态

`business_worker/chat.py:663` 从同工作区、同启动用户、同会话的持久 Run 重建根请求和已接受的 steer 请求归属。查询使用带明确归属条件的 Core 轻量字段查询，不受 `runs.view` 展示过滤和聊天历史可配置的展示窗口影响。恢复回执增加私有 `recovery_identity`，不接受用户提供的归属声明。

`services/dsh_host/sessions.mjs:13` 按这份服务端证据确定每个运行的原生日志窗口。当前运行的 steer、多次模型轮次、压缩与后台通知保留；下一运行的用户请求及后续正文不再用于上一运行。未知/冲突用户身份保守标记 `NATIVE_REQUEST_UNCONFIRMED`，不自动重做。

真实 Host 验证又确认了一处原生顺序：下一请求 B 的 `turn/start` 在 B 的 `user/message` 之前。仅截到 B 用户消息会把 B 的开启轮次残留给 A，误判 A 未完成。现在仅当 `next-turn` 收件箱明确插入 B，且开启轮次后只有 `agent/inbox/spliced`、`step/start`、`system/message` 前导事件时，才把该开启轮次随 B 一起截走。已有正文、工具执行或其他无法证明归属的活动不会被提前丢弃。

### C02：已落 failed 的未知外部回执仍阻止 completed

`dispatch_state.py:119` 增加纯持久证据检查 `unconfirmed_operations`，复用现有 `unconfirmed_replay_result` 的 provider/幂等键/可信未知契约。不重新执行工具、不解密密钥、不读取远端、不改写原回执、不要求工具目前仍可用，也不依赖 `runs.view`。普通错误、模型参数伪造、错误幂等键和派生 `replayBlocked` 回执不冒充新的外部操作。

`chat.settle:519` 在现有 tool/events 锁内先收尾未完成调用，再检查已经落终态的未知外部结果。原生请求 `completed` 且存在这类事实时，规范业务终态为 `interrupted / tool_result_unconfirmed`，同时保留 `native_status=completed` 和已经真实生成的附件。原生 cancelled/failed/interrupted 不被覆盖成另一种终态，重复结算沿用首次规范回执。

## 修复前失败证据与夹具修正

证据均在本树 `.implementation/dsh-completion-batch14/`，失败文件没有删除。

| 证据 | 实际结果 | 解释 |
| --- | --- | --- |
| `red-node.log` | 新 9 项全部失败 | A 取到 B 正文；B completed 覆盖 A aborted/failed；B 后台活动污染 A；steer/未知身份等窗口错误 |
| `red-python.log` | 5 failed / 1 passed | 其中 **4 项为产品失败**：HTTP/MCP × 无文件/真实 Word 齐备时，可信 unknown 仍结算 completed。另 1 项是新增夹具 steer ID 长度不足 8，不能记成产品缺陷 |
| `green-python-initial.log` | 5 passed / 1 failed | 核心 4 项已转绿；剩余仍是前述无效 steer ID，随后修正为 `steer-A-001` |
| `green-python-expanded.log` | 92 passed / 5 failed | 相邻 80 项和新增 12 项通过；5 项新增负例因手工 ToolCall 漏 id 失败，修正夹具主键后 17 项专项全部通过 |
| `host-first.log`、`host-first-fixture-failure.json` | Host 用例失败 | 模型桩把引擎追加的技能目录当成末条业务请求。修正为识别两个明确夹具标记；这不是产品失败 |
| `host-second.log`、`host-test_real_host_recovers_only_i0.json` | 真实 Host 用例失败 | A/B 正文已正确分离，但 A 被误标 interrupted；原生 seq 20 的 B turn/start 早于 seq 24 的 B 用户消息，属于真实产品顺序缺口 |
| `red-native-order.log` | 1 failed / 10 passed | 将真实顺序提炼为确定性 Node 红测：A status interrupted，应为 completed；同时保留 A 自身未完成工具轮次的对照 |

没有把夹具错误、预期故障注入日志或重复运行累计成新增产品缺陷数。

## 通过结果

| 范围 | 实际通过数 | 证据 |
| --- | --- | --- |
| 新窗口专项与现有 lifecycle/event-order/projection | **37 / 37**（新 12 + 现有 25） | `green-node-final.log` |
| 新 Worker 终态/归属专项 | **17 / 17**，25.31 秒 | `green-python-contract.log` |
| 相邻派发恢复、防重、重放授权、steer、Worker | **80 / 80** | `green-python-expanded.log` 内相邻用例通过；该次混跑整体并非全绿，原因见上表 |
| 真实 Host + Worker + SQLite 冷恢复故障注入 | **1 / 1**，37.84 秒 | `host-third.log`、`host-pytest-882-test_real_host_recovers_only_i0.json` |
| 本代理 3 个产品文件 diff 空白检查 | 通过 | `git diff --check -- ...` 无错误，仅仓库已有换行提示 |

Python 专项实际覆盖：HTTP/MCP 未知回执；真实 Word 已齐仍不能掩盖 unknown；新 call-id/改参数防重；独立只读工具允许继续；普通参数错误修正成功；规范 Message/Run/task 终态；重复 settle；终态后 stale 工具拒绝；持久 steer 去重和根身份冲突；工具不可用及撤销 runs.view 仍保留内部事实；5 类非可信标记；3 类非 completed 原终态；其他 run 未知不污染本 run。

Node 专项实际覆盖：A/B 同业务 task 但不同 Run；两轮正文和终态分离；A aborted/failed；后轮后台/未完成状态；本轮多 steer、多轮和压缩；真实原生先开轮后入正文顺序；同顺序正常 steer/后续模型轮次；后台通知保持现有保守策略；多未结算运行倒序恢复；未知/冲突身份。

## 真实 Host 闭环证据

模型桩和 Host 均只绑定本机随机回环端口；沿用现有 Host 的 Python/Node 网络守卫。SQLite、存储、日志与 DSH_HOME 均位于本轮临时目录，Redis/ES 被指向守卫禁止的本机 9 端口。没有业务 MySQL、真实模型、外网、8011/8012 或在用服务操作。

通过轮次：`pytest-882/test_real_host_recovers_only_i0`。一次性故障模块只在 tests preload 中存在；注入前检查 `DSH_TEST_ISOLATION=1` 与证据路径归属；只拒绝 A 的首个 `chat.settle` 转发。

- A：`01M42BH7234AY1865YNHTVNN6K`；B：`01M42BH7Q1TNB5V5D9CNBQMA4Y`。
- 故障恰好一次，`forwarded=false`。重启前独立 SQLite 中 A 为 running、B 为 completed。
- 原生日志真实包含两轮用户请求、assistant/message、turn/end；完整相关事件序号保存在 JSON 证据中。
- 重启后的规范历史：A 为 `completed / FIXTURE_A_FINAL`；B 为 `completed / FIXTURE_B_FINAL`。
- 模型桩总请求恰好 2 次，分别 A/B；恢复新增请求 0。两次数据库核对工具调用总数都为 0。
- 两个本轮 Host 进程均已退出，退出码如实记录为 `[1, 0]`，清理异常列表为空；不将首个故障阶段的退出码 1 表述为正常退出码 0。测试复用已有 Host 管理器的 shutdown/超时进程树回收逻辑，没有结束其他服务。

## 实际执行命令

cwd 全部固定为新 `major-task-lifecycle` 工作树；Python 一律经主树绝对解释器与隔离 harness。以下为最终专项命令；对应混跑和红测日志保留原结果。

```powershell
node --test services/dsh_host/tests/recovery-request-window.test.mjs services/dsh_host/tests/lifecycle-recovery.test.mjs services/dsh_host/tests/event-order.test.mjs services/dsh_host/tests/projection.test.mjs
& 'E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe' -X utf8 tests/run_isolated_backend.py tests/backend/test_dsh_completion_contract.py --disable-warnings
& 'E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe' -X utf8 tests/run_isolated_backend.py tests/backend/test_dsh_completion_host.py --disable-warnings
```

相邻混跑入口：`test_business_worker_dispatch_recovery.py`、`test_business_worker_replay_guard.py`、`test_tool_replay_authorization.py`、`test_dsh_steering.py`、`test_business_worker.py`。统一门禁由根代理执行，本记录不冒充整仓门禁结果。

## 已知边界

- 本批落实 C01/C02；未扩展到 C03 的复合交付语义、PPT 实际页内容完备性或 C04 的所有自然语言“继续”策略，未宣称“任意任务一定得到成功结果”。
- 普通失败是否仍缺业务成果，继续由既有交付合同和工具实际回执判断；不采用“出现一次 failed 就永远失败”的规则。
- 可信未知副作用没有自动核实/解除接口；本批保持不重发。跨新 Run 的精确一次及远端人工核实仍不在本次保证范围内。
- 未单独执行可选的真实 HTTP POST 断开连接 Host 测试；C02 通过实际 Worker/网关/SQLite/派发日志，但最后 HTTP/MCP provider 为可计数内存桩。C01 的 Host、Worker、原生引擎和本机模型 HTTP 桩均真实运行。
- 未增加产品超时或模型循环上限；本机测试桩自身保留最多 4 请求预算、现有 Host 测试生命周期超时与清理，避免测试失控。
- Python 存在既有 Pydantic/SQLAlchemy 等 warning，实际计数保留于日志，没有据此宣称零警告。

## 冻结文件 SHA-256

以下为本代理交付时的准确文件哈希；除下列文件，不接管根代理与其他代理的并发改动。

| 文件 | SHA-256 |
| --- | --- |
| `services/dsh_host/sessions.mjs` | `038c3097783903354bf95f1ea0c354fe8b8b7a6f4bb8e85220d40dab060b5bc4` |
| `backend/app/business_worker/chat.py` | `9681835a4359926c27a4b9d4c60c169a638141a2a47fc762ac11a8c9d8524332` |
| `backend/app/business_worker/dispatch_state.py` | `8304ceebafca46591b22d7fc40e195718ddae961597af46168a0c13775f69e27` |
| `services/dsh_host/tests/recovery-request-window.test.mjs` | `a2cc970ec9e112fc2308b8a1873a1b127bcf3780c57335e510d7bbd778b35757` |
| `tests/backend/test_dsh_completion_contract.py` | `d43716d22ac2ddb05f6b0b8872cd50df95d82b79034d25776a1f98c7b9b7177a` |
| `tests/backend/test_dsh_completion_host.py` | `9e34636ae6b5cd4c9549a29661519cae644c24a64093980c6a507c46948f7f03` |
| `tests/dsh/completion_settle_fault.mjs` | `f1b87a022cd6d76af80ecbc01f5d2be9b0a69e556af9f47f280a0652fabc8664` |
