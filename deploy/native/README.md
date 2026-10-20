# Windows 本机原生部署

面向开发与本机部署人员。默认入口为 `windows.py`，不使用 Docker，不读取旧 `.env`，不连接既有 PostgreSQL/Redis。Dify 1.17.1 与 Open WebUI 0.12.3 使用不同 Python/Node 环境，保留原生功能及许可。

**2026-10-20实际状态：原生整体验收未完成，安装/构建已因本机资源异常暂停。** Open WebUI后端已安装，Torch为2.14.1+cpu（CUDA=None），未下载模型权重；原生前端在Pyodide资产准备阶段被主动终止。Dify依赖安装出现Windows os1450，Next构建出现内存分配失败，插件daemon Go编译出现GC/堆资源故障。没有启动Dify API/worker或运行迁移，首次API-only在45秒内未监听而停止；也未验首次登录/短对话/知识与完整工作流。

本轮独立PG(:15432)、Redis(:16379)、Qdrant(:16333)曾按新目录/新凭据启动，已核对无客户端后安全关闭，保留数据和manifest。停止安装器后测得可用内存4336MB、提交量33424MB/上限40113MB；不能把此时物理内存有空余理解为允许恢复并行构建。`doctor`显示资源故障和各组件失败状态；`install/start`在故障标记有效时拒绝自动恢复。须先处理资源问题并按用户答复决定后续，不能重试、扩大内存或删除功能绕过。

首次API-only实际结果：新SQLite迁移已开始，45秒内未获得监听；本次进程树最大RSS约251MiB。已回收全部本次进程，13000无监听，无HTTP/模型验收请求。没有重试或扩大预算；后续须按用户对超时的约束明确授权，不能将下面示例当成自动恢复命令。

## 受控API单独启动

在主协调者明确恢复该范围、串行核验资源后，可显式运行：

```powershell
.venv/Scripts/python.exe deploy/native/windows.py start openwebui --api-only --ack-resource-incident
.venv/Scripts/python.exe -X utf8 tools/verify_native_api.py --run-id native-api-20261020-01
.venv/Scripts/python.exe deploy/native/windows.py doctor
.venv/Scripts/python.exe deploy/native/windows.py stop openwebui
```

两个标志仅确认本次Open WebUI API启动，**不清除 `resource_incident.active`，不允许恢复install、前端构建或其他服务**。不是通用资源异常解除命令。安装和构建另外有跨进程排他锁，第二个安装器立即拒绝，不等待或重试。

启动重新核验本项目 `.local` 内固定源码副本、独立Python3.12环境、基础静态资产、本机磁盘和新SQLite/data目标；未登记的既有数据拒绝接管。所有本地模型权重自动更新、旧dotenv加载和Ollama回退均关闭，模型密钥仍只从加密helper交给子进程。

预检要求物理可用至少2GiB、提交余量至少3GiB。只等待最多45秒，逐次核验PID/创建时间/exe、进程树RSS不超过1GiB及127.0.0.1:13000监听归属；超限或超时只回收本次创建的进程树，记录故障，不重试或加预算。原先已经存在的进程不接管、不终止。启动后的有限验证由协调者继续监控；`doctor`展示当前进程树RSS及超限标记，只读检查不会自动终止。

API模式metadata明确 `frontend_available=false` 并保存SQLite/data目标，专用前端路径保持不存在。此时首页404是预期；`smoke`只检查一次 `/health`，不访问首页、`/ready`或`/health/db`，也不执行模型调用。监听就绪和健康200均不能替代登录、权限、模型、知识与工作流验收。

启动守卫与验收工具已串行完成56项Mock/临时密文文件测试，证明脚本边界，不代表原生启动、真实登录、对话、知识或流程通过；实际原生结果另行记录。默认verifier覆盖两账号登录、普通用户私聊读写隔离、模型目录、唯一64token短SSE和回复落库；包含一次health，不要叠加smoke后仍统计14请求。run-id一经预约不得重放；启动45秒与后续验收60秒分别计时。

## 资源问题解决后的完整部署流程

以下安装/构建步骤当前仍被故障标记阻止，不属于本次API-only继续范围。

```powershell
.venv/Scripts/python.exe deploy/native/windows.py doctor
.venv/Scripts/python.exe deploy/native/windows.py prepare
.venv/Scripts/python.exe deploy/native/windows.py install openwebui
.venv/Scripts/python.exe deploy/native/windows.py install openwebui-web
.venv/Scripts/python.exe deploy/native/windows.py start openwebui
```

`prepare` 只复制代码到 `.local/native_windows/sources/`，排除旧密钥、环境文件、数据库、依赖与缓存。下载、虚拟环境、数据、日志和新随机凭据全部归该隔离目录。凭据为本机加密文件，不进入 Git。只有 `install` 下载和构建，只有 `start` 创建进程；默认 `doctor` 只读核查，不连接数据库。

| 组件 | 本机入口 | 数据与运行要求 |
|---|---|---|
| Open WebUI | http://127.0.0.1:13000 | Python 3.12、Node 22、独立 SQLite/data；模型密钥由本机加密 provider helper 注入 |
| Dify API | http://127.0.0.1:15001 | Python 3.13、新 PG/Redis/Qdrant；原生 gevent/Socket.IO |
| Dify Web | http://127.0.0.1:13001 | Node 24、pnpm 12.10.1、原生 Next.js 构建 |
| PostgreSQL | 127.0.0.1:15432 | PG15二进制、新 datadir、仅本轮 dify/dify_plugin 两库 |
| Redis | 127.0.0.1:16379 | 新 data/config/密码；当前可用二进制为 Redis5，尚未证明与上游Redis6全部行为一致 |
| Qdrant | 127.0.0.1:16333 | 官方1.19.2 Windows发行包、独立存储、鉴权；gRPC16334 |
| 插件daemon | 127.0.0.1:15002 | 官方0.6.10服务源码与可移植Go1.26.10编译；发行页Windows工具是开发CLI，不能冒充daemon |

依赖与服务分步执行，任一步失败即停止该步。查看 `.local/native_windows/logs/`，不把日志或密文上传。支持 `install python313/dify/dify-web/qdrant/plugin_daemon`，随后依次 `start postgres`、`start redis`、`start qdrant`、`start plugin_daemon`、`start dify_api`、`start dify_worker`、`start dify_web`。构建曾完成依赖安装时，可用 `install openwebui-web-build` 仅重新构建。通过 `stop 组件名` 只停止已核验PID、创建时间及可执行文件的项目进程；PG使用其新datadir正常停库，绝不停止系统服务。

Dify API/worker 启动前必须核验依赖进程及回环监听归属；禁止端口被占用时自动回退。数据库初始化及迁移只针对本项目新集群。Celery Windows 使用 solo 单工作进程，吞吐与长期稳定性须单独验收，不能宣称等同Linux生产运行。

## 原生兼容性与验收

- `windows_patch.py` 在隔离副本修复 Open WebUI 加密校验脚本的 Windows URL/路径问题：先核验原源码指纹、原WASM全部SHA256与文件集合，再使用 `fileURLToPath` 和 POSIX逻辑路径。产物字节不变，校验不跳过，修改前后hash保存在副本manifest。
- 前端仍运行原生构建、Pyodide资产准备和加密检查；未下载本地模型权重。模型质量、知识导入检索、工作流发布运行、权限及插件均需分别记录，进程存活/健康200不代表全功能验收。
- 当前Dify代码节点沙箱与Agent V2存在Linux运行时要求。Windows启动器拒绝启动Agent V2，也不把关闭隔离当作成功；代码节点端点保持本项目未启用端口，不能借用旧沙箱。
- Agent V2的本机Linux候选方案与源码证据见 `linux-runtime.md`；尚未安装WSL或改Windows组件，须由主协调者按用户确认继续。
- `versions.lock.json` 保存已核验原参考源码与历史构建来源；`windows.lock.json` 固定本轮Windows下载资产、运行时及兼容边界。源码完整副本与依赖仍在Git忽略目录，不上传原参考项目/数据包。

## 集成协议

平台与原生账号显式映射，不伪造SSO，不把Dify App Key当Console Cookie。Dify固定发布UUID运行、读/停、Dataset导入检索由typed adapter提供；Open WebUI保留原生聊天/Agent。跨网络调用先提交本地幂等预约，未知结果不重放。工具只接受已校验OpenAPI或MCP初始化边界；签名回调含时间、事件幂等、序号与终态保护。

`prepare.py` 和 `control.py` 的容器CLI已停用，其内部只保留历史边界测试和源码指纹函数。旧 `.runtime/` 配置未运行，不属于本轮入口。没有自动清理、系统安装、修改全局PATH或连接远程数据库的步骤。
