"""生成项目架构示意图；交互原型由独立HTML承载，截图另行浏览器验证。"""

from pathlib import Path
from html import escape
import cairosvg

ROOT = Path(__file__).resolve().parents[1] / 'assets'


def text(x, y, value, size=20, color='#233348', weight=400):
    """统一图中文字，内容经XML转义，避免标签破坏。"""
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}">{escape(value)}</text>'


def rect(x, y, width, height, fill, stroke='none', radius=12):
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>'


def shell(width, height, content):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#6a80a0"/></marker></defs><g font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif">{content}</g></svg>'


def arrow(x1, y1, x2, y2):
    return f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" stroke="#6a80a0" stroke-width="2.5" marker-end="url(#arrow)"/>'


def box(x, y, width, title, lines, fill='#ffffff', accent='#1668dc', height=140):
    out = rect(x, y, width, height, fill, '#dbe4ee')
    out += rect(x, y, 6, height, accent, radius=3)
    out += text(x + 22, y + 35, title, 23, accent, 700)
    for index, line in enumerate(lines):
        out += text(x + 22, y + 68 + index * 27, line, 18)
    return out


def architecture():
    out = rect(0, 0, 1600, 1010, '#f3f6fa', radius=0)
    out += text(60, 58, '制造业 Agent 平台：完整复用与业务聚合', 32, '#152a47', 700)
    out += text(60, 94, '入口、身份、业务资源与结果统一；原生应用、数据库与执行语义保持清楚边界', 20, '#5b6e87')
    out += box(60, 130, 1480, '员工 · 设备工程师 · 质检人员 · 管理员', ['同一企业身份；按组织、工厂、工作区和具体资源授权'], fill='#edf4ff', height=105)
    out += arrow(800, 237, 800, 273)
    out += box(60, 275, 1480, 'qKnow 风格统一产品入口', ['/home 工作台 · /ai 原生对话 · /studio 原生开发 · /data 分析 · /reports 报表 · 工业业务页面'], height=105)
    positions = [60, 440, 820, 1200]
    data = [('Open WebUI', ['Svelte + FastAPI', '对话、工具循环、子 Agent', '附件、记忆、成果与个人任务']), ('Dify', ['React/Next + Python', 'Agent V2、知识、工作流节点', '调试、发布、触发与执行']), ('qKnow 特色领域', ['Vue + 可复用领域服务', '本体、抽取、图谱与运营', '预留功能补齐真实接口']), ('Test5 分析报表', ['Vue + Python 提取模块', '受控查询、设计器与组件', '20 模板、绑定、版本与导出'])]
    for x, (title, lines) in zip(positions, data):
        out += arrow(x + 170, 382, x + 170, 422)
        out += box(x, 425, 340, title, lines, height=170)
        out += arrow(x + 170, 598, x + 170, 647)
    out += box(60, 650, 1480, '平台业务服务、薄适配器与运行目录', ['设备 / 产品 / 检测 / 异常处置 / 分析成果；资源 ID 与版本、输入输出、来源证据与执行身份', '权限二次校验 · Outbox 投递 · 幂等回执 · 状态对账 · 实际取消 · 不明终态不自动重放'], fill='#edf8f4', accent='#16866f', height=135)
    stores = [('平台业务库', ['设备、检测、处置、映射和审计']), ('原生应用各自存储', ['会话、Dify 资源和运行、图谱']), ('客户业务数据', ['受控查询；不由聊天直接写库']), ('文件与模型服务', ['资料成果、检索、视觉、代码执行'])]
    for x, (title, lines) in zip(positions, stores):
        out += arrow(x + 170, 788, x + 170, 825)
        out += box(x, 828, 340, title, lines, fill='#ffffff', accent='#49607b', height=105)
    out += text(60, 977, '设计示意，尚未实施。部署、SSO、子路径、原生权限和资源容量必须在技术验证阶段实测。', 19, '#60718a')
    return shell(1600, 1010, out)


def main():
    """保留矢量原图与PNG阅读副本，便于后续文档使用和视觉验收。"""
    for name, render in [('platform-architecture', architecture)]:
        source = render()
        (ROOT / f'{name}.svg').write_text(source, encoding='utf-8')
        cairosvg.svg2png(bytestring=source.encode('utf-8'), write_to=str(ROOT / f'{name}.png'))
        print(f'{name}: SVG/PNG已生成')


if __name__ == '__main__':
    main()
