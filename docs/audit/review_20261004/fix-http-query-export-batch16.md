# 批次十六追加：HTTP 成功查询原结果 Excel 导出

状态：2026-10-04 已冻结产品与专项。仅在 major-delivery-acceptance 独立树修改；未提交、未推送、未连接真实数据库/模型/8011。B17 只读工作已暂停并保存在 `review-batch17-query-dispatch-boundaries.md`。

## 真实问题与原始证据

浏览器首次复合场景 `.implementation/full-business-browser/20261004-120305-40d679d9` 与 `.implementation/ui-results/full-browser-ai-documents-mutapp08/result.json` 记录：HTTP `query_business_data` 成功、相同查询生成饼图成功，但 `export_query_spreadsheet` 经 `analytics.exports.export_query → queries.historical_result` 返回“HTTP 采集结果请从数据源的采集历史查看”。浏览器查询标识 `01M42HC52256P5VE61P0EWG86J`、来源 `01M42HC3CTAHTEMTMTFJ1PDYRG`、聊天 Run `01M42HC4PZ12PHTF2XYHG0R6FC`；这些属于浏览器专属临时平台及回环合成数据，不是生产业务记录。

另建本机回环 HTTP 桩，经正式创建源、采集、导出 API 复现：采集 200 且实际请求一次，XLSX 导出 422。原红证据 `.implementation/http-query-export-before.xml/.log`（1 failed，首失败停止，2.45 秒）。首轮日志受旧标准输出编码影响，中文不完整，JUnit 原文与 HTTP 422 断言保留；后续设置 PYTHONIOENCODING=utf-8。

## 修复范围

- `backend/app/modules/analytics/queries.py:648` 新增 `_http_historical_result`，由 `historical_result:712` 在原有查询记录权限、数据源查看权和成功状态检查后调用。HTTP 分支验证来源 provider、同一配置版本、CollectionResult 结构、queryId/runId/sourceId/sourceVersion、空 SQL、完整成功、列目录/行键一致、当前显式提取字段以及逐请求 rowOrigins/outcomes 行数一致。字段匹配区分大小写；空 fields 沿用正式“自动保留全部字段”语义。
- 来源版本不同明确拒绝。历史没有保存原 JSONPath，不能仅因输出列同名就断言提取范围相同；本轮没有新增配置历史架构。
- 返回原 `CollectionResult`，JSON 导出保留 outcomes、rowOrigins、HTTP 类型和真实来源。没有发起 HTTP/SQL、没有手抄或重新生成业务行，也没有改写原 QueryHistory。
- `backend/app/modules/analytics/exports.py:48,90` 按真实类型区分 HTTP 采集和 SQL 查询；XLSX 来源页增加“查询类型”“数据范围”，沿用已有宋体、公式保护及精度规则。HTTP 不再被说明为 SQL 查询。
- 无需修改 `SnapshotAccess`、`query_spreadsheet.py`、`dashboard_image.py` 或浏览器脚本。同一公共恢复入口同时打通原 Excel 工具和大屏来源读取；工具本轮/续作成功回执及 Run 归属校验仍由原 loader 执行。

## 红绿与实际范围

新增 `tests/backend/test_http_query_export_contract.py` 最终 21 项：真实 API XLSX、真实工具工件字节、真实大屏 panels 来源读取、完整 JSON；正式编辑造成字段收窄、同名 JSONPath 变化、停用改版；queryId/sourceId/sourceVersion/runId、额外行字段、重复列、行来源/请求计数损坏、非对象快照、同版本当前字段收窄；导出权限、源资源授权、查询所有者范围撤回；达到行数预算后未发送的 HTTP 批次仍为 partial。每例均核对原回环请求计数，导出和后续读取没有追加请求。

验证过程保留所有失败：

| 证据前缀（均位于 `.implementation`） | 结果 | 说明 |
| --- | --- | --- |
| http-query-export-before | 1 failed | 原产品 422，首失败停止 |
| http-query-export-after-1 | 1 passed / 1 failed | API XLSX 已修通；新增工具用例夹具漏填 ToolCall.id，触发真实 NOT NULL，停止后修正夹具；同轮静态核实实际存储读取方法为 read_bytes |
| http-query-export-after-2 | 20 passed，28.29 秒 | 修正夹具后所有正反例通过 |
| http-query-export-regression | 42 passed / 27 deselected，46.73 秒 | 21 新专项与 21 既有 SQL 撤权/停用、Excel 字体/精度/截断说明回归；进程退出 0 |

最后一组命令（从独立树根目录执行）：

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_http_query_export_contract.py tests/backend/test_openwebui_query_spreadsheet.py tests/backend/test_analytics_dsh_and_export_contract.py tests/backend/test_analytics_snapshot_authorization.py -k 'http or spreadsheet or xlsx or exports_show or query_snapshot_rechecks or allowed_history_survives' -x --disable-warnings --junitxml=.implementation/http-query-export-regression.xml
```

21 项新增不等于 42 项新增；本次未重跑完整后端门禁或浏览器。最终日志中的 5597 条 warning 为既有依赖/时间戳等警告，不计测试失败。浏览器代理已收到冻结通知，随后独立复验。

## 边界与兼容影响

1. 相同来源版本下的完整成功 HTTP 快照可导出。正式 HTTP 编辑均递增版本，因此改名、停用即使不收窄字段，也会阻止该通用导出/大屏图片入口复用旧结果；这是缺少原提取规则情况下的保守边界。HTTP 专属 `/collection/history` 原合同未改，旧结果仍可追溯；原 SQL 的停用、软删除后已授权历史读取未改。
2. 当前 `collection/execution.py:232–249` 将截断或未完成批次标记为 `partial/success=False`，不会生成合法的 `history.status=success && truncated=True`。本轮只打通“完整成功 HTTP”的通用导出，不把 partial 提升为成功；HTTP 部分/截断结果仍从采集历史查看。SQL 有界截断导出与标识继续支持，并通过相邻回归。
3. 这不是所有报表/HTTP 历史撤权口的全面审查。公共 SnapshotAccess 的 HTTP 分支、采集专属历史本轮均未改；来源改版后停用历史的更细粒度规则，需要另批配置历史证据设计。
4. 隔离专项大屏只验证原真实历史回执到 panels，不冒称已运行浏览器截图或完整复合文档验收；实际截图/组合任务由浏览器代理复测。

## 冻结指纹

以下为本追加完成时 SHA256；queries.py 同时包含 B16 已冻结的来源重验增量，因此替代此前该文件的旧指纹，不能按本次两处入口增量推断它只改了导出。

| 文件 | SHA256 |
| --- | --- |
| backend/app/modules/analytics/queries.py | 3F60BEBADE0E1C7439E57DE045C981DC2E57C6EB697EBA5B6FEE035C88CACADF |
| backend/app/modules/analytics/exports.py | 9C9956B40AAD3186C6302468286DD388F176AD1EE1D3507C1C5B13C4447B06B0 |
| tests/backend/test_http_query_export_contract.py | 84FA05028047DB5C61CFDE3121C2FBD7D1587296DF3073F0B9AACD2B5BFEE907 |

独立复核建议：沿真实 `export_query_spreadsheet → export_query → historical_result` 检查没有绕开工具回执范围；核对版本变化/同名路径边界和 HTTP 专属历史合同仍分开；核对 JSON 中 outcomes/rowOrigins 保留及 Excel 来源页没有 SQL 冒称。
