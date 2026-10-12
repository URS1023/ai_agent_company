"""Align reviewed local workflow schedules without changing their firing times."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'scripts')]


def main():
    """把已复核的本地工作流定时任务版本对齐到工作流当前版本。

    前置条件：本机 MySQL（库名必须为 szh_new、平台库为 company_agent），
    且 .implementation/workflow_query_repair_20260917 下已存在 applied.json 与 workflows_before.json；
    运行方式是直接执行本文件（无参数），命令必须在平台数据库所在本机运行（会校验 @@hostname == COMPUTERNAME）。
    影响范围：写库（更新 ScheduledJob.payload.version/version 并写审计），不动工作流本身；
    结束后逐一断言 scheduleKind/表达式/时区/下次运行时间/状态均保持不变——只要触发时间被改动就视为失败。
    """
    from sqlalchemy import select, text
    from sqlalchemy.engine import make_url
    from app.config import settings
    from benchmark_local_szh import require_local, local_listener
    url = make_url(settings.DATABASE_URL)
    require_local(url.host, url.port or 3306, 'szh_new')
    if url.database != 'company_agent':
        raise RuntimeError('Unexpected platform database')
    local_listener()
    from app.db import new_session
    from app.models import User, Workspace, WorkspaceMember
    from app.common.deps import AuthContext
    from app.common.authorization import require_permission
    from app.common.audit import audit
    from app.modules.system_tools.models import ScheduledJob
    from app.modules.system_tools.schedules import job_view
    from app.modules.workflows.models import Workflow

    folder = ROOT / '.implementation/workflow_query_repair_20260917'
    applied = {r['id']: r for r in json.loads((folder / 'applied.json').read_text(encoding='utf-8'))}
    originals = {r['id']: r for r in json.loads((folder / 'workflows_before.json').read_text(encoding='utf-8'))}
    with new_session() as db:
        if db.scalar(text('SELECT @@hostname')).lower() != os.environ['COMPUTERNAME'].lower():
            raise RuntimeError('Local server identity mismatch')
        jobs = db.scalars(select(ScheduledJob).where(
            ScheduledJob.target_id.in_(applied), ScheduledJob.task_type == 'workflow',
            ScheduledJob.status == 'active',
        ).with_for_update()).all()
        if len(jobs) != 15:
            raise RuntimeError('Expected exactly 15 reviewed schedules')
        before = [job_view(job) for job in jobs]
        backup = folder / 'schedules_before_alignment.json'
        if not backup.exists():
            backup.write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding='utf-8')
        for job in jobs:
            workflow = db.get(Workflow, job.target_id)
            if (workflow.workspace_id != job.workspace_id or workflow.status != 'published'
                    or workflow.published_revision != applied[job.target_id]['publishedRevision']
                    or job.payload.get('mode') != 'published'):
                raise RuntimeError('Reviewed schedule target changed')
            member = db.scalar(select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == job.workspace_id,
                WorkspaceMember.user_id == originals[job.target_id]['owner_user_id'],
            ))
            if member is None:
                raise RuntimeError('Missing current workspace membership')
            ctx = AuthContext(db, db.get(User, member.user_id), db.get(Workspace, member.workspace_id), member.role, '')
            require_permission(ctx, 'system.operations.manage')
            if job.payload.get('version') == workflow.version:
                continue
            previous = job.payload.get('version')
            job.payload = {**job.payload, 'version': workflow.version}
            job.version += 1
            audit(db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
                  action='system.schedule.align_workflow_revision', resource_type='scheduled_job',
                  resource_id=job.id, metadata={'previousVersion': previous, 'workflowVersion': workflow.version})
        db.commit()
        after = [job_view(job) for job in jobs]
        for old, new in zip(before, after):
            for key in ('scheduleKind', 'scheduleExpression', 'timezone', 'nextRunAt', 'status', 'targetId'):
                assert old[key] == new[key], key
        (folder / 'schedules_aligned.json').write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Verified 15 published workflow schedules; versions aligned; firing times and status unchanged.')


if __name__ == '__main__':
    main()
