"""工作区辅助配置仓储：乐观并发、明确作用域、不可变历史和审计。"""
from copy import deepcopy
from datetime import datetime, timezone
import json

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from ...core.errors import DomainError
from ...core.models import now_iso
from ...core.records import RecordStore
from .support_models import OperationSupportResource as Resource, OperationSupportRevision as Revision
from .support_validation import form_schema, validate_definition, validate_values

KINDS = {"dictionary", "parameter", "announcement", "form"}


def view(row):
    return {key: deepcopy(getattr(row, key)) for key in ("id", "workspace_id", "kind", "code", "name", "scope", "owner_id", "definition", "enabled", "revision", "deleted", "created_at", "updated_at")}


class SupportService:
    def __init__(self, session, actor):
        self.session, self.actor = session, actor

    def conditions(self, kind):
        if kind not in KINDS:
            raise DomainError(404, "support_kind_missing", "配置类型不存在")
        conditions = [Resource.workspace_id == self.actor.workspace_id, Resource.kind == kind, Resource.deleted.is_(False)]
        if not self.actor.is_admin:
            conditions.append(or_(Resource.scope == "workspace", Resource.owner_id == self.actor.user_id))
        return conditions

    def get(self, kind, identifier, *, check_permission=True):
        if check_permission:
            self.actor.require("m16.read")
        row = self.session.scalar(select(Resource).where(*self.conditions(kind), Resource.id == identifier))
        if row is None:
            raise DomainError(404, "support_resource_missing", "配置不存在或不在当前作用域")
        return row

    def list(self, kind, q="", enabled=None, scope=None, limit=50, offset=0):
        self.actor.require("m16.read")
        conditions = self.conditions(kind)
        if q:
            conditions.append(or_(Resource.name.contains(q, autoescape=True), Resource.code.contains(q, autoescape=True)))
        if enabled is not None:
            conditions.append(Resource.enabled == enabled)
        if scope:
            if scope not in {"workspace", "personal"}:
                raise DomainError(422, "support_scope_invalid", "请选择有效作用域")
            conditions.append(Resource.scope == scope)
        total = self.session.scalar(select(func.count()).select_from(Resource).where(*conditions)) or 0
        rows = self.session.scalars(select(Resource).where(*conditions).order_by(Resource.updated_at.desc(), Resource.id).limit(min(max(limit, 1), 500)).offset(max(offset, 0))).all()
        return {"items": [view(row) for row in rows], "total": total}

    def snapshot(self, row, action, fields=None):
        self.session.add(Revision(resource_id=row.id, workspace_id=row.workspace_id, number=row.revision, snapshot=view(row), created_by=self.actor.user_id))
        RecordStore(self.session, self.actor).audit("operation_support." + action, "operation_" + row.kind, row.id, {"name": row.name, "kind": row.kind, "code": row.code, "revision": row.revision, "fields": fields or []})
        self.session.flush()

    def create(self, kind, data):
        self.actor.require("m16.admin")
        self.conditions(kind)
        definition = validate_definition(kind, data["definition"])
        if kind == "parameter" and data["code"].split(".")[-1].lower() in {"password", "secret", "token", "api_key", "access_token", "authorization"}:
            raise DomainError(422, "parameter_secret_not_allowed", "凭据请使用集成连接中的密钥管理", {"code": "此编码用于凭据，请改用密钥管理"})
        row = Resource(workspace_id=self.actor.workspace_id, kind=kind, code=data["code"], name=data["name"], scope=data["scope"], scope_owner=self.actor.user_id if data["scope"] == "personal" else "workspace", owner_id=self.actor.user_id, definition=definition, enabled=data["enabled"])
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            raise DomainError(409, "support_code_conflict", "该作用域中的编码已被使用", {"code": "编码已被使用"}) from exc
        self.snapshot(row, "create")
        return view(row)

    def patch(self, kind, identifier, data):
        self.actor.require("m16.admin")
        row = self.get(kind, identifier, check_permission=False)
        if row.scope == "personal" and row.owner_id != self.actor.user_id and not self.actor.is_admin:
            raise DomainError(403, "support_owner_required", "不能修改其他人的个人配置")
        changes = {key: value for key, value in data.items() if key != "revision" and value is not None}
        if set(changes) - {"name", "scope", "definition", "enabled", "deleted"}:
            raise DomainError(422, "support_field_invalid", "配置字段无效")
        if "scope" in changes:
            if row.owner_id != self.actor.user_id and not self.actor.is_admin:
                raise DomainError(403, "support_owner_required", "只有创建者可更改作用域")
            changes["scope_owner"] = row.owner_id if changes["scope"] == "personal" else "workspace"
        if "definition" in changes:
            changes["definition"] = validate_definition(kind, changes["definition"])
        try:
            with self.session.begin_nested():
                updated = self.session.execute(update(Resource).where(Resource.id == row.id, Resource.workspace_id == self.actor.workspace_id, Resource.revision == data["revision"], Resource.deleted.is_(False)).values(**changes, revision=data["revision"] + 1, updated_at=now_iso()), execution_options={"synchronize_session": False})
                if updated.rowcount != 1:
                    raise DomainError(409, "revision_conflict", "配置已更新，请刷新后再编辑")
        except IntegrityError as exc:
            raise DomainError(409, "support_code_conflict", "目标作用域中的编码已被使用", {"scope": "目标作用域已有相同编码"}) from exc
        self.session.refresh(row)
        self.snapshot(row, "delete" if changes.get("deleted") else "update", sorted(changes))
        return view(row)

    def revisions(self, kind, identifier):
        row = self.get(kind, identifier)
        revisions = self.session.scalars(select(Revision).where(Revision.resource_id == row.id, Revision.workspace_id == self.actor.workspace_id).order_by(Revision.number.desc()).limit(100)).all()
        return {"items": [{"id": item.id, "number": item.number, "snapshot": deepcopy(item.snapshot), "created_at": item.created_at} for item in revisions], "total": len(revisions)}

    def restore(self, kind, identifier, data):
        self.actor.require("m16.admin")
        row = self.get(kind, identifier, check_permission=False)
        revision = self.session.scalar(select(Revision).where(Revision.resource_id == row.id, Revision.workspace_id == self.actor.workspace_id, Revision.number == data["target_revision"]))
        if revision is None or revision.snapshot.get("deleted"):
            raise DomainError(404, "support_revision_missing", "可恢复版本不存在")
        snapshot = revision.snapshot
        return self.patch(kind, identifier, {"revision": data["revision"], **{key: snapshot[key] for key in ("name", "scope", "definition", "enabled")}})

    def schema(self, identifier):
        return form_schema(view(self.get("form", identifier)))

    def validate(self, identifier, body):
        row = self.get("form", identifier)
        if row.revision != body["revision"]:
            raise DomainError(409, "revision_conflict", "表单已更新，请重新打开预览")
        try:
            if len(json.dumps(body["values"], ensure_ascii=False, allow_nan=False).encode("utf-8")) > 100000:
                raise ValueError()
        except (TypeError, ValueError, RecursionError):
            raise DomainError(422, "form_preview_values_invalid", "预览值无效或超过100KB")
        result = validate_values(view(row), body["values"])
        RecordStore(self.session, self.actor).audit("operation_support.validate", "operation_form", row.id, {"revision": row.revision, "valid": result["valid"]})
        return result

    def dictionary_options(self, code):
        # 业务控件只读取当前工作区/本人启用字典；不授予系统配置管理权限。
        rows = self.session.scalars(select(Resource).where(Resource.workspace_id == self.actor.workspace_id, Resource.kind == "dictionary", Resource.code == code, Resource.deleted.is_(False), Resource.enabled.is_(True), or_(Resource.scope == "workspace", Resource.owner_id == self.actor.user_id)).order_by((Resource.scope == "personal").desc())).all()
        if not rows:
            raise DomainError(404, "dictionary_missing", "启用字典不存在")
        row = rows[0]
        return {"code": row.code, "revision": row.revision, "items": [deepcopy(item) for item in row.definition["entries"] if item["enabled"]]}

    def announcements(self):
        now = datetime.now(timezone.utc)
        rows = self.session.scalars(select(Resource).where(Resource.workspace_id == self.actor.workspace_id, Resource.kind == "announcement", Resource.scope == "workspace", Resource.deleted.is_(False), Resource.enabled.is_(True)).order_by(Resource.updated_at.desc()).limit(100)).all()
        result = []
        for row in rows:
            definition = row.definition
            if definition.get("publish_at") and datetime.fromisoformat(definition["publish_at"]) > now:
                continue
            if definition.get("expires_at") and datetime.fromisoformat(definition["expires_at"]) <= now:
                continue
            result.append({"id": row.id, "name": row.name, "category": definition["category"], "content": definition["content"], "published_at": definition.get("publish_at") or row.created_at})
        return {"items": result, "total": len(result)}
