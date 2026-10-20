"""本机演示数据库的实例证明；地址、进程和实例身份同时匹配才开放读取。"""

import json
from pathlib import Path
import socket

import psutil

from .errors import DomainError


def verified_local_mysql(proof_file, provider, target):
    """不连接数据库；检查授权两库、原生mysqld监听及本机pid文件。

    证明文件由部署阶段核实服务后生成，不接收浏览器传入的路径。
    服务更换或端口改为隧道时拒绝，管理员需重新核实归属。
    """
    if not proof_file or provider != 'mysql' or target.get('host') != '127.0.0.1':
        return None
    if target.get('port') != 3306 or target.get('database') not in {'szh_new', 'szh_new_food'}:
        return None
    try:
        proof = json.loads(Path(proof_file).read_text(encoding='utf-8'))
        server = proof['server']
        if proof['status'] != 'passed' or server['hostname'].casefold() != socket.gethostname().casefold():
            raise ValueError('instance_identity')
        directory = Path(server['data_directory']).resolve(strict=True)
        pid_file = Path(server['pid_file']).resolve(strict=True)
        if pid_file.parent != directory or pid_file.is_symlink():
            raise ValueError('pid_path')
        pid = int(pid_file.read_text().strip())
        process = psutil.Process(pid)
        parent = process.parent()
        if process.name().casefold() != 'mysqld.exe' or parent is None or parent.name().casefold() != 'mysqld.exe':
            raise ValueError('native_mysql_process')
        listeners = {c.pid for c in psutil.net_connections(kind='tcp')
                     if c.status == psutil.CONN_LISTEN and c.laddr.port == target['port']}
        if listeners != {pid}:
            raise ValueError('listener_owner')
        return server
    except (OSError, ValueError, KeyError, TypeError, psutil.Error) as exc:
        raise DomainError(403, 'local_database_unverified', '本机数据库归属已变化，请重新核实连接') from exc


def verify_connected_mysql(cursor, proof):
    """同一短连接先核实例身份，随后才允许元数据或业务读取。"""
    cursor.execute('SELECT @@server_uuid, @@datadir, @@port')
    actual = cursor.fetchone()
    if (actual[0] != proof['server_uuid'] or actual[2] != proof['port']
            or Path(actual[1]).resolve() != Path(proof['data_directory']).resolve()):
        raise DomainError(403, 'local_database_changed', '数据库实例与已核验目标不一致')
