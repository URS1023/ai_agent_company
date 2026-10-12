# 批次十三：分析历史、报表快照与导出授权修复记录

日期：2026-10-04。独立树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-capability-contracts`。
分支：`codex/major-authorization-followup`。开始基线：`112bdd3efdc242a95f174e497922b42e56d6f1a9`。

本记录只描述本代理负责的 ANA-N01/N02/N03/N05/N06；ANA-N04/N07 的统计精度与预测公式由根代理负责，不能把本文专项结果当作其最终门禁，也不能当作全部产品功能验收。

## 执行边界

- 只在上述独立树修改；没有修改主树，没有提交。
- 唯一测试入口为主树解释器 `E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe tests/run_isolated_backend.py ...`，工作目录为独立树。
- 使用临时平台 SQLite、临时业务 SQLite 和明确模型桩；没有连接 szh_new、局域网/远程业务数据库、真实模型或 8011，没有启动浏览器/业务服务，没有安装依赖。
- 历史读取、导出、重放与修订权限测试在准备数据后阻断业务 execute_read、SQL/Elastic 元数据发现与模型调用，断言额外调用数为 0。DSH 拒绝错误巡检 SQL 的用例断言执行适配器调用数为 0。
- 所有新增注释和文档字符串使用中文；保留其他代理对 statistics、Bot、工具与前端等文件的编辑。

## 修复与合同

### ANA-N01：历史与保存报表的表字段撤权

新增 `analytics/snapshot_access.py`，仅用当前源配置和已保存的 SourceMetadata 重新授权历史 SQL。当前 allowedTables/allowedColumns 每次都重新施加；不因为停用或更名导致元数据版本变化就盲目拒绝，也不刷新元数据或连接业务库。受限源没有可核对的缓存元数据时明确拒绝。

查询历史列表、详情、JSON/XLSX 导出共用校验；列表的 SQL/参数摘要也不能泄露被撤权字段。列表在原有分页后过滤不再可读项目，页内数量可能少于 limit，未扩大无界查询。

旧 `SELECT *` 不仅重新解析，还检查保存行的实际键和列目录是否超出当前投影，防止新权限缩列但旧行仍含秘密字段。未生成 SQL 的失败查询在相同源版本下保留可读摘要；版本变化后不能证明原请求范围时隐藏，避免本次修复令正常失败历史全部消失。

保存报表、列表、详情、修订和恢复统一校验。多源且组件没有具体来源的内嵌行，只要存在受限源便拒绝；无业务数据的空布局仍可读。除 data/chartData，还识别 config.option、echartsOption、option 中的 dataset 对象/数组、series 与坐标/图例数据；dataset 对象行也核对字段投影。旧修订不可读时返回 available=false、snapshot=null，不能恢复其旧数据。

停用源下仍有授权的历史结果继续可读/导出，未按 active=True 封死追溯。普通历史保留既有软删除追溯合同；报表沿用其源已删除即拒绝的既有合同。

### 相邻回归：可信仓库参考画布

真实相邻测试发现 33 个内置导入报表被新校验全部隐藏；素材确实存在，原因不是独立树缺图片。目录前 20 个为固定展示样例，后 13 个保留历史 SQL 和内嵌数据；测试临时源仅授权 sales，不能把旧 t_xiaoshoudingdan 等绑定当成新授权查询。

`reports/source_reports.py` 增加 `CatalogSnapshotProof`：必须具有当前工作区平台 ReportSourceImport 登记，源内容/目录摘要与随包目录匹配，组件载荷逐字段等于该登记的原始素材。只忽略几何位置和已知绑定位置的改绑源标识；数据行中名为 datasourceId 的字段仍逐值比较。SQL、值、配置或其他字段变化就撤销这条素材例外。客户端 lineage、相似名称、无登记报表都不能得到例外。

仍逐个检查实际绑定源的资源查看权；例外不参与 SQL 执行权限。每个请求只读取一次导入登记和随包目录证明，不缓存跨请求权限。返回 description 明确说明：**仓库参考示例，固定展示数据来自随包素材，不代表当前业务查询结果；执行 SQL 仍需当前授权。** 新导入说明也持久化此文字；旧记录读取/修订返回时补充说明。

新增真实 HTTP 边界覆盖：原始载荷可读且有说明、改值拒绝、改 SQL 拒绝、篡改登记摘要拒绝、仅保留 lineage 而无登记拒绝、无源查看权拒绝；原目录 SQL 的实际执行仍被正常权限校验拒绝。

### ANA-N02：自动来源变化后的幂等重放

完成 claim 的结果、旧版本 ReportGeneration 回放和生成详情都复核留存结果的实际来源。不会用本次自动选中的 B 给旧 A 的结果背书，拒绝回放不改写已完成 claim，不重新查询或调用模型。当前生产 record.request_json 保留 resolved_datasource_ids，result 保留 datasourceIds；旧/损坏回执若连逐组件来源也没有，则明确 403，而非从当前请求补来源。

新增实际用户/角色 HTTP 复现：先以工作区范围访问 A，后创建本人 B 并收窄为 self，原隐式请求只会解析到 B；旧 A 的详情与相同 sessionId 回放均被拒绝。另覆盖正常重放零额外 IO、旧无 claim 记录、列撤权、完全丢失来源的 claim/result。

### ANA-N03：DSH 共享巡检口径

在共享 `_execute_query_with_source` 的只读授权之后、真实 SQL 执行之前，施加已有巡检聚合和设备有效记录校验。DSH 以 mode=sql 提交的模型 SQL 不能绕过旧自然语言入口的保护；业务意图只来自服务端保存的用户要求/上下文，显式用户 SQL 保持原意。

DSH 桩覆盖：缺 isDeleted 过滤、把 c_recordType 当完成状态、用 COALESCE 将 NULL 设备 ID 计入 distinct 的已知错误在执行前拒绝；合法巡检和用户明确 SQL 保持可执行。此修复不代表所有任意业务 SQL 的口径都能自动证明正确。

### ANA-N05/N06：导出精度、范围与字体

- Excel 超过 15 位十进制数字的 Python 整数写为文本，普通整数保留数值类型；真实 openpyxl 读取验证 1234567890123456789 不丢位、42 仍为数值。
- XLSX 查询结果页和数据来源页全部已创建单元格设置 SimSun；头行保留白字粗体。真实读取检查所有非空单元格字体、表头颜色/粗体。HTML 正文字体优先 SimSun/宋体。
- CSV/HTML/Markdown 的截断说明独立于 needAnalysis，即使关闭分析仍明确返回行数、仅部分结果和查询标识。
- **CSV 兼容边界：只有 truncated=true 的 CSV 前置两条说明记录（# 数据范围、# 查询标识），下游解析器需要识别并跳过说明；未截断 CSV 仍以原字段表头为第一行。** HTML/Markdown 正文增加独立范围说明。没有扩展修改 PDF/Word/PPT 等其他导出分支。

## 完整红绿证据

以下名称均位于独立树 ignored `.implementation/`，每项同时保留 `.xml` 与 `.log`，未删除首次失败。

| 证据前缀 | 结果 | 说明 |
| --- | --- | --- |
| analytics-batch13-before | 1 收集错误 | 新测试误用 pytest 保留参数名 request；修为 user_request。未运行产品行为，不计漏洞复现。 |
| analytics-batch13-before-behavior | 23 失败、2 通过（25项） | 原始 HTTP/DSH/导出行为红灯；含 A→B 错源回放 HTTP 200、错误巡检实际成功。 |
| analytics-batch13-after-1 | 25 通过 | 首修结果，尚未加入后续边界。 |
| analytics-batch13-star-before | 2 失败 | 新授权重新展开 SELECT *，旧历史/报表行仍保留额外列。 |
| analytics-batch13-multisource-before | 1 失败、1 通过 | 多源无绑定行绕过；空装饰可读保持。 |
| analytics-batch13-failure-history-before | 1 失败 | 本次首修导致未生成 SQL 的正常失败历史消失，已修复。 |
| analytics-batch13-after-2 | 196 通过、2 失败（198项） | 新专项及 reports、DSH query、inspection semantics、task control 相邻回归。两个失败为真实目录导入/模板提升授权合同回归，已定位并修复；未归因素材缺失。 |
| analytics-batch13-after-3 | 12 通过、1 失败（13项） | 原两个相邻回归、字体/精度/截断、失败历史及素材边界。唯一失败为新测试中文 role.code 不符合接口正则，改 ASCII；不是产品权限漏拦。 |
| analytics-batch13-option-before | 2 失败 | echartsOption 与 dataset 数组内嵌行漏识别，已补齐。 |
| analytics-batch13-after-final | **145 通过，0 失败，117.73 秒** | 最终 42 项新增专项和完整 test_reports.py 的 103 项；产品已冻结，根代理安排统一门禁。 |

最后一轮命令：

```powershell
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_analytics_snapshot_authorization.py tests/backend/test_analytics_dsh_and_export_contract.py tests/backend/test_reports.py --junitxml=.implementation/analytics-batch13-after-final.xml
```

新增文件共 42 项：snapshot_authorization 34 项，dsh_and_export_contract 8 项。原 test_reports.py 未改断言或删除用例。先前相邻 198 项是在当时编辑快照执行；最终统一门禁需以根代理冻结后结果为准。既有 datetime.utcnow/Pydantic alias 等弃用警告保留在原始日志，未静默隐藏。

## 本代理最终源码清单与 SHA256

| 文件 | SHA256 |
| --- | --- |
| backend/app/modules/analytics/queries.py | 0BC57BC6463043AB366C2E2D0666C41536A8F2B44E6377142F82DD9DBA905064 |
| backend/app/modules/analytics/exports.py | 6465DFC348FF8894D3A89B0C4C8EB0116271E91E1C96DEA57397D25FD70D00CB |
| backend/app/modules/analytics/snapshot_access.py | DABA39718C395D68122B21BB42CE3959A9F724DD3878B64F7FC571CEB6CF88E2 |
| backend/app/modules/reports/service.py | CD2F1758B9603C2441BB93F0828AFD920D34456CAA21C73D7E2F689804C43912 |
| backend/app/modules/reports/generation.py | 2C2AD58A0BF289FC713F523B46F87EFD7C66B40AC7E9651F06D920562FD3940F |
| backend/app/modules/reports/source_reports.py | C5C8B4D7E00588345F3CA4EB21CC2C28F0008991EBE8C5D9CC3A7A80C753AFED |
| tests/backend/test_analytics_snapshot_authorization.py | CAFD040A1290A797C1A017045E2EADC8663714BC8E32592378119629DE350EDB |
| tests/backend/test_analytics_dsh_and_export_contract.py | 62E3B528C9250BC816CF41ACF7A8AE65A26CBDBBE3DA9AEBE39FC8F8F4C71170 |

`git diff --check` 无错误（Windows CRLF 转换提示保留）。未做真实生产/远程数据库验收，未恢复 100 轮浏览器循环。最终门禁、构建、提交由根代理统一负责。

最终专项已结束，ANA-N01/N02/N03/N05/N06 的本轮修复及必需兼容边界全部完成；没有本代理范围内已知失败留待本批修复。上述 145 项不替代根代理最终统一门禁，不声称所有未知口径、所有外部数据库或所有长任务已经保证正确。

## 发布前追加复核更正

上述“最终”描述及 SHA 是第一轮冻结快照。之后根代理发现二维数组/仅 series 可能绕过字段检查，本代理真实 HTTP 复现了 2 个泄露，保留了首次红灯，并在首轮统一门禁结束后追加局部修复。详情与新的 snapshot_access SHA、新增 11 项测试及复测结果见 `.implementation/fix-analytics-chart-shape.md`。首轮 145 项和首轮门禁不代表此剩余边界已经通过，不得省略本更正。
