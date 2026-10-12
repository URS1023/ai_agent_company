# B18 自然续办任务身份：只读审查与最小修复设计

2026-10-04。工作树 `E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-recovery-continuation`，本次读取 HEAD `d94c8b61fd184285ff84e4d420175f14b1a17124`。工作树已有 B17 产品、测试及门禁修改，由其他代理负责。本轮只写本 ignored 报告；未修改产品、测试、共享文档或构建，未执行测试、数据库访问、模型请求、服务或浏览器，也未提交/推送。

## 结论与证据强度

“继续，使用已有结果制作大屏和Excel”失去 task 的直接原因是自然续办回指词表缺口，不是 halt/未知回执过滤器自身丢记录。B15/B16 已解决的长句仅覆盖“上一条、刚才、原任务、剩下的”等明确回指，没有覆盖“使用已有结果”。缺失识别使新 Run 创建新 task_id；后续正确按 task_id 隔离的回执恢复、未知操作保护及 halt 查询均不再关联原 Run。

已读取现存 `.implementation/b17-halt-saved-focused-01.log` 首部及末尾：真实 Worker 专项在 `test_same_task_continue_delivers_q_but_keeps_sql_schema_and_profile_stopped:179` 因两个 task_id 不等失败，首失败即停。原句来源由同轮 `.implementation/fix-halt-saved-delivery-batch17.md:38` 记录；日志首部没有打印请求原句，因此不能声称日志本身逐字记录原句。当前测试 178 行已改用“继续”，其成功不覆盖原始自然句。本次没有重跑任何一条。

另有两个从当前源码可直接推导、尚未实际运行的反例：

- “继续，另做一个新任务，使用刚才的结果”：第一分句只有“继续”，绕过 leading 否决；全文含“刚才”，仍可命中 continuation。以最近同范围未完成 task 为前提，会错误继承。
- “继续，另开一个新任务，把刚才的PPT改成Word”：即使仅在 is_continuation 中加入新任务否决，choose_task 的 modifying 分支仍可独立选回旧 task。

以上须在开发时先取得真实 Worker 红例；本报告不把静态推导计作已执行测试。

## 确切调用链

1. `delivery_contract.py:15-24`：先取第一个逗号/分号/句号/换行前的 leading；否定、咨询及新任务检测只检查 leading（19）。短句 fullmatch（21）接受“继续”，不接受带后续内容的原句。长句要求动作开头且全文至少一个回指词（23-24）。原句只有“已有结果”，不在白名单中，返回 False。
2. `tasks.choose_task:115-161`：显式 task_id、继续、修改、澄清选项分别选候选；本句无显式 task_id，不含修改匹配，也不是 A-H 回复，所以 131 行 candidates 为空，157-161 行生成全新 task_id/draft_id，并按新句重新编译交付清单。
3. `chat.prepare:348-368`：选择 task 后才找同 task 的上一 Run、继承缺省资源并重新 validate_resources。新 task 无对应 previous_task_run，不会走原任务资源继承。这不是新任务获得旧数据权限的依据；显式 query_id 的工具访问仍有自己的当前权限核验。
4. `dispatch_state._task_run_criteria:118-131`：限定 workspace/user/conversation，再纳入当前 Run、同 task 的 Run，以及受绑定保护的旧草稿原 Run。`task_datasource_halted:134-142`、`_task_calls:145-153`、`unconfirmed_operations:193-214` 共用这个真实任务关联。新 task 自然不扫描旧 task 的 halt/unknown；不要用扩大整个会话扫描范围补偿语言识别缺陷。
5. `tools.execute:125-135`：先检查同任务未知外部操作，再检查数据源 halt，最后才进入真实 gateway。修复应恢复正确身份，让既有保护生效，不复制布尔快照代替持久任务检查，也不重置 halt。
6. `chat.settle:517-530`：同任务 unresolved 会使 completed 收为 interrupted/tool_result_unconfirmed。原任务身份丢失会影响“本轮完全不重调旧工具”时的最终未知核验，因此不能只测试工具入口重发阻断。
7. `chat_artifacts/resume.py:106-110,133-160`：DSH 直接按持久 task_id、三重归属、资源集合、插件版本、前轮终态及成功回执筛选。此路径不调用旧 `is_continue_request` 的短句识别。旧引擎 15-24、73-92 的纯短句规则不应为本问题一并放宽。

## B15/B16 已有合同需要保留

- B15 `test_dsh_compound_contract:64-79`：长句回指保留 task、draft 和整个交付清单，“先生成剩余一份”不缩减原总数。
- B15 167-195：真实 PPT 草稿和已写页数可跨轮恢复；steer、排队续办编辑不抹掉结构约束。
- B15 `test_dsh_compound_boundaries:251-256`：后半句“不要继续查询数据”是执行限制，不能据此取消原任务归属。
- B15 contract 272-277 和 B16 operation guard 65-70：咨询、否定、明确新任务不自动继承。
- B16 operation guard 13-50：新 Run/call_id/参数不能重发同任务未知 HTTP/MCP；不再调用旧工具也不能用 completed 掩盖原未知事实。
- B16 datasource halt 35-51：前轮先失败或新轮提前排队两种时序，都要从持久同任务事实阻止后续取数；独立本地工具可用。
- `choose_task:137-147`：隐式续办只沿用最近同范围、尚未 completed 的任务；改选资源不应因为一句“使用已有结果”扩大继承。显式 task_id 有既有的查找/修改合同，不能顺带改写。
- `test_task_clarification_delivery:68-94`：只有上一轮真实提供过的选项且上一轮 partial 才继承；随意字母和已完成后的选项不得回捞旧任务。
- B15 boundaries 95-104：新任务可在独立授权后复用已有大屏；“使用旧成果”本身不等于所有情况都属于旧 task。

## 最小修复建议：有界扩展，身份保护保持集中

建议最多改两个产品模块：`delivery_contract.py` 的意图识别辅助及 `tasks.py` 的隐式候选门控；不改 dispatch_state、tools、resume、存储模型或 API。

1. 保留短句规则及原有动作开头约束。长句在原回指列表之外，只增加完整的复用短语：复用动作（使用/用/沿用/复用/基于）紧邻“已有/已保存/现有/之前”的结果或查询结果，允许中间有限的“的”。不要把裸“结果、数据、已”加入全文宽泛匹配，也不要取消开头动作要求。
2. 在不改变用户正文的前提下按命令分句识别明确另开/取消原任务。否决针对命令语义，如“另开一个新任务”“这次是独立任务”“不要继续原任务”；不把“新任务指南”之类标题文字、否定的新任务指令或“不要继续查询数据”当作另开任务。可先采用分句开头的有界语法，不必引入模型、泛化语义解析或新依赖。
3. 这个明确新任务的否决应作用于 `choose_task:131` 的**隐式** continuation/modifying 候选入口，防止修改词分支绕过；原始 A-H 澄清支路与显式 task_id 支路保持现有合同。只有布尔 is_continuation 被否决而 modifying 仍成立，不足以修复明确新任务边界。
4. 候选仍只看当前最近 Run，不扫描全会话寻找“某个未完成任务”；same_scope、状态检查、旧草稿迁移及 prepare 的资源再次授权不变。自然续办只能沿用同范围且未完成的既有任务，包括 partial/failed/interrupted 以及已覆盖的排队续办场景；实际门控仍核对持久 business_task.status，不能只看一句“继续”。成功 completed 任务的普通“继续”及带复用结果的自然句不因此新增继承。续办仍调用 update_contract 合并原清单和约束，不因为自然句只提大屏/Excel 而删除原 Word/PPT。
5. 若句子同时要求继续旧任务和另开新任务，当前一次发送只绑定一个 task，不能根据“继续”子串强行归旧；本批不自动执行两任务混合含义。明确另开指令至少不得隐式绑定旧 task；复合歧义的后续交互能力另列范围，不能靠宽正则声称解决。
6. 未知结果与 halt 是原任务的未解决事实。“使用已有结果”只表达静态交付意图，不是重新取数许可、外部操作重发许可或未知核实回执。无论新轮是否调用旧工具，都要保留原任务收口。

建议验收文本矩阵（以同主体、同会话、同资源、最近未完成 task 为前提）：

| 文本 | 预期与理由 |
| --- | --- |
| 继续，使用已有结果制作大屏和Excel | 原 task；本次原话回归 |
| 请继续，沿用已保存的查询结果导出Excel | 原 task；明确复用结果 |
| 接着，基于现有结果生成大屏 | 原 task；有动作开头及完整回指 |
| 继续，不要重新取数，只用已有结果生成Excel | 原 task；限制取数不取消任务 |
| 请继续完成上一条请求里尚未完成的产物，不要继续查询数据 | 原 task；保留 B15 回归 |
| 继续，把剩下的做完／重试刚才失败的任务 | 原 task；保留 B16 回归 |
| 使用已有结果制作一份独立的新报表 | 不隐式继承；复用数据与任务身份不同 |
| 如何继续使用已有结果制作大屏／能否继续使用已有结果 | 不继承；咨询 |
| 不要继续原任务，使用已有结果另做报表 | 不继承；否定原任务 |
| 继续，另做一个新任务，使用刚才的结果 | 不继承；明确另开优先于回指词 |
| 继续，另开一个新任务，把刚才的PPT改成Word | 不继承；modifying 不得绕过 |
| 继续原任务，不要新建任务，使用已有结果 | 原 task；“新建任务”在否定中不能误判另开 |
| 继续原任务，制作题为《新任务指南》的Word | 原 task；标题中的词不是命令 |
| 最近任务已 completed 后说“继续，使用已有结果制作大屏和Excel” | 不隐式继承成功 task；新增回指词不绕过状态门控 |
| 新独立任务已完成后只说“继续” | 不回捞更早旧 task；保留最近轮次边界 |

矩阵是下一批建议验收合同，未执行。超出有界句式、跨多轮指代、标题/引语嵌套以及冲突意图不能因此宣称已通用识别。

## 三组真实 Worker 原始红例设计（未写测试、未运行）

### R1：原句身份、完整交付合同与成功回执恢复

复用 `test_business_worker.worker_context/prepare`，模型仅固定配置，真实调用 chat.prepare/chat.settle。先建立包含大屏、Excel、PPT 页数及 Word 的未完成原任务，再原样发送“继续，使用已有结果制作大屏和Excel”，不传 task_id。第一断言必须是 task_id/draft_id 与原值相等，修改前应在这里得到原始红；不能像 B17 已有专项那样将文本改成“继续”后宣称覆盖原句。

通过后还断言 continuation_of_run_id 指向原 Run，原 required_deliveries/页数约束完整，缺省资源经原路径继承并重新授权。成功回执恢复可沿用 B17 `saved_delivery_case` 的已持久 Q/ToolCall 结构，最小版只使用临时平台 SQLite 持久夹具及内存结果，不连任何现用业务源；若扩展真实大屏/Excel交付，则保持实际 loader/save_report/exporter，检查 QueryHistory 和原 ToolCall 不变、真实静态报表数据和 XLSX 单元格匹配。此时新取数入口全部挂 fail-fast，不能靠重新查询生成结果掩盖丢失身份。

边界副例：改选资源不继承、已完成的最近任务不继承、不越过一个明确新主题回捞早期旧 task、原任务资源撤权后 prepare/实际工具按既有权限合同拒绝。

### R2：原句必须保留同任务 unknown 与 halt，两条有界支路

未知支路复用 `completion_provider`，保持真实 ToolGateway 与 `_before_tool_dispatch` 派发日志，只以可计数内存 HTTP/MCP provider 代替最后执行。原 Run 发一次返回 uncertain，settle 为 interrupted；第二 Run 发送原句，不注入 task_id。断言身份、原 idempotency key/ToolCall/安全原 Run 引用保持。用不同 call_id/参数请求原工具，provider 调用数必须仍为 1，回执为 tool_result_unconfirmed/replayBlocked，原 receipt/data/artifacts 不改写。另一个独立例在续办后完全不调用原工具，直接声称 completed，仍应 interrupted/tool_result_unconfirmed。两个 provider × 原句即可，不做重试循环。

halt 支路复用 `failing_source`，真实 Worker 将一次内存 connection_failure 保存为 Run halt；不创建真实业务连接。前轮先提交故障、续办提前排队两种顺序分别断言新 Run 沿用身份；随后 database/query_business_data 的 SQL/schema/profile 或等价明确取数入口被 database_access_halted 阻断，执行计数不增加，本地独立工具和已授权静态交付仍可用。原停止事实不复制/抹除，仍由 task_datasource_halted 从持久原 Run 取得。

R2 不允许测试先强改 task_id 或复制 halt 标志；那会绕过本次缺陷。真实执行器计数、持久 ToolCall/Run 及最终收口共同构成证据，不能只测正则返回值。

### R3：逗号后明确新任务、修改词旁路与否定限制的最小对照

使用同一真实 Worker 夹具，以 partial 原任务为前置。各例独立建立平台任务，不派发外部动作。参数化上述“继续，另做一个新任务，使用刚才的结果”及“继续，另开一个新任务，把刚才的PPT改成Word”，预期新 task/draft 且不合并原交付约束；当前源码预计在身份断言失败，需先保存真实红。

对照必须包含“继续原任务，不要继续查询数据”和“继续原任务，不要新建任务，使用已有结果”，预期仍同 task；防止用全文否定词/新任务子串粗暴拦截。再保留咨询、否定原任务、纯短句及 A-H 澄清既有回归。若新任务句与显式 task_id 同时传入，本批先保留既有 API 优先级，不在语言修复中静默改为另一任务；冲突策略应另外明确。

真实测试执行时仍应走现有 isolated backend harness，使用 MAIN Python、临时平台 SQLite、无真实模型、无现用业务源、无外网；首失败停并保留原始日志。以上只是设计，本轮没有执行命令或取得新增红绿。

## 实际读取范围与 SHA256

以下是本次读取时的工作树字节指纹；B17 其他代理仍可能后续修改文件。范围列限定实际核对片段，不把 rg 定位或被截断输出算全文审查。

| 文件 | 实际重点范围 | SHA256 |
| --- | --- | --- |
| backend/app/business_worker/delivery_contract.py | 15-24、127-163；其余合同编译浏览 | afcd5955cd191e5704595bf0ef3fc1e8a9f44fa715b52ac7ea5fc3954e68d621 |
| backend/app/business_worker/tasks.py | 115-179；要求抽取片段 | ad5fbaf2dbbbb4abdcbb9e7c25116e17b1e97ede9c239e2eebf7ef609c591b07 |
| backend/app/business_worker/chat.py | 324-368、510-530、727-752；资源校验片段 | b9e65d49313212763a4f8fa92fee4cac8116976ad347482b0a9aa19a26e36004 |
| backend/app/business_worker/dispatch_state.py | 118-170、193-214 | b3823d07ec5cd4ba3645347e91c21f85ea9a84f5199952b35f7efc53e0e140a6 |
| backend/app/business_worker/tools.py | 123-178 | cc7a86de750e9b915187878686dec6eebecb7c32323eb4dcfd5d2ec894f5b08e |
| backend/app/modules/chat_artifacts/resume.py | 15-33、36-192 | 9254aafd34f81e30f1aa629d16b0468732c1aa40223c91bafb3d057e9c47fda8 |
| tests/backend/test_dsh_task_operation_guard.py | 13-144 的续办/未知/归属边界 | dfece20d66853bc972390c1974d7a892f1df299ec975ad9475190037cab2618e |
| tests/backend/test_dsh_task_datasource_halt.py | 夹具、35-末尾的 halt/独立/归属边界 | 1f9ae008fd5b5c7fde93baae49fccddf536e4563de49b39acdb77642f22a17ea |
| tests/backend/test_dsh_compound_contract.py | 64-79、167-195、272-277；其余 rg 定位 | 42e3138a1ceb8dc9d1995d28fe546fa3d2e6f695800c2f8a94885077a01fe1fc |
| tests/backend/test_dsh_compound_boundaries.py | 95-104、251-265；其余 rg 定位 | 66c125a5f9f045fe4c9fa3dec0950124c45d85b1f9b09df27fe42c3e312fec9c |
| tests/backend/test_task_clarification_delivery.py | 59-94 | 67b16cf8dec5ee1fc77c7328da9dddb7882d8b9057a45c9e695f2bb2d53879cb |
| tests/backend/test_dsh_halted_task_saved_deliveries.py | 1-137、164-200；其余 rg 定位 | 1c0f37a3e8b84686c2a3599b2eae7d697b0a3970f74cfb3fec526122d040dbab |
| tests/backend/test_business_worker.py | 27-41；夹具/调用 rg 定位 | 97e6dc5a23313d23dab3c11ddfd8ef56d7ed72f3ae1d0c1a2eeaff730019da5d |
| tests/backend/test_dsh_completion_contract.py | 19-42；其余 rg 定位 | 33e4c663d52cc596051c9af51ecea6b1006a4bb0767fe692770ba209adc48742 |
| docs/audit/review_20261004/fix-dsh-compound-batch15.md | 开发记录与边界 | 8614ae5ace160a1a90252f5bda000900037655188b3250b3854aa1da09322adf |
| docs/audit/review_20261004/fix-task-operation-guard-batch16.md | 开发记录、自然续办与 halt 追加 | 5ea3ab63fe57332bb3e0000884154c43ad0adb526fd337c17d990f6d27065d55 |
| docs/audit/review_20261004/review-task-lifecycle-batch16.md | 既有只读复核与保留边界 | 9ce3eecc94679a9bfd7a0fab874e1e2cf2ad46ba9a6668ff1ee73e24496b3e17 |
| .implementation/fix-halt-saved-delivery-batch17.md | 1-23、原句失败表及边界 38-43 | cedfab837ce0ebcf715e361643ed4766fab5d80d0b21d4840c78a2b3baeba019 |
| .implementation/b17-halt-saved-focused-01.log | 首部身份失败、相关初始化片段及末尾首失败停止 | b15cd85a194f8ab086d0dd813e8f40601d53404c6a783e2d0a6c135a9a0c88d9 |

未改动或执行任何被审查文件；本报告不新增测试通过数，不把建议修复写成已实现。
