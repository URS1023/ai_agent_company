"""官方Next制品安全提取回归；只构造很小的临时tar，不请求网络。"""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[3]


def fixture(tmp_path, monkeypatch, unsafe=None):
    spec = importlib.util.spec_from_file_location('wsl_oci_test', ROOT / 'deploy/native/wsl_oci.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    layers = []
    for role, entries in [('public', [('app/targets/next/web/public/icon.svg', b'<svg/>')]),
            ('standalone', [('app/targets/next/web/server.js', b'// native server'),
                ('app/targets/next/web/.next/BUILD_ID', b'test-build'),
                ('app/targets/next/node_modules/@img/sharp-linuxmusl-x64/package.json',
                    b'{"name":"@img/sharp-linuxmusl-x64","version":"0.34.5"}')]),
            ('static', [('app/targets/next/web/.next/static/main.js', b'// static')])]:
        path = tmp_path / (role + '.tar.gz')
        with tarfile.open(path, 'w:gz') as handle:
            for name, content in entries:
                item = tarfile.TarInfo(name)
                item.size = len(content)
                handle.addfile(item, io.BytesIO(content))
            if unsafe and role == 'static':
                item = tarfile.TarInfo(unsafe['name'])
                item.type = unsafe.get('type', tarfile.REGTYPE)
                item.linkname = unsafe.get('linkname', '')
                handle.addfile(item, io.BytesIO(b''))
        layers.append({'role': role, 'size': path.stat().st_size, 'digest': 'sha256:' + module.sha(path)})
    monkeypatch.setattr(module, 'LAYERS', layers)
    (tmp_path / 'fetch.json').write_text(json.dumps({'status': 'ready', 'manifest_digest': module.MANIFEST,
        'revision': module.REVISION, 'source_equivalence_verified': False}))
    return module


def test_valid_three_layers_keep_provenance_and_record_gnu_requirement(tmp_path, monkeypatch):
    module = fixture(tmp_path, monkeypatch)
    target = tmp_path / 'web'
    result = module.extract(tmp_path, target)
    assert (target / 'web/server.js').read_text() == '// native server'
    assert result['gnu_packages_required'] == {'@img/sharp-linux-x64': '0.34.5'}
    assert result['source_equivalence_verified'] is False
    assert result['frontend_available'] is None and result['extracted']


@pytest.mark.parametrize('member', [{'name': '../outside'}, {'name': '/app/targets/next/escape'},
    {'name': 'app/targets/next/.wh.static'}, {'name': 'other/standalone'},
    {'name': 'app/targets/next/socket', 'type': tarfile.FIFOTYPE},
    {'name': 'app/targets/next/link', 'type': tarfile.SYMTYPE, 'linkname': '/etc/passwd'},
    {'name': 'app/targets/next/hardlink', 'type': tarfile.LNKTYPE, 'linkname': 'etc/passwd'}])
def test_unsafe_layer_never_extracts_outside_target(tmp_path, monkeypatch, member):
    module = fixture(tmp_path, monkeypatch, member)
    with pytest.raises((RuntimeError, tarfile.FilterError)):
        module.extract(tmp_path, tmp_path / 'web')
    assert not (tmp_path / 'outside').exists()


def test_changed_layer_refused_before_target_creation(tmp_path, monkeypatch):
    module = fixture(tmp_path, monkeypatch)
    with (tmp_path / 'static.tar.gz').open('ab') as output:
        output.write(b'tampered')
    with pytest.raises(RuntimeError, match='frontend_layer_changed_before_extract'):
        module.extract(tmp_path, tmp_path / 'web')
    assert not (tmp_path / 'web').exists()


def test_existing_target_is_not_overwritten(tmp_path, monkeypatch):
    module = fixture(tmp_path, monkeypatch)
    target = tmp_path / 'web'
    target.mkdir()
    (target / 'original').write_text('keep')
    with pytest.raises(RuntimeError, match='frontend_target_must_be_new'):
        module.extract(tmp_path, target)
    assert (target / 'original').read_text() == 'keep'
