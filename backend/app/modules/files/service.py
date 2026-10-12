# -*- coding: utf-8 -*-
"""Versioned file assets, categories, storage and parse evidence."""
from __future__ import annotations

import os
import re
from datetime import datetime
from threading import RLock
from typing import Iterable, List

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...common.storage import build_key, get_storage, sha256_bytes
from ...errors import ConflictError, NotFoundError, ValidationError
from ...ids import new_id
from ...models import File
from .models import FileCategory, FileMetadata, FileParseEvent

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_FILES = 20
MAX_BATCH_BYTES = 50 * 1024 * 1024

ALLOWED_MIME = {
    "application/octet-stream",
    "application/pdf", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel", "text/csv", "text/plain", "text/markdown",
    "application/json", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint", "image/png", "image/jpeg", "image/webp",
    "image/gif", "image/bmp", "image/svg+xml",
}

_MIME_BY_EXT = {
    ".txt": "text/plain", ".csv": "text/csv", ".md": "text/markdown", ".json": "application/json",
    ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp", ".svg": "image/svg+xml",
}

_adoption_lock = RLock()


def infer_mime_from_name(filename: str, fallback: str = "application/octet-stream") -> str:
    """Guess MIME from a filename when browser/agent uploads omit it."""
    # 中文说明：扩展名优先于浏览器上报的 MIME。浏览器和自动化上传常把 MIME 报成
    # application/octet-stream 甚至 text/plain，若以 MIME 为准会让 docx/pdf 走错解析分支；
    # 只有扩展名不在 _MIME_BY_EXT 时才回落到调用方给的 fallback。
    ext = os.path.splitext(filename or "")[1].lower()
    return _MIME_BY_EXT.get(ext, fallback)


def normalize_filename(filename: str) -> str:
    """清洗用户文件名：只保留路径最后一段，并剔除控制字符。

    安全约束：``replace("\\\\", "/")`` 后再 rsplit 是为同时挡住 Windows 反斜杠与 POSIX 斜杠两
    种"穿越路径"写法，返回的文件名只用于展示与生成存储 key，绝不作为真实磁盘路径拼接。
    控制字符（<32 及 DEL）会被删除，避免日志注入与前端展示错乱。
    异常：ValidationError —— 空名或超过 500 字符。
    """
    value = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    value = "".join(character for character in value if ord(character) >= 32 and character != "\x7f")
    if not value:
        raise ValidationError("文件名不能为空")
    if len(value) > 500:
        raise ValidationError("文件名不能超过 500 个字符")
    return value


def validate_renamed_filename(original: str, requested: str) -> str:
    """Renaming never converts bytes; retain the original format/extension."""
    # 中文说明：重命名只改展示名，不改字节，所以扩展名必须保持一致——否则 mime_type 与实际内容
    # 会错配，解析器按错误分支解析会失败或产出乱码。想换格式必须走"上传新版本"。
    name = normalize_filename(requested)
    original_ext = os.path.splitext(original)[1].lower()
    if os.path.splitext(name)[1].lower() != original_ext:
        raise ValidationError("重命名不能修改文件扩展名；如需转换格式，请上传转换后的文件")
    return name


def copied_filename(original: str) -> str:
    """生成"xxx（副本）.ext"形式的复制文件名。

    截断长度按扩展名反算（500 上限减去扩展名与"（副本）"4 个字符），保证拼出来的名字
    不会越过 normalize_filename 的 500 字限制而抛错。
    """
    stem, extension = os.path.splitext(original)
    return normalize_filename(stem[:500 - len(extension) - 4] + "（副本）" + extension)


def validate_upload(filename: str, mime_type: str, data: bytes) -> tuple[str, str]:
    """上传前的统一校验，返回 (清洗后的文件名, 最终 MIME)。

    校验顺序固定为"文件名 → 大小 → 类型"，先报文件名问题可以让用户在最小代价下修正。
    MIME 以扩展名推断结果为准（见 infer_mime_from_name），并把上报值截到分号前的主类型
    （浏览器会附带 ``; charset=utf-8`` 之类的参数）。
    异常：ValidationError —— 文件名为空/过长、超过 MAX_FILE_BYTES、或类型不在 ALLOWED_MIME。
    """
    name = normalize_filename(filename)
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError("单个文件不能超过 20 MB")
    resolved_mime = infer_mime_from_name(name, (mime_type or "application/octet-stream").split(";", 1)[0].strip())
    if resolved_mime not in ALLOWED_MIME:
        raise ValidationError("不支持该文件类型")
    return name, resolved_mime


def normalize_tags(values: Iterable[str] | None) -> list[str]:
    """清洗标签列表：压缩内部空白、去重（保持首次出现顺序）、单个 ≤40 字符、总数 ≤20。

    顺序敏感：去重保留原顺序，前端标签顺序在多次保存之间是稳定的。
    异常：ValidationError —— 单个标签超长或标签数量超限。
    """
    result = []
    for value in values or []:
        item = re.sub(r"\s+", " ", str(value)).strip()
        if not item:
            continue
        if len(item) > 40:
            raise ValidationError("单个标签不能超过 40 个字符")
        if item not in result:
            result.append(item)
    if len(result) > 20:
        raise ValidationError("每个文件最多设置 20 个标签")
    return result


def get_category(db: Session, workspace_id: str, category_id: str | None) -> FileCategory | None:
    """按 ID 取本工作区的活动分类；category_id 为空时返回 None（=不分类，是合法状态）。

    所有分类写入路径都先过这里，因此"跨工作区引用分类"与"引用已归档分类"都会被挡住。
    异常：NotFoundError —— 分类不存在、不属于该工作区或已 archived。
    """
    if not category_id:
        return None
    row = db.scalar(select(FileCategory).where(
        FileCategory.id == category_id,
        FileCategory.workspace_id == workspace_id,
        FileCategory.status == "active",
    ))
    if row is None:
        raise NotFoundError("文件分类不存在")
    return row


def _metadata_for_file(db: Session, workspace_id: str, file_id: str) -> FileMetadata | None:
    """取某个物理文件对应的治理元数据行；返回 None 表示该文件尚未纳管。

    用 file_id 而不是 logical_id 查询：纳管是按物理版本逐个建立的，
    同一 logical_id 的历史版本各自持有自己的一行元数据。
    """
    return db.scalar(select(FileMetadata).where(
        FileMetadata.workspace_id == workspace_id,
        FileMetadata.file_id == file_id,
    ))


def ensure_metadata(db: Session, row: File) -> tuple[FileMetadata, bool]:
    """Adopt one legacy physical file without changing its existing ID."""
    # 中文说明：历史数据里存在只有 File、没有 FileMetadata 的物理文件（治理模块上线前上传的）。
    # 这里做幂等"纳管"：logical_id 直接取 row.id、revision_number=1、is_current=True，
    # 即把旧文件当成自己的第一个版本，从而不需要数据迁移脚本。
    # 返回 (metadata, 是否本次新建)，调用方据此决定要不要 commit。
    # 并发保护：先无锁快路径，再在进程内锁下二次确认；跨进程冲突靠 uq_file_metadata_file
    # 唯一约束 + 嵌套事务捕获 IntegrityError 后回读既有行，避免并发纳管产生重复行。
    metadata = _metadata_for_file(db, row.workspace_id, row.id)
    if metadata is not None:
        return metadata, False
    with _adoption_lock:
        metadata = _metadata_for_file(db, row.workspace_id, row.id)
        if metadata is not None:
            return metadata, False
        candidate = FileMetadata(
            id=new_id(), workspace_id=row.workspace_id, file_id=row.id,
            logical_id=row.id, category_id=None, description="", tags=[],
            revision_number=1, is_current=True, previous_file_id=None,
            change_note="迁入现有文件", created_by_user_id=row.owner_user_id,
            created_at=row.created_at, updated_at=datetime.utcnow(),
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            return candidate, True
        except IntegrityError:
            db.expire_all()
            existing = _metadata_for_file(db, row.workspace_id, row.id)
            if existing is None:
                raise
            return existing, False


def ensure_all_metadata(db: Session, workspace_id: str) -> None:
    """为工作区内所有未删除文件批量纳管（列表页打开前的自愈调用）。

    只读调用（list_files / file_out）也会触发它，所以这里必须廉价：无新建时不 commit，
    避免每个查询请求都产生一次空事务。已删除的文件（deleted_at 非空）不纳管。
    """
    created = False
    rows = db.scalars(select(File).where(
        File.workspace_id == workspace_id,
        File.deleted_at.is_(None),
    )).all()
    for row in rows:
        _metadata, adopted = ensure_metadata(db, row)
        created = created or adopted
    if created:
        db.commit()


def save_upload(
    db: Session,
    *,
    workspace_id: str,
    owner_user_id: str,
    filename: str,
    mime_type: str,
    data: bytes,
    category_id: str | None = None,
    description: str = "",
    tags: Iterable[str] | None = None,
    change_note: str = "首次上传",
) -> File:
    """写入一个新文件（首次上传），同时落存储与两行数据库记录，返回物理 File 行。

    事务与存储的先后顺序是有意的：先 put_bytes 再开嵌套事务写库，写库失败时删除已落盘对象
    （见 except 分支），避免"库里有记录、磁盘没有字节"这种不可恢复状态；反向的残留孤儿对象
    比脏记录更容易清理。因此本函数不 commit，由调用方（router 层）决定事务边界。
    参数：change_note 默认"首次上传"，会截断到 500 字。
    异常：ValidationError（文件名/大小/类型）、NotFoundError（分类无效）以及写库时的原始异常。
    副作用：写对象存储、写 files 与 file_metadata 两张表。
    """
    name, resolved_mime = validate_upload(filename, mime_type, data)
    category = get_category(db, workspace_id, category_id)
    category_id = category.id if category else None
    tag_values = normalize_tags(tags)
    storage = get_storage()
    file_id = new_id()
    key = build_key("files", workspace_id, file_id, name)
    storage.put_bytes(key, data)
    row = File(
        id=file_id, workspace_id=workspace_id, owner_user_id=owner_user_id,
        name=name, mime_type=resolved_mime, size=len(data), storage_key=key,
        sha256=sha256_bytes(data), source_type="upload", status="ready",
        created_at=datetime.utcnow(),
    )
    metadata = FileMetadata(
        id=new_id(), workspace_id=workspace_id, file_id=file_id, logical_id=file_id,
        category_id=category_id, description=(description or "").strip()[:2000], tags=tag_values,
        revision_number=1, is_current=True, previous_file_id=None,
        change_note=(change_note or "首次上传").strip()[:500],
        created_by_user_id=owner_user_id,
    )
    try:
        with db.begin_nested():
            db.add_all([row, metadata])
            db.flush()
    except Exception:
        storage.delete(key)
        raise
    return row


def find_same_content(
    db: Session, workspace_id: str, name: str, sha: str, file_ids: List[str] | None = None,
) -> File | None:
    """按"同名 + 同 sha256"查找是否已存在同样内容的文件，返回最新的一条或 None。

    只比 sha256 会误判"不同名但同内容"（例如多个部门各自上传同一份模板），所以名称也参与匹配，
    这是刻意的产品语义：同名同内容才算重复。file_ids 可选，用于把查重范围限制在候选集合内。
    调用方用它来做上传去重与"是否已加入知识库"的前置判断；不抛异常。
    """
    q = select(File).where(
        File.workspace_id == workspace_id,
        File.name == name,
        File.sha256 == sha,
        File.deleted_at.is_(None),
    )
    if file_ids:
        q = q.where(File.id.in_(file_ids))
    return db.scalar(q.order_by(File.created_at.desc()).limit(1))


def get_file(db: Session, workspace_id: str, file_id: str) -> File:
    """按 ID 取本工作区未删除的物理文件，是几乎所有文件操作的入口校验。

    工作区过滤是权限边界的一部分：即使拿到了别人工作区的 file_id 也只会得到 404。
    异常：NotFoundError —— 不存在、不属于该工作区或已被软删除（deleted_at 非空）。
    """
    row = db.scalar(select(File).where(
        File.id == file_id,
        File.workspace_id == workspace_id,
        File.deleted_at.is_(None),
    ))
    if row is None:
        raise NotFoundError("文件不存在")
    return row


def current_file(db: Session, workspace_id: str, file_id: str) -> tuple[File, FileMetadata]:
    """把任意历史版本的 file_id 归一到"当前版本"，返回 (当前 File, 当前 FileMetadata)。

    所有写操作（改名、改元数据、加版本、删除）都必须经由此函数，否则会改到历史版本上，
    导致"编辑旧版本"这类不可见的数据错乱。若传的是历史版本，则通过 logical_id 反查
    is_current 的那一行再取对应 File。
    异常：NotFoundError（文件不存在）、ConflictError（版本链里没有任何 is_current 行，
        属于数据损坏，需要管理员修复而不是自动选一个）。
    """
    row = get_file(db, workspace_id, file_id)
    metadata, adopted = ensure_metadata(db, row)
    if adopted:
        db.commit()
    if metadata.is_current:
        return row, metadata
    current_meta = db.scalar(select(FileMetadata).where(
        FileMetadata.workspace_id == workspace_id,
        FileMetadata.logical_id == metadata.logical_id,
        FileMetadata.is_current.is_(True),
    ))
    if current_meta is None:
        raise ConflictError("文件版本链没有当前版本，请联系管理员修复")
    return get_file(db, workspace_id, current_meta.file_id), current_meta


def list_files(
    db: Session,
    workspace_id: str,
    file_ids: List[str] | None = None,
    *,
    query: str = "",
    category_id: str | None = None,
) -> List[File]:
    """列出生效文件（每个 logical_id 只返回当前版本），供资料库列表与选择器使用。

    固定的三条过滤：仅未删除、仅 is_current、排除 source_type == "knowledge_manual"
    （手工分段产生的文件是知识库内部资产，不应出现在资料库里）。
    参数：query 同时模糊匹配文件名与描述（两侧 %）；category_id 精确匹配分类。
    副作用：会触发 ensure_all_metadata 做历史数据纳管，因此本函数可能写库。
    """
    ensure_all_metadata(db, workspace_id)
    statement = select(File, FileMetadata).join(
        FileMetadata, FileMetadata.file_id == File.id,
    ).where(
        File.workspace_id == workspace_id,
        File.deleted_at.is_(None),
        File.source_type != "knowledge_manual",
        FileMetadata.is_current.is_(True),
    )
    if file_ids:
        statement = statement.where(File.id.in_(file_ids))
    if query.strip():
        pattern = f"%{query.strip()}%"
        statement = statement.where(or_(
            File.name.ilike(pattern),
            FileMetadata.description.ilike(pattern),
        ))
    if category_id:
        statement = statement.where(FileMetadata.category_id == category_id)
    return [item[0] for item in db.execute(statement.order_by(File.created_at.desc(), File.id)).all()]


def file_out(db: Session, row: File) -> dict:
    """把 File 行组装成对外返回的 dict（API/工具统一使用的序列化口径）。

    注意返回里混了两张表的数据：物理字段取自 File（name/mime/size/sha256），治理字段取自
    FileMetadata（分类/描述/标签/版本）。metadata_version 是与后端做乐观锁的字段，前端改元数据
    必须回传它。
    分类名会二次校验 workspace_id，防止分类被移到别的空间后仍被显示出来。
    副作用：可能补建元数据（历史文件首次展示时）。
    """
    metadata, adopted = ensure_metadata(db, row)
    if adopted:
        db.flush()
    category = db.get(FileCategory, metadata.category_id) if metadata.category_id else None
    return {
        "id": row.id,
        "logical_id": metadata.logical_id,
        "name": row.name,
        "mime_type": row.mime_type,
        "size": row.size,
        "sha256": row.sha256,
        "status": row.status,
        "category_id": metadata.category_id,
        "category_name": category.name if category and category.workspace_id == row.workspace_id else None,
        "description": metadata.description,
        "tags": metadata.tags or [],
        "revision_number": metadata.revision_number,
        "metadata_version": metadata.version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": metadata.updated_at.isoformat() if metadata.updated_at else None,
    }


def update_metadata(
    db: Session,
    workspace_id: str,
    file_id: str,
    *,
    version: int,
    category_id: str | None,
    description: str,
    tags: Iterable[str],
    name: str | None = None,
) -> tuple[File, FileMetadata]:
    """更新当前版本的分类/描述/标签，可选改名，返回 (File, FileMetadata)。

    乐观锁：version 必须与 FileMetadata.version 相等，否则抛 ConflictError 并带上
    current_version，前端据此提示"已被他人修改"。改名复用 validate_renamed_filename，
    因此扩展名不可变。
    参数：name 为 None 表示不改名（区分"没传"与"传了空字符串"）。
    异常：ConflictError（版本冲突）、ValidationError（文件名校验）、NotFoundError（分类无效）。
    副作用：写 files.name 与 file_metadata 行；不 commit。
    """
    row, metadata = current_file(db, workspace_id, file_id)
    if metadata.version != version:
        raise ConflictError("文件信息已被修改，请刷新后重试", current_version=metadata.version)
    renamed = validate_renamed_filename(row.name, name) if name is not None else row.name
    category = get_category(db, workspace_id, category_id)
    row.name = renamed
    metadata.category_id = category.id if category else None
    metadata.description = (description or "").strip()[:2000]
    metadata.tags = normalize_tags(tags)
    metadata.updated_at = datetime.utcnow()
    db.flush()
    return row, metadata


def create_version(
    db: Session,
    workspace_id: str,
    file_id: str,
    *,
    actor_user_id: str,
    filename: str,
    mime_type: str,
    data: bytes,
    change_note: str,
) -> File:
    """把新上传的字节追加为当前逻辑文件的下一个版本（不是覆盖写入）。

    沿用旧版本的 logical_id / category_id / description / tags，只有 revision_number+1，
    并记录 previous_file_id 形成链；旧行的 is_current 置 False。老版本行与老存储对象都会保留，
    因此知识库对旧版本的引用不会失效（这点是刻意设计，见文件治理需求）。
    事务顺序与 save_upload 一致：先落盘再写库，写库失败回删对象；不 commit。
    参数：filename 为空时沿用当前版本的名字（mime_type 仍按实际传入值校验）。
    异常：ValidationError（上传校验）、NotFoundError（当前版本/文件不存在）。
    副作用：写对象存储 + files/file_metadata 两表，并把旧版本置为非当前。
    """
    current, current_meta = current_file(db, workspace_id, file_id)
    name, resolved_mime = validate_upload(filename or current.name, mime_type, data)
    storage = get_storage()
    new_file_id = new_id()
    key = build_key("files", workspace_id, new_file_id, name)
    storage.put_bytes(key, data)
    row = File(
        id=new_file_id, workspace_id=workspace_id, owner_user_id=current.owner_user_id,
        name=name, mime_type=resolved_mime, size=len(data), storage_key=key,
        sha256=sha256_bytes(data), source_type="version", status="ready",
        created_at=datetime.utcnow(),
    )
    metadata = FileMetadata(
        id=new_id(), workspace_id=workspace_id, file_id=new_file_id,
        logical_id=current_meta.logical_id, category_id=current_meta.category_id,
        description=current_meta.description, tags=list(current_meta.tags or []),
        revision_number=current_meta.revision_number + 1, is_current=True,
        previous_file_id=current.id, change_note=(change_note or "上传新版本").strip()[:500],
        created_by_user_id=actor_user_id,
    )
    try:
        with db.begin_nested():
            current_meta.is_current = False
            current_meta.updated_at = datetime.utcnow()
            db.add_all([row, metadata])
            db.flush()
    except Exception:
        storage.delete(key)
        raise
    return row


def list_versions(db: Session, workspace_id: str, file_id: str) -> list[dict]:
    """列出某个逻辑文件的所有版本，按 revision_number 倒序（最新的在前）。

    入参可以是任意版本的 file_id，内部会先归一到当前版本再按 logical_id 捞全链，
    因此前端从历史版本页刷新也能拿到完整版本列表。current 为 True 的那条即当前版本。
    """
    _current, metadata = current_file(db, workspace_id, file_id)
    rows = db.execute(select(FileMetadata, File).join(
        File, File.id == FileMetadata.file_id,
    ).where(
        FileMetadata.workspace_id == workspace_id,
        FileMetadata.logical_id == metadata.logical_id,
    ).order_by(FileMetadata.revision_number.desc())).all()
    return [{
        "file_id": file_row.id,
        "revision_number": item.revision_number,
        "name": file_row.name,
        "mime_type": file_row.mime_type,
        "size": file_row.size,
        "sha256": file_row.sha256,
        "current": item.is_current,
        "change_note": item.change_note,
        "created_by_user_id": item.created_by_user_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    } for item, file_row in rows]


def restore_version(
    db: Session,
    workspace_id: str,
    file_id: str,
    revision_number: int,
    actor_user_id: str,
) -> File:
    """把指定历史版本的字节重新作为一个新版本追加到链尾（回滚 = 再发一版，而非删历史）。

    实现方式是读出目标版本的字节后调用 create_version，因此恢复动作本身也会留下一条
    版本记录和 change_note（"从 vN 恢复"），审计链完整、也可再次恢复回去。
    文件名取"当前版本的主干名 + 目标版本的扩展名"：版本间扩展名一致（见 validate_upload），
    这样写既能保持用户最新改过的名称，又不会用错扩展名。
    异常：NotFoundError（版本号不存在/文件不存在）、IO 异常（存储对象缺失时 read_bytes 抛出）。
    """
    current, current_meta = current_file(db, workspace_id, file_id)
    target_meta = db.scalar(select(FileMetadata).where(
        FileMetadata.workspace_id == workspace_id,
        FileMetadata.logical_id == current_meta.logical_id,
        FileMetadata.revision_number == revision_number,
    ))
    if target_meta is None:
        raise NotFoundError("指定文件版本不存在")
    target = get_file(db, workspace_id, target_meta.file_id)
    data = get_storage().read_bytes(target.storage_key)
    return create_version(
        db, workspace_id, current.id, actor_user_id=actor_user_id,
        filename=os.path.splitext(current.name)[0] + os.path.splitext(target.name)[1],
        mime_type=target.mime_type, data=data,
        change_note=f"从 v{revision_number} 恢复",
    )


def rename_file(db: Session, workspace_id: str, file_id: str, name: str) -> File:
    """直接在物理行上改名（仅改展示名，不产生新版本）。

    与 create_version 的区别：改名不动字节，所以不占用版本号；扩展名仍受
    validate_renamed_filename 约束。入参 file_id 可为历史版本，current_file 会归一到当前版本。
    异常：ValidationError（文件名非法或扩展名被改）、NotFoundError。
    """
    row, metadata = current_file(db, workspace_id, file_id)
    row.name = validate_renamed_filename(row.name, name)
    metadata.updated_at = datetime.utcnow()
    db.flush()
    return row


def mark_deleted(db: Session, workspace_id: str, file_id: str) -> list[str]:
    """软删除整个逻辑文件（含所有历史版本），返回被删除的物理 file_id 列表。

    整链删除：取 metadata.logical_id 后把该链上所有版本行一起置 deleted，并把全部元数据行
    is_current 置 False（链上不再有当前版本，这正是"已删除"的标记）。
    只改状态与 deleted_at，不删对象存储、不删数据库行，因此知识库引用仍可解析；
    真正清理字节是另一条离线流程。调用方需要自己 commit。
    副作用：批量更新 files 与 file_metadata；返回的 file_id 列表供调用方清理关联引用。
    """
    _current, metadata = current_file(db, workspace_id, file_id)
    metadata_rows = db.scalars(select(FileMetadata).where(
        FileMetadata.workspace_id == workspace_id,
        FileMetadata.logical_id == metadata.logical_id,
    )).all()
    file_ids = [item.file_id for item in metadata_rows]
    rows = db.scalars(select(File).where(File.id.in_(file_ids))).all()
    now = datetime.utcnow()
    for row in rows:
        row.deleted_at = now
        row.status = "deleted"
    for item in metadata_rows:
        item.is_current = False
        item.updated_at = now
    return list(file_ids)


def category_out(row: FileCategory, child_count: int = 0, file_count: int = 0) -> dict:
    """分类行的对外序列化；child_count/file_count 由调用方统计后传入（不在本函数查库）。

    version 字段用于前端做乐观锁回传（见 update_category/delete_category）。
    """
    return {
        "id": row.id, "parent_id": row.parent_id, "name": row.name,
        "description": row.description, "sort_order": row.sort_order,
        "status": row.status, "version": row.version,
        "child_count": child_count, "file_count": file_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_categories(db: Session, workspace_id: str) -> list[dict]:
    """列出工作区所有生效分类（扁平结构，前端自行按 parent_id 组装树）。

    只返回 status == "active" 的分类（archived 的被 delete_category 归档后不再出现）。
    子分类数只统计 active 子节点，文件数只统计 is_current 的元数据行，
    保证列表上的计数与"点进去能看到的内容"一致。
    排序：sort_order → name → id，最后的 id 兜底保证顺序稳定（避免同序时前端跳动）。
    """
    rows = db.scalars(select(FileCategory).where(
        FileCategory.workspace_id == workspace_id,
        FileCategory.status == "active",
    ).order_by(FileCategory.sort_order, FileCategory.name, FileCategory.id)).all()
    child_counts = dict(db.execute(select(
        FileCategory.parent_id, func.count(FileCategory.id),
    ).where(
        FileCategory.workspace_id == workspace_id,
        FileCategory.status == "active",
    ).group_by(FileCategory.parent_id)).all())
    file_counts = dict(db.execute(select(
        FileMetadata.category_id, func.count(FileMetadata.id),
    ).where(
        FileMetadata.workspace_id == workspace_id,
        FileMetadata.is_current.is_(True),
    ).group_by(FileMetadata.category_id)).all())
    return [category_out(row, child_counts.get(row.id, 0), file_counts.get(row.id, 0)) for row in rows]


def _same_category_name(
    db: Session, workspace_id: str, parent_id: str | None, name: str, exclude_id: str | None = None,
) -> bool:
    """同级重名检查：传 exclude_id 可排除自身（更新场景必须传，否则自己会撞自己）。

    parent_id 为 None 表示顶层；这里用 `.is_(None)` 而不是 `== None`，因为 SQL 中 `= NULL`
    永远不成立，写成等号会导致顶层的重名检查恒为 False。
    只比对 active 分类：已归档的同名分类不占用名字。
    """
    statement = select(FileCategory.id).where(
        FileCategory.workspace_id == workspace_id,
        FileCategory.status == "active",
        FileCategory.name == name,
        FileCategory.parent_id.is_(None) if parent_id is None else FileCategory.parent_id == parent_id,
    )
    if exclude_id:
        statement = statement.where(FileCategory.id != exclude_id)
    return db.scalar(statement) is not None


def create_category(
    db: Session, workspace_id: str, *, name: str, parent_id: str | None,
    description: str, sort_order: int,
) -> FileCategory:
    """新建文件分类，返回新建行；名称内部连续空白会被压缩成单空格。

    校验顺序：名称非空 → 父分类存在（get_category 同时校验工作区与未归档）→ 同级重名。
    名称截断到 120 字、描述 1000 字，与 file_categories 的列宽保持一致。
    异常：ValidationError（名称空）、NotFoundError（父分类无效）、ConflictError（同级重名）。
    副作用：写 file_categories；不 commit。
    """
    value = re.sub(r"\s+", " ", name or "").strip()
    if not value:
        raise ValidationError("分类名称不能为空")
    if parent_id:
        get_category(db, workspace_id, parent_id)
    if _same_category_name(db, workspace_id, parent_id, value):
        raise ConflictError("同级分类名称已存在")
    row = FileCategory(
        id=new_id(), workspace_id=workspace_id, parent_id=parent_id,
        name=value[:120], description=(description or "").strip()[:1000],
        sort_order=sort_order, status="active",
    )
    db.add(row)
    db.flush()
    return row


def update_category(
    db: Session, workspace_id: str, category_id: str, *, version: int,
    name: str, parent_id: str | None, description: str, sort_order: int,
) -> FileCategory:
    """更新分类的名称/父级/描述/排序，返回更新后的行。

    校验顺序（先失败先返回）：乐观锁 version → 不能以自己为父 → 父链无环 → 名称非空 → 同级重名。
    环检测从目标父节点往上走并用 seen 集合判断是否会回到自己，防止把子树挂到自己下面造成
    无限循环的层级。
    异常：ConflictError（version 不匹配或同级重名）、ValidationError（自引用/成环/名称空）、
        NotFoundError（分类或父分类无效）。
    副作用：写 file_categories；不 commit。
    """
    row = get_category(db, workspace_id, category_id)
    if row.version != version:
        raise ConflictError("分类已被修改，请刷新后重试", current_version=row.version)
    if parent_id == row.id:
        raise ValidationError("分类不能以自己作为上级")
    if parent_id:
        parent = get_category(db, workspace_id, parent_id)
        seen = {row.id}
        while parent is not None:
            if parent.id in seen:
                raise ValidationError("分类层级不能形成循环")
            seen.add(parent.id)
            if not parent.parent_id:
                break
            parent = get_category(db, workspace_id, parent.parent_id)
    value = re.sub(r"\s+", " ", name or "").strip()
    if not value:
        raise ValidationError("分类名称不能为空")
    if _same_category_name(db, workspace_id, parent_id, value, row.id):
        raise ConflictError("同级分类名称已存在")
    row.name = value[:120]
    row.parent_id = parent_id
    row.description = (description or "").strip()[:1000]
    row.sort_order = sort_order
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def delete_category(db: Session, workspace_id: str, category_id: str, version: int) -> FileCategory:
    """删除（归档）分类，返回归档后的行。

    非空保护是刻意的产品约束：有 active 子分类、或还有 is_current 文件时要先搬走，
    避免"删掉分类导致文件失去归属"的静默数据丢失。因此单节点删除永远不会级联。
    真删除改为把 status 置为 "archived"，历史引用与审计仍可回溯。
    异常：ConflictError（version 不匹配、存在下级分类、分类下仍有文件）、NotFoundError。
    副作用：写 file_categories.status；不 commit。
    """
    row = get_category(db, workspace_id, category_id)
    if row.version != version:
        raise ConflictError("分类已被修改，请刷新后重试", current_version=row.version)
    if db.scalar(select(FileCategory.id).where(
        FileCategory.workspace_id == workspace_id,
        FileCategory.parent_id == row.id,
        FileCategory.status == "active",
    )):
        raise ConflictError("请先移动或删除下级分类")
    if db.scalar(select(FileMetadata.id).where(
        FileMetadata.workspace_id == workspace_id,
        FileMetadata.category_id == row.id,
        FileMetadata.is_current.is_(True),
    )):
        raise ConflictError("请先移动该分类中的文件")
    row.status = "archived"
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def record_parse(
    db: Session, *, workspace_id: str, file_id: str, logical_id: str,
    actor_user_id: str, status: str, block_count: int = 0, char_count: int = 0,
    error_summary: str | None = None,
) -> FileParseEvent:
    """记录一次解析/预览尝试的证据（只追加，不修改历史记录）。

    无论成功还是失败都会写入，因此这张表既是审计线索也是"该文件历史上解析成过什么"的依据。
    operation 固定为 "preview"，parser 固定为 "builtin"（当前只有内置解析器）。
    error_summary 截断到 1000 字，避免异常堆栈把行撑爆。
    副作用：写 file_parse_events；不 commit。
    """
    row = FileParseEvent(
        id=new_id(), workspace_id=workspace_id, logical_id=logical_id,
        file_id=file_id, operation="preview", status=status, parser="builtin",
        block_count=block_count, char_count=char_count,
        error_summary=(error_summary or "")[:1000] or None,
        actor_user_id=actor_user_id,
    )
    db.add(row)
    db.flush()
    return row


def extract_preview(db: Session, workspace_id: str, file_id: str, actor_user_id: str) -> dict:
    """解析文件正文用于在线预览，返回 {file_id, logical_id, revision_number, text, blocks, char_count}。

    两条必须写审计的分支：存储对象缺失、以及解析过程抛异常。两者都会先落一条 failed 事件并
    commit，再抛业务异常——顺序很重要，因为调用方的事务回滚不能把"失败证据"一起撤销。
    成功分支的 blocks 保留原始定位信息（page/sheet/slide），前端预览与后续引用都依赖它。
    异常：NotFoundError（文件不存在或存储对象缺失）、ValidationError（解析失败，原文写入事件）。
    副作用：读存储文件；写 file_parse_events。
    """
    row = get_file(db, workspace_id, file_id)
    metadata, adopted = ensure_metadata(db, row)
    if adopted:
        db.flush()
    storage = get_storage()
    if not storage.exists(row.storage_key):
        record_parse(
            db, workspace_id=workspace_id, file_id=row.id, logical_id=metadata.logical_id,
            actor_user_id=actor_user_id, status="failed", error_summary="文件数据不存在",
        )
        db.commit()
        raise NotFoundError("文件数据不存在")
    try:
        from .extractor import extract_text
        blocks = extract_text(row.name, row.mime_type, storage.get_path(row.storage_key))
        text = "\n\n".join(item["text"] for item in blocks)
        record_parse(
            db, workspace_id=workspace_id, file_id=row.id, logical_id=metadata.logical_id,
            actor_user_id=actor_user_id, status="completed", block_count=len(blocks),
            char_count=len(text),
        )
        return {
            "file_id": row.id, "logical_id": metadata.logical_id,
            "revision_number": metadata.revision_number,
            "text": text, "blocks": blocks, "char_count": len(text),
        }
    except Exception as exc:
        record_parse(
            db, workspace_id=workspace_id, file_id=row.id, logical_id=metadata.logical_id,
            actor_user_id=actor_user_id, status="failed", error_summary=str(exc),
        )
        db.commit()
        raise ValidationError(str(exc)) from exc


def list_parse_events(db: Session, workspace_id: str, file_id: str, limit: int = 50) -> list[dict]:
    """按逻辑文件（而非单个物理版本）列出解析事件，倒序取前 limit 条。

    按 logical_id 查而不是 file_id：用户关心的是"这个文件历史上解析成过没有"，
    换版本后旧证据仍然相关。入参可为历史版本 file_id，内部会归一到当前版本取 logical_id。
    """
    _row, metadata = current_file(db, workspace_id, file_id)
    rows = db.scalars(select(FileParseEvent).where(
        FileParseEvent.workspace_id == workspace_id,
        FileParseEvent.logical_id == metadata.logical_id,
    ).order_by(FileParseEvent.created_at.desc()).limit(limit)).all()
    return [{
        "id": item.id, "file_id": item.file_id, "operation": item.operation,
        "status": item.status, "parser": item.parser,
        "block_count": item.block_count, "char_count": item.char_count,
        "error_summary": item.error_summary, "actor_user_id": item.actor_user_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    } for item in rows]
