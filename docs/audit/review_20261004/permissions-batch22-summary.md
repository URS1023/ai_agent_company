# 大版本更新批次二十二：COV-01 文件消费权限浏览器补证

基线 `d40f0e6bfb2faa4fa984dd9f9113e97244a17ce0`，分支 `codex/major-permission-consumers`。只改新增 `tests/ui/full_browser_permissions_acceptance.js` 与 runner 的 `permissions` suite 注册，保留未提交状态。没有修改产品代码、共享浏览器守卫、网络报告 writer 或进程清理实现。

## 实际覆盖

- 管理员与成员位于独立真实 Edge BrowserContext；管理员通过可见控件创建 `self` 数据范围角色和成员，成员完成临时密码强制修改及重新登录。断言成员只有 `home.view/files.view/files.export` 三个功能权限。
- 管理员通过可见控件创建并测试受控 embedding 模型、HTTP 设备连接器，各产生一次真实回环请求。成员的模型/设备调用直接请求均为 403；整个后续消费、拒绝和撤权阶段两种实际请求均没有新增。
- 管理员上传仅本轮使用的合成文本文件。成员授权前列表隐藏，直接预览/下载均 404；管理员通过资源授权 UI 授予该文件 `view/export` 后，成员通过真实按钮预览正文和下载原始字节，校验全文及 SHA256。
- 只读成员没有上传、修改、复制、删除控件；对已授权文件的直接 PATCH 为 403，末尾确认管理员原文件字节未变。
- 管理员通过 UI 创建第二工作区并复制成员。原授权仍有效时，第二工作区成员携带明确 `X-Workspace-Id` 的真实浏览器请求无法读取第一工作区文件 ID/原下载地址（404）；管理员真实 UI 切换到第二工作区，同一下载地址也为 404。随后原工作区同身份同文件仍为 200，避免以撤权的 404 冒充租户隔离。
- 管理员通过 UI 撤权，成员保持授权时打开的旧文件页面，再次点击原预览/下载控件均 404；原下载 URL 的认证请求和仅使用内容 Cookie 的请求均 404；刷新页面后文件消失。

共 6 组业务检查、14 个明确拒绝观察。结果中用 `visible_ui`、`visible_stale_control`、`browser_same_origin_request` 等标记分开记录。预览控件还会同时请求 parse-events，其拒绝产生额外一条浏览器控制台 404，未将控制台错误数量当作断言数量。

## 两轮记录

统一执行命令：MAIN `.venv_py312/Scripts/python.exe tests/dsh/business_browser_acceptance.py --suites permissions --iterations 1 --case-timeout 300`。每轮只执行新套一次，首失败即停止，未扩大观察预算。UTF8、绝对 TEST5_E2E_PYTHON 与 Git longpaths 环境按授权设置。

| 轮次 | manifest（相对此工作树） | 结果 | 用时 | 网络证据 | 进程清理 |
|---|---|---|---:|---|---|
| 首轮 | `.implementation/full-business-browser/20261004-145727-a960d44e/manifest.json` | 5 组通过后，测试误用确认按钮文本“撤销”，实际为“撤销授权”；撤权尚未执行 | 51.422 秒 | allowed 207，background blocked 84，unexpected/application_blocked 均 0 | Windows Job active_after=0 |
| 定点复验 | `.implementation/full-business-browser/20261004-145847-5b23dc3e/manifest.json` | 仅修正上述一行文本后，6 组全部通过 | 48.860 秒 | allowed 221，background blocked 90，unexpected/application_blocked 均 0 | Windows Job active_after=0 |

每轮 `001-permissions` 中保留 `browser.log`、`network.json`、`host-diagnostics.log`、临时平台和 `permissions/result.json`。第二轮还保留实际下载文本、授权预览截图和撤权后旧页面截图。背景网络候选已阻断，不能描述为完全没有浏览器背景联网尝试。网络观察器与上游错误均未导致清理失败。

## 边界

这是文件单资源消费权限的最小真实浏览器闭环，不代表 COV-01 所有资源类型完成。没有验证知识召回、Plugin/Skill 成员复用、数据源执行、只读报表设计器、全部角色组合、移动端或 100 轮稳定性。成员第二工作区验证使用浏览器直接请求显式工作区头，不能描述为成员通过工作区切换 UI 操作。撤权阻止后续服务器读取，不能抹除之前已合法下载到本机的字节。

本轮仅临时 SQLite、合成文件、Node 当前进程绑定的受控回环桩；未读取 dotenv 内容、未连接真实业务库/模型或在用 8011，未更改业务绑定。源码哈希及每轮 manifest/result 哈希完整记录在 `.implementation/permissions-batch22-evidence.json`。

最终两个源码 SHA256：

- `tests/ui/full_browser_permissions_acceptance.js`：`F360335FBC83647371868A2C8E8A0F0FE92D8D370846AEE6BAD8B0D43B381DFF`
- `tests/dsh/business_browser_acceptance.py`：`8EA80C5C289EDAF83A3729C5E66CEF7C05B892F1953A740BD0ED338E1225CD05`
