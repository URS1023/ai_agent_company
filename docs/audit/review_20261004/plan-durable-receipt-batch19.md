# B19 候选计划：持久工具回执与丢失回复恢复

2026-10-04，只读准备。树：`major-natural-continuation`；读取时 HEAD：`4f47d51eb2deb1de7e39dccd21310c9133e1da8e`。其他代理正在本树工作，本文件只记录所读相关文件，不宣称整树冻结。唯一写入是本计划；没有运行代码、测试、服务、数据库、模型或网络请求。

本文 ACK 指 Worker 的 JSON-RPC result 回复被 Host 接收并应用，不暗示当前协议已有独立第三次确认包。建议以现有 RunEvent 为规范回执，增加唯一身份，同事务提交 ToolCall/Step/消息结果/结果事件；Host 消费已提交事件，恢复只走只读查询，不再次调用 tool.execute。B17 的 receivedResult、防二次覆盖、started 清理及主异常保留已完成，**本计划不重新修这些分支，也不将其旧红例计入 B19**。

前置设计参考：正式 `docs/audit/review_20261004/plan-host-receipt-durability-batch17.md`、`review-batch17-receipt-migrations.md`、`fix-host-failure-cleanup-batch17.md`。本计划根据当前 B18 源码补齐成功动作再次调用、真实丢包测试与健康管道静默丢回复等边界。

## 1. 当前真实顺序与缺口

| 位置 | 当前事实 | 对下一批的约束 |
|---|---|---|
| `services/dsh_host/sessions.mjs:178–192,299–339` | Host 先 publish started，再 request tool.execute；得到对象后再 publish completed。publish 另外调用 chat.event 并等待 commit 后 emit | 工具结果事务与完成事件事务分开；得到对象不等于所有早退路径都已持久化 |
| `backend/app/business_worker/tools.py:61–105` | 幂等键由根 session identity、invocation_seq、call_id 生成；已有 completed/failed 可授权回放。终态新调用、工具不可用、prepared/unknown、撤权拒绝均有早退 | 保留旧执行键，不以新 receipt_key 未命中为执行许可；新调用拒绝与旧成功撤权要分开 |
| `tools.py:111–120,176–186` | prepared 单独 commit；正常末段 finish_tool_call/step/message parts 后 commit，再返回 | 末段是加入规范 RunEvent 的最小原子边界；helper 不得自行另开事务或 commit |
| `dispatch_state.py:62–82` | start_dispatch commit 后 provider 才可能发送 | dispatch_started 证明跨入不安全重试边界，不能证明已发送；commit 抛错后的 rollback 不证明提交未发生 |
| HTTP `collection/transport.py:405–408`；MCP `mcp_provider.py:183–188` | HTTP 先登记再 request；MCP initialize 后登记再 call_tool | 保留既有先登记后发送，不通过 receipt 恢复重发远端动作 |
| `protocol.py:73–85` | handler 返回后才 write/flush result；写 reply 失败可能随后再尝试 error 包 | error/EOF/丢包都不能推翻 handler 已提交结果 |
| `services/dsh_host/worker.mjs:7–10,40–58` | 仅短控制 RPC 有等待预算；tool.execute 无总时限 | 健康管道只丢一个 result 时不会自然进入 catch。不能把所有 ACK lost 都写成已被 EOF 恢复覆盖 |
| `chat.py:436–501` | chat.event 自己提交，tool.failed 可以按 call_id 替换此前成功消息 part；仅 native_sequence 可查重且无数据库唯一键/载荷冲突核验 | Worker 成功 commit 后 reply 丢失，可被 Host 的无回执失败事件覆盖 UI 结果；普通重发完成事件又可能重复事件 |
| `models.py:601–637` | ToolCall.idempotency_key 唯一；RunEvent 无 receipt_key，sequence 非唯一 | 执行幂等不等于回执唯一；需要窄迁移，不用 JSON 扫描或进程锁冒充持久约束 |
| `sessions.mjs:351–369,519–557`；`chat.py:684–721` | follow 跳过业务工具原生结果；recover 按 request window 决定整轮状态，只取非终态 Run，没有逐调用只读回执对账 | 冷恢复必须先尊重业务执行痕迹；工具成功不自动等于整轮完成；终态 Run 不重新 admit |
| `context.py:20–24,67–77` | tool/events 锁仅进程内；服务允许自行提交事务 | 单 Host 正常串行可实现最小修复，不能据此宣称多 Host 执行/消息投影原子 |

领域动作并不全部包含在 Worker 最终事务里。例如 `_file_ops` 在 `builtin_provider.py:1445–1509` 自行 commit，库存结果在 `analytics/inventory.py:299` 已提交 QueryHistory，presentation 服务也有独立提交。磁盘工件与远端 HTTP/MCP 更不受平台 SQL 回滚控制。故“最终回执同事务”只覆盖末段的调用结果、步骤、消息和事件，**不承诺所有业务副作用与回执跨系统原子提交**。

## 2. 哪些成功会被误判或再次执行

| 已发生事实/窗口 | 当前可能行为 | 安全结论 |
|---|---|---|
| 普通成功工具最终 commit，tool.execute 回复丢失/被错误包替代 | Host 尚未 receivedResult，可能发布 tool.failed，替换消息中的原成功 part；ToolCall 原成功仍在 | 不能把该失败当作原动作失败；应精确 lookup 原规范回执 |
| 成功已回给 Host，chat.event 完成事件提交前失败 | B17 已避免二次覆盖，但 native ToolRuntime 仍可能收到发布异常；模型未得到可靠成功结果 | 用已提交事件替代第二次完成写入；订阅者推送异常不能改变已提交业务结果 |
| HTTP POST/MCP 已保存 completed，模型因原生错误换新 call_id 再调用 | `replay_guard.py:36–40` 跳过 completed；未知副作用保护仅拦可信 unknown，新的执行键仍可派发 | 这是潜在重复窗口，不声称已有生产重发现象；修复让原调用得到真实成功，并在未确认时暂停本轮，不能以“未知保护已存在”假装覆盖 |
| 查询、文件写入、生成工件/草稿等成功后换新调用身份 | 可能重复取数、再写文件、再创建成果；具体工具也可能自行覆盖/复用，不能统一称为必然新增副本 | 恢复路径对所有工具都禁止 execute，不因为只读就重新查询；不扩成通用语义去重 |
| 同一已完成调用身份再次进入 tools.execute | 当前先查 ToolCall，重新鉴权后可返回原结果；provider 不重做 | 这是已有正确能力，不把 B19 说成首次实现同键幂等；新 lookup 不借 execute 入口回查 |
| provider 已返回但最终结果未提交，或仅 dispatch_started | 持久记录只能说明派发边界已跨入；HTTP/MCP 同任务换调用 ID 的 unknown 保护已存在 | 保持 unknown，不生成成功回执、不自动重发；需可信远端操作查询或人工核实 |
| 只有 prepared/一次 absent，原 handler 可能仍在途 | 当下没有最终回执，不代表它稍后不会发送/提交 | 不以 absent/prepared 解冻或授权重发；原执行器已停止/持有工具锁才可按现有规则收口 |
| 底层 SQL commit 已完成但客户端收到 commit 异常 | rollback 无法撤销已提交事实，旧 ORM Session 可能不能证明结果 | 新独立 Session 精确查证；查不到保持未知，不 retry provider 或整个事务 |

现有 `has_external_side_effect` 仅按服务端配置认 HTTP POST 与 MCP；本批不擅自改变其工具分类合同。客户端 Idempotency-Key 稳定传递也不等于远端承诺去重或 exactly-once。

## 3. 最小跨 Host/Worker 合同

### 3.1 规范回执身份与存储

- RunEvent 新增可空 `receipt_key`（固定 SHA256、具名唯一索引）。旧事件保留 NULL，不回填、不删除重复旧事件、不改旧 ToolCall key。
- 服务端规范编码 `version/run_id/native_agent_session_id/invocation_seq/call_id/phase` 后生成键；phase 分开 business-result 与 control-denied。可保持 started 现有去重方式，首轮不要为 unrelated progress 推广新 receipt 机制。
- native_agent_session_id 来自 Host 捕获的真实 invocation，作为私有 RPC 顶层字段，不能从模型 arguments 取值。首次登记同时保存该身份及工具/参数摘要；lookup 必须与已保存身份一致。子代理与根会话要有单独对照，不能拿根 seq 代表所有子代理。
- 当前旧 ToolCall 执行键没有 child agent session 维度；新 receipt 键补齐身份不自动修复旧执行键可能存在的歧义。首版若遇旧键映射到不同真实 invocation，必须明确冲突，不能返回另一子代理的结果或偷偷改键绕过去；执行键升级/旧数据映射需要单独迁移设计。R7 要证明不串回执，不等于承诺所有历史歧义可透明继续执行。
- 幂等键与语义摘要分开：同身份换工具/参数是冲突，同结果键不同规范载荷是冲突。先执行现有 preserve_json_integers，再稳定编码摘要；保留 null/bool/数值文本及嵌套结构，不从 Error 字符串反解析“可信结果”。
- 一个不 commit 的事务内 helper 创建 Message 投影及 `chat.stream/tool.completed` 规范行；对 failed/cancelled 保留原结构状态。与 ToolCall、RunStep、已在本事务中的 Artifact 元数据一起最终提交。短末段按固定 `tool:run → events:run` 顺序加锁并重读 Message，长 provider 阶段不持事件锁。
- 唯一冲突只在可明确定位为本次 receipt_key 约束时做回滚后新 Session 读回并比较；其他 IntegrityError/锁等待/连接异常不能视作重复成功。没有 provider 重跑分支。

### 3.2 早退、撤权和协议升级

- 新调用的 tool_not_available/run_already_settled 等提前结果，写规范拒绝回执后才能称为 durable；不伪造 dispatch_started，不复活终态 Run。
- 旧成功回放遭撤权时保留原 ToolCall/规范事件/消息成功事实，当前回复是独立 control-denied；不能用另一个 phase 的普通 tool.failed 再按相同 call_id 覆盖成功 UI part。拒绝回复不得携带撤权前原 payload。
- 私有 RPC 返回 `{result, receipt:{id,sequence,event,data,receipt_key,identity,semantic_digest}}`，仅提交后的真实行可构造 receipt。provider ToolResult 与模型业务结果不塞入信任字段；如需保持 Python 直接调用契约，在 server 适配层包装。
- Host/Worker 共同协商新能力/协议；缺能力、旧 Worker 或未迁移数据库要明确拒绝新模式，不能偷偷回退旧二次写入并称为已持久化。
- Host 收到并验证 receipt 后仅 emit 同一 event id，不再调用 chat.event 重写业务完成。live 订阅者异常记诊断并由历史游标恢复，不能把已确认成功改成 provider 失败；原业务 failed/cancelled 照原结果给原生工具层。

### 3.3 新的只读 RPC

`tool.receipt.get` 使用独立短控制执行器/新 Session，精确查询，不等待长 tool 锁，不调用 gateway.execute/provider/model/业务源，不修改 ToolCall/Message/RunEvent。建议返回 found、absent、access_denied、conflict、legacy 五种事实。

核验 run/workspace/actor/conversation、可信 invocation、工具与参数摘要，再重查当前工具/资源权限。可沿 `_saved_tool_calls` 的 Core 联表避免 runs.view 展示过滤把内部记录伪装为 absent，但必须固定归属并进行当前资源授权。终态 Run 允许授权查询，不允许因此重新 admit。

`gateway.authorize_replay:415–478` 已有只读授权路径：current_datetime、部分 skill/单文件/显式查询与版本一致的连接器可以提供证明；文档/PPT/聚合生成默认拒绝。第一版应完整实现机制但诚实保留该授权边界。若要把 PPT/Word/大屏也列为可恢复，必须另补逐工件/草稿/报告/QueryHistory 的当前授权与 SnapshotAccess 证明，不能删兜底拒绝或只检查工具名。仅有旧 ToolCall 成功却没有新规范 receipt 的记录返回 legacy，旧数据投影迁移另案处理，不现场补执行。

### 3.4 丢回复的触发条件必须写清

1. **已观察到 RPC error/EOF、Worker 退出或宿主重启：** 对准确 invocation 最多一次有界只读 lookup。原 Worker 已 closed 时不能向同一对象假装查成功；应在受控替代 Worker/既有独立恢复入口或下次启动中查询。found 返回原事件；查不到/失权/冲突/查询失败保持未知并停止本轮后续业务，不换 call_id 重试。
2. **只丢 result、管道仍健康：** 现工具 RPC 没有总超时，不能指望 catch。若 B19 要覆盖此场景，建议增加提交后的最小 `tool.receipt.committed` 通知，只携带身份/键，不带业务 payload。Host 为在途调用建立 receipt waiter；通知触发至多一次短 lookup，与普通 result 竞速，以相同 canonical receipt 完成原调用并移除其本地 pending。迟到回复/重复通知只清理或去重，不再次 publish、不再次 execute。
3. ready 通知绝不在 commit 前发，也不能作为成功证据；成功证据仍是实际查询到的持久行。通知后 lookup absent 不能 retry execute。普通长工具执行不加整轮总时限；短 receipt 查询可使用控制面独立预算。
4. ready 和 result 同时静默丢失且管道仍健康，没有可观察信号；首版只能在显式恢复/重启时查证。不能承诺任意静默多包丢失下自动有界完成。若首版不加 ready 通知，则必须把“健康管道单包丢失实时恢复”明确列为未实现，不能用 EOF 用例冒充。
5. Worker 成功后取消，仅停止后续工作，不把原成功改成 not_sent。未取得可信 receipt 时暂停本轮，不能把持久化故障投成普通参数错误让模型自动修参重做。

### 3.5 冷恢复

- 延用 B17 已修 request window/steer 归属；只在窗口内按真实 tool/call 身份查回执，不借其他运行的成功事实。
- Worker 规范回执优先于原生日志中的 TOOL_OUTCOME_UNKNOWN/TOOL_NOT_STARTED 修复占位；不解析任意 native 错误文本重建 result，不伪写原生成功掩盖实际取消。
- 已提交结果事件本来就能由 history/SSE 恢复，无须再写完成事件。native result 未 flush 时仍不能重做工具；缺乏整轮完成证据时保持 Run interrupted，同时展示已确认工具成果。
- 首版不自动接着运行崩溃轮次，也不重启模型来“确认成功”。下一轮如何把授权后的恢复摘要送入模型须有专门断言，不能靠测试全局缓存补已丢失上下文。
- 旧无 receipt_key 数据、不同键多 Host 同 Message 的并发投影、跨 Host 执行 ownership 与全局事件提交顺序不在最小承诺内；缺少单执行器静止证明时不得自行对在途 ToolCall 做终态修复。

## 4. 结构迁移与启动前置

当前 `schema.py:35` 为 `20260917_32`；create_all 不替旧 run_events 加列，checksum 不含索引，启动只核列名。新增列须升级版本并增加窄的列/唯一索引实库核验。不能仅改 models 或写一个同名普通索引就通过。

建议首版采用明确停旧 Worker → 专用迁移 → 验证 → 启动新能力，保留所有旧事件/id/sequence/payload/ToolCall key。SQLite 文件库使用可空列及单列唯一索引；索引允许多条旧 NULL，新非 NULL 重复必须失败。已加列但缺索引可按明确中断状态续建；列型/可空性/同名索引定义错误拒绝就绪，不静默 DROP 重建。版本完成记录在结构核验后写，AUTO_MIGRATE=0 不自动代替显式迁移。

SQLite 当前只有进程内 RLock，不能声称两个迁移进程受保护。首版可以要求部署所有权保证只有一个迁移进程；若承诺并发启动，则须在同一连接实际写锁内重新 inspect/迁移/验证并限定锁预算。不得顺手改 WAL、同步级别或全局数据库配置。MySQL GET_LOCK/DDL 隐式提交另有语义；未做获授权本地 MySQL 实测前，只能报告 SQLite 迁移已验证，不能宣称所有方言升级通过。

## 5. 可区分旧/新行为的受控测试矩阵（本轮未执行）

所有“提交后”用例必须实际 commit，再从新 Session/新进程读取；不能把 commit 改成空函数、把 provider 桩回执当成持久事实，或以同进程缓存代替冷恢复。

| 编号 | 最小夹具与故障点 | 当前基线预期/应保留的旧行为 | 新实现必须断言 |
|---|---|---|---|
| R1 | 真实 Worker tool_not_available 早退，started 已存，Host 不补完成事件 | 没有规范结果事件，预期新合同红 | 失败回执已持久；provider=0；不伪造派发、不复活 Run |
| R2 | current_datetime/内存计数 provider 真提交后，JsonRpc sink 丢一次 result 并使 Host 感知传输失败 | 现无 lookup；Host 可投成 failed | 新独立 Session lookup 原 result/event id；provider=1、canonical event=1、消息成功不被覆盖 |
| R3 | 真提交后只丢 result，管道保持可处理控制消息；不丢 committed 通知 | 旧调用仍 pending，外层测试预算到达即失败 | ready 触发一次 lookup 完成同一调用；迟到 result 不重复事件；不增加业务工具总超时 |
| R4 | provider 返回后、末段 commit 前 BaseException；另例 commit 真完成后抛异常 | 前者已有 unknown 保护应继续；后者不能靠 rollback 判断失败 | 前者 absent/unknown 不重发；后者新 Session found；两者不可给相同“未发送”结论 |
| R5 | HTTP POST/MCP 合成外部动作计数1，完成提交后丢回复；受控模型在得到失败时会换 call_id 重试 | 当前 completed 不被 unknown guard 拦，存在动作计数2的逻辑风险 | 原调用恢复成功，模型不再要求重做；动作=1；lookup 入口永不派发。模型仅内存桩或本轮回环桩 |
| R6 | 提交前只有 prepared/dispatch_started，handler 被受控屏障阻塞；独立 lookup 看不到结果 | 现派发保护边界有效，不能放宽 | lookup 不解锁、不重发、不假成功；原 handler 停止前不自动 reconcile |
| R7 | 同身份改工具/参数、同键不同载荷、两个子代理同 seq/call_id、跨租户/用户/会话 | 现 ToolCall 参数冲突应保留 | 精确冲突/拒绝；子代理不串回执；同一规范载荷回放相同 id且零写入 |
| R8 | 原成功后撤销成员/工具/源/文件权限，及缺 runs.view 但业务资源授权仍有效 | 当前 authorize_replay 保守拒绝生成工具 | 不泄露旧 payload；不覆盖旧成功 part/ToolCall；展示过滤不制造假 absent；未证明的PPT回执保持拒绝 |
| R9 | 成功 receipt 已提交，Host emit 监听器抛错；另例收到 receipt 后取消 | 当前已有 receivedResult 清理通过，不重做旧断言 | canonical event只1、业务结果不改；history能取回原id；取消只影响后续，不能激发第二次执行 |
| R10 | 临时文件 SQLite +真实 Worker进程 commit后退出，关闭连接，再启动第二Worker/Host；native result未flush或为修复占位 | 现冷恢复没有逐调用 canonical receipt 对账 | 原event/result/hash可见，provider=1，无重入队/模型补事实；整轮证据不全仍interrupted |
| R11 | native日志已有后续Run/steer/子代理，恢复旧Run；另例旧Run已terminal | request window和terminal不复活是已正确边界 | 不取后轮回执、不重复完成事件，已terminal只读history不admit |
| R12 | 两个独立Session同receipt插入；真实唯一约束；同键相同/不同载荷分别对照 | 当前无receipt唯一约束 | 仅1规范行；同载荷原id，不同载荷冲突；不自动retry整个业务事务 |
| M1 | 真实旧SQLite文件结构、旧版本/事件/ToolCall；独立Engine迁移后重开 | 当前create_all不能完成既有表加列 | 旧行hash/id/key不变、旧NULL保留、新唯一键生效、二次迁移无新增变化 |
| M2 | 半迁移、错误索引、缺索引但已有版本、AUTO_MIGRATE=0、旧新协议混用 | 旧检查不验证索引；现protocol_version=2 | 拒绝不一致启动；明确版本/能力握手；不回退旧二次写入 |
| B1 | 两个完成信号均静默丢失；无EOF；随后显式恢复/重启 | 缺乏可观察自动触发 | 不伪称自动有界恢复；显式查证仍provider=1；作为明确限制对照 |

现有测试不能替代这些新红例：`test_business_worker_dispatch_recovery.py:178` 的 lost_rpc 只是连续两次 invoke；`:186` cold recovery 是同进程授权失败桩；两者使用 conftest 内存 SQLite。`native-host.test.mjs:310–336` 确有宿主重启/强杀，但业务 Worker 为 fake-worker；`real_platform_smoke.py:439–454` 的真实重启证明历史与交付记录恢复，未在工具最终 commit 与 result 包之间注入丢包。`test_business_catalog_cards.py:110–123` 只补缺失整表，不证明既有 run_events 加列/唯一索引迁移。

实施时先按 R1/R2/R3 三个有界原始反例验证真实缺口，再做事务/协议/只读恢复与迁移；其余作为相邻保护矩阵。每组合初始最多一次，先失败停；原本已正确的 unknown、撤权、取消等是保持绿的保护项，不捏造成新红。建议第一批每 Node 子进程 25秒、receipt控制查询5秒、合成动作至多1次（仅R5旧行为反例允许最多2次受控内存动作），不重试/扩预算；具体总组预算由根代理在实施时统一批准。

平台存储仅指定临时 SQLite 文件，必须断言绝对路径属于本次目录并关闭后重开；模型/HTTP/MCP只用进程内桩或受限本轮回环，不读 .env、不连 szh_new/业务源/8011/8012。这是下一批测试设计，不是本轮执行授权或验收结果。

## 6. 建议所有权与不扩展项

| 责任 | 最小候选文件 |
|---|---|
| 数据结构与显式迁移 | models.py、schema.py、一个窄迁移入口/模块、文件SQLite专项 |
| Worker规范回执与查询 | business_worker/tools.py、chat.py内事务helper、server.py、独立receipt模块；protocol/transport仅为提交后通知及返回适配所需 |
| Host消费与恢复 | sessions.mjs、worker.mjs短控制方法/在途回执关联、必要host恢复入口；真实ToolRuntime相邻专项 |
| 受控故障与冷启动 | 新专项文件/私有stdio故障夹具，旧dispatch/recovery测试保留真实边界 |

不修改provider实际动作重试策略，不改变业务源权限/通用SQL白名单，不做历史payload猜测回填，不复制上游实现，不通过浏览器重试掩盖ACK窗口。跨Host全局ownership、原生日志格式重写、所有生成工具的资源回放授权和任意多包静默丢失恢复应分别划界，不能随唯一索引一起声称完成。

本轮唯一产物为此计划。已有 reviewer 独立核查 HTTP/MCP 最危险发送边界及测试覆盖，结论一致；其未运行代码或修改文件。


## 所读相关快照指纹

大文件按本文相关函数/测试区段阅读，哈希覆盖全文件，不等于通读或执行验证。以下哈希在计划完成时只读计算；其他代理可继续修改其所有权文件。

| 文件 | SHA256 |
|---|---|
| `backend/app/business_worker/tools.py` | `e8dfc75a07cbaa152755bc0f5627486fe1ed1c6792353cfa84083f6e94e3cefb` |
| `backend/app/business_worker/chat.py` | `b9e65d49313212763a4f8fa92fee4cac8116976ad347482b0a9aa19a26e36004` |
| `backend/app/business_worker/dispatch_state.py` | `f2aff32d0f1d1f3ef72acd57bb59f17f8700000d32dcb45f10610c1bbb3f959e` |
| `backend/app/business_worker/replay_guard.py` | `f0c26592c9f05f09323df090d94184a90a6f40f94aa5bc24d9d48646263f0e8c` |
| `backend/app/business_worker/protocol.py` | `69f4d3616dbd09aeb6bf273a023f84af832aaba0bdadd558848d2c7b22958793` |
| `backend/app/business_worker/server.py` | `f2d389753c4da2534b078a59cb2fc239661316b9dc920034330356e50be4022a` |
| `backend/app/providers/tool/gateway.py` | `5ad873f400107f1885b4f69e072966c9a69fa84fe5439bee86f8b9c5964fb8dc` |
| `backend/app/models.py` | `dce44685054d4efc2713b88362cb553f63c9e34ad9efb4efab16222f74620340` |
| `backend/app/schema.py` | `fd6af3494fe7362470e6cdb5d265bf868a484ba5ba21c4379d3df5944e31e7fa` |
| `services/dsh_host/sessions.mjs` | `0c643f126310aa298ce4fb96814c11d9624f68bb28089e7e633cc3071ad5f3cd` |
| `services/dsh_host/worker.mjs` | `35830bc723575836eae151fb54153d8b7f7a785d7042d89436f4591a85d87201` |
| `tests/backend/test_business_worker_dispatch_recovery.py` | `d1851b6a10e7ae04d54550f71cbc961055811529c7e463774a7a73503310a922` |
| `services/dsh_host/tests/recovery-request-window.test.mjs` | `6f687c918798a30b81b039b992ff1b28e25838dd4d202a098a75ac694937cd9a` |
| `services/dsh_host/tests/lifecycle-recovery.test.mjs` | `1984534a29b2330300dfa393a02d12d7809febc16ae10337172ea6e0c71aa38a` |
| `services/dsh_host/tests/native-host.test.mjs` | `09a01e8debd5d115f4e7b1c514e2e2f1b9819483ac4c1c5da51934e16063c047` |
| `tests/dsh/real_platform_smoke.py` | `22b02809d505fa3301e768587a72c411395f2a6afc3059f913135825f3c0b318` |
| `docs/audit/review_20261004/plan-host-receipt-durability-batch17.md` | `2951772d8c51e77e87732a05039989a6a3f8c7ef34fb6e42bb92e958054fe1f4` |
| `docs/audit/review_20261004/review-batch17-receipt-migrations.md` | `3b236c105a36aff0a3e2e3c71258b494324ce0fd138a060a84f5c49506f8af58` |
| `docs/audit/review_20261004/fix-host-failure-cleanup-batch17.md` | `aaf19d0a45dd830509bd103b878aa9aaed3502c242e480063a474699981eeadf` |
