"""设备、产品和标准共用质量分类树，保留明确对象绑定而非自由JSON标签。"""
from sqlalchemy import select, update

from agent_platform.core.errors import DomainError

from . import models as m
from .service import ManufacturingService, view


class CategoryService(ManufacturingService):
    def save(self, data, category_id=None):
        values = {key: value for key, value in data.items() if key != "revision"}
        values["code"] = values.get("code", "").upper()
        original = self.get(m.QualityCategory, category_id) if category_id else None
        if original and original.target_type != values["target_type"]:
            raise DomainError(422, "category_target_immutable", "分类对象不能修改")
        if values["code"]:
            duplicate = self.session.scalar(select(m.QualityCategory.id).where(m.QualityCategory.workspace_id == self.actor.workspace_id, m.QualityCategory.target_type == values["target_type"], m.QualityCategory.code == values["code"], m.QualityCategory.archived.is_(False), m.QualityCategory.id != category_id))
            if duplicate:
                raise DomainError(409, "duplicate_code", "分类编号已使用", {"code": "请使用其他编号"})
        current, seen = values.get("parent_id"), {category_id} if category_id else set()
        while current:
            if current in seen or len(seen) > 100:
                raise DomainError(422, "category_cycle", "上级分类不能形成循环", {"parent_id": "请选择其他上级"})
            seen.add(current)
            parent = self.get(m.QualityCategory, current)
            if parent.target_type != values["target_type"] or parent.status != "active":
                raise DomainError(422, "category_parent_invalid", "请选择同类且启用的上级分类")
            current = parent.parent_id
        return self.change(m.QualityCategory, category_id, values, data["revision"]) if original else self.create(m.QualityCategory, values)

    def remove(self, category_id, revision):
        row = self.get(m.QualityCategory, category_id)
        child = self.session.scalar(select(m.QualityCategory.id).where(m.QualityCategory.workspace_id == self.actor.workspace_id, m.QualityCategory.parent_id == row.id, m.QualityCategory.archived.is_(False)).limit(1))
        bound = self.session.scalar(select(m.QualityCategoryBinding.id).where(m.QualityCategoryBinding.workspace_id == self.actor.workspace_id, m.QualityCategoryBinding.category_id == row.id, m.QualityCategoryBinding.archived.is_(False)).limit(1))
        if child or bound:
            raise DomainError(409, "category_in_use", "请先移出下级分类和已关联对象")
        return self.change(m.QualityCategory, row.id, {"archived": True}, revision)

    def resources(self, resource_type, target_type=None, category_id=None, q="", limit=50, offset=0):
        model, field = {"device": (m.Device, "device_id"), "product": (m.Product, "product_id"), "template": (m.InspectionTemplate, "template_id")}[resource_type]
        clauses = [model.workspace_id == self.actor.workspace_id, model.archived.is_(False)]
        if q:
            clauses.append(model.name.contains(q, autoescape=True))
        if resource_type == "template" and target_type:
            clauses.append(model.target_type == target_type)
        bindings = self.session.scalars(select(m.QualityCategoryBinding).where(m.QualityCategoryBinding.workspace_id == self.actor.workspace_id, getattr(m.QualityCategoryBinding, field).is_not(None))).all()
        by_id = {getattr(binding, field): binding for binding in bindings}
        categories = {row.id: row for row in self.session.scalars(select(m.QualityCategory).where(m.QualityCategory.workspace_id == self.actor.workspace_id, m.QualityCategory.archived.is_(False)))}
        rows = self.session.scalars(select(model).where(*clauses).order_by(model.name, model.id)).all()
        if category_id:
            self.get(m.QualityCategory, category_id)
            rows = [row for row in rows if row.id in by_id and not by_id[row.id].archived and by_id[row.id].category_id == category_id]
        total = len(rows)
        items = []
        for row in rows[max(offset, 0):max(offset, 0) + min(max(limit, 1), 200)]:
            binding = by_id.get(row.id)
            category = categories.get(binding.category_id) if binding and not binding.archived else None
            items.append({"id": row.id, "name": row.name, "revision": row.revision, "category_id": category.id if category else None,
                          "category_name": category.name if category else None, "binding_id": binding.id if binding else None, "binding_revision": binding.revision if binding else None})
        return {"items": items, "total": total}

    def bind(self, data):
        category = self.get(m.QualityCategory, data["category_id"])
        if category.status != "active":
            raise DomainError(422, "category_inactive", "该分类已停用")
        model, field = {"device": (m.Device, "device_id"), "product": (m.Product, "product_id"), "template": (m.InspectionTemplate, "template_id")}[data["resource_type"]]
        resource = self.get(model, data["resource_id"])
        target = resource.target_type if data["resource_type"] == "template" else data["resource_type"]
        if category.target_type != target:
            raise DomainError(422, "category_target_mismatch", "分类与对象类型不一致")
        binding = self.session.scalar(select(m.QualityCategoryBinding).where(m.QualityCategoryBinding.workspace_id == self.actor.workspace_id, getattr(m.QualityCategoryBinding, field) == resource.id))
        if binding:
            if data.get("revision") is None:
                raise DomainError(409, "category_binding_exists", "对象分类已变更，请刷新")
            if binding.archived:
                restored = self.session.execute(update(m.QualityCategoryBinding).where(m.QualityCategoryBinding.id == binding.id, m.QualityCategoryBinding.workspace_id == self.actor.workspace_id, m.QualityCategoryBinding.revision == data["revision"]).values(category_id=category.id, archived=False, revision=data["revision"] + 1, updated_at=m.now()))
                if restored.rowcount != 1:
                    raise DomainError(409, "revision_conflict", "对象分类已更新，请刷新")
                self.session.flush()
                self.session.refresh(binding)
                self.audit("restore", "quality_category_binding", binding.id)
                return view(binding)
            return self.change(m.QualityCategoryBinding, binding.id, {"category_id": category.id}, data["revision"])
        if data.get("revision") is not None:
            raise DomainError(409, "revision_conflict", "对象分类不存在，请刷新")
        return self.create(m.QualityCategoryBinding, {"category_id": category.id, field: resource.id})
