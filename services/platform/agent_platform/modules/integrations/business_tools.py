"""把已有业务接口投影为原生引擎可导入的工具契约，不复制业务查询与权限逻辑。"""

from copy import deepcopy
from typing import Any

from agent_platform.core.context import Actor


# 只登记读取平台已保存结果的接口；取企业库、执行检查或发布报告继续走明确业务操作。
READ_TOOLS = {
    "/api/v1/devices": ("m10.read", "list_devices", "查找设备"),
    "/api/v1/devices/{id}": ("m10.read", "get_device", "读取设备资料"),
    "/api/v1/maintenance-analyses": ("m10.read", "list_maintenance_analyses", "查找维护分析"),
    "/api/v1/maintenance-analyses/{id}": ("m10.read", "get_maintenance_analysis", "读取维护分析及证据"),
    "/api/v1/incidents": ("m10.read", "list_incidents", "查找设备预警"),
    "/api/v1/incidents/{id}": ("m10.read", "get_incident", "读取设备预警及处置"),
    "/api/v1/products": ("m11.read", "list_products", "查找产品"),
    "/api/v1/product-batches": ("m11.read", "list_product_batches", "查找产品批次"),
    "/api/v1/product-batches/{id}": ("m11.read", "get_product_batch", "读取产品批次"),
    "/api/v1/inspections": ("m11.read", "list_inspections", "查找设备或产品检查"),
    "/api/v1/inspections/{id}": ("m11.read", "get_inspection", "读取检查任务"),
    "/api/v1/inspections/{id}/results": ("m11.read", "get_inspection_result", "读取检查结果与复核状态"),
    "/api/v1/inspections/{id}/runs": ("m11.read", "list_inspection_runs", "读取检查历史判定"),
    "/api/v1/reports": ("m07.read", "list_reports", "查找报告"),
    "/api/v1/reports/{id}": ("m07.read", "get_report", "读取报告及数据来源"),
}


def business_tool_spec(source: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """按调用者当前模块权限导出白名单；真实调用仍由原接口核验工作区和资源权限。"""
    actor.require("m02.read")
    paths: dict[str, Any] = {}
    for path, (permission, operation_id, title) in READ_TOOLS.items():
        original = source.get("paths", {}).get(path, {}).get("get")
        if not actor.can(permission) or original is None:
            continue
        operation = deepcopy(original)
        operation.update(
            operationId=operation_id, summary=title, tags=["制造业务"],
            security=[{"PlatformToken": []}],
        )
        paths[path] = {"get": operation}

    # 保留契约真正引用的Schema，避免把整个平台数百个管理接口交给模型。
    available = source.get("components", {}).get("schemas", {})
    schemas: dict[str, Any] = {}

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            prefix = "#/components/schemas/"
            if isinstance(ref, str) and ref.startswith(prefix):
                name = ref[len(prefix):]
                if name not in schemas and name in available:
                    schemas[name] = deepcopy(available[name])
                    collect(schemas[name])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(paths)
    return {
        "openapi": source.get("openapi", "3.1.0"),
        "info": {"title": "制造业务查询", "version": "1.0.0"},
        "servers": [{"url": "/"}],
        "paths": paths,
        "components": {
            "schemas": schemas,
            "securitySchemes": {
                "PlatformToken": {"type": "http", "scheme": "bearer"},
            },
        },
    }
