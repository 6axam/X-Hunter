from io import BytesIO

import pytest
from PIL import Image

from app.config import Settings
from app.media.storage import MediaRejected, MediaStore


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 24), (24, 110, 76)).save(output, "PNG")
    return output.getvalue()


def test_media_store_validates_and_generates_safe_name(tmp_path) -> None:
    store = MediaStore(Settings(media_root=tmp_path))
    result = store.store_bytes(png_bytes())
    assert result["mime_type"] == "image/jpeg"
    assert result["width"] == 32
    assert str(result["safe_path"]).startswith(str(tmp_path.resolve()))
    assert str(result["safe_path"]).endswith(".jpg")


def test_media_store_rejects_html_and_untrusted_hosts(tmp_path) -> None:
    store = MediaStore(Settings(media_root=tmp_path, media_allowed_hosts=["olxcdn.com"]))
    assert store.host_allowed("https://ireland.apollo.olxcdn.com/image.jpg") is True
    assert store.host_allowed("https://olxcdn.com.evil.test/image.jpg") is False
    assert store.host_allowed("http://olxcdn.com/image.jpg") is False
    with pytest.raises(MediaRejected):
        store.store_bytes(b"<html>not an image</html>")
