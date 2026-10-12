# 智慧云脑 Cloud Brain

企业业务平台，包含 AI 对话、数据分析、工作流、文件与知识库、文档制作、报表大屏、设备预警、质量检查、应用与系统管理。

## 当前架构

统一启动入口为 `backend/start_server.ps1 → backend/run.py → services/dsh_host/launch.mjs`。对外 HTTP/TLS、原生 DSH 会话、模型调用、任务排队、取消与事件订阅由一个长驻 Node 宿主管理。

`backend/app/business_worker/` 是宿主管理的 Python 业务进程，只通过私有双向 JSON-RPC 通信。它以进程内 ASGI 保留既有业务接口，执行查询、工作流、文件渲染、身份验证、数据存取及调度。它不启动 Uvicorn，也不运行旧的 ChatJob 对话循环。业务辅助文本和视觉模型请求反向调用同一 DSH 模型适配；向量嵌入仍属于知识库的专用 provider。

`configurations/business_apps.json` 注册 14 个业务插件，包含既有 34 个路由模块、工具、Skills、前端入口及依赖。现有业务页面继续调用原接口。模型可用工具根据实际业务权限、插件与本轮 Skill 选择构建，HTTP 接口不自动全部成为模型工具。

`apps/web/` 保留静态页面与现有界面外壳；聊天传输、会话状态和过程组件已独立为 `chat-session.js`、`chat-session-view.js`。分析、工作流和大屏组件继续由 `apps/analytics/` 提供。

## 初次安装与启动

需要 Python 3.12，以及 Node 22.19+（22.x）或 Node 24+。推荐使用已有的 `.venv_py312`：

```powershell
uv pip install --python .venv_py312\Scripts\python.exe -r requirements.txt
.\.venv_py312\Scripts\python.exe scripts/install_harness.py
.\.venv_py312\Scripts\python.exe backend/run.py --check
.\start.bat
```

安装器使用 `vendor/` 中的固定 DeepSeek Harness `0.1.7-alpha.2` 源码包，校验完整文件清单，按上游锁文件安装 pnpm 依赖并构建。构建目录是 `.implementation/harness_migration/upstream`；原始源码目录不再是新克隆的运行前提。可用 `--source <目录>` 安装已核验快照，或 `--unpack-only` 只解包校验。

`start.bat` 默认端口 8011，绑定所有网卡；本机地址为 `http://127.0.0.1:8011`。也可使用：

```powershell
.\backend\start_server.ps1 -Port 8011 -BindAddress 127.0.0.1
.\backend\start_server.ps1 -DisableScheduler
.\start_lan.ps1 -Port 8011
.\start_lan_https.ps1 -LanIp <本机IP>
```

启动器只加载配置和检查依赖，再拉起 DSH。业务初始化由 Worker 的显式生命周期调用完成；宿主模型处理器就绪之后才开启后台调度。停止启动器会通知宿主收敛运行、关闭 Worker，并回收本次创建的模型代理。

配置顺序为进程环境优先于项目 `.env.local`。保留 `HOST/PORT`、`SSL_CERTFILE/SSL_KEYFILE`、`SCHEDULER_ENABLED` 与现有数据库/存储配置；`DSH_UPSTREAM_ROOT` 可指定已构建上游，`DSH_HOME` 可指定原生宿主持久化目录。不要复用别的平台的会话目录。旧 `CHAT_ENGINE` 不再切换这个统一启动入口。

## 对话与业务任务

- 发送接口保留原 URL；请求 `Accept: application/json` 后返回受理回执，前端另订阅会话事件流。客户端关闭订阅不会取消任务。
- `request_id` 用于请求幂等；原生 session 保存对话；稳定 `task_id/draft_id` 保存跨轮业务草稿；每次执行生成独立 `run_id`。
- 会话 SSE 提供快照、正文、思考、工具、队列、任务与交付事件。页面刷新后从持久记录恢复。运行结束不截断后面的排队消息。
- 普通参数或渲染错误不冻结后续对话。数据库故障有独立停止语义，未知副作用回执不能盲目重放。
- 任务完成时核对真实工件记录与存储；明确要求的文件缺失会返回 `partial`，正文写“已生成”不能替代交付。
- 通过 `GET /api/chat/capabilities` 获取本部署实际能力。未配置的网页搜索、深度研究，以及未接通的原生 steer/结构化提问会明确显示不可用。
- 管理员在模型管理中配置角色和 `runtime_options`；主对话与业务辅助调用使用统一的协议、思考开关、上下文及输出预算。选择模型应依据实际端点能力，不在代码里假定某个供应商或固定最优档位。
- 主模型声明 `input_modalities:["text","image"]` 时，上传图片通过 DSH 原生附件进入模型；纯文本主模型可用 `read_file` 调用视觉角色识图。视觉调用的模型信息、用量、思考开关与输出预算保留原业务合同。
- 旧平台历史仅在原生会话首次迁移时导入；大历史和工具结果保留完整来源并提供按会话分页读取，减少重复上下文和超长工具结果对后续对话的影响。

旧 `services/deepseek_harness/`、`modules/harness/`、`modules/openwebui/` 和旧 `app.main` 保留兼容资料与测试所需接口；当前 `backend/run.py` 不调用旧聊天宿主。回滚到重构前行为应切回 Git 基线，而不是仅修改 `CHAT_ENGINE`。

## 业务 Skills 与扩展

活动技能位于 `plugins/business/<id>/SKILL.md`，并在业务插件清单中注册。它们说明业务方法、口径与工具使用；任务草稿、执行回执、模型账目和交付校验由代码实现。

新增业务应用时，在 `modules/<feature>/` 实现确定性 service/router，将模块和路径、已实现工具、技能资源、前端入口登记到 `configurations/business_apps.json`。同一清单由宿主和 Worker 消费。前端组件保持原有页面协议，无需进入 DSH 核心修改 Agent 循环。

`list_business_skills`、`read_business_skill_resource` 与技能库存接口使用活动技能目录。已保存旧版本引用的 `backend/app/business_skills/` 资源名保留只读兼容。

## 验证命令

所有本轮端到端验收使用临时 SQLite、临时存储和本机模型桩，禁止连接在用应用端口和外部数据库。它们验证真实运行链路与业务结果语义，不代表真实模型的内容质量、现场查询性能或所有第三方连接器均已验收。

```powershell
.\.venv_py312\Scripts\python.exe backend/compile_check.py
.\.venv_py312\Scripts\python.exe tests/run_isolated_backend.py tests/backend/test_business_worker.py tests/backend/test_dsh_platform_contract.py tests/backend/test_skill_contracts.py
.\.venv_py312\Scripts\python.exe tests/run_isolated_backend.py tests/backend/test_schedule_result_contract.py tests/backend/test_analytics_task_control.py
node --test services/dsh_host/tests/*.test.mjs
.\.venv_py312\Scripts\python.exe -X utf8 tests/dsh/real_platform_smoke.py
node tests/ui/dsh_chat_session.test.js
node tests/ui/chat_stream_recovery.test.js
node tests/ui/workflow_authoring_regressions.js
node tests/ui/analytics_bundle_integrity.js
node tests/ui/dsh_chat_browser.js
```

`tests/run_isolated_backend.py` 仍支持旧业务单元测试并钉定其旧引擎 mock，不能把这类测试等同于新的原生 DSH 验收。浏览器新验收使用本地静态页面与完全拦截的 API 夹具；旧 `npm run ui:check` 依赖运行服务，执行前应准备独立验收环境。

项目业务数据库测试规则见 `AGENTS.md`：默认仅允许本机已核实的测试目标，当前指定业务库为 `szh_new`；不因连接配置存在就允许访问局域网或远程库，不修改在用工作流的数据源绑定。

## 交付与回滚

重构前基线已推送 Gitee `main`：`a9e8a9c433be6e62f05550b0a2782c7f6d51a847`。重构开发位于 `codex/dsh-business-platform`。

源码审查的原始记录在 `docs/code-audit-20260926/` 和 `docs/dsh-conversation-review-20260926/`，描述的是基线状态，不能当作重构后的现状。当前协议、验证结果与迁移记录在 `docs/implementation/`，总交付记录为 `dsh-platform-delivery.md`。回滚代码时使用独立检出并沿用原来的数据库/存储配置，不删除业务文件或原生会话记录。
