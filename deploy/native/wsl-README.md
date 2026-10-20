# Dify 本机 WSL 原生运行

用于维护部署的开发人员。保留 Windows 平台和 Open WebUI，Dify 在专属 Ubuntu 中运行；不用 Docker，不重写原生工作流、Agent、知识库或代码执行器。

当前电脑已完成安装、新库迁移和14角色启动；Python、JavaScript原生代码节点实际执行成功，指定DeepSeek模型配置校验与bge-m3知识导入、检索、引用追溯已通过。平台调用已发布的无模型流程、任务输出回流和同键不重复执行已验。两引擎均已登记15个只读业务工具，schema与设备列表读取已通过。日常使用根目录 `启动系统.cmd`；下面的安装步骤供新机器部署和维护，不应在当前已初始化目录上重复执行。Agent V2、完整节点组合及模型自主选工具仍分别验收。

## 固定范围

| 项目 | 本次目标 |
|---|---|
| 发行版 | `ai-agent-company`，Ubuntu 24.04 / x86_64 |
| 虚拟磁盘 | `D:/WSL/ai-agent-company`，由 Windows 协调入口核实 |
| 程序与数据 | Linux ext4 `/opt/ai-agent-company` |
| 应用身份 | `agentapp`；PG、Redis、任务各用独立用户 |
| Dify 来源 | 本机锁定 1.17.1 API、Agent、runtime；插件源 0.6.10 |
| Web 来源 | 官方 1.17.1 Next 制品，revision `8387590ace4a094de812b7847fc6a4c3a27cd52b` |
| 代码沙箱 | 官方 0.2.15，commit `5631afef06ec88f80c28129aec7fd22a30006b14` |
| 兼容差异 | Ubuntu PG16 对比上游 PG15；部署 uv0.13.4 对比上游0.8.9；Alpine前端同版本GNU sharp制品替换；沙箱匹配Python3.13库、私有UID映射及默认seccomp补充stat元数据查询 |
| 沙箱工具链 | 独立Go1.24.13、上游模板指定Node20.11.1；前端/插件保持Node24.21.0和Go1.26.10 |

官方 Web 保留 Agent V2 创建、构建、发布、版本及聊天核心界面。本地来源另有 Agent 语音路由，官方制品不具备该额外界面；不能据同版本声称两者逐字节等价。所有安装收据与 `full_native_verified=false` 均保留，进程监听不等于业务全功能验收。

`wsl_sandbox_patch.py` 仅对已核SHA的Python默认seccomp表补充本机审计确认的stat调用，不使用环境变量覆盖允许表。JavaScript路径保持官方双层目录；早期误改已撤回，纠正记录与失败历史独立保留。Node24的libuv会触发默认沙箱不允许的io_uring，因此代码沙箱单独复用上游Node20.11.1，不添加io_uring权限。私有passwd在Go启动前登记上游UID池，以供Python chroot同步复制。旧Node仅为此锁定上游的兼容基线，升级沙箱/运行时必须另做隔离及节点回归。

## 分阶段准备

以下命令在仓库根使用 `.venv/Scripts/python.exe`。每一步单独执行；失败保留账本和部分产物，不自动重试。实际实施进度以唯一共享进度文档为准。

1. 已注册的专属 Ubuntu 只执行一次 `deploy/native/wsl.py bootstrap`。基础安装禁止包管理器当场启动服务；还须核实本次新发行版的默认 `postgresql`、`postgresql@16-main`、`redis-server`、`nginx`、`squid` 没有随后自启动。不得接管默认数据目录。
2. 在 Windows 本地打一个源码包，避免跨 `/mnt/e` 逐文件传输：

   ```powershell
   .venv/Scripts/python.exe deploy/native/wsl_sources.py sources-archive --source .local/native_windows/sources/dify --plugin-archive .local/native_windows/tools/plugin-daemon-0.6.10-source.zip --lock deploy/native/versions.lock.json --output .local/native-linux-artifacts/dify-source-1.17.1.tar
   .venv/Scripts/python.exe deploy/native/wsl.py verify-resume
   ```

   恢复先核对整包、完整清单和已有 partial 的 SHA，再只补缺失文件。现有差异或额外文件拒绝覆盖；原 `sources` attempted 历史保留，另记 `verify-resume`。只读核对可直接调用 Linux `wsl_setup.py verify-resume ... --verify-only`。
3. 顺序执行 `wsl.py tools`、`wsl.py python`、`wsl.py binaries`、`wsl.py qdrant`、`wsl.py sandbox`。工具链固定 Node24.21.0、Go1.26.10、pnpm12.10.1、Python3.13.14、Qdrant1.19.2。Python 按原 `uv.lock` 安装 API 与 Agent server extra，不重新解析依赖。
4. 前端优先复用官方三层制品：`wsl_oci.py fetch --directory .local/native-linux-artifacts/dify-web-1.17.1-next`（Windows）；再执行 `wsl.py web-extract`、`wsl.py web`。只下载约72MB前端层，不下载基础镜像，也不启动 Docker。`web-build` 仅是明确选择的源码构建回退。
5. `wsl.py prepare` 固化程序为 root 只读，生成随机加密凭据和隔离配置；必须在安装构建完成之后执行。`wsl.py doctor` 只核文件、摘要、Landlock/cgroup 与账本。
6. `wsl.py init-data` **第一次创建新数据库**：核新PG PID/端口后，只初始化 `/opt/.../data/postgres`，创建 `dify`、`dify_plugin` 并运行原生迁移。插件库使用官方0.6.10 CLI独立迁移；已有主库初始化时仅补 `init-plugin-data`，不重迁或清库。失败即停，不能改连默认/远程库。
7. `wsl.py start`、`wsl.py status`、`wsl.py stop` 分别启动、只读状态、停止；停止保留全部数据、密文和网络记录。Web 地址为 `http://127.0.0.1:13001`。

旧 `binaries` 若已完成六个 Go 构建但最后下载失败，使用 `wsl.py verify-binaries` 核已有 ELF、Go 包身份与 SHA；再单独 `qdrant`。不重跑已完成构建，不把失败历史改成成功。

首次归档的旧过滤规则曾错误排除所有同名 `storage` 目录；修复为仅排除明确运行数据位置，并保留嵌套业务源码。已部署旧包时，使用独立 `sources-storage-repair` 补源、`python-package-repair` 只重装本地 Agent 包、`verify-agent-binaries` 核已有五个 Go 程序、`plugin-build` 单独构建插件。uv生成的额外build/egg-info及旧配置只按只读审核清单的路径、类型、所有者、权限、SHA核准，不能用目录名字笼统跳过。已失败的补源必须显式传 `--resume-reviewed-failure --reviewed-extras ... --reviewed-extras-sha256 ...`；旧失败保留在同阶段history中。该开关不能恢复数据库或构建失败。

| 阶段 | 单次工作预算 | 主要网络/构建 |
|---|---:|---|
| bootstrap / tools | 各900秒 | Ubuntu包、固定工具链 |
| verify-resume / web-extract | 各180秒 | 本地归档读取；无网络 |
| python | 两个锁定环境各3600秒 | uv原锁安装 |
| binaries | 总3600秒，每构建不超过剩余额度/1800秒 | 原生runtime五个程序和插件server |
| qdrant | 300秒 | 官方31.3MB单制品，固定SHA |
| sandbox | 总3600秒，每构建不超过剩余额度/1800秒 | 固定commit源码、四个原生目标 |
| web | 900秒 | 同版本GNU可选包，无Next大型构建 |

这些是部署步骤预算，不限制用户使用。Windows桥接额外只留有限的失败记录/清理时间；WSL客户端退出仍不能当作Linux进程已全部停止。

## 隔离与失败处理

服务仅使用新回环端口：PG25432、Redis26379、Qdrant26333、API15001、插件15002、Agent15050、Next23001；用户入口13001。环境从允许列表生成，不继承旧 `.env`、数据库地址、代理或模型密钥。模型供应商需随后通过原生有效接口配置，准备脚本不发模型请求。

原生 `api/docker/entrypoint.sh` 用于 API/worker；beat复用其中原生Celery命令，仅把schedule文件放到可写data目录。Agent server、shellctl、插件server、代码沙箱均保留原实现。shellctl 继续启用 Landlock；传统沙箱保留原 seccomp、chroot、UID池。代码沙箱在私有 mount namespace 中隔离 `/tmp`、`/var/sandbox`，不覆盖宿主同名目录。

Agent 任务和传统代码沙箱各用专属 network namespace；只允许调用对应代理，禁止直接连宿主数据库、局域网或云元数据。代理只放行公共HTTP/HTTPS，以及经过认证的精确 Agent Stub/文件入口。此网络规则只写新 namespace，不改宿主全局防火墙。

此处保留上游local backend共享shellctl/任务UID的运行模型，不等同于每租户独立OS沙箱；生产环境不可信代码的强隔离仍需配置和验证原生enterprise/openshell/e2b等方案。Windows平台工具经已核验的13002认证桥接入，主代理只允许绑定IP、固定端口与16个GET路径（schema及15个业务路径），不放开数据库或整段私网。Windows平台/桥重启后需重新核当前快照与上游归属再配置代理；失效快照不会产生代理例外。

每次启动、每种角色先创建 root 专属 cgroup v2 并记录 inode，再加入该组并降权执行原入口。cgroup 只用于进程归属，不设自定内存上限。停止核 runtime/launch/role/path/inode，先 TERM 再有界 kill 本组；主进程退出后仍核子孙。归属变化则拒绝停止，残留明确报告失败。

`wsl_lifetime.py` 在已核发行版中保留本项目前台生命周期，避免服务运行期间WSL空闲退出。发行版重启后，统一启动入口仅在旧PID、cgroup、veth均已不存在时调用 `reconcile-boot` 归档旧归属，再启动；独立 `doctor`/`status` 会报告需要此核对。旧数据、凭据与失败账本不会重置。

部署验收仍要逐层完成：原生登录和权限、固定无LLM流程、Python/JavaScript沙箱、Agent V2 build/publish/files/版本、一个有预算的真实模型回合、知识导入检索及平台聚合调用。离线测试和监听成功均不能代替这些验收。
