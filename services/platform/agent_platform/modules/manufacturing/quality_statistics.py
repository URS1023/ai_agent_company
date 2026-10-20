"""质量统计按首个原始运行计首检，复检另列，缺样本保留在分母中。"""
from collections import Counter
from decimal import Decimal, localcontext

from sqlalchemy import case as sql_case, func, select

from agent_platform.core.errors import DomainError

from . import models as m
from .quality_lifecycle import QualityService
from .workflow import as_utc, authorize_run, input_time


def rate(passed, denominator):
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = 40
        return str((Decimal(passed) * 100 / Decimal(denominator)).quantize(Decimal("0.01")))


def quality_statistics(session, actor, *, target_type=None, start_at=None, end_at=None):
    actor.require("m11.read")
    start, end = input_time(start_at), input_time(end_at)
    if start and end and (end < start or (end - start).days > 366):
        raise DomainError(422, "statistics_time_range_invalid", "请选择不超过366天的有效时间范围")
    service = QualityService(session, actor)
    clauses = [m.Inspection.workspace_id == actor.workspace_id, m.Inspection.archived.is_(False)]
    if target_type:
        clauses.append(m.Inspection.target_type == target_type)
    # 先按各任务实际纳入统计的运行时间筛选，再施加任务预算；否则缩日期无法解除上限。
    runs_scope = select(m.InspectionRun.inspection_id.label("inspection_id"), func.min(m.InspectionRun.created_at).label("first_at"), func.max(m.InspectionRun.created_at).label("last_at")).where(m.InspectionRun.workspace_id == actor.workspace_id).group_by(m.InspectionRun.inspection_id).subquery()
    children_query = select(m.InspectionReinspection.child_inspection_id).where(m.InspectionReinspection.workspace_id == actor.workspace_id)
    stamp = sql_case((m.Inspection.id.in_(children_query), runs_scope.c.last_at), else_=func.coalesce(runs_scope.c.first_at, m.Inspection.created_at))
    if start:
        clauses.append(stamp >= start)
    if end:
        clauses.append(stamp <= end)
    inspections = session.scalars(select(m.Inspection).outerjoin(runs_scope, runs_scope.c.inspection_id == m.Inspection.id).where(*clauses).limit(2001)).all()
    if len(inspections) > 2000:
        raise DomainError(422, "statistics_scope_too_large", "统计任务过多，请缩小范围")
    children = set(session.scalars(select(m.InspectionReinspection.child_inspection_id).where(m.InspectionReinspection.workspace_id == actor.workspace_id)))
    initial, retests, dimensions = Counter(), Counter(), {}
    tasks, retest_tasks, pending_tasks = 0, 0, 0
    for inspection in inspections:
        runs = session.scalars(select(m.InspectionRun).where(m.InspectionRun.inspection_id == inspection.id, m.InspectionRun.workspace_id == actor.workspace_id).order_by(m.InspectionRun.created_at, m.InspectionRun.id)).all()
        if not runs:
            if inspection.id not in children and not (start and as_utc(inspection.created_at) < start) and not (end and as_utc(inspection.created_at) > end):
                pending_tasks += 1
            continue
        # 同任务重复执行也不能重复计首检；复检任务的结果单独统计，不替换原首检事实。
        run = runs[-1] if inspection.id in children else runs[0]
        if (start and as_utc(run.created_at) < start) or (end and as_utc(run.created_at) > end):
            continue
        try:
            authorize_run(session, actor, inspection, run.id)
        except DomainError as exc:
            if exc.status in (403, 404):
                continue
            raise
        result = run.result
        counter = retests if inspection.id in children else initial
        denominator = max(result.get("expected_samples", 0), result.get("actual_samples", 0))
        counter["denominator"] += denominator
        counter["actual_samples"] += result.get("actual_samples", 0)
        counter["missing_samples"] += result.get("missing_samples", 0)
        counter.update(sample.get("outcome", "incomplete") for sample in result.get("samples", []))
        if inspection.id in children:
            retest_tasks += 1
            continue
        tasks += 1
        per_category = Counter(metric.get("category", "numeric") for metric in inspection.standard_snapshot["metrics"])
        for category, metrics in per_category.items():
            target = dimensions.setdefault(category, Counter())
            target["denominator"] += denominator * metrics
            target["missing"] += result.get("missing_samples", 0) * metrics
        for sample in result.get("samples", []):
            for metric in sample.get("metrics", []):
                dimensions[metric.get("category", "numeric")][metric.get("outcome", "incomplete")] += 1
    def projection(counter, count):
        return {"task_count": count, "denominator": counter["denominator"], "actual_samples": counter["actual_samples"], "missing_samples": counter["missing_samples"],
                "passed": counter["passed"], "failed": counter["failed"], "review": counter["review"], "incomplete": counter["incomplete"], "pass_rate": rate(counter["passed"], counter["denominator"])}
    categories = [{"category": key, "denominator": value["denominator"], "passed": value["passed"], "failed": value["failed"], "review": value["review"], "incomplete": value["incomplete"] + value["missing"], "pass_rate": rate(value["passed"], value["denominator"])} for key, value in dimensions.items()]
    # 处置分布也经原运行授权；让步/报废单独呈现，不把人工放行改写成系统合格。
    cases = service.cases(limit=200)
    dispositions = Counter()
    offset = 0
    while True:
        for case in cases["items"]:
            inspection = service.inspection(case["inspection_id"])
            if target_type and inspection.target_type != target_type:
                continue
            stamp = input_time(case["created_at"])
            if (start and stamp < start) or (end and stamp > end):
                continue
            dispositions[case["disposition"] if case["status"] == "closed" else "open"] += 1
        offset += len(cases["items"])
        if offset >= cases["total"]:
            break
        cases = service.cases(limit=200, offset=offset)
    return {"basis": "first_run_task_samples", "target_type": target_type, "first_inspection": projection(initial, tasks), "reinspection": projection(retests, retest_tasks),
            "pending_tasks": pending_tasks, "dimensions": categories, "dispositions": [{"name": key, "value": value} for key, value in dispositions.items()]}
