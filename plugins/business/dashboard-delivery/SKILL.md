---
name: dashboard-delivery
description: 复用实际查询创建和修改报表、大屏及图表产物。
---

# 业务报表与数字大屏

先明确新建还是修改已有大屏。已有对象用 get_business_dashboard 查看当前内容，再用 update_business_dashboard 修改用户指定范围；不能每次另建副本并声称原屏已更新。

用户指定模板时用 list_dashboard_templates 校验模板存在；无指定时依据实际数据维度选择。优先把已成功的查询标识交给 create_dashboard_from_queries/create_business_dashboard，避免重复取数与重复模型分析。

`create_dashboard_from_queries(query_ids,title)` 自动保留本轮手选模板；`create_business_dashboard` 在 DSH 中同样直接复用成功快照。最多传 8 份所需查询，不把覆盖诊断混进来。同一成功查询可分别展示其中的真实指标，生成工具负责按组件容量绑定，不为填满模板重复查询、重复合计或编造指标。需要选定某个金额列时传 `bindings=[{query_id,value_field,label_field}]`，字段必须存在且确为数值；不能为绕过容量检查擅自缩减用户要求的指标。

生成工具在保存前检查必需字段覆盖及可读容量，并返回 `quality` 与每份查询的 `requiredFields/displayedFields/unshownFields`。容量不足时依据结构化回执修正布局或用户已明确允许的展示范围；无指定模板时由工具自动单栏或分组展示，不要求用户把复合需求拆成多句话。模板已手选且确需改布局或减少指标时说明具体限制，不能悄悄换模板，也不要为了布局问题重新查询业务库。数值字段格式异常时保留错误及原查询证据，不转成零、不从必需指标中删除。只有实际保存及任务验收均通过，才能宣布大屏完成。

在 DSH 中，当前 Agent 先读取 schema，再用 `query_business_data(mode="sql", task="当前取数子任务", query="明确SQL")` 执行。工具不调用第二个模型把 SQL 改回自然语言规划；原始月份、部门和会话由服务端保存并校验。收到范围或财务口径结构化错误时，依据回执指出的条件修正当前步骤，不能删条件或换指标凑数据。一个复合请求中的明细、汇总可分别查询，但各自保留完整原始范围，再将真实成功的 queryId 交给大屏生成工具。

当前库存、仓库、物料的标准取数优先 `query_inventory_data`，其他明确业务范围走 `query_business_data`；两者成功回执的 `queryId` 都能直接交给 `create_dashboard_from_queries(query_ids,title)` 和 `render_chart_image(query_id,...)`。同一数据的 Excel、PPT、大屏使用同一成功查询快照，不再按附件格式重复取数。查询已截断时大屏注明部分结果；不能把该样本绘成完整统计占比。

图表必须有真实分类、序列、单位与时间口径，不能补造数值。图表或大屏图片使用相应渲染工具，交付以实际 report_id/artifact_id/链接为准。创建成功、图片导出成功分别报告，不把其中一项替代另一项。

业务查询零行只表示该实际执行范围没有匹配记录，不表示营收或应收金额为 0。需要核实所选表的日期覆盖时，可使用 `query_business_data(mode="profile", table_name="实际表名", date_column="实际日期列", query="核对日期覆盖")`；表名和日期列须来自 `schema`。它只返回未删除记录的记录数、空日期数及最早/最晚日期，`evidenceKind="coverage_only"`、`profileQueryId` 均属诊断证据，不能当作业务金额或传给大屏生成工具。诊断后正式查询仍保留用户原始月份、部门和指标；同一诊断不重复执行，不靠不断扩大 SQL 范围凑数据。

没有已核实的业务结果时明确说明未完成的大屏及缺少的数据口径，不能拿诊断计数、合同总额或其他月份代替应收/营收，也不能凭部分召回表或单表零行断言全库没有数据。

不同物料或不同计量单位不合计；缺失单位、阈值或实测值保留未知，不改成 0 或正常。图表因混合/缺失单位被拒绝时，保留原始表格或使用口径明确的独立查询，不删除单位列绕过核验。

## 工具与执行边界

工具：`query_inventory_data`、`query_business_data`、`list_dashboard_templates`、`get_business_dashboard`、`update_business_dashboard`、`create_business_dashboard`、`render_chart_image`、`create_dashboard_from_queries`、`render_dashboard_image`。

本 Skill 提供业务方法；工具参数校验、身份、任务草稿、交付记录由插件代码负责。只使用本轮实际提供的工具，未注册工具不能假装已经调用。任务结束时按真实工具与交付记录说明完成、部分完成或失败。
