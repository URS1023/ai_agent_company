"""可信委托工具的固定读取契约；身份来自请求头，不能由模型参数填写。"""

from copy import deepcopy

from .business_tools import READ_TOOLS

PREFIX = "/api/v1/delegated-business-tools"
SCHEMA_PATH = PREFIX + "/openapi.json"
HEADERS = ("X-OpenWebUI-User-Jwt", "X-OpenWebUI-Chat-Id", "X-OpenWebUI-Message-Id")
OPERATIONS = {operation: (path, permission, title) for path, (permission, operation, title) in READ_TOOLS.items()}
OPERATIONS.update({
    'list_saved_queries': ('/api/v1/queries', 'm06.read', '查找已保存的数据查询'),
    'get_saved_query': ('/api/v1/queries/{id}', 'm06.read', '读取已保存的查询结果'),
    'list_knowledge_spaces': ('/api/v1/kb/spaces', 'm08.read', '查找平台知识空间'),
    'search_platform_knowledge': ('/api/v1/kb/search', 'm08.read', '检索指定平台知识空间'),
})
TOOL_PATHS = frozenset(PREFIX + "/" + operation for operation in OPERATIONS)
FILTERS = {
    "list_devices": {"q"}, "list_maintenance_analyses": {"q", "device_id"},
    "list_incidents": {"q", "status"}, "list_products": {"q"},
    "list_product_batches": {"q", "product_id"}, "list_inspections": {"q", "target_type"},
    "list_inspection_runs": {"id"}, "list_reports": {"q"},
    'list_saved_queries': {'q'}, 'get_saved_query': {'id'}, 'list_knowledge_spaces': set(),
    'search_platform_knowledge': {'space_id', 'query', 'top_k'},
}
PAGED_OPERATIONS = frozenset(operation for operation in OPERATIONS if operation.startswith('list_')) | {'get_saved_query'}


def delegated_tool_spec(actor):
    """仅导出固定读取与知识检索操作；分页不返回隐藏记录总量或下一页提示。"""
    actor.require("m02.read")
    paths = {}
    for operation, (original, permission, title) in OPERATIONS.items():
        if not actor.can(permission):
            continue
        fields = set(FILTERS.get(operation, {"id"}))
        if operation in PAGED_OPERATIONS:
            fields.update({"page", "limit"})
        parameters = []
        for field in sorted(fields):
            maximum = {'limit': 20, 'page': 1000, 'top_k': 5}
            defaults = {'limit': 20, 'page': 1, 'top_k': 5}
            schema = ({"type": "integer", "minimum": 1, "maximum": maximum[field], 'default': defaults[field]}
                      if field in maximum else {"type": "string", 'minLength': 1 if field in {'id', 'space_id', 'query'} else 0,
                          "maxLength": 1000 if field == 'query' else 160 if field == "q" else 80})
            parameters.append({"name": field, "in": "query", "required": field in {'id', 'space_id', 'query'}, "schema": schema})
        result = ({"type": "object", "required": ["items", "returned_count"], "additionalProperties": False,
                   "properties": {"items": {"type": "array", "items": {"type": "object"}},
                                  "returned_count": {"type": "integer", "minimum": 0}}}
                  if operation.startswith("list_") else {"type": "object"})
        description = ('检索明确指定的一个平台知识空间，返回授权片段与原件来源。' if operation == 'search_platform_knowledge' else
                       '读取已保存查询的一页结果，不执行SQL；source_truncated表示原取数已被截断。' if operation == 'get_saved_query' else
                       '读取当前用户获准访问的已保存结果。列表只提供当前页及本页条数。')
        paths[PREFIX + "/" + operation] = {"get": {"operationId": operation, "summary": title,
            "description": description,
            "parameters": parameters, "security": [{"PlatformToken": []}],
            "responses": {"200": {"description": "已授权结果", "content": {"application/json": {"schema": result}}}}}}
    return {"openapi": "3.1.0", "info": {"title": "当前用户的制造业务查询", "version": "1.0.0"},
            "servers": [{"url": "/"}], "paths": deepcopy(paths),
            "components": {"securitySchemes": {"PlatformToken": {"type": "http", "scheme": "bearer"}}}}
