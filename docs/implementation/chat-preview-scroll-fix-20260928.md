# 聊天大屏、任务堆叠与运行中滚动修复

日期：2026-09-28

## 问题与修复

- 大屏结果原先只提供打开按钮，消息重绘还会销毁用户打开的 iframe。现在自动挂载已保存数据的预览，按报表路径和版本保留容器及其祖先，流式正文更新不使 iframe 重载。
- 聊天预览使用 `chat_preview=1`。Vue 查看器把已有组件数据转换为静态快照，关闭组件自动取数、刷新和钻取；保留原报表，不写回配置。支持现有生成器保存的 `chartDataParsed`、`chartData`、`data` 及旧图表 `echartsOption.dataset.source`。未保存数据和内嵌网页组件显示说明，空值与空结果按原值保留。
- 预览具有加载、成功、错误和手动重试状态。报表删除、权限不足和登录失效会向父页面报告，不停留在空白框。
- 已移除的任务区域 `#dsh-session-results` 在会话投影更新时继续清理，不再根据历史 tasks 创建“已完成 / 继续处理”列表。成品附件保留且去重。
- 流式更新原先每次排队执行强制滚到底部，覆盖用户手动上滑。现在记录跟随意图，并在动画帧实际执行时再次检查。滚轮、触摸、键盘及大屏内部滚动意图均纳入处理；回到底部后恢复跟随。
- 消息与内容块按签名更新，保留历史节点及交付卡，避免浏览器滚动锚点改变阅读位置。复制和重试按钮更新事件绑定，避免复用 DOM 后仍引用旧回答或旧问题。
- 聊天 JS/CSS 使用版本参数，并通过精确静态路由返回 `Cache-Control: no-store, max-age=0`。其他平台资源和带哈希的大屏构建文件保持原有缓存行为。

## 验证范围

浏览器测试使用真实聊天代码和真实 Vue 构建产物，接口全部由离线内存夹具回答。未调用真实模型或业务数据库，未修改工作流和数据源绑定。

| 验证 | 内容 |
| --- | --- |
| `tests/ui/chat_stream_scroll.js` | 桌面、手机持续 SSE；实际滚轮、小幅上滑、触摸；返回底部恢复跟随；iframe 内上滑；预览交互状态和节点保留；旧任务清理；复制与重试取最新内容 |
| `tests/ui/chat_dashboard_preview.js` | 双尺寸真实 Vue 指标、表格、ECharts 画布和有效像素；快照不自动查询；同源登录与工作区；错误、重试和中文显示 |
| `tests/ui/chat_dashboard_snapshot.test.js` | 3 项快照转换检查，覆盖空值、损坏数据、旧图表数据和内嵌网页 |
| `tests/ui/chat_output_compact.js` | 双尺寸过程折叠、附件去重、耗时、旧资源卡及任务区移除 |
| `tests/ui/dsh_chat_browser.js` | 双尺寸会话、排队、部分交付、断线恢复、继续使用附件与停止操作 |
| `tests/ui/dsh_chat_session.test.js` | 18 项离线会话状态回归 |
| `tests/ui/chat_stream_recovery.test.js` | 19 项流式恢复回归 |
| `tests/backend/test_chat_asset_cache.py`、`test_analytics_asset_mount.py` | 9 项隔离静态资源检查 |
| `apps/analytics` 的 `npm run build` | Vue 类型检查与 Vite 构建 |

大屏截图：`.implementation/ui-results/chat-dashboard-preview/preview-1440.png`、`preview-390.png`。截图使用明确标记的离线示例数据，不代表用户数据库结果。

## 本机更新

在 `/api/health` 确认 `active_sessions=0` 后重启本项目 8000 服务，仍关闭自动调度和初始报表导入。健康检查通过；页面、聊天脚本、预览脚本和样式与工作区逐字节一致，聊天资源均返回禁用缓存头。大屏入口及其构建资源也已核对。

已打开的旧页面需要刷新一次才能加载新版脚本。在当前会话按 `Ctrl+F5` 后，可检查大屏自动显示、底部无旧任务列表，以及生成中向上阅读历史消息的行为。
