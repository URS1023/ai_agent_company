"""复用已核验18080归属入口，匿名管道传递账号，单次执行报表来源UI验收。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import subprocess

import psutil

from verify_client_certificates_ui import DATA, REPO, stop_verifier_tree, verified_access


REVIEWED_RECEIPT = DATA / 'report-sources-3c5ac7be324b/acceptance.json'
REVIEWED_SHA256 = '88459df684511bc4e76dfd6eeecfc39024000ea8fde415856128fa571b9016bd'


def reviewed_resume(path: Path, prefix: str) -> dict:
    """仅允许此次已核验的控件顺序错误续作，不重建夹具；原收据只读，独占一次认领。"""
    if path.resolve() != REVIEWED_RECEIPT.resolve() or path.is_symlink():
        raise RuntimeError('report_resume_not_reviewed')
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != REVIEWED_SHA256:
        raise RuntimeError('report_resume_receipt_changed')
    previous = json.loads(raw)
    if (previous.get('passed') is not False or previous.get('api_requests') != 26
            or previous.get('actual_query_upper_bound') != 3 or previous.get('report_refreshes') != 1
            or previous.get('last_step') != 'report preview' or previous.get('api_failures')
            or previous.get('blocked_requests') or previous.get('page_errors')
            or previous.get('network_failure') or previous.get('requires_query_stop_check')
            or previous.get('origin') != 'http://127.0.0.1:18080'):
        raise RuntimeError('report_resume_history_not_eligible')
    claim = path.parent / 'reviewed-ui-resume-claim.json'
    try:
        with claim.open('x', encoding='utf-8') as output:
            json.dump({'receipt_sha256': REVIEWED_SHA256, 'resume_prefix': prefix,
                       'claimed_at': datetime.now(timezone.utc).isoformat()}, output)
    except FileExistsError as exc:
        raise RuntimeError('report_resume_already_claimed') from exc
    return {
        'receipt': str(path.resolve()), 'receipt_sha256': REVIEWED_SHA256,
        'previous_prefix': previous['prefix'], 'previous_platform_pid': previous['platform_pid'],
        'previous_api_requests': previous['api_requests'], 'previous_static_requests': previous['static_requests'],
        'previous_elapsed_seconds': previous['elapsed_seconds'],
        'previous_actual_query_upper_bound': previous['actual_query_upper_bound'],
        'fixture_ids': previous['fixture_ids'],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium', default=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE',
        'C:/Program Files/Google/Chrome/Application/chrome.exe'))
    parser.add_argument('--resume-receipt', type=Path)
    args = parser.parse_args()
    process = None
    born = None
    try:
        access = verified_access()
        browser = Path(args.chromium)
        if not browser.is_absolute() or not browser.is_file() or str(browser).startswith('\\\\'):
            raise RuntimeError('local_chromium_missing')
        if args.resume_receipt is None and REVIEWED_RECEIPT.exists():
            raise RuntimeError('report_resume_required')
        prefix = 'report-sources-' + secrets.token_hex(6)
        payload = {
            'source': 'verified-report-source-helper-v1', 'prefix': prefix,
            'origin': access['origin'], 'username': access['username'], 'password': access['password'],
            'output_directory': str(DATA / prefix),
            'ownership': {'verified': True, 'component': access['component'], 'platform_pid': access['pid'],
                          'data_directory': access['data_directory']},
        }
        if args.resume_receipt is not None:
            payload['resume'] = reviewed_resume(args.resume_receipt, prefix)
        environment = {key: value for key, value in os.environ.items() if key not in {'E2E_USERNAME', 'E2E_PASSWORD'}}
        environment['PLAYWRIGHT_CHROMIUM_EXECUTABLE'] = str(browser)
        process = subprocess.Popen(['node', 'tests/report-source-components.mjs'], cwd=REPO / 'apps/web',
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
            raw = b''
            payload.clear()
            access.clear()
        if len(stdout) > 65536:
            raise RuntimeError('unsafe_verifier_output')
        result = json.loads(stdout)
        safe = {key: result[key] for key in (
            'passed', 'error_code', 'failed_phase', 'api_requests', 'actual_query_upper_bound',
            'elapsed_seconds', 'receipt', 'screenshots', 'checks', 'error', 'first_failure', 'last_step',
            'history', 'cumulative_api_requests', 'cumulative_actual_query_upper_bound', 'cumulative_elapsed_seconds',
        ) if key in result}
        print(json.dumps(safe, ensure_ascii=False))
        return 0 if process.returncode == 0 and safe.get('passed') is True else 1
    except Exception as error:
        if process is not None and born is not None and process.poll() is None:
            stop_verifier_tree(process, born)
        known = {'acceptance_target_mismatch', 'acceptance_ownership_not_verified',
                 'local_chromium_missing', 'unsafe_verifier_output', 'report_resume_not_reviewed',
                 'report_resume_receipt_changed', 'report_resume_history_not_eligible', 'report_resume_already_claimed'}
        known.add('report_resume_required')
        detail = str(error) if str(error) in known else '验收入口或子进程未生成有效收据'
        print(json.dumps({'passed': False, 'error_code': 'report_source_verifier_unavailable',
                          'error_type': type(error).__name__, 'detail': detail}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
