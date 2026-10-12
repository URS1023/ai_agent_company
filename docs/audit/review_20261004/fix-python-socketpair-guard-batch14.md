# 批次十四：隔离 Python socketpair 守卫修复

2026-10-04。仅修复测试隔离基础设施，产品与 `scripts/verify_chat_delivery.py` 未修改。根代理确认此前统一门禁 12 阶段完成并解除本项冻结后实施。源码及专项已冻结；本代理未提交或推送。

## 已发生的故障

完整浏览器第三轮 `.implementation/full-business-browser/20261004-110911-ea02c1e9/manifest.json` 中，system/content 通过；capability 13 个业务步骤通过后，因捕获 `/api/v1/ai/business-actions` 的 HTTP 500 及 console error 判整套失败。此原始结果未修改，后续六套当时未执行。

宿主堆栈确认在 `application.dispatch -> asyncio.run -> ProactorEventLoop -> socket._fallback_socketpair -> python_guard.check_target` 被拦，尚未进入业务 ASGI 路由。标准库内部回环监听 `bind(...,0)` 随机分配到守卫保留端口 `{9,8011,8012}`，普通连接守卫无法区分内部配对。原日志没有目标端口的具体值，不宣称精确命中了哪个端口。完整只读证据见 `.implementation/diagnose-capability-socketpair-batch14.md`。

## 实际修改

仅两个文件：

- `tests/dsh/python_guard/sitecustomize.py`：新增 `ProtectedLoopbackPort(AssertionError)`。先验证目标为数字回环，随后才对保留端口抛该专用异常。普通 connect/connect_ex 照常在底层连接前拒绝。
- 同文件只在 `socket.socketpair is socket._fallback_socketpair` 时包装 fallback；保留原签名及参数验证，调用原标准库握手。仅捕获专用内部端口冲突，最多 32 次分配，耗尽重抛。其他 AssertionError、OSError 与外部地址错误不重试。原生 socketpair 与关闭隔离的环境不变。
- 新增 `tests/dsh/test_python_socketpair_guard.py`：标准库 fallback 代码搭配假 socket 的确定性回归，以及一次真实本机配对和事件循环启动。

没有复制标准库握手，没有临时替换/关闭全局 connect 守卫，没有开放保留业务端口。原标准库 `socket.py:619–630` 在连接异常时关闭 client，`613–632` 的 finally 关闭 listener；该时刻尚未 accept。包装只在此资源已释放的专用拒绝分支重新分配内部 socketpair，不构成业务请求重试。

## 红绿证据

工作目录：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-task-lifecycle`。

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/dsh/test_python_socketpair_guard.py -v
```

首次红灯 `.implementation/python-socketpair-before.log`：1 项、1 失败，0.003 秒。假 socket 强制内部 listener 第一次取端口 8011，实际执行当前解释器的标准库 `_fallback_socketpair` 函数控制流，旧 guard 直接抛出 AssertionError，未能重新分配。受限端口由纯桩模拟，未对真实 8011 进行 bind 或 connect。

修后 `.implementation/python-socketpair-after.log`：9 项全部通过，0.532 秒。无重跑、无额外大组。

| 专项 | 实际证据 |
| --- | --- |
| 首次内部受限端口后重新分配 | 首次 listener/client 均关闭，第二次安全候选完成配对，仅安全目标到达底层 connect |
| 普通直连禁用端口 | 9、8011、8012 的 connect/connect_ex 均立即拒绝，底层调用为 0 |
| 外部与非数字地址 | 文档保留外网地址/非数字地址/非法结构均拒绝，无底层调用；不被错认成可重建冲突 |
| 耗尽 | 32 次失败、64 个已创建 socket 全部恰好关闭，未进入第 33 次，无底层连接 |
| 其他异常 | 与专用异常相同文案的普通 AssertionError 及 OSError 均立即原样传播，不依据文案重试 |
| 原契约 | 签名一致，非法 family/type/proto 保留 ValueError，IPv6 fallback 仍正常且清理完整 |
| 原生与未隔离 | 原生 socketpair 身份不变，未隔离时 socketpair/connect 均不包装 |
| 数据库守卫 | PyMySQL connect/Connection 与 Psycopg connect 仍立即拒绝 |
| 真实本机启动 | 子进程启用实际 sitecustomize；socketpair 双向收发，asyncio.run 正常结束，无 stderr |

`git diff --check` 无空白错误；Git 仅提示仓库 LF/CRLF 工作树转换约定。

## 验证边界

唯一真实连接是标准库自建回环 socketpair，子进程有 20 秒执行预算、读写各 2 秒预算，全部正常完成；未启动平台、浏览器、业务数据库或模型，未执行 SQL，未连接外网或在用 8011/8012。假 socket 执行的握手函数来自当前 Python 标准库代码，而非另写测试握手模拟。

本项不能证明完整 capability 或九域验收已经通过。原 500 仍是失败证据；后续宿主、Worker 和完整九域由根代理执行。修复不改变产品异常策略，不把客户端连接关闭视为服务端业务查询停止，也不改变批次十五文件。

## SHA256

| 文件 | SHA256 |
| --- | --- |
| tests/dsh/python_guard/sitecustomize.py（修前） | B1E71E12BC4131A2D2A418869C692BB0377EED2FB9FAE3933802A9F912B975CE |
| tests/dsh/python_guard/sitecustomize.py（修后） | D33816E7B5DEF19594207BCA2A36819B44B0109E9FAA955E2AA47F8F675F68A0 |
| tests/dsh/test_python_socketpair_guard.py | 641C115A86C0E64E78B945ECA5F881E6E0044730704D513DE7937806FC562A48 |
| .implementation/python-socketpair-before.log | 90B4380126E753792F3A66F133BA7CB1E5220687DA30B802C55D034FA4076CA2 |
| .implementation/python-socketpair-after.log | AA0986BC03D975FA31D44293C9187FBF79F84505D2B11DA783296E554FB0174F |
| 原失败 manifest.json | D2A293D9C7A4E3C35EC4D6DA2461C2B774BBB11D3B8436E3C0A06146742A0889 |
| 原失败 host-diagnostics.log | 5D53A2231DF8DE4AC8357F633660C2050E23051FF35DB59E4EA75761762A5B5B |
| 原失败 browser.log | 375854E886079F3D57774E68F9EAF054E38F5046FE01597AE351B2475C1E3336 |
