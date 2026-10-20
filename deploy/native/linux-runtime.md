# Agent V2 本机Linux候选方案（未安装、未验收）

面向部署/安全开发人员。用户要求本机部署且不使用Docker；当前Windows尚无WSL发行版。本文件只提供可审阅方案，不授权修改Windows组件，不执行安装，也不把候选方案算作功能完成。

## 确认的源码边界

| 证据 | 影响 |
|---|---|
| `dify-agent-runtime/internal/server/tmux.go` 调用 `exec.Command("tmux", ...)`；README描述pty和stdio都由tmux管理 | 单独安装Windows Python无法得到等价执行运行时 |
| `dify-agent-runtime/internal/landlock/landlock_stub.go` 非Linux构建返回不支持；README要求Linux>=5.13且启用Landlock | 不能关闭路径隔离来冒充企业验收成功 |
| `dify-agent/pyproject.toml` 的server组依赖 openshell>=0.0.106 | 该版本PyPI只有macOS arm64与manylinux_2_39轮子，无Windows轮子/源码包 |
| `dify-agent/src/dify_agent/runtime_backend/profile.py` 同时支持 local/enterprise/e2b/openshell，local直接接shellctl | 本机Linux可选择local运行时，不必采用OpenShell gateway容器模式 |
| `docker/docker-compose.yaml` 的local_sandbox网络只经专用SSRF代理允许agent后端/特定API | 去掉容器后必须重新实现同等网络隔离，WSL本身不自动保证这一点 |

主来源：[OpenShell 0.0.106发布文件](https://pypi.org/pypi/openshell/0.0.106/json)、本机锁定Dify1.17.1源码。OpenShell gateway自身文档要求容器运行环境；候选方案明确选择Dify的local backend，不把WSL等同Docker替代。

## 候选部署，不使用Docker

1. 用户明确允许后，在本机建立独立WSL2 Ubuntu24.04发行版和专用未提权用户，只给该发行版新目录；不挂旧数据库/密钥目录、不使用Windows现有数据。24.04 glibc2.39满足现有manylinux轮子要求。
2. **先验内核能力**：核对Linux版本与Landlock ABI探测，必须实际成功。若WSL内核未启用Landlock，不继续任务执行，也不默认编译/更换内核；将该前提返回主协调者。
3. 在该发行版安装锁定Python3.13/Go1.26/Node24与tmux/bash；从本机锁定副本构建shellctl、runner、sanitize-pty、runner-exit、dify-agent CLI，不复制运行数据或旧.env。
4. 用独立Linux用户和目录运行 `shellctl serve --listen 127.0.0.1:25004 --require-auth-token`；启用路径隔离。Agent backend用 `DIFY_AGENT_RUNTIME_BACKEND=local`，只接该新shellctl，并使用本轮新Redis/插件服务，不接外部服务。
5. 用Linux网络命名空间及明确SSRF代理/egress allowlist实现运行任务网络边界。执行用户不能直接访问Dify内部管理面、数据库端口或局域网；禁止以关闭隔离代替。该步骤涉及本机Linux系统策略，须包含在用户批准的范围并逐项验证。
6. Windows平台只对接该本机发行版新回环映射端口。跨WSL访问Windows依赖时先验证端口实际归属与防火墙边界，不能把任意127.0.0.1转发误判为本地服务；任何DB仍只允许本轮同机新实例。

## 必须通过的验收

- 新Linux执行用户无法读Windows用户目录、平台密钥、新PG数据或其他任务Home；Landlock实际拒绝越界。
- 网络只可达到许可代理/后端入口，不能绕过代理访问LAN、数据库、云metadata或内部管理接口。
- shellctl鉴权、任务中止、退出码、输出分页、Home快照、租约清理与失败回收通过。
- Agent V2真实创建/构建/发布、一次短任务、取消和证据引用通过；传统Dify workflow验收不能替代。
- 所有进程与数据归新发行版/目录，停止脚本仅清理自身，不删除保留数据或停止其他服务。

目前只核验了源码依赖和本机WSL缺失；上述Linux能力、网络命名空间与真实Agent V2运行均未验证。若只要求本机Windows、不允许Linux组件，则完整Agent V2执行器保留为明确未完成项，聊天/传统工作流等Windows可运行部分继续独立交付。

## 系统变更的可审阅范围

2026-10-20核对[微软安装命令](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)：可以先只安装WSL组件，不自动安装或启动默认发行版；发行版支持指定安装目录和`--no-launch`。安装需要管理员权限，并可能要求重启，不能在后台替用户自动重启电脑。

确认后拟分两步执行，尚未运行：

```powershell
# 先只安装本机Linux运行组件；不自动重启，不接任何数据库。
wsl --install --no-distribution --web-download
# 核实组件就绪且不存在同名发行版后，才安装到本项目独立目录。
wsl --install -d Ubuntu-24.04 --no-launch --location E:\code\Yun_tian\new_agent\ai_agent_company\.local\native-linux
```

微软当前[6.18分支默认配置](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.18.y/arch/x86/configs/config-wsl)包含`CONFIG_SECURITY_LANDLOCK=y`且LSM列表启用Landlock；这只说明候选方案有上游依据，不能证明本机最终安装的内核已具备同等能力。实际安装后仍先探测ABI和网络隔离，失败即停止，不自动更换内核、关闭隔离或扩大权限。本方案不授权恢复此前暂停的大型原生构建。
