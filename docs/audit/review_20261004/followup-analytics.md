# 分析查询、快照复用与导出链路只读审查（下一批）

审查日期：2026-10-04。独立树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-capability-contracts`。基准提交：`077e6e7f2f10f32e08ecb64a7c0b885db6b8ced7`。

**本文件属于后续审查，不计入本批已经通过的门禁范围。** 没有改动产品或测试源码，没有构建、启动服务、打开浏览器、执行测试、连接业务库或调用模型。以下“确认”指按实际源码调用链/算式确认，均未进行动态复现；给出的回归步骤是后续隔离验收建议，不能作为已经通过的结果。

本轮聚焦 `analytics` 查询授权/执行/历史/导出，以及直接上下游的 DSH SQL、查询快照大屏、报表生成幂等返回。全文阅读与片段阅读、SHA256 在末尾分开记录。`finance_semantics.py`、语义召回大文件、连接池和权限缓存实现没有全文审查，本文件不宣称整个分析域或全部跨租户链路安全。

## 结论与优先级

| 编号 | 级别 | 静态确认的问题 | 影响路径 |
| --- | --- | --- | --- |
| ANA-N01 | P1 | 缩窄表/字段授权后，历史结果与导出、已保存报表内容仍可包含被撤回字段 | 分析历史 GET、分析导出 POST、报表列表/详情/修订 |
| ANA-N02 | P1 | 自动解析数据源的报表生成幂等重放，仅核对本次新解析的数据源，可能回放已撤权旧源的结果 | `/reports/{screen,widget}/ai-generate` 相同 sessionId 重试 |
| ANA-N03 | P1 | DSH 主聊天显式 SQL 不执行现有设备有效记录/巡检完成口径校验 | `query_business_data` → DSH → SQL 执行 |
| ANA-N04 | P2 | 线性预测遗漏自变量中心偏移，最简单的等差数列也预测错误 | 分析结果 enhancedAnalysis.projection |
| ANA-N05 | P2 | XLSX 将大整数写为数值单元格，安装的 openpyxl 会按 16 位有效数字输出，原值静默改变 | BIGINT 标识/数量导出 |
| ANA-N06 | P2 | CSV 丢失截断标记；关闭分析时 HTML/Markdown 同样无独立截断说明 | 限量结果导出后脱离页面流转 |
| ANA-N07 | P2 | Decimal 运算与 normalize 使用默认精度上下文，超过 28 位的合计/展示可能静默舍入 | 高精度 DECIMAL/大整数统计 |

P1 中的 N01、N02 已及时通知根代理。未发现本轮已阅读 DSH 快照路径存在“仅按同名直接复用”的实现；旧兼容生成路径存在不同的约束，见后文，不混为同一个执行器。

## ANA-N01：字段授权撤回后，旧结果仍可经历史、导出和报表读取

**证据链：**

- `analytics/metadata.py:14-49` 明确 `allowedTables` / `allowedColumns` 是授权快照，字段过滤到空会移除整张表；`sources.py:235-256` 修改配置递增版本。
- 新执行的 `queries.py:452-468` 用当前授权快照校验 SQL；`metadata.py:170-189` 只使用版本一致的元数据缓存。
- 旧结果入口 `queries.py:605-613` 只对 QueryHistory 和 DataSource 调用 `owned_record`，验证成功状态后直接返回原 `result_json`；没有按当前字段范围校验原 SQL，也没有源授权版本对照。
- `exports.py:45` 所有格式均直接复用 `historical_result`。
- 报表的 `query_dashboard.py:134-149` 会把原始行复制进 `data`、`chartDataParsed` 和 `chartData`。`reports/service.py:39-54`、`:64-73`、`:247-269` 读取详情/列表/修订时仅验证源资源可见性，随后完整序列化这些副本。

**触发与后果：** 用户原先有 `sales(id, amount, note)` 的权限并保存查询/大屏；管理员将该源收窄为 `sales(id)`，但保留源查看权限。新 SQL 不能读 amount/note；旧查询 GET、导出和大屏副本仍可返回原值。这是“源仍可见但表/字段已经撤权”，不是跨租户 ID 枚举，也不是停用数据源本身。

**设计边界：** `test_analytics.py:384-392` 明确要求停用后可追溯历史，因此不能粗暴把所有版本变化/停用都当作撤权。应区分连接、启用状态变化和数据授权范围变化；历史读取必须对当下仍授权的范围做判断。`chart_renderer.py:58-66` 的新交付已经使用源版本防护，但它不能保护普通分析导出和已有报表读取。

**现有测试缺口：** `test_analytics.py:266-276` 验证新 SQL 的列限制；`:318-328` 验证另一角色完全看不到数据源；`test_reports.py:548-563` 验证完全移除源查看权限。没有覆盖“资源仍可见，仅缩窄表/列”的历史读取/导出/已保存大屏路径。

**建议隔离回归：** 保存包含受限列的成功结果和报表 → 收窄表/列 → 用同一普通账号分别 GET 历史、导出 JSON/XLSX、查看报表列表/详情/修订。确保已撤回的列和值均不可返回，同时保留未缩窄授权的停用历史回归。不要连接真实库。

## ANA-N02：省略数据源的幂等请求可回放已撤权源的历史数据

**证据链：**

- `reports/generation.py:976-1010` 支持没有显式 datasourceId(s) 时选取当前唯一可用源。
- `:1045-1049` 的 `_generation_request_hash` 只纳入原始 payload 与 kind，没有纳入解析后源身份/权限快照。
- `:1293` 校验本次解析源，随后 `:1312-1321` 对 completed claim 直接返回旧 `result_json`。
- `common/idempotency.py:96-109` 只以工作区、用户、scope、key 和 request_hash 匹配；作为通用设施，它并不验证结果中数据源的当前权限。
- 与之不同，`reports/generation.py:2027-2036` 的普通生成记录 GET 会遍历旧请求及旧结果的全部来源重新鉴权；POST 重放未走此保护。旧版本 `_existing_generation` 的 `:1075-1078` 也直接返回已保存结果。

**具体条件：** 第一次同一用户省略 datasourceId(s)，系统唯一可用源为 A，生成成功并保存 sessionId=S；之后撤销该用户的 A 访问，另一个源 B 成为其唯一可用源。用户带 S 和相同原始 payload 再次 POST：`_resolve_sources` 验的是 B，请求 hash 没变，却直接返回旧 A 的 widgets/data。这既是错范围复用，也会绕过旧源完整撤权后的读取限制。跨工作区/跨用户因 claim 条件过滤不在本项确认范围。

**为什么不是正常幂等：** 幂等可返回同一结果，但必须复核同一历史结果的当前授权；不能用 B 的权限证明 A 的结果仍可读。无需重新执行查询也能先验证旧 result 的来源。

**现有测试缺口：** `test_reports.py:1106-1117` 验证撤权后的生成记录 GET 被拒；它使用显式源，且未重发同一 sessionId 的 POST。后续应补“省略源、唯一源由 A 变 B、原 A 已撤权”与“显式源保持、仅列授权收窄”的回放测试，确认无模型/数据库调用且不返回旧值。

## ANA-N03：DSH 提交 SQL 绕过已经实现的巡检口径校验

**证据链：**

- `providers/tool/builtin_provider.py:681-725` 当前 DSH 对话明确分流至 `execute_dsh_query`。
- `analytics/dsh_query_execution.py:305-322` 先做 AST 与原始日期/部门范围校验，再以 `QueryInput(mode="sql")` 调用共享执行层。
- `analytics/queries.py:300-303` 是 `validate_inspection_aggregation` / `validate_device_active_records` 的唯一生产调用点，位于 `natural_language_sql` 内；共享 `_execute_query_with_source` 仅在 `payload.mode == "natural_language"` 时调用它。
- 共享 SQL 执行处 `queries.py:457-461` 已接入财务语义校验，却没有接入这两条设备语义规则。`inspection_semantics.py:52-104` 原本可拒绝用周期编码 c_recordType 冒充完成状态、用 COALESCE 空标识统计已巡检、未删除请求漏掉严格 isDeleted=0。

**触发与后果：** 原始用户问“统计有效设备中已巡检的设备”，DSH 提交字段均存在但业务口径错误的 SQL（例如按 `c_recordType IS NOT NULL` 算完成）。该语句无需日期/部门条件，因而 `validate_scope` 可给出 `no_explicit_date_or_department`，随后 SQL 路径跳过巡检校验而被执行。结果 SQL 合法，业务结论却错误。

**边界：** 这不是说 DSH 没有鉴权，也不是所有巡检请求必定错误；是旧路径已拥有的确定性保护没有覆盖新主路径。巡检校验本身也并非通用业务语义证明，修复接入后仍需真实业务口径回归。

**现有测试缺口：** `test_inspection_sql_semantics.py:18-25` 直接调用校验函数，只能证明规则可拒绝该 SQL，不能证明 DSH 实际会调用。其后两条测试走自然语言旧路径。`test_dsh_query_execution.py` 的函数定位显示日期/部门、财务验证与同轮复用覆盖，但本轮未全文阅读，不宣称其全部断言。

**建议：** 把与查询模式无关的已知业务校验接到共享执行前，使用服务端 user_intent 的原始要求/当前子任务组合，保留人工明确 SQL 的既有契约；新增 DSH 路径拒绝上述两类统计的离线回归，确认执行计数为零，而非只测函数。

## ANA-N04：线性预测公式偏高/偏低

`analytics/statistics.py:189-197` 计算 `x_mean=(n-1)/2`、`y_mean` 和中心化斜率。拟合值正确使用 `y_mean + slope*(index-x_mean)`；预测却写为 `y_mean + slope*n`，漏掉 `-x_mean`。

静态代入四个等间隔日期的值 `[1,2,3,4]`：`x_mean=1.5, y_mean=2.5, slope=1`；下一点应为 `2.5 + (4-1.5)=5`，现式为 `6.5`。下降序列同样向错误方向偏移；标注“谨慎预测”不能使错误算式成为正确结果。

`test_analytics.py:519-538` 只断言时间维度、趋势、异常数、置信等级与提示文案，没有断言预测数值。建议补严格等差上升/下降/常数序列的下一点，并单独规定不等间隔时间序列的语义，不扩大为正式业务预测。

## ANA-N05：大整数 XLSX 导出丢精度

`analytics/execution.py:25-40` 对 Decimal 返回字符串、对 int 保持整数；`analytics/exports.py:64-71` 仅将 str/dict/list 设为文本，int 原样赋给数值单元格。

为避免仅凭库常识推断，本轮只读了当前本机 venv 内已安装 openpyxl：`openpyxl/cell/_writer.py:81-84,127-130` 数值 XML 使用 `safe_string`；`openpyxl/compat/strings.py:12-18` 使用 `"%.16g" % value`。因此超过该精度的大整数，例如 `1234567890123456789`，无法原样进入 XLSX 数值内容。该源值属于合法 64 位整数范围，设备/单据业务 ID 可真实触发。

后果：查询历史里的整数仍准确，下载表格里的标识/金额却可能改变；没有报错或降级说明。`test_analytics.py:299-315` 只验证公式防护、HTML 转义和来源页，没有大整数逐值往返断言。

建议对不能由 Excel 精确表示的整数明确使用文本单元格并保留数据类型说明；后续静态样例导出/读取和 ZIP XML 检查即可验证，不必接业务库。本轮没有实际创建 XLSX。

## ANA-N06：部分导出格式缺少独立范围标记

- CSV 分支 `analytics/exports.py:51-56` 仅写列名与行，不写 queryId、截断状态、行数范围或配套说明。
- HTML/Markdown `:87-106` 只依赖 `result.analysis` 来显示范围。`queries.py:494-505` 在 `needAnalysis=False` 时明确返回空 analysis；当前 DSH `dsh_query_execution.py:321-322` 正是这个模式。
- 因而 DSH 成功查询返回 `truncated=True` 时，CSV 以及其 HTML/Markdown 导出会只展示限量行，读者脱离原页面后没有独立“仅样本”提示。JSON 保留完整契约，XLSX 的来源页 `exports.py:77-83` 有截断字段，不属于此问题。

这不等于导出代码主动写了“全量”，而是可追溯范围在文件交付环节丢失。建议根据格式固定写范围元信息或提供同一下载包中的说明，不以模型分析文案是否存在为前提；测试 `needAnalysis=False + truncated=True` 和零行、未截断各分支。

## ANA-N07：高精度统计并非无损 Decimal 运算

`statistics.py:37-49` 通过十进制字符串构造 Decimal，避免了 float 起始误差；但 `:311-313` 的 `sum(numbers, Decimal(0))`/均值与 `:52-57` 的 `normalize()` 都在默认 Decimal 上下文内运算。该模块没有 `localcontext`，本轮对 `backend/app` 的 `getcontext/localcontext/prec=` 检索仅定位到别的模块使用局部上下文，没有为统计模块设置高精度的代码。

默认 28 位有效数字不足以无损处理例如 30 位整数 `123456789012345678901234567890`，即便只有一个值，`0 + value` 也受上下文精度限制；更高精度 DECIMAL 或多行大整数求和同样会舍入。原始 result.data 不变，但 statistics.sum / 后续展示可能不相等，且没有舍入口径说明。`test_analytics.py:502-505` 的精度测试只覆盖 0.1+0.2。

建议为精确总和按输入最大位数与行数预算使用局部上下文；均值/预测等本就可能无限展开的量应明确舍入规则；不要用受上下文影响的 normalize 作为“仅格式化”而不说明。后续纯内存测试即可复核 28 位边界、正负抵消和超长小数，本轮未执行。

## 已确认存在的保护与排除项

1. **没有把同名查询或大屏作为 DSH 复用键。** `dsh_query_execution.py:315-325` 的查询缓存含工作区、用户、run、source/version、原始请求、前轮要求、当前子任务、规范化 SQL 与 max_rows，只缓存未截断且有界结果。`query_dashboard.py:188-229,259-339` 创建快照用 query_ids+title+template/bindings 请求指纹、行内容指纹和本 run 检索；报表改动/删除也会拒绝无声复用。
2. `chart_renderer.py:34-70` 对生成新交付重新获取当前身份，核对用户/工作区、成功回执、本轮/明确续作、选择数据源、源版本；普通历史导出没有这一层，正是 N01 的区别。
3. `query_intent.py:43-59,73-109` 仅加载当前工作区当前用户的会话及已保存原话，不使用助手结论代替用户范围；源变化可中止追问链。`common/owned_records.py:60-113` 对显式查询使用 workspace、owner/资源授权条件，不是只按传入 ID 读取。
4. `sql_safety.py:116-288` 对只读形态、危险操作/函数、授权表列、参数、LIMIT/OFFSET 做 AST 校验后重建 SQL；`execution.py:220-244` 拒绝 SQL 重名列、以多取一行判定截断、8 MB 结果上限、保留业务和取消异常。未发现本轮读到的主查询失败路径用空列表冒充查询成功。
5. `queries.py:479-484` 不把 SQL 生成前模型猜测当作查询后观测结论；`test_query_analysis_provenance.py` 有针对性静态可见断言。本轮没有执行该测试。
6. `reports/data.py:24-95` 的查看者刷新/钻取使用已保存绑定并重新进入统一查询；钻取只接收已声明参数，不能直接提交任意 SQL。这个读取权限契约与任意查询权限不同，不能据此判成越权。
7. 停用后保留历史是显式测试合同（见 N01），不作为独立缺陷。SQL 自带更小 LIMIT 后 truncated=False 也只表示“原 SQL 结果已完整返回”，现统计文案明确不等于整库，不能直接计成截断谎报。
8. 旧 `business_ai/service.py:489-550` 存在“一条 run 只交付第一个大屏、后续参数改变仍复用”的合同，`test_business_ai.py:207-249` 专门验证该行为；但 `builtin_provider.py:873-889` 对 DSH 已明确改走查询快照管线，所以不把该旧行为当作当前 DSH 的同名错范围漏洞。旧业务页面请求键回放是否应允许目标报表删除后仍报成功，属于后续单独合同复核，本轮不扩展修复。

## 必须保留的未验证边界

- 所有问题需用临时平台库/受管 SQLite 或纯内存样例复现；本轮没有启动数据库或使用任一真实数据源，不把静态审查写成实际 HTTP 复现。
- 没有穷举 SQLGlot 的所有 AST 形态、CTE/联合查询或全部方言；白名单已读不代表证明解析器绝对不可绕过。
- 没有全文审查财务业务口径、库存固定查询、所有 HTTP 采集、模板快照映射、PPT/Word/图片资产下载权限，不能把本结论外推到这些路径。
- 没有验证运行中的权限缓存刷新、并发角色撤权和 SQL 执行中的取消传播；本轮只确认显式静态过滤与异常分支。已有测试及以前浏览器门禁结果不计为本轮的新证据。
- 模型 JSON 解析只吞 JSONDecodeError/TypeError 的极端脏输出、非法字符与超长数字等边界仍值得后续纯函数检查；本轮没有把这些尚未复现的分支写成新的确认漏洞。

## 实际阅读清单与哈希

以下由读取后的文件内容计算。行号指本报告基准提交下的一基行号；“检索定位”不等于全文阅读。所有相对路径均以本报告开头的独立树为根；venv 第三方文件单独给出绝对路径。正式归档时须保留这一范围，不把目录 inventory 命中数量当成审查覆盖率。

### 全文阅读（18 个项目文件）

| 路径 | 已读行 | SHA256 |
| --- | --- | --- |
| `backend/app/modules/analytics/queries.py` | 1–644 | `78FABFE3A4660235904E984ABE6C24DAAC7272ADD004B696ACA440B9350B49B0` |
| `backend/app/modules/analytics/exports.py` | 1–110 | `63873F70CA286F3C605A220F185D118D7B370A17A6D61F196CA3EA3F2320D26D` |
| `backend/app/modules/analytics/execution.py` | 1–244 | `DB289962DB2C50997928A12E1D6B09D9908BB185DE5A99EE827B3CD239B939A5` |
| `backend/app/modules/analytics/dsh_query_execution.py` | 1–330 | `0D564ECEF2AA7AF85B7C0EC516A400C6117639D2F8BB229D10DE9A386B727D34` |
| `backend/app/modules/analytics/metadata.py` | 1–220 | `B74059D8DC7FB6F7EB4D01740D429F80B330BE5774142A6F3CE7956DD0499C35` |
| `backend/app/modules/analytics/query_intent.py` | 1–109 | `9C288915003F19C7F21D8C743C30C6D7917695DF1875DACE7F74E390D5A5E763` |
| `backend/app/modules/analytics/sql_safety.py` | 1–288 | `BA8033AD00E2E3E98485A6F8AC9F28291D9704877375589C70DA5AD48F22DC4D` |
| `backend/app/modules/analytics/statistics.py` | 1–374 | `763FD4B2EC2E42774CB617D3917318005AAD1DFB8CC3AAC5883B951FBBCC6CB2` |
| `backend/app/modules/analytics/inspection_semantics.py` | 1–104 | `15B9CB649D06495F3D55DCBD58F700BBDCF79A4854012CAB7061CFE1F6B84487` |
| `backend/app/modules/analytics/query_templates.py` | 1–80 | `CC50C8C7938C0FEF5CCAB0495BAE9B14CDDB66670A00DB7FE7421E6A2A5D48B6` |
| `backend/app/modules/analytics/data_profile.py` | 1–148 | `735633753FB303D33C69C9FD3766D8E091C4EF8595EE96595CDF83051FE4CB79` |
| `backend/app/modules/chat_artifacts/query_dashboard.py` | 1–339 | `E3CC7561ACF343FD3010F98791C657E03BAA2838F1F90B9A581E0D5070BE6257` |
| `backend/app/modules/reports/query_delivery.py` | 1–184 | `DF94B327AAE3915F60AE009B1F49CFCD28D42BE3C8A77F63C4977C1A27E0D827` |
| `backend/app/modules/reports/data.py` | 1–95 | `A4422920A354B1DAC24EA41FD0852ADC3157C4F214F8C14B8E3716BA5A2C1721` |
| `backend/app/modules/reports/service.py` | 1–292 | `AF54550927CA8F9A3492D4A27C54966B66C055676BD610524FDC3D386A738DDD` |
| `backend/app/common/owned_records.py` | 1–113 | `373205D9FEC10BA3A5F7A730649EFF4668572F863BD51F045EFDB38D52BF473A` |
| `tests/backend/test_inspection_sql_semantics.py` | 1–48 | `549F570C18C6A2332E7BF3897FF82C18C65AE7257EAB1CEA0DDDF7B27F1F2814` |
| `tests/backend/test_query_analysis_provenance.py` | 1–26 | `E7AB713714E49C82EA6E570080C245804200AD612F9132B502C03589228A8B4A` |

### 片段阅读（14 个项目文件）

| 路径 | 已读行 | SHA256 |
| --- | --- | --- |
| `backend/app/modules/analytics/connections.py` | 16–42 | `BE239A4AB87CFB3DEDA4F58D75C135319B88AD656E1C3F206C25B8E157379659` |
| `backend/app/modules/analytics/sources.py` | 146–175、231–322 | `CA3B31DF279B11C41660F9556D3973ED1099A5259012D56F13913AF5911232CE` |
| `backend/app/modules/analytics/router.py` | 176–275 | `B5087B8FDE3DFC904F3CC06D1084CAC6F4DFB2C68D53291F666E7CD941ECCCC8` |
| `backend/app/modules/analytics/schemas.py` | 226–334 | `7147FF1983A2BCBFAAA69381335557E3341F49B2C5FF58287BECC921350198DC` |
| `backend/app/modules/analytics/models.py` | 71–143 | `BCB0A306C62E9DBF9D75459ACE70E79ED690055DDDF7BFD2DA7BDCAD20DB59BA` |
| `backend/app/modules/chat_artifacts/chart_renderer.py` | 1–75 | `276B9F33DF2DB047BE00724F933AE7471E11CDBE39DF9E84FAAAF01C722590E3` |
| `backend/app/modules/business_ai/service.py` | 89–118、406–602 | `3DB90F16D7FC569AF60990659E47885D76B1942A5786254704E0F6084D36C316` |
| `backend/app/modules/reports/generation.py` | 1–62、976–1089、1246–1333、1548–1564、2019–2036 | `7DD7B293C9F2B8F97BB4137B1F188FC7A8DFF83483F1BDCA48E1501FF3D534DD` |
| `backend/app/common/idempotency.py` | 1–169（含正文全文；未把尾部空行计入覆盖） | `2785F8933B43F9692C016005357F724188A312F9172E31AF7566850077326825` |
| `backend/app/providers/tool/builtin_provider.py` | 648–765、856–912 | `3765C188A55CAE0615FED4329BAF9932702814E5C6C38AB9D70976AA3456479A` |
| `backend/app/modules/reports/router.py` | 1–150 | `A8D3D9CB8615BBF26668DD34CF17594EF0AB6A4D9B855E96A33B43E93FD45EED` |
| `tests/backend/test_analytics.py` | 266–335、384–399、502–570 | `2FC052C4026BD9CB20C20D04E69CFCD3D1D8723759AD187A8D97DD6B4C13A01C` |
| `tests/backend/test_reports.py` | 548–579、1106–1119 | `647BEAB46395483F76C48D6A732BB89571F162E1BF980DB72D8238193C51C96A` |
| `tests/backend/test_business_ai.py` | 100–137、207–253 | `E498CFCC2B4E656D51A5085108AA530C8E8E56829A8BDDF11F00FEE4AACEE457` |

### 仅检索定位（未计入全文/片段通读）

| 路径 | 检索用途 | SHA256 |
| --- | --- | --- |
| `tests/backend/test_dsh_query_execution.py` | 相关定义、权限/缓存/快照或现有断言的 rg 行命中；不代表读过函数体 | `41A78A34E5CCB4CC17B721F9CF103D55FA1896B1883334F9C614F2561EC200B0` |
| `tests/backend/test_query_user_scope.py` | 相关定义、权限/缓存/快照或现有断言的 rg 行命中；不代表读过函数体 | `CC77A5A60BFCDA08AA5EE51C8459EE4C80AC9A023EA2FD8ED6BE8C563CAC8784` |
| `tests/backend/test_template_query_snapshot.py` | 相关定义、权限/缓存/快照或现有断言的 rg 行命中；不代表读过函数体 | `20C5B3D746AC52D1E48CC8F95FD900BBCED088F5275E1565964A9C736B7928A5` |
| `tests/backend/test_query_chart_units.py` | 相关定义、权限/缓存/快照或现有断言的 rg 行命中；不代表读过函数体 | `695809D8138CA93F298227ABCFAC7DD6CE2EB65D704EE98ACC1248440102DCFB` |
| `backend/app/modules/analytics/finance_semantics.py` | 相关定义、权限/缓存/快照或现有断言的 rg 行命中；不代表读过函数体 | `DDAFDC7FAC415242D26865DFF53DBAF0A18E3762D0774F5175E107FF3519BAED` |
| `backend/app/modules/analytics/connection_pool.py` | 相关定义、权限/缓存/快照或现有断言的 rg 行命中；不代表读过函数体 | `BBBCEAEB79BA956163D5C9D2622C560195BE5EB2CF70B56F2880A7F434C331E9` |
| `backend/app/modules/analytics/semantic.py` | 相关定义、权限/缓存/快照或现有断言的 rg 行命中；不代表读过函数体 | `87046F984FE989AC8D277C52DEFE5DB861441D2B7FA53F2DBAB4F7A84FEC6B9C` |
| `backend/app/modules/analytics/governance.py` | 相关定义、权限/缓存/快照或现有断言的 rg 行命中；不代表读过函数体 | `C6432A1A7642BF4E1C391E47194672CC4E328BDB2FED28BEF266B3F778A6D6AA` |
| `backend/app/modules/analytics/inventory.py` | 相关定义、权限/缓存/快照或现有断言的 rg 行命中；不代表读过函数体 | `50B965514E546A244FAC0B5F34817D0896170CB653734D55D24C2ABC806269E2` |

### 当前 venv 第三方实现（只读，不属于提交）

| 绝对路径 | 已读行 | SHA256 |
| --- | --- | --- |
| `E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Lib/site-packages/openpyxl/compat/strings.py` | 1–25 全文 | `0FF4D67FC427307E96331EB1B820DF5E5D1BA1CD64F6AEE3F2119A9554364549` |
| `E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Lib/site-packages/openpyxl/cell/_writer.py` | 73–86、119–132 片段 | `DC8E962CA1091AE15EF2B3A3C5D015B834F8B19623718A39EFEEAF7A519EA5D4` |
