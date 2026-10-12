# B17 知识入库硬中断恢复边界复核（只读）

日期：2026-10-04。目标工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-delivery-acceptance`。读取 HEAD：`7c7174f96a283c54da659d719b1632ff7e73eb7f`。

授权范围：只读源码与 B16 计划，仅写本 ignored 审阅文件。未修改产品、测试、构建、前端、正式 docs；未运行 Python、测试、服务、模型、数据库或网络。工作树内已有他人修改均保留。读取时 `.implementation/batch16-frozen-files.json` 的 34 个文件全部 SHA256 匹配（34/34）；这不是测试或产品验收。

## 1. 结论与 B16 计划的差异

`docs/audit/review_20261004/plan-knowledge-recovery-batch16.md` 的核心静态结论仍成立：知识入库没有持久执行 owner / attempt / generation，没有硬中断后的接管或原同步批次续跑入口。知识 service、router、governance、模型及本次读取的启动链 SHA 与该计划列出的源码 SHA 相同；B16 计划标注的是另一工作树及较早 HEAD，不能把该计划当成本树实施证明。

需要补准四处口径：

1. **当前正常 API 重试不会直接删除旧块或启动新 attempt。** 同 KB / 物理 File 的任意文档仍为 `processing` 时，导入和重建均在 `service.py:480–481` 抛冲突；它们在解析前停止。同步接口把此冲突按单文件失败计入一个新批次，并不修复旧文档。
2. **“删除文档后再导入”有损，不是恢复。** 删除接口不核验 `processing` 或执行 owner，直接删除旧分块、分段 metadata、ES 和文档行（`service.py:851–867,912–945`）。这本来是删除动作的含义，不能把它包装成安全恢复流程。
3. **较早执行覆盖新 attempt 尚未动态复现。** 缺少代际约束和无条件失败收尾是确定的源码事实；当前没有自动接管，不能仅凭这个缺口就报告“普通 409 重试已覆盖新任务”。提交结果不明或将来引入接管后的迟到收尾是单独的故障证明任务。正常 ES 清理异常被 `1049–1057` 吞为日志，不会因为 ES 普通错误自动落入外层失败分支。
4. **B16 的跨进程测试建议需要专用持久化夹具。** 当前 `tests/backend/conftest.py:17,49–56` 固定内存 SQLite，且公共 `client` 明确断言 `:memory:`。Windows 新进程也不会继承父进程 Python monkeypatch。仅在现有 `_setup/client/db` 上加 kill，无法证明重启后读同一数据库。

## 2. 哪些状态硬杀后卡住，哪些成果已经完成

以下是源码路径推导，全部尚未通过真实进程硬终止验证。硬杀后未提交 SQL 回滚、已提交 SQL 保留的推导仍需在本机临时磁盘 SQLite 实证。

| 精确窗口 | 持久状态与保留成果 | 现有入口之后会怎样 |
| --- | --- | --- |
| `service.py:508` 已提交首次导入的 `processing`，`517` 解析入口或 `628` embedding 尚未完成 | 文档 `processing`，首次导入尚无已提交新块；前态只在函数内存 `491–492` | 再导入或重建 409；启动不归档、不续做。不能仅按 `updated_at` 判断执行者已死。 |
| 重建同样越过 `508`，在解析、模型或最终提交前被杀 | 文档 `processing`；旧已提交块、旧 parser 与 metadata 保留，新的 parser/块仍属后续未提交事务 | 导入/重建 409；旧的 enabled 块仍可检索，因为 SQL 权威条件允许 `ready/processing`（`1124`）。停用块仍应被排除。 |
| `666–687` 已向 ES 写出至少一块，尚未完成 `706` | 新 SQL 块未提交，ES 可能已有孤儿候选；内存 `indexed_es_ids` 丢失 | 无自动清理清单。检索用 SQL ID 和正文复核（`1288–1304`）排除孤儿；不能按 File / KB 宽删 ES 来“恢复”。 |
| `706` 的 SQL 提交已确定成功，尚未清旧 ES / 返回 HTTP | 新文档 `ready` 与新块已完成，旧 SQL 块及旧 metadata 已原子删除；旧 ES 可能残留 | 在当前授权、源文件与配置校验仍通过的前提下，普通导入 `487–489` 复用 ready；显式 reindex 仍再向量化，现接口没有 operation key 判断是否重发。不得把缺 HTTP 回执当作 SQL 未完成。 |
| 普通异常 `709–724` 收尾正常完成 | 首次导入 `failed`；原 ready 重建恢复 `ready` 和旧 parser，保留旧 SQL 块 | 可按当前权限重试；这是现有普通异常恢复能力，不是硬杀恢复。`508` 在 try 外、`BaseException` 和硬杀不进该收尾；收尾自身失败也可能遗留 `processing`。 |
| `governance.py:698` 已提交 sync 的 `running`，尚未 `757` 完成终态提交 | 旧 run 永留 `running`；文档可能无开始、processing、已经 ready，计数只是最后一次成功提交的数值 | `/sync-runs` 只读；再次 `/sync` 创建新 run，重新查候选，不续旧 run。旧 processing 项在新 run 中记失败，其它项仍可能正常处理。 |
| 同步某文档 `service.py:706` 完成，`governance.py:739–740` 计数尚未提交 | 文档已经 ready，但旧 run 的 imported_count 未包含它 | 没有 run→file→attempt→result ledger，不能从计数或当前 ready 集合恢复原批次的精确完成集合。`reindex=True` 重开全批会重做已完成项。 |

同步 `713` 候选查询、`716–720` 已有文档查询位于逐文件 try 外；`747–757` 最终收尾也在其外。普通异常同样可能遗留 `running`，所以不能将全部残留批次归因于硬 kill。错误摘要在局部列表 `errors`，直到最终提交才保存；硬杀也可能留下 failed_count 已增加而 error_summary 仍为空的记录。

## 3. 重试、旧成果与较新执行之间的实际边界

### 已确定的源码行为

- 正常重建在 `512–515` 读取旧块，随后仅在新块全部准备好后于 `705–706` 同一 SQL 提交中删除旧块并切换新块。普通模型异常触发 rollback，不能把这条路径描述成“先清空旧 SQL 再重试”。
- 成功重建会删除旧块对应的 `KnowledgeChunkMetadata`（`937–940`），包括 enabled、QA、source、updated_by 信息；旧块正文也可能经过人工编辑。恢复如果未经明确重建意图就重放成功重建，会抹掉这些人工成果。新建 sync 的 `reindex=True` 对已 ready 项也走重建（`governance.py:721–731`）。
- `remove_document` 没有执行期互斥或 processing 检查，且 `_delete_document_rows` 在 SQL commit 前调用旧 ES 删除（`925`）。删除后重导既会丢旧成果，也不能证明仍存活的旧线程已退出。这是恢复流程不得借用该接口的原因，不主张改变正常删除动作的产品语义。
- 文档、分块与 SyncRun 模型没有执行 owner、attempt、generation 或固定候选清单；Document 也没有可用于最终写入比较的版本号（`models.py:288–322`）。`service.py:712–723` 仅按 document_id 读回行，再按内存 previous_status/parser 写入，没有状态、generation 或结果归属条件。

### 确定缺口，具体故障仍待复现

1. **最终提交结果不明。** 如果 `706` 在服务端完成、调用端收到异常，当前分支会 rollback 后删除 `indexed_es_ids`（`711`），即可能删掉已经成为当前成功成果的 ES；首次导入还会把 doc 写为 failed（`714`）。旧 SQL 已经切换，rollback 不会撤销已完成提交。代码没有读回稳定 attempt 来先确认成功。必须使用“实际 commit 已完成 + 确认丢失”的故障夹具；单纯在 commit 前 raise 只证明未提交失败，不能冒充该边界。
2. **旧收尾覆盖新执行。** 在上面的确认丢失路径，若另一请求在 ready 窗口启动同一 document 的重建并提交 processing，旧请求随后执行 `712–723`，没有代际条件阻止它重写状态/parser。未来若仅把陈旧 processing 改成 failed 后放行，也会暴露同类迟到收尾。当前静态检查只确认无防护，未确认实际数据库时序、缓存刷新或锁行为下的最终结果；不把这个场景归入已证实的普通 API 重试缺陷。
3. **最终 SQL 已完成后的显式 reindex 重发。** 请求体只有分块参数，不能区分回执丢失重发和用户有意再次重建。重发会再次产生新块并删除上一次的 metadata；这是现有明确重建语义与幂等合同不足的交界，不能声称现接口恰好一次。

最低修复验收口径应是：显式重试前确认执行权、前态与本次结果归属；失败收尾和清理不得命中较新结果；最终 SQL 已成功时不把它回退为失败。此处不扩展为新队列架构，也未实现控制记录。

## 4. 启动与操作接口不能安全辨认旧 owner

| 已读取入口 | 能证明什么 | 不能证明什么 |
| --- | --- | --- |
| Worker `initialize:31–39`、`activate:41–49`、`_start_maintenance:51–91` | 建结构、种子、业务路由，启动连接池维护与调度器 | 不扫描知识 processing / sync running；调度器 lease 不属于知识同步 HTTP 请求。 |
| Worker `shutdown:93–101` | 设置 stop、请求 context.cancel_all、join 后台维护线程、关闭读连接池 | 没有登记并 join 每个知识 API 执行线程；`stopped=True` 不是知识 owner 已释放证明。 |
| `api.dispatch` 与 `application.dispatch:108–148` | 当前 Worker 同步分发 ASGI / 原 HTTP 认证路由 | 没有持久知识任务 ID、owner registry 或续跑 RPC。聊天恢复/取消方法名称不能推导为知识任务恢复能力。 |
| Node Worker `exit:33`、`close:94–103` | 单个子进程 exit 会拒绝 pending RPC；close 等 1.5 秒后可能 kill | kill 后不再等待 exit；close 返回不能证明进程结束，单个进程结束更不能证明全部共享库写入者停止。 |
| 兼容 `main.py:100–190`、`seed.py:175–201` | 启动/关闭与种子维护 | 没有知识旧执行者辨认、升级静止点或限定范围迁移。 |
| 导入、reindex、sync、sync-runs、DELETE 文档 | 当前资源校验与相应业务动作 | 没有 takeover/recover/cancel 知识 attempt 合同；时间戳、actor_user_id、document_id 不等于可验证的活跃进程所有权。 |

因此，新协议首次取得内核锁也不能自动接管旧版 processing：旧版根本没有持该知识锁。需要由部署侧证明所有可能写共享库的旧执行者已退出，再限定范围处理旧记录；未取得此证据时保持旧成果，不调用模型。仅“重启过一个 Worker”“PID 不存在”“等待够久”都不足以由当前源码证明全体旧 owner 静止。

## 5. B17 最小真实故障测试路径（全部未执行）

### 先解决测试夹具，不改现有内存库合同

建议新增一个专项测试文件及子进程入口；不复用公共 `client/db` 建持久库。父测试可经项目规定的 `tests/run_isolated_backend.py` 执行，但每个 Windows spawn 子进程必须先调用该入口的 `configure_isolation()`，随后在导入 `app.config/app.db` 之前，把 DATABASE_URL 明确设置为父进程创建的 tmp_path 内绝对磁盘 SQLite 路径，并设置临时 STORAGE_ROOT / LOCAL_STORAGE_ROOT / LOG_DIR / HARNESS_HOME_ROOT、禁用 scheduler。父进程公共 app.db 即使已导入，也不得承担子进程持久库的断言；断言在全新子进程 / Session 读同一路径。

每个子进程重新安装文本/视觉/embedding/ES 本地桩；不能依赖父 monkeypatch。使用 `business_worker.application.create_application()` 的 TestClient 和真实登录/认证 HTTP，不开监听端口，不 override `get_current_context`，不调用在用服务。一次 setup 建结构与种子；重开进程沿原库读取，不 drop_all。ES 桩用同一临时目录中逐条 flush 的日志记录 chunk ID、写入与删除，模型桩记录调用数；不使用真实 ES/模型。

预算：三个独立小场景，每场景最多 1 KB、2 个短文本文件、每文档最多 3 块、单知识写请求并发、一次指定硬终止；单屏障 10 秒、单场景 30 秒、总计 90 秒，无自动重试。超时或子进程未退出立即停止该场景及后续场景，保留日志、确认只针对本测试 child 的退出，不扩长超时。记录每进程实际 SQLite 路径、连接/语句次数、API 次数、模型桩次数、已发出的终止和 exit 结果。全程不连接 szh_new 或其它业务库，也不读取部署连接串。

### K1：首次 processing 已持久化后硬杀

1. setup 上传单个短文本并创建 KB，保存已提交 file_id；子进程以真实 `/api/knowledge/documents` 调入库。在 `extract_text` 的入口桩发屏障并阻塞；`service.py:508` 此时应已返回。父进程只在收到屏障后终止该 child，并等待真实 exit。
2. 新进程独立 Session 读取：同 document 为 processing、新块为零、原 file_id 不变。再普通导入和 reindex 应各 409；模型桩调用数仍为零。可用一次仅含该 file_id 的 sync 确认其新 run 为 partial / failed_count=1，而原 processing 不变。
3. 这证明当前缺陷的真实硬杀红例。将来的修复不能直接把这种无 owner 历史行视作新协议孤儿：分别测“有合法协议 owner、退出已证明可接管”和“旧版无 owner、静止点未知必须拒绝”，不得用手工 SQL 改成 failed 伪造通过。

### K2：重建 ES 候选已经写出，最终 SQL 尚未提交时硬杀

1. 单文件先成功导入 2–3 块（一次 embedding）；经合法分段入口设一个 enabled=False，另一个记录手工正文/QA，保存全部旧 Chunk ID、正文、location、metadata、parser、ES ID。
2. 显式 reindex；第一条候选 ES 桩写入并 flush 日志后屏障，父进程硬杀。新进程逐字段验证旧 SQL / metadata 不变、无已提交候选 SQL、doc processing，日志确实含新候选 ES（本场景第二次 embedding）。
3. 通过真实搜索路由及受控 ES 返回旧候选与孤儿候选，验证启用旧块仍可检索、停用旧块与新孤儿不可返回；再重建 409，零额外模型调用。修复绿例再增加：接管后一次 embedding 故障仍保留旧集与 parser；最后显式成功重建才切换。不得把修复绿例预算或运行状态混入本次三个红例。

### K3：新 SQL 已提交，旧 sync 计数尚未确认时硬杀

1. 建两个有确定创建顺序的 ready 文档，快照各自旧块。以明确 file_ids 调一次 `reindex=True` sync。第一个文档的 `_delete_chunks_from_es(previous_es_ids)` 入口设屏障；此时 `service.py:706` 已返回而 `governance.py:739` 尚未运行。父进程硬杀并确认退出。
2. 新进程验证第一个文档 ready 且块已切换、第二个文档仍为原块、旧 sync running 且 imported_count=0；第一项旧 ES 可以残留。把模型桩改成任何调用即失败，再普通导入第一项，应复用 document_id 且零模型调用。
3. 当前没有按 run_id 续跑的操作接口，不应伪造“恢复成功”。B17 最小修复只需在可证明退出后把旧 run 归档为 interrupted 并保留最后确认计数；若另外承诺原批次精确续跑，须增加逐项结果关联及其独立验收，不能根据当前 ready 数量推算。

上述三项直接证明持久状态窗口，仍不证明 MySQL FOR UPDATE / RR、真实网络提交确认丢失、多主机互斥或外部 ES 清理效果。较早收尾与较新 attempt 的交错、commit 已完成但确认丢失应列为后续专项，不能用常规模型 raise 或 SQLite 红例宣称已覆盖。

## 6. 实际读取范围与内容快照

SHA256 是整文件标识，不代表整文件已逐行通读。范围外仅有明示的关键字检索。启动无恢复结论限于下列实际链路；没有执行全仓动态审计。读取时 source SHA 均稳定，与 B16 计划同名源码记录一致；测试夹具为本次补读。

| 文件 | 本次实际阅读范围 | 整文件 SHA256 |
| --- | --- | --- |
| AGENTS.md | 全文 1–54 | 68CA458E59A041CF7C43EEA367AA3F93F976E1DCDED7DACA643619A6BF47CF2A |
| docs/audit/review_20261004/plan-knowledge-recovery-batch16.md | 全文 1–185 | 699FB8B9AF2321FBAF21FB703A924B335239B108F0D4F8D054BADA4AA1FE67E0 |
| backend/app/modules/knowledge/service.py | 426–744、850–979、983–1057、1115–1158、1273–1324；processing/recovery/attempt 全文件检索 | 65A4985B242E18EA8C576B32FFA4E12CB9CF4C4B6DF04DF151660BEA8580F7F2 |
| backend/app/modules/knowledge/router.py | 1–38、185–228、530–582、685–780；全部路由关键词检索 | BB91C122F25E067DF8041A21EC7164C5D57536B53BE67818DCD6FBC349381557 |
| backend/app/modules/knowledge/governance.py | 628–785；processing/recovery/attempt 检索 | C76BAA8A7A9AA83D15ED8194184D7A7329978A609EF938A70538D2A9A48DD45C |
| backend/app/modules/knowledge/models.py | 全文 1–104 | 2930DA2A4B19A69E237A671025842E78E81C82E6D0D8600192580CE7B6FE2E9C |
| backend/app/models.py | 261–333 | DCE44685054D4EFC2713B88362CB553F63C9E34AD9EFB4EFAB16222F74620340 |
| backend/app/business_worker/application.py | 全文 1–148 | 189FA665417DB3B701BF52A8F912573CDE5D0839817C66A3A9E0022AC02EEB4F |
| backend/app/business_worker/server.py | 全文 1–124 | F2D389753C4DA2534B078A59CB2FC239661316B9DC920034330356E50BE4022A |
| services/dsh_host/worker.mjs | 1–40、80–105 | 35830BC723575836EAE151FB54153D8B7F7A785D7042D89436F4591A85D87201 |
| backend/app/main.py | 93–195；启动/恢复关键词检索 | 9D920B5D6B5FC3C485512566C11BE6BE6DED92635200204E8CDAC39ACE32D357 |
| backend/app/seed.py | 175–205；bootstrap/knowledge/recover 检索 | 12E67F9E5E22917D8C46FB30319B9262E938219E855A1B5DDBEE3C2D38247C8E |
| backend/app/db.py | 全文 1–89 | 3020883D529CE5B65625C29C756395D5E2EE71045C79505BFA1912BAFC4E3973 |
| tests/backend/test_knowledge_recovery.py | 全文 1–99，只读未运行 | AF6283452C4006B217611859426F1E1A04984E36813FA2E892BC80694F32AFE2 |
| tests/backend/conftest.py | 全文 1–81，只读未运行 | 91841932FA664D081357164145A4DFFC74FD89C869C3638690A14F298D583274 |
| tests/run_isolated_backend.py | 全文 1–105，只读未运行 | CE3F744FC0C948E973C1D4CE367C6FB207FD962F52F70D36EE2C32AD6F268DBB |

现有 `test_knowledge_recovery.py` 只涉及 ready 复用、普通 embedding 故障保留旧块、failed 同步重试、陈旧 ES 过滤；没有硬 kill、跨进程持久化、owner/generation 或同步 ledger。此记录未声称任何新增恢复已经实现或任何测试已经通过。
