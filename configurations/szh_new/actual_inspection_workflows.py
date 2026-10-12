"""Add source inspection records to an existing device canvas.

Record linkage stays in the dataset; all result decisions are visible nodes.
Inspection dates never serve as lubrication completion dates.
"""
from copy import deepcopy
from value_workflows import Builder, calc, compare, expr, missing

SQL = """WITH records AS (
 SELECT CONCAT('point:',d.deviceInspectionDetailsId) recordKey,
 :deviceKey deviceKey,'inspection-record' category,
 '巡检点明细' AS `记录来源`,COALESCE(m.c_xunjianjihua,'未填写计划名称') AS `计划`,
 d.c_inspectionName AS `检查项目`,d.c_inspectionIndicators AS `检查指标`,
 d.c_inspectionContent AS `检查要求`,d.c_inspectionResult AS `原巡检结果`,
 d.c_inspectionDesc AS `实测说明`,m.c_inspectionStatus AS `任务巡检状态`,
 m.c_recordTime AS `记录时间`,COALESCE(m.c_recordTime,m.createdOn) sortTime,
 m.deviceInspectionMaintenanceId AS `原任务`,d.deviceInspectionDetailsId AS `原记录`
 FROM t_deviceinspectiondetails d JOIN t_deviceinspectionmaintenance m
 ON m.deviceInspectionMaintenanceId=d.mdDeviceInspectionMaintenanceId AND m.isDeleted=0
 WHERE d.isDeleted=0 AND :deviceKey<>''
 AND (d.c_shebei=:deviceKey OR ((d.c_shebei IS NULL OR d.c_shebei='') AND m.c_deviceId=:deviceKey))
 AND COALESCE(m.c_recordTime,m.createdOn)<=:asOf
 UNION ALL
 SELECT CONCAT('area:',d.quyuleixingshebeibaoyangmingxiId),:deviceKey,'inspection-record',
 '区域或类型巡检明细',COALESCE(m.c_xunjianjihua,'未填写计划名称'),
 '设备巡检','整体巡检结果',NULL,d.c_xunjianjieguo,d.c_xunjianjieguobeizhu,
 m.c_inspectionStatus,m.c_recordTime,COALESCE(m.c_recordTime,m.createdOn),
 m.deviceInspectionMaintenanceId,d.quyuleixingshebeibaoyangmingxiId
 FROM t_quyuleixingshebeibaoyangmingxi d JOIN t_deviceinspectionmaintenance m
 ON m.deviceInspectionMaintenanceId=d.mdDeviceInspectionMaintenanceId AND m.isDeleted=0
 WHERE d.isDeleted=0 AND :deviceKey<>'' AND d.c_shebei=:deviceKey
 AND COALESCE(m.c_recordTime,m.createdOn)<=:asOf
), ranked AS (
 SELECT r.*,ROW_NUMBER() OVER (PARTITION BY `记录来源`,`计划`,`检查项目`,`检查指标`
 ORDER BY sortTime DESC,recordKey DESC) latestRow,
 ROW_NUMBER() OVER (PARTITION BY `记录来源`,`计划`,`检查项目`,`检查指标`,
 CASE WHEN `原巡检结果` IN (1,2) THEN 1 ELSE 0 END ORDER BY sortTime DESC,recordKey DESC) resultRow,
 COUNT(*) OVER (PARTITION BY `记录来源`,`计划`,`检查项目`,`检查指标`) AS `历史记录数`
 FROM records r
)
SELECT r.recordKey,r.deviceKey,r.category,v.c_deviceName AS `设备`,
 r.`记录来源`,r.`计划`,r.`检查项目`,r.`检查指标`,r.`检查要求`,r.`原巡检结果`,
 r.`实测说明`,r.`任务巡检状态`,r.`记录时间`,r.`原任务`,r.`原记录`,r.`历史记录数`,
 CASE WHEN latestRow=1 THEN '最新任务记录' ELSE '最近已填写结果的历史记录' END AS `记录范围`,
 :asOf AS `参考时间`
 FROM ranked r JOIN t_devicemanagement v ON v.deviceManagementId=r.deviceKey AND v.isDeleted=0
 WHERE latestRow=1 OR (`原巡检结果` IN (1,2) AND resultRow=1)
 ORDER BY r.`记录来源`,r.`计划`,r.`检查项目`,r.`记录时间` DESC,r.recordKey
"""

COLUMNS = ['设备','记录范围','记录时间','记录来源','计划','检查项目','检查指标','检查要求',
           '原巡检结果','实测说明','任务巡检状态','历史记录数','原任务','原记录','参考时间']

def add_actual_inspections(graph, dataset, source, device_id):
    graph=deepcopy(graph)
    if any(n['id']=='actual_inspection_read' for n in graph['nodes']): return graph
    b=Builder({'actual_inspection':dataset},source,device_id)
    read=b.read('actual_inspection','读取值 · 本设备实际巡检记录',0)
    calculated=b.calculate('actual_inspection_calc','计算值 · 原始巡检结果对应含义',[
        calc('原记录结论','lookup','原巡检结果',mapping={'1':'正常','2':'异常'}),
        calc('任务状态含义','lookup','任务巡检状态',mapping={'1':'已巡检','2':'未巡检'})],read,660,0)
    outputs=b.chain('actual_inspection',calculated,[
        ('判断值 · 原始巡检结果是否填写',missing('原记录结论'),
         'pending','这条巡检记录未填写可识别的正常/异常结果，请核对本行记录时间及原任务。'),
        ('判断值 · 原记录是否为异常',compare('eq','原记录结论','异常'),
         'warning','原始巡检记录填写为异常；请按本行记录时间和实测说明核对处置情况。'),
        ('判断值 · 任务是否完成',expr('or',missing('任务状态含义'),compare('ne','任务状态含义','已巡检')),
         'pending','此项已填写正常，但所属巡检任务尚未确认完成。'),
    ],0,('normal','原记录正常','原始巡检记录填写为正常，任务已巡检；结论仅对应本行记录时间，不代表今天现场状态。'))
    y=max(n.get('position',{}).get('y',0) for n in graph['nodes'])+430
    for node in b.nodes[1:]:
        node['position']['y']+=y
        graph['nodes'].append(node)
    graph['edges'].extend(b.edges)
    for output in outputs:
        graph['edges'].append({'id':output+'_out_results','source':output,'target':'results','port':'out'})
    return graph
