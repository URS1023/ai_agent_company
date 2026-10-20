# 系统辅助配置

面向维护该模块的前后端开发、测试人员。入口是系统状态页的“系统配置”标签，沿用现有运营模块，不增加全局导航。

## 来源与适配

| 功能 | 原始来源 | 当前实现 |
|---|---|---|
| 字典 | `qKnow-develop/qknow-ui/src/views/system/system/dict/index.vue`、`data.vue` | 保留标签、键值、排序、默认项、标签色和启停的控件结构，接平台会话与版本接口 |
| 参数 | 同目录 `config/index.vue` | 类型化文本、数值、开关、JSON配置，工作区/个人作用域 |
| 公告 | 同目录 `notice/index.vue`、`detail.vue` | 纯文本公告、类型、发布时间、失效时间和受控公告流 |
| 表单 | `qKnow-develop/qknow-ui/src/views/system/tool/build/index.vue` | 原版仅占位页；本次实现声明式字段编辑、服务端预览校验和结构导出，不声称复制了成熟表单设计器 |
| 开发文件 | 原版 `system/tool/gen/` | 原版Java/DDL生成不适用。当前只从已保存表单导出Vue控件骨架和TypeScript类型，不执行代码或DDL |
| 接口目录 | 原版 `system/tool/swagger/index.vue` | 展示当前FastAPI实际OpenAPI并导出JSON，不加载外站Swagger或提供任意接口执行器 |

qKnow许可保存在根目录 `licenses/qKnow-LICENSE.txt`；前端提取文件保留来源注释。没有复制原库凭据、数据库绑定或无用提示文案。

## 调用与权限

`support_router.py` 经现有 `operations/router.py` 注册，统一前缀 `/api/v1/operations`。四种资源路径为 `dictionaries`、`parameters`、`announcements`、`forms`。

| 操作 | 路径与输入 | 权限 |
|---|---|---|
| 列表/详情 | `GET /{资源}`，`q/enabled/scope/limit/offset`；`GET /{资源}/{id}` | `m16.read` |
| 新增 | `POST /{资源}`，`{code,name,scope,enabled,definition}` | `m16.admin` |
| 修改/启停 | `PATCH /{资源}/{id}`，局部字段和 `revision` | `m16.admin` |
| 软删除 | `DELETE /{资源}/{id}?revision=N` | `m16.admin` |
| 历史/恢复 | `GET .../revisions`；`POST .../restorations {revision,target_revision}` | 读 `m16.read`，恢复 `m16.admin` |
| 表单结构/开发文件 | `GET /forms/{id}/schema`；`GET .../scaffold?format=vue|typescript` | `m16.read` |
| 表单预览校验 | `POST /forms/{id}/validations {revision,values}` | `m16.read` |
| 当前接口目录 | `GET /developer-tools/openapi` | `m16.read` |
| 字典业务消费 | `GET /dictionary-options/{code}` | 已登录；仅当前工作区及本人启用配置 |
| 公告消费 | `GET /announcement-feed` | 已登录；仅当前工作区已发布、未失效且启用公告 |

所有Cookie写请求继续使用公共CSRF校验。入参禁止增加工作区、所有者或任意执行字段。编码创建后固定，删除后保留编码及历史，不复用同作用域的历史编码。普通管理员只能读取自己的个人配置；平台全局管理员可管理本工作区的个人配置。

`scope=workspace` 对当前工作区可见；`scope=personal` 对创建者可见。字典业务消费优先本人启用版本，停用后回落到工作区版本。`parameter` 是受控配置登记，不会自动覆盖进程环境变量、数据库连接或业务判定；新增消费者须显式选择编码、读取权限并记录调用方。凭据继续使用独立密钥管理，不将参数当密钥仓库。

## 数据流与边界

- `support_schemas.py` 限制公开输入；`support_validation.py` 归一化各类型定义，并同时生成JSON Schema与执行预览校验。表单支持文本、多行文本、数值、整数、选择、开关、日期，共1—100字段，不支持自定义脚本、正则执行或数据库写入。
- `support_service.py` 先限定工作区/作用域，再执行版本比较更新。每次创建、修改、恢复和删除在请求事务内追加不可变快照与审计；恢复创建新版本，不覆盖历史。唯一约束冲突返回409，版本过期返回409，不自动重试。
- 预览只返回字段错误与校验结果，不保存输入值；审计仅记录资源编码、版本、修改字段名及校验是否通过。完整历史定义受同一资源当前作用域检查保护。
- 公告不渲染HTML。发布时间必须带时区并转为UTC；定时发布通过读取公告流时的有效性判断实现，不伪造后台定时任务。正文不自动推送给外部用户。
- 表单JSON Schema使用2020-12，`additionalProperties=false`；`x-control` 与 `x-options` 保留控件及选项标签。Vue导出只产生静态声明式控件，调用方仍需显式接入业务提交与授权。
- 定义与预览值各最多100,000 UTF-8字节，字典/下拉选项最多200，资源列表分页上限500。无外部连接、任意SQL、动态代码求值、生产表DDL或模型调用。

数据库两张表由 `support_models.py` 注册；正式环境必须先执行独立04迁移。迁移由协调者维护，本模块不在启动时修改生产结构。

## 验证入口

`services/platform/tests/test_operations_support.py` 使用每次测试新建的临时本机SQLite，覆盖作用域、消费者可见性、权限、版本冲突、恢复与软删除、审计脱敏、参数类型、发布时间、字段定义、JSON Schema、预览及ASGI真实路由/CSRF。没有执行数据源业务查询、原生模型或外部服务。页面本轮先完成脚本/模板静态编译；真实浏览器验收由协调者在独立本机平台串行执行。
