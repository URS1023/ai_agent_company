# B16 图例浏览器验收时序修复与证据

本轮工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-delivery-acceptance`。读取及修改起点 HEAD：`7ed8f58b9fd81a151bc0d078216e822f9670aa15`。只修改 `tests/ui/chart_precision_browser.cjs` 及本报告/ignored 原始诊断证据；没有修改 B17 树、产品源码、构建资产、门禁、正式文档，没有提交或推送。

## 原始失败与归档

- 首轮 18 阶段门禁在第 12 阶段 `chart-precision-browser` 停止。原始目录 `.implementation/quality-gates/chat-delivery/20261004-044214-4a0297e4` 保持原样；此前 11 阶段历史没有重写。
- 原失败 UI 目录 `.implementation/ui-results/chart-precision-mutcpjgg`：第一图例检查出现 `Waiting failed: 1500ms exceeded`；`errors=[]`；原截图显示 `__proto__` 灰色，取消选择已生效。
- 根代理此前新增观测的单例目录 `.implementation/ui-results/chart-precision-mutct03g` 保留：首项点击 `y=751`，下一项 `constructor` 点击 `y=854`；图例项间距 19 像素，额外 84 像素来自上方布局变化。这次单例绿不是原门禁通过。
- 修改前原脚本字节归档：`.implementation/chart-precision-batch16/legend-timing/chart_precision_browser.before.cjs`；原测试 SHA256：`97022a666806a08933dfcd7c4628f875be7c51f3be665e030c7de26d0c102a8a`。
- 修改前产品/资产指纹：同目录 `before.json`。修改后比对：`frozen-after.json`，6 个产品源码加 88 个最终构建资产合计 94 个，94 个字节指纹相同、0 个变更。

## 定位结论及读取范围

这是测试台布局就绪条件与坐标复用问题，本轮没有发现需要改动冻结产品的证据。

1. `SmartChart.vue:140-172` 在分析开始设置 loading，300 ms 后清除；模板 30、44、46 行分别切换加载占位、图表容器和洞察区。图表及加载占位同为 450 px（634-659），洞察区额外 margin-top 20 px（662-664）。本样例洞察内容高 64 px，总增量为 84 px。
2. 原 `chartRead` 只等待 Vue nextTick、80 ms 和 100 ms；首项可能在 SmartChart 的 300 ms 加载延迟结束前点击。原 149-164 行计算一次坐标后，用相同点完成取消和恢复；期间下方 Jimu 整体下移，恢复点落空。
3. `JimuChart.vue:817-884` 读取真实 ECharts 初始化、图例事件映射、setOption/resize 与 ResizeObserver 路径；下方图表的宿主位置变化来自上方 DOM 布局，和图例选择数据本身无关。原渲染动画配置保持不变；已有 chartRead 设置 animation=false 的行为保持原样。
4. 只读现有 ECharts `LegendView` 的 `getContentGroup`、文字节点与 `__legendDataIndex`，以及 zrender 的 `findHover`，用于观测真实图例边界和鼠标命中，不修改依赖。
5. 只读本轮隔离浏览器守卫与既有网络结果确认范围。Python 使用 MAIN `E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe`，没有安装依赖。

正常完整运行实际记录：SmartChart 高度 `553 -> 637`，Jimu 顶部 `589 -> 673`；loading `true -> false` 同时洞察区 `false -> true`，下移量 84 px。见最终 UI `legend-clicks.json` 的 `layout`。

## 测试修改

- 每次取消和恢复都重新等待 SmartChart 加载结束、可见图表与位置采样稳定，再从当前 ECharts 图例文字及当前宿主矩形获取新坐标。
- 将匹配限定在真实 legend content group，并同时校验 DOM 命中与 zrender 命中；不从整个展示列表任意匹配同名文字。
- 每次选择要求产生新的原名 `legend-select` 事件，并核对实际 zrender 点击命中该图例、`event.isTrusted=true`、一次点击对应一次命中记录。保留特殊类别独立切换、其他类别不串、原始行数据不变以及完整原型描述符比较。
- 保存 `legend-clicks.json`：位置、宿主矩形、SmartChart 布局变化、期望选择值、真实选择事件及实际命中。保留图例全恢复截图。
- 加入显式环境变量 `TEST5_CHART_LEGEND_LAYOUT_CONTROL=1` 的测试台故障注入分支：用测试台 CSS 隐藏/恢复已经存在的洞察区占位，确定性制造相同幅度的位移。这不是用户操作路径，也不宣称用户手动隐藏洞察是生产问题。
- 不用 dispatchAction 代替图例点击，不删除首项，不放宽 1500 ms 选择等待，不改变子进程总 90 秒预算。默认仍执行原有 13 项业务检查。

## 本轮实际验证

按顺序只做一次确定性专项对照，再做一次无筛选完整 13 项；没有以重复运行碰运气。

### 1. 确定性对照：`chart-precision-mutcwyax`

命令环境：`TEST5_E2E_PYTHON` 指向 MAIN Python；`TEST5_CHART_LEGEND_LAYOUT_CONTROL=1`；`TEST5_CHART_CASE_FILTER=图例保留字`；执行 `node tests/ui/chart_precision_browser.cjs`。进程成功，检查 1/1，工具记录墙钟约 6.20 秒。

- 占位隐藏时：真实取消首项，坐标 `(620.416650390625, 751)`，Jimu top=589。
- 占位恢复后：首项当前坐标 `(620.416650390625, 835)`，Jimu top=673，确定性位移 84 px，与真实初始加载相同。
- 旧点 probe `hitLegend=null`；实际鼠标点击也记录 `name=null`、局部 y=78、`trusted=true`。没有新增图例选择事件，首项仍为 false；原有 1500 ms 恢复选择等待发生预期 `TimeoutError`，保留在证据内。
- 使用修后定位重新真实点击：局部 y=162，实际命中 `__proto__`，原名选择事件变为 true；之后原有五个类别均完成取消与恢复。
- 已查看 `legend-control-stale-click.png` 和 `legend-control-relocated-click.png`：前者首项灰色且缺少该扇区，后者首项恢复颜色及对应扇区。
- 原始日志：`.implementation/chart-precision-batch16/legend-timing/control.log`。
- 对照 `legend-clicks.json` SHA256：`953631f0c1c751d83bd8b75777e0e1c8ed0cfa2fcec298fe9290287c94d12bc1`。

### 2. 正常完整专项：`chart-precision-mutcy54u`

清空故障注入、用例筛选、注入诊断和基线环境变量，使用相同 MAIN Python 执行正式脚本。13/13 全通过，`errors=[]`、`failures=[]`，工具记录墙钟约 8.72 秒。

- 首项及后续四类合计 10 次真实图例点击、10 条选择事件、10 条命中，全部 trusted，完整原型比较差异为空。
- 三种图表路径的真实 tooltip、大整数/尾零、仪表未知值、雷达原文、SmartChart 柱/饼精确聚合、保留字数据点击原名/原数据，以及 Negative/Stack/Percent/Mix 原始数值及系列检查均通过。
- 原始日志：`.implementation/chart-precision-batch16/legend-timing/full-13.log`。
- `result.json` SHA256：`ebfc8f9dc146d2571ede176a292381b05c810cc367bebc7f30477346b5b69c50`。
- `legend-clicks.json` SHA256：`81fb1ae0a7b21f58e8672e9fe9fc0eb64573f87be19e5041fb5d2121fc108f76`。

两轮只使用各自创建的回环随机静态页和内存样例；每轮 allowed_requests=2，application_blocked_requests=0、unexpected_requests=0。浏览器背景网络请求均由守卫阻断，各轮 15 次；没有转发外网。对照轮另有 1 次 client disconnect ECONNRESET，upstream_errors=0；正常完整轮两项均为 0，均通过既有网络证据检查。Windows Job 清理证据两轮均为 active_before=0、active_after=0。没有 DB、业务 API、模型或 8011/8012 访问。

`node --check tests/ui/chart_precision_browser.cjs` 和 `git diff --check` 通过。工作树另有根代理生成的 `docs/audit/review_20261004/validation-batch16-first-failed.json`，本轮未修改。

## 最终冻结与边界

- 正式测试 SHA256：`aee0ce3d92e43adb051898e8eaa2e7676ca9b258db49047812103d159f0e6df0`。
- 6 个产品源码及 88 个最终构建输出保持字节冻结。原测试、原门禁失败及所有新证据都保留。
- 定位观测使用当前已安装 ECharts/zrender 的内部图例组结构；将来升级依赖需重新核对测试观测适配。
- 本轮未重跑 18 阶段统一门禁，不能将这次专项 13/13 记为完整门禁通过。首次第 12 阶段失败历史仍有效；最终整轮由根代理另行冻结并执行。
