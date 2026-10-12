"""Read-only datasets for visual nodes. No pass/fail, elapsed time or thresholds
are evaluated here: those operations belong to the saved workflow graphs.

SQL only relates source records to their own plan/item, selecting the latest
record and retaining raw statuses. Device scope comes from explicit plans.
"""


def definitions(device_keys, repair_keys):
    def literals(keys):
        assert keys and all(len(k) <= 40 and k.replace('-', '').isalnum() for k in keys)
        return ','.join("'" + k + "'" for k in keys)

    scope = literals(device_keys)
    base = f"""SELECT i.c_device deviceKey,v.c_deviceName deviceName,
        p.dInspectionMaintenancePlanId planId,p.c_planName planName,
        p.c_xunjianzhibiao cycleCode,p.c_jihualeixing planType,
        (SELECT COUNT(*) FROM t_dinspectionmaintenanceplan p2
         WHERE p2.isDeleted=0 AND p2.c_planName=p.c_planName) sameNameCount
        FROM t_dinspectionmaintenanceplan p JOIN t_inspectiondevice i
        ON i.mdDInspectionMaintenancePlanId=p.dInspectionMaintenancePlanId AND i.isDeleted=0
        JOIN t_devicemanagement v ON v.deviceManagementId=i.c_device AND v.isDeleted=0
        WHERE p.isDeleted=0 AND p.c_jihualeixing IN (1,2)
        AND i.c_device IN ({scope}) AND (:deviceKey='' OR i.c_device=:deviceKey)"""
    inspection_tasks = f"""SELECT m.deviceInspectionMaintenanceId taskId,m.c_deviceId deviceKey,
        m.c_xunjianjihua planName,m.c_inspectionStatus taskStatus,m.c_recordTime recordedAt,
        MAX(CASE WHEN m.c_inspectionStatus=1 THEN m.c_recordTime ELSE NULL END)
          OVER (PARTITION BY m.c_deviceId,m.c_xunjianjihua) lastCompleted,
        ROW_NUMBER() OVER (PARTITION BY m.c_deviceId,m.c_xunjianjihua
          ORDER BY COALESCE(m.c_recordTime,m.createdOn) DESC,m.deviceInspectionMaintenanceId DESC) rowNum
        FROM t_deviceinspectionmaintenance m WHERE m.isDeleted=0 AND m.c_jihualeixing=1
        AND m.c_deviceId IN ({scope}) AND COALESCE(m.c_recordTime,m.createdOn)<=:asOf"""
    cycle = f"""WITH plans AS ({base}), tasks AS ({inspection_tasks})
        SELECT CONCAT(p.deviceKey,':',p.planId,':cycle') recordKey,p.deviceKey,p.planId,'cycle' itemId,
        'inspection' category,p.deviceName AS `设备`,p.planName AS `计划`,
        '巡检周期' AS `检查项目`,p.cycleCode AS `周期编码`,p.sameNameCount AS `同名计划数`,
        t.lastCompleted AS `上次完成时间`,:asOf AS `参考时间`
        FROM plans p LEFT JOIN tasks t ON t.deviceKey=p.deviceKey AND t.planName=p.planName AND t.rowNum=1
        WHERE p.planType=1 ORDER BY p.deviceKey,p.planId"""
    inspection = f"""WITH plans AS ({base}), tasks AS ({inspection_tasks})
        SELECT CONCAT(p.deviceKey,':',p.planId,':',s.detailsOfInspectionIndicatorsId) recordKey,
        p.deviceKey,p.planId,s.detailsOfInspectionIndicatorsId itemId,'inspection' category,
        p.deviceName AS `设备`,p.planName AS `计划`,p.sameNameCount AS `同名计划数`,
        s.c_inspectionName AS `检查项目`,s.c_inspectionIndicators AS `指标`,s.c_inspectionContent AS `要求`,
        t.taskStatus AS `任务状态`,t.recordedAt AS `记录时间`,:asOf AS `参考时间`,
        d.c_inspectionResult AS `检查结果`,d.c_inspectionDesc AS `实际值`,
        d.c_inspectionContent AS `记录要求`,d.deviceInspectionDetailsId AS `原记录`,
        COUNT(d.deviceInspectionDetailsId) OVER (PARTITION BY p.deviceKey,p.planId,s.detailsOfInspectionIndicatorsId) AS `匹配记录数`
        FROM plans p JOIN t_detailsofinspectionindicators s
        ON s.mdDInspectionMaintenancePlanId=p.planId AND s.isDeleted=0
        LEFT JOIN tasks t ON t.deviceKey=p.deviceKey AND t.planName=p.planName AND t.rowNum=1
        LEFT JOIN t_deviceinspectiondetails d ON d.mdDeviceInspectionMaintenanceId=t.taskId AND d.isDeleted=0
        AND (d.c_shebei IS NULL OR d.c_shebei='' OR d.c_shebei=p.deviceKey)
        AND d.c_inspectionName=s.c_inspectionName AND d.c_inspectionIndicators=s.c_inspectionIndicators
        WHERE p.planType=1 ORDER BY p.deviceKey,p.planId,itemId,d.deviceInspectionDetailsId"""
    maintenance = f"""WITH plans AS ({base}), linked AS (
        SELECT p.deviceKey,p.planId,p.deviceName,p.planName,p.sameNameCount,
        s.xunjianjihuarunhuabaoyangId itemId,s.c_runhuadianweimingcheng point,
        s.c_runhuadianweibianhao pointCode,s.c_runhuazhouqi days,
        s.c_runhuayoupaihao oil,s.c_runhualiang amount,s.c_runhuafangshi method,
        s.c_huanyouzhouqi changeCycle,s.c_huanyouliang changeAmount,
        m.c_maintenanceStatus taskStatus,m.c_recentMaintenancePeriod completedAt,
        d.c_baoyangzhuangtai pointStatus,d.c_runhuayoupaihao actualOil,
        d.c_runhualiang actualAmount,d.c_runhuafangshi actualMethod,
        d.shebeirunhuabaoyangmingxibiaoId sourceRecord,
        MAX(CASE WHEN m.c_maintenanceStatus=1 AND d.c_baoyangzhuangtai=1
            AND m.c_recentMaintenancePeriod<=:asOf THEN m.c_recentMaintenancePeriod ELSE NULL END)
          OVER (PARTITION BY p.deviceKey,p.planId,s.xunjianjihuarunhuabaoyangId) lastCompleted,
        ROW_NUMBER() OVER (PARTITION BY p.deviceKey,p.planId,s.xunjianjihuarunhuabaoyangId
            ORDER BY m.createdOn DESC,m.deviceInspectionMaintenanceId DESC,d.shebeirunhuabaoyangmingxibiaoId DESC) rowNum
        FROM plans p JOIN t_xunjianjihuarunhuabaoyang s
        ON s.mdDInspectionMaintenancePlanId=p.planId AND s.isDeleted=0
        LEFT JOIN t_deviceinspectionmaintenance m ON m.c_deviceId=p.deviceKey
        AND m.c_xunjianjihua=p.planName AND m.isDeleted=0 AND m.createdOn<=:asOf
        LEFT JOIN t_shebeirunhuabaoyangmingxibiao d ON d.mdDeviceInspectionMaintenanceId=m.deviceInspectionMaintenanceId
        AND d.isDeleted=0 AND ((s.c_runhuadianweibianhao IS NOT NULL AND s.c_runhuadianweibianhao<>''
          AND d.c_runhuadianweibianhao=s.c_runhuadianweibianhao)
          OR ((s.c_runhuadianweibianhao IS NULL OR s.c_runhuadianweibianhao='')
          AND d.c_runhuadianweimingcheng=s.c_runhuadianweimingcheng))
        WHERE p.planType=2)
        SELECT CONCAT(deviceKey,':',planId,':',itemId) recordKey,deviceKey,planId,itemId,'maintenance' category,
        deviceName AS `设备`,planName AS `计划`,sameNameCount AS `同名计划数`,
        point AS `润滑点`,pointCode AS `点位编号`,days AS `周期天数`,
        oil AS `要求油品`,amount AS `要求用量`,method AS `要求方式`,
        changeCycle AS `换油周期`,changeAmount AS `换油量`,lastCompleted AS `上次完成时间`,
        taskStatus AS `任务状态`,pointStatus AS `点位状态`,completedAt AS `本次完成时间`,
        actualOil AS `记录油品`,actualAmount AS `记录用量`,actualMethod AS `记录方式`,
        sourceRecord AS `原记录`,:asOf AS `参考时间`
        FROM linked WHERE rowNum=1 ORDER BY deviceKey,planId,itemId"""
    repair = f"""SELECT CONCAT(m.c_zhubiaoshebei,':repair:',m.equipmentMaintenanceLogId) recordKey,
        m.c_zhubiaoshebei deviceKey,m.c_weixiujihuamingcheng planId,m.equipmentMaintenanceLogId itemId,
        'repair' category,v.c_deviceName AS `设备`,m.c_jianxiudianmingcheng AS `维修点`,
        m.c_jianxiuneirong AS `维修内容`,m.c_weixiuzhuangtai AS `维修状态`,
        m.c_yujichuliwanchengshijian AS `预计完成时间`,m.c_shijichuliwanchengshijian AS `实际完成时间`,
        m.c_daochangyanshoushifouhege AS `验收状态`,m.c_shifouanzhuangwancheng AS `安装状态`,:asOf AS `参考时间`
        FROM t_equipmentmaintenancelog m JOIN t_devicemanagement v ON v.deviceManagementId=m.c_zhubiaoshebei AND v.isDeleted=0
        WHERE m.isDeleted=0 AND m.c_zhubiaoshebei IN ({literals(repair_keys)})
        AND (:deviceKey='' OR m.c_zhubiaoshebei=:deviceKey) AND m.createdOn<=:asOf
        ORDER BY m.c_zhubiaoshebei,m.equipmentMaintenanceLogId"""
    return {
        'cycle': ('巡检计划与上次完成时间', cycle),
        'inspection': ('巡检项目要求与最新实际记录', inspection),
        'maintenance': ('润滑点周期要求与实际保养记录', maintenance),
        'repair': ('维修任务期限与完成验收记录', repair),
    }
