"""导出代码实际注册的接口，不将设计基线覆盖为“已经实现”。"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/platform'))
from agent_platform.main import create_app


if __name__ == '__main__':
    schema = create_app().openapi()
    target = ROOT / '项目文档/contracts/platform.openapi.json'
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    operations = [(path, method, operation['operationId']) for path, methods in schema['paths'].items()
                  for method, operation in methods.items() if method in {'get', 'post', 'patch', 'put', 'delete'}]
    ids = [operation[2] for operation in operations]
    if len(ids) != len(set(ids)):
        raise RuntimeError('实际接口operationId重复')
    print(json.dumps({'operations': len(operations), 'paths': len(schema['paths']), 'file': str(target),
                      'note': '仅表示代码注册，不等于业务验收通过'}, ensure_ascii=False))
