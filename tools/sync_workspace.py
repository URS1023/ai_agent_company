"""把指定工作区镜像到发布目录；不复制依赖、缓存、真实密钥及Git内部数据。"""
from pathlib import Path
from collections import Counter
import argparse
import json
import os
import shutil

parser = argparse.ArgumentParser()
parser.add_argument('workspace')
args = parser.parse_args()
source = Path(args.workspace).resolve()
target = Path(__file__).resolve().parents[1]
if source == target or not target.is_relative_to(source):
    raise SystemExit('发布目录必须是工作区内独立子目录')
skip_dirs = {'.git', 'node_modules', '.venv', '.venv_py312', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.next', '.cache'}
private_names = {'.env', '.env.local', 'id_rsa', 'id_ed25519', 'credentials.json', 'token.json'}
private_suffixes = {'.p12', '.pfx', '.pem', '.key'}
counts = Counter()
excluded = []
large = []
total = 0
for base, dirs, files in os.walk(source):
    base_path = Path(base)
    dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.venv_') and (base_path / d).resolve() != target]
    for name in files:
        src = base_path / name
        rel = src.relative_to(source)
        if name.lower() in private_names or src.suffix.lower() in private_suffixes:
            excluded.append({'path': rel.as_posix(), 'reason': '本地凭据或密钥文件'})
            continue
        if not src.resolve().is_relative_to(source):
            excluded.append({'path': rel.as_posix(), 'reason': '工作区外链接'})
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        size = src.stat().st_size
        counts[rel.parts[0]] += 1
        total += size
        if size > 50 * 1024 * 1024:
            large.append({'path': rel.as_posix(), 'bytes': size})
report = {'scope': '当前工作区项目文件，排除本机依赖、缓存、Git内部目录和真实凭据', 'files': sum(counts.values()), 'bytes': total, 'groups': dict(counts), 'excluded': excluded, 'large_files': large}
(target / 'workspace-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'copied_files': report['files'], 'megabytes': round(total / 1024 / 1024, 1), 'excluded_sensitive_files': len(excluded), 'large_files': large}, ensure_ascii=True))
