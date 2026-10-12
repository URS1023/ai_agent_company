"""Optional equipment/product catalogue pictures; never execute a data source.

职责：设备与产品"名片图"的存取 —— 上传时校验并压缩为 WEBP 落文件表，读取时按文件哈希
与大小复核后再返回字节。

位置与依赖：设备侧被 ``alerts.device_catalog``、产品侧被
``quality.product_catalog`` 调用（两者都只读 ``image_map`` 取图片标识）；
上传/删除走 ``files`` 服务与 ``storage`` 抽象。

重要约束：
- 本模块**只碰文件与图片表，从不执行数据源查询**（"never execute a data source"），
  产品图片的存在性校验依赖产品目录配置，而不是去连产品源库。
- 权限：目标对象需可编辑（设备 ``devices.edit`` / 产品 ``quality.edit``），
  文件操作另需 ``files.create`` / ``files.view``；图片内容读取还要复核文件未删除且来源类型正确。
- 并发：图片行有 ``version``，上传/删除都必须携带版本，冲突抛 409（``ConflictError``）。
- 一致性：先写文件后写审计，审计失败会回滚数据库并**删除已落盘的文件**，避免孤儿文件。
"""
from io import BytesIO

from PIL import Image, ImageOps
from sqlalchemy import select

from app.common.authorization import require_permission
from app.common.owned_records import owned_record
from app.common.storage import get_storage, sha256_bytes
from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import File
from app.modules.alerts.models import BusinessCardImage
from app.modules.files.service import save_upload
from app.modules.quality.images import inspect_image, MAX_IMAGE_BYTES
from app.modules.system.service_helpers import commit_change


def authorize_target(ctx, kind, target_id, *, edit=False):
    """校验名片对象存在且当前账号可访问；``edit=True`` 时要求可编辑。

    设备：委托 ``alerts.registry.get_record``（会连带校验设备采集源与质检服务可见性），
    动作取 ``devices.edit`` 或 ``devices.view``。
    产品：产品来自目录配置而非库表，故先查 ``quality.product_catalog.catalogue``，
    再按 ``productId`` 是否存在判断；编辑需 ``quality.edit`` 权限。
    不满足时统一抛 ``NotFoundError``（不区分"不存在"与"无权"，避免泄露对象是否存在）。
    """
    if kind == "device":
        from app.modules.alerts.registry import get_record
        return get_record(ctx, "devices", target_id, "edit" if edit else "view")
    if kind == "product":
        from app.modules.quality.product_catalog import catalogue
        if edit:
            require_permission(ctx, "quality.edit")
        catalog = catalogue(ctx)
        if any(entry["productId"] == target_id for entry in catalog["entries"]):
            return
    raise NotFoundError("设备或产品不存在，或当前账号无权访问")


def image_map(ctx, kind, target_ids):
    """批量取 ``{target_id: {fileId, version}}``，无图对象不出现在结果里。

    供列表接口一次性取图，避免逐行查询；``target_ids`` 为空时直接返回空字典（不查库）。
    只读，不做逐对象授权（调用方的列表查询已经过滤过可见对象）。
    """
    if not target_ids:
        return {}
    rows = ctx.db.scalars(select(BusinessCardImage).where(
        BusinessCardImage.workspace_id == ctx.workspace_id,
        BusinessCardImage.kind == kind, BusinessCardImage.target_id.in_(target_ids),
    )).all()
    return {row.target_id: {"fileId": row.file_id, "version": row.version} for row in rows}


def _record(ctx, kind, target_id, *, lock=False):
    """取某个对象的名片图行（可能为 ``None``，表示尚未上传）。

    ``lock=True`` 时用 ``SELECT ... FOR UPDATE`` 行锁，上传/删除必须加锁，
    否则并发上传会绕过 ``version`` 检查互相覆盖。
    """
    query = select(BusinessCardImage).where(
        BusinessCardImage.workspace_id == ctx.workspace_id,
        BusinessCardImage.kind == kind, BusinessCardImage.target_id == target_id,
    )
    return ctx.db.scalar(query.with_for_update() if lock else query)


def save(ctx, kind, target_id, data, version):
    """保存/替换名片图，返回 ``{fileId, version}``。

    参数：``data`` 为原始图片字节；``version`` 是前端读到的当前图片版本
    （从未上传过则为 0）。
    权限与顺序：目标对象可编辑 + ``files.create`` + ``files.view`` → 图片安全校验
    （``inspect_image``：格式、尺寸、体积）→ 加锁取行 → 版本比对（不一致抛
    ``ConflictError``，提示刷新后重选）→ 压缩为最长边 640 的 WEBP → 落文件 → 写行。
    图片处理：按 EXIF 方向纠正（``exif_transpose``）再转 RGBA，卡片图不需要保存原始大图。
    副作用：新增/更新 ``business_card_images``、新增 ``files`` 行与磁盘文件、写审计；
    审计失败时回滚数据库并删除刚写入的文件（防止孤儿文件）。
    """
    authorize_target(ctx, kind, target_id, edit=True)
    require_permission(ctx, "files.create")
    require_permission(ctx, "files.view")
    inspect_image(data)
    row = _record(ctx, kind, target_id, lock=True)
    if (row.version if row else 0) != version:
        raise ConflictError("图片已被更新，请刷新后重新选择")
    # Cards need a small, correctly oriented image, not a multi-megabyte original.
    with Image.open(BytesIO(data)) as original:
        picture = ImageOps.exif_transpose(original).convert("RGBA")
        picture.thumbnail((640, 640))
        buffer = BytesIO()
        picture.save(buffer, format="WEBP", quality=88)
    file = save_upload(ctx.db, workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                       filename=f"{kind}-photo.webp", mime_type="image/webp", data=buffer.getvalue())
    # 来源类型用于读取时校验"这个文件确实是名片图"，不能被其它上传复用。
    file.source_type = "business_card_image"
    if row is None:
        row = BusinessCardImage(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                                kind=kind, target_id=target_id, file_id=file.id)
        ctx.db.add(row)
    else:
        row.file_id = file.id
        row.version += 1
    stored_key = file.storage_key
    try:
        commit_change(ctx, "business.image.save", "files", file.id, {"kind": kind, "target_id": target_id})
    except Exception:
        ctx.db.rollback()
        get_storage().delete(stored_key)
        raise
    return {"fileId": row.file_id, "version": row.version}


def remove(ctx, kind, target_id, version):
    """解除名片图（置空 ``file_id`` 并递增版本），返回 ``{fileId: None, version}``。

    只加锁并校验版本，任何不匹配（含从未上传）都抛 ``ConflictError``；
    文件本身不删除（保留在文件表中供审计/追溯），因此这里是"取消关联"而非物理删除。
    """
    authorize_target(ctx, kind, target_id, edit=True)
    row = _record(ctx, kind, target_id, lock=True)
    if row is None or row.version != version:
        raise ConflictError("图片已被更新，请刷新后重试")
    row.file_id = None
    row.version += 1
    commit_change(ctx, "business.image.remove", "files", row.id, {"kind": kind, "target_id": target_id})
    return {"fileId": None, "version": row.version}


def content(ctx, kind, target_id):
    """读取名片图原始字节（WEBP），供接口直接返回给浏览器。

    校验链：对象可见 → 图片行存在且已关联文件 → 文件未删除且 ``source_type`` 为
    ``business_card_image`` → 存储中文件存在 → 重新比对**大小与 SHA-256**
    （与文件表记录不符即拒绝），防止磁盘文件被替换或截断后仍被当作有效图片返回。
    任一步不满足抛 ``NotFoundError`` / ``ValidationError``；只读，无副作用。
    """
    authorize_target(ctx, kind, target_id)
    row = _record(ctx, kind, target_id)
    if row is None or not row.file_id:
        raise NotFoundError("尚未上传图片")
    file = owned_record(ctx, File, row.file_id, "files.view")
    if file.deleted_at is not None or file.source_type != "business_card_image":
        raise NotFoundError("图片已移除")
    storage = get_storage()
    if not storage.exists(file.storage_key):
        raise NotFoundError("图片文件不存在，请重新上传")
    # 多读 1 字节用于判断是否超上限，避免把超大文件整体读进内存。
    with open(storage.get_path(file.storage_key), "rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES or len(data) != file.size or sha256_bytes(data) != file.sha256:
        raise ValidationError("图片内容校验失败，请重新上传")
    return data
