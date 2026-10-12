"""Replace one workspace's report data with the checked-in Lynx source reports.

This is intentionally an explicit, scoped maintenance command. It never
touches data sources, analysis metadata, prompts, users, workflows, alerts,
quality records or knowledge content.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import delete, func, select


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db import new_session  # noqa: E402
from app.models import DataSource, Workspace, WorkspaceMember  # noqa: E402
from app.modules.reports.content import lynx_report_catalog  # noqa: E402
from app.modules.reports.models import (  # noqa: E402
    Report,
    ReportBuiltinTemplateState,
    ReportGeneration,
    ReportRevision,
    ReportSourceImport,
    ReportTemplate,
    ReportTemplateRevision,
    ReportTemplateUsage,
)
from app.modules.reports.source_reports import ensure_lynx_source_reports  # noqa: E402
from app.schema import initialize_schema  # noqa: E402


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete only report/template data in one workspace and recreate direct Lynx reports",
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--datasource-name", default="variantorm")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the reset. Without this flag the command is read-only.",
    )
    return parser.parse_args()


def _count(db, model, workspace_id: str) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace_id)) or 0)


def _scope(db, workspace_id: str, datasource_name: str) -> tuple[Workspace, DataSource, str]:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise SystemExit(f"workspace does not exist: {workspace_id}")
    datasources = db.scalars(select(DataSource).where(
        DataSource.workspace_id == workspace_id,
        DataSource.name == datasource_name,
        DataSource.status == "active",
    )).all()
    if len(datasources) != 1:
        raise SystemExit(
            f"expected exactly one active {datasource_name!r} datasource in workspace; found {len(datasources)}"
        )
    members = db.scalars(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
    ).order_by(WorkspaceMember.created_at)).all()
    if not members:
        raise SystemExit("workspace has no member who can own imported reports")
    owner = next((member.user_id for member in members if member.role == "admin"), members[0].user_id)
    return workspace, datasources[0], owner


def main() -> int:
    args = arguments()
    initialize_schema()
    catalog = lynx_report_catalog()
    if catalog.get("schemaVersion") != "test5.lynx-source-reports/v2":
        raise SystemExit("checked-in Lynx catalog is not a direct-report v2 catalog")
    if int(catalog.get("reportCount") or 0) <= 0:
        raise SystemExit("checked-in Lynx catalog is empty")

    with new_session() as db:
        workspace, datasource, owner = _scope(db, args.workspace_id, args.datasource_name)
        models = (
            ReportRevision,
            ReportSourceImport,
            ReportTemplateUsage,
            ReportTemplateRevision,
            ReportBuiltinTemplateState,
            ReportTemplate,
            ReportGeneration,
            Report,
        )
        before = {model.__tablename__: _count(db, model, args.workspace_id) for model in models}
        print(f"workspace={workspace.id} datasource={datasource.id} sourceReports={catalog['reportCount']}")
        print("current=" + ", ".join(f"{name}:{count}" for name, count in before.items()))
        if not args.apply:
            print("dry-run only; pass --apply to perform the scoped reset")
            return 0

        # Child rows first. Every DELETE includes the exact workspace id even
        # when the foreign key would otherwise make a broader delete possible.
        for model in models:
            db.execute(delete(model).where(model.workspace_id == args.workspace_id))
        db.flush()
        result = ensure_lynx_source_reports(db, args.workspace_id, owner)
        db.commit()

        after_reports = _count(db, Report, args.workspace_id)
        after_templates = _count(db, ReportTemplate, args.workspace_id)
        if after_reports != int(catalog["reportCount"]) or after_templates != 0:
            raise RuntimeError(
                f"post-reset invariant failed: reports={after_reports}, templates={after_templates}"
            )
        print(
            f"completed reports={after_reports} templates={after_templates} "
            f"created={result['createdCount']} datasource={datasource.id}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

