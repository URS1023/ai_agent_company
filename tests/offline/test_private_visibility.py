"""Exercise actual authorization functions and compile queries without a DB.

中文说明：调用真实授权函数并编译查询语句（ast/源码抽取），验证私有可见性规则。
隔离方式：不连数据库；只验证授权判定与生成 SQL 的过滤条件。
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from sqlalchemy import Column, String, or_, select
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).resolve().parents[2]
Base = declarative_base()


class Skill(Base):
    __tablename__ = "skills"
    id = Column(String, primary_key=True)
    workspace_id = Column(String)
    owner_user_id = Column(String)
    visibility = Column(String)


def load(path, names, ns):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])), path, "exec"), ns)


class PrivateVisibility(unittest.TestCase):
    def setUp(self):
        self.ctx = SimpleNamespace(user_id="reader", workspace_id="workspace", admin=False)
        self.ns = {
            "PRIVATE_RESOURCE_TABLES": {"skills", "plugins", "connectors"},
            "access_policy": lambda ctx: SimpleNamespace(administrator=ctx.admin),
            "require_permission": lambda *args: None,
            "allowed_owner_ids": lambda *args: None,  # broadest ordinary role
            "resource_grant_ids": lambda *args: {"resource"},
            "PermissionErrorEx": PermissionError, "or_": or_, "select": select,
        }
        load("backend/app/common/authorization.py", {"private_visibility_predicate", "require_resource"}, self.ns)
        load("backend/app/common/owned_records.py", {"visible_records"}, self.ns)

    def record(self, **values):
        return SimpleNamespace(__tablename__="skills", id="resource", workspace_id="workspace", owner_user_id="owner", visibility="private", **values)

    def test_workspace_role_and_grant_cannot_override_private(self):
        for table in ("skills", "plugins", "connectors"):
            record = self.record()
            record.__tablename__ = table
            with self.assertRaises(PermissionError):
                self.ns["require_resource"](self.ctx, table + ".view", record)
        record.visibility = "workspace"
        self.ns["require_resource"](self.ctx, "connectors.view", record)

    def test_owner_and_admin_allowed_but_other_workspace_never(self):
        record = self.record()
        record.owner_user_id = self.ctx.user_id
        self.ns["require_resource"](self.ctx, "skills.view", record)
        record.owner_user_id = "owner"
        self.ctx.admin = True
        self.ns["require_resource"](self.ctx, "skills.view", record)
        record.workspace_id = "other"
        with self.assertRaises(PermissionError):
            self.ns["require_resource"](self.ctx, "skills.view", record)

    def test_private_owner_still_requires_action_permission(self):
        def denied(*args):
            raise PermissionError("action denied")
        self.ns["require_permission"] = denied
        self.ctx.user_id = "owner"
        with self.assertRaises(PermissionError):
            self.ns["require_resource"](self.ctx, "skills.publish", self.record())

    def test_list_predicate_preserves_workspace_and_owner_private_boundary(self):
        query = self.ns["visible_records"](self.ctx, Skill, "skills.view")
        sql = str(query.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("skills.workspace_id = 'workspace'", sql)
        self.assertIn("skills.visibility != 'private' OR skills.owner_user_id = 'reader'", sql)
        self.ctx.admin = True
        self.assertIsNone(self.ns["private_visibility_predicate"](self.ctx, Skill))


if __name__ == "__main__":
    unittest.main()
