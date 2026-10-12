# 批次十三：统计文本精度展示修复

日期：2026-10-04。范围仅为独立工作树的统计文本展示；没有连接业务数据库、真实模型或在用服务，没有安装依赖，没有生成正式前端构建，没有提交代码。

## 根因与变更

两处根因不同，不能统称为 Number 截断：

1. `QueryResultPanel.vue` 的 `valueText` 先 `Number(value)`，再用最多两位小数的 `Intl.NumberFormat`，导致后端 Decimal 字符串的大整数位、较长小数及极小数丢失。例如 `9007199254740993` 显示为 `9,007,199,254,740,992`，`0.0000000000000000000123` 显示为 `0`。
2. `DataAnalysisDashboard.vue` 的 `formatNumber` 没有执行 Number 转换，但千分位正则作用于整个字符串，错误地给小数及科学计数部分加逗号。例如 `1234.123456` 显示为 `1,234.123,456`。

新增纯函数 `utils/decimalDisplay.ts`，两组件共用：只按字符串分组整数部分，保留符号、小数全部位数和末尾零；科学计数原样保留，不展开异常大指数。缺值显示破折号，均值为空仍遵循原有“未计算”文案。已经作为 JSON number 到达前端的不安全整数增加“约”及原始精度需核对的提示，不声称能够恢复丢失位数。

趋势、投影及均值显示保留完整原值 title；超长文本在原有卡片内省略，不改变组件职责或页面布局。Vue 沿用现有 Composition API，未新增状态层或无必要组件。

## 先红后绿的实际证据

采用本机已有 Vue SFC 编译器、TypeScript、真实 Vue/ElementPlus 及 Edge，直接编译这两个源组件，并通过真实标签页点击进入统计与洞察。测试数据全部是内存夹具；ECharts 渲染、SmartChart 与请求依赖为明确桩。没有覆盖图表坐标绘制或后端传输，不把本次结果称作完整页面到数据库的端到端验证。

- 修复前：`.implementation/ui-results/numeric-display-mut5qgru/result.json`，14 个案例产生 35 个失败检查（包括精度、原值提示及布局）；控制台错误 0。完整输出在 `.implementation/numeric-display-red.log`。原失败证据保留。
- 修复后：`.implementation/ui-results/numeric-display-mut5rh26/result.json`，1 项 Node 测试通过，内含 14 个真实 Edge 案例，每案核验趋势、投影、均值；失败检查 0，控制台错误 0。截图为同目录 `numeric-components.png`。
- 14 案例：大整数、负高精度数、小数不分组、极小数、科学计数小数、巨大指数、正号及末尾零、普通短值、零、null、空字符串、已有 number 小数、不安全 JSON 整数、500 位长整数与小数。
- 长值另检查三处完整 title、实际发生省略及 1200px 视口没有横向溢出；截图已人工查看。
- 网络证据 `network.json`：允许本轮本机请求 2，阻断浏览器背景候选请求 9，应用违规请求 0，未知目标 0。所有外部请求在连接前阻断。
- 清理证据 `cleanup.json`：Windows Job，`active_after=0`，本轮浏览器已回收。
- `npm run typecheck`（`apps/analytics` 目录，`vue-tsc --noEmit`）退出 0。
- `git diff --check` 通过；仅现存 Git 换行格式提示。

可复跑命令（在独立工作树根目录）：

```powershell
$env:TEST5_E2E_PYTHON='E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe'
node --test tests/ui/numeric_statistics_display.test.cjs
```

无需安装。`apps/analytics/node_modules` 使用主流程已核对并建立的现有依赖 junction。正式构建由主流程统一执行。

## 文件与冻结 SHA256

| 文件 | SHA256 |
|---|---|
| `apps/analytics/src/utils/decimalDisplay.ts` | `76144870B67B7E87587C418D9FB62A83E2BA25CF7B90115C85DCF572CCFA6A7D` |
| `apps/analytics/src/components/business/QueryResultPanel.vue` | `6B6A8294DDD8FCF742593FD131F6A27086C958BBA9745427CD219157FA10C216` |
| `apps/analytics/src/components/DataAnalysisDashboard.vue` | `6F73452AA351AE4CE6149BFA8D9042F0EFD3B76737A770B63E9C5848F83227F2` |
| `tests/ui/numeric_statistics_display.test.cjs` | `F734252CF029548DA2AC67612754AF27C48078DCA13AA4A11DABA64B03CBDEF6` |

## 明确保留的边界

- 图形库及原有图表数据坐标仍可能将字符串转成浮点近似；本次只修统计文本，未重写图表。
- 前端收到的 JSON number 若此前已舍入，无法推导原值；只有后端 Decimal 字符串可以完整保留原精度。
- 科学计数保持原样，用户仍可查看原值；不执行任意大指数展开。
- 本次布局证据覆盖 1200px 桌面视口，未宣称已验证所有窄屏或读屏行为。
- 原均值更新依赖组件现有 data watcher，本次未扩大为统计独立更新生命周期改造。

以上四个源码/测试文件已完成并冻结，等待统一构建与门禁。
