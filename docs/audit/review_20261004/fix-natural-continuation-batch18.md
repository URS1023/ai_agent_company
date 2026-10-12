# B18：自然续办任务身份的有界修复

2026-10-04。唯一修改树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-natural-continuation`；分支 `codex/major-natural-continuation`，起点 `b7241b663101e8931ffa0a4d514a7cb4984fcdbd`。已读本树 AGENTS.md 和正式 `docs/audit/review_20261004/plan-natural-continuation-batch18.md`。

只修改授权的两个产品模块和一个新测试文件；本报告及原始日志位于 ignored `.implementation`。未修改 B17 树、其他产品模块、旧测试、共享文档、门禁、README 或构建；未提交、推送、启动浏览器/服务、请求模型或连接业务数据源。

本报告前部保留首版 **43/43** 的阶段记录。根代理复核后确认首版新增了两项回归，均已取得真实 Worker 红例并修复；**最新专项为 53/53，76.81 秒，最终指纹见末节**。不能把首版 43 项通过描述为当时已经覆盖这两项回归。

## 修复结果

原句 **“继续，使用已有结果制作大屏和Excel”** 现在能够在最近任务尚未完成、资源范围一致时保留 task_id/draft_id、原交付清单与结构约束。没有将原句替换成“继续”。

明确另开任务的命令在逗号之后也可阻止隐式继承，并同时约束 continuation 和 modifying 两个入口。“继续，另开一个新任务，把刚才的PPT改成Word”不再被后句修改词绑回原任务。显式 task_id 仍按原有优先级查找任务；原 A-H 澄清支路、资源继承/再次授权、已完成任务边界及回执/停止事实过滤器没有改动。

## 先红后修：三个真实 Worker 原始失败

三个单例均先写入新测试文件，再在产品未修改时分别运行。每次只指定一个 node id，`-x` 首失败停止，日志与 XML 分开保留。全部使用真实 chat.prepare/chat.settle、临时平台 SQLite；这些三个身份红例没有调用工具执行器、模型或业务源。

公共入口为 MAIN Python：

```text
E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe
  tests/run_isolated_backend.py
  tests/backend/test_dsh_natural_continuation.py::<单例名称>
  -x --disable-warnings --junitxml=<独立原始XML>
```

| 日志前缀 | 原始结果 | 直接证据 |
| --- | --- | --- |
| `.implementation/natural-continuation-batch18/red-original` | 1 failed，2.45 s | `test_original_saved_results_phrase_keeps_task_and_contract`，原句生成不同 task_id，日志断言打印原句 |
| `.implementation/natural-continuation-batch18/red-new-task` | 1 failed，2.50 s | `test_new_task_after_comma_does_not_inherit`，“继续，另做一个新任务，使用刚才的结果”错误沿用同 task_id |
| `.implementation/natural-continuation-batch18/red-modifying` | 1 failed，2.49 s | `test_new_task_with_modification_does_not_inherit`，“继续，另开一个新任务，把刚才的PPT改成Word”错误沿用同 task_id |

每个前缀都有 `.log` 和 `.xml`，没有覆盖成绿灯。三份日志报告给根代理后才实施产品修改。

修改前产品 SHA256：

- delivery_contract.py：`afcd5955cd191e5704595bf0ef3fc1e8a9f44fa715b52ac7ea5fc3954e68d621`
- tasks.py：`ad5fbaf2dbbbb4abdcbb9e7c25116e17b1e97ede9c239e2eebf7ef609c591b07`

## 最小实现

`delivery_contract.py:17-58` 增加/调整三处任务意图辅助：

- `task_intent_text` 只为任务判定屏蔽成对书名号、中文/英文引号及反引号内文字；不修改用户正文、snapshot 的 submitted_content 或传给 update_contract 的文本。
- `blocks_implicit_task` 按分句开头的明确命令判断另开/取消原任务、咨询及否定执行。没有用全文“新任务”“不要继续”关键词一刀切；“不要新建任务”、后句“不要继续查询数据”、标题《新任务指南》和引用的另开命令均保留正确身份。
- `is_continuation` 保持动作开头及回指要求。只在原回指词之外增加“使用/沿用/复用/基于/用 + 已有/已保存/现有/之前 + 结果/查询结果”的有限短语，不将裸“结果、数据、已”作为自动续办依据。

`tasks.py:129-134` 用意图文本执行隐式修改判断，让明确任务切换的否决同时约束 continuation 和 modifying。显式 task_id 优先分支和真实澄清选项分支保留。后续同资源范围、持久任务状态、原草稿迁移、合同合并仍走原逻辑。

没有修改 dispatch_state/tools/resume/chat 的未知操作、halt、成功回执恢复或权限机制；恢复正确 task 身份后由这些既有机制继续保护原事实。没有复制 halt 标志到新 Run，没有扩大全会话扫描范围，没有重新派发未知操作，也没有放宽旧引擎短句恢复函数。

## 已执行的绿灯及范围

根代理原先只允许三个短红例；之后明确放行新增单文件，首失败停、总预算 180 秒。执行前已通知根代理。正式执行用 MAIN Python 的外层 subprocess timeout=180 包住相同隔离 harness；无用例筛选，`-x`，一次运行完成。

```text
tests/run_isolated_backend.py tests/backend/test_dsh_natural_continuation.py
  -x --disable-warnings
  --junitxml=.implementation/natural-continuation-batch18/green-first.xml
```

结果：**43 passed，0 failures，0 errors，0 skipped，59.62 秒**。原始输出还报告 6441 warnings；本报告不将其隐藏或改算失败。日志 `.implementation/natural-continuation-batch18/green-first.log`，XML 同前缀。

| 新专项内容 | 数量 | 实际证明 |
| --- | --- | --- |
| 原始三个身份反例 | 3 | 原句继承；逗号后明确新任务及 modifying 旁路不继承 |
| 自然续办与执行限制 | 11 | 查询限制、否定新建、书名号/引语、旧“剩下的”“重试刚才”继续保留原 task/draft/清单/结构约束 |
| 新任务、咨询、否定、仅引用回指 | 12 | 不因全文出现继续、刚才或被引号包住的修改文字绑定原 task |
| 引语屏蔽不改合同正文 | 1 | 《新任务指南》5页 PPT 的结构约束 source_text 保留完整书名号，submitted_content 保持原句 |
| failed/interrupted | 2 | 两种未完成终态可继续原 task 和约束 |
| completed 与最近主题 | 2 | 短句和自然句均不恢复已完成的最近任务，也不回捞更早 partial 任务 |
| 显式 task_id 优先 | 3 | 原句、明确新任务、带修改词的明确新任务，传显式 task_id 时仍按现有 API 合同定位 |
| 资源边界 | 2 | 未传资源时按旧任务继承；替换选中 File 开新 task；隔离 File 归属改变后真实 prepare 再次验证并拒绝 |
| HTTP/MCP 未知操作及最终收口 | 4 | 各 provider 的不重调/尝试重调分支；真实网关与派发日志保留，只有末端 provider 为计数内存桩，调用数仍为 1，原 key/ToolCall/Run 引用及 receipt 不变，最终 interrupted |
| halt 前后排队顺序 | 2 | 使用既有 failing_source 内存执行器；原 Run 的停止事实已持久，新 Run 自身没有复制 halt，但同 task 阻断两类取数，本地工具仍成功 |
| 真实 PPT 草稿 | 1 | 真实 create_plan/write_slides 写第一页后 partial，自然原句续办重新取得相同 draft_id 与 written_count=1 |

合计 43 个独立新增参数化用例。原始三红也包含在最终 43 项内，不能把红绿运行相加为 46 个独立用例。

未知支路还通过真实 current_datetime 成功回执证明 validated_resume_calls 只恢复前轮成功项、不把失败 uncertain 回执当成功复用。四例均经实际 chat.settle 核实，即使续办轮完全不重调原外部工具，仍不能声称已完成。

halt 支路复用了已有测试夹具 `failing_source`：Worker 与持久 Run 真执行，ToolGateway.execute 的最末调用行为由已有计数夹具模拟；不将其称为真实业务数据库连接测试。没有发送 SQL、访问业务 HTTP/MCP endpoint 或调用模型。

三文件 AST parse 通过，`git diff --check` 通过；仅提示仓库既有 LF/CRLF 规范化信息。最后 git status 只有授权的两产品修改与一新测试文件。

## 当前未验证与范围边界

- 未运行相邻旧测试大组、统一门禁、构建或浏览器；43/43 不是全量验收。根代理需协调后续测试窗口。
- 没有在本新增专项中生成原句对应的真实大屏/XLSX；真实静态交付链已有 B17 专项，当前专项验证原句的身份、合同、成功回执、未知/停止保护和真实 PPT 草稿，不将两个证据直接拼成新的完整浏览器成功。
- 资源再次校验用的是隔离 File 归属变化，不冒称覆盖所有角色/服务令牌撤权组合；原来的细粒度权限实现未修改。
- 这是有界中文命令与结果回指扩展，不是通用自然语言理解。复杂嵌套/未闭合引语、任意跨句冲突、跨多个既有任务指代及任意另开表达未全面覆盖。单次消息同时要求继续旧任务并创建新任务，仍没有两个 task 的自动拆分协议。
- 完成任务的显式修改支路和显式 task_id 原有行为保留；普通自然继续不绕过 completed 门控。显式身份与文字相互矛盾时仍按既有 API 优先级，不在本修复里偷偷改变合同。
- B17 门禁/浏览器运行与修复由根代理独立负责，本轮没有触碰其树或旧夹具，也不重写其失败历史。

## 首版 43 项阶段 SHA256

| 文件 | SHA256 |
| --- | --- |
| backend/app/business_worker/delivery_contract.py | 3a1f8d5e31c5e8f780f2a7ff8f7a5d7d3370fa56003897b9d554e5cfcb5b2f27 |
| backend/app/business_worker/tasks.py | 2d2287a9471de4e2919ef418495a357ac6ef14976a7ba65c94cba92fc2b24844 |
| tests/backend/test_dsh_natural_continuation.py | af0827b1c51308d07887c3b3ba5e06cb4df328f4d8dec3d85d803b166e2d47cc |
| red-original.log | e9913803d45e4994d704474d20c792132e03eccba852b497520250224940da95 |
| red-new-task.log | eff2f31f074194e671057495ac7569e1cc08920c8eae5c43abe18ecd6f3e7a5f |
| red-modifying.log | 46770d2b9181dbbac9b077057f7f119019e8d4aa54837e77f5ace22a0dfa0562 |
| green-first.log | fcc909b72bfc94a5a6e834d8990ef2253a8f734a89fc4653e3daf15fedf2215a |
| green-first.xml | a434185f11ef7860e888baf5e71803af0b15a246a3a2c436be4902388d00e452 |

上述日志都在 `.implementation/natural-continuation-batch18/`，原始红绿文件保持独立。三文件在此次专项后冻结，等待根代理安排后续独立复核和相邻门禁窗口。

## 根代理复核追加：引用对象与继续解释咨询

根代理复核指出，首版把所有引号内容从 modifying 判定中移除，会回退已有明确修改句。另一个疑点是原来 leading 包含“如何”会否决的继续解释咨询。此时工作树已由根代理快进至 `4f47d51eb2deb1de7e39dccd21310c9133e1da8e`，只增加 B17 测试/文档；执行前重新核对过 B18 三文件仍为上表 43 项阶段字节。

按新增授权最多运行两个短单例，均在 43 项已绿的产品上先取得失败：

| 新日志前缀 | 结果 | 原句及回归 |
| --- | --- | --- |
| red-review-quoted | 1 failed，3.34 s | `把“刚才的PPT”改成Word` 本应沿用 task，却创建新 task |
| red-review-consultation | 1 failed，3.19 s | `继续解释刚才的问题该如何处理` 本应保持原咨询边界，却沿用旧 task |

这些是**首版 B18 引入的回归**，不能归为原始产品缺陷或测试夹具错误。两个 `.log/.xml` 均保留；旧 `green-first.log/xml` 43 项证据没有覆盖。

还通过只读 git show 取得 b7241b 基线文件，从 AST 中仅抽出实际 `is_continuation` 等意图函数及 `modifying` 表达式在内存执行，比较基线与首版 B18：

- 引用对象句：基线 continuing=False、modifying=True、implicit=True；首版 modifying=False、implicit=False。
- 继续解释咨询句：基线 continuing=False、modifying=False、implicit=False；首版 continuing=True、implicit=True。

这个对比没有导入 app、初始化 Worker 或连接数据库，不算新的真实 Worker 测试。第一份诊断 stdout 未设置 UTF-8，中文经 Windows 解码显示乱码；原文件 `baseline-review-comparison.log` 保留，另以显式 UTF-8 文件写入保存可读的 `baseline-review-comparison-utf8.log`，没有覆盖或把它当第三个 Worker 单例。

### 局部修正

- 新增 `task_modification_text`：由引号外紧邻的“把/将/修改/调整/更改”动作指向的引用文本作为真实修改对象保留。单独被引用的整条指令仍屏蔽。因此 `修改《刚才的PPT》` 保持旧任务，`说明这段文字：“把刚才的PPT改成Word”` 不因引语内动作继承。
- `blocks_implicit_task` 仍使用屏蔽引语的意图文本。明确另开任务再修改 quoted 对象仍受共同否决；成果标题中的“新任务”不会变成另开命令。
- 咨询只新增首命令“继续/接着 + 解释/说明/讲解/告诉我”且本分句包含如何/怎么/怎样/是否/能否的有界识别，没有恢复全文关键词否决。`继续生成刚才的Word，内容说明如何使用已有结果` 仍可续办。
- 用户原文、交付合同 source_text、资源/状态/显式任务身份逻辑继续保持。

### 最新实际验证与冻结

在根代理允许的窗口，先通知再以 MAIN Python 和同一 isolated harness 执行新增单文件，外层总预算 180 秒、`-x` 首失败停。一次完成：**53 passed，0 failures，0 errors，0 skipped，76.81 秒**，原始输出报告 7906 warnings。新日志/XML 为 `green-review.log/xml`，不是覆盖旧 43 项证据。

新增 10 项包括上述两个单例、三个引用对象变体、一个正常制作后附解释文字的正例，以及四个完整引语/明确新任务 quoted 对象/继续解释咨询对照。其余 43 项继续通过，包括原句身份、未知 HTTP/MCP 与最终收口、halt 双时序及真实 PPT 草稿。

三文件再次 AST parse 和 git diff --check 通过。没有跑相邻大组、统一门禁或浏览器。工作树另有其他代理负责的 `test_dsh_presentation_schema.py` 和 `presentation_schema_probe.mjs`，本轮未修改。

| 最终文件/新证据 | SHA256 |
| --- | --- |
| backend/app/business_worker/delivery_contract.py | 01180f565755a0bda5775b234b2509d16b7ac32d0b44f06446d66c3f6321a2fd |
| backend/app/business_worker/tasks.py | 17ac39974fe8fa9ffbf21188677801998353c787c6b561096bc77f22cebf2988 |
| tests/backend/test_dsh_natural_continuation.py | 7d226415bb402f9d7e1226ae502560d439ab04addbcc648cd450b6a73347c8e4 |
| red-review-quoted.log | 6742f2c45d31b114154bd5d680495e3409b372f8753ed9362fb821c2c6a7531d |
| red-review-consultation.log | 97c78937d98c399b3d220df3eeb8ae261543958f285072edae5cda2d01fade6f |
| baseline-review-comparison-utf8.log | bdf6457f5170f6599611b2f3e07b1122b54960dab183884b52b75791e93fe4f7 |
| green-review.log | 7819789800ec6a1f524e87244b22f3b85c6be00ade750adb5fca3ed86351bb8a |
| green-review.xml | 8b2da5d2e5929b6985c18032701d219bdc95dece98b20e739fdeaf271ad2a7e2 |

最新三文件在 53 项专项后重新冻结。前部“未验证与范围边界”继续有效：本单文件不构成相邻全量、真实大屏/XLSX 或完整浏览器验收。

## 根代理追加授权：原始自然句的留存结果实际交付

本节补齐上一阶段明确未覆盖的“原句实际大屏/XLSX”Worker 集成路径。根代理额外授予 `tests/backend/test_dsh_halted_task_saved_deliveries.py` 唯一写入权；仅将现有 `test_same_task_continue_delivers_q_but_keeps_sql_schema_and_profile_stopped` 参数化为 `继续`（`short-continue`）和原始句 `继续，使用已有结果制作大屏和Excel`（`saved-results-natural`），并让原 `prepare` 使用该参数。fixture、源查询、快照 loader、真实 exporter、权限及其全部断言未改。

执行前旧测试文件 SHA256 为 `174dc966752d28eb6ccff7885ae2707b8f8d6bc81cc8b082672834e60e8d4b82`。开跑前已通知根代理，只执行新增自然句一个参数化单例一次，未重跑短句参数、旧文件全组或已经通过的 53 项：

```text
E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe tests/run_isolated_backend.py tests/backend/test_dsh_halted_task_saved_deliveries.py::test_same_task_continue_delivers_q_but_keeps_sql_schema_and_profile_stopped[saved-results-natural] -x --disable-warnings --junitxml=.implementation/natural-continuation-batch18/green-saved-deliveries-natural.xml
```

实际一次结果：**1 passed，244 warnings，pytest 输出耗时 3.81 秒，进程退出码 0**；外层子进程总耗时 11.016 秒（含 Python/pytest 启动），预算 180 秒。独立 JUnit XML 记录 tests=1、failures=0、errors=0、skipped=0，suite time=3.181 秒、case time=2.980 秒。两种计时来自不同计时边界，原始证据均保留，不互相替代。

此例使用真实 Worker 工具路径和隔离 runner：平台临时 SQLite、两份测试专属本地 SQLite 源、临时文件存储；模型调用和结构刷新被 fixture 明确禁止，runner 禁止外部网络及真实 MySQL/PostgreSQL 驱动。实际业务路径与既有断言证明：

- 来源 A 完成真实本地 SELECT，QueryHistory 留存两条原始数据；来源 B 在连接边界注入故障，未向 B 发出 SQL，原 Run 保留 `database_access_halted=True`。
- 将前轮收口为 failed 后，原始自然句建立同 task 的后续 Run。恢复记录只包含来源 A 已存 queryId，task 级 halt 保持。
- `create_dashboard_from_queries` 实际创建静态 Report，refresh_seconds=0，queryIds/sourceQueryIds 均为原 Q；所有 verified widget 的 data/chartDataParsed 与两条原数据逐值相同。
- `export_query_spreadsheet` 实际生成新 Run 的 Artifact，并通过存储读取 XLSX、openpyxl 解包核验“查询结果”两行分别为 `(1, 设备甲, 12)`、`(2, 设备乙, 18)`。
- 后续来源 A SQL、来源 B SQL、来源 A schema、来源 A profile 四次请求全部返回 `database_access_halted`；原 Q、原成功/失败 ToolCall 回执与前轮 halt 未被改写。
- 既有计数断言在首轮结束及全部交付/拒绝结束均通过：`case.reads == case.connections == [A.id, B.id]`。即业务 execute_read 尝试数 **2**、连接边界进入数 **2**；其中 A 有 **1** 次真实本地业务 SELECT，B 在真实源连接前失败。halt 后以及同 task 续办后的新增业务查询/连接边界尝试均为 **0**。此计数不包含 fixture 建表插入或平台 SQLite 操作，不能表述成整个进程只执行两次 SQL。

本轮静态 AST parse 与限定文件 `git diff --check` 通过。两份产品源码和新增 53 项测试的 SHA256 与上一节完全相同；未修改其他代理的 UI guard、network writer 或 schema 文件。新增参数化测试随此次专项再次冻结，没有提交、构建、门禁或浏览器运行。现在可以声称“原始自然句的实际静态大屏/XLSX Worker 集成单例已验证”；仍不能声称 Vue 页面、下载交互或完整浏览器验收已通过。

| 本轮文件/证据 | SHA256 |
| --- | --- |
| tests/backend/test_dsh_halted_task_saved_deliveries.py | 0084b916543df9f9446c8cd6aa492d44db7d407edde2a12bb6f67dacd08f11ed |
| green-saved-deliveries-natural.log | 0be13ceabbfb6acd0c1b0ff0fb77e52babff0ce1a7fe8845b4dd9696b6d72c11 |
| green-saved-deliveries-natural.xml | 23c3627f7e10f3f2c4b3fe7964c7440d2e4ddfbb7826d969d41edbbe185e1394 |

本节日志/XML 位于 `.implementation/natural-continuation-batch18/`。原三份红例、43 项绿例、根代理复核追加两份红例及 53 项绿例证据均保留，新增组合结果不改写其历史。
