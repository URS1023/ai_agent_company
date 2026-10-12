# Repository Guidelines

## Project Structure & Module Organization

- `services/dsh_host/` 是唯一对外宿主，负责原生 DSH 会话、模型、队列、HTTP/TLS 与事件流；`backend/app/business_worker/` 是私有 stdio 业务 Worker，不启动 Uvicorn 或旧聊天执行循环。
- `configurations/business_apps.json` 是业务应用路由、工具与 Skills 的统一注册目录；业务实现仍按 `backend/app/modules/<feature>/` 组织，`plugins/business/` 保存可加载的业务技能。
- `backend/app/providers/tool/` implements the tool gateway and HTTP, database, MCP, built-in, and sandbox providers; `backend/app/workers/` contains isolated workers.
- `backend/run.py` 将现有启动脚本接到 Node 宿主；`--check` 只校验安装，不连接业务库。语法入口为 `backend/compile_check.py`，隔离测试在 `tests/`。
- `apps/web/` is the static Chat/Codex-style frontend; `tests/ui/` contains browser checks. `需求文档/` holds specifications, `参照/` the visual baseline, and `storage/` runtime data.

## Build, Test, and Development Commands

Install dependencies with `uv pip install --python .venv_py312\Scripts\python.exe -r requirements.txt`. Start with `start.bat`, or run `cd backend; ..\.venv_py312\Scripts\python.exe run.py`; both serve `http://127.0.0.1:8000`/`8011`（启动统一走 `backend/start_server.ps1 → backend/run.py`）。

初次克隆先执行 `python scripts/install_harness.py`，从仓库携带的固定 DSH 源码包安装依赖与构建；需要 Node 22.19+（22.x）或 24+。旧 `CHAT_ENGINE` 配置不再切换这个启动入口。

- `python backend/compile_check.py` parses backend modules for syntax errors.
- `python tests/run_isolated_backend.py tests/backend/<file>` runs isolated integration tests（内存 SQLite、禁外网/业务库、引擎钉定，见 `tests/run_isolated_backend.py`）；装依赖后回车。
- `node tests/ui/chat_stream_recovery.test.js` 校验前端流式恢复/阶段事件（离线 Node VM）。
- `python tests/dsh/real_platform_smoke.py` 验证真实 Node DSH 与 Python Worker，使用临时 SQLite 和本机模型桩，不连接业务库。
- `node --test services/dsh_host/tests/*.test.mjs` 验证宿主与原生协议。
- `node tests/ui/dsh_chat_session.test.js` 验证会话状态、队列和交付组件的离线合同。
- `npm run ui:check` 覆盖桌面/移动端 shell 几何与主要页面（需先启动隔离验收服务，参考 `tests/ui`）。
- `.venv_py312\Scripts\python.exe scripts/verify_chat_delivery.py` 是 AI 会话、模板大屏、查询 PPT 和跨轮交付的统一回归入口；修改这些链路后运行，失败不得将相应能力报告为已通过。默认包含前端构建、合成数据多模板/多尺寸浏览器矩阵与真实 DSH/Worker 隔离流程，禁止连接业务库或真实模型；不能只用某个历史会话截图代替通用回归。

## Coding Style & Naming Conventions

Use UTF-8 and four-space indentation in Python. Keep modules and functions `snake_case`; preserve the router/service/provider split and add type hints to public interfaces. Match the surrounding vanilla-JS formatting; no formatter or linter is configured.

## Testing Guidelines

### 用户明确的数据库测试约束（2026-09-11）

- 默认只允许本机数据库测试；本项目指定本地 `szh_new`。局域网和远程数据库均禁止用于测试，除非用户在当前任务中明确指定非本地目标与测试范围。已有凭据、已保存的数据源、历史授权、“副本”名称、只读查询或泛泛的测试请求都不构成授权。
- 先核实实际主机、端口、库名和本地服务归属。本机代理/隧道转发到非本地数据库不算本地；不确定时停止，不得自动回退到非本地数据源。
- 限制覆盖 SQL/脚本/性能测试/EXPLAIN ANALYZE，以及浏览器、API、工作流和定时任务间接产生的数据库测试；不得为了测试修改在用工作流的数据源绑定。
- 从小范围、少量轮次、单条业务查询并发开始，设置超时和总工作量预算，记录连接/查询次数。出现超时、断连或资源异常立即停止后续测试并核对残留查询，不得自动重试、恢复测试循环或扩大超时。
- 全局同类约束还记录在 Codex 用户目录的 `AGENTS.md` 中。用户明确授权的范围内继续工作，无需重复索要确认。

Name backend checks by intent as `tests/backend/test_<area>.py`（经 `tests/run_isolated_backend.py` 隔离运行）；浏览器验收放 `tests/ui/`。Cover success, validation, authorization, and provider-failure paths. UI changes must keep the Codex shell geometry and update `tests/ui/chat_shell.js` when behavior changes. No coverage threshold is defined.

## Commit & Pull Request Guidelines

Use an imperative scoped subject, for example `backend: preserve task drafts across turns`. PRs should explain the approach, list checks, link requirements, include UI screenshots, and call out API, database, or environment changes. 当前仓库有 Git 历史；提交钩子默认自动推送，未准备发布的开发提交需显式设置 `GIT_AUTO_PUSH=0`。

### 后续版本管理（用户明确要求，2026-10-04）

- 后续架构调整、功能补齐与集中修复统一归入“大版本更新”，在提交说明及 `docs/implementation/major-version-update-20261004.md` 中记录所属批次。
- 保留更新前基线及分批审查、复现和验收证据，分别记录已修复、已验证、未覆盖与在用服务是否加载；不得把局部测试通过写成全功能验收完成。
- 未经用户指定不擅自确定正式版本号或发布标签；大版本开发记录不等于已正式发布。

## Security & Configuration Tips

Keep credentials in `.env.local` or encrypted admin secrets; never commit or screenshot them. Change defaults before sharing an environment. Treat `storage/` as runtime data and preserve read-only integration boundaries.
