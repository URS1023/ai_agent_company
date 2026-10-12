# B21 显式平台回执迁移：实现与离线验证

日期：2026-10-04。唯一树 `major-platform-migration`，分支 `codex/major-platform-migration`，基线 `cfe11ac88fa56c714c8c75e4c36ce34dfcb214d9`。本轮未修改 MAIN/B18/B19/B20/B22，未提交、推送、启动服务、浏览器或完整门禁。当前四个产品/测试文件保持最后完整迁移组通过时的字节，等待根代理审查。

本轮已交付显式 MySQL v32→v33 迁移代码与受控离线合同。**没有连接或迁移真实 MySQL/PG、业务库、模型或 8011；不声称生产升级完成。** PG 完整迁移没有实施，仅有真实方言编译检查和继承 B19 的空 INCLUDE 校验补丁。历史设计文件仍叫 `plan-receipt-platform-migration-batch20.md`，编号未追改；当前业务批次是 B21。

## 固定来源与目标契约

从精确历史提交 `4f47d51eb2deb1de7e39dccd21310c9133e1da8e` 提取原始 `backend/app/*.py` 到本树 ignored 快照目录，运行其真实模型注册。配置/DB 模块由无连接受控桩提供，禁止 socket、SQLite 连接及默认引擎构造；旧 `initialize_schema` 在完成原始注册后、获取引擎前由哨兵停止，再调用原始 `_contract_checksum`。没有改写历史模型或用手造表集合充当摘要依据。

| 契约 | 固定版本 | 实际注册摘要 |
|---|---|---|
| 精确旧提交 | `20260917_32` | `7700101ef2693dd853f84a0c724c916d45e9d6d8e83fe4aa5c071f2e1827b8e4` |
| B19 基线真实模型 | `20261004_33` | `097b9b907ad59a4c729f45670227fc4582806c2c17a6787da4ecb4b8061f7f7a` |

两者均 154 张表；完整已注册契约只差 `run_events.receipt_key VARCHAR(64), nullable=True`。全表列清单及实际加载源码 SHA 在 `receipt-contract-batch21-v32.json`、`receipt-contract-batch21-v33.json`，差异在 `receipt-contract-batch21-diff.json`。首轮快照因控制桩缺 `session_factory` 失败，原 stderr 和首版脚本均保留；补齐只会拒绝连接的函数后才成功。此错误是夹具错误，不是产品红例，也没有连接数据库。

生产迁移每次重新注册真实模型并核对固定 v33 摘要；去掉回执列后的摘要也必须等于已证明的 v32。未来模型变化不能悄悄借用此历史工具。不接受 `legacy-fixed`、任意调用方摘要或覆盖旧台账的强制开关。

## 改动范围与最终行为

| 文件 | 内容 |
|---|---|
| `backend/app/business_worker/receipt_platform_migration.py`（新增） | 显式目标、离线目录计划、MySQL 目录适配器、迁移状态机、凭据入口和刷盘证据文件 |
| `backend/app/schema.py` | 将 config/db 运行时导入移入实际启动函数；应用与 B19 待补验相同的精确空 INCLUDE 豁免 |
| `backend/app/common/owned_records.py` | 经根代理追加授权，将仅用于两处类型注解的 AuthContext 导入放到 TYPE_CHECKING；权限执行逻辑不变 |
| `tests/backend/test_receipt_platform_migration.py`（新增） | 35 项真实导入、固定模型、目录转换、状态机、丢确认、日志、CLI 脱敏与方言编译合同 |

没有修改既有 `receipt_migration.py` 或原迁移测试语义。只读检索未找到后端/测试对 OwnedRecord 查询函数调用 `get_type_hints/get_annotations` 或从其模块导出 AuthContext 的依赖；与相邻 authorization.py 的既有 TYPE_CHECKING 模式一致。

迁移入口不读取 `.env` 或默认 DATABASE_URL，不运行 seed、不创建默认引擎。真实完整 `_register_models` 导入合同通过阻断 app.config/app.db、引擎构造和 socket 的子进程验证。离线 `plan` 接受目录 JSON 与明确目标，只返回操作计划；不会连接或写部署 DDL 文件。

当前 MySQL 执行器限定数字回环目标与显式 host/port/database/user、预期 server hostname/port，先比较服务器返回的实际数据库、版本与服务标识。仅实现 MySQL 8.0.13+ 的 8.0 系列和 8.4 系列，拒绝 MariaDB 和其他版本范围。真正 apply 还须明确确认已停旧写入者、本机服务归属、备份引用，并提供独占新建的本机绝对日志路径。参数确认是维护人员的声明，不能代替进程、端口、数据目录与隧道排除核实，也不构成执行授权。

凭据仅从不回显终端或显式 `--password-stdin` 读取，没有密码命令行参数；控制台/证据不打印连接 URL 或第三方异常正文。显式引擎使用 NullPool、禁止 pre-ping/自动重连、5 秒连接/写期限与 30 秒读取期限。代码不接业务来源数据源，不修改在用绑定。

取得与启动完全相同的 MySQL GET_LOCK 名称后，在同一连接检查：固定 v32 台账、目标/较新版冲突、154 张表必需列、两张目标表的 InnoDB 普通未分区属性、规范回执列与完整唯一索引。任何冲突在第一条 DDL 前停止。已有 v33 却缺回执结构属于损坏，不能自动补成成功。旧结构只增加回执列和索引，不复制或回填历史事件；不会 DROP、去重或覆盖已有 checksum。

明确区分四个可处理状态：无列无索引、已有正确列但无索引、列与索引正确但未登记、完整已登记。最多两次 DDL 尝试和一次版本插入；重复执行完整版本不修改旧行或版本时间。DDL 由真实 RunEvent 列/索引按方言编译并引用目标库名，固定保留发布索引名。

列与索引完成后复查实物，并调用真实启动校验器，最后才提交 v33 台账。MySQL DDL 有隐式提交，列成功但索引明确失败报告 partial；DDL/提交 ACK 丢失报告 unknown，保留已确认步骤，废弃连接，不在不确定连接上继续 SQL、释放锁查询或重试。通过真实临时 JSONL 验证提交丢确认时没有成功记录；证据刷盘失败不能当作成功，即使台账已实际提交也保留 committed 标记并报 evidence_failed。

## 原始红绿和故障记账

所有 pytest 均用 MAIN 的绝对 Python 路径和本树 `tests/run_isolated_backend.py`，UTF-8 输出，串行、首失败停。后续小组显式 `--noconftest`，避免不相关公共夹具导入。第一轮原始导入红例的父进程加载了既有公共 conftest，但 runner 已指定隔离 SQLite/合成配置；当时三个实际 dotenv 候选路径均不存在，受测子进程在加载 app.config 前即被守卫拒绝。不能把父进程的公共夹具导入写成受测迁移已经隔离成功。

| 独立轮次（文件前缀） | 实际结果 | 说明 |
|---|---|---|
| `receipt-platform-batch21-import-red` | 1 failed；pytest 2.09 秒，XML 2.092 秒，外层 13.063 秒 | 基线真实既有 receipt_migration→schema 尝试导入 app.config；产品尚未修改，合格导入行为红例 |
| `receipt-platform-batch21-small-first` | 1 passed、1 failed；5.88 秒，外层 7.453 秒 | 修复后导入已通过；新增迁移成功用例在 DDL 前失败，立即停组 |
| `receipt-platform-batch21-small-diagnostic` | 1 failed；2.55 秒，外层 4.281 秒 | 仅增加控制桩异常诊断，定位 SQLAlchemy 复制自动索引时把 schema 拼进名字，固定名称查找 StopIteration；不是新的基线业务红例 |
| `receipt-platform-batch21-small-corrected` | 1 passed；1.81 秒，外层 3.532 秒 | 修复为按真实原索引列/唯一性匹配副本并保留固定原名；实际走成功与重复执行 |
| `receipt-platform-batch21-migration-group-first` | **44 passed**；pytest 文本 53.14 秒，XML 52.918 秒，外层 55.156 秒 | 新增 35 项＋原有临时 SQLite 迁移 9 项；外层 180 秒，执行前后四文件 SHA 一致 |

原始 `.log/.xml/.json` 均独立保存，没有覆盖失败日志、合并轮次或将诊断失败充当额外产品红例。XML 计时与 pytest 文本整体收尾耗时并不相同，按实际值分别记录。首轮受控快照导入错误另存 `receipt-contract-batch21-v32.stderr.log`，不计入 pytest 结果。

44 项覆盖真实完整模型注册不加载部署配置、固定前后摘要、四种恢复状态、错误旧/新摘要、未知较新版、缺表列、错误实际库/服务、MariaDB、分区、错误列/默认值/弱索引、已登记缺结构、同连接锁、成功后重复不写、已持久列后的索引失败、三处丢 ACK 与读取断连、锁拒绝、提交后日志失败、真实 JSONL 未知记录、真实 MySQL/PG 编译、目录转换/读取上限、离线 CLI 无引擎、凭据脱敏。目录适配器用驱动行形状桩实际调用，单次读取固定五个受控 execute 调用；它不是五条真实数据库 SQL 执行证据。

既有九项迁移测试实际使用临时文件 SQLite，覆盖旧行/旧版本保留、重复执行、NULL、多非 NULL 唯一约束、部分迁移、错误列/索引拒绝和普通启动不替代迁移。它们的历史 `legacy-fixed` 仍是原测试夹具，未放宽旧测试；B21 的 MySQL 入口另用精确固定发布摘要。

AST 解析与 `git diff --check` 通过。Git 提示本机换行转换（LF→CRLF），无空白错误；交付以下 SHA 均为工作树实际原始字节，并另存四文件冻结副本。

## 部署与未覆盖边界

- 本轮实际 MySQL/PG 连接和 DDL 为零。MySQL 状态机用受控连接模拟真实“效果已落地、ACK 丢失”的先后顺序；这证明代码决策和日志，不证明服务器驱动、目录字段、锁、DDL、崩溃恢复或性能在真实实例已通过。
- PG 只有编译验证，没有 PG apply 分支。精确空 `postgresql_include=[]` 的三行行为改动属于先前 B19 发现与补验范围，本批只是同样应用以保护共享校验；未重复计为 B21 新修复。B19 之后合入时须保留本批 schema 的配置导入隔离。
- 需要另行授权本机临时 MySQL 实例的明确 host/port/库名/服务归属，先做极小真实迁移与半完成恢复验证。首版未证明在线迁移、复制集/多主、分区表、MySQL 5.7/MariaDB 或高负载行为；不应直接宣称兼容所有驱动允许的数据库。
- 120 秒总预算在主要阶段边界阻止后续步骤，网络读取期限限制等待；它不是服务器 DDL 的可靠取消机制，目录反射内部调用也可能跨越检查间隔。出现超时/断连必须先停止、核实残留操作，不自动延长预算/重连/重试。执行生产前仍需按实际实例规模冻结维护窗口与语句预算。
- MySQL 加列、索引与版本台账不具备整体事务回滚。日志 partial/unknown 时需另一次明确检查识别实物状态，优先向前完成；不能自动删除列/索引或台账恢复表面旧版。
- v33 台账不阻止旧版启动，旧程序可能继续写 NULL 回执。必须真正停止旧 Host/Worker 与写入者，禁止未经验证的混跑或直接把旧代码回滚当作数据库回滚。
- 未实际执行备份/还原、部署升级或服务重启；旧代码降级兼容与并发真实锁竞争仍待验。不涉及工具外部副作用的跨系统原子性，不声称 exactly-once。

## 冻结 SHA-256

| 文件 | SHA-256 |
|---|---|
| `backend/app/business_worker/receipt_platform_migration.py` | `413864dd769d96f69e2da7e62fa8fbed32c31c600eba783fcc23df18a811a41d` |
| `backend/app/schema.py` | `ebad2fa661b9587b6c4942a3e3d08fa125ab25fc7e072032ede08f1d2f3f300f` |
| `backend/app/common/owned_records.py` | `5b639e604687e7c62d1e8bf7b40da4f95292cf3cb9da811caa46e889e4f873fe` |
| `tests/backend/test_receipt_platform_migration.py` | `ec5c3f18e8f06375a0b72c7630dec0dad0975abbf9cbea93edcfdd31ec6c83bb` |

完整源码/日志/XML/契约快照/辅助脚本的 SHA 及逐轮实际 XML 数据在 `.implementation/receipt-platform-batch21-evidence.json`；当前相对基线完整 patch 为 `.implementation/receipt-platform-batch21.patch`；原字节副本在 `.implementation/receipt-platform-batch21-freeze/`。这些原件均保留，不替代尚未完成的真实 MySQL 验收。
