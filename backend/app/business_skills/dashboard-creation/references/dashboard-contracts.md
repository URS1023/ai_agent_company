# 大屏契约（dashboard-creation）

## 1. 数据来源
- 快照大屏只能来自本轮成功查询回执：`create_dashboard_from_queries(query_ids=[...])`，
  每条 query_id 必须是在当前 Run（或明确续作）中 `query_business_data` 或 `query_inventory_data` 成功的回执。
- 不允许为造大屏再次查询或调用模型；发现目录/未成功回执不能作为来源。
- 同 query_ids+title 但取数结果已变化的场景：平台会生成新快照，不复用旧值；正文需要相应更新。

## 2. 模板与模式
- 默认 `dashboard_template_mode=auto` 走快照布局；用户明确手选模板（manual）时，
  `create_dashboard_from_queries` 自动保留所选模板并绑定兼容槽。
  `create_business_dashboard` 的 DSH 路径也接收 query_ids 并使用同一快照，不启动第二个模型或重查数据库。
- 模板原来的“完成率”“%”不代表本次业务指标；组件展示实际绑定的字段标签和已核实单位，未绑定槽保留为空。
- 大屏标题 1–200 字符；query_ids 1–8 条、不重复。

## 3. 快照语义
- 大屏组件带数据快照且关自动刷新；文本注明“已保存的查询快照；保留原始字段与返回范围，
  不自动刷新，不代表实时或全库总量”。
- 截断结果必须保留 truncation 标注；不得把部分结果当成全量。

## 4. 交付与修改
- 放进 Word：先 `render_dashboard_image`（从已保存大屏的真实查询快照导出图片），
  再把图片工件嵌入 `render_document`；正文“已生成大屏”不能替代实际图片/链接。
- 修改布局：`update_business_dashboard` 只改展示不改数据；已保存快照被删除/修改后不自动重建。
- 权限：dashboards.create / analytics.view 等由平台逐项校验；工具 ID 不构成跨越权。
