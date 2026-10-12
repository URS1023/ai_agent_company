# 批次十七只读复核：RunEvent 回执键、既有库迁移与丢 ACK 核验

2026-10-04。工作树 `major-delivery-acceptance`。仅复核 `.implementation/plan-host-receipt-durability-batch17.md` 的落地约束，唯一新增文件为本报告。没有修改 B16 产品、测试、正式文档或门禁，没有运行 Python 导入、测试、服务、迁移、数据库或模型请求。其他代理仍在同树工作。

## 结论

采用可空 `RunEvent.receipt_key` 和数据库唯一索引可行，但本仓库没有现成的“加一列后自动升级所有既有库”的迁移框架。必须同时落地：**显式增量迁移、实际唯一索引核验、现有 ToolCall 与旧事件不变、同事务消息投影保护、只读恢复的当前授权**。仅更新 ORM/SCHEMA_VERSION 或仅做同键查询不能满足计划。

本报告确认的是源码机制与实现约束，下面的迁移/多进程/丢 ACK 反例均为下一批建议，尚未执行。特别是 SQLite 文件库竞争和 MySQL DDL 不在本轮实测范围。

## 1. 当前结构管理机制及必须补上的检查

| 源码证据 | 当前行为 | 对回执迁移的直接影响 |
| --- | --- | --- |
| `backend/app/schema.py:35,53–62` | SCHEMA_VERSION 为 `20260917_32`；checksum 只含表名、列名、列类型文本和 nullable | 加列必须升版本；增加/丢失/改变唯一索引不会改变 checksum |
| `schema.py:97–131` | AUTO_MIGRATE 时 create_all；随后仅检查表、列名是否存在；版本记录在最后写入 | create_all 不给既有 run_events 增列。仅 models 增列会拒绝启动；手工补列但遗漏唯一索引则可能通过当前启动检查 |
| `schema.py:115–116` | 实库列检查不核对类型、nullable、默认值或索引 | 仅“有 receipt_key 列”不足以证明列和约束正确；已有错误同名列/索引应明确失败 |
| `schema.py:37,97–104` | SQLite 只有进程内 RLock；MySQL 才获取按库名生成的 GET_LOCK | 两个 SQLite Worker 互不持锁；不能沿用这个 RLock 声称跨进程迁移串行 |
| `seed.py:183–185`、`business_worker/server.py:31–39` | 每个 Worker 显式 initialize 时调用 bootstrap→initialize_schema；自身 initialize_lock 也仅进程内 | 不应让未协调的多个 Worker 各自修改既有库；结构完成验证之前不能 ready |
| `models.py:624–637` | RunEvent 仅 id 主键，sequence 无唯一约束 | 新回执键是新增持久保证；既有 sequence 不能当作去重键 |
| `models.py:601–621` | ToolCall 已有全局唯一 idempotency_key | 必须与回执键分开；不要为了新回执重新计算或回填旧执行幂等键 |

已检查跟踪文件名与 backend/scripts 中的结构初始化调用，没有找到 Alembic 配置/版本目录或通用 ALTER 迁移链。`modules/system_tools/migrations.py` 实际是业务来源只读扫描归档，不是平台结构升级器，不能借其名字作为已有框架依据。现有 `test_business_catalog_cards.py:110–123` 只证明缺失整表可以补建，不能覆盖既有 run_events 的加列/索引升级。

建议保留现有 checksum 算法，针对回执引入窄的结构验证，避免为一个索引重算所有历史版本摘要。核验应同时看实库 column 元数据和唯一索引/唯一约束：列可空、长度/类型能容纳规范 SHA256，唯一性覆盖完整单列 receipt_key，不是普通索引、前缀索引、表达式索引或只覆盖部分新回执的条件索引。同名索引存在不代表定义正确；同名但错误定义应拒绝就绪，不静默重建。具体命名由下一批实施决定，本报告不虚构已经存在的迁移命令。

## 2. 既有 SQLite 文件库的最小迁移方式

### 结构形态

- 模型新增 nullable 字符串 receipt_key，并声明具名的单列唯一索引。新回执总写入规范固定长度键；旧/非回执事件保留 SQL NULL。不可用空字符串默认值，否则旧行会互相冲突。
- SQLite 既有表用“增可空列 + 独立 CREATE UNIQUE INDEX”的增量方式，无需为 UNIQUE 重建 run_events。迁移不得更新旧 payload、sequence、created_at 或主键，不扫 JSON 猜旧调用身份，不清理重复旧事件。
- SQLite 唯一索引允许多个 NULL 是该兼容设计的依赖；必须在文件库专项中实际断言多个旧 NULL 行保留、重复非 NULL 键失败。本轮没有运行 SQL 验证。
- 不改 `RunEvent.id` 的 BigInteger→SQLite Integer 变体，不重排 id；history 当前以 id 游标恢复（chat.py:606–612）。索引添加不等于事件清理/游标重建。

### 执行与完成标记

最小发布方式是停止旧 Worker 写入，在唯一迁移进程中升级专属平台库，再启动经过结构验证的新 Worker。升级不得运行 seed 的业务补齐来假装是纯 DDL。若迁移明确允许两个进程同时启动，应在**同一连接、检查旧结构之前**取得 SQLite 实际写锁（例如经审查的显式 BEGIN IMMEDIATE 事务），锁内重新 inspect；迁移、索引校验、版本记录遵循一个可恢复流程。不能在检查后才加锁，也不能认为 SQLAlchemy 的普通 connection.begin 在所有当前 sqlite3 事务配置下必然已发出所需写锁。

当前 db.py:29–35 只设置 check_same_thread，内存库才用 StaticPool；没有配置 WAL、共享缓存或跨进程锁。不要为本迁移顺手修改 SQLite 全局 journal_mode/同步配置。锁等待设置独立有限预算；若 locked/busy/DDL 错误就失败且未就绪，不自动扩大等待或无限重试。

迁移需支持三个可识别状态：

1. 旧列不存在：取得迁移所有权后增列，再建索引。
2. 列已存在但索引未创建：仅在列定义正确时补索引，适应上次迁移中断；不能写“已完成”后再建索引。
3. 列、正确唯一索引及新版本记录均存在：只核验并返回；不更新旧 version/checksum，不改旧事件。

若新版本记录存在但唯一索引缺失/错配，启动应失败，不能凭版本号放行。若列或索引错配，不做自动 DROP/重建来掩盖未知结构。迁移完成标记必须在真实结构验证之后；`AUTO_MIGRATE=0` 仍应禁止普通启动自动写迁移记录，显式迁移入口需自行完成新版本登记。新空库也走同样索引验证，不能只测试 create_all 路径。

MySQL 当前已有 GET_LOCK，但 DDL 和 rollback 的语义不能直接套 SQLite 的事务证明。若同时宣称 MySQL 升级受支持，需要按其隐式提交边界设计“检测→DDL→验证→登记”的可恢复步骤，并核实唯一索引不是前缀索引；否则第一轮应明确只完成 SQLite 已测路径。不得连接实际业务库证明本计划。

### 混合版本边界

旧 Worker 不认识 nullable receipt_key，仍可写 NULL；新唯一索引不阻止旧代码继续制造无规范回执的工具事件。故“支持旧行读取”不等于“支持新旧 Worker 滚动混写”。Host/Worker 协议需同步协商 receipt 能力（当前 server.describe/initialize 返回 protocol_version=2），缺能力或迁移未完成时不可切回旧二次写入路径冒充已持久化。最小实施应明确停旧进程后迁移并启动新协议；混合版本运行另设测试/合同，不能默许。

## 3. 多进程：唯一键能保证什么，不能保证什么

### 同键竞争

两个进程同时发现“没有回执”再 INSERT 时，由真实唯一索引裁决。只有在本次回执 INSERT 的明确唯一冲突下，才允许 rollback→独立新 Session→精确读相同键并比较身份/语义/载荷。普通 IntegrityError、busy、断连不能统称 duplicate；如果原记录不存在或身份/载荷不同，保留错误/冲突，不返回假成功。

相同键相同规范载荷返回原 id/sequence/data；不同载荷不 UPDATE、不 REPLACE、不 on-conflict-update。不改变原完成时间或写第二个事件。唯一冲突之后不能回到 provider 或重新 tool.execute。

现有 `common/idempotency.py:51–116` 可借鉴“冲突后回滚再查”的概念，但函数会提前 commit，且已完成状态的含义是业务占位；不可直接放入计划的事务内回执 helper，否则会割裂 ToolCall/RunStep/Message/RunEvent 的最终原子提交。需要窄专用函数，helper 不自行 commit/开新 Session，外层提交失败后才做独立只读核验。

### 不同键与消息投影

回执唯一索引只保护同一个键。两个进程写不同 receipt_key 但同一 Message，现有 `parts_json = ...` 整包替换仍可互相覆盖；`max(sequence)+1` 也可能重复。`context.lock_for` 与 events/run 锁只在一个进程内有效。计划中的“刷新 Message 再写”可避免旧 identity map，却不能解决刷新后、提交前的并发覆盖。

若 B17 要宣称多进程下消息投影也不会丢：在短收尾事务内选择实质串行/比较交换策略。SQLite 可使用短写事务并在取得写入权后重读 Message；MySQL 可对同一 Run/Message 加行锁并保持统一顺序，或显式版本 CAS。不能持有此锁等待长 provider；锁竞争失败保留未知/失败并只读核验，不重新派发业务。若首轮只保证默认单宿主串行，请明确不同键多宿主 Message 竞争不在已完成范围，不能把数据库唯一键泛化成全部事件原子化。

现有恢复游标使用 RunEvent.id，不应为了“修 sequence”在本批顺手给 (run_id, sequence) 加唯一约束；旧库可能已有重复值，需独立调查。SQL INSERT 的 id 唯一也不自动等于多进程事务提交顺序，跨进程迟提交小 id 是否遗漏需另有游标合同；本报告不扩展事件流重设计。

## 4. 旧工具执行事实与撤权拒绝必须分离

tools.py:76–81 的旧执行键仍由 session_identity、invocation_seq、call_id 生成；新 receipt 身份拟包含 run 与真实子 Agent session，可做补充但不能迁移旧 ToolCall 键。同调用改名称/参数当前明确 ConflictError，应保留。不得因为新 receipt_key 未命中就忽略已有 ToolCall 的 completed/prepared/dispatch_started/unknown 状态。

tools.py:104–116 当前已完成回放会重新鉴权，拒绝时直接返回且不改原 ToolCall；未派发/未知分支也保留原记录。统一回执出口需要区分这些分支，不能都调用 finish_tool_call 覆写旧 success。

**独立 phase 还不够：** chat.append_event:469–487 对 tool.completed/tool.failed 按同 call_id + part.type 替换消息结果，还会改变旧 tool_call 状态。若将“当前撤权拒绝”作为另一个 phase 的 tool.failed 发进同样投影，虽然数据库有两个唯一键，UI 仍会将原成功改成失败。控制拒绝需独立事件/投影身份或明确不投影到原执行 part，同时不能在响应中携带已撤权原 payload。旧成功事实可以保留，当前有权返回哪些内容另行校验；二者不可混合。

迟到 started、重复 started、终态 Run 新调用拒绝也不得恢复旧 running 状态或替换原工具结果。receipt 载荷摘要不能含每次恢复重新生成的时间/id；应对同一已规范化结果稳定计算。先执行 preserve_json_integers，保持 bool、安全整数、数字文本与嵌套结构；不得通过解析模型可见 JSON 文本重造“可信回执”。

## 5. 提交成功但 ACK 丢失：只读核验的准确入口

protocol.py:73–85 先调用 handler，再写 JSON-RPC reply。reply 写失败可能发生在真实 commit 之后；worker.mjs:40–55 控制超时只移除 pending，不终止原 handler。回滚调用或一次 absent 均不能证明工具动作未发生。

建议 `tool.receipt.get` 为独立短控制 RPC，走新的 Session/事务，不复用失败的 tools.execute Session、长事务快照或 ORM 缓存，也不等同一长工具锁退出后才开始读取。加入 Host 有界控制方法列表，Python 现有独立 control_executor 可承接。若读不到已提交行，返回 absent/unknown 事实，不用查结果作为重新执行许可。

只读核验顺序：

1. 由可信 Host invocation 传精确身份及预期语义摘要，不接受模型 arguments 声明 receipt_key。新 Session 读取 Run，核对预期工作区、发起人、会话及真实 Agent invocation 归属。
2. run_actor 重查当前用户、成员、工作区、服务客户端/令牌权限。其实现会 bind_data_scope；RunEvent/ToolCall 均继承 Run 的展示范围过滤（data_scope.py:78–80）。因此不能让缺 runs.view 导致内部去重记录假 absent：可参照 `_saved_tool_calls` 的显式 Core 联表读取，但必须保留 run/workspace/user/conversation 归属谓词，读取后另做当前工具和资源授权。不是跳过授权。
3. 单次精确 receipt_key 查询，比较 run、phase、工具、参数摘要和规范结果摘要；读取实际提交的 id/sequence/payload。不要扫描 history_window 或 native_sequence 猜回执。查到工具日志但没有规范回执，只能报告 legacy/unconfirmed，不补写假结果。
4. 当前权限不允许返回原业务载荷时，返回 access_denied/不可核验，保留原执行事实；查询自身不写 ToolCall、Message、RunEvent，不修复状态，不触碰 provider/模型/业务源。found 表示已查证提交且允许读取，不表示原业务动作一定成功；status failed/cancelled 照原事实返回。
5. Host 最多一次有界立即核验；查询失败/冲突/absent 仍保持 unknown，不换 call_id、不自动重新执行。终态 Run 的 receipt 可以授权读取，不可因此重新 admit 或复活运行。

### 必须明确的现有授权限制

`gateway.authorize_replay:415–478` 本来是只读授权辅助，不解密、不执行；但 470 行对未建立逐资源证明的聚合/生成工具默认拒绝。直接复用它会保守拒绝 PPT/文档/大屏生成回执恢复。**这是当前合同，不能为 ACK 恢复删掉兜底拒绝，也不能写“接入该函数即可恢复所有生成结果”。**

最小第一轮可用 current_datetime 等已有可证明授权的工具验证 receipt 机制。若下一批同时要求恢复实际 PPT 等，应单独增加所需的工件/计划/报告/查询归属与当前资源权限证明，保留已有 SnapshotAccess/文件下载权及源撤权边界；超出可证明范围保持 access_denied，不原样透传历史结果。仅核验 `tool.execute` 权限或“当时成功”不能替代当前内容授权。

## 6. 下一批最小隔离反例与验收清单（未执行）

现有 backend conftest 的 client 每次 drop_all 后使用 StaticPool 内存 SQLite（conftest.py:17–19,50–59），无法证明旧文件升级、独立进程可见性或跨进程锁。以下用例需要专属 tmp_path 文件和独立连接/子进程，明确 assert 平台目标是该文件；不得取 .env/业务 DATABASE_URL。保持现有 Python 隔离入口及首失败停止，不启动真实模型/业务源。

| 反例/正对照 | 最小真实断言 |
| --- | --- |
| 旧 B16 run_events 文件库升级 | 构建不含新列的真实旧结构、旧版本记录、多条事件和 ToolCall；升级后旧事件主键/载荷/顺序/执行键哈希不变，receipt_key 全 NULL；再开新 Engine 查证 |
| 新库与重复运行迁移 | 正确字段及唯一索引存在；二次迁移不变更版本 applied_at/旧 checksum/事件数；AUTO_MIGRATE=0 不自动修改旧库 |
| 半成品迁移与错误索引 | 已有列但缺索引可按专用迁移完成；同名普通索引、错列/条件索引、记录已完成却漏索引均被启动检查拒绝；不得改旧记录“修绿” |
| 两个迁移进程争用同一 SQLite 文件 | 锁内重查；最终仅一个版本记录/正确结构；第二个要么幂等完成要么有界显式失败，不未就绪继续启动、不删数据、不扩预算 |
| 两进程同 receipt | 两个独立 Session，真实唯一约束；仅一规范事件；相同载荷返回同 id，不同载荷冲突；SQL/模型/provider 调用不被恢复触发 |
| 不同 receipt 同 Message | 仅在承诺跨进程投影时做：两个短收尾都保留自己的 part、既有正文/工件不丢；否则明确此项未实现而非仅测同键就宣称多进程通过 |
| 真 commit 后丢一次 reply | 在 JsonRpc sink 故障注入，handler 真实提交；独立新 Session/Worker lookup 返回原 id、精确结果和 metadata；事件数 1、provider 次数 1、重复 lookup 的所有业务表行数/哈希不变 |
| 提交前失败/仅 prepared/在途不可见 | 一次 lookup 无回执保持 unknown；无成功事件、无第二次 provider；不能把错误文本转成成功对象 |
| 旧成功撤权/同键改参数/错归属 | 当前回复拒绝，不改旧 ToolCall/result/event，不让控制拒绝覆盖旧成功消息 part；跨租户/用户/会话不能命中并泄露 payload |
| 可授权回执与聚合默认拒绝 | current_datetime 正例；真实查询当前撤权负例；PPT/文档/大屏未提供逐资源证明时仍拒绝，不为了机制测试放宽 replay guard |

## 7. 最小所有权与边界

下一批预计需要 `models.py`、`schema.py`、一个独立窄迁移模块/入口及 SQLite 文件库专项；Worker 事务 helper/receipt.get、Host 协议与投影按原计划分工。迁移与索引核验属于发布前置，不能由每次 tool.execute 懒升级。旧数据修复/历史回执回填、跨宿主整体执行 ownership、通用事件顺序改造和所有生成工具的聚合授权不是本报告已经完成的功能。

本次只写报告。没有确认任何部署正在多进程竞争，没有读取或更改实际 SQLite/MySQL 内容，没有新增红/绿结果。路径探测曾确认不存在 backend/app/database.py、backend/app/bootstrap.py、backend/alembic.ini、根 pyproject.toml、backend/requirements.txt 和 common/visibility.py，随后改读真实 db.py/schema.py/requirements.txt/data_scope.py；未创建这些缺失路径。

## 实际阅读与文件指纹

以下范围为本次真正阅读或明确检索，SHA256 覆盖相应文件全部字节；不把未阅读全文的文件算作完整审查。

| 相对独立树路径 | 本次范围 | SHA256 |
| --- | --- | --- |
| .implementation/plan-host-receipt-durability-batch17.md | 通读 | 4E15398EAEAE6274327FF4D517479BD6771E37A4D3DC05315EF6E68038F939B8 |
| backend/app/schema.py | 通读 | FD6AF3494FE7362470E6CDB5D265BF868A484BA5BA21C4379D3DF5944E31E7FA |
| backend/app/db.py | 通读 | 3020883D529CE5B65625C29C756395D5E2EE71045C79505BFA1912BAFC4E3973 |
| backend/app/models.py | 片段：531–645；结构/幂等标识定位 | DCE44685054D4EFC2713B88362CB553F63C9E34AD9EFB4EFAB16222F74620340 |
| backend/app/seed.py | 片段：173–205 | 12E67F9E5E22917D8C46FB30319B9262E938219E855A1B5DDBEE3C2D38247C8E |
| backend/app/business_worker/server.py | 通读 | F2D389753C4DA2534B078A59CB2FC239661316B9DC920034330356E50BE4022A |
| backend/app/business_worker/tools.py | 通读 | B17A0959F9FA1C815976653CFA3D040522F5427900C3DF58B13DEB9391C518FA |
| backend/app/business_worker/context.py | 通读 | 1036FEF26440AD1280680F3C80468BD4AA971AB97B991D65EF2F8676033FA8E1 |
| backend/app/business_worker/chat.py | 片段：81–166、427–519、581–674；RunEvent/锁定点定位 | B9E65D49313212763A4F8FA92FEE4CAC8116976AD347482B0A9AA19A26E36004 |
| backend/app/business_worker/protocol.py | 通读 | 69F4D3616DBD09AEB6BF273A023F84AF832AABA0BDADD558848D2C7B22958793 |
| services/dsh_host/worker.mjs | 通读 | 35830BC723575836EAE151FB54153D8B7F7A785D7042D89436F4591A85D87201 |
| backend/app/providers/tool/gateway.py | 片段：416–570 | 25F71461AE224895AE1DC28CF028CF2DF78EF707E38D36D236C2490B7433EE8A |
| backend/app/common/data_scope.py | 片段：56–104、167–190；criteria定位 | 910673325E7033998390973077C45B178846AAB2470A9BE571F35C39EF2C1703 |
| backend/app/common/ordered_rows.py | 通读 | 57ECD09E5B19803F9B50147ACAA8ACD6D8A1E7DCD95134096BB293245FE3C3BF |
| backend/app/common/json_numbers.py | 通读 | 6097543C63B8201E38C6FCA5F06C93441FB25C36C78A4F04B69E2F019DEB72C9 |
| backend/app/common/idempotency.py | 通读 | 2785F8933B43F9692C016005357F724188A312F9172E31AF7566850077326825 |
| backend/app/modules/runtime/service.py | 片段：176–254；步骤收尾函数定位 | E074939540B43A93E0CA25C26B0AD1DBC2A9B472CE6854FBCEAE3DFF5A9F6637 |
| backend/app/modules/system_tools/migrations.py | 通读，用于排除其作为平台结构迁移框架 | 2EC211681142544BDCD9E17E3EB15BFE96485C72D60BB0856732A9153C45A679 |
| backend/app/config.py | 仅检索AUTO_MIGRATE定义，不读取凭据 | 9C181E74F84C940D31FBF955D8D99D39DDEF38DE40CFED360DCCBE8C39380C5D |
| requirements.txt | 仅检索SQLAlchemy/alembic字样 | 59130C77B1F6FE0CF6512946410CC8F8DB8035F6F275C6A9A62CC4F8C892D27F |
| tests/backend/test_business_catalog_cards.py | 片段：104–124 | 8445EBB6E8AFCE0203900498EAF1ED38B484E0FB2FAD6E1E6467E126F1FB36CF |
| tests/backend/conftest.py | 通读 | 91841932FA664D081357164145A4DFFC74FD89C869C3638690A14F298D583274 |
