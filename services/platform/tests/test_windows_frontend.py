"""官方前端提取边界；仅内存ZIP，不下载或启动服务。"""
import importlib.util
import io
from pathlib import Path
import zipfile

import pytest


@pytest.fixture
def extractor(monkeypatch):
    root = Path(__file__).resolve().parents[3] / 'deploy/native'
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location('frontend_under_test', root / 'windows_frontend.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive(names):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as result:
        for name in names:
            result.writestr('open_webui/frontend/' + name, 'fixture')
    stream.seek(0)
    return zipfile.ZipFile(stream)


@pytest.mark.parametrize('path', ['../outside.js', '/outside.js', 'assets/x:stream', '..\\outside.js'])
def test_reject_unsafe_archive_paths(extractor, path):
    with archive(['index.html', '_app/main.js', path]) as packed:
        with pytest.raises(ValueError, match='非法路径'):
            extractor.frontend_entries(packed)


def test_requires_entry_and_compiled_assets(extractor):
    with archive(['index.html']) as packed:
        with pytest.raises(ValueError, match='缺少原生入口'):
            extractor.frontend_entries(packed)


def test_normal_frontend_and_case_collision(extractor):
    with archive(['index.html', '_app/main.js']) as packed:
        assert len(extractor.frontend_entries(packed)) == 2
    with archive(['index.html', '_app/main.js', '_app/MAIN.js']) as packed:
        with pytest.raises(ValueError, match='路径重复'):
            extractor.frontend_entries(packed)
