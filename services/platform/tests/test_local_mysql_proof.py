"""仅使用文件与进程替身验证本机边界，不连接已有数据库。"""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from agent_platform.core import local_mysql
from agent_platform.core.errors import DomainError
from agent_platform.core.settings import Settings
from agent_platform.modules.data.connectors import require_approved_target


@pytest.fixture
def proof(tmp_path, monkeypatch):
    pid = tmp_path / 'local.pid'
    pid.write_text('42')
    server = {'hostname': 'demo-host', 'port': 3306, 'server_uuid': 'demo-instance',
              'data_directory': str(tmp_path), 'pid_file': str(pid)}
    path = tmp_path / 'proof.json'
    path.write_text(json.dumps({'status': 'passed', 'server': server}))
    monkeypatch.setattr(local_mysql.socket, 'gethostname', lambda: 'demo-host')
    process = SimpleNamespace(name=lambda: 'mysqld.exe', parent=lambda: SimpleNamespace(name=lambda: 'mysqld.exe'))
    monkeypatch.setattr(local_mysql.psutil, 'Process', lambda _: process)
    monkeypatch.setattr(local_mysql.psutil, 'net_connections', lambda **_: [
        SimpleNamespace(status='LISTEN', pid=42, laddr=SimpleNamespace(port=3306))])
    return path, server


def source(host='127.0.0.1', database='szh_new'):
    target = {'host': host, 'port': 3306, 'database': database}
    return SimpleNamespace(provider='mysql', connection_config=target, target_approval={**target,
        'scope': ['query'], 'service_owner': '本机测试替身',
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})


def test_verified_local_only_does_not_enable_remote(proof, tmp_path):
    settings = Settings(data_dir=tmp_path, local_mysql_proof=str(proof[0]))
    assert require_approved_target(source(), settings)['database'] == 'szh_new'
    assert require_approved_target(source(database='szh_new_food'), settings)['database'] == 'szh_new_food'
    for candidate in (source(host='192.0.2.1'), source(database='other'), source(host='localhost')):
        with pytest.raises(DomainError, match='当前环境'):
            require_approved_target(candidate, settings)


def test_local_proof_does_not_replace_explicit_approval(proof, tmp_path):
    candidate = source()
    candidate.target_approval = {}
    with pytest.raises(DomainError) as error:
        require_approved_target(candidate, Settings(data_dir=tmp_path, local_mysql_proof=str(proof[0])))
    assert error.value.code == 'source_target_not_approved'


def test_changed_port_owner_is_rejected(proof, monkeypatch):
    monkeypatch.setattr(local_mysql.psutil, 'net_connections', lambda **_: [
        SimpleNamespace(status='LISTEN', pid=99, laddr=SimpleNamespace(port=3306))])
    with pytest.raises(DomainError) as error:
        local_mysql.verified_local_mysql(str(proof[0]), 'mysql', source().connection_config)
    assert error.value.code == 'local_database_unverified'


def test_changed_server_identity_is_rejected(proof):
    calls = []
    cursor = SimpleNamespace(execute=calls.append, fetchone=lambda: ('different', proof[1]['data_directory'], 3306))
    with pytest.raises(DomainError) as error:
        local_mysql.verify_connected_mysql(cursor, proof[1])
    assert error.value.code == 'local_database_changed'
    assert len(calls) == 1
