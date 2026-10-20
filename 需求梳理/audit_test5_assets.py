"""静态登记 Test5 界面、报表组件与模板资产，不导入业务模块或连接数据库。"""

from pathlib import Path
import csv
import json
import re

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent / 'the_company_agent-codex-python-snapshot-20261020'


def save_csv(name, rows):
    """以带编码标记的 CSV 保存静态清单，便于 Excel 直接查看。"""
    with (BASE / name).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    """完整枚举分析子应用 Vue 文件和报表资源中的实际记录。"""
    frontend = ROOT / 'apps/analytics/src'
    rows = []
    for path in sorted(frontend.rglob('*.vue')):
        rows.append({'类型': 'Vue界面或子组件', '路径': str(path.relative_to(ROOT)).replace('\\', '/'), '标识': path.stem, '用途依据': '实际源码文件；具体用途见文件及核查报告', '状态': '已枚举，未逐操作运行'})
    routes = re.findall(r"path:\s*'([^']*)'", (frontend / 'router.ts').read_text(encoding='utf-8'))
    for route in routes:
        rows.append({'类型': '路由声明', '路径': 'apps/analytics/src/router.ts', '标识': route, '用途依据': '实际路由声明，包含子路由、重定向和兜底', '状态': '静态读取，非独立功能数'})
    for module in ['analytics', 'reports', 'report_prompts']:
        directory = ROOT / 'backend/app/modules' / module
        for path in sorted(directory.glob('*.py')):
            rows.append({'类型': '分析报表后端模块', '路径': str(path.relative_to(ROOT)).replace('\\', '/'), '标识': path.stem, '用途依据': '实际模块文件，入口和依赖见核查报告', '状态': '静态枚举，未执行业务'})
    save_csv('Test5_界面源码索引_20261020.csv', rows)

    resource = ROOT / 'backend/app/modules/reports/resources'
    catalog = json.loads((resource / 'component_catalog.json').read_text(encoding='utf-8'))
    assets, types = [], set()
    for group in catalog.get('toolbarGroups', []):
        for category in group.get('categories', []):
            for index, item in enumerate(category.get('items', [])):
                comp = str(item.get('compType', ''))
                if comp:
                    types.add(comp)
                assets.append({'种类': '工具栏组件记录', '分组': group.get('label', group.get('key', '')), '分类': category.get('name', ''), '标识': item.get('id', f'{comp}-{index}'), '名称': item.get('name', ''), '组件类型': comp, '来源': 'backend/app/modules/reports/resources/component_catalog.json', '状态': '目录登记；渲染及数据替换尚未动态验收'})
    for item in catalog.get('rendererExtensions', []):
        comp = item if isinstance(item, str) else str(item.get('compType', item.get('type', '')))
        types.add(comp)
        assets.append({'种类': '渲染扩展', '分组': '', '分类': '', '标识': comp, '名称': comp, '组件类型': comp, '来源': 'backend/app/modules/reports/resources/component_catalog.json', '状态': '静态登记，未动态验收'})
    templates = json.loads((resource / 'hifi_templates.json').read_text(encoding='utf-8'))['templates']
    for item in templates:
        assets.append({'种类': '高保真模板', '分组': '', '分类': item.get('category', ''), '标识': item.get('id', ''), '名称': item.get('name', ''), '组件类型': '', '来源': 'backend/app/modules/reports/resources/hifi_templates.json', '状态': '结构存在；不代表全部组件可编辑性通过'})
    reports = json.loads((resource / 'lynx_source_reports.json').read_text(encoding='utf-8'))['reports']
    for item in reports:
        assets.append({'种类': 'Lynx来源报表记录', '分组': '', '分类': item.get('category', ''), '标识': item.get('sourceRecordId', item.get('id', '')), '名称': item.get('name', ''), '组件类型': '', '来源': 'backend/app/modules/reports/resources/lynx_source_reports.json', '状态': '来源快照记录；不是本轮新增或运行成功数'})
    save_csv('Test5_报表组件模板清单_20261020.csv', assets)
    summary = {'vue_files': sum(row['类型'] == 'Vue界面或子组件' for row in rows), 'route_declarations': len(routes), 'source_index_rows': len(rows), 'component_toolbar_records': sum(row['种类'] == '工具栏组件记录' for row in assets), 'catalog_render_type_identifiers': len(types), 'renderer_extensions': len(catalog.get('rendererExtensions', [])), 'hifi_templates': len(templates), 'lynx_source_reports': len(reports), 'asset_index_rows': len(assets), 'catalog_meta': catalog.get('meta', {})}
    (BASE / 'Test5_静态盘点统计_20261020.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in summary.items() if key != 'catalog_meta'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
