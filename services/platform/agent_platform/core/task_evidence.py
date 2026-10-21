"""对任务投影核验原业务证据，防止历史摘要绕过当前数据行列权限。"""

from sqlalchemy import or_, select

from .errors import DomainError

DATA_TASK_KINDS = {
    'inspection', 'quality_case', 'maintenance_analysis', 'maintenance_work_order', 'energy_analysis',
}


def query_references(value):
    """仅提取固定证据字段；查询ID去重后交由数据服务授权，不重新执行SQL。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {'query_id', 'source_query_id'} and isinstance(item, str) and item:
                yield item
            elif isinstance(item, (dict, list)):
                yield from query_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from query_references(item)


def authorize_task_evidence(session, actor, task):
    """读取任务对应的不可变运行/来源，不调用会反查TaskService的领域入口。"""
    if task.kind == 'platform_native_chat':
        # 对话属于具体用户；同工作区的m02.read不能旁路会话及历史来源授权。
        from ..modules.chat.native_sources import authorize_native_chat_task
        authorize_native_chat_task(session, actor, task)
        return
    if task.kind == 'platform_workflow':
        from ..modules.flows.models import WorkflowRun
        from ..modules.knowledge.evidence import authorize_evidence
        run_id = (task.inputs or {}).get('run_id')
        run = session.get(WorkflowRun, run_id) if isinstance(run_id, str) and run_id else None
        if run is None or run.workspace_id != actor.workspace_id or run.task_id not in {None, task.id}:
            raise DomainError(403, 'task_evidence_access_denied', '当前账号无法访问任务引用的流程运行')
        if actor.can('m04.read') and run.task_id == task.id:
            from ..modules.flows.execution import reconcile_expired
            reconcile_expired(session, actor, run)
        authorize_evidence(session, actor, run.knowledge_sources, legacy_steps=run.steps)
        return
    if task.kind not in DATA_TASK_KINDS:
        return
    from ..modules.data.service import DataService
    from ..modules.manufacturing import models as m
    from ..modules.manufacturing.energy.models import EnergyAnalysis

    inputs, outputs = task.inputs or {}, task.outputs or {}
    evidence = [inputs, outputs]
    if task.kind in {'inspection', 'quality_case'}:
        # 只读本次运行，不能用检验任务后来换源的最新result替旧任务授权。
        run_ids = {item for item in (
            inputs.get('run_id'), inputs.get('source_run_id'), outputs.get('run_id'),
        ) if item}
        runs = session.scalars(select(m.InspectionRun).where(
            m.InspectionRun.workspace_id == actor.workspace_id,
            or_(m.InspectionRun.task_id == task.id, m.InspectionRun.id.in_(run_ids)),
        ))
        evidence.extend(run.result for run in runs)
    elif task.kind == 'energy_analysis':
        rows = session.scalars(select(EnergyAnalysis).where(
            EnergyAnalysis.workspace_id == actor.workspace_id, EnergyAnalysis.task_id == task.id,
        ))
        evidence.extend(row.provenance for row in rows)
    elif task.kind == 'maintenance_analysis' and outputs.get('analysis_id'):
        row = session.scalar(select(m.MaintenanceAnalysis).where(
            m.MaintenanceAnalysis.workspace_id == actor.workspace_id,
            m.MaintenanceAnalysis.id == outputs['analysis_id'],
        ))
        if row is None:
            raise DomainError(404, 'task_not_found', '任务不存在或已无访问权限')
        evidence.append(row.provenance)
    elif task.kind == 'maintenance_work_order':
        rows = session.scalars(select(m.MaintenanceWorkOrder).where(
            m.MaintenanceWorkOrder.workspace_id == actor.workspace_id, m.MaintenanceWorkOrder.task_id == task.id,
        ))
        evidence.extend(row.source_snapshot for row in rows)
    for query_id in sorted(set(query_references(evidence))):
        try:
            DataService(session, actor).get_query(query_id)
        except DomainError as exc:
            # 列权限校验的422带table字段；数据已不可见时，任务标题/事件也不对外透露。
            from ..modules.data.sql_safety import SqlFieldValidationError
            if exc.status in {403, 404} or isinstance(exc, SqlFieldValidationError) or (
                    exc.status == 422 and exc.fields.get('table')):
                raise DomainError(403, 'task_evidence_access_denied', '当前账号无法访问任务所引用的数据') from exc
            raise
