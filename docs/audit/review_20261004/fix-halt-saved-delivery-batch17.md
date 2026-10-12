# B17-Q01：异常停止后继续交付已授权留存结果

2026-10-04。独立树 `major-recovery-continuation`，B16 源码基线 `7ed8f58`。本记录为 ignored 工作证据，不改审计文档、门禁或在用服务。产品及测试在最终 25 项专项通过后冻结，等待根代理整合；未提交、推送、构建、打开浏览器或运行整批门禁。

## 已复现与修复

原始产品源码下，真实 `chat.prepare → tools.execute(query_business_data)` 用来源 A 的临时 SQLite 取得两行，落 `QueryHistory` 与成功 `ToolCall`。来源 B 在真实执行器的 `readonly_connection` 边界注入一次 `connection_failure(... TimeoutError(...))`，未向 B 发送慢查询，失败 `ToolCall.failure_domain=datasource`、`recovery=stop_datasource` 及 Run halt 均已提交。随后真实 `create_dashboard_from_queries` 被 `database_access_halted` 拒绝，没有到达报表服务。

原始红例：`.implementation/b17-halt-saved-red.log`、`.implementation/b17-halt-saved-red.xml`，1 项失败，首次执行即停。日志包含明确 `step_name="create_dashboard_from_queries" error_code="database_access_halted"`。原始失败证据未覆盖。

分类集中在 `dispatch_state.accesses_datasource`：仅 `provider=builtin` 且由 Worker 固定传入 `execution_owner=dsh` 时，以下五项按历史读取/静态交付处理：`create_dashboard_from_queries`、`create_business_dashboard`、`get_business_dashboard`、`update_business_dashboard`、`view_device_alerts`。`tools.execute` 的停止检查与故障分类使用同一可信归属。

`provider=database` 始终被停止；SQL/结构刷新/采样共用的 `query_business_data`，以及库存/设备采集保持原限制。非 DSH 同名入口不享受例外；HTTP/MCP 也不因同名得到例外。动态未知副作用的既有回执保护、task 归属读取、取消、派发日志、QueryAuthority 和 SnapshotAccess 没有修改。

## 实际验证范围

- A 成功、B 故障持久化 halt 后，真实大屏创建双入口、读取、主题修改及 Excel 均成功；每个大屏显示行与 Q 原行逐项相等，`queryIds/sourceQueryIds` 保留 Q，`refresh_seconds=0`。Excel 从真实 Artifact/storage 中读取并用 openpyxl 验证三列两行及来源 query id；没有 mock loader、exporter、save_report 或 persist_artifacts。
- 主链包装 `SnapshotAccess.content` 并调用原实现，证明真实保存链执行了快照授权。另例通过正式数据源 API 缩列后直接调用真实 `save_report`，含旧额外列的星号快照得到 `snapshot_scope_revoked`；不依赖 loader 的 datasource_version 比较碰巧拒绝。
- 原任务 `chat.settle(failed)` 后发送明确“继续”，task id 不变，`task_datasource_halted` 仍为真，`validated_resume_calls` 只接受 A 的成功回执。新 Run 大屏及真实 Excel 成功，A/B 新 SQL、schema、profile 均被停止。
- 正式数据源 API 停用和缩列、Q 属于其他真实用户/工作区、同 task id 但不同会话的 Q，均拒绝新大屏/Excel，没有落新报表或工件。原 Q 行数据及 A/B 原始 ToolCall 不改写。
- 普通成员通过正式角色/用户 API 建立后真实取数、真实交付。正式撤回 dashboards.create 与 analytics.export 后，旧调用同 key 回放和新调用都拒绝；原成功回执和工件记录保留。
- 旧 mock 用例把 `view_device_alerts` 当新取数的合同改成真正取数工具 `query_inventory_data`。新增预警正反对照先用平台输入常量建立实际规则/工作流报告，halt 后由真实 gateway/provider 读取原报告和问题，再确认 `database.query` 仍拒绝。此预警不访问设备、不重跑流程。

本专项仅覆盖两个测试文件，不代表整批门禁或全功能通过。相邻 `test_business_worker_replay_guard.py`、`test_business_worker_dispatch_recovery.py`、`test_analytics_snapshot_authorization.py` 未在本代理阶段追加运行；由根代理按统一预算整合。B17-Q02 的非原子并发窗口及跨宿主 ownership 不在本实现范围。

## 隔离、预算及原始失败记录

全部测试仅经 MAIN `.venv_py312/Scripts/python.exe tests/run_isolated_backend.py`，设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，单进程、`--maxfail=1`；没有真实业务库、真实模型或在用 8011/8012。

平台库为 runner 钉定的 `sqlite:///:memory:`。A/B 是 pytest `tmp_path` 内两份专属 `halt-delivery-A.sqlite3` / `halt-delivery-B.sqlite3`，不存在数据库服务器/主机端口、隧道或转发。准备阶段只为合成测试建表/插入两行并经 API 生成本地元数据；正式业务段每例预算最多两次 `execute_read` 和两次连接边界尝试，A 实际执行一条有界 SELECT，B 在发 SQL 前注入失败；每次查询 timeoutSeconds=2。halt 后读/连接计数不得增加，任何后续元数据刷新或模型调用立即使测试失败。新测试有九个此类夹具实例，最终一组共有 18 次业务查询尝试、9 条真正执行的 A SELECT、9 次 B 的未发 SQL 失败；不会自动重试 B。上述计数不包含夹具建库和元数据准备。

执行记录全部保留：

| 证据前缀（`.implementation/`） | 结果 | 说明 |
| --- | --- | --- |
| `b17-halt-saved-red` | 1 failed | 产品原始红例，真正旧 Q 大屏被 halt 拦截 |
| `b17-halt-saved-green-single` | 1 passed，2.597 秒 | 修复后真实大屏/Excel 主链，SnapshotAccess 原实现仍执行 |
| `b17-halt-saved-focused-01` | 1 passed / 1 failed | 句子“继续，使用已有结果制作大屏和Excel”未被现有严格续办识别识别，开启新 task；属于现有能力边界，未修改任务识别 |
| `b17-halt-saved-focused-02` | 4 passed / 1 failed | 测试先给 Q 写不存在的其他 owner，SQLite 外键拒绝；后补建真实用户/工作区，不放松外键 |
| `b17-halt-saved-focused-03` | 7 passed / 1 error | 普通成员测试角色缺少 chat.create，正式会话 API 403；后补足初始权限再执行真实撤权 |
| `b17-halt-saved-focused-04` | **25 passed，0 failures/errors/skips，28.754 秒** | 新真实交付 14 项 + 修订后的任务停止 11 项 |

每个前缀均有 `.log` / `.xml`，失败记录保留。前三次专项均按首失败停止，再修正对应前置；没有资源超时或连接异常引发自动恢复。现有自然语句续办识别边界应留给后续任务归属改进，不能把用户原话称为无效输入，也不能将此次通过泛化为所有“继续…”表达均正确继承任务。

最终命令：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_dsh_halted_task_saved_deliveries.py tests/backend/test_dsh_task_datasource_halt.py -q --maxfail=1 --disable-warnings --junitxml=.implementation/b17-halt-saved-focused-04.xml *> .implementation/b17-halt-saved-focused-04.log
```

`git diff --check` 对本代理四个产品/测试文件通过；仅提示工作树 LF 在后续 Git 操作可能转 CRLF，不是测试失败。

## 冻结指纹

| 文件 | SHA256 |
| --- | --- |
| `backend/app/business_worker/dispatch_state.py` | `B3823D07EC5CD4BA3645347E91C21F85EA9A84F5199952B35F7EFC53E0E140A6` |
| `backend/app/business_worker/tools.py` | `CC7A86DE750E9B915187878686DEC6EEBECB7C32323EB4DCFD5D2EC894F5B08E` |
| `tests/backend/test_dsh_task_datasource_halt.py` | `1F9AE008FD5B5C7FDE93BAAE49FCCDDF536E4563DE49B39ACDB77642F22A17EA` |
| `tests/backend/test_dsh_halted_task_saved_deliveries.py` | `1C0F37A3E8B84686C2A3599B2EAE7D697B0A3970F74CFB3FEC526122D040DBAB` |
| `.implementation/b17-halt-saved-red.log` | `52FE975B2AA0E0ABA0357D0FA979674B1BFB07499C59A851667ECA684D45B182` |
| `.implementation/b17-halt-saved-red.xml` | `AB86262A6CC39C8E60EDC7D8BA64DB7C36C83C5CAA2B096DB282BA1C60448585` |
| `.implementation/b17-halt-saved-focused-04.log` | `0861033BDC2D6203BD8178972703A811B101E61FC065ED32DFD1A4824AB482F0` |
| `.implementation/b17-halt-saved-focused-04.xml` | `144829FF4CD580282397305D4554AE57200A836D34C1F6A955E9C639838122E1` |
