# 第3轮质检首失败诊断

复核时间：2026-10-04。原始批次：`.implementation/full-business-browser/20261004-084542-6b78129f`；质检现场：`.implementation/ui-results/full-browser-quality-mut4hk6m`。只读取文件与截图，没有恢复100轮、连接在用服务或查询平台/业务数据库。

## 结论

本次停止发生在**隔离浏览器网络证据门禁**，不是质检业务断言失败，也不是检测服务/模型失败。直接原因是代理拒绝了一次新的目标 `https://telem-edge.smartscreen.microsoft.com`，该精确主机尚未登记为已观察到的浏览器背景候选，因此 `unexpected_requests=1`，runner依约停止。

按已有CDP观察证据，这次请求未被归属于应用页面或Worker；结合精确域名、已有背景流量和业务源码中未找到该域的引用，判断为Edge背景流量的新候选。现有记录只有origin和分类计数，没有该请求的完整CDP initiator/netlog，不能声称已经识别到Edge内部具体功能。候选分类只改变证据归类，**仍须在连接前拒绝目标**；应用页面/Worker主动请求同域仍必须触发失败。不能以“微软域名”泛化放行，也不能把旧manifest改成passed。

## 原始证据

- `003-quality/browser.log`：11项业务检查全部PASS，包括两个回环检测服务各一次、精确成分值、原图与证据、历史报告与390px深链接。
- `result.json`：11项checks，`failures=[]`、`accessibilityFindings=[]`；appearance/component各一个POST；外观原图6369字节，SHA256为`c1f66035f20ab54c176538cc807d7fed8e36655546761dc87084fcd025d4e18d`。
- manifest质量项：`exit_code=0`，耗时45.187秒；最终`failed`来自`cleanup_errors[0] = network_evidence: RuntimeError ...`，而不是Node业务脚本失败。
- `003-quality/network.json`：allowed=282、blocked=119、background_candidate=118、unexpected=1、application_blocked=0、upstream_errors=0、client_disconnects=0。唯一不在background_candidate_origins中的blocked origin就是上面的精确SmartScreen主机。
- `002-quality/network.json`：allowed同为282、unexpected=0、application_blocked=0、upstream_errors=0，背景origin没有该主机。这支持本次差异来自新增背景目标，而不是业务调用量改变。
- `003-quality/host-diagnostics.log`：有既有Pydantic字段警告，工作流执行步骤均完成，末尾为“工作流执行器：完成”；没有发现本次质检失败或网络上游错误堆栈。
- 已查看`quality-report-mobile.png`：390px报告弹窗正常显示样本、检测报告和“不合格”判定；这是夹具的预期业务结论，不是系统执行失败。没有`failure.png`，与业务脚本未进入失败分支一致。

## 为什么manifest的network为null

`tests/dsh/business_browser_acceptance.py:138`的`check_network_evidence`在任何unexpected/application_blocked/observer_errors/upstream_errors非零时抛错；`:149`起的`finish_case`在清理阶段独立执行该检查。抛错被记录为network_evidence清理项，因此manifest中`network=null`，但原始network.json完整保留，不能解释为网络证据丢失。

`tests/ui/isolated_browser_guard.cjs:11`当时的精确背景主机集合缺少该主机；`:83`起代理授权逻辑仍将其拒绝，`:92`未命中背景候选而累加unexpected。CDP的`observeApplicationRequest`是独立计数，目标即便列为背景候选，只要被应用页面/Worker观察到仍会进入application_blocked。

## 清理和停止边界

- Windows job清理为`active_before=1`、`active_after=0`，没有进程回收失败记录；宿主清理与诊断保存也未记异常。
- quality suite的finally执行检查记录、流程、指标/模板、设备、两个数据源、原图清理；`:648`会因任何已收集清理错误设置非零退出码。本轮result失败数组为空且退出0，说明这些UI清理断言没有失败。未再打开SQLite反查已删记录，不把它写成额外数据库核验。
- 整批08:45:42开始，09:09:32停止，共25个套件执行：24通过、1在网络门禁失败；完成前两整轮和第3轮system至alert。第3轮workflow/ai_documents以及后续轮次未启动。这个批次不能记成100轮通过。

## 后续最小修复边界

依据本轮已观测证据，只给`telem-edge.smartscreen.microsoft.com`增加精确背景候选分类，不添加通配子域、不允许任何实际外网转发。补回归证明：代理CONNECT仍403，未产生外部连接；页面和Worker向同域请求均被CDP标记并导致门禁失败；相似/子域名称仍unexpected。先跑网络专项，再单套quality一轮，使用新的证据目录；保持原失败证据和100轮停止状态。

该最小修复已由主代理另行授权在独立工作树中实施，不属于本报告的只读诊断动作。

## 独立修复与新验收结果

工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-capability-contracts`。修改后已冻结以下两个文件，未提交、未改主树代码：

- `tests/ui/isolated_browser_guard.cjs:13`：增加精确 `BACKGROUND_ORIGINS` 条目 `https://telem-edge.smartscreen.microsoft.com`；`:93`只改变拒绝后的证据分类，不改变允许目标/上游连接逻辑。未观察到的HTTP、8443、子域和相似域不随之纳入候选。
- `tests/ui/isolated_browser_guard.test.cjs:121`：新回归先失败（背景候选实际0、期望1），修复后通过；证明CONNECT仍403、外部上游连接数0、page/worker同域访问仍进入application_blocked，证据不保存查询令牌。现有真实Edge用例也加入此域的页面、弹窗、Worker请求，要求这些CDP证据全部出现。`:167`支持通过`TEST5_E2E_PYTHON`显式指定现有Python，供独立树复用主树venv，不安装/复制环境。

网络专项执行命令：

```powershell
$env:TEST5_E2E_PYTHON='E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe'
$env:PYTHONUTF8='1'
node --test tests/ui/isolated_browser_guard.test.cjs
```

结果：9项通过、0失败、0跳过，3.837秒，包含真实Edge页面/弹窗/Worker和Windows后代回收。该专项没有连接业务数据库或真实模型。

随后仅执行一次：

```powershell
& 'E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe' tests/dsh/business_browser_acceptance.py --suites quality --iterations 1 --case-timeout 900
```

新证据位于独立树 `.implementation/full-business-browser/20261004-091502-44f9774b/manifest.json`；UI结果位于独立树 `.implementation/ui-results/full-browser-quality-mut4pkg5`。2026-10-04 09:15:02至09:15:44，quality耗时42.328秒，退出0、status=passed。网络allowed=282、blocked=116、background_candidate=116、unexpected=0、application_blocked=0、upstream_errors=0；Windows active_before=0/active_after=0，无清理异常。

**新quality单轮未自然再次出现SmartScreen目标**；因此它证明当前业务场景与完整门禁正常收尾，精确目标归类、保持阻断和应用同域判失败的行为由新增确定性网络专项证明。两种证据不能互相替代。没有恢复原100轮、没有补写或翻转旧失败结果，未额外重复测试。

冻结时SHA256：

| 文件（独立树） | SHA256 |
|---|---|
| `tests/ui/isolated_browser_guard.cjs` | `03353E9A6110E10BDB60EB3EEEC60057315250E608D2F31ED1A0B0F134BF486A` |
| `tests/ui/isolated_browser_guard.test.cjs` | `B11878A29FF1DDBF60AF8BE168062CECD49EB59ADD8BBB9CBAD77EB6A89A9201` |
| `.implementation/full-business-browser/20261004-091502-44f9774b/manifest.json` | `BCB029EC23008010C55C1F1CF4DFE6A1E549F3F8039109CE1964E7160B64AF74` |
| `.implementation/full-business-browser/20261004-091502-44f9774b/001-quality/network.json` | `C94568D6100BCA687E05801E2F615CD7B43F24E5D039CB205DDA39A0B5ABD2F2` |
