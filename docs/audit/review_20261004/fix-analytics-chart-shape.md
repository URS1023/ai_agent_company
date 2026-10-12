# 批次十三发布前补洞：图表数组快照撤权

日期：2026-10-04。独立树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-capability-contracts`。

这是根代理在首轮统一门禁完成后要求复核的剩余 ANA-N01 边界，不能用此前 145 项或首轮 10 阶段通过来代表本补洞已验收。生命周期下一批只读报告在收到此任务后暂停，未执行其中建议。

## 先复现后修改

新增独立 `tests/backend/test_analytics_chart_snapshot_scope.py`，通过真实 HTTP 创建带旧 `SELECT * FROM sales` 的报表，随后通过数据源正式编辑接口只保留 id 列，最后在阻断业务 SQL、结构发现、模型额外调用条件下读取报表详情。

补洞前证据 `.implementation/analytics-chart-shape-before.xml` / `.log`：**2 失败，4.68 秒**。二维 dataset `[['id','note'],[1,'正常']]` 与仅含 series.data 的旧星号快照都返回 HTTP 200，继续包含已撤销列的数据。这里的值来自一次性 SQLite 夹具，不是非公开业务数据。

红灯完成后通知根代理；根代理确认首轮门禁 session 71861 已退出、取码窗口结束，才修改产品。首轮门禁保留为补洞前快照，不能改写成补洞后的结果。

## 最小产品修改

仅修改 `backend/app/modules/analytics/snapshot_access.py`：

- 受限投影的二维数据必须有可靠字符串表头或 dimensions 名称，逐字段属于当前授权投影；检查表头/维度冲突、重复、缺失和行宽不一致。列数碰巧相同不能当成授权证据。
- dataset.source 的对象行保留既有字段检查；合法表头矩阵及明确 dimensions（含 `{name, type}` 维度对象）仍能读取。
- 旧 SQL 外层投影为 `*` / `table.*` 时，单独内嵌在 series/坐标等位置的值无法确定原输出列，受限源明确要求重新生成；不把新授权展开后的列位置当成旧数据来源。
- `COUNT(*)` 不是外层星号投影，显式已授权 SQL 生成的正常 series 保持可读。可信仓库素材的登记与逐字段比对例外保持不变。

不修改字体、前端、连接生命周期或其他产品文件。新增代码注释为中文。

## 用例与兼容边界

独立新文件 11 项：原二维矩阵/series 泄露两项，合法对象行、表头矩阵、dimensions 矩阵、显式列 SQL series、COUNT(*) series 五项正向对照，以及无表头、被撤销表头、冲突 dimensions、额外单元格四项拒绝对照。

修复后没有来源证据的星号 series 在受限范围下连新建也会拒绝；为验证“历史原先可读→撤权后拒绝”的阶段变化，最终测试通过正式接口将临时源设置为尚未限制表列，再保存旧快照、收窄为 id。详情必须拒绝的原断言未放宽。缺证据旧快照需要按当前权限重新生成，不以匿名数组位置恢复授权。

复测命令：

```powershell
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_analytics_chart_snapshot_scope.py tests/backend/test_analytics_snapshot_authorization.py tests/backend/test_analytics_dsh_and_export_contract.py tests/backend/test_reports.py --junitxml=.implementation/analytics-chart-shape-after.xml
```

结果：**156 项全部通过，0 失败，123.70 秒**。组成：11 项补洞专项、42 项先前新增专项、103 项完整既有 reports 相邻测试；既有 test_reports.py 未改。原始日志保留既有弃用警告。XML 为 `.implementation/analytics-chart-shape-after.xml`，完整日志同前缀 `.log`。

## 修改快照

| 文件 | SHA256 |
| --- | --- |
| backend/app/modules/analytics/snapshot_access.py | 8C73CD51FBAF1BEAA13182AEADCCC8F5F619CF80C27A05E14FA3678017707105 |
| tests/backend/test_analytics_chart_snapshot_scope.py | F35043F78BD86F0965F5F6BB992DBE7D2D3986A33D85F2CE379EBE1C72DC0B9A |

没有连接真实数据库/模型/8011，没有启动浏览器、安装依赖或提交代码。全部首次失败证据保留。最终统一门禁需根代理以本次冻结后的源码重新执行。

已向根代理报告产品及测试正式冻结；本补洞没有已知未通过项。后续统一门禁结果仍以根代理执行记录为准。
