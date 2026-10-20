"""组合已验证的qKnow交互样例与制造业务页面，生成可直接打开的单HTML。"""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'qknow-design-test/qknow-design-test.html'
if not source.exists():
    source = ROOT.parent / 'qknow-design-test/qknow-design-test.html'
html = source.read_text(encoding='utf-8')
css = (ROOT / 'frontend/preview.css').read_text(encoding='utf-8')
script = (ROOT / 'frontend/preview.js').read_text(encoding='utf-8')
html = html.replace('</head>', '<style>\n' + css + '\n</style>\n</head>')
html = html.replace('</body>', '<script>\n' + script + '\n</script>\n</body>')
(ROOT / 'index.html').write_text(html, encoding='utf-8')
print('index.html generated')
