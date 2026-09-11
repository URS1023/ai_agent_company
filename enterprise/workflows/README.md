# 默认设备工作流

`default-alert.yml` 与 `default-quality.yml` 是可在 Dify 中导入和继续编辑的 DSL 资产。结构为开始 → 企业设备评估 → 返回结果，结束节点的 `result` 原样引用评估节点的 `text`，不经模型改写。两个模板的业务区别由企业运行所绑定的场景及不可变规则版本决定；模板名称不构成权限或场景检查。

模板没有嵌入数据库密码、插件签名密钥、工作区身份或伪造的凭据 UUID，也不包含 SQL/HTTP/代码节点。默认无凭据，导入本身不表示可运行。实际使用需：

1. 安装现有企业设备评估插件，在原生工具节点中选择该工作区的插件凭据。
2. 发布工作流，记录真实发布版本 UUID；在原生管理配置中登记工作区、应用、版本、`assessment` 节点与所选凭据的精确组合。
3. 配置对应的数据源、规则版本和企业设备绑定，再从企业运行入口派发。编辑器试运行没有受管执行上下文，不替代企业联调。

详细运行协议沿用 `../MANAGED-EXECUTION.md`。一键安装/发布/绑定向导仍需继续制作。

## 生成与校验

`node enterprise/workflows/write-defaults.mjs` 重新生成两个文件；JSON 语法是原生 YAML 解析器接受的子集。`node --test enterprise/workflows/default-workflows.test.mjs` 校验结构及逐字节一致性。`test_native_defaults.py` 使用原生 API 的 graphon/PyYAML 环境检验真实节点模型，不启动任何服务、不创建应用。

`buildDefaultWorkflow(scenario, { credentialId })` 可供后续受控创建流程填入已选择的规范非零 UUID；省略时保持未配置状态，不猜测或沿用其他应用的凭据。
