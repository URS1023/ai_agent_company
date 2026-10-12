"""Twenty customer-facing PPT systems with deliberately different composition rules."""
from __future__ import annotations


def _template(
    template_id: str,
    name: str,
    category: str,
    description: str,
    recommended_for: list[str],
    colors: tuple[str, str, str, str, str, str, str],
    *,
    cover: str,
    chrome: str,
    background: str,
    cards: str,
    metrics: str,
    comparison: str,
    timeline: str,
    title_font: str = "Microsoft YaHei UI",
    body_font: str = "Microsoft YaHei",
    density: str = "balanced",
) -> dict:
    """构造一条模板目录项：色板（bg/surface/accent/accent2/text/muted/line 七个色值）+ 各版式
    风格键（封面/页眉/背景/卡片/指标/对比/时间线）+ 字体与密度。

    这些键会被 ``engine._DeckBuilder`` 直接读取来切换构图，因此新增模板只能组合既有风格值，
    不能引入未实现的分支（否则渲染会落到默认构图，视觉与描述不符）。
    """
    bg, surface, accent, accent2, text, muted, line = colors
    return {
        "id": template_id,
        "name": name,
        "category": category,
        "description": description,
        "recommended_for": recommended_for,
        "layout_family": "premium",
        "style_profile": template_id,
        "density": density,
        "layouts": [
            "statement", "agenda", "section", "content", "two_column",
            "cards", "metrics", "comparison", "timeline", "table", "chart",
            "quote", "image", "matrix", "closing",
        ],
        "preview": {
            "background": "#" + bg,
            "accent": "#" + accent,
            "text": "#" + text,
            "layout": cover,
        },
        "typography": {
            "title_font": title_font,
            "body_font": body_font,
            "title_size": 34 if density == "dense" else 36,
            "body_size": 17 if density == "dense" else 18,
        },
        "theme": {
            "background": bg,
            "surface": surface,
            "accent": accent,
            "accent2": accent2,
            "text": text,
            "muted": muted,
            "line": line,
        },
        "design": {
            "cover": cover,
            "chrome": chrome,
            "background": background,
            "cards": cards,
            "metrics": metrics,
            "comparison": comparison,
            "timeline": timeline,
        },
    }


PREMIUM_TEMPLATES = [
    _template("executive-ivory", "象牙高管", "商务", "克制留白、结论先行的高管决策材料", ["董事会", "战略汇报"], ("F4F1EA", "FFFFFF", "183A5A", "C9963B", "17191C", "62666B", "D8D2C6"), cover="split", chrome="minimal", background="plain", cards="editorial", metrics="columns", comparison="split", timeline="line"),
    _template("midnight-analytics", "午夜数据", "数据", "深色分析画布、明亮数字与原生图表", ["经营分析", "财务复盘"], ("0D1520", "172332", "38C6D9", "F2B84B", "F4F7FA", "A8B5C3", "2D3C4B"), cover="data_wall", chrome="rail", background="grid", cards="dark_tiles", metrics="hero", comparison="split", timeline="nodes", density="dense"),
    _template("aurora-glass", "极光玻璃", "发布", "通透渐层、漂浮界面与数字产品质感", ["AI 产品", "SaaS 发布"], ("EEF2FF", "FFFFFF", "6257E8", "18AFA4", "171A3D", "62698B", "D4DAF2"), cover="aurora", chrome="floating", background="orbs", cards="glass", metrics="tiles", comparison="cards", timeline="pills"),
    _template("swiss-signal", "瑞士信号", "极简", "严格网格、超大字号与红色视觉信号", ["咨询观点", "设计提案"], ("FFFFFF", "F7F7F7", "E21D2D", "111111", "111111", "666666", "D8D8D8"), cover="swiss", chrome="grid", background="grid", cards="outline", metrics="poster", comparison="rules", timeline="ticks", title_font="Arial", body_font="Arial"),
    _template("editorial-vermilion", "编辑部红", "叙事", "杂志标题、栏线、引文和证据层级", ["行业洞察", "品牌故事"], ("F5F0E7", "FBF8F1", "D94C32", "294754", "201D1A", "6B6259", "D6CCBE"), cover="editorial", chrome="editorial", background="paper", cards="editorial", metrics="columns", comparison="rules", timeline="line", title_font="SimSun"),
    _template("blueprint-pro", "工程蓝图", "技术", "深蓝图纸、坐标网格与工程标注", ["架构评审", "工程方案"], ("0B365B", "124A77", "78C7FF", "F4C14E", "EAF5FF", "A8D0EC", "2F6D9C"), cover="blueprint", chrome="technical", background="blueprint", cards="schematic", metrics="blueprint", comparison="technical", timeline="dimension", title_font="Consolas", body_font="Microsoft YaHei"),
    _template("paper-strategy", "纸张策略", "咨询", "温润纸色、铅笔规则和手工批注感", ["战略共创", "工作坊"], ("F7F0E3", "FFFDF7", "355C55", "B86D43", "242521", "6B685F", "D8CEBD"), cover="paper", chrome="notebook", background="paper", cards="paper", metrics="ledger", comparison="notes", timeline="handline"),
    _template("ink-wash-modern", "现代水墨", "文化", "宣纸留白、墨色层次与朱砂印记", ["文化项目", "品牌哲思"], ("F3F0E8", "FAF8F2", "242824", "A23A32", "1E211F", "696B65", "D2CEC3"), cover="ink", chrome="ink", background="wash", cards="ink", metrics="vertical", comparison="ink", timeline="brush", title_font="SimSun", body_font="Microsoft YaHei"),
    _template("memphis-studio", "孟菲斯创意", "创意", "高饱和几何、粗描边与年轻节奏", ["营销活动", "创意提案"], ("FFF5E8", "FFFFFF", "EF5B3C", "6C5CE7", "17202B", "5D6470", "17202B"), cover="memphis", chrome="playful", background="confetti", cards="pop", metrics="bubbles", comparison="pop", timeline="steps"),
    _template("neo-brutalist", "黑白粗野", "观点", "黑白硬边、粗线框和直接表达", ["宣言", "创新提案"], ("F6F4EF", "FFFFFF", "111111", "FF5A36", "111111", "555555", "111111"), cover="brutalist", chrome="boxed", background="plain", cards="brutal", metrics="poster", comparison="boxed", timeline="blocks", title_font="Arial Black", body_font="Arial"),
    _template("soft-saas", "柔和 SaaS", "产品", "柔和曲面、友好层级和清晰产品信息", ["产品方案", "客户成功"], ("F5F4FA", "FFFFFF", "7058A3", "E9A05A", "292535", "696274", "DED8E7"), cover="soft", chrome="floating", background="soft", cards="soft", metrics="tiles", comparison="cards", timeline="pills"),
    _template("cyber-neon", "赛博霓虹", "科技", "黑色舞台、霓虹边缘与未来系统感", ["网络安全", "前沿科技"], ("070B14", "111827", "2FF3E0", "9C6CFF", "F5F7FF", "9AA8BC", "243247"), cover="cyber", chrome="technical", background="circuit", cards="neon", metrics="hero", comparison="technical", timeline="nodes", title_font="Consolas", body_font="Microsoft YaHei", density="dense"),
    _template("esg-canopy", "ESG 自然", "可持续", "自然绿、年轮曲线与可持续证据", ["ESG 报告", "可持续战略"], ("EEF3E9", "FAFCF7", "2E6B4F", "C89B4B", "203329", "617064", "CFDCCF"), cover="canopy", chrome="organic", background="rings", cards="organic", metrics="vertical", comparison="organic", timeline="path"),
    _template("noir-gold", "黑金典藏", "高端", "黑色底幕、金色细线和克制奢华", ["高端品牌", "投资推介"], ("0C0C0D", "171719", "C9A45D", "8E6C35", "F7F2E8", "AAA397", "35312B"), cover="luxury", chrome="luxury", background="frame", cards="luxury", metrics="columns", comparison="rules", timeline="ticks", title_font="SimSun"),
    _template("civic-red", "政务红", "政务", "庄重红、规范横幅与正式汇报秩序", ["政府汇报", "国企总结"], ("FAF7F2", "FFFFFF", "A71924", "D7B06A", "29201F", "6E5A58", "DDCFC8"), cover="civic", chrome="band", background="plain", cards="formal", metrics="ledger", comparison="boxed", timeline="blocks", title_font="Microsoft YaHei"),
    _template("clinical-blue", "医疗洁净", "医疗", "洁净蓝白、临床数据和可信证据", ["医学研究", "医疗方案"], ("F1F7FA", "FFFFFF", "24749A", "45A88B", "193540", "5B727B", "CDE0E7"), cover="clinical", chrome="clinical", background="soft_grid", cards="clinical", metrics="tiles", comparison="clinical", timeline="nodes"),
    _template("academic-burgundy", "学术酒红", "研究", "论文式层级、酒红注脚和方法证据", ["论文答辩", "研究报告"], ("F8F6F2", "FFFFFF", "7B2937", "244B63", "222222", "66625F", "D9D4CC"), cover="academic", chrome="academic", background="paper", cards="academic", metrics="columns", comparison="rules", timeline="line", title_font="SimSun", density="dense"),
    _template("industrial-orange", "工业橙", "工业", "石墨灰、警示橙与制造现场秩序", ["制造汇报", "安全生产"], ("20252B", "2A3138", "F28C28", "5CB8B2", "F5F6F7", "B4BCC4", "414A53"), cover="industrial", chrome="technical", background="grid", cards="industrial", metrics="hero", comparison="technical", timeline="dimension", density="dense"),
    _template("cinematic-story", "影像叙事", "品牌", "电影字幕、宽幅影像区和强叙事节奏", ["品牌故事", "案例发布"], ("171718", "232326", "F05A3E", "D5B26D", "FAF7F1", "B7B0A7", "39383A"), cover="cinematic", chrome="cinematic", background="frame", cards="cinematic", metrics="poster", comparison="cinematic", timeline="film"),
    _template("data-press", "数据新闻", "数据", "多栏证据、来源线和新闻编辑节奏", ["市场研究", "行业报告"], ("F7F5EF", "FFFFFF", "155D78", "D95D39", "1B292E", "606B70", "D8D3C9"), cover="press", chrome="press", background="paper_grid", cards="press", metrics="ledger", comparison="rules", timeline="line", title_font="SimSun", density="dense"),
]

