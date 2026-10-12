"""将业务工具和沙箱产物转换成现有页面可下载的引用。"""
from pathlib import Path

from ...config import settings
from ...models import Artifact
from ...common.authorization import require_resource
from ..artifact.service import artifact_reference, create_artifact, infer_artifact_type
from ..business_ai.context import auth_context_from_tool


def persist_artifacts(db, run, name, result, cache):
    """只登记当前运行沙箱内的文件；复用工件必须重新验证当前用户的读取权限。"""
    candidates = list(result.artifacts or [])
    if isinstance(result.data, dict) and result.data.get("artifact_id"):
        candidates.append({"id": result.data["artifact_id"]})
    root = (Path(settings.STORAGE_ROOT) / "sandbox" / f"run_{run.id}").resolve()
    references = {}
    auth = None

    def authorized_artifact(artifact_id):
        """工具返回的 ID 不构成授权，卡片元数据与下载引用同样受资源范围保护。"""
        nonlocal auth
        if auth is None:
            auth = auth_context_from_tool({"db": db, "workspace_id": run.workspace_id,
                                           "user_id": run.started_by_user_id})
        row = db.get(Artifact, artifact_id, populate_existing=True)
        if row is None or row.workspace_id != run.workspace_id:
            raise ValueError("工具返回的工件不存在或不可访问")
        require_resource(auth, "files.view", row)
        return row

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        row = None
        if candidate.get("id"):
            row = authorized_artifact(candidate["id"])
        elif candidate.get("path"):
            path = Path(candidate["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("工具产物不在当前运行的沙箱目录")
            stat = path.stat()
            display = str(candidate.get("name") or path.name).replace("\\", "/").rsplit("/", 1)[-1]
            # 去重键含显示名：同一路径被工具以不同名称交付时按名称分别登记（评审 R25）。
            key = (str(path), display, stat.st_size, stat.st_mtime_ns)
            if key in cache:
                reference = artifact_reference(authorized_artifact(cache[key]["id"]))
                references[reference["id"]] = reference
                continue
            filename = display
            row = create_artifact(db, workspace_id=run.workspace_id, run_id=run.id,
                owner_user_id=run.started_by_user_id, type=infer_artifact_type(filename, "document"),
                name=filename, data=path.read_bytes(), metadata={"tool": name})
            cache[key] = artifact_reference(row)
        if row is not None:
            reference = artifact_reference(row)
            references[reference["id"]] = reference
    result.artifacts = list(references.values())
    if isinstance(result.data, dict) and candidates:
        result.data = {**result.data, "artifacts": result.artifacts}
        stdout = result.data.get("stdout")
        if isinstance(stdout, str):
            for candidate in candidates:
                if candidate.get("path"):
                    stdout = stdout.replace(str(candidate["path"]), "（文件见附件）")
                    stdout = stdout.replace(str(candidate["path"]).replace("\\", "/"), "（文件见附件）")
            result.data["stdout"] = stdout
    return result.artifacts
