# DSH 企业平台重构契约

重构基线：Gitee `main` 的 `a9e8a9c`。开发分支：`codex/dsh-business-platform`。

## 所有权

- DSH 长驻宿主负责 HTTP 入口、原生会话、模型请求、队列、取消、事件投影与业务插件生命周期。
- Python 作为长驻业务 Worker，不启动 Uvicorn，不运行旧聊天引擎。保留既有业务模块的确定性操作、数据校验、文件处理和数据存取。
- 对话事实归原生 session；跨轮业务成果归稳定 task_id；一次执行归 run_id；产物登记保留 task_id/source_run_id。
- 前端保留现有样式与主 DOM，抽取传输及过程组件。模式 standard/web/deep 与消息投递 queue/steer 分开。
- 业务应用通过 `configurations/business_apps.json` 登记；既有确定性 service/router 保留，公共入口和对话执行不再经过旧聊天宿主。

## Worker 双向逐行 JSON-RPC

协议包 `{jsonrpc:"2.0",id,method,params}`；返回 result 或结构化 error。stdout 只传协议，诊断写 stderr。读取线程持续服务反向模型响应与取消，API、工具和控制操作使用独立线程池，避免同步业务等待反向模型时阻塞台账与取消。

统一 HTTP 请求对象为 `{method,path,query_string,headers:[[name,value]],body_base64}`，其中 path 不含 query。HTTP 响应为 `{status,headers:[[name,value]],body_base64}`。重复响应头必须保留。身份取自请求原有 cookie/header，不信任前端传入的用户标识。

| RPC | 参数 | 结果与责任 |
| --- | --- | --- |
| `system.describe` | `{}` | 返回平台名称、业务插件/路由目录、存储/运行目录、能力目录。不要在公共接口返回私有模型凭据。 |
| `system.initialize` | `{}` | 宿主就绪前执行平台必要初始化；测试由环境隔离指定 SQLite、临时存储并禁调度。 |
| `system.activate` | `{}` | 原生模型和反向请求处理器就绪后启动后台维护及调度。 |
| `api.dispatch` | `{request}` | 进程内 ASGI 分发既有业务路由，无独立监听器。不得分发旧聊天 send/events/cancel。 |
| `chat.prepare` | `{request}` | 验证 send 请求、幂等接纳、持久化用户消息/运行/助手占位，解析模型/资源/企业工具，返回下述 PreparedChat。不启动旧 ChatJob/HarnessClient。 |
| `chat.authorize` | `{request,conversation_id?,run_id?}` | 核验会话/运行，返回身份与稳定 session_id，供 SSE/cancel/queue 使用。 |
| `chat.event` | `{run_id,event,data,native_sequence?}` | 持久化平台视图与 RunEvent，返回 `{id,sequence}`；同一原生事件可幂等提交。正文/工具/状态依据真实事件。 |
| `chat.settle` | `{run_id,status,content,error?,usage?}` | 完成当前运行，核验业务交付，返回 `{status,message_id,content,task,deliveries,error?}`；content 为持久化后的规范正文，终态事件采用同一内容。允许业务 partial，不把非空正文当交付完成；重复结算不覆盖已完成结果。 |
| `chat.history` | `{request,conversation_id,after?}` | 现有会话 detail 与持久业务 projections、已登记事件；用于恢复。 |
| `chat.recover` | `{}` | 返回未结算的 DSH 运行；宿主与原生持久事件核对，已执行但中断的业务不自动重复。尚未执行的已受理队列可以继续。 |
| `chat.queue.update` | `{run_id,action,content}` | 修改尚未执行的持久用户消息与运行输入，返回重建的 PreparedChat 和 user_message（含 parts）。宿主同步模型输入、任务约束并通过 queue.updated 更新用户气泡。执行中编辑返回冲突。 |
| `tool.execute` | `{run_id,call_id,invocation_seq,name,arguments}` | 为当前运行建立独立执行上下文，执行并持久化工具、工件和结果，返回现有 ToolResult 结构。 |
| `model.authorize` | `{run_id?,workspace_id?,profile?,provider,model,purpose,capability?,usage_context?}` | 验证预算并登记模型调用，返回 `{allowed,model_call_id,run_id}`。辅助调用没有聊天 run 时建立独立运行台账。 |
| `model.settle` | `{run_id,model_call_id,status,usage?,error?}` | 用量和成功/失败分开记录。 |
| `run.cancel` | `{run_id}` | 置业务 TaskControl 取消，停止可取消的 Worker 操作；宿主另取消原生 agent。 |
| `shutdown` | `{}` | 取消/收敛 Worker 任务、关闭资源后退出。 |

PreparedChat：`{run_id,request_id,client_request_id,conversation_id,session_id,workspace_id,user_id,message_id,status,duplicate,model,system_prompt,history_seed?,tools,content,home,cwd,task,resources,mode,delivery_mode}`。内部 request_id 使用 run_id，防止不同会话的客户端标识冲突；HTTP 和前端事件返回 client_request_id。model 使用当前规范 `{protocol,baseURL,model,apiKey,contextWindow,maxTokens,reasoningEffort?,thinking?,compat?,reasoningEfforts?,inputModalities?}`；只在私有进程管道传输。tools 为 JSON function schema 列表。content 为 DSH 支持的内容块；初始文本保留用户原文。duplicate 不再发起新的原生请求。

历史迁移仅在原生会话为空时注入 history_seed；之后直接使用 DSH 持久历史，不在每轮 system_prompt 中重复全部平台消息。大历史和大工具结果保存完整来源，模型侧按预算保留预览，使用 `read_tool_result` 按会话分页读回。原始业务回执与产物台账不因上下文裁剪而丢失。

Worker 反向 `model.generate`：`{run_id?,profile,workspace_id,messages,tools?,temperature?,response_format?,request_timeout?,model,capability?,usage_context?}`。宿主执行同一 DSH 模型适配路径并返回现有 `{content,tool_calls,usage,finish_reason}`；没有 run 的后台业务请求也显式解析 profile/model。辅助旧 stream 调用在完整响应后调用原有回调，主对话使用原生实时增量。`json_object` 使用输出指令和实际 JSON 解析校验，不能宣称供应商原生严格结构化输出支持；其它 response_format 明确拒绝。

主对话图片使用 SessionController 支持的 encoded 图片内容，依照模型声明的 image 输入模态登记原生附件。辅助图片接受 PNG/JPEG/WebP/GIF 的 base64 image_url，规范化为原生 ImageBlock；不接受任意远程 image_url。文字主模型读取图片时走已实现的 read_file 视觉服务。

反向 `model.cancel` 按请求或 run 取消原生辅助请求；`chat.execute` 将 Plugin 试运行交给同一原生会话宿主并等待真实终态。`business.event` 只承接 `task.progress`，由宿主与原生事件统一排序后持久化。

双方可在实现中细化字段，但必须同步本契约与调用方，并提供端到端桩验证。

## 对外聊天接口

保留原 `/api/chat/conversations`、详情、send、run events、cancel、files/artifacts URL。DSH 宿主接管聊天执行接口，其余业务由插件路由清单转 Worker。

- `GET /api/chat/capabilities`：`{engine:"dsh",protocol_version:2,modes:[{id,available,reason?}],features:{queue,steer,reasoning,questions,tasks,deliveries},skills:[],plugins:[]}`。未实现或未配置的能力明确 unavailable。
- send 新增 `delivery_mode:"queue"|"steer"`、`skill_ids:[]`、可选 reasoning_effort；原 mode 为 standard/web/deep。`Accept: application/json` 返回受理信息 `{accepted,request_id,run_id,session_id,delivery_mode,status}`；旧 SSE 可兼容。
- `GET /api/chat/conversations/{id}/events?after=<cursor>`：会话级事件流，可跨 Run 继续；首帧 session.snapshot 含 messages/projections/cursor。每帧带稳定事件 id 与 run_id/session_id。Run done 只结束该轮，不代表整个队列结束。
- 详情 projections：`{mode,model,queue:[],tasks:[],deliveries:[],pending_questions:[]}`。历史和实时状态必须一致。
- 新事件：`queue.updated`、`task.updated`、`delivery.created`、`question.requested/resolved`；保留 message.delta/replace、tool.started/completed、run.completed/failed 等。
- `POST /api/chat/conversations/{id}/queue/{item_id}`：`{action:"remove"|"edit",content?}`。URL 会话必须与运行所属会话一致。
- steer 和 `/api/chat/conversations/{id}/questions/{question_id}/answer` 当前返回 501；能力目录同步声明 unavailable。
- 删除会话先取消原生活动运行及排队运行，再归档业务会话。
- 只在后端实际支持并完成闭环时声明对应 feature=true；不以菜单文字代替能力实现。

## 业务应用与 Skills

业务插件目录列出全部既有业务路由及功能分组，但不把所有 HTTP 操作全变成模型工具。AI tools 按业务应用/Skill 选择，前端业务页面直接调用确定性 service。

插件声明保存为项目内可追踪清单，包含 id、名称、路由模块/路径、工具、技能资源、前端入口和依赖。Skills 使用中文说明业务步骤、口径与工具引用。PPT 草稿和交付检查必须代码持久化，不能只写在 Skill 中。

清单支持 `BUSINESS_APPS_MANIFEST` 与 `enabled:false`；启用插件的依赖必须存在且启用，循环依赖拒绝启动。Skill 可以使用所属插件及其传递依赖提供的工具。内置查询和动态 database provider 的故障按结构化 datasource 分类停止该轮后续数据库操作，普通文件/渲染故障不冻结后续聊天。

## 验收

使用临时 SQLite、本机模型桩与临时文件验证真实 Node 宿主 → 原生 DSH → Python Worker → 工具/产物 → 前端事件闭环；不连接在用业务库。保留旧业务 API 合同，验证上传下载、流式恢复、模型参数、跨轮草稿、失败后新提问、交付完整性、队列取消和宿主重启。前端保存桌面/移动端布局基线并比较；默认不改已有 CSS。
