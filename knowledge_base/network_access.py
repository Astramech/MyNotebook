from __future__ import annotations

import io
import ipaddress
import socket
from functools import lru_cache


PORT = 8502


def local_ipv4() -> str | None:
    """Return the IPv4 address used for the machine's default network route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect chooses a route without sending application data.
        probe.connect(("8.8.8.8", 80))
        value = str(probe.getsockname()[0])
        if _usable_lan_address(value):
            return value
    except OSError:
        pass
    finally:
        probe.close()

    try:
        candidates = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return None
    for candidate in candidates:
        value = str(candidate[4][0])
        if _usable_lan_address(value):
            return value
    return None


def tablet_url(port: int = PORT) -> str | None:
    address = local_ipv4()
    return f"http://{address}:{port}" if address else None


@lru_cache(maxsize=8)
def qr_png(value: str, scale: int = 7, border: int = 3) -> bytes | None:
    """Generate a QR PNG with OpenCV when available; the app works without it."""
    try:
        import cv2
        from PIL import Image, ImageOps

        encoder = cv2.QRCodeEncoder_create()
        matrix = encoder.encode(value)
        image = Image.fromarray(matrix).convert("L")
        image = ImageOps.expand(image, border=border, fill=255)
        image = image.resize(
            (image.width * scale, image.height * scale),
            resample=Image.Resampling.NEAREST,
        )
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    except Exception:
        return None


def _usable_lan_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.version == 4 and not address.is_loopback and not address.is_link_local)
