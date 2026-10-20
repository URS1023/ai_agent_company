"""沙箱UID副本与宿主身份的纯文本回归，不启动WSL或执行代码。"""

import importlib.util
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[3] / "deploy/native/wsl_sandbox_entry.py"
spec = importlib.util.spec_from_file_location("sandbox_entry_under_test", path)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def test_private_passwd_contains_complete_pool_and_preserves_host():
    original = "root:x:0:0:root:/root:/bin/bash\nsandbox:x:65537:65537::/nonexistent:/usr/sbin/nologin\n"
    result = entry.sandbox_passwd(original)
    assert result.startswith(original)
    pool = [line for line in result.splitlines() if line.startswith("sandbox1")]
    assert len(pool) == 1000
    assert pool[0] == "sandbox10000:x:10000:0::/nonexistent:/usr/sbin/nologin"
    assert pool[-1] == "sandbox10999:x:10999:0::/nonexistent:/usr/sbin/nologin"
    assert entry.sandbox_passwd(result) == result


@pytest.mark.parametrize("conflict", [
    "business:x:10000:10000::/home/business:/bin/bash\n",
    "sandbox10000:x:12000:0::/nonexistent:/usr/sbin/nologin\n",
])
def test_private_passwd_refuses_existing_uid_or_name_conflicts(conflict):
    with pytest.raises(RuntimeError, match="sandbox_uid_pool_conflict"):
        entry.sandbox_passwd(conflict)
