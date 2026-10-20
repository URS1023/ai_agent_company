# 制造业务模块

面向维护本模块的开发、测试人员。入口为`router.py`；HTTP只做身份、入参和响应，`service.py`负责事务与资源归属，`assessment.py`和`maintenance.py`为可离线复算的纯函数。

## 已组装的业务链

1. 新建产品/设备 → 新建检验标准，保存指标的精确小数字符串与单位。
2. 新建检验任务，显式选择标准版本和设备或产品/批次。修改标准会追加修订，不改变既有任务。
3. 数据模块导入CSV或查询平台隔离数据源；`POST /inspections/{id}/imports`重执行保存的查询并映射到任务下样本。
4. `POST /inspections/{id}/runs`逐样本逐指标判定。缺样本、缺观测、单位不一致、低置信度分别保留，不默认合格。
5. 每次运行创建统一Task并保存不可变InspectionRun：queued→running→waiting_review；复核完成转succeeded。输入变更记录事件并取消旧待复核执行，原运行结果仍可查询。
6. 复核/偏差记录追加保存；复核后的任务不能覆盖证据，需要新建复检。导出实际结果CSV。

设备维护从测点`datasource_id + mapping.sql/parameters`取数，或调用维护分析接口显式指定受控查询。直接`rows`入参为有标记的导入证据入口，不能把它标成真实数据库来源。

## 关键接口

所有路径均以`/api/v1`开头；列表返回`items/total`，修改携带`revision`，过期返回409。

| 路径 | 方法 | 输入/作用 |
|---|---|---|
| `/devices`、`/products`、`/measurement-points` | GET/POST | 台账；测点含设备、单位、受控源及字段/SQL映射 |
| 上述路径`/{id}` | GET/PATCH/DELETE | 局部修改与软删除；未完成检验引用的主数据不能删除 |
| `/inspection-templates`、`/{id}/revisions` | GET/POST、GET | 标准及不可变修订；模板修改追加版本 |
| `/product-batches` | GET/POST | 产品批次；批次不能跨产品使用 |
| `/inspections`、`/{id}` | GET/POST、GET/PATCH/DELETE | 检验任务；固定对象和标准，名称/样本计划可修改 |
| `/inspections/{id}/samples` | GET/POST | 创建本任务样本并返回平台样本ID及最新任务版本 |
| `/inspections/{id}/samples/{sample_id}` | GET/PATCH/DELETE | 严格验证样本归属；软删保留原证据 |
| `/inspections/{id}/imports` | POST | `revision/query_id/sample_field/mappings`；每个样本一行的宽表映射 |
| `/inspections/{id}/results` | GET/POST | 原始计算、标准、复核；人工补录必须标manual |
| `/inspections/{id}/runs` | POST | 当前revision，重新按固定标准计算 |
| `/inspections/{id}/reviews`、`/deviations` | POST | revision、outcome、note；证据不全不能复核通过 |
| `/maintenance-analyses` | GET/POST | 数据源/SQL、设备、值/时间/负荷/两期字段、分箱与覆盖门槛 |
| `/alert-rules/{id}/dry-runs`、`/evaluations` | POST | 实测值/单位/含时区时间；试算不创建预警，执行可按窗口去重 |
| `/incidents/{id}/actions` | POST | acknowledge/comment；解决与重新打开由维修工单承担，不允许空记录直接关闭 |

数据库质检映射示例：

```json
{
  "revision": 1,
  "query_id": "服务端真实查询ID",
  "sample_field": "sample_id",
  "mappings": [
    {"metric_id": "weight", "value_field": "weight", "unit_field": "unit"},
    {"metric_id": "surface", "image_ref_field": "image_ref", "defects_field": "defects_json"}
  ]
}
```

## 口径与扩展

- 质量判定提取并适配自用户当前Test5快照的`backend/app/modules/quality/assessment.py`和`schemas.py`。精确数值用Decimal；外观需来源与显式缺陷数组，空数组和缺结果不同。不包含相机采集或缺陷模型训练。
- 维护比较按负荷分箱及显式配方/阶段等condition_fields匹配，剔除重复与冲突时间观测，只有两期样本足够的箱参与；使用当前样本权重计算两期均值，返回各箱样本、均值、覆盖率及不足原因。可配置上升/下降/双向变化，数据不足返回insufficient_data；每次分析关联统一Task，失败回滚后独立保留失败任务。它是状态变化证据，不输出故障概率、剩余寿命或自动控制命令。
- 新指标类型在`schemas.Metric`与纯判定中增加分支，并补相应边界测试。新采样来源只接数据模块受控查询，不在设备接口直接拼接连接字符串。
- 证据、标准修订、复核记录追加保存；删除业务主对象为软删除。页面字段/配置可增删改，历史判定仍引用当时的快照。

隔离验证见`tests/test_manufacturing_workflow.py`与`test_manufacturing_http.py`；仅创建测试目录下的新SQLite文件，没有连接旧数据库、局域网数据库、模型或原业务服务。

## 显式合成数据与来源预警

`simulation_generator.py`精提Test5 equipment_simulation/generator.py纯算法，`simulation_fields.py`仅提取76字段，不导入旧schema/runner/数据库。管理员创建`food-equipment-v1`激活批次，再以每次最多10步推进；总计最多120步×8模拟设备，有总计算预算、心跳、revision和明确取消。默认不启动，不后台无限循环，最终只写平台新隔离导入源，数据集source_kind为synthetic，设备名称/质量字段也保留模拟标识。

接口：`POST /data-generators/{id}/activations`；`GET /data-generator-activations/{id}`；`POST /data-generator-activations/{id}/advances`和`/cancellations`。

`POST /alert-rules/{id}/source-evaluations`重跑已授权查询，按映射字段取得最新含时区观测，验证单位再判定。预警保存query来源；重复读取同一时间同一值不累加次数，不同时间在窗口内合并并追加事件。手填试算继续标记manual。

## 复检、质量处置与维修闭环（T-domain12）

`quality_lifecycle.py`复用现有检验、样本和运行，只新增关系/处置表。`maintenance_work.py`复用预警、同工况分析和统一Task；`workflow.py`负责同工作区有效人员、历史运行授权及固定标准核验。事务由请求会话统一提交，revision比较更新失败返回409；不在内部提交半个业务结果或自动重试。

| 路径 | 输入/结果 |
|---|---|
| `POST /inspections/{id}/reinspections` | `revision,run_id,name,reason,sample_ids?,case_id?`；父任务须已复核，固定父标准，不复制测量结果。省略sample_ids复检异常与缺失；显式选择须为原运行样本。返回新Inspection。 |
| `GET /inspections/{id}/lineage` | 完整首检与后续复检`items/links/total`；全部来源重授权，不默默截断。 |
| `GET/POST /quality-cases` | 创建含`inspection_id,run_id,revision,name,note`；一运行一处置单。列表支持`target_type,inspection_id,status,limit,offset`。 |
| `GET /quality-cases/{id}`、`POST /quality-cases/{id}/actions` | 详情含人员名和追加events；动作含`revision,action,note`及当前动作所需字段。 |
| `POST /incidents/{id}/work-orders`、`POST /maintenance-analyses/{id}/work-orders` | `revision,name,note`；一来源一工单，分析须有attention证据。固定原预警标准，缺快照须重新评估。 |
| `GET /maintenance-work-orders`、`/{id}`、`POST /{id}/actions` | 列表支持`q,status,device_id,limit,offset`；详情含events、source_snapshot、verification_snapshot。Incident/Analysis投影work_order_id便于直接进入。 |
| `GET /manufacturing-assignees?area=quality或maintenance` | 当前组织/工作区未停用且具m11.write或m10.write的人员，仅返回id/name。 |
| `GET /quality-statistics` | `target_type,start_at,end_at`；首检用原任务首run，复检用子任务末run，按样本计数。缺样本留分母，让步/报废不改写系统合格率。日期先筛再预算；超过2000任务明确报错。 |
| `/quality-categories`、`/{id}` | GET/POST/PATCH/DELETE；target_type区分设备/产品，parent_id树拒绝循环，关联对象或子类别未移出不能删。 |
| `/quality-category-resources`、`POST /quality-category-bindings` | 资源类型device/product/template；设置类别含`category_id,resource_type,resource_id,revision?`，已有绑定须带版本。DELETE绑定软删，重新绑定仍校验原版本。 |

质量状态：open→contained（可选）→assigned→in_progress→awaiting_reinspection（返工/维修）或awaiting_review（报废/退回/让步）→closed。返工/维修必须关联复检全部完整合格且已复核，才能提交到待复核；失败复检可再建，旧链不删除。复核退回in_progress；重新打开建新Task保留旧Task与事件。

维修状态：open→assigned→in_progress→awaiting_review→resolved。责任人执行开始和提交维修；另一名有m10.publish的人员复核。预警复测使用维修后生成的完整授权查询，观测也须晚于维修、单位相同、满足原阈值恢复；原来源是数据库查询时数据源和指标映射不可变。维护分析复测须同设备、维修后新normal分析、同指标与工况比较口径。数据不足不能关闭。

质量verify/reject需要m11.publish，维修verify/reject需要m10.publish；执行人员即使管理员也不能复核自己的处置。所有历史来源每次读、操作、列表及统计重授权，撤权记录从可见列表与total排除；真正损坏的证据仍反馈错误。关键验证在`test_manufacturing_lifecycle.py`，全部使用新临时SQLite/显式Mock。
