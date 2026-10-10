from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from app.config import Settings


class MediaRejected(ValueError):
    pass


class MediaStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.media_root.resolve()

    def host_allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
            return False
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self.settings.media_allowed_hosts)

    async def _validate_network_target(self, url: str) -> None:
        if not self.host_allowed(url):
            raise MediaRejected("media_host_not_allowed")
        host = urlsplit(url).hostname
        assert host is not None
        records = await asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
        for record in records:
            address = ipaddress.ip_address(record[4][0])
            if not address.is_global:
                raise MediaRejected("media_private_address")

    async def download(self, url: str) -> tuple[bytes, str]:
        current = url
        timeout = httpx.Timeout(12, connect=5)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for _ in range(4):
                await self._validate_network_target(current)
                async with client.stream("GET", current, headers={"User-Agent": "OLX-Hunter/0.1"}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise MediaRejected("media_redirect_missing")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0]
                    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                        raise MediaRejected("media_content_type")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.settings.media_max_bytes:
                            raise MediaRejected("media_too_large")
                    return bytes(body), content_type
        raise MediaRejected("media_too_many_redirects")

    def store_bytes(self, data: bytes) -> dict[str, str | int]:
        if len(data) > self.settings.media_max_bytes:
            raise MediaRejected("media_too_large")
        Image.MAX_IMAGE_PIXELS = self.settings.media_max_pixels
        try:
            with Image.open(BytesIO(data)) as probe:
                probe.verify()
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
                if width * height > self.settings.media_max_pixels:
                    raise MediaRejected("media_too_many_pixels")
                normalized = image.convert("RGB")
                checksum = hashlib.sha256(data).hexdigest()
                folder = self.root / checksum[:2]
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{checksum}.jpg"
                normalized.save(path, "JPEG", quality=88, optimize=True)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise MediaRejected("media_decode_failed") from exc
        safe = path.resolve()
        if self.root not in safe.parents:
            raise MediaRejected("media_path_escape")
        return {
            "checksum": checksum,
            "safe_path": str(safe),
            "mime_type": "image/jpeg",
            "width": width,
            "height": height,
            "byte_size": safe.stat().st_size,
        }

