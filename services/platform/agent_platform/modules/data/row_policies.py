"""行策略生命周期与当前主体范围；只读平台授权元数据，不连接业务数据源。"""
from __future__ import annotations

from hashlib import sha256
import json
import math

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from agent_platform.core.errors import DomainError
from agent_platform.core.models import Membership, Role, User, Workspace
from agent_platform.core.records import RecordStore

from . import models as m


def canonical(value):
    """规范化规则顺序用于范围比较；摘要不包含客户端或角色自报的身份。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def invalid(message, path):
    return DomainError(422, "row_policy_invalid", message, {"path": path})


class RowPolicyService:
    def __init__(self, session, actor):
        self.session, self.actor = session, actor

    def source(self, source_id):
        row = self.session.scalar(select(m.DataSource).where(m.DataSource.id == source_id, m.DataSource.workspace_id == self.actor.workspace_id, m.DataSource.archived.is_(False)))
        if row is None:
            raise DomainError(404, "resource_not_found", "数据源不存在或不在当前工作区")
        return row

    def policies(self, source_id):
        return list(self.session.scalars(select(m.RowPolicy).where(m.RowPolicy.workspace_id == self.actor.workspace_id, m.RowPolicy.datasource_id == source_id)))

    def subjects(self, source_id):
        """仅返回本工作区配置策略所需的ID和名称，不暴露成员凭据或其他组织信息。"""
        self.actor.require("m05.admin")
        self.source(source_id)
        workspace = self.session.get(Workspace, self.actor.workspace_id)
        roles = self.session.scalars(select(Role).where(Role.workspace_id == self.actor.workspace_id).order_by(Role.name)).all()
        members = select(Membership.user_id).where(Membership.workspace_id == self.actor.workspace_id, Membership.disabled.is_(False))
        users = self.session.scalars(select(User).where(User.organization_id == workspace.organization_id, User.disabled.is_(False), or_(User.id.in_(members), User.is_admin.is_(True))).order_by(User.display_name, User.id)).all()
        return {"roles": [{"id": row.id, "name": row.name} for row in roles], "users": [{"id": row.id, "name": row.display_name or row.username} for row in users]}

    def validate(self, source, table_name, mode, rules):
        """保存前核实字段和主体，动态值由服务端解析，非法规则不能退化为开放访问。"""
        table = next((table for table in source.schema_snapshot if table["tableName"] == table_name), None)
        if table is None:
            raise invalid("请选择已登记的数据表", "table_name")
        if mode not in ("all", "rules") or not isinstance(rules, list) or len(rules) > 50:
            raise invalid("行权限模式或规则数量无效", "rules")
        if mode == "all" and rules:
            raise invalid("全部数据模式不能保留条件规则", "rules")
        fields = {column["columnName"] for column in table["columns"]}
        subjects = self.subjects(source.id)
        allowed = {"role": {item["id"] for item in subjects["roles"]}, "user": {item["id"] for item in subjects["users"]}}
        for i, rule in enumerate(rules):
            prefix = f"rules.{i}"
            kind, ids = rule["subject_type"], rule["subject_ids"]
            if kind not in ("all", "role", "user") or len(ids) > 100 or (kind == "all" and ids) or (kind != "all" and (not ids or len(set(ids)) != len(ids) or not set(ids).issubset(allowed[kind]))):
                raise invalid("请选择当前工作区有效的授权对象", prefix + ".subject_ids")
            if not 1 <= len(rule["conditions"]) <= 20:
                raise invalid("每条规则需要1至20个条件", prefix + ".conditions")
            for j, condition in enumerate(rule["conditions"]):
                path = prefix + f".conditions.{j}"
                if condition["field"] not in fields:
                    raise invalid("条件引用了未登记的字段", path + ".field")
                value, op, dynamic = condition.get("value"), condition["operator"], condition["value_source"] != "literal"
                if dynamic:
                    if value is not None or op not in ("eq", "ne", "in", "not_in"):
                        raise invalid("动态值不接受手填值或当前比较方式", path + ".value")
                    continue
                if op in ("is_null", "not_null"):
                    if value is not None:
                        raise invalid("空值判断无需填写值", path + ".value")
                    continue
                values = value if isinstance(value, list) else [value]
                if (op in ("in", "not_in")) != isinstance(value, list) or not 1 <= len(values) <= 100:
                    raise invalid("包含条件须填写1至100个值，其他条件填写单值", path + ".value")
                if any(v is None or not isinstance(v, (str, int, float, bool)) or isinstance(v, float) and not math.isfinite(v) or isinstance(v, str) and len(v) > 1000 or isinstance(v, int) and not -(2**63) <= v < 2**63 for v in values):
                    raise invalid("条件值须为有限标量，空值请使用空值判断", path + ".value")
                if len({"boolean" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else "string" for v in values}) != 1:
                    raise invalid("同一包含条件的值须使用相同类型", path + ".value")
                if op in ("gt", "gte", "lt", "lte") and isinstance(value, bool):
                    raise invalid("范围条件不接受布尔值", path + ".value")

    def create(self, source_id, data):
        self.actor.require("m05.admin")
        source = self.source(source_id)
        self.validate(source, data["table_name"], data["mode"], data["rules"])
        if any(row.table_name == data["table_name"] for row in self.policies(source_id)):
            raise DomainError(409, "row_policy_exists", "该表已有行权限，请编辑已有配置", {"table_name": data["table_name"]})
        row = m.RowPolicy(workspace_id=self.actor.workspace_id, datasource_id=source_id, **data)
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            raise DomainError(409, "row_policy_exists", "该表已有行权限，请刷新", {"table_name": data["table_name"]}) from exc
        RecordStore(self.session, self.actor).audit("row_policy.create", "row_policy", row.id, {"datasource_id": source_id, "table_name": row.table_name, "mode": row.mode})
        return row

    def patch(self, row_id, data):
        self.actor.require("m05.admin")
        row = self.session.scalar(select(m.RowPolicy).where(m.RowPolicy.id == row_id, m.RowPolicy.workspace_id == self.actor.workspace_id))
        if row is None:
            raise DomainError(404, "row_policy_not_found", "行权限配置不存在")
        self.validate(self.source(row.datasource_id), row.table_name, data["mode"], data["rules"])
        changed = self.session.execute(update(m.RowPolicy).where(m.RowPolicy.id == row.id, m.RowPolicy.workspace_id == self.actor.workspace_id, m.RowPolicy.revision == data["revision"]).values(mode=data["mode"], rules=data["rules"], revision=data["revision"] + 1, updated_at=m.now()))
        if changed.rowcount != 1:
            raise DomainError(409, "revision_conflict", "权限已更新，请刷新后重试")
        self.session.flush()
        self.session.refresh(row)
        RecordStore(self.session, self.actor).audit("row_policy.update", "row_policy", row.id, {"datasource_id": row.datasource_id, "table_name": row.table_name, "mode": row.mode})
        return row

    def effective(self, source, tables):
        """基于当前有效成员角色计算条件，角色名称改动不会改变行策略主体。"""
        member = self.session.scalar(select(Membership).where(Membership.workspace_id == self.actor.workspace_id, Membership.user_id == self.actor.user_id, Membership.disabled.is_(False)))
        role_ids = set(self.session.scalars(select(Role.id).where(Role.workspace_id == self.actor.workspace_id, Role.id.in_(member.role_ids)))) if member and member.role_ids else set()
        policies = {row.table_name: row for row in self.policies(source.id)}
        scopes, versions = {}, {}
        for name in sorted(tables):
            policy = policies.get(name)
            if policy is None or policy.mode == "all":
                scopes[name] = {"mode": "all"}
            else:
                clauses = {}
                known_fields = {column["columnName"] for table in source.schema_snapshot if table["tableName"] == name for column in table["columns"]}
                for rule in policy.rules:
                    matched = rule["subject_type"] == "all" or rule["subject_type"] == "user" and self.actor.user_id in rule["subject_ids"] or rule["subject_type"] == "role" and bool(role_ids.intersection(rule["subject_ids"]))
                    if not matched:
                        continue
                    conditions = []
                    for condition in rule["conditions"]:
                        if condition["field"] not in known_fields:
                            raise DomainError(403, "row_policy_field_missing", "行权限字段已变更，请联系管理员核查")
                        value = {"current_user_id": self.actor.user_id, "current_workspace_id": self.actor.workspace_id}.get(condition["value_source"], condition.get("value"))
                        if condition["operator"] in ("in", "not_in"):
                            value = value if isinstance(value, list) else [value]
                            value = sorted({canonical(v): v for v in value}.values(), key=canonical)
                        item = {"field": condition["field"], "operator": condition["operator"], "value": value}
                        conditions.append(item)
                    normalized = sorted({canonical(item): item for item in conditions}.values(), key=canonical)
                    clauses[canonical(normalized)] = normalized
                scopes[name] = {"mode": "rules", "clauses": sorted(clauses.values(), key=canonical)}
            versions[name] = {"id": policy.id, "revision": policy.revision} if policy else None
        return scopes, versions


def scope_digest(workspace_id, source_id, fields, scopes):
    """只绑定有效范围；同范围可跨人复核，版本留痕但不因无关编辑永久锁死历史。"""
    return sha256(canonical({"workspace_id": workspace_id, "datasource_id": source_id, "fields": fields, "rows": scopes}).encode()).hexdigest()
