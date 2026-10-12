# B19 只读计划：逐份交付修订与证据继承

2026-10-04。工作树 `E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-natural-continuation`，读取时 HEAD `4f47d51eb2deb1de7e39dccd21310c9133e1da8e`。本轮仅写本 ignored 计划；未修改产品、测试、构建、门禁或共享文档，未执行测试、服务、浏览器、数据库连接或模型请求。B18 两份产品及既有测试继续冻结。其他代理正在修改门禁/UI 证据文件，未触碰或回退。

已对照旧 `docs/audit/review_20261004/plan-delivery-revisions-batch16.md`，但以下结论均以当前 B18 字节重新定位。旧计划中的自然续办身份及未知操作防重议题不作为本轮新缺陷；B17/B18 的独立修复和测试历史不改写。本节没有取得新的动态红测，后面的执行步骤都是待根代理授权的设计。

## 1. 当前四层身份及证据如何对应

| 层次 | 当前真实合同 | 已能证明 | 尚不能据此证明 |
| --- | --- | --- | --- |
| task 合同 | `tasks.py:143–149` 合并原合同，整 task 设置 `requires_new_delivery`；`delivery_contract.py:169–205` 保留 `required_deliveries` 格式/数量及两类约束 | 同 task 的原要求、PPT 总页数、Word 嵌指定大屏关系不会随纯继续丢失 | 没有逐份 obligation/revision，不能表达“PPT1 修订2、Word1/Excel1 仍接受修订1” |
| 实体文件 | `Artifact` 保存 id/run_id/sha256/parent_artifact_id；`artifact/service.py:137–149` 对字体规范化后的最终字节计算 SHA | 最终存储字节有独立指纹，来源 Run 可追溯 | artifact 新 id、新 run_id、新文件名都不等于用户要求的内容已经改变；parent 本身也不是合同 fulfillment |
| 工具成功回执与恢复 | `tools.py:155–176` 保存 task_id/source_run_id/call_id/序号及最终结构化结果；`resume.py:133–160` 只允许同用户/工作区/会话/task、非活跃过去 Run、同数据范围/plugin 的成功回执 | 查询等原成功事实可作为后续安全输入，不需要重查 | 返回历史成功 ToolCall 并没有将它绑定为新要求的修订完成证明；不能把查询复用成功推导为新 PPT/Word/Excel 修改成功 |
| 合同验收 | `tasks.py:190–223` 汇总 task 的文件/大屏，按类型计数；`delivery_evidence.py:211–242` 创建 `constraint_receipts` | 指定 PPT 的最终 SHA、真实可达页数/非空页和首屏对象；Word 实际关系引用的图片 SHA 与当前授权大屏版本对应 | 无通用“哪份成果的哪个修订由哪个实体满足”的持久映射；页数/图片关系 receipt 没有 change revision，且每次合约更新会清空本轮 receipt |

不存在一个已被这些路径读取的通用 `fulfillment`/`accepted_deliveries` 表达。`requirement_id` 当前代表页数或嵌图规则：`delivery_contract.py:69–70` 按 kind/target/ordinal 生成身份，页数更改可以沿用同一 requirement_id 并替换数值（:189–203），它不是一份文件的修订号。

### 文件与大屏各自的当前边界

- **PPT**：`draft.py:14–37` 将具名草稿放在稳定 task 目录；:278–319 是同一草稿内容覆盖，:523–550 检查正文已写齐。`builtin_provider.py:1053–1180` 从草稿/参数渲染后新建 Artifact，metadata 有模板、实际页数、spec_summary，但没有绑定旧 artifact/合同目标修订。`test_dsh_multiple_presentation_drafts.py:75–84` 验证次轮改第一稿不改第二稿，尚未到“新旧最终文件 + task settle fulfillment”的组合。
- **Word**：`builtin_provider.py:1139–1180` 解析真实图片后渲染文件，新建当前 Run Artifact。`delivery_evidence.py:224–242` 仅在存在嵌大屏合同约束时读取 Word 最终字节、检查被实际引用图片的 SHA；普通 Word 标题/段落修改没有相应变更规则。
- **Excel**：`query_spreadsheet.py:27–49` 重鉴权旧 Q、真实导出、记录 source_query_id，再新建当前 Run Artifact。`analytics/exports.py:34–99` 从授权留存数据写真实值和来源页，不发新查询。这是数据来源证明，仍没有“Excel1 修订2 应新增/修改的标题、列或布局”绑定。当前 `verify_constraints` 的分支只有 page_count/embedded_dashboard，无 Excel 单元格变更 fulfillment；普通文件计数只检查 ready 和存储存在，不能把 SHA 保存在 Artifact 行上等同于本次 settle 已读取核对全部字节。
- **dashboard**：存在真实 Report.version 和不可变 ReportRevision，`reports/service.py:99–107,245–254` 每次保存追加修订；`dashboard_deliveries.py:174–207` 对旧对象的新修订要求当前 Run 非 reused 的真实创建回执、version 与保存对象一致且大于以前交付版本，旧对象直接复用仍保留原 source_run_id。`query_dashboard.py:232–255,307–320` 还会核对原参数指纹、版本和结果指纹。以上保护必须保留，不能把大屏问题笼统写成“没有版本”。
- **大屏版本的限制**：`reports/service.py:128,245` 明确规定内容不变也会递增版本，`tests/backend/test_reports.py:489–517` 有相同内容保存后 version 递增的已有测试。因此 version 增长证明保存动作，不能单独证明“标题改成 X”已满足。是否存在真实 Worker 工具在保持旧标题时仍给出可晋升的非 reused 新版本回执，本轮未沿所有 dashboard 修改工具跑通；其误完成结论是待复现推断，不列成已取得缺陷。
- **Word 与大屏版本关联**：`delivery_evidence.py:122–151` 会固化 bound_report_id、要求当前版本或明确请求版本、核对真实 render_dashboard_image 回执、图源 Q/执行 id、图片解码及最终 SHA；旧版图片不能冒充当前版。指定历史版本而当前对象已变化时现实现保守拒绝，未实现完整历史版本来源重建，不能以 ReportRevision 表存在就声称历史图可验收。

## 2. 两条确定的源码缺口与待执行结论

### 路径 A：整 task 新交付开关排除了无关成果和同次修订的早先成果

1. A 真实完成 3 页 PPT、Word、Excel，task completed。
2. B 同 task 请求“只修改刚才的PPT第1页标题为已修订，其他已交付文件保留”。不提供用于整体替换的 `required_deliveries`。
3. `choose_task` 在 `tasks.py:143–147` 设置整个 task 的 `requires_new_delivery=True`；`update_contract:174–177` 合并保留 Word/Excel 义务。
4. 即使 B 真正交付符合新标题的 PPT，`tasks.py:212–215` 也排除 A 的 Word/Excel，只计 B 的文件；`delivery_evidence.py:219` 另有同一当前 Run 过滤。因 Word/Excel 计数不足，原生 completed 会在 :218 降为 partial。

**源码确认**：没有逐份资格，过滤同时影响所有格式。**待动态复现**：按下节 R2 真实三文件路径取得原始失败与精确 missing 列表，不能把本次阅读写成红测。

同次修订跨轮也受影响：B 的修改交付只有一部分而 partial，C 纯“继续”由 :146–147 继承 requires_new_delivery，但只计 C 的输出，B 本已完成的目标修订文件不算。该延伸由相同条件可推导；本轮不再扩成额外大测试。

### 路径 B：跨 Run 原样 present 可以取得“本輪新文件”资格

1. A 请求并实际交付 3 页 PPT，使用 `native_deliveries.present` 登记后 settle completed。A 的原生文件还留在服务端稳定会话工作目录（`native_deliveries.py:61–68`）。
2. B 以明确原 task_id 请求“修改刚才的PPT，把第1页标题改成隔离修订标题”。新目标不是总页数，因此旧 3 页规则仍在，另有 requires_new_delivery=True。
3. B 原样 present A 同路径文件。`native_deliveries.py:128–145` 只查当前 Run 内同路径和 source_sha256；找不到 A 的 Artifact，便创建 B 的新 Artifact，source_run_id=B，跨 Run parent 为空。
4. `tasks.py:212–213` 把它算为本轮 presentation；`delivery_evidence.py:163–207` 页数验收能确认最终文件真有 3 页，却没有“第1页标题已修改为指定文字”的校验。没有别的缺项/阻断条件时，现有判定静态可达 completed，而最终文件仍是旧标题。

**源码确认**：当前 Run 查重、当前 Run 资格、缺少逐份修订/指定标题证明三点。**待动态复现**：重新登记和最终 settle 的具体返回值、最终存储 SHA 是否完全相同都需 R1 实跑。不能预先把 Office 规范化后的 SHA 相等当保证；原生 source_sha 可相同，最终 ZIP/规范化字节可能相同或变化，两种都不能证明标题已修改。

同类对象处理差异也要保留：`reference_saved:44–53` 直接引用旧下载链接会保留原 source_run_id，不能把它改成新 Run 来解决计数；同 Run 同路径不同内容现已设置 parent 并 supersede 旧版（:135–153），已有真实测试，不能误报这条幂等已失效。

**另一个已定位但不在首两红例扩跑的入口**：`chat.queue_update:738–742` 对 queued 修改请求编辑后无条件清 requires_new_delivery。旧页数/图片关系未变化时，可能无需本轮文件就重新接受旧成果；`chat.steer:425–428` 更新合同也没有独立修订 epoch。前者清标志是确定代码事实，完整误完成/工具晚到组合仍待受控复现。后续修订资格接线时不能仅修 present/计数而漏掉这两个入口。

## 3. 最小数据与资格调整建议（未实施）

沿现有 `Run.context_snapshot.business_task` 增加版本化逐份义务和 accepted 证明，先不新建通用工作流表、不改 Host 执行循环。最小关系为：

```text
obligation_id + target_revision
  -> 用户变更来源（message_id/request_id/sequence、提出变更的 Run）
  -> base_artifact_id + base_final_sha 或 base_report_id + base_report_version
  -> accepted artifact_id/final_sha 或 report_id/version
  -> 真实 producer / ToolCall 或 native-present 事件、验收规则版本及结果
```

1. obligation_id 由服务端分配且稳定；具名 PPT 草稿、明确选择的原文件/大屏提供对象锚点。两份未命名同类文件也应是两个 slot。不能按文件名、当前数组位置、模型临时填的 id 猜对应关系；不能唯一定位“要改哪份”时保留待明确状态。
2. 仅明确变更涉及的 slot 升目标 revision；纯继续、同请求重放不升。保留未修改 slot 的 accepted 证据，不缩掉原 required_deliveries，也不靠让用户发送显式新清单来绕过继承问题。现有显式 required_deliveries 的整体替换语义另行保持。
3. `tasks.verify` 计数与 `delivery_evidence` 实体资格共用一个服务端判定：同 slot/同目标 revision、真实来源、当前身份可读、最终存储可核验。纯继续允许 B 的已验收新修订在 C 继承；更高目标 revision 不再接受其旧证明。不能只删除当前 Run 过滤。
4. 新 Run 新 Artifact、新 filename、新 ZIP SHA 都只证明创建/存储；修改要求需另外核对可机器验证的目标字段。例如 PPT 指定页标题从可达 slide XML 读取；Word 指定标题/段落从实际 document 读取；Excel 指定 sheet/header/cell 与受授权 Q 的对应从真实 workbook 读取。只更改包内时间/重压缩不能冒充内容修改。暂不能精确编译的自然变更应明确未核实，不能伪造通用“语义通过”证明。
5. 一份内容相同的文件可合法用于两个被明确要求独立交付的 slot；禁止全任务按 SHA 去重。现有 `test_identical_files_at_different_real_paths_are_separate_deliveries` 必须保持，修订资格不能改变无关的原生文件登记合同。
6. 当前 dashboard.version/非 reused 来源与 Word 当前版图关系沿用；增加目标字段验收时区分 Report 的保存版本与业务义务 revision。原 Artifact/ReportRevision 和历史下载保留，只更新哪一项当前义务接受哪条证明，不能全局删除或覆盖旧历史。
7. queue edit 以本次用户请求的继承基线重编变更，不清空修订资格，也不每次编辑重复升号；steer 接受后绑定新目标 revision，旧目标执行完的迟到回执仅留历史，不能自动满足新要求。旧工具 unknown/halt、资源范围和显式 task_id 优先级继续独立生效。

这不是全面自然语言编辑器方案。首批只关闭两条明确路径所需的单目标修订与无关 slot 继承，再以真实受控跨轮证据决定是否扩展对象选择或更复杂内容检查。

## 4. 只设计两条最小真实 Worker 红例

执行需另获根代理测试窗口；固定 MAIN `.venv_py312` + 本树 `tests/run_isolated_backend.py`，临时平台 SQLite/存储，所有模型和源查询函数明确 forbidden。每条独立 `-x`、独立 log/XML、有界总预算，第一次失败保留原始数据后停止，不自动重跑大组。以下本轮均未创建测试文件或执行。

### R1：旧文件原样跨轮登记不能完成指定内容修订

- 复用 `worker_context/prepare`、`test_dsh_compound_contract.local_outputs/deck_bytes/present_bytes`，A 用真实 3 页非空 PPT 完成初始合同；记录 task、run、artifact、源 SHA、最终存储 SHA、首张可达页标题和页数。
- B 用显式 A.task_id 发送上述修改标题原句，避免把续办语言识别混入此次缺陷。保留3页规则，断言 requires_new_delivery，再原样 present A 同一原生文件。不要重写文件、注入 ready Artifact 或改 metadata。
- 从标准存储/测试客户端下载实际 B 文件，重新算最终 SHA，并读取 PPT 第1页标题。记录原生来源指纹和最终指纹，断言实际仍为 A 原标题且不含目标标题；然后 settle completed，正确行为应为 partial/具体 revision 未满足。当前代码静态预期此最后断言失败（实际返回 completed）。
- 修复后的最小正对照：同 task 的后续 Run 只真正改该页标题后登记，目标 revision 不再增加，接受新文件证明；A 历史文件仍可下载且字节不变。派生控制可覆盖复制改名或仅重压缩，但不在首红自动参数化扩跑。

### R2：只修 PPT 后 Word/Excel 可凭原证据继承

- A 用真实 3 页 PPT、真实 `render_document`、真实 openpyxl/native-present Excel 完成三个格式；Excel 是合成固定两行，不建立任何业务数据源。保留三文件完整最终 SHA，Word 正文、XLSX sheet/单元格以及各 Artifact id。
- B 显式原 task_id，仅要求修改 PPT 第1页标题并保留其他文件，不传整体替换清单；生成真正变化的3页PPT，并从最终字节验证新标题。B 不再制作 Word/Excel。
- 期望同 task completed；accepted PPT 指向新修订文件，Word/Excel 指向 A 原 id/最终 SHA 且本轮重新授权/核验；A 历史不变。当前静态预期 partial，missing Word/Excel 各1。先断言业务状态，以已有协议取得原始红；新 obligation/revision 字段的断言应在方案落地后追加，不用“缺一个尚未实现字段”代替行为红测。
- 关闭修复前再把此已验证资格用于“同次修订 B partial、C 纯继续补剩余”的有界对照，检查 revision 不递增，B 的已完成部分无需重造。该扩展未在本轮执行，也不先假定已通过。

两例均记录工具调用/源查询/模型计数；预期源查询0、模型0。fixture 建表和平台事务不计为业务源查询。不同原始失败须独立保存，不以全组反复跑取代定位。

## 5. 已有测试实际覆盖与缺口

| 已读测试 | 现有覆盖 | 本轮不能外推的能力 |
| --- | --- | --- |
| `test_dsh_native_deliveries.py:121–160` | 同 Run/path/source hash 幂等、改内容 parent/supersede、同内容不同路径可独立交付 | 跨 Run 同 task 重新登记旧字节不能冒充新要求 |
| `test_dsh_compound_contract.py:82–93,129–164` | 真实页数/最终 SHA；Word 可达真实图片、错误大屏/旧版本/无回执/篡改/孤立 media 拒绝 | 任意标题/文本/表格修改的目标 revision fulfillment |
| `test_dsh_compound_boundaries.py:120–176,234–278` | 文件篡改、具名与泛指唯一匹配、绑定大屏不能同名替换、全新 queued 编辑、页数 steer、更改页数、显式版本 | 已交付任务的修改请求 queue edit 清标志；相同页数但内容需改变的跨轮验收 |
| `test_dsh_multiple_presentation_drafts.py:55–84` | 两稿真实字节不同；次轮草稿独立修改 | 次轮 finalize 后旧/新 Artifact 与合同 slot/revision 的最终 settle |
| `test_dsh_query_delivery_resume.py:22–87` | 留存 Q 复用生成真实 XLSX/PPT/图/大屏、不重查；错误任务/来源/身份拒绝 | 原文件修订 vs 新要求的变更内容验收 |
| `test_dsh_dashboard_delivery.py:124–131` | 旧大屏未经实际改变不能冒充当前修改交付 | 无内容变化保存增长 version 后是否仍错误满足指定标题修改 |
| `tests/offline/test_business_task_revisions.py:23–116` | 用 SimpleNamespace/伪存储设计任务清单场景 | 不是当前真实 Worker/最终文件集成证据；未执行，不能援引为本轮绿测 |

B18 刚通过的自然句真实留存 Q→halt→大屏/XLSX 单例验证了身份继承与安全交付，未涉及已交付文件的内容修订，不能拿它代替以上 R1/R2。

## 6. 实际阅读范围与指纹

全文/主要流程阅读：`tasks.py`、`delivery_contract.py`（续办段沿本轮 B18 已审字节，重新读69–205）、`delivery_evidence.py`、`native_deliveries.py`、`query_spreadsheet.py`。定位阅读：tools.py 105–185；chat.py 300–317/412–435/515–545/723–755；dashboard_deliveries.py 131–225；resume.py 95–192；artifact/service.py 102–165；artifact_inspection.py 115–160；builtin_provider.py 1010–1188；query_presentation.py 196–231；query_dashboard.py 215–339；analytics/exports.py 34–99；presentation/draft.py 14–39/278–321/523–556；reports/service.py 99–150/238–255；Artifact/ArtifactLineage 与 ReportRevision 模型定义。测试阅读范围见上一表及 fixture，reports.py 489–517；部分相邻测试仅 rg 定位，未称全文件审计。旧 B16 计划只用于对照，不继承其未执行结论作为本轮动态证据。

| 文件（相对工作树） | SHA256 |
| --- | --- |
| backend/app/business_worker/delivery_contract.py | 01180f565755a0bda5775b234b2509d16b7ac32d0b44f06446d66c3f6321a2fd |
| backend/app/business_worker/tasks.py | 17ac39974fe8fa9ffbf21188677801998353c787c6b561096bc77f22cebf2988 |
| backend/app/business_worker/delivery_evidence.py | 9af88d7d8c6a834bb544f8812b2926ec051c4fcab01cc2a42677f297449b0c04 |
| backend/app/business_worker/native_deliveries.py | 8ad140d8d42046178a93ec54929ce9c817dbaf08a7c347d92074fde948142b10 |
| backend/app/business_worker/dashboard_deliveries.py | 6b7a107e4e7767e211549b2ade83c0f1077611a426213ac32ec06f319a416115 |
| backend/app/business_worker/tools.py | e8dfc75a07cbaa152755bc0f5627486fe1ed1c6792353cfa84083f6e94e3cefb |
| backend/app/business_worker/chat.py | b9e65d49313212763a4f8fa92fee4cac8116976ad347482b0a9aa19a26e36004 |
| backend/app/modules/chat_artifacts/resume.py | 9254aafd34f81e30f1aa629d16b0468732c1aa40223c91bafb3d057e9c47fda8 |
| backend/app/modules/artifact/service.py | 990c63f8357b1175f79c1af9f8f6ab03b0281b3884beb3786a1a0ef99aa8a4b6 |
| backend/app/modules/chat_artifacts/artifact_inspection.py | d4374c7d3f4690368cb266589733e84b7628f89ff150bc7edc7c3d52a026c9e1 |
| backend/app/providers/tool/builtin_provider.py | dfc6f95cbb1a710306231e0a1a43ab5511d111c0443b5801e9d9c2989646cbf5 |
| backend/app/modules/chat_artifacts/query_spreadsheet.py | f72ffa1e50a36e7443ae4a54c3e756e51accff679fbd67e53fc0ba75cf266eff |
| backend/app/modules/chat_artifacts/query_presentation.py | b70529515e2904740c5d1fdf50fa51c9ed6a70d252fe068b42cb84baf0a89a23 |
| backend/app/modules/chat_artifacts/query_dashboard.py | e3cc7561acf343fd3010f98791c657e03baa2838f1f90b9a581e0d5070be6257 |
| backend/app/modules/analytics/exports.py | c562dea09813c1abd2c05ce343d964f435bb54c177f5fca3bda7fa75567d313c |
| backend/app/modules/presentation/draft.py | 029871420a45a6d4a65b4f02a756fc920eeea5a43b4c76a90d00c96d980b1ef6 |
| backend/app/modules/reports/service.py | 68f508e1e17b7fa2fe01f10c07209d286ef889134cdfe3efcaedf52828f42a9c |
| tests/backend/test_dsh_native_deliveries.py | 3b5fbd96d6e9fe6547fd69d47dd2adacf38fc79353613065b6289a8a0981c3a0 |
| tests/backend/test_dsh_compound_contract.py | 42e3138a1ceb8dc9d1995d28fe546fa3d2e6f695800c2f8a94885077a01fe1fc |
| tests/backend/test_dsh_compound_boundaries.py | 66c125a5f9f045fe4c9fa3dec0950124c45d85b1f9b09df27fe42c3e312fec9c |
| tests/backend/test_dsh_multiple_presentation_drafts.py | 28217d5df1bd00e9001f821e6fd19f210f455b6d9bd9a4db9a9a5e4335380eb6 |
| tests/backend/test_dsh_query_delivery_resume.py | 6569bf9bd705679a1344bb0a30170146e4fd8cf26eb4c8ffe274498ecce7376b |
| tests/backend/test_dsh_dashboard_delivery.py | 87f11b638f125aa0e6d9ded4403d9e41aad9d5b250a80e834e45557393d17993 |
| tests/offline/test_business_task_revisions.py | f0098fb8c20a911c92cb5bb3e7c9d9a9de817e1bfdfecaee8ba7ba8a7ee0b9d6 |
| tests/backend/test_reports.py | 647beab46395483f76c48d6a732bb89571f162e1bf980db72d8238193c51c96a |

哈希覆盖文件全部字节，不表示定位读取的文件已逐行全量审计。本轮只有静态证据与待执行设计，没有新增“修复已验证”“浏览器验收”或发布结论。

## 7. 追加：两条待执行测试模块已准备

按根代理追加授权，仅新建 ignored `.implementation/artifact-revision-tests-batch19.py`，未移入正式测试目录，也未触碰 B19 工作树。测试脚本 SHA256 为 `131d7981dd8c7a44c4cffcda3a3607b861522c45c43badf2373305aa67ecb6e6`。本阶段只用标准库 `ast.parse` 读取脚本、确认语法及两个 test 函数名，没有 import/collect/执行该模块，没有初始化 app/SQLite 或调用模型。`git check-ignore` 确认脚本与本报告均 ignored。

拟由根代理在独立窗口复制为 `tests/backend/test_dsh_artifact_revisions.py`，具体 node id：

```text
tests/backend/test_dsh_artifact_revisions.py::test_unchanged_prior_ppt_represented_in_new_run_cannot_complete_title_revision
tests/backend/test_dsh_artifact_revisions.py::test_only_ppt_title_revision_keeps_original_word_and_excel_fulfilled
```

依赖保持既有仓库/环境：`test_business_worker.prepare/worker_context`、`test_dsh_compound_contract.local_outputs/deck_bytes/present_bytes`，以及既有 pytest、python-pptx、python-docx、openpyxl、隔离 backend conftest。不安装依赖、不增加 SQL 数据源、不改 fixture 实现。运行必须仍使用 MAIN Python 和 `tests/run_isolated_backend.py`，两条分开 `-x`、独立 log/XML，不合并进正在运行的 B18 门禁。

实现细节与预期旧行为：

| 单例 | 真实操作及独立证据 | 预期现有产品行为与业务红断言 |
| --- | --- | --- |
| R1 原样重新登记 | A 的真实3页PPT完成后，B 显式 task_id 提出第1页标题修订；不重写 A 原生文件，只从同稳定 cwd 原样 present。分别读取 A/B 标准存储最终字节，重算 SHA，读取3页实际标题；记录源 SHA 而不假定 Office 最终 ZIP SHA 必须相同；核对 A 历史快照和文件不变 | 最后的业务断言要求 partial；当前静态预期返回 completed。不会用尚未实现的 revision 字段缺失制造失败，也不要求实现一定创建新 id，以便正确复用旧引用的修复仍可通过 |
| R2 只修 PPT | A 实际 present 三页PPT、通过真实 Worker `render_document` 和 `render_spreadsheet` 生成 DOCX/XLSX，读最终 Word 正文、Excel“固定明细”表头与12/18两行值。B 显式 task_id，仅交付标题实际已改变的新3页PPT；再次核对原 Word/Excel id、最终 SHA/内容、来源 Run、无新同格式成果及 A 历史不变 | 最后的业务断言要求 completed 且 missing=[]；当前静态预期 partial，Word/Excel 各 missing1。未传替换交付清单，不能通过删原义务变绿 |

R2 的 Excel 使用现有 `render_spreadsheet` 真工具而非另行 openpyxl 造输入文件；真实最终 XLSX 仍由 openpyxl 读取。其 `sheet_name/columns/rows` 接口已只读对照 `builtin_provider.py:506–511`、`artifact/renderers.py:698–761`，没有运行探测。两个单例都保留自然修改原话，但使用显式 task_id，避免 B18 续办语言识别混入修订红例。

autouse guard 在 `queries.execute_read`、`execution.readonly_connection`、`metadata.inspect_sql_source`、`model.chat/chat_for_run` 入口一旦被调用即失败并计数；模型配置仍沿用 worker_context 的本机伪配置。guard teardown 将四项计数写入 `source_model_counts` JUnit 属性，目标全0；平台临时 SQLite 事务不计为业务源查询。每例在最终业务红断言之前写入 `revision_evidence` 属性，包含 task/run/artifact、最终 SHA、实际标题/Word/Excel内容及 settle/missing/constraint_failures，便于首失败证据直接核对。

本模块仅准备了两个单例，没有加入修复后额外正例、复制改名、重压缩、三轮累计或 queue/steer 故障注入；这些仍是上文明确列出的待扩展边界。当前**没有红/绿运行结果**，也未更改此前 B18 的53项及留存交付单例证据。
