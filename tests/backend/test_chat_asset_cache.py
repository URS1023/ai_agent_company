"""聊天组件更新后返回最新资源，缓存策略不影响大屏构建产物。

隔离方式：临时静态目录与独立 FastAPI 应用，不启动业务宿主、不访问数据库或模型。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import static_router


CHAT_ASSETS = ("chat-session.js", "chat-session-view.js", "chat-session.css",
               "chat-dashboard-preview.js", "chat-dashboard-preview.css")


@pytest.fixture
def static_client(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    platform = assets / "platform"
    analytics = assets / "analytics"
    platform.mkdir(parents=True)
    analytics.mkdir(parents=True)
    for name in CHAT_ASSETS:
        (platform / name).write_text("/* 初始组件 */", encoding="utf-8")
    (platform / "shell.js").write_text("/* 其他组件 */", encoding="utf-8")
    (analytics / "bundle.abc123.js").write_text("/* 大屏组件 */", encoding="utf-8")
    (tmp_path / "private.txt").write_text("不可作为静态资源返回", encoding="utf-8")
    monkeypatch.setattr(static_router, "ASSETS_DIR", assets)
    monkeypatch.setattr(static_router, "JIMU_ASSETS_DIR", assets / "missing-jimu")
    app = FastAPI()
    static_router.mount_static(app)
    with TestClient(app) as client:
        yield client, platform


@pytest.mark.parametrize("name", CHAT_ASSETS)
def test_chat_assets_bypass_old_cache_and_return_updated_content(static_client, name):
    client, platform = static_client
    url = "/assets/platform/" + name
    original = client.get(url)
    assert original.status_code == 200
    assert "no-store" in original.headers.get("cache-control", "")
    assert "max-age=0" in original.headers.get("cache-control", "")

    # 同一路径更新后返回新文件；旧 ETag 不得把组件更新误判成未变化。
    (platform / name).write_text("/* 更新后的聊天组件，已移除任务列表 */", encoding="utf-8")
    updated = client.get(url, headers={"If-None-Match": original.headers["etag"]})
    assert updated.status_code == 200
    assert "已移除任务列表" in updated.text
    assert updated.headers["etag"] != original.headers["etag"]
    versioned = client.get(url + "?v=chat-fix-test")
    assert versioned.status_code == 200
    assert versioned.text == updated.text
    assert "no-store" in versioned.headers.get("cache-control", "")


@pytest.mark.parametrize("url", ["/assets/platform/shell.js", "/assets/analytics/bundle.abc123.js"])
def test_other_static_assets_keep_their_existing_cache_behavior(static_client, url):
    client, _ = static_client
    response = client.get(url)
    assert response.status_code == 200
    assert "no-store" not in response.headers.get("cache-control", "")
    assert client.get(url, headers={"If-None-Match": response.headers["etag"]}).status_code == 304


def test_chat_asset_routes_preserve_missing_file_and_path_boundaries(static_client):
    client, platform = static_client
    (platform / "chat-session-view.js").unlink()
    assert client.get("/assets/platform/chat-session-view.js").status_code == 404
    assert client.get("/assets/platform/chat-missing.js").status_code == 404
    assert client.get("/assets/platform/%2e%2e/%2e%2e/%2e%2e/private.txt").status_code == 404
