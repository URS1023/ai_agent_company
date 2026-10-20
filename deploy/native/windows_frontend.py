"""从固定版本官方 wheel 提取前端，复用已安装的 Windows 后端，不安装依赖。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

import windows
from windows_services import exclusive_operation, require_local_drive

VERSION = '0.12.3'
WHEEL_SHA256 = '6d716039a5c15ecdb1db12f99dd9c79ea5c86d3d7bbd9a67054dd26f1f534747'
WHEEL_SIZE = 105980169
WHEEL_URL = ('https://files.pythonhosted.org/packages/22/05/'
             'a40238c1fab2956e6371729482f7389c6b1fec936198c733b0d84f1d5722/'
             'open_webui-0.12.3-py3-none-any.whl')
PREFIX = 'open_webui/frontend/'


def frontend_entries(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, Path]]:
    """仅允许普通前端文件；拒绝路径穿越、链接、Windows特殊路径和过量解压。"""
    result, seen, total = [], set(), 0
    for entry in archive.infolist():
        if not entry.filename.startswith(PREFIX) or entry.is_dir():
            continue
        name = entry.filename[len(PREFIX):]
        parts = PurePosixPath(name).parts
        if (not parts or name.startswith('/') or '\\' in name or
                any(p in {'.', '..'} or ':' in p or p.endswith((' ', '.')) for p in parts) or
                stat.S_ISLNK(entry.external_attr >> 16)):
            raise ValueError('官方前端制品包含非法路径')
        identity = name.casefold()
        if identity in seen:
            raise ValueError('前端制品路径重复')
        seen.add(identity)
        total += entry.file_size
        if total > 1024 ** 3 or len(result) >= 20000:
            raise ValueError('前端解压规模超出预算')
        result.append((entry, Path(*parts)))
    if 'index.html' not in seen or not any(name.startswith('_app/') for name in seen):
        raise ValueError('制品缺少原生入口或编译资源')
    return result


def install(wheel: Path, root: Path = windows.DEFAULT_ROOT) -> dict:
    """摘要通过后写入新目录，再原子发布；已有界面和失败目录不覆盖。"""
    root = windows.ensure_inside(windows.REPO / '.local', root)
    require_local_drive(root)
    wheel = windows.ensure_inside(root, wheel)
    if wheel.stat().st_size != WHEEL_SIZE or windows.sha(wheel) != WHEEL_SHA256:
        raise ValueError('官方 wheel 大小或摘要不匹配')
    with exclusive_operation(root, 'install'):
        data = windows.load(root)
        source = windows.ensure_inside(root, Path(data['sources']['openwebui']))
        if source != root / 'sources/openwebui':
            raise ValueError('前端只能安装到已登记的本项目源码副本')
        if json.loads((source / 'package.json').read_text('utf-8'))['version'] != VERSION:
            raise ValueError('前后端版本不一致')
        destination = source / 'build'
        staging = source / 'build.wheel-pending'
        if destination.exists() or staging.exists():
            raise ValueError('前端目录已存在，先核对已有产物，禁止覆盖')
        with zipfile.ZipFile(wheel) as archive:
            entries = frontend_entries(archive)
            staging.mkdir()
            for entry, relative in entries:
                target = windows.ensure_inside(staging, staging / relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as incoming, target.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        staging.rename(destination)
        receipt = {'version': VERSION, 'source': WHEEL_URL, 'sha256': WHEEL_SHA256,
                   'files': len(entries), 'bytes': sum(e.file_size for e, _ in entries),
                   'frontend_directory': str(destination), 'installed_at': datetime.now(timezone.utc).isoformat(),
                   'backend_dependencies_changed': False}
        windows.write_json(root / 'openwebui/frontend-artifact.json', receipt)
        return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    args = parser.parse_args()
    print(json.dumps(install(args.wheel), ensure_ascii=False, indent=2))
