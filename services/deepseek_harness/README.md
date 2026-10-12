# 企业 Harness 运行服务

此目录只实现协议适配、企业 preset 和摘要模板。对话、工具调度、上下文压缩、历史恢复由本地锁定的 DeepSeek Harness `0.1.7-alpha.2` 执行。

## 构建与运行

上游源码副本默认位于项目 `.implementation/harness_migration/upstream`，原始参考目录保持只读。先按上游锁文件安装依赖，执行 `pnpm run build:lib:host` 与 `pnpm run build:lib:client`，再在此目录执行 `node build.mjs`。会话控制器依赖的 Typert、connection 和 file-upload 是双面包，其 Host 入口也在 client 构建阶段产出，因此不能只运行 host 构建。插件构建使用 Node 自带 TypeScript 类型剥离，不下载第二套依赖；原生验证检查真实 CLI、插件和运行循环。

Python 启动 `node services/deepseek_harness/launch.mjs`，传入独立的绝对 `DSH_HOME`，可用 `DSH_UPSTREAM_ROOT` 指定上游副本。首条请求是 `initialize`，其模型密钥仅放在本进程内存及环境，不写入 profile。工作目录由服务端创建并归属当前会话。

启动器生成 `DSH_HOME/profiles/company-runtime`，随后调用官方 `dsh --profile company-runtime` CLI 入口。profile 使用空 bundle 清单和明确的依赖白名单，不加载原生 shell、通用文件工具、动态插件管理器、开发指令/个人技能扫描、联网工具、会话上传、遥测或插件清单上传。

## 双向 JSON-RPC 2.0

标准输入输出均为每行一个 JSON 对象；诊断日志进入标准错误。

| 方向/方法 | 参数与返回 |
| --- | --- |
| Python → `initialize` | `sessionId,cwd,resumeExpected?,systemPrompt,tools,model`。模型包含 `protocol,baseURL,apiKey,model,contextWindow,maxTokens`；协议为 `deepseek-messages` 或 `openai-completions`。`resumeExpected=true` 时日志缺失将失败。 |
| Python → `session.prompt` | `sessionId,requestId,content,clientTimeZone?`。`content` 使用上游文本/图像 prompt parts。每个进程一次只接收一个活动任务。 |
| Python → `session.cancel` | 等待原生取消清空队列、业务工具静止、Agent idle 和日志持久化后返回 `stopped:true`。 |
| Python → `session.follow` / `session.page` | 重新订阅原生快照及增量，或使用 `throughSeq,beforeSeq,maxMessages` 获取历史页。 |
| Python → `health` / `shutdown` | 查询就绪状态，或完成活动收敛与持久化后关闭。 |
| Node → `model.authorize` | 每次真实模型调用前发送 `sessionId,purpose,provider,model`，必须返回 `{allowed:true}`。包括重试和上下文压缩。 |
| Node → `tool.invoke` | `sessionId,callId,invocationSeq,name,arguments`；`invocationSeq` 是本次原生 `tool/call` 的持久事件序号。Python 返回业务工具的 JSON 结果；参数、资源授权和业务回执由 Python 网关验证。 |
| Node 通知 `tool.cancel` | `sessionId,callId`；Python 确认操作停止或实际完成后才回复原工具请求。 |
| Node 通知 `session.frame` | `{sessionId,frame}`，`frame` 保持上游 `SessionFollowFrame` 原样，包括快照、持久事件、带 attemptId 的即时流式。 |
| Node 通知 `run.settled` | `sessionId,requestId,status,cursor,error?`；只有原生终态与日志落盘后才报告完成或取消。 |

重复 `requestId` 返回同一接收回执和已有终态；冷恢复从原生日志恢复去重信息，不重放已经执行的请求。模型错误仅由上游 provider retry policy 处理，正常可恢复错误最多重试两次。模型重试不重放业务工具。

工具去重使用会话身份与 `invocationSeq`，不能只使用模型提供的 `callId`：供应商可在后续步骤或新一轮任务合法复用同一个工具 ID。缺少对应原生调用事件时拒绝执行业务工具。重复调用回执仍必须经过当前权限核验。

## 企业职责

固定 `company` preset 在创建和恢复时装载当前授权的业务工具与系统提示，所有工具串行执行。`CompanyCompaction` 仅覆盖上游 `BasicCompactionEngine.summarize`，保留上游触发、保留区间、收敛、取消和持久化机制；摘要中文记录日期、单位、业务口径、资源来源、确认条件和已完成操作。

本服务无数据库驱动和业务数据库凭据。业务工具通过 Python 网关运行，必须继续遵守项目本机 `szh_new` 测试边界。原生运行测试只使用 loopback 模型服务和合成工具，不连接业务数据库或真实模型。
