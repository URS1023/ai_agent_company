"""Bounded read-only device snapshots for the compact scheduled workflow.

职责：为"设备巡检/保养/维修"业务提供**只读**采集能力 —— 用固定的 SQL 模板从设备源库
（MySQL）按设备 key 与时间窗口抓取计划、明细、润滑要求和维修记录，组装成一份
``device-monitor-v1`` 快照，交给画布节点展示与判定。

位置与依赖：被 ``alerts.router`` 与 ``alerts.device_catalog`` 调用；通过
``analytics.execute_query`` 执行查询（因此受数据源授权、只读校验与行数上限约束），
本身不写库、不产生审计记录。

重要约束：
- 只支持 provider 为 ``mysql`` 的设备源库，且表名/列名按现场库结构硬编码，属专用采集方案。
- ``keys`` 必须是设备台账 ``deviceKey`` 对应的源库标识，1–30 个、且匹配
  ``[A-Za-z0-9_-]{1,80}``；SQL 中直接内联这些 key（正则已排除引号），其余时间条件亦为
  拼接字面量，**因此调用方不得放宽该正则**。
- 结果被截断（``truncated``）时抛 ``ValidationError`` 而不是静默截断，避免"看到的记录不全
  却当成正常"。
- 缺记录不等于正常：无计划/无记录时快照会带 ``scope`` 说明并返回空数据集。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re

from app.errors import ValidationError
from app.modules.analytics.queries import execute_query
from app.modules.analytics.schemas import QueryInput
from app.modules.analytics.sources import get_source


def snapshot_queries(keys, since, until):
    """按设备 key 与时间窗口生成各数据集的只读 SQL 模板。

    参数：``keys`` 为源库设备标识列表（1–30 个，正则白名单校验）；``since`` / ``until``
    为 ``'YYYY-MM-DD HH:MM:SS'`` 字面量，直接拼进 SQL（它们由本模块用
    ``datetime.isoformat`` 生成，不接受外部字符串）。

    返回：``{数据集名: SQL}``，键包括 plans / inspection / areaInspection /
    maintenanceRequirements / maintenance / repair；调用方（``collect_snapshot``）再按
    ``category`` 过滤允许的数据集。

    异常：``keys`` 为空、超过 30 个或含白名单外字符时抛 ``ValidationError``。

    业务口径（勿随意改动，影响"最近一次"的判定）：
    - 巡检/区域巡检的明细只取所属任务 ``MAX(c_recordTime)`` 的那一批（``latest`` CTE），
      因此"历史很久没做"会被识别为上次完成时间而非无记录；
    - 保养以 ``MAX(c_recentMaintenancePeriod)`` 作为最近完成时点，且只认
      ``c_jihualeixing=2``（保养计划）；
    - 维修返回"当前未完成/失败任务（状态 1/2/4）"加上窗口内新增或更新的记录。
    性能约束：只聚合窄字段（key/时间/名称），不排序宽明细或 JSON 列。
    """
    if not keys or len(keys) > 30 or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", k) for k in keys):
        raise ValidationError("设备范围必须为 1–30 个明确的源库标识")
    ids = ','.join("'" + k + "'" for k in keys)
    window = f"COALESCE(m.modifiedOn,m.createdOn) >= '{since}' AND COALESCE(m.modifiedOn,m.createdOn) < '{until}'"
    # Aggregate only narrow task keys/times, never sort wide detail/JSON rows.
    names = f"""SELECT p.c_planName FROM t_inspectiondevice i JOIN t_dinspectionmaintenanceplan p
      ON p.dInspectionMaintenancePlanId=i.mdDInspectionMaintenancePlanId AND p.isDeleted=0
      WHERE i.isDeleted=0 AND i.c_device IN ({ids})"""
    latest_inspection = f"""WITH latest AS (SELECT COALESCE(c_deviceId,'') deviceKey,c_xunjianjihua planName,
      c_jihualeixing planType,c_recordType cycleCode,MAX(c_recordTime) completedAt
      FROM t_deviceinspectionmaintenance WHERE isDeleted=0 AND c_inspectionStatus=1
      AND c_recordTime <= '{until}'
      AND (c_deviceId IN ({ids}) OR (c_jihualeixing IN (3,4) AND c_xunjianjihua IN ({names})))
      GROUP BY COALESCE(c_deviceId,''),c_xunjianjihua,c_jihualeixing,c_recordType) """
    latest_join = """ JOIN latest l ON l.deviceKey=COALESCE(m.c_deviceId,'') AND l.planName=m.c_xunjianjihua
      AND l.planType=m.c_jihualeixing AND l.cycleCode=m.c_recordType AND l.completedAt=m.c_recordTime """
    latest_maintenance = f"""WITH latest AS (SELECT c_deviceId deviceKey,c_xunjianjihua planName,
      MAX(c_recentMaintenancePeriod) completedAt FROM t_deviceinspectionmaintenance
      WHERE isDeleted=0 AND c_jihualeixing=2 AND c_maintenanceStatus=1 AND c_deviceId IN ({ids})
      AND c_recentMaintenancePeriod <= '{until}'
      GROUP BY c_deviceId,c_xunjianjihua) """
    return {
        'plans': f"""SELECT i.c_device deviceKey,p.dInspectionMaintenancePlanId planId,
          p.c_planName planName,p.c_jihualeixing planType,p.c_xunjianzhibiao cycleCode,
          p.c_baoyangzhouqi maintenanceCycle,p.c_xiaoshi hours
          FROM t_inspectiondevice i JOIN t_dinspectionmaintenanceplan p
          ON p.dInspectionMaintenancePlanId=i.mdDInspectionMaintenancePlanId AND p.isDeleted=0
          WHERE i.isDeleted=0 AND i.c_device IN ({ids})""",
        'inspection': latest_inspection + f"""SELECT COALESCE(NULLIF(d.c_shebei,''),m.c_deviceId) deviceKey,
          m.deviceInspectionMaintenanceId taskId,m.c_xunjianjihua planName,m.c_jihualeixing planType,
          m.c_recordType cycleCode,m.c_inspectionStatus taskStatus,m.c_recordTime completedAt,
          d.deviceInspectionDetailsId recordId,d.c_inspectionName point,
          d.c_inspectionIndicators standard,d.c_inspectionContent requirement,
          d.c_inspectionResult result,d.c_inspectionDesc actual
          FROM t_deviceinspectiondetails d JOIN t_deviceinspectionmaintenance m
          ON m.deviceInspectionMaintenanceId=d.mdDeviceInspectionMaintenanceId AND m.isDeleted=0
          {latest_join}
          WHERE d.isDeleted=0 AND m.c_inspectionStatus=1 AND COALESCE(NULLIF(d.c_shebei,''),m.c_deviceId) IN ({ids})""",
        'areaInspection': latest_inspection + f"""SELECT d.c_shebei deviceKey,m.deviceInspectionMaintenanceId taskId,
          m.c_xunjianjihua planName,m.c_jihualeixing planType,m.c_recordType cycleCode,
          m.c_inspectionStatus taskStatus,m.c_recordTime completedAt,
          d.quyuleixingshebeibaoyangmingxiId recordId,d.c_xunjianjieguo result,
          d.c_xunjianjieguobeizhu actual
          FROM t_quyuleixingshebeibaoyangmingxi d JOIN t_deviceinspectionmaintenance m
          ON m.deviceInspectionMaintenanceId=d.mdDeviceInspectionMaintenanceId AND m.isDeleted=0
          {latest_join}
          WHERE d.isDeleted=0 AND m.c_inspectionStatus=1 AND d.c_shebei IN ({ids})""",
        'maintenanceRequirements': f"""SELECT i.c_device deviceKey,p.dInspectionMaintenancePlanId planId,
          p.c_planName planName,s.xunjianjihuarunhuabaoyangId itemId,
          s.c_runhuadianweimingcheng point,s.c_runhuadianweibianhao pointCode,
          s.c_runhuazhouqi days,s.c_runhuayoupaihao oil,s.c_runhualiang amount,s.c_runhuafangshi method,
          s.c_huanyouzhouqi oilChangeCycle
          FROM t_inspectiondevice i JOIN t_dinspectionmaintenanceplan p
          ON p.dInspectionMaintenancePlanId=i.mdDInspectionMaintenancePlanId AND p.isDeleted=0
          JOIN t_xunjianjihuarunhuabaoyang s ON s.mdDInspectionMaintenancePlanId=p.dInspectionMaintenancePlanId
          AND s.isDeleted=0 WHERE i.isDeleted=0 AND p.c_jihualeixing=2 AND i.c_device IN ({ids})""",
        'maintenance': latest_maintenance + f"""SELECT m.c_deviceId deviceKey,m.c_xunjianjihua planName,
          m.deviceInspectionMaintenanceId taskId,m.c_maintenanceStatus taskStatus,
          m.c_recentMaintenancePeriod completedAt,d.shebeirunhuabaoyangmingxibiaoId recordId,
          d.c_runhuadianweimingcheng point,d.c_runhuadianweibianhao pointCode,
          d.c_baoyangzhuangtai pointStatus,d.c_runhuayoupaihao oil,d.c_runhualiang amount,d.c_runhuafangshi method
          FROM t_deviceinspectionmaintenance m JOIN t_shebeirunhuabaoyangmingxibiao d
          ON d.mdDeviceInspectionMaintenanceId=m.deviceInspectionMaintenanceId AND d.isDeleted=0
          JOIN latest l ON l.deviceKey=m.c_deviceId AND l.planName=m.c_xunjianjihua AND l.completedAt=m.c_recentMaintenancePeriod
          WHERE m.isDeleted=0 AND m.c_jihualeixing=2 AND m.c_maintenanceStatus=1 AND m.c_deviceId IN ({ids})""",
        'repair': f"""SELECT m.c_zhubiaoshebei deviceKey,m.equipmentMaintenanceLogId recordId,
          m.c_jianxiudianmingcheng point,m.c_jianxiuneirong requirement,m.c_weixiuzhuangtai status,
          m.c_yujichuliwanchengshijian deadline,m.c_shijichuliwanchengshijian completedAt,
          m.c_daochangyanshoushifouhege acceptance,m.c_shifouanzhuangwancheng installation
          FROM t_equipmentmaintenancelog m WHERE m.isDeleted=0 AND m.c_zhubiaoshebei IN ({ids})
          AND m.createdOn < '{until}' AND (m.c_weixiuzhuangtai IN (1,2,4)
          OR (COALESCE(m.modifiedOn,m.createdOn) >= '{since}' AND COALESCE(m.modifiedOn,m.createdOn) < '{until}'))""",
    }


def changed_task_query(keys, category):
    """Union changed task keys before joining; never fan out two detail tables.

    生成"窗口内新增/更新任务"清单 SQL（增量采集用）。三个 UNION 分支分别覆盖：
    主表自身变更、明细表变更、以及巡检专用的区域巡检明细表变更。

    参数：``keys`` 同上（1–30 个白名单 key）；``category`` 只区分 ``maintenance``（只看
    ``c_jihualeixing=2``）与其他值（``IN (1,3,4)``，即巡检口径）。
    ``:windowStart`` / ``:windowEnd`` 用绑定参数传入（外层 ``execute_query`` 负责绑定）。

    返回一条以 ``WITH changed AS (...)`` 开头的 SQL；不返回行数据。

    约束：设备可能通过"主表 c_deviceId"或"明细表 c_shebei"任一关系归属，故分支必须并集，
    不能只按主表过滤；同一条任务在多个分支重复出现由外层 ``changed`` 的去重语义与
    后续按 taskId JOIN 折叠，故此处不做 DISTINCT（会影响索引）。
    """
    if not keys or len(keys) > 30 or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", k) for k in keys):
        raise ValidationError("设备范围必须为 1–30 个明确的源库标识")
    ids = ','.join("'" + k + "'" for k in keys)
    period = lambda alias: (f"COALESCE({alias}.modifiedOn,{alias}.createdOn) >= :windowStart "
                            f"AND COALESCE({alias}.modifiedOn,{alias}.createdOn) < :windowEnd")
    kind = '=2' if category == 'maintenance' else 'IN (1,3,4)'
    table = 't_shebeirunhuabaoyangmingxibiao' if category == 'maintenance' else 't_deviceinspectiondetails'
    # A parent change can belong to a device through either detail relationship.
    scopes = [f"SELECT m.deviceInspectionMaintenanceId taskId FROM t_deviceinspectionmaintenance m "
              f"WHERE m.isDeleted=0 AND m.c_deviceId IN ({ids}) AND m.c_jihualeixing {kind} AND {period('m')}"]
    related = f"m.c_deviceId IN ({ids})" if category == 'maintenance' else f"(m.c_deviceId IN ({ids}) OR d.c_shebei IN ({ids}))"
    scopes.append(f"SELECT d.mdDeviceInspectionMaintenanceId taskId FROM {table} d "
        "JOIN t_deviceinspectionmaintenance m ON m.deviceInspectionMaintenanceId=d.mdDeviceInspectionMaintenanceId "
        f"WHERE m.isDeleted=0 AND d.isDeleted=0 AND m.c_jihualeixing {kind} AND {related} "
        f"AND (({period('d')}) OR ({period('m')}))")
    if category == 'inspection':
        scopes.append("SELECT a.mdDeviceInspectionMaintenanceId taskId FROM t_quyuleixingshebeibaoyangmingxi a "
            "JOIN t_deviceinspectionmaintenance m ON m.deviceInspectionMaintenanceId=a.mdDeviceInspectionMaintenanceId "
            f"WHERE m.isDeleted=0 AND a.isDeleted=0 AND m.c_jihualeixing {kind} AND a.c_shebei IN ({ids}) "
            f"AND (({period('a')}) OR ({period('m')}))")
    return '''WITH changed AS (''' + ' UNION '.join(scopes) + ''')
        SELECT m.deviceInspectionMaintenanceId taskId,m.c_deviceId deviceKey,
        m.c_xunjianjihua planName,m.c_inspectionStatus inspectionStatus,m.c_maintenanceStatus maintenanceStatus,
        m.c_recordTime inspectionTime,m.c_recentMaintenancePeriod maintenanceTime,m.modifiedOn changedAt
        FROM changed c JOIN t_deviceinspectionmaintenance m ON m.deviceInspectionMaintenanceId=c.taskId'''


def collect_snapshot(ctx, datasource_id, value, category="all", *, window=None):
    """采集一份设备监控快照（只读），供画布展示与规则判定。

    参数：
    - ``ctx``：授权上下文，查询走 ``execute_query``，因此仍受数据源可见性与只读校验约束。
    - ``datasource_id``：设备业务源库，必须是 ``active`` 的 MySQL 源。
    - ``value``：上游设备节点的输出，需为 ``{'data': [{deviceKey...}]}``；
      ``truncated`` 为真时直接拒绝，避免用不完整设备清单做判定。
    - ``category``：``all`` / ``inspection`` / ``maintenance`` / ``repair``，决定采集哪些数据集。
    - ``window``：可选增量窗口 ``{start, end, timeZone, mode}``；给出时按该窗口替换默认
      30 天窗口，并额外采集 ``changedRecords``。

    返回：``device-monitor-v1`` 快照字典，含 ``devices`` / ``datasets`` / ``queries``
    （查询与运行标识，便于追溯）/ ``scope``（口径说明）以及 ``visualRows``（见
    ``monitoring_rows.visual_rows``）。

    异常：非 MySQL 源、``value`` 缺 ``data`` 或已截断、未知类别、任一数据集被截断时抛
    ``ValidationError``；不做任何写入、不产生审计。

    边界：设备 key 为空时**不查库**，直接返回空 ``visualRows`` 并把 ``scope`` 改为
    "不能判断设备正常"，避免把"没有设备"表述成"设备都正常"。
    """
    if category not in {"all", "inspection", "maintenance", "repair"}:
        raise ValidationError("未知设备业务类别")
    source = get_source(ctx, datasource_id, active=True, provider='any')
    if source.provider != 'mysql':
        raise ValidationError("本采集方案仅适用已配置的 MySQL 设备业务表")
    if not isinstance(value, dict) or value.get('truncated'):
        raise ValidationError("设备输入缺失或已截断")
    devices = value.get('data')
    if not isinstance(devices, list):
        raise ValidationError("设备输入必须包含 data 数组")
    # 兼容上游两种交付：`[{deviceKey: ...}]` 或直接 `["设备名"]`；空串设备名会被后续去重剔除。
    normalized_devices = []
    for item in devices:
        if isinstance(item, dict):
            normalized_devices.append(item)
        else:
            normalized_devices.append({"deviceKey": str(item or "")})
    # 去重且保持顺序：同一设备重复出现不应导致重复查询或重复行。
    keys = list(dict.fromkeys(str(d.get('deviceKey') or '') for d in normalized_devices))
    now = datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    until = now.isoformat(sep=' ', timespec='seconds')
    since = (now - timedelta(days=30)).isoformat(sep=' ', timespec='seconds')
    if window:
        from app.modules.workflows.windows import local_time
        # 窗口时间按调度配置的时区换算为源库本地时间字面量。
        since = local_time(window['start'], window['timeZone']).isoformat(' ')
        until = local_time(window['end'], window['timeZone']).isoformat(' ')
    snapshot = {'schemaVersion': 'device-monitor-v1', 'category': category, 'datasourceId': datasource_id,
                'asOf': until, 'timezone': 'Asia/Shanghai', 'windowStart': since,
                'devices': normalized_devices, 'datasets': {}, 'queries': {},
                'scope': '巡检与保养取各计划/周期最近一次已完成任务及明细，不因30天窗口丢弃历史完成时间；维修包含当前未完成/失败任务及最近30天记录。不是全量历史趋势分析。'}
    if not keys:
        snapshot['scope'] = '设备范围无匹配数据，不能判断设备正常。'
        snapshot['visualRows'] = {name: [] for name in ('plans', 'inspection', 'maintenance', 'repair')}
        return snapshot
    # 类别 → 允许的数据集；未列入的数据集即使 SQL 已生成也不执行（少一次无效查询）。
    allowed = {
        'all': {'plans', 'inspection', 'areaInspection', 'maintenanceRequirements', 'maintenance', 'repair'},
        'inspection': {'plans', 'inspection', 'areaInspection'},
        'maintenance': {'plans', 'maintenanceRequirements', 'maintenance'},
        'repair': {'repair'},
    }[category]
    scope = {'inspection': '巡检：截至检查时间，各计划/周期最近一次已完成任务及明细。',
             'maintenance': '保养：截至检查时间，各计划最近一次已完成任务及润滑要求。',
             'repair': '维修：未完成/失败任务及最近30天记录。'}
    if category in scope:
        snapshot['scope'] = scope[category] + '仅判断本业务；缺少记录不等于正常。'
    for name, sql in snapshot_queries(keys, since, until).items():
        if name not in allowed:
            continue
        if name == 'plans' and category in {'inspection', 'maintenance'}:
            # 单业务模式只查该业务计划类型：保养=2，巡检=1/3/4。
            sql += ' AND p.c_jihualeixing ' + ('= 2' if category == 'maintenance' else 'IN (1,3,4)')
        plan_types = {str(row.get('planType')) for row in snapshot['datasets'].get('plans', [])}
        # 计划类型决定依赖数据集是否有意义：没有区域巡检计划(3/4)时不查 areaInspection，
        # 没有保养计划(2)时不查润滑要求与保养记录 —— 直接置空，避免执行注定为空的查询。
        if (name == 'areaInspection' and not plan_types.intersection({'3', '4'})) or (
                name in {'maintenanceRequirements', 'maintenance'} and '2' not in plan_types):
            snapshot['datasets'][name] = []
            continue
        result = execute_query(ctx, QueryInput(datasourceId=datasource_id, query=sql, mode='sql',
                               maxRows=500, needAnalysis=False, needChart=False))
        # 截断即失败：部分记录会被误读成"设备没有做保养"。
        if result.truncated:
            raise ValidationError("设备采集结果已截断，请缩小设备范围或时间窗口", dataset=name)
        snapshot['datasets'][name] = result.data
        snapshot['queries'][name] = {'queryId': result.queryId, 'runId': result.runId}
    from app.modules.alerts.monitoring_rows import visual_rows
    if window and category in {'inspection', 'maintenance'}:
        # 增量模式：额外记录窗口内变更任务数，供调度节点判断"本轮是否有新情况"。
        sql = changed_task_query(keys, category)
        changed = execute_query(ctx, QueryInput(datasourceId=datasource_id, query=sql, mode='sql',
            parameters={'windowStart':since,'windowEnd':until}, maxRows=500, needAnalysis=False, needChart=False))
        if changed.truncated:
            raise ValidationError('本时段更新记录超过单批范围，未推进采集时间')
        snapshot['datasets']['changedRecords'] = changed.data
        snapshot['queries']['changedRecords'] = {'queryId':changed.queryId,'runId':changed.runId}
        snapshot['changeCount'] = len(changed.data)
        snapshot['scope'] = f"采集 {since} 至 {until} 新增或更新记录；计划与最近完成记录作为基线，用于持续判断超期。"
    if window:
        # 原样保留窗口配置（含 mode），执行记录与前端据此解释增量语义。
        snapshot['collectionWindow'] = {k:window[k] for k in ('start','end','timeZone','mode')}
    snapshot['visualRows'] = visual_rows(snapshot)
    return snapshot
