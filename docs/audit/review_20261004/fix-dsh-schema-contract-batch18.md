# B18 当前 DSH PPT 嵌套 schema 合同专项

## 范围与基线

- 任务起点：`b7241b663101e8931ffa0a4d514a7cb4984fcdbd`。实现后测试前 HEAD：`4f47d51eb2deb1de7e39dccd21310c9133e1da8e`；根代理同步后续 B17 测试/文档修正，本任务未改产品。
- 独占新增文件：`tests/backend/test_dsh_presentation_schema.py`、`tests/dsh/presentation_schema_probe.mjs`。
- 未修改既有测试、门禁、产品源码、README 或正式文档；另一代理的自然续办改动保留。
- 原缺口是当前 DSH 嵌套声明未被实际适配器合同检查覆盖，不声称生产已有丢字段故障。旧 OpenWebUI AST legacy 检查不能替代本专项。

## 真实路径与合成边界

1. Python 从实际 `builtin_specs()` 取得 `write_presentation_slides`，使用真实 `ToolSpec.to_openai_function()` 导出 schema，不复制自写 schema 当真值。
2. Node 使用真实 `SessionHost.installBusiness` 注册到已安装 DSH 的 `ToolRuntime`，检查 `ctx.tools.schemas()` 与 `ctx.systemPrompt.assemble().tools`。上游 agent-loop 的 `preStep/buildRequest` 实际也使用该 assembly 的 tools。
3. 两个协议各启动独占 `127.0.0.1:随机端口` 合成 HTTP 模型桩，实际 LlmRuntime 与 pi-ai / DeepSeekAdapter 构造请求和解析流；`BlockAssembler` 还原模型工具调用，再经真实 ToolRuntime → Host 送至内存 Worker 桩。
4. 模型桩和 Worker 回执是合成数据；未运行完整 session controller/agent loop，未调用真实 Python 业务 Worker 写入 PPT，未渲染/导出/浏览器验收。内存 Worker 不能证明真实业务执行成功。
5. Host 使用 raw register；ToolRuntime 不自动执行输入 schema 校验。四个深层非法参数交真实 Python 网关公共 `validate_arguments` 拒绝，明确独立于 Node 调用链。
6. 实际 HTTP 声明与原始 builtin 完整对比，并检查 items/chart/table/left/right 独立路径。随后从实际捕获声明删除 `chart.properties.categories`，同一断言必须失败；不是输入对象与自身相等。

## 预算与隔离

- Python 必须经 MAIN `.venv_py312/Scripts/python.exe tests/run_isolated_backend.py`；环境为临时存储及 SQLite 内存库。本专项无需数据库 fixture，业务源连接/SELECT 为 0。
- Node 子环境只保留系统执行所需变量，不继承实际模型密钥、代理、用户配置，也不加载 .env。
- 引入既有 `tests/dsh/local_network_only.mjs`，额外 net.Socket.connect 守卫仅放行本轮 127.0.0.1 的确切随机端口；连接上限 1/协议。
- 实际适配器显式合成配置 `retryPolicy.maxRetries=0`；模型请求 5 秒超时；每个 Node 子进程 25 秒上限。两协议总请求上限 2，不重试、不扩预算。
- 上游依赖按本树标准 `.implementation/harness_migration/upstream` 加载，当前为指向 MAIN 同名安装目录的 junction；没有复制或改写上游。新克隆需先执行仓库的 DSH 安装流程。

## 首轮执行前指纹

- Python 新测试 SHA256：`bb6e2bf01be72a4694a1b4534404adb67eac5718e41730e95bff978b2557d713`
- Node 新探针 SHA256：`5d0215d2c2d21f10273d9952700c2787d89b80e96c844388ff4da91ac852b1a8`
- `node --check tests/dsh/presentation_schema_probe.mjs` 通过；`git diff --check` 通过（既有产品文件仅显示 autocrlf 提示）。

## 执行记录

唯一一次运行命令（工作目录为 B18 独立树）：

```powershell
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_dsh_presentation_schema.py --maxfail=1 -s --junitxml=.implementation/dsh-schema-batch18-first.xml 2>&1 | Tee-Object -FilePath .implementation/dsh-schema-batch18-first.log
```

- 2026-10-04 13:43:45 +08:00，首轮 6/6 通过，0 failed/errors/skipped；XML 精确用时 1.057 秒，pytest 显示 1.06 秒；命令进程总计 9.56 秒，退出码 0。
- XML 实际两条链路用例：OpenAI 0.595 秒，DeepSeek 0.341 秒。每个协议实测 1 连接、1 HTTP 请求、1 内存 Worker 派发，共 2/2/2；两次均检测到模拟删除的深层 schema 字段。没有失败重试。
- 四个实际拒绝用例：图表数值数组中对象、表格单元格中对象、左侧条目说明中数组、缺 chart.categories；四次均被真实 `SchemaValidationError` 拒绝且错误路径命中对应深层位置，各约 0.002 秒。
- 无产品红例运行；这是补齐测试缺口，原产品在本新增合同下首轮为绿。深层 schema 删除是内存负对照，由 `pytest.raises` 捕获预期失败，并非声称生产红例。
- 两次 `DSH_SCHEMA_EVIDENCE` 计数见原日志第 2–3 行；汇总见第 12 行。日志首行已有中文初始化文本在 PowerShell 管道中出现编码替换，原件保留未重写；英文计数、结果和 XML 均可精确读取。
- 原日志 SHA256：`39cb5dd1b02c4f519cef38c94188847b60100c9aa14fcd8199bccd34f8f265fa`。
- JUnit XML SHA256：`19438bb0380ac9584a3afc67956171d0e5d1f1b72ea03bdddefaa7d6b8593e6f`。
- 唯一 warning 来自既有 `system_tools/monitoring.py:46` 的 utcnow 废弃提示，没有修改无关文件。
- 本专项未使用 DB fixture，没有执行业务源连接/SELECT；隔离 runner 仅准备临时 SQLite 配置与临时存储。真实模型调用 0，全部模型响应为本轮 127.0.0.1 合成服务。

## 最终只读指纹与限制

新增两文件测试后未改，SHA256 与首轮执行前指纹相同。为追溯实际真值与 Host，附本次未修改的生产文件指纹：

| 文件 | SHA256 |
|---|---|
| `backend/app/providers/tool/builtin_provider.py` | `dfc6f95cbb1a710306231e0a1a43ab5511d111c0443b5801e9d9c2989646cbf5` |
| `backend/app/modules/presentation/tool_schema.py` | `606a6a39a3d91d9ec9c5483d155c78b91bc15a550ecb26aa826e5f335c640606` |
| `services/dsh_host/sessions.mjs` | `0c643f126310aa298ce4fb96814c11d9624f68bb28089e7e633cc3071ad5f3cd` |

已安装 DSH 根包版本为 `0.1.7-alpha.2`。直接使用的已构建入口指纹（相对上游根目录）：

| 入口 | SHA256 |
|---|---|
| `packages/core/tools/lib/index.js` | `0fb13adfedeab0ff7bfd6bf8691063f771c37b5f896502a26809340e620e7d04` |
| `packages/core/system-prompt/lib/index.js` | `ff422afac6ba85f89afa985d921aed0b603feb354a05a549c48eae04cf0d2f57` |
| `packages/llm/llm/lib/index.js` | `b8614b7ad251c4ae121aeef3a423b9f2eff99ec602be8eb36ab35ee3511bb7f6` |
| `packages/llm/llm-pi-ai/lib/index.js` | `b49de2029fc843b1212e5df1324e875f0cf38aef8789d84ff5fa8bba378aa56e` |
| `packages/llm/llm-deepseek/lib/index.js` | `7e77a3f5a01df3881f9f0fba6ecc45db9ef7d4d84f77d57931329431ebd69428` |

未执行大组、统一门禁、浏览器、构建、真实模型、业务库、提交或推送。ModelRoutes 的生产重试策略不是本专项验证对象：为严格零重试，探针按相同真实适配器公开 API 注入合成路由并设 `maxRetries=0`，不调用辅助模型 `ModelRoutes.generate`。本结果不证明当前完整会话循环、续办决策或 PPT 文件质量。

探索代理对最终两文件另做只读复核：未发现实质假阳性或越界；确认实际 HTTP 声明、SSE → BlockAssembler → ToolRuntime → Host → 内存 Worker 路径及网络限制。该复核未执行测试，不新增通过次数。
