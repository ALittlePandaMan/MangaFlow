import hashlib

import pytest
from app.core.serialization import file_digest


@pytest.mark.parametrize("data", [b"", b"manga", bytes(range(256)) * 12_289], ids=["empty", "small", "multi-megabyte"])
def test_file_digest_keeps_content_addressed_assets_stable(tmp_path, data):
    source = tmp_path / "source.bin"
    source.write_bytes(data)
    assert file_digest(source) == hashlib.sha256(data).hexdigest()
    assert source.read_bytes() == data


def test_file_digest_does_not_hide_missing_assets(tmp_path):
    with pytest.raises(FileNotFoundError):
        file_digest(tmp_path / "missing.bin")
