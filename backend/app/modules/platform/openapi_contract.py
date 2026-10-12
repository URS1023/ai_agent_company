"""One executable OpenAPI contract for documentation, collections and SDK snippets.

The contract is generated from the routes that are actually mounted.  Missing
human descriptions and examples are completed deterministically, so the API
center never drifts into a second, hand-maintained interface inventory.
"""
from __future__ import annotations

import copy
import json
from collections import defaultdict
from typing import Any

from fastapi.openapi.utils import get_openapi


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _resolve(schema: dict, root: dict, seen: set[str] | None = None) -> dict:
    """Resolve local component references only; external references stay opaque."""
    seen = seen or set()
    reference = schema.get("$ref", "") if isinstance(schema, dict) else ""
    if not reference.startswith("#/components/schemas/") or reference in seen:
        return schema
    name = reference.rsplit("/", 1)[-1]
    target = root.get("components", {}).get("schemas", {}).get(name, {})
    return _resolve(target, root, seen | {reference})


def schema_example(schema: dict | None, root: dict, depth: int = 0) -> Any:
    """Build a bounded JSON-compatible example from an OpenAPI 3.1 schema."""
    if not schema or depth > 12:
        return None
    schema = _resolve(schema, root)
    if "example" in schema:
        return copy.deepcopy(schema["example"])
    examples = schema.get("examples")
    if isinstance(examples, list) and examples:
        return copy.deepcopy(examples[0])
    if "default" in schema and schema["default"] is not None:
        return copy.deepcopy(schema["default"])
    if schema.get("enum"):
        return copy.deepcopy(schema["enum"][0])
    if schema.get("const") is not None:
        return copy.deepcopy(schema["const"])
    for keyword in ("oneOf", "anyOf"):
        choices = schema.get(keyword) or []
        for choice in choices:
            resolved = _resolve(choice, root)
            if resolved.get("type") != "null":
                value = schema_example(resolved, root, depth + 1)
                if value is not None:
                    return value
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "string")
    if schema_type == "object" or schema.get("properties"):
        result = {}
        for name, child in schema.get("properties", {}).items():
            if child.get("readOnly"):
                continue
            value = schema_example(child, root, depth + 1)
            if value is not None:
                result[name] = value
        if not result and schema.get("additionalProperties") is True:
            return {"key": "value"}
        return result
    if schema_type == "array":
        value = schema_example(schema.get("items", {}), root, depth + 1)
        return [] if value is None else [value]
    if schema_type == "boolean":
        return True
    if schema_type == "integer":
        return int(schema.get("minimum", 1))
    if schema_type == "number":
        return float(schema.get("minimum", 1.0))
    if schema_type == "string" or not schema_type:
        fmt = schema.get("format", "")
        if fmt == "date-time":
            return "2026-09-01T09:00:00Z"
        if fmt == "date":
            return "2026-09-01"
        if fmt == "email":
            return "operator@example.invalid"
        if fmt in {"uri", "url"}:
            return "https://example.invalid/resource"
        if fmt == "binary":
            return None
        title = str(schema.get("title") or "value")
        lowered = title.lower()
        if "idempotency" in lowered:
            return "request-20260901-0001"
        if lowered.endswith("id") or "identifier" in lowered:
            return "01TESTRESOURCE0000000000000"
        minimum = max(1, int(schema.get("minLength", 1)))
        value = str(schema.get("description") or title).split("；", 1)[0][:80].strip()
        if not value:
            value = "示例值"
        return value if len(value) >= minimum else value + "x" * (minimum - len(value))
    return None


def _fallback_description(method: str, path: str, operation: dict) -> str:
    summary = (operation.get("summary") or operation.get("operationId") or path).rstrip("。")
    behavior = {
        "get": "返回当前工作区和当前账号权限范围内的数据，不公开其他工作区记录或敏感凭据。",
        "post": "服务端严格校验请求字段和业务权限；创建或执行结果持久化，并为关键变更保留审计记录。",
        "put": "按完整配置更新已授权资源；并发版本不一致时返回 409，不静默覆盖其他修改。",
        "patch": "仅修改请求中明确提交的字段；跨工作区标识按不存在处理，并保留变更审计。",
        "delete": "只处理当前工作区内已授权资源；存在业务引用或版本冲突时拒绝删除。",
    }.get(method, "按当前工作区权限执行，并返回可追踪的结构化结果。")
    return f"该接口用于{summary}。{behavior}字段错误返回 422，未登录返回 401，无权限返回 403。"


def enrich_openapi(schema: dict) -> dict:
    """Mutate and return a generated schema with complete descriptions/examples."""
    schema.setdefault("info", {}).setdefault(
        "description",
        "智慧云脑 Cloud Brain 真实运行接口。所有业务请求按工作区、功能权限与数据范围隔离。",
    )
    tag_names: set[str] = set()
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            tag_names.update(operation.get("tags") or ["未分组接口"])
            if not str(operation.get("description") or "").strip():
                operation["description"] = _fallback_description(method, path, operation)
            operation.setdefault("x-workspace-isolation", True)
            for parameter in operation.get("parameters", []):
                parameter.setdefault("description", f"{parameter.get('name', '参数')}，按接口约束填写")
                if "example" not in parameter:
                    value = schema_example(parameter.get("schema", {}), schema)
                    if value is not None:
                        parameter["example"] = value
            request_body = operation.get("requestBody", {})
            for media_type, media in request_body.get("content", {}).items():
                if "example" not in media and "examples" not in media:
                    value = schema_example(media.get("schema", {}), schema)
                    if value is not None:
                        media["example"] = value
            for status, response in operation.get("responses", {}).items():
                response.setdefault("description", "操作成功" if str(status).startswith("2") else "请求未完成")
                for media_type, media in response.get("content", {}).items():
                    if "example" not in media and "examples" not in media:
                        value = schema_example(media.get("schema", {}), schema)
                        if value is not None:
                            media["example"] = value
    existing_tags = {item.get("name") for item in schema.get("tags", [])}
    for name in sorted(tag_names - existing_tags):
        schema.setdefault("tags", []).append({
            "name": name,
            "description": f"{name}相关接口；所有业务数据均执行工作区隔离和权限校验。",
        })
    schema["x-contract-quality"] = contract_metrics(schema)
    return schema


def contract_metrics(schema: dict) -> dict:
    operations = [
        operation
        for item in schema.get("paths", {}).values()
        for method, operation in item.items()
        if method in HTTP_METHODS and isinstance(operation, dict)
    ]
    request_bodies = [operation["requestBody"] for operation in operations if operation.get("requestBody")]
    bodies_with_examples = sum(
        1 for body in request_bodies
        if any("example" in media or "examples" in media for media in body.get("content", {}).values())
    )
    operation_ids = [item.get("operationId", "") for item in operations]
    return {
        "paths": len(schema.get("paths", {})),
        "operations": len(operations),
        "describedOperations": sum(bool(str(item.get("description") or "").strip()) for item in operations),
        "requestBodies": len(request_bodies),
        "requestBodiesWithExamples": bodies_with_examples,
        "uniqueOperationIds": len(operation_ids) == len(set(operation_ids)) and all(operation_ids),
    }


def install_openapi_contract(app) -> None:
    """Install the enriched schema after every business router is registered."""
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        generated = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        app.openapi_schema = enrich_openapi(generated)
        return app.openapi_schema

    app.openapi_schema = None
    app.openapi = custom_openapi


def operation_index(schema: dict) -> dict[str, dict]:
    result = {}
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method in HTTP_METHODS and isinstance(operation, dict) and operation.get("operationId"):
                result[operation["operationId"]] = {"path": path, "method": method, "operation": operation}
    return result


def postman_collection(schema: dict, *, base_url: str = "http://127.0.0.1:8000") -> dict:
    """Generate a runnable Postman 2.1 collection from the same schema."""
    groups: dict[str, list] = defaultdict(list)
    for operation_id, item in operation_index(schema).items():
        path, method, operation = item["path"], item["method"], item["operation"]
        group = (operation.get("tags") or ["未分组接口"])[0]
        request_spec: dict[str, Any] = {
            "method": method.upper(),
            "header": [{"key": "X-Workspace-Id", "value": "{{workspace_id}}", "type": "text"}],
            "url": "{{base_url}}" + path,
            "description": operation["description"],
        }
        parameters = operation.get("parameters", [])
        query = []
        for parameter in parameters:
            value = parameter.get("example")
            if parameter.get("in") == "query":
                query.append({
                    "key": parameter["name"], "value": "" if value is None else str(value),
                    "description": parameter.get("description", ""),
                    "disabled": not bool(parameter.get("required")),
                })
            elif parameter.get("in") == "path":
                request_spec["url"] = request_spec["url"].replace(
                    "{" + parameter["name"] + "}", "{{" + parameter["name"] + "}}",
                )
        if query:
            request_spec["url"] = {"raw": request_spec["url"], "query": query}
        content = operation.get("requestBody", {}).get("content", {})
        json_media = content.get("application/json")
        if json_media:
            example = json_media.get("example")
            request_spec["header"].append({"key": "Content-Type", "value": "application/json", "type": "text"})
            request_spec["body"] = {
                "mode": "raw", "raw": json.dumps(example if example is not None else {}, ensure_ascii=False, indent=2),
                "options": {"raw": {"language": "json"}},
            }
        multipart = content.get("multipart/form-data")
        if multipart:
            definition = _resolve(multipart.get("schema", {}), schema)
            fields = []
            for name, field in definition.get("properties", {}).items():
                resolved = _resolve(field, schema)
                variants = resolved.get("anyOf", [resolved])
                binary = any(
                    child.get("format") == "binary" or child.get("items", {}).get("format") == "binary"
                    for child in variants
                )
                entry = {"key": name, "type": "file" if binary else "text", "description": field.get("description", "")}
                entry["src" if binary else "value"] = "" if binary else str(schema_example(field, schema) or "")
                fields.append(entry)
            request_spec["body"] = {"mode": "formdata", "formdata": fields}
        groups[group].append({
            "name": operation.get("summary") or operation_id,
            "request": request_spec,
            "response": [],
        })
    return {
        "info": {
            "name": "智慧云脑 Cloud Brain 业务接口",
            "description": schema.get("info", {}).get("description", ""),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{access_token}}", "type": "string"}]},
        "variable": [
            {"key": "base_url", "value": base_url},
            {"key": "workspace_id", "value": ""},
            {"key": "access_token", "value": ""},
        ],
        "item": [{"name": name, "item": items} for name, items in sorted(groups.items())],
    }
