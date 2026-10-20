"""工作台首检样本趋势复用不可变运行；复检、重复执行和人工放行不改写首检事实。"""

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ...core.errors import DomainError
from ..manufacturing import models as m
from ..manufacturing.quality_statistics import rate
from ..manufacturing.workflow import as_utc, authorize_run

MAX_OVERVIEW_RUNS = 2000
FACTORY_TIMEZONE = timezone(timedelta(hours=8))


def first_inspection_trend(session, actor, *, now=None):
    """按中国工厂自然日取最近30天首检，逐运行检查证据权限，超限不展示部分统计。"""
    if not actor.can("m11.read"):
        return {"items": [], "status": "unavailable"}
    today = (now or datetime.now(timezone.utc)).astimezone(FACTORY_TIMEZONE)
    begin = today.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29)
    end = begin + timedelta(days=30)
    ranked = select(
        m.InspectionRun.id,
        func.row_number().over(
            partition_by=m.InspectionRun.inspection_id,
            order_by=(m.InspectionRun.created_at, m.InspectionRun.id),
        ).label("position"),
    ).where(m.InspectionRun.workspace_id == actor.workspace_id).subquery()
    children = select(m.InspectionReinspection.child_inspection_id).where(
        m.InspectionReinspection.workspace_id == actor.workspace_id,
    )
    rows = session.execute(
        select(m.Inspection, m.InspectionRun)
        .join(m.InspectionRun, m.InspectionRun.inspection_id == m.Inspection.id)
        .join(ranked, ranked.c.id == m.InspectionRun.id)
        .where(
            m.Inspection.workspace_id == actor.workspace_id,
            m.Inspection.archived.is_(False),
            m.Inspection.id.not_in(children),
            ranked.c.position == 1,
            m.InspectionRun.created_at >= begin.astimezone(timezone.utc),
            m.InspectionRun.created_at < end.astimezone(timezone.utc),
        )
        .limit(MAX_OVERVIEW_RUNS + 1)
    ).all()
    if len(rows) > MAX_OVERVIEW_RUNS:
        return {"items": [], "status": "scope_too_large"}
    grouped = {}
    for inspection, run in rows:
        try:
            authorize_run(session, actor, inspection, run.id)
        except DomainError as error:
            if error.status in (403, 404):
                continue
            raise
        day = as_utc(run.created_at).astimezone(FACTORY_TIMEZONE).date().isoformat()
        item = grouped.setdefault(day, Counter())
        result = run.result
        item["total"] += max(result.get("expected_samples", 0), result.get("actual_samples", 0))
        item["tasks"] += 1
        item.update(sample.get("outcome", "incomplete") for sample in result.get("samples", []))
        item["missing"] += result.get("missing_samples", 0)
    return {
        "status": "ready",
        "items": [
            {"date": day, **dict(item), "pass_rate": rate(item["passed"], item["total"])}
            for day, item in sorted(grouped.items())
        ],
    }
