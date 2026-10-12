# 下批 C03/C04：复合交付与长句续办的最小实施切点

2026-10-04，只读设计。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-task-lifecycle`。依据 `docs/audit/review_20261004/plan-presentation-completion.md` 与当前源码。C01/C02 及其他批次十四产品/测试继续冻结；本次只新增这份 ignored 报告，没有运行测试、模型、数据库或 HTTP 请求。下文红断言均为**建议下一批执行的复现**，不是已经取得的失败计数。

## 1. 静态确认的现状与最小范围

原生主路径为 `chat.prepare → tasks.choose_task → 原生/业务工具 → chat.settle → tasks.verify`。`tasks.requirements:62` 只存 type/count，`tasks.verify:186` 只数 ready 附件类型，大屏另经 `dashboard_deliveries` 验证保存对象与查询快照。原生 `native_deliveries.present:115` 校验实际路径、范围和源指纹，并将同 Run/路径的旧版本 superseded，但未验证“15 页”或“Word 内嵌指定大屏”。因此“产物存在”与“复合要求实现”之间仍缺统一关系约束。

需要修正一项容易过度概括的说法：标准业务 PPT 渲染并非完全不读实际页数。`builtin_provider.py:1145–1176` 渲染后用 python-pptx 读物理页数，并保存 `metadata_json.requested_page_count / actual_page_count`；`query_presentation.py:211–227` 保存并核对 `delivery_evidence.page_count`。但这些证据没有进入顶层 task 验收，而且 requested_page_count 也可能来自模型工具参数，不能替代用户要求。原生 present 不经过这两条 PPT 分支。

`presentation/draft.py:523` 已拒绝 missing 正文页，并把内容不足页退回待重写；`draft_status:429` 区分正文索引、物理页码、封面/结尾。应保留这些门槛，不用创建空白页或复制页替代内容。`request_constraints.py:59` 已解析具名 PPT 的明确总页数及范围，但设计上不猜跨对象；它不能直接充当所有任务的页数解析器。例如“制作一份15页PPT”的页数在 PPT 词之前，没有该函数所要求的标题作用域。

**可复用公共能力已存在：** `chat_artifacts/artifact_inspection.py` 能有界解析 OOXML 关系链，返回 PPT `pages/nonempty_pages`、Word 实际引用图片 SHA-256 `images`。旧 `openwebui/artifact_inspection.py` 只是此公共模块的兼容别名；旧 `DeliveryTracker.status:131` 虽已有页数与嵌图哈希判定，但它不是当前 DSH `tasks.verify` 主路径，也只有同批“某一大屏图片”的集合判断。建议复用公共解析器，**不要移植旧模型循环或直接把旧内存 Tracker 当跨轮权威存储**。

## 2. 应读取的可信持久字段

### 2.1 用户要求与产物归属

| 来源 | 当前已有字段 | 用法与信任边界 |
| --- | --- | --- |
| `Run` | `workspace_id / started_by_user_id / conversation_id / id` | 限定运行、用户、工作区、会话，不能按文件名全库查找 |
| `Run.context_snapshot` | `task_id / business_task / submitted_content / user_request / steering_messages[]` | task_id 与同任务历史由服务端建单；submitted_content 是本轮原话；user_request 会被 steer 替换，不能只读它丢掉最初15页和嵌入要求。steer 需读取已持久 accepted 记录 |
| `business_task` | `required_deliveries / requires_new_delivery / resume_from_run_id / status` | 保留当前 type/count 兼容结构、修改任务只认可本轮新产物，以及旧 Run 草稿迁移边界 |
| `Artifact` | `id / workspace_id / owner_user_id / run_id / type / status / storage_key / sha256 / size / parent_artifact_id / metadata_json` | 只纳入同任务可信来源、ready、真实存储存在的工件；父工件当前主要表达 native 同路径修订，**不表示 Word 嵌入关系** |
| native 元数据 | `origin=dsh_native_present / native_path / source_sha256 / task_id / source_run_id` | source_sha256 是字体规范化前原文件指纹；最终文件核验必须绑定 `Artifact.sha256`，不能混用两个 SHA |

**下一批建议新增、目前并不存在：** 在 `business_task` 内保存服务端生成的 `delivery_contract_version` 与 `delivery_constraints`，不改旧 type/count 的外部形状。每个明确对象带稳定 requirement_id、类型、用户原话证据、标题/对象锚点；PPT 带总页数 minimum/maximum；Word 带 embeds 目标 requirement_id（对象解析后再绑定 report_id/version）。模型参数或自由 metadata 不能直接创建/解除这些义务。

单份泛指 PPT 可以唯一绑定；多份具名 PPT 必须各有独立要求与产物对应，不能把一份15页文件同时抵两项。一个任意文件名匹配不是可靠绑定。先支持现有明确具名/唯一对象口径；无法唯一匹配应保留未确认约束，不能默认选择列表第一项。旧 type/count 请求无条件继续兼容，旧任务按已保存原话谨慎补证，不能把缺证据当通过。

### 2.2 “15 页 PPT”的实际证明

1. 先从服务端任务约束确定“15 总页”，与正文第15页、15页Word、正文15页、6–8页范围分别处理；现有工具页数口径包含启用的封面和结尾。
2. 从同任务 `Artifact.storage_key` 读取最终保存字节，核对最终 SHA；调用公共 `inspect_file` 或等价公共包装，使用真实 `ppt/presentation.xml → sldId → relationships → slide` 关系链结果。不能数 ZIP 中 `ppt/slides/` 文件个数：孤立页、重复目标、损坏关系均可能虚增。
3. 当前两条标准工具的 `actual_page_count` / `delivery_evidence.page_count` 可作诊断线索，但统一验收应建立 **schema版本 + producer + 最终Artifact.sha256 + 实际inspection结果** 的服务端缓存。缓存不匹配或历史无缓存时进行有界实际检查；不要相信工具输入 `page_count`、文件名“15页”或模型完成文本。
4. 缺页仍保留附件下载与真实页数，任务 partial；修正后重新读取新字节/新指纹。物理页数正确只证明结构和非空，不证明每页业务内容、数字或叙事不同，不能扩展成“内容完全正确”的承诺。

公共解析器已有单 XML 12 MiB、ZIP总展开量256 MiB、条目12000、图片像素边界。若把检查移到 `create_artifact`，须在字体规范化读取 ZIP 之前先做同等有界预检，再检查最终字节；只在写入后的 verify 检查不能保护前面的 normalize 解压。对历史文件也必须限制读取大小与解析预算。第一版可只对本次受约束的 PPT/Word/image 检查，避免重扫无关附件。

### 2.3 “Word 内嵌指定大屏”的证明链

| 证据层 | 当前可用的实际字段/结构 | 应核对什么 |
| --- | --- | --- |
| 大屏对象 | `Report.id / version / workspace_id / owner_user_id / status / datasource_ids / lineage / content` | 绑定用户指定或本任务明确创建的大屏 A，而非任意大屏；复用 `dashboard_deliveries` 的来源/可见组件/查询快照验证，当前权限用 `get_report` 等既有授权链复核 |
| 固定版本 | `ReportRevision.report_id / revision / snapshot` | 当前Report可变，图片来自v1而大屏已改v2时不能仍称“最新大屏”；明确历史版本请求应按授权后的对应修订核对。当前版本或修订快照须与约束绑定一致 |
| 大屏图片工件 | `Artifact.sha256` 与 `metadata_json.tool=render_dashboard_image`；`delivery_evidence.kind=dashboard_image / producer=server_renderer.v1 / dashboard_id / dashboard_version / source_query_ids / source_execution_ids / source_datasource_ids / render_mode / rendered_at` | 这些字段由专用服务端 renderer 在当次真实授权后写入；联合同任务运行与成功 ToolCall/来源回执核对，不能把自由同名metadata或文件名当签名。实际图片哈希必须等于存储字节摘要 |
| Word实体 | Word Artifact 的归属、ready、最终SHA与实际OOXML | `word/document.xml` 中绘图引用，经 `word/_rels/document.xml.rels` 指到包内真实图片；其字节摘要必须等于上述 **A的指定版本图片**，只把图片塞进 word/media 而正文未引用不算嵌入；外链图片不算离线交付 |

当前 `document_images.resolve_document_images:9` 先解析获准 Artifact 字节，`renderers._maybe_add_image_docx:138` 实际 `add_picture`；因此标准路径有真实嵌入行为。问题是 `render_document` 保存的 `spec_summary` 只有截断字符串，不能当完整关联证明。`ArtifactLineage` 模型虽然存在，本次搜索没找到生成文档时写入该表的调用；**不用为最小修复先加表或把 parent_artifact_id误用为多图关系**。

最小实现是公共检查器提取 Word 可达图片哈希，与同任务可信图片工件集合按 report_id/version 精确匹配，再将验证结果与 Word 最终SHA绑定保存到服务端 inspection缓存；这同时覆盖原生制作的 DOCX 和业务 renderer，不要求相信模型提交“已嵌入”。`resolve_document_images` 可额外返回服务端引用清单作追溯，但此清单不能代替最终 DOCX 字节检查。

当前 `render_dashboard_image` 输出的是 `saved_data_snapshot / saved_inspection_snapshot` 数据快照图片，不是像素级复刻交互设计器。该语义应保留给用户；“与原大屏布局完全一致的截图”需另立渲染合同，本批不可凭图片来源一致就宣称布局保真。

历史图片有当前上述来源元数据时可补读实际字节；历史 Word 只有任意图片或找不到可证实的 source report/version，应明确缺关联证据，不能补造。当前撤权/换数据范围必须沿 `get_report`、`historical_result` 或既有图谱/执行授权链拒绝读取与认可，错误转成明确未验证关系，不重查业务数据库、不自动重发源操作。

## 3. C04 长句继续的最小修复位置

### 已确认的两层问题

`tasks.choose_task:125` 的 `continuing` 使用整句 fullmatch，仅接受“继续/继续生成/继续完成/重试/重新生成/接着做”等有限短句。没有显式 task_id、也不命中 modifying/选项回复的长句，例如：

> 请继续完成上一条请求里尚未完成的产物，沿用原数据范围，不要重新查询。

不会选择前一个 task，随后建新 task/draft_id；该句未形成制作 type/count 时新清单为空，新 Run 即使没有补成果也可能按现有 verify 完成。此为源码分支确认，尚未在本轮运行用例。

另一个关联切点是 `choose_task:136–139`：即便选中了同一 task，也把本轮 `requested` 非空结果直接替换 saved.required_deliveries。例如原来2份PPT加Word/Excel，只完成1份PPT，用户“继续原任务，先生成剩余的一份PPT”，若仅放宽正则，清单可能缩成1份PPT，旧的已交文件就足以通过。显式 task_id 当前也走这个替换分支，所以只由前端补 task_id 不能闭环。

前端 `chat-session-view.js:272` 仅在 `state.dshTask?.id` 已有值时发送 task_id；`task.updated` 落在 `chat-session.js` 的 projections.tasks 中。在当前本地 `apps/web` 对 `dshTask` 的搜索只见读取与清空，没有自动为普通后续消息选择这个状态的赋值。因此不能假定现有正常输入总带 task_id；也不应把每条新消息无条件绑定最近任务，否则新业务会错误继承旧草稿/成果。

### 推荐的最小闭环

1. 在 `tasks.py` 抽出窄范围 continuation 判定：保留短句；补“继续/接着完成 + 上一条/刚才/本任务/剩余未完成产物”等明确回指长句，并排除否定、咨询、新建/另做、明确新资源范围。不做“文本任意位置出现继续即续办”的宽泛正则，也不引入第二个模型来猜身份。
2. 仍只选择同工作区/同用户/同会话的最近可续任务，显式 task_id 走既有归属校验；改变资源范围的隐式续办不能偷偷继承旧任务。`chat.prepare:351–361` 继承遗漏资源后会再次 validate_resources，必须保留。不要为了沿用草稿跳过当前 File、DataSource、client scope 校验。
3. **把“续办原任务”与“修改/重做新的交付”分开合并。** 纯续办保留完整 saved.required_deliveries 与新增结构约束；本轮“先做剩余的一份”是执行顺序/剩余工作，不重写原总数量。明确新增项目可以按稳定 requirement_id追加；明确撤销/替换才修改对应对象，不允许普通 requested 非空就抹掉整个旧清单。老 explicit required_deliveries 行为需版本化或明确操作语义，不能暗改所有API调用。
4. 同 task_id 让现有 `run_context.business_task_id`、`presentation_tasks/{workspace}/{task_id}` 与具名 draft_id 自然复用；保留“修改”requires_new_delivery 与新 Run 身份。**不要把长句继续实现成复用旧 Run/request_id 或原生 steer**：已终态任务的新请求仍是新 Run，C01 的窗口边界不可合并。
5. 新结构约束必须一起流经 `chat.steer:424–431`（当前按类型覆盖）、`queue_update:738–741`（当前按内容重建）、history/task.updated投影及恢复。可引入一个纯任务约束合并函数供这些位置共用，不能只修首次 prepare 后又在 steer/队列编辑时丢掉关系。

C04 只改善身份与义务延续，不承诺 automatic continuation；不能凭“继续”解除数据库异常停止、未知外部副作用或取消状态。对旧 unknown 任务需要保留阻断事实/待核实记录，避免新 Run 的任务级继承被误当成跨 Run 自动重发许可；跨 Run 防重若要新增应单列设计，不能声称 C01/C02 已提供此保证。

## 4. 可先写的三个失败复现

均建议复用 `worker_context / prepare / chat.settle` 真正 Worker 入口、独立 SQLite和临时文件；不启动真实模型，不连接业务数据源。以下为下一批执行计划，**本次未运行**。

### R1：一页真实 PPTX 不能满足15页要求

- `prepare("制作一份15页PPT")`，用 `test_dsh_native_deliveries.presentation_bytes` 写一页有真实标题的合法 PPTX，调用真实 `native_deliveries.present`，然后 `chat.settle(completed)`。
- 首个红断言：规范 status应partial、native_status仍completed，并能定位该PPT的实际1页/要求15页；原源码预计因presentation count=1而返回completed。文件名可故意叫“15页完整报告.pptx”，防止按名称信任。
- 修后对照：相同任务保存真实15个非空页的新文件，最终通过；下载后再用python-pptx查15页；同路径同Run旧版本应superseded。无页数的一页PPT继续合法，问“如何做15页PPT”不产生交付义务。原生与业务renderer两条路径都应最终经过统一证据。

### R2：Word嵌入B或孤立图片不能冒充指定大屏A

- 真实prepare要求“生成应收大屏和Word报告，并将应收大屏嵌入Word”。沿 `test_dsh_dashboard_delivery.saved_dashboard` / `dashboard_case` 的临时平台 QueryHistory 构造已成功保存查询与大屏A；再建另一个标题明确不同的大屏B，不发SQL。
- 通过当前工具网关和 `render_dashboard_image` 得到可信A/B图片。用真实 `render_document` 制作有正常正文但实际只嵌B图片的Word；A大屏和Word均真实ready，然后 `chat.settle(completed)`。
- 首个红断言：应partial且缺“A对应图片嵌入关系”，不能只因1个dashboard+1个document计数齐全而completed。对照缺图、仅word/media放A孤立图片、B图改名成A、A v1图片冒充要求的v2，分别不能通过。
- 修后正例：最终Word正文关系引用A当前约定版本图片的相同SHA，保留原Word/原图片来源证据，任务通过。复用公共 `inspect_file` 检查实际压缩包，不 monkeypatch 成“已嵌入”；已有 `test_openwebui_query_delivery_chain:145` 能作为图哈希与真实Word构造参考，但新的终态断言必须打到DSH chat.settle。

### R3：长句继续不能新建空合同，也不能丢原交付清单

- 原prepare要求2份PPT、Word、Excel；实际只做一部分或暂不做文件，真实settle得到partial。保存task_id、draft_id、原required_deliveries；可通过实际create_presentation_plan/write_presentation_slides保留一份具名草稿页。
- 不传task_id，发送上述“请继续完成上一条请求里尚未完成的产物，沿用原数据范围，不要重新查询”。首个红断言：新Run的task_id/draft_id应相同、原清单不缩水；原源码预计生成新ID和空清单。随后空工具completed仍应partial，已保存草稿可在同任务目录读取。
- 邻接必测：显式task_id或修后隐式续办下，“继续原任务，先生成剩余的一份PPT”不能将原2份PPT和其他格式改成仅1份；这是只修正则后仍会失败的第二断言。普通“另外做一份新的PPT”、资源改变、其他会话、撤权/停用、取消/未知操作等不能误当无条件可续执行。

## 5. 最小产品改动建议与边界

建议下一批先由三组红测固定合同，再分两段实施：

- `tasks.py` 或旁置纯 `delivery_constraints.py`：用户明确要求编译、任务约束合并、对象绑定与verify；`chat.py` 仅接入prepare/steer/队列编辑的共同合并函数。
- 公共附件 inspection 包装：调用既有解析器、校验实际字节与缓存指纹；必要时在 `artifact/service.py` 统一登记服务端结构证据；`dashboard_image.py` 已有来源字段优先复用，缺少最终报告快照摘要时再最小补充。`document_images.py` 的可选引用清单仅用于追溯，不是验收替代品。

不要求重写presentation渲染器、替换DSH运行循环、增加UI确认卡、重复SQL或自动重复外部操作。先保存有据的partial与缺项，不把“模型结束后自动补齐直到成功”混入这次产物完整性修复。所有历史缺证据、撤权、损坏包、指标仍错误、重复页/空洞叙事、布局保真等边界要如实呈现。

## 6. 实际阅读范围与快照

完整通读或补读关键函数：tasks.py；native_deliveries.py；artifact/service.py、document_images.py；公共artifact_inspection.py；request_constraints.py；旧DeliveryTracker及兼容alias；test_dsh_multiple_presentation_drafts.py、test_dsh_query_delivery_resume.py、test_presentation_request_pages.py、test_task_clarification_delivery.py。

针对性读取：chat.py SendInput/prepare/steer/queue_update及已知settle；draft.py的任务键、存取/创建、写页状态与final_spec；builtin_provider.py的create/write/finalize与render_document/render_presentation分支；Artifact/ArtifactLineage定义；Report/ReportRevision与get_report/add_revision；dashboard_image的来源授权、panels与render_dashboard_image（未逐行审图形绘制布局）；query_presentation的导出校验与工件登记；Word renderer的图片分支；前端render/send/task投影；tests中的真实文件/嵌图/来源及fixture入口。目录级rg只用于定位，未声称全模块全量审计。没有阅读业务数据库内容。

下表哈希覆盖整文件，仅表示快照，不表示上面标为局部读取的文件已全文审查：

| 文件 | SHA-256 |
| --- | --- |
| `docs/audit/review_20261004/plan-presentation-completion.md` | `41e2cabf050aa9a16c2a52803a350ecbc6ac2aca929c04a4faca63a36c81959b` |
| `backend/app/business_worker/tasks.py` | `147e449acd7a5daef30ad24f2d9b4edb8d203ee4ad5e48c5d29a3ad677d70455` |
| `backend/app/business_worker/chat.py` | `9681835a4359926c27a4b9d4c60c169a638141a2a47fc762ac11a8c9d8524332` |
| `backend/app/business_worker/native_deliveries.py` | `30b11681140f8b0cec55637e1821e65a94bff1efd853d6d65fdd3201275d22bc` |
| `backend/app/business_worker/dashboard_deliveries.py` | `39a3d86fa127a9bf7d725a9f6467ac0deca7af8b9ee175e8fd37d7ca9d634e1a` |
| `backend/app/models.py` | `dce44685054d4efc2713b88362cb553f63c9e34ad9efb4efab16222f74620340` |
| `backend/app/modules/artifact/service.py` | `990c63f8357b1175f79c1af9f8f6ab03b0281b3884beb3786a1a0ef99aa8a4b6` |
| `backend/app/modules/artifact/document_images.py` | `d024f853f7571cab6c67c0ad9f53cce80322cd591558c40d23406984708e68c8` |
| `backend/app/modules/artifact/renderers.py` | `5d253ac6636b53f8f42f5161d8b69eb7fed2128e598b5655289c5d6713167669` |
| `backend/app/modules/chat_artifacts/artifact_inspection.py` | `d4374c7d3f4690368cb266589733e84b7628f89ff150bc7edc7c3d52a026c9e1` |
| `backend/app/modules/chat_artifacts/dashboard_image.py` | `e3a42adf7727db6f6c2909cb92284ce312684d1e06bc457d34e562280593d40e` |
| `backend/app/modules/chat_artifacts/query_presentation.py` | `b70529515e2904740c5d1fdf50fa51c9ed6a70d252fe068b42cb84baf0a89a23` |
| `backend/app/modules/presentation/draft.py` | `029871420a45a6d4a65b4f02a756fc920eeea5a43b4c76a90d00c96d980b1ef6` |
| `backend/app/modules/presentation/request_constraints.py` | `ad15655ba35cbe5726097c68a2664e1d685b1941b5199d1cf523750a159e97d8` |
| `backend/app/providers/tool/builtin_provider.py` | `dfc6f95cbb1a710306231e0a1a43ab5511d111c0443b5801e9d9c2989646cbf5` |
| `backend/app/modules/reports/models.py` | `376887743ed80535e3fd06369fa6d01383296d4c30666e9a1d37e4d23aed4e58` |
| `backend/app/modules/reports/service.py` | `a857ecd541adf8667bb7bc2a174e318202231c2ac8ffae44b187c17035fd65f3` |
| `backend/app/modules/openwebui/delivery.py` | `d76998254cee5ede0249c7ba82f98590b843c204f1d3d14ff3d1e051f493ab55` |
| `backend/app/modules/openwebui/artifact_inspection.py` | `969f4a7ee0b9cf6b1d72c0d22490bfac62992042fbcabbdd2b131eaf998aa27f` |
| `apps/web/assets/platform/chat-session-view.js` | `d354fe18baebdabb5d4044b3938556304ce227e87e14f20cea2917600cb5551f` |
| `apps/web/assets/platform/chat-session.js` | `26c786d4d71c517e7e7d9271e8d33accc7c42d58ddd76a0fd05fc18d0886c306` |
| `tests/backend/test_dsh_native_deliveries.py` | `3b5fbd96d6e9fe6547fd69d47dd2adacf38fc79353613065b6289a8a0981c3a0` |
| `tests/backend/test_task_clarification_delivery.py` | `67b16cf8dec5ee1fc77c7328da9dddb7882d8b9057a45c9e695f2bb2d53879cb` |
| `tests/backend/test_dsh_multiple_presentation_drafts.py` | `28217d5df1bd00e9001f821e6fd19f210f455b6d9bd9a4db9a9a5e4335380eb6` |
| `tests/backend/test_dsh_query_delivery_resume.py` | `6569bf9bd705679a1344bb0a30170146e4fd8cf26eb4c8ffe274498ecce7376b` |
| `tests/backend/test_presentation_request_pages.py` | `78c60b25b568284c73d4ad4ca17975a5819e83a62c99cb40c56ba687f01c0cb9` |
| `tests/backend/test_openwebui_query_delivery_chain.py` | `f9fa20871279e7c6e70859a86c192657fc79eca720d97b5e2b7be7434847111c` |
| `tests/backend/test_openwebui_delivery.py` | `26b479d2114e3786b56e7391d26af0f6bfd9681742602feb9be8e4f80b14ed5f` |
