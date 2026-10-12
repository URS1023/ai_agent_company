"""Idempotently materialize the checked-in Lynx catalog as editable reports.

Each workspace gets one directly openable report per source record. A separate
mapping row provides stable identity, so restarts never duplicate records and a
report the user deleted is not silently recreated. No report template or
template-usage database row is created during import; the same checked-in
visual source is exposed separately as an immutable, data-empty template.
"""
from __future__ import annotations

from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.branding import PRODUCT_NAME, public_brand_text
from app.ids import new_id
from app.models import DataSource
from app.modules.reports.content import lynx_report_catalog, validate_content
from app.modules.reports.models import (
    Report,
    ReportRevision,
    ReportSourceImport,
)
from app.modules.reports.template_lock import visual_fingerprint


SOURCE_KIND = "lynx-report-screen"
REFERENCE_NOTICE = "仓库参考示例：固定展示数据来自随包素材，不代表当前业务查询结果；执行 SQL 仍需当前授权。"


class CatalogSnapshotProof:
    """本请求内读取一次平台导入登记与目录；不信任客户端 lineage 或相似报表名称。"""

    def __init__(self, ctx):
        catalog = lynx_report_catalog()
        sources = {str(item.get('sourceRecordId')): item for item in catalog.get('reports') or []}
        self.widgets = {}
        for mapping in ctx.db.scalars(select(ReportSourceImport).where(
            ReportSourceImport.workspace_id == ctx.workspace_id,
            ReportSourceImport.source_kind == SOURCE_KIND,
        )).all():
            source = sources.get(mapping.source_record_id)
            if (source is None or not mapping.catalog_sha256 or not mapping.source_content_sha256
                    or mapping.catalog_sha256 != catalog.get('catalogSha256')
                    or mapping.source_content_sha256 != source.get('sourceContentSha256')):
                continue
            self.widgets[mapping.report_id] = {
                str(widget.get('id')): self.payload(widget)
                for widget in validate_content(source.get('content') or {}).get('widgets') or []
            }

    @staticmethod
    def payload(widget):
        """仅忽略几何、导入源标识及精确大整数传输表示；其他数值和类型不得变更。"""
        geometry = {'x', 'y', 'w', 'h', 'position', 'orderNum', 'rotation'}
        payload = deepcopy({key: value for key, value in widget.items() if key not in geometry})
        config = payload.get('config') or {}
        # 只归一化绑定位置，数据行内名为 datasourceId 的业务字段也必须逐值相等。
        for binding in (payload, config, payload.get('query'), payload.get('drilldown'), config.get('drilldown')):
            if not isinstance(binding, dict):
                continue
            for key in ('datasourceId', 'data_source_id', 'datasourceIds', 'data_source_ids'):
                if binding.get(key):
                    binding[key] = '__catalog_source__'
        from app.modules.reports.wire import widget_for_wire
        return widget_for_wire(payload)

    def checker(self, report_id):
        """返回只服务于指定平台报表的证明函数，不把登记外报表纳入例外。"""
        registered = self.widgets.get(report_id) or {}
        return lambda widget: (str(widget.get('id')) in registered
                               and registered[str(widget.get('id'))] == self.payload(widget))


def _source_report_snapshot(report: Report) -> dict:
    """Build the same immutable shape exposed by the report detail endpoint."""

    from app.modules.reports.service import report_view

    return report_view(report).model_dump(mode="json")


def _bind_datasource(value, datasource_id: str):
    """Replace only datasource references while retaining every other field."""

    if isinstance(value, list):
        return [_bind_datasource(item, datasource_id) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    result = {}
    for key, item in value.items():
        lowered = str(key).lower()
        if lowered in {"datasourceid", "data_source_id"} and item not in (None, "", 0):
            result[key] = datasource_id
        elif lowered in {"datasourceids", "data_source_ids"} and isinstance(item, list) and item:
            result[key] = [datasource_id]
        else:
            result[key] = _bind_datasource(item, datasource_id)
    return result


def _report_from_source(*, workspace_id: str, owner_user_id: str, source: dict,
                        datasource_id: str, source_endpoint: str,
                        catalog_sha256: str) -> Report:
    """把一条来源记录转换成待入库的 ``Report`` 行：改绑本地数据源、校验视觉摘要、继承画布信息。

    可信度约束：来源标识必须存在且 ``sourceKind`` 匹配，内容经 ``validate_content`` 校验后
    必须与目录里登记的 ``visualHash`` 完全一致——不一致说明离线目录被改动过，抛 ``RuntimeError``
    拒绝导入，而不是导入一份来路不明的画布。只替换数据源引用，其它字段（布局/样式/组件）原样保留。
    """
    source_record_id = str(source.get("sourceRecordId") or "").strip()
    if not source_record_id or source.get("sourceKind") != SOURCE_KIND:
        raise RuntimeError("接口报表目录包含无效来源标识")
    content = validate_content(_bind_datasource(source.get("content") or {}, datasource_id))
    expected_visual_hash = str(source.get("visualHash") or "")
    if not expected_visual_hash or visual_fingerprint(content) != expected_visual_hash:
        raise RuntimeError(f"接口报表 {source_record_id} 的视觉摘要不一致")
    canvas = content["canvas"]
    theme = canvas.get("theme") if isinstance(canvas.get("theme"), dict) else {}
    background_color = str(
        canvas.get("backgroundColor") or theme.get("background") or "#07111f"
    )[:100]
    return Report(
        id=new_id(),
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        name=public_brand_text(source.get("name") or f"{PRODUCT_NAME} 报表 {source_record_id}").strip(),
        description=REFERENCE_NOTICE + "\n" + public_brand_text(source.get("description") or ""),
        datasource_ids=[datasource_id],
        content=content,
        width=int(canvas["width"]),
        height=int(canvas["height"]),
        background_color=background_color,
        background_image=str(canvas.get("backgroundImage") or ""),
        thumbnail=f"/assets/analytics/jimu-screen/report-thumbnails/lynx-{source_record_id}.png",
        refresh_seconds=max(0, int(source.get("refreshSeconds") or 0)),
        status="draft" if source.get("enabled", True) else "archived",
        template_mode="none",
        template_id=None,
        lineage={
            "entry": "lynx_interface_import",
            "sourceSystem": PRODUCT_NAME,
            "sourceKind": SOURCE_KIND,
            "sourceEndpoint": source_endpoint,
            "sourceReportId": source_record_id,
            "sourceRecordId": source_record_id,
            "sourceContentSha256": str(source.get("sourceContentSha256") or ""),
            "sourceCatalogSha256": catalog_sha256,
            "sourceVisualHash": expected_visual_hash,
            "sourceDatasourceIds": list(source.get("sourceDatasourceIds") or []),
            "boundDatasourceId": datasource_id,
            "componentCount": len(content.get("widgets") or []),
            "sqlWidgetCount": int(source.get("sqlWidgetCount") or 0),
            "dataSlotCount": int(source.get("dataSlotCount") or 0),
            "preserveSourceWidgets": True,
            "styleLocked": False,
            "dataStatus": "bound",
        },
    )


def ensure_lynx_source_reports(db: Session, workspace_id: str, owner_user_id: str) -> dict:
    """Create every missing interface-sourced report exactly once per workspace."""

    catalog = lynx_report_catalog()
    sources = [
        source for source in catalog.get("reports") or []
        if source.get("sourceKind") == SOURCE_KIND
    ]
    if len(sources) != int(catalog.get("sourceRecordCount") or 0):
        raise RuntimeError("接口报表目录不完整，拒绝执行部分导入")
    datasource = db.scalar(select(DataSource).where(
        DataSource.workspace_id == workspace_id,
        DataSource.name == "variantorm",
        DataSource.status == "active",
    ))
    if datasource is None:
        # Workspaces are allowed to exist before a business datasource is
        # configured.  Bootstrap must not make login or user management fail;
        # it simply postpones report materialization until an explicit import
        # (or a later bootstrap) can bind the reports to a real variantorm
        # source.
        return {
            "sourceCount": len(sources),
            "createdCount": 0,
            "existingCount": 0,
            "deletedCount": 0,
            "createdReportIds": [],
            "existingReportIds": [],
            "skippedReason": "variantorm_datasource_missing",
        }

    mappings = db.scalars(select(ReportSourceImport).where(
        ReportSourceImport.workspace_id == workspace_id,
        ReportSourceImport.source_kind == SOURCE_KIND,
    )).all()
    by_source_id = {row.source_record_id: row for row in mappings}
    created_ids: list[str] = []
    existing_ids: list[str] = []
    deleted_ids: list[str] = []
    source_endpoint = str(catalog.get("sourceEndpoint") or "")
    catalog_sha256 = str(catalog.get("catalogSha256") or "")

    for source in sources:
        source_record_id = str(source["sourceRecordId"])
        mapping = by_source_id.get(source_record_id)
        if mapping is not None:
            existing = db.get(Report, mapping.report_id)
            if existing is None:
                raise RuntimeError(f"来源映射 {source_record_id} 指向不存在的报表")
            existing.name = public_brand_text(existing.name)
            existing.description = public_brand_text(existing.description)
            existing.lineage = {
                **(existing.lineage or {}),
                "sourceSystem": PRODUCT_NAME,
            }
            existing_ids.append(existing.id)
            if existing.status == "deleted":
                deleted_ids.append(existing.id)
            continue

        report = _report_from_source(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            source=source,
            datasource_id=datasource.id,
            source_endpoint=source_endpoint,
            catalog_sha256=catalog_sha256,
        )
        db.add(report)
        db.flush()
        db.add_all([
            ReportRevision(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                report_id=report.id,
                revision=report.version,
                snapshot=_source_report_snapshot(report),
                reason="智慧云脑 Cloud Brain 接口报表导入",
            ),
            ReportSourceImport(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                report_id=report.id,
                source_kind=SOURCE_KIND,
                source_record_id=source_record_id,
                source_template_id="",
                source_endpoint=source_endpoint,
                source_content_sha256=str(source.get("sourceContentSha256") or ""),
                catalog_sha256=catalog_sha256,
            ),
        ])
        db.flush()
        created_ids.append(report.id)

    return {
        "sourceCount": len(sources),
        "createdCount": len(created_ids),
        "existingCount": len(existing_ids),
        "deletedCount": len(deleted_ids),
        "createdReportIds": created_ids,
        "existingReportIds": existing_ids,
    }
