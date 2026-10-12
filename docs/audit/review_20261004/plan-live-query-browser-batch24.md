# B24：真实浏览器自然语言到本机 SQL 的最小验收设计

日期：2026-10-04。阅读树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-artifact-revisions`。读取 HEAD 为 `a512776432f2001ca86d9dcbb215003da869a3ab`；该树有根代理正在验收的整合工作区，HEAD 不是所有工作区字节的唯一身份，关键阅读文件 SHA 见末表。

**本文件仅为设计。** 本代理没有运行测试、浏览器、数据库、模型或服务，没有读取 `.env`、OpenCode 配置、真实凭据、业务 SQLite 或 MySQL 数据；没有连接 8011/8012。产品、测试、构建、门禁和 shared docs 均未修改，唯一写入为本 ignored 报告。依据根代理的 `docs/audit/review_20261004/acceptance-next-stage-20261004.md`，本批目标是补 COV-04 的真实交互与独立结果证据，不能把已有桩或 HTTP 脚本记成真实浏览器完成。

## 建议实施范围

新增一个窄 Python 编排入口和一个窄浏览器脚本，复用 Host、现有网络守卫、页面操作及真实下载证明。只做一个固定口径、明确业务范围的样例：页面登录、选定本轮数据源与已授权 Agnes 模型、输入自然语言、真实提交，Agent 读取最小 schema 缓存并规划 SQL，执行后导出本轮 queryId 的 Excel，页面点击下载并核验最终字节。

不接入完整九域循环，不混入 Word/PPT/大屏，不运行原 live_business/live_inventory 的 main，不顺便做数据源健康检查、全库同步、库存分类接口或后续追问。临时平台 setup 的种子创建必须标记为 setup，不能记为页面创建数据源。

根代理已明确：独立新基准与 Agent 执行是 **oracle 1 + Agent 1，总计 2 条业务 SELECT**，两个串行阶段、共 2 个业务连接、并发 1，每条 3 秒、各自先 EXPLAIN。该范围属于用户已有的本机有界测试授权，不需要为每条基准另行审批。实施仍需根代理确认最终设计与运行窗口；本报告不启动真实请求。

## 已有入口能复用什么

| 位置 | 可复用内容 | 必须保留的边界 |
| --- | --- | --- |
| `tests/dsh/live_baseline.py:95-106`、`scripts/verify_local_mysql.ps1` | 监听 PID、mysqld.exe、MySQL80 服务及父子进程归属；后续用 SQL 核对 DATABASE/hostname/port/connectionId | 脚本输出的 database 是期望值，不能替代 SQL 身份核对；每轮重新核验，回环代理或未知归属必须停止 |
| `live_baseline.py:121-145,148-184,279-417` | 单连接计量、有限元数据、EXPLAIN、十进制/NULL 原值、重复行哈希、失败证据 | 当前 prepare_baseline 固定四表保养要求，不是通用库存基准；旧基准不能当今日预期，也不能把保养要求当实际巡检完成 |
| `live_business_acceptance.py:46-78` | 在临时 SQLite 建立数据源和 SourceMetadata，不访问 MySQL；白名单列、timeoutSeconds/maxRows | setup seed，不是页面创建；数据源元数据版本必须一致；不能从在用平台复制绑定或对它做写入 |
| `real_platform_smoke.py:128-249` | 本轮临时平台/存储、随机 loopback 端口、禁用调度、Host ready/login/close 与子进程生命周期 | 保留隔离目录、随机端口和清理；只替换明确的 live guard 与本机模型代理；不沿用全量进程环境 |
| `live_model_proxy.py:19-286` | 固定 upstream、固定补全路径、无重定向/无代理层重试、绝对时限、无正文指标 | 必须增加明确 Agnes 授权目的地/模型校验；configured 不是已授权的证明；真实 key 不进入浏览器/平台 |
| `live_python_guard/sitecustomize.py` | 临时 SQLite、固定本机 MySQL、只读 AST、EXPLAIN、3 秒绝对看门狗、禁止自动重连、异常 STOP、自身查询诊断 | 现有每进程额度过宽且不共享，需要窄收紧，不能原样声称满足总 2 条预算 |
| `browser_only_helpers.js:19-94,116-158` | 实际控件填写/点击、操作前注册 HTTP 响应监听、真实页面登录、iframe.contentFrame 定位 | 不通过 evaluate 写应用 store，不用 API send 代替页面提交；等待本次响应与渲染合同 |
| `full_browser_ai_documents_acceptance.js:563-644,648-724,1163-1189` | 页面选源、输入/发送、202 回执与实际 run 终态、下载事件+转发流 SHA+浏览器落盘字节 | 这些流程不是全部导出函数，不能 require 后直接当通用 helper；窄提取或沿用相同代码，不能运行其 HTTP/模型桩 main |
| `live_inventory_acceptance.py:23-77` | 字段映射、逐字段 NULL、单位口径、Excel 真实字节检查、数据库审计计数 | 固定库存查询成功不等于 Agent 生成 SQL；不能把 query_inventory_data 当本批 COV-04 的 SQL 生成证明 |

`full_browser_analytics_acceptance.js` 现有主场景使用 CSV 导入后健康检查/同步，再做多种图表和设计器操作，不能整套接到 live MySQL，否则会隐式扩大连接与查询范围。只借用页面几何/iframe、精确响应等待习惯即可。

## 先补的入口和预算缺口

### 1. Agnes 目的地不是一个环境变量名字

- 两个旧 live main 都显式调用 `LiveModelProxy.from_opencode(...)`（business:334；inventory:110），读取 OpenCode Go 配置。不能原样调用；本次只授权 Agnes 接收验收业务数据，不授权自动外发其他 provider。
- `from_env(env_file=None)` 可从运行时环境读取而不读文件，但只验证基本 URL/HTTPS，不确认该 hostname/path 是已授权 Agnes。外部 HTTPS 的任意 host 目前都能被构造。
- `_handle:222-226` 仅 opencode 分支固定 payload.model；configured 分支只记录 model，不校验是否等于显式选择。必须在创建代理前验证一份无密钥的授权目标说明：provider=Agnes、明确 HTTPS origin、固定补全 path、端口、模型 ID；配置不匹配即本地失败，不探测/回退其他目标。代理收到不同 model 也应拒绝，不偷偷改模型。
- 真实 Agnes API key 只留在 Python 编排进程的内存，Host/profile/browser 只得到 loopback 代理 URL 和本轮 dummy key。不要写入 manifest、seed、平台 SQLite、命令行或子进程环境；记录允许的 origin/path/model 和非敏感指纹即可。不要记录原始 headers。
- 现有 `seed_platform` 使用 create_secret 将 MySQL 密码**加密写入临时 SQLite**；这不是“所有凭据只在内存”。可以作为现有临时平台连接机制如实记录，结束后随专属临时目录清理；它与 Agnes key 不落盘是两种不同边界。若实施要求 MySQL 密码也绝不入临时密文库，必须先调整这一 setup 设计，不能隐瞒现有行为。

### 2. 查询数量/连接/并发必须跨进程计量

`LiveDatabaseGuard:83-88` 当前 MAX_CONNECTIONS=12、MAX_SELECTS=6、MAX_COMMANDS=120；RLock 和计数在单 Python 进程，seed 或另一个 Worker 都会重新归零。现有父进程 baseline 也不是同一 guard 实例。因此 audit.jsonl 最后加总只能发现超额，不能事前阻止超额。

最小改法是在现有 guard 上加本轮显式预算配置与一个共享、原子更新的轻量账本；可用临时目录的锁文件+小 JSON 计数，沿用 Windows 内建文件锁，不引入新的常驻协调服务。账本须覆盖 oracle、所有 Worker 和 seed：

| 项目 | 固定边界 |
| --- | --- |
| oracle 阶段 | 1 个业务连接、1 次业务 SELECT 尝试；已批准的小表/字段 metadata 与控制命令另计；执行前 EXPLAIN |
| Agent 阶段 | 1 个业务连接、1 次业务 SELECT 尝试；不发取样、profile、schema 同步、附加 count 或第二次取数；执行前 EXPLAIN |
| 全轮 | 2 个业务连接、2 次业务 SELECT 尝试；连接/查询并发 1；阶段严格 oracle→Agent，不并行 |
| seed/setup | 0 个 MySQL 连接、0 条业务 SELECT；只操作本轮临时 SQLite |
| 每条 SQL | 服务端 MAX_EXECUTION_TIME≤3000 与 guard 绝对 3 秒 watchdog；控制语句不得放宽 |
| 控制命令 | 两阶段分别提前固定上限，首版可沿用 baseline 的 16 条作为起点并在离线驱动合同核定；不得运行中动态提高。EXPLAIN、初始化、identity、metadata、ping 都必须入账 |
| 故障诊断 | 只保留一份显式的全轮诊断预留：针对本轮已记录连接 ID 核对残留/必要时 KILL QUERY；单独记录诊断连接/命令，不能将它计成仍只有 2 个总连接，也不能重发业务 SELECT |

关键规则：在打开连接或发出 EXPLAIN 之前原子预占额度；拒绝计划也消耗该阶段一次尝试，崩溃或重启不得退还、重置、再发一遍。共享 STOP 和 in-flight 状态以本轮 run token、PID/进程身份绑定，种子子进程没有业务连接许可。锁只保护计数更新，不能因嵌套 EXPLAIN/控制回调死锁；单飞占用的释放须显式对应同一连接/阶段。失败时保持花费记录，不能用删除目录“恢复”预算。

guard 的 AST 目前限制表但不单独限制字段（206-245），字段白名单由真实产品 authorize_sql 与 source.allowedColumns 执行。本场景必须对最终 SQL 再独立核对字段/常量范围，不能仅凭表名单说明列范围已限制。

### 3. 当前 schema 合同与真实 Agent 规划路径

当前 `builtin_provider.py:690-702 → business_catalog.business_schema()` 使用 `cached_only=True, include_samples=False`，只读临时平台的当前版本缓存，不连接业务库或外部模型。该路径可原样使用，不能误报成 schema 工具会自动全库同步。

需要阻止的路径：`metadata.source_metadata:188-194` 在缓存缺失/过期或 refresh 时走真实检查；`inspect_sql_source:78-122` 会读全库 TABLES 统计和表/视图列表后再收窄；刷新还可触发低代码元数据/语义索引。新 setup 必须种入本次最小 metadata 并匹配 source_version；浏览器不点同步/健康检查，不开放额外刷新动作。缓存缺失就停止，不能为使验收变绿放行通用 information_schema。

`query_business_data(mode=profile)` 会执行真实聚合 SELECT（data_profile.py:97-116），不能因名字叫“诊断”而豁免业务预算。schema 可以多次从缓存读取但工具次数仍有界；profile、额外查询、固定库存接口均不能消耗本轮唯一 Agent 数据查询后又继续补查。

当前 `dsh_query_execution.py:287-337` 要求真正 DSH Agent 以 `mode=sql`、明确 task/query 执行，natural_language 工具模式被拒绝。正确链路为“用户页面自然语言→真实 Agnes Agent 读取缓存 schema→真实 tool-call mode=sql→产品 AST/范围校验→guard EXPLAIN→MySQL SELECT”。不能沿用 AI 文档场景的 HTTP 数据源桩调用，也不能把用户直接输入 SQL 记为自然语言生成。

同一 DSH turn 的完全相同 SQL 可能复用 query receipt，但换 task/query/maxRows 等会改变 cache key。不能靠缓存或模型提示“不要重查”保证总量。旧 analytics NL 路径还存在至多两次模型 SQL 修复（queries.py:249-334）；本批不进入该二次模型规划路径，所有外发仍统一经过有总次数/总时限的 Agnes 代理。

### 4. STOP 必须能终止整个样例

数据库 guard 的 stop 文件能阻止后续 MySQL，但 LiveModelProxy 当前不读取它；Acceptance.run 只在收到 SSE 行时检查 STOP，页面的长等待同样不是独立中止机制。模型可能在数据库已失败后继续调用上游。

新编排父进程应有独立、有界的 STOP/总时间监视：一旦数据库超时、断连、资源异常，立即禁止下一工具阶段/模型转发，关闭当前模型连接，取消当前 run 并回收本轮进程，执行已预留的自身查询诊断后停止。不能等模型下一段文字、换 provider、扩大时限或自动重跑。只有 UI 取证和本轮自身清理可以继续，不能继续业务用例。

三个入口统一使用同一个显式绝对 STOP 路径并核对已有停止记录；不得因为换工作树、输出目录或进程自动获得一个全新的“无 STOP”环境。

## 首个样例与 oracle

首版建议固定一个明确设备业务 ID，使用一个表的主键/有效标记条件取设备名称及明确需要的原始字段，结果最多 1 行，先把真实 NL→SQL→页面→Excel 的整条链打通。不要将润滑保养四表 JOIN 的旧扫描例外默认打开；`LIVE_REVIEWED_SMALL_JOIN=1` 在旧 business main 被无条件设置（340 行），不适用于任意新 SQL。

如根代理选择直接沿用四表保养样例，必须保持 live_baseline 的原口径、固定设备、10 行上限、各有效标记和 plan_type=2，重新核对当次 EXPLAIN；超限就结束该样例，不改查其他设备、不沿用历史扫描数字。其优点是已有独立 SQL；代价是连接计划更复杂。两种首例只能选其一，不能失败后自动切换。库存多行样例作为后续独立阶段，不能偷偷追加在本轮两次预算内。

预期由 oracle 阶段的独立固定 SQL 得到，不把 Agent 返回行作为 golden，也不把 golden 行或 oracle SQL塞入用户提示供模型照抄。交给 Agent 的只有明确自然语言范围与最小 schema/字段语义，独立预期留在验收端。新 baseline 与 Agent 查询均保存各自 SQL/参数、计划、返回列、行数、规范化哈希、时间和连接 ID。

比较合同：

1. 先验证列名/所需字段的完整集合与别名映射；缺列、错列和额外字段独立失败。不能只用旧 `records()` 的 `row.get()`，因为缺字段会与真实 NULL 混同。
2. 对每个字段保留 `None`、空串、零、bool 的区别；数值采用精确十进制文本和明确类型映射，不能先转 JS Number。旧 normalized 把 bool 和一般字符串都转 str，需按 schema 改为带类型的规范值；这是 oracle 适配，不修改产品数据。
3. 使用 Counter/多重集保留重复次数；有明确排序则同时检查稳定顺序，不能使用 set 去重。每个样例记实际 NULL 数、重复行数与字段数；若首个单设备样例没有 NULL/重复，只能说比较器支持，不能声称已真实覆盖这些分支。
4. 数据在 oracle 与 Agent 阶段间发生变化会导致不一致；没有共同数据库快照的两连接不能宣称严格同一时点。不一致时先停止并保留两边证据，不自动执行第三条查询“解释”差异，不直接归罪模型。

## 浏览器与下载证据链

1. Python 父入口先核对授权 Agnes 配置与本机服务、STOP、预算清单，建立 oracle 证据；关闭 oracle 连接后才能启用 Agent 阶段。Host 保持临时 SQLite/storage/home、随机 loopback、禁调度/视觉/embedding。真实 Agnes key 不给 Host。
2. setup seed 只在本轮临时平台建立专用 source、最小 metadata 和指向本机代理的模型 profile；profile 可用受控本地 setup API，报告标记 setup。浏览器仍必须在 `#chat-model-select` 真实选择该 profile。应用 app.js:701-709,3773 以 profile_name 为 option value 并在 change 时更新实际提交字段。
3. 通过 `browser_only_helpers.login` 真实登录；点 `#business-source-btn`，在 `.datasource-option` 找到本轮唯一 source 并勾选，完成后核对标签。不写 localStorage 伪造登录，不直接改 dshChat store。
4. 填写 `#prompt-textarea` 并点 `#composer-submit-button`。捕获这次真实 POST，断言原句、精确 `[source_id]`、选定 model_profile、无直接 SQL/基准文件注入。保存 202 的 request_id/session_id/run_id，等待这个 run 的实际终态，不能仅等“发送”按钮恢复。
5. 从真实 tool-call 与对应回执取得 query_business_data 的 mode/task/SQL/source/max_rows；再核对该 queryId 的平台 QueryHistory/历史结果与 guard 数据库审计。queryType 可能是 sql；modelUsage.sqlRepairs 不再是本 DSH 链的正确模型规划证据。审计 SQL 是参数替换、授权改写后的真实 driver SQL，需记录规范化映射及 hash，不能将 raw tool SQL 的哈希直接与其作字节等同。
6. 断言一次成功 Agent 数据 SELECT、其 EXPLAIN 在前、无额外 profile/refresh/count；schema工具可以是缓存读取。核对结果字段/行与独立 oracle，再核对页面真正显示的查询数据卡、用户可见来源与结果，不把只读 store 的数据当页面已经渲染。
7. 在同一自然语言请求中要求 Excel，模型只能对已完成 queryId 调 `export_query_spreadsheet`。若模型不产出文件，样例失败，不能临时以 HTTP API 补造产物。导出应消费留存结果，下载完成前后业务 SELECT 数保持 2。
8. 按本轮 artifactId/source_run_id 找实际产物卡，点击下载控件；使用 Browser.downloadWillBegin/downloadProgress、落盘 GUID、实际流 SHA-256，与 `verifyDownloadedBytes` 相同合同证明下载。原 helper 监听 GET `/api/artifacts/{id}/download`，本方案选择此路径，避免把 analytics POST 导出误套 GET helper。
9. 用已安装 openpyxl 离线打开浏览器保存的 XLSX，检查真实表头、行数、每个字段值和重复次数；核对 NULL 的空单元格表示。Excel 空白可能无法区分 SQL NULL 与空字符串，须保留原 QueryHistory 的精确区分证据并诚实记录格式表达限制，不能把二者都转空字符串后称严格等价。

现有下载 helper 中 `sendChat/selectSourceInChat/downloadThroughUi` 并未作为通用导出；可将必要的小段提取到新共享 helper，保持原 AI 文档场景行为和离线合同不变，或在新窄脚本中保持同样的明确实现。不要为了首例抽象出整套新浏览器框架。原 full_ai main 会启动模型/HTTP 桩，禁止直接调用。

## 最小实施与先行离线验证

拟新增 `tests/dsh/live_query_browser_acceptance.py` 与 `tests/ui/live_query_browser_acceptance.cjs`。对现有 live guard 仅增加显式的 B24 共享预算/阶段/STOP 合同，对模型代理仅增加显式授权目标/模型与 STOP 接口；不改产品查询算法、不改统一门禁或冻结资产。只有必要时提取页面下载 helper；不要在冻结 B20 树直接实施。

先在合成/本机桩条件下验证以下基础，不消费真实数据或 Agnes 配额：

- 目的地或模型不符、redirect、配额耗尽、STOP 后请求均不得产生上游转发；真实 key 不出现在子进程环境和证据。
- 两个独立 Python 进程/seed 竞争同一个预算账本，最多 oracle1+Agent1；第二个 Agent SELECT 在发 EXPLAIN 前拒绝；重启不重置；控制/metadata/diagnostic 各自记账。此处是必要的跨进程约束测试，不以模型文字代替。
- 最小 schema 缓存命中为 0 MySQL；缓存缺失/刷新/profile 越界停止；数据库故障立即产生统一 STOP 并让模型代理/浏览器编排结束。
- oracle 比较器用缺列、NULL/空串、重复行、超大精确数值构造正负例；真实浏览器下载的 ZIP/XLSX 检查用合成文件验证。

上述基础通过并由 root 固定运行窗口后，才执行一个两阶段真实样例。首失败保留全部证据并结束，不自动换问题/重跑。通过也只记一个 COV-04 真实浏览器样例，不记 1000 条、100 遍、所有 SQL 方言或 24 小时完成。

## 证据输出与读取指纹

每轮 manifest 至少包含源码/资产 SHA、允许的无密钥 Agnes origin/path/model、物理服务核验、阶段预算及实际开销、STOP 状态、模型转发次数/耗时/状态、真实页面 POST 的去敏字段、request/session/run/task/query/artifact 关联、工具记录、两份 SQL 与 EXPLAIN、独立 oracle/实际行的私有文件及规范哈希、XLSX 下载 SHA/字节检查、截图/控制台错误、剩余进程和残留自身查询的处理结果。真实业务行只进受控私有证据；终端不打印凭据或原始业务数据。用户授权发送至 Agnes 的 1000 条范围不等于首例可以扩大到 1000 条扫描/返回。

实际阅读还包括 AGENTS.md、live guard 与 proxy 的测试名称/部分预算合同、real_platform_smoke 的 Host、本机服务核验脚本、business_catalog、data_profile、queries/execution 的相关路径、builtin_provider 的 schema/sql/profile 分支、app.js 模型选择合同。未运行/import 这些模块。

| 阅读文件 | SHA-256 |
| --- | --- |
| tests/dsh/live_business_acceptance.py | `a1b3d9be046763302125320303b2dbad414ce8b04c697bf468730e0070103b6e` |
| tests/dsh/live_inventory_acceptance.py | `9e38caddda3ea18fe6eb4d49a86ae0efc231f594fff5eafd1fca882fa60a0372` |
| tests/dsh/live_baseline.py | `bbd486bd37ae576b1ee55bdc001772d38f54d9b78d3a64b7cc6b1b95ab74fc5d` |
| tests/dsh/live_model_proxy.py | `9eaab8da47845ee20f88a26f26200e5602e8c4d8f33c05be369c949fd136ff14` |
| tests/dsh/live_python_guard/sitecustomize.py | `b144d7d9e7497e65f61bbddd4d228e18f69f2c4d8efee71bc2528d91013311a9` |
| tests/dsh/real_platform_smoke.py | `22b02809d505fa3301e768587a72c411395f2a6afc3059f913135825f3c0b318` |
| tests/ui/browser_only_helpers.js | `6def3687e076c8be6c925851f1a414d47eed668829e32a606745de093aa29f9f` |
| tests/ui/full_browser_ai_documents_acceptance.js | `5c3b312f0ca1217a209ee2ab3906d6cf6671f32749ab02d9b08ca2c75c47b206` |
| tests/ui/full_browser_analytics_acceptance.js | `9d8fda29b1ddfec99d24023474a73ba9de202a3c4eed12dd480ba3de42e320f8` |
| tests/ui/isolated_browser_guard.cjs | `0dbbf67e08274ea48e722dd5b2a62cc9abf4c926ec4e0aa245d46246ee78fb2b` |
| scripts/verify_local_mysql.ps1 | `5e5798669f3083effaae95f165c2bad43e8b5eac04d61369749646bfe478475e` |
| backend/app/modules/analytics/dsh_query_execution.py | `103c17a9ce4df16abe7bb5668d914cecb09761fdc079aa4c42138dd1c49e18f5` |
| backend/app/modules/analytics/metadata.py | `7becdd75237ffd3c5e9e74968b5b9ca08373de2cf92a7aff3b26a9a614a4c993` |
| docs/audit/review_20261004/acceptance-next-stage-20261004.md | `38397092e19604b18b54a5eb9ec54b484216badb2fe0660690e01f52903fe8d2` |
