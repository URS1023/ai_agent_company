"""SDK snippets generated from the authenticated, enriched OpenAPI contract.

中文补充（SDK 代码生成）：
- 输入不是手写模板，而是平台"已鉴权、已补全"的 OpenAPI 契约
  （platform.openapi_contract 的 operation_index / schema_example），
  因此生成的客户端与服务端实际契约始终一致。
- 只生成"按 operationId 精确选取"的调用片段（不导出整个 API 面），
  未知 operationId 直接拒绝；语言支持 python / typescript 两种。
- 安全约束：生成的代码只含 URL 路径与参数示例，**不包含任何密钥或令牌**；
  调用方需自行传入访问令牌。生成过程不执行任何被生成的代码。
- 权限：system.development.view；本模块纯字符串拼装，无副作用。
"""
from __future__ import annotations

import json
import keyword
import re

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.errors import ValidationError
from app.modules.platform.openapi_contract import contract_metrics, operation_index, schema_example

from .schemas import ClientCodeInput


def _identifier(value: str) -> str:
    """把任意接口/参数名转成合法的 Python/TS 标识符。

    非单词字符替换为下划线；数字开头或与关键字冲突时加 ``call_`` 前缀，
    避免生成语法错误的代码片段。空结果回落为 ``call_api``。
    """
    result = re.sub(r"\W+", "_", value).strip("_") or "call_api"
    if result[0].isdigit() or keyword.iskeyword(result):
        result = "call_" + result
    return result


def _request_media_type(operation: dict) -> str | None:
    """取请求体媒体类型：优先 application/json，否则取契约中声明的那一种。

    一个操作可能声明多种请求表示，客户端示例只演示一种，因此这里固定"优先 JSON"，
    保证生成的示例与绝大多数调用方一致的写法。
    """
    content = operation.get("requestBody", {}).get("content", {})
    if not content:
        return None
    # Prefer the standard JSON contract when an operation advertises more than
    # one request representation; otherwise preserve the OpenAPI declaration.
    return "application/json" if "application/json" in content else next(iter(content))


def api_directory(ctx: AuthContext, schema: dict, *, keyword_text: str = "", tag: str = "") -> dict:
    """生成接口目录（按 operationId 索引，可按关键字与标签筛选）；权限 system.development.view。

    条目含 method/path/summary/请求与响应媒体类型及示例，供开发者工具页面检索；
    筛选在内存中完成（契约规模有限）。返回目录结构，不执行任何接口。
    """
    require_permission(ctx, "system.development.view")
    keyword_text = keyword_text.strip().lower()
    items = []
    for operation_id, item in operation_index(schema).items():
        operation = item["operation"]
        group = (operation.get("tags") or ["未分组接口"])[0]
        searchable = " ".join((operation_id, operation.get("summary", ""), operation.get("description", ""), item["path"])).lower()
        if tag and group != tag:
            continue
        if keyword_text and keyword_text not in searchable:
            continue
        request_content = operation.get("requestBody", {}).get("content", {})
        json_media = request_content.get("application/json", {})
        response_media = next((
            response.get("content", {}).get("application/json", {})
            for status, response in operation.get("responses", {}).items() if str(status).startswith("2")
        ), {})
        response_media_types = sorted({
            media_type
            for status, response in operation.get("responses", {}).items()
            if str(status).startswith("2")
            for media_type in response.get("content", {})
        })
        items.append({
            "operationId": operation_id,
            "method": item["method"].upper(),
            "path": item["path"],
            "tag": group,
            "summary": operation.get("summary", ""),
            "description": operation.get("description", ""),
            "requestExample": json_media.get("example"),
            "responseExample": response_media.get("example"),
            "requestMediaTypes": list(request_content),
            "responseMediaTypes": response_media_types,
        })
    tags = sorted({value["tag"] for value in items})
    return {"items": items, "total": len(items), "tags": tags, "quality": contract_metrics(schema)}


def _python_client(selected: list[dict], include_examples: bool) -> str:
    """拼装 Python（requests）客户端源码：每个 operationId 一个函数。

    include_examples 为真时在函数内附注释形式的请求示例；生成的代码不含令牌，
    认证由调用方传入。纯字符串拼装，不执行生成结果。
    """
    lines = [
        '"""Generated from the live Cloud Brain OpenAPI contract; secrets are supplied at runtime."""',
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "import httpx",
        "",
        "",
        "class CloudBrainClient:",
        "    def __init__(self, base_url: str, access_token: str, workspace_id: str, timeout: float = 30.0):",
        "        self.base_url = base_url.rstrip('/')",
        "        self.client = httpx.Client(timeout=timeout, headers={",
        "            'Authorization': 'Bearer ' + access_token,",
        "            'X-Workspace-Id': workspace_id,",
        "        })",
        "",
        "    def close(self) -> None:",
        "        self.client.close()",
        "",
        "    @staticmethod",
        "    def _decode_response(response: httpx.Response) -> Any:",
        "        if not response.content:",
        "            return None",
        "        content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()",
        "        if content_type == 'application/json' or content_type.endswith('+json'):",
        "            return response.json()",
        "        if content_type.startswith('text/') or content_type in {'application/xml', 'application/csv'}:",
        "            return response.text",
        "        return response.content",
    ]
    for item in selected:
        operation = item["operation"]
        name = _identifier(operation["operationId"])
        summary = operation.get("summary") or operation["operationId"]
        description = operation.get("description", "").replace('"""', "'''")
        request_example = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("example")
        request_media = _request_media_type(operation)
        lines.extend([
            "",
            f"    def {name}(self, *, path_params: dict[str, Any] | None = None,",
            "                 query: dict[str, Any] | None = None, body: Any = None,",
            "                 files: dict[str, Any] | None = None) -> Any:",
            f'        """{summary}。\n\n        {description}"""',
            f"        path = {item['path']!r}",
            "        for key, value in (path_params or {}).items():",
            "            path = path.replace('{' + key + '}', str(value))",
            "        if '{' in path:",
            "            raise ValueError('缺少路径参数: ' + path)",
        ])
        if request_media and request_media.startswith("multipart/"):
            lines.extend([
                "        response = self.client.request(",
                f"            {item['method'].upper()!r}, self.base_url + path, params=query or None,",
                "            data=body or {}, files=files or None)",
            ])
        elif request_media == "application/x-www-form-urlencoded":
            lines.extend([
                "        response = self.client.request(",
                f"            {item['method'].upper()!r}, self.base_url + path, params=query or None,",
                "            data=body, files=files or None)",
            ])
        elif request_media and request_media.startswith("text/"):
            lines.extend([
                "        response = self.client.request(",
                f"            {item['method'].upper()!r}, self.base_url + path, params=query or None,",
                "            content=body, files=files or None)",
            ])
        else:
            lines.extend([
                "        if files:",
                "            raise ValueError('该接口不接受 multipart 文件')",
                "        response = self.client.request(",
                f"            {item['method'].upper()!r}, self.base_url + path, params=query or None, json=body)",
            ])
        lines.extend([
            "        response.raise_for_status()",
            "        return self._decode_response(response)",
        ])
        if include_examples and request_example is not None:
            lines.extend([
                "",
                f"# Request example for {name}:",
                f"# {json.dumps(request_example, ensure_ascii=False, indent=2).replace(chr(10), chr(10) + '# ')}",
            ])
    return "\n".join(lines) + "\n"


def _typescript_client(selected: list[dict], include_examples: bool) -> str:
    """拼装 TypeScript 客户端类源码（baseUrl/token/workspaceId 由构造参数注入）。

    与 Python 版本一样只含路径与参数结构，不内嵌任何凭据。
    """
    lines = [
        "/** Generated from the live Cloud Brain OpenAPI contract. */",
        "export class CloudBrainClient {",
        "  constructor(private baseUrl: string, private token: string, private workspaceId: string) {}",
        "",
        "  private async request(method: string, path: string, query?: Record<string, unknown>, body?: unknown, files?: Record<string, Blob | File>, multipart = false): Promise<unknown> {",
        "    const url = new URL(this.baseUrl.replace(/\\/$/, '') + path);",
        "    Object.entries(query ?? {}).forEach(([key, value]) => value !== undefined && url.searchParams.set(key, String(value)));",
        "    const headers: Record<string, string> = { Authorization: `Bearer ${this.token}`, 'X-Workspace-Id': this.workspaceId };",
        "    let requestBody: BodyInit | undefined;",
        "    if (multipart || (files && Object.keys(files).length > 0)) {",
        "      const form = new FormData();",
        "      if (body && typeof body === 'object' && !(body instanceof Blob) && !(body instanceof FormData)) {",
        "        Object.entries(body as Record<string, unknown>).forEach(([key, value]) => {",
        "          if (value === undefined || value === null) return;",
        "          if (value instanceof Blob) form.append(key, value);",
        "          else if (Array.isArray(value)) value.forEach(item => form.append(key, String(item)));",
        "          else form.append(key, String(value));",
        "        });",
        "      }",
        "      Object.entries(files ?? {}).forEach(([key, value]) => form.append(key, value));",
        "      requestBody = form;",
        "    } else if (body !== undefined) {",
        "      headers['Content-Type'] = 'application/json';",
        "      requestBody = JSON.stringify(body);",
        "    }",
        "    const response = await fetch(url, {",
        "      method,",
        "      headers,",
        "      body: requestBody,",
        "    });",
        "    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);",
        "    if (response.status === 204 || response.headers.get('content-length') === '0') return undefined;",
        "    const contentType = (response.headers.get('content-type') ?? '').split(';', 1)[0].trim().toLowerCase();",
        "    if (contentType === 'application/json' || contentType.endsWith('+json')) return response.json();",
        "    if (contentType.startsWith('text/') || contentType === 'application/xml' || contentType === 'application/csv') return response.text();",
        "    return response.blob();",
        "  }",
    ]
    for item in selected:
        operation = item["operation"]
        name = _identifier(operation["operationId"])
        summary = (operation.get("summary") or operation["operationId"]).replace("*/", "* /")
        request_example = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("example")
        request_media = _request_media_type(operation)
        lines.extend([
            "",
            f"  /** {summary} */",
            f"  async {name}(options: {{ path?: Record<string, string | number>; query?: Record<string, unknown>; body?: unknown; files?: Record<string, Blob | File> }} = {{}}): Promise<unknown> {{",
            f"    let path = {json.dumps(item['path'], ensure_ascii=False)};",
            "    Object.entries(options.path ?? {}).forEach(([key, value]) => { path = path.replace(`{${key}}`, String(value)); });",
            "    if (path.includes('{')) throw new Error(`缺少路径参数: ${path}`);",
            f"    return this.request({json.dumps(item['method'].upper())}, path, options.query, options.body, options.files, {str(bool(request_media and request_media.startswith('multipart/'))).lower()});",
            "  }",
        ])
        if include_examples and request_example is not None:
            lines.extend(["", f"  // {name} request example: {json.dumps(request_example, ensure_ascii=False)}"])
    lines.append("}")
    return "\n".join(lines) + "\n"


def generate_client(ctx: AuthContext, schema: dict, payload: ClientCodeInput) -> tuple[str, str]:
    """按 operationId 生成客户端代码，返回 (建议文件名, 代码内容)。

    参数：``payload.language`` 为 python / typescript；``payload.include_examples``
    决定是否附带请求示例注释。权限 system.development.view。
    异常：存在未登记的 operationId 抛 ValidationError（不生成半截代码）。
    生成的代码不含凭据，调用方需自行注入访问令牌。
    """
    require_permission(ctx, "system.development.view")
    index = operation_index(schema)
    missing = [item for item in payload.operation_ids if item not in index]
    if missing:
        raise ValidationError("存在未登记的 operationId", operation_ids=missing)
    selected = [index[item] for item in payload.operation_ids]
    if payload.language == "python":
        return "cloud_brain_client.py", _python_client(selected, payload.include_examples)
    return "cloud-brain-client.ts", _typescript_client(selected, payload.include_examples)
