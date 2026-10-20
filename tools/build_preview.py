"""刷新单HTML内嵌的业务样式与交互，不依赖原参考项目源码。"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'index.html'
html = source.read_text(encoding='utf-8')
css = (ROOT / 'frontend/preview.css').read_text(encoding='utf-8')
script = (ROOT / 'frontend/preview.js').read_text(encoding='utf-8')
for tag, marker, content in [('style', 'manufacturing-preview-style', css), ('script', 'manufacturing-preview-script', script)]:
    pattern = rf'<{tag} id="{marker}">[\s\S]*?</{tag}>'
    html, count = re.subn(pattern, lambda _: f'<{tag} id="{marker}">\n{content}\n</{tag}>', html)
    if count != 1:
        raise SystemExit(f'无法定位唯一嵌入区：{marker}')
(ROOT / 'index.html').write_text(html, encoding='utf-8')
print('index.html generated')
