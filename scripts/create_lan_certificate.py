"""Create a private, address-constrained LAN CA without installing trust."""
from __future__ import annotations

import argparse
import ipaddress
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def create_certificate(address: str, directory: Path) -> dict:
    """为局域网 IP 生成（或复用）私有 CA 与服务器证书。

    参数：address 必须是服务器的私网 IP（回环/未指定/组播地址会被拒绝）；directory 为证书输出目录。
    返回：元数据字典（lan_ip、CA 的 SHA-256/SHA-1 指纹、CA 到期时间、trust_installed 恒为 False）。
    副作用：在 directory 下写入 root-ca.crt/root-ca.key、server.crt/server.key 以及客户端分发用的
    Cloud-Brain-LAN-CA.cer 与 fingerprint.txt，但不安装系统信任、不改动系统证书库（需人工分发）。
    异常：既有证书集不完整、LAN IP 变更、CA 剩余有效期不足 90 天或密钥与证书不匹配时抛 ValueError；
    服务器证书仍有效（剩余 >14 天且 SAN 一致）时复用，不重新签发。
    """
    ip = ipaddress.ip_address(address)
    if not ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_multicast:
        raise ValueError("Provide the server's private LAN IP address.")
    directory.mkdir(parents=True, exist_ok=True)
    client = directory / "client"
    client.mkdir(exist_ok=True)
    ca_file, ca_key_file = directory / "root-ca.crt", directory / "root-ca.key"
    metadata_file = directory / "metadata.json"
    now = datetime.now(timezone.utc)
    names = [x509.IPAddress(ip), x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]
    if ca_file.exists() or ca_key_file.exists():
        if not all(p.exists() for p in (ca_file, ca_key_file, metadata_file)):
            raise ValueError("Existing certificate set is incomplete; preserve it and inspect before continuing.")
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        if metadata["lan_ip"] != address:
            raise ValueError("LAN IP changed. Use a new certificate directory and redistribute its certificate.")
        ca = x509.load_pem_x509_certificate(ca_file.read_bytes())
        ca_key = serialization.load_pem_private_key(ca_key_file.read_bytes(), password=None)
        if ca.not_valid_after_utc <= now + timedelta(days=90):
            raise ValueError("CA expires soon. Create and distribute a replacement certificate set.")
        if ca.public_key().public_numbers() != ca_key.public_key().public_numbers():
            raise ValueError("CA key and certificate do not match.")
    else:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Cloud Brain LAN CA ({address})")])
        network = ipaddress.ip_network(f"{ip}/{ip.max_prefixlen}")
        ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
              .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1825))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .add_extension(x509.NameConstraints(
                  permitted_subtrees=[x509.IPAddress(network), x509.IPAddress(ipaddress.ip_network("127.0.0.1/32")), x509.DNSName("localhost")],
                  excluded_subtrees=None), critical=True)
              .sign(ca_key, hashes.SHA256()))
        ca_key_file.write_bytes(ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    server_key_file, server_file = directory / "server.key", directory / "server.crt"
    reuse_server = False
    if server_key_file.exists() and server_file.exists():
        existing = x509.load_pem_x509_certificate(server_file.read_bytes())
        existing_key = serialization.load_pem_private_key(server_key_file.read_bytes(), password=None)
        existing.verify_directly_issued_by(ca)
        reuse_server = (existing.not_valid_after_utc > now + timedelta(days=14)
                        and existing.public_key().public_numbers() == existing_key.public_key().public_numbers()
                        and list(existing.extensions.get_extension_for_class(x509.SubjectAlternativeName).value) == names)
    if not reuse_server:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server = (x509.CertificateBuilder()
                  .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, address)]))
                  .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
                  .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=90))
                  .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                  .add_extension(x509.SubjectAlternativeName(names), critical=False)
                  .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                  .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, None, None), critical=True)
                  .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                  .sign(ca_key, hashes.SHA256()))
        server_key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        server_file.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    public_file = client / "Cloud-Brain-LAN-CA.cer"
    public_file.write_bytes(ca.public_bytes(serialization.Encoding.DER))
    metadata = {"lan_ip": address, "sha256": ca.fingerprint(hashes.SHA256()).hex().upper(),
                "thumbprint": ca.fingerprint(hashes.SHA1()).hex().upper(),
                "ca_expires": ca.not_valid_after_utc.isoformat(), "trust_installed": False}
    metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (client / "fingerprint.txt").write_text(f"Cloud Brain LAN: {address}\nSHA-256: {metadata['sha256']}\nCertificate only; no private keys are included.\n", encoding="utf-8")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create_certificate(args.ip, args.directory), indent=2))
