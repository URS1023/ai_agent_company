# B20 逐份成果修订与证据继承

2026-10-04。唯一可写树为 `E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-artifact-revisions`，分支 `codex/major-artifact-revisions`，实施基线 `faaa6de447c9af7811ec6fc8a69d56e2de5224e5`。本批保持未提交，未合入另一树正在验证的 B19 回执修复。未改 MAIN、B18、B19 树、共享依赖、chat/tools/schema/Host、构建、门禁或正式文档。

## 原始行为证据

先将 B18 已准备但未运行的 ignored 模块移入新增 `tests/backend/test_dsh_artifact_revisions.py`，原始模块 SHA256 为 `131d7981dd8c7a44c4cffcda3a3607b861522c45c43badf2373305aa67ecb6e6`。两个单例分别只跑一次，各自保存首失败。

| 轮次 | 实际结果 | 原始文件 |
| --- | --- | --- |
| R1：旧 PPT 跨 Run 原样 present 后声称改完第 1 页标题 | 1 failed，pytest 4.30s，外层 16.312s；最终业务断言 expected partial / actual completed | `artifact-revisions-batch20/red-r1.log` 与 `.xml` |
| R2：只改 PPT，保留旧 Word/Excel | 1 failed，pytest 3.80s，外层 14.063s；实际新标题已变，旧文件字节完整，最终 expected completed / actual partial；缺 Word/Excel 各 1 | `artifact-revisions-batch20/red-r2.log` 与 `.xml` |

两次均进入最后业务状态断言；没有夹具异常，没有用尚未新增的 revision 字段缺失制造红测。R1 旧/新 Artifact 分别为 `01M42TCHFTQN7K4P1BW077AGM2`、`01M42TCHR80FRA4JNE8QJ17GFF`，两份最终 SHA 均为 `101f81a9d23ca255803783a3983f0ba3665e4b1e6eebdb5f5f0b6e0d0517a919`，源 SHA 均为 `2d8cbc633849ec2b6911ff3985786f09b28829116af2e856a905bcb923256ae5`。真实 PPT 标题未变。R2 用 python-pptx、python-docx、openpyxl 读取最终三种文件，原历史 Run 快照及旧文件字节保持相等。具体内容及身份保存在 XML 的 `revision_evidence` 属性。

## 实施范围与验证含义

只修改三个产品模块：

- `tasks.py`：首次修改已有任务时建立逐份对象，后续从每份对象的当前有效证据计数；未进入修订的原任务仍走原逻辑。保留原 `required_deliveries`、原约束、原始 native terminal 状态及历史交付列表，不删文件、不按全部 currentRun 过滤、不按全任务 SHA 去重。
- `delivery_contract.py`：稳定 `object_id/type/ordinal`，逐对象 `revision`、基线 `base`、变更 `change` 与请求时间，纯继续不升号；相同 pending 请求 queue edit 不重复升号。`checks` 按字段保留同句及跨轮未完成的全部修改，仅同字段明确新值替换旧值，未知具体附加修改保留 unsupported。未指定修订的 Word/Excel/大屏保留原资格。明确具名对象优先，多份同类未明确、历史候选超过份数均保持未完成。显式格式替换仍跟随既有 API 合同同步 slot。
- `delivery_evidence.py`：当前身份重新鉴权、重新读取最终存储字节并比对 Artifact.sha256；从真实文档正文、有序幻灯片/工作表及 XML 实际引用的关系构建规范化 XML/二进制指纹，不遍历整个 ZIP 目录。孤立 XML、未被正文使用的关系、ZIP 重压缩和 docProps 变化不计为正文修订；XLSX 共享字符串按实际单元格解析。PPT 第 N 页标题读取真实页序的标题 placeholder，逐项精确比较目标文字，并同时屏蔽所有目标标题后核对原基线其余正文及非目标页标题。修改总页数仍验证实际非空页数。新 Run/new id/new 最终 SHA 单独均不构成完成依据。

`delivery_objects[].fulfillment` 保存实际 Artifact/Report 身份、最终文件 SHA（文件）、内容指纹、来源 Run 等证明；`revision_receipts` 是本次重新核验通过的列表。仅候选先满足该对象原页数/嵌图合同后才固化。随后仍执行完整原合同份数与唯一分配检查，逐对象检查的 count=1 不替代总合同。错误页数的先行文件不会锁住 slot，后轮真修好可以补正。先前已经绑定的文件遇到撤权或字节损坏时不抹掉身份，恢复原证据可继续使用；不能偷偷换另一份旧文件填空。

跨 Run 修改记录旧 source Run 集合；旧轮次迟到的新文件即便登记时间较晚，也不能越过新修订边界。新文件在历史中仍保留。大屏沿用现有授权、真实留存数据验证、Report.version 与来源晋升逻辑，并增加 name/content 指纹。未改大屏版本协议。

`native_deliveries.py` 无需改动。跨 Run 合法登记新 Artifact 仍可发生，但其新身份不再自动获得业务修订资格；同字节不同真实路径的独立交付语义保持原样。未增加依赖、数据库字段、外部服务或自动工具重试。

## 执行环境与逐轮记录

所有动态测试固定 MAIN Python `E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe`，`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，经本树 `tests/run_isolated_backend.py`。各次 `-x --disable-warnings`，独立 log/XML；新增组及相邻组外层 subprocess timeout 为 180s。仅临时平台 SQLite/临时文件，未读取 .env、未连接真实源/模型。新增文件 autouse fixture 对源查询、源连接、源元数据及模型入口均计数并拒绝实际调用；所有已执行专项 XML 的四项计数均为 0。

| 顺序 | 结果 | pytest / 外层耗时 | log/XML stem |
| --- | --- | --- | --- |
| 原始 R1 | 1 failed，产品行为红 | 4.30s / 16.312s | `red-r1` |
| 原始 R2 | 1 failed，产品行为红 | 3.80s / 14.063s | `red-r2` |
| 初修后两原例 | 2 passed | 5.53s / 16.046s | `green-initial` |
| 第一批扩展 | 11 passed | 21.90s / 30.453s | `boundaries-first` |
| 扩展前静态 AST | 新测试中文 bytes literal 语法错误，pytest 未启动；修为 `.encode()` 后 AST 通过 | 不属于产品红测 | `static-fixture-error.log` |
| 第二批扩展 | 20 passed | 44.37s / 53.187s | `boundaries-expanded` |
| 新增大屏对照 | 3 passed，20 deselected | 5.55s / 13.844s | `dashboard-boundaries` |

23 个专项在第一版候选产品字节上分 20+3 执行；后 3 个仅增加测试，当时产品未改。覆盖：原始 R1/R2；失败修订后继续；仅 ZIP/docProps 改动、改错页、换错基线；两份具名 PPT 的独立继承及绑定页数；未明确对象的拒绝；同次两份修订跨两 Run 补齐；队列编辑不清修订；已有修订后追加独立 Word；旧历史候选超份数；旧 Run 迟到交付；错误页数后补正；篡改/撤权后恢复；真实 Word/Excel 原样与内容变化对照；PPT 修订继承原大屏；新 Report id 内容不变/确实改变的对照。后续自审及独立复核确有追加修复，不能把这批 23 绿当作最终版本全部验证。

相邻离线第一轮：`adjacent-compound.log/.xml` 67 passed，pytest 113.43s / 外层 122.797s；`adjacent-native-continuation.log/.xml` 85 passed，pytest 105.81s / 外层 116.125s。前组是 compound contract/boundaries 两文件；后组是 native deliveries、natural continuation、multiple presentation drafts 三文件及既有 dashboard 修改负例。两组均在 180s 内通过，无首失败。这是后续基线保护/累计要求/可达指纹修正前的真实历史，不冒称其已在最终字节重新全跑。

## 追加自审与独立复核记录

1. 首次建立修订基线前已撤 `files.view`：`red-baseline-scope.log/.xml` 1 failed，3.30s / 13.890s，预期 prepare 拒绝但实际创建 Run。测试尚未走到恢复权限后的断言，因此当时只实测“错误建立了新基线”，未实测后续误 completed。第一尝试仅在逐行鉴权处抛错仍未关闭：`final-specialist.log/.xml` 23 passed + 1 failed，49.51s / 60.781s，首失败停。原因是 ORM 已先隐藏 Artifact，逐行代码没有收到原文件；这属于自身产品补丁不充分，不是夹具错误。
2. 现改为首次基线采集前检查文件读取权限，再对照同任务已保存交付清单，防止资源过滤/丢失/损坏被误当作“原来没有文件”。无法核验旧证据时在新 Run 创建前拒绝，恢复后可以重新发起。已有基线之后的撤权仍使用原 partial/恢复路径，不新增工具重试。
3. `baseline-protection-focused.log/.xml` 先通过权限/损坏两例，随后 CSV 夹具失败，1 failed + 2 passed，7.79s / 17.625s。既有 `\bcsv\b` 解析不把紧邻汉字的“和CSV”当 CSV 义务，夹具缺少前置合同断言；未改产品解析器，改为“再生成 CSV”并显式断言类型。`remaining-protection-focused.log/.xml` 2 passed，4.11s / 12.000s，验证 CSV 最终字节继承及附加关系分片边界。没有把夹具失败登记为产品原始红。
4. 根代理独立静态复核提出同句/跨轮多项要求及孤儿 XML 风险后，停止原最终整组计划，另取三条真实 Worker 红。`red-multiple-title`：只修改 page1，page2 未改，actual completed，1 failed，3.76s / 12.438s；`red-cumulative-title`：次轮只改 page2、保留的 page1 仍旧，actual completed，1 failed，3.28s / 12.047s；`red-orphan-word`：正文完全相同，仅新增未被 rels 引用的 `word/revision-marker.xml`，actual completed，1 failed，4.39s / 12.219s。三条均到达最后业务状态断言；实际标题/段落/SHA 记录在 XML 属性，源/模型计数仍 0。
5. 修复后 `review-fixes-focused.log/.xml` 6 passed，8.50s / 15.546s，覆盖三条负例、同句/跨轮全部改完的两正例及未知附加字体修改仍 partial。新增可达指纹规则下，未被正文使用的坏 `.rels` 不应强制否定已正确改变的正文，因此该旧附加分片测试改为“正文不变、仅增加孤立坏分片”仍 partial，不把包内无关成员当变化依据。

`review-extra-boundaries.log/.xml` 随后 2 passed，3.44s / 10.546s：关系表新增指向孤立 XML 的关系但正文完全没有使用该 rId，仍 partial；同页新明确标题可以替换 pending 旧值并真实 completed。以上每次日志/XML均保留，未用后来的绿覆盖原始失败。最终本地验证与冻结字节见末节。

## 当前边界

- 此批确定性内容语义只解析单类型对象的明确 PPT 页标题（可以多项/累计）、页数，或未给具体字段的重做/重新生成（必须可达正文内容变化）。无法确定性验证的具体 Word 标题/段落、Excel 单元格、样式/颜色等请求保守保持 partial；没有声称已实现通用自然语言编辑器。
- 指纹是规范化可达正文结构及被引用资源，不是任意自然语言语义正确性或像素等价性的证明。未引用的模板/主题/孤立成员不参与正文变化资格，不称这套规则验证了所有 Office 样式和渲染路径。精确 PPT 标题变更要求其余可达正文结构、非目标页标题相同，可能拒绝同时做了未请求额外修改的文件；这是有意限制。
- 旧合同首次修订时，历史同类候选多于份数且没有既存 slot 映射，当前拒绝自动猜“最新”。未实现复杂历史候选的交互消歧或手工绑定 API。
- 当前具名 selector 使用已绑定对象的原始业务标签；并未全面实现引用改名后的所有称谓、跨格式变换、多种格式同时修改或任意分句意图识别。
- 新大屏对照使用临时 SQLite 中真实 Report 与合成留存 Q，经真实 Worker settle 验证；未调用业务源，没有假工具回执。未新增“同一 Report 相同内容保存版本递增”的全工具链动态验收，不将新 id 对照夸大为这项覆盖。
- 本批动态覆盖跨 Run 修订及既有对象的 queue edit，不宣称解决初始 Run 尚未建立对象时的全部中途 steer/并发迟到语义。未改变冻结 chat/tools 事件协议。
- 未运行浏览器、真实模型、真实业务库、构建、统一门禁或发布；旁树 B18/B19 的门禁与浏览器不能作为本批通过证据。

## 字节指纹

基线产品 SHA256：tasks `909a9e8131bd5b9206d1ee164e8125ca7b88d7e7182814e24df28f49c11884f7`；delivery_contract `984856f3fe6339df8e63e3004aef5f2f9fe35354baba37e79365e6defe475997`；delivery_evidence `9af88d7d8c6a834bb544f8812b2926ec051c4fcab01cc2a42677f297449b0c04`。

第一版候选字节（23 绿及两组相邻通过时，供历史追溯；最终字节将在末节另列）：

| 文件 | SHA256 |
| --- | --- |
| backend/app/business_worker/tasks.py | `c18f0ed22ef5d2461d3a65a42e14cf721b1d2ac650427aaf05efb4c1cf8c96a4` |
| backend/app/business_worker/delivery_contract.py | `e4ace8590588411dd9bb57b42448945563ed9c06d84cd5e319d15004c2e41ab0` |
| backend/app/business_worker/delivery_evidence.py | `9437c451ef3a23e6c4f2241d57d18e4ba31347b3bd895d8d386d91644c69ae93` |
| backend/app/business_worker/native_deliveries.py（未改） | `8ad140d8d42046178a93ec54929ce9c817dbaf08a7c347d92074fde948142b10` |
| tests/backend/test_dsh_artifact_revisions.py | `1d154c21af979fdbdfcc7371baba47dbaee582c61652f0d48b1deefe4e658dbb` |

已执行 AST 语法检查及 `git diff --check`；后者仅提示仓库既定 LF/CRLF 转换提示，无 whitespace error。没有回滚其他修改，没有提交或推送。

## 最终本地验证与冻结

最终源码上 `final-revision-and-adjacent.log/.xml` 一次 **40 passed**，pytest 输出耗时 **55.73s**，外层耗时 **63.969s**，180s 预算内完成。XML suite 精确 time 为 55.449s，0 errors / 0 failures / 0 skipped。其中新增专项完整文件 **35 项**，另五条既有路径是：

1. `test_dsh_compound_boundaries.py::test_continuation_clause_can_explicitly_change_page_requirement`
2. `test_dsh_dashboard_delivery.py::test_revision_task_cannot_count_unchanged_previous_dashboard`
3. `test_dsh_natural_continuation.py::test_actual_modification_of_quoted_previous_artifact_keeps_task`
4. `test_dsh_multiple_presentation_drafts.py::test_later_run_can_edit_first_named_draft_without_affecting_second`
5. `test_dsh_native_deliveries.py::test_identical_files_at_different_real_paths_are_separate_deliveries`

35 个新增专项全部留下 `source_model_counts`，四项全部为 0。五个既有测试仍经同一 isolated runner 的外部数据库/网络隔离，但未给它们虚构本文件专用计数。最终没有重跑两组 67/85 全文件组；它们的较早验证历史与当前 40 项验证分别记录。未进行 B19 回执合入后的验证，整合与统一门禁由根代理安排。

最终 AST 检查和 `git diff --check` 通过。工作树仍是 `codex/major-artifact-revisions`，HEAD `faaa6de447c9af7811ec6fc8a69d56e2de5224e5`。`git status --short` 仅显示三产品 M 及新增测试 `??`，未提交。

| 冻结文件 | SHA256 |
| --- | --- |
| backend/app/business_worker/tasks.py | `c18f0ed22ef5d2461d3a65a42e14cf721b1d2ac650427aaf05efb4c1cf8c96a4` |
| backend/app/business_worker/delivery_contract.py | `30a927b1729b81605fa839437d571c8dcd175a58a5a3ee9913d1bdacb936fe4a` |
| backend/app/business_worker/delivery_evidence.py | `577562460a891159e04f864201e60b9d4ff87af74f6ef8bd2b780ee7e0556d11` |
| backend/app/business_worker/native_deliveries.py（未改） | `8ad140d8d42046178a93ec54929ce9c817dbaf08a7c347d92074fde948142b10` |
| tests/backend/test_dsh_artifact_revisions.py | `9b458fd7bb21881c3e93239370a651fd909b3171e0afb3a43de112da68e1023a` |

最终 XML SHA256：`12d20fb0a3e4cf5bb03a92db063213d12cec499aead733f629548285c3aa18e8`。完整源码与历次 log/XML SHA、suite 统计、自计数行数均存于 `.implementation/artifact-revisions-batch20/final-manifest.json`，该索引 SHA256 为 `71dfeab0454a41ba1e8b2b9acf3e2ca53f8d4e08b5ecdb6d025948370d847c36`。索引不包含本报告自身，避免循环指纹；报告完成后另报自身 SHA。

关键入口定位：`tasks.choose_task:115`、`tasks.verify:211`；`delivery_contract.revision_request:213`、`revision_checks:260`、`revise_objects:266`；`delivery_evidence._revision_content:30`、`prepare_revision:189`、`_satisfies_revision:249`、`verify_revision_objects:282`、`verify_constraints:517`。这些位置对应上表最终字节。
