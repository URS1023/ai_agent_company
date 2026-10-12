"""Resolve authenticated artifact references before document rendering, without HTTP."""
from copy import deepcopy
import re
from app.common.storage import get_storage
from app.errors import ValidationError
from .service import get_artifact


def resolve_document_images(spec, db, workspace_id):
    """把文档规格里的图片引用解析成真实字节（渲染前的一次性鉴权步骤）。
    接受的引用形式：内嵌 data:/base64: 直接透传；/api/artifacts/<id>/(content|download) 或裸 26 位 id
    会通过 get_artifact 做工作区归属校验后读存储字节并挂到 block["_image_bytes"]；
    其它形式一律拒绝。刻意不走 HTTP：避免自发请求绕过鉴权与产生网络依赖。
    校验点：必须是 dict 规格；图片 MIME 必须在白名单内；附件必须存在且不超过 20MB。
    异常：ValidationError（规格非对象、引用非法、类型不支持、内容不可用或过大）、NotFoundError（产物不存在）。
    副作用：只读存储与数据库，不修改原 spec（deepcopy 后返回新对象）。
    """
    if not isinstance(spec, dict):
        raise ValidationError("文档内容必须是结构化对象")
    result = deepcopy(spec)
    for section in result.get("sections") or []:
        for block in section.get("blocks") or []:
            if block.get("type") != "image":
                continue
            reference = block.get("artifact_id") or block.get("source_url") or block.get("image") or block.get("path")
            if isinstance(reference, str) and reference.startswith(("data:", "base64:")):
                block["image"] = reference
                continue
            match = re.fullmatch(r"/api/artifacts/([A-Za-z0-9]{26})/(?:content|download)", str(reference or ""))
            identity = match.group(1) if match else str(reference or "")
            if not re.fullmatch(r"[A-Za-z0-9]{26}", identity):
                raise ValidationError("文档图片必须引用本工作区的图片附件或内嵌图片")
            artifact = get_artifact(db, workspace_id, identity)
            if artifact.mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"}:
                raise ValidationError("文档附件不是受支持的图片")
            if not artifact.storage_key or artifact.size > 20 * 1024 * 1024:
                raise ValidationError("文档图片内容不可用或超过 20 MB")
            block["_image_bytes"] = get_storage().read_bytes(artifact.storage_key)
    return result


def image_bytes(block):
    """从已解析的块里取图片字节：优先用 resolve_document_images 预置的 _image_bytes，
    否则退回到解析内嵌的 data:/base64: 字符串。
    异常：ValueError（既没有预置字节也没有可解析的内嵌图片）——这会中断渲染，避免产出缺图文档。
    """
    import base64
    data = block.get("_image_bytes")
    if isinstance(data, bytes):
        return data
    value = block.get("image") or block.get("path") or ""
    if isinstance(value, str) and value.startswith(("data:", "base64:")):
        encoded = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value[7:]
        return base64.b64decode(encoded, validate=True)
    raise ValueError("文档图片尚未解析，未生成缺图文档")
