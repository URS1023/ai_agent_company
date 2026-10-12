"""参考模板绑定、裁片安全与空数据边界，均在隔离内存数据库中运行。"""
from copy import deepcopy

import pytest

from app.errors import ValidationError
from app.modules.reports.content import hifi_templates, validate_content
from app.modules.reports.template_lock import clear_template_runtime, visual_fingerprint


def test_reference_data_edit_and_clear_preserve_visual_contract():
    """示例开关和取值属于运行期数据，不得导致视觉指纹变化。"""
    for template in hifi_templates():
        content = deepcopy(template["content"])
        for widget in content["widgets"]:
            if widget["referenceSkin"]["kind"] == "backdrop":
                continue
            widget["config"].update(referenceSample=False, dataStatus="local", chartDataParsed=[{"value": 765.43}],
                                    dataMapping={"valueField": "value"}, resultColumns=["value"])
        assert visual_fingerprint(content) == visual_fingerprint(template["content"])
        cleared = clear_template_runtime(content)
        assert visual_fingerprint(cleared) == visual_fingerprint(content)
        for widget in cleared["widgets"]:
            assert "referenceSample" not in widget["config"]
            assert widget["config"]["chartDataParsed"] == []


@pytest.mark.parametrize("change", [
    {"asset": "javascript:alert(1)"}, {"sourceWidth": float("nan")},
    {"rect": [-1, 2, 10, 10]}, {"rect": [0, 0, 99999, 10]}, {"holes": "错误类型"},
])
def test_reference_skin_rejects_invalid_asset_and_regions(change):
    """皮肤地址和裁片几何必须在保存前校验，不能交给浏览器自行处理。"""
    content = deepcopy(hifi_templates()[0]["content"])
    content["widgets"][0]["referenceSkin"].update(change)
    with pytest.raises(ValidationError):
        validate_content(content)


def test_reference_report_create_edit_and_layout_boundary(client, administrator, monkeypatch):
    """真实保存接口接收数据修改并拒绝越过原图布局边界；不调用取数接口。"""
    template = hifi_templates()[1]
    created = client.post('/api/v1/reports', headers=administrator, json={
        'reportName': '参考数据编辑验收', 'templateMode': 'manual', 'templateId': template['id'],
    })
    assert created.status_code == 201, created.json().get('message')
    report = created.json()['data']
    content = report['content']
    if isinstance(content, str):
        import json
        content = json.loads(content)
    metric = next(widget for widget in content['widgets'] if widget['componentName'] == '本月计划完成率')
    metric['config'].update(referenceSample=False, dataStatus='local', chartData='[{"value":87.65,"unit":"%"}]',
                            chartDataParsed=[{'value': 87.65, 'unit': '%'}])
    saved = client.put(f"/api/v1/reports/{report['id']}", headers=administrator,
                       json={'reportName': report['reportName'], 'content': content, 'version': report['version']})
    assert saved.status_code == 200, saved.text
    actual = client.get(f"/api/v1/reports/{report['id']}", headers=administrator).json()['data']
    assert visual_fingerprint(content) == visual_fingerprint(template['content'])
    metric['x'] += 1
    rejected = client.put(f"/api/v1/reports/{report['id']}", headers=administrator,
                          json={'reportName': report['reportName'], 'content': content, 'version': actual['version']})
    assert rejected.status_code == 422, rejected.text
    bypassed = client.put(f"/api/v1/reports/{report['id']}", headers=administrator,
                         json={'reportName': report['reportName'], 'content': content, 'version': actual['version'], 'editTemplateLayout': True})
    assert bypassed.status_code == 422, bypassed.text
    metric['x'] -= 1
    from app.modules.reports import templates as template_service
    original_get = template_service.get_template

    def upgraded_template(*args, **kwargs):
        """模拟目录升级，旧报表仍应能保存自己的数据且保持旧布局。"""
        result = deepcopy(original_get(*args, **kwargs))
        result['version'] += 1
        result['content']['widgets'][1]['x'] += 9
        return result

    monkeypatch.setattr(template_service, 'get_template', upgraded_template)
    preserved = client.put(f"/api/v1/reports/{report['id']}", headers=administrator,
                           json={'reportName': report['reportName'], 'content': content, 'version': actual['version']})
    assert preserved.status_code == 200, preserved.text
    assert preserved.json()['data']['lineage']['templateVersion'] == template['version']
