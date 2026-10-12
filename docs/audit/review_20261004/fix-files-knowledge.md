# 文件与知识库隔离修复记录

对应只读报告：`review-files-knowledge.md`。修复仅发生在独立工作树 `E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-capability-contracts`，没有改动正在百轮验收的主工作树产品源码或构建，没有提交/推送。

## 已修复并回归

| 问题 | 修复 | 运行证据 |
| --- | --- | --- |
| FK-001 旧正文及错误来源 | SQL 当前授权分块/文档/源文件联表重建返回正文和引用；索引正文与当前正文不一致时剔除旧相关性，不发送旧正文到 rerank | 嵌入失败、ES 写失败两种更新失败都先复现返回 80℃，修后返回当前 70℃；送重排的文本同样为 70℃；引用从 SQL 恢复；纯语义模式拒绝旧向量 |
| FK-003 无效候选阻挡合法结果 | ES 候选完成权限、状态和正文复核后，数量不足才补充有界数据库关键词召回；SQL 回退复用相同授权/启停/删除过滤 | 删除候选、撤权 KB 候选不再阻挡合法分块；撤权内容不返回，未取得 files.export 的用户仍不能下载原文件 |
| 关联问题：重建中的旧可用内容 | SQL 回退与索引复核都接受 ready/processing 文档的既有分块 | processing 重建期间旧分块在索引不可用时仍能关键词检索；没有增加自动抢占任务逻辑 |
| FK-005 ordinal=0 被误判为空 | 最大序号仅在 None 时从 0 开始，其余正常递增 | 顺序创建 3 条分段由修前 [0,0,0] 变为 [0,1,2]，location.ordinal 同步正确 |
| FK-006 DOCX 字号单位错误 | 用户字号按 Pt 换算；东亚字体槽显式写入指定字体，保留未覆盖的加粗等模板样式 | 12 和 12.5 输入修前均实际变为 0pt；修后分别为 12/12.5pt、宋体 East Asia 属性正确、源模板不变 |
| FK-007 软删文件仍给下载链接 | 文件必须 ready 且 deleted_at 为空才返回下载 URL；不可用时显示明确说明 | 修前软删仍有 /download，修后 URL=null、名称明确已删除或不可用 |

## 测试命令与结果

解释器固定为主工作树虚拟环境；运行目录为独立工作树。全部通过仓库隔离 harness，真实 SQLite、内存模型/ES 桩与临时文档；没有连接 szh_new、其他业务库或网络服务。

先写失败回归：

- 新增搜索/引用/序号测试首次运行：7 失败、1 通过，失败与原审查控制流一致。
- 字号与已删下载链接专项首次运行：3 失败（12pt→0pt、12.5pt→0pt、软删仍提供 URL）。

完成修复后执行：

```powershell
$env:PYTHONIOENCODING='utf-8'
E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe tests/run_isolated_backend.py tests/backend/test_knowledge_search_authority.py tests/backend/test_file_template_font_units.py tests/backend/test_knowledge.py tests/backend/test_knowledge_recovery.py tests/backend/test_knowledge_file_integrity.py tests/backend/test_knowledge_governance.py tests/backend/test_file_governance.py --disable-warnings -q --tb=short --show-capture=no
```

结果：7 个测试文件完整通过，退出码 0；其中新增 12 个案例通过。`git diff --check` 通过，仅 Git 的 LF/CRLF 提示。最后只改了一条中文说明性文档字符串，没有执行逻辑变化。

## 本轮未处理

- FK-002 永久 processing：未实施按时长强制抢占。需要真实任务身份/租约或明确执行者终止证据，才能安全恢复硬中断的入库任务。
- FK-004 HTTP 与 AI 知识共享作用域差异：涉及公共工具/工作流授权上下文，本轮没有通过绕开 File 过滤扩权。
- 并发手工分段 ordinal 分配仍需专门事务/锁设计。本轮仅修正顺序创建时把 0 误判为 None 的确定错误。
- 私有知识库、请求内撤权时点、解压解析工作量边界仍按原报告状态保留。
- 搜索仍保留既有 5000 个当前可见分块的有界词法回退；纯语义模式没有在缺失/过期向量时假称语义检索成功。

## 改动文件

- `backend/app/modules/knowledge/service.py`
- `backend/app/modules/knowledge/governance.py`
- `backend/app/modules/files/template_fill.py`
- `tests/backend/test_knowledge_search_authority.py`（新增）
- `tests/backend/test_file_template_font_units.py`（新增）
