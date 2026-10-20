"""保存用户指定的模型密钥，并可进行一次有限、不含业务数据的连通验证。"""
import argparse
from getpass import getpass
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/platform'))
from agent_platform.core.provider_settings import LocalProviderStore


def probe(store, roles=('chat', 'embedding')):
    """每服务只发一个短请求，不重试、不切模型、不输出密钥或向量内容。"""
    import httpx
    values = store.read()
    result = {}
    with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False) as client:
        for role, data in values.items():
            if role not in roles:
                continue
            body = ({'model': data['model'], 'messages': [{'role': 'user', 'content': 'Reply OK.'}],
                     'max_tokens': 64, 'stream': False} if role == 'chat' else
                    {'model': data['model'], 'input': ['本机部署连通性验证'], 'encoding_format': 'float'})
            try:
                headers = {'Authorization': 'Bearer ' + data['api_key'], 'User-Agent': 'manufacturing-agent-platform/0.1'}
                if role == 'chat':
                    headers['x-opencode-session'] = 'deployment-check-' + uuid4().hex
                response = client.post(data['base_url'] + ('/chat/completions' if role == 'chat' else '/embeddings'),
                    headers=headers, json=body)
                summary = {'status_code': response.status_code, 'model': data['model'], 'ready': False}
                if response.status_code == 200:
                    output = response.json()
                    if role == 'chat':
                        choices = output.get('choices', [])
                        text_present = bool(choices and choices[0].get('message', {}).get('content'))
                        summary.update(ready=text_present, returned_model=output.get('model'), usage=output.get('usage'),
                                       text_reply_present=text_present)
                    else:
                        vectors = output.get('data', [])
                        summary.update(ready=bool(vectors), dimensions=len(vectors[0].get('embedding', [])) if vectors else 0,
                                       usage=output.get('usage'))
                else:
                    # 第三方错误可能回显鉴权信息，只提取状态码，不打印响应正文。
                    summary['error'] = '服务拒绝请求，请核对账户授权、额度和模型可用性'
                result[role] = summary
            except (httpx.HTTPError, ValueError) as exc:
                result[role] = {'model': data['model'], 'ready': False, 'error_type': type(exc).__name__}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stdin-json', action='store_true', help='从标准输入读取密钥对象，不使用命令行参数传递密钥')
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--check', action='store_true', help='只查看不含密钥的配置摘要')
    args = parser.parse_args()
    store = LocalProviderStore()
    if not args.check:
        if args.stdin_json:
            value = json.load(sys.stdin)
            store.write(value['chat_key'], value['embedding_key'])
        else:
            store.write(getpass('OpenCode API Key: '), getpass('硅基流动 API Key: '))
    result = {'providers': store.metadata()}
    if args.probe:
        result['verification'] = probe(store)
    print(json.dumps(result, ensure_ascii=False, indent=2))
