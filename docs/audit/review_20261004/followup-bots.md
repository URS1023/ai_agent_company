# 公开 Bot 下一批只读审查

日期：2026-10-04。审查基线：`077e6e7f2f10f32e08ecb64a7c0b885db6b8ced7`。

本报告属于下一批静态审查，不计入前一批已验证结果。已完整读取 `backend/app/modules/knowledge_graph/bots.py` 第 1–1792 行，并按需读取路由、请求模型、公共身份/资源授权和错误响应依赖的片段。没有修改产品代码，没有启动服务，没有访问数据库、真实模型或网络，也没有执行隔离运行测试。下文“确认”表示从当前源码完整调用链可以确认的行为，不表示已经完成动态复现。

工作树：`E:\code\Yun_tian\the_company_agent\test5\.implementation\worktrees\major-capability-contracts`。本报告在被 Git 忽略的 `.implementation` 下；源文件在审查结束前与 HEAD 对比未见修改。全文和片段的原始字节 SHA-256 记录在文末。

## 确认问题

### BOTS-N01 / P1：正式版调试丢失服务客户端 scope

- 主位置：`bots.py:1639–1647`、`bots.py:806–817`；工具执行还会在 `bots.py:1294–1303` 再次建立不含原授权上下文的执行字典。
- HTTP `get_current_context` 正确保留 `client_scopes/client_id`（`common/deps.py:319–322`）。`debug_bot` 的入口 `get_bot(..., "bots.execute")` 也会按该受限身份校验 Bot。可是 `_run_bot` 只收到 `actor_user_id`，未收到原 AuthContext；`_bot_context` 用 `build_context` 重新生成身份时没有携带原 `auth_context`。`business_ai/context.py:43–50` 仅能从传入的 `auth_context` 保留 scope，因此这里变成 `client_scopes=None`。
- 最小触发：底层用户有 Bot 及知识库权限，服务客户端仅授予 `bots.execute`，未授予 `knowledge.view`；对已发布、绑定知识库的 Bot 发送 `usePublished=true`、`mode=retrieval`。正式版分支跳过草稿绑定校验，之后知识检索身份重新取得底层用户权限。若底层用户为工作区管理员，`authorization.py:100` 还会因为 scope 丢失恢复管理员身份。
- 影响：受限服务客户端可以获得超出 scope 的知识片段；图谱、Skill、工作流及工具阶段沿用同类身份重建方式。工具网关自身再次鉴权无法恢复已经丢失的 scope。
- 最小修复方向：将原执行身份的客户端约束贯穿 `_run_bot` 和所有阶段；重读用户/成员/角色时，仍传入同一工作区、同一用户且同 Session 的受限 AuthContext。公开 Bot Key 的 Bot 所有者执行身份应另行明确构建，不能与受限登录客户端调试混用。
- 必须隔离验证：真实 HTTP 服务客户端令牌，`usePublished=true`，有/无 knowledge、graphs、skills、connectors/workflows 执行 scope 的正反例；底层普通用户与管理员分别覆盖。模型、ES、外部传输全部桩替换。不能只直接调用 `_run_bot`，那样会跳过造成问题的 HTTP scope 传递链。

### BOTS-N02 / P1：公开 Key 调用未要求 Bot 所有者仍有 bots.execute

- 主位置：`bots.py:1514–1516`，依赖 `bots.py:806–817` 和 `bots.py:1777–1780`。
- `invoke_public` 核对 Key、Bot 状态及正式修订后，以 `bot.owner_user_id` 进入 `_run_bot`。入口只调用 `_bot_context(..., "bots.execute")`；该函数重建身份、设置动作、绑定范围，却没有 `require_permission` 或对 Bot 的 `require_resource/get_bot` 校验。
- `bind_data_scope` 不是动作授权函数：`common/data_scope.py:118–123` 对不具备权限的根资源安装恒假查询条件；其根/子资源登记表没有 BusinessBot 或 BotDebugRun。因此传入 `bots.execute` 不会自动拒绝无此权限的用户。运行跟踪创建函数 `runtime/service.py:78–104` 也没有补上这一授权。
- 最小触发：普通 Bot 所有者先发布并创建有效 Key，再只撤销 `bots.execute`，保留其知识库查看及正常运行观测相关权限；下一次公开调用仍可走知识片段检索。Bot 所有者停用/移出工作区与 Key 显式撤销是其他已有门禁，不等于本项动作权限撤销。
- 最小修复方向：在创建 BotDebugRun 或调用任何源能力之前，明确要求 `bots.execute` 并对本 Bot 进行资源级检查。不要以 `bind_data_scope` 代替动作闸门。
- 必须隔离验证：只撤 `bots.execute`，不移除成员、不撤 Key、不撤 knowledge；下一次新请求应拒绝且不创建运行、不调用模型/动作。保留恢复权限后的成功对照。

### BOTS-N03 / P2：固定 Skill/工具版本仍被“当前发布指针”限制

- 主位置：工具 `bots.py:1178–1195`；Skill `bots.py:1360–1378`。
- 工具已经记录了快照 `versionId`，查询却先要求 `ToolVersion.id == Tool.published_version_id`，再要求该 ID 等于快照 ID。Skill 同样要求 `Skill.published_version_id == version_id`。一旦源资源正常发布新版本、当前指针改变，保存完好的旧正式版本就不再可解析。
- 后果：工具会在动作目录阶段被归为不可用（`948–1000`），公开响应可能只提示未执行；Skill 会直接使 Bot 运行失败。Bot 正式修订和历史内容虽然没被修改，其固定能力仍因源资源升级失效。
- 这与图谱/工作流的固定历史修订行为不同：`bots.py:712–716`、`876–882` 明确保留历史版本，仅用源资源的停用/删除状态作为撤销点。
- 最小修复方向：通过固定版本 ID 与资源归属关系加载历史正式版本，同时独立检查源资源当前可执行状态及当前用户权限；不要要求固定 ID 仍等于当前发布指针。若产品确实要求“源资源任何新发布都强制重发所有 Bot”，则应明确写入契约并调整固定版本描述；当前模块和 API 描述没有表达该语义。
- 必须隔离验证：Bot 固定 v1；源 Skill/工具发布 v2 后 Bot 仍执行 v1；显式停用/归档源资源后拒绝。图谱/工作流作为同类对照。

### BOTS-N04 / P2：工具资源查询发生在 connectors.execute 范围绑定之前

- 主位置：`bots.py:1203–1208`。
- `_bound_tool_spec` 先用当前 Session 的范围执行 Tool/ToolVersion/Connector/ConnectorVersion 联表，再调用 `_bot_context(..., "connectors.execute")` 和 `require_resource`。此前知识、图谱、Skill、工作流阶段或入口身份通常把连接器域回落到 `connectors.view`；首次工具读取会受查看范围限制。
- 最小触发：用户保留 `connectors.execute` 的目标资源授权，但查看权限被撤销，或 view 只有 self、execute 对该连接器有明确授权。第一次联表已被过滤为空，代码到不了正确的执行范围校验，动作被错误标记为失效。工具目录顺序/前一阶段不同可能影响结果。
- 最小修复方向：在查询之前建立当前 `connectors.execute` 身份与范围，之后仍进行资源级检查、版本和状态检查。不要移除过滤来解决。
- 必须隔离验证：view/execute 的权限和数据范围不同，执行动作授权存在时可解析；撤销 execute 后必须拒绝；切换前置阶段和绑定顺序不应改变结论。

## 与 root 图谱删除审查的联动

root 已全文审查 graphs.py 并发现 remove_graph 只检查 Bot 当前草稿 graph_ids，未检查当前正式 BotRevision 快照；本报告不重复把该读取计入独立全文覆盖。

Bot 侧后果可以确认：`bots.py:703–716` 在读取历史 GraphRevision 前调用 `graph_service.get_graph`；后者 `graphs.py:56–61` 将 deleted 图谱视为不存在。即使历史快照仍在，草稿解绑、未重发的正式 Bot 在源图谱删除后也会运行失败，不会凭旧快照独立继续运行。固定版本不应被视作绕过显式撤销的凭证；应由删除依赖检查保护仍在使用它的正式 Bot，或明确执行联动下线。

## 已确认的数据流，但需区分策略和未验证推断

1. 历史回答/引用只有 Bot 查看门禁：`list_debug_runs`（1656–1663）仅验证 bots.view 和 Bot 归属，再返回该 Bot 全部历史运行；`debug_run_view`（157–175）原样输出 answer、references、errorMessage，没有按当前知识库/图谱/连接器来源授权重验，也未按 BotDebugRun.owner_user_id 过滤。检索正文、图谱属性、工作流结果及工具数据确实可以保存在这些字段。若“看 Bot 配置”不应自动授予历史业务内容，或源资源撤权要求同时收回历史内容，本路径是绕过当前源资源权限的读取面，需要单独修复；若产品把已存 Bot 结果定义为独立可共享记录，则必须有明确授权契约。本次没有把这一策略判断冒充已完成动态验证的越权漏洞。

2. 错误字符串传播路径存在：动作目录收集 `str(exc)[:180]`（962–976）并放入 action_guidance（997–1019），运行失败时保存 `str(exc)[:4000]`（1624）；工作流和工具错误也进入返回引用。`main.py:213–220` 对 AppError 透传 message/extra，普通非业务异常的公开 HTTP 响应则被 234–240 统一隐藏。可确认“内部异常文本进入历史或指导引用”，但本次未生成任何真实外部故障，未证实实际凭据、SQL 参数或内部地址外泄；需要受控哨兵字符串的隔离测试后确定具体脱敏边界。

3. 公共 Key 以 Bot 所有者执行是代码明确契约（1747–1748、1779），不是以发 Key 的用户执行。创建者停用或失去 bots.publish 不自动撤销其已签发 Key；Key 仍受自身 status/expires_at、Bot 状态/API 开关和 Bot 所有者状态控制。该主体选择没有被本次当作未证明的越权问题；但运维撤权文档不能把“撤 Key 创建者权限”误写成“撤销 Key”。

4. 分阶段新 AuthContext 不代表中途撤权一定立即生效：同 Session 中的 MySQL REPEATABLE READ 快照、identity map、已取出的 Python references 都可能保留之前读取的状态。当前代码在 1601 提交已组装引用后才生成模型回答，没有最终源授权再验；Key 和 Bot API 开关也仅在公开入口核对一次。本报告仅讨论下一次新请求可观察到撤权提交后的门禁；未验证并发调用、在途 Key 吊销或生成期间撤权。若要求在途撤权后的最后响应不得含内容，需要另行定义并使用新事务/会话进行重验，不能仅宣称创建新 ctx 即可。

5. 知识分块独立 SQL 路径在 615–629 检查文档、知识库、File 工作区及可用状态，但没有像本轮 knowledge.service 的新查询那样显式要求 Document.knowledge_base_id == Chunk.knowledge_base_id。正常写入是否可能形成不一致关系未在本次范围验证；只作为关系完整性补测点，不据此声称可通过公开接口跨库读取。

## 未发现问题的边界

- Key 使用 `token_urlsafe(32)`，持久化 SHA-256 摘要；普通视图只返回前缀，创建响应一次性给明文。审计只记前缀，未看到明文 Key 写入日志/审计的本模块路径。
- 公开入口按 bot_id、prefix、active 候选及 constant-time digest 比对，检查到期；随后检查 Bot 与 Key 的 workspace 归属、published 状态、API 开关和当前正式修订。未找到仅更换 bot_id 就复用其他 Bot Key 的路径。
- revoke_api_key 检查当前工作区及 Bot 归属、版本；remove_bot 将 Bot 删除与 active Key 撤销放在同次提交中。
- 正式运行配置来自 BotRevision.snapshot。当前源资源权限/状态仍由知识、图谱、Skill 和工作流/连接器执行路径约束；正常授权不是通过对 ES 或模型返回值的信任产生。知识源 File 例外只在 knowledge 动作阶段启用。
- graph/workflow 固定历史修订不回退到草稿，源资源停用/删除拒绝；本次未追查工作流节点或第三方 provider 的内部执行权限，也不将其纳入全面验证声明。

## 下一批最低测试范围（未执行）

使用隔离临时数据库、内存模型/传输桩，禁止连接业务库/真实模型。至少覆盖：BOTS-N01 的正式版服务 scope；BOTS-N02 的 bots.execute 单项撤权；Key 错 Bot/跨工作区/过期/显式撤销/删除 Bot 后失效；Bot 所有者停用或退组；绑定源资源显式授权撤销；BOTS-N03 新发布与显式下线区别；BOTS-N04 执行和查看范围交叉；历史引用当前权限策略；错误哨兵不应泄漏；root 提出的草稿解绑后删除仍被正式 Bot 使用的图谱。发生失败时检查真实调用计数和原始资源结果均没有被当作成功引用返回。

## 读取证据 SHA-256

原始文件字节保留原换行；片段摘要使用 `read_bytes().splitlines(keepends=True)` 后按一基行号闭区间拼接，再计算 SHA-256。附录的文件级摘要用于定位版本，不表示依赖文件都已全文审查；只有 bots.py 标记全文。部分公共依赖沿用本会话前一窄审已经读过的相同片段，并以本次 HEAD 无差异及摘要复核。


| 文件（工作树相对路径） | 覆盖 | 全文 SHA-256 |
|---|---|---|
| `backend/app/modules/knowledge_graph/bots.py` | 全文 1–1792 | `c4e957ad9b1c37bb37a22bbaf92e47916c1a7a54e2203fbfdc9378ca8b476e10` |
| `backend/app/modules/knowledge_graph/router.py` | 片段 1–37、429–443、486–496 | `27415ee2fd823982a3b49a3c2f41de4592d6ae4a0b4f760ea57a4c118f6c5413` |
| `backend/app/modules/knowledge_graph/schemas.py` | 片段 206–239 | `ade7955b17e24c6ebc0e043a396d691e55cee0d97adfa555f37347ac7d01cd53` |
| `backend/app/modules/knowledge_graph/models.py` | 片段 202–268 | `929a1dcec503210afc54724dd02a09565e9e219c41ea0d692941964d0c82f089` |
| `backend/app/modules/knowledge_graph/graphs.py` | 片段 56–61 | `e195b5d187b4d4952a79d791a7ab4d3a2624518e7469a51229465131a3497d7e` |
| `backend/app/common/deps.py` | 片段 53–65、79–109、289–328 | `232d683120a0416762076d84d11560f7b9f09c0d9061091a7c4b3111539ef27d` |
| `backend/app/common/authorization.py` | 片段 67–133、180–217、257–283、314–348 | `a8dd477faf94e8b4471bd405984fa34120d42a5c321781e48f15c65a7312c190` |
| `backend/app/common/data_scope.py` | 片段 39–82、85–186 | `910673325e7033998390973077c45b178846aab2470a9be571f35c39ef2c1703` |
| `backend/app/common/owned_records.py` | 片段 55–113 | `373205d9fec10ba3a5f7a730649eff4668572f863bd51f045efdb38d52bf473a` |
| `backend/app/modules/business_ai/context.py` | 片段 1–52 | `4faf7d87a5cb9ba534f9698e89cd86ca0aba40393f5afcd378c158555c4d9513` |
| `backend/app/providers/tool/gateway.py` | 片段 101–130、236–262、472–509 | `f48bbb7f591d68e9510dd1938477e2586aed41f26a2b34c287286de788659219` |
| `backend/app/modules/runtime/service.py` | 片段 78–104 | `e074939540b43a93e0ca25c26b0ad1dbc2a9b472ce6854fbceae3dff5a9f6637` |
| `backend/app/main.py` | 片段 213–240 | `9d920b5d6b5fc3c485512566c11be6be6ded92635200204e8cdac39ace32d357` |

| 文件 | 片段行号（闭区间） | 片段 SHA-256 |
|---|---|---|
| `backend/app/modules/knowledge_graph/bots.py` | 1–299 | `007c2e77aa24d80f6053747f8c03f71b1c2da026d347416f924801b64241dba0` |
| `backend/app/modules/knowledge_graph/bots.py` | 300–620 | `dd9825c828632c0b5c9f7d7282018d05cf53b3962fccef42035675d499b784d4` |
| `backend/app/modules/knowledge_graph/bots.py` | 621–805 | `e074cbbaa6e468ceab4d9afa3f44adbc272058befa8d4d08696876ec1615e002` |
| `backend/app/modules/knowledge_graph/bots.py` | 806–930 | `5507cf7cfef989d54a91296e932dfe2cdcf20c3a4cd4d952f39686defa87ab00` |
| `backend/app/modules/knowledge_graph/bots.py` | 931–1250 | `260682105a000ebb1707ed3a5d47d51eab8c4a472108baf2c8d642a55946e5ee` |
| `backend/app/modules/knowledge_graph/bots.py` | 1251–1488 | `30df89c117174d355dab5d920eeab4dc5dd16946baa601113858bfedb518cb8d` |
| `backend/app/modules/knowledge_graph/bots.py` | 1489–1792 | `16751f1418e45873ac561f5d6fc9988e8446428fbf7cd315cab17c8016cbf9b7` |
| `backend/app/modules/knowledge_graph/router.py` | 1–37 | `23d9c9d7696ae63b1b6af651e22909ff4b57b830fd0a299a890de6f462a19cdf` |
| `backend/app/modules/knowledge_graph/router.py` | 429–443 | `f95f52d13aa4838697f0d3572a9110e70f1bd9965b3ecd416da470502552e281` |
| `backend/app/modules/knowledge_graph/router.py` | 486–496 | `d3a24764f523cd5150350bd701fe0f3c3b8b378d4f8729498ddbdaedead9bc89` |
| `backend/app/modules/knowledge_graph/schemas.py` | 206–239 | `904b498e380e71f3fadb238b4fbde19d60f008e3bb5d0fd8b12117e37108eecd` |
| `backend/app/modules/knowledge_graph/models.py` | 202–268 | `32120f4abe618b775056758e45d62bd52e1233a8cf20e6dbae79d5e83740e694` |
| `backend/app/modules/knowledge_graph/graphs.py` | 56–61 | `471f9590d05bc00aceb30fca0a8499d2ccbae18c6b7d6131e02f96febe3e73f7` |
| `backend/app/common/deps.py` | 53–65 | `2bcf784b45caf16e1aa78f759006f8ca1e057a59bb66308a099db05a8447b07c` |
| `backend/app/common/deps.py` | 79–109 | `0373fb0d5719282e3b10ebeb1b8b735e7c75e71b6ec01da0ba3541515a6f8a98` |
| `backend/app/common/deps.py` | 289–328 | `5a9c39034f75eb6c79d3cffcd4bd4086b02ef511fb6bde977148eec537927c3d` |
| `backend/app/common/authorization.py` | 67–133 | `2cf6f50c36fae4279c7c9f73c70549795dc0e88f02d3a3f4cff029bf31add231` |
| `backend/app/common/authorization.py` | 180–217 | `7c5304c46e2a4c7902891bcdbd30a26a74653d059bde6753ff614adee7c1eeca` |
| `backend/app/common/authorization.py` | 257–283 | `0313ee27bf4455ce735acd8274743c2d72ef4042759f248e9780f4c03bc22cc2` |
| `backend/app/common/authorization.py` | 314–348 | `dcee2d5f7e0b20f198e79a6290ed1135b55eb724352a5b49a6d403621314b906` |
| `backend/app/common/data_scope.py` | 39–82 | `770467f66f0f6193919b916242564b379f456e3168fc097eafd6f0c22e59afd0` |
| `backend/app/common/data_scope.py` | 85–186 | `d3115c2ec3494f480c6cbfa4b576a354ac34d4d89acc87f238a944bf625ec720` |
| `backend/app/common/owned_records.py` | 55–113 | `59442d8325ae683a1754cf889b3c18c953d0a4084e25eafeb1013c679e4374ba` |
| `backend/app/modules/business_ai/context.py` | 1–52 | `4faf7d87a5cb9ba534f9698e89cd86ca0aba40393f5afcd378c158555c4d9513` |
| `backend/app/providers/tool/gateway.py` | 101–130 | `2491ba8237fc2ef48205ffb0450c5a9b52d9218b0222cc1fc521eb5b072fda13` |
| `backend/app/providers/tool/gateway.py` | 236–262 | `8aa705c4885ac019a6134cc822e32ceb2c79b1210cfc95d9b0dad88f7c3db486` |
| `backend/app/providers/tool/gateway.py` | 472–509 | `56c70d7438d3f35c6bf90b1e9ace5429bece92c9c3d2a4c7a601fcd174a4092f` |
| `backend/app/modules/runtime/service.py` | 78–104 | `456a5240f707eb3d2834adf33ae9e92a4899f0be129b5f85d1763c9a47b428da` |
| `backend/app/main.py` | 213–240 | `04a7b95ec5114a8c8339de2e97bf7c228f370933ad9303106cfdd1492152006b` |
