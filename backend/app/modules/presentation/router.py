"""PPT 演示模板中心的 HTTP 入口（``/api/presentation-templates``）。

职责：暴露内置/自定义 PPT 模板目录、参考资源清单与图标检索，以及自定义模板的上传、预览、
示例下载与删除。上游依赖 ``presentation.service``（目录与自定义模板持久化）和
``presentation.resource_library``（懒加载资源清单），并复用 ``common.deps`` 的鉴权上下文与
``common.audit`` 审计。

路由与权限：读接口用 ``get_current_context``；预览图与示例文件用 ``get_content_auth``，因为它
们会被 <img>/下载链接直接访问，需要允许内容级凭据。上传与删除会写入审计事件（模板上传、
模板删除），删除只移除自定义模板记录，不影响引用过的历史文档。

约束：本模块不做模板内容校验，传入的 PPTX 由 service 层解析；``default_id`` 只是前端首屏
的展示建议，**不代表后端会为缺失选项目回退默认模板**。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File as FileForm, Form, UploadFile, Response

from ...common.audit import audit
from ...common.deps import AuthContext, get_content_auth, get_current_context
from .service import (
    delete_custom_template,
    list_templates,
    read_template_example,
    read_template_preview,
    save_custom_template,
)
from .resource_library import resource_inventory, search_icons

router = APIRouter(prefix="/api/presentation-templates", tags=["presentation"])


@router.get("")
def templates(ctx: AuthContext = Depends(get_current_context)):
    """列出当前工作区可见的 PPT 模板（内置目录 + 自定义模板）。

    ``GET /api/presentation-templates``，需登录上下文。返回 ``items`` 与 ``count``；
    ``default_id`` 仅供前端首屏展示偏好，客户端收到空列表时不得据此假定模板存在。
    """
    items = list_templates(ctx.db, ctx.workspace_id)
    return {"items": items, "count": len(items), "default_id": "executive-ivory"}


@router.get("/resources")
def resources(ctx: AuthContext = Depends(get_current_context)):
    """返回模板可直接引用的参考资源清单（``GET /api/presentation-templates/resources``）。

    资源按需懒加载，缺失的可选适配器只报告为不可用，不视为错误。
    """
    return resource_inventory()


@router.get("/resources/icons")
def icons(
    library: str,
    query: str,
    limit: int = 40,
    ctx: AuthContext = Depends(get_current_context),
):
    """在指定图标库内检索图标（``GET /api/presentation-templates/resources/icons``）。

    参数：``library`` 图标库名（必填，未知库返回空结果而非抛错）、``query`` 关键词、
    ``limit`` 返回条数上限。无副作用。
    """
    items = search_icons(library, query, limit)
    return {"items": items, "count": len(items)}


@router.post("/upload", status_code=201)
async def upload_template(
    file: UploadFile = FileForm(...),
    name: str = Form(default=""),
    ctx: AuthContext = Depends(get_current_context),
):
    """上传 PPTX 作为工作区自定义模板（``POST /api/presentation-templates/upload``，201）。

    唯一允许写入本模块模板的入口；解析与落盘由 ``save_custom_template`` 负责，``name``
    为空时用文件名。成功后会写两条审计所需的提交（模板记录 + 上传事件），因此返回前
    必须 commit。解析失败的 PPTX 由 service 层抛业务异常，不产生半成品模板。
    """
    data = await file.read()
    item = save_custom_template(
        ctx.db, workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        filename=file.filename or "template.pptx",
        mime_type=file.content_type or "application/octet-stream",
        data=data, display_name=name,
    )
    ctx.db.commit()
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="presentation_template.upload", resource_type="file",
          resource_id=item["file_id"])
    ctx.db.commit()
    return {"template": item}


@router.get("/{template_id}/preview")
def template_preview(
    template_id: str,
    ctx: AuthContext = Depends(get_content_auth),
):
    """返回模板预览图（``GET /api/presentation-templates/{id}/preview``，image/webp）。

    用内容级鉴权而非普通接口鉴权，因为该地址会被 <img> 直接引用；响应声明私有缓存
    86400 秒，避免被共享缓存串用不同工作区的预览。
    """
    data = read_template_preview(ctx.db, ctx.workspace_id, template_id)
    return Response(
        content=data,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/{template_id}/example")
def template_example(
    template_id: str,
    ctx: AuthContext = Depends(get_content_auth),
):
    """下载模板示例 PPTX（``GET /api/presentation-templates/{id}/example``）。

    内容级鉴权，强制以附件方式下载；``filename*`` 同时给出 UTF-8 文件名，兼容中文模板名
    在旧浏览器下的乱码问题。
    """
    from urllib.parse import quote

    data, filename = read_template_example(template_id)
    disposition = "attachment; filename=example.pptx; filename*=UTF-8''" + quote(filename)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": disposition},
    )


@router.delete("/{template_id}")
def delete_template(template_id: str, ctx: AuthContext = Depends(get_current_context)):
    """删除工作区自定义模板（``DELETE /api/presentation-templates/{id}``）。

    不可恢复，且不影响已生成的历史文档；``audit`` 的 resource_id 去掉 ``custom:`` 前缀，
    与其它模块的资源标识保持一致。
    """
    delete_custom_template(ctx.db, ctx.workspace_id, template_id)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="presentation_template.delete", resource_type="presentation_template",
          resource_id=template_id.split(":", 1)[-1])
    ctx.db.commit()
    return {"ok": True}
