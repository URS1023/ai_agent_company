# 批次十六：用户原话复合交付浏览器验收（专项完成并冻结）

工作树：`major-delivery-acceptance`，基线 `79220893b1945ce766758d8b6b70c3e5ad669536`。本代理只修改 `tests/ui/full_browser_ai_documents_acceptance.js`，新增 `tests/ui/compound_file_inspector.py`、`tests/ui/compound_browser_contract.test.cjs`、`tests/offline/test_compound_file_inspector.py`。第八次开发单轮已在根代理冻结的产品/测试/资产上完整通过：14 项业务、0 无障碍问题、退出0；本专项结果不代替根代理随后执行的批次统一门禁。

## 实现范围

- 保留 AI 会话与文档中心原有两份五页 PPT；新增逐字输入用户原始巡检整句的普通会话，不预设 PPT 插件页数。
- 独立合成账本为窗口内 8 台应检、3 台已检、5 台未检；接口返回两行五列，合成用途和窗口在可见数据源、正文及文件中保留。它验证 HTTP 取数与交付，不验证生产 SQL 生成。
- 模型桩只能从本次真实请求包含的工具回执读 query/draft/report/artifact 标识，不允许测试全局缓存补回丢失上下文。首次真实调用终稿工具故意缺正文第 13 页，仅接受该精确失败与 partial；刷新恢复后第二轮只补第 13 页并完成 15 页。未知副作用和其他失败不放行。
- 真实 UI 下载由 Browser CDP 的 GUID/完成事件绑定磁盘文件与 SHA；独立 OOXML 检查器核验顺序引用 15 页、实际正文与原生饼图数值；Word 正文及实际引用的两幅图摘要；Excel 真实单元格数值/类型、查询来源页与宋体。拒绝孤立 ZIP 内容、重复引用、空页、假图、公式和文本数字。
- 两轮 Task/Run/draft、首次交付归属、查询接口读取次数、刷新后的任务状态和第二轮文件 SHA 均形成证据；大屏从可见链接打开，旁听同一页面真实报告响应核对保存快照、关闭刷新和查询标识。

## 已执行离线红绿

1. 原检查器红例：五个物理幻灯片文件、演示顺序五次引用同一页；旧 `assertPresentationFile` 未抛错。`compound-browser-batch16/red-existing-ppt-oracle.log`：1 失败。此为验收夹具缺陷，不是产品渲染缺陷。
2. 替换检查器后同例通过：`green-ppt-oracle.log`，1 通过。
3. 初版阶段模型合同和上述反例为 5 项 Node；随后补下载关联、原生错误前缀与 Run/UI 错误合同，最终 **9 项 Node 通过**，`green-fixture-contract.log` 保留最终结果。包括两轮阶段推进、丢上下文拒绝、只放行精确缺页、未知副作用拒绝、查询来源/数值/调用身份保护；具体扩展及对应开发失败按下文各轮登记。
4. 独立文件解析 19 项通过：`green-file-inspector.log`。经主树 Python 调用 `tests/run_isolated_backend.py tests/offline/test_compound_file_inspector.py`；不导入产品、不连接数据库/模型/HTTP。
5. `node --check tests/ui/full_browser_ai_documents_acceptance.js` 通过；限定本文件 `git -c core.longpaths=true diff --check` 无空白错误。
6. 既有 `--self-test` 经 `tests/dsh/local_network_only.mjs` 守卫通过，保留 `green-existing-model-contract.log`。该独立检查会启动本轮回环 SSE 模型桩，验证原五页阶段/旧分析值/跨轮隔离；没有启动 Host/Edge 或连接数据库。

## 浏览器开发首轮（夹具下载关联失败）

前端确认最终构建与资产冻结后启动，入口 `index-CkMAeOxi.js`。运行时 HEAD 为 `7c7174f96a283c54da659d719b1632ff7e73eb7f`；待提交 B16 产品同时存在，不等同仅测试该提交。完整取码摘要保存 `compound-browser-batch16/browser-first-source-sha.json`。

首轮 `full-business-browser/20261004-115802-aa99e47e`，116.640 秒后失败即停；UI 证据 `ui-results/full-browser-ai-documents-mutaj84y`。普通会话与原五页 PPT 都是实际 completed，PPT 下载接口返回 200/38127 字节，真实 GUID 文件已落盘。新增夹具误要求 CDP 下载 URL 等于 API URL，而正式安全下载通过授权 fetch 后创建同源 Blob，因此 60 秒后误报下载未完成。未进入新复合场景。原轮没有记录原始 CDP 下载事件，根因证据是实际落盘文件/传输与当前产品 `protected-content.js`、`app.js` 的 Blob 实现；不能把未记录的事件内容写成首轮已观测事实。

本次只修夹具为“真实点击 → 精确 API GET 响应字节 SHA → 单个同源 Blob（或精确直接路径）GUID → 完成事件 → 磁盘同 SHA”，文档中心按钮也沿实际请求绑定，不要求虚构 href。新增异源、并发额外下载、错误 GUID、取消、错误字节等负例，Node 合同由 5 项增至 **7 项全部通过**。成功和失败记录均落原始下载事件。

首轮网络 unexpected/application blocked 均为 0，40 个背景候选请求全部被阻断；Windows Job 清理后 active_after=0。失败证据保留未覆盖。

第二轮 `full-business-browser/20261004-120118-83b5c70e`，37.734 秒后首失败停。`browser-second-source-sha.json` 相较第一份只有本代理 JS 与 Node 测试变化，所列产品与资产摘要一致。证据 `ui-results/full-browser-ai-documents-mutanem0` 直接记录到真实同源 Blob 下载事件与 38127 字节，但 CDP `response.buffer()` 为零长度；附件响应体经 CDP 不可靠，不能使用它代替真实 HTTP 字节证据。仍未进入新复合场景。

随后沿用旧夹具已有的隔离代理传输旁听机制，在**同一实际 API 响应流**逐块计算 SHA，不再次请求接口、不改产品 fetch。只匹配本次点击之后本轮 host/path/GET 的唯一响应，要求响应 complete、无 aborted/error、大小与 GUID 完成事件一致，并核对磁盘 SHA。7 项 Node 负例补充不完整响应、错误字节、错误大小等，继续全过。

## 第三轮真实产品红例：成功 HTTP 查询不能导出 Excel

第三轮 `full-business-browser/20261004-120305-40d679d9`，126.969 秒后首失败停，UI 证据 `ui-results/full-browser-ai-documents-mutapp08`，保存 `browser-third-source-sha.json`。**11 个业务检查已过，但整套失败**；两份原有五页 PPT、Word/PDF/Excel 均通过新下载绑定，5 个落盘文件分别与实际 API 转发流 SHA 一致。

新场景：`query_business_data` 从本轮 HTTP 来源成功取得两行五列；`render_chart_image` 用同一查询成功生成 60659 字节宋体饼图；`export_query_spreadsheet` 接收同一真实 query_id 后返回 `validation_error`，正文“HTTP 采集结果请从数据源的采集历史查看”。运行因此 failed，还没有到故意缺 PPT 页的阶段，不能把这次失败算作预期 partial。

- 查询：`01M42HC52256P5VE61P0EWG86J`。
- 数据源：`01M42HC3CTAHTEMTMTFJ1PDYRG`。
- Run：`01M42HC4PZ12PHTF2XYHG0R6FC`。
- Tool 路径：`chat_artifacts/query_spreadsheet.py` → `analytics/exports.py:export_query` → `analytics/queries.py:historical_result`，其中 `query_mode == 'http'` 被无条件拒绝。
- 后续静态同根：`dashboard_image.py:dashboard_panels` 同样调用该 `historical_result(..., permission='analytics.export')`，也将被拒绝；尚未执行到该步。`create_dashboard_from_queries` 与 Word 图片解析链未见同类 HTTP query_mode 拒绝。
- 另一个已观测问题：`sessions.mjs` 先发布完整失败工具回执，再 throw 序列化 JSON，由 catch 再发仅 error 字符串的 `tool.failed`；UI 中原 error_code/metadata 被覆盖成 JSON 字符串。若不修，预定缺页的 presentation_progress 同样无法按结构核验；不能通过夹具反解析掩盖产品回执丢失。

两条均已通知根代理。查询代理负责最小 HTTP 历史结果/导出修复，根代理协调 Host 失败回执；本代理没有改产品、改用手抄 Excel、重新查询或减弱断言，暂停浏览器复测等待产品红绿。

随后根代理已通知 Host 修复冻结：合法 Worker 失败回执收到后不再二次发布无结构失败覆盖，仍通过原生抛错维持失败语义；根代理执行的真实 ToolRuntime 新 11 项及既有恢复 37 项共 48 项通过，日志 `.implementation/host-tool-receipt-before.log`/`after.log`。这是根代理报告的专项结果，不是本代理重跑或浏览器通过。仍等待 HTTP 导出修复冻结后复测原场景，不增加嵌套 JSON 反解析。

Host 最终补到新增 12 项＋既有 37 项＝49 项，最终日志 `.implementation/host-tool-receipt-final.log`；注释限定“已取得”，不假称所有 Worker 提前返回都已持久化。首次事件在持久化前失败的恢复边界保留在根代理记录中。

另将第三轮原始模型请求精读后分清两个合同：原生 ToolRuntime 给模型的 `role=tool` 内容本来就是 `Error: {结构化ToolResult}`，这是模型协议文本；UI 的原 ToolResult 被二次发布覆盖则是产品持久投影问题。根代理确认保留原生模型格式。本夹具**仅模型输入 `payloadReceipts`** 接受精确 `Error: ` 前缀，之后继续严格校验 failed、缺页错误码、真实 draft、缺正文13/物理14页；拒绝错误前缀、冒充成功、双层字符串、未知副作用和无进度结构。对应 Node 合同增至 **8 项全部通过**。UI 断言仍直接读取 error_code/metadata，没有对 UI 的嵌套 JSON 错误串进行反解析。

本轮 network allowed=300、unexpected=0、application_blocked=0；88 个背景候选均被阻断。Job active_after=0。实际饼图已只读查看，中文标签与 3/5、37.5%/62.5% 正常；该观察不代替完整 Word/Excel/PPT 浏览器交付验收。

## 第四轮：真实达到预期 partial，夹具字段位置错误

HTTP 修复与 42 项相关测试由查询代理完成后冻结；Host 最终 49 项由根代理完成后冻结。第四轮 `full-business-browser/20261004-121312-dc873d60`、UI `ui-results/full-browser-ai-documents-mutb2poa`，130.016 秒后首失败停。`browser-fourth-source-sha.json` 额外登记实际 Host/查询/导出源码。

本轮真实执行完 11 个工具，成功保存饼图、Excel、大屏、含双图 Word；最后 `finalize_presentation` 缺正文13/物理14页，UI 保留完整 error_code 和 presentation_progress，Run/Task 均为 partial，仅 presentation 缺项，Word 内嵌大屏已有服务端真实字节核验收据。说明上轮 HTTP 导出与 Host 回执覆盖两个产品缺陷已在该段浏览器链路验证；不是整套通过。

停点为夹具错误：原断言读取 `error` 展示片段的 `code`，实际流式前端片段合同只有 `{type,message}`，结构身份在 `run.error.code`，本轮确为 `delivery_incomplete`。经根代理确认，只将 oracle 改为检查 Run partial/结构码、唯一展示片段与 Run 错误文案一致；仍验证原工具精确缺页进度、任务义务和部分文件保留，不给产品添字段迁就测试。

新增纯函数反例涵盖错误 Run 码、错误/多/缺失展示片段、假 completed 和片段矛盾 code，**Node 最终增至 9 项通过**；Python 文件核验仍为 19 项通过，未无故重复运行。根代理保留修改前冻结 `batch16-frozen-files-before-oracle5.json`，下一轮使用新冻结，不覆盖旧证据。

## 第五轮：首轮四个文件真实下载通过，恢复等待错误

第五轮 `full-business-browser/20261004-121711-e1254670`、UI `ui-results/full-browser-ai-documents-mutb7t9t`，221.219 秒后首失败停。原工具缺页与 Run/Task partial 校验均通过，随后饼图、Excel、大屏图片、含双图 Word 的四个真实浏览器下载通过 API 响应流 SHA → 同源 Blob GUID → 落盘 SHA 关联。首次巡检接口读取次数为 1，不能将尚未执行的继续步骤算作不重查已通过。

本轮停止在新增 `page.reload({waitUntil:'networkidle0'})` 的 90 秒等待。失败截图显示历史和附件已恢复，但对话页有永久 SSE 事件订阅，不应使用网络空闲判断恢复。经根代理确认只将该导航等待改为 `domcontentloaded`，保留本轮 history GET 200、持久 Task partial 与 Store 同一 Task partial 的全部断言，仍为 90 秒，不延长超时。新增的大屏弹窗原本等待真实 report GET 与可见内容，没有同类网络空闲假设；旧通用导航助手未改。

本轮网络 application blocked/unexpected 均为 0，99 个背景候选被阻断。新冻结前根代理保留 oracle5 版本；本次只改 `full_browser_ai_documents_acceptance.js`，SHA256 `69CA8B5ACA57FB7F8D3264AFA88131B1104344DC8F189195A3771E05CD6A5B60`，语法及定向空白检查通过。第六轮取码摘要另存 `compound-browser-batch16/browser-sixth-source-sha.json`。

## 第六轮：两轮真实交付核验通过，大屏框架定位失败

第六轮 `full-business-browser/20261004-122344-f3683872`、UI `ui-results/full-browser-ai-documents-mutbg8rs`，166.250 秒后首失败停。本轮已完成原话首轮 partial → 刷新恢复 → 同 Task/draft 继续，第二轮仅写正文13页并终稿；两次 Run 不同，原文件仍归属第一轮且 Word/Excel 重下载 SHA 不变。四个业务读取检查点依次为 0、1、1、1。独立文件检查实际通过：15 个不重复且非空幻灯片、原生饼图3/5、Excel数值单元格3/5与来源、Word正文引用与实际饼图/大屏PNG摘要一致；服务端15页与指定大屏内嵌的约束回执也匹配真实下载。

停点是 `inspectCompoundDashboard` 复合断言未同时得到 `reportFrame` 与 `responseBody`，尚未执行最后页面内容与清理校验，不能算整套通过。事后静态确认夹具用 `frame.url().includes('/report/view/')` 定位子框架，而真实外壳 `apps/web/assets/platform/main.js:248` 使用 `/analytics-app?path=` 加 `encodeURIComponent(path)`，子框架 URL 不含该明文片段。这是明确的定位合同错误。首响应监听发生在 popup 事件之后还存在潜在盲区，但该轮没有保存弹窗独立截图及分项状态，不将这个推测记为已复现的 race。拟先沿真实 iframe 控件取得 `contentFrame()`，保留 HTTP 响应与所有内容校验，并补失败现场保存。

本轮 network allowed=405、unexpected=0、application_blocked=0，105 个背景候选连接被拒绝；Windows Job active_after=0。全部原证据与第六轮源码 SHA 保留。

根代理归档 oracle6 后批准最小修复：仅 JS 改为实际 `#business-module-frame.contentFrame()` 加 `.report-viewer` 就绪，拆开框架/响应断言，失败落独立弹窗 URL/各 frame URL 与正文/响应及截图。没有修改产品或增加额外查询；没有为尚未证实的首响应 race 扩增代理解析。语法和定向空白检查通过，oracle7 JS SHA256 为 `52C8101BE95516315912DDFF8994487630818656DA77B687885E0B6FFF4AC964`。原 9 项 Node 与 19 项 Python 合同保持，未新增镜像选择器单测。

第七轮启动前独立只读复核另确认新增清理助手误用旧 `/reports`，而正式列表为 `/dashboards`。根代理批准一并改这一个路径；精确报告 ID、真实确认文案、DELETE 200 与列表移除断言不变。子代理核对 Envelope.data、报告版本及刷新字段、完整静态快照、真实表格 DOM 文本、删除按钮/确认/接口和源删除次序，未发现其他确定合同差异；仍需动态执行验证。最终 oracle7 JS SHA256 为 `C0368747C21FD8D77C95AC7137B4540CAE0C413DE5A0B6CD55489764DABC6B4D`，上一个 `52C810…` 为未运行的中间修订，保留以便追溯。

## 第七轮：14 项业务通过，真实大屏卡对比度缺陷拦截整套

第七轮 `full-business-browser/20261004-123244-d6cf9af9`、UI `ui-results/full-browser-ai-documents-mutbrtio`，141.125 秒结束，**14 项业务检查全部通过，但整套失败**。第六轮已通过的文件与续办断言继续通过；本轮实际收到 popup 报告响应，版本1、完整原始两行五列、static/snapshotOnly、刷新0均匹配，页面表格可见，打开后巡检业务接口仍只读1次。真实界面删除报告返回200、列表移除，再删除合成数据源；既有移动端文档弹窗与最终源清理也通过。

最终 `accessibilityFindings` 留下1组 serious `color-contrast`、5个节点：聊天大屏卡4个 meta 标签 `#77879a` 对白底对比3.67，来源标题 `#718196` 对 `#fbfcfd` 对比3.87，均为9/9.5px小字，未达4.5。定位产品 `apps/web/assets/platform/ai-workbench.css:107,110`。已交根代理最小产品修复，本代理没有放宽 Axe 或更改产品；待修复冻结后复测。

本轮 network allowed=464、unexpected=0、application_blocked=0，122个背景候选被拒绝；Windows Job active_after=0。报告与文件证据均保留。已人工查看 `compound-dashboard.png`，截图捕获到 JimuCanvas 入场动画的 blur 阶段；源码有0.72秒有限入场动画，不能据此断言持续模糊，也不能把当前截图记为稳定视觉验收。已向根代理提出仅在截图前等待有限入场动画结束的取证改进，不暂停无限背景动画、不改页面样式或数据。

## 第八轮：冻结版本完整通过

根代理仅将 `ai-workbench.css` 两处前景色改为 `#536275`，并保存前次冻结后重新冻结34个源码/测试/门禁文件。截图取证仅增加同主页面1440×960视口、在30秒内等待有限入场动画自然结束；无限背景动画保留，不修改页面样式或业务数据，也没有延长总预算。新版本取码在 `compound-browser-batch16/browser-eighth-source-sha.json`，含本轮CSS；根代理另核对88个最终前端资产。

执行命令：主树绝对 Python 运行 `tests/dsh/business_browser_acceptance.py --suites ai_documents --iterations 1`，环境 `TEST5_E2E_PYTHON` 指向同一主树解释器。全程单套、900秒阶段预算、首失败停，没有改用业务数据库或外部模型。

- 终态：`full-business-browser/20261004-123925-4b77640e/manifest.json` 为 **passed**，144.281秒，退出0。
- 页面/文件/工具证据：`ui-results/full-browser-ai-documents-mutc0fhw/result.json`；14项业务检查，`failures=[]`，`accessibilityFindings=[]`。
- Task `01M42KERMMYRFS3T4YPB7TP2QW`：首Run `01M42KERMW2R7ZJF25DNAGNV12` 为 partial，续Run `01M42KEYM3A87S055887DPJK36` 为 completed；两轮沿用同一 `deck-85af880f8f7870071dc5`，只补正文第13页。
- 查询 `01M42KERZVSN6NAQCTT0XCC369`；巡检读取检查点为0→1→1→1→1，最终含打开大屏/全部下载仍只有一次业务 GET。
- 15页PPT独立顺序/正文/原生饼图核验、Excel真实数值与来源、Word双图字节与指定报告版本、原文件继承归属与SHA不变全部通过。原有两份5页PPT继续通过。
- 大屏 `01M42KETDNGZKZG01Q18SFKAEF` 的真实浏览器GET、版本1、完整保存数据、刷新0、可见表格文本通过；随后真实UI删除返回200且对应卡片移除，再删除合成来源。
- Network：469次允许请求；121个浏览器背景候选连接均被阻断；unexpected=0、application_blocked=0。允许计数包含页面/平台API和回环模型等，不能混称业务数据库查询数。
- Windows Job：active_before=1，清理后active_after=0。未留下本轮服务或浏览器进程。

独立文件核验详情另存 `compound-file-inspection.json`，本轮字节摘要：

| 产物 | SHA256 |
| --- | --- |
| 饼图PNG（1303×1138） | `d8582e77b37f4562d7a3b9e3dd221193053f7b696c1af3d2cf5e46592faf0a00` |
| 大屏快照PNG（1600×624） | `00e6a1d42c4c5cf809c1201b85c7beb260cad92d3e4d21d0924aa9a2a4c07f83` |
| Word DOCX | `6b20d713f0a90ed1e62b79be747caba3c34e786d67495bd0545e073302d57a3f` |
| Excel XLSX | `99f2376771328eb941b42fd85b8dda8af1aa82e6b7302faf5a25e103a89beda7` |
| 15页PPTX | `504c0627e7b628c57a14fd429eb3d9bc55d73490c33ea9b8c29def8330193d1f` |

已人工查看最终 `compound-dashboard.png`：入场模糊消失，中文状态、3/5、单位与非生产标记清晰，长统计期间按组件规则省略；完整统计期间仍在DOM、保存快照和Word/Excel核验中。此观察不代替逐页Office渲染验收。首次六轮的夹具/产品失败、第七轮对比度失败全部保留，不改写为通过。

最终本代理四个文件冻结（详见 `compound-browser-batch16/final-test-sha.json`）：

| 文件 | SHA256 |
| --- | --- |
| `tests/ui/full_browser_ai_documents_acceptance.js` | `5C4852DC00C13B65509DD4E49B2C16AE4554D53FC808942B51E9BE01B15E49B5` |
| `tests/ui/compound_file_inspector.py` | `C20E7256971CF999F460EE26A030F3597D28DDD3AEBCCDBCD065295686DE4F67` |
| `tests/ui/compound_browser_contract.test.cjs` | `751A45D009289C3B4A5F456592A5B5B5D753B618EA37E59404B3B7B331F877A7` |
| `tests/offline/test_compound_file_inspector.py` | `1C97DE1E4B817A767785A5B04269D69EBFFE2AE61FEE09C5183BBE7605F7F532` |

专项汇总：9项Node合同、19项独立Python文件核验、既有模型合同自检通过；第八次真实Edge单套14项业务完整通过。HTTP历史导出42项及Host回执49项为相应代理执行、已在本链路使用的相邻结果，不能累计成“本代理自行运行”的用例数。源码/测试已冻结，不提交推送，统一门禁由根代理执行。

## 目前边界

所有新增数据为明确标记的本轮回环合成数据；外部模型为有界规则桩。通过也不代表真实模型稳定性、真实 `szh_new` 巡检口径或 SQL 正确性。Word 核对嵌入实际可信图字节；不宣称 Office 软件视觉排版全量验收。图片核验尺寸/可见非空、来源与数值摘要及内嵌关系，不进行 OCR。PPT 读取 OOXML 实际幻灯片与原生图表，没有通过空页或复制同页凑数。
