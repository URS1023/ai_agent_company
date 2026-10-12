# 批次十四：浏览器验收调度端口修复

日期：2026-10-04。修复范围仅为隔离浏览器验收调度器与小型专项；不属于产品业务修复。源码与专项已冻结，未提交、未推送。

## 原始失败与归因

原始运行 `.implementation/full-business-browser/20261004-105434-97b6e556/manifest.json` 的前八套通过，`ai_documents` 使用 `http://127.0.0.1:6666`，在登录导航前收到 `net::ERR_UNSAFE_PORT`。浏览器日志保存在该目录 `001-ai_documents/browser.log`。本套退出码 1、耗时 14.297 秒，网络观察记录 allowed_requests=0、blocked_requests=9、unexpected=0、application_blocked=0、background_candidates=9；Windows Job 清理 active_before=1、active_after=0。上述失败证据未修改。

调度器原先直接 `Host(platform, 9)`；共享 `tests/dsh/real_platform_smoke.py` 的 Host 默认环境 `PORT=0`。DSH 宿主把 0 交给 listen，由操作系统随机选择端口，本轮选中浏览器拒绝访问的 6666。此时页面尚未加载，不能把这次失败记为 AI 文档业务功能失败或通过。

## 最小修复

- `tests/dsh/business_browser_acceptance.py:21–45`：新增高位回环空闲端口探测。候选范围固定 49152–65535，最多 32 次；仅对 127.0.0.1 做 bind，不发起连接。Windows 使用 SO_EXCLUSIVEADDRUSE，不用复用端口绕过占用。成功与失败均关闭探测 socket，耗尽明确失败。
- `tests/dsh/business_browser_acceptance.py:248`：利用既有 `env_overrides` 向 Host 显式传入 PORT。
- 新增 `tests/dsh/test_browser_port_allocation.py`：仅测试分配边界与实际调度接线。

未修改共享 Host、产品、浏览器安全设置或 `scripts/verify_chat_delivery.py`。不停止占用端口的进程，不自动重跑业务套件。

## 红绿证据

使用命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/dsh/test_browser_port_allocation.py -v
```

工作目录为本报告所在独立树 `E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-task-lifecycle`。

1. 首次红灯：1 项、1 失败，0.018 秒，退出码 1。实际 runner.main 使用假 Host 验证接线，旧代码未传覆盖环境，得到端口 0，报“浏览器宿主仍使用未筛选端口：0”。日志 `.implementation/browser-port-before.log`。
2. 修后：4 项全部通过，0.040 秒，退出码 0。日志 `.implementation/browser-port-after.log`。
3. `git diff --check -- tests/dsh/business_browser_acceptance.py tests/dsh/test_browser_port_allocation.py` 无空白错误；Git 仅提示既有 LF/CRLF 工作树转换约定。

四项覆盖：候选下上边界 49152/65535 与 socket 关闭；真实本机回环 bind 占用后跳过、返回前释放；32 次全占用后停止；runner.main 实际传入 Host 的 PORT 与生成 manifest 地址一致。调度接线测试替换 Host、Node 子进程和进程清理，输出至临时目录。

测试未启动应用或浏览器，未执行 HTTP、数据库查询或模型请求，未恢复九域或 100 轮。唯一真实网络原语为本机 socket.bind 空闲探测，无 socket.connect。

## 明确边界

空闲探测关闭 socket 后，宿主才实际 bind，二者间仍有端口被其他进程抢占的竞态。发生时继续首失败停止，不宣称端口被持续预留。修复避开本次低位受限端口及同类默认受限端口，不绕过浏览器企业策略，也不宣称任何环境策略下所有高位端口均必然可访问。完整浏览器验收仍由根代理在冻结提交后执行；本小测不能替代 AI 文档业务验收。

## SHA256

| 文件 | SHA256 |
| --- | --- |
| tests/dsh/business_browser_acceptance.py | 812A6146C70B9A684306C9DB92C0699503D86E49AC20951EBEBA7CF0BFD4EA7E |
| tests/dsh/test_browser_port_allocation.py | A97FA85D9BB8C0766323B55194D2A0EEAE32EEB77241D82F413451F0069A005F |
| .implementation/browser-port-before.log | 56F84228ABF309B4042A335851E307C944597CB245286B98790BF21563C83710 |
| .implementation/browser-port-after.log | 1E824D561F380A464067A2D02C6A20C330BE6C2A54F0C68F6C8A01660CA3BCEB |
| 原始 manifest.json | 29B3C84DEAA6AB62898CAC05AD92471F97791E1192CD9824996BF2CC161B0C44 |
| 原始 001-ai_documents/browser.log | 9A87A54F0D331BB2F560071E9B3FD8224CA6529690C2952B14235AA8533B2884 |

批次十五独立树仍保持此前冻结，无本任务编辑。
