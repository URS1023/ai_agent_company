"""Split the six reviewed local device entries into three independently scheduled flows."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend'), str(ROOT / 'scripts')]


def main():
    """把已发布的 6 个综合设备流程拆分为 18 个独立流程（巡检/保养/维修各一套）。

    参数：--phase prepare（默认，只生成 before.json/prepared.json）、publish、activate；
    三阶段必须按序执行，且各阶段产物文件存在时才继续（publish 前若已有 published.json 会拒绝重跑）。
    前置条件：仅本机（校验 @@hostname 与 szh_new/company_agent），
    .implementation/compact_device_release_20260917/published.json 列出 6 个待拆分流程。
    副作用：publish 阶段写库（新建 18 个工作流、设备域绑定、每小时的错峰调度，默认 paused）；
    activate 阶段才激活调度。每步都校验数据源仍是本地库、图可执行、版本未被并发修改。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['prepare', 'publish', 'activate'], default='prepare')
    phase = parser.parse_args().phase
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
    from app.models import User, Workspace, WorkspaceMember, DataSource
    from app.common.deps import AuthContext
    from app.modules.workflows.models import Workflow, DeviceWorkflowBinding
    from app.modules.workflows.schemas import WorkflowInput, FlowGraph
    from app.modules.workflows.graph import executable_graph, graph_sources
    from app.modules.workflows.service import save_workflow, change_status
    from app.modules.system_tools.models import ScheduledJob
    from app.modules.system_tools.schemas import JobInput
    from app.modules.system_tools.schedules import job_view, save_job, change_job
    from app.modules.system.service_helpers import commit_change
    from configurations.szh_new.compact_device_workflow import build_graph
    folder = ROOT / '.implementation/device_split_20260917'
    folder.mkdir(exist_ok=True)
    def save(name, value):
        (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    ids = [r['id'] for r in json.loads((ROOT / '.implementation/compact_device_release_20260917/published.json').read_text())]
    with new_session() as db:
        if db.scalar(text('SELECT @@hostname')).lower() != os.environ['COMPUTERNAME'].lower():
            raise RuntimeError('Local database identity mismatch')
        contexts, prepared, before = {}, [], []
        for index, workflow_id in enumerate(ids):
            row = db.get(Workflow, workflow_id)
            member = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == row.workspace_id,
                                WorkspaceMember.user_id == row.owner_user_id))
            if not member:
                raise RuntimeError('No current workspace membership')
            ctx = contexts[row.id] = AuthContext(db, db.get(User, member.user_id), db.get(Workspace, member.workspace_id), member.role, '')
            source_ids = graph_sources(FlowGraph.model_validate(row.graph))
            if len(source_ids) != 1:
                raise RuntimeError('Non-unique source')
            source = db.get(DataSource, next(iter(source_ids)))
            cfg = source.connection_config
            require_local(cfg.get('host'), int(cfg.get('port') or 3306), cfg.get('databaseName') or cfg.get('database'))
            root = next(n for n in row.graph['nodes'] if n['id'] == 'devices')
            keywords = [f['value'] for f in root['config']['filters'] if f['field'] == 'c_deviceName']
            if not keywords:
                raise RuntimeError('Missing device scope')
            binding = db.scalars(select(DeviceWorkflowBinding).where(DeviceWorkflowBinding.workflow_id == row.id)).one()
            job = db.scalars(select(ScheduledJob).where(ScheduledJob.target_id == row.id, ScheduledJob.status != 'archived')).one()
            before.append({'id': row.id, 'name': row.name, 'version': row.version, 'graph': row.graph,
                           'publishedRevision': row.published_revision, 'schedule': job_view(job),
                           'bindingId': binding.id, 'deviceId': binding.device_id})
            base_name = row.name.removesuffix('工作流').removesuffix('监控').removesuffix('巡检')
            for offset, (category, label) in enumerate([('inspection', '巡检'), ('maintenance', '保养'), ('repair', '维修')]):
                graph = build_graph({'id': source.id, 'version': None}, keywords, category=category)
                executable_graph(ctx, FlowGraph.model_validate(graph))
                prepared.append({'parentId': row.id, 'id': row.id if category == 'inspection' else None,
                                 'version': row.version if category == 'inspection' else None,
                                 'deviceId': binding.device_id, 'category': category,
                                 'name': base_name + ' · ' + label + '工作流', 'graph': graph,
                                 'scheduleId': job.id if category == 'inspection' else None,
                                 'cron': f'{index * 9 + offset * 3} * * * *'})
        if phase == 'prepare':
            save('before.json', before)
            save('prepared.json', prepared)
            print('Prepared 18 isolated workflows and confirmed six existing device bindings; no execution.')
            return
        reviewed = json.loads((folder / 'prepared.json').read_text(encoding='utf-8'))
        if phase == 'publish':
            if (folder / 'published.json').exists():
                raise RuntimeError('Publication manifest exists; inspect partial progress before rerunning')
            for item in reviewed:
                if item['id'] and db.get(Workflow, item['id']).version != item['version']:
                    raise RuntimeError('Workflow changed since review')
            for item in reviewed:
                if item['scheduleId']:
                    job = db.get(ScheduledJob, item['scheduleId'])
                    if job.status == 'active':
                        change_job(contexts[item['parentId']], job.id, job.version, 'pause')
            published = []
            for item in reviewed:
                ctx = contexts[item['parentId']]
                saved = save_workflow(ctx, WorkflowInput(name=item['name'], domains=['alert'],
                    description='独立' + item['category'] + '流程；仅采集本业务，保存快照和判断，再生成单独AI报告。',
                    graph=FlowGraph.model_validate(item['graph']), version=item['version']), item['id'])
                changed = change_status(ctx, saved.id, saved.version, publish=True)
                workflow = db.get(Workflow, saved.id)
                if item['category'] != 'inspection':
                    binding = DeviceWorkflowBinding(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                              device_id=item['deviceId'], domain='alert_' + item['category'], workflow_id=workflow.id, provider='platform')
                    db.add(binding)
                    commit_change(ctx, 'workflow.device_category.bind', 'workflows', workflow.id,
                                  {'deviceId': item['deviceId'], 'category': item['category']})
                old_job = db.get(ScheduledJob, item['scheduleId']) if item['scheduleId'] else None
                job = save_job(ctx, JobInput(code=old_job.code if old_job else 'device.' + workflow.id.lower(),
                    name=item['name'] + ' · 每小时自动分析', description='独立取数、规则判断与AI报告；每小时错峰运行。',
                    taskType='workflow', targetId=workflow.id, scheduleKind='cron', scheduleExpression=item['cron'],
                    timezone='Asia/Shanghai', payload={'mode': 'published', 'version': workflow.version},
                    status='paused', version=old_job.version if old_job else 0), old_job.id if old_job else None)
                published.append({**{k: item[k] for k in ['parentId', 'category', 'name', 'cron']},
                                  'id': workflow.id, 'version': workflow.version, 'scheduleId': job['id']})
                save('published.json', published)
            print('Published 18 workflows; schedules paused pending backend reload.')
            return
        published = json.loads((folder / 'published.json').read_text(encoding='utf-8'))
        if len(published) != 18:
            raise RuntimeError('Incomplete publication')
        jobs = []
        for item in published:
            workflow, job = db.get(Workflow, item['id']), db.get(ScheduledJob, item['scheduleId'])
            if workflow.version != item['version'] or job.status != 'paused':
                raise RuntimeError('Activation state changed')
            jobs.append(change_job(contexts[item['parentId']], job.id, job.version, 'activate'))
        save('schedules_after.json', jobs)
        print('18 category schedules activated; original combined schedules replaced, quality unchanged.')


if __name__ == '__main__':
    main()
