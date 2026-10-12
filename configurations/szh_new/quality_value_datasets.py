"""Raw quality evidence only; decisions live in quality_value_workflows.py.

Missing required items are retained as rows with no measurement, and forms
without any items are retained by the LEFT JOIN. No pass/fail is computed here.
"""
import json
from pathlib import Path

ITEMS_SQL = '''WITH selected_forms AS (
 SELECT h.incomingMaterialQcFormId formId,h.c_incomingQcNo formNumber,
 h.c_qualityInspectionTemplate templateId,h.c_testResults originalResult,
 h.c_zhijianshifouhege originalQualified,h.c_zhijiandanzhuangtai documentStatus
 FROM t_incomingmaterialqcform h WHERE h.isDeleted=0 AND h.c_qualityInspection=:category
 AND (:formNumber='' OR h.c_incomingQcNo=:formNumber)
), items AS (
 SELECT h.formId,i.qualityInspectionItemDetailsId rowId,i.qualityInspectionItemDetailsId actualId,
 i.c_qualityInspectionProject projectId,i.c_jianyanjieguo measured,
 i.c_judgeRule ruleCode,i.c_standardMin lowerBound,i.c_standardMax upperBound
 FROM selected_forms h JOIN t_qualityinspectionitemdetails i
 ON i.mdIncomingMaterialQcFormId=h.formId AND i.isDeleted=0
 UNION ALL
 SELECT h.formId,d.qITemplateDetailsId rowId,NULL actualId,
 d.c_xuanzezhijianxiangmu projectId,NULL measured,NULL ruleCode,NULL lowerBound,NULL upperBound
 FROM selected_forms h JOIN t_qitemplatedetails d
 ON d.mdQualityInspectionTemplateId=h.templateId AND d.isDeleted=0
 LEFT JOIN t_qualityinspectionitemdetails i ON i.mdIncomingMaterialQcFormId=h.formId
 AND i.c_qualityInspectionProject=d.c_xuanzezhijianxiangmu AND i.isDeleted=0
 WHERE i.qualityInspectionItemDetailsId IS NULL
)
SELECT COALESCE(i.actualId,CONCAT(h.formId,':',COALESCE(i.rowId,'empty'))) recordKey,
 h.formId AS `单据标识`,h.formNumber AS `质检单号`,t.qualityInspectionTemplateId AS `有效模板`,
 (SELECT COUNT(*) FROM t_qitemplatedetails d WHERE d.mdQualityInspectionTemplateId=h.templateId AND d.isDeleted=0) AS `必检项数`,
 h.originalResult AS `原检验结果`,h.originalQualified AS `原合格状态`,h.documentStatus AS `原单据状态`,
 i.actualId AS `实测记录`,p.qualityInspectionProjectId AS `有效项目`,p.c_projectName AS `检验项目`,
 p.c_inspectionStandard AS `标准文本`,i.measured AS `测量值`,i.ruleCode AS `判定方式`,
 i.lowerBound AS `下界`,i.upperBound AS `阈值`
FROM selected_forms h LEFT JOIN items i ON i.formId=h.formId
LEFT JOIN t_qualityinspectiontemplate t ON t.qualityInspectionTemplateId=h.templateId AND t.isDeleted=0
LEFT JOIN t_qualityinspectionproject p ON p.qualityInspectionProjectId=i.projectId AND p.isDeleted=0
ORDER BY h.formId,i.rowId'''


def definitions(previous_graph):
    """Retain the three original supplementary evidence queries and add filters."""
    output = {'quality_items': ('质检项目、实测值和必检要求', ITEMS_SQL)}
    for key in ('gas1', 'gas2', 'statistics'):
        old = next((n for n in previous_graph['nodes'] if n['id'] == key), None)
        if old is None:
            # Once migrated, the graph references a saved dataset. Reuse that
            # checked-in definition instead of requiring an obsolete SQL node.
            saved = json.loads((Path(__file__).parent / 'value_datasets' / ('quality_' + key + '.json')).read_text(encoding='utf-8'))
            output['quality_' + key] = (saved['name'], saved['content'])
            continue
        sql = old['config']['query'].replace('WHERE d.isDeleted=0', "WHERE (:formNumber='' OR h.c_incomingQcNo=:formNumber) AND d.isDeleted=0")
        sql = sql.replace('h.c_incomingQcNo formNumber', 'h.c_incomingQcNo AS `质检单号`')
        sql = sql.replace('h.incomingMaterialQcFormId formId', 'h.incomingMaterialQcFormId AS `单据标识`')
        aliases = {'sampledAt': '检测时间', 'furnaceEye': '炉眼', 'gasYield_L_kg': '发气量_L每kg',
                   'phosphine_pct': '磷化氢_百分比', 'hydrogenSulfide_pct': '硫化氢_百分比', 'originalResult': '原检验结果'}
        for before, after in aliases.items():
            sql = sql.replace(' ' + before, ' AS `' + after + '`')
        output['quality_'+key] = (old['title'], sql)
    return output
