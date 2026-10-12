# B23 抽取恢复实现独立只读复核

日期：2026-10-04。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-extraction-recovery`。本报告对应实现迭代中的阅读快照，不能当作最终冻结版本验收。

本代理仅阅读源码、测试、设计报告及既有日志；只新增本 ignored 报告。不修改产品、测试或依赖，不执行任何测试、数据库、模型、服务或浏览器，不提交，不扩展到知识 ES 或调度。应用 `code-review-excellence` 技能。下面的触发序列是静态推导的可构造反例，**本代理未实际运行**。

## 需落实的两项边界

### P2：异常 owner 创建时间可使整个 Worker 初始化失败

位置：`backend/app/modules/knowledge_graph/extraction_recovery.py:81-94`，尤其 87-88 行；影响传播至 156、163 行和 `business_worker/server.py:37-40`。

`owner_state()` 对 `created` 先调用 `str.isdigit()`，再执行无异常保护的 `int()`。Python 的 isdigit 接受部分不能被 int 解析的 Unicode 数字，例如 `"²"`；超过 Python 十进制转换上限的长数字串也会在 int 处抛 ValueError。故该函数并非对所有格式异常返回 unknown。

具体触发序列：

1. 临时 SQLite 中准备一个真实 running Job/Run，Job.result_summary.runId 指向它。owner 的协议、平台、本机 host、PID、generation 均有效，只将持久 `created` 设为 `"²"`。这种元数据不由现有普通 API 产生，属于损坏/异常私有元数据边界，不声称普通用户可直接写入。
2. 调用 `recover_interrupted()` 或真实 Worker.initialize；该行满足当前初扫的 protocol/platform/host 条件。
3. `current_run` 通过后，owner_state 在 int 转换处抛 ValueError；恢复器仅捕获 StaleDataError，整个初始化退出，ready 不会置 true，后续合法项也不再检查。

期望：身份格式不能证明死亡，应计入 unknown 并保留原状态；不能让单条非法 owner 阻断整批恢复。数据库真实故障仍应传播，不能为修这一点宽泛吞掉所有异常。

最小建议：转换前限制 ASCII 十进制、合理长度及 FILETIME 的正整数范围，并用可控的失败返回覆盖解析异常。protocol 应严格为 int 而非 bool/float，generation 应符合实际生成的固定十六进制格式。后两点是同一输入合同的加固建议，不另登记成已触发故障。

可先添加一个无数据库的 owner_state 负例，再以一条异常 owner 加一条正常 dead owner 做临时 SQLite 启动恢复验证。当前已有未知 owner 参数化覆盖缺失、外机、非 Windows、created=None、pid=0/bool、空 generation，未覆盖上述 Unicode 数值异常。

### P2：最终 ACK 恢复可拼接旧 Run 与新 Job 的不同代次状态

位置：`backend/app/modules/knowledge_graph/extraction.py:593-598,609-623`。

`_record_failure()` 发现原 Run 已 review/completed 时返回 True，这是保护成功终态的正确方向；但 `_committed_result()` 只检查原 Run 的 generation/status，读取当前 Job 后未检查 `job.result_summary.runId` 是否仍为该 Run，随后直接返回两个视图。

具体触发序列：

1. Run1 的最终 commit 已真实完成，Job/Run1 均为 review，候选已落存。
2. 在模拟 ACK 丢失之前，用另一个真实会话启动同一 Job 的 Run2，并在其首次 running commit 后暂停；此时 Job.status=running、Job.result_summary.runId=Run2，Run1 仍为 review。
3. Run1 的原请求抛 ACK 异常进入补偿；独立 `_record_failure()` 看见 Run1 已成功，不降级。
4. `_committed_result()` 返回 HTTP 200 的组合为 `job.resultSummary.runId=Run2`、`job.status=running`，但 `run.id=Run1`、`run.status=review`。

该分支**没有覆盖持久 Run2**，缺陷是将当前 Job 与原执行的 Run 混作恢复结果，不能把其表述成“新任务被改成成功”或“候选重复生成”。前次设计报告已要求避免异常恢复拼接不同时点状态，而当前新增 25 项测试尚无这一交错。

最小建议：重新认证之后，按一致事务读取并校验 Job 当前 runId 与原 runId。若已有更晚 Run，保留成功的历史 Run 和当前 Job，返回无法恢复本次组合响应的明确边界或继续传播原异常；不要把 Job 改回旧 Run。若产品决定允许“原 Run + 当前 Job”响应，必须显式定义响应语义并避免调用方将两者视作同一执行，此方案不宜由私有恢复 helper 静默引入。

受控测试可以沿用 `test_commit_failure_uses_actual_persisted_generation[final_after]` 的真实 commit hook：仅在第二次 commit 后、抛异常之前，用独立会话和原始 execute_job 启动并暂停 Run2，避免 hook 递归。断言 Run1 仍成功且候选仅一份、Run2 仍 running、execution_count=2，并拒绝把两个不同 runId 当作同一次恢复成功。无需真实来源或模型。

## 前次两项设计发现已有修正，不重复登记

- 首次 running `_commit` 已移入 try，且提交前保存 workspace/user/job/run/generation/bind 标量。异常后先 rollback，再独立事务读取当前持久状态；首次 commit 前失败不制造 Run，首次已提交后失败可按同代次写 failed。既有四阶段测试直接注入真实 commit 前/后异常，覆盖 initial_before、initial_after、final_before、final_after。
- 历史候选复核和物化现在只在 Job.result_summary.runId 等于所操作 Run 时写 Job 汇总/状态。Run2 已 running 后物化 Run1 的用例已在本轮测试中。并发窗口内若 Job 已变更，现有 ORM version CAS 应使陈旧 Job 写整事务失败；本复核未发现静默覆盖新 Job 的新路径。未宣称真实 MySQL/PG 锁调度已验证。

## 其余重点核对结论

### Windows 身份与私有字段

- `windows_process()` 以 OpenProcess 的同一个句柄读创建时间并做零等待检查；使用 QUERY_LIMITED_INFORMATION 与 SYNCHRONIZE。只有有效非零 PID 的 ERROR_INVALID_PARAMETER、已退出句柄、或同主机活跃 PID 的创建时间不匹配才判旧 owner 死亡；访问拒绝、GetProcessTimes 失败、WAIT_FAILED 等走 unknown，finally 关闭句柄。未调用系统探针来复验这些分支。
- PID 严格排除 bool、范围限制为 DWORD；非 Windows/无机器身份不自动恢复。machine GUID 与 hostname 哈希用于本机匹配，generation 每次新执行生成。上述解析异常是单独发现，不代表正常真实 Windows 身份逻辑整体失败。
- owner 仅新增在 Run.input_snapshot；run_view、job_view、审计 metadata 的现有显式映射未包含它。run_view 输出 summary/events，而新 started、failed、interrupted 事件均未写 owner；自动恢复审计 actor=None、workspace 取对应 Job。未发现本次正常视图/审计路径外泄 PID、host、created 或 generation。这里只核对所列显式输出路径，不将此结论扩展为全仓库日志安全认证。

### CAS、事务和结果真实性

- `locked_pair` 统一 Run→Job 锁顺序，workspace/job 关联约束齐备，populate_existing 避免原会话缓存覆盖重读。`current_run` 同时检查双方 running、workspace/job、Job.runId 和 generation。
- ExtractionJob/Run 继承的 version_id_col 对 ORM 更新提供 CAS。mark_failed 把 Job/Run、事件、审计放入同一个事务；恢复仅捕获 StaleDataError 并完整 rollback，不将数据库访问失败报告成成功。已有双恢复会话测试检查实际 SQL WHERE 中的 version，并断言只一条中断审计。
- 最终候选写入前重新锁定并校验归属；候选、review 终态和审计在同次 commit。最终 commit 已成功时 `_record_failure` 不降级；恢复响应使用新 Session、真实令牌重新认证，避免沿用旧权限缓存。认证心跳发生在业务读取之前，未见认证提前提交候选的路径。
- 四阶段测试和撤销 membership 测试证明的是其指定注入分支；没有据此宣称所有连接丢 ACK、rollback 失败或二次收尾 commit 失败均已恢复。异常处理自身再遇数据库故障时仍可能向上传播，本次没有加入无界重试要求。
- startup 恢复有 200 项上限，deferred 只表示还有未检查项；同主机活跃/未知项长期占据前段仍可能使后续项延后。当前日志不宣称全部历史运行均恢复，该扫描限制保留。

## 已读验证证据与未执行范围

完整阅读 `tests/backend/test_extraction_recovery.py` 的首轮 266 行版本。既有 `.implementation/extraction-batch23-contract-first.log` 末尾为 **25 passed, 2080 warnings in 20.08s**；这是实现代理已运行的日志，不是本代理复验。

阅读期间 `test_extraction_recovery.py` 后续字节已变化，新出现 `test_extraction_recovery_process.py` 与 `tests/dsh/extraction_recovery_probe.py`；本报告未完整复核这些新增进程测试，也未读取其进行中的结果来作通过结论。产品三文件在本次两次取 SHA 之间保持相同，后续实现仍可能继续修改。

实际阅读范围：AGENTS.md；前次设计报告；extraction_recovery.py 全文；extraction.py 全部本次 diff 及 get_job/_get_run/视图/_commit/execute/review/materialize 相关上下文；server.py 初始化挂接；模型 version 定义；deps 的重新认证/工作区/权限绑定路径；audit 的同事务行为；新测试首轮全文与首轮结果日志。未读取业务数据库、凭据或 `.env`。

## 阅读快照 SHA-256

| 文件/阶段 | SHA-256 |
| --- | --- |
| extraction_recovery.py | `3409e60d70ad742c43be5c825b12c1d53ed8c235b66d4ac99299744e1d9b93b3` |
| extraction.py | `510e3b65d49bcd3eaf8124bbbc8fb87fa82f236c480a4314329e86b25425b07d` |
| business_worker/server.py | `3faf2454cc3b72a4012b10b92fb1332c73fe4bddac061cc0832733bcd85a2dec` |
| test_extraction_recovery.py，已完整阅读首轮版本 | `9399383f52225f499547d55a3c736ae800de621baab89fafb30617dad8e795f0` |
| test_extraction_recovery.py，后续变化版本，仅记录观测，不宣称已完整复核 | `68bccb8f4014c6924c44e2c0d2234a25d427e7e2f090b5b92d9d15aec7f52f06` |
| extraction-batch23-contract-first.log | `c94aa26fd820c35d54b05177950a75f4db88ef62b98e944aab21bae5bcfb2eca` |
| review-extraction-recovery-design-batch23.md | `b9b0902d52044d50a9de73e3bec4747f52773f7289f05c69b137df652173e7e8` |

两项具体反例已经发给 root 和实现代理。任何后续红绿及修正需追加新版本证据，不能覆盖本次静态发现或把尚未执行的触发序列称为实测。
