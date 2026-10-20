"""核验本轮18080新平台归属，用匿名管道把临时证书和密文账号交给单次浏览器验收。"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import psutil

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / '.local/acceptance/r34-20261021'
ORIGIN = 'http://127.0.0.1:18080'


def new_material(label: str) -> dict:
    """只在内存生成独立EC密钥和客户端叶证书，不写文件，不建立CA服务。"""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .sign(key, hashes.SHA256()))
    return {
        'certificate_pem': certificate.public_bytes(serialization.Encoding.PEM).decode(),
        'private_key_pem': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()).decode(),
    }


def verified_access() -> dict:
    """复用root已有进程/监听/数据目录核验，不启动服务或查询任何数据库。"""
    sys.path.insert(0, str(REPO / 'deploy/native'))
    import windows
    import windows_services
    access = json.loads(Fernet((REPO / '.local/desktop-access.key').read_bytes()).decrypt(
        (DATA / 'access.enc').read_bytes()))
    if (access.get('origin') != ORIGIN or access.get('component') != 'r34-acceptance'
            or Path(access.get('data_directory', '')).resolve() != DATA.resolve()
            or not isinstance(access.get('username'), str) or not access['username']
            or not isinstance(access.get('password'), str) or not access['password']):
        raise RuntimeError('acceptance_target_mismatch')
    state = windows.load(windows.DEFAULT_ROOT)
    process = windows.owned_process(windows.DEFAULT_ROOT, access['component'], state)
    if (process is None or process.pid != access.get('pid')
            or 18080 not in windows_services.listening_processes(process)
            or Path(state['processes'][access['component']]['data_directory']).resolve() != DATA.resolve()):
        raise RuntimeError('acceptance_ownership_not_verified')
    return access


def stop_verifier_tree(process: subprocess.Popen, born: float) -> None:
    """外层到期只回收本次Node及其浏览器后代，核PID出生时间避免触及平台或其他Chrome。"""
    try:
        root = psutil.Process(process.pid)
        if abs(root.create_time() - born) > 0.01:
            return
        owned = [(child, child.create_time()) for child in root.children(recursive=True)] + [(root, born)]
    except psutil.NoSuchProcess:
        return
    for child, created in owned:
        try:
            if abs(child.create_time() - created) <= 0.01:
                child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs([child for child, _ in owned], timeout=2)
    for child in alive:
        try:
            created = next(stamp for entry, stamp in owned if entry.pid == child.pid)
            if abs(child.create_time() - created) <= 0.01:
                child.kill()
        except psutil.NoSuchProcess:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium', default=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE',
        'C:/Program Files/Google/Chrome/Application/chrome.exe'))
    args = parser.parse_args()
    process = None
    born = None
    try:
        access = verified_access()
        browser_exe = Path(args.chromium)
        if not browser_exe.is_absolute() or not browser_exe.is_file() or str(browser_exe).startswith('\\\\'):
            raise RuntimeError('local_chromium_missing')
        prefix = 'cert-ui-' + secrets.token_hex(6)
        payload = {
            'source': 'verified-client-certificate-helper-v1', 'prefix': prefix,
            'origin': access['origin'], 'username': access['username'], 'password': access['password'],
            'ownership': {'verified': True, 'component': access['component'], 'platform_pid': access['pid'],
                          'data_directory': access['data_directory']},
            'materials': [new_material(prefix + '-A'), new_material(prefix + '-B')],
        }
        environment = {key: value for key, value in os.environ.items() if key not in {'E2E_USERNAME', 'E2E_PASSWORD'}}
        environment['PLAYWRIGHT_CHROMIUM_EXECUTABLE'] = str(browser_exe)
        process = subprocess.Popen(['node', 'tests/client-certificates.mjs'], cwd=REPO / 'apps/web',
            env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        born = psutil.Process(process.pid).create_time()
        raw = json.dumps(payload, ensure_ascii=False).encode()
        try:
            stdout, _ = process.communicate(input=raw, timeout=190)
        except subprocess.TimeoutExpired:
            stop_verifier_tree(process, born)
            print(json.dumps({'passed': False, 'error_code': 'verifier_parent_deadline'}, ensure_ascii=False))
            return 1
        finally:
            # 释放明文引用；操作系统进程退出负责回收其内存，不把数据存入恢复文件。
            raw = b''
            payload.clear()
            access.clear()
        if len(stdout) > 65536:
            raise RuntimeError('unsafe_verifier_output')
        result = json.loads(stdout)
        # 不转发Node stderr或任意异常正文，只打印约定的非敏感验收投影。
        safe = {key: result[key] for key in (
            'passed', 'error_code', 'failed_phase', 'api_requests', 'expected_rejections',
            'elapsed_seconds', 'receipt', 'screenshots', 'checks',
        ) if key in result}
        print(json.dumps(safe, ensure_ascii=False))
        return 0 if process.returncode == 0 and safe.get('passed') is True else 1
    except Exception as error:
        if process is not None and born is not None and process.poll() is None:
            stop_verifier_tree(process, born)
        print(json.dumps({'passed': False, 'error_code': 'certificate_verifier_unavailable',
                          'error_type': type(error).__name__}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
