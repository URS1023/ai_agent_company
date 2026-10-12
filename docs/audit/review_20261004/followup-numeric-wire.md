# 下一批只读审查：大整数传输、历史快照与图表原值

审查日期：2026-10-04。工作树：`major-capability-contracts`。本轮只读当前本地源码和已安装依赖，没有修改产品或测试，没有执行测试、业务数据库查询、真实模型请求或外部网络访问。以下为源码可达链路及待验证测试方案；不是新一轮运行时复现，也不计入批次十三通过数。

## 结论与优先级

1. **P1，确定存在编码缺口**：查询执行保留 Python 大整数，但对外仍编码为 JSON number。普通查询首先在浏览器解析时进入不安全浮点范围；DSH 工具更早在 Node Worker RPC 解码时进入该范围。此次统计文本格式化修复不能恢复已经丢掉的整数位。
2. **P1，聊天历史投影会再次写入宿主解析后的值**：Worker 初次持久化工具结果后，Node 将 `tool.completed` 回传，后端用该数据覆盖同调用的 Message 工具结果。原 QueryHistory / ToolCall 与 Message 投影可能因此出现差异。是否已有具体受影响历史，需要另行在授权范围内查验，不能从源码推定数量。
3. **P2，图表默认提示不是精确原文的可靠来源**：多个组件在绘图前 Number/parseFloat；未显式转换的 SmartChart 折线也会经现有 ECharts 默认 tooltip 数值格式化。最小修复应分离近似坐标和精确原值，不要求浮点图形坐标支持任意精度。
4. **现有保护不能误报为缺陷**：查询 XLSX 已对超过 15 位的 Python int 写文本，Decimal 字符串也写文本；HTML / Markdown / CSV 文件的服务器文本化不先转浮点。CSV 被 Excel 自动识别为数值是后续消费边界，应单独说明。

## 一、查询与历史的真实路径

`analytics/execution.py:27–40` 的 `json_value` 对有限 Decimal 返回 `str(value)`，对 `str/int/float/bool/None` 原样返回，对容器递归处理。`execute_read:234–236` 将每格送入此函数，并用 Python `json.dumps` 检查 8 MB 限额。列类型在编码前由原始样本推断，int 可标为 bigint。

因此源驱动返回 `9007199254740993` 这个 **Python int** 时，该层没有把它变成 `9007199254740992`；数字字面量仍可精确写入 JSON 文本。JSON 文本本身并非浮点格式，真正风险来自后续消费者如何解码。源驱动若早已给出 float，原始整数位可能在更早阶段丢失，不能由此函数恢复。

`queries.py:479–484,511–522` 由原行计算统计，构造 `QueryResult(data=rows)`，再 `model_dump(mode='json')` 存 `QueryHistory.result_json`。`schemas.py:238–261` 的 `data: list[dict[str, Any]]` 无大整数编码规则；ORM 字段是 `models.py:93` 的 JSON。`historical_result:634–644` 完成现有归属、数据源和 SnapshotAccess 校验后直接 `QueryResult.model_validate(record.result_json)`，不会把历史大整数转成字符串。

此处只能确认应用没有主动做 float 转换；本轮未连接任何实际持久化数据库，不能额外担保所有数据库方言、驱动、历史导入工具均未改变旧 JSON 数字。

普通 HTTP 的链路是：

`analytics/router.py:194–210,226` → FastAPI `Envelope[QueryResult]` → `business_worker/application.py:128–142` 收集原响应字节并 base64 包装 → `services/dsh_host/transport.mjs:20–24` 解码后直接写 HTTP 响应 → `api/request.ts:91–105` 的 Axios → 本机 Axios `lib/defaults/index.js:110–130` 的原生 `JSON.parse`。

**这里 Host 不解析嵌套业务响应体**，不能把 HTTP 路径的第一舍入点错误定位到 Host。前端 QueryResult 类型是 `Record<string, unknown>[]`（`types.ts:16–23`）；`QueryResultPanel.vue:66–70` 直接给 ElementPlus 表格，由已解析值显示。列 dataType=bigint 只是元数据，没有据此重建原整数。

## 二、DSH 工具与再持久化

DSH 的 SQL 分支从 `builtin_provider.py:715–725` 进入 `execute_dsh_query`。`dsh_query_execution.py:321–326` 返回 `result.model_dump(mode='json')`，同轮缓存是其 deepcopy。`normalizer.normalize` 和 `ToolResult.to_dict` 只包装 data，没有数值编码。

链路如下：

1. `business_worker/tools.py:180–191` 保存 ToolCall 结果、完成步骤、保存 Message 工具部分后 commit，随后返回原 Python 结构。
2. `business_worker/protocol.py:31–35` 使用 `json.dumps(..., default=str)`。`default=str` 不处理 Python 原生 int，不能保护超安全整数。
3. `services/dsh_host/worker.mjs:59–80` 对整包执行原生 `JSON.parse(line)`。这就是 DSH 工具的明确不安全整数解码点。
4. `sessions.mjs:262,273–279` 将解析后结果作为工具结果和 `JSON.stringify` 文本交给 Agent；`spill.mjs` 也对既有结构/文本做 JSON 解析与投影，无法在丢精度后补回原值。
5. `sessions.mjs:275` 的 `tool.completed` 经 `publish:137–149` 再次发 `chat.event`；`chat.py:483–489` 根据该结果构造并替换同 call_id 的 Message 工具结果。故可能在 QueryHistory/ToolCall 原回执仍精确时，Message 历史投影已经变成宿主收到的近似数。

这不意味着原回执必定被覆盖：本路径明确覆盖的是 Message 工具投影，ToolCall 原回执的保存发生在 Node 解码前。历史修复应优先核对同一次调用的原回执，而非重新查询当前业务库冒充当时结果。

## 三、报表快照不会自动避开解码问题

两条主要快照生成路径都保留 Python 数据类型：

- `chat_artifacts/query_dashboard.py:104,119–144` 从 QueryHistory 读取 data，复制到 `widget.data` / `config.chartDataParsed`，并用 `json.dumps` 写 `config.chartData`；模板版 `template_query_snapshot.py:263–273` 相同。
- `reports/generation.py:1186–1257` 直接用 `query_result.data` 填 dataset、series.data 或 point.value；`1888–1896` 同时保存上述三种快照表达。`reports/query_delivery.py:118–123` 是重新执行原查询的另一条交付路径，`149–153` 也保存相同表达，不能把它描述成全部沿用旧快照。

`reports/service.py:29–49` 把完整 content JSON 编成 **字符串**。外层 HTTP JSON 解析暂不会解析 content 内数字；随后 `ReportDesigner.vue:667` / `jimuReportAdapter.js:222` 的 `JSON.parse(content)` 才读取内层 number。若只存 chartData 字符串，`JimuChart.vue:63` 解析时同样有问题；不能以“已包一层字符串”作为精度保证。

已有快照卡片 `snapshotWidgetLayout.js:22–28` 的 `exactSnapshotNumber` 能按字符串格式化高精度值；`JimuWidgetRenderer.vue:359–373` 在 snapshotBinding 分支复用它。但已解码为不安全 number 的值再 `String(value)`，仍只能得到近似值。普通 AI 视觉卡片另有 Number/toLocaleString 分支，本报告不据此扩大本次修复范围。

还有必须保留的验证边界：`business_worker/dashboard_deliveries.py:82–90` 比较 `rows != query.result_json.data`；`reports/source_reports.py:53–72` 对已登记导入源做严格 payload 比较。因此不能只改某一侧类型，不能将严格比较改成 float 比较，也不能为方便迁移取消数据源授权或原文一致性检查。

## 四、图表原值和 tooltip 现状

| 路径 | 绘图前数值处理 | 原值提示现状 |
|---|---|---|
| SmartChart 柱图、饼图 | `394–398`、`466–470` Number 后按分类累加 | 系列仅剩浮点合计，默认 tooltip / `{c}` 使用已转换值，没有精确合计或源值旁路 |
| SmartChart 散点 | `529–542` 两轴 Number | 自定义 tooltip 读取 params.value，即已转换坐标 |
| SmartChart 折线、面积 | `326–330` 系列保留输入 row 值 | raw series 字符串仍可利用，但默认 tooltip 不能保证原样，见下方依赖链 |
| snapshotChartOption | `8` Number，`54–61` tooltip 默认、series.data=values | 未保存 rawValue，也无精确 formatter；坐标轴“万”短写另属近似刻度 |
| DataAnalysisDashboard | `407–408` parseFloat 分类合计；`516` Number 明细序列；`574–580` Number 雷达统计 | 对应 tooltip 未绑定原值。右侧饼图是返回样本分类计数（`459–468`），不能把它错报为金额转换丢精度 |
| 服务端 PNG | `chart_renderer.py:73–84,161` Decimal 后转 float；饼图图例 `204` 使用 float 的 `:g` | 坐标近似是正常绘图需求，但目前图例也复用近似值；需保留独立精确标签 |

已只读核对当前安装的 ECharts 源码：`seriesFormatTooltip.js:defaultSeriesFormatTooltip` 先取 `series.getRawValue` / `retrieveRawValue`；`tooltipMarkup.js:179–191` 默认走 `makeValueReadable`；`util/format.js:100–101` 对非 ordinal 值走 `numericToNumber`；`util/number.js:517–521` 使用 parseFloat。因此不能仅凭 line.data 保留字符串就断言默认悬浮框精确。此结论来自本机依赖实现，没有运行新浏览器测试。

`safeContent.ts:43–67` 保留可信前端 formatter，同时强制 richText，未做数值格式化。未来原值 tooltip 应由可信前端代码生成，继续 richText / 安全文本，不能为精度回显放开用户提供的函数或 HTML。

## 五、导出消费与已存在的测试入口

`analytics/exports.py:70–78` 已将字符串、容器及超过 15 位的 Python int 写成 XLSX 文本；小整数维持数值单元格。`query_spreadsheet.py:38–40` 复用此服务，模型不重抄单元格。`test_analytics_dsh_and_export_contract.py:72–81` 已有 19 位整数文本与 42 数值的断言，本轮仅阅读，没有重跑。

JSON 导出 `exports.py:51` 仍包含原生大 int 的 JSON 数字，文件字面量可精确，但 JS 消费者仍会遇到同一解码边界。HTML / Markdown 调用 `_text` 的 str；CSV 也输出精确文本字符，不过 Excel 自动数值推断可能再次损失位数，CSV 本身无单元格类型信息。CSV 现有公式防护必须保留，不能用 `=...` 公式伪装精确字符串。

Word/PPT 及所有模板专项导出未逐一通读，不宣称其全链路已验证。PNG 核对到明确的 float / 图例格式化位置；没有生成新图片。

## 六、建议的最小编码与迁移边界

### 先修业务结果编码，再修坐标与原值分离

1. 新业务结果采用明确契约：**Python int 若绝对值大于 9007199254740991，转十进制字符串；边界内 int 保持 number；bool 先于 int；Decimal 延续字符串**。在 `execution.json_value` 的业务单元格边界递归执行，保留 columns.dataType=bigint、null 和结构；序列化后再计算结果字节上限。不要把整站版本号、像素、RPC 控制字段一律字符串化。
2. 只有执行入口还不够。历史 QueryResult 返回、查询工具旧回执 replay、旧 report content/内部 chartData 字符串均有旁路，需复用同一业务数值编码 helper，限于明确的结果数据位置。旧 ToolCall 的缓存直接返回路径也要覆盖，不能因此重执行工具或查询。
3. HTTP QueryResult 与 DSH 应在**第一次 JavaScript JSON.parse 之前**得到安全表达。不要在 Node/浏览器 Number 已产生之后执行 String，不靠普通 JSON.parse reviver 倒推原字面量，不给数字后加 n 生成非法 JSON。
4. 每个绘图点保留 exactText / rawValue 与近似 coordinate 分离；可信 tooltip 读取 exactText。有分类聚合时必须保留精确聚合结果（服务器按 Decimal 或本地明确的十进制实现），不能将浮点合计转字符串冒充精确值。极端指数/非有限坐标应明确标记不可绘制并保留表格，不偷偷填 0。只显示原值 tooltip 不能解决前端浮点聚合本身。
5. 若旧结果只有 float 或已由 JS 保存的近似 number，标记已知精度不确定，不能凭末尾 0 判断原值。当前 `decimalDisplay` 的“约”边界可复用，但它不是编码修复。

### 历史处理策略

- 优先做授权后的只读兼容表示；保留原记录与审计，不默认批量重写业务结果。先核对 QueryHistory / ToolCall 原 JSON 是否仍含完整原整数，只有已知类型的整数才做无损转换。
- Message 中已被 Node 往返改写的工具部分，可按原 run/call/query 身份与当前资源权限从原 ToolCall/QueryHistory 重建新的展示投影；不要替换模型历史文字为“从未发生过”，不要自动重跑业务库取得不同时间数据。
- Report 已在浏览器编辑保存后，`data/chartDataParsed/series` 可能只剩近似值。优先以已授权且身份一致的原 QueryHistory、固定投影列和原版本生成新的修订；无法证明时保持旧证据并说明精度未知。禁止以“最新查询结果”覆盖历史。
- 兼容需要同步处理 `widget.data`、`config.chartDataParsed`、内部 `chartData` JSON、dataset/series 中的关联业务值，避免一个页面从不同字段读出不同数。不能全字符串替换 content 中数字，因为布局参数及普通文本不属于数值编码范围。
- `dashboard_deliveries` 和导入源严格证明应在编码变换前完成，或使用明确版本与字段类型约束的共同无损规范表示后比较；禁止浮点模糊相等、忽略数据字段、绕过 source_reports 的登记证明。为 UI 无修改后保存仍有效设计专门回归。

## 七、下一批必须新增的兼容测试（本轮未执行）

1. `test_analytics.py` / 新 numeric-wire 专项：临时 SQLite 的有符号 64 位整数，断言原始 HTTP 响应 JSON token 类型与 Python 值，覆盖 ±(2^53−1)、±2^53、±(2^53+1)、±64 位极值、0、bool/null/嵌套结构。超 SQLite 整数范围可用纯 helper/模型夹具，不连接业务库。
2. 历史 QueryResult：直接保存旧 int JSON，调用实际历史 HTTP；编码返回但原记录不改；数据源/字段撤权照旧拒绝。不要仅用 Python TestClient.json() 判定跨语言精度安全，它的 int 本就精确。
3. DSH：实际 JsonRpc.write → BusinessWorker.receive →工具render → tool.completed → Message 落地；Node 断言完整字符串、类型、同调用原回执与消息一致；再测缓存 replay 不重执行。入口可复用 `services/dsh_host/tests/worker.test.mjs` 和 `tests/backend/test_business_worker.py`。
4. 快照：旧/新查询各创建普通与模板大屏，读回content并由 JS 解析，三份 data 一致；随后无修改保存、revision、交付校验均通过，恶意改一位仍失败。入口 `test_openwebui_query_dashboard.py`、`test_template_query_snapshot.py`、`test_analytics_snapshot_authorization.py`，不放松权限用例。
5. 图表：在 `snapshot_chart_option.test.js` 和新真实 ECharts 组件测试中分开断言近似坐标、精确 tooltip、分类精确合计；大正负整数、长小数、极小数、科学计数、重复分类、null/不可绘制值均覆盖；实际触发 hover 而非仅检查 series raw 字符串。继续检查 richText 和恶意标签转义。
6. 既有 XLSX 文本/小整数数值/宋体/公式防护不回归；CSV字节、JSON跨JS解析、HTML/Markdown原文字串分别断言。需要 PNG 标签时对规范化 spec 中 exactText 与绘制value双轨断言，再做限定本机渲染检查。

## 实际阅读范围与指纹

下面列出本轮实际全文或定点阅读的文件。标为定点的文件不代表整文件审计；文件指纹对应记录时的当前工作树，不代表这些文件都由本代理修改。
| 文件 | 实际阅读范围 | SHA256 |
|---|---|---|
| backend/app/modules/analytics/execution.py | 全文 | DB289962DB2C50997928A12E1D6B09D9908BB185DE5A99EE827B3CD239B939A5 |
| backend/app/modules/analytics/schemas.py | 定点 238–261 | 7147FF1983A2BCBFAAA69381335557E3341F49B2C5FF58287BECC921350198DC |
| backend/app/modules/analytics/queries.py | 定点 386–430、464–529、621–644 | 0BC57BC6463043AB366C2E2D0666C41536A8F2B44E6377142F82DD9DBA905064 |
| backend/app/modules/analytics/models.py | 定点 77–99 | BCB0A306C62E9DBF9D75459ACE70E79ED690055DDDF7BFD2DA7BDCAD20DB59BA |
| backend/app/modules/analytics/router.py | 定点 194–230 | B5087B8FDE3DFC904F3CC06D1084CAC6F4DFB2C68D53291F666E7CD941ECCCC8 |
| backend/app/modules/analytics/dsh_query_execution.py | 定点 execute_dsh_query 287–330 | 0D564ECEF2AA7AF85B7C0EC516A400C6117639D2F8BB229D10DE9A386B727D34 |
| backend/app/modules/analytics/exports.py | 全文 | 6465DFC348FF8894D3A89B0C4C8EB0116271E91E1C96DEA57397D25FD70D00CB |
| backend/app/providers/tool/builtin_provider.py | 定点 705–730、769–797 | C5986E694DA9A624B5587A387B4EB0EF45E0AEC746E33B99A0340CD6DAED9EF0 |
| backend/app/providers/tool/base.py | 全文 | F328F4D94637B2FE6C964B302678FBF9642B63C0B989CACBCFFD6132FB345B4F |
| backend/app/providers/tool/normalizer.py | 全文 | 159B248FEAC97CDDC83D79CB28417F6897CCDB98C6265C068D74DE72AEBD384A |
| backend/app/business_worker/tools.py | 定点 166–191 | 5530F66D710B693A26CCDFA920AD49F436D265A89B9F2384DCCF31D63E83D46D |
| backend/app/business_worker/chat.py | 定点 457–496 | 472082B8E38B735F9E0616EADEEA4E099BA1C8E3B56AB8C45D3A0FDB74EB4EF9 |
| backend/app/business_worker/protocol.py | 定点 1–45 | 8F59FBF4BAC756D340E438A9F5ECAD1F97426D3834692BE930E19E375EC1F3D4 |
| backend/app/business_worker/application.py | 定点 128–146 | 189FA665417DB3B701BF52A8F912573CDE5D0839817C66A3A9E0022AC02EEB4F |
| backend/app/business_worker/server.py | 全文 | F2D389753C4DA2534B078A59CB2FC239661316B9DC920034330356E50BE4022A |
| backend/app/business_worker/__main__.py | 全文 | 027371E88554C9D3E0893E8BEBA1440C97B42292B9350C67AE8EC358508F03D5 |
| backend/app/business_worker/dashboard_deliveries.py | 定点 61–100 | 59E3B65922EA87693892762AE960F4D9FD6EA898658D2CF74C3B9AB34E57BCC0 |
| services/dsh_host/worker.mjs | 全文 | 35830BC723575836EAE151FB54153D8B7F7A785D7042D89436F4591A85D87201 |
| services/dsh_host/sessions.mjs | 定点 129–150、239–284 | E12BB5F6B51DB7CFEB2CCF3E706BBDB9027901C780861B3B21AD3D4EEA5CCDA3 |
| services/dsh_host/spill.mjs | 检索 JSON 解析与序列化调用点，未通读 | 0F7BF02068B32D33ACDF0C2CBBB7292B87862B70C20843F6650827F36ED4EB13 |
| services/dsh_host/transport.mjs | 全文 | 4F415682AC8E1165AC9F0AB18C2FF4EE5A7BF19AD7E75BBCB96011965AD38E75 |
| backend/app/modules/reports/service.py | 定点 29–54 | CD2F1758B9603C2441BB93F0828AFD920D34456CAA21C73D7E2F689804C43912 |
| backend/app/modules/reports/generation.py | 定点 1181–1225、1231–1260、1887–1897 | 2C2AD58A0BF289FC713F523B46F87EFD7C66B40AC7E9651F06D920562FD3940F |
| backend/app/modules/reports/query_delivery.py | 定点 111–170 | DF94B327AAE3915F60AE009B1F49CFCD28D42BE3C8A77F63C4977C1A27E0D827 |
| backend/app/modules/reports/source_reports.py | 定点 50–73 | C5C8B4D7E00588345F3CA4EB21CC2C28F0008991EBE8C5D9CC3A7A80C753AFED |
| backend/app/modules/chat_artifacts/query_dashboard.py | 定点 96–170、260–283；其余仅检索 | E3CC7561ACF343FD3010F98791C657E03BAA2838F1F90B9A581E0D5070BE6257 |
| backend/app/modules/chat_artifacts/template_query_snapshot.py | 定点 241–277、数值识别调用检索 | 4D4A10BB4C7087B9C1C1699EBBBC64148BDF3EA9C1CEA7B4C46C3146EF6F54FD |
| backend/app/modules/chat_artifacts/query_spreadsheet.py | 全文 | F72FFA1E50A36E7443AE4A54C3E756E51ACCFF679FBD67E53FC0BA75CF266EFF |
| backend/app/modules/chat_artifacts/chart_renderer.py | 定点 73–87、151–177、186–224 | 276B9F33DF2DB047BE00724F933AE7471E11CDBE39DF9E84FAAAF01C722590E3 |
| apps/analytics/src/components/business/QueryResultPanel.vue | 全文 | 6B6A8294DDD8FCF742593FD131F6A27086C958BBA9745427CD219157FA10C216 |
| apps/analytics/src/components/SmartChart.vue | 定点 1–611，样式未通读 | 919D311569AC7C9024981B1C178B28D93F99A213789CF1D929CC51377BE5ADB0 |
| apps/analytics/src/components/DataAnalysisDashboard.vue | 定点 400–426、455–482、513–531、571–609 | 6F73452AA351AE4CE6149BFA8D9042F0EFD3B76737A770B63E9C5848F83227F2 |
| apps/analytics/src/components/jimu-screen/JimuChart.vue | 定点 51–81、206–246 | 5910F507E88A3D69464F3F5FDF223EB7FA3C1F5435F27631BF9B1220F81067BB |
| apps/analytics/src/components/jimu-screen/JimuWidgetRenderer.vue | 定点 354–396、表格消费位置检索 | B1F1882C63AA5BB83B3865E8CA3729C1223D8CC2EAB0B8FBF1E0B4FD18F399E7 |
| apps/analytics/src/components/ReportDesigner.vue | 定点 657–675 | DF1CE2C636199726FEA8B7D3DB3F6B670CB4254C760A3CD2662BA77E0E8FFA7A |
| apps/analytics/src/utils/jimuReportAdapter.js | 定点 215–229 | 895098B834FC8D303FC488850B671568BEC773C09F48C20980425B8ED74AC218 |
| apps/analytics/src/utils/snapshotChartOption.js | 全文 | FD31497627B36E858DB127D488FA2254DED0101BC2C38AE2AFBF2F8371BF561E |
| apps/analytics/src/utils/snapshotWidgetLayout.js | 全文 | B9DB47243E3DEB850DB40053A02D7836ADFC68A329F79C05BA4C9C8F2B2DDD00 |
| apps/analytics/src/utils/safeContent.ts | 全文 | D4508B86FACCB5A6C2DA66E4F315A4D493DC83CB23A60EBC41B3B99F1053B31A |
| apps/analytics/src/api/request.ts | 定点 1–111，并检索全src的transformResponse/parseReviver | F9DAEFC805E7FC5FD57FE34935048DDB9EFA7364B6FE76564796F9A57C3ABD94 |
| apps/analytics/src/types.ts | 定点 16–42 | 571E848279185C6D3C7541495E43EFB71CED0FE0616A5EF3DE0325626D2CD507 |
| tests/ui/snapshot_chart_option.test.js | 定点 1–95 | 5D91EE0C31EADCDD6E11A152BDAD5E8E636FD0ACB5BFB26CC351EE7DFE448D10 |
| tests/backend/test_analytics_dsh_and_export_contract.py | 定点 68–84 | 62E3B528C9250BC816CF41ACF7A8AE65A26CBDBBE3DA9AEBE39FC8F8F4C71170 |
| apps/analytics/node_modules/axios/lib/defaults/index.js | 已安装依赖，定点 110–137 | F1337A16E0FA11D1F431804F7E30CCDC4477D6807EA68BBE3B3ADADDA0290874 |
| apps/analytics/node_modules/echarts/lib/component/tooltip/seriesFormatTooltip.js | 已安装依赖，全文 | 07AB4BC57D4E7D86A4A2117F1F4B8F69268CCE49902882EDCB5B9ED2EE4F64C4 |
| apps/analytics/node_modules/echarts/lib/component/tooltip/tooltipMarkup.js | 已安装依赖，定点格式化调用 179–191 | 3BB32E5DCE33C670C5DE87927ED29D47D76D717670CCF48FAB6C1C9DA9AEF509 |
| apps/analytics/node_modules/echarts/lib/util/format.js | 已安装依赖，定点 77–102 | B469AEACD010634DA3CB4B2625331F51F7EE133A0F5259CD6D81698E6EAF9712 |
| apps/analytics/node_modules/echarts/lib/util/number.js | 已安装依赖，定点 517–527 | EDA989FB0E55AE7DE28F9EAB2B16D8EB3313C9AFD4233CD425E0BFB5361FFDCD |
