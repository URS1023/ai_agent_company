# 制造业 Agent 平台

面向设备质检、设备维护分析、产品批次质检。平台负责受控取数、业务判定、人工复核、报表与权限；对话使用原生 Open WebUI，Agent、知识和固定流程使用原生 Dify。

**当前已有可运行的前后端与真实业务闭环，整体仍在实施。** 原生运行、全部报表组件及完整需求验收见[共享进度](需求梳理/PROJECT_STATUS.md)。根目录 index.html 是历史样式原型，下面启动的是实际应用。

## 本机启动

**当前电脑直接双击根目录的 `启动系统.cmd`**，会复用或启动 Windows 平台、Open WebUI 以及已配置的本机 WSL Dify，并打开平台与对话页面：

- 平台：<http://127.0.0.1:8080>，账号 `admin`。
- AI 对话：<http://127.0.0.1:13000>，账号 `admin@local.example`；也可从平台“AI工作台 → AI对话 → 进入对话”打开。
- 流程与知识：<http://127.0.0.1:13001>，也可通过平台原生入口打开。
- 双击 `查看本机账号.cmd` 查看三套服务的本机账号；双击 `停止系统.cmd` 关闭本项目服务。密码只保存在本机加密文件，Git 不包含账号密码或模型密钥。

Open WebUI 前端复用官方同版本 Python wheel；Dify 前端复用官方 1.17.1 制品，API、插件、Agent 和代码沙箱沿用锁定源码。Dify 的独立发行版位于 `D:/WSL/ai-agent-company`，14 个原生服务已启动、数据库迁移及账号初始化已完成。工作流、Agent 和知识等功能分别验收，详见[实施验收表](项目文档/contracts/实施验收.csv)。

此入口不启动 Docker，按用户要求不设置进程内存上限；启动超时、所属进程和数据目录检查仍保留。平台与 Open WebUI 可直接在 Windows 运行，WSL 供 Dify 的 Linux 执行组件使用。

其他电脑安装平台时，需要 Python 3.12—3.14、Node.js 22.12+；原生环境与本机加密配置需另行准备，见[部署说明](deploy/native/README.md)。下面命令仅启动平台：

```powershell
npm run setup
npm run build
npm start
```

访问 http://127.0.0.1:8080，首次自行创建管理员与工作区，无预置密码。开发运行 `npm run dev`，前端为 http://127.0.0.1:5173。

平台默认只创建自己的 `.local/platform/platform.sqlite3`、文件和密文凭据，不读取旧项目环境文件，不连接旧业务库，不改旧工作流绑定。配置项见 [.env.example](.env.example)，只读取当前进程明确设置的 AGENT_ 变量。原生服务准备与实际限制见 [本机部署](deploy/native/README.md)；未启动的依赖不会伪造成功。

## 三个业务操作流程

| 任务 | 用户操作 | 实际结果 |
|---|---|---|
| 设备质检 | 创建设备/标准 → 导入或查询检测数据 → 映射指标 → 判定 → 复核 | 测值、缺值、单位、判定、标准版本和复核可回查 |
| 设备维护 | 创建设备/测点 → 选择受控查询 → 指定时间、负荷、工况 → 分析 → 处理 | 两期同工况变化、样本与覆盖率、来源和不足原因，不将统计变化当成确诊 |
| 产品质检 | 创建产品/批次 → 固定标准与样本计划 → 映射数值/成分/外观结果 → 判定/复核 | 批次、样本、原始证据和人工决定分别保存，数据不足不默认合格 |

质检运行可生成正式报告，保留当次标准、精确数值、样本明细与复核记录；支持HTML、PDF、Word、Excel、PPT、CSV、JSON。报告正文来源固定，修改规则后应重新检验，不能手工改写已保存的判定事实。

可先用CSV导入完成平台自有数据库闭环。接企业数据库时另行核验实际主机、端口、库名、服务归属和授权范围；已有配置或回环地址不等于批准测试。

## 模型与密钥

指定 OpenCode Go 的 `deepseek-v4.1-flash`，向量为硅基流动 `BAAI/bge-m3`。密钥通过以下命令输入，输入不回显：

```powershell
.venv/Scripts/python.exe tools/configure_models.py
.venv/Scripts/python.exe tools/configure_models.py --check --probe
```

配置加密保存在 `.local/providers/`，原生启动只向所属子进程注入，不提交Git。每次显式probe各发一条短测试请求，不含业务数据、不重试、不切模型；本机部署和模型API接入分别配置。

## 修改与扩展

| 修改目标 | 主要入口 | 维护边界 |
|---|---|---|
| 菜单、页面、按钮权限 | apps/web/src/modules/manifest.ts 与对应模块 | 页面围绕任务，字段校验反馈规则 |
| 质检、维护算法 | services/platform/agent_platform/modules/manufacturing/ | 纯计算独立，标准/证据/复核保留历史 |
| 数据连接与SQL预算 | modules/data/connectors.py、sql_safety.py | 新适配器继续通过同一权限和预算层 |
| 报表组件和模板 | 后端 modules/reports/、前端 modules/reports/reused/ | 分别验收画布和导出，保留原类型语义 |
| 新平台模块 | agent_platform/registry.py 与前端 manifest | 独立router/service/model，声明权限 |
| 原生API/运行版本 | adapters/、modules/integrations/、deploy/native/ | 不重写聊天、节点或插件内核 |
| 数据表 | services/platform/migrations/ | 新建增量迁移，不改已发布历史迁移 |

编辑/删除携带revision；冲突返回409，引用资源先检查影响。配置目录复用仓储，质检、样本、查询和报表使用独立业务模型。

## 验证与数据库升级

```powershell
npm run check
npm test
npm run db:check
npm run db:upgrade
```

开发库首次启动可建表。已有未登记迁移版本的开发库，先停止写入并备份，再运行 `.venv/Scripts/python.exe tools/manage_database.py adopt`；结构不符会拒绝接管。生产启动禁止隐式建表，需明确迁移、独立 AGENT_MASTER_KEY 与HTTPS入口。

页面备份/恢复演练当前只操作平台自有SQLite，恢复到新文件，不替换运行库、不重放业务。文件目录、平台与模型主密钥、原生引擎数据库另行保管；单个平台数据库备份不等于完整灾备。

## 文档与来源

- [项目文档唯一入口](项目文档/README.md)：产品/UX、架构、开发与验收四份核心文档。
- [实际代码OpenAPI](项目文档/contracts/platform.openapi.json)：`.venv/Scripts/python.exe tools/export_api.py` 生成；原openapi.json仍为设计基线，不混记实现数量。
- [进度和问题](需求梳理/PROJECT_STATUS.md)、[文件索引](需求梳理/FILE_INDEX.md)：认领、验证证据及缺口。
- [前端复用位置](apps/web/REUSE.md)、[内部契约](services/platform/INTERNAL.md)、[来源许可](NOTICE.md)。

只提交新增平台、必要提取组件/素材和文档，不提交四个原项目、旧数据包、依赖、运行数据或密钥。历史样式预览使用 `npm run preview:legacy`。
