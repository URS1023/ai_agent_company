# 批次十七候选计划：业务工具回执持久化与有界恢复

2026-10-04。工作树 `E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-delivery-acceptance`。**本文件是只读设计，不是实现或验收结论。** 本轮没有修改已冻结 B16 产品/测试/资产，没有运行任何测试、Worker、Host、模型、数据库、构建或网络请求；唯一写入为本计划。其他代理仍在同树工作。

## 建议与完成标准

建议复用现有 `RunEvent` 保存可查证的工具回执，为回执增加数据库唯一键；让 Worker 的规范业务结果、消息 part 和规范结果事件在同一事务中提交。Host 对完成事件只推送 Worker 已提交的事件，不再额外调用一次 `chat.event` 重写同一业务事实。响应丢失时只查询已保存回执，不重新调用 `tool.execute`。

这比新增通用消息队列、改写原生工具运行时或依赖解析原生错误文本范围更小。需要一个窄数据库迁移，不能以当前进程锁或 JSON 扫描冒充数据库幂等保证。本批目标是“已经提交的结构回执可恢复、未能确认提交时不冒充成功、恢复不重发业务动作”，**不是保证远端副作用 exactly-once，也不是保证存储不可用时仍完成业务**。

验收标准：

- Worker 的新调用提前拒绝与正常执行结果，只要返回“已提交”，就存在同事务写入的规范结果事件及对应消息结果；原始状态、error_code、metadata、精确数字文本、工件引用一致。
- 提交后丢失 RPC 响应，恢复得到原事件 id，不创建第二次业务执行或第二个规范回执。
- 提交是否成功无法确认时，原生/业务运行明确保持失败或 interrupted/unknown，禁止模型通过新 call_id 自动重做未核实副作用。
- `tool.started` 发布失败、正常回执、取消、传输失败及第二个发布错误都释放 Host 调用身份；第二个异常不能遮住第一个异常。

## 当前路径与已确认的缺口

| 位置 | 只读确认的事实 | 对设计的约束 |
| --- | --- | --- |
| `backend/app/business_worker/tools.py:73–114` | 运行已结束、工具不可用、重新鉴权拒绝、prepared/unknown 回放等在统一最终提交前返回 | 收到对象不等于已持久化；新调用拒绝和已有执行的拒绝回放须分开 |
| `tools.py:120–126、184–194` | 执行前 prepared 有一次提交；正常结果、ToolCall、Step、Message parts 最后提交后返回 | 保留执行前派发日志；将规范结果事件并入最后事务，不能跨两个独立 session 假装原子 |
| `chat.py:436–500` | `append_event` 在同事务中改消息 part、加 RunEvent、commit；只有传入 native_sequence 才查重，依据是 run/event/native_sequence，未比较内容摘要 | 当前完成事件不带 native_sequence，盲重试会新增事件；同 seq 不同载荷也未冲突校验 |
| `models.py:601–638` | ToolCall 有全局唯一 idempotency_key；RunEvent 只有主键 id，没有回执唯一键 | 执行幂等和回执去重要分开；不能改旧 ToolCall key 绕过旧执行记录 |
| `context.py:20–24` | `lock_for` 是进程内 RLock | 可协调同 Worker 线程，不能替代跨进程/崩溃后的持久唯一约束 |
| `sessions.mjs:178–193、309–328` | publish 串行写 chat.event 后再 emit；完成发布与 Worker 工具提交分离；started 在 finally 外；无回执时失败发布异常可遮住原异常 | 消除第二次完成写入；清理范围扩大到 started；保留主异常与补充异常 |
| `worker.mjs:40–86`、`protocol.py:73–85` | Host 控制请求超时删除 pending，但不终止 Worker handler；Worker 先执行 handler 再写 JSON-RPC 回复 | 超时/EOF/回复丢失均不能证明未提交；一次 absent 查询也不能证明原 handler 已停 |
| `sessions.mjs:351–359、507–547` | follow 跳过业务工具结果；恢复按运行窗口结算，没有按工具回执补投；chat.recover 仅返回非终态运行 | 原生工具结果不是现成的业务回执恢复器；已终态运行需靠历史事件恢复，不能重新 admit |

原生路径另外确认：`agent-loop/src/tool-calls.ts:151–162、255–290` 先写 `tool/call`，工具 runtime 完成后写 `tool/result`。结果保存 content/isError/error.info/meta，没有自动保存原始 `result.value`。目前 Host 发布异常会成为 native 失败文本，PPT 原结构不必然在其中。`core/session/src/index.ts:683–726` 的 append 先进入内存并通知，I/O 异步；`1176–1192` 的 `sessions.flush` 才等待实际持久化监听器。Host 当前在整轮 idle 后 flush（`sessions.mjs:411`）。不能把“在工具 body 里 flush”误认为未来尚未 append 的 tool/result 已持久化。

真实 Host 使用 `session-persistence-jsonl`（`profile.mjs:39`）；其存储 handle 支持持久 append/flush，原生恢复还会合成 `TOOL_OUTCOME_UNKNOWN` / `TOOL_NOT_STARTED` 结果（`core/session/src/repair.ts:124–155`）。这些恢复占位结果不能被当成实际业务成功或失败回执，也不能覆盖已提交 Worker 结果。

## 最小实现切分

### 1. 以 RunEvent 作为规范回执，不新增通用队列

给 `RunEvent` 增加可空的服务端 `receipt_key`（唯一索引）。历史/非回执事件保持 null；迁移不回填或猜测旧回执。建议键由 `run_id + native_agent_session_id + invocation_seq + call_id + phase` 规范编码后哈希生成，phase 至少区分 started、业务结果和控制面拒绝。所有身份来自 Host 捕获的原生 invocation / Worker 可信运行，不能从模型 arguments 取值。子代理必须包含真实 agent session 身份，不能仅拿根会话 seq 充当全局工具身份。

该键只标识回执，**不改变现有 ToolCall.idempotency_key 的执行幂等口径**。保留工具名和规范 arguments 摘要作为语义指纹；相同身份更换工具/参数必须拒绝。结果摘要在服务端做精确整数保护之后生成，使用稳定 JSON 口径；现成随机 RPC `host-N`、时间戳、错误字符串不作为幂等键。

同 receipt_key 相同规范 payload：返回原事件 id/sequence/data；不同 payload：明确冲突，不 last-write-wins。唯一约束冲突后先回滚本次事务，再在新 session 读取原记录并校验，不继续重执行 provider。事件流仍按既有 id 恢复游标，不要求新增一个消息队列服务。

### 2. 抽出“调用方事务内追加回执”小函数

将 `chat.append_event` 中结果 part 与 RunEvent 的生成抽成接收现有 db/session 的内部函数；函数自身不能 commit、不能另开 session。外层 `append_event` 保持旧公开行为。为规范回执加入唯一键和内容冲突检查。

`tools.execute` 最终路径在已完成真实 provider 工作后，用既有 db/session 一次提交 ToolCall/Step、消息 part、规范 `tool.completed` 事件。只在末段拿 `events:run` 锁，锁顺序固定 `tool:run → events:run`，与 settle 一致；不可在长 provider 操作期间持有事件锁。取 Message 最新内容时使用刷新读取，避免用早期缓存覆盖其他正文/进度事件。特别检查被抽出的 helper 不调用另一个会自行提交的 append_event。

对“新调用、没有原执行记录”的提前拒绝，也走该最终回执函数，但不伪造 provider 已派发，不为了记拒绝而制造 dispatch_started。工具不可用仍保留原错误，metadata 明确 not_sent。运行已结束的拒绝可以追加审计/结果事件，但不能将 run/message 复活为 running，也不能改写已确认旧调用。

对已有执行的回放/重新鉴权拒绝，保留原 ToolCall/result 和原业务结果事件。当前权限拒绝是控制面回执，不能替换原执行事实或返回已撤权的原数据；如需持久记录本次拒绝，使用独立 phase 的控制事件和独立响应身份，不写原业务结果键。实施前必须用同调用撤权对照锁定此分支的 UI 投影口径，不能把“统一出口”写成所有结果覆盖同一 ToolCall。

私有 `tool.execute` RPC 可以由 Worker 适配层返回 `{ result, receipt: { id, sequence, event, data, receipt_key } }`，receipt 只能由提交后的服务器行构造。保持 provider 的 ToolResult、模型看到的业务结果和原 Python 直接调用契约不变；不要把 receipt 信任字段放进模型参数。此处属于 Host/Worker 成套协议变更，需要版本兼容检查；旧 Worker 不提供 receipt 时，Host 不得假设已提交，也不得自动切回会覆盖结果的旧逻辑。

### 3. Host 完成事件只推送已提交行

增加一个窄的“推送已保存事件”入口复用现有 session/run emit；它不请求 `chat.event`。规范完成事件使用 Worker receipt 的 id/data，业务 failed/cancelled 仍通过原生失败分支返回。同步监听器异常只表示本次推送失败，不能改写已保存业务结果；重连从既有历史游标读取即可。不要为恢复推送生成新 event id，也不要让 live emit 失败导致第二次 provider 执行。

开始事件仍可走 `publish`，但应同样使用 phase=started 的稳定键；把 started 发布、abort 监听安装与 tool.execute 纳入同一个 try/finally。仅在确认 started 所需前置步骤成功后派发业务；started 失败时本段明确没有调用 tool.execute。finally 无条件删除本次 invocation，监听安装与否用局部标志或幂等 remove 处理。

catch 保留首个异常为 primary。为未派发失败保存可识别的本地阶段码；仅在还没有可信业务回执时尝试一次结构化终止记录。记录也失败时，保留 secondary（日志/原因链），最后仍返回 primary 的 code/message，不以补充错误替换它。现有 ToolRuntime 会转为 isError，需针对其实际 error.info 投影验证原因链哪些字段可保留，不假设任意 Error.cause 自动出现在模型历史。

普通参数/业务失败不冻结会话；持久化或提交状态未知则暂停本运行后续业务执行、进入可恢复 interrupted/unknown。收到已提交成功之后取消只取消后续工作，不能将已发生成功改成未发送或失败。取消监听的控制 RPC 自身失败同样是辅助异常，不证明原工具已取消。

### 4. 只读回执查询与恢复，绝不通过重新 execute 查结果

新增私有 `tool.receipt.get`（必要时同一方法支持小批量明确调用身份），只读查 RunEvent/ToolCall 的现有可信记录，不调用 gateway/provider，不创建新工具调用。入口重查 run 的工作区、发起人和会话归属、当前 run_actor 及工具/资源权限；返回状态至少区分 found、absent、access_denied、conflict。found 才可携带已保存的规范 result/event。权限撤销不能借内部恢复读取绕过，也不能把 access_denied 当成 absent 去补写或重新执行。

Host 丢失 tool.execute 回复时最多立即做一次有界只读查询；不能重复 execute。若 receipt found 且身份/语义指纹一致，按该事实恢复业务回执，向 UI 推送原 event id。若查询失败、absent、并发 handler 尚未确认退出或只有 prepared/dispatch_started，则保持 unknown；记录需要对账，不创建成功/普通参数失败假象。一次 absent 不能证明回滚，也不能授权更换 call_id 重发。

冷恢复在既有 recoveryWindow 范围内，将真实 native tool/call 身份与 Worker 回执对照；优先使用 Worker 已提交事实。原生 tool/result 仅作调用/取消/未派发证据，不解析嵌套 JSON 错误来重造 ToolResult；人工合成的 TOOL_OUTCOME_UNKNOWN 不能推翻 Worker 成功。当前 `chat.recover` 仅含非终态运行：已终态运行的已提交回执由 history/SSE 游标提供，不能为补推送重新 admit。若需要补历史投影，必须是按 receipt_key 的幂等投影操作，不能调用 tool.execute。

本方案把规范结果与事件同事务落存，因此正常恢复不需要“补写完成事件”。旧 B16 遗留数据没有 receipt_key 时先保守分类：已有 ToolCall 确认结果只能经授权、单独幂等迁移/投影补齐；只有原生错误或 running part 时不猜测。该遗留迁移可作为后续独立项，不混入第一次最小修复。

## 提交结果不明与幂等边界

| 观察到的事实 | 可以做 | 禁止推断/动作 |
| --- | --- | --- |
| provider 未被调用，started 前置阶段失败 | 保留 not_dispatched 主异常、清理身份；若存储恢复可记同身份终止回执 | 不能说开始事件一定未提交；不能为了事件恢复派发 provider |
| 工具事务已 commit、RPC ACK 丢失 | 用同回执身份只读查询，返回原 event id | 不能重发工具，即便看起来只读；不能新建另一完成事件 |
| commit 抛错、连接断开、控制请求超时 | 承认 commit outcome unknown，等可信读取/冷恢复 | rollback 调用、空查询、原生 tool/result 错误均不证明远端未发生 |
| 只有 prepared/dispatch_started | 沿现有派发日志及同任务未知操作保护收口 | 不给新幂等键、新call_id或“继续”绕过未知保护 |
| receipt 已提交，但 native result 尚未 flush | 以 Worker receipt 为业务事实，原生日志只说明本轮记录尚不完整 | 不能重做已完成工具；不能伪写原生成功来抹掉实际取消 |
| native tool/result 已 flush，但 Worker receipt 不存在 | 若有明确未派发阶段码，可收口 not_sent；否则保持未知 | 不能从 Error 文本认定业务结果已落存 |
| 原结果存在，但当前权限拒绝 | 拒绝暴露结果，保留原不可变事实和独立控制拒绝 | 不覆写旧 success、不把拒绝当成“原动作未发生” |

业务副作用的最终确认仍由已有 ToolCall 派发日志/操作对账承担；本计划只消除本地回执的二次写入缺口。数据库本身全部丢失、Worker 进程中断在外部动作之后但最终事务之前、权限撤销、原生日志也未落盘等情况不能凭此获得端到端 exactly-once。

## 三组原始红例设计（尚未编写/执行）

### 红例1：真实 Worker 提前返回没有最终结构回执

复用 `tests/backend/test_business_worker.py` 的 prepare/worker_context 和真实 tools.execute、chat.append_event；provider 只用内存计数器。隔离运行写 started，然后制造当前工具不在可用列表，得到真实 `tool_not_available`，不调用 Host 的完成发布。换独立 DB session 读取 Message/RunEvent，期望存在相同调用身份的规范失败回执、原 error_code、not_sent，provider计数0。当前 B16 预期红在“没有最终结果事件/part”，不是把测试夹具 throw 当产品红。

补充同组对照：正常业务失败带完整 metadata；既有 success 撤权后拒绝回放不得覆盖原 ToolCall；已终态 run 拒绝不得复活消息。前者证明提前返回缺口，后两者守住统一出口的边界。

### 红例2：真实提交后丢 ACK，恢复不能再次执行或重复记事件

使用真实 Worker 函数和 JsonRpc handler，加仅在私有本地管道丢弃一次 result 包的故障注入；不要把 db.commit 替成空函数。为跨进程重开对照使用测试专用 tmp_path SQLite 文件，明确 assert 引擎=sqlite、绝对路径在本次测试目录；不沿用任何业务 DATABASE_URL。计数 provider 返回带大整数文本和 metadata 的结果，实际 commit 后丢 ACK，再让恢复方查询同回执身份。

当前 B16 预期红：正常 tools.execute 最终只保存ToolCall/parts，没有同事务规范结果事件及可读receipt；若尝试对相同 completed 调用现有append_event两次，会出现两条RunEvent。后续实现期望读取同一event id、规范事件数1、provider计数1、原结果SHA相同，重复lookup仍零写入。参数化提交前中断与提交后丢包：提交前/无可证回执只能unknown，不得假称成功；两个同调用不同参数/载荷返回冲突，跨工作区/用户/子代理身份不能命中错误回执。

每个故障组合一次，Node 侧单次等待预算不超过既有短测试预算；停止后检查私有Worker退出与临时DB连接关闭。只验证本地存储与协议，不访问真实HTTP provider、模型、业务MySQL或8011。

### 红例3：Host started 失败清理，以及双故障保留原异常

复用 `business-tool-receipt.test.mjs` 的真实 ToolRuntime + SessionHost.installBusiness，只用内存 Worker/publish 桩，不启动Agent模型。

- started 发布在记录前失败：期望 native.isError、tool.execute 请求0、invocations归零，之后触发abort不再发本调用run.cancel。B16 预期红在身份未清理，因为 started 在finally之外。
- tool.execute 抛带特定code/message的PrimaryError，随后失败记录抛SecondaryError：期望原生主错误仍为PrimaryError，Secondary有独立日志/诊断记录，invocations归零。B16 预期红在原错误被await publish的异常替代。
- 同组正对照保留已提交成功后取消/发布失败：原规范回执保持成功，原生取消继续为失败，无第二次业务execute。现有B16 12例继续保留，新增断言不以isError=true这一宽条件代替原错误身份检查。

三组红例均先在冻结B16读取基线上产生原始证据，再在下一批独立变更上验证；不得回写或修改已冻结B16。当前只提出设计，**不存在本轮新增红/绿结果**。

## 预计修改范围与实施次序

候选范围：`models.py`与一份窄迁移（RunEvent回执唯一键）、Worker tools/chat 的事务内helper及私有receipt读取入口、server RPC适配、Host sessions/worker控制方法列表、三组专用测试。保持其他业务provider、图表、HTTP导出、原生依赖源码不变。迁移方案及现有 migration 约定本轮未读取，实施前需单独核对；不能直接生成未经核对的迁移名或SQL。

实施顺序为原始红例 → 数据库唯一键与同事务回执 → Host只推送已存事件及try/finally → 只读恢复/原因链 → 有界专项验证。第一轮只跑这三组与既有49项相邻Host回归；通过后再由根代理安排完整复合浏览器或gate，不把本计划视为测试/构建授权。发现首个超时/异常立即停止本轮后续测试，不自动重试或扩大预算。

## 本次实际读取范围与指纹

以下SHA256为本次只读时的工作树字节；表示所读快照，不表示已执行。tools/chat/protocol/server/session等大文件只读取上述相关区段，哈希覆盖完整文件。`test_dsh_task_operation_guard.py`只读前130行用于确认同任务未知保护和内存provider用法；不是全面复核。原生JSONL index文件只检索flush定位，本轮未审计完整落盘实现。`tests/backend/conftest.py`仅检索SQLite环境与引擎断言；未导入或建立数据库连接。

| 文件 | SHA256 |
| --- | --- |
| backend/app/business_worker/tools.py | b17a0959f9fa1c815976653cfa3d040522f5427900c3df58b13deb9391c518fa |
| backend/app/business_worker/chat.py | b9e65d49313212763a4f8fa92fee4cac8116976ad347482b0a9aa19a26e36004 |
| backend/app/business_worker/context.py | 1036fef26440ad1280680f3c80468bd4aa971ab97b991d65ef2f8676033fa8e1 |
| backend/app/business_worker/protocol.py | 69f4d3616dbd09aeb6bf273a023f84af832aaba0bdadd558848d2c7b22958793 |
| backend/app/business_worker/server.py | f2d389753c4da2534b078a59cb2fc239661316b9dc920034330356e50be4022a |
| backend/app/models.py | dce44685054d4efc2713b88362cb553f63c9e34ad9efb4efab16222f74620340 |
| services/dsh_host/sessions.mjs | df2d80bcde9873bcb03ddafb63f80b5cc934b3a6453100506182718d169328ca |
| services/dsh_host/worker.mjs | 35830bc723575836eae151fb54153d8b7f7a785d7042d89436f4591a85d87201 |
| services/dsh_host/projection.mjs | 692f7073e90581e495aa34a89774da1b5c700d6cef5bdb37983435e56741aa16 |
| services/dsh_host/profile.mjs | e1cc1da6b8f30beafd3738c0997b412e9a63048da16a43bd27bcea30347d1c0a |
| services/dsh_host/tests/business-tool-receipt.test.mjs | 96dbf789793e26d7abd1914e89b78f5304616bdbbf975845a2ece748c38d1839 |
| services/dsh_host/tests/event-order.test.mjs | adb79767e3dd0300ff41642b0cfd3e49fe7925a9bb7b724b24d99e6347312bf3 |
| tests/backend/test_business_worker.py | 97e6dc5a23313d23dab3c11ddfd8ef56d7ed72f3ae1d0c1a2eeaff730019da5d |
| tests/backend/test_dsh_task_operation_guard.py | 4d7ff25a1d8db45bb1cfe085c7dffe310dbc5bec034466e62a25f510c0fded76 |
| tests/backend/conftest.py（仅检索） | 91841932fa664d081357164145a4dffc74fd89c869c3638690a14f298d583274 |
| upstream/packages/core/agent-loop/src/tool-calls.ts | 82d2ff692320a9a26e0f5cd1c2ce1252bfe589138b44706ee18af033e2b51df4 |
| upstream/packages/core/session/src/index.ts | df2744c0db654676a10729556468c9f40d9b006737c6ef95015bdbb7be7be090 |
| upstream/packages/core/session/src/repair.ts | 6b66b7510bdd00a9baa352fda4ca444dc882f327872550933553d8258b7d444a |
| upstream/packages/session/session-persistence-jsonl/src/storage.ts | 41d6e4af01d3f613eb9d4e89e3d8ddded5070b4e73c7b3f858a61cbbd3b43b33 |
| upstream/packages/session/session-persistence-jsonl/src/index.ts（仅检索） | 6c56c465034c7673dd167f2ad7270ad8ce9ae7536457a291dc6a7abb81328436 |

表中 upstream 前缀实际为工作树 `.implementation/harness_migration/upstream/`。只读路径探测曾遇到不存在的 `services/dsh_host/index.mjs` / `tests/conftest.py` / `backend/app/models/`，已改读真实 `profile.mjs`、`tests/backend/conftest.py` 和 `models.py`；没有为缺失路径创建文件。
