# 大版本批次十六：前端图表原值、精确聚合及保留字分类

2026-10-04。工作树 `major-delivery-acceptance`，开发起点 `7922089`，最终清单记录 HEAD `7c7174f96a283c54da659d719b1632ff7e73eb7f`（由其他并行工作推进，本代理没有提交或推送）。所有产品源码与资产现已冻结，已通知复合文档负责人可以启动其独立验收。

## 授权与边界

- 仅修改 `SmartChart.vue`、`JimuChart.vue`、`dashboardData.js`、`aiChartOption.js`、`snapshotChartOption.js`，新增纯工具 `chartPrecision.js` 与两份专用 UI 测试。后续经主代理放行生成 `apps/web/assets/analytics` 构建资产；旧已追踪资产没有删除。
- 没有修改 backend、`full_browser_ai_documents_acceptance.js`、统一门禁、README 或版本记录；没有安装依赖，没有重启/连接在用 8011/8012，没有业务数据库/模型请求，没有外网转发。
- 已阅读仓库 AGENTS.md，应用 `C:/Users/urdcbk/.agents/skills/vue/SKILL.md`、`vue-best-practices/SKILL.md` 及其 reactivity/sfc/component-data-flow/composables 四份核心引用。保留 Composition API；无新增响应式业务状态，精度运算为纯工具；ECharts 实例改用 shallowRef，避免第三方实例深层代理。
- 组件职责未扩展：SmartChart 继续负责分析页图型与生命周期；JimuChart 继续负责大屏装配和点击下钻。原 props 不变，新增可选 `legend-select` 事件用于向调用方返回已反解的原始业务名与选择状态。

## 产品修复

1. 查询数值分别保存原始十进制文本与有限浮点坐标：`exactValues/exactValue` 用于 tooltip/标签，`value/data` 用于 ECharts 坐标；保留尾零、科学计数、大整数。已经作为不安全 JSON number 到达的整数标为“约”，不伪造恢复精度。
2. SmartChart 的柱/饼分类桶使用 Map；BigInt 系数与小数位对齐后精确相加。大整数相消得到 `1`，`0.10+0.20` 得到 `0.30`。全部缺值分组保持未知，部分有效值的合计显示有效记录数，空白/布尔/非有限数不能成为零或数值列。
3. 快照、普通/AI 图表、仪表和雷达保留原值通道；仪表未知值不补零；雷达不再取绝对值/把缺失点补零，均值基线明确标为近似。散点同样保留各维原值。tooltip 使用 richText 纯文本，不插入 HTML 或 marker；HTML 洞察文案中的业务字段经过实体转义。
4. 删除普通 Jimu 模板中按奇偶行翻转正负号及固定 `52/31/17%`、`82%` 制造系列的示例变换。Negative 保留原始符号；Stack/Multiple/Percent/Mix 只使用实际系列。Percent 类型本批按实际值展示已有系列，不声称已计算百分比，不引入固定分母或伪百分比。
5. ECharts 非坐标系图型统一按 item 提示；修复真实雷达已有原文但沿用 axis 触发导致看不到精确提示的问题。
6. 分类保留字需要处理绘图库内部边界。仅用 Map 保留 `__proto__` 后，旧 zrender `LRU._map={}` 会把文本缓存查询命中 Object.prototype，进而写入 prev/next；旧 LegendModel 也直接读写普通 selected 对象。新增仅针对原型保留字的内部标识，保证与已有业务名不冲突；数据点、轴、图例等在 ECharts 内使用内部键。绘制 formatter 显示原文并使用不可见分隔符隔离文本测宽缓存；原始 rows、下载/导出数据不改写，点击与 `legend-select` 出口反解原始业务身份。没有修改 node_modules、全局原型或删除污染字段。

## 原始红与诊断证据

文件均在 `.implementation/chart-precision-batch16/`，保留失败原文，没有覆写成绿日志。

| 文件 | 实际结果 |
| --- | --- |
| `red-node.log` | 初始 6 例全红：原文缺失、未知仪表变零、大整数相消为零/小数浮点尾差、特殊类别消失、布尔空白误判 |
| `red-jimu-transforms.log` | 夹具 setup 失败：漏传 defineExpose；不是产品红 |
| `red-jimu-product.log` | 修正夹具后从 `git show 7922089` 读取真实 Jimu 源码：Negative `7→-7`，Stack `1→3` 系列 |
| `red-jimu-four-paths.log` | 同一基线下四条独立产品红：Negative、Stack、Percent、Mix（Mix 1→4 系列） |
| `browser-first-snapshot.log` | 初次 Getter/null 注入失败，最初仅定位在 wait 路径；后续对照证实不能归为单纯夹具错误 |
| `injection-blank.log` / `injection-special-baseline.log` | 空白页和 7922089 原始组件特殊类别对照的 waitForFunction 均通过 |
| `injection-special-current.log` | 只做 Map 精确聚合后的中间实现，特殊类别真实渲染新增 Object.prototype.prev/next，waitForFunction 失败；证据目录 `chart-precision-mutaahhw` |
| `browser-final-boundary-check.log` | 单纯显示 formatter 隔离仍有遗漏的实际红：SmartChart 柱图后原型 descriptor 改变，按首失败停止 |
| `red-special-legend-click.log` | 真实鼠标点击 `__proto__` 图例后没有变为未选中；证据目录 `chart-precision-mutaeimj` |
| `browser-remaining-first.log` | 雷达 tooltip 真实渲染缺原文；保留后修正 item 触发，并单独复验 |
| `browser-names-after-mapping.log` | 图例点击已完成，但直接经 CDP 返回 Vue Proxy 被序列化为 `{0:{}}`，改为显式 JSON 快照比较原始行 |
| `browser-names-proxy-serialization-fixed.log` | 图例第二次点击回执读取过早；改为等待确切同名选择事件与期望布尔值，不过滤错误 |

## 最终验证

- `node --test tests/ui/chart_precision.test.cjs tests/ui/snapshot_chart_option.test.js tests/offline/frontend_audit_fixes.test.mjs`：**27/27 通过**，`green-node-final.log`。其中新增 13 项、既有快照 8 项、既有离线前端 6 项；没有替换原先数值/布局断言。
- 真实 Vue + ElementPlus + ECharts（没有替换图形库）在隔离 Edge 中分两小组检查，合计 **13/13 通过**。第一组 `browser-names-event-wait.log` / `ui-results/chart-precision-mutagt28` 为 4 项：五种类别图例各自鼠标取消和恢复、SmartChart 柱图与饼图精确聚合、Jimu 真实柱图点击原文映射。第二组 `browser-remaining-after-mapping.log` / `ui-results/chart-precision-mutahfjk` 为 9 项：快照/普通/AI tooltip 大整数尾零、普通/AI 仪表雷达、Negative/Stack/Percent/Mix。
- 浏览器检查读取真实 ECharts getOption 与实际 zrender 文本显示列表；图例与柱图通过鼠标点击；每项对比绘制前后 Object.prototype 的所有自有属性 descriptor（包括内建函数值的自有 descriptor），没有新增/修改。SmartChart 两图均再次执行原 `page.waitForFunction` 严格断言，未再出现 Getter/null。
- 两次最终浏览器记录均 `errors=[]`、`unexpected_requests=0`、`application_blocked_requests=0`、`upstream_errors=0`，各只有 2 次获准本轮静态回环请求。浏览器背景域请求分别 10/15 次均被拒绝；第二组有一次被拒绝连接的客户端 ECONNRESET，日志保留，未重试。Windows Job 清理均 `active_after=0`。
- `npm run build`：`vue-tsc --noEmit && vite build` 最终成功，`build-final.log`，Vite 15.22 秒。第一次中间构建不作为最终证据。最终构建前后 6 个源码 SHA256 恒等，完整核验由 `capture_assets.cjs` 记录。
- 所属产品文件 `git diff --check` 通过（只有仓库 CRLF 规范提示）。

## 稳定构建与 SHA

完整清单：`.implementation/chart-precision-batch16/source-assets-manifest.json`，包含 6 个源码、2 个测试、88 个本次构建输出、98 个新增未追踪资产（包括保留的中间构建产物）、入口引用与摘要；2867 个旧已追踪资产全部仍在，删除数 0。不删除旧 chunk。

| 对象 | SHA256 |
| --- | --- |
| `chartPrecision.js` | `715af71dfa9a4e3455939663030b8c763cda2cee6fcf86bac28fb7e59939be50` |
| `dashboardData.js` | `035c0a4829f27e4995194747f0601fd3a8bda8f8733fcfb75b846574c67ecc0a` |
| `aiChartOption.js` | `e5a9bbbb28a187d9157717418b5dfcd0b7f181d2d45b0d8661272a93e5deb0dc` |
| `snapshotChartOption.js` | `5984bdb7bc961d7ec42811270ef1699eeaee76e6593ff57842e07980e8ef44b8` |
| `SmartChart.vue` | `d60e1e45a3f75c4be9fcbdd4507955aca14a6c8a81860c866e1682e60aa33883` |
| `JimuChart.vue` | `172fd63aefcbdf92e28237ad6fc1a497d6dc7e6b21b07003873125331274ea8f` |
| `apps/web/assets/analytics/index.html` | `28459b26bc3fbeb966ad749865b8a8d1131b0fb4d8868b18db6f3d1729ec71d6` |
| 入口 JS `assets/index-CkMAeOxi.js` | `c3fd1e0db7e836ab0a77a3871b31bb0a42703a11a2b94326c4c59837c4382b0e` |
| 入口 CSS `assets/index-R-IMtGv9.css` | `01d16f4f874dd1e50154cd658f8de7125f4505298ef6b45198e0647eff1d35e2` |

## 限制与未声称完成的部分

- 本批没有运行统一 gate、九域全量或复合文档完整 Host 流程；由主代理/父代理分别负责。真实浏览器在内存样例上加载并编译实际 SFC，最终构建资产由 source/hash 清单证明一致；不能写成在用服务已加载。
- 没有在本批额外遍历所有地图/词云/任意自定义富文本模板；已验证上述实际使用的轴、图例、tooltip、普通/AI 雷达及点击入口。没有将任意图表配置都已验收作为结论。
- 输入文本与指数/小数位有 4096 上限，超过预算的内容保持未知；有限绘图坐标无法表达的溢出或非零下溢为 null，保留预算内原文。浮点坐标本身继续是近似图形，不声称像素位置具有十进制精度。
- Percent 模板未实现新的百分比业务口径；仅显示已返回的真实系列。雷达均值基线明确近似；实际业务值与分类求和仍走精确通道。
- 本代理没有提交、推送或重启在用服务。最终资产已交出冻结窗口，之后仅新增本报告和只读审计证据。

## 冻结后只读资产审计与交接清单

按主代理补充要求，使用 `audit_assets.cjs` 逐文件读取工作区字节与 `git show HEAD:path` 原始对象，未执行 checkout、restore、删除、git add 或再次构建。只对单次 Git 命令传 `core.longpaths=true`，没有改仓库配置。完整证据保存在 `.implementation/chart-precision-batch16/asset-audit.json`。

| 审计对象 | 结果 |
| --- | --- |
| Git 状态显示 M 的 29 个旧编译 JS/CSS | SHA256 与 HEAD 原始字节完全相同，不只是忽略空白后相同 |
| `apps/web/assets/analytics/index.html` | 唯一有实质内容差异的已追踪构建资产：入口改为最终 `index-CkMAeOxi.js`；同时有换行差异 |
| 全部 26 个 jimu-screen-reference JS | 22 个字节完全相同；下表 4 个仅 LF/CRLF 差异，代码正文相同 |
| 最终构建输出与入口引用闭包 | 87 个 JS/CSS/字体依赖，加 index.html 对齐最终构建 88 个输出；无缺失、无未分类输出 |
| 98 个新增未追踪资产 | 50 个是最终入口直接或间接引用；48 个仅为中间构建产物，仍留在磁盘 |
| 冻结一致性 | 6 个源码、2 个测试、入口、88 个输出和98个新增资产 SHA256 均与冻结清单一致；旧追踪资产删除数 0 |

换行差异文件均位于 `apps/web/assets/analytics/jimu-screen-reference/积木报表 BI工作台—免费数据可视化_files/`：

| 文件 | HEAD 字节数 → 当前字节数 | 当前 CRLF 数 |
| --- | --- | --- |
| `ChartViewModal.chunk.js` | 1163690 → 1163692 | 2 |
| `ChartViewModal~JCommonTable~JTable.chunk.js` | 943566 → 943569 | 3 |
| `chunk-vendors.chunk.js` | 4242617 → 4243000 | 383 |
| `hm.js` | 29903 → 29970 | 67 |

构建提交候选的精确路径清单是 `.implementation/chart-precision-batch16/final-build-submit-paths.txt`，共 51 行（50 个最终新增产物与 index.html）。`.implementation/chart-precision-batch16/intermediate-build-retain-paths.txt` 列出48个中间产物；不属于当前入口依赖，按要求保留、不删除、不加入本批候选清单。完整 `asset-audit.json` 的 `newAssets` 为每个新增产物记录 `finalEmitted`、`reachableFromFinalEntry` 和 SHA256，`edges` 保留静态 import、动态 import、Vite 预加载表及 CSS 字体引用边。

这里只做本地构建文件的引用闭包核对，没有声称运行了全部路由。审计脚本初稿把业务元数据里的 `/jimu-screen/iconlib/*.png` 当成相对模块路径，断言失败；经核对改为仅解析构建 JS/CSS 与 CSS 字体，未修改任何产品或构建内容，也未将该脚本错误计作产品红。

全原型比较及事件反解的证据对应 `tests/ui/chart_precision_browser.cjs` 冻结 SHA `97022a666806a08933dfcd7c4628f875be7c51f3be665e030c7de26d0c102a8a`：第46–60行对全部 Object.prototype 自有 descriptor 的 value/get/set/writable/enumerable/configurable 做 Object.is 对照，并检查内建函数值的自有 descriptor；第145–171行对5个类别真实点击取消/恢复、断言原始 name 和独立 selected；第212–225行检查柱图点击原始名、原始行和完整原始数据。最终 `ui-results/chart-precision-mutagt28/result.json` 留存四项通过与空 errors/failures，另一组 `chart-precision-mutahfjk/result.json` 留存九项通过。成功记录保留的是这些严格断言结果，未另序列化函数对象或保存可重放的原型对象快照；不能将其描述为保存了完整运行时对象。
