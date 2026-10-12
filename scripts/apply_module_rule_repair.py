"""Save/publish reviewed graph revisions on the verified local platform only.

No workflow execution, schedule creation, source rebinding or business writes.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend'), str(ROOT / 'scripts')]


def main():
    """校验（默认）或保存并发布（--apply）15 个工作流图的复核修订。

    参数：--apply 才真正调用 save_workflow/change_status 写入并发布；缺省是只读校验。
    前置条件：本机 szh_new 与平台库 company_agent，且 .implementation/workflow_query_repair_20260917
    下有 workflows_prepared.json 与 workflows_before.json；必须先在本机执行（校验 @@hostname）。
    副作用：--apply 时写库并生成 applied.json；不执行工作流、不新建调度、不改数据源绑定。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
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
    from app.modules.analytics.connections import source_version
    from app.modules.analytics.models import SourceMetadata
    from app.modules.workflows.models import Workflow
    from app.modules.workflows.schemas import WorkflowInput, FlowGraph
    from app.modules.workflows.service import save_workflow, change_status
    from app.modules.workflows.graph import executable_graph
    folder = ROOT / '.implementation/workflow_query_repair_20260917'
    prepared = json.loads((folder / 'workflows_prepared.json').read_text(encoding='utf-8'))
    originals = {r['id']: r for r in json.loads((folder / 'workflows_before.json').read_text(encoding='utf-8'))}
    with new_session() as db:
        if db.scalar(text('SELECT @@hostname')).lower() != os.environ['COMPUTERNAME'].lower():
            raise RuntimeError('Local server identity mismatch')
        contexts = {}
        for item in prepared:
            old = originals[item['id']]
            row = db.get(Workflow, item['id'])
            if row.version != item['version']:
                raise RuntimeError('Workflow changed since review: ' + row.name)
            if row.workspace_id not in contexts:
                member = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == row.workspace_id, WorkspaceMember.user_id == old['owner_user_id']))
                if not member:
                    raise RuntimeError('Missing current workspace membership')
                contexts[row.workspace_id] = AuthContext(db, db.get(User, member.user_id), db.get(Workspace, member.workspace_id), member.role, '')
            for source_id in {n['config']['datasourceId'] for n in item['graph']['nodes'] if n['config'].get('datasourceId')}:
                source = db.get(DataSource, source_id)
                cfg = source.connection_config
                require_local(cfg.get('host'), int(cfg.get('port') or 3306), cfg.get('databaseName') or cfg.get('database'))
                metadata = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == source_id, SourceMetadata.workspace_id == row.workspace_id))
                if not metadata or metadata.status != 'ready' or metadata.source_version != source_version(source):
                    raise RuntimeError('Source metadata must be synchronized separately; no implicit refresh')
            assert {n['config']['datasourceId'] for n in item['graph']['nodes'] if n['config'].get('datasourceId')} == {n['config']['datasourceId'] for n in old['graph']['nodes'] if n['config'].get('datasourceId')}
            executable_graph(contexts[row.workspace_id], FlowGraph.model_validate(item['graph']))
        print('Validated all 15 graphs against local authorized metadata; no workflow executed')
        if not args.apply:
            return
        applied = []
        for item in prepared:
            old = originals[item['id']]
            ctx = contexts[old['workspace_id']]
            payload = WorkflowInput.model_validate({k: v for k, v in item.items() if k != 'id'})
            saved = save_workflow(ctx, payload, item['id'])
            published = change_status(ctx, item['id'], saved.version, publish=True)
            applied.append({'id': item['id'], 'name': item['name'], 'previousRevision': old['published_revision'], 'publishedRevision': published.published_revision if hasattr(published, 'published_revision') else published.model_dump(by_alias=True)['publishedRevision']})
            (folder / 'applied.json').write_text(json.dumps(applied, ensure_ascii=False, indent=2), encoding='utf-8')
            print(item['name'] + ': published')


if __name__ == '__main__':
    main()
