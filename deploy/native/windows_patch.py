"""只在隔离副本修复上游Windows路径；先核验原加密产物，再保留完整校验。"""

import hashlib
import json
from pathlib import Path


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_files(project: Path, relative: str) -> list[str]:
    result = []
    for child in (project / relative).iterdir():
        if child.name == 'target' or child.name.startswith('.'):
            continue
        path = child.relative_to(project).as_posix()
        result.extend(source_files(project, path) if child.is_dir() else [path])
    return sorted(result)


def fingerprint(project: Path) -> str:
    paths = sorted(source_files(project, 'crypto/encryption') +
        ['scripts/encryption/build.py', 'scripts/encryption/artifact.mjs'])
    return digest('\n'.join(path + '\0' + digest((project / path).read_bytes()) for path in paths).encode())


def apply_crypto_path_fix(project: Path) -> dict:
    """修复URL.pathname的Windows盘符解析；不重建/替换WASM，也不跳过任何产物校验。"""
    script = project / 'scripts/encryption/artifact.mjs'
    manifest_path = project / 'src/lib/encryption/vendor/manifest.json'
    manifest = json.loads(manifest_path.read_text('utf-8'))
    if fingerprint(project) != manifest['sourceFingerprint']:
        raise ValueError('原加密源码指纹不匹配；拒绝修改和构建')
    actual = source_files(project, 'src/lib/encryption/vendor')
    actual = [str(Path(path).relative_to('src/lib/encryption/vendor').as_posix()) for path in actual if not path.endswith('/manifest.json')]
    if sorted(actual) != sorted(manifest['checksums']):
        raise ValueError('原加密产物文件集合不匹配')
    for relative, expected in manifest['checksums'].items():
        path = (manifest_path.parent / relative).resolve()
        if not path.is_relative_to(manifest_path.parent.resolve()) or digest(path.read_bytes()) != expected:
            raise ValueError('原加密产物SHA256不匹配')
    content = script.read_text('utf-8')
    before = digest(script.read_bytes())
    original = "const root = new URL('../../', import.meta.url).pathname;"
    corrected = "const root = fileURLToPath(new URL('../../', import.meta.url));"
    if original not in content:
        if corrected in content and 'windowsPortability' in manifest and 'posix.join(dir, e.name)' in content:
            return manifest['windowsPortability']
        if corrected not in content or 'windowsPortability' not in manifest:
            raise ValueError('原加密校验脚本已变化，需人工重新核查')
    else:
        content = content.replace("import { join, relative } from 'node:path';", "import { join, relative } from 'node:path';\nimport { fileURLToPath } from 'node:url';")
        content = content.replace(original, corrected)
    # 指纹输入中的逻辑相对路径沿用发布产物的POSIX格式，磁盘读取仍由原生join处理。
    content = content.replace("import { join, relative } from 'node:path';", "import { join, posix } from 'node:path';")
    content = content.replace('const path = join(dir, e.name);', 'const path = posix.join(dir, e.name);')
    content = content.replace('relative(artifact, p)', 'posix.relative(artifact, p)')
    script.write_text(content, 'utf-8', newline='\n')
    receipt = {'file': 'scripts/encryption/artifact.mjs', 'before': manifest.get('windowsPortability', {}).get('before', before), 'after': digest(script.read_bytes()),
        'reason': '使用Node官方fileURLToPath处理Windows盘符，保留加密产物全部原校验和'}
    manifest['sourceFingerprint'] = fingerprint(project)
    manifest['windowsPortability'] = receipt
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    return receipt
