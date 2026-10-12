"""Create a temporary XLSX fixture for the real-browser identity test.

中文说明（身份导入夹具生成器，非测试用例）：
用途：按浏览器身份用例的要求生成一份用户导入 XLSX（含表头与一行测试账号），供 tests/ui/identity_administration_workspace.js 上传。
运行方式：python tests/ui/make_identity_fixture.py <输出路径> <登录账号>（由 JS 用例通过 child_process 调用）。
影响范围：只写指定的临时 XLSX 文件，不连数据库、不需要服务在跑；账号口令是固定的测试值，仅用于隔离环境。
"""
from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook


output = Path(sys.argv[1]).resolve()
username = sys.argv[2]
workbook = Workbook()
sheet = workbook.active
sheet.title = "用户导入"
sheet.append([
    "登录账号", "显示姓名", "初始临时密码", "账号状态", "部门编码",
    "岗位编码", "角色编码", "员工编号", "联系邮箱", "联系电话",
])
sheet.append([
    username, "浏览器批量成员", "Temporary12345", "启用", "", "", "",
    "EDGE-001", f"{username}@example.com", "13800000001",
])
output.parent.mkdir(parents=True, exist_ok=True)
workbook.save(output)

