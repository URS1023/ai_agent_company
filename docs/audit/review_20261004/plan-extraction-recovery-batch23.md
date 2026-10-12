# B23 抽取硬中断恢复实施方案

基线 `a512776432f2001ca86d9dcbb215003da869a3ab`；独立树 `major-extraction-recovery`。只修改抽取恢复模块、抽取执行、Worker 启动钩子与窄测试。模型和 schema 均不变，沿用现有 JSON 与 version 乐观锁，不涉及 v33 的结构迁移。

原始行为红引用同基线已跟踪的 `reproduction-extraction-hard-interrupt-batch23.md` 和 `extraction-hard-interrupt-evidence-batch23.json`。r2 是真实 Worker 被 Windows Job 确认清零后的遗留 running；首轮 launcher PID 断言错误仅是夹具错误，不计产品红。原始 ignored 日志留在 B19 树，禁止修改。

## 身份与条件末段

- 新运行在首次 running 提交中把私有 executionOwner 写入 Run.input_snapshot：格式版本、随机代次、Windows 主机指纹、PID、内核进程创建时间。Job.result_summary 同时记录 runId。对外视图不输出 input_snapshot，不公开主机信息。
- Windows 指纹使用本机 MachineGuid 与计算机名的摘要；PID 只用于打开进程。GetProcessTimes 与 WaitForSingleObject(0) 区分仍存活的同一进程、已退出进程、PID 被复用。访问拒绝、机器身份不一致、元数据缺失或异常均未知。Linux 本轮保持未知，不实现未经实际验证的恢复。
- Worker initialize 在 bootstrap 后做最多 200 个 running 抽取运行的有界只读枚举，每个候选分别打开短事务，锁 Run 再锁 Job，重新验证归属、当前 runId、代次、双方状态和进程死亡。通过后同事务把二者改为 failed，附 interrupted 事件与审计，用户可明确重试；不自动重做抽取、外部副作用、知识入库、ES 或调度。
- 首轮枚举不持整批锁。超过扫描上限记录未检查提示；未知历史记录保留，不宣称旧无 owner 卡单已修复。多恢复者由数据库行锁与既有 version UPDATE 条件共同仲裁，冲突回滚，不重试。
- 执行末段先重读锁定 Run/Job 并验证代次，再写候选与 review。原失败 catch 改成独立 Session 的条件失败收尾，旧代次不得覆盖新运行，已提交 review/completed 不改失败。
- 提交 ACK 异常时只在独立 Session 看到同代次成功且当前认证/授权通过后返回该成功结果；找不到、无法核验或撤权均不编造成功、不重做执行。内部调用没有可重新验证的令牌时可保留已提交成功而继续抛原错误，不能降级其持久结果。

## 验证边界

使用 MAIN Python 与隔离入口、临时 SQLite、合成单条输入；网络和模型调用必须为零。不启动应用端口，不连接真实平台/业务数据库。

窄合同覆盖当前活进程、PID 复用、访问拒绝、旧 owner/外机/不支持平台、失败收尾代次、已提交 ACK 丢失与当前撤权、两个恢复者过期版本竞争。实际进程故障点覆盖首次 running 提交后、候选已 flush 未提交、最终 review 提交后及原 Worker 仍存活。沿用原探针 Windows Job 对 launcher 与真实 Python 的双重归属检查，每阶段 30 秒、退出等待 5 秒，故障不自动重试或扩大预算。

SQLite 不证明真实 MySQL/PG 锁调度；需保留方言生成的 FOR UPDATE 和 version CAS 断言，真实服务执行另验。未知 owner 与非 Windows 自动恢复、知识/ES/schedule、完整 Host/RPC 端到端均不在本批覆盖。

## 工作树建立记录

首次 worktree add 因 6 个既有长路径资产触发 Windows 文件名过长而失败，Git 自动撤除目录和注册，但分支已创建。确认目标目录不存在且无 worktree 注册后，以单条命令 `git -c core.longpaths=true worktree add` 成功创建。未改变 Git 全局或仓库配置；之后所有 Git 命令显式启用 core.longpaths。未写 B19/B20、MAIN 或其他树。
