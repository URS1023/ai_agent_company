"""Original-image integrity, scoped preview and actual dynamic HTTP detector requests."""
from base64 import b64decode
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from threading import Thread

from PIL import Image
import pytest
from sqlalchemy import select

from app.config import settings
from app.common.storage import get_storage
from app.models import File
from app.modules.quality.models import QualityDetection, QualityInspection
from app.modules.system.models import Role
from app.modules.system.permissions import MEMBER_PERMISSIONS
from test_alerts import equipment
from test_collection import create_source, collect
from test_quality import API, metric, template, inspection
from test_system_foundation import create_role, create_user, activate_user
from test_workflows import result, graph, node, create_flow, execute


@pytest.fixture(autouse=True)
def isolated_images(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "quality-images"))


def picture_bytes(format="PNG", orientation=1):
    buffer = BytesIO()
    picture = Image.new("RGB", (120, 80), (30, 80, 120))
    if format == "JPEG":
        exif = Image.Exif()
        exif[274] = orientation
        picture.save(buffer, format=format, exif=exif)
    else:
        picture.save(buffer, format=format)
    return buffer.getvalue()


def upload(client, headers, data=None):
    return client.post(API + "/images", headers=headers, files={"file": ("原始样本.png", data or picture_bytes(), "application/octet-stream")})


def appearance_report(client, headers, image):
    m, _ = metric(client, headers, category="appearance", standardMin=None, standardMax=None)
    t, _ = template(client, headers, m)
    d = equipment(client, headers)
    observation = {"metricId": m["id"], "imageFileId": image["id"], "defects": [{"defectType": "划痕", "confidence": "0.95", "severity": "moderate", "bbox": [0.1, 0.2, 0.3, 0.2]}]}
    response, _ = inspection(client, headers, d, t, [observation])
    return result(response, 201), observation, d, t


@pytest.mark.parametrize("format,orientation,width,height", [("PNG", 1, 120, 80), ("JPEG", 6, 80, 120), ("WEBP", 1, 120, 80)])
def test_original_bytes_sniffed_and_orientation_is_explicit(client, administrator, format, orientation, width, height):
    raw = picture_bytes(format, orientation)
    image = result(upload(client, administrator, raw), 201)
    assert (image["width"], image["height"], image["orientation"]) == (width, height, orientation)
    assert image["sha256"] == sha256(raw).hexdigest()
    response = client.get(API + f'/images/{image["id"]}/content', headers=administrator)
    assert response.status_code == 200 and response.content == raw
    assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"].startswith("image/")
    token = administrator["Authorization"].split(" ")[1]
    assert client.get(API + f'/images/{image["id"]}/content?token={token}').status_code == 401


@pytest.mark.parametrize("data", [b'<svg><script>alert(1)</script></svg>', b'not an image', b'x' * (5 * 1024 * 1024 + 1)], ids=['svg', 'invalid', 'over-limit'])
def test_invalid_or_oversized_upload_never_creates_a_file(client, administrator, db, data):
    assert upload(client, administrator, data).status_code == 422
    assert not db.scalars(select(File)).all()


def test_report_pins_image_and_keeps_soft_deleted_original_without_network(client, administrator, db):
    raw = picture_bytes()
    image = result(upload(client, administrator, raw), 201)
    report, _, device, selected = appearance_report(client, administrator, image)
    assert report["outcome"] == "failed" and report["results"][0]["provenance"]["image"] == image
    path = API + f'/inspections/{report["id"]}/images/{image["id"]}'
    assert client.get(path, headers=administrator).content == raw
    other = result(upload(client, administrator), 201)
    assert client.get(API + f'/inspections/{report["id"]}/images/{other["id"]}', headers=administrator).status_code == 404
    assert client.delete('/api/files/' + image['id'], headers=administrator).status_code == 200
    assert client.get(API + f'/images/{image["id"]}/content', headers=administrator).status_code == 404
    assert client.get(path, headers=administrator).content == raw
    exported = client.get(API + f'/inspections/{report["id"]}/export', headers=administrator)
    assert exported.status_code == 200 and 'data:image/png;base64,' in exported.text and image['sha256'] in exported.text
    assert 'left:10.0%' in exported.text
    row = db.get(File, image['id'])
    get_storage().put_bytes(row.storage_key, b'replaced externally')
    assert client.get(path, headers=administrator).status_code == 409
    # Original judgments stay available for tracing; replacement is never displayed.
    assert result(client.get(API + f'/inspections/{report["id"]}', headers=administrator))['outcome'] == 'failed'


def test_file_scope_and_revocation_apply_through_quality_report(client, administrator, db):
    image = result(upload(client, administrator), 201)
    report, _, _, _ = appearance_report(client, administrator, image)
    role = create_role(client, administrator, code='quality_reader', permissions=['quality.view', 'devices.view', 'files.view'], scope='workspace')
    create_user(client, administrator, [role['id']])
    reader = activate_user(client)
    path = API + f'/inspections/{report["id"]}'
    assert client.get(path, headers=reader).status_code == 200
    assert client.get(path + '/images/' + image['id'], headers=reader).status_code == 200
    persisted = db.get(Role, role['id'])
    persisted.permissions = ['quality.view', 'devices.view']
    db.commit()
    assert client.get(path, headers=reader).status_code == 403
    assert client.get(API + '/images/' + image['id'] + '/content', headers=reader).status_code == 403
    assert upload(client, reader).status_code == 403


def test_narrow_file_scope_cannot_be_widened_by_quality_workspace_scope(client, administrator):
    image = result(upload(client, administrator), 201)
    broad = create_role(client, administrator, code='quality_workspace', permissions=['quality.view', 'quality.create', 'devices.view'], scope='workspace')
    own = create_role(client, administrator, code='own_files', permissions=['files.view', 'files.create'], scope='self')
    create_user(client, administrator, [broad['id'], own['id']])
    reader = activate_user(client)
    assert client.get(API + '/images/' + image['id'], headers=reader).status_code == 404
    mine = result(upload(client, reader), 201)
    assert client.get(API + '/images/' + mine['id'], headers=reader).status_code == 200


def test_image_api_collection_includes_real_multipart_upload_field(client, administrator):
    operation = client.app.openapi()['paths'][API + '/images/{file_id}/content']['get']
    assert set(operation['responses']['200']['content']) == {'image/png', 'image/jpeg', 'image/webp'}
    collection = client.get('/api/v1/platform/api-collection', headers=administrator).json()
    group = next(item for item in collection['item'] if item['name'] == '质量检查与追溯')
    spec = client.app.openapi()
    documented_operations = [operation for path, methods in spec['paths'].items()
                             if path.startswith('/api/v1/quality') for method, operation in methods.items()
                             if method in {'get', 'post', 'put', 'patch', 'delete'}]
    assert len(group['item']) == len(documented_operations) == 34
    request = next(item['request'] for item in group['item'] if item['request']['method'] == 'POST' and item['request']['url'].endswith('/quality/images'))
    assert request['body']['mode'] == 'formdata'
    assert request['body']['formdata'][0]['key'] == 'file' and request['body']['formdata'][0]['type'] == 'file'
    assert not any(header['key'].lower() == 'content-type' for header in request['header'])
    assert administrator['Authorization'].split(' ')[1] not in json.dumps(collection)


@pytest.fixture
def dynamic_detector():
    calls, state = [], {'mode': 'normal'}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            sample = body['sample'] if state['mode'] != 'wrong' else 'wrong-sample'
            if state['mode'] == 'swap':
                sample = 'two' if sample == 'one' else 'one'
            item = {'sampleId': sample, 'defects': [{'defectType': 'scratch', 'confidence': '0.99', 'severity': 'severe', 'bbox': [0.1, 0.2, 0.3, 0.2]}],
                    'components': [{'componentName': 'C', 'measuredValue': '0.15', 'unit': '%'}], 'echo': body.get('image', '')}
            items = [] if state['mode'] == 'missing' else [item, item] if state['mode'] == 'duplicate' else [item]
            raw = json.dumps({'items': items}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield (f'http://127.0.0.1:{server.server_port}', calls), state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def dynamic_flow(client, headers, remote, category='appearance', evaluate=True):
    body = {'sample': '${input.sampleId}'}
    if category == 'appearance':
        body.update(image='${input.imageBase64}', sha='${input.imageSha256}', mime='${input.imageMimeType}')
    source, _ = create_source(client, headers, remote, method='POST', bodies=[body], allowAutomatic=True)
    device = equipment(client, headers, datasourceId=source['id'])
    m, _ = metric(client, headers, **({'category': 'appearance', 'standardMin': None, 'standardMax': None} if category == 'appearance' else {}))
    selected, _ = template(client, headers, m)
    config = {'metricId': m['id'], 'metricVersion': 1, 'deviceId': device['id'], 'datasourceId': source['id'], 'datasourceVersion': 1, 'requestMode': 'input'}
    middle = [node('detect', 'qc-' + category, config)]
    if evaluate:
        middle.append(node('evaluate', 'qc-evaluate', {'deviceId': device['id'], 'templateId': selected['id'], 'templateRevision': 1, 'sampleId': 'LOT-1'}))
    return create_flow(client, headers, graph(*middle)), source


def test_dynamic_actual_image_request_and_persisted_report_no_replay(client, administrator, dynamic_detector):
    remote, _ = dynamic_detector
    image = result(upload(client, administrator), 201)
    flow, source = dynamic_flow(client, administrator, remote)
    assert collect(client, administrator, source, confirmed=True).status_code == 422
    assert not remote[1]
    inputs = {'data': [{'sampleId': 'LOT-1', 'imageFileId': image['id']}]}
    run = result(execute(client, administrator, flow, inputs, idempotencyKey='image-test-fixed-key'))
    assert run['status'] == 'completed', run
    assert len(remote[1]) == 1
    assert b64decode(remote[1][0]['image']) == picture_bytes() and remote[1][0]['sha'] == image['sha256']
    report_id = run['result']['outputs']['end']['qualityInspectionId']
    report = result(client.get(API + '/inspections/' + report_id, headers=administrator))
    assert report['results'][0]['provenance']['image']['sha256'] == image['sha256']
    assert report['results'][0]['observation']['imageFileId'] == image['id']
    assert report['results'][0]['provenance']['mode'] == 'detector'
    assert 'imageBase64' not in json.dumps(run)
    query_id = report['results'][0]['provenance']['queryId']
    historical = client.get('/api/v1/collection/history/' + query_id, headers=administrator)
    assert historical.status_code == 200, historical.text
    assert remote[1][0]['image'] not in historical.text and '[REDACTED]' in historical.text
    again = result(execute(client, administrator, flow, inputs, idempotencyKey='image-test-fixed-key'))
    assert again['id'] == run['id'] and len(remote[1]) == 1
    assert client.get(API + f'/inspections/{report_id}/images/{image["id"]}', headers=administrator).status_code == 200
    assert len(remote[1]) == 1


@pytest.mark.parametrize('mode', ['wrong', 'missing', 'duplicate'])
def test_dynamic_wrong_missing_duplicate_samples_never_create_evidence(client, administrator, dynamic_detector, db, mode):
    remote, state = dynamic_detector
    state['mode'] = mode
    flow, _ = dynamic_flow(client, administrator, remote, category='component')
    run = result(execute(client, administrator, flow, {'data': [{'sampleId': 'LOT-1'}]}), 502)
    assert run['status'] == 'failed' and len(remote[1]) == 1
    assert not db.scalars(select(QualityDetection)).all() and not db.scalars(select(QualityInspection)).all()


def test_entire_dynamic_batch_checks_file_authorization_before_first_request(client, administrator, dynamic_detector):
    remote, _ = dynamic_detector
    image = result(upload(client, administrator), 201)
    flow, _ = dynamic_flow(client, administrator, remote, evaluate=False)
    run = result(execute(client, administrator, flow, {'data': [{'sampleId': 'one', 'imageFileId': image['id']}, {'sampleId': 'two', 'imageFileId': 'missing'}]}), 502)
    assert run['status'] == 'failed' and not remote[1]


def test_dynamic_batch_sample_binding_and_no_secret_reference_injection(client, administrator, dynamic_detector):
    remote, _ = dynamic_detector
    flow, _ = dynamic_flow(client, administrator, remote, category='component', evaluate=False)
    run = result(execute(client, administrator, flow, {'data': [{'sampleId': 'one'}, {'sampleId': 'two'}]}))
    assert run['status'] == 'completed' and [item['sample'] for item in remote[1]] == ['one', 'two']
    injected = result(execute(client, administrator, flow, {'data': [{'sampleId': '${secret.access}'}]}), 502)
    assert injected['status'] == 'failed' and len(remote[1]) == 2


def test_dynamic_image_send_requires_export_permission(client, administrator, dynamic_detector):
    remote, _ = dynamic_detector
    image = result(upload(client, administrator), 201)
    flow, _ = dynamic_flow(client, administrator, remote)
    role = create_role(client, administrator, code='quality_no_image_export', permissions=sorted(MEMBER_PERMISSIONS - {'files.export'}), scope='workspace')
    create_user(client, administrator, [role['id']])
    member = activate_user(client)
    run = result(execute(client, member, flow, {'data': [{'sampleId': 'LOT-1', 'imageFileId': image['id']}]}), 502)
    assert run['status'] == 'failed' and not remote[1]


def test_dynamic_batch_cannot_swap_images_between_requested_samples(client, administrator, dynamic_detector, db):
    remote, state = dynamic_detector
    state['mode'] = 'swap'
    flow, _ = dynamic_flow(client, administrator, remote, category='component', evaluate=False)
    run = result(execute(client, administrator, flow, {'data': [{'sampleId': 'one'}, {'sampleId': 'two'}]}), 502)
    assert run['status'] == 'failed' and '实际请求不一致' in run['errorMessage']
    assert len(remote[1]) == 2 and not db.scalars(select(QualityDetection)).all()


@pytest.mark.parametrize('body', [{'sample': 'prefix ${input.sampleId}'}, {'sample': '${input.unknown}'}, {'token': '${input.sampleId}'}])
def test_unsafe_saved_input_templates_rejected(client, administrator, dynamic_detector, body):
    remote, _ = dynamic_detector
    response = client.post('/api/v1/collection/sources', headers=administrator, json={'name': 'invalid', 'url': remote[0], 'allowPrivate': True, 'method': 'POST', 'bodies': [body]})
    assert response.status_code == 422 and not remote[1]


def test_repeated_image_expansion_is_rejected_before_serializing_large_body(monkeypatch):
    from app.errors import ValidationError
    from app.modules.collection import parameters
    from app.modules.collection.schemas import HttpSourceInput
    config = HttpSourceInput(name='bounded template', url='https://example.invalid', method='POST', bodies=[{'images': ['${input.imageBase64}'] * 500}])
    original = parameters.json.dumps
    def guarded(value, *args, **kwargs):
        assert not isinstance(value, dict), 'Do not serialize the whole huge expanded body'
        return original(value, *args, **kwargs)
    monkeypatch.setattr(parameters.json, 'dumps', guarded)
    with pytest.raises(ValidationError, match='最多 8 MB'):
        parameters.bind_parameters(config, [{'imageBase64': 'A' * (5 * 1024 * 1024)}])


def test_dynamic_request_batch_budget_is_checked_before_execution(monkeypatch):
    from app.errors import ValidationError
    from app.modules.collection import parameters
    from app.modules.collection.schemas import HttpSourceInput
    config = HttpSourceInput(name='bounded batch', url='https://example.invalid', method='POST', bodies=[{'sample': '${input.sampleId}'}])
    monkeypatch.setattr(parameters, 'MAX_DYNAMIC_BATCH_BYTES', 100)
    with pytest.raises(ValidationError, match='整批最多'):
        parameters.bind_parameters(config, [{'sampleId': str(index) * 50} for index in range(4)])
