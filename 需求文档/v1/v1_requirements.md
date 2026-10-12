# 企业 AI 工作平台 V1 产品需求与技术设计文档

> 文档类型：PRD + 技术架构设计 + 开发约束  
> 当前阶段：V1 / 第一阶段  
> 核心目标：通过 Chat 统一入口，将企业知识、用户文件、数据库、API/MCP、程序执行能力与 Skill 组合起来完成任务，并把成功任务沉淀为可复用能力。  
> 原则：**第一阶段实现核心闭环；复杂 Workflow、多 Agent、完整企业治理暂不实现，但底层必须预留。**

---

# 0. 文档目的

本文档用于指导第一阶段产品、前端、后端、AI/RAG、数据、基础设施团队进行统一开发。

本文档重点解决：

1. 第一阶段到底开发哪些功能。
2. 哪些能力现在不开发，但必须预留数据结构或模块位置。
3. Chat、Knowledge、File、Skill、Connector、Plugin、Run、Artifact 之间如何定义。
4. 如何接入现有程序、REST API、MCP、数据库。
5. 如何让 AI 根据知识库、用户文件、用户意图自动组织 JSON 参数调用程序。
6. 如何将一次成功任务沉淀为 Skill / Plugin。
7. 如何进行代码执行、数据分析、文档/PPT/Excel 生成。
8. 如何支持多用户、Workspace、权限、Secret、版本和审计。
9. 如何保证未来增加 Workflow、Multi-Agent、SSO、DLP、复杂审批时不推翻 V1。

---

# 1. 产品定位

## 1.1 产品定义

本产品不是单纯的聊天机器人、MCP 管理器或 Workflow 编排平台。

产品定位为：

> **一个通过自然语言把企业知识、用户文件、数据库、现有程序和 AI 方法组合起来，并将成功工作方式沉淀为可复用 AI 能力的企业工作平台。**

核心使用方式：

```text
用户进入 Chat
    ↓
上传文件 / 选择知识库 / 连接数据 / 使用插件
    ↓
描述业务需求
    ↓
AI 理解任务
    ↓
读取知识 / 文件 / 数据
    ↓
使用 Skill 决定“怎么做”
    ↓
调用 Connector / Tool / Program
    ↓
必要时运行 Python / 代码
    ↓
生成 Artifact
    ↓
Chat 展示结果
    ↓
用户可将成功处理方式保存成 Skill / Plugin
```

---

# 2. 第一阶段核心原则

## 2.1 V1 只验证一条核心闭环

第一阶段必须优先验证：

> **上传/连接数据 → 对话完成任务 → 调用企业已有程序 → 生成结果文件 → 保存为可复用能力 → 再次稳定使用**

不得为了“功能完整”提前实现大量复杂模块。

---

## 2.2 用户层概念必须简单

普通用户 V1 主要看到：

- 对话
- 知识库
- 插件
- 文件
- 数据源
- 上传
- 输出文件

高级技术概念尽量隐藏：

- MCP
- Tool
- Connector
- Skill
- Schema
- Function Calling
- Runtime
- Sandbox
- Adapter

管理员/高级用户可以在能力管理页看到这些对象。

---

## 2.3 系统内部必须区分能力类型

内部严禁把所有东西都叫“插件”。

### Resource

数据从哪里来。

包括：

- 文件
- 知识库
- 数据库
- API 数据
- 模板
- Artifact
- 未来其它数据资源

### Skill

AI 应该怎么完成某类任务。

例如：

- 财务分析方法
- 招投标文件编制方法
- 咨询报告写作方法
- 商业 PPT 内容组织方法

### Connector / Tool

机器实际可以执行什么。

例如：

- query_database
- read_docx
- render_ppt
- generate_report
- query_erp
- python_execute

其后端可以是：

- REST API
- MCP
- 数据库
- 本地程序
- Python
- 内置服务

### Plugin

面向用户的可复用业务能力包。

示例：

```text
财务经营分析 Plugin
=
财务分析 Skill
+
财务数据库 Resource
+
Python Tool
+
图表 Tool
+
PPT Tool
```

### Run

一次真实执行过程。

### Artifact

执行后产生的文件或结构化产物。

---

# 3. 第一阶段范围说明

## 3.1 V1 必须实现

### 用户产品功能

- Chat 对话
- Streaming 输出
- 上传文件
- 临时文件上下文
- 知识库
- 选择知识库
- 数据源连接
- REST API Connector
- MCP Connector
- 数据库 Connector
- 基础 Python Sandbox
- Skill 创建与修改
- Plugin 创建与修改
- Plugin 使用
- Plugin 复制
- Plugin 基础分享
- Artifact 展示
- DOCX/XLSX/PPTX/PDF 类结果管理
- Run 执行状态
- Run Trace
- Workspace
- Admin / Member 两级角色
- 基础资源访问权限
- Secret 加密存储
- Skill / Plugin / Connector Version

---

## 3.2 V1 只预留，不实现

以下能力需要预留数据库字段、模块接口或抽象，但第一阶段不做完整产品功能：

- 可视化 Workflow Builder
- Plugin 调 Plugin
- Agent 调 Agent
- Multi-Agent
- 复杂审批流
- SSO
- SAML
- OIDC 企业登录
- SCIM
- LDAP / AD
- ABAC
- DLP
- Data Classification
- Plugin Marketplace
- Plugin Signing
- Supply Chain Security
- 企业成本中心
- 复杂 Evals 平台
- 多区域部署
- HA
- Disaster Recovery
- Browser Agent
- Desktop Agent
- 定时任务
- Event Trigger
- Batch Workflow UI
- 复杂长期 Memory

---

# 4. 产品信息架构

## 4.1 首页

建议：

```text
┌──────────────────────────────────────────────────┐
│ Logo                                       用户   │
├──────────────┬───────────────────────────────────┤
│ + 新建对话    │                                   │
│              │                                   │
│ 对话         │            Chat                   │
│              │                                   │
│ 知识库       │                                   │
│ 插件         │                                   │
│ 文件         │                                   │
│ 数据源       │                                   │
│              │                                   │
│ 最近对话     │                                   │
│              │    ┌────────────────────────┐     │
│              │    │ + 输入消息...          │     │
│              │    └────────────────────────┘     │
└──────────────┴───────────────────────────────────┘
```

第一阶段不要把：

- Workflow
- Agent
- Skill
- MCP

全部放到主导航里。

---

# 5. Chat 功能需求

## 5.1 Chat 输入区

输入框支持：

```text
+
├── 上传文件
├── 选择知识库
├── 选择插件
├── 连接数据源
├── 添加 API / 程序
└── 添加 MCP
```

需要支持：

- 多文件上传
- 文件移除
- 当前会话绑定知识库
- 当前会话绑定 Plugin
- 当前会话临时绑定 Connector
- 当前会话 Artifact 继续作为新输入

---

## 5.2 Chat Message 类型

前端不能只使用：

```json
{
  "role": "assistant",
  "content": "..."
}
```

必须支持结构化 message parts：

```text
TextPart
ToolCallPart
ToolResultPart
StatusPart
CitationPart
FilePart
ArtifactPart
ErrorPart
ApprovalPart（预留）
```

推荐定义：

```typescript
type MessagePart =
  | TextPart
  | StatusPart
  | ToolCallPart
  | ToolResultPart
  | CitationPart
  | FilePart
  | ArtifactPart
  | ErrorPart;
```

---

# 6. 文件系统

## 6.1 V1 支持格式

至少支持：

- PDF
- DOCX
- XLSX
- CSV
- TXT
- PPTX
- 图片

---

## 6.2 File 与 Knowledge 必须分离

### File

当前任务临时输入。

例如：

- 本次招标文件
- 本次报价表
- 当前客户需求
- 当前模板

### Knowledge

长期持久知识。

例如：

- 公司资质
- 历史招投标文件
- 历史案例
- 企业规章制度
- 产品知识

不得把用户每次临时上传的文件默认自动进入知识库。

---

## 6.3 File 数据模型建议

```text
File

id
workspace_id
owner_user_id
name
mime_type
size
storage_key
sha256
source_type
status
metadata
created_at
deleted_at
```

预留：

```text
classification
acl
retention_policy
```

V1 可以不启用复杂数据分类。

---

# 7. Knowledge Base

## 7.1 V1 功能

支持：

- 创建知识库
- 上传文档
- 文档解析
- Chunk
- Embedding
- Vector Search
- Metadata Filter
- Rerank（可选第一阶段简化）
- 引用来源
- 删除文件
- 重建索引
- Knowledge Version 预留

---

## 7.2 RAG 基本流程

```text
File
 ↓
Parser
 ↓
Cleaner
 ↓
Chunker
 ↓
Metadata
 ↓
Embedding
 ↓
Vector DB
 ↓
Search
 ↓
Rerank
 ↓
LLM
```

---

## 7.3 知识引用

输出内容应该尽量保存：

```text
source_file_id
chunk_id
page
location
```

后续用于：

- 来源展示
- 事实核验
- 数据血缘
- 文档证据

---

# 8. Skill

## 8.1 Skill 定义

Skill 表示：

> AI 完成某种任务的方法、步骤、规则和注意事项。

Skill 不负责直接执行外部程序。

---

## 8.2 Skill 示例

```text
名称：
招投标文件编制

目标：
根据本次招标要求、公司知识库和用户模板生成投标材料。

Rules:

1. 首先解析招标要求。
2. 提取所有强制资格要求。
3. 提取评分标准。
4. 查询公司知识库寻找对应材料。
5. 企业资质和历史业绩禁止编造。
6. 缺失资料必须明确标注。
7. 必须按照用户提供模板组织内容。
8. 输出前进行完整性检查。
```

---

## 8.3 SkillSpec

建议结构：

```json
{
  "id": "skill_xxx",
  "name": "招投标文件编制",
  "description": "",
  "instructions": "",
  "input_description": "",
  "output_description": "",
  "rules": [],
  "examples": [],
  "recommended_tools": [],
  "required_resources": [],
  "version": 1,
  "status": "published"
}
```

必须支持：

- Draft
- Published
- Archived

---

## 8.4 自然语言修改 Skill

用户说：

> 技术方案以后重点强调项目实施计划。

系统应：

```text
读取当前 Skill
 ↓
生成修改 Proposal
 ↓
生成新 Skill Version
 ↓
测试
 ↓
发布
```

禁止直接覆盖历史版本。

---

# 9. Connector / Tool

## 9.1 定义

Connector 表示：

> AI 可调用的确定性执行能力。

可能的 Provider：

```text
HTTP
MCP
Database
Built-in
Sandbox Program
```

---

## 9.2 统一 ToolSpec

无论底层是哪种协议，Runtime 应统一使用内部 ToolSpec：

```json
{
  "id": "tool_generate_ppt",
  "name": "generate_ppt",
  "description": "根据结构化页面内容生成PPT",
  "provider": "http",
  "input_schema": {},
  "output_schema": {},
  "permissions": [],
  "timeout_seconds": 120,
  "retry_policy": {},
  "version": 1
}
```

LLM 不应关心 Tool 背后是 MCP 还是 HTTP。

---

# 10. REST API / 现有程序接入

这是 V1 核心能力。

## 10.1 接入方式

用户或管理员可以：

- 手动填写 API
- 粘贴接口说明
- 上传接口文档
- 输入自然语言注释
- 后续预留 OpenAPI 自动导入

---

## 10.2 示例

现有程序：

```text
POST /api/render-ppt
```

作用：

```text
根据结构化 JSON 生成 PPT
```

输入：

```json
{
  "title": "...",
  "template_id": "...",
  "pages": []
}
```

返回：

```json
{
  "url": "https://..."
}
```

平台内部应转换成：

```text
Connector
 ↓
ToolSpec
 ↓
Runtime
```

---

## 10.3 Connector Builder

流程：

```text
接口说明
 ↓
AI 分析
 ↓
生成 description
 ↓
生成 input_schema
 ↓
生成 output_schema
 ↓
配置 auth
 ↓
生成测试参数
 ↓
Test Connection
 ↓
Test Invoke
 ↓
保存 Connector
```

---

## 10.4 参数描述要求

建议要求开发者对参数提供足够语义。

错误示例：

```text
projectId
```

推荐：

```text
projectId

项目系统中的唯一项目ID。
禁止使用项目名称代替。
示例：P2026080312
```

枚举：

```text
reportType

financial
project
bidding
```

日期：

```text
startDate

YYYY-MM-DD
```

---

# 11. MCP 接入

## 11.1 MCP 在产品里的定位

MCP 属于 Connector Provider。

用户层不必强制理解 MCP。

内部：

```text
Tool Gateway
├── MCP
├── HTTP
├── Database
├── Builtin
└── Sandbox
```

---

## 11.2 MCP 接入

需要支持：

- 添加 MCP Server
- 拉取 Tools
- 读取 Tool description
- 读取 inputSchema
- 读取 outputSchema（如提供）
- Tool Enable / Disable
- Workspace Scope
- Secret Reference
- Tool Version / Server Version 预留

---

# 12. Database Connector

## 12.1 V1 支持

建议第一阶段：

- PostgreSQL
- MySQL

---

## 12.2 第一阶段安全要求

默认：

```text
READ ONLY
```

仅允许：

```text
SELECT
```

不允许：

```text
INSERT
UPDATE
DELETE
DROP
ALTER
TRUNCATE
```

---

## 12.3 数据库能力

内部 Tool：

```text
database.describe
database.list_tables
database.describe_table
database.query
```

数据源作为 Resource：

```text
finance_db
sales_db
project_db
```

Tool 与 DataSource 分离。

不要：

```text
finance_db_mcp
sales_db_mcp
project_db_mcp
```

各写一套重复逻辑。

---

# 13. Parameter Compiler

这是本系统 V1 的关键模块之一。

## 13.1 作用

将：

```text
User Intent
+
Conversation
+
Files
+
Knowledge
+
Tool Schema
+
Resource
```

转换成：

```text
ToolArguments JSON
```

---

## 13.2 示例

用户：

> 给我生成北京 XX 项目的经营报告。

目标程序要求：

```json
{
  "projectId": "",
  "startDate": "",
  "endDate": "",
  "templateId": ""
}
```

Parameter Compiler 应自动完成：

```text
北京 XX 项目
 ↓
调用 search_project
 ↓
获得 P001823

经营报告
 ↓
确定日期范围

默认模板
 ↓
获得 OP-02
```

最终：

```json
{
  "projectId": "P001823",
  "startDate": "2026-01-01",
  "endDate": "2026-08-01",
  "templateId": "OP-02"
}
```

---

# 14. 参数验证

LLM 生成参数以后禁止直接调用程序。

必须经过：

```text
LLM Arguments
 ↓
JSON Parse
 ↓
JSON Schema Validation
 ↓
Business Rule Validation
 ↓
Permission Validation
 ↓
Execution
```

---

## 14.1 V1 Validator

至少：

- Required
- Type
- Enum
- Min / Max
- String Length
- Array Length
- Format
- 自定义业务 Validator

---

# 15. Field Source Policy（建议 V1 数据结构预留）

这是企业文档场景的重要能力。

定义某些字段允许从哪里取得。

例如：

```json
{
  "project_amount": {
    "source": "database",
    "allow_llm_generate": false
  },
  "company_profile": {
    "source": "knowledge",
    "allow_llm_generate": false
  },
  "executive_summary": {
    "source": "llm",
    "citations_required": true
  }
}
```

V1 可以只实现部分规则，但数据模型必须预留：

```text
field
source_policy
fallback_policy
require_citation
require_confirmation
```

---

# 16. Plugin

## 16.1 Plugin 定义

Plugin 是用户看到的业务能力包。

内部：

```text
Plugin
├── Skill
├── Tools
├── Resource Bindings
├── Inputs
├── Outputs
├── Permissions
└── Version
```

---

## 16.2 PluginSpec

建议：

```json
{
  "id": "plugin_financial_analysis",
  "name": "财务经营分析",
  "description": "",
  "skill_refs": [],
  "tool_refs": [],
  "resource_bindings": [],
  "input_schema": {},
  "output_schema": {},
  "permissions": {},
  "version": 1,
  "status": "published"
}
```

---

## 16.3 Plugin 功能

V1：

- 创建
- 修改
- 删除
- 复制
- 发布
- 使用
- 分享到 Workspace
- 历史版本
- 回滚
- Test Run

暂不：

- Plugin → Plugin
- Plugin Marketplace
- 跨组织公共市场

---

# 17. 自然语言创建能力

不能全部强制生成 Plugin。

需要 Capability Builder 判断用户想创建哪种对象。

---

## 17.1 用户说

> 以后分析 Excel 都按照这个方法。

生成：

```text
Skill
```

---

## 17.2 用户说

> 接一下这个 PPT API。

生成：

```text
Connector
```

---

## 17.3 用户说

> 把刚才的分析方法和这个 PPT 接口组合起来。

生成：

```text
Plugin
```

---

## 17.4 Capability Builder

```text
Natural Language
 ↓
Intent Classification
 ↓
Skill / Connector / Plugin / Resource Binding
 ↓
Draft
 ↓
Test
 ↓
Publish
```

---

# 18. Chat → Skill / Plugin

这是 V1 的核心差异能力。

用户完成一次任务后：

```text
“以后都这样处理。”
```

系统分析此次 Run：

```text
User Intent
Tool Calls
Data Sources
Files
Knowledge
Prompts
Results
```

生成：

```text
Skill Draft
```

如果包含固定工具依赖，可以同时生成：

```text
Plugin Draft
```

禁止只把聊天内容直接保存成 Prompt。

---

# 19. 失败诊断与修改

## 19.1 原则

代码或程序报错时，不得直接默认“修改程序”。

需要首先判断错误归属。

---

## 19.2 Error Classifier

```text
Execution Error
 ↓
Diagnoser
 ↓
├── Skill Error
├── Parameter Error
├── Connector Adapter Error
├── Tool / Program Error
├── Data Error
└── Permission Error
```

---

## 19.3 自动修复策略

### Skill Error

生成 Skill 新版本。

### Parameter Error

修改 Parameter Compiler / Prompt / Mapping。

### Adapter Error

生成 Connector Adapter 新版本。

### AI 自己生成的托管程序 Error

允许：

```text
Create Patch
 ↓
Run Test
 ↓
Create Version
```

不得直接覆盖线上版本。

### 企业已有外部程序 Error

只报告错误和请求上下文。

除非程序代码也托管在本平台且拥有明确修改权限，否则 AI 不得修改外部系统代码。

---

# 20. 招投标业务 Demo

建议作为 V1 标杆场景。

## 20.1 数据

知识库：

```text
公司资质
历史招投标文件
历史项目
产品资料
成功案例
```

当前文件：

```text
此次招标文件.pdf
模板.docx
其它附件
```

用户：

> 根据此次招标要求、公司历史资料和这个模板生成投标文件。

---

## 20.2 执行链

```text
Chat
 ↓
读取招标文件
 ↓
Requirement Parser
 ↓
BidRequirementSpec
 ↓
读取知识库
 ↓
资料匹配
 ↓
Gap Detection
 ↓
招投标 Skill
 ↓
生成 DocumentSpec
 ↓
Document Renderer
 ↓
DOCX Artifact
```

---

# 21. BidRequirementSpec

建议结构：

```json
{
  "project_name": "",
  "deadline": "",
  "qualification_requirements": [],
  "technical_requirements": [],
  "business_requirements": [],
  "scoring_rules": [],
  "required_documents": [],
  "required_sections": []
}
```

---

# 22. Requirement Gap Detection

如果招标要求：

```text
营业执照
ISO9001
项目经理证书
近三年项目业绩
```

知识库中缺少：

```text
项目经理证书
```

系统必须显示：

```text
发现资料缺失：

1. 项目经理 XX 证书

[上传资料]
[继续生成并保留占位符]
```

严禁模型虚构资质。

---

# 23. 文档生成方式

不要：

```text
LLM
↓
直接操作 Word 格式
```

建议：

```text
LLM
 ↓
DocumentSpec JSON
 ↓
Document Renderer
 ↓
DOCX
```

---

## 23.1 DocumentSpec

示例：

```json
{
  "title": "XX项目投标文件",
  "template_id": "template_xxx",
  "sections": [
    {
      "title": "项目理解",
      "blocks": []
    }
  ]
}
```

Renderer 负责：

- 标题
- 字体
- 页边距
- 页码
- 页眉
- 页脚
- 目录
- 表格
- 图片
- 模板样式

---

# 24. PPT 生成

同样：

```text
LLM
 ↓
PresentationSpec
 ↓
PPT Connector / Renderer
 ↓
PPTX / Online URL
```

不要让模型直接承担格式排版。

---

# 25. Artifact

Artifact 是一等对象。

支持：

```text
document
spreadsheet
presentation
pdf
image
chart
code
dataset
json
```

Artifact 可以：

- 在 Chat 中打开
- 下载/预览
- 继续作为下一次 Run 的输入
- 绑定 Run
- 记录来源
- 未来进行版本管理

---

# 26. Artifact 数据模型

```text
Artifact

id
workspace_id
run_id
owner_user_id
type
name
mime_type
storage_key
external_url
metadata
created_at
```

预留：

```text
version
parent_artifact_id
lineage
classification
```

---

# 27. Python Sandbox

V1 必须有。

用途：

- Excel
- CSV
- 数据清洗
- 财务计算
- 统计
- 图表
- 文件处理
- JSON 转换

---

## 27.1 第一阶段包

建议至少：

```text
pandas
numpy
matplotlib
openpyxl
python-docx
```

根据业务扩展。

---

## 27.2 Sandbox 安全要求

至少：

- 每个 Run 独立工作目录
- 执行超时
- CPU 限制
- 内存限制
- 文件范围限制
- 默认限制公网网络
- Secret 不直接暴露给 LLM
- 运行日志记录

---

# 28. Programmatic Tool Calling 预留

复杂任务中不要让大量数据不断返回给 LLM。

未来支持：

```text
LLM
 ↓
生成受控程序
 ↓
Sandbox
 ↓
调用多个 Tool
 ↓
仅返回最终结果
```

V1 可以先使用普通 Tool Loop。

但 Runtime Tool SDK 需要支持未来在 Sandbox 内安全调用 Tool。

---

# 29. Tool Gateway

所有执行工具必须通过统一 Gateway：

```text
Tool Gateway

├── MCP Provider
├── HTTP Provider
├── Database Provider
├── Builtin Provider
└── Sandbox Provider
```

Tool Gateway 负责：

- Tool lookup
- Permission
- Secret
- Parameter validation
- Timeout
- Retry
- Logging
- Result Normalization

---

# 30. Result Normalizer

不同工具返回格式必须统一。

外部程序可能返回：

```json
{"url": "..."}
```

平台应转为：

```json
{
  "status": "success",
  "data": {},
  "artifacts": [
    {
      "type": "presentation",
      "name": "经营报告.pptx",
      "url": "..."
    }
  ],
  "metadata": {}
}
```

前端只面向平台内部结果协议。

---

# 31. 长任务

虽然第一阶段可以简化，但数据结构必须支持：

```text
queued
running
waiting
completed
failed
cancelled
```

Tool 可以返回：

```json
{
  "status": "running",
  "job_id": "xxx"
}
```

Runtime 后续继续查询。

---

# 32. Run

Run 表示一次用户任务执行。

```text
Run

id
workspace_id
conversation_id
user_id
plugin_id
status
started_at
completed_at
model_usage
cost
error
metadata
```

---

# 33. Run Step

虽然 V1 不做 Workflow Builder，但一定要记录 Step。

```text
Run
├── Step 1 read_file
├── Step 2 search_knowledge
├── Step 3 query_database
├── Step 4 python
└── Step 5 render_ppt
```

后续可以：

```text
Run History
↓
Convert To Workflow
```

而不用重新设计执行引擎。

---

# 34. Run Event Stream

前端和 Runtime 之间建议统一事件协议。

例如：

```json
{
  "event": "tool.started",
  "run_id": "...",
  "step_id": "...",
  "tool": "query_database"
}
```

支持：

```text
run.created
run.started
step.started
step.completed
step.failed
tool.started
tool.completed
artifact.created
message.delta
run.completed
run.failed
```

前端使用 SSE 或 WebSocket。

V1 推荐 SSE。

---

# 35. Runtime 状态机

```text
CREATED
 ↓
QUEUED
 ↓
RUNNING
 ↓
COMPLETED
```

异常：

```text
RUNNING
 ↓
FAILED
```

预留：

```text
WAITING_APPROVAL
WAITING_INPUT
PAUSED
CANCELLED
```

---

# 36. Agent Runtime

第一阶段可以使用：

```text
LLM Tool Calling Loop
```

结构：

```text
User
 ↓
Context Resolver
 ↓
LLM
 ↓
Tool Call?
├── No → Reply
└── Yes
      ↓
    Tool Gateway
      ↓
    Tool Result
      ↓
      LLM
```

暂不需要复杂 Multi-Agent。

---

# 37. Context Resolver

负责统一准备：

```text
Conversation
User Files
Knowledge
Plugin
Skill
Available Tools
Resource Bindings
User Permissions
```

不能让各模块自己拼 Prompt。

---

# 38. Model Gateway

V1 建议即使只有一两个模型，也必须通过统一 Model Gateway。

功能：

- provider abstraction
- model routing
- retry
- timeout
- token usage
- logging
- future fallback
- future local model support

Plugin 不直接绑定供应商 API。

建议：

```text
model_profile = reasoning
```

而不是把模型名称写死在业务代码中。

---

# 39. 多用户 / Workspace

第一阶段：

```text
Organization
 ↓
Workspace
 ↓
Users
```

如果产品早期不需要 Organization UI，也建议数据库中预留 organization_id。

---

## 39.1 V1 Role

只需要：

```text
Admin
Member
```

---

## 39.2 V1 权限

至少控制：

- Plugin 可否使用
- Knowledge 可否使用
- DataSource 可否使用
- Connector 可否使用

---

# 40. Secret Management

以下数据禁止进入 Prompt / Chat / PluginSpec 明文：

- 数据库密码
- API Key
- Token
- MCP Credential
- 企业系统账户密码

使用：

```text
Secret Reference
```

示例：

```text
secret://workspace/finance-db
```

Connector 只保存 Secret ID。

---

# 41. Version

以下对象 V1 就必须版本化：

- Skill
- Connector
- Plugin

建议：

```text
skills
skill_versions

connectors
connector_versions

plugins
plugin_versions
```

禁止直接覆盖正式版本。

---

# 42. Draft / Published

至少：

```text
Draft
Published
Archived
```

修改产生：

```text
new Draft Version
```

测试完成：

```text
Publish
```

---

# 43. Test Run

Skill / Connector / Plugin 发布前支持测试。

Connector：

```text
Test Connection
Test Invocation
```

Plugin：

```text
Test Run
```

Skill：

使用 Test Cases 运行基础验证。

V1 Evals 不需要很复杂。

---

# 44. 审计

V1 需要基础 Audit。

记录：

```text
谁
什么时候
创建/修改/删除/运行了什么
```

至少：

- User
- Workspace
- Action
- Resource Type
- Resource ID
- Timestamp
- Run ID
- IP（可选）
- Metadata

复杂企业审计第二阶段再做。

---

# 45. 前端 Plugin 体验

用户不应面对复杂 JSON。

Plugin 卡片显示：

```text
财务经营分析

数据：
Finance DB

能力：
财务分析
Python
图表
PPT

输出：
报告 / PPT

[运行]
[修改]
[复制]
```

高级编辑页再显示：

- Skill
- Tools
- Resource Binding
- Input
- Output
- Permissions
- Version

---

# 46. 修改能力时的智能判断

用户：

> 以后这个章节详细一点。

→ Skill

用户：

> 改用新 PPT 接口。

→ Connector Binding

用户：

> 数据以后从 ERP 来。

→ Resource / Tool Binding

用户：

> 以后同时生成 PPT 和 Word。

→ Plugin Output

用户：

> 把刚才这套方法保存下来。

→ Skill

用户：

> 把这套方法和 PPT 程序组合成一个能力。

→ Plugin

---

# 47. 用户未来行为预判

以下行为应在架构中提前考虑。

## 47.1 修改方法

> 以后先分析收入，再分析成本。

修改 Skill。

---

## 47.2 修改数据源

> 不要用 Excel，改查正式数据库。

Resource Rebinding。

---

## 47.3 修改接口

> 新版接口增加 templateCode。

Connector Version。

---

## 47.4 修改输出

> 改成 PPT。

Plugin Output / Renderer。

---

## 47.5 复制能力

> 复制财务分析插件，改成销售版本。

Plugin Clone。

---

## 47.6 分享能力

> 给项目部使用。

Workspace Sharing。

---

## 47.7 批量处理

> 对 100 个项目分别生成报告。

未来 BatchRun。

V1 数据模型预留：

```text
batch_id
parent_run_id
```

---

## 47.8 Artifact 继续处理

> 把刚才生成的 Excel 做成 PPT。

Artifact 成为新 Resource。

---

## 47.9 强制数据来源

> 金额必须从 ERP 获取，不允许 AI 生成。

Field Source Policy。

---

## 47.10 用户确认

> 这个字段以后必须让我确认。

未来 Human Input / Approval。

Run State 预留：

```text
WAITING_INPUT
WAITING_APPROVAL
```

---

## 47.11 固定知识版本

> 使用 2025 年资料，不使用后来更新内容。

未来 Knowledge Snapshot。

---

## 47.12 自动选插件

> 帮我生成经营报告。

系统根据：

- Plugin Description
- User Intent
- Permission
- Context

自动选择 Plugin。

V1 可以允许用户手动选择 + 模型辅助推荐。

---

# 48. 数据库核心表建议

第一阶段建议至少：

```text
organizations
workspaces

users
workspace_members

conversations
messages

files

knowledge_bases
knowledge_documents
knowledge_chunks

skills
skill_versions

connectors
connector_versions

data_sources

plugins
plugin_versions

plugin_skill_bindings
plugin_tool_bindings
plugin_resource_bindings

secrets

runs
run_steps
tool_calls

artifacts

audit_logs
```

---

# 49. 核心关系

```text
Workspace
│
├── Users
│
├── Files
│
├── Knowledge
│
├── Skills
│
├── Connectors
│
├── DataSources
│
├── Plugins
│
├── Conversations
└── Runs
```

Plugin：

```text
Plugin
├── Skill Version
├── Connector Version
├── Resource Binding
└── Permission
```

Run：

```text
Run
├── Conversation
├── Plugin Version
├── Step
├── Tool Call
└── Artifact
```

---

# 50. 建议服务划分

V1 不建议一开始做过度微服务。

可以采用模块化单体 + 独立 Sandbox Worker。

建议：

```text
apps/
  web/

backend/
  api/
  chat/
  runtime/
  plugin/
  skill/
  connector/
  knowledge/
  files/
  artifact/
  auth/
  audit/

workers/
  agent/
  sandbox/

providers/
  model/
  tool/
    mcp/
    http/
    database/
    builtin/

storage/
```

后期再拆服务。

---

# 51. 推荐技术栈

非强制，可根据团队能力调整。

## Frontend

```text
Next.js
React
TypeScript
SSE
```

## Backend

```text
Python
FastAPI
```

AI/RAG/Python 生态更方便。

如果团队 Go 更强，也可以：

```text
Go Control Plane
+
Python AI Runtime
```

---

## Database

```text
PostgreSQL
```

---

## Cache / Queue

初期：

```text
Redis
```

---

## Object Storage

```text
S3 Compatible
MinIO / Cloud OSS
```

---

## Vector Database

优先避免过度复杂。

可选择：

```text
PostgreSQL + pgvector
```

规模增加以后再单独拆 Vector DB。

---

# 52. Runtime 与 API 必须逻辑分离

不能：

```text
POST /chat
↓
HTTP 请求里一直运行几十秒程序
```

应：

```text
POST /runs
 ↓
Create Run
 ↓
Worker Execute
 ↓
Event Stream
 ↓
Frontend
```

第一阶段即使部署在同一台机器，也要逻辑分离。

---

# 53. 未来 Workflow 预留

V1 不开发可视化 Workflow。

但 Run Step 数据模型需要包含：

```text
step_id
parent_step_id
type
input
output
status
started_at
completed_at
retry_count
metadata
```

以后：

```text
Run Steps
↓
Workflow Definition
```

可以自然升级。

---

# 54. 未来 Plugin → Plugin 预留

Plugin Tool Binding 中不要限制 Tool 只能是 Connector。

未来：

```text
ToolReference
type =
connector
plugin
agent
```

V1 仅允许：

```text
connector
```

---

# 55. 未来 Approval 预留

ToolSpec / PluginSpec 保留：

```text
risk_level
requires_approval
```

Run State 保留：

```text
WAITING_APPROVAL
```

V1 暂不实现复杂审批中心。

---

# 56. 未来 SSO / 企业身份预留

User 不要和密码认证逻辑强耦合。

预留：

```text
identity_provider
external_subject_id
```

后期支持：

- SAML
- OIDC
- LDAP
- SCIM

---

# 57. 未来 DLP 预留

Resource / Artifact / File 预留：

```text
classification
```

例如未来：

```text
PUBLIC
INTERNAL
CONFIDENTIAL
RESTRICTED
```

V1 可以不启用策略。

---

# 58. 未来 Evals 预留

Skill / Plugin Version 预留：

```text
test_suite_id
evaluation_status
```

V1 只做基础 Test Run。

---

# 59. 开发必须注意的安全事项

## V1 必须

1. 数据库默认 READ ONLY。
2. 用户代码必须 Sandbox。
3. API Key / Password 不进入 LLM Prompt。
4. Workspace 数据必须隔离。
5. Tool 调用必须 Permission Check。
6. Tool 参数必须 Schema Validation。
7. 文件路径必须隔离。
8. Runtime 必须 Timeout。
9. Tool Call 必须记录。
10. 生成 Artifact 必须绑定 Run。

---

# 60. 禁止的实现方式

## 60.1 禁止 Plugin = Prompt

Plugin 必须是结构化对象。

---

## 60.2 禁止把 MCP 当所有能力

MCP 只是 Tool Provider 之一。

---

## 60.3 禁止所有文件自动进入知识库

File 与 Knowledge 分离。

---

## 60.4 禁止 LLM JSON 直接调用接口

必须：

```text
Schema Validation
+
Business Validation
```

---

## 60.5 禁止 AI 直接修改正式程序

必须：

```text
Draft / Patch
 ↓
Test
 ↓
Version
 ↓
Publish
```

---

## 60.6 禁止所有 Run 都强绑定 HTTP 请求生命周期

使用 Run + Event Stream。

---

## 60.7 禁止 Secret 明文进入 Plugin / Skill / Chat

使用 Secret Reference。

---

# 61. V1 推荐开发顺序

## Phase 1：基础骨架

完成：

- User
- Workspace
- Chat
- Conversation
- Message
- File
- Object Storage
- Model Gateway
- Run
- Run Step
- Event Stream

目标：

```text
可以稳定聊天
+
可以上传文件
+
可以看到 Run
```

---

## Phase 2：Knowledge

完成：

- Knowledge Base
- Parser
- Chunk
- Embedding
- Retrieval
- Citation

目标：

```text
Chat
+
知识库
+
文件
```

---

## Phase 3：Tool Gateway

完成：

- ToolSpec
- HTTP Connector
- Database Connector
- MCP Connector
- Secret
- Schema Validation
- Result Normalizer

目标：

```text
LLM 可以稳定调用企业程序
```

---

## Phase 4：Sandbox

完成：

- Python Execute
- Excel / CSV
- 图表
- 文件处理

目标：

```text
可以做数据分析
```

---

## Phase 5：Skill

完成：

- SkillSpec
- Skill Version
- Skill Builder
- Skill 修改
- Test Run

目标：

```text
可以保存“AI怎么做”
```

---

## Phase 6：Plugin

完成：

- PluginSpec
- Skill Binding
- Tool Binding
- Resource Binding
- Plugin Version
- Plugin Run
- Clone
- Share

目标：

```text
可以组合为业务能力
```

---

## Phase 7：Chat → Capability

完成：

- Run Summarizer
- Capability Builder
- Chat → Skill
- Chat → Plugin
- 修改能力识别

目标：

```text
用户完成一次任务
↓
以后都这么做
↓
自动沉淀能力
```

---

## Phase 8：Artifact / 文档

完成：

- DocumentSpec
- PresentationSpec
- Spreadsheet Output
- Renderer Connector
- Artifact Preview

目标：

```text
完成企业文档生成闭环
```

---

# 62. V1 验收主场景

必须完整跑通以下场景。

---

## 场景 A：招投标

```text
1. 用户建立招投标知识库
2. 上传历史资料
3. 新建 Chat
4. 选择知识库
5. 上传此次招标文件
6. 上传 DOCX 模板
7. 输入生成要求
8. AI 提取招标要求
9. 检索公司资料
10. 识别缺失内容
11. 生成 DocumentSpec
12. 调用文档 Renderer
13. 返回 DOCX Artifact
14. 用户说“以后都按这个方法”
15. 保存为招投标 Skill / Plugin
16. 下一次可以复用
```

---

## 场景 B：财务分析

```text
1. 连接财务数据库
2. 用户上传补充 Excel
3. 输入分析需求
4. AI 读取 Schema
5. 查询数据库
6. Python 分析
7. 生成图表
8. 输出分析
9. 调用 PPT 程序
10. 返回 PPT URL / PPTX
11. 保存为财务经营分析 Plugin
```

---

## 场景 C：企业已有 API

```text
1. 管理员粘贴接口说明
2. AI 生成 ToolSpec
3. 管理员填写 Auth
4. 测试
5. 发布
6. 用户在 Chat 描述业务需求
7. AI 从 Knowledge/File/Data 获取信息
8. Parameter Compiler 生成 JSON
9. Validator 校验
10. Connector 调程序
11. 程序返回 JSON
12. Result Normalizer
13. Chat 展示结果
```

---

# 63. V1 产品成功指标建议

不要第一阶段只看“创建了多少插件”。

建议关注：

```text
Task Success Rate
```

一个真实任务成功完成的比例。

```text
Tool Success Rate
```

工具调用成功率。

```text
Repeat Success Rate
```

保存为 Plugin 后，再次执行是否仍然成功。

```text
Time To First Artifact
```

用户从描述需求到得到文件的时间。

```text
Plugin Reuse Rate
```

保存后的能力是否真的重复使用。

```text
Manual Correction Rate
```

执行过程中需要多少人工修正。

---

# 64. 最重要的开发目标

第一阶段产品绝对不要以：

> “我们支持很多 Agent、MCP 和 Workflow”

作为成功标准。

真正标准应该是：

> **用户能够把企业知识、临时文件、数据库和已有程序组合起来，通过自然语言完成一次真实业务任务，并把这次成功的方法稳定保存下来，下次继续使用。**

---

# 65. 最终核心对象

系统第一阶段围绕以下核心对象开发：

```text
Workspace
Resource
Skill
Connector
Plugin
Run
Artifact
```

其中：

```text
Resource
= 数据是什么

Skill
= 怎么做

Connector
= 能执行什么

Plugin
= 如何组合成一个业务能力

Run
= 实际发生了什么

Artifact
= 最终生成了什么

Workspace
= 谁拥有和可以访问这些东西
```

---

# 66. 最终推荐 V1 架构

```text
                         Web
                          │
                        Chat
                          │
           ┌──────────────┼──────────────┐
           │              │              │
          File         Knowledge       Plugin
           │              │              │
           └──────────────┼──────────────┘
                          ↓
                   Context Resolver
                          ↓
                        Skill
                          ↓
                    Agent Runtime
                          ↓
                Parameter Compiler
                          ↓
                Schema / Rule Validator
                          ↓
                     Tool Gateway
         ┌────────────────┼──────────────────┐
         │                │                  │
        MCP             HTTP             Database
         │                │                  │
         └────────────────┼──────────────────┘
                          │
                       Sandbox
                          │
                        Python
                          ↓
                 Result Normalizer
                          ↓
                       Artifact
                          ↓
                         Chat


---------------- Enterprise Foundation ----------------

Workspace
Basic Permission
Secret
Version
Run Trace
Audit

---------------- Future Reserved ----------------

Workflow
Plugin → Plugin
Agent → Agent
Approval
SSO / SCIM
DLP
ABAC
Evals
Marketplace
HA / DR
Browser Agent
```

---

# 67. 开发团队统一约束

在开发开始前必须确认：

1. 所有业务对象必须带 `workspace_id`。
2. Skill、Connector、Plugin 必须版本化。
3. Secret 只能保存引用。
4. Tool 必须统一走 Tool Gateway。
5. Tool 输入必须有 Schema。
6. Tool 输出必须经过 Result Normalizer。
7. Run 必须有 Step。
8. Artifact 必须绑定 Run。
9. File 和 Knowledge 不得混为同一概念。
10. Skill 和程序代码不得混为同一概念。
11. MCP 和 HTTP API 必须统一映射为 ToolSpec。
12. Plugin 必须是结构化业务能力，不得只是 Prompt。
13. AI 修改正式能力必须生成新 Draft Version。
14. 外部程序错误不得默认允许 AI 修改外部代码。
15. V1 不开发 Workflow UI，但 Runtime 必须支持未来转换。
16. 第一阶段数据库写操作默认禁止。
17. 任何 Connector 调用前都必须做权限和参数验证。
18. Chat 是统一入口，但配置必须有结构化管理页面，不能只存在聊天记录中。

---

# 68. 下一阶段建议

V1 稳定以后再进入：

## V1.5

- Batch Run
- Plugin 自动推荐
- 更复杂测试
- Artifact Chain
- Connector OpenAPI 自动导入
- 简单审批
- Plugin → Plugin
- Workflow Definition

## V2

- Workflow Builder
- Agent Builder
- Multi-Agent
- Enterprise SSO
- SCIM
- ABAC
- DLP
- Data Classification
- Plugin Registry
- Plugin Signing
- Evals
- Cost Center
- Private Deployment
- HA / DR
- Browser Agent

---

# 69. 一句话交付标准

第一阶段交付后，应能够演示：

> 用户在 Chat 中选择企业知识库，上传本次业务文件，通过自然语言描述需求；系统能够读取知识、分析文件、查询数据库或调用已有 API/MCP，根据接口 Schema 自动组织 JSON 参数，通过程序完成计算或文档/PPT 生成，在聊天中返回可继续使用的 Artifact；用户可以把这次成功的方法保存为 Skill，并进一步组合 Tool、Resource 形成 Plugin，供同一 Workspace 中其他用户重复使用。
