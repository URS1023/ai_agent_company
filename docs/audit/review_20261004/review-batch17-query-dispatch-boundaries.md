# 批次十七设计：停止取数后保留已有结果交付，及查询派发边界

2026-10-04，只读完成。工作树 `major-delivery-acceptance`；本报告不属于 B16 已完成修复，也不增加任何测试通过数。期间按根代理要求暂停处理 HTTP 导出实测阻断，随后恢复本报告；本报告阶段未改产品或测试、未运行测试/浏览器/数据库/模型/网络。B16 33 文件继续冻结。

## 结论与实施顺序

1. **B17-Q01：正常 DSH 顺序执行即可触发“停止新取数，同时错误阻断旧结果交付”**。`DATA_TOOLS` 将纯平台历史读取和静态大屏创建也算为业务源访问。已有 Q 查询成功，另一查询发生明确数据源异常并提交 halt 后，复用 Q 创建大屏仍会在到达真正业务服务前被拒绝。此结论由确定分支直接推出，尚未在本报告执行动态红例；它比多宿主并发窗口优先。
2. **B17-Q02：halt 检查与实际 SQL 派发之间确实有非原子窗口，但默认单宿主、同会话正常队列不直接产生所需的两个并发 Run**。私有 RPC 绕队列或多宿主共同处理同一平台/会话是额外前提，不能将其描述成“普通点击继续必然绕过停止”。下一批先保留有界反例设计，不先建设跨进程锁或追杀 SQL。
3. **DATA_TOOLS 的已注册业务内置入口未发现确定的间接查询漏项**。工作流插件注册的是读取已保存报告，不是运行工作流。真实运行及质检提交是独立鉴权 HTTP API，默认没有所属聊天 task。未知 HTTP/MCP 不因名称像“查询”就推定有业务库访问；未连接在用平台读取动态连接器清单，因此不宣称覆盖实际部署中的所有自定义工具。

## B17-Q01：停止标记过度阻断既有交付

### 确定调用链

`business_worker/tools.py:25–31` 的 `accesses_datasource` 对 `provider=database` 或 DATA_TOOLS 中名称返回真。在 `tools.execute:139–144`，同 Run 快照或 `task_datasource_halted` 为真时直接返回 `database_access_halted`，不会进入 gateway。

下面五个工具在真实 DSH Worker 路径中无需发起新业务查询，却被上述提前分支拦住：

| 工具 | 实际 DSH 链路 | 保留的鉴权/来源检查 | 新业务源 I/O |
| --- | --- | --- | --- |
| create_dashboard_from_queries | builtin_provider:947 → query_dashboard.create_dashboard_from_queries:259–339 | 本轮/明确续作成功回执、当前主体、analytics.view、dashboards.create、来源范围/版本；save_report:224 仍执行 SnapshotAccess | 无；读取 QueryHistory、可选平台模板，生成静态内容，refreshSeconds=0，保存平台报表 |
| create_business_dashboard | Worker:102 固定 execution_owner=dsh → builtin_provider:873–889 必走 query_ids 分支，缺 Q 则明确拒绝 | 调用同一个 create_dashboard_from_queries | DSH 无；不能将非 DSH 同名入口一并视为无新取数 |
| get_business_dashboard | builtin_provider:975–979 → dashboard_actions.execute:317–333 → 已保存 Report | Run 归属、dashboards.view、get_report/SnapshotAccess、当前来源查看权 | 工具调用本身无；返回预览链接不等于之后用户打开页面也不会刷新 |
| update_business_dashboard | dashboard_actions:334–350，apply_theme 后 save_report 新修订 | 当前报表修改权、来源查看权、同轮回执、SnapshotAccess | 本次工具无；保留旧 refreshSeconds，不能声称用户后来打开报表永远不触发独立刷新 |
| view_device_alerts | builtin_provider:796–802 → business_ai.service.alert_overview:356–449；可选原页 Excel 导出 | alerts.view、报告/问题/日志的当前数据范围 | 无；读平台持久报告与处置记录，不请求设备、不重跑流程 |

`query_dashboard` 的普通布局仅复制 Q 的行列；手选模板的 `template_query_snapshot._field_info:40–79` 只读平台 SourceMetadata 和已保存 SQL 做本地解析，不调用元数据刷新。`save_report` 校验内容与来源后写平台记录，没有执行组件查询。

反例顺序不需要并发或第二宿主：同 Run 内 Q 成功 → B 失败且 classification 写入 `database_access_halted=True` → 模型转做 Q 的静态大屏 → DATA_TOOLS 提前拒绝。同 task 后续明确“继续”亦如此。反之 `export_query_spreadsheet`、`render_chart_image`、`render_dashboard_image`、`export_query_presentation` 没有被 DATA_TOOLS 拦截，具备相应真实 Q/报告时本应继续接受它们各自的当前权限校验。

这不表示 B 的失败可忽略。复用 A 的 Q 只交付 A 的真实范围；B 的取数仍停止，最终结果仍必须保留未完成/缺失事实，不得把大屏或 Excel 存在当作全部业务范围已成功。

### 与其他后端调用的差异

- 该拦截在 `business_worker.tools.execute`，不在通用 `ToolGateway.execute`。它的调用上下文由 Worker 固定为 DSH，不应把“DSH 专用分类修改”复制成所有后端统一放行。
- 非 DSH `create_business_dashboard` 没有 query_ids 时可以进入 `business_ai.service.create_dashboard_draft:592–610`，进而 `reports.generation.generate`；generation:1578 会调用 execute_query。这个同名后端分支仍可能取数，不能从本报告推出它是纯历史工具。
- API 独立报表刷新、分析页面查询、工作流新运行和定时任务，不属于已有聊天 task 的结果交付；仍保留各自权限、预算、版本、只读或明确写回确认合同。本批不把聊天 task halt 扩展为工作区/数据源全局停机。

### 最小实施所有权

首选下一独立树只改 `business_worker/tools.py` 的 DSH 新取数分类及中文说明，加 `tests/backend/test_dsh_halted_task_saved_deliveries.py`。保留 query_business_data（含 schema/profile/overview/HTTP）、query_inventory_data、collect_device_monitoring_snapshot 和所有 database provider 的停止限制。五个已证实的历史交付入口要经过原 gateway、loader、SnapshotAccess，不允许增加绕过授权的特殊直接返回。

现有 `tests/backend/test_dsh_task_datasource_halt.py:40–47` 用假 gateway 把 view_device_alerts 与 database.query 一起断言拒绝。这是需要修正的旧测试合同；不能只删断言，应换成真实平台报告读取能继续、真正数据库查询仍拒绝的正反对照。该文件使用的 mock 执行器没有证明预警工具实际会访问业务源。

### 最小真实 Worker 红例设计（本报告未执行）

复用 `tests/backend/test_business_worker.py:26–48` 的 worker_context/prepare，它只替换模型配置，不替换网关、身份或持久化。使用正式 API 建立两份专属临时 SQLite 源 A/B 或来源固定的假 driver；不得使用业务库。A 的成功数据经真实 `tools.execute(query_business_data)` 落 QueryHistory 与 ToolCall，不能只拼一个假 Q 响应。B 在驱动边界注入 `connection_failure(... TimeoutError(...))`，不发真实慢查询。最大 2 次有界取数，模型 0 次，后续源访问计数必须为 0。

1. `prepare` 明确要求查询 A/B 并制作可核验的大屏及 Excel，选择 A/B；同 Run 调 A 的 SQL 工具，取得真实 Q。
2. 同 Run 调 B，核对最终 ToolCall 的 failure_domain/recovery=stop_datasource 与 Run 持久 halt 已提交；保存 B 的失败回执作为证据。
3. 通过真实 `tools.execute` 分别调用 create_dashboard_from_queries 和 DSH create_business_dashboard（Q 参数显式提供）；**当前预期红点**是 database_access_halted。修后应真正保存 Report，验证 lineage/queryIds=Q、所有显示行等于原 Q、refreshSeconds=0、源访问计数没有增加，而不是只看 status=success。
4. 调真实 export_query_spreadsheet、render_chart_image 作为既有独立交付对照，读取实际工件/工作簿和查询来源；禁止 stub 掉 loader/exporter/persist_artifacts。必要时先只做 Excel，避免无关渲染扩大首轮预算。
5. 相同 task 再发“继续”，确认 task_id 不变、halt 仍真；只通过 validated_resume_calls 接受 Q，重放/再交付不查询 B。用另一会话或另一用户的 Q 做归属负例。
6. halt 之后再调 A 或 B 的新 SQL、database.query、schema/profile 仍拒绝。A 过去成功不构成恢复新访问许可。明确新 task 的旧行为对照保留，不通过换 call_id 或新 Run 解除旧 task halt。
7. **Q 撤权负例**：Q 生成后，通过正式权限/字段编辑撤销 A 的访问或缩列，再尝试大屏/Excel。应在当前 loader/来源版本/SnapshotAccess 拒绝，不能返回旧行、创建带旧数据的新报表或落新工件；源访问计数仍为 0，原 QueryHistory/ToolCall 不改写。
8. 为证明没有靠 loader 的版本比较“碰巧通过覆盖”，保留/增加受限源的旧星号快照额外列反例，并对真实 save_report 调用链确认 SnapshotAccess 被执行；不能直接 mock save_report 或给所有静态内容加可信例外。现有批十三快照撤权测试应作为相邻回归。

首轮用 `-x` 首失败停止并保存 XML/log；修后再跑上述小组与已有 task halt 组，不在定位期间开 100 轮或全浏览器。实际场景无需模型/外部数据库。该红例设计不宣称已执行。

## B17-Q02：同 task 并发 halt 窗口的实际可达性

### 默认单宿主正常用户路径

1. HTTP `/api/chat/conversations/:id/send` 在 host.mjs:125–143 先 Worker chat.prepare，再 sessions.admit。`session_identity` 固定工作区/用户/会话三元组（chat.py:82–83）。
2. `SessionHost.pump:224–239` 同步设置 current.active，再启动异步执行；只有 active 为空才启动该会话下一 Run。宿主允许多个会话并行不等于同会话并行。
3. `nativeIdle:382–395` 等主 Agent、同 Run 子 Agent 和已归属后台 Job；`execute:397–423` 在 finally 等 settle，外层 finally 才释放 active。steer:195–210 沿用原 Run。
4. 业务工具 `isConcurrencySafe=false`，执行前核对 current.active===run；真正 tool.execute RPC 没有总超时，普通 abort 只发 run.cancel，仍等待 Worker 回执（sessions:299–329；worker.mjs:39–59）。Worker 又持有 `lock_for('tool:'+run_id)` 到最终 commit（tools.py:70–194）。子 Agent 使用同一个 Run，因此其业务调用也被 Worker 锁串行。
5. `chat.settle:503–506` 还按同 Run 工具锁收口。当前 task halt 已提交时，排队的同 task Run 会重新读取该持久事实；B16已有顺序负例覆盖。这些约束阻止默认路径产生“A/B 两个同 task Run 都越过 halt 检查，随后 B 失败但 A 继续”的必要重叠。

结论：不把该窗口记为默认单宿主聊天的已确认并发缺陷。宿主崩溃、协议断连、持久化故障各有自己的恢复路径；不能用源码正常路径推导它们全部安全，也不能未复现就假定发生同 task 并发。

### 窗口仍存在的位置

`tools.execute:139` 查同 task halt → gateway 校验/解密 → `gateway.py:410–412` 的 `_before_tool_dispatch` → `dispatch_state.start_dispatch:43–65` 检查取消、Run 终态并提交派发日志 → provider/业务查询准备 → `QueryAuthority.check` → execution.py:205/214/253 在连接与 SQL/ES 派发前再检查授权 → 发出查询。

`start_dispatch` 没有再次查询 task halt，QueryAuthority 的职责是当前用户/客户端/源/工具版本及列范围，也不查 task halt。若另一个可信执行者能在此期间为同 task 提交 halt，后续 query authority 不会因它拒绝。但 SQL 派发前再做一遍 halt 读取也只能缩小窗口，不能宣称平台状态与业务服务器 SQL 发送原子，更不能宣称关闭客户端即服务端 SQL 已停。

### 额外先决条件

| 路径 | 必须具备的条件 | 本报告结论 |
| --- | --- | --- |
| 直接 Worker RPC | 获得可信父子 stdio 调用入口，绕过 SessionHost.admit/pump，以两条同归属 Run 调 tool.execute | protocol.py:23–29、112–116 确有多线程 tool executor；锁仅按 run_id，故机制上允许；不是公开 HTTP 入口，也未动态复现 |
| 多宿主共平台 | 同平台 DB、同用户/会话/task，同一时刻各自存活且各自接受/恢复执行；有可用的各自 DSH home/监听端口或绕过默认启动入口 | host队列、Worker锁均为进程内；未见聊天执行全局 ownership lease。需专属临时平台双实例红例，不能据此声称现部署已经多宿主 |
| 默认重复点启动 | PowerShell 脚本先查监听端口和本项目健康，已有正常进程时直接返回（start_server.ps1:127–145） | 通常不会起第二实例；不是全局原子锁，也不等于任意直接启动方式都被保护 |

补充：`DSH_HOME` 与 PORT 可配置（launch.mjs:11、run.py:28–35）；`host.mjs:235–241` 在 publicCarrier 绑定端口前已经调用 sessions.recover。`chat.recover:684–715` 扫描平台非终态 DSH Run，未按当前宿主所有权筛选；sessions.recover:507–548 对无原生历史且无执行痕迹的 Run 可 admit，有执行痕迹则保守 interrupted。因此“同端口最终会 EADDRINUSE”本身不能作为恢复阶段已有全局独占的证明。这是部署/恢复前提的静态发现，尚未运行双宿主验证。

较低优先级反例：临时平台建立两个同 task、同工作区/用户/会话 Run；通过两个受控私有 RPC/线程，在 A 已通过 Worker halt 查询但尚未实际 SQL 派发处设置屏障；B 产生真实平台 halt 并提交后才释放 A。记录假 driver 派发次数与时间顺序。另做单 SessionHost 队列的正对照，证明普通 send 下 B 不会与 A 重叠。若下一批实施，应独立引入“可信 DSH task 停止复核”，借用已有连接前/派发前钩子但不把它伪装成来源权限检查；平台读取使用新短事务，保留取消身份、来源鉴权、一次治理计量和零自动重试。是否需要跨宿主 ownership 是另外的部署决定。

## DATA_TOOLS 与工作流/业务检查核对

| 入口 | 实际读取或执行 | 当前停止覆盖/判断 |
| --- | --- | --- |
| query_business_data | SQL/DSH execute_query；schema 可能刷新元数据；profile 诊断采样；overview 多次有界查询；HTTP collect | 已在 DATA_TOOLS；mode不能一概当纯缓存。overview:196–201 遇异常重抛，不在同一次工具里吞掉查询失败继续采样 |
| query_inventory_data | inventory.query_inventory → execute_query | 已覆盖 |
| collect_device_monitoring_snapshot | monitoring.collect_snapshot:238–267 多个有界 execute_query；异常立即退出，截断也拒绝 | 已覆盖，无循环内吞错后继续查询分支 |
| 动态 database.* | provider=database；管理台只允许 list_tables/describe_table/query | 按 provider 全覆盖，不依赖名称白名单；表目录/结构也属于可能接触源的操作 |
| list_business_data_sources | 平台目录/SourceMetadata；service:301、317 cached_only=True，include_samples=False | 不算漏项；语义缓存升级可写平台派生元数据，但不会访问业务库/模型采样 |
| query_workflow_results | registry 的 workflows 插件只注册此工具；builtin:971–974 → business_results.tool_result/list_reports → 已保存 WorkflowExecution 报告 | 不执行工作流；authorize_graph 使用 active=False/execute=False；read-values 不走实时表结构，output-table 不连库核验，qc检查也仅平台引用。不能因中文名“业务检查”推断它在重新检测 |
| export_inspection_report | inspection_export:125–149 调同一 list_reports，将已保存逐项证据渲染成文档/图表；沙箱 allow_network=False | 未列 DATA_TOOLS 合理；不是新检测/新采样 |
| 工作流 POST /workflows/{id}/runs；质检 POST /quality/workflows/{id}/runs | router → execution.start / quality.runs.submit；预检权限及确认后确实执行 DB/HTTP/目标写回节点 | 独立 API，不是当前注册的聊天内置工具。execution.start:159 创建自己的 Run，快照 entry=workflow、source_ids，没有继承聊天 task；用户明确发起流程是另一执行范围，不能默认全局禁止 |
| 工作流 tool 节点 | nodes.py:787–842 直接走 gateway；database-query 节点:1315 走 execute_query(permission=workflows.execute) | 确实不经过 business_worker.tools 的 DSH task halt，但同样没有当前聊天 task 绑定。若以后增加“聊天执行工作流”工具，必须显式传可信归属并另设计停止继承，不能只加名字就声称子节点已受保护 |

HTTP/MCP：现有代码允许发布固定 HTTP 目标（私网授权需 system.admin；执行还需已发布工具及当前调用者权限）。若管理员特意把目标配置成本系统某个运行工作流的 API，并配置该目标 API 接受的身份，或 MCP 服务内部会查询业务源，则可形成条件性的间接访问；没有读取运行中的连接器配置，也未证实仓库默认注册了这种工具。本批不扫描凭据，不把所有 HTTP/MCP 默认定义为数据库工具，不扩大到未知语义治理。

原生 `workflow` 工具是 DSH 的原生 Agent 编排工具，不能仅凭同名等同 `/api/v1/workflows` 业务执行；需要实际业务桥接证据。原生 bash/pwsh 也不是已注册业务 SQL 接口，不凭“能运行命令”推定持有业务 API 身份或已绕过源停止。

## 阅读记录与指纹

以下仅记录真正通读或片段阅读的文件。SHA 是本次报告完成时的文件快照；行范围明确为片段时，不将其余内容算作逐行审查。注册清单读取 plugins/tools/skills/routers 结构，不把扫描到的全部文件路径当作已审阅。

| 相对独立树路径 | 实际阅读范围 | SHA256 |
| --- | --- | --- |
| services/dsh_host/sessions.mjs | 片段：64–140、157–239、294–340、373–566 | DF2D80BCDE9873BCB03DDAFB63F80B5CC934B3A6453100506182718D169328CA |
| services/dsh_host/host.mjs | 片段：114–177、224–248 | E4372DE59C56C143C54E5266AE1601C1FA8B7037CE0A9B1765F85AC28F4DD50B |
| services/dsh_host/worker.mjs | 通读 | 35830BC723575836EAE151FB54153D8B7F7A785D7042D89436F4591A85D87201 |
| services/dsh_host/launch.mjs | 通读 | 5C9E6D86AF462FED4CE6A069257DF1F4B2F8CEFDED073293178B907741B33B7F |
| services/dsh_host/connection.mjs | 通读 | 4F5945E75E1D6AEC4D465A8D196F88E4BEE56829446E1A97DD9179FA1389EFA2 |
| backend/run.py | 片段：28–66 | B1369597B35CEA369683B30FA90C79B679D464A8437862FCBF6AE167F6D860EF |
| backend/start_server.ps1 | 片段：79–169 | 7411F8BCAB6078FAC629B717067191350748D0F846A86A7908E09295BE41DA97 |
| backend/app/business_worker/tools.py | 通读 | B17A0959F9FA1C815976653CFA3D040522F5427900C3DF58B13DEB9391C518FA |
| backend/app/business_worker/dispatch_state.py | B16通读；本次复核43–133 | FED7F1636FE9F654145F67E91873D95884D26050746313CFAD35868D9026C1AE |
| backend/app/business_worker/context.py | 通读 | 1036FEF26440AD1280680F3C80468BD4AA971AB97B991D65EF2F8676033FA8E1 |
| backend/app/business_worker/protocol.py | 通读 | 69F4D3616DBD09AEB6BF273A023F84AF832AABA0BDADD558848D2C7B22958793 |
| backend/app/business_worker/server.py | 通读 | F2D389753C4DA2534B078A59CB2FC239661316B9DC920034330356E50BE4022A |
| backend/app/business_worker/registry.py | 通读 | 75A101CCE35BC177A719A76E246F4950737A222085455DDE2F367F547F029634 |
| backend/app/business_worker/chat.py | 片段：82–83、207–384、503–506、684–757 | B9E65D49313212763A4F8FA92FEE4CAC8116976AD347482B0A9AA19A26E36004 |
| backend/app/business_worker/tasks.py | 片段：112–176 | AD5FBAF2DBBBB4ABDCBB9E7C25116E17B1E97EDE9C239E2EEBF7EF609C591B07 |
| configurations/business_apps.json | 结构读取：plugins/tools/skills/routers；未逐行通读所有描述 | DE76BD9D439F56B02E24D1DACB1A75419D1DFFB67B285D53CC87EB4A4D19C289 |
| backend/app/providers/tool/builtin_provider.py | 片段：170–359、500–614、628–1007、1514–1552 | DFC6F95CBB1A710306231E0A1A43AB5511D111C0443B5801E9D9C2989646CBF5 |
| backend/app/providers/tool/gateway.py | 片段：1–65、378–470、480–547 | 25F71461AE224895AE1DC28CF028CF2DF78EF707E38D36D236C2490B7433EE8A |
| backend/app/providers/tool/database_provider.py | 片段：118–150；B16实现及审查时已通读 | 3F497C290902F2DFCFFD8D2023CE8B9D47ED6D39E2B4CA0E7FBCF9DE104C949D |
| backend/app/providers/tool/http_provider.py | 片段：106–148 | 65D44D3AEB7CBE32587F12E01DC0DD441D35EE094AE42A1A87765070FC412852 |
| backend/app/modules/analytics/query_authority.py | 通读 | 433D32C95C6F0A3F0172A1B9459101BE83E51048CA611F642F24082992BA8A4F |
| backend/app/modules/analytics/execution.py | 片段：163–270；B16实现及审查时已通读 | 19C6DC358895337D9D4219A86DCE6A2F3AFD3DE8A0BE986AFD603B1510363706 |
| backend/app/modules/analytics/business_overview.py | 片段：150–214 | E54D0FDBF5E9D735C8C968D7F55A6C75E39B6163FA110C41878067646249BAA0 |
| backend/app/modules/alerts/monitoring.py | 片段：169–273 | 48F786F49BAFE181E00FA19BEE63EF8721089AB7ABA47BBF46AC524618C943C6 |
| backend/app/modules/business_ai/service.py | 片段：285–449、590–615 | 3DB90F16D7FC569AF60990659E47885D76B1942A5786254704E0F6084D36C316 |
| backend/app/modules/business_ai/dashboard_actions.py | 片段：292–350 | 1104E8D03E140642FF2969FB4B062AAF594137AA7C44564BC9E4644E6695A745 |
| backend/app/modules/chat_artifacts/query_dashboard.py | 片段：1–35、60–152、259–339 | E3CC7561ACF343FD3010F98791C657E03BAA2838F1F90B9A581E0D5070BE6257 |
| backend/app/modules/chat_artifacts/template_query_snapshot.py | 片段：1–109；执行相关标识定点检索 | 4D4A10BB4C7087B9C1C1699EBBBC64148BDF3EA9C1CEA7B4C46C3146EF6F54FD |
| backend/app/modules/chat_artifacts/chart_renderer.py | 片段：1–145 | 276B9F33DF2DB047BE00724F933AE7471E11CDBE39DF9E84FAAAF01C722590E3 |
| backend/app/modules/chat_artifacts/query_spreadsheet.py | 通读 | F72FFA1E50A36E7443AE4A54C3E756E51ACCFF679FBD67E53FC0BA75CF266EFF |
| backend/app/modules/chat_artifacts/dashboard_image.py | 片段：1–130 | E3A42ADF7727DB6F6C2909CB92284CE312684D1E06BC457D34E562280593D40E |
| backend/app/modules/reports/service.py | 片段：110–255；此前B16审查已核对get_report | 68F508E1E17B7FA2FE01F10C07209D286EF889134CDFE3EFCAEDF52828F42A9C |
| backend/app/modules/reports/generation.py | 定点读取：1578附近实际execute_query调用 | CD367CF6FFB11E94623167FFF95832EBD9E05BA63CE4099F2C0B0A803876E0E9 |
| backend/app/modules/workflows/business_results.py | 片段：1–65、122–222、237–338 | F9DFBF44591A841873B7621635D67EC30D073984BB3497E19681506B27C48B2D |
| backend/app/modules/workflows/graph.py | 片段：316–466 | 400577B63A9AA394452DCA9072B148A4BB40E69156AB6FFFBD24134615FE28C9 |
| backend/app/modules/workflows/execution.py | 片段：98–190 | 1547B24FC076AECB92DCDA4D48231F0503557DCFBDD8C4502EBF28E77F514F84 |
| backend/app/modules/workflows/nodes.py | 片段：125–265、780–845、1300–1327 | EB745D9349FE3512FE18F09D47548E5881D17FB61678C4526D0220EB0ABB8FC8 |
| backend/app/modules/workflows/router.py | 片段：171–236 | 63AE934018B25AEC15D69CB8D1F0D994F42D7A87D33D86D0DEE58AD5772DB6BF |
| backend/app/modules/quality/router.py | 片段：136–147 | 0A9D2C8BB988260A5EAABD3DF95340A6DEB14E509142B7C76F65436CCE913977 |
| backend/app/modules/quality/runs.py | 通读 | 830A5184DB4D5A6AA9E3317F295ED77A1C29699741A0B83F8D6D5169007400B2 |
| backend/app/modules/quality/detection.py | 片段：37–91 | 75041D142D3568122262A96B62D09C63667CE43E58A5E5351804EB01D79CA4CB |
| backend/app/modules/writeback/targets.py | 片段：154–198 | 42BD4CC87BB10FA8D1022546E23E9B8BF20FA8C664E61839DB1C9D914995CED7 |
| backend/app/modules/alerts/inspection_export.py | 片段：104–151 | 22BEE0573BE6B58CA179667CCCD0E974E390AA0324D5FD88D6E09D23B15891B9 |
| backend/app/modules/analytics/semantic.py | 片段：496–516、655–744及cached_only相关分支 | A533F07B4F6EE08EDEEA656D01B7790BB9E10CA963E4BA35EBF760CC83080D36 |
| backend/app/modules/capabilities/service.py | 片段：192–291；allow_private相关定点检索 | 5468A5509D2AEDD33084DD7E2D3EF1D6C17E7026FDE9B90521E29BDA5BC75661 |
| tests/backend/test_dsh_task_datasource_halt.py | 通读；仅静态对照，未执行 | C7AE19F340D87401EDFF0B5F9DC318CFE8C3C30EA43CA752FD08E4A7B2F86830 |
| tests/backend/test_business_worker.py | 片段：1–124；仅静态对照，未执行 | 97E6DC5A23313D23DAB3C11DDFD8EF56D7ED72F3AE1D0C1A2EEAFF730019DA5D |

报告冻结：以上发现均为静态审查和下一批实施设计；没有在本阶段新增动态复现、通过测试或访问在用平台。当前 B16 成功记录与本报告候选项分别记录。
