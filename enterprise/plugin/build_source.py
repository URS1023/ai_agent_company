"""Build a reproducible, verified source archive, not a Dify installation package."""

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent
SOURCE_FILES = (
    ".difyignore",
    "README.md",
    "_assets/icon.svg",
    "build_source.py",
    "main.py",
    "managed_device_plugin/__init__.py",
    "managed_device_plugin/bridge.py",
    "managed_device_plugin/client.py",
    "managed_device_plugin/models.py",
    "manifest.yaml",
    "provider/enterprise_device.py",
    "provider/enterprise_device.yaml",
    "pyproject.toml",
    "requirements.txt",
    "tests/test_backend_contract.py",
    "tests/test_bridge.py",
    "tests/test_client.py",
    "tests/test_package.py",
    "tests/test_sdk.py",
    "tools/evaluate_device.py",
    "tools/evaluate_device.yaml",
)


def build_source(destination: Path | None = None) -> Path:
    directory = (destination or ROOT / "dist").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    contents = {name: (ROOT / name).read_bytes() for name in SOURCE_FILES}
    checksums = {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}
    contents["SOURCE_SHA256.json"] = (json.dumps(checksums, indent=2, sort_keys=True) + "\n").encode("utf-8")
    archive = directory / "enterprise-device-assessment-0.1.0-source.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
        for name, content in sorted(contents.items()):
            entry = ZipInfo(name, date_time=(2026, 9, 8, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            output.writestr(entry, content)
    with ZipFile(archive) as verified:
        if verified.testzip() is not None or set(verified.namelist()) != set(contents):
            raise ValueError("Source archive verification failed")
        for name, expected in contents.items():
            if verified.read(name) != expected:
                raise ValueError("Source archive content mismatch")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive


if __name__ == "__main__":
    print(build_source())
