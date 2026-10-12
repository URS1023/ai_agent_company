"""Publish six local device workflows; resume schedules only after backend reload."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend'), str(ROOT / 'scripts')]


def main():
    """把 6 个既有设备流程改写为「紧凑版」并按阶段发布。

    参数：--phase prepare（默认，只落盘 before/prepared）、publish、activate；三阶段按序执行，
    各阶段都校验工作流版本未被并发修改、数据源仍是本地库、元数据已就绪（不隐式刷新）。
    前置条件：仅本机执行（校验 @@hostname 与 szh_new/company_agent），
    依赖 .implementation/workflow_query_repair_20260917/workflows_before.json 中的 6 条 alert 域流程。
    副作用：publish 阶段保存并发布新图、暂停原调度；activate 阶段把调度改成每小时错峰并激活；
    期间会对采集 SQL 做一次授权校验（authorize_sql），保证只读且表范围合规。
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
    from app.modules.analytics.models import SourceMetadata
    from app.modules.analytics.connections import source_version
    from app.modules.analytics.sql_safety import authorize_sql
    from app.modules.alerts.monitoring import snapshot_queries
    from app.modules.workflows.models import Workflow
    from app.modules.workflows.schemas import WorkflowInput, FlowGraph
    from app.modules.workflows.graph import executable_graph, graph_sources
    from app.modules.workflows.service import save_workflow, change_status
    from app.modules.system_tools.models import ScheduledJob
    from app.modules.system_tools.schemas import JobInput
    from app.modules.system_tools.schedules import job_view, save_job, change_job
    from configurations.szh_new.compact_device_workflow import build_graph

    folder = ROOT / '.implementation/compact_device_release_20260917'
    folder.mkdir(exist_ok=True)
    previous = json.loads((ROOT / '.implementation/workflow_query_repair_20260917/workflows_before.json').read_text(encoding='utf-8'))
    targets = [r for r in previous if 'alert' in r['domains'] and r['published_revision']]
    if len(targets) != 6:
        raise RuntimeError('Expected six existing device workflows')
    with new_session() as db:
        if db.scalar(text('SELECT @@hostname')).lower() != os.environ['COMPUTERNAME'].lower():
            raise RuntimeError('Local database identity mismatch')
        contexts, prepared, originals = {}, [], []
        for offset, original in enumerate(targets):
            row = db.get(Workflow, original['id'])
            member = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == row.workspace_id,
                               WorkspaceMember.user_id == row.owner_user_id))
            if not member:
                raise RuntimeError('Missing workspace membership')
            ctx = contexts[row.id] = AuthContext(db, db.get(User, member.user_id), db.get(Workspace, member.workspace_id), member.role, '')
            sources = graph_sources(FlowGraph.model_validate(row.graph))
            if len(sources) != 1:
                raise RuntimeError('Source binding is not unique')
            source = db.get(DataSource, next(iter(sources)))
            cfg = source.connection_config
            require_local(cfg.get('host'), int(cfg.get('port') or 3306), cfg.get('databaseName') or cfg.get('database'))
            metadata = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == source.id,
                                  SourceMetadata.workspace_id == row.workspace_id))
            if not metadata or metadata.status != 'ready' or metadata.source_version != source_version(source):
                raise RuntimeError('Ready metadata required; no implicit refresh')
            for sql in snapshot_queries(['device-validation'], '2026-08-18 00:00:00', '2026-09-17 00:00:00').values():
                authorize_sql(sql, provider='mysql', tables=metadata.tables_json, max_rows=500, database='szh_new')
            name = row.name
            keyword = ('主变' if '110kV' in name else ['电石炉', '相变压器'] if '变压器' in name else
                       '机器人' if '机器人' in name else '加料机' if '加料机' in name else '液压' if '液压' in name else 'DCS')
            graph = build_graph({'id': source.id, 'version': None}, keyword)
            executable_graph(ctx, FlowGraph.model_validate(graph))
            job = db.scalars(select(ScheduledJob).where(ScheduledJob.target_id == row.id,
                             ScheduledJob.workspace_id == row.workspace_id, ScheduledJob.status != 'archived')).one()
            originals.append({'workflow': {'id': row.id, 'name': name, 'graph': row.graph, 'version': row.version,
                              'publishedRevision': row.published_revision, 'description': row.description, 'domains': row.domains},
                              'schedule': job_view(job)})
            prepared.append({'id': row.id, 'name': name, 'domains': row.domains, 'graph': graph, 'version': row.version,
                             'description': '按源库设备范围采集最近30天巡检/保养及未完成维修，保存快照、确定性判断并生成AI报告；缺失数据待核实。',
                             'scheduleId': job.id, 'scheduleExpression': f'{offset * 5} * * * *'})
        if phase == 'prepare':
            (folder / 'before.json').write_text(json.dumps(originals, ensure_ascii=False, indent=2), encoding='utf-8')
            (folder / 'prepared.json').write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding='utf-8')
            print('Six graphs and all collection SQL validated against authorized local metadata; no workflow executed.')
            return
        reviewed = json.loads((folder / 'prepared.json').read_text(encoding='utf-8'))
        if phase == 'publish':
            for item in reviewed:
                row = db.get(Workflow, item['id'])
                if row.version != item['version']:
                    raise RuntimeError('Workflow changed since preparation')
            for item in reviewed:
                job = db.get(ScheduledJob, item['scheduleId'])
                if job.status == 'active':
                    change_job(contexts[item['id']], job.id, job.version, 'pause')
            published = []
            for item in reviewed:
                ctx = contexts[item['id']]
                payload = WorkflowInput.model_validate({k: item[k] for k in ['name', 'domains', 'description', 'graph', 'version']})
                saved = save_workflow(ctx, payload, item['id'])
                change_status(ctx, item['id'], saved.version, publish=True)
                row = db.get(Workflow, item['id'])
                published.append({'id': row.id, 'version': row.version, 'publishedRevision': row.published_revision})
                (folder / 'published.json').write_text(json.dumps(published, indent=2), encoding='utf-8')
            print('Published six compact workflows. Their schedules are paused for backend reload.')
            return
        published = {r['id']: r for r in json.loads((folder / 'published.json').read_text(encoding='utf-8'))}
        after = []
        for item in reviewed:
            row, job = db.get(Workflow, item['id']), db.get(ScheduledJob, item['scheduleId'])
            if row.version != published[row.id]['version'] or job.status != 'paused':
                raise RuntimeError('Activation state changed; inspect before continuing')
            payload = JobInput.model_validate({
                'code': job.code, 'name': row.name + ' · 每小时自动分析',
                'description': '按源库巡检和保养周期判断，每小时错峰采集并保存AI报告；业务周期不等于AI运行间隔。',
                'taskType': job.task_type, 'targetId': row.id, 'scheduleKind': 'cron',
                'scheduleExpression': item['scheduleExpression'], 'timezone': 'Asia/Shanghai',
                'payload': {**job.payload, 'mode': 'published', 'version': row.version}, 'status': 'active', 'version': job.version,
            })
            after.append(save_job(contexts[row.id], payload, job.id))
        (folder / 'schedules_after.json').write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Six hourly staggered schedules activated; existing workflow IDs, source bindings and history retained.')


if __name__ == '__main__':
    main()
