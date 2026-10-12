"""Real database coverage for no-SQL table/field selection and successive reads."""
import pytest

from app.modules.workflows.value_schemas import ReadValuesConfig
from test_analytics import source
from test_workflows import API, create_flow, execute, graph, node, result


def config(source, **extra):
    return {"readMode": "table", "datasourceId": source["id"], "tableName": "sales",
            "fields": ["id", "department", "amount"], **extra}


def run_read(client, administrator, source, **extra):
    flow = create_flow(client, administrator, graph(node("read", "read-values", config(source, **extra))))
    return execute(client, administrator, flow)


def test_select_fields_preview_and_saved_execution(client, administrator, source):
    selected = config(source, fields=["id", "amount"])
    preview = result(client.post(API + "/value-preview", headers=administrator, json=selected))
    assert len(preview["data"]) == 5
    assert set(preview["data"][0]) == {"id", "amount"}
    assert preview["tableName"] == "sales" and "generatedSql" not in preview
    run = result(run_read(client, administrator, source, fields=["id", "amount"]))
    assert run["status"] == "completed", run
    assert run["result"]["outputs"]["end"]["data"] == preview["data"]


@pytest.mark.parametrize("operator,value,ids", [
    ("eq", "设备", [1, 3, 5]), ("ne", "设备", [2, 4]), ("contains", "备", [1, 3, 5]),
    ("eq", "设备' OR 1=1 --", []), ("contains", "%", []), ("contains", "_", []),
])
def test_filters_bind_values_and_do_not_treat_literal_as_sql(client, administrator, source, operator, value, ids):
    response = run_read(client, administrator, source, filters=[{"field": "department", "operator": operator, "value": value}])
    run = result(response)
    assert run["status"] == "completed", run
    assert [r["id"] for r in run["result"]["outputs"]["end"]["data"]] == ids


@pytest.mark.parametrize("operator,ids", [("is_null", [4]), ("not_null", [1, 2, 3, 5])])
def test_explicit_null_filters(client, administrator, source, operator, ids):
    run = result(run_read(client, administrator, source, filters=[{"field": "amount", "operator": operator}]))
    assert [r["id"] for r in run["result"]["outputs"]["end"]["data"]] == ids


@pytest.mark.parametrize("department,ids", [("设备", [1, 3, 5]), ("没有此部门", [])])
def test_two_reads_use_upstream_values_including_empty(client, administrator, source, department, ids):
    flow = create_flow(client, administrator, graph(
        node("first", "read-values", config(source, fields=["id"], filters=[{"field": "department", "value": department}])),
        node("second", "read-values", config(source, filters=[{"field": "id", "operator": "in", "valueSource": "input", "inputField": "id"}]))))
    run = result(execute(client, administrator, flow))
    assert run["status"] == "completed", run
    assert [r["id"] for r in run["result"]["outputs"]["end"]["data"]] == ids


def test_ambiguous_upstream_and_missing_field_are_explained(client, administrator, source):
    for field, expected in [("id", "多个值"), ("missing", "没有字段")]:
        flow = create_flow(client, administrator, graph(
            node("first", "read-values", config(source, fields=["id"])),
            node("second", "read-values", config(source, filters=[{"field": "id", "valueSource": "input", "inputField": field}]))))
        run = result(execute(client, administrator, flow), 502)
        assert expected in run["errorMessage"], run


def test_preview_bound_and_runtime_never_forwards_partial_rows(client, administrator, source):
    preview = result(client.post(API + "/value-preview", headers=administrator, json=config(source, maxRows=2)))
    assert len(preview["data"]) == 2 and preview["truncated"]
    run = result(run_read(client, administrator, source, maxRows=2), 502)
    assert "部分记录" in run["errorMessage"]


@pytest.mark.parametrize("changes,status", [
    ({"tableName": "restricted_data", "fields": ["private_note"]}, 404),
    ({"tableName": "sales; DROP TABLE sales"}, 404),
    ({"fields": ["not_a_column"]}, 422),
    ({"filters": [{"field": "unknown", "value": "x"}]}, 422),
    ({"fields": []}, 422), ({"datasourceVersion": 999}, 422),
])
def test_table_and_column_permissions_and_versions(client, administrator, source, changes, status):
    response = client.post(API + "/value-preview", headers=administrator, json=config(source, **changes))
    assert response.status_code == status, response.text


def test_previous_dataset_contract_stays_compatible():
    value = ReadValuesConfig(datasourceId="source", templateId="template", templateVersion=1)
    assert value.read_mode == "dataset"


@pytest.mark.parametrize('mode,expected_id,count', [('all', None, 5), ('latest', 5, 2), ('unique', None, 2)])
def test_associated_read_keeps_upstream_rows_and_orders_matches(client, administrator, source, mode, expected_id, count):
    first = config(source, fields=['department'], fieldAliases={'department': '所属部门'},
                   filters=[{'field': 'id', 'operator': 'lte', 'value': 2}])
    second = config(source, fields=['id', 'amount'], fieldAliases={'id': '匹配编号', 'amount': '实测值'},
                    matchMode=mode, matches=[{'field': 'department', 'inputField': '所属部门'}],
                    orderBy=[{'field': 'id', 'direction': 'desc'}])
    flow = create_flow(client, administrator, graph(node('first', 'read-values', first), node('second', 'read-values', second)))
    run = result(execute(client, administrator, flow))
    rows = run['result']['outputs']['end']['data']
    assert len(rows) == count and all('所属部门' in row for row in rows)
    assert rows[0]['匹配记录数'] == 3
    if mode == 'latest': assert rows[0]['匹配编号'] == expected_id
    if mode == 'unique': assert rows[0]['匹配编号'] is None


def test_associated_read_retains_unmatched_and_rejects_overwriting(client, administrator, source):
    first = node('first', 'read-values', config(source, fields=['id']))
    second = config(source, fields=['amount'], matchMode='unique', matches=[{'field':'id','inputField':'id'}],
                    filters=[{'field':'id','value':999}])
    flow = create_flow(client, administrator, graph(first, node('second', 'read-values', second)))
    rows = result(execute(client, administrator, flow))['result']['outputs']['end']['data']
    assert len(rows) == 5 and all(row['amount'] is None and row['匹配记录数'] == 0 for row in rows)
    second['fields'] = ['id']
    flow = create_flow(client, administrator, graph(first, node('second', 'read-values', second)))
    assert '重复' in result(execute(client, administrator, flow),502)['errorMessage']


def test_optional_blank_filter_and_empty_batch_remain_distinct(client, administrator, source):
    # Scalar start input supplies the optional filter; an empty upstream batch must never expand.
    read = node('read', 'read-values', config(source, filters=[{'field':'department','valueSource':'input','inputField':'filterText','skipEmpty':True}]))
    data = graph(read)
    data['nodes'][0]['config'] = {'variables':[{'variable':'filterText','label':'筛选','default':''}]}
    flow = create_flow(client,administrator,data)
    assert len(result(execute(client,administrator,flow))['result']['outputs']['end']['data']) == 5
    first = node('first', 'read-values', config(source, fields=['department'], filters=[{'field':'id','value':999}]))
    read['config']['filters'][0]['inputField'] = 'department'
    flow = create_flow(client,administrator,graph(first,read))
    assert result(execute(client,administrator,flow))['result']['outputs']['end']['data'] == []


def test_composite_match_does_not_borrow_other_device_or_missing_identifier():
    from app.modules.workflows.table_matching import attach_rows
    cfg = ReadValuesConfig(readMode='table', datasourceId='source', tableName='measurements',
        fields=['actual'], matchMode='unique',
        matches=[{'field':'device', 'inputField':'设备'}, {'field':'point', 'inputField':'测点'}],
        keepInputFields=['设备', '测点'])
    inputs = {'input': {'data': [{'设备':'A', '测点':'1', 'unneeded':'large'},
                                {'设备':'B', '测点':'2'}, {'设备':None, '测点':'1'}]}}
    rows = attach_rows(cfg, inputs, [{'device':'A', 'point':'1', 'actual':12},
        {'device':'A', 'point':'2', 'actual':999}, {'device':None, 'point':'1', 'actual':999}])
    assert [r['actual'] for r in rows] == [12, None, None]
    assert [r['匹配记录数'] for r in rows] == [1, 0, 0]
    assert all('unneeded' not in r for r in rows)


def test_explicit_ordered_limit_is_a_complete_intentional_selection(client, administrator, source):
    run = result(run_read(client, administrator, source, fields=['id'],
        orderBy=[{'field':'id', 'direction':'desc'}], take=2))
    assert run['result']['outputs']['end']['data'] == [{'id':5}, {'id':4}]
