# 平台内部协作契约

本轮唯一共享进度：`E:/code/Yun_tian/new_agent/ai_agent_company/需求梳理/PROJECT_STATUS.md`。协调者串行追加各作者回执；本文件约定协作接口，不替代进度。

## 结构与调用

- 业务包 `services/platform/agent_platform/`，入口 `agent_platform.main:create_app`。
- 路由 → 服务 → 领域模型/仓储；纯判定与计算不依赖HTTP、会话或连接配置。
- 每模块导出 `router: APIRouter`，路由自身使用 `/api/v1` 前缀。模块模型继承 `core.database.Base`，由入口在首次建库前导入。
- SQLite开发/测试库为本项目新文件；不读取旧系统环境变量，不连接既有业务库。生产连接独立配置，测试不可自动回退远端。
- 新增代码和说明注释使用中文。优先提取Test5纯函数/组件/模板，记录来源与适配；不重建Open WebUI/Dify内核。

## 核心Python接口（root负责实现）

```python
from agent_platform.core.database import Base, get_session
from agent_platform.core.context import Actor, get_actor, require_permission
from agent_platform.core.errors import DomainError
from agent_platform.core.records import RecordStore
from agent_platform.core.settings import get_settings
```

- `get_session(request)`：SQLAlchemy Session请求依赖；成功提交，异常回滚。服务按需 `flush`，保持业务操作与审计同事务，不各自隐式提交。
- `Actor`：`user_id: str`、`workspace_id: str`、`permissions: frozenset[str]`、`roles: tuple[str,...]`，`require(permission)`无权限抛403。支持`*`及前缀`m10.*`，资源工作区须再次验证。
- `require_permission("m10.read")`：返回FastAPI依赖函数，结果为Actor；写用`m10.write`，删除/发布/管理分别用`m10.delete/publish/admin`。模块编号对应基线。
- `DomainError(status:int, code:str, message:str, fields:dict|None=None)`：统一错误`{"error":{"code","message","fields"},"trace_id"}`。
- `get_settings(request)`：配置；`data_dir:Path`、`files_dir:Path`、`database_url:str`、`environment:str`、`allow_remote_connections:bool=False`。

## 元数据仓储

仅用于绑定、配置、资源目录等动态元数据。设备、样本、检查、实测、复核、查询、报表等有强业务约束的实体应有各自模型和服务，不全部塞入一张JSON记录表。

`RecordStore(session, actor)`提供：

- `list(kind, q="", limit=50, offset=0, include_archived=False)` → `{"items":[],"total":int}`。
- `create(kind, data:dict, *, id=None, visibility="workspace")` → 扁平资源dict，附id/workspace_id/revision/created_at/updated_at。
- `get(kind, id)` → 当前工作区可见资源或404。
- `update(kind, id, changes:dict, expected_revision:int)` → 新版本；版本冲突409。
- `delete(kind, id, expected_revision:int)` → 软删除；引用约束由业务服务预检。
- `audit(action, resource_type, resource_id, details:dict|None=None)` → 追加审计；不得写入密码、token及原始密钥。

请求列表统一`{"items":[],"total":int}`，详情返回资源对象。新增/编辑/删除返回实际持久化结果，不返回假成功。资源更新携带`revision`；已引用标准和证据采用追加版本，不能覆盖历史。

## 跨模块协议

- 制造业务从data模块的受控查询服务取数；统计纯引擎允许直接接受经过验证的观测数组作隔离测试输入，并明确来源。
- 原生适配器配置从integrations模块的服务获取；无配置返回503 `integration_not_configured`，无网络连接时不生成成功运行。
- 原生控制台用真实用户会话/独立深链接，应用API Key不能冒充控制台登录。Dify应用/数据集Key类型分开；OWUI不自建消息树或聊天循环。
- 所有外发请求需显式配置URL和凭据，有限超时、不自动重试写操作、不跟随重定向；测试使用HTTPX MockTransport或经核实本机服务。
- 补充接口可以增加，但须匹配前端实际操作，记录契约，禁止用一个通用CRUD界面冒充领域行为。

## 正式质检报告的数据流

`POST /inspection-report-generations`经`reports.service.ReportService.generate_inspection`调用`inspection_source.InspectionSource`，要求报表写权限和当前质检读权限。请求只传指定检验、运行和检验版本；来源从不可变`InspectionRun`、任务输入和该执行任务的复核事件读取，不重跑业务数据查询。报告保存标准、运行摘要和当时复核快照；原查询的字段权限在每次读取、历史版本与导出时重新核验。

正式报告以`layout.kind=inspection_report`识别。正文只能通过显式刷新捕获新复核并增加修订；改名不覆盖事实。发布另查当前运行、检验版本和复核序号。恢复、复制、模板和自定义画布都不得删除来源后保留敏感结果。列表按当前授权过滤并计算可见总数；源字段失权不会阻塞其他可见报告。正式HTML在`inspection_document.py`按正文流排版，PDF完整分页，Office复用结构化表格；多页正文拒绝PNG导出。前端`InspectionReportAction`选择运行，`InspectionReportView`用空sandbox展示真实服务端HTML。

## 前端约定

Vue3/TypeScript，Composition API，单一API client；同源`/api/v1`，开发代理到`127.0.0.1:8080`。身份使用HttpOnly会话Cookie；不在localStorage存凭据。`GET /api/v1/me`返回`user/workspace/workspaces/roles/permissions/csrf_token`，写请求带`X-CSRF-Token`。首次部署`GET /api/v1/auth/status`，未初始化时通过`POST /api/v1/auth/setup`创建管理员与工作区；登录`POST /api/v1/auth/login`，退出`POST /api/v1/auth/logout`。

应用外壳/表单/表格/图表/领域工作区各有组件；模块manifest维护导航、路由与所需权限。保持qKnow独立滚动，不在页面堆技术说明与规则文案。原生编辑器通过真实深链接打开，未配置时提供当前操作所需的连接配置入口。
