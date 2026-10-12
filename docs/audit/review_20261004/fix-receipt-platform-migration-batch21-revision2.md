# B21 revision 2：等待数据库确认时的进程中断

唯一树 `major-platform-migration`；分支与基线仍为 `codex/major-platform-migration`、`cfe11ac88fa56c714c8c75e4c36ce34dfcb214d9`。未提交、推送、连接真实数据库或模型，没有启动服务/浏览器/整门禁。

前版报告、证据与源码副本原样保留：`fix-receipt-platform-migration-batch21.md` SHA256 `1b7cbf0c3dc02becf7455e0989389fb7ce5c0713e67d407b307c0c1e56b06e91`；`receipt-platform-batch21-evidence.json` SHA256 `41351dc6e28d27a723c4bd26cbaae396774efb146b8235ea53e3800a952df828`。前版 44 项通过属于前版字节，不当作本轮同一次验证。

## 独立审查与原始红例

独立 reviewer 静态发现迁移只捕获 Exception，KeyboardInterrupt 会在已发出 DDL/等待 COMMIT 确认时绕过 unknown、invalidate 和失败证据，仅执行 finally close。审查报告为 `review-platform-migration-batch21-independent.md`，SHA256 `80127eba98586e2e178b258caea998138d2e4654d96a22b2824828fabc3d5e18`。审查本身未运行反例。

根代理随后授权两个受控行为红。修改产品前，复用真实迁移状态机及 ControlledConnection，分别先把列写入 snapshot、先把 pending 版本提交到 snapshot，再抛 KeyboardInterrupt。两次均保存真实 JSONL 中的全部记录和连接调用序列到 pytest 原始日志；不是仅以缺字段或不存在 API 制造失败。

| 独立轮次 | 实际结果与证据 |
|---|---|
| `receipt-platform-batch21-revision2-red-interrupt-ddl` | 1 failed；pytest 1.80 秒，外层 3.218 秒。日志首行证明列效果已存在，错误仍为 KeyboardInterrupt，只 close；最后 started 的 ddl_attempts=0，无 unknown |
| `receipt-platform-batch21-revision2-red-interrupt-commit` | 1 failed；pytest 1.52 秒，外层 2.656 秒。日志首行证明版本已在 snapshot 提交，只 close；最后 register_version started 的 version_insert_attempts=0，无 unknown |

两次红例产品 SHA 均保持前版 `413864dd769d96f69e2da7e62fa8fbed32c31c600eba783fcc23df18a811a41d`。用例捕获 BaseException 只为让 pytest 报出明确断言，不让 KeyboardInterrupt 终止整个测试进程；产品仍原样处理。

## 窄修复

- 产品只改 `receipt_platform_migration.py`：捕获 BaseException，在确实处于 sql_inflight 确认窗口时保留 unknown，失效连接后关闭，不发送 rollback、RELEASE_LOCK 或再次业务语句；通过 MigrationFailed 返回失败，不能吞成成功。
- 写入窗口外的进程中断记录 interrupted，并重抛原始中断；不编造未知写入。新增负对照证明预检完成、尚未进入写入前中断仍为 KeyboardInterrupt，写入尝试为 0。
- 将尝试编号自增移到 operation_started 刷盘之前。编号表示本次写入意图，不证明实际发送或提交；未结束的意图必须人工核验。日志中的 committed=false 表示客户端未确认提交成功，尤其在 unknown 下不能解释为“数据库一定没提交”。
- 测试只改原新增迁移专项文件：两种实际效果后的中断故障和一个写入前中断对照。schema.py 与 owned_records.py 字节保持前版不变，没有扩大产品范围。

## 修复后验证

| 独立轮次 | 实际结果 |
|---|---|
| `receipt-platform-batch21-revision2-interrupt-green` | 3 passed，pytest 1.57 秒，外层 2.953 秒；两个等待确认中断均记录 unknown→invalidate→close，started 有本次 attempt；写入前中断保持原异常 |
| `receipt-platform-batch21-revision2-migration-final` | **47 passed**，pytest 文本 29.43 秒，外层 30.718 秒；新增迁移合同 38 项＋原临时 SQLite 迁移 9 项；四文件执行前后 SHA 一致 |

均经 MAIN Python 的本树 isolated runner，UTF-8，串行、首失败停止，受影响组外层 60 秒，最终组外层 180 秒。没有扩大超时或自动重试。XML 实际计时及每个用例结果另保存在 revision2 evidence，未用文本耗时覆盖 XML。前后轮耗时不同不能推断迁移性能改善。git diff --check 无空白错误；换行转换提示仍保留为环境信息。

## 当前冻结与证明边界

| 文件 | 当前 SHA256 |
|---|---|
| `backend/app/business_worker/receipt_platform_migration.py` | `b5da74c6f1b834db64bc9f49e449cb6ee72ec52617edcea3fa23ccce08e99a39` |
| `tests/backend/test_receipt_platform_migration.py` | `936ab302cca8c83bbe2834ac4b14564e6410410335701dd685385e40422f6c7d` |
| `backend/app/schema.py`（本轮未改） | `ebad2fa661b9587b6c4942a3e3d08fa125ab25fc7e072032ede08f1d2f3f300f` |
| `backend/app/common/owned_records.py`（本轮未改） | `5b639e604687e7c62d1e8bf7b40da4f95292cf3cb9da811caa46e889e4f873fe` |

本轮是受控 MySQL 效果/确认顺序桩和真实迁移控制器、真实本机 JSONL 的验证。没有向 MySQL 服务发送 DDL，没有验证真实驱动在 Ctrl-C 时的服务器取消、断线或崩溃行为。SQLite 原专项仍实际使用临时文件。PG 仍只有编译、没有 apply；固定来源摘要、154 表必需列名检查、维护确认和凭据边界不变，必需列名检查不等于全表实际类型/索引/默认值比对。

硬杀进程、断电、重复中断导致 Python 无法执行异常处理、磁盘无法继续记录等仍不能保证写出完整终态；日志停在 operation_started 时必须按未知执行状态人工复核，不能凭缺失败记录自动重发。120 秒仍是主要阶段间检查预算，不是可靠的服务器 DDL 取消保证。所有前版真实 MySQL 待验、备份/维护窗口/停旧进程、部分 DDL 向前恢复、旧二进制混跑和非 exactly-once 限制继续有效。

新证据为 `receipt-platform-batch21-evidence-revision2.json`；相对前版真实冻结副本的补丁为 `receipt-platform-batch21-revision2.patch`；当前四文件原字节副本保存于 `receipt-platform-batch21-freeze-revision2/`。旧 evidence、patch、freeze 目录没有覆盖。当前产品与测试再次冻结，等待根代理复核，不再扩跑。
