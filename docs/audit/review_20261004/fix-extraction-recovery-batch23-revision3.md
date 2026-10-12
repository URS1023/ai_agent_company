# B23 revision3：移除抽取恢复枚举中的协议数值强制转换

工作树 `major-extraction-recovery`，分支 `codex/major-extraction-recovery`，本轮 HEAD 仍为 `a512776432f2001ca86d9dcbb215003da869a3ab`。未 merge、提交或推送；没有修改 root 复制的五份正式旧报告或 overview。r2 的六文件快照、37/4/74 三轮结果及原始证据保持独立。

## 确认的事实与证据边界

root 最终复核指出扫描条件 `owner["protocol"].as_integer() == 1` 可能先于 Python 的未知判定失败。离线探针直接捕获实际 `recover_interrupted` 创建的 SQLAlchemy 语句，没有复制查询作替身。原 r2 PostgreSQL 编译结果明确包含：

```sql
CAST(((kg_extraction_runs.input_snapshot -> 'executionOwner') ->> 'protocol') AS INTEGER) = 1
```

MySQL 同处编译为 `CAST(... AS SIGNED INTEGER)`。因此 PG 对异常协议文本进行整数转换的风险是有实际编译依据的静态结论；本轮没有执行任何 PostgreSQL/MySQL SQL，没有真实 PG 启动崩溃复现，也不新增完整 PG 平台支持承诺。前后编译探针均禁止 `Engine.connect`，连接尝试为 0，并阻止 dotenv 内容读取。

另一个临时 SQLite 对照同时保存 `protocol="abc"` 与正规整数 `1`。r2 正规行正常恢复，坏协议行被 SQL 预筛掉，结果为 examined=1、recovered=1、unknown=0。此红是新逐行分类合同的差异，不是 SQLite 整批崩溃，更不能拿这条计数断言宣称 r2 发生真实 PG 崩溃。

修后 SQL 仅按 running、Windows platform、本机 host 收窄，仍取最多 201 个 ID 并最多检查 200 条；没有引入 JSON 对象相等比较。现有 `owner_state` 保留 protocol 严格整数且必须等于 1 的判断，不做字符串或布尔兼容。声明同机归属的坏协议现在计入 examined/unknown，并占用本轮 200 条配额；超额仍明确 deferred，不扩大扫描或重试。

## 唯一产品改动与测试修正

- `backend/app/modules/knowledge_graph/extraction_recovery.py`：删除协议数值 SQL 筛选一处，更新相应中文说明。归属验证、当前代次、Run/Job 条件收尾、version CAS 与已成功结果保护均未改变。
- `tests/backend/test_extraction_recovery.py`：增加实际扫描查询 PG 编译合同，以及坏协议 `"abc"`、字符串 `"1"`、布尔 `True` 与正规协议共存的三项 SQLite 对照。只控制进程死亡观测；SQL 查询、协议判断、条件更新和审计均调用真实产品逻辑。

首轮全组到布尔夹具时停止：Python 将字典中的 `True` 与原值 `1` 比作相等，SQLAlchemy 未发出该 JSON 更新。随后独立诊断在提交后 refresh，明确读到类型 int、值 1；这不是产品协议判断错误。只给该测试赋值加 `flag_modified`，并保留提交后实际类型断言，未修改或放宽产品判断。

## 各轮原始结果

统一使用 MAIN `.venv_py312/Scripts/python.exe`、`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`、`GIT_AUTO_PUSH=0`，经本树隔离测试入口。单进程、首失败停止、外层 180 秒上限不变，无自动重试或扩时。每轮 `.log/.xml/.json` 单独保留，XML time 与 pytest 总耗时分别记录。

| 原始文件前缀（均位于 .implementation） | 实际结果 | pytest 耗时 | 外层耗时 | 分类 |
|---|---:|---:|---:|---|
| extraction-batch23-r3-pg-compile-red | 1 failed | 2.17 秒 | 13.328 秒 | 实际语句 PG 编译合同红，未执行 PG |
| extraction-batch23-r3-mixed-protocol-red | 1 failed | 4.16 秒 | 14.735 秒 | SQLite 未知协议分类合同红；正规行原本正常恢复 |
| extraction-batch23-r3-contract-final | 37 passed / 1 failed 后停止 | 33.36 秒 | 45.969 秒 | 布尔 JSON 夹具没有真实落库 |
| extraction-batch23-r3-bool-fixture-diagnostic | 1 failed | 4.32 秒 | 17.328 秒 | 实际回读证明库内仍是 int 1 |
| extraction-batch23-r3-contract-final-r2 | 41 passed | 38.95 秒 | 50.468 秒 | 当前最终恢复文件全组 |
| extraction-batch23-r3-process-final | 4 passed | 93.24 秒 | 105.093 秒 | 当前最终四个实际 Worker 进程场景 |

最终四进程样本原始目录：

- `extraction-batch23-process-started-05a614b8`：running commit 后硬杀，新 Worker 条件收尾；明确 edit/retry 生成 Run2、单份候选。
- `extraction-batch23-process-flushed-a3ac95b4`：候选 flush 后未 commit 硬杀；未提交材料回滚，恢复后明确重试。
- `extraction-batch23-process-committed-803b4ed5`：最终 commit 后硬杀；新 Worker 保持原 review 成功与单候选。
- `extraction-batch23-process-alive-f035de42`：原 Worker 活着时第二 Worker 不接管，释放原执行后正常完成。

全部实际 launcher/Python 已退出，所有 Windows Job 清理结果 active_after=0，网络/模型计数均为 0。每阶段 30 秒、退出 5 秒、单探针 110 秒不变；只使用本机临时文件 SQLite、受控单条合成输入。四场景是单独一次 pytest 运行，不与 41 项拼成同轮。r2 相邻 74 项未在本轮重跑，按 root 协调留给后续整合门禁。

## 冻结与原件保留

最终 41 项开跑至四进程结束，六文件均匹配 `extraction-batch23-freeze-revision3-final.json`。与 r2 相比只有以下两份字节变化：

| 文件 | r3 SHA-256 |
|---|---|
| backend/app/modules/knowledge_graph/extraction_recovery.py | `bf611bcf49a3b49b6b0aededd774b5823392957d746a631cc4321a429f7d1225` |
| tests/backend/test_extraction_recovery.py | `c5d029a2f8e36780a60a87386d9a505b68de7bab8f4e6a7e918bf14373855685` |

其余四份 server.py、extraction.py、进程测试及探针的 SHA 均沿用 r2。r2 与 r3 六文件原始字节分别存于 `extraction-batch23-source-revision2/` 和 `extraction-batch23-source-revision3/`；r2 测试原件由逆向移除本轮新增片段恢复后，逐字节 SHA 与原 freeze 匹配，未覆盖工作文件或旧证据。

本轮可审查差异 `extraction-batch23-revision3.patch`，SHA `c5a7a45ea2c5c9e727696da9d8fff8ea1fbd41bed042e4bc23a69396020bb7d5`。

最终六文件清单 `extraction-batch23-freeze-revision3-final.json`，SHA `5cd39e3e9e6b517772e5e07660d00cd60b190a0e9fa39e60b8539d231bbc50e7`。首次未完成全组的 `extraction-batch23-freeze-revision3.json` 另行保留，没有改写其测试 SHA。

全部源码、前后编译、逐轮原始日志/XML、四进程结果与清理 SHA 见 `extraction-recovery-evidence-batch23-revision3.json`，SHA `298306055587208dfde1f1a64fff3acd00d058bb1b7c52bffc8fc9bced01e06a`。纯文件记录器 `record-extraction-batch23-revision3.py` 不导入业务或连接数据库。

原 r2 证据 JSON SHA 仍为 `aafd24d5c4425972fc198d94c0baccb03d1a1ac9b8f8bd74f8f72ded57f9649a`；root 复制的五份正式旧报告逐字节匹配原 ignored 文件。未触碰 overview、MAIN、B19/B20/B21/B22 或部署服务；未执行真实数据库、模型、浏览器、8011、构建或统一门禁。`git -c core.longpaths=true diff --check` 通过。当前保持冻结，待 root 后续 FF 继承稳定基线与整合验收。
