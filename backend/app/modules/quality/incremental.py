"""Select documents updated within a collection window, including edited child rows.

职责：质检增量采集的 SQL 改写 —— 在已保存的查询外面套一层"窗口内新增/更新"过滤条件，
让调度每轮只取有变化的单据。

位置与依赖：被质检增量采集路径调用（与 ``alerts.monitoring.changed_task_query`` 思路一致，
但针对质检表结构）；``:windowStart`` / ``:windowEnd`` 为绑定参数，由执行层传入。

业务规则（为什么条件这么多）：一张质检单据的"更新"可能只发生在**明细行**上
（检验项明细、来料明细、模板与模板明细/检验项目），因此必须用 EXISTS 覆盖这些子表；
只看主表的 ``modifiedOn`` 会漏掉"只改了明细"的单据。
约束：``query`` 必须是带 `` ORDER BY `` 的完整查询 —— 本函数用 ``rsplit`` 在 ORDER BY 前插入
条件，改写失败（没有 ORDER BY）会直接抛异常，调用方需保证输入形状。
"""
# 子表清单：检验项明细、质检项目统计明细、来料检验明细（两套表名）、
# 以及模板/模板明细/检验项目（后三者用 EXISTS 直接写在条件里）。
TABLES = ('t_qualityinspectionitemdetails', 't_zhijianxiangmutongjimingxi',
          't_incomingmaterialqcitemdetails', 't_incomingmaterialqcitemdetails2')


def changed_documents_query(query):
    """把"窗口内新增或更新"的过滤条件注入到既有查询的 WHERE 中，返回改写后的 SQL。

    条件构成：主表单据自身在窗口内变更（``COALESCE(modifiedOn, createdOn)``），
    或任一相关子表（见 ``TABLES`` 与模板/项目相关表）在窗口内变更。
    返回新 SQL 字符串（不执行查询、不访问数据库）。
    异常：``query`` 不含 `` ORDER BY `` 时 ``rsplit`` 失败抛 ``ValueError``。
    """
    conditions = ['COALESCE(f.modifiedOn,f.createdOn) >= :windowStart AND COALESCE(f.modifiedOn,f.createdOn) < :windowEnd']
    for index, table in enumerate(TABLES):
        alias = f'changed{index}'
        conditions.append(f'EXISTS (SELECT 1 FROM {table} {alias} WHERE '
            f'{alias}.mdIncomingMaterialQcFormId=f.incomingMaterialQcFormId AND '
            f'COALESCE({alias}.modifiedOn,{alias}.createdOn) >= :windowStart AND '
            f'COALESCE({alias}.modifiedOn,{alias}.createdOn) < :windowEnd)')
    conditions.append('EXISTS (SELECT 1 FROM t_qualityinspectiontemplate ct WHERE '
        'ct.qualityInspectionTemplateId=f.c_qualityInspectionTemplate AND '
        'COALESCE(ct.modifiedOn,ct.createdOn) >= :windowStart AND COALESCE(ct.modifiedOn,ct.createdOn) < :windowEnd)')
    conditions.append('EXISTS (SELECT 1 FROM t_qitemplatedetails cd WHERE '
        'cd.mdQualityInspectionTemplateId=f.c_qualityInspectionTemplate AND '
        'COALESCE(cd.modifiedOn,cd.createdOn) >= :windowStart AND COALESCE(cd.modifiedOn,cd.createdOn) < :windowEnd)')
    conditions.append('EXISTS (SELECT 1 FROM t_qitemplatedetails cp JOIN t_qualityinspectionproject pj '
        'ON pj.qualityInspectionProjectId=cp.c_xuanzezhijianxiangmu WHERE '
        'cp.mdQualityInspectionTemplateId=f.c_qualityInspectionTemplate AND '
        'COALESCE(pj.modifiedOn,pj.createdOn) >= :windowStart AND COALESCE(pj.modifiedOn,pj.createdOn) < :windowEnd)')
    before, order = query.rsplit(' ORDER BY ', 1)
    return before + ' AND (' + ' OR '.join('(' + c + ')' for c in conditions) + ') ORDER BY ' + order
