# 下一批查询生命周期：LIFE-N03—N05 只读细化

审查时间：2026-10-04 10:37（北京时间）。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-task-lifecycle`，分支 `codex/major-task-lifecycle`；读取时 HEAD 为 `782d346bc1e3df6a0e63dd199c0157c9ec47476b`。下方 SHA256 对应实际未提交工作树文件，而非仅该提交。

本次只读源码、既有测试及本机已安装驱动源码；仅新增本报告。没有修改产品、测试或正式文档，没有执行测试、SQL、模型、网络请求、浏览器或服务。本报告不增加批次十四的测试通过数。批次十四生命周期源码保持冻结。

## 结论与范围

1. **LIFE-N03：取消事件已到 Worker，但主业务查询没有传给 `execute_read`，返回后也缺少取消检查。** SQLite 驱动进度回调已有一部分支持；MySQL、PostgreSQL、Elasticsearch 当前执行分支不读取该事件。控制异常一旦已被正确抛出，主查询和网关已有保留异常身份的处理；需要修的是事件到异常、前置工作到执行及迟到成功之间的断点。
2. **LIFE-N04：查询租约在元数据/自然语言规划前取得，过期后没有 SQL 派发前复核。** 普通自然语言查询或冷元数据准备可消耗租约有效期。另一请求清除过期租约后取得名额，先前请求仍可继续发 SQL。当前 DSH 查询工具已强制显式 SQL 和 ready 元数据，不能把 DSH 工具调用前的模型规划时间都算入该工具租约。
3. **LIFE-N05：`database.query` 连接器直接调用底层执行器，绕过查询租约与按 `QueryHistory` 计算的日限额。** 它已有来源权限、字段范围、只读 AST 和最多 200 行限制，不能误报为全部鉴权缺失。只加 acquire/release 不足以修复日限额，因为当前 provider 不创建 `QueryHistory`。

三项为源码可确定的路径缺口；本轮没有新运行反例。下列“最小隔离反例”均是下一批应先执行的红灯设计，不是已取得的红灯结果。不扩展跨进程配置失效设计、不重做连接池版本屏障、不讨论图形坐标或数值序列化。

## 一、实际调用链与已有保护

### 1. DSH 停止信号与业务 SQL

`services/dsh_host/sessions.mjs:457–474` 的 `cancel(runId)` 设置 `run.cancelRequested`，取消模型请求，向 Worker 发 `run.cancel`；活动任务还会取消持有的作业和 agent，并等待 `run.execution`。返回的 `stopped`/run 状态描述宿主任务状态，不能直接作为数据库服务端停止证据。

`business_worker/protocol.py:108–116` 将 `run.cancel` 与 `tool.execute` 分开派发，SQL 工具占用执行线程不必阻止协议接收取消；`business_worker/context.py:25–39,79–96` 找到本 run 的 `TaskControl`，设置其 `cancelled` Event，并在运行上下文中安装该 control。

`business_worker/tools.py:129–153` 进入上下文后先 checkpoint，再走工具网关；收到真正的 `ChatCancelled` 时输出取消结果。`providers/tool/gateway.py:490–522` 也保留已抛出的 `ChatCancelled`/`ChatTaskTimeout`，不会把这些异常当普通 provider 失败吞掉。

业务 SQL 的主路径是：

```text
DSH tool.execute / ToolGateway 内置工具派发
  → analytics/dsh_query_execution.py:287–330 execute_dsh_query
  → analytics/queries.py:346–382 execute_query
  → queries.py:385–561 _execute_query_with_source
  → execution.py:162–247 execute_read
  → connections.py:178–206 readonly_connection
  → sql_engine / borrow_read_connection / connection_pool
```

`execute_dsh_query` 当前拒绝非 SQL 模式，并要求已同步的 ready schema；它内部不再先调模型生成 SQL。它的同轮成功查询回执缓存还核对来源、版本、工作区、用户、run 和本轮用户意图，且不复用截断结果。本报告不将这条有前提的同轮复用扩大描述为跨进程缓存漏洞。

工作流路径在 `workflows/nodes.py:1304–1321` 构造 QueryInput：数据库节点使用 SQL，其他相关入口可用自然语言，然后以 `workflows.execute` 权限进入 `execute_query`。`workflows/engine.py:400–437` 在节点间检查取消、适时归还读取批次，并通过条件状态更新避免已取消节点被普通完成回执覆盖；这不能替代正在执行的 SQL 内部取消。

Worker 的内存 ASGI 适配 `business_worker/application.py:108–148` 在请求体后等待本地响应完成才给 `http.disconnect`；这里没有把浏览器断开直接转成 TaskControl/数据库取消。连接断开与用户显式停止必须区分，不能据此引入“页面断开就取消整个长任务”的新合同。

### 2. 当前连接器测试确实走哪个入口

正式 `connector/router.py:231–241` 的 `POST /{connector_id}/test-invoke` 调用的是 **`capabilities.service.test_connector`**，不是旧 `connector.service.test_invoke`。

```text
connector/router.py:231–241
  → capabilities/service.py:885–923 test_connector
      connectors.execute + 连接器资源范围 + 指定版本工具
  → capabilities/service.py:926–1001 _execute_tool_test
      Run / Step / ToolCall 记录、独立幂等键
  → gateway.execute_spec（gateway.py:471–489）
      工具授权、参数校验、secret 准备
  → database_provider.invoke（database_provider.py:60–115）
      datasources.execute + active + 只读 SQL / 列范围
  → execute_read（直接调用，没有查询治理租约/QueryHistory）
```

`capabilities.service.test_method:1004–1029` 也复用 `_execute_tool_test`。其 Run/Step/ToolCall 留痕并不会增加 `_usage_counts` 查询的 QueryHistory 日计数。因此后续必须在真实入口经网关做反例，不能只对已废弃 service helper 测试。

### 3. 已有防线应保留

- `queries.py:366` 已有初始 checkpoint；`execute_query:376–382` 在 finally 释放取得的租约，异常分支保留取消/超时身份并更新记录。
- `connections.py:117–136` 对建连和驱动设置预算：MySQL connect/read/write timeout；PostgreSQL connect、statement、lock timeout 和默认只读事务。池还有短等待、pre-ping、回收和 rollback。不能为满足“长任务做完”为由删除单次 I/O 预算。
- `readonly_connection:191–206` 设置只读事务和 MySQL/MariaDB 执行时间限制；正常退出 rollback，任何 BaseException 使连接 invalidate 后重抛。
- SQLite 还有 query_only、禁止扩展加载、authorizer 和 progress handler；SQL AST/表列授权在执行之前。
- 池对异常借用/旧版本归还有清理逻辑。本轮没有证据支持“所有取消都泄漏连接”这种结论。问题在阻塞调用尚未返回时，finally 尚不能运行，以及返回后的取消状态未检查。
- 治理 acquire/release 只对平台库锁冲突做有限重试，并非业务 SQL 自动重试；保持该区别。

## 二、LIFE-N03：取消传播与迟到成功

### 源码证据与后果

`queries.py:479` 调用 `execute_read(ctx.db, source, safe_query, metadata.tables_json)` 时没有传入当前 `TaskControl.cancelled`。查询开始时的 checkpoint 之后，元数据读取、语义准备、SQL 生成、执行和 fetch 可发生取消；SQL 派发前和返回后没有对应 checkpoint。记录随后在 `queries.py:519` 附近写 success，关联 Run 也可收口 completed。

所以，“已有 control 且停止消息已接收”不等于“正在执行的主查询知道取消”，也不等于“迟到 rows 不会写成成功”。这条断点涵盖 DSH 显式 SQL 主路径；不依赖自然语言规划慢才出现。

`execution.py:202–205` 的 SQLite progress handler 每 1000 个 VM 操作检查 Event/本次查询耗时；但主路径没有传 Event，简单查询也可能在第一次回调前结束。事件触发 progress handler 后，驱动通常以 interrupted 的数据库异常退栈；当前 `except Exception` 将其转成 datasource failure，而不是根据本次明确的取消事件恢复 `ChatCancelled`。不能把任意 OperationalError 都重分类成取消，应同时有本次 control 状态和被中断的可靠证据。

MySQL、PostgreSQL、ES 分支没有检查 Event；关系数据库执行后的 fetch/转换与最终返回也没有 checkpoint。`except (AppError, ChatCancelled, ChatTaskTimeout)` 能保留异常，只说明“已经有人正确抛出时不会损坏”，不说明事件传播完整。

前置工作还存在两个具体异常身份断点：

- `metadata.py:175–183` 的通用 `except Exception` 包装来源连接失败；若检查器未来抛控制异常，需明确保留身份，不能被这里重新包装或误记来源故障。
- `semantic.py:692–700` 的 build embedding 和 `900–904` 的 query embedding 在通用异常后降级关键词/空向量。普通模型不可用可以继续保留降级；`ChatCancelled` 与 `ChatTaskTimeout` 不应被当作可忽略错误。

元数据检查 `metadata.py:64–129` 多次调用 inspector，ES 映射读取在 `136–158`；这些步骤目前没有逐步 checkpoint。取消后应至少停止派发后续表/字段读取。语义关系读取 `semantic.py:528–535` 也是多次读；本批先保护此次查询实际经过的确定前置边界，不泛化重构所有采样任务。

### 本机驱动源码能证明什么

| 驱动/路径 | 已见支持与限制 | 下一批可保证/不可声称 |
| --- | --- | --- |
| SQLite | 当前 progress handler 可在 VM 回调中停止；短语句未必触发回调 | SQL 前检查、回调中断正确分类、返回后拒绝迟到成功；本次未执行 SQLite interrupt 行为验证 |
| PostgreSQL / psycopg | 本机 `connection.py:312–336` 有 `cancel_safe(timeout=30)`；libpq 能力不满足时退回 `cancel()`。`_connection_base.py:296–314` 有取消请求实现 | 可研究对**当前借出连接**的所有权受控取消；不能从 Python 包版本推断 libpq runtime 能力。驱动文档明确取消请求成功不保证服务端已取消 |
| MySQL / PyMySQL | `close:421–435` 发送 COM_QUIT 后关闭；`_force_close:437–460` 关闭 socket。`kill:592–595` 本质是再执行一条 `KILL` SQL | 关闭连接不是已收到服务端停止确认；不得在同一繁忙连接上从另一线程执行 KILL。另开控制连接、权限及 owner 校验属额外设计，不默认纳入最小修复 |
| Elasticsearch SQL | `execution.py:184–195` 有 request/page timeout；拿到响应 cursor 后才 `_sql/close` | cursor close 不等于取消尚未返回的请求；HTTP 客户端退出也不证明远端查询终止 |

只读搜索还确认本机 PyMySQL 错误常量含 1317/3024、psycopg `QueryCanceled` 为 57014；错误码的存在不是本环境数据库实际行为验证。不要只凭某个错误码就推断是本用户取消，可能是数据库超时/管理员操作。

**明确边界：**最小修复可以保证取消后不再派发新的已受控 SQL、阻止迟到结果被当成成功、正确传播控制异常、在拥有执行资源的调用退栈后清理。无法仅凭这些代码保证阻塞中的远端 SQL 当场停止；连接 invalidate、租约删除、宿主 run.cancelled、客户端超时都不是服务端停止的单独证据。工作线程尚未退栈时，应保留“正在停止/停止状态待确认”的语义，不能虚报底层已停止。

### 下一批最小隔离红例（尚未运行）

1. 经真实临时平台记录和 DSH tool.execute 上下文调用显式 SQL；ready 元数据固定，替换 execute_read 为有计数的驱动桩。桩记录收到的 Event，在调用期间设置当前 control.cancelled 后返回 rows。断言 Event 传递、返回取消、没有 success QueryHistory/成功查询回执、租约释放、无第二次业务 SQL。
2. 用假的 readonly SQLite driver 捕获 progress handler。预置取消应阻止实际 exec_driver_sql；中途事件触发并由假驱动抛 interrupted 时应形成 `ChatCancelled`。正对照：普通驱动异常仍为 datasource failure；没有用户取消的查询预算耗尽仍保持正确超时身份；短语句也靠前后 checkpoint 保护。
3. 两张表 inspector 计数桩：第一个检查步骤设取消，下一次 inspector 调用次数保持零；控制异常穿过 `source_metadata` 原样上抛。已产生的 CAS 派生状态不因取消误记 ready 或把旧配置写回。
4. build/recall 的 `embed_texts` 分别抛实际 ChatCancelled/ChatTaskTimeout，断言向外传播且不写 ready 索引；普通模型错误仍按既有策略关键词降级。
5. 取消/超时/普通失败均保留已有 finally 清理；增加迟到结果和取消身份覆盖，不仅给 execute_read mock 直接 side_effect=ChatCancelled。必要时另用极小临时 SQLite 做一次可控中断，不使用业务库、递归大 SQL 或长时间 sleep。

现有 `test_analytics_task_control.py:34–69,97–131` 主要覆盖 model/readonly/execute_read **已经抛出**控制异常时的身份与记录收口；不等价于上述事件传播验证。既有连接池取消用例检查读取之间取消，并未证明中途 driver 中断。

## 三、LIFE-N04：SQL 前有效租约与尝试计量

### 源码证据

`queries.py:376–382` 先 acquire，再进入内部函数；内部 `407–418` 创建并提交 QueryHistory/Run，随后 `420–455` 读取元数据、自然语言语义与 SQL 规划，最后 `479` 才发 SQL。

`governance.py:89–112` 清理过期租约、统计未过期 active，以及 UTC 当日 QueryHistory 数量。`_reserve_query_lease:377–407` 先锁数据源，再锁 policy，验证 active/daily 后插入租约并提交；有效期为 `max(120, min(900, timeout + 120))` 秒，配置 15 秒时是 135 秒。没有续约或 SQL 派发前重新确认。资源状态查询也可清理过期租约。

因此存在确定的时间顺序缺口：A 在前置阶段耗尽租约，B 清理过期 A 并获得唯一名额，A 恢复后仍继续发 SQL。是否在真实模型/库上达到该时长，本轮未测；不应通过等待数分钟的脆弱用例验证，应该用时钟和阶段屏障。

DSH 显式 SQL + ready schema 正常路径没有内部模型阶段；普通自然语言接口及可能触发自然语言查询的工作流才是模型前置时间的直接入口。检查器冷同步仍需具体区分。

### 最小修复合同与容易误修之处

- 在**将要派发业务 SQL**的边界拥有有效、归属正确的租约；租约已过期/被替换时，不可继续使用旧令牌。保持 source → policy 锁顺序，释放只能释放本请求自己的租约，不删除 B 的名额。
- 不简单延长 TTL 代替验证。还需清楚界定前置阶段占用与真正查询占用；若移动 acquisition，需要同步考虑 QueryHistory 的计量时机。
- 日限额目前按 QueryHistory 数行。把 acquire 直接搬到初始历史记录创建后，日限额为 1 时可能把第一次请求自己计为已用并拒绝自己；过期后的重获也不能双计同一次尝试。应绑定稳定 attempt/history 身份，明确预留、执行、失败的计量合同。
- 当前 acquisition 提交发生在 QueryHistory 插入之前；并发名额大于 1 时，两请求在历史记录尚未写入的间隔都可能看到 today=0。这是同一次计量设计需要覆盖的竞争窗口，尚未取得运行反例，不能只测试串行 dailyLimit。
- 对确实在 driver 中运行超过 TTL 的查询，单次 SQL 前验证不能承诺始终准确反映所有服务端活跃 SQL。若下一批不实施执行中续约，明确保留该边界；不能用客户端 read timeout 推断服务端已结束。
- 保留平台库死锁有限重试；任何重获租约都不自动重试业务 SQL。失败记录与清理失败不应掩盖原始失败。

### 下一批最小隔离红例（尚未运行）

1. 临时平台 SQLite 使用真实独立 Session 和正式 execute_query；自然语言模型仅本机内存桩。A 取得 maxConcurrency=1 的租约后卡在模型桩，推进**测试时钟**超过租约期限，让 B 在独立 Session 获得名额，再释放 A。断言 A 的业务 execute_read 计数为 0，不能使用过期令牌执行或删掉 B；不实际等 135 秒、不连模型。
2. ready 元数据显式 SQL 正对照：只取一次有效租约，执行一次 SQL，正常释放；已取消请求不为等待名额持续重试。
3. dailyLimit=1 下第一次真实服务请求可以执行且只计一次；同一 attempt 的前置准备、派发校验和失败收口不双计，第二次新 attempt 被拒绝。
4. 通过真实临时 Session 的阶段屏障核对 acquire 提交→历史插入竞争窗口，保留数据库事务事实。SQLite 用例只能证明本实现的序列/CAS与计量结果，不能代称已验证 MySQL RR 行锁行为。

既有 `test_datasource_governance.py:113–171` 覆盖普通查询满额和 daily limit，`173–198` 覆盖平台锁冲突重试；没有上述过期后迟到派发/本次历史自计数/connector attempt 覆盖。

## 四、LIFE-N05：database.query 必须走同一执行治理

### 现有权限与缺失治理分别是什么

`database_provider.py:60–115` 的执行顺序包括：还原 actor 权限；从 connector 的已保存配置取 source；`get_source(..., permission="datasources.execute", active=True)`；query 先做只读 AST 解析；元数据按列范围过滤；SQL 按真实字段/参数/库名授权；行上限 `min(source.maxRows, 200)`；最后直接执行。

它确实向 execute_read 传了 `current` control 的 cancelled Event，比主查询多这一层；但仍受 N03 中底层 driver/返回后检查的限制。

此函数没有 acquire/release，没有 QueryHistory。网关/能力测试的 Run、Step、ToolCall 也不会被 `_usage_counts` 的 `select(QueryHistory)` 计入。因此用户可通过这条合法工具路径绕过管理页设置的并发/日限额。

最小实现可抽出共享的受治理 SQL 调用，或给 provider 显式接入同一 guard 和单次尝试计量。不能机械改成 execute_query 默认入口而引入额外 `analytics.execute` 权限、改变最大 200 行、输出 `_result` 格式、既有只读校验或禁用源行为。也不能只加租约让并发测试变绿，却让连续 connector 查询仍永远 today=0。

缓存的 `list_tables`、`describe_table` 不应未经产品合同确认就当成一次业务 SQL 查询扣费；冷元数据若连接驱动需明确属于 schema 同步还是 query，本次重点是 query，避免一次实际 query 被多记成几次。

### 下一批最小隔离红例（尚未运行）

1. 正式 connector `test-invoke` → capabilities → gateway → database provider 链路，临时平台预先占满该源租约，execute_read 用调用计数桩。预期业务 SQL 0 次，结果带稳定 datasource_busy 类错误；测试 HTTP 200 的 ToolResult 失败形态，不能一律期待 endpoint HTTP 429。
2. 临时 QueryHistory 达到今日限额，同入口调用 query，SQL 0 次。再测**没有预置历史**的 dailyLimit=1 连续两次 query：第一次创建恰好一份计量证据，第二次拒绝。此项能识别“仅套租约”的不完整修复。
3. 成功、普通驱动失败、ChatCancelled、ChatTaskTimeout 均释放自身租约，原始稳定错误身份不丢；每次 attempt 最多一份计量，不触发任何业务 SQL 重试。
4. 权限正对照：有连接器执行及 datasources.execute、没有 analytics.execute 的既有合法用户仍可执行；隐藏列/停用源仍在 SQL 前拒绝；同工作区来源范围与工具版本限制不放宽。缓存 list/describe 维持既有结果合同。

现有 `test_database_provider.py` 覆盖结果类型、截断、字段范围和停用源，没有治理满额/日计量反例。测试应从正式 router/service 入口或生产网关执行，而非只 monkeypatch `_usage_counts` 得到预期值。

## 五、下一批建议的最小所有权与顺序

1. **取消边界负责人：** `analytics/queries.py`、`execution.py`，必要时 `connections.py` 的执行 control 接口；`metadata.py` 和 `semantic.py` 仅控制异常身份/明确步骤 checkpoint。新增独立取消传播测试。先补红，再修事件传递和迟到成功，不改已冻结的配置 CAS/池屏障。
2. **治理负责人：** `analytics/governance.py`、必要的 query 调用集成，以及 `providers/tool/database_provider.py`；若抽共享 guard，用新小模块明确职责。补有效租约、稳定 attempt 计量与正式 connector 入口测试。N04/N05 共用计量模型，宜由一人串行持有，避免各自记一次历史。
3. **宿主/驱动取消边界：**先只读核对已有 run 状态，不默认改 DSH 生命周期或增加 MySQL KILL 控制连接。确定需要异步 driver cancel 时另行说明所有权、绑定当前连接与非复用竞态；不得在归还池后对同一裸连接发迟到 cancel。

实施先把“请求取消→不发新 SQL/不认迟到成功”做成确定合同，再统一 SQL 前租约与 connector 计量。所有取红使用临时平台 SQLite、内存模型/驱动桩、显式调用计数和可控时钟；不需要连接真实 szh_new、局域网库或模型。任何 driver 行为未实测，报告保留未实测标记。

## 六、实际阅读范围与 SHA256

下面是本次实际阅读，不把搜索命中或旧审查概括成全文通读。路径相对上述独立树；范围是本次读取时的行号，后续改动须按 hash 重新定位。

| 文件 | 实际范围 | SHA256 |
| --- | --- | --- |
| `backend/app/modules/analytics/queries.py` | 346–580，相关调用搜索 | `0EA8424168C219C512A4EEC9646D42E47B2928EFF84737A41FC425D81495F5FB` |
| `backend/app/modules/analytics/execution.py` | 162–247 | `2D3FBEAA097F7CD3295F2341A683ACF43AD6F846C40D814573FCC68596709BD7` |
| `backend/app/modules/analytics/governance.py` | 62–113、377 至文件末尾相关租约/资源状态段 | `C6432A1A7642BF4E1C391E47194672CC4E328BDB2FED28BEF266B3F778A6D6AA` |
| `backend/app/modules/analytics/connections.py` | 106 至末尾；110–206 再定位 | `BF20406D81747480DD430C2520CB9C90B2E6C1B661BE7A1BD6AF6718F0D6002B` |
| `backend/app/modules/analytics/metadata.py` | 56–207 | `048844FD2594B6065373F4217CF7174F120A0667E3B5646E7C9D5BDF50558A85` |
| `backend/app/modules/analytics/semantic.py` | 451–535、681–711、817–860、884–911，取消/读取位置搜索 | `15DB17DE2CE4682A9BC660B5887F7C9770F5F35EC9BA2F5919029E7DDAF80E40` |
| `backend/app/providers/tool/database_provider.py` | 全文 1–115 | `637222A2628A424B848991AA0A395A025F69643699A76DE36ACFC2D3012A457F` |
| `backend/app/providers/tool/gateway.py` | 444–466 派发片段、471–541 | `F48BBB7F591D68E9510DD1938477E2586AED41F26A2B34C287286DE788659219` |
| `backend/app/modules/runtime/task_control.py` | 全文 1–117 | `6DFB6801618EDF9229238E94D6EF83D9E502C1F71AAF2CE39A9386121E2D365C` |
| `backend/app/business_worker/context.py` | 全文至 96 | `1036FEF26440AD1280680F3C80468BD4AA971AB97B991D65EF2F8676033FA8E1` |
| `backend/app/business_worker/application.py` | 94–148 | `189FA665417DB3B701BF52A8F912573CDE5D0839817C66A3A9E0022AC02EEB4F` |
| `backend/app/business_worker/tools.py` | 129–155 取消上下文段及定位；此前数值审查另读 66 至末尾 | `28AF4F034130DCF9F6A69144C683E28A47947E39B212AA17BE8FB2B28929E06E` |
| `backend/app/business_worker/protocol.py` | 102–124 派发段 | `C6F2111B24CA2B5394C3CA9FCBD7EF696F1D8A23EF3D704FC6F1DA584ED01AD2` |
| `backend/app/modules/analytics/dsh_query_execution.py` | 275–330 | `0D564ECEF2AA7AF85B7C0EC516A400C6117639D2F8BB229D10DE9A386B727D34` |
| `backend/app/modules/analytics/models.py` | 1–184 | `BCB0A306C62E9DBF9D75459ACE70E79ED690055DDDF7BFD2DA7BDCAD20DB59BA` |
| `tests/backend/test_analytics_task_control.py` | 1–82、97 至末尾相关控制异常测试段 | `C7C4E58563E248AE82A8E43D65E26035072F22C9AC09F6DE0F664B7AAF7E8123` |
| `tests/backend/test_database_provider.py` | 全文 | `581392D0F512A758C7619114A9C89C03C000D8EC3A10C197980D1C5513032585` |
| `tests/backend/test_datasource_governance.py` | 113–198 | `971D0B83BA8A895B9915616376998C81C31780550ED8E6A98E7A678798196697` |
| `services/dsh_host/sessions.mjs` | 450–482 及 cancel 引用搜索 | `038C3097783903354BF95F1EA0C354FE8B8B7A6F4BB8E85220D40DAB060B5BC4` |
| `backend/app/modules/connector/router.py` | 231–252 及工具引用搜索 | `BDE9192D2E26928862265716B6882C1B1F67A6AC96A9ABFB14A8B4C89D9BD671` |
| `backend/app/modules/connector/service.py` | 196–224；确认不是当前 router 测试入口 | `EB5FB87FF3941647E68E04B01AA2463D8E0604CA68E5CCF9A33A846C23CF576D` |
| `backend/app/modules/capabilities/service.py` | 698–700、885–1029；额外输出的 703–783、1032–1045 只作上下文 | `5468A5509D2AEDD33084DD7E2D3EF1D6C17E7026FDE9B90521E29BDA5BC75661` |
| `backend/app/modules/workflows/engine.py` | 400–437 及取消搜索 | `133F050B43BF617B444C0EBC8ACEDF2185C1AF9193F3E239031F269C800A551C` |
| `backend/app/modules/workflows/nodes.py` | 1304–1322 | `EB745D9349FE3512FE18F09D47548E5881D17FB61678C4526D0220EB0ABB8FC8` |

本机驱动读取自 `E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Lib/site-packages/`，未导入模块、未访问网络：

| 文件 | 阅读范围 | SHA256 |
| --- | --- | --- |
| `psycopg/connection.py` | 312–343 | `5DFACD2EF35F748C5A60C07C8BF5D6E6A2FA25438EE1794591B4A4E4E70AFD7E` |
| `psycopg/_connection_base.py` | 296–319 | `7ADD2044E037E5042F582C2A1436FBB5D00D41DB2CEA20980D6F270E5469D5E0` |
| `pymysql/connections.py` | 421–463、592–600 | `649BB1E0C3E3E797B2B6CD4AA35B2084EDD75C4B2F6C5B1900AE696285B9ACCE` |

此外仅搜索 `pymysql/constants/ER.py` 中 QUERY_INTERRUPTED/QUERY_TIMEOUT 和 `psycopg/errors.py` 中 QueryCanceled 常量，未通读，未据此宣称服务端行为验证。对照基线为已入库的 `docs/audit/review_20261004/followup-query-lifecycle.md`，本报告细化其 N03—N05，不改变本批已通过范围。
