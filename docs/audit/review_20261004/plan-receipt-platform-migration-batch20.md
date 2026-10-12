# B20 平台回执结构迁移设计与 PG 反射离线红例

状态：仅设计与一轮获准离线反射验证，尚未实施 MySQL/PostgreSQL 迁移。日期：2026-10-04。

唯一工作树为 `E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-durable-receipts`，读取时 HEAD 为 `faaa6de447c9af7811ec6fc8a69d56e2de5224e5`。B19 产品、测试和门禁文件冻结；本轮只在 `.implementation` 新增本计划、离线测试与证据、未应用补丁。没有修改 MAIN/B18，没有运行服务、浏览器、完整门禁或数据库迁移，没有连接任何数据库或模型，没有读取 `.env`。数据库官方文档使用公开网页只读核实，不应将本轮笼统称为“零网络访问”。

根代理初始只授权只读设计；发现 PG 反射差异后，另行明确授权本目录离线测试、独立日志及待应用补丁。测试调用当前真实校验函数，只有目录反射结果和配置依赖是受控桩；数据库连接全部禁止。后文待实施内容不构成已交付支持。

## 1. 当前声明、启动链和已部署 MySQL 的实际缺口

- `backend/app/config.py:81–112`：`AUTO_MIGRATE` 默认开启；平台默认方言为 `mysql+pymysql`，声明主机 `127.0.0.1`、端口 `3306`、库名 `company_agent`；显式 `DATABASE_URL` 覆盖这些默认值。本轮没有读取部署配置，不能据此确认在用实例的实际地址、版本或服务归属。用户指定的业务测试库 `szh_new` 不能替代平台库，也不能为迁移改变工作流数据源绑定。
- `requirements.txt:4–5,19` 有 SQLAlchemy、PyMySQL、psycopg；PG 驱动也用于业务数据源。当前配置与数据库模块明确以 MySQL 为平台默认路径，SQLite 为本地/测试路径。在本轮检索的 README、平台配置和实施记录中，没有找到“PostgreSQL 平台部署已全面验收”的承诺；允许显式 URL 和安装 PG 驱动不等于已证明 PG 平台支持。
- 当前对外启动经 `backend/start_server.ps1 → backend/run.py → services/dsh_host/launch.mjs`；Host 先调用 `system.initialize`（launch 第 88 行），Worker 初始化调用 `seed.bootstrap()`，后者先调用 `initialize_schema()`。结构检查成功是就绪前提。`bootstrap` 随后补管理员、角色和业务种子，不能当作窄结构迁移工具。
- `backend/app/schema.py:35,103–160` 的版本为 `20261004_33`。`AUTO_MIGRATE` 只通过 `create_all` 创建缺失表，再检查必需列和回执索引；它不会给已有 `run_events` 增加列。缺少 `receipt_key` 或规范唯一索引时，新版应拒绝就绪，开启 `AUTO_MIGRATE` 不能补齐旧库升级。
- `RunEvent.receipt_key` 是可空 `String(64)`，索引名为 `ix_run_events_receipt_key`，完整单列唯一。旧事件保持 NULL；不推断/回填历史回执，不改变旧事件 ID、sequence、payload、时间或 ToolCall 幂等键。
- `4f47d51:backend/app/schema.py` 的确切旧版本为 `20260917_32`。目标升级是这个被确认的旧契约到 `20261004_33`，不能写成任意历史库自动升级。

因此，当前 B19 不能直接交付“已部署 MySQL 一键升级”：必须先有独立、显式目标、可审计的 MySQL 窄迁移实现与验证，再在另行授权的停写维护窗口执行。PG 路径还要修复第 3 节的校验兼容问题；该修复也不代表整个 PG 平台已验收。

## 2. 已有工具的复用范围与导入边界

| 当前入口 | 实际用途 | 本次可复用程度 |
|---|---|---|
| `business_worker/receipt_migration.py` | 显式既有本机绝对路径 SQLite 文件；`BEGIN IMMEDIATE`，加列/索引，核验并登记版本 | 可复用窄契约、旧行保留与重复执行思路；当前明确拒绝 MySQL/PG，不可直接改方言字符串冒充支持 |
| `schema.py` | 统一模型注册、ORM checksum、启动检查、MySQL 初始化锁 | 复用相同注册集合、checksum 算法、索引名和严格校验；需解决无默认配置导入与 PG 默认反射值 |
| `modules/system_tools/migrations.py` | 业务来源库扫描、行数统计、可选归档到 MigrationBatch/MigrationRecord | 不能用作结构迁移；`_source_engine` 明确排除 `PLATFORM_DB`；`archive_rows=False` 仍读取来源并写批次/审计 |
| 既有组件导入、来源报表重置、模块规则修复脚本 | 目录资产或工作流/业务配置修复 | 不是 schema 升级工具；不能直接复用其默认库、种子或业务更新入口 |

仓库未发现 Alembic 版本链或通用 ALTER 升级工具；为这一列引入完整迁移框架不是最小方案。

需要纠正的设计事实：当前 `receipt_migration.py:9` 顶层导入 `app.schema`，后者顶层导入 `app.config/app.db`。`config.py:54–55` 读取 dotenv；`db.py:27–35` 读取默认 URL 并构造默认 Engine。这条链本身未自动连接数据库，但“给 migrate 传显式 Engine”并不能保证不读取默认配置。现有正式记录中的“不读默认 DATABASE_URL”只能解释为迁移连接目标不使用默认引擎，不能扩大为整个导入链不读取配置。本轮没有修改旧记录或冻结模块。

建议未来最小文件范围：新增独立 `backend/app/business_worker/receipt_platform_migration.py` 与其窄测试；在 `schema.py` 把运行时 `settings/get_engine` 的导入移入 `initialize_schema`，使契约辅助函数可在无部署配置情况下导入，并仅规范化已证明安全的 PG 空 INCLUDE。先以禁配置读取/禁连接测试证明直接、间接导入链安全；若模型注册仍存在隐式配置依赖，再提取一个无配置的共享契约模块，不复制另一套 ORM 定义。现有 SQLite 入口继续保留其行为。

## 3. PG 空 INCLUDE：真实函数离线证据和待应用补丁

本机共享依赖实际 SQLAlchemy 版本为 `2.0.52`。只读源码显示：

- `sqlalchemy/dialects/postgresql/base.py:5080–5084`：PG 服务版本大于等于 11 时，普通索引也写出 `include_columns=[]` 与 `dialect_options={"postgresql_include": []}`。这是反射的正常默认表示。
- `backend/app/schema.py:49–50`：当前对每个方言值判断 `value is not None`；空列表不是 None，故正常 PG 索引被拒绝。
- 同版本 MySQL `base.py:3555–3605` 仅在存在非空特殊选项时添加 `dialect_options`；普通全长 UNIQUE 没有 PG 的空 INCLUDE。不能由 PG 失败推断默认 MySQL 也因相同空值失败。特殊前缀/解析器等仍应拒绝。

获准的一轮测试：`.implementation/test_receipt_pg_reflection_batch20.py`，经 MAIN `.venv_py312/Scripts/python.exe` 和本树 `tests/run_isolated_backend.py`，`--maxfail=1 --noconftest -p no:cacheprovider`，外层预算 60 秒；输出编码显式 UTF-8。实际 XML：6 项，5 passed、1 failed、0 errors、0 skipped，`time=1.399` 秒；pytest 文本 1.40 秒，外层 2.590 秒。未再次运行。原始日志第 1 行记录产品与反射源码 SHA，第 2–6 行是五个控制输入，第 7–17 行是正常 PG 输入与真实拒绝栈，第 21 行是本轮汇总。

| 离线用例 | 当前真实函数实际结果 |
|---|---|
| partial / `postgresql_where` | 按预期拒绝，通过 |
| prefix / `mysql_length` | 按预期拒绝，通过 |
| expression | 按预期拒绝，通过 |
| 非空 INCLUDE | 按预期拒绝，通过 |
| 普通 MySQL 全长唯一索引反射 | 接受，通过 |
| PG 普通唯一索引、空 INCLUDE | 应接受却抛“工具回执唯一索引不匹配”，合格离线行为红例 |

载入的是完整真实 `app.schema`，未摘抄校验函数。导入前仅用合成 `app.config/app.db` 阻断部署配置和默认引擎；运行期间拒绝 `.env*` 读取、socket 连接和 SQLite 连接，隔离 runner 同时禁 MySQL/PG 驱动。`inspect` 返回的字典是参照实际反射源码构造的桩，不是实际 PG 目录查询。结论仅证明当前 Python 校验分支与该反射形状不兼容，不证明真实 PG 服务、迁移、驱动连接或完整平台行为。

待应用补丁 `.implementation/receipt-pg-empty-include-batch20.pending.patch` 只豁免精确的 `postgresql_include=[]`；继续拒绝其他非 None 方言选项及表达式、错误唯一性、错误列集合。不把所有空值、False 或未知选项一并放行。补丁尚未应用，也未做补丁后的绿例；产品 `schema.py` 执行前后 SHA 均为 `82d03adf1932e81033203dbb4073f480bcd16af51a97a098aefc7d570691e585`。

## 4. 独立迁移入口的共同合同

1. 离线计划模式仅接收显式方言、目标标识和冻结的目录快照，不构造默认 Engine、不读取 `.env`、不连接数据库，不输出可直接对生产运行的 DDL 文件。真正 inspect/apply 是不同模式，必须提供显式连接目标、预期库/schema、来源版本、固定发布契约。敏感 DSN 不回显；凭据不放命令行或证据 JSON。
2. 将 FROM 固定为已审查的 `20260917_32`，TO 固定为 `20261004_33`。先从精确旧发布代码在隔离条件下生成并审查合法旧 checksum，再固化到迁移包；本轮未计算此值，不能把现测试的 `legacy-fixed` 当作部署摘要。不得引用随未来代码变动的“当前 SCHEMA_VERSION”执行历史迁移，也不得提供随意接受来源摘要的 force 开关。
3. 第一条 DDL 前检查来源版本、合法旧契约、全部必需既有表/列、目标版本冲突、未知较新版本及回执结构状态。旧版 checksum 是 ORM 描述，未覆盖索引、默认值，也不证明数据库实物正确；目标 checksum 必须在相同模型注册集合下计算，仍须独立核验真实列/索引。较老或不完整平台库走其他明确升级，不由本工具补齐全部平台。
4. `receipt_key` 必须为 nullable VARCHAR(64)，无非空默认值/生成表达式；索引为指定名称、完整单列、唯一、普通 B-tree。拒绝 MySQL 前缀、PG partial/expression/非空 INCLUDE、无效/未就绪索引、`NULLS NOT DISTINCT` 和未经支持的分区/特殊表类型。多个旧 NULL 必须仍合法。PG 默认 NULL 互不视为相等，不能误选 NOT DISTINCT。[PostgreSQL 18 CREATE INDEX](https://www.postgresql.org/docs/18/sql-createindex.html)
5. 首版只操作回执列、回执索引和一条版本登记。不回填历史 JSON，不删除旧索引或版本，不修改旧行，不扫描业务数据源。已存在非 NULL 回执时，唯一约束冲突直接停止，不去重、不擦除。
6. 标识符由明确库/schema与固定表列名构造，经方言标识符编译器引用；值参数化。PG 全限定 schema，不能让 search_path 导致检查 A 表却修改 B 表。默认只支持声明并实际验证过的具体服务族/版本/存储引擎；不能把 MySQL 8.4 文档当作 MariaDB、5.7、全部 PG 版本的实测证明。

状态机采用以下最小、可审查分支。所有分支先验证来源和已有对象没有冲突：

| 物理状态 | v33 台账 | 允许动作 |
|---|---|---|
| 无回执列/无回执索引 | 无 | 加列，建索引，复验，最后登记 |
| 正确列/无索引 | 无 | 建索引，复验，最后登记；识别为 MySQL 可恢复半成品 |
| 正确列/正确索引 | 无 | 复验后登记；不能重复加列/建索引 |
| 正确列/正确索引 | 正确 checksum | 验证后无写入返回；保留 applied_at 与全部旧版本行 |
| 任意错误列/索引，或版本摘要冲突 | 任意 | 立即失败，禁止自动 DROP/改列/覆盖摘要 |
| 结构不完整 | 已有 v33 | 拒绝；已宣告完成却缺结构属于损坏，不自动补成“成功” |

## 5. MySQL 首版方案：停写、同连接锁、分阶段完成

在另行授权执行前，核实实际主机/端口/库名/服务 PID 与数据目录，排除隧道/代理；停旧 Host/Worker 和该平台库其他写入者，保存可恢复备份和发布契约。当前没有核实这些实际部署信息，因此本计划没有授权或执行任何目标连接。

使用一个专用短生命周期连接，禁隐式默认连接池与自动重连；在首次目录检查前取得与 `initialize_schema` 完全相同的锁名：`test5_schema_` 加目标库名 SHA-256 前 32 位。持锁检查、DDL、验证及登记都在同一连接上。MySQL 命名锁不会因事务提交/回滚自动释放，必须显式释放或确认会话结束；该锁只协调遵守约定的同一 mysqld 上的参与者，不代替实际停写。[MySQL 8.4 锁函数](https://dev.mysql.com/doc/refman/8.4/en/locking-functions.html)

在 S0 状态最多两条结构操作：添加列、建立唯一索引。每次明确成功后重新获取相关目录状态，避免 Inspector 缓存；最后通过结构检查，才以普通事务插入 v33 台账。若目标记录已经存在且正确，完全不改时间和历史。获取锁失败、结构冲突、任意语句失败立即停止，不重试。

MySQL `ALTER TABLE/CREATE INDEX` 会隐式提交，不能套一个 Python transaction 就宣称列、索引、台账全有或全无。[MySQL 8.4 隐式提交](https://dev.mysql.com/doc/refman/8.4/en/implicit-commit.html) 支持的 InnoDB 原子 DDL 是单条语句的崩溃原子性，不是跨两条 DDL 与台账 DML 的事务原子性。[MySQL 8.4 Atomic DDL](https://dev.mysql.com/doc/refman/8.4/en/atomic-ddl.html)

因此，列成功后索引失败留下 S1；索引成功后台账失败留下 S2，须如实记录已完成步骤和错误。连接丢失/提交 ACK 丢失时标记 unknown，不在异常连接上继续探测、不自动重连重跑。操作人员确认原操作停止后，另一次明确授权的检查识别 S0/S1/S2/S3，选择向前完成。日志写失败也不能报告成功；从目标目录与台账重新核实，不能只信本地“最后一步”日志。

建议锁等待上限 5 秒；具体目录读取与单条 DDL 预算须在受控实例先量定，再作为维护窗口参数固定。MySQL metadata lock 的等待与索引构建总时长不同，客户端读超时也不保证服务器 DDL 已停止；不能使用 SELECT 的 MAX_EXECUTION_TIME 冒充 DDL 总超时。超时后停止后续步骤，记录连接/操作标识，由有权限的操作人员确认/终止残留语句，不自动延长预算或重试。首版不承诺在线、不阻塞、不重建表，也不自动回退到更昂贵的算法。

## 6. PostgreSQL 首版方案：维护窗口内单事务

PG 迁移仍是待交付兼容路径。先解决第 3 节校验器与正常反射的差异，再以明确 schema 和受支持版本为范围。停所有旧写入者；一个事务内获取基于目标数据库/schema的事务级 advisory lock，检查、加列、普通唯一索引、复验、登记，一起提交。事务级锁随事务结束释放；它仍是合作锁。当前 startup 对 PG 只有进程内 RLock，不能宣称现有 Host 启动已参与跨进程 advisory lock。首版以停机维护约束保证互斥；若将来允许并发启动，需要显式接入相同锁协议。[PostgreSQL 18 锁机制](https://www.postgresql.org/docs/18/explicit-locking.html)

使用非阻塞 try-lock 或固定短等待，并给本事务设置有限 lock_timeout/statement_timeout，不修改数据库全局设置。首版使用普通 CREATE INDEX，不使用 CONCURRENTLY；普通索引可处于事务内，而并发索引不允许位于事务块内，还可能留下无效索引，增加恢复状态。[PostgreSQL 18 CREATE INDEX](https://www.postgresql.org/docs/18/sql-createindex.html)

预计任意提交前异常回滚本批 DDL 与版本登记；这须在真实受控 PG 实例验证后才可作为已验证结论。即使 PG 事务本身原子，提交 ACK 丢失仍是客户端未知，不能依据一次异常断言“肯定回滚”。后续只读复核仍需显式执行。目录验证须补充 index valid/ready 等状态，普通 SQLAlchemy 索引字典未覆盖的状态不能仅靠名称存在推断成功。

## 7. 离线测试设计、后续准入与回滚边界

下表是待实现测试设计，除第 3 节六项反射测试外，本轮没有运行这些项目：

| 测试层 | 固定输入/负对照 | 可证明内容与不能证明内容 |
|---|---|---|
| 导入与目标隔离 | 禁 `.env` 文件读取、禁 socket/全部 DBAPI connect；缺显式目标、目标 schema 不符 | 证明计划/校验无默认目标副作用；不能证明服务器可达 |
| 真实方言编译 | 从共享真实 RunEvent 契约生成 MySQL/PG 编译结果；怪异 schema 名、引号字符 | 检查 VARCHAR(64)、NULL、完整唯一与标识符引用；编译不等于数据库执行 |
| 版本来源 | 正确 v32 固定摘要；旧摘要冲突、目标冲突、未知较新版、缺既有表列 | 任何冲突在第一条 DDL 前失败；不覆盖已有台账；禁止 `legacy-fixed` 作为发布证明 |
| 严格目录校验 | 当前六项，加非唯一/多列/错长度/NOT NULL/默认空串/生成列/PG invalid/NOT DISTINCT | 正常默认值兼容，同时不能把弱索引判为规范唯一；桩不冒称真实目录 |
| 可恢复状态机 | S0/S1/S2/S3 与损坏状态，记录连接及操作调用序列 | 至多两条 DDL、一条新增版本记录；重复执行不改旧行/时间；不靠宽泛异常吞掉失败 |
| MySQL 故障边界 | 受控假连接模拟 DDL 隐式提交；加列后、建索引后、版本提交前/后及 ACK 丢失注入 | 验证控制器报告 partial/unknown，不错误报告整体回滚；不能代替 MySQL 原子性实测 |
| PG 故障边界 | 受控事务桩分开已暂存/已提交状态；锁冲突、DDL 错误、提交 ACK 丢失 | 验证无重试与事务调用顺序；不能证明真实 PG 回滚行为 |
| 冻结契约一致性 | startup 和 migrator 共用校验；安装 SQLAlchemy 版本与反射源 SHA 记录 | 防止迁移接受而启动拒绝；不把单一依赖版本当作所有允许版本已覆盖 |

后续离线组建议一个进程、外层 60 秒、首失败停，不并行扩大组；连接次数和实际操作序列由桩记录。真正 DB 验收另行明确授权本机一次性 MySQL/PG 实例、主机/端口/临时库名/本机服务归属与上限，不能默用 `company_agent` 或 `szh_new`。从少量旧事件和旧版本行起步，真实验证 NULL 多行、相同非 NULL 唯一冲突、重复执行旧行逐字段不变、结构反射、失败恢复和锁竞争。MySQL/PG 各自分别记账；SQLite 通过不替代它们。目录反射内部 SQL 也要计数，不能将一个 Inspector 调用当作一条 SQL；事前冻结实际小组预算，零业务源/模型请求。

代码回滚与数据库回滚须分开：

- 旧版 `4f47d51` 的启动检查只查自身版本记录与必需列；额外 v33 行/列不会自动阻止它启动。保留 v32 后旧二进制可能正常启动、继续插入 `receipt_key=NULL`，从而失去新增回执保证。不能把 v33 登记当作阻止旧版混跑的屏障。
- MySQL 部分 DDL 后优先向前恢复；禁止自动 DROP 回执列/唯一索引、删除 v33 记录或覆盖旧 checksum。这些操作可能丢失已经生成的恢复证据。
- PG 已确认的提交前失败可按事务语义回滚；提交 ACK 未知时先核实状态。已提交迁移也不能用“回滚事务”撤销。
- 若必须退回旧代码，应停止新版 Host/Worker，保留新增结构与回执数据，单独验证旧版运行边界并明确回执恢复功能不可保证；本轮没有验收降级兼容。恢复备份是另一个有数据丢失风险的写操作，须考虑备份后新增数据并获得具体授权，不能随迁移失败自动执行。

本方案不解决工具外部副作用的跨系统原子性，不扩展 ACK 双丢/EOF 自动接管，不声称 exactly-once。MySQL/PG 迁移实现、真实本机数据库执行、生产维护窗口和服务加载均未交付。

## 8. 源码与证据 SHA-256

以下读取源用于约束计划；本轮未写这些文件。已存在的 B19 根代理改动和门禁工作原样保留。

| 路径 | SHA-256 |
|---|---|
| `backend/app/config.py` | `9c181e74f84c940d31fbf955d8d99d39ddef38de40cfed360dccbe8c39380c5d` |
| `backend/app/db.py` | `3020883d529ce5b65625c29c756395d5e2ee71045c79505bfa1912bafc4e3973` |
| `backend/app/schema.py` | `82d03adf1932e81033203dbb4073f480bcd16af51a97a098aefc7d570691e585` |
| `backend/app/business_worker/receipt_migration.py` | `79c110d53dc4876d8c655272389b7eb7e8d12ffa82d00b535c1c34eb66b87537` |
| `backend/app/models.py` | `dc14bc37e674e1e10ad2c21a81f35d7462c99b701962008c4215a85681ca9adf` |
| `backend/app/seed.py` | `12e67f9e5e22917d8c46fb30319b9262e938219e855a1b5ddbee3c2d38247c8e` |
| `backend/app/modules/system_tools/migrations.py` | `2ec211681142544bdcd9e17e3eb15bfe96485c72d60bb0856732a9153c45a679` |
| `services/dsh_host/launch.mjs` | `5c9e6d86af462fed4ce6a069257df1f4b2f8cefded073293178b907741b33b7f` |
| `docs/audit/review_20261004/fix-durable-receipt-batch19-revision2.md` | `6ae4f17871c426523ecbfd3690a45029c2f2c7cd53402eeaeb60a13e4830fb94` |
| MAIN `.venv_py312/Lib/site-packages/sqlalchemy/dialects/postgresql/base.py` | `68e64840a3941d8725ccc8f0bb642da8a75f3b60a2ad34944b21a06828837a8d` |

本轮新增证据均位于本树 `.implementation`：

| 文件 | SHA-256 |
|---|---|
| `test_receipt_pg_reflection_batch20.py` | `0dc659184f0bfefd2e576d8b7b4e9d173efd86e0eff6b61248e7e81e3d704c26` |
| `receipt-pg-reflection-batch20-red.log` | `c45e1a70958fd999fd3f3004f54a17e963b1bb1a55b5d37dffe9f54e5e4a2b81` |
| `receipt-pg-reflection-batch20-red.xml` | `87513beb2e6eba484cfe23bc5d3b14f7ab5fde4d121c047278be70732a26f04c` |
| `receipt-pg-reflection-batch20-red.json` | `ec65a93363b50938e76683c7b9fc018c8d29a197a4ab5cec7a711fdd0d660b19` |
| `receipt-pg-empty-include-batch20.pending.patch` | `3616e01b9b8ddd742f64e79d6e782b2af523089ab07f89f0dbee33ab024bc313` |

独立 reviewer 仅做了只读复核，确认业务归档工具不可复用、默认配置导入链、checksum 不等于实物校验、旧版不会被新台账自动隔离。未让 reviewer 修改文件或重复运行测试。收尾逐项对照 B19 revision2 evidence 的 16 个产品/测试源码 SHA，全部一致；没有把该 16 项对照写成重新运行根代理全部 45 文件/88 assets 冻结校验。本计划自身 SHA 在交付消息记录，不写入自身造成递归变化。
