"""验证 /analytics 静态资源挂载只暴露其自身公共根目录。

验证行为：静态挂载解析到正确的公共根，且越界路径（跳出该根的相对路径）被拒绝。
隔离方式：只在 tmp_path 下造目录 + monkeypatch，配 FastAPI TestClient，不启动完整应用、不连库。
边界：目录穿越（../）请求必须失败；正常前缀请求必须 200。
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import static_router


def test_analytics_mount_resolves_its_public_root_and_rejects_escape(tmp_path, monkeypatch):
    assets = tmp_path / 'assets'
    analytics = assets / 'analytics'
    analytics.mkdir(parents=True)
    (analytics / 'bundle.js').write_text('export const available = true', encoding='utf-8')
    (tmp_path / 'private.txt').write_text('private', encoding='utf-8')
    monkeypatch.setattr(static_router, 'ASSETS_DIR', assets)
    monkeypatch.setattr(static_router, 'JIMU_ASSETS_DIR', analytics / 'jimu-screen')
    app = FastAPI()
    static_router.mount_static(app)
    route = next(route for route in app.routes if route.name == 'analytics-assets')
    assert Path(route.app.directory) == analytics.resolve()
    with TestClient(app) as client:
        assert client.get('/assets/analytics/bundle.js').status_code == 200
        assert client.get('/assets/analytics/%2e%2e/%2e%2e/private.txt').status_code == 404
        assert client.get('/assets/analytics/missing.js').status_code == 404
