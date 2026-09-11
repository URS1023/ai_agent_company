import ast
import hashlib
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def test_ci_folded_test_commands_have_uniform_indentation() -> None:
    workflow = (ROOT.parents[1] / ".github/workflows/enterprise-foundation.yml").read_text(encoding="utf-8")
    lines = workflow.splitlines()
    checked = 0
    for index, line in enumerate(lines):
        if line.strip() != "run: >-":
            continue
        base_indent = len(line) - len(line.lstrip())
        command_lines: list[str] = []
        for following in lines[index + 1 :]:
            if not following.strip():
                continue
            if len(following) - len(following.lstrip()) <= base_indent:
                break
            command_lines.append(following)
        assert command_lines
        expected_indent = len(command_lines[0]) - len(command_lines[0].lstrip())
        for command_line in command_lines:
            assert len(command_line) - len(command_line.lstrip()) == expected_indent, (
                f"Folded run block at line {index + 1} contains a preserved newline: {command_line.strip()}"
            )
        checked += 1
    assert checked > 0


def test_ci_runs_real_sdk_tests_with_a_non_skippable_import_preflight() -> None:
    workflow = (ROOT.parents[1] / ".github/workflows/enterprise-foundation.yml").read_text(encoding="utf-8")
    title = "      - name: Verify managed plugin against pinned real SDK"
    assert title in workflow
    step = workflow.split(title, 1)[1].split("      - name:", 1)[0]
    assert "working-directory: ." in step
    assert "--with dify-plugin==0.9.1" in step
    assert "import dify_plugin" in step
    assert "version('dify-plugin') == '0.9.1'" in step
    assert "enterprise/plugin/tests/test_sdk.py" in step
    assert step.index("import dify_plugin") < step.index("enterprise/plugin/tests/test_sdk.py")
    assert "continue-on-error" not in step


def test_source_package_is_a_verified_zip_of_explicit_source_files(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("plugin_packaging", ROOT / "build_source.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive = module.build_source(tmp_path)
    assert archive.suffix == ".zip"
    with ZipFile(archive) as source:
        assert source.testzip() is None
        assert set(source.namelist()) == set(module.SOURCE_FILES) | {"SOURCE_SHA256.json"}
        checksums = json.loads(source.read("SOURCE_SHA256.json"))
        for name in module.SOURCE_FILES:
            assert source.read(name) == (ROOT / name).read_bytes()
            assert checksums[name] == hashlib.sha256(source.read(name)).hexdigest()
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in source.namelist())
        assert not any(
            token in name for name in source.namelist() for token in (".env", "__pycache__", ".venv", ".pytest_cache")
        )
    assert archive.with_suffix(".zip.sha256").read_text().split()[0] == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_all_python_sources_parse_without_importing_or_launching_the_sdk() -> None:
    for directory in ("managed_device_plugin", "provider", "tools", "tests"):
        for path in (ROOT / directory).glob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))


def test_declared_plugin_has_no_tool_identity_parameters_and_defaults_to_tls() -> None:
    tool = (ROOT / "tools/evaluate_device.yaml").read_text(encoding="utf-8")
    provider = (ROOT / "provider/enterprise_device.yaml").read_text(encoding="utf-8")
    manifest = (ROOT / "manifest.yaml").read_text(encoding="utf-8")
    assert "parameters: []" in tool
    assert "__enterprise_execution" not in provider
    assert "allow_insecure_http:" in provider and "default: false" in provider
    assert "permission: {}" in manifest
    assert "dify-plugin==0.9.1" in (ROOT / "requirements.txt").read_text()
