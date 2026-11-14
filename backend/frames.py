"""Frame-level image helpers: resize, blur/dedupe metrics, crop, JPEG encode.

These are the building blocks of the *preprocess* stage. Keeping them pure
numpy/cv2 means they can be unit-tested without a decoder or a model.
"""
from __future__ import annotations

import cv2
import numpy as np


def sharpness(img: np.ndarray) -> float:
    """Variance of Laplacian — higher = sharper. Used to drop motion-blurred frames."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def resize_long_edge(img: np.ndarray, long_edge: int) -> np.ndarray:
    """Downscale so the longer side is `long_edge`. No upscaling (scale >= 1 is a no-op)."""
    h, w = img.shape[:2]
    scale = long_edge / max(h, w)
    if scale >= 1:
        return img
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def thumb(img: np.ndarray) -> np.ndarray:
    """Tiny grayscale thumbnail (32x18) used for cheap frame dedupe."""
    return cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (32, 18)).astype(np.float32)


def crop(img: np.ndarray, box: tuple[float, float, float, float], pad: int = 8) -> np.ndarray:
    """Crop `box` = (x1, y1, x2, y2) with padding, clamped to image bounds.

    Downstream (agent.py) uses this to zoom into a detected part before sending it to Claude.
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    x1 = max(0, int(x1) - pad)
    y1 = max(0, int(y1) - pad)
    x2 = min(w, int(x2) + pad)
    y2 = min(h, int(y2) + pad)
    return img[y1:y2, x1:x2]


def encode_jpeg(img: np.ndarray, long_edge: int = 1200, quality: int = 80) -> bytes:
    """Resize + compress for the LLM (image tokens scale with resolution)."""
    img = resize_long_edge(img, long_edge)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()