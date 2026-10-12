# 批次十六待实施：逐份交付修订与受证据约束的继承

2026-10-04，只读设计。工作树 `major-compound-delivery`，当前 HEAD `a34fdc0b1ab01049a468c9068a11e7c0af9b1137`。批次十五的 5 个产品文件、2 个新测试文件哈希保持冻结值。本次只写此 ignored 计划，**没有修改产品/测试，没有运行测试、模型、数据库、HTTP、浏览器或服务**。以下缺陷是源码分支确认，测试条目均为下一批待执行，不是新取得的红测。

## 1. 现状：一个布尔值承担了三个不同问题

`required_deliveries` 只有格式和总数；`delivery_constraints` 只为明确页数、嵌图关系保存 requirement_id，并不是“每一份交付物”的统一身份。当前没有能够表达“PPT第1份已是修订2，Word第1份仍沿用修订1”的记录。

| 环节 | 实际位置 | 当前行为及后果 |
| --- | --- | --- |
| 修改识别 | `tasks.py:115–145` | `modifying` 或显式 task_id 下的修改词令 `revising=True`；结果写整个 task 的 `requires_new_delivery=True`。保存原交付清单后，PPT局部修改也携带原Word/Excel义务 |
| 未完成修订继续 | `tasks.py:143–144` | 旧标志为真且 task 未完成时，纯继续仍继承真值。它没有区分“修订已经开始”与“这次又提出新修订” |
| 数量核验 | `tasks.py:205–212` | 真值下所有格式都只计 `source_run_id == 当前Run.id`；旧Word/Excel即使合法且未要求修改，也不计数 |
| 实体约束核验 | `delivery_evidence.py:211–219` | 所有文件同样只留下 `artifact.run_id == 当前Run.id`，所以只放宽上面的计数仍无法完整解决；PPT和嵌图Word的检查也需使用逐项资格 |
| 已产物聚合 | `tasks.py:164–170,187–202` | 实际已聚合同工作区/用户/会话/任务的历史文件；问题不是找不到历史，而是整任务标志把它们全部排除 |
| 队列编辑 | `chat.py:723–744` | 保存合并后的合同后，无条件写 `requires_new_delivery=False`；修订任务编辑文字可重新接受原附件，即使没有新修订 |
| 原生重新登记 | `native_deliveries.py:127–145` | 旧版本只在**当前Run**内查找。新Run可以把同一会话工作目录中未改动的旧文件再次 present 成新 Artifact；新 run_id 不证明内容已经修订 |
| 复用下载链接 | `native_deliveries.py:28–59` | 明确保留原 `source_run_id`；这是安全的展示引用，不应改成当前Run来伪装修订 |
| 大屏版本 | `dashboard_deliveries.py:176–208` | 已按真实回执、`reused=False`、当前版本大于旧版本识别本轮修改，可作为“修改需要专用证据”的参考；不能仅靠字符串title或更新时间 |

因此下一批必须同时处理：**无关文件被迫重做、同次修订跨轮不能累计、旧文件重新登记冒充修订、队列编辑取消修订要求**。单纯删除 `requires_new_delivery` 会把前两个问题变成后两个问题，不能采用。

### 具体静态路径 A：只改PPT却缺Word/Excel

1. Run A 要求并真正交付3页PPT、Word、Excel，task completed。
2. 新消息显式带同 task_id：“只修改刚才的PPT第1页标题为《已修订》”。`choose_task` 判修改，保留全部三种义务并把布尔值置真。
3. Run B 真正生成修改后的3页PPT，未重生成Word/Excel。
4. `verify` 对B的计数为 PPT=1、Word=0、Excel=0，虽然 `deliveries` 内仍有A的有效文件。因此静态必走 partial 的缺项分支。

### 具体静态路径 B：同次修订被强制在一轮全部做完

若要求修改两份PPT，B只完成第一份后partial；用户纯“继续”，C只完成第二份。真值继续继承，但C只数C产物，B已经完成的第一份仍不计。与每轮重新修订不同，这里用户没有再次修改第一份。应当让B、C共同满足**同一目标修订号**，而不是要求全部成果都生于C。

### 具体静态路径 C/D：清标志或换Run可反向误完成

- C：A的3页PPT已完成；B提出改标题而进入queued；`queue_update(B, edit, 相同修改要求的补充文字)` 清掉真值。B即使未产生文件，旧A文件的页数/格式仍符合，于是现有数量和结构门槛可能completed。页数核验不等于标题修改核验。
- D：A的PPT文件仍留在该会话稳定工作目录；B提出修改，在B原样present同路径。当前Run内无旧Artifact，`create_artifact` 新建B记录，最终SHA可与A相同；只看当前Run就会把它当新成果。换文件名、复制字节也有同类风险。

这些路径尚未执行。本计划不把它们记成批次十五门禁已经发现的动态失败。

## 2. 两种新增说法目前均不会自动继承

前提：省略显式 task_id，最近一轮是同用户、同工作区、同会话、同资源范围的可续任务。

| 输入 | `delivery_contract.is_continuation:15–24` 的实际分支 | `tasks.choose_task` 的结果 |
| --- | --- | --- |
| `重试刚才失败的任务` | 短句fullmatch不接受后缀；长句分支只允许开头“继续/接着”，不包含“重试” | modifying正则也不匹配；candidates为空，建新task/draft。该句没有制作格式，required_deliveries为空 |
| `继续，把剩下的做完` | 短句fullmatch被逗号/后续文字打断；长句虽然以继续开头，但回指词表有“剩余”没有“剩下” | 同样没有modifying/选项命中，建新task/draft；“做完”虽有“做”但无格式，原交付清单不继承 |

随后若原生模型无产物直接结束，新任务的空合同不会阻止completed。**这是静态可达，不是本次真实运行结果。** 显式传正确task_id时，两句可走旧任务分支；不能假定普通前端输入一定会传它，本次未重新审查前端发送实现。

最小解析改动：让“重试”进入带明确回指的动作集合，给“剩下的/剩下部分”增加窄范围剩余工作表达。仍按句首动作、明确回指、最近可对应任务、资源范围核对，不把任意文本中出现“失败/继续”都当授权。“如何重试”“不要重试刚才任务”“另做一个新的任务”是负例。

“重试”只决定**继续哪个业务任务**，不自动重发失败工具。普通参数错误、明确not_sent、用户取消、已派发结果未知必须分别处理。不能为命中这两句话复活旧Run/request_id；终态后的继续仍创建新Run，保留业务task/draft身份和目标修订。

## 3. 最小数据设计：义务身份、修订号、验收证据分开

建议在现有 `business_task` JSON 中引入 `delivery_contract_version=2` 与 `delivery_obligations`，第一版不增加通用任务表或重写DSH执行器。既有 `required_deliveries` 继续作为兼容的按类型计数投影。

每个要求交付的“份”建立**服务端持久分配**的 `obligation_id`；它不能由文件名、列表当前位置、模型参数临时生成。具名文件附对象锚点；两份无名PPT也有两个不同slot。现有页数/嵌图 constraint_id 继续代表校验规则，关联至这些slot，不把一个constraint_id误当成全部文件身份。

建议最小字段（以下均为设计，不是已实现字段）：

```json
{
  "obligation_id": "服务端稳定标识",
  "type": "presentation",
  "target": "ERP介绍",
  "revision": 2,
  "request_event": {
    "run_id": "提出修改的Run",
    "message_id": "真实用户消息",
    "sequence": 12
  },
  "base_revision": 1,
  "base_artifact_id": "此前绑定的真实文件",
  "base_sha256": "此前最终字节SHA",
  "constraint_ids": ["总页数等规则"],
  "change_requirements": ["可机器核对的明确修改要求"],
  "scope_fingerprint": "服务端资源范围摘要"
}
```

另存验收证据 `accepted_deliveries`，至少绑定：`obligation_id + revision + artifact_id/report_id + final_sha256/report_version + producer + 来源Run/ToolCall或原生事件 + 验收规则版本 + 资源范围 + 验收结果`。不可只存 `accepted=true`，也不能因为本轮只有一个同类型文件就把它覆盖到任意slot。

三个原则：

1. **修订号只由真实、已接受的用户变更递增。** 纯继续/重试不递增；同一request_id重放不递增；不能每产生一个新Run就递增。queued消息重编辑应基于其继承基线重新计算目标变更，不让循环编辑重复升号或撤掉修改要求。
2. **修改只影响解析明确的slot。** “只改ERP PPT”不能让Word/Excel升号；“两份PPT都改”影响两份；无法区分同类型哪一份时保留未解析变更，不默认选第一份，也不能把所有格式都设为新修订。显式整体清单替换仍遵守当前API的替换语义。
3. **继承的是同一slot的同一修订证据，不是随便找一个历史ready文件。** 纯继续可以接受B产生、C再次核验通过的revision2成果；改变目标为revision3后，B的revision2结果不能再补齐最新义务。

## 4. 继承证明与新修订证明

### 4.1 未要求修改的文件可继承，但每次重新核验

继承需要同时满足：

- 原Run仍属于同workspace/user/conversation/task，不能扩大现有 `task_runs` 边界。
- 当前 `run_actor`、服务客户端与scope有效；文件权限、底层数据源/字段范围、报告及来源授权仍有效。继承旧证据不能继续使用旧AuthContext或旧token授权。
- Artifact真实ready、存储存在、最终SHA与原证据相同；有界实际检查不能被文件名、大小、自由metadata或历史completed文本替代。
- 对应slot修订号、规则版本和资源范围匹配；Word图片关联继续重验。大屏“当前版本”义务在Report变化后不能无条件沿用旧版本截图；明确版本仍按所选版本，无法证明保持未完成。
- 继承结果记 `inherited_from`，原Artifact.run_id、ToolCall身份、原报告版本不被改写。`reference_saved` 只加展示卡片，不制造新修订。

依赖应局部传播：若修改的是PPT，而Word没有引用PPT的义务，Word不失效；若修改的是大屏且Word要求嵌入该大屏**当前版本**，旧Word关系需要新证明。明确固定旧版本的Word不能被无关当前版本变化自动改写。不要用整任务布尔代替这条依赖边。

### 4.2 新Run、新artifact_id、不同ZIP哈希都不够证明修改

新修订至少要绑定修改请求的服务端epoch/slot revision与真实生成回执，且通过最终文件/来源核验。应从工具准备/派发时捕获本次合同epoch，不能在结果返回时直接贴上那一刻的最新revision：同Run接受steer后，旧指令下已经开始生成的工具可能晚到。

两层最小防伪：

1. 对明确内容修订，拒绝与基线相同的最终SHA；跨Run原路径present、复制改名、`reference_saved`都不能仅靠新来源Run达成修订。
2. **不同SHA不是语义修改证明。** 重压ZIP、修改docProps日期、关系条目顺序等会改变字节。优先复用可信编辑工具对基线文件/指定页的修改回执，并针对支持的变更读取实际实体断言；必要时提供OOXML内容规范化摘要，排除纯包元数据差异。规范化摘要变化仍不代表用户要求被正确修改，需区分“内容改变”与“指定修改已验证”。

第一批可优先支持明确、可检查的改动，例如总页数、指定物理页标题精确文本、指定大屏版本/嵌图。对于任意“更好看/更专业”等无法证明的修改，不伪造内容正确凭证；保留新附件，并给出具体 `revision_evidence_missing` 或 `revision_change_unverified` 边界，而不是声称换了artifact_id就完成。

原生路径尤其不能信模型自填revision或修改时间。`present`目前只能证明所读文件在登记时真实存在；如无法取得与用户变更对应的可信执行epoch，就必须依靠基线内容与支持的实际修改断言，证据不够时不能自动升级为最新修订。`parent_artifact_id`当前只链接本Run同路径版本，既不是跨任务授权，也不是完整编辑语义证明。

### 4.3 多轮和并发晚到

- B已为slot1生成revision2，C纯继续为slot2生成revision2：二者可共同交付。资格由slot revision决定，不要求同一个Run。
- B为revision2生成过程中，用户C明确要求revision3：B的晚到结果只能登记为revision2候选，不能覆写revision3证据。
- 同Run steer也遵循相同规则。工具dispatch捕获epoch；生成完成后按捕获值登记，不能读取最新合同后补贴新号。
- 在既有会话锁/事务下更新合同，落验收时校验期望epoch。保持旧Run证据追加可追溯，不能用“最后完成时间”选择最新业务要求。多进程场景是否需数据库比较并更新应在实现时明确，进程内锁不能当成数据库并发证明。

## 5. 最小代码切点

| 位置 | 下一批建议 |
| --- | --- |
| 新 `delivery_revisions.py` | 纯合同变更分类、稳定slot/修订规划、资格/继承证明判断；所有字段服务端生成 |
| `delivery_contract.py` | 将规则关联到slot；明确局部变更，不在普通继续时重置旧修订。补两种窄续办说法 |
| `tasks.choose_task / ensure_contract` | 规划受影响义务；version1迁移先重查旧证据，不假设历史accepted；保留task/draft，不复活旧Run |
| `tasks.verify` + `delivery_evidence` | **共享一套**逐义务资格集合和唯一匹配结果，数量与实体规则不能各用不同范围。逐项输出缺失/过期/撤权/变更未证明原因 |
| `chat.prepare / steer / queue_update` | 接受用户变更时持久epoch；保留重复请求幂等。队列编辑不再直接清除修订要求，新建消息编辑与续办编辑有不同继承基线 |
| `tools.execute`、native登记边界 | 从服务端执行上下文登记epoch与来源，最终字节绑定实际Artifact；不能允许模型自由metadata声明满足某revision。涉及这些文件需要根代理另行分配所有权 |
| `dashboard_deliveries` | 复用实际Report版本/非reused回执；为新slot资格提供来源，不拿报告更新时间替代修订证明 |
| 投影/历史 | 原文件继续可下载；标注满足哪个slot/revision和是否继承。不要为了界面整洁全局删除或supersede仍被其它用途引用的Artifact |

第一批不要求重写Host循环、另加模型分类器或自动无限续跑；不改字体/查询/图谱工具，不建立泛用工作流引擎。逐slot模型仍可先保存在Run.context_snapshot。历史不能唯一匹配slot时保守保留未分配候选，不能按文件名猜。

## 6. 未知副作用与数据源异常是独立的执行约束

当前 `tools._saved_tool_calls:52–66`、`replay_guard:22`、`dispatch_state.unconfirmed_operations:119` 的关键边界仍是当前Run，同task并不等于跨Run防重。下一批扩展“重试”识别不能暗含旧外部操作可重做。

- 从可信原ToolCall/派发日志读取同任务未核实操作及原幂等键。仅按服务端验证的workspace/user/conversation/task/run归属查内部回执，不能因 `runs.view` 被撤销而看不到阻断事实。
- 普通确定失败、prepared/not_sent与dispatch_started/uncertain分开。unknown在新Run仍待核实，模型说“重试”、换call_id、换参数、换义务revision都不能自动解除。
- 若允许新的Run继续无关文件制作，应在**新派发之前**对涉及旧未知副作用的操作执行保守阻断；只把一段提醒写进system prompt不构成防重。复用原幂等键及目标资源身份；核实来源缺失时不能臆造“远端未执行”。
- 本批修订计划不把数据库异常停止标志变成自动重试许可，不自动重查原库。用户取消后新任务可由明确请求继续，但旧运行的取消终态和记录保持。

## 7. 下一批先执行的反例与正常对照

统一复用现有 `worker_context/prepare`、真实 `tools.execute/render_document`、`native_deliveries.present`、`deck_bytes`、真实openpyxl文件、`saved_dashboard/dashboard_with_complete_query` 和临时SQLite。未知副作用仅使用本机回环受控桩或持久化故障夹具。命令仍必须经主树绝对Python和本树 `tests/run_isolated_backend.py`，不连接业务库/真实模型/8011。先小组红例，失败后定位，不默认重跑大组或扩大超时。

| 编号 | 待执行场景 | 应断言的结果；当前状态判断 |
| --- | --- | --- |
| R1 | A完成PPT/Word/Excel；B明确只改PPT标题，真实输出修改后的PPT | B完成，Word/Excel沿用同artifact_id并重验，PPT是revision2；当前静态预期partial，核心首红 |
| R2 | 两份PPT都进入revision2；B只改第一份partial，C纯继续只改第二份 | B、C共同满足同一修订，两份不重做；当前整轮过滤静态预期仍partial |
| R3 | 只要求PPT修订，B把A工作目录旧文件原样present，或复制改名后present | 应partial且revision仍未证明；当前新Run新Artifact可能completed，待红 |
| R4 | B仅重新压缩旧PPT或改包内时间字段，最终SHA变了，语义内容没变 | 仍不能满足明确标题/页内容修改；防止只加sha!=base就误过 |
| R5 | A的旧文件已完成；B修改queued；编辑B文字后不产生文件直接settle | 仍partial，修改义务不能因queue edit消失；当前静态可能completed |
| R6 | ERP与MES两份PPT，只修ERP；保留MES、Word/Excel旧文件；同名/交换文件干扰 | 仅ERP升号，唯一文件对应；不能把MES旧文件或另任务文件绑定ERP新修订 |
| R7 | 真工具准备/生成后阻塞；同Run接受steer改新页数/标题；旧结果随后返回 | 旧epoch回执保留但不满足新revision；再生成符合新要求的文件才能完成。用事件屏障，不靠sleep制造顺序 |
| R8 | 重放同request_id/同steer，及同任务新的纯继续 | 不重复升号/不重派原调用；新的明确修改才升号。未知副作用原幂等键不被清掉 |
| R9 | 继承前撤files.view/analytics.export/数据源资源授权，或删文件/篡改存储SHA | 旧accepted不能继续充数；仅返回安全的缺证据原因，恢复三个Session授权字段，无新查询/工具调用 |
| R10 | 改大屏，Word要求嵌当前版本；另一Word固定历史版本 | 前者关系失效待新证明；后者按固定版规则，不靠当前v2图冒充v1。独立PPT修改不影响二者 |
| R11 | 分别发送`重试刚才失败的任务`、`继续，把剩下的做完`，不带task_id | 保留原task/draft、全清单/目标revision；无产物结束仍partial。当前静态预期新ID/空清单 |
| R12 | R11下普通已确认失败、not_sent、cancelled、unknown；unknown工具故意换call_id/参数 | 可续业务任务不等于可重放；未知外部桩发送次数保持原值，独立安全文件动作可继续 |
| R13 | 显式新任务、咨询/否定、换资源范围、其他会话/用户、最近任务无法唯一对应 | 不继承错误任务；不能扫描并随意选择更早任务，也不能用空合同悄悄宣布旧任务完成 |
| R14 | 旧v1快照没有逐slot验收，部分文件缺sha/来源或同名无法分配 | 只对真实可重验的证据建立继承；不批量标accepted；原始历史不改造为虚假新回执 |

优先次序：R1/R3/R5/R11先建立行为红；再落实共同资格函数与版本接线，R2/R6/R7验证跨轮/晚到；授权与副作用R9/R12为关闭修复前的必过门槛。不能先只放宽历史文件计数而延后补这些安全边界。

## 8. 实际阅读范围和源码快照

全文复读 `tasks.py`、`delivery_contract.py`；定位复读 `delivery_evidence`资格/匹配与继承相关函数、`chat` prepare/steer/queue edit、native reference/present、dashboard版本识别、工具派发/回放/持久回执、dispatch/replay边界。相邻测试只读对应fixture和相关案例，没有全量重审所有用例。未重新分析前端、渲染布局、各工具内部所有语义，也没有运行正则测试来替代真实链路测试。

| 文件 | SHA-256 |
| --- | --- |
| `business_worker/tasks.py` | `F486471D42A3C03F3A43570A7AF5F7851E0764A79E4A683EBB0E030272FE7444` |
| `business_worker/delivery_contract.py` | `22D625193E29A223C6ECED88810311C3258A084801C27ED6F210341710C506B8` |
| `business_worker/delivery_evidence.py` | `6750EC4F7367720AFB1ADD1E547CC3C057A2F7169189838D9644CB7D3BEA97A4` |
| `business_worker/chat.py` | `08B9F68CE2A248BAAF0C3F2973119979B4661E4CAAC74C54366F94724B999CBB` |
| `business_worker/native_deliveries.py` | `8917719DDCAFF1EFEE08FCC7631E848938B506208170124515CE4931DC02C558` |
| `business_worker/dashboard_deliveries.py` | `6B7A107E4E7767E211549B2ADE83C0F1077611A426213AC32EC06F319A416115` |
| `business_worker/dispatch_state.py` | `DF6FD0CCE163690D7561523594C431298DA9054424795C2BD42890E267627AA2` |
| `business_worker/replay_guard.py` | `FAA4610C6137A9C652A89756D1421D8C4E0D4DCE4129C4E0720A421BCD5FB58E` |
| `business_worker/tools.py` | `1430514BC410EACAA01DF4F22E567251189BEBA86FAF4C2C3DA69F4689F8C867` |

以上路径均位于 `backend/app/` 下。读取哈希覆盖整文件，但不表示标为定位复读的文件已经再次逐行全量审计。
