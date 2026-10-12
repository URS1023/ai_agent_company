# Octop 选定组件集成

来源为本机 `E:\code\Yun_tian\Octop-main`，项目版本 1.0.1，许可证为 MIT。原始许可证保存在本目录的 `LICENSE`；`UPSTREAM.json` 记录每个源文件与移植文件的 SHA-256，便于复核来源和后续升级。

当前移植 `infra/trajectory/types.py`、`projector.py`、`metrics.py`，代码位于 `backend/app/modules/octop_bridge/trajectory/`。三个组件保留原实现，只将内部导入改为相对导入并增加中文来源说明。此处集成的是事件投影与指标聚合，未安装或启动 Octop 服务，也未引入其依赖的 Harness Agent 模型执行循环。

新增适配器 `RunTrajectory` 将当前后端的运行、模型、工具和上下文事件转换为 Octop 轨迹。它仅保存调用 ID、工具名、状态、耗时、错误码、产物 ID 和用量，不保存模型正文、工具参数、业务结果或异常原文。它使用锁支持模型线程记录；快照仅返回最后 160 个事件，累计指标和调用闭环检查覆盖全部已记录事件。事件中途失败会保留已经完成的节点。

使用方式：

```python
from app.modules.octop_bridge.trajectory.bridge import RunTrajectory

trace = RunTrajectory(run_id, conversation_id, workspace_id, user_id)
trace.observe("run.started")
trace.observe("tool.started", {"tool_call_id": call_id, "tool": tool_name})
trace.observe("tool.completed", {"tool_call_id": call_id, "status": "success"})
snapshot = trace.snapshot()
```

模型事件使用 `model.started/completed/failed` 和稳定的 `request_id`。完成事件可传入 `duration_ms`、`ttft_ms` 及 `usage`。输入用量兼容 OpenAI 的 `prompt_tokens`、`completion_tokens` 与缓存字段。所有事件均要求同一运行内使用稳定且唯一的调用 ID。适配器提供轨迹检查证据，业务交付校验仍由现有 Word、Excel、PPT、大屏交付检查负责。

隔离验证：在项目根目录执行 `.venv_py312\Scripts\python.exe backend\smoke_octop_trajectory.py`。测试仅使用合成事件，不连接业务数据库或外部模型接口。


## 已接入当前应用

Open WebUI 的 runtime 已接入本目录记录的轨迹、完成闭环检查和多文件技能。
来源清单分别为 UPSTREAM.json、COMPLETION_UPSTREAM.json、SKILLS_UPSTREAM.json。
业务技能位于 backend/app/business_skills；工具 list_business_skills 与 read_business_skill_resource 通过原网关执行。
详细整合与验证记录：docs/audit/OCTOP_OPENWEBUI_INTEGRATION_20260922.html（宋体）。
