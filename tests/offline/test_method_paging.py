"""Exercise the actual catalog filter/page function with an in-memory registry.

中文说明：用内存注册表调用真实的目录过滤/分页函数，验证筛选与分页行为。
隔离方式：内存注册表 + 源码抽取（ast），不连库、不启动应用。
边界：分页参数与过滤条件必须同时生效，且不重复/不漏项。
"""
import ast
import heapq
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MethodPaging(unittest.TestCase):
    def load(self, count=5001):
        tree = ast.parse((ROOT / "backend/app/modules/capabilities/catalog_service.py").read_text(encoding="utf-8-sig"))
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "list_methods")
        calls = []
        def registry(_, provider=""):
            calls.append(provider)
            for i in range(count):
                yield {"name": f"tool-{i:05}", "displayName": f"方法-{i:05}", "enabled": i % 2 == 0, "categoryId": "even" if i % 2 == 0 else "odd"}
        ns = {"heapq": heapq, "_available_tools": registry, "require_permission": lambda *_: None}
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), fn], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), "catalog-function", "exec"), ns)
        return ns["list_methods"], calls

    def test_entry_after_old_5000_ceiling_is_searchable(self):
        function, calls = self.load()
        result = function(None, keyword="05000", provider="http", limit=50)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["name"], "tool-05000")
        self.assertEqual(calls, ["http"])

    def test_filtering_precedes_page_count_and_selection(self):
        function, _ = self.load(101)
        result = function(None, category_id="even", enabled=True, offset=50, limit=50)
        self.assertEqual(result["total"], 51)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["name"], "tool-00100")

    def test_stale_page_clamps_and_export_keeps_all_records(self):
        function, _ = self.load(101)
        result = function(None, offset=1000, limit=50)
        self.assertEqual(result["offset"], 100)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(function(None)["items"]), 101)


if __name__ == "__main__":
    unittest.main()
