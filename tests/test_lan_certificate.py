"""Offline checks for LAN certificates; never modify operating-system trust.

中文说明：局域网证书的离线检查（证书地址约束、有效期与密钥匹配等）。
隔离方式：直接加载 scripts/create_lan_certificate.py 的纯函数 + tmp_path 生成临时证书文件，
绝不修改操作系统信任库、不安装任何系统证书。
"""
import importlib.util
import ipaddress
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes

spec = importlib.util.spec_from_file_location("lan_certificate", Path(__file__).resolve().parents[1] / "scripts/create_lan_certificate.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_lan_certificate_reuse_and_constrained_scope(tmp_path):
    metadata = module.create_certificate("192.168.6.16", tmp_path)
    ca = x509.load_pem_x509_certificate((tmp_path / "root-ca.crt").read_bytes())
    server = x509.load_pem_x509_certificate((tmp_path / "server.crt").read_bytes())
    server.verify_directly_issued_by(ca)
    constraints = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert constraints.critical
    assert x509.IPAddress(ipaddress.ip_network("192.168.6.16/32")) in constraints.value.permitted_subtrees
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.path_length == 0
    assert server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("192.168.6.16"), ipaddress.ip_address("127.0.0.1")]
    assert metadata["sha256"] == ca.fingerprint(hashes.SHA256()).hex().upper()
    before = {name: (tmp_path / name).read_bytes() for name in ["root-ca.key", "server.key", "server.crt"]}
    assert module.create_certificate("192.168.6.16", tmp_path) == metadata
    assert all((tmp_path / name).read_bytes() == value for name, value in before.items())
    assert not list((tmp_path / "client").glob("*.key"))
    with pytest.raises(ValueError, match="LAN IP changed"):
        module.create_certificate("192.168.6.17", tmp_path)


@pytest.mark.parametrize("address", ["0.0.0.0", "127.0.0.1", "8.8.8.8", "224.0.0.1"])
def test_reject_non_lan_certificate_address(tmp_path, address):
    with pytest.raises(ValueError, match="private LAN"):
        module.create_certificate(address, tmp_path)
