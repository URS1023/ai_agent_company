# 第十六批：查询准备期来源与授权重验

日期：2026-10-04。执行树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-delivery-acceptance`，分支 `codex/major-delivery-acceptance`。起始产品基线 `79220893b1945ce766758d8b6b70c3e5ad669536`；期间由根代理快进至仅增加第十五批浏览器验收记录的 `7c7174f96a283c54da659d719b1632ff7e73eb7f`，本报告产品模块未被该提交改变。

本记录只证明以下查询链路的修复，不表示全部业务、全部缓存或全部数据库撤权问题已解决。测试未接触业务数据库、真实模型、外网或在用 8011/8012 服务。原红灯、夹具失败和复测均保留，未恢复任何百轮浏览器循环。

## 1. 两个首先复现的真实缺陷

1. `source-revalidation-before.xml/log`：通过正式 HTTP 自然语言查询入口，在受控模型等待点用第二个正式 PUT 请求将源字段收窄并取得版本 2 回执，原查询仍返回 HTTP 200、版本 1 及 `amount=100`。读执行器为 Mock；红例无需向已撤权的实际业务库发 SQL。修复后 HTTP 409，模型恰好一次，执行器零次；已建立的 QueryHistory 失败收口、租约释放。
2. `source-replay-before.xml/log`：真实 `business_worker.tools.execute` 持久回放，在正式 PUT 收窄字段后仍原样返回旧 ToolCall 内金额。执行器、provider dispatch 和密钥解析全部设为禁止调用。修复后拒绝回放，无新增查询/模型/密钥操作，原 ToolCall 与 QueryHistory 证据保持原样。

新增校验器自检另有 `source-replay-malformed-before.xml/log`：异常留存 result_json 为数组时，初版校验器出现 AttributeError。该失败不是原产品范围的第三个漏洞；已对新校验器补结构保护，并验证数组历史/错误列目录均明确拒绝回放且保留原证据。

## 2. 变更与具体调用点

- 新 `backend/app/modules/analytics/query_authority.py`：保存准备时的不可变源基线。比较源 ID、工作区、provider、凭据引用、owner、启用状态、完整非派生连接/授权配置，以及 source_version；来源结构单独在模型等待前保存摘要。`PROFILE_FIELDS` 中表数量、自动说明等画像更新不会无故使合法查询失败。同版本但权限字段或元数据变化也会拒绝。
- `QueryAuthority.authorized_source` 每次使用独立短平台 Session，重新读取用户启用状态、工作区/组织生命周期、成员、角色权限、显式资源授权和客户端当前范围。保留原调用 permission，不把 database 工具权限改成 analytics.execute；不刷新/提交调用者待保存的 ORM 对象，不复用其策略或 Session 范围缓存。摘要和底层连接配置不会进入用户错误。
- 服务客户端有原令牌摘要，或 Run actor 保存了 client_token_id 时，复核原令牌存续和当前客户端范围，并取原请求范围与当前范围交集。没有原令牌身份的旧上下文只复核客户端启用状态和原范围上限，不能声明已验证特定令牌。
- `queries.execute_query` 在取得来源后建立基线并初验；`_execute_query_with_source` 在元数据准备后绑定结构、完成 SQL 授权/租约核验后再次检查，然后传递 `before_dispatch`。公开入口始终建立检查器；私有函数和底层执行器的可选参数保留现有内部兼容合同。
- `execution.execute_read` 在打开关系型连接/ES 客户端前，以及 `exec_driver_sql` / `POST /_sql?format=json` 派发前调用检查器。连接已借出后发生收窄会在真正业务 SQL 前拒绝，并经已有上下文管理器归还；不会为了通过而换源、重规划、重发 SQL 或延长单查询时限。
- `database_provider._query` 使用同一个基线与派发检查器，保留 datasources.execute、原连接器权限、只读 SQL、200 行上限及治理计量。已发布工具额外记住 connector/version/tool-version 绑定，在短事务内按当前工具治理和发布绑定复核。新成功回执增加 `data.queryId`，与已存在 QueryHistory.id 相同，以便不重查业务库地核验持久回放。
- `dsh_query_execution.execute_dsh_query` 的实际注册入口是 `query_business_data(mode=sql)`。进入准备阶段建立来源/结构基线；SQL 范围校验后，同轮缓存返回使用新权限上下文与 `SnapshotAccess.query_result` 独立复核，同时遵守 query_business_data 停用开关。未缓存路径将同一基线传给 execute_query，不在两个阶段间接受新的来源基线。
- `ToolGateway.authorize_replay` 增加可选 saved_result。数据库查询回放使用当前执行权限、源资源权限及当前工具合同；有 queryId 时必须匹配当前工作区、执行用户、数据源、成功 QueryHistory、规范 SQL 和实际行/列投影，再按当前表字段范围检查。新数据库工具和原分析工具两种回执形态均支持；不额外要求 analytics.view/runs.view，不改写原证据。
- 根代理持有的共享集成文件 `business_worker/tools.py` 将 `previous.result_json` 传入上述可选参数。本代理未编辑其未知副作用/任务续办逻辑，最终共享文件指纹由根代理统一冻结。

## 3. 验证记录

统一命令前缀为主树 Python：

```powershell
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py <本组文件或用例> -x -q --disable-warnings --junitxml=.implementation/<记录名>.xml
```

工作目录始终是本独立树。每组串行执行，首失败即停，未扩大超时预算。平台使用临时文件 SQLite 的真实独立事务；合法正例业务 SQL 仅针对测试夹具创建的受管临时 SQLite。ES 为受控假客户端，无外发请求；模型为固定桩。隔离 runner 禁止真实 MySQL/Postgres 驱动及外部主机、在用服务端口。

| 证据名（均位于 `.implementation/`，XML 与同名 LOG） | 结果 | pytest XML 秒数 | 说明 |
|---|---:|---:|---|
| source-revalidation-before | 1 失败 | 4.711 | 源配置准备期变化的原始产品红例 |
| source-revalidation-after-1 | 1 通过 | 3.988 | 同一 HTTP 红例转绿 |
| source-revalidation-after-2 | 10 通过 | 36.437 | HTTP/数据库工具/DSH 来源变化、停用/删除、角色撤权与不变正例 |
| source-replay-before | 1 失败 | 5.191 | Worker 持久回放撤列泄露原红例 |
| source-replay-after-1 | 1 通过 | 3.435 | 根代理接线后原回放红例转绿 |
| source-revalidation-boundaries-1 | 10 通过 | 24.712 | 7 项新 ES/客户端/事务边界 + 3 项既有 database provider 正反例 |
| source-revalidation-final-1 | 7 通过、1 失败 | 27.138 | 失败属于新测试误用资源授权 API camelCase；正确合同为 snake_case，目标业务断言尚未执行 |
| source-revalidation-final-2 | 6 通过、1 失败 | 22.715 | 前述字段修正后正式撤共享权通过；失败属于新测试给 owner 设置不存在的用户而触发真实 FK；改用已存在的另一用户，未放松 FK |
| source-revalidation-final-3 | 15 通过 | 44.833 | 全部查询证明篡改/他人记录/缺证明拒绝及合法数据库回执正例；含原回放例再次通过 |
| source-replay-malformed-before | 1 失败 | 4.500 | 新增校验器自检，异常留存结构穿透 AttributeError；随后补明确拒绝 |
| source-revalidation-adjacent-control | 33 通过 | 33.419 | 既有 test_query_lifecycle_governance / test_analytics_task_control / test_dsh_query_cancellation；保留取消身份、租约释放、失败计量与连接归还 |
| source-revalidation-adjacent-replay | 24 通过 | 45.442 | 全部 9 项新回放例 + 15 项既有 test_tool_replay_authorization，覆盖新增结构保护 |
| source-revalidation-adjacent-dsh | 47 通过 | 23.402 | 既有 test_dsh_query_execution 全文件；透明 SQL、原始业务范围、多步骤与缓存合同保持 |

四个新增测试文件原有 **27 个唯一用例** 已在 final-1/2/3 三个首失败停止的小组中全部通过；结构保护追加 2 项后，新用例总数为 **29**，回放文件全部 9 项已在 adjacent-replay 通过。不能把重复复测次数相加当作更多覆盖。其余相邻回归及最终状态见本报告最后的冻结补充。

新增用例覆盖：正式字段收窄、源停用/删除、正式角色撤权、正式显式源授权撤回、借出连接后派发阻断与归还、ES 打开客户端后的零 SQL POST、客户端范围收窄/停用、同版本范围/结构变化、派生画像更新允许、调用者脏数据与 Session 范围不受修改、调用者非默认执行权限保持、DSH 同轮新/旧查询拒绝、缓存不重查、准备中工具停用、Worker 撤列拒绝且保全旧证据、他人 queryId/错 ID/行/SQL/列混搭/缺证明拒绝，以及真实 database.query 回执到历史的精确关联。

## 4. 明确边界

1. 这是平台当前状态的最终检查点，不是平台授权表与业务数据库之间的分布式原子事务。最后一次检查之后、实际服务器接收 SQL 之前仍有竞态窗口；检查后才提交的撤权不能追杀已派发 SQL。没有宣称客户端关闭等于数据库服务器 SQL 已停止。
2. 临时文件 SQLite 正式双请求证明已提交变更不会被旧 ORM/权限缓存遮住；独立 Session 的 MySQL RR 行为与真实驱动仍需授权环境验证。这里没有运行 MySQL/Postgres/真实 ES 测试，也不以 SQLite 测试替代它们。读事务关闭后不跨检查复用权限缓存。
3. `source_metadata` inspector、语义索引/预备采样不是本批逐语句动态重验范围；保留前批 CAS 写回防旧配置覆盖的合同。schema/profile、后台元数据/非 DSH 其他缓存路径也不属于本批新回放保证。
4. 持久回放缺 queryId 的老回执，仅为**没有表列限制**的绑定源保留既有合同；一旦有 allowedTables/allowedColumns 则拒绝猜测来源。HTTP 源回放仍用其原资源授权合同。纯 schema/profile 回放仍按旧资源/工具授权检查，不能宣称做了本批查询投影验证。
5. 原客户端 scopes 不会扩大；无原令牌标识的旧上下文无法验证某一个历史服务令牌。人机会话注销不是本批新增的长任务撤销机制，用户/成员/角色/源授权与 TaskControl 取消按各自合同检查。
6. source.secret_id、连接配置、版本纳入基线；同一 secret_id 下凭据内容独立轮换的管理合同未在本批新增专项，不作完整密钥生命周期保证。
7. 同 task 新 Run 的 database_access_halted 继承与未知操作恢复由根代理另行处理；本报告不把静态待复现线索计作已修复。

## 5. 建议独立复核点

- 比对源基线中剔除的 PROFILE_FIELDS 仅为派生画像，不能藏入可执行授权配置；检查同版本直接修改和正式版本更新两条反例。
- 检查 fresh Session 是否保持调用者原 permission、角色/资源范围与客户端上限，以及持久回放不被新增历史查看权限阻断。
- 检查实际 SQL 与 ES 派发前回调位置，权限错误、ChatCancelled/ChatTaskTimeout 是否保持异常身份，原租约失败计量/释放是否没有变化。
- 检查 Worker 集成只传留存结果做只读复核，没有为补来源证据重发查询或覆盖历史。
- 最终统一门禁需纳入四个新测试文件；本报告的局部绿灯不代替根代理的统一门禁或浏览器验收。

## 6. 本代理冻结文件 SHA256

以下为本代理持有的 6 个产品文件、4 个新增测试文件；共享 `business_worker/tools.py` 由根代理仍在处理其他 B16 事项，本表不对该文件单独承诺冻结。

| 路径（相对本独立树） | SHA256 |
|---|---|
| backend/app/modules/analytics/query_authority.py | 433D32C95C6F0A3F0172A1B9459101BE83E51048CA611F642F24082992BA8A4F |
| backend/app/modules/analytics/queries.py | 488B5FF6B1B3CF1895D44190A0038A38686F318C93E87FD99C28E7F3D194FC81 |
| backend/app/modules/analytics/execution.py | 19C6DC358895337D9D4219A86DCE6A2F3AFD3DE8A0BE986AFD603B1510363706 |
| backend/app/modules/analytics/dsh_query_execution.py | 3F3E4EC4D37F5F2C9829B6AB288A05BFC8BFB6B299F25E93BED8DDA9B1F35A11 |
| backend/app/providers/tool/database_provider.py | 3F497C290902F2DFCFFD8D2023CE8B9D47ED6D39E2B4CA0E7FBCF9DE104C949D |
| backend/app/providers/tool/gateway.py | 25F71461AE224895AE1DC28CF028CF2DF78EF707E38D36D236C2490B7433EE8A |
| tests/backend/test_query_source_revalidation.py | FA77BEB827C491FDDCB418747F83E396EE499068DB786EFCBF20D6CB2869BAAA |
| tests/backend/test_query_source_revalidation_dsh.py | A5E79660BDE848A5C7ECA0A8FEDDD45CDDE98A3CF862AD310BB366765C6E6529 |
| tests/backend/test_query_source_revalidation_replay.py | 66344FE61572FDA9D8BEDB55E51179EDD7FC28E1CC8C2610A6A0C3AF17B3505E |
| tests/backend/test_query_source_revalidation_boundaries.py | 1263B65F0AFE3566C321C39775A89DDBE60AD036CD4ED75EEF470ED5D5BCE06E |

## 7. 最终状态

上述 6 个产品文件和 4 个新增测试文件已冻结。29 项新增专项均已通过；已执行的相邻既有用例为 98 个（33 取消/租约 + 15 回放 + 47 DSH + 3 database provider），全部通过。它们分组执行，不是一次 127 项整组门禁；取码顺序与两次夹具失败、一次新增校验器自检失败均如上保留。根代理已通读来源检查器及执行/回放接线，目前未报告新的确定问题。`git diff --check` 对本代理改动通过（只有既有 CRLF 转换提示，无空白错误）。未提交、推送、修改正式文档、构建前端或启动浏览器。最终统一门禁由根代理执行。
