# B23 抽取硬中断恢复：实现、独立红例与最终冻结

工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-extraction-recovery`。
分支：`codex/major-extraction-recovery`；起点及当前 HEAD：`a512776432f2001ca86d9dcbb215003da869a3ab`。未提交、未推送，执行入口设置 `GIT_AUTO_PUSH=0`。

本批结果：带可信本机 Windows owner 的新抽取运行，Worker 硬退出后能在新 Worker 初始化时有条件收尾；明确编辑与重试可继续。旧 Worker 仍活时不接管，最终事务已提交时保留成功候选。当前源码分别通过 37 项专项、4 个真实进程场景、74 项相邻抽取合同；三次运行分别记账，不称同轮 115 项，也不计入完整产品门禁。

## 原始基线与独立审查

原始真实行为红沿用已跟踪 `docs/audit/review_20261004/reproduction-extraction-hard-interrupt-batch23.md` 和 `extraction-hard-interrupt-evidence-batch23.json`。root 原探针 r2 在真实 running commit 后，用 Windows Job 终止 launcher 与实际 Python，active 2→0；新 Worker ready 后 Job/Run 仍 running，实际 retry/edit 都返回 ConflictError。28.640 秒，无模型、网络、Host/RPC 或浏览器参与。首轮 14.828 秒在 launcher PID 假设上失败，只是夹具错误，不算产品红。

纯文件记录器逐一复核 B19 ignored 原始脚本、结果与日志 SHA，原件未改。亦核对 B19 复现时的 extraction.py、models.py、server.py 原始摘要，并与本树起点 Git 内容在换行归一后逐字节相同；原红不是拿不同产品基线推断。原始报告的 source_head 字段仍保留 cfe11ac，不改写成 a512。

两份只读独立审查保留原版本：

- `review-extraction-recovery-design-batch23.md`，SHA `b9b0902d52044d50a9de73e3bec4747f52773f7289f05c69b137df652173e7e8`。指出首次 running commit 位于 try 外，以及历史 Run 物化会覆盖新 running Job。
- `review-extraction-recovery-implementation-batch23.md`，SHA `6d7b584ec8e7b1a1cf77e9259a740adaf4d802e835de4d17a64340fd097d3a8b`。指出首版 owner 数字格式异常和跨代次 ACK 回读；二者均在首版上保留独立红，再修成 revision2。

## 最小产品改动及原子边界

1. `extraction_recovery.py` 在既有 Run.input_snapshot 中保存私有 executionOwner：格式版本、随机 generation、Windows 机器指纹、PID、内核创建时间。Job.result_summary 同事务保存当前 runId。没有新增或修改模型、列、schema 版本或迁移，未绕过 v33 合同。
2. Windows 机器指纹为只读 MachineGuid 与本机计算机名摘要。OpenProcess/GetProcessTimes/WaitForSingleObject(0) 共同区分同一活进程、已退出与 PID 复用，不靠 PID 或年龄清理。身份读取失败、无权限、不同机器、缺失/损坏 owner、Linux 均保留未知。机器身份属于部署操作系统身份，修改机器身份后不会自动认领旧记录。
3. initialize 在 bootstrap 后，仅枚举本机新格式 owner 的 running 抽取运行，最多 200 条；每条独立短事务，Run→Job 锁定并强制重新查询，验证 runId/代次/双方状态。确定死亡后，Job/Run failed、interrupted 事件与 actor=None 系统审计同事务提交。既有 version UPDATE CAS 抵御两个恢复者的过期更新。无自动重试、候选重新生成或外部副作用重放。
4. 执行末段在添加候选前重新锁定并检查归属；候选、review、任务摘要和审计仍一次提交。首次 running commit 也纳入 try；初次提交前失败保留原 draft，初次提交后 ACK 异常按实际持久同代次 running 条件收尾，不留下 owner 活着但计算未开始的卡单。
5. 原异常收尾改为独立 Session：只允许同当前运行仍 running 时改 failed，已 review/completed 不降级。成功 ACK 丢失时以另一个新 Session 重走实际认证、当前权限/资源范围与持久结果查询；没有可重新验证的内部令牌、撤权、未知提交或当前 Job 已切换代次时不返回假成功，也不重做业务。令牌心跳发生在独立认证事务中，不混入候选原子末段。
6. 历史合法 Run 仍可复核和物化；只有 Job.result_summary.runId 匹配该 Run 时，才修改 Job 汇总/状态，并由既有 version CAS 保护。图谱内容、固定本体和复核算法未改。已有相邻锁序断言保持通过。
7. revision2 将进程创建时间限定为 ASCII 规范 uint64 字符串，先限制长度再转换；generation 必须为 32 位小写十六进制，protocol 必须是严格整数。损坏记录归 unknown，不能由 Unicode 数字或超长字符串异常阻止 Worker ready。成功回读还要求当前 Job.runId/status 与原 Run 相符，禁止跨代次响应。

完整 owner 不写 events、summary、resultSummary 或审计 metadata；现有 run_view 不输出 input_snapshot。对外只见运行状态、计数和中断说明。

## 每轮验证与失败分类

所有 Python 均用 MAIN `.venv_py312/Scripts/python.exe`，设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，经本树 `tests/run_isolated_backend.py`。每次单进程 pytest、`--maxfail=1`、外层上限 180 秒。没有安装依赖，没有读取 MAIN 配置；本树三个 dotenv 候选路径均不存在。每轮原始 `.log/.xml/.json` 独立保留。

| 独立轮次 | 实际结果 | pytest 文本耗时 | 外层耗时 | 说明 |
|---|---:|---:|---:|---|
| history-red | 1 failed | 2.96 秒 | 17.781 秒 | 本体夹具未声明默认 value 属性，实际 422；不计产品红 |
| history-red-r2 | 1 failed | 2.70 秒 | 12.703 秒 | 原产品真实历史物化成功后，新 running Job 被改 completed |
| contract-first | 25 passed | 20.08 秒 | 30.125 秒 | 首版产品合同，不代替 revision2 |
| process-started-first | 1 passed | 24.78 秒 | 33.156 秒 | 首版真实 running commit 后硬杀；单独保留源码映射 |
| owner-format-red | 1 failed | 1.36 秒 | 12.813 秒 | 首版新增恢复代码的 Unicode created 引发 ValueError |
| cross-generation-red | 1 failed | 2.92 秒 | 12.093 秒 | 首版真实 Run2 已开始时，Run1 丢 ACK 恢复返回混合 200 |
| contract-final | 37 passed | 31.29 秒 | 41.125 秒 | revision2 全专项，包含上述补充与旧/外机/损坏 owner、上限 |
| process-final | 4 passed | 79.25 秒 | 89.484 秒 | revision2 四个真实独立进程场景 |
| adjacent-first | 74 passed | 100.25 秒 | 108.718 秒 | revision2 三个既有相邻文件；未改旧测试语义 |

XML 原始 time 与 pytest 总耗时不是同一计时口径，证据 JSON 两者分别保留。首轮失败均停止目标组，诊断后只修明确夹具或代码再运行；无预算超时、数据库断连、自动重试或超时扩张。

相邻范围仅为 `test_extraction_materialization_contract.py`、`test_extraction_review_contract.py`、`test_extraction_source_authority.py`。按 root 协调未重复跑 `test_knowledge_graph.py`，未跑统一门禁、浏览器、构建或在用服务。

## 四个真实进程样本

正式进程证据为以下四个目录；旧首版 started 目录 `extraction-batch23-process-started-29f5bde3` 保持独立，不拼入最终四场景。

- `extraction-batch23-process-started-7270ca9e`：running 已 commit 后硬杀。新 Worker 把原 Job/Run 收尾 failed，原候选 0；真实 edit 后显式 retry 只新增 Run2，review、候选 1。
- `extraction-batch23-process-flushed-2ecca787`：候选及 review 已真实 flush，但最终 commit 尚未执行时硬杀。原未提交材料回滚，新 Worker 条件收尾后候选 0；人工重试 Run2 后候选 1。
- `extraction-batch23-process-committed-be7f33a8`：最终真实 commit 成功、返回前硬杀。新 Worker 保持 Run1/Job review、候选 1，不自动新建运行、不假失败。
- `extraction-batch23-process-alive-f42f2d1a`：原 Worker 活着并停在屏障，第二 Worker initialize 后原 Job/Run 仍 running；释放原屏障后原 Run1 正常 review。没有接管或重复执行。

每阶段仍为 30 秒，退出等待 5 秒，子探针上限 110 秒。父探针登记 launcher 与实际 Python，逐一核对 Windows Job 归属；死亡场景须先 active_after=0 且旧 launcher wait 完成，再启动新 Worker。所有场景最终进程均退出、各 Job active_after=0，旧屏障与新 Worker 的 network/model 计数均为 0。模型桩只会拒绝任何调用，socket connect/connect_ex 全拒绝；数据是临时文件 SQLite 内单条合成输入。

两个恢复者测试属于临时文件 SQLite 上的真实双连接/双线程旧版本竞争：双方先读取相同版本后提交，一方恢复、另一方 SQLAlchemy StaleDataError 回滚，实际 SQL UPDATE WHERE 含 version 条件，最终只有一次系统审计。迟到写入测试通过受控代次撤销检查真实数据库条件；这两个层次不能冒称另一个真实 Worker 硬杀。

## 最终源码冻结

`extraction-batch23-freeze-revision2.json` 的六文件 SHA 从最终 37 项开始、四进程和 74 相邻结束后均相同；`git -c core.longpaths=true diff --check` 通过。Git 提示将来 checkout 的 LF/CRLF 转换，不代表此次字节被改写。

| 文件 | SHA-256 |
|---|---|
| backend/app/business_worker/server.py | `3faf2454cc3b72a4012b10b92fb1332c73fe4bddac061cc0832733bcd85a2dec` |
| backend/app/modules/knowledge_graph/extraction.py | `6c13cd1ca678537acb3d295ee3330e07bce246790146b2493c8399c08c283a93` |
| backend/app/modules/knowledge_graph/extraction_recovery.py | `ce0b3d933661f52fd48535806c5bfccd5a22f8964e7f81257369c16c9e3afaab` |
| tests/backend/test_extraction_recovery.py | `a8aa048e758044743e5f49cfc901866620d476403202c1eb6d4d5041d49a382f` |
| tests/backend/test_extraction_recovery_process.py | `a70c91cfc4105ac53218db92119c15c75e1f2b12347da4ffe470b2bcf5bca5ea` |
| tests/dsh/extraction_recovery_probe.py | `61642f4833612f05c2ffa689f81c18d7588b6c673e444a59507700ba42bdf41c` |

逐轮源码对应、原始红绿日志、XML、真实进程结果及日志 SHA：`extraction-recovery-evidence-batch23.json`，SHA `aafd24d5c4425972fc198d94c0baccb03d1a1ac9b8f8bd74f8f72ded57f9649a`。纯文件记录器 `record-extraction-batch23.py` 不导入业务代码或连接数据库。

## 明确限制与部署状态

- 只自动恢复本批之后保存可信 owner 的本机 Windows 新运行。旧无 owner、外机、身份读取失败/访问拒绝、Linux 不自动接管；不宣称历史所有卡单已解决。unknown 计数只涵盖此次符合筛选的已扫描行，旧/外机行不纳入 examined。
- 单次启动最多检查 200 条符合身份范围的活动运行，超额有明确 deferred 与警告；未检查部分仍待后续有界核对，不能称全部已恢复。
- 没有实际 MySQL/PG 连接或锁/中断验证。SQLite 事务与版本 CAS、相邻方言编译断言不能替代生产数据库的锁等待、连接死亡检测与部署验证。
- 本批为抽取初次/最终事务与同 Job 运行归属恢复；不涵盖知识文档入库、Elasticsearch 补偿、同步计数、计划任务、完整 Host/RPC 丢包或浏览器全链路，也不宣称 exactly-once。
- 没有触碰 schema/models/B21迁移、B20成果代码、MAIN 或其他工作树；没有更新在用服务或启动 8011。源码尚未提交、部署或整合统一门禁。

工作树建立时首次 add 因六个既有长路径资产失败，Git 自动撤销目录/注册但保留分支；确认后以单次 `-c core.longpaths=true` 成功建立。该事件、正式设计和限定范围详见 `plan-extraction-recovery-batch23.md`；未修改 Git 全局配置或其他工作树。此后所有 Git 命令显式启用 longpaths。
