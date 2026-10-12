"""The model plans with a resource reference, never its credential-bearing URL.

中文说明：模型规划时只拿到"资源引用"，绝不包含带凭据的连接地址（避免密钥进入提示/日志）。
隔离方式：SimpleNamespace + monkeypatch，离线运行，不连接数据源。
边界：凭据只能在后端解析，模型上下文中不得出现。
"""
import json
from types import SimpleNamespace


def test_http_catalog_keeps_contract_and_omits_endpoint_credentials(monkeypatch):
    from app.modules.workflows import generation
    source = SimpleNamespace(id="source", name="设备接口", provider="http", status="active", connection_config={
        "url": "https://user:private_password@host.test/private/path?signature=private_signature",
        "description": "设备运行数据", "method": "GET", "recordPath": "$.items",
        "allowAutomatic": True, "fields": [{"name": "设备", "path": "$.name", "comment": "设备名称"}],
    })
    monkeypatch.setattr(generation, "require_permission", lambda *_: None)
    monkeypatch.setattr(generation, "owned_record", lambda *_: source)
    monkeypatch.setattr(generation, "source_version", lambda _: 4)
    catalog = generation._resource_catalog(None, ["source"])
    assert catalog[0]["id"] == "source" and catalog[0]["version"] == 4
    assert catalog[0]["fields"][0]["name"] == "设备"
    assert "url" not in catalog[0]
    text = json.dumps(catalog)
    assert "private_password" not in text and "private_signature" not in text and "private/path" not in text
