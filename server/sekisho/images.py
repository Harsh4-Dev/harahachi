"""Upload validation + re-encoding for vision calls (v4).

* ``validate`` trusts the **bytes, not the Content-Type**: the magic number must be
  JPEG / PNG / WebP and Pillow must be able to parse the header. Anything else is
  rejected with a clean 400 ``bad_image``. Decompression bombs are rejected too.
* ``fit_for_nim`` re-encodes the photo to an EXIF-free, upright JPEG whose base64
  form fits the NVIDIA NIM hosted-API inline limit (base64 < 180_000 chars,
  verified from NVIDIA's API reference samples; larger images would need the
  NVCF assets API). Re-encoding always happens before a NIM call so GPS/EXIF
  metadata never leaves the server.

Images are never stored or logged — they live only in request memory.
"""
from __future__ import annotations

import base64
import io
import warnings

from PIL import Image, ImageOps

# A phone photo is ~12-50 MP (and the app downscales before upload); anything
# larger is a decompression bomb, not a meal. Pillow only WARNS between
# MAX_IMAGE_PIXELS and 2x that, so the warning is promoted to an error too and
# every open is followed by an explicit width*height check.
MAX_PIXELS = 25_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
warnings.filterwarnings("error", category=Image.DecompressionBombWarning)
# Longest edge we ever need (fit_for_nim caps at 1280): JPEG draft mode decodes
# straight to a reduced scale, so a big JPEG never materializes at full size.
DRAFT_EDGE = 1280

ALLOWED_MIME = ("image/jpeg", "image/png", "image/webp")


class ImageRejected(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def sniff(data: bytes) -> str | None:
    """MIME type from magic bytes, or None if not a supported image."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _too_big() -> ImageRejected:
    return ImageRejected("bad_image", "Image dimensions are too large", 400)


def _check_pixels(im: "Image.Image") -> None:
    w, h = im.size
    if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
        raise _too_big()


def validate(data: bytes) -> str:
    """Return the sniffed MIME or raise ImageRejected('bad_image').

    CPU-bound: async handlers run it via ``asyncio.to_thread``."""
    mime = sniff(data)
    if mime is None:
        raise ImageRejected("bad_image", "Upload must be a JPEG, PNG or WebP image", 400)
    try:
        with Image.open(io.BytesIO(data)) as im:
            _check_pixels(im)
            fmt = (im.format or "").upper()
            im.verify()
    except ImageRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise _too_big()
    except Exception:
        raise ImageRejected("bad_image", "Image data is corrupt or unreadable", 400)
    if fmt not in ("JPEG", "PNG", "WEBP"):
        raise ImageRejected("bad_image", "Upload must be a JPEG, PNG or WebP image", 400)
    return mime


def b64_len(n_bytes: int) -> int:
    return 4 * ((n_bytes + 2) // 3)


def fit_for_nim(data: bytes, max_b64_chars: int, *, max_edge: int = 1280,
                min_edge: int = 256) -> tuple[bytes, str]:
    """Re-encode to an upright, metadata-free JPEG whose base64 is < max_b64_chars.

    Strategy: cap the long edge at ``max_edge``; walk JPEG quality 85 -> 40; if
    still too big shrink the long edge by 20% and repeat, down to ``min_edge``.
    Raises ImageRejected('image_too_large', 413) only if even the smallest
    rendition cannot fit (practically impossible for real photos).
    """
    # 3% safety margin under the hard limit (data-URL prefix is not counted in
    # NVIDIA's sample check, but stay well clear anyway).
    target = int(max_b64_chars * 0.97)
    try:
        with Image.open(io.BytesIO(data)) as src:
            _check_pixels(src)
            if (src.format or "").upper() == "JPEG":
                # Decode at 1/2, 1/4 or 1/8 scale (whatever still covers 1280px).
                src.draft("RGB", (DRAFT_EDGE, DRAFT_EDGE))
            src.load()
            im = ImageOps.exif_transpose(src)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
    except ImageRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise _too_big()
    except Exception:
        raise ImageRejected("bad_image", "Image data is corrupt or unreadable", 400)

    edge = min(max_edge, max(im.size))
    while True:
        frame = im.copy()
        frame.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        for quality in (85, 75, 65, 55, 45, 40):
            buf = io.BytesIO()
            frame.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
            out = buf.getvalue()
            if b64_len(len(out)) < target:
                return out, "image/jpeg"
        if edge <= min_edge:
            break
        edge = max(min_edge, int(edge * 0.8))
    raise ImageRejected("image_too_large", "Image could not be reduced to the model's size limit", 413)


def data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
