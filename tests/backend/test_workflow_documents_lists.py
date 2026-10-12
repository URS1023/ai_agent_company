"""验证工作流里的列表节点（筛选/排序/分页）与文档抽取节点。

验证行为：列表节点的筛选、排序与条数限制在真实执行路径生效；空列表给出显式空输出；
非法排序参数报出可操作错误；上传文档由真实工作流完成抽取。
隔离方式：复用 test_workflows/test_workflow_node_matrix 的建图与执行夹具，
落库走 conftest 内存 SQLite，不连业务库、不调用外部模型。
边界：空集合不能变成「没有输出」；非法参数必须早失败而不是产出错误结果。
"""
import pytest

from app.errors import ValidationError
from app.modules.workflows.nodes import execute_node
from test_workflow_node_matrix import budget, make_node
from test_workflows import create_flow, execute, graph, node, result


def test_list_filter_sort_limit_runs_in_workflow(client, administrator):
    flow = create_flow(client, administrator, graph(node('movies', 'list-operator', {
        'filterEnabled': True,
        'expression': {'op': 'gte', 'args': [{'field': 'rating'}, {'value': 9}]},
        'sortEnabled': True, 'sortField': 'rating', 'sortOrder': 'desc',
        'limitEnabled': True, 'limit': 1,
    }, {'input': {'nodeId': 'start', 'path': '$.movies'}})))
    output = result(execute(client, administrator, flow, {'movies': [
        {'title': 'A', 'rating': 9.2}, {'title': 'B', 'rating': 8.9}, {'title': 'C', 'rating': 9.5},
    ]}))['result']['outputs']['end']
    assert output == {'result': [{'title': 'C', 'rating': 9.5}], 'first_record': {'title': 'C', 'rating': 9.5},
                      'last_record': {'title': 'C', 'rating': 9.5}, 'count': 1}


def test_empty_list_has_explicit_empty_outputs():
    assert execute_node(None, make_node('list-operator'), {'input': []}, budget()) == {
        'result': [], 'first_record': None, 'last_record': None, 'count': 0,
    }


@pytest.mark.parametrize('items', [[1, '2'], [{'rating': 1}, {}], [True, False]])
def test_invalid_sort_is_actionable(items):
    with pytest.raises(ValidationError):
        execute_node(None, make_node('list-operator', {'sortEnabled': True, 'sortField': 'rating'}), {'input': items}, budget())


def test_uploaded_documents_are_extracted_by_real_workflow(client, administrator):
    upload = client.post('/api/files/batch', headers=administrator, files=[
        ('files', ('movies.txt', '电影 A：9.5 分'.encode(), 'text/plain')),
        ('files', ('more.txt', '电影 B：9.2 分'.encode(), 'text/plain')),
    ])
    assert upload.status_code == 201, upload.text
    assets = upload.json()['items']
    flow = create_flow(client, administrator, graph(node('document', 'document-extractor', {'separator': '\n'},
        {'input': {'nodeId': 'start', 'path': '$.files'}})))
    response = result(execute(client, administrator, flow, {'files': assets}))
    assert response['result']['outputs']['end']['text'] == '电影 A：9.5 分\n电影 B：9.2 分'
