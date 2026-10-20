"""只读提取相关项目的直接用户历史，用于需求溯源；不读取凭据文件或连接数据库。"""

from pathlib import Path
import hashlib
import json
import re
import subprocess

BASE = Path(__file__).resolve().parent
CODEX = Path('C:/Users/urdcbk/.codex')
PROJECT = re.compile(r'test5|the_company_agent|new_agent|qknow|[/\\]lynx', re.I)
EXCLUDED_SESSION = '01a15830-b81d-71a0-a3eb-8f34d2041948'


def redact(text):
    """移除常见凭据、联系信息及临时签名参数，保留业务需求表述。"""
    sensitive_line = re.compile(r'(?i)password|passwd|密码|口令|密钥|密匙|authorization\s*:|bearer\s+|--cookie|(?:\badmin|\broot)\s+\S+|\b(?:admin|root)\d{3,}|oc_sk_|\bsk-[A-Za-z0-9_-]{12,}')
    text = '\n'.join('[该行涉及账号或凭据，已省略；业务意图见需求归纳]' if sensitive_line.search(line) else line for line in text.splitlines())
    text = re.sub(r'(?i)(password|passwd|api[_ -]?key|access[_ -]?token|secret|密码|口令)\s*[:：=]\s*[^\s,，;；]+', r'\1=[已脱敏]', text)
    text = re.sub(r'(密码|口令|密钥|密匙)(?:修改为|修改|改为|改成|设为|是|为)?\s*[:：=]?\s*[^\s,，;；|]+', r'\1[已脱敏]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{12,}\b', '[已脱敏密钥]', text)
    text = re.sub(r'\boc_sk_[A-Za-z0-9_-]+\b', '[已脱敏密钥]', text)
    text = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._-]+', 'Bearer [已脱敏]', text)
    text = re.sub(r"(?i)(?:-b|--cookie)\s+'[^']*'", '--cookie [已脱敏]', text)
    text = re.sub(r'(?i)([?&](?:token|x-signature|signature|key)=)[^&\s]+', r'\1[已脱敏]', text)
    text = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[已脱敏邮箱]', text)
    text = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[已脱敏电话]', text)
    return text


def user_text(record):
    """仅返回直接用户消息，忽略系统指令与子任务工具输出。"""
    payload = record.get('payload', {})
    if record.get('type') != 'response_item' or payload.get('role') != 'user':
        return ''
    content = payload.get('content', [])
    if isinstance(content, str):
        return content
    return '\n'.join(item.get('text', '') for item in content if isinstance(item, dict))


def main():
    """扫描现存会话元数据，导出相关主会话的用户需求及可追溯行号。"""
    paths = subprocess.check_output(['rg', '--files', str(CODEX / 'sessions'), str(CODEX / 'archived_sessions'), '-g', '*.jsonl'], text=True, encoding='utf-8').splitlines()
    titles = {}
    for line in (CODEX / 'session_index.jsonl').read_text(encoding='utf-8').splitlines():
        try:
            row = json.loads(line)
            titles[row['id']] = row.get('thread_name', '')
        except (ValueError, KeyError):
            continue
    sessions, messages = [], []
    for name in paths:
        path = Path(name)
        try:
            with path.open(encoding='utf-8') as stream:
                meta = json.loads(next(stream))['payload']
                session_id = meta.get('id', meta.get('session_id', ''))
                if session_id == EXCLUDED_SESSION or isinstance(meta.get('source'), dict) or meta.get('thread_source') not in (None, 'user'):
                    continue
                cwd = meta.get('cwd', '')
                if not PROJECT.search(cwd + ' ' + titles.get(session_id, '')):
                    continue
                item = {'session_id': session_id, 'title': titles.get(session_id, ''), 'cwd': cwd, 'source': str(path), 'timestamp': meta.get('timestamp', ''), 'messages': 0}
                seen = set()
                for number, line in enumerate(stream, start=2):
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    text = user_text(record).strip()
                    if not text or '# AGENTS.md instructions' in text or text.startswith(('<codex_internal_context', '<environment_context>', '<external_codex_apps_open_page>', '<send_user_message_question_reply>')) or text in seen:
                        continue
                    if '<turn_aborted>' in text:
                        continue
                    seen.add(text)
                    identity = hashlib.sha256(f'{session_id}:{number}'.encode()).hexdigest()[:10]
                    messages.append({'id': f'H-agent-1020-root-{identity}', 'timestamp': record.get('timestamp', ''), 'session_id': session_id, 'title': item['title'], 'source': str(path), 'line': number, 'text': redact(text)})
                    item['messages'] += 1
                sessions.append(item)
        except (OSError, ValueError, KeyError, StopIteration):
            continue
    sessions.sort(key=lambda row: row['timestamp'])
    messages.sort(key=lambda row: (row['timestamp'], row['session_id'], row['line']))
    (BASE / 'Codex历史需求原文_脱敏_20261020.jsonl').write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in messages) + '\n', encoding='utf-8')
    (BASE / 'Codex历史会话覆盖_20261020.json').write_text(json.dumps({'scanned_files': len(paths), 'related_sessions': len(sessions), 'user_messages': len(messages), 'sessions': sessions}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'scanned_files': len(paths), 'related_sessions': len(sessions), 'user_messages': len(messages)}, ensure_ascii=False))
    for row in sessions:
        print(json.dumps({key: row[key] for key in ('session_id', 'title', 'cwd', 'messages')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
