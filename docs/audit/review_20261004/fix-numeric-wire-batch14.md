# 批次十四：业务大整数跨语言传输

2026-10-04；基线 `782d346bc1e3df6a0e63dd199c0157c9ec47476b`，工作树 `major-task-lifecycle`。本项不调整业务数值，不连接业务库或真实模型。

## 问题和修复

Python 和平台 JSON 快照能保存 `9007199254740993`，但把它作为 JSON number 发送给 Node 或浏览器后，会变成 `9007199254740992`。只在 Excel 导出处理大整数不能保护聊天回执、历史查询和报表设计器。

- `common/json_numbers.py` 递归复制 JSON 容器，绝对值超过 `2**53-1` 的整数改用精确十进制文本。布尔、安全整数、原字符串保持；不随意解析普通字符串。查询列的 bigint 类型在编码前确定，不改成字符串列。
- `analytics/execution.py` 的查询结果、`QueryResult` 的结果/统计/解释/参数以及 `HistoryView.parameters` 采用上述编码。读旧历史不重新查询、不回写原始快照。
- 私有 JSON-RPC 输出以及新工具结果落库采用同一表示。旧幂等回执仍先经过现有重放授权，再返回转换副本，不能靠重放绕过权限。
- `reports/wire.py` 保护当前报表与旧修订的 content，单独处理契约约定的 `config.chartData` JSON 字符串和修订 content JSON 字符串。未发生转换的字符串保留既有格式，持久修订不被读取操作修改。
- 大屏交付与可信目录素材证明只归一化这种精确整数表示；保留原来的工作区、来源、查询、字段、登记、目录哈希和组件匹配条件。相邻错误整数、补零文本、安全整数改成字符串仍不通过。

## 独立复核及失败记录

原始 XML 和日志均在本工作树 `.implementation/`，后续通过不覆盖失败证据。

| 阶段 | 结果 | 证据前缀 |
| --- | --- | --- |
| 初始查询/历史/RPC/工具反例 | 9 failed、7 passed | numeric-wire-before |
| 核心修复 | 16 passed | numeric-wire-after-initial |
| 旧报表/大屏证明补例 | 3 failed、3 passed、16 deselected | numeric-wire-legacy-before |
| 修复与既有查询、报表、大屏及恢复回归 | 226 passed，163.96 秒 | numeric-wire-after |
| 独立复核后首次补例 | 3 failed；其中 2 个为夹具问题 | numeric-wire-review-before |
| 去除未绑定参数后的补例 | 3 failed；其中列表 URL 仍是夹具问题 | numeric-wire-review-red |
| 夹具全部修正后的确证红测 | 3 failed、22 deselected，4.86 秒 | numeric-wire-review-red-final |

独立复核记录 `review-business-numeric-wire-batch14.md` 指出 NW-R01 旧修订 content 和 NW-R02 查询参数两个遗漏。本批已补实现；最后三个反例均经真实 HTTP 和真实 Node JSON.parse，分别观察到详情参数、列表参数、旧修订中的原整数被舍入。首次夹具错误为无 SQL 占位符的附加参数被授权校验拒绝，以及列表 URL 使用了不存在的 `/history`，不计作产品问题。

新增专项最终为 25 项。最终修复后专项与既有报表回归及统一门禁结果另在本文件后续追加；不能用前面的 226 项证明其后新增修复已验证。

## 证据边界

本项验证临时 SQLite 实际查询、HTTP 响应、Node 解码、Worker 工具记录与聊天投影，以及历史报表/修订不被读取改写。它不证明任意端点已全部覆盖：图形坐标、图表默认 tooltip、客户端主动 Number/parseFloat 聚合、模板参数等其他视图仍需逐条核对；不从已舍入浮点恢复原数字，也不自动解析其他任意业务字符串。

MySQL、大型真实业务数据、外部模型、8011 在用服务均未参与本项测试。后台现有超时和查询预算没有被扩大。

## 最终补充回归与文件冻结

旧修订和参数修复后，25 个新专项与 103 个既有报表用例共 **128 passed，86.93 秒**，证据 `numeric-wire-review-green.xml/.log`。此前 226 项对应本项早一版代码，保留分开记录。最终统一门禁另见批次十四验证摘要。

| 文件 | SHA256 |
| --- | --- |
| `backend/app/common/json_numbers.py` | `d01d3745f951bbcc6ee87632b5130b5e0110e9875f5b0ddc32d9e5a6cce4220e` |
| `backend/app/modules/analytics/execution.py` | `2d3fbeaa097f7cd3295f2341a683acf43ad6f846c40d814573fcc68596709bd7` |
| `backend/app/modules/analytics/schemas.py` | `ac4f21fd494415b05aaadc606a9f8602defe18184eaf7b10f1599dbe380136a0` |
| `backend/app/business_worker/protocol.py` | `c6f2111b24ca2b5394c3ca9fcbd7ef696f1d8a23ef3d704fc6f1da584ed01ad2` |
| `backend/app/business_worker/tools.py` | `28af4f034130dcf9f6a69144c683e28a47947e39b212aa17be8fb2b28929e06e` |
| `backend/app/business_worker/dashboard_deliveries.py` | `39a3d86fa127a9bf7d725a9f6467ac0deca7af8b9ee175e8fd37d7ca9d634e1a` |
| `backend/app/modules/reports/wire.py` | `9d17bb13f5eb911af13f24213931c46aeef049b85317f833c322b66d95adc625` |
| `backend/app/modules/reports/service.py` | `a857ecd541adf8667bb7bc2a174e318202231c2ac8ffae44b187c17035fd65f3` |
| `backend/app/modules/reports/source_reports.py` | `8d218e7e41293b17e46e04e876c530043132a05eab608b26bc8d3c494ad7e17c` |
| `tests/backend/test_business_numeric_wire.py` | `868f4e843bfb2f5b5423323fd4225a4bba459206988bb5ebcebdf209ce630a6f` |
