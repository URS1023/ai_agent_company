"""Customer-facing multi-device/sample quality runs use real HTTP and shared workflow records."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import settings
from app.modules.quality.models import QualityInspection
from app.modules.system.models import Role
from app.modules.workflows.models import WorkflowExecution
from test_alerts import equipment
from test_collection import create_source
from test_quality import API, metric, template
from test_quality_images import dynamic_detector, upload
from test_system_foundation import create_role, create_user, activate_user
from test_workflows import node, edge, create_flow, result, execute


@pytest.fixture(autouse=True)
def isolated_images(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "quality-run-images"))


def prepared(client, headers, remote, kinds=("appearance",)):
    nodes, edges, groups, assets = [node('start', 'start')], [], [], []
    for index, category in enumerate(kinds):
        body = {"sample": "${input.sampleId}"}
        if category == 'appearance':
            body['image'] = '${input.imageBase64}'
        source, _ = create_source(client, headers, remote, method='POST', bodies=[body], allowAutomatic=True)
        device = equipment(client, headers, code='QC-' + uuid4().hex[:12], datasourceId=source['id'])
        indicator, _ = metric(client, headers, **({'category': 'appearance', 'standardMin': None, 'standardMax': None} if category == 'appearance' else {}))
        standard, _ = template(client, headers, indicator)
        detector_id, evaluator_id = 'detect' + str(index), 'evaluate' + str(index)
        nodes.extend([node(detector_id, 'qc-' + category, {'deviceId': device['id'], 'metricId': indicator['id'],
            'metricVersion': 1, 'datasourceId': source['id'], 'datasourceVersion': 1, 'requestMode': 'input'},
            inputs={'input': {'nodeId': 'start', 'path': '$.samples' + str(index)}}),
            node(evaluator_id, 'qc-evaluate', {'deviceId': device['id'], 'templateId': standard['id'], 'templateRevision': 1, 'sampleMode': 'input'})])
        edges.extend([edge('start', detector_id), edge(detector_id, evaluator_id), edge(evaluator_id, 'merge')])
        groups.append({'nodeId': evaluator_id, 'samples': [{'sampleId': 'one', 'imageFileId': None}, {'sampleId': 'two', 'imageFileId': None}]})
        if category == 'appearance':
            image = result(upload(client, headers), 201)
            for sample in groups[-1]['samples']:
                sample['imageFileId'] = image['id']
        assets.append((source, device, indicator, standard))
    nodes.extend([node('merge', 'merge'), node('end', 'end')])
    edges.append(edge('merge', 'end'))
    flow = create_flow(client, headers, {'nodes': nodes, 'edges': edges})
    flow = result(client.post('/api/v1/workflows/' + flow['id'] + '/publish', headers=headers, json={'version': flow['version']}))
    payload = {'version': flow['version'], 'publishedRevision': flow['publishedRevision'], 'idempotencyKey': uuid4().hex,
               'groups': groups, 'confirmWrites': False, 'timeoutSeconds': 120}
    return flow, payload, assets


def submit(client, headers, flow, payload):
    return client.post(API + '/workflows/' + flow['id'] + '/runs', headers=headers, json=payload)


def test_customer_batch_has_fixed_plan_real_samples_and_no_replay(client, administrator, dynamic_detector, db):
    remote, _ = dynamic_detector
    flow, payload, assets = prepared(client, administrator, remote, ('appearance', 'component'))
    plan = result(client.get(API + '/workflows/' + flow['id'] + '/run-plan', headers=administrator))
    assert len(plan['groups']) == 2 and plan['publishedRevision'] == flow['publishedRevision']
    assert [item['imageRequired'] for item in plan['groups']] == [True, False]
    assert not remote[1]
    run = result(submit(client, administrator, flow, payload))
    assert run['execution']['status'] == 'completed' and len(run['samples']) == 4
    assert [row['outcome'] for row in run['samples']] == ['failed', 'failed', 'passed', 'passed']
    assert len(remote[1]) == 4
    reports = db.scalars(select(QualityInspection)).all()
    assert len(reports) == 4 and all(row.execution_id == run['execution']['id'] for row in reports)
    assert len({(row.device_id, row.sample_id) for row in reports}) == 4
    assert result(submit(client, administrator, flow, payload)) == run and len(remote[1]) == 4
    stored = result(client.get(API + f'/workflows/{flow["id"]}/runs/{run["execution"]["id"]}', headers=administrator))
    assert stored == run
    for sample in run['samples']:
        report = result(client.get(API + '/inspections/' + sample['inspectionId'], headers=administrator))
        assert report['sampleId'] == sample['sampleId'] and report['results'][0]['provenance']['mode'] == 'detector'
    assert len(remote[1]) == 4
    changed = deepcopy(payload); changed['groups'][0]['samples'][0]['sampleId'] = 'new'
    assert submit(client, administrator, flow, changed).status_code == 409 and len(remote[1]) == 4


@pytest.mark.parametrize('change', ['stale-version', 'stale-revision', 'missing-group', 'extra-group', 'duplicate-group', 'duplicate-sample', 'empty-sample', 'missing-image', 'unknown-image', 'unrelated-image'])
def test_entire_form_validated_before_any_device_call(client, administrator, dynamic_detector, db, change):
    remote, _ = dynamic_detector
    flow, payload, _ = prepared(client, administrator, remote, ('component', 'appearance'))
    if change == 'stale-version': payload['version'] += 1
    elif change == 'stale-revision': payload['publishedRevision'] += 1
    elif change == 'missing-group': payload['groups'].pop()
    elif change == 'extra-group': payload['groups'].append({'nodeId': 'absent', 'samples': [{'sampleId': 'new'}]})
    elif change == 'duplicate-group': payload['groups'].append(payload['groups'][0])
    elif change == 'duplicate-sample': payload['groups'][0]['samples'][1]['sampleId'] = 'one'
    elif change == 'empty-sample': payload['groups'][0]['samples'][0]['sampleId'] = ' '
    elif change == 'missing-image': payload['groups'][1]['samples'][1]['imageFileId'] = None
    elif change == 'unknown-image': payload['groups'][1]['samples'][1]['imageFileId'] = 'not-owned'
    elif change == 'unrelated-image': payload['groups'][0]['samples'][0]['imageFileId'] = payload['groups'][1]['samples'][0]['imageFileId']
    assert submit(client, administrator, flow, payload).status_code in {404, 409, 422}
    assert not remote[1] and not db.scalars(select(WorkflowExecution)).all()


def test_broken_detector_produces_explicit_unreported_samples_and_no_replay(client, administrator, dynamic_detector, db):
    remote, state = dynamic_detector
    flow, payload, _ = prepared(client, administrator, remote)
    state['mode'] = 'wrong'
    run = result(submit(client, administrator, flow, payload), 502)
    assert run['execution']['status'] == 'failed' and all(row['inspectionId'] is None for row in run['samples'])
    assert not db.scalars(select(QualityInspection)).all()
    count = len(remote[1]); result(submit(client, administrator, flow, payload), 502)
    assert len(remote[1]) == count


def test_image_export_permission_for_later_group_checked_before_first_request(client, administrator, dynamic_detector, db):
    remote, _ = dynamic_detector
    flow, payload, _ = prepared(client, administrator, remote, ('component', 'appearance'))
    role = create_role(client, administrator, code='quality_runner', permissions=['quality.view','quality.execute','devices.view','datasources.view','workflows.view','workflows.execute','files.view'], scope='workspace')
    create_user(client, administrator, [role['id']]); runner = activate_user(client)
    assert submit(client, runner, flow, payload).status_code == 403 and not remote[1]
    saved = db.get(Role, role['id']); saved.permissions = [*saved.permissions, 'files.export']; db.commit()
    run = result(submit(client, runner, flow, payload))
    assert len(run['samples']) == 4 and len(remote[1]) == 4
    saved.permissions = [permission for permission in saved.permissions if permission != 'files.view']; db.commit()
    assert client.get(API + f'/workflows/{flow["id"]}/runs/{run["execution"]["id"]}', headers=runner).status_code == 403


def test_persisted_batch_retry_after_disable_does_not_request_new_detection(client, administrator, dynamic_detector):
    remote, _ = dynamic_detector
    flow, payload, _ = prepared(client, administrator, remote)
    run = result(submit(client, administrator, flow, payload)); count = len(remote[1])
    client.post('/api/v1/workflows/' + flow['id'] + '/disable', headers=administrator, json={'version': flow['version']})
    assert result(submit(client, administrator, flow, payload)) == run and len(remote[1]) == count
    payload['idempotencyKey'] = uuid4().hex
    assert submit(client, administrator, flow, payload).status_code == 422


def test_fixed_single_sample_remains_compatible(client, administrator, dynamic_detector):
    from test_quality_images import dynamic_flow
    remote, _ = dynamic_detector
    flow, _ = dynamic_flow(client, administrator, remote)
    image = result(upload(client, administrator), 201)
    run = result(execute(client, administrator, flow, {'data': [{'sampleId':'LOT-1', 'imageFileId': image['id']}]}))
    assert run['status'] == 'completed' and run['result']['nodes']['evaluate']['output']['qualityInspectionId']


def test_quality_run_contracts_are_documented(client, administrator):
    spec = client.app.openapi()
    operations = [value for path, methods in spec['paths'].items() if path.startswith(API + '/workflows') for method, value in methods.items() if method in {'get','post'}]
    assert len(operations) == 4 and all(item.get('description') and item.get('summary') for item in operations)
    assert len({item['operationId'] for item in operations}) == 4


@pytest.mark.parametrize('change', ['fixed-sample', 'nested-path', 'reserved-path', 'missing-metric'])
def test_unsupported_or_incomplete_plans_are_explained_before_detection(client, administrator, dynamic_detector, change):
    remote, _ = dynamic_detector
    flow, _, assets = prepared(client, administrator, remote)
    changed = deepcopy(flow['graph'])
    if change == 'fixed-sample':
        changed['nodes'][2]['config'].update(sampleMode='fixed', sampleId='one')
    elif change == 'nested-path':
        changed['nodes'][1]['inputs']['input']['path'] = '$.nested.samples'
    elif change == 'reserved-path':
        changed['nodes'][1]['inputs']['input']['path'] = '$.__qualityForm'
    else:
        additional, _ = metric(client, administrator)
        standard, _ = template(client, administrator, assets[0][2], additional)
        changed['nodes'][2]['config']['templateId'] = standard['id']
    edited = result(client.put('/api/v1/workflows/' + flow['id'], headers=administrator,
        json={'version': flow['version'], 'name': flow['name'], 'domains': flow['domains'], 'graph': changed}))
    result(client.post('/api/v1/workflows/' + flow['id'] + '/publish', headers=administrator, json={'version': edited['version']}))
    response = client.get(API + '/workflows/' + flow['id'] + '/run-plan', headers=administrator)
    assert response.status_code == 422 and response.json()['message']
    assert not remote[1]


def test_generic_batch_refuses_silently_omitted_detector_samples(client, administrator, dynamic_detector, db):
    remote, _ = dynamic_detector
    flow, payload, _ = prepared(client, administrator, remote)
    changed = deepcopy(flow['graph'])
    changed['nodes'].insert(2, node('filter', 'filter', {'expression': {'op':'eq','args':[{'field':'sampleId'}, {'value':'one'}]}}))
    changed['edges'] = [item for item in changed['edges'] if item['source'] != 'detect0']
    changed['edges'].extend([edge('detect0','filter'), edge('filter','evaluate0')])
    edited = result(client.put('/api/v1/workflows/' + flow['id'], headers=administrator,
        json={'version': flow['version'], 'name': flow['name'], 'domains': flow['domains'], 'graph': changed}))
    run = result(execute(client, administrator, edited, {'samples0': payload['groups'][0]['samples']}), 502)
    assert run['status'] == 'failed' and '遗漏' in run['errorMessage']
    assert len(remote[1]) == 2 and not db.scalars(select(QualityInspection)).all()
