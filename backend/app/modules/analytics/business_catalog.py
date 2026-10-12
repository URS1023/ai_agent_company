"""从完整授权缓存检索候选表，并明确目录覆盖与业务事实的边界。"""
from sqlalchemy import select

from app.common.authorization import require_permission
from app.errors import ValidationError
from app.modules.analytics.connections import source_version
from app.modules.analytics.models import SourceMetadata
from app.modules.analytics.finance_semantics import finance_metadata_profile
from app.modules.analytics.semantic import explicitly_requested_tables, is_business_table, recall_tables


def business_schema(ctx, source, question: str) -> dict:
    """调用方先解析数据源权限；此处只召回已同步缓存，不连接业务库或外部模型。"""
    require_permission(ctx, "analytics.execute")
    record = ctx.db.scalar(select(SourceMetadata).where(
        SourceMetadata.workspace_id == ctx.workspace_id,
        SourceMetadata.datasource_id == source.id,
    ))
    if not record or record.status != "ready" or record.source_version != source_version(source):
        raise ValidationError("当前数据源尚无有效的已同步元数据，请在数据源管理中同步元数据后再查询")
    tables, _ = recall_tables(ctx, source, record.tables_json, question, include_samples=False, cached_only=True)
    explicit = explicitly_requested_tables(record.tables_json, question)
    searched = [table for table in record.tables_json if is_business_table(table.get("tableName"))
                or table.get("tableName") in explicit]
    coverage = {
        "source": "authorized_metadata_cache", "authorizedTableCount": len(record.tables_json),
        "searchedTableCount": len(searched),
        "searchedFieldCount": sum(len(table.get("columns") or []) for table in searched),
        "returnedTableCount": len(tables),
        "returnedFieldCount": sum(len(table.get("columns") or []) for table in tables),
        "allSearchableMetadataScanned": True, "candidateListTruncated": len(tables) < len(searched),
        "businessDataQueried": False, "canProveDataAbsence": False,
    }
    return {
        "datasourceId": source.id,
        "summary": f"已检索授权缓存中的 {len(searched)} 张表及全部字段，返回 {len(tables)} 张候选表，未执行业务 SQL",
        "coverage": coverage,
        "tables": [{**{key: table[key] for key in (
            "tableName", "tableComment", "columns", "foreignKeys", "declaredRelations",
        ) if key in table}, "financeSemantics": finance_metadata_profile(table)} for table in tables],
        "guidance": "根据这些已授权表字段继续查询用户要求的业务数据；不要查询系统表，也不要要求用户再次提供已有表名。"
                    "返回表是排序候选，未返回或没有专用表不能证明业务信息不存在；信息也可能保存在其他业务表字段或声明关系中。"
                    "应收与应付、收款与付款须按业务方向区分；付款表的收款单位是付款的接收方，不代表应收或已收款。"
                    "financeSemantics 来自已授权字段含义；meaningConfirmed=false 的金额字段不能充当应收/应付近似值。"
                    "joinKeysKnown=false 的关系只有表级线索，sourceDeclared 是未验证旧声明，不得用其生成 JOIN。",
    }
