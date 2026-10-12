# DSH 前端实施与隔离验收

日期：2026-09-26。外壳基线：Gitee `main` 的 `a9e8a9c`。本文件记录实际完成的前端工作；原生宿主、Worker 与真实业务工具的隔离集成验收由各自测试负责。

## 已完成的聊天链路

`app.js` 保留原有导航、消息容器、输入区、附件、模型选择和业务结果卡。新增 `chat-session.js` 管理会话事实与传输，`chat-session-view.js` 负责有实际数据才出现的过程组件；新增样式均限定在 DSH 组件，不改原 `app.css`、`business-chat.css`、`ai-workbench.css`。

| 交互 | 实际行为与契约 |
| --- | --- |
| 发送 | `Accept: application/json` 接收受理回执；客户端 `request_id` 对应用户消息，`run_id` 对应执行；不把 POST 回执当作回答完成 |
| 会话事件 | 按会话订阅 `/conversations/{id}/events?after=`；`session.snapshot` 恢复消息、活动执行和业务投影；某轮 `done` 不关闭会话订阅 |
| 回执与事件竞争 | 开始事件或终态先到时，迟到的 202 不降级活动状态、不复活结束轮次；助手已返回时仍把对应用户消息放在前面 |
| 断线恢复 | 只重新订阅和读取历史，不自动重发提问；回执丢失时按持久 `request_id` 回查；无权限等不可恢复响应停止自动重连 |
| 队列 | 运行期间可继续输入并排队；待执行项可编辑、移除；编辑后的 `queue.updated.user_message` 原位替换用户气泡并同步 text part、保留附件，`task` 同步交付约束；活动任务可请求取消，终态由服务端事件确认 |
| 思考与工具 | 思考与正文分开显示；新尝试和放弃尝试清除旧思考；接受 `call_id/tool_name` 与既有命名；工具失败停止转圈，显示真实错误；原生技能等文本工具回执可展开查看 |
| 业务任务 | 展示真实任务状态与缺少的交付；`partial` 保持“部分完成”；任务卡“继续处理”将明确 `task_id` 放入下一条发送，输入区可移除此上下文 |
| 交付 | 使用真实下载/预览 URL；`artifact_id` 经既有 `/api/artifacts/{id}/use` 转为附件继续使用；消息 parts 与交付投影出现同一工件时不重复展示 |
| 能力目录 | 读取 `/api/chat/capabilities`，显示实际技能与业务插件；选中 Skill 透传 `skill_ids`；不支持的 web/deep/steer/questions 按能力状态禁用或隐藏 |
| 问答组件 | 仅 `questions=true` 且收到请求时出现；校验答复、提交后等待明确解决事件。当前宿主声明 false，浏览器测试通过额外夹具验证组件未来可接入的契约，并非声称宿主已实现问答 |
| 页面切换 | 切换会话撤销旧订阅，旧会话回执不会清空新会话输入或附件；切换后恢复相应历史和活动状态 |

自然语言“修改 PPT 第 2 页”还由 Worker 的任务识别处理；前端提供明确选中既有任务的路径，避免用户需要猜测后端识别范围。模型配置页新增协议、上下文/输出预算、思考开关和推理等级控件；提交 `runtime_options`，保留已有模态与兼容字段。关闭思考与高推理等级冲突、超限/不合理预算在表单提交时拒绝。

## 前次功能审查问题修复

| 问题 | 实际修复 | 回归证据 |
| --- | --- | --- |
| F01 首次保存死锁 | 保存单飞 Promise 先完成，再迁移 `/new` 地址；守卫可串行冲刷后续编辑；同一草稿 id 迁移不重新加载覆盖画布 | 保存期间新增描述，实际两次串行保存、一次路由迁移，两批编辑保留 |
| F02 无限自动重试 | 保存失败清除定时器并暂停自动保存，保留草稿与错误；继续编辑不触发暗中重试；原有更多菜单新增“保存草稿”入口 | 409 后写请求维持 1 次，明确手动保存后才出现第 2 次并恢复正常 |
| F03 正式输入取错版本 | 新增 `usePublishedWorkflow`，正式页和调试面板正式模式读取 `published-revision`；核对发布修订与当前乐观锁版本；失败/过期请求不回退草稿、不允许运行 | 草稿 `batchId` 与正式 `sampleId` 分离，晚到响应被丢弃，读取失败保持不可运行 |
| F04 JSON 精度丢失 | 结构化 JSON、整体 JSON、字段错误统一调用 `parseWorkflowJson`；非法精度草稿不写回，不让其它字段编辑悄悄覆盖原始 JSON | `0.10000000000000000001` 被拒绝并保留原文本；带引号的精确小数可提交 |
| F09 Skill 改名丢配置 | 先继承完整旧 spec，再覆盖四类可见字段 | 推荐工具、资源、示例、输入/输出说明及扩展字段均保留，旧版本对象不变 |

Vue 组件继续使用 Composition API 和 `<script setup lang="ts">`。正式契约加载独立为 composable；已有表单、工具栏和布局样式保持原职责，不引入新全局状态库。

## 可复现验证

所有下列测试均不连接现有 8011 服务，不连接业务数据库，不调用真实模型。Node 状态与 Vue 回归中的接口/路由/计时器为内存替身；浏览器打开本地文件构成的真实页面资源，所有 API 由内存夹具回答，所有非夹具来源请求中止。

| 命令 | 结果 |
| --- | --- |
| `node tests/ui/dsh_chat_session.test.js` | 15 个场景通过：事件去重、尝试替换、工具字段/持久化恢复、跨轮队列、编辑排队消息实时同步、partial、恢复、迟到回执、问答、SSE 分片、回执丢失、权限失败 |
| `node tests/ui/chat_stream_recovery.test.js` | 旧聊天恢复 19 个场景通过 |
| `node tests/ui/workflow_authoring_regressions.js` | 6 组通过：F01/F02/F03/F04/F09 与模型运行参数 |
| `node tests/ui/workflow_json_contracts.mjs` | 4 个已有精度契约测试通过 |
| `npm run typecheck`，工作目录 `apps/analytics` | 通过 |
| `npm run build`，工作目录 `apps/analytics` | 通过，含 `vue-tsc --noEmit`；Vite 转换 2648 模块，构建 27.61 秒 |
| `node tests/ui/analytics_bundle_integrity.js` | 当前入口及 89 项静态依赖完整 |
| `node tests/ui/dsh_chat_browser.js` | 1440×900 与 390×844 均通过；实际点击发送/排队/编辑/移除/能力选择/附件复用/同任务继续/停止，事件结果、断线重订阅、整页刷新恢复通过 |
| `$env:TEST5_E2E_DSH_OFFLINE='1'; node tests/ui/chat_shell.js` | 同一隔离浏览器流程可从现有 shell 验收入口运行；该分支在任何真实健康检查/登录之前进入 |
| `node --check`：`app.js`、`chat-session.js`、`chat-session-view.js`、`model-governance.js` | 通过 |
| `git diff --check -- apps/analytics/src apps/web tests/ui` | 无空白错误；仅工作区 LF/CRLF 提示 |

浏览器每个尺寸还运行原有 19 项滚动布局断言、停止控制及业务问题展示回归。问答可用场景由能力夹具显式开启；宿主当前 false 不会被前端改成 true。

最终交叉核对后追加“排队 PPT 改 Word”回归：保存编辑后立即断言用户气泡与任务标题为 Word、交付约束为 document、旧 PPT 气泡消失，整页刷新后仍只有一条正确用户消息。事件中的 `user_message.id` 是用户消息标识，不使用顶层的助手 `message_id`。此追加修复未修改 Vue，故只重跑受影响的会话测试与两尺寸浏览器，不重复 Vue 构建。

## 外观与构建记录

- 几何基线可复现输入：`tests/ui/fixtures/dsh-chat-shell-baseline.json`。
- 基线/修改后实测：`docs/implementation/dsh-chat-layout-verification.json`。
- 两个尺寸共 48 个 x/y/width/height 比较，最大差异 **0 px**，均无横向溢出。比较范围为 sidebar、composer-wrap、composer-form、prompt-textarea、scroll-root、messages；新增过程组件只在有数据时占用内容空间。
- 本次前后截图保存在 `.implementation/ui-results/dsh-chat-baseline/` 与 `.implementation/ui-results/dsh-chat-after/`；本地生成截图不要求写入 Git。
- 当前 Analytics HTML 入口更新为 `assets/index-r1uAudNb.js`；全局 CSS 仍为 `assets/index-Bi4LxymL.css`。
- Vite 原配置 `emptyOutDir: false` 有意保留历史哈希资源供旧页面继续加载；本次新增相关 JS/CSS 哈希文件并更新入口，没有清空整个目录，也没有删除旧资源。新增文件数量随依赖哈希传播，不能仅保留 Workflow chunk 而漏掉它引用的新入口/共享模块。

## 界限

本轮前端验收证明组件、状态和已约定接口可闭环工作，不能替代原生宿主的真实 DSH 隔离集成测试。web/deep 是否可用、原生问答/steer 是否支持、图像是否实际形成多模态内容均由宿主真实能力和 Worker 内容决定；前端没有用选项名代替功能实现。
