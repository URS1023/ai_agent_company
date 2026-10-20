"""健康探针与系统页面刷新兼容；不启动lifespan，不连接任何数据库。"""
from fastapi.testclient import TestClient
import pytest

from agent_platform import main
from agent_platform.core.settings import Settings


@pytest.mark.parametrize('accept', ['*/*', 'application/json', 'text/html;q=0', 'text/html; q=0.0'])
def test_probe_remains_json(tmp_path, monkeypatch, accept):
    index = tmp_path / 'apps/web/dist/index.html'
    index.parent.mkdir(parents=True)
    index.write_text('<div id="app">本机页面</div>', encoding='utf-8')
    monkeypatch.setattr(main, 'PROJECT_ROOT', tmp_path)
    client = TestClient(main.create_app(Settings(data_dir=tmp_path / 'not-opened')))
    response = client.get('/health', headers={'Accept': accept})
    assert response.status_code == 200 and response.json()['status'] == 'ok'
    assert response.headers['vary'] == 'Accept'
    assert not (tmp_path / 'not-opened').exists()
    client.close()


def test_browser_refresh_gets_portal_without_database(tmp_path, monkeypatch):
    index = tmp_path / 'apps/web/dist/index.html'
    index.parent.mkdir(parents=True)
    index.write_text('<div id="app">本机页面</div>', encoding='utf-8')
    monkeypatch.setattr(main, 'PROJECT_ROOT', tmp_path)
    client = TestClient(main.create_app(Settings(data_dir=tmp_path / 'not-opened')))
    response = client.get('/health', headers={'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'})
    assert response.status_code == 200 and 'text/html' in response.headers['content-type']
    assert '本机页面' in response.text and response.headers['vary'] == 'Accept'
    assert not (tmp_path / 'not-opened').exists()
    client.close()
