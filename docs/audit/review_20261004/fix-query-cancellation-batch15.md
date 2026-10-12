# 批次十五：查询取消、派发租约与数据库工具计量

状态：2026-10-04 已完成本范围实现与隔离验证，8 个产品文件、3 个新专项文件冻结；未提交、未发布、未重启在用服务。正式大版本统一门禁由根代理执行，本报告不能替代全功能验收。

工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-compound-delivery`，分支 `codex/major-compound-delivery`。开始基线 `ea81027`；期间根代理快进到 `e36533e`，内容为上一批前端/构建/记录，未覆盖本范围后端工作。未修改旧 `major-task-lifecycle` 或主工作树。

## 实现结果

### LIFE-N03：取消传播、迟到结果与控制异常

- `queries.py` 把当前 TaskControl 的取消 Event 传给执行器；元数据返回、模型生成返回、SQL 派发前、SQL 返回后和成功登记前增加检查点。取消后不把迟到行写为成功，失败查询与嵌套 Run 仍按原合同收口，finally 释放本次租约。
- `execution.py` 同时检查显式 Event 和当前任务控制。SQLite 进度回调记录**本回调实际触发的**中断原因，驱动退栈后恢复 ChatCancelled/ChatTaskTimeout 或单查询超时，不把取消误包装为来源故障；普通驱动错误保持原类别。连接建立、exec/fetch 前后及返回前也检查取消，保护短 SQL 没有触发进度回调的情形。
- `metadata.py` 在每次 inspector 调用前后检查停止，冷结构读取取消后不再扫描下一张表；控制异常穿过元数据包装时保持身份。
- `semantic.py` 的 build/recall 不再把控制异常吞成关键词降级；补充 embedding 批次和关系读取检查点。可选样本路径另取 2 个红例，修复单表/整段 catch 吞掉任务停止后继续下一张样本表的问题。普通模型/可选样本错误保留既有降级合同。
- `connections.py` 在借连接及 SET 只读事务前后检查停止；取消使异常连接失效并归还借用，不再继续初始化下一条语句。驱动连接/读取/语句预算不变。
- 经根代理追加授权，`dsh_query_execution.py` 仅补入口、缓存返回及写成功回执前检查点。同轮缓存不再绕过准备阶段收到的取消，查询完成与回执登记之间停止时不再添加成功回执。

**不能扩大声称的保证：**本批能阻止已检测到取消后的新派发、拒绝迟到成功、保持控制异常身份，并在拥有连接的调用退栈后清理。没有实现 MySQL 控制连接 KILL、PostgreSQL 异步 cancel 或 ES 服务端任务取消；阻塞中的驱动仍依赖原有单次预算退出。关闭客户端连接、invalidate、租约释放或宿主 run 状态，都不单独证明服务端 SQL 已停止。未修改宿主停止状态、Worker 通用工具或原生任务逻辑。

### LIFE-N04：SQL 派发前必须仍持有本次有效租约

- 新增 `validate_query_lease`：在 SQL 派发前按工作区、来源、用户与本次 lease ID 锁定读取；租约缺失或过期返回明确 `datasource_busy` 并标识 `lease_expired`。不重取名额、不重放业务 SQL，也不会释放其他请求的租约。
- 有效租约刷新执行窗口；窗口仍沿用 `timeoutSeconds + 120` 的原有有界计算。这是租约存活时间，**不是扩大实际 SQL/网络超时**。
- 预留日额度时使用当前锁定读，避免 MySQL RR 的先前一致性快照漏掉刚提交预留/历史。保留 source → policy → 受保护的历史/租约锁顺序；正式 Session 默认 `autoflush=False`，实际 SQL 次序正对照证实历史待更新没有抢在父来源锁前写入。
- 仅正数日额度读取当日历史 ID，最多取到该上限；达到上限已足够拒绝。不设日限额（0）时不锁扫全天历史，仍检查有效租约并发数。普通管理状态页保持聚合计数，不加载整天历史对象。

隔离事务验证使用真实临时平台 Session 和可控时钟：A 的模型桩阶段推进时钟到租约过期，B 从另一 Session 取得名额，随后 A 被拒绝且业务 SQL 调用数为 0，B 租约仍在。未真实等待数分钟、未提高测试预算。MySQL 方言编译证明实际构建的是 FOR UPDATE 当前读，**不能代称实测 MySQL RR 并发行为**。

**租约验证不等于权限重验。**来源在准备期间改版本/改列范围/停用的进一步边界记录在 `.implementation/followup-source-revalidation-batch16.md`；仅静态发现，尚未复现或修复。N01 的派生写回 CAS 和 N02 的同进程 MySQL/PostgreSQL 池屏障不能被外推给 SQLite、ES、其他进程或已借出操作。

### LIFE-N05：database.query 使用相同治理与单次尝试计量

- 保留正式 `test-invoke → capabilities.test_connector → gateway.execute_spec → database_provider` 调用链；source 执行权限、当前范围、只读 AST/字段校验、最多 200 行和原 ToolResult 结构不变，没有新增 `analytics.execute` 要求。
- query 先静态拒绝明显非法 SQL，再预留租约、登记历史、取得受限元数据与授权 SQL，派发前校验原租约，执行后检查取消并收口。缓存的 list/describe 不新增 QueryHistory，不消耗业务 query 日额度。
- 主查询与连接器 QueryHistory 使用该次 `lease.id` 作为历史 ID；当日计量是“历史 + 尚未形成历史的有效预留”。预留提交到历史插入之间不漏计，历史插入后按同 ID 去重，失败与取消记录在租约释放后仍占本次日尝试额度。
- 连接器历史保存完整统一 QueryResult，并绑定现有 Tool Run ID。验证了 queryId、runId、SQL、行数据与原 history/export 入口追溯；没有给某种失败制造成功结果或伪造业务数据。
- 原正式资源可见性要求仍存在。新增非管理员正对照最终授予来源查看/执行和连接器查看/执行权，明确没有 `analytics.execute`，工具成功而分析入口拒绝。

## 红灯、修复与复测证据

所有命令使用主树绝对解释器：

```text
E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe
  tests/run_isolated_backend.py <下表测试文件> -q --tb=short --disable-warnings
  --junitxml=.implementation/<证据名>.xml
```

进程从新树根目录执行；平台内存 SQLite、测试私有受管 SQLite、假 driver/engine/模型与可控时钟。外部数据库驱动、业务库、真实模型、8011 都被隔离入口禁止。本轮没有访问它们。每次原始 XML 与同名 `.log` 均保留，没有覆写失败。

| 证据文件前缀（均在 `.implementation/`） | 收集/执行结果 | 说明 |
| --- | --- | --- |
| `query-lifecycle-before` | 17 项，16 失败、1 通过，19.579 秒 | 先写新取消/计量契约；覆盖真实 DSH 网关、正式 connector HTTP、SQLite 回调异常身份、模型控制异常、两个临时 Session 的租约窗口。身份去重断言也是新设计的合同，失败数量不等于独立漏洞数量 |
| `query-lifecycle-after-1` | 17 项，5 失败、12 通过，17.134 秒 | 我把旧 acquire 末尾 unreachable 断言错留到新 validate 函数末尾造成回归；修正函数位置，保留该失败记录 |
| `query-sampling-before` | 2 项全部失败，0.966 秒 | 可选语义样本两层 catch 吞 ChatCancelled/ChatTaskTimeout 的新增红例，随后修复 |
| `query-lifecycle-after-2` | 47 项，1 失败、46 通过，48.951 秒 | 唯一失败为我新增角色夹具使用旧 data_scope=all，当前 API 应为 workspace，已修正夹具而未放宽产品校验 |
| `dsh-query-cancel-before` | 5 项，2 失败、3 通过，4.885 秒 | 真实 DSH 范围校验期间取消后的缓存返回、SQL 返回后新增回执两处红灯；入口已有外层 guard 和只读 SET 两项正对照通过 |
| `query-lock-order-before` | 1 项通过，2.862 秒 | 排除疑点：实际 Session 默认不 autoflush，物理 SQL 次序正确；没有为假设新增产品修改 |
| `query-lifecycle-after-3` | 0 项，命令退出 4 | 我误写不存在的 `test_analytics_semantic.py`，仅收集失败，不算测试通过 |
| `query-lifecycle-after-4` | 249 项，1 失败、248 通过，287.156 秒 | 12 文件相邻组合；唯一失败为新增非管理员夹具缺少既有来源查看权，旧网关在进入 provider 前已拒绝。补齐来源查看权，仍不授分析执行权 |
| **`query-lifecycle-final`** | **55 项全部通过，61.299 秒，退出 0** | 最终 3 个新专项共 34 项 + 原 provider/governance/task-control 共 21 项；包含最终日额度扫描优化、权限正对照、失败计量与锁顺序。执行进程结束后冻结，未再跑大组 |

最终小组合文件：

- `tests/backend/test_query_lifecycle_control.py`：15 项。
- `tests/backend/test_query_lifecycle_governance.py`：14 项。
- `tests/backend/test_dsh_query_cancellation.py`：5 项。
- `tests/backend/test_database_provider.py`、`test_datasource_governance.py`、`test_analytics_task_control.py`：合计 21 项。

after-4 的其余相邻覆盖包括 `test_analytics.py`、`test_semantic_relation_contract.py`、`test_dsh_query_execution.py`、`test_data_profile.py`、`test_read_connection_pool.py`、`test_analytics_config_lifecycle.py`。该 249 项快照早于最后日计量扫描优化和权限夹具修正，不能把它写成“最终 249 项全绿”。最终大组交由根代理统一门禁，不重复占用并行验收 CPU。

首轮日志保留原捕获编码，后续命令显式 `PYTHONIOENCODING=utf-8`；断言及汇总以各轮 XML 为准。没有将宿主截图、未启动的服务或真实模型可用性写成通过证据。

## 最终冻结文件 SHA256

| 文件 | SHA256 |
| --- | --- |
| `backend/app/modules/analytics/queries.py` | `AC0F00D3305C025F3792F41BBC6ED978DDFD6C2CE18137AF1019A8C0A30E8C87` |
| `backend/app/modules/analytics/execution.py` | `4FA1FC04F356F68D4ADF033AE0B12A08EC0ACBC99A60CCF8750E3A6951A24CEF` |
| `backend/app/modules/analytics/governance.py` | `707DE4C893574EC22E4E20D3DFCA97E32939A4A035E672CA2AD1203BAE72ABBF` |
| `backend/app/modules/analytics/metadata.py` | `5B9A6BB2432B84372E005CC47A3C1169F0A1658F316586CF636416C0E39D1B99` |
| `backend/app/modules/analytics/semantic.py` | `858270D7971BC6CD9AFBEEBCD9A2DC61F153202BD299FEDAE01E1E4283F20410` |
| `backend/app/modules/analytics/connections.py` | `AE50AA8709F13DBB88005312E4ED1907E854BA0551B331E520F555C67A9A555A` |
| `backend/app/modules/analytics/dsh_query_execution.py` | `329D809BBAB850C01D5CD302B4E3028899EF249CBC370239FAAF81B554BC818E` |
| `backend/app/providers/tool/database_provider.py` | `8344825E57D296A378C3D67CD5ABF0F24AFBE32E6D119702F33906328A63EC4C` |
| `tests/backend/test_query_lifecycle_control.py` | `5916DEE2B4E30165BAFC34F7761F942B611B7F1CB09BF7A4538DBFC27C694F78` |
| `tests/backend/test_query_lifecycle_governance.py` | `50D5ACB7980F5CC5A3695AD13A182FAB0D2DE327201C41908D21EAEEFBB605A3` |
| `tests/backend/test_dsh_query_cancellation.py` | `C19FEE33D72FFDC8EAC749D0A9730400BB601CB30F6B31EB19EC6B5D00802240` |

未修改数据库结构、QueryHistory 外键、既有测试文件、正式 docs、统一门禁、图谱/Vue 或其他代理的 Worker 文件。没有提交或推送。本范围之外的来源重验仅单独登记，阻塞中 SQL 的服务端停止仍需进一步机制与实证。
