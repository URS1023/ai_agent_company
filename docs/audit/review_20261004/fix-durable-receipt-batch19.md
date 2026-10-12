# B19 持久工具回执与丢失回复恢复（专项完成，代码冻结）

唯一实施树 `major-durable-receipts`，分支 `codex/major-durable-receipts`，产品起点 `4f47d51eb2deb1de7e39dccd21310c9133e1da8e`。本树不包含 B18 自然续办产品改动。只修改本批授权的 Worker/Host 回执、模型、窄结构初始化及新增专项测试；未修改 tasks/delivery_contract、知识域、配置、统一门禁、正式文档、共享 README，没有提交或推送。

## 原始观察与红例分类

所有原始日志独立保存，不覆盖。三个原始基线尝试均发生在产品修改前，真实工具只有本机确定性的 current_datetime，不访问数据源或模型。

| 证据文件前缀 | XML 结果与耗时 | 准确含义 |
|---|---|---|
| durable-receipt-batch19-red-canonical | 1 failed，2.746 秒 | 真实 tools.execute 提交后在 JsonRpc sink 丢 result，独立 Session 证明 ToolCall completed/success；最终断言缺新规范事件，属于新增合同基线，不计原产品行为红 |
| durable-receipt-batch19-red-projection | 1 failed，3.386 秒 | 实际包与 Message 已证 result.status 被写成 failed，但夹具误取 part 顶层 status，最终 KeyError；不计合格业务红 |
| durable-receipt-batch19-red-projection-corrected | 1 failed，2.995 秒 | 根代理授权修正后的唯一补跑；同次真实成功调用丢 result，随后调用现有 chat.append_event(tool.failed)，nested result.status 实际 failed，与期待 success 冲突；是 Worker+消息投影入口层原产品行为红，不称完整 Host 复现 |
| durable-receipt-batch19-green-projection-first | 1 passed，2.458 秒 | 第一版修复后，保持相同受控丢包与失败投影输入，既有成功 Message 未被覆盖 |

## 实施边界

- ToolCall、RunStep、Message 和带唯一 receipt_key 的规范 RunEvent 在最终同一事务提交；此前 provider 自行提交的业务状态不被夸大为跨系统原子事务。
- 规范身份绑定工作区、发起人、run、root session、native agent session、call_id、invocation_seq，工具名和参数使用稳定摘要比对；execution 与 pre_dispatch_rejection 分阶段。原 ToolCall 幂等键不改、旧事件不回填。
- `tool.receipt.get` 使用独立 Session，核验当前主体与 `authorize_replay` 的当前工具/资源权限；只读，不调用 provider，不查询源数据，不重新执行工具。生成/聚合类没有来源授权证明时仍拒绝自动回放。
- 仅服务端固定、空 data/工件、明确 not_sent 的派发前拒绝可在缺工具规格时安全读取；先前已有执行事实不能降格成新的 not_sent。安全拒绝也原子写消息投影，历史不残留 running。
- Host 协商 `receipt_protocol=1` 后直接发布已存事件，不再独立 chat.event 重写完成结果。JSON-RPC reply 后附加身份提交通知；回复或通知触发竞速的只有一次 5 秒只读 lookup，tool.execute 没有新增总期限。未知/拒绝/冲突停止原生续推，整轮仍走既有交付核验，不把单工具成功当复合交付全部完成。
- 原始兼容事件缺代理/序号时只阻止覆盖，返回无业务 payload 的 ignored 事件；不能猜测并广播别的代理结果。本批对同 run 跨 agent/seq 复用同 call_id 保守拒绝，消息 part 仍按现有 call_id 合同存储，不宣称完整支持这类重名并行子代理。
- SQLite 显式窄迁移只接受显式文件 Engine/路径，不读默认 DATABASE_URL；BEGIN IMMEDIATE 后加可空列与唯一索引，验证完整既有结构和索引后登记新版本。正常 AUTO_MIGRATE=0 启动不修复结构；缺列/索引拒绝就绪。默认 MySQL、PostgreSQL 未实际迁移验证。

## 独立审查与补充修正

只读 reviewer 发现 early rejection 未同步 Message 投影、旧 call_id 可能串代理、旧无身份 lookup 对 child 比 execute 更宽，均在首轮 18 项前修正。其后发现 unknown 仅取消根 Agent 不会停止跨轮后台 job/child；已复用已有 owned-job 与子代理取消路径，并在 started 等待后、模型授权前后、工件呈现入口复核停止状态，最终收尾为 interrupted。

根代理发现 `publishReceipt` 的监听器异常可让已提交成功抛给原生 ToolRuntime，已将 session/run 两路推送各自捕获并记录诊断；成功值不变。另一发现是 chat.event 的规范回执短路在当前授权重查前泄露旧 payload，现统一只返回不含 result 的 ignored 标识，旧结果必须经 execute/lookup 鉴权才可读取。新增真实共享文件读取后删除 ResourceGrant 的对照确认：同身份 started/failed/completed 均不披露旧正文，lookup/execute 拒绝，原成功仍保存。

## 验证结果

| 独立轮次 | 原日志/XML前缀 | 实际结果 | 范围 |
|---|---|---|---|
| 第一轮 Worker/迁移 | worker-migration-first | 18 passed，44.46 秒 | 当时版本，不作为后来根代理修正后字节的同轮证据 |
| 真实 Host/Worker 首轮 | host-real-first | 1 failed，12.00 秒 | 新夹具全网络桩误拦 Windows asyncio 自唤醒 socketpair；真实工具尚未执行，属于夹具失败 |
| 真实 Host/Worker 修正单例 | host-real-corrected | 1 passed，22.60 秒 | 真 JsonRpc/Worker/SessionHost/ToolRuntime，成功 result 静默丢包1次；业务工具只执行1次；在线只读lookup1次；旧Python exit事件确认后重开同临时文件库，lookup1次；两路订阅监听器抛错仍返回success，台账/规范事件各1，pending=0 |
| 实际资源撤权新增单例 | grant-revocation-first | 1 passed，2.230 秒 | 真 read_file、真实临时文件、删除真实 ResourceGrant；不替换鉴权为假答案 |
| Host针对新增组 | host-races-first | Python 1 passed，0.379 秒；内部Node 10 passed，152.632 ms | 内存管道竞速、拒绝/未知/旧回执、同归属通知、双信号缺失边界、后台停止与两个异步窗口 |
| 最终当前字节 Worker/迁移 | worker-migration-final | 19 passed，53.45 秒 | 10项回执＋9项文件迁移，maxfail=1，外层180秒，顺序不并发 |
| 最终当前字节 Host组 | host-node-final | Python 1 passed，2.34 秒；内部Node 26 passed，1945.0118 ms | 新回执Node文件＋既有business-tool-receipt＋worker三文件，Node20秒上限，外层180秒 |

上述后六项前缀均加 `durable-receipt-batch19-`；原件保存在本目录，详细小数值以XML为准。没有将各轮用例数累加称“同轮全量”。最终19项与26项后产品/测试冻结，等待根代理相邻回归；未跑统一门禁或浏览器。

grant-revocation-first 与 host-races-first 均完成并生成通过XML后，外层仅打印摘要时遇 GBK 无法编码 Node 勾号而退出1。原始pytest/XML成功与外层输出失败分开记账，没有为掩盖外层码重跑。最终入口明确外层 `sys.stdout.reconfigure(encoding='utf-8')`、子进程 `PYTHONUTF8=1`，输出正常；显式 `PYTHONIOENCODING=utf-8` 提醒在最终命令已启动后收到，没有为同效编码设置重跑。

真实 Host/Worker 22.60秒单例后，没有改 Host/Worker/probe 路径的执行字节；随后仅将 schema.py 原有注册说明注释改中文、把另一个测试包装函数扩到3个Node文件。该单例的原有测试函数保持不变；最终源码哈希记录的是冻结时整文件，不能称整个代码树在所有轮次都相同。

## 隔离与剩余边界

受控测试只用 MAIN `.venv_py312/Scripts/python.exe tests/run_isolated_backend.py`；临时 memory/file SQLite、本机私有 stdio，不连接业务库、外部或本机模型，不打开 HTTP/8011。新 Node probe 显式合成环境，不加载部署 .env，Node 与子 Python 都禁止应用网络。Windows asyncio 自唤醒所需的内部配对复用 tracked `tests/dsh/python_guard/sitecustomize.py` 已验收守卫，并进一步限制到当前线程 `socketpair` 动态作用域；普通 connect/connect_ex 全拒绝。这是标准库内部自唤醒连接，不是业务/模型/HTTP访问。

未声称 exactly-once：最终回执提交前的外部效果仍可能 unknown；reply 与 committed 通知双丢且健康管道未关闭时没有自动探测/定时重发。该边界只做内存传输关闭清理对照，未做双丢的真实业务全链路。真实 EOF 后自动重启/重连、真实外部 HTTP/MCP 写动作、跨进程并发与多 Host 所有权、平台断电均未覆盖。冷重开只恢复有授权的已存工具事实，不自动续跑模型或直接完成整轮。真实ToolRuntime+Host注册调用没有运行原生模型对话，不称完整生成端到端验收。

结构升级操作要求：本批没有连接或迁移任何在用数据库。既有部署必须先停止旧 Worker，备份并完成适配本地平台库的显式结构迁移，再启动新版。已实现并验证的是绝对路径本机 SQLite 文件入口 `python -m app.business_worker.receipt_migration <existing-absolute-file.sqlite>`；它不读默认连接，也不运行seed。旧版本事件主键/时间/sequence/payload与旧SchemaMigration行保留，新字段NULL，重复迁移applied_at不变，重复非NULL键由实际唯一索引拒绝。默认MySQL/其他方言的既有库升级脚本与真实执行均未交付；缺列/索引应拒绝就绪，不能直接在在用默认库打开AUTO_MIGRATE当作升级。SQLite真实重复执行已覆盖，不将它说成MySQL/PostgreSQL对照通过。

最终源码/测试与全部原始log/XML的SHA见同目录 `durable-receipt-batch19-evidence.json`；最终报告SHA由根代理交付回报，不自包含。
