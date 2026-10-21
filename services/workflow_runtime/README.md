# 平台内部纯图执行器

面向后端开发与测试。平台继续使用 Python 3.14；本执行器用独立 Python 3.13 运行 Graphon 0.8.0，不启动 HTTP 服务，不接管原 Dify 数据库、账号或管理界面。

本轮只复用原 Graph、Engine、VariablePool、节点实现和容器调度。支持 `start`、`end`、`answer`、`if-else`、`variable-aggregator`、旧聚合别名 `variable-assigner`、`assigner` 1/2、`list-operator`、`iteration`、`iteration-start`、`loop`、`loop-start`、`loop-end`。模型、知识、工具、HTTP、代码、完整 Jinja、人工输入与触发器明确拒绝；后续适配不能绕过平台权限和来源证据。

## 调用

Windows独立环境就绪后，父进程可调用 `worker.run_isolated(python_executable, request, timeout_seconds=15)`；解释器必须来自项目 `.local`。当前原 Windows Dify 环境缺 `pydantic`，不能作为已可用环境，也不修改该环境。

实际纯图使用 `worker.run_wsl(request, timeout_seconds=15)`：先复用已有注册表/D盘/Windows keeper/Linux启动证明，仅在已运行的 `ai-agent-company` 内用 `agentapp` 和 `/opt/ai-agent-company/venvs/api/bin/python` 执行此worker。清空Linux继承环境；GNU timeout单独约束本次进程组。子进程核同一PID namespace的启动摘要和实际用户/解释器，不读取root私有manifest或密钥。实时回调模式增加PID、出生ticks、UID、命令摘要及nonce握手，停止时再次核验并只回收本次worker；未确认Linux停止时必须保留unknown，不能以Windows客户端退出替代。

平台`modules/flows/graph_runtime.py`已接入上述实时回调。定义明确保存`format: graph`、`engine: graphon`、`engine_version: 0.8.0`，开始节点字段生成结构化输入，原步骤格式继续兼容。执行前预约Run和Task，回调在主线程逐条提交TaskEvent；SQL错误回滚后停止派发，不做补偿查询。事件接口按cursor分页，输出保留JSON类型，同一幂等键不会重复运行。平台实际温度分支验收通过，完整前端画布仍在实施。

日常`启动系统.cmd`调用`desktop.py start-workspace`，先启动平台及已配置的本机Open WebUI，再对已配置的专属WSL调用`ensure_windows_lifetime()`并核对运行环境。纯图不需要PostgreSQL、Redis或Dify的14个业务角色；未配置的新电脑不会自动安装WSL。当前已有环境复用已实测，整机重启后的启动链尚未验收。

请求只通过 stdin 输入一份 UTF-8 JSON，随后关闭 stdin。stdout 每行一条 JSON；`kind=event` 为真实 Graphon 事件，最后一条 `kind=result` 才是运行终态。协议版本为 1，事件 sequence 从 1 连续递增，每条记录携带原 run_id。

```json
{
  "protocol_version": 1,
  "run_id": "owned-pure-graph-1",
  "root_node_id": "start",
  "inputs": {"message": "设备点检"},
  "graph": {
    "nodes": [
      {"id": "start", "data": {"type": "start", "version": "1", "variables": []}},
      {"id": "end", "data": {"type": "end", "version": "1", "outputs": [{"variable": "message", "value_selector": ["start", "message"], "value_type": "string"}]}}
    ],
    "edges": [{"id": "start-end", "source": "start", "target": "end", "sourceHandle": "source"}]
  },
  "variables": [],
  "limits": {"max_steps": 1000, "max_seconds": 10}
}
```

`variables` 可放 `{selector: ["conversation", "count"], value: 0}` 初始 JSON 变量。保留 Dify 的节点版本、变量选择器、连接分支和容器归属；工厂的四个方法不把迭代或循环降级为外层顺序执行。节点及后代全部通过 Graph.init 校验后才运行。

单次输入最多 1 MiB、256 节点、1024 连接；输出最多 2 MiB。默认单个 Engine 工作线程；执行步数及时间有独立预算。父进程用已持有的 Popen 句柄在总超时后停止自己的子进程，不重试、不换执行内核。Graphon 的事件限时仍保留，但它不能替代父进程硬超时。只有进程正常退出且收到一致终态时才返回成功。

子进程不继承模型、数据库、系统代理或 Python 路径配置。纯节点白名单之外再用 Python audit hook 拒绝网络、SQLite、派生进程及文件修改。该边界用于本次纯图能力；不将 Python audit hook 当作允许任意代码节点的系统沙箱。

Graphon 0.8.0 的分发元数据把文档提取、transformers 等依赖均声明为必需，没有可单独安装的 core extra。本轮未安装任何包；不能通过 `--no-deps` 装入少量库后宣称官方分发的 pip check 完整。后续 Windows 小环境可另行提取 Apache 核心闭包，保留文件摘要和独立发行身份，须另行验证。

纯图验证显式选择现有 WSL：在项目根目录设置 `PYTHONPATH=services/platform`、`WORKFLOW_PURE_TEST_WSL=1` 后运行 `pytest services/platform/tests/test_workflow_runtime.py`。不自动回退环境、不启动依赖服务；所选运行环境缺失时应保留失败或明确跳过。

## 来源与许可

原依赖为 Graphon 0.8.0，来自 [langgenius/graphon](https://github.com/langgenius/graphon)，官方 wheel 摘要记录在同目录 requirements.txt，并与锁定 Dify 1.17.1 的 `api/uv.lock` 对照。已核本机 `graphon-0.8.0.dist-info/METADATA`：`Requires-Python >=3.12,<3.14`、`License-Expression: Apache-2.0`；许可证在该分发包 `licenses/LICENSE`。本目录只新增协议与依赖适配，没有复制修改 Graphon 引擎源码。

Dify `web/` 或 API 扩展节点若后续提取，须单独记录 Dify 源码 LICENSE 的附加条件；Graphon 的 Apache-2.0 不能替代整个 Dify 前端的许可。统一 NOTICE 由主项目维护。
