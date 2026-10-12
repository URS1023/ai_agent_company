---
name: dashboard-creation
description: 把已确认的成功查询制作成大屏快照，自动绑定用户所选模板，不重查库不冒充实时。
---
# 数据大屏
当用户要把查询结果做成数字大屏、看板或大屏截图并放入文档时使用。
开始前先通过 read_business_skill_resource 读取本技能 references/dashboard-contracts.md。
大屏数据来自本轮已成功的 `query_business_data` 或 `query_inventory_data` 回执（query_ids），不重新查询数据库或调用模型。
用 `create_dashboard_from_queries(query_ids,title)` 生成“已保存查询快照”；用户手选模板时工具自动保留模板并绑定兼容槽，不调用额外设计模型。也可用 `create_business_dashboard(query_ids,requirement,title)`，DSH 走相同查询快照路径。
同一查询包含多个金额时，可用 `bindings=[{query_id,value_field,label_field}]` 明确选择真实字段。兼容槽不足属于输入待修正，尚未保存，可减少 query_ids 或明确调整布局；不要重新取数或反复改描述调用旧生成流程。
快照是静态留存：标注返回行数、截断状态与查询时间，不代表实时或全库总量。
需要把大屏放进 Word 时，先 `render_dashboard_image` 得到可信大屏图片，再在 render_document 中引用该图片工件。
修改布局用 `update_business_dashboard`，只改展示不改数据；对已保存快照不自动重建、不覆盖。
