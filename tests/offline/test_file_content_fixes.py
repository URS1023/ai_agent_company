"""Offline extracted-function checks: no app startup, DB/session factory or network.

中文说明：从源码中抽取函数单独加载后做检查（ast 解析 + importlib），不启动应用、
不初始化数据库/会话工厂、不发网络请求。
隔离方式：源码抽取 + 桩替换，完全离线。
边界：只验证被抽取函数的解析/修复契约，不覆盖启动流程。
"""
import ast
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]


class ValidationError(Exception):
    pass


def load_functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), path, "exec"), namespace)
    return namespace


class FileFixes(unittest.TestCase):
    def setUp(self):
        self.ns = load_functions("backend/app/modules/files/service.py", {
            "normalize_filename", "validate_renamed_filename", "copied_filename", "rename_file", "update_metadata",
        }, {"os": os, "ValidationError": ValidationError, "datetime": datetime})

    def test_rename_keeps_format_and_allows_normal_name_edits(self):
        check = self.ns["validate_renamed_filename"]
        self.assertEqual(check("before.txt", "业务资料.TXT"), "业务资料.TXT")
        for name in ["after.pdf", "after", "after.txt.exe"]:
            with self.assertRaisesRegex(ValidationError, "扩展名"):
                check("before.txt", name)

    def test_default_copy_preserves_extension_and_length(self):
        copy = self.ns["copied_filename"]
        self.assertEqual(copy("合同.pdf"), "合同（副本）.pdf")
        self.assertEqual(copy("README"), "README（副本）")
        self.assertLessEqual(len(copy("x" * 496 + ".pdf")), 500)

    def test_invalid_rename_does_not_mutate_metadata_or_flush(self):
        row = SimpleNamespace(name="资料.txt")
        meta = SimpleNamespace(version=1, description="原说明", category_id=None)
        self.ns.update(current_file=lambda *_: (row, meta), ConflictError=ValidationError)
        db = SimpleNamespace(flush=lambda: self.fail("must not flush invalid changes"))
        with self.assertRaises(ValidationError):
            self.ns["update_metadata"](db, "ws", "f", version=1, category_id=None, description="新说明", tags=[], name="资料.pdf")
        self.assertEqual(row.name, "资料.txt")
        self.assertEqual(meta.description, "原说明")

    def test_stale_metadata_version_cannot_rename_file(self):
        row = SimpleNamespace(name="资料.txt")
        class Conflict(Exception):
            def __init__(self, message, **_): super().__init__(message)
        self.ns.update(current_file=lambda *_: (row, SimpleNamespace(version=2)), ConflictError=Conflict)
        with self.assertRaises(Conflict):
            self.ns["update_metadata"](None, "ws", "f", version=1, category_id=None, description="", tags=[], name="新名称.txt")
        self.assertEqual(row.name, "资料.txt")


class ContentSessionFixes(unittest.TestCase):
    def test_cookie_is_http_only_scoped_and_secure_on_https(self):
        spec = importlib.util.spec_from_file_location("content_session_test", ROOT / "backend/app/common/content_session.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from starlette.requests import Request
        from starlette.responses import Response
        for scheme in ["https", "http"]:
            response = Response()
            module.set_content_session(response, Request({"type": "http", "scheme": scheme, "path": "/api/auth/me", "headers": [(b"host", b"localhost")]}), "test-only-token")
            cookie = response.headers["set-cookie"]
            self.assertIn("HttpOnly", cookie)
            self.assertIn("Path=/api", cookie)
            self.assertIn("SameSite=lax", cookie)
            self.assertEqual("Secure" in cookie, scheme == "https")
            self.assertEqual(response.headers["cache-control"], "private, no-store")
        response = Response()
        module.clear_content_session(response)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])

    def test_url_tokens_are_ignored_before_any_db_access(self):
        tree = ast.parse((ROOT / "backend/app/common/deps.py").read_text(encoding="utf-8-sig"))
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "get_content_auth")
        # Only replace the local constant import; execute the actual auth branches.
        for node in ast.walk(fn):
            if isinstance(node, ast.If):
                node.orelse = [ast.Assign(targets=[ast.Name(id="COOKIE_NAME", ctx=ast.Store())], value=ast.Constant("cloud_brain_content")) if isinstance(child, ast.ImportFrom) and child.module == "content_session" else child for child in node.orelse]
        ns = {"Depends": lambda *_: None, "Header": lambda **_: None, "get_db": None, "bearer_scheme": None, "AuthError": ValidationError}
        def reached_db(_, token):
            raise RuntimeError("resolved:" + token)
        ns["resolve_principal"] = reached_db
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), fn], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), "content-auth", "exec"), ns)
        request = SimpleNamespace(headers={}, query_params={"token": "old-url-secret"}, cookies={}, method="GET")
        with self.assertRaisesRegex(ValidationError, "缺少认证信息"):
            ns["get_content_auth"](request)
        request.cookies["cloud_brain_content"] = "cookie-session"
        with self.assertRaisesRegex(RuntimeError, "resolved:cookie-session"):
            ns["get_content_auth"](request)
        request.headers["Authorization"] = "Bearer header-session"
        with self.assertRaisesRegex(RuntimeError, "resolved:header-session"):
            ns["get_content_auth"](request)


if __name__ == "__main__":
    unittest.main()
