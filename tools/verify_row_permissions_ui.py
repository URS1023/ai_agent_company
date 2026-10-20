"""核验本轮18080本机归属，经匿名管道交给单次行权限UI验收，不启动平台服务。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess

import psutil

from verify_client_certificates_ui import DATA, REPO, stop_verifier_tree, verified_access


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium', default=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE',
        'C:/Program Files/Google/Chrome/Application/chrome.exe'))
    args = parser.parse_args()
    process = None
    born = None
    try:
        access = verified_access()
        browser = Path(args.chromium)
        if not browser.is_absolute() or not browser.is_file() or str(browser).startswith('\\\\'):
            raise ValueError('local_chromium_missing')
        prefix = 'row-permissions-' + secrets.token_hex(6)
        output = DATA / prefix
        output.mkdir(exist_ok=False)
        (output / 'started.json').write_text(json.dumps({'prefix': prefix,
            'started_at': datetime.now(timezone.utc).isoformat(), 'platform_pid': access['pid'],
            'origin': access['origin'], 'maximum_http': 80, 'maximum_queries': 3,
            'maximum_seconds': 180}, ensure_ascii=False), encoding='utf-8')
        payload = {'source': 'verified-row-permissions-helper-v1', 'prefix': prefix,
            'origin': access['origin'], 'username': access['username'], 'password': access['password'],
            'output_directory': str(output), 'ownership': {'verified': True,
                'component': access['component'], 'platform_pid': access['pid'],
                'data_directory': access['data_directory']}}
        environment = {key: value for key, value in os.environ.items() if key not in {'E2E_USERNAME', 'E2E_PASSWORD'}}
        environment['PLAYWRIGHT_CHROMIUM_EXECUTABLE'] = str(browser)
        process = subprocess.Popen(['node', 'tests/row-permissions.mjs'], cwd=REPO / 'apps/web',
            env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        born = psutil.Process(process.pid).create_time()
        raw = json.dumps(payload, ensure_ascii=False).encode()
        try:
            stdout, _ = process.communicate(input=raw, timeout=190)
        except subprocess.TimeoutExpired:
            stop_verifier_tree(process, born)
            print(json.dumps({'passed': False, 'error_code': 'row_verifier_parent_deadline',
                'requires_query_stop_check': True, 'evidence_directory': str(output)}, ensure_ascii=False))
            return 1
        finally:
            raw = b''
            payload.clear()
            access.clear()
        if len(stdout) > 65536:
            raise ValueError('unsafe_verifier_output')
        result = json.loads(stdout)
        safe = {key: result[key] for key in ('passed', 'error_code', 'failed_phase', 'api_requests',
            'actual_query_upper_bound', 'elapsed_seconds', 'receipt', 'screenshots', 'checks',
            'requires_query_stop_check', 'last_step', 'fixture_ids', 'expected_rejections') if key in result}
        print(json.dumps(safe, ensure_ascii=False))
        return 0 if process.returncode == 0 and safe.get('passed') is True else 1
    except Exception as error:
        if process is not None and born is not None and process.poll() is None:
            stop_verifier_tree(process, born)
        print(json.dumps({'passed': False, 'error_code': 'row_verifier_unavailable',
            'error_type': type(error).__name__}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
