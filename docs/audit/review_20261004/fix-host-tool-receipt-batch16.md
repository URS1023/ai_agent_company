# 批次十六补充：保留真实工具结构化失败回执

2026-10-04，工作树 `major-delivery-acceptance`，源码基线 `7c7174f` 加本批未提交修改。该缺陷由新增巡检复合浏览器实际触发，并非只由源码推断。

## 复现与影响

第三开发单轮 `.implementation/full-business-browser/20261004-120305-40d679d9/manifest.json` 中，HTTP查询与饼图成功，Excel导出返回带 `error_code=validation_error` 的结构化失败。宿主 `installBusiness` 先发布含完整 `result` 的 `tool.completed`，为保留原生工具失败语义抛出 `Error(JSON.stringify(result))`，随后被自身 catch 捕获，再发布没有 `result` 的 `tool.failed`。业务消息投影按调用身份更新，第二事件将原始失败替换为只含JSON字符串的 `error_summary`。

这不仅影响可读日志，也会丢掉 `presentation_progress`、缺页列表、未知外部操作的不可重试标记等机器可用字段。测试脚本不能靠再次解析错误字符串将此冒充正常产品协议。

## 修复

`services/dsh_host/sessions.mjs` 在得到合法Worker对象回执后记录已取得回执事实。后续原生工具失败、事件发布失败或用户取消均不另发无结构失败来覆盖它。Worker请求本身失败或返回非对象/数组/空回执时仍发布工具失败；调用身份和abort监听仍在finally清理。

业务失败继续抛给真实原生ToolRuntime，保留 `isError=true`；没有把失败当成功，也没有为取得成功回执重发工具。结构化终态沿用原有事件协议：`tool.completed` 表示调用返回，由 `data.status` 与 `result.status` 判断业务成功、失败或取消。

## 验证

- 新 `services/dsh_host/tests/business-tool-receipt.test.mjs` 通过实际宿主工具注册及真实原生ToolRuntime执行。仅Worker传输和事件发布是内存桩，无模型、业务库或在用服务请求。
- 修前PPT缺页用例1项真实失败：事件序列多出末尾无结构 `tool.failed`。原始日志 `.implementation/host-tool-receipt-before.log` 保留。
- 修后11项新增及37项既有宿主恢复/事件投影用例合跑，**48项通过**，296.60毫秒。日志 `.implementation/host-tool-receipt-after.log`。
- 新增覆盖缺页进度、未知操作、取消及旧isError回执，成功原文，Worker传输错误，已取得回执后的发布失败/取消，以及三类非法回执。每种失败仍要求原生工具报错，不能仅检查事件数。

这不是完整浏览器验收。HTTP查询历史导出缺口由另一专项修复，复合场景重跑与整批门禁另行登记。本补丁也不提供事件存储跨网络精确一次保证；事件发布失败仍向原生运行时传播，持久结果与运行收口遵循已有恢复合同。

独立审查指出，`tool_not_available` 等Worker提前返回并未统一保存ToolCall回执，因此“已取得回执”不总等于“已落存”。注释已修正；若这种提前返回的首次事件发布在写入前失败，结构回执仍可能仅存在内存，本补丁不宣称可以完整恢复它。新增发布前即抛错的用例只证明原生失败继续传播、调用清理、没有再执行工具或假称持久成功；没有以该测试通过冒充持久恢复问题已关闭。该边界与既有Worker传输失败后失败事件再次失败的双故障分别列入后续事项。

补充用例后的最终专项为12项新增及37项既有，**49项通过**，303.70毫秒，日志 `.implementation/host-tool-receipt-final.log`。此前48项记录作为开发步骤保留，两次组合不相加。最终源码及测试会进入整批统一冻结和宿主阶段门禁。
