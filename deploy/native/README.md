# 本机部署与维护

面向开发和实施人员。当前部署采用 **Windows 平台 + Windows Open WebUI + 专用 WSL Dify**，不使用 Docker。完整需求的实际验收状态以[实施验收表](../../项目文档/contracts/实施验收.csv)为准。

## 日常使用

| 操作 | 入口 |
|---|---|
| 启动或复用本项目服务 | 根目录 `启动系统.cmd` |
| 查看本机账号 | 根目录 `查看本机账号.cmd`，仅在用户主动打开的本机窗口显示 |
| 正常停止本项目服务 | 根目录 `停止系统.cmd`，保留数据和凭据 |
| 平台 | `http://127.0.0.1:8080` |
| AI 对话 | `http://127.0.0.1:13000` |
| 流程、Agent、知识 | `http://127.0.0.1:13001`，也可从平台打开 |

`desktop.py` 核验进程和数据归属后复用已运行服务。Dify 已启用时通过 `wsl.py` 启动专属发行版；失败会保留诊断，不接管其他进程。R32“取消内存限制”持续有效，不使用 Agent 自设的空闲内存门槛或 RSS 上限；Windows 平台45秒、Open WebUI180秒的启动期限及数据边界保留。

## 实际部署

| 服务 | 来源与数据位置 | 已核事实 |
|---|---|---|
| 平台 | 仓库代码、`.local/platform/platform.sqlite3` | 前后端已运行；更新前保存独立备份 |
| Open WebUI 0.12.3 | `.local/native_windows/`，独立 SQLite | 官方完整前端、登录和一次64-token回复已验；多轮、工具与知识另验 |
| Dify 1.17.1 | `D:/WSL/ai-agent-company` → `/opt/ai-agent-company` | 14个角色、新库迁移、账号与Python/JavaScript代码节点已验；已发布固定流程调用、任务输出回流及同键不重复执行已验 |
| Dify 前端 | 官方 1.17.1 Next 制品 | 保留原生页面，匹配 GNU 原生依赖；没有重新编写工作流画布 |
| Dify 执行组件 | 锁定 API/Agent 源码、插件0.6.10、代码沙箱0.2.15 | 官方签名模型插件已装；DeepSeek配置验证、bge-m3导入/检索/引用已验，Agent V2/工具链及完整节点组合待验 |

Open WebUI 前端 wheel SHA256 为 `6d716039a5c15ecdb1db12f99dd9c79ea5c86d3d7bbd9a67054dd26f1f534747`，由 `windows_frontend.py` 提取；后端关键文件与同版本 wheel 已核一致。前端制品和本机依赖不提交 Git。

Dify 专用 PG16 使用 `127.0.0.1:25432`，数据库 `dify`、`dify_plugin`；Redis `26379`，Qdrant HTTP `26333`，均为该专属发行版内的本项目新数据。具体进程、隔离、安装来源及命令见 [WSL 部署](wsl-README.md)。旧 Windows Dify 候选端口15432/16379/16333不属于当前运行方案。

## 配置、升级与停止

- 模型密钥由 `tools/configure_models.py` 写入本机加密配置。本机Dify已配置bge-m3及DeepSeek V4.1；后者经WSL窄桥和专属普通Open WebUI账号复用已配置的OpenCode Go模型，不保存管理员令牌。两引擎均已登记15个只读业务工具，使用各自证书身份和短令牌；schema及设备列表读取已通过，模型自主选工具另验。
- 交付账号在 `.local/desktop-access.enc`，密钥与运行日志仅留本机。模型密钥、数据库口令、依赖目录、原始数据包不推送 Git。
- 平台升级先备份，使用独立迁移校验版本。当前开发数据库由应用建表，不能自动给既有未版本化数据库盖上生产迁移版本。
- WSL 的 `status`/`doctor` 只检查归属和文件，不查询业务数据库。`stop` 只停止登记的进程树；不执行全局 `wsl --shutdown`，不停止其他发行版。
- 异常后保留原数据和失败收据，定位后执行明确的修复。不能删除新库重建来隐藏失败，也不能回退企业旧库或外部数据库测试。

## 验收与历史

进程启动、页面能打开、节点实际运行、企业数据效果是不同的验收层次。当前脚本和用例中的临时 SQLite、CSV 与 Mock 不能证明企业故障预测准确率或生产容量。

原生工作流验收工具为 `tools/verify_dify_native.py`；其账号、请求计数和派发记录加密保存。固定 Python/JavaScript 流程验证代码节点，后续模型、知识和 Agent 使用独立明确范围。部署收据保留 `full_native_verified=false`，直到相应完整验收满足。

Docker 安装和 Windows Dify 构建的旧尝试、故障及恢复记录保存在[共享进度](../../需求梳理/PROJECT_STATUS.md)。这些历史命令不再是当前启动入口；Docker Desktop 已停止，用户授权临停的 Elasticsearch 状态单独记录。旧候选文件与数据未清理。
