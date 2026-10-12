# 批次十六开发记录：同任务未知回执、自然续办与数据源停止保护

2026-10-04，工作树 major-delivery-acceptance。基线源码7922089，期间只快进十五最终文档7c7174f；这是开发专项记录，尚未统一门禁/浏览器验收或推送。

## 当前变化

- “重试刚才失败的任务”“继续，把剩下的做完”保留原业务task、draft和交付清单；咨询、否定及显式新任务不据此继承。
- 从可信平台记录读取同工作区、发起人、会话、task的未知回执。更换Run/call_id/参数不能重发同工具未知HTTP POST/MCP操作；保存原幂等键和回执，不执行远端核实或自动重试。
- 其他独立工具可以继续。新独立task仍沿用既有执行合同；本保护不宣称跨task、不同工具别名或并发首次派发的全局精确一次。
- 即使本轮没有再次调用旧工具，也不能用新completed掩盖同task旧未知结果。旧版无provider且原工具下线时，可信legacy_unstaged未知终态仍保留。
- 跨Run阻断只带安全状态及原调用/运行标识，不复制旧data/artifacts或旧错误正文。内部读取绕开展示域ORM过滤，但保留三重归属条件。
- tools.py另接查询代理的saved_result=previous.result_json，用于持久查询回放的当前来源核验；该接线与query_authority/gateway实现共同验收，不单独归功本专项。

## 实际红绿

1. 原产品首个真实Worker反例失败：换Run继续后再次发送未知HTTP请求，受控provider计数从1变2。-x停止，未调用真实业务接口。
2. 初次实现误用了不存在的ToolCall.created_at，10项失败；保留after.xml/log，修为实际started_at后10项全过。不能把这一阶段说成产品原始10个问题。
3. 扩展自然续办、异工作区/用户/会话task碰撞、缺任务标识边界，18新专项加17既有终态合同，共35项通过。
4. 再补旧版无provider、工具下线的未知回执，先取得1项失败，再修同task终态计数。
5. 最终19新专项加17既有合同，36项全部通过，40.12秒。所有测试使用临时SQLite和可计数内存provider，无业务库、真实模型、浏览器或8011连接。重复组合不累加为独立用例数。

## 仍未关闭的边界

- 本批没有实现逐份修订、内容修改证明、queue edit合同epoch及跨Run精确一次，原plan-delivery-revisions-batch16.md仍需继续。
- 数据源停止保护现已取得原始红并修复，见后续追加；没有增加自动恢复、远端SQL终止或跨数据库原子事务。独立新task测试只验证既有产品边界，不代表实际数据库异常后可未经核实恢复测试。
- _task_calls可读取同task原发送事实，但最终检查与外部动作不是原子事务；不追杀已发远端操作、不自动判断远端是否执行，持久存储恶意篡改不在信任模型内。
- 无真实MySQL/PostgreSQL并发验证；根代理代码仍待独立通读与整批门禁。以下指纹是本次开发快照，tools.py由根代理负责共享接线，整批冻结时需再次核对。

## 源码与证据指纹

| 文件 | SHA256 |
| --- | --- |
| `backend/app/business_worker/dispatch_state.py` | `24cb8fd2c209d797fca14ce7c5cf1544190696d9ac652e3988fbd9b68497742e` |
| `backend/app/business_worker/replay_guard.py` | `b2fd558b88b61c47aba8356c2d5bcfd9e6c7fa1b1b21f73663d24c6c5aef00eb` |
| `backend/app/business_worker/tools.py` | `935305b396c2683d210937cb38cc67937e4c9e7a2108f9f2c7578a1c70015cf5` |
| `backend/app/business_worker/delivery_contract.py` | `ef95db92e30637c44f66dae84b9b4ec1760ed6645f6fd539055b2e3c3703d1eb` |
| `tests/backend/test_dsh_task_operation_guard.py` | `4d7ff25a1d8db45bb1cfe085c7dffe310dbc5bec034466e62a25f510c0fded76` |
| `.implementation/task-operation-before.xml` | `ffc1360dca7c923dafa8f1cf1724fbe04d4ac9d638fb004141326781e515e8dc` |
| `.implementation/task-operation-before.log` | `696045424b6558793e29b50dcbfef41800d2fa21c1992e6d7d377966a644b94f` |
| `.implementation/task-operation-after.xml` | `5327317f06c39abf38fc93f919f35ef7911e56cee12675cc75e2b01e5621a1e9` |
| `.implementation/task-operation-after.log` | `16821761cdf7b1de8c94ac12f3f4d74d2d523363ef31985912efede63f960b26` |
| `.implementation/task-operation-after2.xml` | `9bb3b10388bf2c64c1cf555246aa44ec42f7743d25e7ba54cd7b66c9413d0d6f` |
| `.implementation/task-operation-expanded.xml` | `c0385c3c184143487dae486d466b1bf75e5900650fb3ff3e0f3ce813b414bb4b` |
| `.implementation/task-legacy-before.xml` | `7524455f4e22ede8482c9f45e710e65f584e2ed610a2befd848cae752e233805` |
| `.implementation/task-legacy-before.log` | `dbc856c1429f590cf0f151b496f9bb5d2843c24b37c57cd83ef0ad74db1f0fc4` |
| `.implementation/task-operation-final.xml` | `f1013a907cab7ed910ee4b84372c0ac93babf60eac979fc541ed2a23a6e5f2d5` |
| `.implementation/task-operation-final.log` | `bfc263c5ec0443720984250003d57986fc54482e19e4c9e286abda18290e8f71` |

## 追加：同任务的数据源异常停止不能被新一轮绕开

- 原实现仅在当前Run检查database_access_halted。真实Worker受控provider第一次生成连接异常后，同task“继续”新Run再次进入执行器；原始反例1项失败，-x立即停，`.implementation/task-datasource-halt-before.xml/log`保留。没有建立业务数据库连接。
- `_task_run_criteria`统一持久task及工作区、发起人、会话边界。每次数据源工具派发前，从同一可信任务各Run查询已经提交的停止事实。新Run事先排队、故障稍后发生也受保护；不靠创建Run时复制一次标志。
- 无须runs.view展示权限；不读取/返回旧业务结果。数据源动态工具和内建分析工具受同一保护，本地独立工具仍可执行。
- 新增10项：3种续办语句×是否预先排队=6，独立新任务既有边界1，异工作区/用户/会话3。与19项未知操作专项及17项既有完成合同合跑，最终46项全部通过、50.44秒；重复运行不累加。
- 46项均使用临时SQLite与可计数的内存provider；没有真实MySQL、业务SQL、真实模型、浏览器或8011连接。
- 已提交停止事实与派发之间仍非跨进程原子事务。本保护不终止已运行SQL，不证明MySQL服务端取消，也没有核实恢复按钮；原查询应先核实再由明确的新任务继续。
- 上述最终代码已由另一代理独立全文复核四个模块及两个新增测试；未发现确定新增归属或旧结果泄露问题，读取范围、指纹和静态并发边界另存 `review-task-lifecycle-batch16.md`。这是只读审查，不额外累计测试通过数。

### 当前源码与新增证据 SHA256

旧表保留作为前一步开发快照；以下更新为本次46项验证的源码。

| 文件 | SHA256 |
| --- | --- |
| `backend/app/business_worker/dispatch_state.py` | `fed7f1636fe9f654145f67e91873d95884d26050746313cfad35868d9026c1ae` |
| `backend/app/business_worker/replay_guard.py` | `b2fd558b88b61c47aba8356c2d5bcfd9e6c7fa1b1b21f73663d24c6c5aef00eb` |
| `backend/app/business_worker/tools.py` | `b17a0959f9fa1c815976653cfa3d040522f5427900c3df58b13deb9391c518fa` |
| `backend/app/business_worker/delivery_contract.py` | `ef95db92e30637c44f66dae84b9b4ec1760ed6645f6fd539055b2e3c3703d1eb` |
| `tests/backend/test_dsh_task_operation_guard.py` | `4d7ff25a1d8db45bb1cfe085c7dffe310dbc5bec034466e62a25f510c0fded76` |
| `tests/backend/test_dsh_task_datasource_halt.py` | `c7ae19f340d87401edff0b5f9dc318cfe8c3c30ea43ca752fd08e4a7b2f86830` |
| `.implementation/task-datasource-halt-before.xml` | `ee66624a4d5b12d755006685b2c6fc6ad20d5054d86e85bd546e817ce6592e3a` |
| `.implementation/task-datasource-halt-before.log` | `7e1f154cd4e2e6b6468e656da7f0bd35f7739e4ba540c46671d47abd6d5f5eb5` |
| `.implementation/task-lifecycle-batch16-final.xml` | `6389fa30794c11126ccfaa95b21f1ec712bf9b8196a5d6b657ca737f76df8df6` |
| `.implementation/task-lifecycle-batch16-final.log` | `772700332c797891fa30323703e2fbd372c9f2a9a18d9f12cb2c17f723a3c959` |
