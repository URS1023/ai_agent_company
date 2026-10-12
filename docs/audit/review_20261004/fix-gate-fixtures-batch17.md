# 十七批门禁首轮失败与可复跑夹具修正

2026-10-04。本记录不增加产品修复数量。首轮统一门禁在第二阶段结束：**568 passed、2 failed，216.60秒**，之后17阶段未运行。原始manifest、日志SHA及13文件冻结见 [首轮失败证据](validation-batch17-first-failed.json)，未覆写或跳过失败。

## 两个独立原因

1. `test_business_worker.py` 的旧停止测试把 `view_device_alerts` 当成新取数。本批真实DSH分支只读取已经保存的预警，已有25项专项及独立复核确认其权限与来源边界。修正夹具增加真正需要新取数的 `query_inventory_data`，保留动态数据库、新取数都拒绝，并明确检查已保存预警和本地时间仍可执行及精确调用次数；没有删掉停止断言、修改产品或放宽权限。
2. `test_presentation_partial_batches.py` 的旧OpenWebUI schema检查读取本机ignored `services/openwebui/upstream/`，新工作树不存在该目录。检查 Git 后确认旧源码没有已跟踪归档，DSH安装也不会恢复它；仅在本机挂目录会让新克隆仍失败。因此将唯一所需的固定完整 `utils/tools.py` 原字节保存为测试样本，连同三份许可证保留来源，不能据此称完整旧引擎被纳入仓库。

## 固定原文件及实际断言

样本位于 `tests/fixtures/openwebui-0.11.4/`，来自MAIN固定快照，原文件69826字节。复制前按既有 `services/openwebui/UPSTREAM.json` 核验，文件SHA256为 `ae4fc82448f914863a770bb105591f5c161a372b16bcbf40014f22b28c8bc9b9`；与十六批实际使用的源文件相同。LICENSE、LICENSE_HISTORY、LICENSE_NOTICE也按原清单核验并二进制复制，未改第三方代码或许可证。

局部 `.gitattributes` 对这四份原文件逐一设为 `-text`，实际 `git check-attr` 均为text unset，防止Windows检出转换CRLF而破坏证据字节。测试读取原始bytes并先校验摘要，再解码、解析AST，只接受且执行原来的两个函数 `clean_properties`、`clean_openai_tool_schema`；不import整模块，不安装/启动OpenWebUI，不额外连接模型或源。

检查重命名为 `test_legacy_openwebui_schema_keeps_complete_body_shapes`，原来的PPT正文items/chart/table/left/right、网关验证和旧客户端传递断言保留。它明确只证明固定0.11.4的历史兼容性，不能替代当前DSH完整嵌套schema传输验证。`services/openwebui/README.md` 增加历史状态说明，旧迁移记录及验证数字仍保留；当前生产入口以DSH为准。

## 针对验证及后续完整重验

通过既有隔离runner，两个失败点各运行一次，**2 passed、0 failed/errors/skips，JUnit记录2.846秒**。本树仍没有ignored OpenWebUI upstream目录；检查通过依赖的是已跟踪样本。原始输出和XML位于 `.implementation/batch17-harness-focused.log/.xml`。没有实际业务源、外部模型或8011操作。

原13份产品/专项/门禁字节与88份前端文件保持不变。第二门禁冻结扩为21文件，仅增加这两个旧测试和六份固定样本/说明/属性文件；首版冻结另存 `.implementation/batch17-frozen-files-first.json`。第一次固定源码九域浏览器启动在 `b7241b6` 干净工作树，运行期间这些变化仅涉及不会被浏览器执行的后端pytest/历史样本与文档，所有产品和浏览器脚本保持原字节。最终浏览器摘要会保留原启动提交及候选差异，不声称整棵工作树从开始到结束都没有任何变化。

完整19阶段第二轮结果另记；不能用这两项专项替代完整门禁，也不将首轮568项与后续结果相加。

## 独立九域入口遗漏解释器

首轮完整浏览器八域通过，AI文档域在首份PPT生成后的独立检查入口停止：没有 `TEST5_E2E_PYTHON`，而新树也没有本地 `.venv_py312`。原脚本严格拒绝找不到指定解释器，未用其他Python或跳过文件检查；因此整轮仍是失败。九套最终活动后代、未知/未授权应用网络请求均为0，原始证据见 [首轮浏览器失败](browser-validation-batch17-first-failed.json)。

根因是单独执行 `business_browser_acceptance.py` 时只转发调用者环境；统一门禁会设置该变量，独立入口本身没有。修正仅在传给Node的环境中明确使用当前 `sys.executable` 的绝对路径，不创建虚拟环境链接、不放宽文件断言、不增加预算。AST检查通过，随后以干净提交重跑完整九域；浏览器冻结22文件（原门禁21份另加入口文件），产品及88份前端字节不变。正在执行的第二统一门禁仍使用其原21份冻结记录，此独立九域入口不属于门禁的执行入口。
