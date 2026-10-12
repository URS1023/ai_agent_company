# B17-Q01 独立只读复核：停止新取数后交付留存结果

2026-10-04。工作树 `E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-recovery-continuation`，HEAD `7ed8f58b9fd81a151bc0d078216e822f9670aa15` 加本批未提交修改。本复核阶段只读产品、测试、已有报告及日志；唯一新增文件为本报告。没有运行任何测试、浏览器、服务、模型、数据库或构建；没有修改已冻结 Host 文件、被审产品/测试、已有报告或其他代理正在编辑的 inventory 模块。

## 结论

在本次明确检查的五个 Builtin DSH 分支中，**未找到由本次 `execution_owner=dsh + provider=builtin` 豁免引入的确定新取数、当前权限或未知外部副作用绕过路径**。可接受此有限修复，不需要据本复核新增产品修正。

这项判断同时核对了聊天自动 iframe：源码与当前入口实际引用的构建 Viewer 均把聊天预览强制转为静态快照，不能仅凭“原报表有 dataType=2/3 或 refreshSeconds>0”判定它会在聊天自动重查。用户显式点击“独立打开”则保留既有实时页面行为，确实可以触发保存 SQL/HTTP 取数；它与聊天自动预览是不同入口，不能将“零追加查询”扩展为平台所有页面永不查询。

作者最终 25 项日志/XML 的计数和冻结指纹与实际读取一致。本代理没有重新执行这些测试。真实留存 Q、静态大屏、Excel 文件和 halt 后源访问计数有实质断言；同任务跨轮正例和同轮撤权反例分别存在，**同任务新 Run 再撤权的组合没有在这两个文件中实测**，应保留此验证边界。

## 豁免条件与派发顺序

`dispatch_state.py:16–33` 将五项例外集中在 `DSH_SAVED_DELIVERY_TOOLS`；判断先处理 `provider == database`，只有 builtin + dsh 同时成立才返回 False。HTTP/MCP 或无 DSH 归属的同名工具仍按原 DATA_TOOLS 判为需要数据源访问。`query_business_data`、`query_inventory_data`、`collect_device_monitoring_snapshot` 均不在例外中。

`tools.py:77–90` 从已验证 Run 建立 context，`execution_owner` 在 Worker 内固定写为 `dsh`，不从 arguments 读取。模型参数既不能选择这个归属，也不能选择 selected.provider。`tools.py:93–103` 在停止判断前重新列举当前可用工具，并对相同调用键回放执行 `authorize_replay`；`gateway.py:236–287、392–410` 会重新解析当前主体、工具权限与治理开关。对 builtin 名称的实际执行解析也优先绑定真实 builtin 定义。

`tools.py:124–140` 保持顺序：先检查同任务未知外部操作，再检查新取数停止，最后才调用 gateway。`has_external_side_effect`、同任务回执归属、HTTP/MCP 派发日志和未知回执处理均未被本次 diff 修改。五项例外没有外部发送动作；同名 HTTP/MCP 不取得本次例外。因此不能从该豁免推出“新 call_id 可以重发原未知外部动作”。相关 replay/dispatch 独立专项本代理未运行，也未把静态保持当成组合动态验收。

## 五工具实际分支

| 工具 | 实际入口和行为 | 当前权限/来源边界 |
| --- | --- | --- |
| `create_dashboard_from_queries` | `builtin_provider.py:931–950` 转 `query_dashboard.create_dashboard_from_queries`；加载已成功 QueryHistory，生成快照并 `save_report(refreshSeconds=0)` | 新鲜身份、`dashboards.create/analytics.view`；Q 属于当前用户/工作区、本轮或明确同任务续办；选择源、源启用与版本重查；真实保存链调用 SnapshotAccess |
| `create_business_dashboard` | `builtin_provider.py:873–890` 对 `execution_owner=dsh` 必进 Q 分支；没有 query_ids 时只从当前成功回执提取，缺少 Q 返回失败，不落后面的 `create_dashboard_draft` 新取数流程 | 最终转同一个 `create_dashboard_from_queries`；模型提供 query_ids 不能免除 loader 的持久成功回执与当前范围检查 |
| `get_business_dashboard` | `builtin_provider.py:975–978` → `dashboard_actions.execute` → `resolve_report/get_report`；返回平台报表信息和预览链接 | `dashboards.view`、报表资源范围、SnapshotAccess 与源资源重查；不是查询/刷新报表组件接口 |
| `update_business_dashboard` | 同入口；`dashboard_actions.py:329–350` 仅允许 dark-tech，复制现有内容改视觉、保存修订；明确跳过 data/query/sql/parameters/bindings | `dashboards.edit` 与报表/源/快照范围仍校验，save_report 会再校验；没有把 arguments 当任意报表内容或任意 SQL |
| `view_device_alerts` | `builtin_provider.py:797–802` → `service.alert_overview`，仅从持久报告/问题/处置日志拼装；可选 export_name 导出本次已授权页面 | `alerts.view` 及 `alerts.reports.get_report` 的工作流执行、规则修订、设备、旧源资源链；不调用设备采集、workflow execute 或重跑规则 |

`chart_renderer.load_authorized_query:34–70` 会重新取得当前 auth context，核对本轮成功 ToolCall 或 `validated_resume_calls`，随后检查 Q 所有者/工作区/成功状态、所选来源、源启用与版本。`resume.py:143–169` 的 DSH 回执跨轮读取还限定工作区、用户、会话、task_id、非活动历史 Run、插件版本及资源集合，不只凭 task_id 碰巧相同放行。

`SnapshotAccess.source/sql` 只读取平台资源和已存 SourceMetadata，再做 SQL 字段范围分析；不会调用业务库或刷新元数据。已有报表的停用来源可保留可追溯读取，和新 Q 交付要求源 active 是不同的既有合同；没有将“停用”一概等同于所有历史报表内容都必须删除。新增测试的 disabled/columns 反例针对新大屏与 Excel。

## 既有动态报表与聊天自动 iframe 的进一步核对

确认 get/update 接受有权访问的既有报表，不要求它原本就是静态快照。`dashboard_actions.py:343` 保留原 `refresh_seconds`。这条事实本身不足以认定聊天会自动新取数，实际默认链为：

1. `apps/web/app.js:1052、1074` 对 `resultType=dashboard` 调用统一 preview markup；`get_business_dashboard`/`update_business_dashboard` 的结果类型也进入该路径。
2. `apps/web/assets/platform/chat-dashboard-preview.js:8–15` 校验同源 `/report/view/:id`，为 iframe URL 强制 `chat_preview=1`；`hydrate:93–98` 在进入视口后自动挂载，不需要额外点击。实际 frame.src 只取这条规范化路径。
3. `ReportViewer.vue:29、96–99` 识别聊天模式，先运行 `chatReportSnapshot`，再将转换后 screen 传给舞台，并令 refreshSeconds=0。`DashboardViewerStage.vue:56–60` 只在 loading 完成且无错误时挂载 JimuCanvas，避免先挂原动态组件再补转静态。
4. `chatReportSnapshot.js:7–32` 将每个 widget 的 dataType 统一设为 1；2/3 远程绑定只取留存 data/chartData/dataset，缺失数据变为文本提示，Iframe/WebPage 组件也变文本。它同时删除 drilldown 和 linkageConfig。Viewer 聊天模式隐藏手动刷新/钻取入口。
5. `useWidgetData.ts:70、84、93–100、111–115` 的 immediate watcher 会调用 refresh，但 isRemote 仅接受 dataType 2/3；转换后 type=1 在请求前直接 return，不会进入 reportApi.widgetData 或 SQL/HTTP POST。refreshSeconds=0 也不会安装查询刷新计时器。

当前 `apps/web/assets/analytics/index.html` 引用 `index-CkMAeOxi.js`，其中路由实际加载 `ReportViewer-CNuCFuIq.js`。只读定点检查该构建文件确有 `chat_preview === 1` 判定、snapshot 转换、dataType 1、snapshotOnly、去 drilldown/linkage 和刷新 0 的对应逻辑，未只根据未发布源码推断。没有运行浏览器，因此这里是实际源码/构建链静态证据，不冒称本代理浏览器实测。

**另一个明确入口是用户点击“独立打开”**：preview routes 保存的 open 链接刻意去掉 chat_preview，Viewer 沿用原 dataType 2/3 和 refreshSeconds。useWidgetData 的 immediate watcher 会调用 `GET /reports/{report_id}/widgets/{widget_id}/data`，`reports/router.py:112–115` 转 `data.refresh_widget:24–41`，该实现确实会执行保存 SQL，或 dataType=2 时调用固定 HTTP collect。这是显式独立页面的已有行为；本修复没有实现跨页面/平台全局数据源冻结。现有结果卡在聊天里自动出现不会自动走这个独立链接。

已只读查看既有 `tests/ui/chat_dashboard_preview.js` 的前段：它覆盖含 SQL/HTTP 绑定与缺失快照的预览 fixture，并拦截未知 API；本阶段未运行，也不把它当作本次新增 25 项后端专项的一部分。

## 新测试证据强度

`tests/backend/test_dsh_halted_task_saved_deliveries.py` 共 336 行，已通读；`test_dsh_task_datasource_halt.py` 读取本批 diff 与新增预警路径。确认：

- `saved_delivery_case:29–106` 建立本轮临时 A/B SQLite 来源；真实 `chat.prepare/tools.execute(query_business_data)` 取得 A 两行并检查真实 QueryHistory。B 仅在真实 readonly_connection 边界制造连接失败，随后核对已提交 halt 与原 ToolCall，未发送慢查询。
- halt 后每次 `queries.execute_read` 或 `execution.readonly_connection` 进入都断言失败，来源结构刷新与模型调用被明确禁止；最终还断言读取/连接数组没有增加。真实 save_report、load_authorized_query、export_query、artifact/storage 没有被成功桩替代。
- 主链 `126–160` 检查两个大屏入口、get、update；真实报表每一份 verified widget 行与 Q 两行完全一致，`queryIds/sourceQueryIds` 保持 Q，refresh=0。Excel 真实打开 storage 文件检查列、两行及 source_query_id。SnapshotAccess.content 包装器调用原实现，不是空成功替身。
- 跨轮正例 `172–200` settle 原 Run 后以严格“继续”建立新 Run，task_id 不变，检查 `validated_resume_calls` 只保留 A 的成功 Q；新大屏/Excel 成功，A/B SQL 及 schema/profile 仍拒绝，原 Q/成功与失败回执不改写。
- 权限/归属反例 `203–260` 对来源停用、缩列使用正式 API；另构造真实其他 owner/workspace 及不同会话同 task_id。三种交付入口均失败且不产生新报表/工件，不是只检查 status 而忽略输出数据。
- 普通成员例 `264–292` 通过正式角色 API 撤销 dashboards.create 和 analytics.export，再拒绝原 key 回放与新调用，保留旧回执。**该例仍在原 Run；不是“新 Run 后撤权”的组合例。** 角色撤权会在工具可用性前置检查返回 tool_not_available；这证明不可回放泄露，不证明一定进入了 authorize_replay 内部。
- 星号缩列例 `307–336` 直接进入真实 `save_report` 并要求 snapshot_scope_revoked，补足 loader 仅以 source_version 不同拒绝时无法单独证明 SnapshotAccess 的问题。
- 五种名称的 provider/owner 参数化测试为分类纯函数对照；不能把它们称作五种不同外部 provider 的真实派发测试。新增预警正例则确实使用输入常量生成实际规则/工作流报告，halt 后经真实 gateway/provider 读取它，随后 database.query 仍被拒绝。

只读核对 `.implementation/b17-halt-saved-red.xml` 为 1 failed / 0 errors、2.966 秒；`.implementation/b17-halt-saved-focused-04.xml` 为 **25 tests / 0 failures / 0 errors / 0 skipped、28.754 秒**。两份 log 所示内容与对应结果一致。作者列出的前三次失败/夹具修正记录没有在此改写，本代理未追加执行。

## 保留边界

1. 没有“同任务新 Run + 再撤角色/来源权限”的单一动态组合；源码每次重新鉴权，但跨轮正例与同轮撤权反例不能相加为该组合已实测。
2. get/update 的已有动态报表及聊天快照转换本次只读核查；新增后端正例使用的是本次新建 refresh=0 报表。自动 iframe 无新取数结论由源码和当前构建链支持，本代理没有新增浏览器或网络观测。
3. view_device_alerts 的 report_id 正例覆盖真实历史报告；本批未新增 export_name、problem_id/分页、同任务新 Run 再撤预警资源权的组合实测。读取链仍静态保留原鉴权，未找到明确绕过。
4. 未实现 Q02 跨宿主 ownership、并发检查/派发原子化、所有前端独立页面的全局停止、Worker 回执事务化或 Host 丢 ACK 恢复。没有用本次局部通过宣称这些边界关闭。

以上为明确的验证/产品范围，不因未实测就一概判作本次缺陷。

## 只读指纹

下列为本代理本次读取的实际文件字节 SHA256；产品/测试四项及最终日志/XML与作者冻结报告一致。没有更改被读文件。

| 文件 | SHA256 |
| --- | --- |
| `backend/app/business_worker/dispatch_state.py` | `b3823d07ec5cd4ba3645347e91c21f85ea9a84f5199952b35f7efc53e0e140a6` |
| `backend/app/business_worker/tools.py` | `cc7a86de750e9b915187878686dec6eebecb7c32323eb4dcfd5d2ec894f5b08e` |
| `tests/backend/test_dsh_halted_task_saved_deliveries.py` | `1c0f37a3e8b84686c2a3599b2eae7d697b0a3970f74cfb3fec526122d040dbab` |
| `tests/backend/test_dsh_task_datasource_halt.py` | `1f9ae008fd5b5c7fde93baae49fccddf536e4563de49b39acdb77642f22a17ea` |
| `.implementation/fix-halt-saved-delivery-batch17.md` | `cedfab837ce0ebcf715e361643ed4766fab5d80d0b21d4840c78a2b3baeba019` |
| `.implementation/b17-halt-saved-focused-04.log` | `0861033bdc2d6203bd8178972703a811b101e61fc065ed32dfd1a4824ab482f0` |
| `.implementation/b17-halt-saved-focused-04.xml` | `144829ff4cd580282397305d4554ae57200a836d34c1f6a955e9c639838122e1` |
| `backend/app/providers/tool/builtin_provider.py` | `dfc6f95cbb1a710306231e0a1a43ab5511d111c0443b5801e9d9c2989646cbf5` |
| `backend/app/providers/tool/gateway.py` | `5ad873f400107f1885b4f69e072966c9a69fa84fe5439bee86f8b9c5964fb8dc` |
| `backend/app/modules/chat_artifacts/chart_renderer.py` | `276b9f33df2db047be00724f933ae7471e11cdbe39df9e84faaaf01c722590e3` |
| `backend/app/modules/chat_artifacts/query_dashboard.py` | `e3cc7561acf343fd3010f98791c657e03baa2838f1f90b9a581e0d5070be6257` |
| `backend/app/modules/chat_artifacts/resume.py` | `9254aafd34f81e30f1aa629d16b0468732c1aa40223c91bafb3d057e9c47fda8` |
| `backend/app/modules/business_ai/dashboard_actions.py` | `1104e8d03e140642ff2969fb4b062aaf594137aa7c44564bc9e4644e6695a745` |
| `apps/web/assets/platform/chat-dashboard-preview.js` | `b217b2dbd26f0f8f7694e495f23a2c39d92b0162467f9c927b86f2fd7902ecd6` |
| `apps/analytics/src/components/ReportViewer.vue` | `0f76f371ac2371d62c5e5a46d0234a978522fe4ce59d5bce891a030e1f235002` |
| `apps/analytics/src/utils/chatReportSnapshot.js` | `97d97e4b483bed030b4b217a2d3d31164547d29347c4b3c71c4d221edff9f33c` |
| `apps/analytics/src/composables/useWidgetData.ts` | `d65a72f458555978f24c68c9240cdc9a85d58dea04aba04561332aa7b1bf8900` |
| `apps/web/assets/analytics/index.html` | `fd21c926af9a7b036af03e5899f8863d1bfcfd96a46414936dd7f84657864e85` |
| `apps/web/assets/analytics/assets/ReportViewer-CNuCFuIq.js` | `447e1506f13c7b358437d6d9727fd412d26ef396e15744a70204c5870452cb9b` |

其他实际读取为 `reports/service.py` 的 get/save 主链、`reports/data.py/router.py` 的组件刷新入口、`analytics/snapshot_access.py` 的源/字段校验、`business_ai/service.py` 的 alert_overview、`alerts/reports.py` 的报告授权、`query_spreadsheet.py` 与前端相关渲染入口；并非对这些模块做全面审计。文件定位中曾探测到不存在的 `builtin.py`、`query_inputs.py/query_loader.py`、`chatReportSnapshot.ts`、`useWidgetData.js`，随后均改读真实文件，没有为缺失路径创建文件。
