"""按最终文件字节及已授权的服务端来源核验复合交付，不重发查询或渲染操作。"""
from __future__ import annotations

from io import BytesIO
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from pydantic import ValidationError as PayloadValidationError
from sqlalchemy import select

from app.common.storage import get_storage, sha256_bytes
from app.errors import AppError, ValidationError
from app.models import Artifact, ToolCall
from app.modules.chat_artifacts.artifact_inspection import inspect_file, _xml, _relations, _target, _PPT, _REL, _DRAWING, _SHEET
from app.modules.files.service import MAX_FILE_BYTES
from .delivery_contract import normalize_target


MAX_INSPECTION_BYTES = 80 * 1024 * 1024


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _revision_content(data: bytes, kind: str) -> dict:
    """从真实正文及实际关系引用构建指纹；孤立 ZIP 成员不能制造内容变化。"""
    if kind not in {"pptx", "docx", "xlsx"}:
        return {"content_sha256": hashlib.sha256(data).hexdigest()}
    with ZipFile(BytesIO(data)) as archive:
        marker = {"pptx": "ppt/presentation.xml", "docx": "word/document.xml", "xlsx": "xl/workbook.xml"}[kind]
        relations = _relations(archive, marker)
        roots, labels = [marker], []
        if kind == "pptx":
            roots = [_target(archive, relations, reference.get(_REL + "id"), "slide")
                     for reference in _xml(archive, marker).findall("./" + _PPT + "sldIdLst/*")]
        elif kind == "xlsx":
            sheets = _xml(archive, marker).findall("./" + _SHEET + "sheets/*")
            roots = [_target(archive, relations, sheet.get(_REL + "id"), "worksheet") for sheet in sheets]
            labels = [sheet.get("name", "") for sheet in sheets]
        trees = {name: _xml(archive, name) for name in roots}
        if kind == "xlsx":
            shared = next((row for row in relations.values() if row["type"].endswith("/sharedStrings")), None)
            strings = ["".join(node.text or "" for node in item.iter(_SHEET + "t"))
                       for item in _xml(archive, shared["target"]).findall(_SHEET + "si")] if shared else []
            for tree in trees.values():
                for cell in tree.iter(_SHEET + "c"):
                    if cell.get("t") == "s":
                        value = cell.find(_SHEET + "v")
                        value.text = strings[int(value.text)]
                        cell.set("t", "resolvedSharedString")

        def content_digest():
            parts, queue = {}, list(roots)
            for name in queue:
                if name in parts:
                    continue
                raw = archive.read(name)
                links = {}
                if name.endswith(".xml"):
                    tree = trees.setdefault(name, _xml(archive, name))
                    available = _relations(archive, name)
                    references = {value for node in tree.iter() for key, value in node.attrib.items() if key.startswith(_REL)}
                    for identity in references:
                        relation = available.get(identity)
                        if not relation or not relation["target"]:
                            raise ValueError("正文关系引用缺失")
                        links[identity] = relation
                        if not relation["external"]:
                            if relation["target"] not in archive.namelist():
                                raise ValueError("正文引用部件缺失")
                            queue.append(relation["target"])
                    raw = ET.canonicalize(ET.tostring(tree, encoding="unicode"), strip_text=True).encode()
                parts[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "links": links}
            return _digest({"roots": roots, "labels": labels, "parts": parts})

        result = {"content_sha256": content_digest()}
        if kind != "pptx":
            return result
        titles = []
        for name in roots:
            page = trees[name]
            title_shapes = [shape for shape in page.iter(_PPT + "sp")
                            if (placeholder := shape.find("./" + _PPT + "nvSpPr/" + _PPT + "nvPr/" + _PPT + "ph")) is not None
                            and placeholder.get("type") in {"title", "ctrTitle"}]
            titles.append("".join(node.text or "" for shape in title_shapes for node in shape.iter(_DRAWING + "t")))
            for shape in title_shapes:
                for node in shape.iter(_DRAWING + "t"):
                    node.text = ""
        return {**result, "page_titles": titles, "non_title_content_sha256": content_digest()}


def preflight_office(name: str, data: bytes) -> None:
    """原生文件进入字体规范化前先限定 ZIP 展开量，避免先解压再验证。"""
    if Path(name).suffix.lower() not in {".pptx", ".docx", ".xlsx"}:
        return
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if (len(entries) > 12_000 or len(names) != len(set(names))
                    or sum(item.file_size for item in entries) > 256 * 1024 * 1024
                    or any(item.filename.endswith((".xml", ".rels")) and item.file_size > 12 * 1024 * 1024 for item in entries)):
                raise ValidationError("Office 文件超过安全解析范围，不能登记为完整交付")
    except BadZipFile as exc:
        raise ValidationError("Office 文件结构损坏，请完成保存后重新交付") from exc


class Inspector:
    """单次验收共享有界读取预算；任何缓存仅限本轮，不能越过最终 SHA 校验。"""

    def __init__(self, *, revisions=False):
        self.used = 0
        self.cache = {}
        self.revisions = revisions

    def inspect(self, artifact) -> dict:
        if artifact.id in self.cache:
            return self.cache[artifact.id]
        result = {}
        try:
            if artifact.status != "ready" or not artifact.storage_key or self.used >= MAX_INSPECTION_BYTES:
                return result
            with Path(get_storage().get_path(artifact.storage_key)).open("rb") as stream:
                data = stream.read(min(MAX_FILE_BYTES, MAX_INSPECTION_BYTES - self.used) + 1)
            self.used += len(data)
            if len(data) > MAX_FILE_BYTES or self.used > MAX_INSPECTION_BYTES or sha256_bytes(data) != artifact.sha256:
                return result
            result = inspect_file(artifact.name, data)
            if not result and self.revisions and artifact.type not in {"presentation", "document", "spreadsheet", "image"}:
                # 其他格式继承既有最终字节合同，不把未提供的格式解析器伪装成内容语义验证。
                result = {"kind": "opaque_file"}
            if result:
                result = {**result, "artifact_id": artifact.id, "sha256": artifact.sha256,
                          "producer": "compound_inspector.v1"}
                if self.revisions:
                    result.update(_revision_content(data, result["kind"]))
            if result.get("kind") == "pptx":
                # 名称不能证明具名 PPT 的对象归属，只使用正文第一张可达幻灯片的实际文字。
                with ZipFile(BytesIO(data)) as archive:
                    marker = "ppt/presentation.xml"
                    first = _xml(archive, marker).find("./" + _PPT + "sldIdLst/*")
                    slide = _target(archive, _relations(archive, marker), first.get(_REL + "id"), "slide")
                    result["opening_text"] = "".join(node.text or "" for node in _xml(archive, slide).iter(_DRAWING + "t"))
        except (OSError, ValueError, KeyError, BadZipFile, ET.ParseError):
            result = {}
        self.cache[artifact.id] = result
        return result


def _revision_proofs(db, run, produced, source_runs, *, actor=None, strict=False) -> dict:
    """修订和继承都重新核验当前文件授权、实际字节和来源，不调用任何生成/查询工具。"""
    from .chat import run_actor
    from app.common.authorization import require_resource
    from app.modules.reports.models import Report
    actor = actor or run_actor(db, run)
    inspector, output = Inspector(revisions=True), {}
    allowed_runs = {row.id for row in source_runs if row.created_at <= run.created_at}
    for item in produced:
        if item["status"] != "ready" or item["source_run_id"] not in allowed_runs:
            continue
        if item["type"] == "dashboard":
            report = db.get(Report, item["report_id"])
            require_resource(actor, "dashboards.view", report)
            output[item["id"]] = {"id": item["id"], "type": "dashboard", "label": report.name,
                "report_id": report.id, "report_version": report.version, "source_run_id": item["source_run_id"],
                "created_at": report.updated_at.isoformat(), "content_sha256": _digest({"name": report.name, "content": report.content})}
            continue
        row = db.get(Artifact, item.get("artifact_id"))
        if row is None or row.workspace_id != run.workspace_id or row.owner_user_id != run.started_by_user_id:
            continue
        try:
            require_resource(actor, "files.view", row)
        except AppError:
            if strict:
                raise
            continue
        proof = inspector.inspect(row)
        if proof:
            output[row.id] = {**proof, "id": row.id, "type": row.type, "source_run_id": row.run_id,
                "label": proof.get("opening_text") or row.name, "created_at": row.created_at.isoformat()}
    return output


def prepare_revision(db, source_run, task: dict, content: str, *, actor=None) -> dict:
    """首次修订为已有交付建立稳定对象；不凭文件名或模型声称补造验收。"""
    from app.ids import new_id
    from .tasks import deliveries, task_runs
    from .delivery_contract import revise_objects
    source_runs = task_runs(db, source_run)

    def bind_request_epoch(result):
        previous_objects = {row["object_id"]: row for row in
                            ((source_run.context_snapshot or {}).get("business_task") or {}).get("delivery_objects", [])}
        for obj in result["delivery_objects"]:
            if obj.get("change") and obj.get("requested_at") != previous_objects.get(obj["object_id"], {}).get("requested_at"):
                # 旧轮次迟到的新文件仍属旧要求；不能仅靠登记时间越过新修订边界。
                obj["excluded_source_runs"] = [row.id for row in source_runs if row.created_at <= source_run.created_at]
        return result

    if "delivery_objects" in task:
        return bind_request_epoch(task)
    keys = ("resource_access_criteria", "actor_user_id", "actor_workspace_id")
    previous = {key: db.info[key] for key in keys if key in db.info}
    try:
        from app.common.authorization import require_permission
        from .chat import run_actor
        required_types = {row["type"] for row in task.get("required_deliveries", [])}
        if required_types - {"dashboard"}:
            # ORM 可能先隐藏文件行；不能等到逐行鉴权才发现“空集合”实际是没有读取权限。
            require_permission(actor or run_actor(db, source_run), "files.view")
        produced = deliveries(db, source_run)
        proofs = _revision_proofs(db, source_run, produced, source_runs, actor=actor, strict=True)
        states = {row["id"]: row["status"] for row in produced}
        saved = [row for source in source_runs for row in (source.context_snapshot or {}).get("deliveries", [])
                 if row.get("status") == "ready"]
        if any(row["type"] in required_types and states.get(row["id"], row["status"]) != "superseded"
               and row["id"] not in proofs for row in produced + saved):
            # 无法读取已有内容不等于没有基线；恢复原证据后才能开始可核验的修订。
            raise ValidationError("原交付的实际内容暂不可核验，请恢复文件或大屏证据后再修改")
        objects = []
        old_receipts = ((source_run.context_snapshot or {}).get("business_task") or {}).get("constraint_receipts", [])
        for required in task.get("required_deliveries", []):
            candidates = [proof for proof in proofs.values() if proof["type"] == required["type"]]
            ambiguous = len(candidates) > required["count"]
            # 历史多于合同份数时不猜“最新文件”；后续要求必须明确对象。
            if len(candidates) != required["count"]:
                candidates = []
            for ordinal in range(1, required["count"] + 1):
                accepted = candidates[ordinal - 1] if candidates else None
                objects.append({"object_id": new_id(), "type": required["type"], "ordinal": ordinal, "revision": 1,
                    "label": accepted["label"] if accepted else "", "fulfillment": accepted,
                    "baseline_ambiguous": ambiguous,
                    "constraint_ids": [row["requirement_id"] for row in old_receipts
                                       if accepted and row.get("artifact_id") == accepted["id"]]})
        return bind_request_epoch(revise_objects({**task, "delivery_objects": objects}, content))
    finally:
        for key in keys:
            if key in previous:
                db.info[key] = previous[key]
            else:
                db.info.pop(key, None)


def _satisfies_revision(obj: dict, proof: dict) -> bool:
    change = obj.get("change")
    if not change:
        return True
    if proof["created_at"] < obj["requested_at"] or proof["source_run_id"] in obj.get("excluded_source_runs", []):
        return False
    from .delivery_contract import revision_checks
    base, checks = obj.get("base"), revision_checks(change)
    if any(check["kind"] == "unsupported" for check in checks):
        return False
    if base and proof.get("content_sha256") == base.get("content_sha256"):
        return False
    if proof["type"] == "dashboard" and base and proof.get("report_id") == base.get("report_id"):
        if proof.get("report_version", 0) <= base.get("report_version", 0) or proof["source_run_id"] == base["source_run_id"]:
            return False
    titles = proof.get("page_titles", [])
    changed_pages = {check["page"] - 1 for check in checks if check["kind"] == "slide_title"}
    for check in checks:
        if check["kind"] == "page_count" and not check["minimum"] <= proof.get("pages", 0) <= check["maximum"]:
            return False
        if check["kind"] == "slide_title":
            page = check["page"] - 1
            if page >= len(titles) or titles[page] != check["value"]:
                return False
    if changed_pages and base:
        before = base.get("page_titles", [])
        if (base.get("non_title_content_sha256") != proof.get("non_title_content_sha256")
                or len(before) != len(titles)
                or any(title != titles[index] for index, title in enumerate(before) if index not in changed_pages)):
            return False
    return True


def verify_revision_objects(db, run, task, produced, source_runs):
    """逐份验收资格与原有页数/嵌图约束共用，不能由新 Run 或新 SHA 单独通关。"""
    keys = ("resource_access_criteria", "actor_user_id", "actor_workspace_id")
    previous = {key: db.info[key] for key in keys if key in db.info}
    accepted, failures = [], []
    try:
        proofs = _revision_proofs(db, run, produced, source_runs)
        used = set()
        objects = deepcopy(task["delivery_objects"])

        def qualifies(obj, proof):
            if obj.get("baseline_ambiguous") or not _satisfies_revision(obj, proof):
                return False
            # 新证据必须先满足该对象已有内容合同，不能先绑定坏文件而阻塞下一轮真正修好。
            constraints = [row for row in task.get("delivery_constraints", []) if row["type"] == obj["type"]
                           and (row["requirement_id"] in obj.get("constraint_ids", []) or not row.get("target")
                                or not obj.get("constraint_ids"))]
            if not constraints:
                return True
            scoped = {**task, "delivery_constraints": [{**row, "count": 1} for row in constraints],
                      "delivery_objects": [{**obj, "fulfillment": proof}]}
            failures, _ = verify_constraints(db, run, scoped, produced, source_runs, eligible_ids={proof["id"]})
            return not failures

        for obj in sorted(objects, key=lambda row: not bool(row.get("fulfillment"))):
            prior = obj.get("fulfillment")
            candidates = [proofs[prior["id"]]] if prior and prior["id"] in proofs else []
            if prior:
                candidates = [proof for proof in candidates if proof.get("sha256", proof.get("content_sha256"))
                              == prior.get("sha256", prior.get("content_sha256"))]
            else:
                candidates = [proof for proof in proofs.values() if proof["type"] == obj["type"]]
            matched = next((proof for proof in candidates if proof["id"] not in used and qualifies(obj, proof)), None)
            # 撤权或存储损坏不抹掉原绑定，下一轮不能趁空位继承另一份旧文件。
            obj["fulfillment"] = matched or prior
            if matched:
                used.add(matched["id"])
                accepted.append({"object_id": obj["object_id"], "revision": obj["revision"], **matched})
            else:
                failures.append({"object_id": obj["object_id"], "revision": obj["revision"], "type": obj["type"],
                    "code": "delivery_revision_unverified", "message": "该份成果的实际内容、来源或继承证据尚未满足当前修订"})
        task["delivery_objects"] = objects
        if task.get("revision_unresolved"):
            failures.append({"code": "delivery_revision_ambiguous", "message": task["revision_unresolved"]["error"]})
        return accepted, failures
    except AppError:
        return [], [{"code": "delivery_authorization_required", "message": "当前身份不能核验修订成果，请恢复授权后继续"}]
    finally:
        for key in keys:
            if key in previous:
                db.info[key] = previous[key]
            else:
                db.info.pop(key, None)


def _trusted_image_ids(db, run, run_ids, report_id) -> set[str]:
    """内部来源回执只按已核对任务归属读取，不依赖用户是否拥有运行日志查看权。"""
    calls = ToolCall.__table__
    rows = db.execute(select(calls.c.result_json).where(calls.c.workspace_id == run.workspace_id,
        calls.c.run_id.in_(run_ids), calls.c.tool_name == "render_dashboard_image",
        calls.c.status.in_(["success", "completed"]))).scalars()
    result = set()
    for receipt in rows:
        data = (receipt or {}).get("data") or {}
        if isinstance(data, dict) and data.get("dashboard_id") == report_id and data.get("artifact_id"):
            result.add(data["artifact_id"])
    return result


def _dashboard_images(db, run, source_runs, produced, constraint, artifacts, inspector):
    """图片来源必须指向唯一当前可读大屏及其当前版本，历史无证明时保持未完成。"""
    from .chat import run_actor
    from app.common.authorization import require_resource
    from app.modules.reports.service import get_report
    from app.modules.chat_artifacts.dashboard_image import dashboard_panels

    target = constraint.get("target", "")
    dashboards = [row for row in produced if row["type"] == "dashboard" and row["status"] == "ready"
                  and (not target or target in normalize_target(row["name"]) or target == row["report_id"].casefold())]
    # 身份重建会绑定会话过滤器；核验成功或异常均恢复调用方的三个会话字段。
    keys = ("resource_access_criteria", "actor_user_id", "actor_workspace_id")
    previous = {key: db.info[key] for key in keys if key in db.info}
    try:
        actor = run_actor(db, run)
        # 明确引用既有大屏时，允许本任务真实渲染回执指向它；不能用任意旧图片猜测对象。
        bound = constraint.get("bound_report_id")
        candidates = ({bound: {"report_id": bound}} if bound else {row["report_id"]: row for row in dashboards})
        for artifact in artifacts:
            if bound:
                break
            proof = (artifact.metadata_json or {}).get("delivery_evidence") or {}
            report_id = proof.get("dashboard_id")
            if artifact.type != "image" or not report_id or report_id in candidates:
                continue
            if artifact.id not in _trusted_image_ids(db, run, [row.id for row in source_runs], report_id):
                continue
            linked = get_report(actor, report_id)
            if not target or target in normalize_target(linked.name) or target == linked.id.casefold():
                candidates[report_id] = {"report_id": linked.id, "report_version": linked.version}
        if len(candidates) != 1:
            return [], "指定的大屏尚未交付，或同名大屏无法唯一匹配"
        dashboard = next(iter(candidates.values()))
        report = get_report(actor, dashboard["report_id"])
        # 第一次唯一解析后固化对象身份；续办不能用另一张同名大屏悄悄替换原目标。
        constraint["bound_report_id"] = report.id
        version = constraint.get("requested_report_version")
        constraint["report_version_policy"] = "explicit" if version is not None else "current"
        if version is not None and version != report.version:
            return [], f"指定的大屏版本 {version} 尚无可核验的完整历史来源，不能以当前版本代替"
        selected = set((run.context_snapshot or {}).get("datasource_ids") or [])
        if selected and not set(report.datasource_ids or []).issubset(selected):
            return [], "指定大屏超出本次数据范围"
        # 只重新授权已保存快照，不执行 SQL、源工作流或生成图片。
        panels = dashboard_panels(actor, report, selected_datasource_ids=selected)
        query_ids = {panel["query_id"] for panel in panels if panel.get("query_id")}
        execution_ids = {identity for panel in panels for identity in panel.get("source_execution_ids", [])}
        image_ids = _trusted_image_ids(db, run, [row.id for row in source_runs], report.id)
        images = []
        for artifact in artifacts:
            if artifact.type != "image" or artifact.id not in image_ids:
                continue
            metadata = artifact.metadata_json or {}
            proof = metadata.get("delivery_evidence") or {}
            if (metadata.get("tool") != "render_dashboard_image" or proof.get("producer") != "server_renderer.v1"
                    or proof.get("kind") != "dashboard_image" or proof.get("dashboard_id") != report.id
                    or proof.get("dashboard_version") != report.version
                    or set(proof.get("source_query_ids") or []) != query_ids
                    or set(proof.get("source_execution_ids") or []) != execution_ids):
                continue
            require_resource(actor, "files.view", artifact)
            checked = inspector.inspect(artifact)
            if checked.get("kind") == "image":
                images.append({**checked, "report_id": report.id, "report_version": report.version})
        return images, "缺少当前指定大屏版本的可信图片来源或实际嵌入图片"
    except (AppError, PayloadValidationError):
        return [], "当前身份或数据范围不能核验指定大屏及原图"
    finally:
        for key in keys:
            if key in previous:
                db.info[key] = previous[key]
            else:
                db.info.pop(key, None)


def _verify_pages(constraints, eligible, inspector, bindings=None):
    """对要求与真实文件做唯一匹配，泛指文件不能抢占仅能满足具名要求的文件。"""
    if not constraints:
        return [], []
    checked = {row.id: inspector.inspect(row) or {"artifact_id": row.id, "validation": "unreadable_or_changed"}
               for row in eligible if row.type == "presentation"}
    candidates, observed, units = {}, {}, []
    for index, constraint in enumerate(constraints):
        target = constraint.get("target")
        bound = (bindings or {}).get(constraint["requirement_id"])
        observed[index] = [row for row in checked.values() if row["artifact_id"] in bound] if bound else [
            row for row in checked.values() if not target or target in normalize_target(row.get("opening_text", ""))]
        candidates[index] = [row["artifact_id"] for row in observed[index]
                             if constraint["minimum"] <= row.get("pages", 0) <= constraint["maximum"]]
        units.extend([index] * constraint.get("count", 1))
    owners = {}
    for start in range(len(units)):
        # 用有界图遍历寻找增广路径，避免靠排序猜测或递归挤满栈。
        queue, visited, parents, finish = [start], {start}, {}, None
        for unit in queue:
            for artifact_id in candidates[units[unit]]:
                owner = owners.get(artifact_id)
                if owner is None:
                    finish = (unit, artifact_id)
                    break
                if owner not in visited:
                    visited.add(owner)
                    parents[owner] = (unit, artifact_id)
                    queue.append(owner)
            if finish:
                break
        if finish:
            unit, artifact_id = finish
            while True:
                owners[artifact_id] = unit
                if unit == start:
                    break
                unit, artifact_id = parents[unit]
    failures, receipts = [], []
    for index, constraint in enumerate(constraints):
        matched = [checked[identity] for identity, unit in owners.items() if units[unit] == index]
        receipts.extend({"requirement_id": constraint["requirement_id"], **row} for row in matched)
        if len(matched) < constraint.get("count", 1):
            failures.append({"requirement_id": constraint["requirement_id"], "code": "presentation_page_count",
                "message": "PPT 实际非空总页数或对象对应关系未满足要求", "target": constraint.get("target", ""),
                "expected": {"minimum": constraint["minimum"], "maximum": constraint["maximum"], "count": constraint.get("count", 1)},
                "observed": observed[index]})
    return failures, receipts


def _verify_constraints(db, run, task, produced, source_runs, *, eligible_ids=None) -> tuple[list[dict], list[dict]]:
    """失败只改变任务完整性，不删除部分成果，也不自动重发有副作用的工具。"""
    constraints = task.get("delivery_constraints") or []
    if not constraints:
        return [], []
    artifacts = list(db.scalars(select(Artifact).where(Artifact.workspace_id == run.workspace_id,
        Artifact.owner_user_id == run.started_by_user_id, Artifact.run_id.in_([item.id for item in source_runs]),
        Artifact.status == "ready")))
    eligible = [row for row in artifacts if row.id in eligible_ids] if eligible_ids is not None else [
        row for row in artifacts if not task.get("requires_new_delivery") or row.run_id == run.id]
    inspector = Inspector()
    bindings = {}
    for obj in task.get("delivery_objects", []):
        if obj.get("fulfillment"):
            for identity in obj.get("constraint_ids", []):
                bindings.setdefault(identity, set()).add(obj["fulfillment"]["id"])
    failures, receipts = _verify_pages([row for row in constraints if row["kind"] == "page_count"], eligible, inspector, bindings)
    for constraint in constraints:
        identity = constraint["requirement_id"]
        if constraint["kind"] == "embedded_dashboard":
            images, reason = _dashboard_images(db, run, source_runs, produced, constraint, artifacts, inspector)
            matched = []
            for artifact in eligible:
                if artifact.type != "document":
                    continue
                checked = inspector.inspect(artifact)
                image = next((image for image in images if image["sha256"] in checked.get("images", [])), None)
                if image:
                    matched.append({"requirement_id": identity, **checked, "embedded_image": image})
            needed = constraint.get("count", 1)
            receipts.extend(matched[:needed])
            if len(matched) < needed:
                if matched:
                    reason = f"要求 {needed} 份 Word 分别嵌入指定大屏，当前只有 {len(matched)} 份通过核验"
                failures.append({"requirement_id": identity, "code": "embedded_dashboard_missing",
                    "message": reason, "target": constraint.get("target", ""),
                    "expected_documents": needed, "verified_documents": len(matched),
                    "verified_image_ids": [image["artifact_id"] for image in images]})
    return failures, receipts


def verify_constraints(db, run, task, produced, source_runs, *, eligible_ids=None) -> tuple[list[dict], list[dict]]:
    """文件证据也服从当前身份，不因后台验收绕过撤权；恢复调用方会话范围。"""
    if not task.get("delivery_constraints"):
        return [], []
    from .chat import run_actor
    keys = ("resource_access_criteria", "actor_user_id", "actor_workspace_id")
    previous = {key: db.info[key] for key in keys if key in db.info}
    try:
        run_actor(db, run)
        return _verify_constraints(db, run, task, produced, source_runs, eligible_ids=eligible_ids)
    except AppError:
        return [{"requirement_id": row["requirement_id"], "code": "delivery_authorization_required",
                 "message": "当前身份不能核验交付证据，请恢复授权后继续"} for row in task["delivery_constraints"]], []
    finally:
        for key in keys:
            if key in previous:
                db.info[key] = previous[key]
            else:
                db.info.pop(key, None)
