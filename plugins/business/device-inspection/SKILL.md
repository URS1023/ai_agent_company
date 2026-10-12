---
name: device-inspection
description: 基于设备监控快照和巡检结果分析异常、生成巡检报告。
---

# 设备预警与巡检报告

明确设备、时间范围与指标口径。用 collect_device_monitoring_snapshot/view_device_alerts 获取实际快照、告警与来源时间，不能将历史告警表述为当前实时状态。

用户同时要求预警 Excel 时，一次调用 `view_device_alerts(limit=50, export_name="设备预警信息表.xlsx")`，直接交付服务端保存的报告、问题、数据来源和分页说明。不要为做表逐页读取工具全文、重抄记录或再次调用 render_spreadsheet。limit/offset 分别作用于报告和问题，50 条返回记录不代表全量；需要全量时先确认实际范围并按分页状态处理。分析仅需工具摘要与关键记录，完整明细在附件中保留。

用户同时要求备品备件时，库存快照走 `query_inventory_data(kind="inventory")`，仓库、物料目录分别使用 `kind="warehouses"`、`kind="materials"`。现有库存回执能提供的名称、单位、批次直接复用；只有实际缺少且目录有依据时才另查对应目录，不把查询仓库写成再次查询库存。带历史区间、部门或特殊统计条件的请求交给 `query_business_data`，不得丢掉范围条件。

备件 Excel 用该库存查询返回的 `queryId` 调用 `export_query_spreadsheet(query_id, name="备品备件库存表.xlsx")`；数据 PPT 通过文档交付插件复用同一查询标识，不能把预警报告/问题 ID 当作查询标识。预警和库存来源分别说明。数量缺少单位或各物料单位不同不合计；安全库存/阈值缺失时明确“未知／待补充”，不能判为正常或需要补货。

区分数据缺失、阈值命中和已确认故障；没有证据不得推断设备正常或给出确定故障结论。需要报告时用 export_inspection_report，记录巡检批次、结果数与真实文件。告警确认、质量规则更改和后续处理以业务接口操作回执为准。

## 工具与执行边界

工具：`collect_device_monitoring_snapshot`、`view_device_alerts`、`export_inspection_report`、`query_inventory_data`、`query_business_data`、`export_query_spreadsheet`。

本 Skill 提供业务方法；工具参数校验、身份、任务草稿、交付记录由插件代码负责。只使用本轮实际提供的工具，未注册工具不能假装已经调用。任务结束时按真实工具与交付记录说明完成、部分完成或失败。
