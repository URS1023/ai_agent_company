# 批次十四：第三轮能力验收 500 只读定位

2026-10-04。只读读取原始日志、当前测试守卫、Python 标准库及触发调用链；没有修改产品或测试，没有启动服务、浏览器或查询。

## 确认结论

这次 500 来自隔离验收 Python 网络守卫误拦 Windows asyncio 的进程内部 socketpair。失败在创建事件循环时发生，业务 ASGI 路由尚未进入。不是能力 HTTP 桩、模型提供方、参数编解码或业务取消控制异常。

原运行 `.implementation/full-business-browser/20261004-110911-ea02c1e9/manifest.json` 为干净快照 `a34fdc0b1ab01049a468c9068a11e7c0af9b1137`；system/content 通过，capability 在 `http://127.0.0.1:49336` 执行 65.625 秒后退出 1。该套 13 个业务步骤 PASS，但最终失败列表含业务动作目录 GET 的 500 和相应 console error，不能记整套通过。Windows Job 清理 active_before=1、active_after=0。后续六套没有执行。

## 原始堆栈与调用链

`001-capability/host-diagnostics.log:42–80`：

```text
protocol.py:76 handler(api.dispatch)
server.py:113 application.dispatch
application.py:146 asyncio.run(execute())
asyncio.runners / events.new_event_loop
windows_events.ProactorEventLoop
proactor_events.py:787 _make_self_pipe -> socket.socketpair()
socket.py:623 _fallback_socketpair -> csock.connect((addr, port))
tests/dsh/python_guard/sitecustomize.py:28 -> check_target
sitecustomize.py:25 AssertionError: DSH 验收禁止外部网络及在用平台连接
```

随后 `execute was never awaited` 和 `_ssock` 属性不存在是循环初始化失败引出的次级告警，不能误认业务协程自己抛错。`protocol.py:79–85` 将未知异常映射 worker_error/status 500，宿主 `host.mjs:174` 正常返回失败；不是通过重试或吞异常可解决的产品契约。

`tests/dsh/real_platform_smoke.py:138–140` 为 Worker 设置 DSH_TEST_ISOLATION=1 和 python_guard。守卫 `sitecustomize.py:20–25` 允许数字回环但拒绝 `{9,8011,8012}`；没有对 socketpair 的识别处理。

实际解释器标准库 `C:/Users/urdcbk/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/Lib/socket.py:598–627` 固定回环主机（第 96 行 127.0.0.1），`lsock.bind((host,0))` 后取得随机端口，再 `csock.connect` 自己；第 665 行把 Windows socketpair 绑定到此 fallback。因此本次拦截是内部随机端口落入上述禁用集合。日志没有打印具体目标端口，不能断言本次究竟是 9、8011 或 8012。

UI 触发源是 `full_browser_capability_acceptance.js:472–479` 点击已发布应用进入 `/ai?plugin_id=...`；`apps/web/app.js:451` 初始化聊天页并行读取业务动作，`3853–3858` 请求目录并在失败时清空目录。该错误被套件全程收集后在 `552` 严格断言，最终发现是正确行为；不应删除错误或放宽断言。

原 `result.json` 位于 `.implementation/ui-results/full-browser-capability-mut8uvs7/result.json`：13 步 PASS、accessibilityFindings=[]、本地 fixtureRequests 两次成功 POST `/equipment/status`。原网络记录 271 allowed、145 blocked、0 unexpected、0 application_blocked；这与 Worker 内部 socketpair 拦截并不冲突，浏览器网络观察器不观察 Python 自唤醒连接。

## 初始修复建议（尚未实施）

仅在隔离 `tests/dsh/python_guard/sitecustomize.py` 对 Windows fallback socketpair 使用本进程持有的高位回环监听 socket，完成连接和 peer 身份核对后返回两个端点；监听期间不释放端口。普通 connect/connect_ex 仍逐次经过既有目标校验，数据库连接仍被禁止。不要全局临时关闭守卫、放宽 8011/8012 或切换生产事件循环实现。

小型专项应覆盖受限候选不连接、占用/异常清理、真实安全回环双向配对与 asyncio 启动、普通禁用端口/外网连接仍在调用真实网络前阻断。需要明确有限端口探测预算及清理路径；完整业务验收需修复后重跑，单纯 socketpair 单元测试不能替代。

当前只读阶段没有执行复现测试；真实失败证据即此轮宿主/浏览器原日志。继续等待根代理解除测试守卫冻结，不修改正在门禁取码的产品。

## 收敛后的最小方案与待执行小测

根代理建议避免复制握手。只读核对标准库后优先采用更小的专用异常加有界重建：

1. 守卫先校验数字回环地址；仅内部目标端口为 `{9,8011,8012}` 时抛专用 AssertionError 子类。外部地址仍抛普通隔离拒绝，不根据错误文案或通用 AssertionError 识别冲突。
2. 仅当 `socket.socketpair is socket._fallback_socketpair` 时包装原 fallback，保持原签名和参数校验；原生实现原样保留。
3. 原 fallback 分配内部端口时捕获上述专用冲突，最多 32 次重建；耗尽明确失败。仅内部 socketpair 分配可重建，普通 connect/connect_ex 直接拒绝，不进行业务请求重试。
4. 标准库 `socket.py:619–630` 在 connect 失败后关闭 csock 并重抛，`613–632` 的 finally 关闭 lsock；触发拒绝时尚未 accept，不存在需要关闭的 accepted socket。因此可在确认两者已释放后重新调用原 fallback，不复制或削弱其 peer 身份核对。

冻结范围：产品、门禁脚本与现有测试均暂不修改。根代理发开始信号后，只新增/修改 `tests/dsh/python_guard/sitecustomize.py` 及专属小型测试；十五树保持冻结。

计划专项：以受控假 socket 驱动真实标准库 fallback 代码，强制首次选中受限端口，检查 listener/client 均关闭且第二个安全端口配对成功；普通直连的禁用端口与外网仍在底层连接前拒绝；32 次耗尽全部归还；非专用异常不重试；原生 socketpair 路径和无隔离环境不变；一次本机安全 asyncio 启动。不会访问 8011/8012 或创建业务库连接，受限候选由纯桩模拟。

## 原始证据与源码 SHA256

| 文件 | SHA256 |
| --- | --- |
| 原 manifest.json | D2A293D9C7A4E3C35EC4D6DA2461C2B774BBB11D3B8436E3C0A06146742A0889 |
| 001-capability/host-diagnostics.log | 5D53A2231DF8DE4AC8357F633660C2050E23051FF35DB59E4EA75761762A5B5B |
| 001-capability/browser.log | 375854E886079F3D57774E68F9EAF054E38F5046FE01597AE351B2475C1E3336 |
| tests/dsh/python_guard/sitecustomize.py | B1E71E12BC4131A2D2A418869C692BB0377EED2FB9FAE3933802A9F912B975CE |
| backend/app/business_worker/application.py | 189FA665417DB3B701BF52A8F912573CDE5D0839817C66A3A9E0022AC02EEB4F |

阅读范围：host-diagnostics.log、browser.log、manifest、network、result 与 python_guard 全文；Worker application/protocol、stdlib socket、前端初始化、能力套件应用启动及最终断言为上述行号片段。business_ai/router.py 与 service.py 初步阅读证明路由定义；精确失败点在这些函数执行前，未将无关业务模块纳入修复建议。
