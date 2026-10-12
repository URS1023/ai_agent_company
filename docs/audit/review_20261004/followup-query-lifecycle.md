# 下一批只读审查：查询连接生命周期、取消与版本隔离

日期：2026-10-04。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-capability-contracts`。HEAD：`112bdd3efdc242a95f174e497922b42e56d6f1a9`，同时存在已冻结的批次十三改动；以下 SHA256 指本次实际读取内容，不用 HEAD 冒充工作树内容。

本生命周期审查仅阅读本地源码和既有测试，未连接任何数据库/模型/8011，未启动服务或浏览器，未针对生命周期运行查询或新测试，未修改本审查产品/测试。使用 `code-review-excellence` 技能；没有依据技能增加审批或外部操作。结果不纳入批次十三已经通过的验收范围。其间根代理暂时切换本代理去处理图表数组撤权补洞；那项单独执行的产品修改和156项隔离测试见 `.implementation/fix-analytics-chart-shape.md`，不是本生命周期发现的运行验证。本报告在补洞冻结后恢复完成。

## 结论及证据强度

发现 5 项可由代码路径确认的生命周期/权限合同缺口，其中配置并发写回和旧池版本倒退优先级最高。这里的“静态确证”表示遗漏条件和可成立的调用次序已在源码核对，**本轮未执行隔离复现**；不把客户端超时等同于数据库服务端查询已终止，也不把正常保留的工作流连接误报成泄漏。

| 编号 | 级别 | 问题 | 证据强度 |
| --- | --- | --- | --- |
| LIFE-N01 | P1 | 耗时元数据同步整包写回旧 connection_config，可覆盖并发更新后的字段权限/主机/version | 静态确证无版本条件；待双 Session 隔离回归 |
| LIFE-N02 | P1 | 旧池退役证据随 entry 删除消失；迟到旧 key 可重建旧池，并先退役新池 | 静态确证状态机；无需真实驱动即可复现 |
| LIFE-N03 | P1 | DSH 主查询没有传递取消事件，驱动与元数据同步缺取消检查；已接事件的 SQLite 分支又将中断包装成数据源失败 | 静态确证传播/分类缺口；服务端停止程度另需驱动验证 |
| LIFE-N04 | P1 | 模型/元数据阶段提前领取的固定租约可先过期，旧任务随后仍执行 SQL，突破治理并发 | 静态确证无续租/执行前重占；假时钟可复现 |
| LIFE-N05 | P2 | database.query 连接器走同一读适配器，却绕过治理租约/日限额 | 静态确证直接调用路径；隔离网关回归即可证明 |

## 实际执行路径

**当前 DSH 业务工具**：`business_worker/protocol.py:107-116` 将 tool.execute 放在工具线程池，run.cancel 独立及时分发 → `business_worker/tools.py:69-143` 创建独立平台 Session、当前用户和 run_context → ToolGateway → `analytics/dsh_query_execution.py:287-330` → `queries.execute_query:346-382` → `_execute_query_with_source:385-561` → `execution.execute_read:159-244` → `connections.readonly_connection:177-206` → `sql_engine:110-174` → `pooled_engine/borrow_read_connection`。取消控制包能及时置 Event，但这并不代表同步数据库调用会观察该 Event。

**分析页/组件 HTTP**：当前宿主仍将业务 HTTP 转发给 Worker 内存 ASGI `application.py:108-148`；不是依赖旧 Uvicorn 独立聊天引擎。`analytics/router.py:194-210` 的 query、execute-sql 同样进入 execute_query。`application.receive:120-126` 只在本地 response 完成后产生 http.disconnect，未包含浏览器断开/宿主丢弃请求到数据库取消的传递。因此用户关页面不能作为数据库已停止的证明。

**数据库连接器**：`database_provider.py:60-115` 先检查数据源执行权、解析 SQL、收窄元数据，再直接 execute_read；它传入 TaskControl.cancelled，但没有 queries 层的治理租约。此路径与 DSH 的 query_business_data 不是同一条入口合同。

**工作流批次连接**：`connection_pool.py:214-228` 为一次工作流建立主体隔离的 ReadBatch；成功查询后留住一条连接是明确复用设计，并非泄漏。常规异常、切换源、进入模型/等待节点、离开批次均有归还路径。工作流入口与节点只有搜索级核对，本轮未通读整个工作流引擎。

## LIFE-N01：旧元数据任务可以把权限配置写回旧版本

**位置**：`metadata.py:170-206`，特别是 202-206；`sources.py:235-262`；`models.py:439-456`；`db.py:55`。

```python
# metadata.py:202-206
source.connection_config = {**(source.connection_config or {}), **profile}
flag_modified(source, "connection_config")
ctx.db.flush()
```

source_metadata 在结构发现、可选低代码补充、可选语义索引之后，仍读取最初 ORM 对象的 connection_config，整包覆盖 JSON 列。DataSource 没有 ORM version_id_col，平台 Session 使用 expire_on_commit=False；此更新也没有来源版本的 WHERE 条件。只有管理员编辑入口使用 version 条件更新，并不能保护另一个元数据任务的直接 flush。

成立次序：A 读取 v1/旧 allowedColumns 并开始同步 → B 经正式 update_source 将配置收窄成 v2 并提交、退役旧池 → A 完成耗时检查，使用仍在 Session 中的旧 source 将 v1/旧字段范围/旧地址和新 profile 一起写回。HTTP 同步入口随后 commit；普通 tables/info GET 在缓存缺失时也会同步并 commit（router:148-177）。这会使新授权收窄被覆盖，同时把旧结构重新标为 ready。即便相邻场景某数据库事务隔离让写入报锁冲突，也不能据此认定代码拥有版本保护。

**最小修复所有权建议**：`analytics/metadata.py`，必要时 `sources.py` 的元数据更新辅助函数；新增 metadata 并发合同测试。同步前捕获版本/连接指纹，外部工作结束后以条件更新只保存派生元数据字段；版本改变必须废弃旧结果，不能覆盖连接参数与权限。结构快照及派生 profile 应保证同一版本的一致提交。

**隔离复现建议（未执行）**：两条临时平台 SQLite Session，元数据 inspection/model 全部用受控桩；A 的检查桩暂停，B 用正式 HTTP 编辑将 allowedColumns 收窄，A 放行后提交。断言 v2/收窄配置未倒退、旧结构不被标作 v2、有界错误且业务连接/模型真实调用为 0。另测改主机、停用、删除期间的迟到同步。MySQL/Postgres 的实际锁行为需要单独验证，不能由该 SQLite 结果代替。

## LIFE-N02：退役状态不是单调版本屏障

**位置**：`connection_pool.py:79-97,108-115`；`connections.py:137-156`；`sources.py:256-259`；`queries.py:376,419-479`。

```python
# connection_pool.py:82-96 的顺序
for old_key, entry in list(_pools.items()):
    if old_key[:2] == key[:2] and old_key != key:
        entry.retired = True
        if _idle(entry):
            _remove(old_key)
entry = _pools.get(key)
if entry is not None and entry.retired:
    raise ToolExecutionError(...)
if entry is None:
    entry = PoolEntry(factory(), time.monotonic())
```

retired 只存在于池 entry；旧池空闲或最后借用者归还后会被删除，没有保留“这个源的旧版本已禁止”依据。某个持有旧 source ORM 对象的长任务稍后进入 sql_engine，仍能用旧连接信息生成旧 key；entry 已删则重新调用 factory。它还会在检查自身旧 key 是否已退役之前，先把同源的新 key 退役：旧 entry 尚在时虽最终抛错，也可能已影响合法新池；旧 entry 不在时则直接重建旧连接。

这与“允许已经派发的旧查询结束”不同：这里是更新完成后才再次借出/新建。fingerprint 包含密码、配置版本能避免正常新请求误用旧池，但哈希本身不能判断新旧方向。source 对象只在入口读取；长元数据/模型阶段后没有重新取得当前配置与权限。owned_record 查询也没有 populate_existing，因此同 Session 已加载对象不能凭再次 SELECT 自动视为最新。

**最小修复所有权建议**：`connection_pool.py`、`connections.py` 及执行前当前源版本复核的最小入口。池层需要单调的源代际/失效屏障，而非“后来请求的 key 一定更新”；同时新 SQL 派发前复核当前持久配置，不能让任意旧对象刷新代际。

**隔离复现建议（未执行）**：纯假引擎/假 pool，无 SQLite 连接也可做。借 v1→退役并回收→借 v2→迟到借 v1；断言 v1 不再触发 factory、v2 不被迟到 v1 退役。再覆盖旧 v1 忙、新 v2 活跃、多个工作区同名源、凭据变更。现有 `test_read_connection_pool.py:59-71` 仅测旧 entry 尚存在时拒借，缺“回收后迟到旧 key”和“旧 key 先误伤新池”。

## LIFE-N03：取消没有完整到达正在运行的数据库操作

**位置**：`business_worker/context.py:37-39`、`tools.py:128-153`；`queries.py:366-368,419-479,511-530`；`execution.py:159,198-244`；`database_provider.py:111-114`；`metadata.py:64-129,175-183`。

1. DSH run.cancel 只置 TaskControl.cancelled。queries 在最开始 checkpoint 后，向 execute_read 传递的只有 db/source/query/metadata，没有 cancelled；所以主 DSH 查询连已有的 SQLite progress handler 都无法响应该 Event。
2. MySQL/Postgres 的执行与 ES 请求分支不读取取消控制，也不使用任务剩余预算去缩短单次调用。现有会话 statement/read timeout 是独立的 1–60 秒预算；用户取消后仍可能等该次调用自然结束或超时。
3. execute_read 返回后直到 QueryHistory/嵌套 Run 被写为 success/completed，没有新的 checkpoint。因此执行中收到取消、查询随后正常返回时，业务查询仍可持久化成功回执。外层原生运行最终显示什么状态需另核对宿主，本报告不把这条子记录行为冒充整轮状态结论。
4. 连接器 database.query 虽传了 Event，但 SQLite progress handler 只返回中断整数；驱动抛 OperationalError 后被 execution:243-244 包装成 failure_domain=datasource，并未依据事件恢复 ChatCancelled。上层可能按数据源故障停止后续源访问，取消语义丢失。
5. 元数据同步每表多次 inspection，无取消检查；若未来在检查适配器抛 ChatCancelled，metadata:182 的通用 Exception 分支还会把它包装成 connection_failure。仅有每条底层语句预算不等于整个元数据同步能及时停止。
6. HTTP 断开信号没有从 Worker ASGI receive 传播到数据库。该边界应明确契约，而不是宣称关闭页面等同于停止查询。

**最小修复所有权建议**：`queries.py`、`execution.py`、`metadata.py`、必要的 `connections.py` 驱动取消辅助；DSH/连接器入口测试。统一控制检查：派发前、取结果后、元数据迭代之间；SQLite 传 Event 并保持取消异常身份。MySQL/Postgres/ES 的驱动取消必须具体设计和测试，不能粗暴从别线程调用同一个 SQLAlchemy Connection，不能用超时后台线程遗留查询冒充停止。

**隔离复现建议（未执行）**：先用阻塞驱动桩在已开始执行后置 Event，断言主 DSH 传入事件、取消不会落 success、不再派发后续元数据请求、连接归还一次。SQLite 中断先用假 progress handler/模拟驱动异常验证映射；需要真实中断再使用一次性本地 SQLite 小工作量。不得用生产库长 SQL 来探取消。

**需要真实驱动验证的部分**：客户端 invalidate/close 后，MySQL/Postgres 服务端是否仍存在执行、锁或残余事务；MySQL MAX_EXECUTION_TIME/MariaDB max_statement_time 对特定查询/等待场景的实际效果；网络超时与服务端终止是否同一时刻。本轮只确认缺取消链，不声称已观察到永久服务端孤儿 SQL。

**既有测试局限**：`test_analytics_task_control.py:34-69,97-131` 由 Mock 直接抛 ChatCancelled/ChatTaskTimeout，证明异常一旦抛出能保留身份，未证明执行中 Event 能触发异常。`test_read_connection_pool.py:210-268` 的 cancelled 用例是在两次读取完成之后让工作流检查点返回取消，不能覆盖一条运行中的 SQL。

## LIFE-N04：长前置阶段可使治理租约先于 SQL 失效

**位置**：`queries.py:376-380,419-479`；`governance.py:89-106,377-406`；`task_control.py:39-45,90-104`。

治理名额在元数据/模型工作之前领取，expires_at 固定为 now + max(120,min(900,timeout+120))；常见 timeout=15 时为 135 秒。查询执行中没有续租，派发 SQL 前也没有再次确认租约仍有效。另一请求调用 _usage_counts(cleanup=True) 会删除过期租约并释放名额，而原任务仍能继续走到 execute_read。

因此长元数据/多次模型工作超出租约期时，配置 max_concurrency=1 仍可出现旧任务与新任务同时取数。每池上限 2 并不能替代治理值 1，多个工作进程的局部池更不能构成统一并发计数。此处不是建议给整轮对话重新添加固定超时。

**最小修复所有权建议**：`analytics/governance.py` + queries 执行阶段；补假时钟/临时平台租约测试。将实际数据库执行租约与前置准备区分，或有受控的续租/重新预占；一旦租约过期必须重新取得许可，不能无证据继续旧执行。

**隔离复现建议（未执行）**：假时钟跨过 expires_at、第一任务的模型桩暂停，第二临时 Session 领取名额，释放第一任务。用执行桩记录重叠数，断言不能出现两个 SQL 同时派发；不 sleep 135 秒、不连业务库、不做压力循环。

## LIFE-N05：database.query 未走治理名额

**位置**：`database_provider.py:83-114`；`connection_pool.py:130-145` 的并发上限说明；`queries.py:375-382`。

数据库连接器做了数据源权限、SQL 白名单和字段收窄，但直接 execute_read，既不调用 queries.execute_query，也不 acquire/release_query_lease。于是治理页面 max_concurrency/daily_query_limit 只约束分析/业务查询入口，不约束这条正式连接器入口；对 SQLite/ES 的 NullPool，局部连接池上限也不能补上治理限制。

**最小修复所有权建议**：`providers/tool/database_provider.py` 与治理执行公共辅助，不把所有路径机械套成需 analytics.execute（连接器原权限是 datasources.execute，必须保留）。按一次业务 SQL 预占/释放，避免重复嵌套计数；元数据工具是否计入单查询额度需要明确产品合同。

**隔离复现建议（未执行）**：临时平台中先用治理记录占满/耗尽限额，经实际 ToolGateway 调 database.query，驱动全桩，断言被拒绝且执行调用数 0；正常执行、异常、取消均释放名额。此问题不等于 SQL 可写或跨工作区访问，已有权限/只读检查仍存在。

## 已核对的正常保护与未确认项

- 普通 readonly_connection 正常 rollback、异常 invalidate，borrow_read_connection 的批次分支捕获 BaseException 关闭；read_batch finally 重置 ContextVar。没有证据证明常规成功/异常路径普遍泄漏连接。
- 工作区/源ID在池键中；配置指纹包含 URL、连接选项、版本、secret_id。正常顺序的改密与新配置不会使用原引擎。问题在迟到旧对象/旧 key，而不是没有指纹。
- 复制 ContextVar 到别的线程或 asyncio task 后 owner 不一致，不共享同一 ReadBatch 连接。
- 池数16、单池2且 max_overflow=0；空闲池在访问或 Worker 每30秒维护时回收。繁忙池不会为了腾槽被强制销毁。
- 元数据统计仅 MySQL 明确权限码1142/1143/1227可降级；超时/断连会向上抛，不再继续后续结构查询。该路径未发现吞掉超时继续同步。
- ES 正常返回 cursor 时有显式 close 请求；没有仅凭“未用 finally”就登记正常游标泄漏。异常响应/关闭请求失败后的服务端保留时间需驱动服务验证。
- 默认驱动缓冲、CursorResult GC、invalidate 本身异常或服务器取消语义，未做运行验证，不登记为已复现漏洞。未重复数值 JSON 编码审查。

## 实际阅读清单与 SHA256

“通读”表示完整文件逐行读取；“片段”只代表所列行段；搜索命中不扩大为全文覆盖。主要四文件读取前后 SHA 相同。

| 文件 | 实际范围 | SHA256 |
| --- | --- | --- |
| backend/app/modules/analytics/connection_pool.py | 通读1-253 | BBBCEAEB79BA956163D5C9D2622C560195BE5EB2CF70B56F2880A7F434C331E9 |
| backend/app/modules/analytics/execution.py | 通读1-244；数值转换只读上下文未重复审查 | DB289962DB2C50997928A12E1D6B09D9908BB185DE5A99EE827B3CD239B939A5 |
| backend/app/modules/analytics/connections.py | 通读1-249 | BE239A4AB87CFB3DEDA4F58D75C135319B88AD656E1C3F206C25B8E157379659 |
| backend/app/modules/analytics/metadata.py | 通读1-220 | B74059D8DC7FB6F7EB4D01740D429F80B330BE5774142A6F3CE7956DD0499C35 |
| backend/app/modules/analytics/queries.py | 片段335-565及生命周期搜索 | 0BC57BC6463043AB366C2E2D0666C41536A8F2B44E6377142F82DD9DBA905064 |
| backend/app/modules/analytics/dsh_query_execution.py | 片段287-330及函数/取消搜索 | 0D564ECEF2AA7AF85B7C0EC516A400C6117639D2F8BB229D10DE9A386B727D34 |
| backend/app/modules/analytics/sources.py | 片段55-111、154-181、208-282 | CA3B31DF279B11C41660F9556D3973ED1099A5259012D56F13913AF5911232CE |
| backend/app/modules/analytics/governance.py | 片段85-111、377-459及续租全模块搜索 | C6432A1A7642BF4E1C391E47194672CC4E328BDB2FED28BEF266B3F778A6D6AA |
| backend/app/modules/analytics/router.py | 片段146-180、194-210 | B5087B8FDE3DFC904F3CC06D1084CAC6F4DFB2C68D53291F666E7CD941ECCCC8 |
| backend/app/business_worker/context.py | 通读1-95 | 1036FEF26440AD1280680F3C80468BD4AA971AB97B991D65EF2F8676033FA8E1 |
| backend/app/business_worker/application.py | 通读1-148 | 189FA665417DB3B701BF52A8F912573CDE5D0839817C66A3A9E0022AC02EEB4F |
| backend/app/business_worker/tools.py | 片段69-186及取消/分类搜索 | 5530F66D710B693A26CCDFA920AD49F436D265A89B9F2384DCCF31D63E83D46D |
| backend/app/business_worker/protocol.py | 片段79-127及线程池/取消搜索 | 8F59FBF4BAC756D340E438A9F5ECAD1F97426D3834692BE930E19E375EC1F3D4 |
| backend/app/business_worker/server.py | 片段51-63及维护/关闭搜索 | F2D389753C4DA2534B078A59CB2FC239661316B9DC920034330356E50BE4022A |
| backend/app/providers/tool/database_provider.py | 片段2-115 | 637222A2628A424B848991AA0A395A025F69643699A76DE36ACFC2D3012A457F |
| backend/app/providers/tool/gateway.py | 片段450-515及checkpoint搜索 | F48BBB7F591D68E9510DD1938477E2586AED41F26A2B34C287286DE788659219 |
| backend/app/modules/runtime/task_control.py | 通读1-117 | 6DFB6801618EDF9229238E94D6EF83D9E502C1F71AAF2CE39A9386121E2D365C |
| backend/app/common/owned_records.py | 片段88-113及刷新搜索 | 373205D9FEC10BA3A5F7A730649EFF4668572F863BD51F045EFDB38D52BF473A |
| backend/app/models.py | 片段439-456及DataSource版本映射搜索 | DCE44685054D4EFC2713B88362CB553F63C9E34AD9EFB4EFAB16222F74620340 |
| backend/app/db.py | 片段44-84及session选项搜索 | 3020883D529CE5B65625C29C756395D5E2EE71045C79505BFA1912BAFC4E3973 |
| tests/backend/test_read_connection_pool.py | 通读1-268 | 9ACA388C393DB5A80502853CF0FD1DCAD56A7CE60459B9C48BA6D95E72CD8378 |
| tests/backend/test_analytics_task_control.py | 片段2-146及取消用例搜索 | C7C4E58563E248AE82A8E43D65E26035072F22C9AC09F6DE0F664B7AAF7E8123 |

搜索级辅助：workflows/engine.py、workflows/nodes.py 的 read_batch/release/取消/execute_query 引用；analytics 全模块的 heartbeat/renew/expires_at/acquire_query_lease；business_worker 全目录的 cancel/线程/timeout 引用。以上未宣称通读，未作为独立已审模块计数。PowerShell 下早期两个不存在的 glob 路径和旧测试文件名搜索报错，随后使用 rg --files 定位真实模块；未据空命中下结论。

建议下一批先修 LIFE-N01/N02，二者决定配置撤权能否稳定生效；然后实现 LIFE-N03 的端到端取消合同。LIFE-N04/N05 共用治理边界但应分别留失败回归。所有隔离复现方案只是后续建议，本轮没有运行；需要真实驱动的行为必须按用户本地数据库边界另行核实。
