# Open WebUI 后端集成

状态说明（2026-10-04）：下文是此前 Open WebUI 迁移阶段的历史记录，不代表当前启动架构。当前唯一对外入口是 `services/dsh_host/`，业务由私有 Python Worker 执行，安装遵循仓库根 `AGENTS.md` 与 `scripts/install_harness.py`。`upstream/` 为本机未跟踪快照，当前 Git 仓库仅保留此目录的元信息和旧桥接代码，不能把下文“纳入项目”理解为新克隆包含完整旧引擎。历史 schema 兼容测试使用 `tests/fixtures/openwebui-0.11.4/` 的固定原文件及许可证，不安装或启动 Open WebUI。以下验证数字保留其历史范围，不用于证明当前 DSH 已验收。

当前项目保留现有聊天界面，将 AI 对话执行主路径迁移到完整的 Open WebUI 0.11.4 后端。Open WebUI 负责原生对话历史、任务、技能和工具调用循环；现有业务服务负责业务权限、数据源访问和业务工具实现。

## 源码与运行环境

- `upstream/`：纳入项目的上游后端源码及根元信息、许可证，共 299 个文件。具体来源和文件摘要见 `UPSTREAM.json`。
- `.venv_openwebui/`：项目根目录下的独立 Python 环境，避免与现有应用依赖冲突。
- `storage/openwebui/`：独立运行数据；日志保存在 `storage/openwebui/logs/runtime.jsonl`。
- Open WebUI 内部服务监听本机 `127.0.0.1:8012`，要求桥接令牌；现有界面和应用入口继续使用 `8011`。
- 上游许可证和声明随源码保留。分发或部署时应遵守上游 `LICENSE`、`LICENSE_HISTORY` 和 `LICENSE_NOTICE` 中的要求。

## 安装与启动

在项目根目录执行 `scripts/install_openwebui.ps1` 安装独立依赖；通过原有 `start.bat` 启动应用时，会启动完整 Open WebUI 后端。内部服务与现有应用使用各自的数据配置，不能将业务 MySQL 配置直接作为 Open WebUI 的运行数据库配置。

## 对话执行路径

`chat_jobs → openwebui.runtime → Open WebUI 原生 HTTP / Socket.IO → 原生工具调用循环`

现有模型网关只承担 Open WebUI 请求的单次模型推理传输，保留现有模型配置和选择入口。业务工具经过现有 `ToolGateway` 执行，保留用户、工作区和工具访问控制。技能按用户及工作区隔离注册到 Open WebUI，原生会话历史和任务落库。

旧 `agent.py` 暂时保留供历史测试等兼容使用，生产聊天入口已不再引用旧 Agent 循环；插件 `test_run` 执行入口也已迁移。现有聊天接口及 SSE 是界面兼容层，不负责替代 Open WebUI 的原生工具循环。

## 已验证范围

- `smoke_openwebui_adapter`：六项通过，覆盖产物引用与沙箱边界、后端故障持久回复、取消终态、现有 API / SSE 与归档同步、工具及原生多轮历史和流式输出、用户与工作区隔离。
- `smoke_openwebui_native`：一项通过，实际运行四轮 `create_tasks → 业务检查工具 → update_task → 最终答复`，验证原生历史和任务落库。
- 后端语法检查：322 个文件通过。
- 启动复核：内部 8012 健康检查为 True，8011 的 `/api/ready` 与 `/ai` 为 200，内部未认证请求返回 403。
- 源码完整性：运行静态目录隔离到 `storage/openwebui/static` 后，299 个上游文件摘要全部匹配；原生错误及取消事件映射补齐后，六项适配验收再次全部通过。

以上验收使用合成模型和工具。原生后端验收运行在独立端口 `18012`，数据位于 `.implementation/openwebui_acceptance/data`，平台侧使用临时 SQLite。本次没有重新开展外部真实模型的业务验收或 1000 条 SQL 验收，不能据此承诺所有业务回答正确。

## 能力边界

完整后端已纳入项目，但外部 RAG、语音、图像等能力仍取决于其服务配置；本次没有将这些未配置能力列为可用。当前直接模型连接模式未启用上游子 Agent 功能。企业数据库分析、文档和工作流通过项目业务工具接入，其业务口径及执行权限仍由现有业务服务管理。

迁移前基线为提交 `49871e2`，标签 `pre-openwebui-refactor-20260922`。这份记录不表示迁移后的修改已提交或推送。
