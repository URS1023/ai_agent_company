"""把宿主收到的 DSH 原生 present 文件登记为标准下载工件，不执行模型或业务查询。"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path
import stat

from sqlalchemy import select

from app.common.storage import get_storage, sha256_bytes
from app.config import settings
from app.core.parts import artifact_part
from app.errors import ConflictError, ValidationError
from app.models import Artifact, Message, Run
from app.common.authorization import require_resource
from app.modules.artifact.service import artifact_reference, create_artifact, infer_artifact_type
from app.modules.files.service import MAX_FILE_BYTES
from .chat import get_run, run_actor, session_identity
from .context import lock_for, session


MAX_PRESENT_FILES = 16
MAX_PRESENT_TOTAL_BYTES = 80 * 1024 * 1024
_ORIGIN = "dsh_native_present"


def reference_saved(run_id: str, files: list[dict]) -> dict:
    """复用当前任务已生成的工件；下载链接不是路径，整批验证后才补充消息卡片。"""
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_PRESENT_FILES:
        raise ValidationError("交付工件数量无效")
    with lock_for("tool:" + run_id), lock_for("events:" + run_id), session() as db:
        run = get_run(db, run_id)
        actor = run_actor(db, run)
        snapshot = run.context_snapshot or {}
        message = db.get(Message, snapshot.get("assistant_message_id"))
        if message is None or message.workspace_id != run.workspace_id or message.conversation_id != run.conversation_id:
            raise ValidationError("本次运行缺少可交付的助手消息")
        references = {}
        for item in files:
            match = re.fullmatch(r"/api/artifacts/([A-Za-z0-9_-]+)/download", str(item.get("path") or "")) if isinstance(item, dict) else None
            if not match or set(item) - {"path", "description"}:
                raise ValidationError("仅能复用工具返回的真实工件下载链接")
            row = db.get(Artifact, match.group(1))
            source = db.get(Run, row.run_id) if row else None
            same_task = bool(source and snapshot.get("task_id") and snapshot["task_id"] == (source.context_snapshot or {}).get("task_id"))
            if (not row or row.workspace_id != run.workspace_id or row.owner_user_id != run.started_by_user_id or row.status != "ready" or not source
                    or source.conversation_id != run.conversation_id or source.started_by_user_id != run.started_by_user_id
                    or (row.run_id != run.id and not same_task) or not row.storage_key or not get_storage().exists(row.storage_key)):
                raise ValidationError("工件不存在、未就绪或不属于当前会话任务")
            require_resource(actor, "files.view", row)
            references[row.id] = {**artifact_reference(row), "artifact_id": row.id, "status": row.status,
                                  "task_id": snapshot.get("task_id"), "source_run_id": row.run_id}
        preserved = [part for part in message.parts_json or [] if not (
            part.get("type") == "artifact" and (part.get("artifact") or {}).get("id") in references)]
        message.parts_json = preserved + [artifact_part(item) for item in references.values()]
        db.commit()
        return {"run_id": run.id, "artifacts": list(references.values()), "count": len(references), "already_presented": True}


def _workspace(run) -> Path:
    """沿用 chat.prepared 的服务端路径规则，不接受事件传入的 cwd 或会话目录。"""
    identity = session_identity(run.workspace_id, run.started_by_user_id, run.conversation_id)
    home = Path(settings.STORAGE_ROOT).resolve() / "dsh" / "sessions" / identity
    root = (home / "workspace").resolve()
    if not root.is_relative_to(home) or not root.is_dir():
        raise ValidationError("本次原生会话工作目录不可用")
    return root


def _read_files(root: Path, files: list[dict]) -> list[dict]:
    """先核验整批实际文件及总量，再登记工件，避免无效路径导致部分交付。"""
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_PRESENT_FILES:
        raise ValidationError(f"一次原生交付须包含 1 至 {MAX_PRESENT_FILES} 个文件")
    selected, seen, total = [], set(), 0
    for item in files:
        if not isinstance(item, dict) or set(item) - {"path", "description"}:
            raise ValidationError("原生交付仅接受 path 和 description，工件标识与展示名由服务端生成")
        raw_path, description = item.get("path"), item.get("description")
        if not isinstance(raw_path, str) or not raw_path.strip() or "\0" in raw_path:
            raise ValidationError("原生交付缺少有效的本地文件路径")
        if description is not None and (not isinstance(description, str) or len(description) > 2000):
            raise ValidationError("文件描述须为不超过 2000 字符的文本")
        candidate = Path(raw_path)
        try:
            path = (candidate if candidate.is_absolute() else root / candidate).resolve(strict=True)
            relative = path.relative_to(root)
        except (OSError, ValueError, RuntimeError) as exc:
            raise ValidationError("原生交付文件不存在或不在当前会话工作目录") from exc
        if any(":" in part for part in relative.parts) or not relative.parts:
            raise ValidationError("原生交付须指向工作目录内的普通文件")
        if path in seen:
            continue
        try:
            with path.open("rb") as stream:
                before = path.stat()
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                    raise ValidationError("原生交付须为普通文件，且单个文件不能超过 20 MiB")
                data = stream.read(MAX_FILE_BYTES + 1)
                after = path.stat()
        except OSError as exc:
            raise ValidationError("原生交付文件暂时无法读取，请完成写入后重新交付") from exc
        if (len(data) > MAX_FILE_BYTES or len(data) != before.st_size
                or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino)):
            raise ConflictError("文件在交付时仍发生变化，请完成写入后重新交付")
        total += len(data)
        if total > MAX_PRESENT_TOTAL_BYTES:
            raise ValidationError("一次原生交付的文件总量不能超过 80 MiB")
        from .delivery_evidence import preflight_office
        preflight_office(path.name, data)
        seen.add(path)
        selected.append({"path": relative.as_posix(), "name": path.name, "sha256": sha256_bytes(data),
                         "data": data, "description": description or ""})
    return selected


def present(run_id: str, files: list[dict]) -> dict:
    """只接收宿主原生事件，按运行、真实相对路径和源内容指纹幂等登记下载附件。"""
    # 与业务工具、消息事件共享既有锁，避免流式更新覆盖新附件卡片。
    with lock_for("tool:" + run_id), lock_for("events:" + run_id), session() as db:
        run = get_run(db, run_id)
        run_actor(db, run)
        snapshot = run.context_snapshot or {}
        message = db.get(Message, snapshot.get("assistant_message_id"))
        if message is None or message.workspace_id != run.workspace_id or message.conversation_id != run.conversation_id:
            raise ValidationError("本次运行缺少可交付的助手消息")
        selected = _read_files(_workspace(run), files)
        existing = list(db.scalars(select(Artifact).where(
            Artifact.run_id == run.id, Artifact.workspace_id == run.workspace_id,
            Artifact.owner_user_id == run.started_by_user_id).order_by(Artifact.created_at, Artifact.id)).all())
        existing = [row for row in existing if (row.metadata_json or {}).get("origin") == _ORIGIN]
        storage, references, replaced, created = get_storage(), [], set(), []
        try:
            for item in selected:
                versions = [row for row in existing if (row.metadata_json or {}).get("native_path") == item["path"]]
                exact = next((row for row in versions if (row.metadata_json or {}).get("source_sha256") == item["sha256"]
                              and row.storage_key and storage.exists(row.storage_key)), None)
                if exact is None:
                    kind = infer_artifact_type(item["name"], "file")
                    exact = create_artifact(db, workspace_id=run.workspace_id, run_id=run.id,
                        owner_user_id=run.started_by_user_id, type=kind, name=item["name"], data=item["data"],
                        mime_type=mimetypes.guess_type(item["name"])[0] or "application/octet-stream",
                        parent_artifact_id=versions[-1].id if versions else None,
                        metadata={"origin": _ORIGIN, "native_path": item["path"], "source_sha256": item["sha256"],
                                  "description": item["description"], "task_id": snapshot.get("task_id"), "source_run_id": run.id})
                    created.append(exact)
                    existing.append(exact)
                # Office 文件仍走标准字体规范化，源指纹与对象存储指纹分别保存，不能混用幂等条件。
                exact.status = "ready"
                replaced.update(row.id for row in versions)
                for row in versions:
                    if row.id != exact.id:
                        row.status = "superseded"
                references.append({**artifact_reference(exact), "artifact_id": exact.id, "status": exact.status,
                                   "task_id": snapshot.get("task_id"), "source_run_id": run.id})
            replaced.update(reference["id"] for reference in references)
            preserved = [part for part in message.parts_json or [] if not (
                part.get("type") == "artifact" and (part.get("artifact") or {}).get("id") in replaced)]
            message.parts_json = preserved + [artifact_part(reference) for reference in references]
            db.commit()
        except BaseException:
            db.rollback()
            # 仅清理本次已创建、尚未提交的标准存储对象，不触碰 DSH 原始文件或历史工件。
            for row in created:
                if row.storage_key and storage.exists(row.storage_key):
                    Path(storage.get_path(row.storage_key)).unlink(missing_ok=True)
            raise
        return {"run_id": run.id, "artifacts": references, "count": len(references)}
