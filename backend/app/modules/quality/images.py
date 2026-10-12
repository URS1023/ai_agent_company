"""Authorized, content-verified quality images backed by the shared file store.

职责：质检图片的存取 —— 上传前校验（格式/尺寸/方向），读取时按固定证据核对
（大小 + SHA-256 + 解码元数据），并把图片关联到文件模块的共享存储。

位置与依赖：被 ``quality.detection``（证据图片）、``quality.detector_inputs``（检测输入图）、
``quality.image_export``（报告内嵌图）与 ``alerts.card_images``（产品/设备名片图复用
``inspect_image`` 与 ``MAX_IMAGE_BYTES``）调用。

约束：
- 只接受单帧 PNG/JPEG/WebP（不接受 SVG/动画），≤ 5 MB、≤ 2500 万像素，方向必须在 1–8；
- 原图**字节永不改写**（不做压缩/裁剪），缺陷框坐标相对按 EXIF 方向纠正后的显示尺寸；
- 读取必须过 ``files.view``（导出另需 ``files.export``），历史证据允许引用已软删文件，
  但内容不符（哈希/大小/元数据）一律拒绝 —— 绝不用替代图片顶替；
- 上传不触发任何推理/检测。
"""
from __future__ import annotations

from io import BytesIO
import warnings

from PIL import Image, UnidentifiedImageError

from app.common.authorization import require_permission, require_resource
from app.common.deps import AuthContext
from app.common.owned_records import owned_record
from app.common.storage import get_storage, sha256_bytes
from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import File
from app.modules.files.service import save_upload
from app.modules.quality.schemas import QualityImageView
from app.modules.system.service_helpers import commit_change

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
IMAGE_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def inspect_image(data: bytes) -> dict:
    """Decode a bounded still image without changing its original bytes.

    （中文补充）校验并返回 ``{mimeType, width, height, orientation}``；
    ``width`` / ``height`` 已按 EXIF 方向换算（5–8 表示旋转 90°，需交换宽高），
    因此调用方拿到的就是"显示方向"尺寸，缺陷框按此方向定位。
    异常：空文件、超 5 MB、格式不在白名单、超 2500 万像素、多帧/动画、
    方向值非法、损坏或解码失败时统一抛 ``ValidationError``；
    解压炸弹告警被提升为错误（``simplefilter('error')``）以防恶意图片耗尽内存。
    只读校验，不落盘、不改写原始字节。
    """
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValidationError("质检图片不能为空且不得超过 5 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as picture:
                mime = IMAGE_TYPES.get(picture.format)
                width, height = picture.size
                if not mime or width * height > MAX_IMAGE_PIXELS or getattr(picture, "n_frames", 1) != 1:
                    raise ValidationError("只支持单帧 PNG/JPEG/WebP，最多 2500 万像素；不接受 SVG 或动画")
                picture.verify()
            # verify() alone does not fully decode a JPEG; reject truncated data.
            with Image.open(BytesIO(data)) as picture:
                orientation = picture.getexif().get(274, 1)
                if orientation not in range(1, 9):
                    raise ValidationError("图片方向信息无效，请修正图片后上传")
                picture.load()
        if orientation in (5, 6, 7, 8):
            width, height = height, width
        return {"mimeType": mime, "width": width, "height": height, "orientation": orientation}
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError("图片损坏、格式不支持或解码尺寸超限，请上传完整的静态图片") from exc


def image_record(ctx: AuthContext, file_id: str, *, snapshot: dict | None = None, export: bool = False) -> File:
    """Require file scope even when accessed through a quality report or workflow.

    （中文补充）取图片文件行并做权限与一致性校验。``export=True`` 另需 ``files.export``。
    两条路径的差别：
    - 无 ``snapshot``（新检验使用）：要求文件未软删；
    - 有 ``snapshot``（历史证据回读）：允许已软删文件，但 ``id`` / ``sha256`` / ``size``
      必须与证据记录完全一致，否则抛 ``ConflictError``（防止用别的图片顶替历史证据）。
    """
    row = owned_record(ctx, File, file_id, "files.view")
    if export:
        require_resource(ctx, "files.export", row)
    if snapshot is None and row.deleted_at is not None:
        raise NotFoundError("图片已移除，不能用于新检验；已有报告仍保留原图关联")
    if snapshot and (snapshot.get("id") != row.id or snapshot.get("sha256") != row.sha256 or snapshot.get("size") != row.size):
        raise ConflictError("图片元数据与固定证据不一致，未使用替代内容")
    return row


def image_content(ctx: AuthContext, file_id: str, *, snapshot: dict | None = None, export: bool = False) -> tuple[QualityImageView, bytes]:
    """Read only after authorization and verify content, size and original hash.

    （中文补充）返回 ``(图片视图, 原始字节)``。校验链：授权（``image_record``）→ 存储中存在 →
    有界读取（多读 1 字节判超限，同时防御读取期间被外部替换）→ 大小与 SHA-256 必须与文件表一致
    → 重新解码校验（``inspect_image``）→ 若给了 ``snapshot``，解码元数据（类型/宽高/方向）
    也必须与证据一致，最终以**证据快照**为准返回视图。
    异常：文件缺失、超上限、哈希/大小不符、解码元数据不一致时抛 ``NotFoundError`` /
    ``ValidationError`` / ``ConflictError``；**绝不返回替代图片**。
    """
    row = image_record(ctx, file_id, snapshot=snapshot, export=export)
    storage = get_storage()
    if not storage.exists(row.storage_key):
        raise NotFoundError("原始图片数据缺失；报告记录保留，但不能用其他图片替代")
    if storage.stat(row.storage_key)["size"] > MAX_IMAGE_BYTES:
        raise ValidationError("原始图片超过质检读取上限")
    # Bounded reading also protects against a concurrent external file replacement.
    with open(storage.get_path(row.storage_key), "rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) != row.size or sha256_bytes(data) != row.sha256:
        raise ConflictError("原图内容校验失败，图片可能已被替换；未显示或发送此图片")
    metadata = inspect_image(data)
    view = QualityImageView(id=row.id, name=row.name, size=row.size, sha256=row.sha256, **metadata)
    if snapshot:
        for key in ("mimeType", "width", "height", "orientation"):
            if snapshot.get(key) != getattr(view, key):
                raise ConflictError("图片解码信息与固定证据不一致")
        view = QualityImageView.model_validate(snapshot)
    return view, data


def upload_image(ctx: AuthContext, filename: str, data: bytes) -> QualityImageView:
    """Store a validated original in shared files; uploading never triggers inference.

    （中文补充）需要 ``quality.create`` + ``files.create`` + ``files.view``。
    文件名只取路径末段（去掉目录，防路径穿越），拒绝空名/控制字符/超 500 字符；
    校验通过后按原字节写入共享文件存储，``source_type`` 标记为 ``quality_image``
    （读取时据此确认用途），并写审计。返回图片视图（含哈希与显示方向尺寸）。
    **上传不会触发任何检测或推理**。
    """
    require_permission(ctx, "quality.create")
    require_permission(ctx, "files.create")
    require_permission(ctx, "files.view")
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or len(name) > 500 or any(ord(char) < 32 for char in name):
        raise ValidationError("图片文件名不能为空、含控制字符或超过 500 字符")
    metadata = inspect_image(data)
    row = save_upload(ctx.db, workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                      filename=name, mime_type=metadata["mimeType"], data=data)
    row.source_type = "quality_image"
    commit_change(ctx, "quality.image.upload", "files", row.id, {"sha256": row.sha256, "size": row.size})
    return QualityImageView(id=row.id, name=row.name, size=row.size, sha256=row.sha256, **metadata)


def authorize_evidence_images(ctx: AuthContext, results: list[dict]) -> None:
    """History authorization uses fixed image references, including soft-deleted files.

    （中文补充）读取历史检测/检验证据前，逐项取出固定的图片引用并复核权限与一致性
    （``image_record`` 带 ``snapshot`` 的路径，允许已软删文件但必须内容相符）。
    兼容两种形状：``provenance.image`` 与直接挂在项上的 ``image``。
    无图片的项跳过；任一项不通过即抛错（不静默跳过该图片）。只读、无副作用。
    """
    for item in results:
        snapshot = item.get("provenance", {}).get("image") or item.get("image")
        if snapshot:
            image_record(ctx, snapshot["id"], snapshot=snapshot)
