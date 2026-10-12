# 批次十六待实施：用户原话的巡检复合交付浏览器验收

归档说明：以下为独立代理原始只读计划的正式副本。代理当时只写入 ignored 目录；随后由主代理归档到审查目录，内容仍全部属于待实施计划，不新增运行结果。

2026-10-04，只读实施计划。工作树 `major-compound-delivery`；本次读取结束时 HEAD 为 `e28b35b194a5ea84c8dbe26ed5eb68b07763aac2`。只新增本 ignored 文件；未修改冻结的产品/测试/正式记录，未运行测试、解释器导入探测、浏览器、模型、服务、数据库或网络请求。下文所有新场景与反例都**尚未执行**，不是新的红绿结果。

## 1. 要补的实际用户路径

浏览器输入框必须逐字输入以下内容，保留用户的“图饼”说法，不能由测试改写成更容易执行的工具指令：

> 帮我分析一下设备巡检，近期有多少设备需要巡检，但是未巡检的需要图饼并导出为word和excel，再帮我做一份15页的ppt然后做一份数字大屏放到word里面

应覆盖真实 Edge → 可见数据源选择/输入框发送 → 当前 DSH Host → Python Worker → 实际查询、图表、文档、演示、大屏服务 → 持久任务/文件 → 浏览器显示与下载。只替换外部数据和模型为**本轮创建的回环合成桩**；不能把整份 PPT/Word/Excel 预制在模型桩里，也不能从测试直接调用 Worker、业务 API 或写平台表来冒充浏览器完成。

最小主场景是一个新会话、原话加一次继续，共两轮：第一轮先真实交付饼图、Excel、大屏和含双图 Word，15 页 PPT 故意缺最后一页正文，让真实终稿工具拒绝，最终保持 `partial`；第二轮从同一输入框继续，只写缺页并生成 15 页终稿。既证明未完成不会假报成功，也证明已有合法交付保留且无需重新查询。这里的第一轮故意缺页属于验收故障注入，不是产品正常成功体验目标。

现有两个五页 PPT 场景必须继续保留：AI 对话生成五页 ERP；文档中心人工生成五页 ERP。新增场景不替代它们，不把五页断言统一改成十五页。

## 2. 现有套件实际覆盖与最小落点

`tests/ui/full_browser_ai_documents_acceptance.js` 的本机模型桩就在同一文件中，`startFixture` 同时提供 `/business`、`/chart.png` 和 `/v1/chat/completions`，并不是另一个真实模型场景文件。

| 位置 | 现有行为 | 新场景必要扩展 |
| --- | --- | --- |
| 35–38、254–290 | 只识别普通分析原话和五页 PPT 原话；从工具回执逐阶段推进；所有工具结果都要求 success | 单独加入复合原话和续办分支，不扰动旧分支。按本场景实际请求和工具回执推进，不能依据模型调用次数盲走步骤 |
| 58、299–333 | 4×4 固定 PNG 仅用于 Markdown 显示；业务记录只有收入/运行率 | 另加本轮巡检合成接口；真实饼图和大屏图片由产品工具生成，禁止复用这张 4×4 图 |
| 392–410 | 数据源选择函数硬编码旧名称和描述 | 增加带默认值的名称/说明参数，旧调用不变；新场景在可见列表明确勾选独立巡检源 |
| 430–457 | `sendChat` 等待指定 Run 终态，固定要求 completed；禁止任何失败工具/错误 part | 默认行为保留。新增显式 expected outcome，仅允许本故障场景的 partial + delivery_incomplete 与一次精确缺页失败；绝不把 failed/interrupted/任意工具错误一概视为通过 |
| 447–450 | 只收集 `source_run_id == 当前 Run` 的 deliveries | 保留旧返回字段，同时增加按真实 task ID 收集的 task/projections/继承产物快照；否则续办第二轮天然看不到第一轮文件 |
| 461–519 | 下载真实文件，但关联较弱；ZIP 全成员转 UTF-8；PPT 页数写死 5，并用正则计数 | 加下载身份与 SHA；新离线内容检查保留原始字节并走 OOXML 关系。旧五页检查默认仍为 5，新调用显式 15 |
| 599–605 | Word 只搜正文词，Excel 拼全部 XML 搜一个数字 | 新巡检场景逐单元格/双图摘要/正文数量核验，不能复用这些弱断言作为新场景通过条件 |
| 767–807、622–662、846 | 两个五页场景；AI 五页的 stageCalls 使用全局累计模型调用 | 新复合阶段放在 `await createManualPresentation()` 之后、移动端阶段之前，避免污染已有三次阶段调用断言；新断言全部限定本场景起止索引和真实 Run/Task |
| 878–953 | 成功记录与异常记录分别写 result.json，成功模型记录裁剪较多 | 增加 compound evidence；成功与失败都保存同样的关键身份、计数、回执摘要、文件 SHA、检查结果，不能只在异常时保留关键证据 |

外层 `tests/dsh/business_browser_acceptance.py:248–260` 已启动独立 Host 并注入网络守卫；`real_platform_smoke.py:136–152` 使用临时 SQLite、独立 storage/sessions，调度关闭、ES/Redis 指向被守卫拒绝的 9 端口。继续沿用，不新造绕过守卫的启动流程，也不碰 8011/8012。

## 3. 合成数据的业务口径与来源展示

另建 `/inspection-business` 路由和可见 HTTP 数据源，名称例如“隔离验收：设备巡检合成快照-本轮编号”。通过 `createHttpSourceThroughUi` 真实配置 GET、固定地址、记录路径、输出字段、启用和只读自动调用许可；必须在 AI 数据源菜单由用户动作勾选，发送 payload 仅含这个来源。

建议合成账本先固定以下事实并落盘为 `compound-fixture.json`：

- 统计窗口固定为北京时间 `[2026-09-28 00:00:00, 2026-10-05 00:00:00)`，将“近期”明确解释为本验收快照的窗口，不能称今天实时生产结果。
- 应检设备按设备标识去重后为 8 台；其中 3 台在该窗口有完成记录，5 台没有完成记录；未检占比 62.5%，已检占比 37.5%。数量单位是“台”，不是巡检项、告警、待处理问题或检查报告数。
- 原始合成账本可包含重复完成记录和窗口外设备，夹具自行确定预期集合，最后接口只返回两条互斥汇总行。**这是合成接口提供的已统计结果，不证明本产品正确生成真实数据库 SQL 或正确执行去重聚合。**若以后要验 SQL，另立受限本地合成数据库场景，不把它混进本轮 HTTP 流水线覆盖声明。
- 两条返回记录使用明确列名：`巡检状态`、`设备数量`、`单位`、`统计期间`、`数据性质`。分别为 `已巡检/3/台/.../合成验收非生产` 和 `未巡检/5/台/.../合成验收非生产`，共 5 列，便于大屏快照完整显示两行五列。

接口返回值由账本生成；浏览器预期值从独立保留的账本/预期集合读取，不能从被测 Excel 或被测回复反向生成 expected。模型桩首次必须发真实 `query_business_data`，参数明确本次 source ID、自然语言模式、max_rows 足以容纳两行。HTTP 分支实际走 `builtin_provider.py:703–712` 的 `collect`，不假称该路径测试了 SQL 生成。

浏览器应读取并记录：选择器显示的合成用途说明，实际发送 datasource_ids，成功工具结果的 `queryId/datasourceId/datasourceVersion/columns/data/totalCount/truncated`。断言两行、未截断、列和值均符合预期。最终正文、Word/PPT/大屏都明确合成窗口与来源，不把 `totalCount=2` 误认为应检设备只有 2 台。

不能给原话附加隐藏的 task_id、required_deliveries 或 15 页设置。复合场景是新普通会话，不打开 PPT 插件模式给它预置 15 页；检查实际发送 payload 无旧五页 page_count 残留，页数要求来自原话。需要清除残留时通过现有可见控件执行。

## 4. 回环模型的最小有界执行脚本

模型桩只计划调用并根据**实际模型请求所带工具回执**取 ID、行值和进度。严禁通过测试外部全局缓存补回生产上下文已经丢掉的 query_id、draft_id、report_id；缺少必要回执/可信任务摘要时应让测试失败。全局状态可用于核对计数、阶段和预期，不能成为生产遗失上下文的替代品。

### 第一轮：原话、部分交付和确切缺页

| 阶段 | 实际工具 | 从真实回执取得并核对的依赖 |
| --- | --- | --- |
| 1 | `query_business_data` 一次 | 本次 source；Q 为真实 queryId；两行 3、5，未截断 |
| 2 | `render_chart_image` | `{query_id:Q, chart_type:'pie', label_column:'巡检状态', value_column:'设备数量'}`；得到饼图工件 C |
| 3 | `export_query_spreadsheet` | 同一 Q；得到 Excel 工件 X，不使用模型手抄的 render_spreadsheet |
| 4 | `create_dashboard_from_queries` | `[Q]`，明确“巡检合成快照大屏”标题；得到实际 R、保存版本、href；保留查询来源且不开自动刷新 |
| 5 | `render_dashboard_image` | R；得到大屏快照工件 D、同 Q 和真实 render_mode |
| 6 | `render_document` | 宋体 DOCX；含 8/3/5/62.5% 与窗口、来源说明；两个 image 块分别引用 C 和 D；大屏图标注“保存查询数据快照，非实时页面截图” |
| 7 | `create_presentation_plan` | 15 总页，封面+13正文+结尾；保存返回的真实 deck draft_id、outline/page_number 映射 |
| 8–10 | `write_presentation_slides` | 显式同 draft_id，正文 1–4、5–8、9–12；每批确认 written_now、rejected_slides 空，最终 missing_slide_numbers 恰为 `[13]` |
| 11 | `finalize_presentation` | 故意提前执行一次，必须真实失败；`error_code=tool_execution_error`，metadata.presentation_progress 指向同 draft_id，缺正文13/物理14，不生成可下载终稿 |
| 12 | 模型普通文本结束 | 正文明确已交付哪些内容、还差 PPT 一页；平台应保存 partial + delivery_incomplete，不能 completed |

这是 11 次实际工具调用加一次文本完成模型响应。`final_spec` 当前在 `presentation/draft.py:532–540` 拒绝缺页，gateway `:525–529` 保留 presentation_progress，因此无需 mock finalize 或向数据库写失败状态。

回环桩的失败允许清单必须同时绑定“复合场景第一轮、首次 finalize、同 draft_id、精确缺页13、相应进度”，并记录实际错误。任何 SQL/HTTP 异常、未知副作用、权限错误、第二次同类失败或不同缺页都保持失败；其他旧模型场景仍要求全部 success。

13 个正文主题要各有实际可见内容，不能把仅有 3/5 的两行数据伪造成 13 个不同业务事实。可覆盖统计口径、来源、应检概况、状态饼图、风险解释、缺失边界、建议排程、核对清单、文件使用与快照说明；建议须标注为建议，未知设备名单/原因/历史趋势不得编造。最终下载时核对主题分布和正文，而不是接受“补充分析 N”空页。数字图表/表格内容由 Q 的实际行构造并与独立预期再次比对。

### 第二轮：浏览器继续，不重新查询

先在页面保存第一轮 partial 证据和可下载文件，刷新同一会话或经会话栏重新打开，观察页面自然发起的历史响应，以证明任务、原清单和草稿不是仅存于 JS 变量。再在输入框输入：

> 继续，沿用刚才的查询结果，完成剩余PPT，不要重新查询。

这句话是明确承接上一任务的长句；不要使用“重新生成”触发整任务修订。本批另一个计划中的“重试刚才失败的任务”“继续，把剩下的做完”识别缺口，应另有语言分支回归，不能在本场景静默改 payload 来绕过。

浏览器实际 send receipt 必须产生新的 Run，但 Task、目标 deck draft_id、原始四类交付清单、15 页和 Word嵌大屏约束保持。是否由 UI 正常携带 task_id 要如实记录，不能测试主动塞入（`chat-session-view.js:272–273` 会在 state.dshTask 存在时自然携带）。

续办模型请求必须包含可验证的前轮草稿回执/进度；按同 draft_id 只 `write_presentation_slides([13])`，确认缺页列表清空，再 `finalize_presentation`，最后文本回复。计划仅需 2 次工具、1 次文本完成。不重新 create_plan，不重新生成 Word/Excel/图片/大屏，不发 query_business_data。

平台第二轮须 completed，task missing_deliveries 与 constraint_failures 为空；原 Word/Excel/图片的 artifact ID、source_run_id、摘要保持，PPT 为第二轮新工件。`sendChat` 新的 taskDeliveries 不应把这些继承文件错当本轮新产物。

## 5. 浏览器看到什么、下载什么

第一轮：实际用户消息含原话；输入框最终回到发送；任务/正文清楚显示仍有缺项；Word、Excel、饼图和大屏卡片可见；不能只有文件名或模型宣称“已完成”。显示未知副作用时应直接失败退出该受控场景，而不是继续补页。

第二轮：用真实 PPT 卡片下载十五页稿；原 Word/Excel 卡片仍可下载；点击大屏卡片打开真正的报告页面，保存截图与浏览器自然 GET 到的报告响应。确认 report ID/version/Q/源与工具结果一致，`refreshSeconds=0`，页面显示两条真实快照行/表或其对应可见图形。不得为校验自行 POST 报表查询或修改刷新配置。

所有下载使用实际卡片的 artifact ID 限定控件，从可见“下载”链接发起，不另用 fetch/requests。下载记录至少绑定完整 origin/path、期望 artifact ID、浏览器 download GUID、完成状态、实际文件名、字节数、SHA256；采用本轮目录，排除 `.crdownload`/取消/中断。现有 `downloadTransport` 只作旁听，若用于证据要和本次文件关联并断言实际完成，不能只输出日志。

建议下载五个文件：PPTX、DOCX、XLSX、饼图 PNG、大屏 PNG。用下载的两个 PNG 摘要核对 Word 的实际内嵌引用。不能只看 Word 有两个 media 文件；模板装饰图允许存在，两个预期图必须各自真正出现在正文可达关系中。

注意现有 `artifact/service.py:102–116` 的 artifact_reference 只是 id/type/name/size/download_url 等精简字段，不包含全部 metadata 或 SHA。不能等待一个当前接口根本不返回的字段：文件 SHA 由下载字节计算；饼图来源证明读取 render_chart_image 的工具 result.metadata；大屏保存版本读取 create_dashboard_from_queries 的 result.data.version 和页面自然加载的报告；嵌图匹配的服务端证明读取 task.constraint_receipts。render_dashboard_image 的 result.data 已返回 report/Q/render_mode，但未直接返回 dashboard_version，应沿前述实际证据关联，不在测试端伪造 metadata。

## 6. 独立的文件内容判定

最小可靠方案：新增一个只收“本轮下载文件路径+预期合同 JSON”的离线 Python 检查 helper，输出有界 JSON；只用既有 zipfile/XML、Pillow、openpyxl、python-docx/python-pptx 库，不 import app，不读取 .env，不建 HTTP/DB 客户端。主树已装库的目录存在已静态确认，实际可导入性仍待执行。由 TEST5_E2E_PYTHON 显式给主树 `.venv_py312/Scripts/python.exe`；独立运行时同样显式指定，不安装依赖、不回落到未知 Python。输入路径须解析后确实位于本轮 downloads 目录，成员只在内存处理并设数量/总展开上限。

也可保留 Node 原始 Buffer 后在已有浏览器用 DOMParser 解析 XML，但 PNG解码、完整单元格类型与 ZIP 累计安全校验仍要补齐，不能只把现有正则换个地方调用。建议复用 Python 已有库以减少手写解析。产品 `artifact_inspection.inspect_file` 可作为补充交叉证据，**不得作为唯一 oracle**，否则产品与验收可能共享同一遗漏。

| 产物 | 必须从实际下载字节核验 |
| --- | --- |
| PPTX | 沿包入口→presentation→有序 sldId→关系→slide，恰好 15 个不同且存在的内部幻灯片目标；孤立 slide 不计，重复引用不计多页；逐页存在预期可见正文/图表，不是只在 notes 内；封面、13正文、结尾映射正确；查询数字和来源说明在预期页，不能靠整包搜字符串；非空中文文字与宋体声明可检查，视觉质量另存渲染证据 |
| 饼图 PNG | 可完整解码，合理大尺寸与非纯色/非透明图；来自 render_chart_image 成功回执，Q、pie、两列、两行、非截断一致；回执 rendered_values_sha256 对应 `已巡检3/未巡检5`。该 digest 按实际 Python canonical JSON 规则生成预期，不能误用 JS JSON.stringify 浮点/空白差异；图片确为服务端产物并下载留存 |
| Word | 正文 XML 的图像引用通过 document.xml.rels 到内部图像；正文可达摘要同时含饼图 C 和大屏 D，断链/外链/孤立图片/同图重复两次不能冒充；正文为8台应检、3台已检、5台未检、62.5%，含固定窗口、非生产标识、快照说明；实际正文与样式的中文字体为宋体 |
| Excel | 通过 workbook 声明与关系取得真实“查询结果”“数据来源”页；逐表头/行/列/单元格值比对两行完整预期，数字3与5仍为可信数值，无多/漏/重复行、无公式冒充；来源页 Q/source/version/样本行数2/未截断与实际回执一致；宋体；不能从别的 worksheet、sharedStrings 或缓存找到数字就通过 |
| 大屏 PNG/报告 | PNG 可解码且内容可见，真实 render_dashboard_image 回执绑定 R/version/Q；Word 中摘要等于下载PNG。浏览器大屏页面从真实保存报告打开，报告快照的值和来源相同；PNG是数据快照，不冒称交互网页截图 |

`tests/backend/test_openwebui_query_delivery_chain.py:113–162` 已有接近的 Q→图/Excel/大屏/Word 数值、来源、字体与双图摘要断言，可借用纯检查逻辑；它的 ToolGateway/SQLite 调用不能作为新增浏览器阶段的操作方式。`test_dsh_compound_boundaries.py:179–200` 只证明原话的四类义务能被真实文件满足，里面 Excel 是验收说明，不是巡检数值表，不能直接充当本场景合格产物。

## 7. “继续没有重做查询”的可追溯证明

需同时保留三层证据，不能只靠模型说“沿用已有结果”：

1. **实际外部读取账本**：`fixtureCalls` 对 `/inspection-business` 的 GET 总数恰为1；第一轮取数后记录 checkpoint，制作所有产物、刷新聊天、打开大屏、第二轮继续、下载的增量均为0。数据源配置如果未来新增合法探测，单列 setup checkpoint，仍要求业务阶段恰好1，不简单放宽成“至少1”。
2. **真实工具账本**：第一轮只有一个 successful query_business_data 回执，所有关联产物使用同 Q；第二轮工具恰为 write missing13、finalize 两个，不 create_plan、不重复export/render/createDashboard；精确记录调用ID、Run、draft/Q/R引用和状态，不只记录桩的 selectedTool。
3. **任务/持久上下文证据**：刷新后由页面自然读取的历史中 Task/draft/原四类义务保留，继续后Task不变；旧工件ID/source_run_id/SHA保持，15页PPT来自新终稿。若模型实际收到的历史没有前轮必要依据，即使测试全局数组知道这些ID也不能替它补上。

可给 fixtureCalls 增加 scenario、阶段、序号和请求/响应内容摘要；result.json 保存 checkpoints、精确请求累计和本轮下载检查JSON。清理仅从UI删除本轮创建的会话、报告和两个来源；清理前保存证据，异常保存原失败，外层回收自己的Host/Edge。不得借清理先删证据或连接其它工作区。

该场景证明“受控模型按现有成功回执续办时，产品链路能保留并复用结果”，**不证明任意真实模型永远不重新查询，也不证明所有重复业务请求已被服务端全局消重**。后者应另做服务端反例测试。未知外部操作结果仍禁止自动重放；本场景不注入模糊HTTP副作用，也不能把它塞进允许失败清单。

## 8. 最小变更范围与先失败的验收校验反例

后续建议仅：

- 修改 `tests/ui/full_browser_ai_documents_acceptance.js`：独立巡检源/场景、精确partial预期、跨轮证据、真实下载身份关联。现有模型40次请求总预算保留；预计新增第一轮12+第二轮3次模型请求，连旧场景仍在预算内。实施时以真实账本核对，不能为了循环失败取消预算或无限等候。
- 新增一个纯本地文件检查 helper 与窄测试文件；原两份5页检查仍执行，新场景显式传15。若离线 helper 错误，浏览器验收失败并保留输出，不能退回原搜词断言。
- 正常沿用现有 `business_browser_acceptance.py --suites ai_documents --iterations 1`。独立启动时设置正确 TEST5_E2E_PYTHON；无需改产品代码/门禁预算。若产品出现错误，保留本轮失败并另行修复，不能让桩绕过真实工具。

优先编写以下新负例，以下均为**待执行**：

| 编号 | 反例 | 必須失败的断言 |
| --- | --- | --- |
| CB1 | PPT包有15个slide文件，但presentation只引用14页，或重复引用同一页 | 实际有序唯一页数必须15；不能仅数文件/标签 |
| CB2 | 第13正文为空或只有notes，标题/元数据宣称15页 | 正文与实际主题核验失败，不能当完整稿 |
| CB3 | Word有两个孤立media，正文没引用；或两处都引用同一饼图 | 缺少指定大屏真实摘要/正文可达引用 |
| CB4 | Excel仅来源页/孤立sheet含3和5，真正结果表值错列/漏行/文本/公式替代 | 完整单元格/类型/表关系核验失败 |
| CB5 | 模型在继续请求里丢失draft/Q回执，夹具全局缓存仍有 | 桩拒绝偷用旁路缓存，不会虚构连续上下文 |
| CB6 | 继续分支故意重复调用query，或打开大屏触发自动采集 | 实际GET增量与工具数断言失败，不能把重复计数忽略 |
| CB7 | 第一轮 finalize缺13但平台显示completed，或所有工具失败均被统一白名单 | 必须partial+精确错误与缺项；其它错误仍失败 |
| CB8 | 下载另一个同扩展名旧文件/中断文件，文件存在但不是本次artifact | 下载GUID/URL/ID/完成/SHA关联失败 |

再执行一轮实际Edge场景。先拿本轮真实结果再决定后续回归；不启动100轮，不扩大900秒单套观察预算来掩盖重复/挂起，不把测试观察预算当作产品长任务时限。

## 9. 本次阅读范围与结论边界

主审完整分段阅读：`full_browser_ai_documents_acceptance.js`（含同文件模型桩、selfTest、下载和两份5页场景）、`business_browser_acceptance.py`、`chart_renderer.py`、`query_spreadsheet.py`、`analytics/exports.py`、office-delivery 工具契约。

针对性阅读：browser_only_helpers 的来源创建/删除与响应捕获；real_platform_smoke 的 Host 隔离环境；builtin_provider 的查询/图表/Excel/Word/大屏/分阶段PPT声明及调用分派；query_dashboard 的查询来源、快照、复用与保存段；dashboard_image 的快照渲染/来源/输出段；presentation draft 的创建/写页/进度/final_spec缺页拒绝；resume 的原生Task历史调用校验；chat 的prompt任务注入/settle；chat-session 的工具/任务投影与chat-session-view的发送/下载渲染；新旧交付专项的对应段。

独立窄审由子代理只读复核下载/Office判定，额外查看 artifact_inspection 关系、可视图像和工作表解析，test_openwebui_delivery相关伪产物反例、requirements/锁文件及主树安装库目录；未执行导入或测试。部分大文件读取输出截断后只补读所需段，不宣称全面审查全部工具供应器或全部报告/演示代码。

| 关键文件 | 本次只读SHA256 |
| --- | --- |
| tests/ui/full_browser_ai_documents_acceptance.js | 17D2BDC77507019426E114454FB296F14973E89E76C504A4E7564FD1E2F6CC4A |
| tests/ui/browser_only_helpers.js | 6DEF3687E076C8BE6C925851F1A414D47EED668829E32A606745DE093AA29F9F |
| tests/dsh/business_browser_acceptance.py | B90541BD5AF4E8C9F5F7858C2B2F7491C6A81790438A6BC810231AAC37A71430 |
| tests/dsh/real_platform_smoke.py | 22B02809D505FA3301E768587A72C411395F2A6AFC3059F913135825F3C0B318 |
| backend/app/providers/tool/builtin_provider.py | DFC6F95CBB1A710306231E0A1A43AB5511D111C0443B5801E9D9C2989646CBF5 |
| backend/app/modules/chat_artifacts/chart_renderer.py | 276B9F33DF2DB047BE00724F933AE7471E11CDBE39DF9E84FAAAF01C722590E3 |
| backend/app/modules/chat_artifacts/query_dashboard.py | E3CC7561ACF343FD3010F98791C657E03BAA2838F1F90B9A581E0D5070BE6257 |
| backend/app/modules/chat_artifacts/dashboard_image.py | E3A42ADF7727DB6F6C2909CB92284CE312684D1E06BC457D34E562280593D40E |
| backend/app/modules/chat_artifacts/query_spreadsheet.py | F72FFA1E50A36E7443AE4A54C3E756E51ACCFF679FBD67E53FC0BA75CF266EFF |
| backend/app/modules/analytics/exports.py | 8364E6EAD09935F726C1E7F01AA50DAA56E6CAD8EBB3093271497BCEACEEE59E |
| backend/app/modules/presentation/draft.py | 029871420A45A6D4A65B4F02A756FC920EEEA5A43B4C76A90D00C96D980B1EF6 |
| backend/app/modules/presentation/tool_schema.py | 606A6A39A3D91D9EC9C5483D155C78B91BC15A550ECB26AA826E5F335C640606 |
| backend/app/modules/chat_artifacts/resume.py | 9254AAFD34F81E30F1AA629D16B0468732C1AA40223C91BAFB3D057E9C47FDA8 |
| backend/app/modules/chat_artifacts/artifact_inspection.py | D4374C7D3F4690368CB266589733E84B7628F89FF150BC7EDC7C3D52A026C9E1 |
| tests/backend/test_openwebui_query_delivery_chain.py | F9FA20871279E7C6E70859A86C192657FC79ECA720D97B5E2B7BE7434847111C |
| apps/web/assets/platform/chat-session.js | 26C786D4D71C517E7E7D9271E8D33ACCC7C42D58DDD76A0FD05FC18D0886C306 |
| apps/web/assets/platform/chat-session-view.js | D354FE18BAEBDABB5D4044B3938556304CE227E87E14F20CEA2917600CB5551F |

本计划覆盖的是受控模型按约定选择工具时，当前真实应用、持续任务、最终文件与浏览器交付的集成行为。即使后续全过，也不等于 Agnes/DeepSeek 等真实模型能稳定规划任意请求，不等于真实 `szh_new` 的巡检数据、SQL、统计口径或业务结论已经验证，更不能据此承诺所有长任务永远成功。
