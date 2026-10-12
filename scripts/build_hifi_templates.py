"""按参考图原始坐标生成模板和可复用组件皮肤，不读取业务数据源。"""
from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from copy import deepcopy
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'scripts/resources/reference_screen_regions.json'
COMPONENTS = {'text': 'JText', 'metric': 'JStatsSummary', 'bar': 'JBar', 'hbar': 'JHorizontalBar',
              'line': 'JLine', 'area': 'JArea', 'ring': 'JRing', 'pie': 'JPie', 'gauge': 'JGauge',
              'radar': 'JRadar', 'table': 'JScrollBoard'}


def write_json(path: Path, value: object) -> None:
    """以固定格式落盘，重复生成不会引入随机标识或时间差异。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def panel_background(image: Image.Image, rect: list[int]) -> str:
    """从数据区边缘取主色，避免透明背景暴露旧示例值。"""
    x, y, width, height = rect
    colors = [image.getpixel((min(image.width - 1, x + dx), min(image.height - 1, y + dy)))[:3]
              for dx in range(2, max(3, width - 2), max(1, width // 16))
              for dy in (2, max(2, height - 3))]
    color = Counter(tuple(channel // 4 * 4 for channel in rgb) for rgb in colors).most_common(1)[0][0]
    return '#' + ''.join(f'{channel:02x}' for channel in color)


def panel_surface(image: Image.Image, rect: list[int]) -> str:
    """用原图边缘的低亮度采样重建面板纵向底色，避免统一纯色块破坏层次。"""
    x, y, width, height = rect
    stops = []
    for position in (0, 25, 50, 75, 100):
        sy = min(image.height - 1, y + round((height - 1) * position / 100))
        samples = [image.getpixel((min(image.width - 1, x + offset), sy))[:3]
                   for offset in (1, 2, 3, max(1, width - 4), max(1, width - 3), max(1, width - 2))]
        rgb = sorted(samples, key=lambda value: sum(value))[len(samples) // 3]
        stops.append('#' + ''.join(f'{channel:02x}' for channel in rgb) + f' {position}%')
    return 'linear-gradient(180deg, ' + ', '.join(stops) + ')'


def sample_data(region: dict) -> list:
    """编辑起点为显式示例；无法从 JPG 恢复的原始序列不冒充真实数据。"""
    if 'data' in region:
        return deepcopy(region['data'])
    kind = region['kind']
    if kind in {'text', 'metric'}:
        return [{'name': region['name'], 'value': region.get('value', ''), 'unit': region.get('unit', '')}]
    if kind == 'table':
        return [{'名称': '示例项目', '数量': 128, '状态': '完成'}, {'名称': '示例项目二', '数量': 96, '状态': '处理中'}]
    return [{'name': f'{index + 1}月', 'value': value} for index, value in enumerate([36, 58, 42, 76, 64, 85])]


def sample_hash(data: list) -> str:
    """只存示例指纹，不把原始取值混入不可清理的视觉字段。"""
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-16-le')
    value = 2166136261
    for index in range(0, len(encoded), 2):
        value = ((value ^ int.from_bytes(encoded[index:index + 2], 'little')) * 16777619) & 0xffffffff
    return f'{value:08x}'


def generate() -> None:
    """生成恰好 20 套模板，并同步组件库中的各模板专用组件。"""
    specs = json.loads(MANIFEST.read_text(encoding='utf-8'))['screens']
    sources = sorted((ROOT / '参照/模板').glob('*.jpg'))
    if len(specs) != 20 or len(sources) != 20:
        raise ValueError('参考目录与区域清单必须各包含 20 项')
    templates, categories = [], []
    for index, (spec, source) in enumerate(zip(specs, sources), 1):
        number = f'{index:02d}'
        image = Image.open(source).convert('RGB')
        width, height = image.size
        asset = f'/assets/analytics/jimu-screen/templates/template-{number}.jpg'
        public = ROOT / f'apps/analytics/public/jimu-screen/templates/template-{number}.jpg'
        public.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, public)
        widgets, library_items, holes = [], [], []
        for slot, region in enumerate(spec['regions'], 1):
            x, y, w, h = region['rect']
            if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
                raise ValueError(f'模板 {number} 的区域 {region["name"]} 超出参考图')
            holes.append(region['rect'])
            data = sample_data(region)
            skin = {'version': 1, 'kind': region['kind'], 'asset': asset, 'sourceWidth': width,
                    'sourceHeight': height, 'rect': region['rect'], 'sampleHash': sample_hash(data),
                    'background': region.get('background') or panel_background(image, region['rect']),
                    'surface': panel_surface(image, region['rect']),
                    'color': region.get('color') or spec['color'], 'textColor': spec.get('textColor', '#eaf8ff'),
                    'palette': spec['palette'], 'fontSize': region.get('fontSize', max(7, round(width / 125))),
                    'valueFontSize': region.get('valueFontSize', max(12, round(h * .78))),
                    'align': region.get('align', 'center'), 'label': region.get('label', False)}
            identity = f'w-hifi-{number}-{slot}'
            component = COMPONENTS[region['kind']]
            widget = {'id': identity, 'i': identity, 'component': component, 'componentName': region['name'],
                      'type': 'title' if region['kind'] == 'text' else 'metric' if region['kind'] == 'metric' else 'table' if region['kind'] == 'table' else 'chart',
                      'x': x, 'y': y, 'w': w, 'h': h, 'orderNum': slot + 1, 'rotation': 0,
                      'visible': True, 'locked': False, 'referenceSkin': skin,
                      'visualRole': 'dashboard-title' if slot == 1 else 'content',
                      'config': {'dataType': 1, 'dataStatus': 'sample', 'referenceSample': True,
                                 'chartData': json.dumps(data, ensure_ascii=False), 'chartDataParsed': data,
                                 'background': '#FFFFFF00', 'borderColor': '#FFFFFF00', 'option': {}}}
            widgets.append(widget)
            library_items.append({'id': f'reference-{number}-{slot}', 'name': region['name'], 'compType': component,
                                  'width': w, 'height': h, 'referenceWidget': deepcopy(widget),
                                  'icon': 'fa-solid fa-puzzle-piece', 'path': f'参考模板 {number} / {region["name"]}'})
        # 底图只保留装饰区域；数据区挖空，隐藏或删除组件也不会露出旧值。
        widgets.insert(0, {'id': f'w-hifi-{number}-skin', 'i': f'w-hifi-{number}-skin', 'component': 'JDragImg',
                           'componentName': '参考装饰底图', 'type': 'decoration', 'x': 0, 'y': 0, 'w': width,
                           'h': height, 'orderNum': 0, 'rotation': 0, 'visible': True, 'locked': True,
                           'referenceSkin': {'version': 1, 'kind': 'backdrop', 'asset': asset, 'sourceWidth': width,
                                             'sourceHeight': height, 'rect': [0, 0, width, height], 'holes': holes},
                           'config': {'dataType': 1, 'background': '#FFFFFF00', 'borderColor': '#FFFFFF00', 'option': {}}})
        description = '按参考 JPG 原始尺寸拆分装饰与数据区域；参考示例保留原图效果，编辑或绑定后按当前数据重绘。'
        templates.append({'id': f'hifi-template-{number}', 'code': f'HIFI-{number}', 'name': spec['name'],
                          'description': description, 'summary': description, 'category': '参考大屏', 'tags': ['参考样式', '数据可编辑', '原图示例'],
                          'preview': asset, 'enabled': True, 'version': 2, 'builtin': True, 'styleLocked': True,
                          'sourceKind': 'hifi-reference', 'sourceSystem': '参照/模板', 'sourceRecordId': f'reference-{number}',
                          'referenceFile': source.name, 'referenceSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                          'fidelityMode': 'reference-skin-with-editable-data', 'componentCount': len(widgets),
                          'componentCountLabel': f'{len(widgets) - 1} 个可编辑组件', 'width': width, 'height': height,
                          'content': {'canvas': {'width': width, 'height': height, 'backgroundColor': spec['background'],
                                                 'backgroundImage': '', 'styleLocked': True, 'bindingContract': 'data-only-v1',
                                                 'referenceSkin': True}, 'widgets': widgets}})
        categories.append({'name': f'{number} {spec["name"]}', 'count': len(library_items), 'items': library_items})
    write_json(ROOT / 'backend/app/modules/reports/resources/hifi_templates.json', {'templates': templates})
    library_path = ROOT / 'apps/analytics/src/data/jimuComponentLibrary.json'
    library = json.loads(library_path.read_text(encoding='utf-8'))
    library['toolbarGroups'] = [group for group in library['toolbarGroups'] if group['key'] not in {'hifi', 'reference'}]
    library['toolbarGroups'].insert(0, {'key': 'reference', 'label': '参考组件', 'icon': 'fa-solid fa-puzzle-piece', 'categories': categories})
    library['meta']['referenceScreenCount'] = len(templates)
    library['meta']['referenceComponentCount'] = sum(category['count'] for category in categories)
    library['meta']['toolbarGroupCount'] = len(library['toolbarGroups'])
    library['meta']['toolbarItemCount'] = sum(len(category['items']) for group in library['toolbarGroups'] for category in group['categories'])
    write_json(library_path, library)
    print(f'生成 {len(templates)} 套参考模板、{library["meta"]["referenceComponentCount"]} 个组件')


if __name__ == '__main__':
    generate()
