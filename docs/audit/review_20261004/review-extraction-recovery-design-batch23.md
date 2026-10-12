# B23 抽取恢复方案独立静态复核

本次由 root 分派，仅只读核对方案及实现起点；不接管实现，不修改产品、测试或门禁，不运行代码、测试、浏览器、数据库或模型，不读取 `.env`。唯一新增文件为本 ignored 报告。以下是起点源码与方案的静态结论，不是新实现验收，也不重复既有硬杀 r2 行为红。

读取基线：`a512776432f2001ca86d9dcbb215003da869a3ab`，工作树 `major-extraction-recovery`。读取时 tracked 工作区干净。实现代理后续可能继续修改同树，下面的行号与 SHA 固定对应此次读取。

## 两个有具体触发序列的边界

### 1. 历史 Run 物化可覆盖新 Run 的 Job 状态，破坏恢复条件

起点 `backend/app/modules/knowledge_graph/extraction.py:843–845` 读取指定历史 Run，并允许其处于 review/completed 时物化；`:972–974` 无条件把 Job.status 改为 completed，并把 Job.result_summary.runId 改为该历史 Run。`:828` 的历史复核也无条件合并旧 Run 的计数到当前 Job 汇总。

最小序列：

1. Run1 已生成候选并完成接受，处于 review。
2. 同 Job 启动 Run2，在首次 running 提交后阻塞。
3. 用户合法物化 Run1；现有代码把 Job 改成 completed，runId 退回 Run1。
4. 硬杀 Run2 owner；恢复器若按计划要求双方 running 且 Job.runId=Run2，将跳过真实死亡的 Run2。

这是现有历史操作与新增条件恢复的确定交互，不是对尚未写出的新恢复代码猜测。仅最终 CAS 不能补救，因为冲突发生前 Job 已被另一条合法路径改写。

最小处理建议：历史 Run 的复核/物化仍可合法完成，但只在 Job 当前 runId 匹配该 Run 时更新 Job 的统计与终态。若锁定 Job，保持一致的 Run→Job→Graph 锁顺序；不能只检查内存中的旧 runId。实现代理已回复拟采用这一约束，尚未由本复核执行验证。

必需测试：Run2 阻塞期间实际复核/物化 Run1，验证 Run1/图谱结果可提交，但 Job 保持 Run2 的 running/runId；随后结束 Run2 或确认其 owner 死亡后恢复，不得被历史操作挡住。

### 2. 首次 running 提交丢 ACK 时，owner 活着而任务无人执行

起点 `extraction.py:647` 的首次 `_commit` 在 `:649` 的 try 之外。若该 commit 已实际落库而调用方收到连接/ACK 异常，候选计算尚未开始，`:690–694` 的失败收尾也不会运行。进程继续存活时，新死亡探针正确返回 alive，因此任务仍会卡在 running、下一次执行仍被 `:617–618` 拒绝。

最小处理建议：首次持久化也必须进入条件收尾边界；提前保存标量 workspaceId/jobId/runId/generation，异常后先回滚/释放原会话，再用独立会话核实实际持久状态。同代次 running 且本执行已停止时允许写失败/中断；未持久化的 Run 不制造补偿记录；不能自动重做抽取。异常路径不要靠访问已失效 ORM 对象取得标识。

必需测试：首次 running 的真实 commit 完成后抛出模拟 ACK 异常，保持原 Worker 存活。验证无候选、无自动重跑、任务不再保持假 running、可显式重试，审计不多记一次启动。另加 commit 前异常负对照，证明没有幽灵 Run 或虚假恢复审计。

## 方案中必须保持的原子与授权约束

- **真实版本 CAS 已存在，但只对 ORM 行更新生效。** `modules/knowledge_graph/models.py:78、100` 的 Job/Run 均继承 OwnedRecord，最终来自 `modules/system/models.py:57–67` 的 version_id_col。`app/db.py:55` 明确 expire_on_commit=False、autoflush=False。因此重新 select 并不足以保证内存刷新，锁定重读需 populate_existing，且必须重新比较 workspace/job/runId/双方状态/完整 owner 代次。候选、Run、Job、事件、审计在同一事务；任一 version 冲突必须整笔 rollback，不能保留部分候选或成功审计。不能假定普通 Core/bulk UPDATE 自动带此 ORM 版本条件。
- **最终成功依据是持久 Run，不是仍在内存中的对象。** 起点 `extraction.py:688` 提交后，`:689` 的 job_view/run_view 仍会读取数据库统计，序列化失败同样会落到原失败 catch。新条件收尾须对 review/completed 不降级；独立回读成功时同样校验 runId、generation、workspace/job，当前 Job 已指向更晚运行时尤其不能返回拼接的两个不同时点状态。找不到或无法读取的结果保持不确定，不执行第二次抽取。
- **新会话不等于重新授权。** `common/authorization.py:86–88` 直接复用 ctx._access_policy；复制旧 AuthContext 或仅改 ctx.db 会沿用旧权限。ACK 后回读应重建身份上下文，包含令牌吊销、用户/工作区状态、成员角色和服务 scope，再做当前任务资源授权。内部调用无可重验令牌时，保留已持久成功并继续抛错是保守可行边界。
- **认证具有提交副作用。** `common/deps.py:195、219` 的 resolve_principal 可提交 token 心跳，`get_current_context:308–327` 经该路径构造授权；`business_worker/context.py:60–63` 提供 authenticate 包装。不得在候选写入或持有 Run/Job 修改的会话中插入这次认证，否则心跳提交可能提前提交业务。应在独立回读会话、取得业务锁/写入之前完成重验。
- **恢复是系统维护动作，审计仍受工作区隔离。** `common/audit.py:27–57` 仅 add、不 commit；`models.py:744–754` 允许 actor_user_id 为空。恢复审计用该行的 workspace_id，actor=None，不冒充原用户；与 Job/Run 中断标记同事务。双恢复者只有获胜的一笔留下中断事件/审计。`modules/audit/router.py:51–57` 会透出 metadata，`extraction.run_view:130–131` 透出 summary/events，`job_view:157` 透出 resultSummary；故完整 machine/PID/kernel 时间 owner 仅放 input_snapshot，不能顺手复制到这些公开字段或审计 metadata。
- **ready 必须在恢复边界之后。** `business_worker/server.py:31–39` 当前为 bootstrap→application→ready=True；恢复挂接在 bootstrap 后并完成有界处理后再 ready。数据库故障不能被吞掉后假报 ready；合法未知 owner 可保留并明确统计，而不是当 dead。

## Windows 身份判定最小合同

这里只核对方案，未调用系统探针或第三方 API。

1. 持久 owner 必须来自实际 Python Worker，不是 launcher/Node 父进程。PID、创建时间、主机指纹、version、generation 均校验类型和有效值；缺失/异常返回 unknown，不以当前进程信息补齐历史记录。
2. 打开同一个进程句柄后核对创建时间并检查零等待状态；PID 复用只在同主机、有效旧身份与不同内核创建时间的证据充分时判旧 owner 已死。访问拒绝、WAIT_FAILED、创建时间读取失败均 unknown，不能统称“进程不存在”。句柄无论分支如何必须关闭。
3. OS/machine 不匹配与非 Windows 都保持 unknown。完整 owner 必须在首次 running 同事务持久化；generation 一经运行固定，不在重试收尾或 Worker 初始化时重生。
4. 有界扫描200项需记录实际检查/恢复/未知/未检查数，不宣称全部 running 已处理。若前200项长期 unknown，之后的可恢复项可能在重复启动中仍未被检查，应保留这一扫描边界而不扩大“已全部恢复”的文案。

## 最小验证集合

除前述两个具体反例，计划中的以下项不能省略；本复核未执行任何一项。

- 真实硬杀在首次 running 提交后，以及候选已 flush 但尚未最终 commit 后；新 Worker 只标中断，不增加执行次数，不留未提交候选，不自动调用来源或模型。
- 原 Worker 仍活、新 Worker 启动不改 Run/Job；旧无 owner、外机、拒绝读取、不支持 OS 同样不改。
- 两个独立恢复会话都先读旧版本，再竞争提交；仅一笔成功，一条中断事件/审计，另一笔完整回滚。另有旧 generation 和错误 Job.runId 负对照。
- 最终 review 已真实提交后抛 ACK 或视图异常；Run/候选持久成功不降级。回读前分别撤销角色/令牌/资源授权，不能用旧 ctx 缓存返回结果。
- 同 Job 的历史 Run 操作不得改写当前运行汇总；不同工作区、不同 Job、错 runId 不能通过 recovery/failure 私有 helper 混写。
- 对外 run/job 视图及审计 metadata 不暴露 executionOwner；重新显式执行生成新的 runId/generation，旧 Run 中断证据保留。

SQLite 的 version 冲突证据不代表真实 MySQL/PG 锁调度已验证；仅可额外静态核对生成的锁定 SELECT。没有新增模型/schema 的方案可复用现有 JSON 与 version；本报告不宣称已证明实现正确或所有历史卡单都可恢复。

## 读取时 SHA-256

| 文件 | SHA-256 |
| --- | --- |
| backend/app/modules/knowledge_graph/extraction.py | afef0edf95ce71010cdfe3d7b2c87ce96d97ae11f9cc96529993d89c736c7664 |
| backend/app/modules/knowledge_graph/models.py | 929a1dcec503210afc54724dd02a09565e9e219c41ea0d692941964d0c82f089 |
| backend/app/modules/system/models.py | 0cd0eb58a1b6bdf665358bbf924f7dcae50f783b4442ccb2f311dcae164c9b3e |
| backend/app/business_worker/server.py | 57c9f70e40123e3b93ab88d0604109ac9123bc12711952fb191fb3f38a828af0 |
| backend/app/common/audit.py | 1c18f3b9034c0c0df421fb3c825b6c189757c64f01c70da7f63add84025fb67d |
| backend/app/common/deps.py | 232d683120a0416762076d84d11560f7b9f09c0d9061091a7c4b3111539ef27d |
| backend/app/common/authorization.py | 257554d9942acf38f241f70d674ea880bb7b7ec1a1b5578fef25e7ac95b05da8 |
| backend/app/db.py | 3020883d529ce5b65625c29c756395d5e2ee71045c79505bfa1912bafc4e3973 |
| .implementation/plan-extraction-recovery-batch23.md | 3594bc41506f7de275d2369047771b2ea16d575b966aa640f9efa4883ef1db3a |
