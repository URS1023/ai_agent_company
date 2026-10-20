# -*- coding: utf-8 -*-
"""从需求梳理 Markdown 生成可筛选的矩阵看板（单一事实源：MD 文件）。"""
import json, re, pathlib

BASE = pathlib.Path(__file__).parent
MD = BASE / "工业AI平台_需求梳理_v1.md"
OUT = BASE / "工业AI平台_功能矩阵看板.html"

text = MD.read_text(encoding="utf-8")

# ---------- 解析功能清单 ----------
domains = []
cur = None
for line in text.splitlines():
    m = re.match(r"^### 域 ([A-I])｜(.+)$", line.strip())
    if m:
        cur = {"code": m.group(1), "name": m.group(2).strip(), "rows": []}
        domains.append(cur)
        continue
    if cur is None:
        continue
    if not line.strip().startswith("| FR-"):
        continue
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) < 5:
        continue
    fid, feat, impl, ui, status = cells[0], cells[1], cells[2], cells[3], cells[4]
    if status.startswith("✅"):
        st = "done"
    elif status.startswith("🔧"):
        st = "fix"
    else:
        st = "new"
    # 清理 markdown 反引号与加粗
    clean = lambda s: s.replace("`", "").replace("**", "")
    domains.append  # noqa
    cur["rows"].append({
        "id": fid, "feat": clean(feat), "impl": clean(impl),
        "ui": clean(ui), "status": st,
    })

total = sum(len(d["rows"]) for d in domains)
cnt = {"done": 0, "fix": 0, "new": 0}
for d in domains:
    for r in d["rows"]:
        cnt[r["status"]] += 1

# ---------- 解析页面清单 ----------
pages = []
in_pages = False
for line in text.splitlines():
    if line.startswith("### 5.1"):
        in_pages = True
        continue
    if in_pages and line.startswith("### 5.2"):
        break
    if in_pages and line.strip().startswith("|") and "`/" in line:
        cells = [c.strip().replace("`", "") for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4:
            pages.append({"domain": cells[0], "routes": cells[1], "desc": cells[2], "status": cells[3]})

data = {"domains": domains, "total": total, "cnt": cnt, "pages": pages}

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>工业 AI 平台 · 功能矩阵看板</title>
<style>
:root{
  --bg:#f6f7f9; --card:#ffffff; --line:#e5e7eb; --line2:#eef0f3;
  --ink:#1a1d21; --ink2:#5b6472; --ink3:#8b939f;
  --brand:#1f5fd0; --brand-soft:#eaf1fd;
  --ok:#1a7f45; --ok-soft:#e8f6ee;
  --warn:#a2620a; --warn-soft:#fdf3e3;
  --new:#a13b6d; --new-soft:#fbeaf2;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"PingFang SC","Microsoft YaHei","Segoe UI",system-ui,-apple-system,sans-serif;
  font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1360px;margin:0 auto;padding:28px 24px 64px}
header.top{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;flex-wrap:wrap;margin-bottom:20px}
h1{margin:0 0 6px;font-size:24px;letter-spacing:-.3px}
.sub{color:var(--ink2);font-size:13px}
.badge-src{background:var(--brand-soft);color:var(--brand);border-radius:6px;padding:3px 9px;font-size:12px;font-weight:600}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .n{font-size:26px;font-weight:700;letter-spacing:-.5px}
.kpi .l{font-size:12px;color:var(--ink2);margin-top:2px}
.kpi.ok .n{color:var(--ok)} .kpi.warn .n{color:var(--warn)} .kpi.new .n{color:var(--new)}
section.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:18px}
h2{font-size:15px;margin:0 0 14px;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:3px;height:14px;background:var(--brand);border-radius:2px}
.layers{display:flex;flex-direction:column;gap:8px}
.layer{display:grid;grid-template-columns:150px 1fr;gap:12px;align-items:center;
  border:1px solid var(--line2);border-radius:8px;padding:9px 12px;background:#fbfcfd}
.layer .ln{font-size:12px;font-weight:700;color:var(--ink2);letter-spacing:.2px}
.layer .chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{border:1px solid var(--line);background:#fff;border-radius:6px;padding:3px 9px;font-size:12px;color:var(--ink)}
.chip b{font-weight:600}
.chip.hl{background:var(--brand-soft);border-color:#cddff8;color:var(--brand)}
.chip.gap{background:var(--new-soft);border-color:#f2d3e2;color:var(--new)}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px}
.tabs{display:flex;flex-wrap:wrap;gap:6px}
.tab{border:1px solid var(--line);background:#fff;border-radius:20px;padding:5px 13px;font-size:13px;cursor:pointer;color:var(--ink2);transition:.15s}
.tab:hover{border-color:#c9d3e0;color:var(--ink)}
.tab.on{background:var(--brand);border-color:var(--brand);color:#fff}
.sbtn{border:1px solid var(--line);background:#fff;border-radius:6px;padding:5px 11px;font-size:12px;cursor:pointer;color:var(--ink2)}
.sbtn.on{background:#eef1f5;color:var(--ink);font-weight:600;border-color:#d5dbe3}
input.search{flex:1;min-width:200px;max-width:320px;border:1px solid var(--line);border-radius:6px;padding:7px 11px;font-size:13px;outline:none;background:#fff}
input.search:focus{border-color:var(--brand);box-shadow:0 0 0 3px #e8f0fd}
.count{color:var(--ink3);font-size:12px;margin-left:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{position:sticky;top:0;background:#fafbfc;text-align:left;font-weight:600;color:var(--ink2);
  font-size:12px;padding:9px 10px;border-bottom:1px solid var(--line);white-space:nowrap;z-index:2}
tbody td{padding:9px 10px;border-bottom:1px solid var(--line2);vertical-align:top}
tbody tr:hover{background:#fafcff}
td.id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--brand);white-space:nowrap;font-weight:600}
td.feat{min-width:210px;font-weight:500}
td.impl{color:var(--ink2);font-size:12.5px;min-width:260px}
td.ui{white-space:nowrap}
td.ui code{background:#f3f5f7;border:1px solid var(--line2);border-radius:4px;padding:1px 5px;font-size:12px;color:#334155}
.st{display:inline-block;border-radius:5px;padding:2px 8px;font-size:11.5px;font-weight:600;white-space:nowrap}
.st.done{background:var(--ok-soft);color:var(--ok)}
.st.fix{background:var(--warn-soft);color:var(--warn)}
.st.new{background:var(--new-soft);color:var(--new)}
tr.grp td{background:#f3f6fa;font-weight:700;font-size:12.5px;color:#334155;padding:7px 10px;border-bottom:1px solid var(--line)}
.empty{padding:36px;text-align:center;color:var(--ink3)}
.legend{display:flex;gap:16px;font-size:12px;color:var(--ink2);margin-top:10px;flex-wrap:wrap}
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}
.pcard{border:1px solid var(--line2);border-radius:8px;padding:10px 12px;background:#fbfcfd}
.pcard .pd{font-size:11px;color:var(--brand);font-weight:700;margin-bottom:3px}
.pcard .pr{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#334155;word-break:break-all}
.pcard .pt{font-size:12px;color:var(--ink2);margin-top:4px}
footer{color:var(--ink3);font-size:12px;margin-top:26px;border-top:1px solid var(--line);padding-top:14px}
@media (max-width:860px){
  .kpis{grid-template-columns:repeat(2,1fr)}
  .layer{grid-template-columns:1fr}
  .wrap{padding:18px 14px 48px}
}
</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <div>
    <h1>工业 AI 平台（智慧云脑）· 功能矩阵看板</h1>
    <div class="sub">需求来源：豆包对话线程 xtlnQmSMZunC1D576 ｜ 落点：当前目录 7 个项目 ｜ 2026-10-20</div>
  </div>
  <span class="badge-src">功能 / 实现方式 / 界面 三列映射</span>
</header>

<div class="kpis">
  <div class="kpi"><div class="n">__TOTAL__</div><div class="l">功能条目</div></div>
  <div class="kpi ok"><div class="n">__DONE__</div><div class="l">已有（接口+页面均在）</div></div>
  <div class="kpi warn"><div class="n">__FIX__</div><div class="l">改造（有基础需扩展）</div></div>
  <div class="kpi new"><div class="n">__NEW__</div><div class="l">新建（无实现）</div></div>
</div>

<section class="card">
  <h2>分层架构与项目落点</h2>
  <div class="layers">
    <div class="layer"><div class="ln">交互层</div><div class="chips">
      <span class="chip hl"><b>智慧云脑</b> platform.html SPA（20+ 路由）</span>
      <span class="chip hl"><b>智慧云脑</b> /ai 对话入口</span>
      <span class="chip">apps/analytics 独立分析 SPA</span>
      <span class="chip">Open WebUI 0.12.3（交互参照）</span>
      <span class="chip">qknow-design-test（8 页设计稿）</span>
    </div></div>
    <div class="layer"><div class="ln">编排层</div><div class="chips">
      <span class="chip hl"><b>智慧云脑</b> modules/workflows</span>
      <span class="chip">Dify 1.17.1（可选运行时）</span>
      <span class="chip gap">GAP-03 工具网关</span>
    </div></div>
    <div class="layer"><div class="ln">执行层</div><div class="chips">
      <span class="chip hl"><b>智慧云脑</b> Node DSH 宿主</span>
      <span class="chip hl">Python business_worker（JSON-RPC）</span>
      <span class="chip gap">GAP-02 常驻规则 worker</span>
      <span class="chip gap">GAP-05 CV 推理服务</span>
    </div></div>
    <div class="layer"><div class="ln">业务服务层</div><div class="chips">
      <span class="chip hl"><b>智慧云脑</b> 34 个路由模块 / 15 插件</span>
      <span class="chip">alerts 设备预警</span>
      <span class="chip">quality 质量检查</span>
      <span class="chip">reports 报表大屏</span>
      <span class="chip">documents 文档演示</span>
      <span class="chip">erp-demo（业务页样板）</span>
    </div></div>
    <div class="layer"><div class="ln">知识层</div><div class="chips">
      <span class="chip hl"><b>智慧云脑</b> knowledge 插件</span>
      <span class="chip">qKnow（kb / kg / kmc，Neo4j）</span>
      <span class="chip">设备预测维护数据包 · 14 册 KB 文档</span>
      <span class="chip">3 类设备 Skill</span>
    </div></div>
    <div class="layer"><div class="ln">数据层</div><div class="chips">
      <span class="chip hl">datasource / collection / writeback</span>
      <span class="chip">业务库 szh_new</span>
      <span class="chip">设备包 2,332,800 行分钟数据</span>
      <span class="chip gap">GAP-01 工业协议网关</span>
      <span class="chip gap">GAP-09 时序语义层</span>
    </div></div>
  </div>
  <div class="legend">
    <span>◆ 蓝底 = 主平台已具备</span><span>◆ 白底 = 可复用外部资产</span><span>◆ 粉底 = 待新建缺口</span>
  </div>
</section>

<section class="card">
  <h2>功能清单（可按域、状态筛选）</h2>
  <div class="toolbar">
    <div class="tabs" id="tabs"></div>
    <div style="display:flex;gap:6px" id="statusbar">
      <button class="sbtn on" data-st="all">全部</button>
      <button class="sbtn" data-st="done">已有</button>
      <button class="sbtn" data-st="fix">改造</button>
      <button class="sbtn" data-st="new">新建</button>
    </div>
    <input class="search" id="q" placeholder="搜索功能 / 模块 / 接口 / 页面…">
    <span class="count" id="count"></span>
  </div>
  <table>
    <thead><tr><th style="width:78px">编号</th><th style="width:230px">功能</th><th>实现方式（真实模块 / 接口）</th><th style="width:170px">界面</th><th style="width:72px">状态</th></tr></thead>
    <tbody id="tb"></tbody>
  </table>
  <div class="empty" id="empty" style="display:none">没有匹配的条目</div>
</section>

<section class="card">
  <h2>现有页面清单（主平台实测路由）</h2>
  <div class="pgrid" id="pg"></div>
</section>

<footer>
  数据来源：本目录实际代码核查（business_apps.json / static_router.py / 各模块 router.py / 各项目目录结构）。<br>
  本方案为产品设计参考，具体实施请结合公司合规与法务要求确认；erp-demo 数据为虚构示例，不得用于真实业务。
</footer>
</div>

<script>
const DATA = __DATA__;
const stLabel = {done:"已有", fix:"改造", new:"新建"};
let curDomain = "ALL", curStatus = "all", kw = "";

const tabs = document.getElementById("tabs");
const mkTab = (code, name, n) => {
  const b = document.createElement("button");
  b.className = "tab" + (code === "ALL" ? " on" : "");
  b.dataset.d = code;
  b.textContent = code === "ALL" ? `全部 ${n}` : `${code} 域 ${name} ${n}`;
  b.onclick = () => { curDomain = code; [...tabs.children].forEach(c=>c.classList.toggle("on", c===b)); render(); };
  return b;
};
tabs.appendChild(mkTab("ALL","",DATA.total));
DATA.domains.forEach(d => tabs.appendChild(mkTab(d.code, d.name, d.rows.length)));

document.querySelectorAll("#statusbar .sbtn").forEach(btn => {
  btn.onclick = () => {
    curStatus = btn.dataset.st;
    document.querySelectorAll("#statusbar .sbtn").forEach(b=>b.classList.toggle("on", b===btn));
    render();
  };
});
document.getElementById("q").oninput = e => { kw = e.target.value.trim().toLowerCase(); render(); };

const esc = s => s.replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const uiHtml = s => s.split(/[、,]/).map(t => {
  t = t.trim();
  return t.startsWith("/") ? `<code>${esc(t)}</code>` : esc(t);
}).join(" ");

function render(){
  const tb = document.getElementById("tb");
  tb.innerHTML = "";
  let shown = 0;
  DATA.domains.forEach(d => {
    if (curDomain !== "ALL" && curDomain !== d.code) return;
    const rows = d.rows.filter(r => {
      if (curStatus !== "all" && r.status !== curStatus) return false;
      if (!kw) return true;
      return (r.id + r.feat + r.impl + r.ui).toLowerCase().includes(kw);
    });
    if (!rows.length) return;
    const grp = document.createElement("tr");
    grp.className = "grp";
    grp.innerHTML = `<td colspan="5">${d.code} 域 · ${d.name}（${rows.length}）</td>`;
    tb.appendChild(grp);
    rows.forEach(r => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="id">${r.id}</td>
        <td class="feat">${esc(r.feat)}</td>
        <td class="impl">${esc(r.impl)}</td>
        <td class="ui">${uiHtml(r.ui)}</td>
        <td><span class="st ${r.status}">${stLabel[r.status]}</span></td>`;
      tb.appendChild(tr);
      shown++;
    });
  });
  document.getElementById("count").textContent = `显示 ${shown} / ${DATA.total} 条`;
  document.getElementById("empty").style.display = shown ? "none" : "block";
}

const pg = document.getElementById("pg");
DATA.pages.forEach(p => {
  const d = document.createElement("div");
  d.className = "pcard";
  d.innerHTML = `<div class="pd">${esc(p.domain)}</div>
    <div class="pr">${esc(p.routes)}</div>
    <div class="pt">${esc(p.desc)} · ${esc(p.status)}</div>`;
  pg.appendChild(d);
});

render();
</script>
</body>
</html>
"""

HTML = (HTML
        .replace("__TOTAL__", str(total))
        .replace("__DONE__", str(cnt["done"]))
        .replace("__FIX__", str(cnt["fix"]))
        .replace("__NEW__", str(cnt["new"]))
        .replace("__DATA__", json.dumps(data, ensure_ascii=False)))

OUT.write_text(HTML, encoding="utf-8")
print("rows", total, cnt, "pages", len(pages))
print("out", OUT)
