# 批次十五 C03/C04：复合交付合同与跨轮续办

日期：2026-10-04。唯一修改工作树：`major-compound-delivery`；分支 `codex/major-compound-delivery`。开始基线 `ea81027`，根代理随后只快进了批次十四前端/记录至 `e36533e69abb374a7b8795eab8c383e7206e5de8`。没有修改旧 `major-task-lifecycle` 树，没有提交或推送。

## 结果与实际范围

本次修改 5 个产品文件，新增 2 个后端专项文件，共 **67 个新增用例**（contract 33、boundaries 34）。先取得 4 个核心产品红例，再取得 2 个原句/API 红例、1 个混合 PPT 红例、4 个复核补充红例。全部相关红例已有转绿记录。不存在将失败日志覆盖为成功日志的处理。

已经跑过 151 项相邻组合、93 项 PPT/草稿组合，以及最后一次 93 项交付/澄清/steer 组合。此后仅按根代理要求跑新增小组：混合 PPT 6 项、复核补充最终 9 项通过。**这些组合有重复，不能相加为独立测试总数；最后新增的小组修复之后没有再次执行整个大组。最终 67 项新专项及全局门禁由根代理统一执行。**

全部 Python 执行均使用主树绝对路径 `E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe`，经本树 `tests/run_isolated_backend.py`。平台库为临时 SQLite，文件/日志在临时目录，实际模型调用被夹具替换为固定配置，未发送模型请求。MySQL/PostgreSQL 驱动、外网、8011/8012、Redis/ES 在用连接由隔离 harness 禁止。本次没有业务库查询、模型调用、浏览器启动或在用服务重启。

## 实现

### 用户要求与续办

- `business_task` 增加 `delivery_contract_version=1`、`delivery_constraints`、`constraint_failures`、`constraint_receipts`。既有 `required_deliveries=[{type,count}]` 外部形状保持。
- 解析常见中文/阿拉伯总页数与范围，保留封面和结束页属于总页数的口径；“第15页”“正文15页”“15页Word”、咨询、拒绝不会被当成 PPT 总页数。混合格式按相邻格式归属，具名后句范围复用现有 `infer_page_constraint`。
- 制作动作和数量词不作为 PPT 名称。具名要求核对真实第一张可达幻灯片文字，不凭文件名。泛指和具名文件通过唯一匹配分配，每个真实 artifact 最多满足一个 PPT 份数义务。
- 长句继续必须在开头表达继续且明确回指原任务/未完成项目。后半句“不要继续查询数据”是执行限制，不会抹去续办身份；“不要继续原任务”、咨询、新任务保留原先新建边界。
- 同任务保留 task_id/draft_id 和完整原交付清单，正常续办中“先做剩余一份”不缩减原总数。新增种类合并，同类数量默认取旧/新最大值，不把泛指执行顺序当成总数替换。
- 准确沿用 `prepare` 原资源继承和再次校验；没有跳过文件、数据源或服务 scope 授权。已接受 steer、续办队列编辑合并合同，新任务的队列编辑重新编译合同。历史旧任务按原 `submitted_content` 和 accepted steer 补证，不从模型答复/文件名补造。
- **显式 `required_deliveries` 保留旧 API 的整体替换语义**：被移除格式的旧结构约束同步删除；保留格式仍保留明确页数/关系，泛指份数按新清单调整。具名对象缩减存在歧义时不会靠猜测把剩余对象判完成；本批没有新增任意自然语言删除/撤销具名义务的协议。

### 最终字节与大屏来源

- PPT/Word/图片读取最终标准存储字节，单文件 20 MiB、本轮读取 80 MiB；摘要必须等于 `Artifact.sha256`，不使用字体规范化前的 native `source_sha256`。公共 `artifact_inspection` 原文件未修改。
- 原生 Office 在进入字体规范化前增加 ZIP 预检：条目、重复名称、总展开量、单 XML/关系分片范围；不依赖先解压再检查。
- PPT 使用包关系链可达的真实页序和非空页，而不是数 ZIP 文件名或读取 `actual_page_count` 自由元数据。空页填充、伪名称、元数据声称15页及存储字节被改动均不能满足要求。
- Word 必须在正文关系链中真正引用可信大屏图片的相同 SHA。孤立 `word/media` 图片、外链、其他大屏图片、旧版本或失去成功渲染回执均不能充数。
- 图片证明同时读取同任务真实 `render_dashboard_image` 成功持久回执、服务端 renderer 元数据、大屏身份/版本、查询或执行来源，以及实际图片字节。内部回执以显式 workspace/run 归属的 Core 查询读取，撤销 `runs.view` 不会破坏合法来源识别。
- 每次核验重建当前 actor/client scopes，复用 `get_report`、`dashboard_panels` 重新授权数据源/保存结果；只读平台历史，不重跑业务 SQL、不刷新大屏、不重做渲染。`files.view`、`datasources.view`、`dashboards.view`、`analytics.export` 撤权均有真实服务令牌回归。
- 第一次唯一解析后保存 `bound_report_id`；续办不能借另一张同名大屏替换原对象。明确复用既有大屏可由本任务真实图片回执关联，不要求重新创建大屏。
- 无版本要求按当前版本；仅紧跟大屏对象的明确“版本1/v1/第1版”等语义编译版本。正文或标题中的“版本1使用说明”不设版本限制。明确旧版本与当前版本不符且历史来源不能证明时保持 partial，不冒充当前版本就是旧版本。本批没有实现 ReportRevision 全链历史渲染证明。
- “每份 Word”保存独立文件基数，按要求份数核验不同 artifact；只有第一份嵌图不能完成两份要求。普通“嵌入 Word”仍要求至少一份正确关系，保留其他合法部分文件。
- 新辅助逻辑成功/异常均恢复 `Session.info` 的 `resource_access_criteria`、`actor_user_id`、`actor_workspace_id`，回归使用实际 SQLite 与真实 Word/图片，不 mock 掉检查器。

### 终态

原生 completed 只有在数量与结构约束都通过时才保持 completed，否则 partial，结构化缺项有预期值、实际观察值和来源证据；文件不被删除。正文前附简短的真实核验缺项，已生成附件和核实的业务正文保留。

根代理批准一处必要收尾接线：`verified_alert_reply/verified_inventory_reply` 后重新保护 `constraint_failures`，防止正确业务文字掩盖附件未完成。取消/失败/中断状态保持；没有改 C01/C02 原生窗口、未知回执判定，也没有添加自动循环、自动 SQL 或自动副作用重试。

## 红绿记录

全部日志在 `.implementation/compound-batch15/`。测试命令的共同前缀为：

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py --disable-warnings <下列目标>
```

| 日志 | 实际结果 / 含义 |
| --- | --- |
| `red-initial.log` | 4 个真实产品红：1页冒充15页、Word缺大屏、长句丢task、剩余1份缩减清单；6.00秒 |
| `green-core.log` | 中间阶段 3过1错：新验证链遇到历史不完整 QueryResult 的 Pydantic 异常，随后改为缺证据而非崩溃；此文件名不代表整轮成功 |
| `green-expanded-1.log` | 中间阶段20过1错；队列回归误用了HTTP包装参数，是测试夹具调用签名问题，已更正，未当作产品红 |
| `green-adjacent-1.log` | 151项通过；新增当时33项与 task_clarification_delivery、native_deliveries、steering、business_worker、dashboard_delivery；双`-q`未打印耗时总数，进度为72+72+7 |
| `green-boundaries-1.log` | 首批14项权限/字节边界通过；28.50秒 |
| `green-final-specialized.log` | 当时53项新增 + multiple_presentation_drafts、presentation_request_pages、query_delivery_resume 合计93项通过；112.29秒 |
| `red-original-request.log` | 原始长句误计数/对象名、显式清单删除格式后残留约束，2红；7.20秒 |
| `green-original-request.log` / `original-request-diagnostic.log` | 原句尚有对象名截取问题，1红1绿/1红；保留诊断中完整 task JSON |
| `green-original-request-final.log` | 上述2项均转绿；3.78秒 |
| `green-freeze.log` | 当时59项新增 + 澄清/steering，93项通过；85.94秒；是复核补充前的阶段快照 |
| `red-mixed-decks.log` | 泛指15页+具名15页，真实文件齐全却 partial，1红；2.86秒 |
| `green-mixed-decks.log` | 混合/两份/具名/原句/显式续办6项通过；7.56秒 |
| `red-review-followup.log` | 子审3点及明确版本4红：继续附带查询限制、继续后半句改页数、v1被v2代替、两份Word只有一份嵌图；6.98秒 |
| `green-review-followup.log` | 初步6项转绿；8.70秒 |
| `green-review-followup-final.log` | 最终9项通过，包含明确版本匹配/不匹配、版本词标题不误判、两份Word正反例；13.84秒 |

最终小组选择式：

```text
tests/backend/test_dsh_compound_boundaries.py -k 'no_more_queries or clause_can or explicit_dashboard_version or each_word or version_word or original_compound or generic_and_named'
```

供根代理统一门禁加入：

```text
tests/backend/test_dsh_compound_contract.py
tests/backend/test_dsh_compound_boundaries.py
```

## 仍需准确陈述的边界

1. 这是确定性合同核验，不是证明任何自然语言都能完整抽取要求。常见明确格式、页数和嵌图表达有覆盖；模糊“左右/至少”、正文页数、任意撤销/替换多个具名对象、纯 UI plugin_inputs 中未出现在文字里的页数、复杂跨对象指代，没有在本批扩展成通用语义解析。标准 PPT 工具既有 UI 页数校验仍保留。
2. 物理页数与非空不证明各页内容正确、不重复、版式合格。Word 图片来源和 SHA 不证明报告所有数字正确。当前大屏图是带来源的保存数据快照，不是交互大屏像素级截图。没有伪造内容或填空白页换取通过。
3. 原始设备巡检整句正例使用明确合成平台 QueryHistory、真实 PPTX/XLSX/大屏/嵌图 DOCX，证明本批交付合同可完成；**没有连接真实巡检库，也没有因此声称真实未巡检数量、饼图口径和整轮模型行为已经验收。** 图表类型/统计口径是其他链路的职责，本批没有扩展其合同。
4. 旧图片缺服务端来源/成功回执、明确历史大屏版本无完整可验证来源、损坏或超预算附件均保持未完成。没有靠重新查询恢复缺失证据。
5. 本批不加入自动续跑；C01/C02 同 Run 未知副作用保护保持。跨 Run 自动防重没有在这里增加，不能声称同 task_id 自带跨 Run 幂等；“继续”本身不代表允许自动重复原未知外部操作。
6. 没有执行真实 Host+模型的端到端对话或浏览器；本批用真实 Worker/ToolGateway、原生 present、真实文件和临时 SQLite验证。根代理统一门禁和隔离浏览器验收另行记录。

## 冻结文件 SHA-256

`git diff --check` 通过，只有仓库既有 LF/CRLF 提示。

| 文件 | SHA-256 |
| --- | --- |
| `backend/app/business_worker/tasks.py` | `F486471D42A3C03F3A43570A7AF5F7851E0764A79E4A683EBB0E030272FE7444` |
| `backend/app/business_worker/native_deliveries.py` | `8917719DDCAFF1EFEE08FCC7631E848938B506208170124515CE4931DC02C558` |
| `backend/app/business_worker/chat.py` | `08B9F68CE2A248BAAF0C3F2973119979B4661E4CAAC74C54366F94724B999CBB` |
| `backend/app/business_worker/delivery_contract.py` | `22D625193E29A223C6ECED88810311C3258A084801C27ED6F210341710C506B8` |
| `backend/app/business_worker/delivery_evidence.py` | `6750EC4F7367720AFB1ADD1E547CC3C057A2F7169189838D9644CB7D3BEA97A4` |
| `tests/backend/test_dsh_compound_contract.py` | `149C7B7C05F61085745ECFF3DF8C73CCE58291A02C4D90482B18E4D5E6F62E0F` |
| `tests/backend/test_dsh_compound_boundaries.py` | `28858DEEF36A96FCF71B4DF12FE64884DBB368F4CE38D9AA1E428086DE3E1CFC` |
