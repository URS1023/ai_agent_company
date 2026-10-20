#!/usr/bin/env bash
# 仅在本项目独立Ubuntu中准备原生依赖；不启动数据库、应用或Docker。
set -Eeuo pipefail
umask 077

readonly expected_distro='ai-agent-company'
readonly runtime_root='/opt/ai-agent-company'
readonly runtime_user='agentapp'
readonly policy_path='/usr/sbin/policy-rc.d'
readonly bootstrap_kind='ai-agent-company-wsl-bootstrap-v1'
readonly uv_version='0.13.4'
phase='preflight'
created_policy=0

fail() { printf '%s\n' "$1" >&2; exit 1; }
[[ "${WSL_DISTRO_NAME:-}" == "$expected_distro" ]] || fail '发行版名称不匹配，拒绝接管其他WSL环境。'
[[ $(id -u) == 0 ]] || fail 'bootstrap需要在该专用发行版内以root运行。'
[[ -f /etc/os-release ]] || fail '无法核实发行版。'
# 系统发行版文件由操作系统维护，不读取项目旧.env或用户业务配置。
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || fail '仅支持本次Ubuntu24.04。'
grep -qi microsoft /proc/sys/kernel/osrelease || fail '未识别为本机WSL内核。'
[[ ! -L "$runtime_root" ]] || fail '运行根不得为符号链接。'
if [[ -e "$runtime_root" && ! -f "$runtime_root/bootstrap.json" ]]; then
    [[ -d "$runtime_root" && -z $(find "$runtime_root" -mindepth 1 -maxdepth 1 -print -quit) ]] || fail '拒绝接管未标记的既有目录。'
fi
if id "$runtime_user" &>/dev/null; then
    [[ $(getent passwd "$runtime_user" | cut -d: -f6) == /home/agentapp ]] || fail '同名用户的Home不符合本项目约定。'
    [[ $(id -u "$runtime_user") -ne 0 ]] || fail '应用用户不得具有root身份。'
else
    useradd --create-home --shell /bin/bash "$runtime_user"
fi
install -d -m 0750 -o "$runtime_user" -g "$runtime_user" "$runtime_root"
exec 9>"$runtime_root/.bootstrap.lock"
flock -n 9 || fail '已有bootstrap在执行，禁止并行和自动重试。'
if [[ -f "$runtime_root/bootstrap.json" ]]; then
    python3 - "$runtime_root/bootstrap.json" "$bootstrap_kind" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding='utf-8'))
assert value['kind'] == sys.argv[2] and value['distro'] == 'ai-agent-company'
assert value['root'] == '/opt/ai-agent-company' and value['user'] == 'agentapp'
PY
fi
python3 - "$runtime_root/bootstrap.json" "$bootstrap_kind" <<'PY'
import json, os, sys
from pathlib import Path
path = Path(sys.argv[1]); value = {'kind': sys.argv[2], 'distro': 'ai-agent-company',
    'root': '/opt/ai-agent-company', 'user': 'agentapp', 'status': 'preparing'}
temporary = path.with_suffix('.pending'); temporary.write_text(json.dumps(value), encoding='utf-8')
os.replace(temporary, path)
PY
chown "$runtime_user:$runtime_user" "$runtime_root/bootstrap.json"

finish() {
    code=$?
    trap - EXIT
    # 仅删除本次创建且内容未变化的临时防自启动策略，不覆盖发行版既有策略。
    if [[ $created_policy == 1 ]]; then
        if [[ $(cat "$policy_path") == $'#!/bin/sh\nexit 101' ]]; then
            rm -- "$policy_path"
        else
            printf '%s\n' '防自启动策略发生变化，保留原文件并报告失败。' >&2
            code=1
        fi
    fi
    python3 - "$runtime_root/bootstrap.json" "$phase" "$code" <<'PY'
import json, os, sys
from pathlib import Path
path = Path(sys.argv[1]); value = json.loads(path.read_text(encoding='utf-8'))
value.update(status='ready' if sys.argv[3] == '0' else 'failed', phase=sys.argv[2], exit_code=int(sys.argv[3]))
temporary = path.with_suffix('.pending'); temporary.write_text(json.dumps(value), encoding='utf-8')
os.replace(temporary, path)
PY
    chown "$runtime_user:$runtime_user" "$runtime_root/bootstrap.json"
    exit "$code"
}
trap finish EXIT

phase='apt_dependencies'
if [[ -e "$policy_path" ]]; then
    fail '发现发行版既有policy-rc.d，先核实是否阻止服务自启动；本脚本不覆盖。'
fi
printf '#!/bin/sh\nexit 101\n' > "$policy_path"
chmod 0755 "$policy_path"
created_policy=1
export DEBIAN_FRONTEND=noninteractive
# Ubuntu24.04原生PG16满足Dify的PostgreSQL协议；实际版本写入收据，不伪称镜像PG15。
apt-get -o Acquire::Retries=0 update
apt-get -o Acquire::Retries=0 install --yes --no-install-recommends \
    ca-certificates curl git jq xz-utils unzip zip gettext-base util-linux \
    python3 python3-venv python3-dev build-essential pkg-config libseccomp-dev \
    libgmp-dev libmpfr-dev libmpc-dev libffi-dev libssl-dev libpq-dev \
    postgresql-16 postgresql-client-16 redis-server squid nginx \
    tmux iproute2 iptables procps ripgrep openssh-client

phase='uv_tooling'
install -d -m 0750 -o "$runtime_user" -g "$runtime_user" "$runtime_root/tooling" "$runtime_root/cache"
if [[ ! -x "$runtime_root/tooling/bin/python" ]]; then
    runuser -u "$runtime_user" -- python3 -m venv "$runtime_root/tooling"
fi
# 上游镜像uv0.8.9的内置索引早于Python3.13.14；只更新部署工具，不重解Dify依赖锁。
# 此SHA来自PyPI uv0.13.4官方x86_64 manylinux wheel，禁止退回源码构建或漂移latest。
printf 'uv==%s --hash=sha256:05e4332791b4d55b6dde8695b7fa6079b26991519f44760887c5dfbd5ca4fe53\n' "$uv_version" \
    > "$runtime_root/cache/uv-bootstrap-requirements.txt"
chmod 0644 "$runtime_root/cache/uv-bootstrap-requirements.txt"
runuser -u "$runtime_user" -- "$runtime_root/tooling/bin/python" -m pip install \
    --disable-pip-version-check --no-input --no-deps --retries 0 --only-binary=:all: --require-hashes \
    -r "$runtime_root/cache/uv-bootstrap-requirements.txt"
phase='python313'
runuser -u "$runtime_user" -- "$runtime_root/tooling/bin/uv" python list --only-downloads --all-versions '3.13.14'
runuser -u "$runtime_user" -- env UV_CACHE_DIR="$runtime_root/cache/uv" \
    UV_PYTHON_INSTALL_DIR="$runtime_root/tooling/python" UV_HTTP_RETRIES=0 \
    "$runtime_root/tooling/bin/uv" python install '3.13.14'
phase='ready'
python3 - "$runtime_root/bootstrap.json" <<'PY'
import json, subprocess, sys
from pathlib import Path
path = Path(sys.argv[1]); value = json.loads(path.read_text(encoding='utf-8'))
value.update(uv_version='0.13.4', python_requested='3.13.14', upstream_uv_version='0.8.9')
value['packages'] = subprocess.check_output(['dpkg-query', '-W', '-f=${Package}=${Version}\n',
    'postgresql-16', 'redis-server', 'squid', 'nginx', 'tmux', 'libseccomp-dev'], text=True).splitlines()
path.write_text(json.dumps(value), encoding='utf-8')
PY
printf '%s\n' '基础依赖已准备；没有启动数据库/应用。继续运行WSL setup准备源码与工具链。'
