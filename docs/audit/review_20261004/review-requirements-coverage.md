# 九套完整业务浏览器验收的需求覆盖差距

审查日期：2026-10-04。范围是当前主树的需求文档、统一应用目录、前端路由/关键页面，以及九套 `full_browser_*_acceptance.js` 和隔离 runner。本文只审查覆盖，不修改产品或测试，不构建、不启动浏览器、不访问 8011、数据库或真实模型，也不干预正在执行的 100 轮任务。

## 结论与证据边界

九套单轮的 114 组检查确实包含真实页面操作、当前 DSH 宿主与 Python Worker、临时平台数据库、实际文件下载和回环服务回执。它不是 114 项彼此独立的需求，更不是全产品功能完成证明。`business_browser_acceptance.py:18` 固定九套，`:205` 重复同一套循环，`:221` 为每套建立隔离宿主；因此 100 轮增加这些固定场景的重复稳定性证据，不会自动补充成员权限、断流恢复、复杂任务、更多数据口径或全部节点执行。

本报告的条目均为**九套完整服务验收尚未证明的路径**，不直接断言相应产品已经损坏，也不把其它离线/后端测试抹掉。已有覆盖记录明确列出离线 DSH 对话的排队、取消、刷新、断线恢复和大屏权限夹具（`docs/implementation/browser-acceptance-coverage-20261004.md:19`），它们与完整服务故障注入应分别记账。

需求版本也应区分：`需求文档/v1/v1_requirements.md:221` 把复杂可视工作流、调度及部分治理列为未来扩展；当前应用目录已经注册工作流、设备监测、运维和应用中心，相关差距按“当前产品已提供的能力”提出，不倒推为 V1 原文承诺。PDF/PPT 等二进制需求附件及字段字典未在本轮完整解析，不能称“所有需求附件逐页全读”。

## 优先补齐的完整链路

### COV-01 · 高：成员权限、资源授权和工作区隔离没有真实消费者验收

- 需求：V1 Admin/Member 及 Plugin、Knowledge、DataSource、Connector 使用权限（需求 `:1843`、`:1870`）；最终验收要求同工作区其它用户重复使用（`:3118`）。
- 产品：`apps/analytics/src/router.ts:54` 有复合权限、`:62` 有创建工作流拦截、`:63` 有报表只读跳转；`apps/web/assets/platform/permissions.js:44`、`:120` 有资源授权/撤权和页面可见性。
- 已有：system `:213` 管理员登录，`:265` 创建角色，`:296` 创建用户并改临时口令，`:339` 明确只创建/归档第二工作区而不切换；content `:262` 管理员登录、`:487` 由管理员增删知识授权。其它套件同样从 workspace-admin 开始。
- 差距：没有低权限成员实际登录、允许/拒绝同一资源、撤权后已开页面/下载链接重新访问、第二工作区使用第一工作区 ID、只读报表用户进入设计器等验证。管理端显示“已撤销”不能证明服务端实际禁止读取或执行。
- 最小补充：两个浏览器上下文、两个临时工作区和确定资源 ID；证明成员授权前 403/404且模型/设备调用数为 0，授权后可用，撤权后旧链接失效；验证只读可见但不能改/执行。仅在临时平台创建这些身份。

### COV-02 · 高：真实 DSH 长任务的断流、刷新、取消与排队仍缺跨层证明

- 需求：长任务 queued/running/waiting/completed/failed/cancelled、Run Step、事件流（需求 `:1623`、`:1672`、`:1697`），Run 不绑定 HTTP 生命周期（`:2640`）。
- 产品：`apps/web/assets/platform/chat-session.js:193` 处理终态，`:258` 起控制器订阅/恢复，`:291` 区分不可恢复 HTTP 错误；`chat-session-view.js:210` 队列编辑/移除/补充、`:284` 取消运行。
- 已有：ai_documents `:444` 的等待器接受多种终态，但 `:455` 要求无失败；`:737` 普通查询、`:782` PPT 都是成功 completed。`:898` 起 selfTest 中的失败回执是模型桩选择器的纯断言，不是浏览器真实断流/工具失败恢复。
- 差距：未在真实宿主运行期间断开 SSE/刷新页面/切换会话后回来；未真实点击取消、队列修改/移除/steer；未验证宿主/Worker 异常后终态、历史和交付一致；长历史增量重放、重复事件也未在这九套测试中触发。
- 最小补充：可控慢回环工具，按进度屏障断流并重连；同 run_id 不重复调用；刷新后不丢消息与产物；取消后已完成工具证据保留且不继续新工具；第二条排队消息只执行一次。不要靠放大等待时间替代这些状态断言。

### COV-03 · 高：用户要求的多交付复杂任务未进入同一轮验收

- 需求：财务完整链要求文件补充、Schema、查询、Python、图表、PPT，再保存 Plugin（需求 `:2850`）；Artifact 可继续作为下一 Run 输入（`:1453`）。会议转写 `:7`、`:19` 要求设备问答直接返回报告/看板和可交付文件。
- 产品：统一目录 `configurations/business_apps.json:259`、`:358`、`:448` 分别注册文档、报表和监测能力；`chat-session-view.js:184` 的“继续使用”连接下轮。
- 已有：ai_documents 普通查询与 PPT 是两个独立会话，`:765`、`:807` 完成后立即删除；`:809` 后 Word/PDF/Excel 也是独立文档任务。下载了五份文件，不代表单一业务请求完成全部交付。
- 差距：没有“设备未巡检统计→饼图→Word+Excel→指定页数 PPT→大屏并嵌入 Word”同 run/跨轮任务清单与事实一致性验证；没有其中一项失败后保留其它成果、只补缺失项；没有继续使用现有 Artifact 的真实点击与再生成。
- 最小补充：固定合成设备台账、巡检计划/记录及明确时间窗口，预先计算应巡/已巡/未巡设备去重数；核对图、表、文档、大屏同一统计口径及来源；故意让一个渲染步骤失败一次，再补齐且不重复产出/错误宣布全部完成。

### COV-04 · 高：智能 SQL 正确性及实际数据库方言未被“手写 SQL 成功”覆盖

- 需求：MySQL/PostgreSQL、默认只读（需求 `:802`、`:811`）；模型参数先解析、Schema、业务规则和权限校验（`:949`）。
- 产品：`IntelligentQuery.vue:49`、`:50` 分自然语言/SQL，`:106` 有取消和请求代际；`QueryHistory.vue:53` 起按成功/失败复用不同上下文；路由 `router.ts:29` 注册智能问数、SQL、历史和数据质量页。
- 已有：analytics `:256` 上传合成 CSV，`:337` 明确选择“手工 SQL 查询”，`:366` 成功返回五行；ai_documents `:761` 查询的是已选回环 HTTP 源，不是模型生成数据库 SQL。
- 差距：自然语言→SQL→实际结果的完整链、未知字段纠错、日期范围/NULL/重复设备/精确金额、无数据和查询截断解释、只读拒绝、源切换时晚到结果、取消后回读、历史分页及失败记录复用均无本组浏览器证明。MySQL/PostgreSQL 方言不能从 CSV/SQLite 结果推导。
- 最小补充：先用隔离 SQLite 合成关系表验证问数和可计算口径；另建独立本机数据库方言小样本验收计划，先核验主机/端口/库名/服务归属、只读权限和预算。不得拿当前已保存业务源自动测试或默认接局域网库；本轮未连接任何数据库。

### COV-05 · 高：44 类节点表单覆盖不等于复杂工作流执行覆盖

- 产品：`backend/app/modules/workflows/router.py:223`/`:232` 支持 resume/cancel；`WorkflowRunControls.vue:80` 有等待/取消控件，`:97` 提交回答、`:102` 延迟继续。
- 已有：workflow `:47` 的 NODE_FORMS 与目录精确核对，`:639` 验证 44 类表单，`:640` 验证非法草稿阻止发布，`:651` 检查移动表单；真正执行只有 `:476` 和 `:695` 的两条 HTTP/计算/规则小流程。alert/quality 增补了各自业务节点链，但仍没有覆盖所有节点语义。
- 差距：question等待回答、delay到期恢复、取消与cancel_requested、循环/迭代/while/子流程、分支异常策略、重试后只执行一次、文件/Python/知识/数据写回等高风险节点组合没有完整执行证明。也没有双页面并发修改、旧版本409和异步晚到覆盖另一个流程的真实浏览器回归。
- 最小补充：按执行语义而非节点数量划分小图，增加 waiting→刷新→resume→completed、取消、异常分支、两轮循环/子流程及重复提交幂等；每个节点断言输入/输出/状态和下游调用数。写回只允许合成隔离目标并单独授权。

### COV-06 · 高：预警“自动监测”和质检失败恢复缺少服务运行证明

- 产品：`backend/app/modules/system_tools/schedules.py:504` 的到期调度重新核验身份，`:532`/`:535` 暂停失效身份/工作区；`:542` 固定窗口幂等键。`apps/analytics/src/utils/qualityRunRecovery.ts:29`、`:75` 存在恢复令牌/幂等键验证。
- 已有：alert `:425` 强调调度 paused，`:469` 手动执行两次已保存请求，`:486` 处置、`:493` 历史下载不再采集；quality `:552` 两个检测服务各调用一次并合并报告，`:564` 报告刷新不重复检测。system `:374` 是暂停健康任务手动运行。
- 差距：没有调度真正到期触发/跨周期去重/服务重启后继续/授权撤销后禁止执行；没有设备离线、响应字段缺失、一个检测成功一个失败、坏图片或置信度不足后的补救。质检“不合格”是合法业务结果（quality `:436`），与检测服务执行失败不同，不能互相代替。
- 最小补充：可控时钟/短期暂停后显式启用的隔离调度，核对每个窗口执行次数与不可变发布版本；检测桩按步骤返回可恢复失败，刷新/重新进入后沿同一个运行恢复、禁止重复设备副作用，失败原因不能冒充正常检测结论。

### COV-07 · 高：连接器模型派生、脏参数、MCP 与沙箱不在当前成功链中

- 需求：REST/MCP/DB、Parameter Compiler 和 Validator（需求 `:695`、`:784`、`:949`）；错误分类不可默认修改外部程序（`:1189`）。
- 产品：`apps/web/assets/platform/capabilities.js:242` 提供 HTTP/数据库/MCP，`:249` 用 secretId；`backend/app/modules/connector/service.py:83` 接受 http/database/mcp/builtin/sandbox；`connector/router.py:127`、`:159` 有模型派生后的结构处理。
- 已有：capability `:305` 手工配置 HTTP 实调，`:321` 用户参数 Schema 验证，`:538` 新建连接器版本/复制/共享/归档。这个覆盖是真实的，但测试没有走模型派生、不使用 MCP 或沙箱，也没有真实密钥绑定/轮换后的执行。
- 差距：无创建权限时派生应在模型调用前拒绝、模型返回非对象/脏数组/非法 timeout/tools 应明确422、HTTP方法不应被悄悄变更、MCP发现/调用/断连、Secret不进入模型/工具日志、沙箱限权/异常/附件输出均无本组证明。
- 最小补充：先补隔离后端契约，再用本机 HTTP/MCP 桩验证真实页面：未授权调用数为0，合法输出保持方法/路径语义，脏输出不创建半成品，取消和权限异常不被吞。此条是覆盖缺口；本轮未通过执行确认具体缺陷。

### COV-08 · 中高：Chat 成功方法沉淀与其它成员复用没有闭环

- 需求：Chat→Skill/Plugin（需求 `:1151`）；Skill/Connector/Plugin版本化、发布前Test Run（`:1907`、`:1956`）；招投标完整场景（`:2827`）。
- 产品：统一目录注册 `platform.capabilities`；应用页面 `apps/web/assets/platform/applications.js` 实际创建、发布并绑定入口；连接器 UI `capabilities.js:438` 支持新版本。
- 已有：capability `:409` Skill CRUD/发布/复制/共享，`:448` Plugin绑定，`:478` 应用导航到正确plugin_id的 `/ai`，`:493` 启动记录和复制；并非没有应用覆盖。
- 差距：页面到 `/ai` 后没有发送消息验证绑定 Skill/Plugin 被实际采用；没有从一次成功 Chat 保存方法、第二人用新文件运行；Skill/Plugin新版本发布、旧版本任务重放、变更草稿不影响已发布版本和版本回退没有对应执行证明。
- 最小补充：一条真实查询/文档任务→保存能力→成员打开应用→更换输入→执行并核对工具轨迹与版本；编辑草稿后再运行旧正式版本，验证引用与结果稳定。

## 其它重要覆盖差距

| 编号/优先级 | 需求与产品定位 | 现有断言边界 | 建议补充与应保留证据 |
|---|---|---|---|
| COV-09 / 中高 · 知识文件与答案引用 | 需求 `:362` 多文件格式、`:439` 检索、`:484` citation；目录 `business_apps.json:303`；`knowledge-graph.js:271` 还提供非结构化知识抽取 | content `:358` 文本文件版本恢复，`:422`解析/重建，`:455`关键词召回（SQLite回退），没有 Chat选择KB+当前文件生成有引用答案；graph `:371`仅结构化映射 | PDF/DOCX/PPTX/XLSX和损坏/不支持文件状态，文档重索引后旧chunk不残留，权限撤销后不可检索；Chat答案每个关键结论对应允许访问的页/chunk。向量/重排质量必须独立评价，不能由固定embedding桩证明。 |
| COV-10 / 中高 · PPT模板、字体与中断补页 | PPT细分说明 `:31`边界字体、`:76`3–50页、`:80`客户模板、`:88`原母版；`documents.js:197`生成表单、`:333`分阶段计划 | ai_documents `:512`只检查两份5页PPTX的页数/文本/非空；`:869`20模板目录和移动新建框；独立Word/PDF/Excel下载成功 | 最小/大页数、客户模板上传/编辑/移除、原母版字体、宋体中文图表、长文本/负值/空数据、文档导出页面渲染；写到第N页中断后只补缺页、导出不误报成功。PPT细分说明 `:159`引用其它20模板smoke/ui检查，其存在不能视为本组已执行结果。 |
| COV-11 / 中高 · AI大屏实际生成和历史恢复 | `IntelligentQuery.vue:192` 生成入口；目录 `business_apps.json:349` 报表；`router.ts:40`设计器/`:42`查看 | analytics `:505`绑定合成数据/拖动持久化，`:520`预览发布；`:570`只验证auto/manual切换和缺模板阻止任务，没有真正发出AI生成 | 有效模板下模型→SQL→组件数据→保存发布→Word嵌图，生成途中取消/刷新/错误后保留旧画布，旧版本预览/恢复、查看者权限和跨会话引用同一大屏；组件联动与钻取需要独立输入/结果断言。 |
| COV-12 / 中 · 独立页面入口与导航回流 | `router.ts:31`SQL工作区、`:32`查询历史、`:33`数据质量、`:44`提示词中心；`QueryHistory.vue:57`复用、`PromptCenter.vue:97`按用途跳转 | 九套路径扫描没有进入 `/analytics/sql`、`/analytics/history`、`/analytics/quality`、`/prompts`；仅analytics主页面手工SQL | 每页至少完成一个主动作及失败恢复：历史成功/失败复用保留源与参数，提示词query/screen/widget转到正确页面，数据质量异常显示；刷新与分享深链接保留上下文，分页后删除不落空。 |
| COV-13 / 中高 · 运维恢复及真正备份恢复 | 目录 `business_apps.json:484` `/operations`；`operations.js:136`先预览后确认；`operations/router.py:64`敏感运行详情、`:76`中断恢复 | system `:386`仅创建SQLite备份并查看恢复计划；`/system/operations`调度/通知页不等于独立`/operations`运行追踪页 | 隔离库备份→修改临时数据→恢复→核对原数据及审计；中断预览不会改状态，正式恢复只处理确认失去执行者的run，不碰仍在waiting/running的活任务；运行时间线/日志遮蔽与无权限字段可见性。不可在在用库验证恢复。 |
| COV-14 / 中 · 知识图谱/Bot 对外调用 | `knowledge-graph.js:271`知识文档抽取、`:627`retrieval/model两种调试、`:631`APIKey | graph `:387`候选写入图谱，`:457`草稿/正式检索调试，`:478`密钥一次展示/撤销UI | 非结构化抽取与证据完整性，冲突本体版本/重复物化幂等，model模式归纳不得无来源编造；本机公共API真实调用、过期/撤销后拒绝、他工作区资源不可见。 |
| COV-15 / 中高 · 模型配置和身份恢复 | 目录 `business_apps.json:32`账户页、`:53`模型管理；需求Secret禁止入Prompt（`:1881`） | system `:226`验证错口令和管理员登录、`:439`OAuth式client建立/轮换/禁用；ai_documents `:373`单一兼容模型保存/探测 | 账户个人资料/强制改密/会话失效后草稿保留，多模型切换是否影响当前轮与下轮，流式坏JSON/工具名不存在/429/中途断开/认证失效；client禁用后实际接口访问拒绝及脱敏。固定桩只证明协议适配，不证明真实模型输出质量。 |

## 移动端覆盖不能按“套件已设390px”整体认定

| 套件 | 已有390px检查位置与实际操作 | 仍缺的关键移动路径 |
|---|---|---|
| system | 仅 `:201` 桌面1440px | 成员登录/改密/会话过期、权限与系统表单、关键确认按钮可见性。 |
| content | `:583`–`:594`知识管理页面 | 文件上传/多文件选择/预览、知识召回引用与授权对话框全过程。 |
| capability | `:495`–`:515`应用详情编辑弹窗单列、入口收起 | 连接器参数测试/发布、Skill/Plugin编辑、应用参数填写后真正聊天执行。 |
| graph | `:483`–`:502`本体编辑弹窗 | 抽取候选证据/批量复核、图谱探索、Bot回答与来源打开。 |
| analytics | `:656`–`:670`模板中心 | 问数输入与查询结果/表格横滑、导出、画布编辑/预览/大屏分享。 |
| alert | `:393`向导、`:496`历史报告深链接/键盘表格滚动 | 手机上创建测点/策略、开始运行、故障恢复、实际处理问题与通知回流。 |
| quality | `:486`向导、`:556`历史报告刷新 | 手机上传原图→运行→失败恢复→人工复核全过程。 |
| workflow | `:642`–`:651`全部节点属性表单 | 连线/多节点选择、运行输入、等待回答、取消/继续、历史恢复。 |
| ai_documents | `:852`–`:869`文档模板/新建弹窗 | AI对话正文输入+软键盘占位、上传、选择源/模型、运行取消、交付下载/继续使用；页面宽度合格不等于触摸或屏幕阅读器人工体验合格。 |

当前主要尺寸是桌面与390px两档，不能据此推导平板、横屏、200%缩放、系统大字体、真实移动键盘、拖拽触控或不同浏览器均可用。axe与几何断言是有价值的自动证据，但不是全部无障碍验收。

## 建议执行顺序与成功判定

1. 先补 COV-01、02、03、04：它们直接影响“能否访问正确数据、任务是否继续、是否交齐和答案是否准确”。先做小型回环故障注入，不启动另一个100轮掩盖宽度不足。
2. 再补 COV-05、06、07：按等待/取消/幂等/失败分支分别形成小样本，产品修复先有失败回归，浏览器必须等待真实持久化回执。
3. 补 COV-08、09、10、11 的跨模块任务闭环及交付内容校验，再扩移动端和独立页面。成功须有任务终态、每项Artifact实际字节/可打开性、业务数字预期、授权与副作用次数四类证据。
4. 同一清单全部单轮通过后再做有预算的重复稳定性验收。隔离合成数据、固定模型桩、真实本机业务只读数据、真实模型四种证据分别登记，不能相互代替。正常业务“不合格/无数据/无权限”要准确表达，不应通过伪造好结果达标。

## 实际读取文件与SHA256

以下清单记录本轮确实读取内容的文件；“定位阅读”指读取接口/函数/断言附近内容及关键词索引，并非逐行全量人工审查。长需求采用章节索引和涉及条目的正文阅读；早期超长输出曾截断，本报告只使用后续可见的定位证据。SHA256覆盖整个文件字节，供后续发现并行修改或重新定位；并不意味着整个文件均逐行审查。

<!-- READ_SHA256_INVENTORY -->

取样时间：2026-10-04 08:55:06 +08:00。

| 文件 | 阅读范围 | SHA256 |
|---|---|---|
| `AGENTS.md` | 全文/分段正文 | `C6948250A8E090F4BDA243BAD123E9F063F96381503B916BFE12FDDCC44E032B` |
| `需求文档/v1/v1_requirements.md` | 相关章节/函数定位阅读 | `8A0F05DD6E0596D12EE6D293F377CE22C360BA4C63BFFAA0B9059C0078DCA356` |
| `需求文档/细分需求/ppt生成/抽取与落地说明.md` | 全文/分段正文 | `65BE2DD63B982D8487D5CF63B07C2637336A9FAC948EE20EFFACFCC90040ACAE` |
| `需求文档/0909/会议转写.txt` | 相关章节/函数定位阅读 | `121AAD631532F7BB0CCE65BFDC59851402CA38D099B98372E9F8CA646714D9E7` |
| `configurations/business_apps.json` | 全文/分段正文 | `E5C39D3E8DB6588FA52B192359F146E99899706508E447B930CB0115E514AF22` |
| `apps/analytics/src/router.ts` | 全文/分段正文 | `08693B036A0DD2C89569845415FCF28806A1CEF79B74E82A337DA0FE4593D734` |
| `tests/dsh/business_browser_acceptance.py` | 全文/分段正文 | `73E218CA5095B2D75AC274EC92555567AD543D17996B44DAAAD98BCC6D926ECC` |
| `docs/implementation/browser-acceptance-coverage-20261004.md` | 相关章节/函数定位阅读 | `CE4DDD70036CDBF0A15E654CFA55F0B11F233EEFA948151455651D923F2C496A` |
| `tests/ui/full_browser_system_acceptance.js` | 场景流程、断言及定位阅读 | `269A32F469AA03974FB8CB21E19C09833B6C4743E542AE6B76E23097824A8156` |
| `tests/ui/full_browser_content_acceptance.js` | 场景流程、断言及定位阅读 | `3C8AE638C2C616EC155616B5FE14233F0A3ECF8C897E4A0352A488402A30E40D` |
| `tests/ui/full_browser_capability_acceptance.js` | 场景流程、断言及定位阅读 | `5F9AC5AF9FE66F5BE5AF74EA3E3941DE24C6AA7781A76A47578E18A6ACF3D563` |
| `tests/ui/full_browser_graph_acceptance.js` | 场景流程、断言及定位阅读 | `B38958A88B673AECE5F4C9F4692EAF23F6E9C8E4479CC6CF846FEEEB77C9D5EB` |
| `tests/ui/full_browser_analytics_acceptance.js` | 场景流程、断言及定位阅读 | `86A2FDCC81136001E47B1B6B762F348F560E058B0FAB4331D924BF2E3DF0159C` |
| `tests/ui/full_browser_alert_acceptance.js` | 场景流程、断言及定位阅读 | `E0B84E22C8CD1CC115DCF90C506D13EBB6B663B4152D17453091C76DCAEA2CA6` |
| `tests/ui/full_browser_quality_acceptance.js` | 场景流程、断言及定位阅读 | `89797C3C9B984FAC6D2CFA895F33BAEA5576F415780817E1FD3E096A084D8AAD` |
| `tests/ui/full_browser_workflow_acceptance.js` | 场景流程、断言及定位阅读 | `6734F824CEE7FDA30B48EDDAD61CD4622FEE04935AA84B9F88E9639C97DF815C` |
| `tests/ui/full_browser_ai_documents_acceptance.js` | 场景流程、断言及定位阅读 | `85460935E6F6462FB7C36E266E780A53463BD6C96004420DF66B29D50C8F495E` |
| `apps/web/assets/platform/chat-session.js` | 相关章节/函数定位阅读 | `BEED68AE4B427F09EFE754A055A28E998FD3A48652F4910C7EEE65F95F152372` |
| `apps/web/assets/platform/chat-session-view.js` | 相关章节/函数定位阅读 | `458800941EDF171570CB650C694E61A9AABF28993C7D70B87D640E4B6C5531E5` |
| `apps/web/assets/platform/permissions.js` | 相关章节/函数定位阅读 | `86A3B91AC36DF5FE59AF301EFC622B68C0F08356F6430333129B381F6E5463D8` |
| `apps/web/assets/platform/operations.js` | 相关章节/函数定位阅读 | `C339B92893876E6C5656F1F581A8D6DCBFE89D837ACC41DD6C657C86008509B4` |
| `apps/web/assets/platform/applications.js` | 相关章节/函数定位阅读 | `5AC48A38C6620D64D90EF1CD07BF484537FEADC79E940A3AE12CADD3A95C6279` |
| `apps/web/assets/platform/capabilities.js` | 相关章节/函数定位阅读 | `174CD55FAB95B663104A81F7D1D8D91BAD8B2BCF94BA4F1762E09F5C22B1B838` |
| `apps/web/assets/platform/documents.js` | 相关章节/函数定位阅读 | `12EC54FEA794957A2D24FB1CE46DDF4A0A31DB1B5E918D5B5C04C060CA70A9A5` |
| `apps/web/assets/platform/knowledge-graph.js` | 相关章节/函数定位阅读 | `E185FF09B968A2995E09B7C9B4ACA9D34C5FDEA3F6F6DD26143D19705F0EF14B` |
| `apps/analytics/src/components/IntelligentQuery.vue` | 相关章节/函数定位阅读 | `6843BD367E893CDEB21128991AE0CF249D372C21C8DFB3B7D4D4C0F35DF0E152` |
| `apps/analytics/src/views/QueryHistory.vue` | 相关章节/函数定位阅读 | `1094B4392B2C806D1705DD44B6329DE23195694B4834263F06EB26565779CBC0` |
| `apps/analytics/src/views/PromptCenter.vue` | 相关章节/函数定位阅读 | `736B12752D915BBA4FA319B5C969CA8E92B1828C1F0FCB13EC4246204442E34A` |
| `apps/analytics/src/components/workflows/WorkflowRunControls.vue` | 相关章节/函数定位阅读 | `6BD184ABC6DDB377439840184B11C0469228CC44B9DE98E999FA802DE2102E49` |
| `apps/analytics/src/components/workflows/WorkflowPublishedRunner.vue` | 相关章节/函数定位阅读 | `39441A18F9332B3B4643319358FAF1C9F44BAEAC3793AF4D817F7458494208AF` |
| `apps/analytics/src/utils/qualityRunRecovery.ts` | 相关章节/函数定位阅读 | `349551939F0B6D45CC2C8B5045A7484969D72A3BF11FDDC55019225F911A2419` |
| `backend/app/modules/workflows/router.py` | 相关章节/函数定位阅读 | `26F7B99E7B906AA016CA537F109B1562A92F652C713F94B66E1F8625EE1AE074` |
| `backend/app/modules/connector/router.py` | 相关章节/函数定位阅读 | `AFD0C9F1F3502E8DB7D6659EC910C10BD3394E3E9F62B14E7448E6BE00DD635D` |
| `backend/app/modules/connector/service.py` | 相关章节/函数定位阅读 | `5002CE2CF65183FAC9FB2D10D76074B4EA5316C506365B3AC26324E80D985A88` |
| `backend/app/modules/system_tools/schedules.py` | 相关章节/函数定位阅读 | `2BBE3E0C31165D30198866A17BF4C20EE055E765AFD88B189ABA8B3CAD5DD405` |
| `backend/app/modules/operations/router.py` | 相关章节/函数定位阅读 | `49B5F341D9C1B6483B5C2CF16D643E321A3E5C24FF2956A261A33472463D82CA` |
