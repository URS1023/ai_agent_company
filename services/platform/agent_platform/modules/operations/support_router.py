"""qKnow系统辅助功能在平台上的受控入口；接口目录只读，代码导出不执行。"""
import json
from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from ...core.context import get_actor, require_permission
from ...core.database import get_session
from .support_schemas import SupportInput, SupportPatch, SupportRestore, FormValidation
from .support_service import SupportService, view

router = APIRouter(prefix="/operations", tags=["系统辅助配置"])


def resource_router(kind, plural):
    """资源种类在闭包中冻结，不能通过查询参数改变权限或表单类型。"""
    routes = APIRouter(prefix="/" + plural)

    @routes.get("")
    def listing(q: str = "", enabled: bool | None = None, scope: str | None = None, limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0), session=Depends(get_session), actor=Depends(require_permission("m16.read"))):
        return SupportService(session, actor).list(kind, q, enabled, scope, limit, offset)

    @routes.post("", status_code=201)
    def create(body: SupportInput, session=Depends(get_session), actor=Depends(require_permission("m16.admin"))):
        return SupportService(session, actor).create(kind, body.model_dump())

    @routes.get("/{id}")
    def detail(id: str, session=Depends(get_session), actor=Depends(require_permission("m16.read"))):
        return view(SupportService(session, actor).get(kind, id))

    @routes.patch("/{id}")
    def patch(id: str, body: SupportPatch, session=Depends(get_session), actor=Depends(require_permission("m16.admin"))):
        return SupportService(session, actor).patch(kind, id, body.model_dump(exclude_unset=True))

    @routes.delete("/{id}")
    def remove(id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission("m16.admin"))):
        return SupportService(session, actor).patch(kind, id, {"revision": revision, "deleted": True})

    @routes.get("/{id}/revisions")
    def revisions(id: str, session=Depends(get_session), actor=Depends(require_permission("m16.read"))):
        return SupportService(session, actor).revisions(kind, id)

    @routes.post("/{id}/restorations")
    def restore(id: str, body: SupportRestore, session=Depends(get_session), actor=Depends(require_permission("m16.admin"))):
        return SupportService(session, actor).restore(kind, id, body.model_dump())

    return routes


for kind, plural in (("dictionary", "dictionaries"), ("parameter", "parameters"), ("announcement", "announcements"), ("form", "forms")):
    router.include_router(resource_router(kind, plural))


@router.get("/dictionary-options/{code}")
def options(code: str, session=Depends(get_session), actor=Depends(get_actor)):
    return SupportService(session, actor).dictionary_options(code)


@router.get("/announcement-feed")
def announcements(session=Depends(get_session), actor=Depends(get_actor)):
    return SupportService(session, actor).announcements()


@router.get("/forms/{id}/schema")
def schema(id: str, session=Depends(get_session), actor=Depends(require_permission("m16.read"))):
    return SupportService(session, actor).schema(id)


@router.post("/forms/{id}/validations")
def validate(id: str, body: FormValidation, session=Depends(get_session), actor=Depends(require_permission("m16.read"))):
    return SupportService(session, actor).validate(id, body.model_dump())


@router.get("/developer-tools/openapi")
def api_directory(request: Request, actor=Depends(require_permission("m16.read"))):
    return request.app.openapi()


@router.get("/forms/{id}/scaffold")
def scaffold(id: str, format: Literal["vue", "typescript"] = "vue", session=Depends(get_session), actor=Depends(require_permission("m16.read"))):
    resource = view(SupportService(session, actor).get("form", id))
    schema = SupportService(session, actor).schema(id)
    encoded = json.dumps(schema, ensure_ascii=False, indent=2).replace("<", "\\u003c")
    if format == "typescript":
        fields = ["  " + json.dumps(key) + ("" if key in schema["required"] else "?") + ": " + {"number": "number", "integer": "number", "boolean": "boolean"}.get(field["type"], "string") + ";" for key, field in schema["properties"].items()]
        content = "/** 根据已保存表单生成的输入类型；业务提交接口由调用方显式接入。 */\nexport interface FormValues {\n" + "\n".join(fields) + "\n}\n"
    else:
        content = '<script setup lang="ts">\nimport { reactive } from "vue";\nconst schema: Record<string, any> = ' + encoded + ';\nconst values = reactive<Record<string, any>>({});\n</script>\n<template>\n  <el-form label-position="top" @submit.prevent>\n    <el-form-item v-for="(field, key) in schema.properties" :key="key" :label="field.title" :required="schema.required.includes(key)">\n      <el-select v-if="field.enum" v-model="values[key]"><el-option v-for="choice in field[\'x-options\']" :key="choice.value" :label="choice.label" :value="choice.value" /></el-select>\n      <el-switch v-else-if="field.type === \'boolean\'" v-model="values[key]" />\n      <el-date-picker v-else-if="field.format === \'date\'" v-model="values[key]" type="date" value-format="YYYY-MM-DD" />\n      <el-input-number v-else-if="[\'number\',\'integer\'].includes(field.type)" v-model="values[key]" :min="field.minimum" :max="field.maximum" :precision="field.type === \'integer\' ? 0 : undefined" />\n      <el-input v-else v-model="values[key]" :type="field[\'x-control\'] === \'textarea\' ? \'textarea\' : \'text\'" :maxlength="field.maxLength" />\n    </el-form-item>\n  </el-form>\n</template>\n'
    return Response(content, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{resource["code"]}.{ "vue" if format == "vue" else "ts"}"'})
