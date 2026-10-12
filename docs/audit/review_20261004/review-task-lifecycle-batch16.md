# 第十六批任务未知操作与数据源停止事实：独立只读复核

时间：2026-10-04。独立树：`major-delivery-acceptance`，当前基线 `7c7174f96a283c54da659d719b1632ff7e73eb7f`。本报告不修改产品或测试，不运行测试，不连接数据库/模型/在用服务。根代理报告的 46 项小组绿灯是其执行证据，本复核不重复计测试数。

## 结论

在本次全文阅读的四个产品模块和两份专项测试范围内，未发现确定的新增跨归属读取、旧业务结果泄露、已提交同任务停止事实被新/排队 Run 忽略的缺陷。跨运行未知操作检查与数据库异常停止检查复用一致的任务归属条件。下面的并发时序与旧数据边界需要保留，不能宣称为跨系统原子停止或全部旧记录自动恢复证明。

## 逐点证据

1. **任务归属没有被 task_id 单独替代。** `dispatch_state._task_run_criteria:100` 始终同时限制 Run 的 workspace_id、started_by_user_id、conversation_id。当前 Run 恒在候选；只有当前 task_id 为非空字符串且存在会话时才扩展同任务候选。`_task_calls:127` 还限制 ToolCall.workspace_id 并按真实 run_id JOIN。缺 task_id 或会话不会凭空扫描其他任务，符合专项 `test_absent_task_identity_does_not_scan_other_runs:115` 的明示合同。
2. **旧草稿原运行只从平台持久来源纳入。** `_task_run_criteria:109` 只读取 business_task 与当前 task_id 一致时的 resume_from_run_id；同样施加三重归属。补看片段 `tasks.choose_task:134–155` 表明后续任务合并保存旧 task，新增的是 continuation_of_run_id，不会把 resume_from_run_id 不断覆写而遗忘原始旧运行。这排除了“两次继续后旧未知事实丢失”的阅读疑点。
3. **派发前先校验当前工具可用性。** `tools.execute:91–104` 先取得当前工具列表及当前身份允许的 selected，再读取原调用回执或创建新调用。新调用先保存 prepared，再进入 `unconfirmed_task_replay_result:134`、任务停止事实检查 `:139–142`，最后才 `gateway.execute`。使用 Core 表读内部生命周期事实，避免 runs.view 展示过滤把停用/未知事实隐藏；不会因此给用户新增历史列表权限。
4. **跨运行阻断没有复制旧业务数据。** `replay_guard.unconfirmed_replay_result:50–73` 只保留 provider、outcome、幂等键、是否可重试/是否需核实等字段，不复制旧 data、artifacts 或任意 metadata。`dispatch_state.unconfirmed_task_replay_result:148–152` 跨 Run 时再删除 originalErrorSummary，使用固定提示。第一个新测试 `test_continuation_cannot_resend_unknown_external_operation:15` 明确断言数据/工件为空、旧摘要不出现、原 ToolCall 不改写与调用次数保持 1。
5. **未知状态必须有可信证据。** `journal:29–39` 校验 origin/version/phase 与真实 idempotency_key；`replay_guard:43–50` 还核对 uncertain、retryable=False、requiresVerification=True，排除 replayBlocked 的二次阻断回执，防止其自我复制成为新的未知操作事实。已派发日志和旧无阶段遗留记录分别处理。`unconfirmed_operations:175` 保留缺 provider 的明确 legacy_unstaged/unknown 终态，不因工具已下线就让模型声明完成。
6. **完成状态仍核对原任务。** 补看 `chat.settle:517–535`：先 reconcile_dispatches，再 unconfirmed_operations；未核实操作使 completed 收口为 interrupted。`test_continuation_cannot_hide_prior_unknown_by_not_calling_tool:42` 覆盖新 Run 完全不调用原工具也不能冒充完成；`test_legacy_unstaged_unknown_stays_unconfirmed_when_tool_is_no_longer_available:129` 覆盖旧阶段缺失和工具下线。
7. **停止事实不靠 prepare 的瞬时继承。** `task_datasource_halted:116–124` 在工具执行时读取平台已提交的同任务 Run 布尔停止标记，所以提前排队的 Run 即使自身 snapshot 没有标记，也能看到先前 Run 后来提交的异常。`tools.execute:169–170` 在明确 recovery=stop_datasource 时持久保存 True；后续阻断同样保留该事实。`test_task_continuation_preserves_datasource_stop:36` 对 3 种续办说法 × 排队先/后两种时序进行断言，独立本地工具保持可用。
8. **独立任务和确定未发送失败没有被全部冻结。** `test_independent_task_keeps_existing_external_execution_permission:73`、`test_confirmed_validation_failure_can_retry_in_same_task:85` 以及 halt 文件 `test_explicit_independent_task_preserves_existing_dispatch_contract:52` 分别保留独立任务、参数校验失败后纠正、独立本地工具等合同。`delivery_contract.is_continuation:15–25` 本次只增加动作开头的“重试”及明确剩余部分回指，不将咨询、否定或明确新任务文本一概继承。

## 保留的边界与后续反例建议

- **静态时序边界，尚未复现，不作为本批确认漏洞：** `tools.execute` 的 task_datasource_halted 检查位于 gateway.execute 之前；检查通过后，若另一个并行 Run 才提交停止事实，本调用不会在最终 SQL 派发点再读取 task halt。`start_dispatch` 校验的是当前 Run 终态，B16 QueryAuthority 的派发回调校验的是来源/权限，二者不等同于重新查询跨 Run 停止事实。若后续扩大保证，可用两个受控 Run、两个平台事务，在第一个 Run 通过 task_halt 检查后暂停 gateway，第二个 Run 保存真实停止事实，再放行第一个；以假驱动计数确定是否派发。无需真实业务库或数据库超时。
- **不是数据库服务器取消证明。** task_datasource_halted 表示平台禁止后续数据源工具，不表示已发到服务端的 SQL 已停止。查询取消、客户端连接归还和服务端实际执行各有边界。
- **旧记录必须有可识别关联与事实。** 缺 task_id 的当前运行只检查本 Run；已登记旧草稿可经 resume_from_run_id 纳入。没有任务关联、没有可信停止标记的其他历史数据，本实现不会猜测关联。legacy_unstaged 的保守未知收口与“已证实远端执行”也不同。
- **完整性范围有限。** 本次核对的是原未知操作与数据库停止事实，未重新审查全部工具 DATA_TOOLS 名单、所有 HTTP method 支持、所有业务任务语言表达或跨进程锁设计；不以这里没有发现确定问题推断这些范围全部完备。

## 实际阅读范围和 SHA256

下表前六个文件均从第一行通读至 EOF；检查前后哈希一致。后三个文件仅为核对调用/历史迁移来源的片段阅读，不能计作全文审查。

| 文件 | 阅读范围 | SHA256 |
|---|---|---|
| backend/app/business_worker/dispatch_state.py | 全文，226 行 | FED7F1636FE9F654145F67E91873D95884D26050746313CFAD35868D9026C1AE |
| backend/app/business_worker/tools.py | 全文，194 行 | B17A0959F9FA1C815976653CFA3D040522F5427900C3DF58B13DEB9391C518FA |
| backend/app/business_worker/replay_guard.py | 全文，75 行 | B2FD558B88B61C47ABA8356C2D5BCFD9E6C7FA1B1B21F73663D24C6C5AEF00EB |
| backend/app/business_worker/delivery_contract.py | 全文，163 行 | EF95DB92E30637C44F66DAE84B9B4EC1760ED6645F6FD539055B2E3C3703D1EB |
| tests/backend/test_dsh_task_operation_guard.py | 全文，144 行 | 4D7FF25A1D8DB45BB1CFE085C7DFFE310DBC5BEC034466E62A25F510C0FDED76 |
| tests/backend/test_dsh_task_datasource_halt.py | 全文，74 行 | C7AE19F340D87401EDFF0B5F9DC318CFE8C3C30EA43CA752FD08E4A7B2F86830 |
| backend/app/business_worker/tasks.py | 112–176；211 至 EOF 的验证尾段；rg 调用定位 | AD5FBAF2DBBBB4ABDCBB9E7C25116E17B1E97EDE9C239E2EEBF7EF609C591B07 |
| backend/app/business_worker/chat.py | 186–320、343–377、511–535；rg 调用定位 | B9E65D49313212763A4F8FA92FEE4CAC8116976AD347482B0A9AA19A26E36004 |
| tests/backend/test_business_worker.py | 231–264，旧草稿迁移相关片段 | 97E6DC5A23313D23DAB3C11DDFD8EF56D7ED72F3AE1D0C1A2EEAFF730019DA5D |

报告完成后保持只读冻结；未为本次审查新增测试数、修改或提交任何源码。
