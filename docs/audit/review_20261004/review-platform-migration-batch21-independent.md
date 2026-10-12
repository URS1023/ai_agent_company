# B21 显式平台迁移独立只读复核

时间：2026-10-04 15:09:41 +08:00。唯一读取树 `major-platform-migration`。本轮没有执行 Python/Node 产品代码、pytest、浏览器、数据库或模型，没有读取 dotenv；没有修改产品、测试或原报告，只新增本 ignored 审查文件。B19/B20/B22 未被改动。

## 具体遗漏：Ctrl-C 可绕过 DDL/COMMIT 未知结果的记录和连接失效

静态问题位置：`backend/app/business_worker/receipt_platform_migration.py:320` 仅捕获 `Exception`，而 `:350–353` 的 finally 直接关闭仍持有的 connection。`KeyboardInterrupt` 不属于 Exception。

若用户在 `:279` 已发送 ALTER/CREATE、或 `:301` 已发送 COMMIT 后等待回包时按 Ctrl-C，语句可能已在数据库完成，但当前中断会绕过 `:321–347` 的 unknown 分类、connection.invalidate 和 failed 证据记录。main 的 `:429` 同样只捕获 Exception，无法补上结构化终态。这与已有 OperationalError 丢 ACK 分支不同：后者已正确记录 unknown 并禁止后续 SQL。

还有同一窗口的日志计数问题：`operation_started` 在 `ddl_attempts`/`version_insert_attempts` 自增前刷盘（`:276→277`、`:294→295`）。中断或进程崩溃后，最后一条持久记录可能是 `phase=add_column/event=operation_started/ddl_attempts=0`，或 `phase=register_version/version_insert_attempts=0`。不能把这类零计数解释为“未发送结构或版本写入”。现有实现没有自动读取这份日志重试，因此本轮没有断言它已经导致重复 DDL；问题是关键维护中断路径缺少与既有 ACK 丢失相同的明确未知状态。

可直接复用现有离线夹具的最小反例（本轮未执行）：

1. 使用 `test_receipt_platform_migration.py:112` 的 ControlledConnection，保留 `lost_add_ack` 的“先把列加入 snapshot”顺序，仅把后面的 OperationalError 改为 KeyboardInterrupt。
2. 调用真实 migrate，捕获 KeyboardInterrupt 后查看 events 与 connection.calls。
3. 当前会看到物理效果桩已变化、没有 unknown/failed 终态、未 invalidate，最后走 close；持久 operation_started 的 ddl_attempts 仍为 0。
4. 同样沿 `lost_commit_ack`，先把 pending 版本追加到 snapshot，再抛 KeyboardInterrupt：台账可能已提交，却没有 committed/unknown 的确认边界记录。

建议：在 sql_inflight 的进程中断路径也保留 unknown，失效该连接后收尾，不能在未确认连接上追加 SQL 或重发动作。记录开始意图时同时保存当前尝试编号/在途步骤；对于没有 finished 的日志，明确恢复规则为“需人工核验，不能由零计数推断未执行”。允许在完成安全记录后保留原始中断语义。新增两个中断反对照即可，不需要扩大到真实数据库测试或自动恢复。

## 其它已核对项

- DDL 前固定 v32 checksum、目标 checksum 冲突、未知较新版、必需表/列存在性，以及目标两表的 InnoDB/普通/未分区属性会被检查。回执列和唯一索引已有半成品必须满足严格定义；已登记 v33 缺结构会拒绝自动修复。注册当前 ORM 时另以固定 v33 和移除 receipt_key 后的 v32 摘要防止未来模型冒用旧迁移。
- 当前全表旧结构检查是列名存在性，不是对 154 张表的类型、nullable、默认值和索引进行完整实物比对：`expected_columns():90` 返回列名集合，`plan():110–113` 仅比较名字。固定旧 checksum 是历史台账/ORM 契约校验，不能被描述为已对当前数据库逐列重新求结构摘要。原报告称“154 张表必需列”，表述与代码一致；本轮未将这一已限定范围冒充新增阻断缺陷。
- MySQL DDL 不被伪装成整体事务。列完成、索引明确拒绝会保留 partial；现有 DBAPIError ACK 丢失/读取断连路径会失效连接，保留 confirmed_steps，不继续释放锁查询或重发 DDL。固定锁名与 startup 的库名摘要算法一致，整个正常流程沿同一 connection。
- 版本登记在 DDL 后目录复查、plan 检查和实际 startup receipt validator 之后；已有完整 v33 不重复插入。日志刷盘失败后不宣称成功，即使 commit 已确认也保留 committed 状态。
- 引入 TYPE_CHECKING 的 owned_records 改动只移除注解触发的运行时 AuthContext 导入，权限函数调用本身未变化；schema 的配置/默认引擎导入延迟到 initialize_schema。新子进程导入测试同时阻止 app.config/app.db、引擎构造和 socket，测试范围包含真实模型注册。
- 入口要求显式数字回环 host、port、database、user 与预期服务 hostname/port；本机服务/停写/备份仍是操作员必须实际核实的声明。没有自动读取部署 URL。password 来自不回显终端或显式 stdin，URL 只在内存，第三方异常正文不进入受控控制台/证据结果；本轮没有发现一个可具体触发的新增密码日志泄露路径。
- 120 秒总预算仅在主要阶段边界检查，read_catalog 五次目录调用与 SQLAlchemy 反射内部可跨越检查间隔。原报告已明确这不是可靠的服务器 DDL 取消机制，故没有重复列成未披露缺陷。

## 证据范围

本轮只阅读现有 35 项新增＋9 项 SQLite 的 44 passed 记录，未重跑。它们证明离线状态机/目录行形状/导入/日志决策，以及原临时 SQLite 行为；不证明真实 MySQL 锁、驱动、服务器 DDL、崩溃恢复或性能已验证。PostgreSQL 仅方言编译，没有 apply 分支。已知空 INCLUDE 修复没有重复列为发现。

## 读取 SHA256

末次读取四个产品/测试文件与现有完整迁移组的冻结 SHA 一致；后续代理修改不自动包含在本结论中。

| 文件 | SHA256 |
|---|---|
| `backend/app/business_worker/receipt_platform_migration.py` | `413864DD769D96F69E2DA7E62FA8FBED32C31C600EBA783FCC23DF18A811A41D` |
| `backend/app/schema.py` | `EBAD2FA661B9587B6C4942A3E3D08FA125AB25FC7E072032EDE08F1D2F3F300F` |
| `backend/app/common/owned_records.py` | `5B639E604687E7C62D1E8BF7B40DA4F95292CF3CB9DA811CAA46E889E4F873FE` |
| `tests/backend/test_receipt_platform_migration.py` | `EC5C3F18E8F06375A0B72C7630DEC0DAD0975ABBF9CBEA93EDCFDD31EC6C83BB` |
| `.implementation/fix-receipt-platform-migration-batch21.md` | `1B7CBF0C3DC02BECF7455E0989389FB7CE5C0713E67D407B307C0C1E56B06E91` |
