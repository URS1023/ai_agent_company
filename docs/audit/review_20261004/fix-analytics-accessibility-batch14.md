# 批次十四浏览器复验补充：查询中状态的可访问性

源码提交 `ea81027` 的完整九域首次复验在统计分析套件停止，不能登记为九域全通过。系统、内容、能力、图谱四套通过；统计分析业务步骤完成后，axe 检出缺少名称的进度条以及文字对比度 4.32（门槛4.5）的两个下拉框。其余四套未执行。原始失败证据保留在 `.implementation/full-business-browser/20261004-104340-155c7106`；不是业务库超时，也没有自动重试掩盖失败。

## 修复

`IntelligentQuery.vue` 的查询进度条和同类 `GenerationStatusPanel.vue` 的生成进度条增加明确名称与阶段描述。不确定进度仍保持动画，但移除可访问性 `aria-valuenow=100`，避免向辅助工具伪报已经完成。业务请求与进度逻辑没有修改。

`business.css` 将占位文字颜色由 `#677589` 加深到 `#58677b`，覆盖标准输入与选择器占位文本，适配查询中使用的浅灰背景。没有删除可访问性检查、调低对比度阈值或隐藏进度条。

## 已完成检查

- 使用真实 Vue SFC 编译及 Element Plus SSR 输出核对两类进度条：均含角色、名称、阶段文本，均不含 aria-valuenow。证据 `.implementation/progress-accessibility-dom.json`；这是服务端渲染语义检查，不冒充屏幕阅读器人工测试。
- 完整前端构建通过，入口 `index-ByWtSPuV.js`，递归引用的79个JS/CSS文件存在，其中49个新资产全部由本次入口引用；旧资产保留以支持已打开页面。日志 `.implementation/frontend-accessibility-build.log`。
- 修复后的统计分析真实 Edge 专项 **14 组通过，43.687秒**；本轮该页面严重/关键可访问性失败为空。此轮产品尚未提交，按冻结文件指纹追溯，不宣称它在干净提交上执行。证据 `.implementation/full-business-browser/20261004-105155-86de6476/manifest.json`。

修复后统一门禁正在新证据目录 `20261004-025205-2aa57e7d` 执行，完整九域另按修复提交重验。本段不提前登记为全通过。后端28份文件仍匹配首轮门禁指纹。没有连接业务库/真实模型或重启8011。

## 原始证据SHA256

| 文件 | SHA256 |
| --- | --- |
| `.implementation/full-business-browser/20261004-104340-155c7106/manifest.json` | `5ba6c471778ad0fb3311d09b6e798010094cd44916193dc0c4800333700554ad` |
| `.implementation/full-business-browser/20261004-105155-86de6476/manifest.json` | `a8c0f9da5326e3c819bb20c2962b254b69997adfda6b81ea4efbc14eca86e098` |
| `.implementation/progress-accessibility-dom.json` | `545757ce6a009cc7fa87627ad39d23a02966ca2ef14fb57d7791cb25b5a8e19d` |
| `.implementation/frontend-accessibility-build.log` | `567770b58dd896b256d9d2534743b26829822233f2df0518ad3076b2ebec55f4` |
