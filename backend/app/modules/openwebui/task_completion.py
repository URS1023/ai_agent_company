"""核对原生任务清单，未完成事项不能被一句结束语覆盖。"""
from __future__ import annotations

import json


class TaskCompletionGuard:
    """区分当前任务与会话旧清单，并保留被后续清单意外遗漏的事项。"""

    def __init__(self, restored=None):
        self.tasks: dict[str, dict] = {}
        self.baseline = []
        self.active = bool(restored)
        self.previous_signature = None
        self.stalled = 0
        if restored:
            self.observe(restored)

    @staticmethod
    def normalize(tasks) -> list[dict]:
        """只接受有内容的原生任务；异常状态保持待处理，避免误算完成。"""
        if not isinstance(tasks, list):
            return []
        result = []
        for item in tasks:
            if not isinstance(item, dict) or not str(item.get("content") or "").strip():
                continue
            content = str(item["content"]).strip()
            status = item.get("status")
            result.append({"id": str(item.get("id") or content), "content": content,
                           "status": status if status in {"completed", "in_progress", "pending", "cancelled"} else "pending"})
        return result

    def set_baseline(self, tasks):
        """会话上的旧清单不属于新的无关提问；显式恢复任务则继续核对旧项。"""
        self.baseline = self.normalize(tasks)

    def observe(self, tasks):
        """新事件表示本轮已使用清单；删除待办不能冒充已完成。"""
        self.active = True
        for item in self.normalize(tasks):
            # 稳定编号优先，允许细化文案；重新编号时再按内容匹配，避免生成无法更新的旧待办。
            existing = next((key for key, row in self.tasks.items() if row["id"] == item["id"]), None)
            if existing is None:
                existing = next((key for key, row in self.tasks.items() if row["content"] == item["content"]), None)
            key = existing or item["content"]
            self.tasks[key] = item

    def reconcile(self, tasks):
        """从原生持久化记录核对任务，补偿丢失的实时事件。"""
        rows = self.normalize(tasks)
        if self.active or rows != self.baseline:
            self.observe(rows)

    def status(self) -> dict:
        """模型自行取消的事项也不能满足用户原始要求。"""
        rows = list(self.tasks.values())
        pending = [item for item in rows if item["status"] != "completed"]
        return {"tasks": rows, "missing": [item["content"] for item in pending],
                "completed": sum(item["status"] == "completed" for item in rows)}

    def continuation(self, *, reply_errors=None, failed_tools=None, blocker="") -> str | None:
        """有进展就继续；连续原地重复时保留缺项，不伪造成功或无限空转。"""
        from .delivery import DeliveryIncomplete

        status = self.status()
        errors = list(reply_errors or [])
        failures = list(failed_tools or [])
        if not status["missing"] and not errors and not failures:
            return None
        signature = json.dumps({"missing": sorted(status["missing"]), "errors": errors,
                                "failed_tools": sorted({str(item.get("name") or item.get("tool") or "")
                                                         for item in failures})}, ensure_ascii=False, sort_keys=True)
        self.stalled = self.stalled + 1 if signature == self.previous_signature else 0
        self.previous_signature = signature
        if blocker or self.stalled >= 2:
            details = "、".join(status["missing"]) or "有证据的最终答复"
            raise DeliveryIncomplete("尚未完成：" + details + ("。需要先解决：" + blocker if blocker else
                                     "。连续核验未见进展，已保存任务与现有结果，可修正后继续。"))
        return ("系统任务核验：本次任务尚未完成，请继续处理下列事项。已执行成功的动作不要重做，"
                "已查询到的数据直接复用。完成事项后使用 update_task 更新真实进度；不得把未完成事项取消或删除以通过核验。"
                "必须给出有依据的最终答复，不能只说正在继续。参数错误可按工具返回修正；数据库超时、断连和权限错误禁止重试。"
                "核验记录：" + json.dumps({**status, "reply_errors": errors, "failed_tools": failures}, ensure_ascii=False))
