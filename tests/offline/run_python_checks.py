"""Run this directory's Python checks with DB/network access forbidden.

Usage from the repository root:
    .venv_py312/Scripts/python.exe -X utf8 tests/offline/run_python_checks.py
No project pytest startup fixtures or third-party auto-loaded plugins run.

中文说明：tests/offline 的统一入口：在禁止数据库与网络访问的前提下运行本目录的检查。
运行方式：仓库根目录执行 python -X utf8 tests/offline/run_python_checks.py；
它不会加载项目的 pytest 启动夹具，也不自动加载第三方插件（保证离线语义）。
影响范围：只读运行检查并输出结果，不写业务数据；需要联网或连库的验收属于其它 gate。
"""
from pathlib import Path
import os
import socket
import sys


def forbidden(*_args, **_kwargs):
    raise AssertionError("Offline checks cannot open database or network connections")


if __name__ == "__main__":
    directory = Path(__file__).resolve().parent
    os.chdir(directory.parents[1])
    sys.path.insert(0, str(directory.parents[1]))
    sys.path.insert(0, str(directory.parents[1] / "backend"))
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.socket.sendto = forbidden
    socket.create_connection = forbidden
    socket.getaddrinfo = forbidden

    import sqlalchemy.engine
    sqlalchemy.engine.Engine.connect = forbidden
    import pytest
    sys.exit(pytest.main(["-q", "--confcutdir=" + str(directory), str(directory)]))
