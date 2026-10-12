# 大版本十六：大屏交付卡片小字对比度

2026-10-04。本次修改为 `apps/web/assets/platform/ai-workbench.css` 的两处文字颜色，属于静态平台样式，不改数据、任务状态和分析应用构建。

## 原始复现

第七开发单轮 `.implementation/full-business-browser/20261004-123244-d6cf9af9/manifest.json` 的14个功能组通过，包括原话复合任务的两轮续办、实际下载文件、打开静态大屏及删除清理；最后的Axe检查失败。因此该轮完整状态仍是失败，不能把功能分组通过写为全套通过。

`.implementation/ui-results/full-browser-ai-documents-mutbrtio/result.json` 保存原始可访问性发现：四个大屏元信息标签使用 `#77879a`、白底，对比度3.67；一个数据源标签使用 `#718196`、`#fbfcfd`底，对比度3.87。字号分别9px和9.5px，未达到普通小字4.5的要求。

## 修复与取证

两处文字均改为 `#536275`，按sRGB相对亮度公式计算，在上述背景上对比度分别为6.2282、6.0634。不禁用Axe，不过滤失败节点，不修改字号分类。

第七轮的大屏截图在有限入场动画中采集，不能当作稳定视觉验收。下一轮截图使用与主页面一致的1440×960视口，并在业务文本断言后等待报告查看器内有限入场动画自然结束；忽略无限背景动画，等待预算30秒、全套预算900秒均有界。不取消动画或修改产品样式来制造截图通过。

34份源码、测试及门禁文件重新冻结于 `.implementation/batch16-frozen-files.json`。33份旧冻结保存在 `batch16-frozen-files-before-accessibility.json`；其中只有浏览器取证脚本改变，另加入本CSS。新CSS SHA256为 `0681b23186dba7fb3c5bb9bd246f256e8ece0dfba18eaff62537f537ec59e777`，脚本为 `5c4852dc00c13b65509dd4e49b2c16ae4554d53fc808942b51e9be01b15e49b5`。

第八轮 `.implementation/full-business-browser/20261004-123925-4b77640e/manifest.json` 已完整通过，14个业务组、0个Axe问题，144.281秒。最终大屏截图在有限入场动画完成后采集，实际中文、3/5表格清晰；不是对全部Office页的视觉验收。整批门禁和固定提交九域浏览器另行登记。测试仅使用临时SQLite、合成数据和受控回环桩，没有使用真实业务库或模型。
