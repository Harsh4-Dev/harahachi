"""Body-fat estimation from a full-body photo (v2, POST /profile/body-photo).

Asks the vision model (NIM ``body`` slot, or the registry's active vision
backend) for a strict-JSON body-fat estimate, validated with pydantic. Falls
back to a deterministic mock keyed off the image hash ONLY when mock is allowed
(Docker/dev default); otherwise raises ``BackendUnavailable`` so the app gets a
clean 503 and its retry popup instead of a fake number. The photo is never
stored — only the estimate is returned; the app confirms/edits then saves via
PUT /profile.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib

from . import backends
from .models import BodyPhotoEstimate

BODY_PHOTO_SYSTEM_PROMPT = (
    "You are a fitness body-composition estimator. Given a full-body photo, "
    "estimate the person's body fat percentage from visible cues (muscle "
    "definition, waist, overall composition). Respond with ONLY a single JSON "
    "object, no prose, no fences:\n"
    '{"body_fat_pct": float, "range": [lo, hi], "confidence": float 0..1, '
    '"notes": string}\n'
    "body_fat_pct is your single best estimate (typically 8..40 for adults). "
    "range is a plausible low/high bracket around it. confidence 0..1 reflects "
    "how clear the photo is. notes is one short sentence. This is a rough visual "
    "estimate, not a medical measurement — say so briefly in notes. JSON only."
)


def _mock_estimate(image_bytes: bytes) -> BodyPhotoEstimate:
    seed = int.from_bytes(hashlib.sha256(image_bytes).digest()[:8], "big")
    # Deterministic body-fat in a realistic 12..34 range.
    bf = 12 + (seed % 220) / 10.0   # 12.0 .. 34.0
    bf = round(bf, 1)
    spread = 2.5 + (seed >> 8) % 3
    lo = round(max(3.0, bf - spread), 1)
    hi = round(min(60.0, bf + spread), 1)
    conf = round(0.45 + (seed >> 16) % 30 / 100.0, 2)  # 0.45 .. 0.74
    return BodyPhotoEstimate(
        body_fat_pct=bf,
        range=[lo, hi],
        confidence=conf,
        notes="Rough visual estimate from the photo (mock mode). Adjust before saving.",
    )


async def estimate(image_bytes: bytes, mime: str) -> tuple[BodyPhotoEstimate, str]:
    """Return (estimate, source). source is 'vllm' or 'mock'.

    Raises backends.BackendUnavailable when the model fails and mock is off.
    """
    from .config import config
    if backends.is_configured(need_vision=True):
        try:
            send_bytes, send_mime = await asyncio.to_thread(backends.prepare_image, image_bytes, mime)
            b64 = base64.b64encode(send_bytes).decode("ascii")
            data_url = f"data:{send_mime};base64,{b64}"
            messages = [
                {"role": "system", "content": BODY_PHOTO_SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "Estimate this person's body fat percentage."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ]
            content = await backends.chat_completion(
                messages, need_vision=True, max_tokens=300, temperature=0.2,
                op="analyze", task="body",
            )
            raw = backends._extract_json(content)
            return BodyPhotoEstimate.model_validate(raw), "vllm"
        except Exception as e:  # noqa: BLE001
            if not config.allow_mock:
                raise backends.BackendUnavailable(str(e)) from e
    elif not config.allow_mock:
        raise backends.BackendUnavailable("no vision model configured")
    return _mock_estimate(image_bytes), "mock"
