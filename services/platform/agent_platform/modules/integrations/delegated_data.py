"""读取已保存的查询结果；不连接数据源，不接受或执行模型提供的SQL。"""

from agent_platform.modules.data.service import DataService

OPERATIONS = frozenset({'list_saved_queries', 'get_saved_query'})


def execute_data(session, actor, operation, args):
    """复用查询当前行列范围授权，只投影已返回页，来源冻结原查询范围。"""
    from .delegated_sources import capture_source

    actor.require('m06.read')
    service = DataService(session, actor)
    limit, page = args.get('limit', 20), args.get('page', 1)
    offset = (page - 1) * limit
    if operation == 'list_saved_queries':
        visible = service.visible_queries(q=args.get('q', ''), limit=limit, offset=offset)['items']
        items = [{key: row[key] for key in ('id', 'name', 'datasource_id', 'created_at', 'updated_at')
                  if key in row} for row in visible]
        payload = {'items': items, 'returned_count': len(items)}
        ids = [item['id'] for item in items]
    else:
        row = service.get_query(args['id'])
        result = row.result or {}
        rows = result.get('rows', [])[offset:offset + limit]
        payload = {'id': row.id, 'name': row.name, 'columns': result.get('columns', []),
                   'rows': rows, 'returned_count': len(rows), 'page': page,
                   'source_truncated': result.get('truncated') is True}
        ids = [row.id]
    sources = [capture_source(session, actor, 'saved_query', identifier, {'query_id': identifier})
               for identifier in ids]
    return payload, sources
