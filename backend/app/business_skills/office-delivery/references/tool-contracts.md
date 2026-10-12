# 现有工具契约

## Excel
数据库查询结果优先调用 export_query_spreadsheet：
```json
{"query_id":"本任务真实成功查询ID","name":"业务数据.xlsx"}
```
工具直接导出保存的原始行，带数据来源页、截断范围和宋体，不重新查询或让模型抄写数值。
非查询数据的整理表调用 render_spreadsheet：
```json
{"name":"巡检明细","spec":{"sheet_name":"巡检明细","columns":["设备","计划时间","巡检状态"],"data":[]}}
```
data用本轮真实数据替换。无数据时保留表头并在报告说明，不能补造记录。

## Word与内嵌图片
查询结果图表先使用 render_chart_image，列名必须来自对应查询回执：
```json
{"query_id":"本任务真实成功查询ID","chart_type":"pie","label_column":"巡检状态","value_column":"设备数量","title":"本次范围内巡检状态"}
```
单行聚合结果可使用 value_columns 数组代替 label_column/value_column；饼图只能选择互斥组成项，不能把“总量”和“未检子集”并列。
大屏先保存成功，再调用 render_dashboard_image(report_id)。返回的是带查询来源的宋体数据快照，非实时数据或交互页面截图；在Word内明确注明。
调用render_document的document_spec.sections[].blocks，图片块示例：
```json
{"type":"image","artifact_id":"真实图片工件ID"}
```
图片需在当前工作区可访问，不能把图片地址仅写成文字段落。交付核验使用可信工具来源和Word实际图片引用，改文件名不能把普通图片变成大屏证明。

## PPT
已有查询的数据报告优先 `export_query_presentation`，与 Excel 使用同一批 `queryId`：
```json
{"query_ids":["本任务真实成功查询ID"],"title":"业务数据报告","name":"业务数据报告.pptx"}
```
工具直接从保存查询分页展示全部返回行列，各来源独立记录时间和截断范围；长字段全文在本页备注，不经模型重抄。实际页数不能满足用户明确固定要求时说明原因，不删行凑页。

ERP 介绍或参考版式方案等内容型 PPT 使用 create_presentation_plan。page_count 是总页数；默认包含封面和结尾，但用户逐页指定全部主题时按要求设置 include_cover=false、include_closing=false。封尾均启用的15页稿写正文1—13；全部逐页指定的15页稿写15页正文。write_presentation_slides参数slides中的页面例如：
```json
{"slide_number":1,"layout":"content","title":"巡检覆盖范围","items":[{"title":"范围","description":"填写已确认的日期和设备范围"},{"title":"依据","description":"填写计划与完成记录来源"},{"title":"边界","description":"填写缺失数据与不能确认的指标"}]}
```
notes不是正文；图表填写chart.categories与series[].values；不要只传series:[]。
同任务多份内容型 PPT 分别创建大纲，记录各自返回的 `draft_id`。写页参数为 `{"draft_id":"真实草稿ID","slides":[...]}`，完成参数为 `{"draft_id":"真实草稿ID","name":"对应文件名"}`；不能只改文件名就把同一个草稿当成第二份内容。续改时沿用原任务和该稿 ID，同标题重复建纲会复用原稿。
读取每批rejected_slides与缺页进度，修正具体页；最后finalize_presentation返回真实PPT工件才算完成。

## 大屏
已有成功查询且未指定设计模板时，调用 create_dashboard_from_queries(query_ids,title)，直接以原始结果建立可打开的静态大屏，打开时不会自动重新查询。
指定设计模板或复杂布局时调用 create_business_dashboard，沿用本轮已确认来源和指标。先取得可打开的大屏ID，再用 render_dashboard_image 制作对应数据快照图片用于Word；大屏、图片、Word是不同交付物。
缺少数据源时明确范围；不得用随机requestKey重复触发已经失败的SQL生成或创建操作。
export_inspection_report只统计已保存检查报告覆盖的检查项，并非任意应检设备统计工具。
