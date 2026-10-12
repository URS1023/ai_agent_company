"""Binary bytes and invalid encodings must never become ready text blocks.

中文说明：二进制内容与非法编码绝不能变成"已就绪"的文本块（否则会出现乱码被检索）。
隔离方式：离线夹具（tmp_path）直接喂字节与错误编码，不连库。
边界：解码失败必须显式失败，不得以替换字符兜底后标记 ready。
"""
import pytest

from app.modules.files.extractor import UnsupportedDocumentError, extract_text


@pytest.mark.parametrize("extension", ["doc", "xls", "ppt"])
def test_legacy_office_requires_explicit_conversion(tmp_path, extension):
    path = tmp_path / ("old." + extension)
    path.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 32)
    with pytest.raises(UnsupportedDocumentError, match="另存为"):
        extract_text(path.name, "application/octet-stream", str(path))


@pytest.mark.parametrize("extension", ["txt", "csv", "unknown"])
@pytest.mark.parametrize("data", [b"plain\0binary", b"invalid\xfftext"])
def test_binary_or_undecodable_text_fails(tmp_path, extension, data):
    path = tmp_path / ("source." + extension)
    path.write_bytes(data)
    with pytest.raises(ValueError):
        extract_text(path.name, "application/octet-stream", str(path))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_supported_unicode_is_preserved(tmp_path, encoding):
    path = tmp_path / "source.txt"
    path.write_bytes("设备温度\n80℃".encode(encoding))
    assert extract_text(path.name, "text/plain", str(path)) == [{"text": "设备温度\n80℃", "page": 0}]
