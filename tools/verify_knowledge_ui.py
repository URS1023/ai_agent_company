"""核验本轮本机平台归属，将知识夹具与密文账号经匿名管道交给单次UI验收。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

import psutil

from verify_client_certificates_ui import DATA, REPO, stop_verifier_tree, verified_access


def read_fixture(path: Path) -> dict:
    """夹具不含密钥；原生归属和单段/无LLM设置由root先行核验并留下短期证明。"""
    path = path.resolve()
    if not path.is_relative_to(DATA.resolve()) or path.stat().st_size > 16384:
        raise ValueError('knowledge_fixture_outside_acceptance')
    fixture = json.loads(path.read_text(encoding='utf-8-sig'))
    expected = {'schema_version', 'run_id', 'space_id', 'space_name', 'integration_id',
                'native_dataset_id', 'text', 'query', 'expected_phrase', 'native_proof'}
    if set(fixture) != expected or fixture['schema_version'] != 1:
        raise ValueError('knowledge_fixture_schema_invalid')
    if not re.fullmatch(r'knowledge-[a-f0-9]{12}', fixture['run_id']):
        raise ValueError('knowledge_run_id_invalid')
    for key in ('space_id', 'integration_id', 'native_dataset_id'):
        if not isinstance(fixture[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', fixture[key]):
            raise ValueError('knowledge_identifier_invalid')
    text, query, phrase = fixture['text'], fixture['query'], fixture['expected_phrase']
    if (not isinstance(text, str) or not 10 <= len(text) <= 180 or len(text.encode('utf-8')) > 600
            or any(character in text for character in '\r\n\x00')
            or not isinstance(query, str) or not 2 <= len(query) <= 120
            or not isinstance(phrase, str) or not 4 <= len(phrase) <= len(text) or phrase not in text
            or not isinstance(fixture['space_name'], str) or not 1 <= len(fixture['space_name']) <= 100):
        raise ValueError('knowledge_text_fixture_invalid')
    proof = fixture['native_proof']
    proof_keys = {'verified', 'origin', 'runtime_id', 'checked_at', 'embedding_model', 'embedding_provider',
                  'dataset_empty', 'maximum_segments', 'indexing_technique', 'retrieval_method',
                  'reranking_enabled', 'llm_enabled', 'native_retry_policy_verified'}
    if (not isinstance(proof, dict) or not set(proof).issubset(proof_keys)
            or proof.get('verified') is not True
            or proof.get('origin') != 'http://127.0.0.1:13001'
            or not isinstance(proof.get('runtime_id'), str) or not proof['runtime_id']
            or proof.get('embedding_model') != 'BAAI/bge-m3'
            or 'siliconflow' not in str(proof.get('embedding_provider', '')).lower()
            or proof.get('dataset_empty') is not True or proof.get('maximum_segments') != 1
            or proof.get('indexing_technique') != 'high_quality'
            or proof.get('retrieval_method') != 'semantic_search'
            or proof.get('reranking_enabled') is not False or proof.get('llm_enabled') is not False
            or ('native_retry_policy_verified' in proof
                and not isinstance(proof['native_retry_policy_verified'], bool))):
        raise ValueError('knowledge_native_proof_invalid')
    checked = datetime.fromisoformat(str(proof.get('checked_at', '')).replace('Z', '+00:00'))
    if checked.tzinfo is None or not 0 <= (datetime.now(timezone.utc) - checked).total_seconds() <= 600:
        raise ValueError('knowledge_native_proof_expired')
    return fixture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True, type=Path)
    parser.add_argument('--chromium', default=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE',
        'C:/Program Files/Google/Chrome/Application/chrome.exe'))
    args = parser.parse_args()
    process = None
    born = None
    try:
        fixture = read_fixture(args.fixture)
        access = verified_access()
        browser = Path(args.chromium)
        if not browser.is_absolute() or not browser.is_file() or str(browser).startswith('\\\\'):
            raise ValueError('local_chromium_missing')
        output = DATA / fixture['run_id']
        # 每份run_id只能消费一次；失败证据保留，不能重跑同一夹具再次导入或向量化。
        output.mkdir(exist_ok=False)
        (output / 'started.json').write_text(json.dumps({'run_id': fixture['run_id'],
            'space_id': fixture['space_id'], 'native_dataset_id': fixture['native_dataset_id'],
            'started_at': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False), encoding='utf-8')
        payload = {'source': 'verified-knowledge-ui-helper-v1', 'fixture': fixture,
            'origin': access['origin'], 'username': access['username'], 'password': access['password'],
            'output_directory': str(output),
            'ownership': {'verified': True, 'component': access['component'], 'platform_pid': access['pid'],
                          'data_directory': access['data_directory']}}
        environment = {key: value for key, value in os.environ.items() if key not in {'E2E_USERNAME', 'E2E_PASSWORD'}}
        environment['PLAYWRIGHT_CHROMIUM_EXECUTABLE'] = str(browser)
        process = subprocess.Popen(['node', 'tests/knowledge-native.mjs'], cwd=REPO / 'apps/web',
            env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        born = psutil.Process(process.pid).create_time()
        raw = json.dumps(payload, ensure_ascii=False).encode()
        try:
            stdout, _ = process.communicate(input=raw, timeout=190)
        except subprocess.TimeoutExpired:
            stop_verifier_tree(process, born)
            print(json.dumps({'passed': False, 'error_code': 'knowledge_verifier_parent_deadline',
                'requires_native_index_stop_check': True}, ensure_ascii=False))
            return 1
        finally:
            raw = b''
            payload.clear()
            access.clear()
        if len(stdout) > 65536:
            raise ValueError('unsafe_verifier_output')
        result = json.loads(stdout)
        safe = {key: result[key] for key in ('passed', 'error_code', 'failed_phase', 'api_requests',
            'document_import_attempts', 'semantic_search_attempts', 'embedding_operation_upper_bound',
            'observed_total_segments', 'manual_status_refreshes', 'elapsed_seconds', 'receipt',
            'entry_operations_limited_only', 'native_retry_policy_verified',
            'upstream_embedding_http_count',
            'api_request_counting', 'download_events', 'routed_download_requests',
            'reserved_download_http_upper_bound', 'source_download_bytes_verified',
            'screenshots', 'checks', 'requires_native_index_stop_check') if key in result}
        print(json.dumps(safe, ensure_ascii=False))
        return 0 if process.returncode == 0 and safe.get('passed') is True else 1
    except Exception as error:
        if process is not None and born is not None and process.poll() is None:
            stop_verifier_tree(process, born)
        print(json.dumps({'passed': False, 'error_code': 'knowledge_verifier_unavailable',
                          'error_type': type(error).__name__}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
