"""核对用户明确要求的附件交付，模型结束输出不等于文件制作完成。"""
from __future__ import annotations

import re

from ...common.storage import get_storage
from ...models import Artifact
from .artifact_inspection import inspect_file
from .requirements import requested_outputs


class DeliveryTracker:
    """只使用当前运行中已鉴权并真实保存的工具回执核对交付。"""

    def __init__(self, content: str):
        self.required = requested_outputs(content)
        self.files: dict[str, dict] = {}
        self.dashboard = False
        self.dashboard_ids: set[str] = set()
        self.dashboard_images: set[str] = set()
        self.rejected_files: dict[str, dict] = {}
        self.blocker = ""
        self.progress = None
        self.continuations = 0
        self.previous_signature = None
        self.stalled = 0
        self.best_written_pages = 0
        self.best_presentation_match = 0

    def _image_source(self, run, row, name: str, data: dict, *, trusted_source_run_id: str | None = None) -> dict:
        """图类和大屏来源只接受服务端生产回执，文件名或模型参数不能创建来源证明。"""
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        allowed_runs = {run.id}
        resumed_from = (run.context_snapshot or {}).get("resumed_from_run_id")
        if isinstance(resumed_from, str) and resumed_from:
            allowed_runs.add(resumed_from)
        if trusted_source_run_id:
            # 此字段只由服务端逐条鉴权后的恢复回执传入，不读取模型参数或回执自带字段。
            allowed_runs.add(trusted_source_run_id)
        if row.run_id not in allowed_runs:
            return {}
        proof = metadata.get("delivery_evidence") or {}
        if not isinstance(proof, dict):
            return {}
        if name in {"render_chart_image", "render_dashboard_image"} and metadata.get("tool") == name:
            if proof.get("producer") != "server_renderer.v1":
                return {}
            if name == "render_chart_image" and proof.get("kind") == "chart" and proof.get("source_query_id"):
                declared_types = proof.get("chart_types")
                if not isinstance(declared_types, list) or not all(isinstance(value, str) for value in declared_types):
                    return {}
                types = sorted(set(declared_types).intersection({"pie", "bar", "line", "area", "scatter"}))
                if types:
                    return {"kind": "chart", "chart_types": types, "source": "server_renderer.v1",
                            "source_query_id": proof["source_query_id"]}
            if name == "render_dashboard_image" and proof.get("kind") == "dashboard_image":
                dashboard_id = str(proof.get("dashboard_id") or "")
                # 专用工具已校验持久化大屏归属；回执与落库元数据必须指向同一个大屏。
                returned_id = str(data.get("dashboard_id") or data.get("reportId") or "")
                if dashboard_id and returned_id == dashboard_id:
                    return {"kind": "dashboard_image", "dashboard_id": dashboard_id,
                            "source": "server_renderer.v1", "source_query_ids": proof.get("source_query_ids") or []}
        if (name == "export_inspection_report" and metadata.get("source") == "saved_inspection_reports"
                and data.get("resultType") == "inspection_report_export"
                and isinstance(data.get("facts"), dict) and data["facts"].get("checkCount", 0) > 0
                and metadata.get("facts") == data["facts"]):
            # 固定服务端脚本确实渲染饼图和条形图；这是检查项统计，不能推断未巡检设备数。
            return {"kind": "chart", "chart_types": ["bar", "pie"], "source": "saved_inspection_reports"}
        return {}

    def observe(self, db, run, name, args, result, *, trusted_source_run_id: str | None = None):
        """收集真实文件与大屏创建回执，不根据模型的完成声明修改状态。"""
        metadata = result.metadata or {}
        if isinstance(metadata.get("presentation_progress"), dict):
            self.progress = metadata["presentation_progress"]
        if result.status != "success":
            summary = str(result.error_summary or "")
            if metadata.get("requires_user_input") and not summary:
                data = result.data if isinstance(result.data, dict) else {}
                question = data.get("clarification") or metadata.get("clarification")
                if isinstance(question, dict):
                    question = question.get("question") or question.get("message")
                summary = str(question or "需要补充本次任务的业务范围后才能继续")
            if (metadata.get("requires_user_input") or result.error_code in {"forbidden", "unauthorized"}
                    or re.search(r"超时|连接中断|连接失败|timed?\s*out|connection.*(?:lost|refused)", summary, re.I)):
                self.blocker = summary or "当前操作缺少必要权限，请先解决权限范围"
            return
        if name in {"create_presentation_plan", "write_presentation_slides"} and isinstance(result.data, dict):
            self.progress = result.data
        if name in {"create_business_dashboard", "update_business_dashboard", "create_dashboard_from_queries"} and isinstance(result.data, dict):
            if result.data.get("reportId") or result.data.get("dashboard_id"):
                self.dashboard = True
                self.dashboard_ids.add(str(result.data.get("reportId") or result.data["dashboard_id"]))
        if name == "export_inspection_report" and isinstance(result.data, dict):
            dashboard = result.data.get("dashboard")
            if (result.data.get("resultType") == "inspection_report_export" and isinstance(dashboard, dict)
                    and dashboard.get("reportId")):
                self.dashboard = True
                self.dashboard_ids.add(str(dashboard["reportId"]))
        for reference in result.artifacts or []:
            if not isinstance(reference, dict):
                continue
            row = db.get(Artifact, reference.get("id"))
            if row is None or row.workspace_id != run.workspace_id or not row.storage_key or row.status != "ready":
                continue
            try:
                evidence = inspect_file(row.name, get_storage().read_bytes(row.storage_key))
            except (OSError, ValueError):
                continue
            if evidence:
                if evidence.get("kind") == "image":
                    source = self._image_source(run, row, name, result.data if isinstance(result.data, dict) else {},
                                                trusted_source_run_id=trusted_source_run_id)
                    evidence.update(source)
                    if source.get("kind") == "dashboard_image":
                        self.dashboard_ids.add(source["dashboard_id"])
                        self.dashboard = True
                        self.dashboard_images.add(evidence["digest"])
                    elif not source:
                        evidence["validation_note"] = "图片可读取且非空；尚无可信图表类型或大屏来源回执"
                self.files[row.id] = {**evidence, "id": row.id, "name": row.name}
                self.rejected_files.pop(row.id, None)
            else:
                self.files.pop(row.id, None)
                self.rejected_files[row.id] = {"id": row.id, "name": row.name,
                    "reason": "附件损坏、内容为空、图片尺寸过小或内部引用不完整，不能计为已交付"}

    def status(self) -> dict:
        """返回可回灌给原生任务的缺项，完整性与业务数据准确性分别验证。"""
        missing = []
        rows = list(self.files.values())
        for kind, expected in self.required.items():
            if kind == "chart_types":
                continue
            matches = [row for row in rows if row.get("kind") == kind]
            if kind == "dashboard":
                complete = self.dashboard
            elif kind == "embedded_dashboard":
                complete = any(self.dashboard_images.intersection(row.get("images", []))
                               for row in rows if row.get("kind") == "docx")
            elif kind == "pptx" and isinstance(expected, int) and not isinstance(expected, bool):
                complete = any(row.get("pages") == expected for row in matches)
            elif kind == "chart":
                actual_types = {value for row in matches for value in row.get("chart_types", [])}
                complete = bool(matches) and set(self.required.get("chart_types") or []).issubset(actual_types)
            else:
                complete = bool(matches)
            if not complete:
                missing.append(f"{expected} 页 PPT" if kind == "pptx" and type(expected) is int else {
                    "docx": "Word 文件", "xlsx": "Excel 文件", "pptx": "PPT 文件", "pdf": "PDF 文件",
                    "dashboard": "可打开的数字大屏", "chart": "图表图片",
                    "embedded_dashboard": "Word 中嵌入的大屏图片（须与大屏图片附件一致）",
                }[kind])
        return {"required": self.required, "missing": missing, "files": rows,
                "presentation_progress": self.progress, "blocker": self.blocker,
                "rejected_files": list(self.rejected_files.values())[-10:],
                "validation_scope": "核验文件结构、内容非空、图类/大屏来源和嵌入关系；不替代数据库口径及数字一致性验收"}

    def continuation(self) -> str | None:
        """缺项有进展时补交；无进展或依赖阻断时停止重复执行，不设整轮时限。"""
        status = self.status()
        if not status["missing"]:
            return None
        # 只累计与缺项相关的实际进度，新增无关附件、改标题或反复重建大纲不能延长空转。
        if "pptx" in self.required and isinstance(self.progress, dict):
            count = self.progress.get("written_count")
            body_count = self.progress.get("body_page_count")
            total_count = self.progress.get("total_page_count")
            expected = self.required["pptx"]
            if (type(count) is int and type(body_count) is int and 0 <= count <= body_count <= 50
                    and (type(expected) is not int or total_count == expected)):
                self.best_written_pages = max(self.best_written_pages, count)
        expected = self.required.get("pptx")
        if type(expected) is int:
            for row in self.files.values():
                if row.get("kind") == "pptx" and type(row.get("pages")) is int:
                    self.best_presentation_match = max(self.best_presentation_match, expected - abs(row["pages"] - expected))
        matched_chart_types = tuple(sorted(set(self.required.get("chart_types") or []).intersection(
            value for row in self.files.values() if row.get("kind") == "chart" for value in row.get("chart_types", []))))
        signature = (tuple(status["missing"]), self.best_written_pages, self.best_presentation_match, matched_chart_types)
        self.stalled = self.stalled + 1 if signature == self.previous_signature else 0
        self.previous_signature = signature
        if self.blocker or self.stalled >= 2:
            raise DeliveryIncomplete("尚未完成：" + "、".join(status["missing"]) +
                                     ("。需要先解决：" + self.blocker if self.blocker else "。补交连续未完成，已保留现有产物。"))
        self.continuations += 1
        import json
        return ("系统交付核验：原请求仍未全部完成，请继续同一任务，只补齐缺项，不重复创建成功的结果。"
                "不要仅回复‘继续生成’。根据工具的字段和页码提示修正参数。不要重查已取得的数据；"
                "不要重试数据库超时、断连或权限失败。饼图等须用 render_chart_image 引用已成功查询，"
                "大屏图片须用 render_dashboard_image 引用已保存大屏；文件改名不构成图类或来源证明。"
                "通过 render_document 的 image 块 artifact_id 将真实大屏图片嵌入 Word。核验记录："
                + json.dumps(status, ensure_ascii=False))


class DeliveryIncomplete(Exception):
    """文件交付缺项，禁止把运行标记为已完成。"""
