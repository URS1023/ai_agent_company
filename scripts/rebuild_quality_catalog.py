"""Rebuild a catalogue from reviewed local evidence, without any database access.

Preserves the deployment workspace/data source identifiers. Workflow identifiers
come only from matching checked-in graph scopes; unresolved scopes stay pending.
This command never imports workflows or changes their data source bindings.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {1: 'incoming', 2: 'process', 3: 'finished'}
# The source template enum differs from the source form enum.
TEMPLATE_CATEGORIES = {1: 1, 3: 2, 2: 3}


def workflow_scopes(directory: Path) -> dict[tuple[int, str], str]:
    """从已入库的流程快照中提取「检验类别 + 质量模板 -> 工作流 id」映射。

    只接受 read-values 节点参数完整固定（category/qualityTemplate 为 fixed、formNumber 为 input）
    且四个读取节点指向同一范围的快照；出现未限定范围、范围不一致或重复范围时抛 ValueError，
    宁可不产出目录也不猜测工作流归属。
    """
    result = {}
    for path in sorted(directory.glob('*.json')):
        graph = json.loads(path.read_text(encoding='utf-8'))['graph']
        reads = [node['config'] for node in graph['nodes'] if node['kind'] == 'read-values']
        if len(reads) != 4 or any(read['parameterSources'] != {
            'category': 'fixed', 'qualityTemplate': 'fixed', 'formNumber': 'input',
        } for read in reads):
            raise ValueError(f'Unscoped workflow snapshot: {path.name}')
        scopes = {(read['parameters']['category'], read['parameters']['qualityTemplate']) for read in reads}
        if len(scopes) != 1:
            raise ValueError(f'Inconsistent workflow scope: {path.name}')
        scope = scopes.pop()
        if scope in result:
            raise ValueError(f'Ambiguous workflow scope: {path.name}')
        result[scope] = path.stem
    return result


def build_catalogue(source: dict, previous: dict, scopes: dict) -> dict:
    """用本地快照重建质量目录：产物/模板/检验项 + 已匹配工作流的条目，未匹配的进入 pendingEntries。

    规则：工作区与数据源标识沿用上一次目录（不在这里改部署绑定）；每个 entry 都注明缺失原因
    （类别无法识别、模板或产品已删除、类别与模板不一致、没有匹配的流程快照）。
    校验：快照文本出现替换字符（解码有损）、id 重复时抛 ValueError——有损数据必须先按 UTF-8 重读。
    """
    for section in ('products', 'templates', 'standards'):
        for row in source.get(section, []):
            if any('\ufffd' in value for value in row.values() if isinstance(value, str)):
                raise ValueError(f'Lossy text decoding in {section}; reread the source as UTF-8 before rebuilding')
    products = {row['id']: row for row in source['products']}
    templates = {row['id']: row for row in source['templates']}
    if len(products) != len(source['products']) or len(templates) != len(source['templates']):
        raise ValueError('Duplicate source identifiers')
    requirements = Counter(row['templateId'] for row in source['standards'])
    counts = Counter()
    for row in source['form_groups']:
        counts[(row['category'], row['templateId'] or '')] += row['formCount']
    entries, pending = [], []
    covered = set()

    def add_scope(category, template_id, count):
        template = templates.get(template_id)
        product = products.get(template['productId']) if template else None
        entry = {
            'key': f'quality_{category}_{template_id or "unassociated"}',
            'templateId': template_id, 'templateName': template['name'] if template else '未关联产品模板的历史单据',
            'productId': product['id'] if product else None,
            'productName': product['name'] if product else '待确认产品',
            'productCode': product['code'] if product else '',
            'category': CATEGORIES.get(category), 'categoryValue': category,
            'requirementCount': requirements[template_id], 'formCount': count,
        }
        reason = None
        if category not in CATEGORIES:
            reason = '单据检验类别缺失或无法识别'
        elif template_id and not template:
            reason = '单据关联的模板已删除或不存在'
        elif template and not product:
            reason = '模板关联的产品已删除或不存在'
        elif template and TEMPLATE_CATEGORIES.get(template['inspectionType']) != category:
            reason = '模板检验类别缺失或与单据类别不一致'
        elif (category, template_id) not in scopes:
            reason = '尚无与此产品模板及检验类别匹配的流程快照'
        if reason:
            pending.append({**entry, 'reason': reason})
        else:
            entries.append({**entry, 'workflowId': scopes[(category, template_id)]})
        covered.add((category, template_id))

    for template in source['templates']:
        category = TEMPLATE_CATEGORIES.get(template['inspectionType'])
        related = [(scope, count) for scope, count in counts.items() if scope[1] == template['id']]
        for (form_category, template_id), count in related:
            add_scope(form_category, template_id, count)
        if not related or (category is not None and (category, template['id']) not in covered):
            add_scope(category, template['id'], 0)
    for (category, template_id), count in counts.items():
        if (category, template_id) not in covered:
            add_scope(category, template_id, count)
    with_requirements = {row['productId'] for row in templates.values() if requirements[row['id']]}
    return {
        'workspaceId': previous['workspaceId'], 'datasourceId': previous['datasourceId'],
        'sourceLabel': 'szh_new 本地数据库', 'snapshotAt': source['verifiedAt'][:10],
        'products': [{**row, 'hasRequirements': row['id'] in with_requirements} for row in source['products']],
        'entries': entries, 'pendingEntries': pending,
        'sourceEvidence': 'szh_new/quality_source_snapshot.json',
    }


def main() -> None:
    """读取本地快照重建 configurations/quality_catalog.json。

    参数：--write 才写回仓库文件，缺省只把结果打到标准输出（便于先人工复核）。
    前置条件：configurations/szh_new/quality_source_snapshot.json 与 product_workflows/ 已存在；
    本脚本完全不访问数据库、不导入工作流、不改数据源绑定。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Write the reviewed repository catalogue (no deployment/database writes)')
    args = parser.parse_args()
    path = ROOT / 'configurations/quality_catalog.json'
    source = json.loads((ROOT / 'configurations/szh_new/quality_source_snapshot.json').read_text(encoding='utf-8'))
    previous = json.loads(path.read_text(encoding='utf-8'))
    result = build_catalogue(source, previous, workflow_scopes(ROOT / 'configurations/szh_new/product_workflows'))
    content = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.write:
        path.write_text(content, encoding='utf-8')
    else:
        print(content)


if __name__ == '__main__':
    main()
