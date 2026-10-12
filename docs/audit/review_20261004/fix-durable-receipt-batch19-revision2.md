# B19 持久工具回执最终冻结：revision 2

唯一树 `major-durable-receipts`，分支 `codex/major-durable-receipts`，起点仍为 `4f47d51eb2deb1de7e39dccd21310c9133e1da8e`。未合入 MAIN/B18，未提交、推送、运行在用数据库或服务。

前版完整实施与边界记录原样保留：`fix-durable-receipt-batch19.md`，SHA256 `23652b04660ac6bc779ceef766629314abd46ccc6af273bd5fd162b12b2995e5`；前版证据JSON `durable-receipt-batch19-evidence.json`，SHA256 `5a0f5a77d051ed872f46839e7f1172022bda729273fd96c8392c57350c6b4ba7`。本文件与revision2 JSON是追加记录，不覆盖前版红绿与哈希。

## 本版唯一差异与行为证据

前版 `chat.append_event` 的规范回执防覆盖条件排除 `native=True`。真实业务工具已成功后，带 native 标记的碰撞事件可继续走旧投影分支，把消息改成失败。根代理在尚未启动B19相邻回归时授权一个真实行为红例及窄修复。

- 产品只改 `backend/app/business_worker/chat.py` 一行：去掉 native 排除。已有规范回执的工具事件统一返回不含业务结果的 ignored 或身份冲突；无规范回执时既有原生事件逻辑仍继续。
- 测试只向 `tests/backend/test_durable_tool_receipts.py` 追加一个真实 current_datetime 成功后注入 native 标记碰撞的用例。未凭缺新字段制造失败，也未用假成功回执代替实际执行。
- 其余14个源码/测试文件逐项对照前版SHA保持不变。JSON记录两文件前后SHA；附加revision2 patch由当前字节反推前版并逐项验证与前版哈希一致，不宣称它是事前另存的源码快照。

| 独立轮次 | 原文件前缀 | 实际结果 |
|---|---|---|
| revision2修复前真实行为红 | durable-receipt-batch19-revision2-red-native | 1 failed，2.43秒。日志第16行保存原成功、实际返回事件和消息failed；第23–27行为真实状态差异断言 |
| revision2受影响回执完整文件 | durable-receipt-batch19-revision2-worker-final | 11 passed，12.01秒，首失败停止，外层180秒，临时内存SQLite，0业务源/模型 |
| 前版最终Worker＋迁移组 | durable-receipt-batch19-worker-migration-final | 19 passed，53.45秒；属于前版，未当作本版同轮验证 |
| 前版Host Node三文件 | durable-receipt-batch19-host-node-final | 26 passed，1945.0118ms；Python包装1 passed，2.34秒。Host文件在本版未修改 |
| 前版真实Host/Worker丢包与冷重开 | durable-receipt-batch19-host-real-corrected | 1 passed，22.60秒。真实调用一次，静默丢reply一次，在线与冷重开lookup各一次，旧Python exit确认后才重开，两路订阅抛错仍返回success。其原执行路径Host/Worker receipt逻辑未变；本版只改变chat.event的native碰撞保护，不称这次真实链路在revision2重跑 |

按授权没有重跑无关迁移或真实Host单例。所有实际XML、原日志、源码SHA均在 `durable-receipt-batch19-evidence-revision2.json`；原件不删不覆盖。revision2命令在外层与子进程显式设置 `PYTHONUTF8=1` 和 `PYTHONIOENCODING=utf-8`。

## 完整成果与限制

规范回执与 ToolCall/RunStep/Message 的最终末段同事务提交；可空唯一receipt_key不回填旧事件、不改变旧幂等键。执行身份含run/agent/call/sequence且参数摘要固定；查询当前主体、工具和资源授权，旧键冲突/未知/无授权均不编造成功。派发前安全拒绝也保存历史投影；`chat.event`只阻止覆盖，不提前返回旧业务载荷。Host直接发布规范游标，订阅失败不改变已提交工具结果；丢回复时单次5秒只读lookup恢复，不给长tool.execute增加总超时，不重新执行工具。未知会停止根/子代理与owned jobs，异步派发边界再检查，整轮保留原交付核验并可收尾interrupted。

不声称exactly-once。最终提交前外部效果仍可能unknown；reply和通知双丢、管道仍存活时没有自动探测或重试；真实EOF自动重启/重连、外部HTTP/MCP副作用、断电、多Host/跨进程并发尚未覆盖。生成/聚合工具无法证明当前资源来源授权时仍拒绝自动回放。同run跨agent/sequence复用call_id保守冲突，不宣称完整支持这类并行重名。真实Host测试运行ToolRuntime与业务Worker，没有运行模型对话或完整文档生成。

迁移只在临时文件SQLite实际验证：保留旧事件/旧版本行、重复执行、NULL兼容、真实重复键约束以及错误列/索引拒绝。没有连接或迁移在用平台库。升级既有部署须先停旧Worker、备份、执行适配平台库的显式迁移，再启动新版。现有文件入口是 `python -m app.business_worker.receipt_migration <existing-absolute-file.sqlite>`，不读默认DATABASE_URL、不运行seed；默认MySQL/其他方言的既有库迁移脚本与执行未交付。缺列或唯一索引应拒绝就绪，不能把AUTO_MIGRATE当作已有库升级。

代码与测试已经冻结，待根代理相邻回归；本代理不启动统一门禁、浏览器或真实服务。
