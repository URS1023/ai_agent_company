"""Compile actual pagination queries against dummy mapped models; never connect.

中文说明：用虚拟映射模型编译真实的分页查询语句，只校验 SQL 生成，不建立连接。
隔离方式：SimpleNamespace/Path 桩 + SQL 编译，不连数据库。
边界：分页与过滤必须体现在生成的 SQL 中（offset/limit 与范围条件）。
"""
from pathlib import Path
from types import SimpleNamespace
import ast
import unittest
import runpy
from sqlalchemy import Column, DateTime, String, and_, func, or_, select
from sqlalchemy.orm import aliased, declarative_base

ROOT = Path(__file__).resolve().parents[2]
Base = declarative_base()


class Source(Base):
    __tablename__ = "offline_sources"
    id = Column(String, primary_key=True)
    workspace_id = Column(String)
    owner_user_id = Column(String)
    name = Column(String)
    provider = Column(String)
    status = Column(String)
    created_at = Column(DateTime)


class Health(Base):
    __tablename__ = "offline_health"
    id = Column(String, primary_key=True)
    workspace_id = Column(String)
    datasource_id = Column(String)
    checked_at = Column(DateTime)


class Relation(Base):
    __tablename__ = "offline_relations"
    id = Column(String, primary_key=True)
    workspace_id = Column(String)
    owner_user_id = Column(String)
    source_type = Column(String)
    source_id = Column(String)
    target_type = Column(String)
    target_id = Column(String)
    status = Column(String)
    updated_at = Column(DateTime)


def load(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])), path, "exec"), namespace)
    return namespace


class Paging(unittest.TestCase):
    def test_asset_relations_filter_both_endpoint_scopes_before_count_and_page(self):
        statements, resolved = [], []
        db = SimpleNamespace(scalar=lambda query: statements.append(query) or 3,
                             scalars=lambda query: statements.append(query) or SimpleNamespace(all=lambda: [SimpleNamespace(id="page-row")]))
        ns = load("backend/app/modules/assets/service.py", {"list_relations"}, {
            "AssetRelation": Relation, "select": select, "func": func, "and_": and_, "or_": or_,
            "visible_records": lambda ctx, model, permission: select(model).where(model.workspace_id == "ws", model.owner_user_id == "authorized"),
            "_available_specs": lambda _: [SimpleNamespace(model=Source, key="datasource", status_attr="status", permission="datasources.view")],
            "_relation_view": lambda _, row: resolved.append(row.id) or {"id": row.id},
        })
        result = ns["list_relations"](SimpleNamespace(db=db), limit=2, offset=2)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["items"], [{"id": "page-row"}])
        self.assertEqual(resolved, ["page-row"])
        for statement in statements:
            sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
            self.assertIn("source_id IN (SELECT offline_sources.id", sql)
            self.assertIn("target_id IN (SELECT offline_sources.id", sql)
            self.assertIn("offline_sources.owner_user_id = 'authorized'", sql)
            self.assertIn("offline_sources.status != 'deleted'", sql)
        self.assertIn("LIMIT 2 OFFSET 2", str(statements[1].compile(compile_kwargs={"literal_binds": True})))

    def test_additive_page_contract_and_exact_last_page_boundary(self):
        contract = runpy.run_path(str(ROOT / "backend/app/common/contracts.py"))["PagedEnvelope"]
        ns = load("backend/app/common/pagination.py", {"page_records"}, {"func": func, "select": select})
        for total, requested, expected, count, more in [(50, 0, 0, 50, False), (51, 0, 0, 50, True), (51, 100, 50, 1, False), (0, 100, 0, 0, False)]:
            queries = []
            db = SimpleNamespace(scalar=lambda _: total, scalars=lambda query: queries.append(query) or iter(range(count)))
            result = ns["page_records"](db, select(Source).where(Source.workspace_id == "authorized").order_by(Source.id), limit=50, offset=requested, view=lambda row: str(row))
            self.assertEqual(result["offset"], expected)
            self.assertEqual(result["hasMore"], more)
            self.assertEqual(len(result["data"]), count)
            serialized = contract[str](**result).model_dump()
            self.assertEqual(serialized["data"], result["data"])
            self.assertTrue(serialized["success"])
            self.assertIn("LIMIT 50 OFFSET " + str(expected), str(queries[0].compile(compile_kwargs={"literal_binds": True})))

    def test_authorized_filter_count_and_limit_precede_view_and_health_loading(self):
        statements = []
        db = SimpleNamespace(scalar=lambda query: statements.append(query) or 101,
                             scalars=lambda query: statements.append(query) or SimpleNamespace(all=lambda: [SimpleNamespace(id="one-page")]))
        views = []
        ns = load("backend/app/modules/analytics/sources.py", {"_source_list_query", "list_sources_page"}, {
            "DataSource": Source, "func": func, "select": select,
            "visible_records": lambda ctx, model, permission: select(model).where(model.workspace_id == ctx.workspace_id, model.owner_user_id == "authorized-owner"),
            "_source_list_views": lambda _, rows: views.extend(rows) or [row.id for row in rows],
        })
        result = ns["list_sources_page"](SimpleNamespace(db=db, workspace_id="ws"), page=3, page_size=12, keyword="100%_", enabled_only=True)
        self.assertEqual(result, {"items": ["one-page"], "total": 101, "page": 3, "pageSize": 12})
        count_sql, page_sql = [str(query.compile(compile_kwargs={"literal_binds": True})) for query in statements]
        for sql in [count_sql, page_sql]:
            self.assertIn("authorized-owner", sql)
            self.assertIn("workspace_id = 'ws'", sql)
            self.assertIn("status = 'active'", sql)
            self.assertIn("100/%/_", sql)
        self.assertIn("LIMIT 12 OFFSET 24", page_sql)
        self.assertEqual(len(views), 1)

    def test_health_query_limits_rows_to_latest_per_authorized_source(self):
        statements = []
        db = SimpleNamespace(scalars=lambda query: statements.append(query) or SimpleNamespace(all=lambda: []))
        ns = load("backend/app/modules/analytics/governance.py", {"latest_health_checks"}, {
            "DataSourceHealthCheck": Health, "aliased": aliased, "select": select, "and_": and_, "or_": or_,
        })
        ns["latest_health_checks"](SimpleNamespace(db=db, workspace_id="ws"), ["visible-source"])
        sql = str(statements[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("NOT (EXISTS", sql)
        self.assertIn("datasource_id IN ('visible-source')", sql)
        self.assertIn("checked_at > offline_health.checked_at", sql)
        self.assertIn("id > offline_health.id", sql)
        self.assertEqual(ns["latest_health_checks"](None, []), {})


if __name__ == "__main__":
    unittest.main()
