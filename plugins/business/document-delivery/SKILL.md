---
name: document-delivery
description: 生成或修改 Word、Excel、PPT，支持跨轮草稿与按实际文件验收。
---

# 文档、表格与 PPT 交付

业务工具成功返回 `artifacts` 或 `metadata.delivery.already_presented=true` 时，产物已自动接入当前回复的下载卡片，直接继续下一项任务。不要再把 `/api/artifacts/.../download` 传给原生 `present`，也不要调用 `list_files/glob/pwsh` 寻找这些工件。`list_files` 列的是文件库，工件不在该列表；接口地址不是工作目录路径。只有原生 `write` 等工具在当前工作目录真实生成的文件才调用 `present`。

先从用户本次要求确定内容、格式、参考样式和交付物；用户要求先审查或计划时先提供审查，不提前制作。参考稿必须先读取，按章节与版式组织内容。

已有业务查询回执的数据汇报 PPT 使用 `export_query_presentation(query_ids=[...], presentation_mode="report", title="业务数据汇报", name="业务数据汇报.pptx", scope_notes=["本次已确认的业务范围与未完成内容"])`。默认汇报模式从实际汇总查询制作指标页、从分组查询制作对比页，明细只作受限预览，完整返回行列保存在来源页备注；不要把几十页原始明细分页当成汇报。总计、分组与明细复用已有成功查询；工具不会替用户对不同查询求和，也不会重新取数。不向 `render_presentation` 或草稿工具重抄数值。

只有用户明确要求完整快照、所有行列逐页展示时才用 `presentation_mode="snapshot"`，宽表跨页保留原始行号；长字段的完整内容在同页备注。无论哪种模式，都不能把备注中的内容称作页面完整展示。固定页数不符时按实际失败说明范围，不删减数据或忽略页数要求来凑成完成。

用户将“应收应付”改为“只做应付”后，实际文件标题应相应标为“应付情况汇报”，应收未提供的事实必须通过 `scope_notes` 写进 PPT 正文，不能只在聊天回复中声称文件已说明。请求支付、合同金额、已付、未付各自保留字段口径；没有业务依据时不能假设金额之间恒等，更不能自动改数凑平。Excel 工具不会自动包含聊天里的范围说明，未实际写入时不要声称 Excel 已标注该说明。

ERP 介绍、方案讲解、指定参考版式等内容型 PPT 使用 create_presentation_plan → write_presentation_slides → finalize_presentation；不能拿库存快照替代用户要求的系统介绍。草稿归稳定 task_id/draft_id；下一轮只修改用户指定的已有任务，不能凭新的 run_id 丢失草稿或默认为所有会话共享同一草稿。

同一任务要交付多份内容型 PPT 时，分别以各自标题创建大纲，保存每份回执中的 `draft_id`，后续 `write_presentation_slides` 和 `finalize_presentation` 显式传该 ID。再次创建同标题会返回原稿及进度，不会另建副本；回执 `available_drafts` 可核对当前任务各稿的标题和标识。修改第一份时不能省略 ID 后误写最后选中的第二份。已有成功查询在同一任务下一轮可直接导出或绘图，不为更换文件名、标题、格式、模板重新查询；资源范围变化时以工具的实际校验结果为准。

用户已逐页指定全部主题且没有要求额外封面、结束页时，在 create_presentation_plan 中设置 include_cover=false、include_closing=false，按指定顺序完整保留所有主题；例如总共五页并列明五个主题，就提供五项正文大纲，不能挤成三页正文。未逐页指定时按用户意图选择封尾，省略开关保持旧稿默认。用户说修改总稿第 N 页时，优先传 physical_page_number=N；slide_number 是正文索引，必须按 presentation_resume 或工具回执的 outline/pages 映射换算，不能把两者混用。

精确页数的表格可能只显示部分数据行，完整溢出明细会写入演讲备注。检查 finalize_presentation 的 presentation_overflow 回执，如实说明 visible_data_rows 与 notes_data_rows；页面和备注中的内容要分别说明，不能称所有行都在页面完整展示。

材料缺少字段时只能说明“未提供该字段”，不能改述为数据库存 NULL；样本行数不代表全库总数。单位、用量按每次还是周期合计未明确时标为待确认，不自行补充业务口径。

表格用 render_spreadsheet，文档用 render_document，有模板用 fill_template。工具参数校验失败时修正输入，保留已成功的其他交付物。渲染失败不能宣布完成。核对输出可下载、格式与任务要求对应；多个文件逐项登记，未完成项明确说明。

已有查询回执的 Excel 使用 `export_query_spreadsheet(query_id, name)` 原样导出；同份数据的 PPT 使用相同 `queryId`，不为两种格式分别查询。库存、仓库、物料取数优先 `query_inventory_data(kind="inventory"/"warehouses"/"materials")`，不支持的历史区间或汇总走 `query_business_data`。保留实际单位、空值和截断状态，不合计不同物料或不同单位，不把缺失安全库存写成正常。

预警需求通过设备插件的 `view_device_alerts(export_name)` 同步生成表格；其报告/问题回执不是分析查询的 `queryId`，不能随意拿报告 ID 调用查询导出。不要逐页读取完整工具日志后手工重抄明细。预警与库存属于不同证据范围，组成同一报告时分别写出范围与来源，不能用库存统计替代预警统计。

最终回答先用一句话说明真实完成状态，再给 2–4 条关键结论和必要的范围限制。已有文件卡片时，不重复制作带 `/api/artifacts/...` 裸路径的下载清单长表格；需要文字引用时用简短文件名称。内部 file_id/query_id、调试步骤和改名尝试不进入主结果。文件名与请求不同可以简述实际名称；未解决的错误、缺失交付物和影响结果的限制必须如实说明，不能被“已完成”掩盖。

正文使用业务用语，例如“本次返回 4 条，已取完本次查询”；不照抄适配器名、`truncated=false` 或草稿存储方式。文件内容与备注的范围用简短必要说明交代，详细字段、技术回执留在文件或可展开的处理过程。

## 工具与执行边界

工具：`export_query_presentation`、`export_query_spreadsheet`、`query_inventory_data`、`query_business_data`、`render_document`、`create_presentation_plan`、`write_presentation_slides`、`finalize_presentation`、`render_presentation`、`render_spreadsheet`、`fill_template`。

本 Skill 提供业务方法；工具参数校验、身份、任务草稿、交付记录由插件代码负责。只使用本轮实际提供的工具，未注册工具不能假装已经调用。任务结束时按真实工具与交付记录说明完成、部分完成或失败。
